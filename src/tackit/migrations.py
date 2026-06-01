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


# Ordered registry. Append migrations here in target-version order as they land
# (T84 -> target_version=2, T85 -> 3, T86 -> 4, ...). Empty until T84.
MIGRATIONS: list[Migration] = []


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
    current schema_version, in order, each in its own transaction + D18
    finalize_mutation. Returns the migrations that ran (possibly empty).

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
            sync.finalize_mutation(conn, store)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        ran.append(mig)
        current = next_v
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
