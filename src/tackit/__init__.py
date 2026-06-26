"""tackit - a deterministic task + dependency tracker for coding agents.

Authoritative design lives in ``docs/plan/`` (local, gitignored):
``design.md`` (slices D1-D18) and ``schema.md`` (tables S1-S6). Code in this
package is tagged with those ids so any line can be traced back to the slice or
table it implements -- grep ``D12`` or ``S3`` to find the relevant code.

Module -> design map:
  errors.py       fail-loud exception hierarchy (D2, D14)
  models.py       D2  typed validation boundary (Pydantic models)
  schema.py       S1-S6 SQLite DDL + FTS5 triggers
  db.py           D1  persistent WAL store + path discovery
  core.py         D3-D17 operations (the single determinism home)
  sync.py         D18 git-text serialization + safe DB<->SQL sync
  cli.py          CLI adapter (thin) + `tackit setup`/`mcp` entry points
  mcp_server.py   MCP stdio adapter (thin; schema auto-generated from type hints)
"""

__version__ = "0.8.5"
