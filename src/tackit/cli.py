"""Interface - CLI (design.md "Interface - CLI").

A thin adapter over :mod:`tackit.core` exposing the same operation surface as a
command line, for debugging / scripting / agent-fallback (the agent's default
door is MCP). No logic lives here. Output is verbose natural-language-with-ids by
default, ``--json`` for structured parsing; every mutating op prints its
obligations inline (same payload as the MCP results). The command<->slice mapping
matches the table in design.md.

This module is also the single ``[project.scripts]`` entry point: ``tackit mcp``
launches the stdio MCP server (design.md "Installation"); ``tackit setup`` emits
the post-install steps (agent-driven install).
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from pathlib import Path

from . import sync
from .core import Core, stale_alert_text
from .db import init_store, require_store
from .errors import TackitError
from .plan import parse_plan


# --- human-readable formatters (──> CLI default output) ---------------------

def _flags(status: str, stale: bool) -> str:
    return f"{status}, STALE" if stale else status


def _fmt_task(t) -> str:
    return f"T{t.id} [{_flags(t.status, t.stale)}] {t.name}"


def _fmt_neighbors(label: str, neighbors) -> list[str]:
    if not neighbors:
        return [f"  {label}: (none)"]
    return [f"  {label}:"] + [
        f"    - T{n.id} [{_flags(n.status, n.stale)}] {n.name}" for n in neighbors
    ]


def _fmt_slice(s) -> str:
    lines = [_fmt_task(s.task)]
    if s.task.description.strip():
        lines.append(f"  {s.task.description.strip()}")
    lines.append(f"  labels: {', '.join(s.labels) if s.labels else '(none)'}")
    lines += _fmt_neighbors("depends on", s.dependencies)
    lines += _fmt_neighbors("depended on by", s.dependents)
    return "\n".join(lines)


def _emit(obj_text: str, json_obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(json_obj, default=str, indent=2))
    else:
        print(obj_text)


def _dump(model):
    return model.model_dump(mode="json")


# --- D19: built-in stale surfacing (design.md "Enforcement" tier 2) ---------
# The stale check is code in the app, not advice: every Core-opening command
# surfaces the outstanding stale set deterministically -- on entry (before the op)
# and again on exit if the op changed it. It goes to STDERR so that `--json` stdout
# stays clean and parseable. The wording is single-sourced in core.stale_alert_text.

def _stale_ids(tasks) -> list[int]:
    out = []
    for t in tasks:
        out.append(t.id)
    return out


def _print_stale_banner(tasks, changed: bool = False) -> None:
    text = stale_alert_text(tasks)
    if text:
        prefix = "[after this change] " if changed else ""
        print(prefix + text, file=sys.stderr)
    elif changed:
        print("✓ stale worklist now empty — reconciliation complete.", file=sys.stderr)


@contextmanager
def _core_session():
    """Open Core, surface the entry stale banner, run the command, then surface the
    exit banner if the stale set changed, and close. Centralizes the mandatory
    before/after stale check so no command can skip it."""
    core = Core.open()
    entry = core.stale_worklist()
    _print_stale_banner(entry)
    try:
        yield core
    finally:
        after = core.stale_worklist()
        if _stale_ids(after) != _stale_ids(entry):
            _print_stale_banner(after, changed=True)
        if core.last_label_nudge:  # D23 anti-sprawl nudge
            print(core.last_label_nudge, file=sys.stderr)
        if core.last_delta:  # T117 cascade-ergonomics delta
            print(f"delta: {core.last_delta}", file=sys.stderr)
        core.close_conn()


# --- D22: board view (rich, grouped, dependency-aware CLI rendering) ----------

def _board_paint(text: str, codes) -> str:
    """Wrap text in ANSI codes, but only when stdout is a real terminal — so piped/
    redirected output, `--json`, and tests stay plain."""
    if not sys.stdout.isatty():
        return text
    prefix = ""
    for c in codes:
        prefix += f"\033[{c}m"
    return prefix + text + "\033[0m"


def _render_board(core, tasks) -> str:
    ACC, STALE, DIM, LAB, TXT, BOLD = "38;5;154", "38;5;203", "38;5;240", "38;5;244", "38;5;253", "1"
    lines: list[str] = []

    def section(title, group, base):
        if not group:
            return
        lines.append("")
        lines.append(_board_paint(f"{title} ({len(group)})", [base, BOLD]))
        for t in group:
            s = core.show(t.id)
            bar = STALE if t.stale else base
            tid = _board_paint(f"T{t.id}", [bar, BOLD])
            name_color = STALE if t.stale else (DIM if t.status == "closed" else TXT)
            name = _board_paint(t.name, [name_color])
            tag = _board_paint(" [STALE]", [STALE, BOLD]) if t.stale else ""
            labs = ("  " + _board_paint(" ".join(s.labels), [LAB])) if s.labels else ""
            lines.append(f" {_board_paint('▌', [bar])} {tid}  {name}{tag}{labs}")
            edges = []
            if s.dependencies:
                edges.append(_board_paint("needs→ ", [DIM]) + " ".join(f"T{n.id}" for n in s.dependencies))
            if s.dependents:
                edges.append(_board_paint("unblocks→ ", [DIM]) + " ".join(f"T{n.id}" for n in s.dependents))
            if edges:
                lines.append("     " + "   ".join(edges))

    opens = [t for t in tasks if t.status == "open"]
    dones = [t for t in tasks if t.status == "closed"]
    section("IN FLIGHT", opens, ACC)
    section("DONE", dones, DIM)
    return "\n".join(lines).lstrip("\n")


# --- command handlers -------------------------------------------------------

def _cmd_init(args) -> int:
    store = init_store(Path.cwd())
    _emit(
        f"Initialized tackit store at {store.dir} "
        f"(db gitignored; tackit.sql is the committed source of truth).",
        {"root": str(store.root), "dir": str(store.dir)},
        args.json,
    )
    return 0


def _cmd_add(args) -> int:
    with _core_session() as core:
        task = core.add(
            args.name,
            kind=args.kind,
            description=args.desc or "",
            labels=args.label,
            deps=args.dep,
        )
        _emit("created " + _fmt_slice(core.show(task.id)), _dump(core.show(task.id)), args.json)
    return 0


def _cmd_show(args) -> int:
    with _core_session() as core:
        s = core.show(args.id)
        _emit(_fmt_slice(s), _dump(s), args.json)
    return 0


def _cmd_load(args) -> int:
    text = Path(args.file).read_text() if args.file else sys.stdin.read()
    specs = parse_plan(text)  # fail loud on a bad plan BEFORE touching the store
    with _core_session() as core:
        keymap = core.load(specs)
        lines = [f"loaded {len(keymap)} task(s):"]
        for key, tid in keymap.items():
            lines.append(f"  {key} → T{tid}")
        _emit("\n".join(lines), {"loaded": keymap}, args.json)
    return 0


def _cmd_search(args) -> int:
    with _core_session() as core:
        hits = core.search(args.terms)
        text = (
            "\n".join(f"T{h.id}  ({h.score:+.3f})  {h.name}" for h in hits)
            if hits
            else "(no matches)"
        )
        _emit(text, [_dump(h) for h in hits], args.json)
    return 0


def _cmd_edit(args) -> int:
    with _core_session() as core:
        result = core.edit(args.id, delta=args.delta, name=args.name, description=args.desc)
        text = ["edited " + _fmt_task(result.task)]
        if result.newly_stale:
            text.append("  ⚠ now STALE (review/reconcile these dependents):")
            text += [f"    - T{n.id} {n.name}" for n in result.newly_stale]
        else:
            text.append("  no dependents to review.")
        _emit("\n".join(text), _dump(result), args.json)
    return 0


def _cmd_close(args) -> int:
    with _core_session() as core:
        result = core.close(args.id)
        text = ["closed " + _fmt_task(result.task), "  review obligations (one hop):"]
        text += _fmt_neighbors("depends on", result.dependencies)
        text += _fmt_neighbors("depended on by", result.dependents)
        _emit("\n".join(text), _dump(result), args.json)
    return 0


def _cmd_wont_do(args) -> int:
    with _core_session() as core:
        result = core.wont_do(args.id, reason=args.reason, delta=args.delta)
        text = [
            f"marked wont_do " + _fmt_task(result.task),
            f"  reason: {result.task.wont_do_reason}",
            "  review obligations (one hop):",
        ]
        text += _fmt_neighbors("depends on", result.dependencies)
        text += _fmt_neighbors("depended on by", result.dependents)
        _emit("\n".join(text), _dump(result), args.json)
    return 0


def _cmd_reopen(args) -> int:
    with _core_session() as core:
        t = core.reopen(args.id)
        _emit("reopened " + _fmt_task(t), _dump(t), args.json)
    return 0


def _cmd_reclassify(args) -> int:
    with _core_session() as core:
        result = core.reclassify(args.id, args.kind, delta=args.delta)
        text = [f"reclassified " + _fmt_task(result.task) + f" -> kind={result.task.kind}"]
        if result.newly_stale:
            text.append("  ⚠ now STALE (review/reconcile these dependents):")
            text += [f"    - T{n.id} {n.name}" for n in result.newly_stale]
        else:
            text.append("  no dependents to review.")
        _emit("\n".join(text), _dump(result), args.json)
    return 0


def _cmd_reconcile(args) -> int:
    with _core_session() as core:
        t = core.reconcile(args.id)
        _emit("reconciled (stale cleared) " + _fmt_task(t), _dump(t), args.json)
    return 0


def _cmd_link(args) -> int:
    with _core_session() as core:
        if args.link_action == "add":
            s = core.link_add(args.a, args.b, because=args.because, delta=args.delta)
            verb = "added"
        else:
            s = core.link_rm(args.a, args.b, delta=args.delta)
            verb = "removed"
        _emit(
            f"{verb} link T{args.a} <-> T{args.b}\n" + _fmt_slice(s),
            _dump(s),
            args.json,
        )
    return 0


def _cmd_label(args) -> int:
    with _core_session() as core:
        if args.label_action == "add":
            t = core.label_add(args.id, args.label)
            verb = "added"
        else:
            t = core.label_rm(args.id, args.label)
            verb = "removed"
        _emit(f"{verb} label '{args.label}' on " + _fmt_task(t), _dump(t), args.json)
    return 0


def _cmd_ls(args) -> int:
    with _core_session() as core:
        stale = True if args.stale else None
        tasks = core.ls(status=args.status, label=args.label, stale=stale)
        text = "\n".join(_fmt_task(t) for t in tasks) if tasks else "(no matching tasks)"
        _emit(text, [_dump(t) for t in tasks], args.json)
    return 0


def _cmd_stale(args) -> int:
    with _core_session() as core:
        tasks = core.stale_worklist()
        if tasks:
            text = "stale worklist (reconcile each, then it's empty):\n" + "\n".join(
                _fmt_task(t) for t in tasks
            )
        else:
            text = "stale worklist empty — reconciliation complete."
        _emit(text, [_dump(t) for t in tasks], args.json)
    return 0


def _cmd_render(args) -> int:
    with _core_session() as core:
        md = core.render(args.label)
        _emit(md, {"label": args.label, "markdown": md}, args.json)
    return 0


def _cmd_board(args) -> int:
    with _core_session() as core:
        stale = True if args.stale else None
        tasks = core.ls(status=args.status, label=args.label, stale=stale)
        if args.json:
            _emit("", [_dump(core.show(t.id)) for t in tasks], True)
            return 0
        allt = core.ls()
        n_open = sum(1 for t in allt if t.status == "open")
        n_done = sum(1 for t in allt if t.status == "closed")
        n_stale = sum(1 for t in allt if t.stale)
        head = _board_paint("tackit", ["38;5;154", "1"]) + f"   {n_open} open · {n_done} done · {n_stale} stale"
        body = _render_board(core, tasks)
        print(head + ("\n" + body if body else "\n(no matching tasks)"))
    return 0


def _cmd_labels(args) -> int:
    with _core_session() as core:
        infos = core.labels_summary()
        if infos:
            lines = []
            for i in infos:
                ex = "  " + " · ".join(i.samples) if i.samples else ""
                lines.append(f"{i.label}  ({i.count}){ex}")
            text = "\n".join(lines)
        else:
            text = "(no labels yet)"
        _emit(text, [_dump(i) for i in infos], args.json)
    return 0


def _cmd_history(args) -> int:
    with _core_session() as core:
        hist = core.history(args.id)
        st_text = "\n".join(
            f"  {r.changed_at}  {r.from_status or '(new)'} -> {r.to_status}"
            for r in hist.status_transitions
        ) or "  (no status transitions)"
        rev_text = "\n".join(
            f"  {r.edited_at}  edit -- {r.delta}"
            for r in hist.description_revisions
        ) or "  (no description revisions)"
        text = (
            f"T{args.id} history:\n"
            f"status transitions:\n{st_text}\n"
            f"description revisions (D29):\n{rev_text}"
        )
        _emit(text, _dump(hist), args.json)
    return 0


# --- D18 sync-management commands (bypass auto startup_sync) -----------------

def _cmd_status(args) -> int:
    info = sync.status(require_store())
    _emit(json.dumps(info, indent=2, default=str), info, args.json)
    return 0


def _cmd_export(args) -> int:
    store = require_store()
    v = sync.export(store)
    _emit(f"exported db -> {store.sql_path} (version {v}).", {"version": v}, args.json)
    return 0


def _cmd_import(args) -> int:
    store = require_store()
    msg = sync.import_sql(store, force=args.force)
    _emit(msg, {"message": msg}, args.json)
    return 0


def _cmd_restore(args) -> int:
    store = require_store()
    backups = sync.list_backups(store)
    if args.list or not args.backup:
        if not backups:
            _emit("(no backups)", [], args.json)
            return 0
        text = "\n".join(f"[{i}] {p.name}" for i, p in enumerate(backups))
        _emit("available backups:\n" + text, [p.name for p in backups], args.json)
        return 0
    # --backup accepts an index into the list or a filename
    chosen = None
    if args.backup.isdigit() and int(args.backup) < len(backups):
        chosen = backups[int(args.backup)]
    else:
        for p in backups:
            if p.name == args.backup:
                chosen = p
                break
    if chosen is None:
        _emit(f"no such backup: {args.backup}", {"error": "not found"}, args.json)
        return 1
    msg = sync.restore(store, chosen)
    _emit(msg, {"message": msg}, args.json)
    return 0


def _cmd_setup(args) -> int:
    from .setup_cmd import render_setup

    print(render_setup(Path.cwd()))
    return 0


def _cmd_mcp(args) -> int:
    from .mcp_server import run

    run()  # blocks serving stdio
    return 0


# --- argument parser (self-documenting via --help, design.md) ----------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="emit structured JSON output")

    p = argparse.ArgumentParser(
        prog="tackit",
        description="Deterministic task + dependency tracker for coding agents. "
        "Each command maps to a design slice (D#); see docs/plan/design.md.",
        parents=[common],
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, handler, help_text, parents=(common,)):
        sp = sub.add_parser(name, help=help_text, parents=list(parents))
        sp.set_defaults(func=handler)
        return sp

    add("init", _cmd_init, "create DB/schema + gitignore the .db (D1)")

    sp = add("add", _cmd_add, "create a task (D3)")
    sp.add_argument("name")
    sp.add_argument(
        "--kind",
        required=True,
        choices=list(("design", "schema", "production", "meta")),
        help="task kind (D26 / T94): design (decision slice), schema (store shape), "
        "production (alters running-app behavior), meta (bookkeeping/experiments). "
        "Classify by the 'alters app behavior' rule.",
    )
    sp.add_argument("--desc", default="", help="task description/body")
    sp.add_argument("--label", action="append", default=[], help="attach a label (repeatable, D4)")
    sp.add_argument("--dep", action="append", type=int, default=[], help="depends_on this id (repeatable, D5)")

    sp = add("search", _cmd_search, "ranked FTS keyword search -> ids (D17)")
    sp.add_argument("terms")

    sp = add("load", _cmd_load, "bulk-import a plan: [key] tasks + depends_on by key (D24)")
    sp.add_argument("file", nargs="?", help="plan file (omit to read stdin)")

    sp = add("show", _cmd_show, "slice fetch: task + deps + dependents + labels (D9)")
    sp.add_argument("id", type=int)

    sp = add("edit", _cmd_edit, "change a task -> stale its dependents (D13/D10)")
    sp.add_argument("id", type=int)
    sp.add_argument("--name")
    sp.add_argument("--desc")
    sp.add_argument(
        "--delta",
        required=True,
        help="One-sentence semantic-change description (T117). The cascade "
        "compares it against each linked task's `because` rationale; future-"
        "you reads it to decide what's relevant. Describe the SHIFT, not "
        "the bytes.",
    )

    sp = add("close", _cmd_close, "close (refused if stale) + print neighbors (D12/D14)")
    sp.add_argument("id", type=int)

    sp = add("reopen", _cmd_reopen, "closed -> open, logged (D7/D8); refused on wont_do (T132)")
    sp.add_argument("id", type=int)

    sp = add(
        "wont-do",
        _cmd_wont_do,
        "mark task as decided-not-to-do; locked forever, distinct from closed (T132)",
    )
    sp.add_argument("id", type=int)
    sp.add_argument(
        "--reason",
        required=True,
        help="durable rationale for not doing this task (persists forever)",
    )
    sp.add_argument(
        "--delta",
        required=True,
        help="one-sentence semantic-change description (T117)",
    )

    sp = add("reconcile", _cmd_reconcile, "clear stale without changing (reviewed-OK, D11)")
    sp.add_argument("id", type=int)

    sp = add(
        "reclassify",
        _cmd_reclassify,
        "change a task's kind (T128); refuses if it would create cross-kind link",
    )
    sp.add_argument("id", type=int)
    sp.add_argument(
        "--kind",
        required=True,
        choices=list(("design", "schema", "production", "meta")),
        help="new kind (D26 / T128)",
    )
    sp.add_argument(
        "--delta",
        required=True,
        help="one-sentence semantic-change description (T117 / T124)",
    )

    sp = add("link", _cmd_link, "add/remove a symmetric link (D5)")
    link_sub = sp.add_subparsers(dest="link_action", required=True)
    for act in ("add", "rm"):
        lsp = link_sub.add_parser(act, parents=[common])
        lsp.add_argument("a", type=int, metavar="A")
        lsp.add_argument("b", type=int, metavar="B")
        if act == "add":
            lsp.add_argument(
                "--because",
                required=True,
                help="WHY this pair is coupled (durable rationale, T116).",
            )
        lsp.add_argument(
            "--delta",
            required=True,
            help="One sentence describing what this op changes semantically "
            "(T117). Future-you will compare it against each linked "
            "task's --because to decide relevance.",
        )
        lsp.set_defaults(func=_cmd_link)

    sp = add("label", _cmd_label, "tag/untag a task (D4)")
    label_sub = sp.add_subparsers(dest="label_action", required=True)
    for act in ("add", "rm"):
        lsp = label_sub.add_parser(act, parents=[common])
        lsp.add_argument("id", type=int)
        lsp.add_argument("label")
        lsp.set_defaults(func=_cmd_label)

    sp = add("ls", _cmd_ls, "query/board: filter by status/label/stale (D15)")
    sp.add_argument("--status", choices=["open", "closed"])
    sp.add_argument("--label")
    sp.add_argument("--stale", action="store_true", help="only stale tasks")

    add("stale", _cmd_stale, "reconciliation worklist: all stale tasks (D11)")

    add("labels", _cmd_labels, "list all labels with usage: count + sample tasks (D21)")

    sp = add("board", _cmd_board, "rich board view (open/label/stale) with dependency edges (D22)")
    sp.add_argument("--status", choices=["open", "closed"])
    sp.add_argument("--label")
    sp.add_argument("--stale", action="store_true", help="only stale tasks")

    sp = add("render", _cmd_render, "narrative render of a label -> markdown (D16)")
    sp.add_argument("--label", required=True)

    sp = add("history", _cmd_history, "status transition history of a task (D8)")
    sp.add_argument("id", type=int)

    add("status", _cmd_status, "db version vs tackit.sql + sync verdict (D18)")
    add("export", _cmd_export, "force-dump .db -> tackit.sql (D18)")

    sp = add("import", _cmd_import, "adopt tackit.sql (backup + rebuild .db) (D18)")
    sp.add_argument("--force", action="store_true", help="adopt even if local db is newer")

    sp = add("restore", _cmd_restore, "restore .db from a rotating backup (D18)")
    sp.add_argument("--list", action="store_true", help="list available backups")
    sp.add_argument("--backup", help="backup index or filename to restore")

    add("setup", _cmd_setup, "emit post-install steps (agent-driven install)")
    add("mcp", _cmd_mcp, "launch the stdio MCP server (agent's primary door)")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except TackitError as exc:
        # design.md "Fail loud": surface the refusal cleanly, non-zero exit.
        print(f"tackit: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
