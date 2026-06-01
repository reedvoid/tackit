"""Schema migration runner -- tackit's first live schema migration framework (T83).

Each migration is a function ``migrate(conn)`` that moves the schema (and
possibly the data) from version N to version N+1. The runner walks the ordered
registry, applying each pending migration in its own transaction + D18
finalize_mutation, so an interrupted run leaves the disk in a consistent state
at the last-applied version.

Forward-only. The supersede convention + rotating backups (D18) are the
rollback story; there are no down-migrations.

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
