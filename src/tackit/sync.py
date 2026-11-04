"""D18 - Git-tracked compressed serialization + safe DB<->dump sync.

The git-canonical form is a deterministic SQL dump, committed as row-chunked gzip
(``tackit.sql.gz``, T301); the binary ``tackit.db`` is gitignored and local. Two
markers in ``meta`` (S6) govern sync:

  * ``version``         - monotonic generation counter, +1 per mutation. The
                          *ordering* signal ("which is newer"). Embedded in the
                          dump (as a meta row) so the disk file carries its Vsql.
  * ``synced_sql_hash`` - sha256 of the decompressed text of the last dump *this
                          db produced*. The *integrity* signal. Local-only:
                          deliberately NOT written into the dumped text (that
                          would be circular), so the hash of the on-disk file's
                          text lines up with what's stored.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import sqlite3
import threading
import zlib
from pathlib import Path

from . import schema
from .db import Store, connect
from .errors import SyncError

MAX_BACKUPS = 20  # design.md D18: "rotating backup (last ~20)"

# D18 compression format: gzip container, level 1, full flush on rows whose crc32
# is divisible by 8, so unchanged runs of rows compress to identical bytes.
_DUMP_COMPRESSION_LEVEL = 1
_GZIP_WBITS = 31
_FLUSH_ROW_MODULUS = 8
_ROW_BOUNDARY = b"\nINSERT INTO "

# Tables serialized into the dump, in a dependency-safe, deterministic order.
# tasks_fts (S5) is intentionally excluded -- it is derived content, rebuilt by
# the S5 triggers when the task rows are re-inserted on import.
_DUMP_TABLES = [
    ("tasks", "id"),
    ("task_labels", "task_id, label"),
    ("links", "task_a, task_b"),
    ("status_transitions", "id"),
    ("description_revisions", "id"),
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
    """Produce the deterministic dump text from the current db state.

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


def compress_dump(text: str) -> bytes:
    """T301 / D18: gzip ``text``, full-flushing at content-defined row boundaries.
    A flush resets the compressor, so the bytes for a run of rows depend only on
    those rows, and git can delta one version of the dump against the next."""
    data = text.encode("utf-8")
    compressor = zlib.compressobj(_DUMP_COMPRESSION_LEVEL, zlib.DEFLATED, _GZIP_WBITS)
    parts: list[bytes] = []
    chunk_start = 0
    row_start = data.find(_ROW_BOUNDARY)
    while row_start != -1:
        next_row = data.find(_ROW_BOUNDARY, row_start + 1)
        row_end = len(data)
        if next_row != -1:
            row_end = next_row
        if zlib.crc32(data[row_start:row_end]) % _FLUSH_ROW_MODULUS == 0:
            parts.append(compressor.compress(data[chunk_start:row_start]))
            parts.append(compressor.flush(zlib.Z_FULL_FLUSH))
            chunk_start = row_start
        row_start = next_row
    parts.append(compressor.compress(data[chunk_start:]))
    parts.append(compressor.flush())
    return b"".join(parts)


def read_dump(path: Path) -> str:
    """Return the decompressed dump text, refusing a damaged file loudly (D18): a
    dump that can't be read is never treated as absent or empty."""
    raw = path.read_bytes()
    if not raw:
        raise SyncError(f"{path} is empty, not a gzip dump; restore it from git.")
    try:
        return gzip.decompress(raw).decode("utf-8")
    except (gzip.BadGzipFile, EOFError, zlib.error, UnicodeDecodeError) as exc:
        raise SyncError(f"{path} is damaged, not a readable gzip dump ({exc}); restore it from git.") from exc


def write_dump(path: Path, text: str) -> None:
    """Write the compressed dump atomically (temp file + os.replace) so a crash
    mid-write never leaves a partial dump. The temp name is unique per writer
    because adopt_legacy_dump runs outside any db lock, so two sessions opening
    a store during its first upgrade can write at once."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_bytes(compress_dump(text))
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _read_legacy_text(store: Store) -> str | None:
    """The text of ``tackit.sql``, or None if there is none."""
    legacy_path = store.legacy_sql_path
    try:
        legacy_bytes = legacy_path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        return legacy_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SyncError(f"{legacy_path} is not valid UTF-8: {exc}") from exc


def ensure_temp_files_ignored(store: Store) -> None:
    """T302 / D18: a store created before ``write_dump``'s temp files has no ignore
    rule for them, and a crash can strand one. Bytes, because the file is the user's
    and may not be UTF-8. A store with no .gitignore is left without one."""
    path = store.dir / ".gitignore"
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return
    if b"*.tmp" in content.splitlines():
        return
    addition = b"*.tmp\n"
    if content and not content.endswith((b"\n", b"\r")):
        addition = b"\n" + addition
    with path.open("ab") as handle:
        handle.write(addition)


def refuse_diverged_legacy(store: Store) -> None:
    """T303 / D18: refuse, changing nothing, a lone ``tackit.sql`` that the sync
    decision would refuse anyway. Converting first would delete a tracked file and
    add an untracked gz, which then blocks switching back to the branch that tracks
    the gz. The sync commands skip this: running them is the resolution."""
    if store.sql_path.exists() or not store.db_path.exists():
        return
    legacy_text = _read_legacy_text(store)
    if legacy_text is None:
        return
    conn = connect(store.db_path)
    try:
        stored_hash = get_synced_hash(conn)
        vdb = get_version(conn)
    finally:
        conn.close()
    if _hash_text(legacy_text) == stored_hash:
        return
    vsql = parse_version_from_sql(legacy_text)
    if vsql > vdb:
        return
    raise SyncError(
        f"tackit.sql (V{vsql}) diverged from tackit.db (V{vdb}) and is NOT strictly "
        f"newer, as after checking out a branch from before tackit.sql.gz. Nothing was "
        f"changed. Switch to the branch holding the current dump, or resolve explicitly: "
        f"`tackit import --force` to adopt tackit.sql (backs up the .db first), or "
        f"`tackit export` to write the .db out."
    )


def adopt_legacy_dump(store: Store) -> str:
    """T301 / D18 legacy adoption: bring a text ``tackit.sql`` into the gz file,
    removing it only once the gz durably holds its content. Returns what it did.

      * no legacy file ............... nothing
      * legacy only .................. convert
      * both, identical text ......... remove legacy (recovers an interrupted run)
      * both, legacy strictly newer .. adopt legacy (an older tackit wrote it)
      * both, anything else .......... refuse; neither is safe to discard

    Each path that removes the legacy file first adds the temp-file ignore rule
    (T302), so an interrupted run retries it.
    """
    legacy_path = store.legacy_sql_path
    legacy_text = _read_legacy_text(store)
    if legacy_text is None:
        return "no-legacy"
    legacy_version = parse_version_from_sql(legacy_text)

    # missing_ok below: another session opening the same store may finish first.
    if not store.sql_path.exists():
        ensure_temp_files_ignored(store)
        write_dump(store.sql_path, legacy_text)
        legacy_path.unlink(missing_ok=True)
        return "converted-legacy"

    current_text = read_dump(store.sql_path)
    if current_text == legacy_text:
        ensure_temp_files_ignored(store)
        legacy_path.unlink(missing_ok=True)
        return "removed-identical-legacy"

    current_version = parse_version_from_sql(current_text)
    if legacy_version > current_version:
        ensure_temp_files_ignored(store)
        write_dump(store.sql_path, legacy_text)
        legacy_path.unlink(missing_ok=True)
        return "adopted-newer-legacy"

    raise SyncError(
        f"tackit.sql (V{legacy_version}) conflicts with tackit.sql.gz "
        f"(V{current_version}) and is not strictly newer -- refusing to discard "
        f"either. Delete tackit.sql to keep tackit.sql.gz, or delete tackit.sql.gz "
        f"to adopt tackit.sql."
    )


def finalize_mutation(conn: sqlite3.Connection, store: Store) -> None:
    """D18 "after every mutation": bump ``version``, re-dump ``tackit.sql.gz``
    (embedding the new version + content), record ``synced_sql_hash``.

    Called by ``core`` inside each mutating op's transaction so the db writes
    (version + hash) commit atomically with the data change; the file is written
    immediately before COMMIT. (If COMMIT then fails, the file is merely *ahead*
    -- Vsql > Vdb -- which startup_sync recovers as a clean rebuild from the dump.)
    """
    _set_meta(conn, "version", str(get_version(conn) + 1))
    text = dump_text(conn)
    _set_meta(conn, "synced_sql_hash", _hash_text(text))
    write_dump(store.sql_path, text)


def export(store: Store) -> int:
    """D18 ``tackit export`` - force-dump the current ``.db`` to ``tackit.sql.gz``
    WITHOUT bumping version (it is not a mutation; it just re-emits current
    state). Used to resolve the "db ahead of disk" ambiguity. Returns version."""
    adopt_legacy_dump(store)
    conn = connect(store.db_path)
    try:
        conn.execute("BEGIN")
        text = dump_text(conn)
        _set_meta(conn, "synced_sql_hash", _hash_text(text))
        write_dump(store.sql_path, text)
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
        raise SyncError(f"the dump is not a valid tackit dump: {exc}") from exc
    finally:
        tmp.close()


def rebuild_db_from_sql(store: Store) -> None:
    """Replace ``tackit.db`` with a fresh db built from ``tackit.sql.gz``, then
    record synced_sql_hash so the rebuilt db is marked in-sync with the file.
    Removes WAL/SHM sidecars so no stale pages survive the swap. The dump is read
    first, so a damaged one refuses before the db is touched."""
    sql_text = read_dump(store.sql_path)
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

    Decision table (db has Vdb + last hash; disk tackit.sql.gz has embedded Vsql +
    a hash computed over its decompressed text):
      * no .db yet (fresh clone) ............... build it from the dump
      * hash(dump text) == synced_sql_hash ..... in sync, trust the .db
      * Vsql > Vdb ............................. strictly newer (a pull): backup
                                                 + rebuild .db from the dump
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

    disk_text = read_dump(store.sql_path)
    if _hash_text(disk_text) == stored_hash:
        return "in-sync"

    vsql = parse_version_from_sql(disk_text)
    if vsql > vdb:
        backup_db(store)
        rebuild_db_from_sql(store)
        return "pulled-newer-sql"

    raise SyncError(
        f"tackit.sql.gz diverged from tackit.db and is NOT strictly newer "
        f"(Vsql={vsql}, Vdb={vdb}) -- refusing to auto-clobber. "
        f"Resolve explicitly: `tackit import --force` to adopt tackit.sql.gz "
        f"(backs up the .db first), or `tackit export` to write the .db out."
    )


def status(store: Store) -> dict:
    """D18 ``tackit status`` - report db version vs disk version and the sync
    verdict, without changing anything (so a legacy text dump is only reported)."""
    db_exists = store.db_path.exists()
    sql_exists = store.sql_path.exists()
    legacy_exists = store.legacy_sql_path.exists()
    info: dict = {
        "db_exists": db_exists,
        "sql_exists": sql_exists,
        "legacy_sql_exists": legacy_exists,
    }
    if db_exists:
        conn = connect(store.db_path)
        try:
            info["db_version"] = get_version(conn)
            info["synced_sql_hash"] = get_synced_hash(conn)
        finally:
            conn.close()
    if sql_exists:
        disk_text = read_dump(store.sql_path)
        info["sql_version"] = parse_version_from_sql(disk_text)
        info["sql_hash"] = _hash_text(disk_text)

    if legacy_exists:
        info["verdict"] = (
            "legacy text tackit.sql present; the next op converts it to "
            "tackit.sql.gz, or refuses if it conflicts with tackit.sql.gz or tackit.db"
        )
    elif not db_exists and sql_exists:
        info["verdict"] = "no local db; `tackit import` (or any op) will build it"
    elif db_exists and not sql_exists:
        info["verdict"] = "no tackit.sql.gz; run `tackit export` to create it"
    elif db_exists and sql_exists:
        if info.get("synced_sql_hash") == info.get("sql_hash"):
            info["verdict"] = "in sync"
        elif info["sql_version"] > info["db_version"]:
            info["verdict"] = "tackit.sql.gz is newer (a pull); next op rebuilds the db"
        else:
            info["verdict"] = "DIVERGED/ambiguous; resolve with import/export"
    else:
        info["verdict"] = "no store contents"
    return info


def _refuse_import_over_newer_db(store: Store, vsql: int, dump_name: str) -> None:
    if not store.db_path.exists():
        return
    conn = connect(store.db_path)
    try:
        vdb = get_version(conn)
    finally:
        conn.close()
    if vdb > vsql:
        raise SyncError(
            f"refusing import: local db (V{vdb}) is newer than {dump_name} "
            f"(V{vsql}); this would discard local work. `tackit export` to "
            f"keep it, or `tackit import --force` to discard and adopt the file."
        )


def import_sql(store: Store, force: bool = False) -> str:
    """D18 ``tackit import`` - adopt ``tackit.sql.gz`` (backup the current .db, then
    rebuild from the file). This is the explicit resolution for the ambiguous /
    merge-collision cases startup_sync refuses to guess at.

    Without ``force`` it still refuses when the db is strictly newer than the
    file (Vdb > Vsql) -- that would discard local work; the agent must pass
    ``--force`` to confirm, having presumably exported or accepted the loss.
    """
    # T303: check a lone legacy dump before converting it, so a refusal changes no file.
    if not force and not store.sql_path.exists():
        legacy_text = _read_legacy_text(store)
        if legacy_text is not None:
            _refuse_import_over_newer_db(store, parse_version_from_sql(legacy_text), "tackit.sql")
    adopt_legacy_dump(store)
    if not store.sql_path.exists():
        raise SyncError("no tackit.sql.gz to import.")
    disk_text = read_dump(store.sql_path)
    vsql = parse_version_from_sql(disk_text)

    if not force:
        _refuse_import_over_newer_db(store, vsql, "tackit.sql.gz")
    backup = backup_db(store)
    rebuild_db_from_sql(store)
    return f"imported tackit.sql.gz (V{vsql}); previous db backed up to {backup}"


def restore(store: Store, backup_path: Path) -> str:
    """D18 ``tackit restore`` - bring a rotating backup back as the live ``.db``,
    snapshotting the current db first so a restore is itself reversible."""
    if not backup_path.exists():
        raise SyncError(f"backup not found: {backup_path}")
    adopt_legacy_dump(store)
    backup_db(store)  # snapshot current before overwriting
    for suffix in ("-wal", "-shm"):
        p = Path(str(store.db_path) + suffix)
        if p.exists():
            p.unlink()
    store.db_path.write_bytes(backup_path.read_bytes())
    # the restored db's view of "synced" may differ from disk; re-export so the
    # file matches the now-live db (keeps version as-is; not a mutation).
    export(store)
    return f"restored {backup_path.name}; re-exported tackit.sql.gz to match"
