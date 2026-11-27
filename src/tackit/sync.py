"""D18 - Git-tracked compressed serialization + safe DB<->dump sync.

The git-canonical form is a deterministic SQL dump, committed as row-chunked gzip
(``tackit.sql.gz``, T301); the binary ``tackit.db`` is gitignored and local. Two
markers in ``meta`` (S6) govern sync:

  * ``version``         - monotonic generation counter, +1 per mutation. The
                          *ordering* signal ("which is newer"). Embedded in the
                          dump (as a meta row) so the disk file carries its Vsql.
  * ``synced_sql_hash`` - the chained per-chunk hash (``dump_hash``, D379) of the
                          decompressed text of the last dump *this db produced*
                          (an untagged value is the older whole-text sha256).
                          The *integrity* signal. Local-only:
                          deliberately NOT written into the dumped text (that
                          would be circular), so the hash of the on-disk file's
                          text lines up with what's stored.
"""

from __future__ import annotations

import bisect
import gzip
import hashlib
import io
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

# Tables serialized into the dump, in a dependency-safe, deterministic order
# (schema.DUMPED_TABLES, the one home of the order), then meta's dumped keys.
# tasks_fts (S5) is intentionally excluded -- it is derived content, rebuilt by
# the S5 triggers when the task rows are re-inserted on import.
_DUMP_ORDER = tuple(schema.DUMPED_TABLES) + (("meta", ("key",)),)
_META_INDEX = len(schema.DUMPED_TABLES)

# Local-only meta keys (S42): never written into the dump text.
LOCAL_META_KEYS = ("synced_sql_hash", "dump_index_stat", "synced_sql_stat")


# --- serialization ----------------------------------------------------------

def _sql_literal(value) -> str:
    """Render a Python value as a SQLite literal for the text dump."""
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _header_lines(conn: sqlite3.Connection) -> list[str]:
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
    return lines


def _tail_lines() -> list[str]:
    # D379 / S381: post-row DDL after every row, so loading the dump records nothing.
    lines: list[str] = []
    for statement in schema.POST_ROW_DDL:
        lines.append(statement.strip())
    lines.append("COMMIT;")
    return lines


def _row_lines(conn: sqlite3.Connection, start=None, end=None):
    """Yield ``(position, line)`` for each dumped row in dump order, where
    position is ``(table index,) + key``; with ``start <= position < end`` when
    either bound is given. A bound of one element means "the start of that table".
    """
    for index, (table, keys) in enumerate(_DUMP_ORDER):
        if start is not None and index < start[0]:
            continue
        if end is not None and index > end[0]:
            break
        if end is not None and index == end[0] and len(end) == 1:
            break
        where: list[str] = []
        params: list = []
        if table == "meta":
            where.append(f"key NOT IN ({', '.join('?' * len(LOCAL_META_KEYS))})")
            params.extend(LOCAL_META_KEYS)
        columns = ", ".join(keys)
        marks = ", ".join("?" * len(keys))
        if start is not None and index == start[0] and len(start) > 1:
            where.append(f"({columns}) >= ({marks})")
            params.extend(start[1:])
        if end is not None and index == end[0] and len(end) > 1:
            where.append(f"({columns}) < ({marks})")
            params.extend(end[1:])
        sql = f"SELECT * FROM {table}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {columns}"
        for row in conn.execute(sql, params):
            names = row.keys()
            values = ", ".join(_sql_literal(row[name]) for name in names)
            key = []
            for name in keys:
                key.append(row[name])
            yield (index,) + tuple(key), f"INSERT INTO {table} ({', '.join(names)}) VALUES ({values});"


def _region_bytes(conn, start, end, first: bool, last: bool):
    """D379: the dump text of one contiguous run of chunks, as utf-8 bytes, with
    the offset of each row it holds. ``first`` adds the header and DDL; ``last``
    adds the post-row DDL and COMMIT. Joined in order, the runs of a whole store
    are byte-identical to dump_text."""
    parts: list[bytes] = []
    positions: list[tuple[int, tuple]] = []
    length = 0
    if first:
        head = "\n".join(_header_lines(conn)).encode("utf-8")
        parts.append(head)
        length = len(head)
    for position, line in _row_lines(conn, start, end):
        encoded = ("\n" + line).encode("utf-8")
        positions.append((length, position))
        parts.append(encoded)
        length += len(encoded)
    if last:
        parts.append(("\n" + "\n".join(_tail_lines()) + "\n").encode("utf-8"))
    return b"".join(parts), positions


def dump_text(conn: sqlite3.Connection) -> str:
    """Produce the deterministic dump text from the current db state: the full
    build, and the reference the incremental write must match (D379).

    Self-contained: schema DDL (so the file rebuilds standalone), ordered INSERTs,
    then the post-row DDL. Excludes the local-only meta keys. The ``version`` meta
    row IS included, so the file carries its own Vsql.
    """
    data, _ = _region_bytes(conn, None, None, True, True)
    return data.decode("utf-8")


_CHAINED_HASH_TAG = "c1:"


def chunk_cuts(data: bytes) -> list[int]:
    """D60/D379: the byte offsets where the dump's chunks start, plus the end.

    A chunk starts at a row boundary (``\\nINSERT INTO ``) whose row, up to the
    next boundary, has a crc32 divisible by 8, so a chunk's extent depends only on
    its own rows. The first chunk (header and DDL) starts at 0. Zero-length
    chunks are never produced."""
    cuts = [0]
    row_start = data.find(_ROW_BOUNDARY)
    while row_start != -1:
        next_row = data.find(_ROW_BOUNDARY, row_start + 1)
        row_end = len(data)
        if next_row != -1:
            row_end = next_row
        if row_start > cuts[-1] and zlib.crc32(data[row_start:row_end]) % _FLUSH_ROW_MODULUS == 0:
            cuts.append(row_start)
        row_start = next_row
    cuts.append(len(data))
    return cuts


def _chained_digest(digests) -> str:
    chain = hashlib.sha256()
    for digest in digests:
        chain.update(digest)
    return _CHAINED_HASH_TAG + chain.hexdigest()


def dump_hash(text: str) -> str:
    """D379: the integrity hash of dump text -- sha256 over the ordered sha256
    digests of its chunks, tagged ``c1:``. It covers the decompressed text, so a
    recompression never reads as divergence (D60)."""
    data = text.encode("utf-8")
    cuts = chunk_cuts(data)
    digests = []
    for start, end in zip(cuts, cuts[1:]):
        digests.append(hashlib.sha256(data[start:end]).digest())
    return _chained_digest(digests)


def hash_matches(stored: str, text: str) -> bool:
    """D379: compare a stored synced_sql_hash with dump text. A tagged value is the
    chained hash; an untagged one is the whole-text sha256 written before D379, so
    a store from before the change still opens in sync."""
    if stored.startswith(_CHAINED_HASH_TAG):
        return stored == dump_hash(text)
    if not stored:
        return False
    return stored == hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def compress_member(chunk: bytes) -> bytes:
    """One standard gzip member holding ``chunk`` (D60): its bytes depend only on
    the chunk, so an unchanged chunk compresses identically in every version."""
    compressor = zlib.compressobj(_DUMP_COMPRESSION_LEVEL, zlib.DEFLATED, _GZIP_WBITS)
    return compressor.compress(chunk) + compressor.flush()


def compress_dump(text: str) -> bytes:
    """T301 / D60 / D379: gzip ``text`` as one member per content-defined chunk, so
    runs of unchanged rows compress to identical bytes whatever precedes them and
    git can delta one version of the dump against the next. Any gunzip reads a
    multi-member file. Empty text is one empty member."""
    data = text.encode("utf-8")
    cuts = chunk_cuts(data)
    parts: list[bytes] = []
    for start, end in zip(cuts, cuts[1:]):
        parts.append(compress_member(data[start:end]))
    if not parts:
        parts.append(compress_member(b""))
    return b"".join(parts)


def read_dump(path: Path) -> str:
    """Return the decompressed dump text, refusing a damaged file loudly (D18): a
    dump that can't be read is never treated as absent or empty."""
    raw = path.read_bytes()
    if not raw:
        raise SyncError(f"{path} is empty, not a gzip dump; restore it from git.")
    try:
        # D379: a dump is many gzip members; GzipFile reads them in one linear
        # pass, while gzip.decompress re-slices the rest of the buffer per member
        # (15 s against 0.4 s on a 5,868-member, 58 MB dump).
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            return stream.read().decode("utf-8")
    except (gzip.BadGzipFile, EOFError, zlib.error, UnicodeDecodeError) as exc:
        raise SyncError(f"{path} is damaged, not a readable gzip dump ({exc}); restore it from git.") from exc


def write_dump(path: Path, text: str) -> None:
    """Write the compressed dump atomically (temp file + os.replace) so a crash
    mid-write never leaves a partial dump. The temp name is unique per writer
    because adopt_legacy_dump runs outside any db lock, so two sessions opening
    a store during its first upgrade can write at once."""
    write_dump_bytes(path, compress_dump(text))


def write_dump_bytes(path: Path, data: bytes) -> None:
    """The atomic write under write_dump, for bytes already compressed (D379)."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_bytes(data)
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
    if hash_matches(stored_hash, legacy_text):
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
    chunks = _load_chunk_index(conn, store)
    if chunks is None:
        _write_full(conn, store)
    else:
        _write_incremental(conn, store, chunks)


# --- D379: incremental dump write ---------------------------------------------

def _stat_key(path: Path) -> str:
    """The file identity D379 and D60 compare: size, mtime_ns and inode."""
    info = path.stat()
    return f"{info.st_size}:{info.st_mtime_ns}:{info.st_ino}"


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return row[0]


def _has_index_tables(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
        "AND name IN ('dump_chunks', 'dump_changes')"
    ).fetchone()
    return row[0] == 2


def _position_from_index(row) -> tuple | None:
    if row["tbl"] is None:
        return None
    keys = _DUMP_ORDER[row["tbl"]][1]
    if len(keys) == 1:
        return (row["tbl"], row["k1"])
    return (row["tbl"], row["k1"], row["k2"])


def _load_chunk_index(conn: sqlite3.Connection, store: Store) -> list[dict] | None:
    """S380: the chunk index, or None when it does not describe the file on disk
    (missing, empty, or its stat key differs: a pull, import, restore, rebuild,
    legacy adoption or migration), in which case the write is a full build."""
    if not store.sql_path.exists():
        return None
    if not _has_index_tables(conn):
        return None
    if _get_meta(conn, "dump_index_stat") != _stat_key(store.sql_path):
        return None
    chunks = []
    for row in conn.execute("SELECT * FROM dump_chunks ORDER BY seq"):
        chunks.append({
            "position": _position_from_index(row),
            "offset": row["byte_offset"],
            "length": row["byte_length"],
            "digest": row["digest"],
        })
    if not chunks:
        return None
    return chunks


def _chunks_of_region(data: bytes, positions, first: bool) -> list[dict]:
    """Split one region's bytes into chunks by the D60 cut rule, each with its
    first row position, gzip member and digest."""
    position_at = {}
    for offset, position in positions:
        position_at[offset] = position
    cuts = chunk_cuts(data)
    chunks = []
    for start, end in zip(cuts, cuts[1:]):
        piece = data[start:end]
        if start == 0 and first:
            position = None
        else:
            position = position_at[start]
        chunks.append({
            "position": position,
            "member": compress_member(piece),
            "digest": hashlib.sha256(piece).digest(),
        })
    return chunks


def _commit_dump(conn: sqlite3.Connection, store: Store, chunks: list[dict], data: bytes) -> None:
    """Write the assembled file, then record its hash, its chunk index and its
    stat, and empty the change log, all inside the caller's transaction."""
    write_dump_bytes(store.sql_path, data)
    digests = []
    for chunk in chunks:
        digests.append(chunk["digest"])
    _set_meta(conn, "synced_sql_hash", _chained_digest(digests))
    _set_meta(conn, "synced_sql_stat", _stat_key(store.sql_path))  # D60 open fast path
    if not _has_index_tables(conn):
        return  # a db not yet migrated to D379's tables (an old backup, a pre-migration export)
    conn.execute("DELETE FROM dump_chunks")
    rows = []
    offset = 0
    for seq, chunk in enumerate(chunks):
        position = chunk["position"]
        tbl = k1 = k2 = None
        if position is not None:
            tbl = position[0]
            k1 = position[1]
            if len(position) > 2:
                k2 = position[2]
        rows.append((seq, tbl, k1, k2, offset, chunk["length"], chunk["digest"]))
        offset += chunk["length"]
    conn.executemany(
        "INSERT INTO dump_chunks(seq, tbl, k1, k2, byte_offset, byte_length, digest) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.execute("DELETE FROM dump_changes")
    _set_meta(conn, "dump_index_stat", _stat_key(store.sql_path))


def _write_full(conn: sqlite3.Connection, store: Store) -> None:
    """D379: the full build -- every row serialized, every chunk compressed."""
    data, positions = _region_bytes(conn, None, None, True, True)
    chunks = _chunks_of_region(data, positions, True)
    members = []
    for chunk in chunks:
        members.append(chunk["member"])
        chunk["length"] = len(chunk["member"])
    _commit_dump(conn, store, chunks, b"".join(members))


def _write_incremental(conn: sqlite3.Connection, store: Store, old: list[dict]) -> None:
    """D379: rebuild only the chunks holding changed rows (plus the header chunk
    and the meta tail, which change on every write) and copy every other chunk's
    member from the current file. Each run of rebuilt chunks starts at a row that
    did not change, so its cut decision -- which depends only on that row -- is
    the same as in a full build, and the result is byte-identical to one."""
    firsts = []
    for chunk in old[1:]:
        firsts.append(chunk["position"])

    def chunk_of(position):
        return bisect.bisect_right(firsts, position)

    affected = {0}
    meta_chunk = chunk_of((_META_INDEX,))
    for index in range(max(meta_chunk - 1, 0), len(old)):
        affected.add(index)
    for row in conn.execute("SELECT DISTINCT tbl, k1, k2 FROM dump_changes"):
        if len(_DUMP_ORDER[row["tbl"]][1]) == 1:
            position = (row["tbl"], row["k1"])
        else:
            position = (row["tbl"], row["k1"], row["k2"])
        index = chunk_of(position)
        affected.add(index)
        if index > 0:
            affected.add(index - 1)

    runs = []
    for index in sorted(affected):
        if runs and runs[-1][1] == index - 1:
            runs[-1][1] = index
        else:
            runs.append([index, index])

    current = store.sql_path.read_bytes()
    chunks: list[dict] = []
    members: list[bytes] = []
    next_index = 0
    for run_start, run_end in runs:
        for index in range(next_index, run_start):
            chunk = old[index]
            members.append(current[chunk["offset"]:chunk["offset"] + chunk["length"]])
            chunks.append(chunk)
        first = run_start == 0
        last = run_end == len(old) - 1
        start = old[run_start]["position"]
        end = None
        if not last:
            end = old[run_end + 1]["position"]
        data, positions = _region_bytes(conn, start, end, first, last)
        for chunk in _chunks_of_region(data, positions, first):
            chunk["length"] = len(chunk["member"])
            members.append(chunk["member"])
            chunks.append(chunk)
        next_index = run_end + 1
    for index in range(next_index, len(old)):
        chunk = old[index]
        members.append(current[chunk["offset"]:chunk["offset"] + chunk["length"]])
        chunks.append(chunk)
    _commit_dump(conn, store, chunks, b"".join(members))


def export(store: Store) -> int:
    """D18 ``tackit export`` - force-dump the current ``.db`` to ``tackit.sql.gz``
    WITHOUT bumping version (it is not a mutation; it just re-emits current
    state). Used to resolve the "db ahead of disk" ambiguity. Returns version."""
    adopt_legacy_dump(store)
    conn = connect(store.db_path)
    try:
        conn.execute("BEGIN")
        _write_full(conn, store)
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
        _set_meta(conn, "synced_sql_hash", dump_hash(sql_text))
        _set_meta(conn, "synced_sql_stat", _stat_key(store.sql_path))  # D60 open fast path
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
        stored_stat = _get_meta(conn, "synced_sql_stat")
    finally:
        conn.close()

    # D60 / D379 fast path: the file is the one this db last wrote or verified.
    disk_stat = _stat_key(store.sql_path)
    if stored_stat == disk_stat:
        return "in-sync"

    disk_text = read_dump(store.sql_path)
    if hash_matches(stored_hash, disk_text):
        _record_verified_stat(store, disk_stat)
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


def _record_verified_stat(store: Store, disk_stat: str) -> None:
    """After a full check finds the file in sync, record its stat so the next open
    takes the fast path (D60)."""
    conn = connect(store.db_path)
    try:
        conn.execute("BEGIN")
        _set_meta(conn, "synced_sql_stat", disk_stat)
        conn.execute("COMMIT")
    finally:
        conn.close()


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
        info["sql_hash"] = dump_hash(disk_text)
        sql_matches = hash_matches(info.get("synced_sql_hash", ""), disk_text)

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
        if sql_matches:
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
