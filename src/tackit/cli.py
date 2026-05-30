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
from pathlib import Path

from . import sync
from .core import Core
from .db import init_store, require_store
from .errors import TackitError


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
    core = Core.open()
    try:
        task = core.add(args.name, description=args.desc or "", labels=args.label, deps=args.dep)
        _emit("created " + _fmt_slice(core.show(task.id)), _dump(core.show(task.id)), args.json)
    finally:
        core.close_conn()
    return 0


def _cmd_show(args) -> int:
    core = Core.open()
    try:
        s = core.show(args.id)
        _emit(_fmt_slice(s), _dump(s), args.json)
    finally:
        core.close_conn()
    return 0


def _cmd_search(args) -> int:
    core = Core.open()
    try:
        hits = core.search(args.terms)
        text = (
            "\n".join(f"T{h.id}  ({h.score:+.3f})  {h.name}" for h in hits)
            if hits
            else "(no matches)"
        )
        _emit(text, [_dump(h) for h in hits], args.json)
    finally:
        core.close_conn()
    return 0


def _cmd_edit(args) -> int:
    core = Core.open()
    try:
        result = core.edit(args.id, name=args.name, description=args.desc)
        text = ["edited " + _fmt_task(result.task)]
        if result.newly_stale:
            text.append("  ⚠ now STALE (review/reconcile these dependents):")
            text += [f"    - T{n.id} {n.name}" for n in result.newly_stale]
        else:
            text.append("  no dependents to review.")
        _emit("\n".join(text), _dump(result), args.json)
    finally:
        core.close_conn()
    return 0


def _cmd_close(args) -> int:
    core = Core.open()
    try:
        result = core.close(args.id)
        text = ["closed " + _fmt_task(result.task), "  review obligations (one hop):"]
        text += _fmt_neighbors("depends on", result.dependencies)
        text += _fmt_neighbors("depended on by", result.dependents)
        _emit("\n".join(text), _dump(result), args.json)
    finally:
        core.close_conn()
    return 0


def _cmd_reopen(args) -> int:
    core = Core.open()
    try:
        t = core.reopen(args.id)
        _emit("reopened " + _fmt_task(t), _dump(t), args.json)
    finally:
        core.close_conn()
    return 0


def _cmd_reconcile(args) -> int:
    core = Core.open()
    try:
        t = core.reconcile(args.id)
        _emit("reconciled (stale cleared) " + _fmt_task(t), _dump(t), args.json)
    finally:
        core.close_conn()
    return 0


def _cmd_dep(args) -> int:
    core = Core.open()
    try:
        if args.dep_action == "add":
            s = core.dep_add(args.from_task, args.to_task)
            verb = "added"
        else:
            s = core.dep_rm(args.from_task, args.to_task)
            verb = "removed"
        _emit(
            f"{verb} edge T{args.from_task} depends_on T{args.to_task}\n" + _fmt_slice(s),
            _dump(s),
            args.json,
        )
    finally:
        core.close_conn()
    return 0


def _cmd_label(args) -> int:
    core = Core.open()
    try:
        if args.label_action == "add":
            t = core.label_add(args.id, args.label)
            verb = "added"
        else:
            t = core.label_rm(args.id, args.label)
            verb = "removed"
        _emit(f"{verb} label '{args.label}' on " + _fmt_task(t), _dump(t), args.json)
    finally:
        core.close_conn()
    return 0


def _cmd_ls(args) -> int:
    core = Core.open()
    try:
        stale = True if args.stale else None
        tasks = core.ls(status=args.status, label=args.label, stale=stale)
        text = "\n".join(_fmt_task(t) for t in tasks) if tasks else "(no matching tasks)"
        _emit(text, [_dump(t) for t in tasks], args.json)
    finally:
        core.close_conn()
    return 0


def _cmd_stale(args) -> int:
    core = Core.open()
    try:
        tasks = core.stale_worklist()
        if tasks:
            text = "stale worklist (reconcile each, then it's empty):\n" + "\n".join(
                _fmt_task(t) for t in tasks
            )
        else:
            text = "stale worklist empty — reconciliation complete."
        _emit(text, [_dump(t) for t in tasks], args.json)
    finally:
        core.close_conn()
    return 0


def _cmd_render(args) -> int:
    core = Core.open()
    try:
        md = core.render(args.label)
        _emit(md, {"label": args.label, "markdown": md}, args.json)
    finally:
        core.close_conn()
    return 0


def _cmd_history(args) -> int:
    core = Core.open()
    try:
        rows = core.history(args.id)
        text = "\n".join(
            f"  {r.changed_at}  {r.from_status or '(new)'} -> {r.to_status}" for r in rows
        )
        _emit(f"status history of T{args.id}:\n{text}", [_dump(r) for r in rows], args.json)
    finally:
        core.close_conn()
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
    sp.add_argument("--desc", default="", help="task description/body")
    sp.add_argument("--label", action="append", default=[], help="attach a label (repeatable, D4)")
    sp.add_argument("--dep", action="append", type=int, default=[], help="depends_on this id (repeatable, D5)")

    sp = add("search", _cmd_search, "ranked FTS keyword search -> ids (D17)")
    sp.add_argument("terms")

    sp = add("show", _cmd_show, "slice fetch: task + deps + dependents + labels (D9)")
    sp.add_argument("id", type=int)

    sp = add("edit", _cmd_edit, "change a task -> stale its dependents (D13/D10)")
    sp.add_argument("id", type=int)
    sp.add_argument("--name")
    sp.add_argument("--desc")

    sp = add("close", _cmd_close, "close (refused if stale) + print neighbors (D12/D14)")
    sp.add_argument("id", type=int)

    sp = add("reopen", _cmd_reopen, "closed -> open, logged (D7/D8)")
    sp.add_argument("id", type=int)

    sp = add("reconcile", _cmd_reconcile, "clear stale without changing (reviewed-OK, D11)")
    sp.add_argument("id", type=int)

    sp = add("dep", _cmd_dep, "add/remove a depends_on edge (D5)")
    dep_sub = sp.add_subparsers(dest="dep_action", required=True)
    for act in ("add", "rm"):
        dsp = dep_sub.add_parser(act, parents=[common])
        dsp.add_argument("from_task", type=int, metavar="A")
        dsp.add_argument("to_task", type=int, metavar="B")
        dsp.set_defaults(func=_cmd_dep)

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
