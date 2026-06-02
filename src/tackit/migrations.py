"""Schema migration runner -- tackit's first live schema migration framework (T83).

Each migration is a function ``migrate(conn)`` that moves the schema (and
possibly the data) from version N to version N+1. The runner walks the ordered
registry, applying each pending migration in its own transaction + D18
finalize_mutation, so an interrupted run leaves the disk in a consistent state
at the last-applied version.

Forward-only. Rotating backups (D18) are the rollback story; there are no
down-migrations.

Register new migrations by appending to :data:`MIGRATIONS` in target-version
order. The runner refuses non-contiguous registries (missing N between current
and target) and refuses downgrade (current > target = made by newer tackit).

Hooks in: :meth:`Core.open` calls :func:`run_pending_migrations` after
:func:`sync.startup_sync`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from . import sync
from .db import Store
from .errors import TackitError


class MigrationError(TackitError):
    """Loud-failure raised when migrations can't proceed: downgrade attempted,
    missing migration script for the next-version slot, or registry contiguity
    violation."""


@dataclass(frozen=True)
class Migration:
    target_version: int
    name: str
    migrate: Callable[[sqlite3.Connection], None]


# --- migration scripts -----------------------------------------------------

def _mig_001_add_kind_column(conn: sqlite3.Connection) -> None:
    """T84 / D26 -- add the `kind` column to S1 tasks. Existing rows backfill to
    'production' via the column default (the safe, "this code is shipped" guess);
    misclassifications get fixed by T87's one-time classification pass."""
    conn.execute(
        "ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'production' "
        "CHECK (kind IN ('design', 'schema', 'production', 'meta'));"
    )


def _mig_002_add_superseded_by_column(conn: sqlite3.Connection) -> None:
    """T85 / D25 -- add the `superseded_by` marker to S1. Nullable FK to tasks.id
    with a CHECK that refuses self-supersede. Existing rows default to NULL
    (not superseded). The supersede() op + surface come later (T92, T95)."""
    conn.execute(
        "ALTER TABLE tasks ADD COLUMN superseded_by INTEGER "
        "REFERENCES tasks(id) "
        "CHECK (superseded_by IS NULL OR superseded_by <> id);"
    )


def _mig_004_add_because_column(conn: sqlite3.Connection) -> None:
    """T116 / cascade-ergonomics A -- add the per-edge `because` rationale to
    S3 links. Existing links backfill to a placeholder ('(pre-T116 link --
    rationale not recorded)') so the column can be NOT NULL while still
    accepting the historical rows. New links require a real, non-empty
    rationale (enforced at the link_add layer; the DDL allows the placeholder
    so the migration can run before authoring rationales)."""
    conn.execute(
        "ALTER TABLE links ADD COLUMN because TEXT NOT NULL "
        "DEFAULT '(pre-T116 link -- rationale not recorded)';"
    )


def _mig_005_add_wont_do_status_and_reason(conn: sqlite3.Connection) -> None:
    """T132 / 2026-06-01 -- add 'wont_do' as a third terminal status,
    distinct from 'closed' (which was overloaded between 'work done' and
    'scope dropped, never doing this'). Adds the wont_do_reason TEXT column
    (nullable in DDL; the op layer enforces non-null on wont_do rows so the
    decision-not-to-do always carries a durable rationale). Extends the
    tasks.status CHECK to include 'wont_do' via the writable_schema pragma
    -- the SQLite escape hatch for adding to a CHECK enum without the
    heavyweight 12-step table rebuild. The pragma path is safe for this
    narrow case (string-replace inside a known CHECK clause) and avoids
    touching FK and FTS5 triggers."""
    conn.execute("ALTER TABLE tasks ADD COLUMN wont_do_reason TEXT;")
    old_check = "CHECK (status IN ('open', 'closed'))"
    new_check = "CHECK (status IN ('open', 'closed', 'wont_do'))"
    conn.execute("PRAGMA writable_schema=ON;")
    conn.execute(
        "UPDATE sqlite_master SET sql = REPLACE(sql, ?, ?) "
        "WHERE type='table' AND name='tasks';",
        (old_check, new_check),
    )
    conn.execute("PRAGMA writable_schema=OFF;")


def _mig_007_add_description_revisions(conn: sqlite3.Connection) -> None:
    """v0.4 / D29 -- add the description_revisions audit table (S7).
    Append-only; written by core.edit() on every successful edit that
    actually changes name or description (no-op edits skipped per D20).
    Existing edits made before this migration have no recorded revision
    (audit starts here, by design -- the table is forward-looking)."""
    conn.execute(
        "CREATE TABLE description_revisions ("
        "  id               INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  task_id          INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,"
        "  prev_name        TEXT    NOT NULL,"
        "  prev_description TEXT    NOT NULL DEFAULT '',"
        "  delta            TEXT    NOT NULL CHECK (length(delta) > 0),"
        "  edited_at        TEXT    NOT NULL"
        ");"
    )


def _mig_006_drop_superseded_by_column(conn: sqlite3.Connection) -> None:
    """v0.4 / D29 -- retire the supersede marker. Drops the superseded_by
    column added by mig_002. The v0.4 simplification replaces the marker's
    archaeology role with the description_revisions audit table (S7) added
    by mig_007: prior name/description preserved verbatim under the same
    task id rather than via an FK pointer to a successor task.

    For the dogfood db expect zero non-null superseded_by values (no
    supersede was ever wired through MCP/CLI; the core op was only used in
    tests). Any non-null values, if present, are silently lost on column
    drop -- the prose they pointed at is still in the new task's row, just
    no longer linked to the old."""
    conn.execute("ALTER TABLE tasks DROP COLUMN superseded_by;")


def _mig_008_rebuild_fts_with_prefix(conn: sqlite3.Connection) -> None:
    """v0.4 / D32 -- rebuild tasks_fts so its indexed name carries the
    synthesized auto-id prefix (<kind_letter><id> — <name>).

    Steps:
      1. Drop the three S5 FTS sync triggers (tasks_fts_ai / _ad / _au) -- the
         pre-D32 versions index the bare name; they must go before we touch
         the virtual table to avoid firing on the rebuild.
      2. Drop the tasks_fts virtual table (clears every pre-D32 indexed row).
      3. Recreate the tasks_fts virtual table.
      4. Bulk-insert one row per existing tasks row, with the synthesized
         prefix prepended to the indexed name.
      5. Recreate the three triggers as the prefix-aware versions, so
         subsequent inserts/updates/deletes on tasks are mirrored with the
         prefix.

    DDL is inlined rather than imported from schema.py: a migration captures
    a point-in-time schema state and shouldn't drift if schema.py is later
    refactored. The CASE statement here, the CASE in schema.S5_FTS_TRIGGERS,
    and models.kind_letter must all agree -- one logical map, three encodings.

    Why conn.execute() per statement rather than conn.executescript(): the
    sqlite3 driver auto-COMMITs before each executescript() call, which would
    break the BEGIN/COMMIT envelope this migration is wrapped in by
    run_pending_migrations (the post-script COMMIT would then fail with 'no
    transaction is active')."""
    conn.execute("DROP TRIGGER IF EXISTS tasks_fts_ai;")
    conn.execute("DROP TRIGGER IF EXISTS tasks_fts_ad;")
    conn.execute("DROP TRIGGER IF EXISTS tasks_fts_au;")
    conn.execute("DROP TABLE IF EXISTS tasks_fts;")
    conn.execute(
        "CREATE VIRTUAL TABLE tasks_fts USING fts5("
        "  name, description, content='tasks', content_rowid='id');"
    )
    conn.execute(
        "INSERT INTO tasks_fts(rowid, name, description) "
        "SELECT id, "
        "       CASE kind "
        "           WHEN 'design'     THEN 'D' "
        "           WHEN 'schema'     THEN 'S' "
        "           WHEN 'production' THEN 'T' "
        "           WHEN 'meta'       THEN 'M' "
        "       END || id || ' — ' || name, "
        "       description "
        "FROM tasks;"
    )
    conn.execute(
        "CREATE TRIGGER tasks_fts_ai AFTER INSERT ON tasks BEGIN "
        "  INSERT INTO tasks_fts(rowid, name, description) "
        "  VALUES ("
        "    new.id, "
        "    CASE new.kind "
        "      WHEN 'design'     THEN 'D' "
        "      WHEN 'schema'     THEN 'S' "
        "      WHEN 'production' THEN 'T' "
        "      WHEN 'meta'       THEN 'M' "
        "    END || new.id || ' — ' || new.name, "
        "    new.description"
        "  );"
        "END;"
    )
    conn.execute(
        "CREATE TRIGGER tasks_fts_ad AFTER DELETE ON tasks BEGIN "
        "  INSERT INTO tasks_fts(tasks_fts, rowid, name, description) "
        "  VALUES ("
        "    'delete', "
        "    old.id, "
        "    CASE old.kind "
        "      WHEN 'design'     THEN 'D' "
        "      WHEN 'schema'     THEN 'S' "
        "      WHEN 'production' THEN 'T' "
        "      WHEN 'meta'       THEN 'M' "
        "    END || old.id || ' — ' || old.name, "
        "    old.description"
        "  );"
        "END;"
    )
    conn.execute(
        "CREATE TRIGGER tasks_fts_au AFTER UPDATE ON tasks BEGIN "
        "  INSERT INTO tasks_fts(tasks_fts, rowid, name, description) "
        "  VALUES ("
        "    'delete', "
        "    old.id, "
        "    CASE old.kind "
        "      WHEN 'design'     THEN 'D' "
        "      WHEN 'schema'     THEN 'S' "
        "      WHEN 'production' THEN 'T' "
        "      WHEN 'meta'       THEN 'M' "
        "    END || old.id || ' — ' || old.name, "
        "    old.description"
        "  );"
        "  INSERT INTO tasks_fts(rowid, name, description) "
        "  VALUES ("
        "    new.id, "
        "    CASE new.kind "
        "      WHEN 'design'     THEN 'D' "
        "      WHEN 'schema'     THEN 'S' "
        "      WHEN 'production' THEN 'T' "
        "      WHEN 'meta'       THEN 'M' "
        "    END || new.id || ' — ' || new.name, "
        "    new.description"
        "  );"
        "END;"
    )


def _mig_009_status_extend_and_partition_check(conn: sqlite3.Connection) -> None:
    """v0.5 / D35 + D36 -- extend tasks.status enum from 3 values to 5
    (open/closed/wont_do for production/meta; spec/retired for design/schema);
    add a kind/status partition CHECK constraint; data-migrate design/schema
    rows to partition-valid statuses (open/closed -> spec; wont_do -> retired);
    backfill status_transitions for the migrated rows.

    Production/meta rows are NOT touched -- their statuses stay; their
    transition history stays. Only design/schema rows migrate, because the
    partition rule maps them to spec/retired.

    Implementation: uses the writable_schema PRAGMA pattern (same as mig_005)
    rather than a full table rebuild. The table-rebuild approach would require
    PRAGMA foreign_keys=OFF outside the transaction (per SQLite docs); the
    migration runner wraps each mig in BEGIN/COMMIT so PRAGMA foreign_keys is
    silently ignored inside it, and dropping `tasks` would cascade-delete
    every dependent row (task_labels, links, status_transitions,
    description_revisions). The in-place writable_schema approach avoids any
    cascade because the table identity is preserved.

    Steps:
      1. Use writable_schema to widen the status CHECK enum from 3 to 5 values
         (replace the CHECK clause text in sqlite_master). After this point
         the table accepts spec/retired in inserts/updates but no existing
         rows have been changed.
      2. UPDATE design/schema rows: wont_do -> retired; open/closed -> spec.
         Production/meta rows are untouched.
      3. Backfill status_transitions for the migrated rows (captured BEFORE
         the UPDATE so we have the original from_status).
      4. Use writable_schema to append the kind/status partition CHECK clause
         to the table definition in sqlite_master. After this point the
         partition rule is enforced on all subsequent writes.

    Idempotency: the migration runner version-gates by SCHEMA_VERSION, so this
    function is invoked at most once per database upgrade. The body itself is
    NOT idempotent if invoked twice in the same v9 -> v10 transition (it would
    double-backfill status_transitions); the runner guarantees single-shot.

    Backs S1 (T37) -- schema extended; D35 (T167) -- spec status; D36 (D171)
    -- retired status + partition rule; D8 (T50) -- transition history extended."""
    # The migration timestamp -- captured once at function entry so all backfilled
    # transition rows share the same `changed_at` (the migration was one event).
    from datetime import datetime, timezone
    mig_ts = datetime.now(timezone.utc).isoformat()

    # Capture original (id, status) of design/schema rows BEFORE the UPDATE,
    # so we can compute correct from_status for the status_transitions backfill
    # (after the UPDATE the original status is no longer recoverable).
    pre_update_rows = conn.execute(
        "SELECT id, status FROM tasks WHERE kind IN ('design', 'schema')"
    ).fetchall()

    # Step 1: widen the status CHECK enum from 3 to 5 values via writable_schema
    # (same pattern as mig_005). This is a narrow, well-known string-replace
    # inside the CHECK clause -- safe.
    #
    # After writable_schema edits, bump PRAGMA schema_version so SQLite
    # recomputes its in-memory schema cache. Without this, the connection
    # continues to enforce the OLD CHECK constraint for the rest of the
    # transaction (writable_schema edits sqlite_master but doesn't invalidate
    # the cache). mig_005 used writable_schema too but didn't refresh -- it
    # got away with it because its CHECK widening (adding 'wont_do') was
    # never exercised by a follow-up UPDATE in the same migration. mig_009
    # DOES need to UPDATE rows to the new 'spec'/'retired' values within the
    # same transaction, so the refresh is required.
    old_status_check = "CHECK (status IN ('open', 'closed', 'wont_do'))"
    new_status_check = (
        "CHECK (status IN ('open', 'closed', 'wont_do', 'spec', 'retired'))"
    )
    conn.execute("PRAGMA writable_schema=ON;")
    conn.execute(
        "UPDATE sqlite_master SET sql = REPLACE(sql, ?, ?) "
        "WHERE type='table' AND name='tasks';",
        (old_status_check, new_status_check),
    )
    conn.execute("PRAGMA writable_schema=OFF;")
    _bump_schema_cache(conn)

    # Step 2: data migrate design/schema rows to partition-valid statuses.
    # wont_do -> retired; open/closed -> spec. (After step 1 the widened
    # CHECK admits these new values; before step 4 the partition CHECK
    # isn't yet active, so the intermediate state is permitted.)
    conn.execute(
        "UPDATE tasks SET status = "
        "  CASE "
        "    WHEN status='wont_do' THEN 'retired' "
        "    ELSE 'spec' "
        "  END "
        "WHERE kind IN ('design', 'schema');"
    )

    # Step 3: backfill status_transitions for the migrated rows. Use the
    # pre-UPDATE statuses captured above; for each, compute the post-UPDATE
    # status with the same CASE rule.
    for task_id, old_status in pre_update_rows:
        new_status = "retired" if old_status == "wont_do" else "spec"
        conn.execute(
            "INSERT INTO status_transitions "
            "(task_id, from_status, to_status, changed_at) "
            "VALUES (?, ?, ?, ?);",
            (task_id, old_status, new_status, mig_ts),
        )

    # Step 4: append the kind/status partition CHECK clause to the table
    # definition in sqlite_master. The CREATE TABLE text currently ends with
    # `updated_at      TEXT    NOT NULL\n);` (the column list closes); we
    # insert the new CHECK clause before the closing `)`.
    #
    # Robust approach: read the current sql, find the final `)` (the table
    # closure -- inner CHECK parens are balanced and shorter), append a comma
    # + new CHECK clause before it, write back. This survives any whitespace
    # variation that ALTER TABLE or prior writable_schema edits introduced.
    partition_check_clause = (
        ",\n    CHECK (\n"
        "        (kind IN ('production', 'meta') "
        "AND status IN ('open', 'closed', 'wont_do'))\n"
        "        OR\n"
        "        (kind IN ('design', 'schema') "
        "AND status IN ('spec', 'retired'))\n"
        "    )"
    )
    sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks';"
    ).fetchone()
    if sql_row is None:
        raise RuntimeError(
            "mig_009: tasks table sql not found in sqlite_master; "
            "schema is in an unexpected state."
        )
    current_sql = sql_row[0]
    last_paren = current_sql.rfind(")")
    if last_paren < 0:
        raise RuntimeError(
            "mig_009: tasks CREATE TABLE sql has no closing `)`; "
            "schema is in an unexpected state."
        )
    new_sql = (
        current_sql[:last_paren] + partition_check_clause + "\n" + current_sql[last_paren:]
    )
    conn.execute("PRAGMA writable_schema=ON;")
    conn.execute(
        "UPDATE sqlite_master SET sql = ? "
        "WHERE type='table' AND name='tasks';",
        (new_sql,),
    )
    conn.execute("PRAGMA writable_schema=OFF;")
    _bump_schema_cache(conn)


def _bump_schema_cache(conn: sqlite3.Connection) -> None:
    """Force SQLite to recompute its in-memory schema cache after a
    ``writable_schema`` edit. SQLite's schema cache is keyed by
    ``PRAGMA schema_version``; bumping it by 1 invalidates the cache so the
    next statement sees the updated sqlite_master. Required after any
    writable_schema-based schema mutation that needs to take effect within
    the same transaction (e.g., a follow-up UPDATE/INSERT that relies on
    the new constraint shape)."""
    current = conn.execute("PRAGMA schema_version;").fetchone()[0]
    conn.execute(f"PRAGMA schema_version = {current + 1};")


def _mig_003_dependencies_to_links_symmetric(conn: sqlite3.Connection) -> None:
    """T86 / D5 / D27 -- rebuild the directional `dependencies` table as the
    symmetric `links` table. Each existing (from_task, to_task) edge becomes
    a canonical (min, max) pair in `links`; INSERT OR IGNORE drops any
    reverse-direction shadow rows (e.g. both (A,B) and (B,A) collapse to one).
    Migration 003 bundles the schema + the semantic shift -- after this runs
    the engine reads symmetric semantics; see T88/T89/T90 in core.py."""
    conn.execute(
        "CREATE TABLE links ("
        "  id     INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  task_a INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,"
        "  task_b INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,"
        "  UNIQUE (task_a, task_b),"
        "  CHECK (task_a < task_b)"
        ");"
    )
    conn.execute(
        "INSERT OR IGNORE INTO links (task_a, task_b) "
        "SELECT MIN(from_task, to_task), MAX(from_task, to_task) "
        "FROM dependencies;"
    )
    conn.execute("DROP TABLE dependencies;")


# Ordered registry. Append migrations here in target-version order as they land
# (T84 -> 2, T85 -> 3, T86 -> 4, ...).
MIGRATIONS: list[Migration] = [
    Migration(
        target_version=2,
        name="add S1.kind column (T84 / D26)",
        migrate=_mig_001_add_kind_column,
    ),
    Migration(
        target_version=3,
        name="add S1.superseded_by column (T85 / D25)",
        migrate=_mig_002_add_superseded_by_column,
    ),
    Migration(
        target_version=4,
        name="dependencies -> symmetric links table (T86 / D5)",
        migrate=_mig_003_dependencies_to_links_symmetric,
    ),
    Migration(
        target_version=5,
        name="add S3.because rationale column (T116 / cascade-ergonomics A)",
        migrate=_mig_004_add_because_column,
    ),
    Migration(
        target_version=6,
        name="add S1.wont_do_reason column + extend status CHECK (T132)",
        migrate=_mig_005_add_wont_do_status_and_reason,
    ),
    Migration(
        target_version=7,
        name="drop S1.superseded_by column (v0.4 / D29: supersede retired)",
        migrate=_mig_006_drop_superseded_by_column,
    ),
    Migration(
        target_version=8,
        name="add S7.description_revisions audit table (v0.4 / D29)",
        migrate=_mig_007_add_description_revisions,
    ),
    Migration(
        target_version=9,
        name="rebuild tasks_fts with kind+id name prefix (v0.4 / D32)",
        migrate=_mig_008_rebuild_fts_with_prefix,
    ),
    Migration(
        target_version=10,
        name="extend status to 5 values + kind/status partition CHECK + design/schema rows migrate to spec/retired (v0.5 / D35 + D36)",
        migrate=_mig_009_status_extend_and_partition_check,
    ),
]


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Read meta.schema_version. Returns 0 if the row is missing (only happens
    on a pre-S6 store, which shouldn't exist in practice)."""
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(row[0]) if row else 0


def _set_schema_version(conn: sqlite3.Connection, v: int) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
        (str(v),),
    )


def run_pending_migrations(
    conn: sqlite3.Connection, store: Store
) -> list[Migration]:
    """Apply every registered migration whose target_version is above the
    current schema_version, in order, each in its own transaction. After all
    pending migrations succeed, run one D18 finalize_mutation to emit a
    consistent ``tackit.sql`` at the final schema. Returns the migrations that
    ran (possibly empty).

    Per-batch finalize (rather than per-migration) because intermediate
    schemas may not match the current ``_DUMP_TABLES`` shape -- a v1 db
    mid-batch has neither the pre- nor post-rename table layout. Crash safety
    still holds: each migration commits independently (schema_version
    advances), so an interrupted batch resumes from where it stopped on the
    next ``Core.open``.

    Refuses (MigrationError):
      * downgrade -- current > target (store made by a newer tackit);
      * missing migration -- no Migration registered for ``current + 1``.
    """
    from .schema import SCHEMA_VERSION as target_str

    target = int(target_str)
    current = get_schema_version(conn)

    if current > target:
        raise MigrationError(
            f"schema_version {current} > target {target}. The store was made by "
            f"a newer tackit; downgrade is not supported. Upgrade tackit and retry."
        )

    ran: list[Migration] = []
    while current < target:
        next_v = current + 1
        mig = _find_migration(next_v)
        conn.execute("BEGIN")
        try:
            mig.migrate(conn)
            _set_schema_version(conn, next_v)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        ran.append(mig)
        current = next_v

    # One D18 finalize after the whole batch -- emits a consistent .sql at the
    # final schema. Skipped on no-op batches so a clean startup doesn't churn.
    if ran:
        conn.execute("BEGIN")
        try:
            sync.finalize_mutation(conn, store)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return ran


def _find_migration(target_v: int) -> Migration:
    for m in MIGRATIONS:
        if m.target_version == target_v:
            return m
    raise MigrationError(
        f"no migration registered for target schema_version {target_v} "
        f"(runner has migrations for: "
        f"{sorted(m.target_version for m in MIGRATIONS)})"
    )
