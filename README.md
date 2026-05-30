# tackit

A deterministic task + dependency tracker **for coding agents**. One local SQLite
file is the single source of truth for a project's build plan — its tasks, their
dependencies, and their reconciliation state. An agent fetches small *slices* on
demand instead of re-reading monolithic plan documents, so project truth survives
across sessions and context-window compaction, and a change to one task can be
traced to everything that depends on it.

Strict Pydantic validation at the boundary (malformed data is refused, not stored),
a manual dirty-propagation discipline (editing a task marks its dependents *stale*;
a stale task can't be closed until reconciled), and full-text search over tasks via
SQLite FTS5. Exposed two ways over one core: an **MCP server** (the agent's primary
door) and a **CLI** (debugging / scripting / fallback).

> **Status: alpha (0.1.0).** The data model, interfaces, and sync design are
> settled and implemented; expect rough edges.

## Install

```bash
pip install tackit && tackit setup
```

`tackit setup` doesn't touch your config — it *emits* the post-install steps with
contextualized paths (the MCP registration snippet, where to drop the bundled
`SKILL.md`, and `tackit init`) for the driving agent to carry out.

## Quickstart (CLI)

```bash
tackit init                                   # create .tackit/ in this project
tackit add "parse FTS5 query" --label search  # create a task (D3)
tackit add "rank search results" --dep 1      # task 2 depends_on task 1
tackit search "fts"                           # ranked keyword search (D17)
tackit show 2                                 # slice: task + deps + dependents
tackit edit 1 --desc "tokenized MATCH"        # edits stale task 1's dependents
tackit stale                                  # the reconciliation worklist
tackit reconcile 2                            # reviewed-OK; clear stale
tackit close 2                                # refused while stale (D14)
tackit ls --status open                       # query/board (D15)
tackit --help                                 # full, self-documenting surface
```

The store lives at `.tackit/tackit.db` (binary, gitignored). Its git-canonical form
is a deterministic SQL text dump, `.tackit/tackit.sql`, re-written on every mutation
and committed — so diffs and merges are reviewable text, never a binary blob. Sync
between the two is automatic; `tackit status` / `export` / `import` / `restore`
exist only for the divergence cases the auto-sync deliberately refuses to guess at.

## MCP

```bash
tackit mcp     # serve the stdio MCP server (the agent's primary door)
```

Tool names are the bare verbs (`add`, `show`, `search`, `edit`, `close`,
`reconcile`, `dep_add`, …); their input schemas are generated from the Python type
hints, so they can't drift from the real interface. Each mutating tool returns the
agent's review obligations in its result.

## License

MIT
