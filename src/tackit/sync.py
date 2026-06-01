"""D18 - Git-tracked text serialization + safe DB<->SQL sync.

design.md D18. The git-canonical form is a deterministic SQL text dump
(``tackit.sql``, committed); the binary ``tackit.db`` is gitignored and local.
Two markers in ``meta`` (S6) govern sync:

  * ``version``         - monotonic generation counter, +1 per mutation. The
                          *ordering* signal ("which is newer"). Embedded in the
                          dump (as a meta row) so the disk file carries its Vsql.
  * ``synced_sql_hash`` - sha256 of the last dump *this db produced*. The
                          *integrity* signal. Local-only: deliberately NOT written
                          into the dumped text (that would be circular), so the
                          hash of the on-disk file lines up with what's stored.

Resolves issue #9 (binary-in-git): commits/PRs are reviewable, mergeable text.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

from . import schema
from .db import Store, connect
from .errors import SyncError

MAX_BACKUPS = 20  # design.md D18: "rotating backup (last ~20)"

# Tables serialized into the dump, in a dependency-safe, deterministic order.
# tasks_fts (S5) is intentionally excluded -- it is derived content, rebuilt by
# the S5 triggers when the task rows are re-inserted on import.
_DUMP_TABLES = [
    ("tasks", "id"),
    ("task_labels", "task_id, label"),
    ("links", "task_a, task_b"),
    ("status_transitions", "id"),
]


# --- serialization ----------------------------------------------------------

def _sql_literal(value) -> str:
    """Render a Python value as a SQLite literal for the text dump."""
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def dump_text(conn: sqlite3.Connection) -> str:
    """Produce the deterministic ``tackit.sql`` text from the current db state.

    Self-contained: schema DDL (so the file rebuilds standalone) followed by
    ordered INSERTs. Excludes the local-only ``synced_sql_hash`` meta row. The
    ``version`` meta row IS included, so the file carries its own Vsql.
    """
    version = get_version(conn)
    lines: list[str] = [
        "-- tackit store (D18 git-canonical serialization). DO NOT hand-edit;",
        "-- regenerated on every mutation. Binary tackit.db is gitignored.",
        f"-- version: {version}",
        "PRAGMA foreign_keys = OFF;",
        "BEGIN;",
    ]
    # Schema first, so a fresh checkout rebuilds by executing this file alone.
    for ddl in schema.ALL_DDL:
        lines.append(ddl.strip())

    for table, order_by in _DUMP_TABLES:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY {order_by}").fetchall()
        for row in rows:
            cols = row.keys()
            vals = ", ".join(_sql_literal(row[c]) for c in cols)
            collist = ", ".join(cols)
            lines.append(f"INSERT INTO {table} ({collist}) VALUES ({vals});")

    # meta last, excluding the local-only integrity hash (see module docstring).
    meta_rows = conn.execute(
        "SELECT key, value FROM meta WHERE key <> 'synced_sql_hash' ORDER BY key"
    ).fetchall()
    for row in meta_rows:
        lines.append(
            f"INSERT INTO meta (key, value) VALUES "
            f"({_sql_literal(row['key'])}, {_sql_literal(row['value'])});"
        )

    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- meta accessors (S6) ----------------------------------------------------

def get_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
    return int(row[0]) if row else 0


def get_synced_hash(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key = 'synced_sql_hash'").fetchone()
    return row[0] if row else ""


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
        (key, value),
    )


def _write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp file + os.replace) so a crash
    mid-write never leaves a partially-parseable dump."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def finalize_mutation(conn: sqlite3.Connection, store: Store) -> None:
    """D18 "after every mutation": bump ``version``, re-dump ``tackit.sql``
    (embedding the new version + content), record ``synced_sql_hash``.

    Called by ``core`` inside each mutating op's transaction so the db writes
    (version + hash) commit atomically with the data change; the file is written
    immediately before COMMIT. (If COMMIT then fails, the file is merely *ahead*
    -- Vsql > Vdb -- which startup_sync recovers as a clean rebuild-from-.sql.)
    """
    _set_meta(conn, "version", str(get_version(conn) + 1))
    text = dump_text(conn)
    _set_meta(conn, "synced_sql_hash", _hash_text(text))
    _write_atomic(store.sql_path, text)


def export(store: Store) -> int:
    """D18 ``tackit export`` - force-dump the current ``.db`` to ``tackit.sql``
    WITHOUT bumping version (it is not a mutation; it just re-emits current
    state). Used to resolve the "db ahead of disk" ambiguity. Returns version."""
    conn = connect(store.db_path)
    try:
        conn.execute("BEGIN")
        text = dump_text(conn)
        _set_meta(conn, "synced_sql_hash", _hash_text(text))
        _write_atomic(store.sql_path, text)
        conn.execute("COMMIT")
        return get_version(conn)
    finally:
        conn.close()


# --- db<->sql rebuild + backups ---------------------------------------------

def parse_version_from_sql(sql_text: str) -> int:
    """Vsql: build a throwaway in-memory db from the dump and read meta.version.
    Robust against format drift -- no fragile line parsing."""
    tmp = sqlite3.connect(":memory:")
    try:
        tmp.executescript(sql_text)
        row = tmp.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error as exc:
        raise SyncError(f"tackit.sql is not a valid tackit dump: {exc}") from exc
    finally:
        tmp.close()


def rebuild_db_from_sql(store: Store) -> None:
    """Replace ``tackit.db`` with a fresh db built from ``tackit.sql``, then
    record synced_sql_hash so the rebuilt db is marked in-sync with the file.
    Removes WAL/SHM sidecars so no stale pages survive the swap."""
    sql_text = store.sql_path.read_text()
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(store.db_path) + suffix)
        if p.exists():
            p.unlink()
    conn = connect(store.db_path)
    try:
        conn.executescript(sql_text)
        # mark the freshly-built db as in-sync with the file it came from
        conn.execute("BEGIN")
        _set_meta(conn, "synced_sql_hash", _hash_text(sql_text))
        if conn.execute("SELECT 1 FROM meta WHERE key='schema_version'").fetchone() is None:
            _set_meta(conn, "schema_version", schema.SCHEMA_VERSION)
        conn.execute("COMMIT")
    finally:
        conn.close()


def backup_db(store: Store) -> Path | None:
    """D18 - snapshot the current ``.db`` into ``backups/`` before an override,
    keeping the last MAX_BACKUPS. Returns the backup path (None if no db yet)."""
    if not store.db_path.exists():
        return None
    store.backups_dir.mkdir(parents=True, exist_ok=True)
    import time

    stamp = time.strftime("%Y%m%d-%H%M%S")
    # include a counter so multiple backups in the same second don't collide
    n = 0
    while True:
        suffix = "" if n == 0 else f"-{n}"
        dest = store.backups_dir / f"tackit-{stamp}{suffix}.db"
        if not dest.exists():
            break
        n += 1
    dest.write_bytes(store.db_path.read_bytes())
    _rotate_backups(store)
    return dest


def _rotate_backups(store: Store) -> None:
    backups = sorted(store.backups_dir.glob("tackit-*.db"))
    excess = len(backups) - MAX_BACKUPS
    for old in backups[:max(0, excess)]:
        old.unlink()


def list_backups(store: Store) -> list[Path]:
    if not store.backups_dir.exists():
        return []
    return sorted(store.backups_dir.glob("tackit-*.db"))


# --- startup sync decision (D18 state machine) ------------------------------

def startup_sync(store: Store) -> str:
    """Run the D18 startup sync decision and leave a valid, in-sync ``.db`` on
    disk (or raise SyncError on the ambiguous cases). Returns a short status
    string describing what it did.

    Decision table (db has Vdb + last hash; disk tackit.sql has embedded Vsql +
    computed hash):
      * no .db yet (fresh clone) ............... build it from tackit.sql
      * hash(tackit.sql) == synced_sql_hash .... in sync, trust the .db
      * Vsql > Vdb ............................. strictly newer (a pull): backup
                                                 + rebuild .db from tackit.sql
      * Vsql < Vdb, or Vsql == Vdb w/ diff ..... AMBIGUOUS: refuse, route to
                                                 import/export (SyncError)
    """
    sql_exists = store.sql_path.exists()
    db_exists = store.db_path.exists()

    if not db_exists and not sql_exists:
        return "fresh"  # nothing committed yet; init created the db already
    if not db_exists:
        rebuild_db_from_sql(store)
        return "built-from-sql"
    if not sql_exists:
        # db exists but was never exported (or the .sql was deleted): emit it.
        export(store)
        return "exported-missing-sql"

    conn = connect(store.db_path)
    try:
        stored_hash = get_synced_hash(conn)
        vdb = get_version(conn)
    finally:
        conn.close()

    disk_text = store.sql_path.read_text()
    if _hash_text(disk_text) == stored_hash:
        return "in-sync"

    vsql = parse_version_from_sql(disk_text)
    if vsql > vdb:
        backup_db(store)
        rebuild_db_from_sql(store)
        return "pulled-newer-sql"

    raise SyncError(
        f"tackit.sql diverged from tackit.db and is NOT strictly newer "
        f"(Vsql={vsql}, Vdb={vdb}) -- refusing to auto-clobber. "
        f"Resolve explicitly: `tackit import --force` to adopt tackit.sql "
        f"(backs up the .db first), or `tackit export` to write the .db out."
    )


def status(store: Store) -> dict:
    """D18 ``tackit status`` - report db version vs disk version and the sync
    verdict, without changing anything."""
    db_exists = store.db_path.exists()
    sql_exists = store.sql_path.exists()
    info: dict = {"db_exists": db_exists, "sql_exists": sql_exists}
    if db_exists:
        conn = connect(store.db_path)
        try:
            info["db_version"] = get_version(conn)
            info["synced_sql_hash"] = get_synced_hash(conn)
        finally:
            conn.close()
    if sql_exists:
        disk_text = store.sql_path.read_text()
        info["sql_version"] = parse_version_from_sql(disk_text)
        info["sql_hash"] = _hash_text(disk_text)

    if not db_exists and sql_exists:
        info["verdict"] = "no local db; `tackit import` (or any op) will build it"
    elif db_exists and not sql_exists:
        info["verdict"] = "no tackit.sql; run `tackit export` to create it"
    elif db_exists and sql_exists:
        if info.get("synced_sql_hash") == info.get("sql_hash"):
            info["verdict"] = "in sync"
        elif info["sql_version"] > info["db_version"]:
            info["verdict"] = "tackit.sql is newer (a pull); next op rebuilds the db"
        else:
            info["verdict"] = "DIVERGED/ambiguous; resolve with import/export"
    else:
        info["verdict"] = "no store contents"
    return info


def import_sql(store: Store, force: bool = False) -> str:
    """D18 ``tackit import`` - adopt ``tackit.sql`` (backup the current .db, then
    rebuild from the file). This is the explicit resolution for the ambiguous /
    merge-collision cases startup_sync refuses to guess at.

    Without ``force`` it still refuses when the db is strictly newer than the
    file (Vdb > Vsql) -- that would discard local work; the agent must pass
    ``--force`` to confirm, having presumably exported or accepted the loss.
    """
    if not store.sql_path.exists():
        raise SyncError("no tackit.sql to import.")
    disk_text = store.sql_path.read_text()
    vsql = parse_version_from_sql(disk_text)

    if store.db_path.exists() and not force:
        conn = connect(store.db_path)
        try:
            vdb = get_version(conn)
        finally:
            conn.close()
        if vdb > vsql:
            raise SyncError(
                f"refusing import: local db (V{vdb}) is newer than tackit.sql "
                f"(V{vsql}); this would discard local work. `tackit export` to "
                f"keep it, or `tackit import --force` to discard and adopt the file."
            )
    backup = backup_db(store)
    rebuild_db_from_sql(store)
    return f"imported tackit.sql (V{vsql}); previous db backed up to {backup}"


def restore(store: Store, backup_path: Path) -> str:
    """D18 ``tackit restore`` - bring a rotating backup back as the live ``.db``,
    snapshotting the current db first so a restore is itself reversible."""
    if not backup_path.exists():
        raise SyncError(f"backup not found: {backup_path}")
    backup_db(store)  # snapshot current before overwriting
    for suffix in ("-wal", "-shm"):
        p = Path(str(store.db_path) + suffix)
        if p.exists():
            p.unlink()
    store.db_path.write_bytes(backup_path.read_bytes())
    # the restored db's view of "synced" may differ from disk; re-export so the
    # file matches the now-live db (keeps version as-is; not a mutation).
    export(store)
    return f"restored {backup_path.name}; re-exported tackit.sql to match"
