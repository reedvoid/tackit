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

from . import __version__, sync
from .core import Core, stale_alert_text
from .db import init_store, require_store
from .errors import TackitError
from .models import project_slice, project_task
from .plan import parse_plan


# --- human-readable formatters (──> CLI default output) ---------------------

def _flags(status: str, stale: bool) -> str:
    return f"{status}, STALE" if stale else status


def _fmt_task(t, include_description: bool = False) -> str:
    line = f"T{t.id} [{_flags(t.status, t.stale)}] {t.name}"
    if include_description and t.description.strip():
        line += f"\n  {t.description.strip()}"
    return line


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
    lines += _fmt_neighbors("links", s.links)
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
        if core.last_code_check_reminder:  # D31 v0.4 design/schema edit nudge
            print(core.last_code_check_reminder, file=sys.stderr)
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
            # T237: symmetric links -> one edge list, not duplicated needs/unblocks
            if s.links:
                edge = _board_paint("links→ ", [DIM]) + " ".join(f"T{n.id}" for n in s.links)
                lines.append("     " + edge)

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


def _dep_arg(value: str) -> tuple[int, str]:
    """D33 / T164 - parse `--dep ID::because` into (id, because). The `::`
    separator matches the plan format (plan.py); chosen because a single
    `:` is ambiguous with becauses containing colons. Empty becauses are
    refused at core.add() (D33 boundary). The argparse type runs at parse
    time, so a malformed `--dep` aborts before any DB touch."""
    if "::" not in value:
        raise argparse.ArgumentTypeError(
            f"--dep {value!r}: missing `::` separator. Expected "
            f"`<id>::<because rationale>` (D33 / T164). Example: "
            f"`--dep 5::'anchor invariant decides this task'`."
        )
    id_str, _, because = value.partition("::")
    try:
        dep_id = int(id_str.strip())
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--dep {value!r}: id {id_str.strip()!r} is not an integer."
        )
    return (dep_id, because.strip())


def _cmd_add(args) -> int:
    deps_dict: dict[int, str] | None = (
        {dep_id: because for dep_id, because in args.dep} if args.dep else None
    )
    with _core_session() as core:
        task = core.add(
            args.name,
            kind=args.kind,
            description=args.desc or "",
            labels=args.label,
            deps=deps_dict,
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


def _cmd_links_add(args) -> int:
    """T216: bulk-link EXISTING tasks. Reads edges from a file or stdin, one
    per line as `<a> <b> :: <because>` (a/b are id or prefixed-name)."""
    text = Path(args.file).read_text() if args.file else sys.stdin.read()
    edges = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "::" not in line:
            print(
                f"line {lineno}: expected `<a> <b> :: <because>` (got {line!r})",
                file=sys.stderr,
            )
            return 2
        endpoints, _, because = line.partition("::")
        parts = endpoints.split()
        if len(parts) != 2:
            print(
                f"line {lineno}: expected exactly two endpoints before `::` "
                f"(got {endpoints.strip()!r})",
                file=sys.stderr,
            )
            return 2
        edges.append({"a": parts[0], "b": parts[1], "because": because.strip()})
    with _core_session() as core:
        result = core.links_add(edges)
        out = (
            f"created {result['created']}, already_linked "
            f"{result['already_linked']}"
        )
        for a, b in result["created_pairs"]:
            out += f"\n  + {a} <-> {b}"
        _emit(out, result, args.json)
    return 0


def _cmd_search(args) -> int:
    with _core_session() as core:
        hits = core.search(args.terms, name_only=args.name_only)
        text = (
            "\n".join(f"T{h.id}  ({h.score:+.3f})  {h.name}" for h in hits)
            if hits
            else "(no matches)"
        )
        _emit(text, [_dump(h) for h in hits], args.json)
    return 0


def _cmd_links(args) -> int:
    with _core_session() as core:
        if args.ids:
            ids = args.ids
        else:
            ids = None
        if args.seen:
            seen = args.seen
        else:
            seen = None
        neighbors = core.links(ids=ids, already_seen=seen)
        if neighbors:
            lines = []
            for n in neighbors:
                lines.append(f"{n.prefixed_name} [{_flags(n.status, n.stale)}]")
            text = "\n".join(lines)
        else:
            text = "(no candidates)"
        payload = []
        for n in neighbors:
            payload.append(_dump(n))
        _emit(text, payload, args.json)
    return 0


def _cmd_edit(args) -> int:
    with _core_session() as core:
        result = core.edit(args.id, delta=args.delta, name=args.name, description=args.desc)
        text = ["edited " + _fmt_task(result.task)]
        if result.newly_stale:
            text.append("  ⚠ now STALE (review/reconcile these linked neighbors):")
            text += [f"    - T{n.id} {n.name}" for n in result.newly_stale]
        else:
            text.append("  no linked neighbors to review.")
        _emit("\n".join(text), _dump(result), args.json)
    return 0


def _cmd_edit_append(args) -> int:
    with _core_session() as core:
        result = core.edit_append(args.id, content=args.content, delta=args.delta)
        text = ["appended to " + _fmt_task(result.task)]
        if result.newly_stale:
            text.append("  ⚠ now STALE (review/reconcile these linked neighbors):")
            for n in result.newly_stale:
                text.append(f"    - T{n.id} {n.name}")
        else:
            text.append("  no linked neighbors to review.")
        _emit("\n".join(text), _dump(result), args.json)
    return 0


def _cmd_edit_replace(args) -> int:
    with _core_session() as core:
        result = core.edit_replace_substring(
            args.id,
            old_string=args.old,
            new_string=args.new,
            delta=args.delta,
        )
        text = ["replaced in " + _fmt_task(result.task)]
        if result.newly_stale:
            text.append("  ⚠ now STALE (review/reconcile these linked neighbors):")
            for n in result.newly_stale:
                text.append(f"    - T{n.id} {n.name}")
        else:
            text.append("  no linked neighbors to review.")
        _emit("\n".join(text), _dump(result), args.json)
    return 0


def _cmd_close(args) -> int:
    with _core_session() as core:
        result = core.close(args.id)
        text = ["closed " + _fmt_task(result.task), "  review obligations (one hop):"]
        text += _fmt_neighbors("links", result.links)
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
        text += _fmt_neighbors("links", result.links)
        _emit("\n".join(text), _dump(result), args.json)
    return 0


def _cmd_retire(args) -> int:
    with _core_session() as core:
        result = core.retire(args.id, reason=args.reason, delta=args.delta)
        text = [
            f"retired " + _fmt_task(result.task),
            f"  reason: {result.task.wont_do_reason}",
            "  review obligations (one hop):",
        ]
        text += _fmt_neighbors("links", result.links)
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
            text.append("  ⚠ now STALE (review/reconcile these linked neighbors):")
            text += [f"    - T{n.id} {n.name}" for n in result.newly_stale]
        else:
            text.append("  no linked neighbors to review.")
        _emit("\n".join(text), _dump(result), args.json)
    return 0


def _cmd_reconcile(args) -> int:
    with _core_session() as core:
        tasks = core.reconcile_many(args.ids)
        remaining = len(core.stale_worklist())
        reconciled_ids: list[int] = []
        id_strs: list[str] = []
        for t in tasks:
            reconciled_ids.append(t.id)
            id_strs.append(str(t.id))
        _emit(
            f"reconciled {len(tasks)} (stale cleared): {', '.join(id_strs)}; "
            f"remaining stale: {remaining}",
            {"reconciled": reconciled_ids, "remaining_stale": remaining},
            args.json,
        )
    return 0


def _cmd_link(args) -> int:
    with _core_session() as core:
        if args.link_action == "add":
            s = core.link_add(args.a, args.b, because=args.because)
            verb = "added"
        else:
            s = core.link_rm(args.a, args.b)
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
        tasks = core.ls(status=args.status, label=args.label, stale=stale,
                        kind=args.kind, name_prefix=args.name_prefix)
        if tasks:
            lines = []
            for t in tasks:
                lines.append(_fmt_task(t, args.include_description))
            text = "\n".join(lines)
        else:
            text = "(no matching tasks)"
        dumps = []
        for t in tasks:
            dumps.append(project_task(t, include_description=args.include_description))
        _emit(text, dumps, args.json)
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
        tasks = core.ls(status=args.status, label=args.label, stale=stale,
                        kind=args.kind, name_prefix=args.name_prefix)
        if args.json:
            cards = []
            for t in tasks:
                cards.append(project_slice(
                    core.show(t.id),
                    include_description=args.include_description,
                    include_neighbor_because=args.include_neighbor_because,
                ))
            _emit("", cards, True)
            return 0
        allt = core.ls()
        n_open = sum(1 for t in allt if t.status == "open")
        n_done = sum(1 for t in allt if t.status == "closed")
        n_wont = sum(1 for t in allt if t.status == "wont_do")
        n_stale = sum(1 for t in allt if t.stale)
        head = _board_paint("tackit", ["38;5;154", "1"]) + f"   {n_open} open · {n_done} done · {n_wont} wont_do · {n_stale} stale"
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
    if args.specs_only:
        # M187 / T193 spec-only mode: emit the spec-layer SQL dump.
        with _core_session() as core:
            text = core.export_specs_only()
        if args.output:
            from pathlib import Path
            Path(args.output).write_text(text)
            _emit(
                f"wrote spec-only dump -> {args.output} ({len(text)} bytes)",
                {"path": args.output, "bytes": len(text)},
                args.json,
            )
        else:
            # stdout: just print the SQL, no envelope -- caller redirects.
            print(text, end="")
        return 0
    # Default: D18 full-sync .db -> tackit.sql (legacy behavior).
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
    # action="version" fires during parse and exits 0 before the required-
    # subparser check, so `tackit --version` works with no subcommand (T195).
    p.add_argument(
        "--version",
        action="version",
        version=f"tackit {__version__}",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, handler, help_text, parents=(common,)):
        # Pass help_text as BOTH the short help (shown in parent --help) AND
        # the subcommand's description (shown by `tackit <name> --help`).
        # Without description=, the subcommand-level --help drops the prose.
        sp = sub.add_parser(
            name, help=help_text, description=help_text, parents=list(parents)
        )
        sp.set_defaults(func=handler)
        return sp

    add("init", _cmd_init, "create DB/schema + gitignore the .db (D1)")

    sp = add(
        "add",
        _cmd_add,
        "create a task (D3 + D36 + D37). Status defaults to 'spec' for "
        "design/schema, 'open' for production/meta. Aim for impl-ready "
        "granularity: a fresh-session agent should be able to implement "
        "the task from its description alone -- avoid vague verbs, "
        "conversation references, TBD/TODO placeholders, pointer-only "
        "bodies. A production task is REFUSED at creation with zero "
        "design/schema --dep links (D256). D276 -- create spec + production "
        "tasks only once the decision is SETTLED; keep unsettled work in a "
        "meta scratchpad first.",
    )
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
    sp.add_argument(
        "--dep",
        action="append",
        type=_dep_arg,
        default=[],
        help="depends_on (repeatable, D5 + D33): `<id>::<because>` per edge; "
        "every link requires a real one-sentence coupling rationale (T164).",
    )

    sp = add(
        "search",
        _cmd_search,
        "ranked FTS keyword search -> ids (D17). M181 #8d: --name-only "
        "scopes the match to the name column (FTS5 {name}: filter) when "
        "you want to look up tasks by distinctive title phrase without "
        "description hits adding noise.",
    )
    sp.add_argument("terms")
    sp.add_argument(
        "--name-only",
        action="store_true",
        help="scope match to the name column only (no description hits).",
    )

    sp = add(
        "links",
        _cmd_links,
        "deterministic link-discovery (D27): no ids -> the design+schema "
        "anchor layer; ids... -> their depth-1 neighborhood (viable targets "
        "only, status in open/spec). Prefer over search for wiring deps.",
    )
    sp.add_argument("ids", type=int, nargs="*", help="seed ids; omit for the anchor layer")
    sp.add_argument("--seen", type=int, nargs="*", default=[],
                    help="ids already judged (excluded from the next hop)")

    sp = add("load", _cmd_load, "bulk-import a plan atomically: [key] tasks with "
             "multi-paragraph desc + depends_on by key (D24/D40). Prefer over N adds. "
             "A production task with zero design/schema depends_on is REFUSED, "
             "rolling back the whole plan (D256). D276 -- a plan presumes "
             "SETTLED decisions; keep exploring work in a meta scratchpad.")
    sp.add_argument("file", nargs="?", help="plan file (omit to read stdin)")

    sp = add("links-add", _cmd_links_add, "bulk-link EXISTING tasks atomically "
             "(D213/T216): edges from a file or stdin, one per line as "
             "`<a> <b> :: <because>` (a/b are id or prefixed-name). Validate-all-"
             "first; already-linked edges are benign no-ops.")
    sp.add_argument("file", nargs="?", help="edges file (omit to read stdin)")

    sp = add("show", _cmd_show, "slice fetch: task + links (symmetric) + labels (D9)")
    sp.add_argument("id", type=int)

    sp = add(
        "edit",
        _cmd_edit,
        "change a task -> stale its dependents (D13/D10 + D36 + D37). Use "
        "edit for ALL partial changes including major rewrites. If a design/"
        "schema slice's premise is completely gone, use retire() instead. Edit is REFUSED on closed/wont_do (D259 -- reopen a closed task first); on production, edit is correction/scope-shrink only (D255) and even that is a smell of thin prep (D277). If impl"
        "reveals under-defined details, edit() is the mechanism to fold "
        "them back BEFORE close -- closing with an out-of-date description "
        "destroys granularity for future readers. **Edits aren't free** -- "
        "fires the cascade depth-1 + pressures the close-gate; make edits "
        "consequential and necessary (substantive impact). See SKILL.md "
        "\"Edits aren't free\" for the discipline.",
    )
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

    sp = add(
        "edit-append",
        _cmd_edit_append,
        "T179: diff-shaped edit -- append `content` to a task's description "
        "without retransmitting the whole body. Fires the cascade depth-1 "
        "and writes the description_revisions audit row exactly like edit(). "
        "META-ONLY: refused on design/schema (D250) and production (D255) -- only meta appends; also refused on closed/wont_do (D259). Refused on empty / whitespace-only content. Cuts large-body edit"
        "cost ~10x. **Edits aren't free** -- fires the cascade depth-1 + "
        "pressures the close-gate; make edits consequential and necessary "
        "(substantive impact). Diff-shape cuts transmission cost, not "
        "cascade cost. See SKILL.md \"Edits aren't free\" for the discipline.",
    )
    sp.add_argument("id", type=int)
    sp.add_argument(
        "--content",
        required=True,
        help="text to append to the task's description (non-empty, "
        "non-whitespace-only).",
    )
    sp.add_argument(
        "--delta",
        required=True,
        help="One-sentence semantic-change description (T117).",
    )

    sp = add(
        "edit-replace",
        _cmd_edit_replace,
        "T179: diff-shaped edit -- replace the exact substring `--old` with "
        "`--new` in a task's description. Refused if `--old` is empty, not "
        "found, or appears multiple times (caller adds context to "
        "disambiguate), or the task is closed/wont_do (D259 terminal-immutability). Empty`--new` is a legitimate deletion. Cuts "
        "large-body edit cost ~10x. **Edits aren't free** -- fires the "
        "cascade depth-1 + pressures the close-gate; make edits "
        "consequential and necessary (substantive impact). Diff-shape cuts "
        "transmission cost, not cascade cost. See SKILL.md \"Edits aren't "
        "free\" for the discipline.",
    )
    sp.add_argument("id", type=int)
    sp.add_argument(
        "--old",
        required=True,
        help="exact substring to find in the description (must be unique).",
    )
    sp.add_argument(
        "--new",
        required=True,
        help="replacement text (may be empty -- that's a deletion).",
    )
    sp.add_argument(
        "--delta",
        required=True,
        help="One-sentence semantic-change description (T117).",
    )

    sp = add(
        "close",
        _cmd_close,
        "close (refused if stale/linked-stale, on status='spec', or already "
        "closed/wont_do -- no double-decide) + print neighbors "
        "(D12 / D14 / D36). For production+meta only. Use edit() to refine "
        "a design/schema decision; retire() if completely abandoned.",
    )
    sp.add_argument("id", type=int)

    sp = add(
        "reopen",
        _cmd_reopen,
        "closed -> open, logged (D7/D8). Refused on wont_do (T132) and "
        "retired (D36) -- both terminal forever; file a fresh D# if the "
        "decision returned.",
    )
    sp.add_argument("id", type=int)

    sp = add(
        "wont-do",
        _cmd_wont_do,
        "mark task as decided-not-to-do; locked forever, distinct from "
        "closed (T132). For production+meta only -- design/schema use "
        "retire() (D36).",
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

    sp = add(
        "retire",
        _cmd_retire,
        "retire a design/schema slice: spec -> retired (D36). Use ONLY when "
        "the decision is completely gone with NO replacement -- partial changes "
        "use edit(). Refused on open neighbors (resolve via link_rm + "
        "wont_do or link_rm alone first).",
    )
    sp.add_argument("id", type=int)
    sp.add_argument(
        "--reason",
        required=True,
        help="durable rationale for retiring this decision (persists "
        "forever). Placeholders (empty / 'TBD' / 'TODO' / 'obsolete' / "
        "'no longer needed') refused per D33 extension.",
    )
    sp.add_argument(
        "--delta",
        required=True,
        help="one-sentence semantic-change description (T117)",
    )

    sp = add(
        "reconcile",
        _cmd_reconcile,
        "clear stale on one or more ids without changing them (reviewed-OK, "
        "D11 + D39). Batch via an explicit id list (one transaction); any "
        "terminal-status id (closed/wont_do/retired) refuses the whole batch "
        "-- stale on those is record-only archaeology (D28 + D36). No "
        "'reconcile all' form by design (D39 guard-rail: that would automate "
        "the judgment the cascade depends on).",
    )
    sp.add_argument("ids", type=int, nargs="+")

    sp = add(
        "reclassify",
        _cmd_reclassify,
        "change a task's kind (D26/T128); refuses a cross-kind meta link "
        "(meta-island) or a cross-partition move with no clean status target "
        "(D36; e.g. closed-production -> design). open<->spec auto-shifts.",
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

    sp = add(
        "link",
        _cmd_link,
        "add/remove a symmetric link (D5). link add refused if either "
        "endpoint has status='retired' (D36): retired specs accept no new "
        "edges.",
    )
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
        lsp.set_defaults(func=_cmd_link)

    sp = add("label", _cmd_label, "tag/untag a task (D4)")
    label_sub = sp.add_subparsers(dest="label_action", required=True)
    for act in ("add", "rm"):
        lsp = label_sub.add_parser(act, parents=[common])
        lsp.add_argument("id", type=int)
        lsp.add_argument("label")
        lsp.set_defaults(func=_cmd_label)

    sp = add("ls", _cmd_ls, "query/board: filter by status/label/stale/kind; "
             "lean by default, no description (D15/D211)")
    sp.add_argument("--status", choices=["open", "closed", "wont_do", "spec", "retired"])
    sp.add_argument("--label")
    sp.add_argument("--stale", action="store_true", help="only stale tasks")
    sp.add_argument("--kind", choices=["design", "schema", "production", "meta"],
                    help="filter by kind (D211)")
    sp.add_argument("--name-prefix",
                    help="literal case-sensitive name prefix, e.g. '§9.1' (T220)")
    sp.add_argument("--include-description", action="store_true",
                    help="include full task bodies (D211; default lean)")

    add("stale", _cmd_stale, "reconciliation worklist: all stale tasks (D11)")

    add("labels", _cmd_labels, "list all labels with usage: count + sample tasks (D21)")

    sp = add("board", _cmd_board, "rich board view (status/label/stale/kind) with "
             "dependency edges; lean by default (D22/D211)")
    sp.add_argument("--status", choices=["open", "closed", "wont_do", "spec", "retired"])
    sp.add_argument("--label")
    sp.add_argument("--stale", action="store_true", help="only stale tasks")
    sp.add_argument("--kind", choices=["design", "schema", "production", "meta"],
                    help="filter by kind (D211)")
    sp.add_argument("--name-prefix",
                    help="literal case-sensitive name prefix, e.g. '§9.1' (T220)")
    sp.add_argument("--include-description", action="store_true",
                    help="include full task bodies in --json (D211; default lean)")
    sp.add_argument("--include-neighbor-because", action="store_true",
                    help="include neighbor edge rationales in --json (D211; default lean)")

    sp = add("render", _cmd_render, "narrative render of a label -> markdown (D16)")
    sp.add_argument("--label", required=True)

    sp = add("history", _cmd_history, "status transition history of a task (D8)")
    sp.add_argument("id", type=int)

    add("status", _cmd_status, "db version vs tackit.sql + sync verdict (D18)")
    sp = add(
        "export",
        _cmd_export,
        "force-dump .db -> tackit.sql (D18 full sync) by default. With "
        "--specs-only: emit a spec-layer-only SQL dump (design + schema "
        "slices + their labels + spec-to-spec links + audit rows) per "
        "M187 / T193 -- the disaster-recovery artifact for the dogfood. "
        "stdout by default; redirect with > or pass --output FILE.",
    )
    sp.add_argument(
        "--specs-only",
        action="store_true",
        help="emit only the spec layer (design + schema slices) per M187 "
        "design. Excludes production/meta task rows + FTS index + meta "
        "table. Output is consumable by `tackit import` against a freshly "
        "initialized store.",
    )
    sp.add_argument(
        "--output",
        type=str,
        default=None,
        help="output file path (--specs-only only). Default: stdout.",
    )

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
