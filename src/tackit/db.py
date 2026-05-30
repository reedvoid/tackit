"""D1 - Persistent task store.

design.md D1: all state in a single local SQLite file (WAL mode); found by
walking up from cwd. design.md "Interface - CLI": the DB lives at
``.tackit/tackit.db`` (binary, gitignored); ``tackit.sql`` (committed text) sits
alongside; backups live under ``.tackit/backups/``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .errors import NotFoundError, TackitError
from .schema import ALL_DDL, SCHEMA_VERSION

DIR_NAME = ".tackit"
DB_NAME = "tackit.db"
SQL_NAME = "tackit.sql"  # D18: git-canonical text dump, committed
BACKUPS_DIR = "backups"  # D18: rotating pre-override snapshots

# .tackit/.gitignore: commit the .sql text dump, ignore the binary db + backups.
GITIGNORE_BODY = (
    "# tackit: the binary store is local; the committed source of truth is "
    "tackit.sql (D18).\n"
    "tackit.db\n"
    "tackit.db-wal\n"
    "tackit.db-shm\n"
    "backups/\n"
)


@dataclass(frozen=True)
class Store:
    """Resolved paths for one tackit store rooted at ``root`` (the dir that
    contains the ``.tackit/`` folder)."""

    root: Path

    @property
    def dir(self) -> Path:
        return self.root / DIR_NAME

    @property
    def db_path(self) -> Path:
        return self.dir / DB_NAME

    @property
    def sql_path(self) -> Path:
        return self.dir / SQL_NAME

    @property
    def backups_dir(self) -> Path:
        return self.dir / BACKUPS_DIR


def discover_root(start: Path | None = None) -> Store | None:
    """Walk up from ``start`` (default cwd) looking for an existing ``.tackit/``
    dir. Returns the Store or None if no store is found above cwd."""
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / DIR_NAME).is_dir():
            return Store(root=candidate)
    return None


def require_store(start: Path | None = None) -> Store:
    """Like discover_root but fails loud if there is no store -- the agent must
    run ``tackit init`` first."""
    store = discover_root(start)
    if store is None:
        raise NotFoundError(
            "no tackit store found (looked for a .tackit/ dir from cwd upward). "
            "Run `tackit init` to create one."
        )
    return store


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with WAL (D1) and foreign-key enforcement (D14 FK
    invariant) on, returning rows as dict-like ``sqlite3.Row``."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    _ensure_fts5(conn)
    return conn


def _ensure_fts5(conn: sqlite3.Connection) -> None:
    """Fail loud (design.md "Fail loud") if this SQLite build lacks FTS5, since
    search (D17) and the FTS triggers (S5) depend on it."""
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _tackit_fts_probe USING fts5(x);")
        conn.execute("DROP TABLE IF EXISTS _tackit_fts_probe;")
    except sqlite3.OperationalError as exc:
        raise TackitError(
            "this SQLite build lacks the FTS5 extension, which tackit requires "
            f"for search (D17/S5): {exc}"
        ) from exc


def init_store(root: Path | None = None) -> Store:
    """D1 - create ``.tackit/`` with a fresh schema and gitignore the binary db.

    Idempotent: running it on an existing store re-applies the (IF NOT EXISTS)
    DDL and rewrites the gitignore, without touching data.
    """
    store = Store(root=(root or Path.cwd()).resolve())
    store.dir.mkdir(parents=True, exist_ok=True)
    store.backups_dir.mkdir(parents=True, exist_ok=True)
    (store.dir / ".gitignore").write_text(GITIGNORE_BODY)

    conn = connect(store.db_path)
    try:
        for ddl in ALL_DDL:
            conn.executescript(ddl)
        # Seed meta (S6) on a fresh db; leave existing values alone (D18).
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('version', '0');"
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('synced_sql_hash', '');"
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?);",
            (SCHEMA_VERSION,),
        )
    finally:
        conn.close()
    return store
