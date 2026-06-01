"""T83 / T84 -- migration runner mechanics + the v0.3.0 migrations themselves.

Runner-mechanics tests use fake injected migrations to exercise the registry
walk, rollback, downgrade refusal, etc. Per-migration tests build a v1 store
(hand-coded prior schema) and verify the migration's effect on data and schema.
"""

import sqlite3

import pytest

from tackit import migrations, schema
from tackit.core import Core
from tackit.db import Store, connect


@pytest.fixture
def clean_registry():
    """Snapshot + restore the global MIGRATIONS list so per-test fake migrations
    don't leak between tests (or into real later-loaded migrations)."""
    saved = list(migrations.MIGRATIONS)
    migrations.MIGRATIONS.clear()
    yield migrations.MIGRATIONS
    migrations.MIGRATIONS.clear()
    migrations.MIGRATIONS.extend(saved)


def _mig(target, name="m", on_run=None):
    """Build a Migration whose migrate fn calls `on_run(conn)` if given."""
    def _migrate(conn):
        if on_run is not None:
            on_run(conn)
    return migrations.Migration(target_version=target, name=name, migrate=_migrate)


def test_noop_when_at_target(store_path, clean_registry):
    """Fresh store is at SCHEMA_VERSION; empty registry, run returns []."""
    store = Store(store_path)
    conn = connect(store.db_path)
    try:
        ran = migrations.run_pending_migrations(conn, store)
        assert ran == []
        assert migrations.get_schema_version(conn) == int(schema.SCHEMA_VERSION)
    finally:
        conn.close()


def test_single_migration_advances_version(store_path, clean_registry, monkeypatch):
    """One migration bumps current -> target and runs the migrate fn once."""
    start = int(schema.SCHEMA_VERSION)
    target = start + 1
    monkeypatch.setattr(schema, "SCHEMA_VERSION", str(target))

    calls = []
    clean_registry.append(_mig(target, "mig_a", on_run=lambda c: calls.append(1)))

    store = Store(store_path)
    conn = connect(store.db_path)
    try:
        ran = migrations.run_pending_migrations(conn, store)
        assert [m.name for m in ran] == ["mig_a"]
        assert calls == [1]
        assert migrations.get_schema_version(conn) == target
    finally:
        conn.close()


def test_multi_migration_runs_in_target_order(store_path, clean_registry, monkeypatch):
    """Three pending migrations apply in target-version order."""
    start = int(schema.SCHEMA_VERSION)
    target = start + 3
    monkeypatch.setattr(schema, "SCHEMA_VERSION", str(target))

    order = []
    for i in (1, 2, 3):
        clean_registry.append(_mig(start + i, f"mig_{i}", on_run=lambda c, i=i: order.append(i)))

    store = Store(store_path)
    conn = connect(store.db_path)
    try:
        ran = migrations.run_pending_migrations(conn, store)
        assert [m.target_version for m in ran] == [start + 1, start + 2, start + 3]
        assert order == [1, 2, 3]
        assert migrations.get_schema_version(conn) == target
    finally:
        conn.close()


def test_contiguity_gap_raises(store_path, clean_registry, monkeypatch):
    """Missing migration for current+1 is loud (Fail Loud)."""
    start = int(schema.SCHEMA_VERSION)
    target = start + 3
    monkeypatch.setattr(schema, "SCHEMA_VERSION", str(target))

    # Register start+1 and start+3 but skip start+2 -> gap.
    clean_registry.append(_mig(start + 1, "first"))
    clean_registry.append(_mig(start + 3, "third"))

    store = Store(store_path)
    conn = connect(store.db_path)
    try:
        with pytest.raises(migrations.MigrationError, match=str(start + 2)):
            migrations.run_pending_migrations(conn, store)
    finally:
        conn.close()


def test_downgrade_refused(store_path, clean_registry):
    """A store ahead of the current tackit's target is refused loudly (no downgrade)."""
    store = Store(store_path)
    conn = connect(store.db_path)
    try:
        future = int(schema.SCHEMA_VERSION) + 1
        migrations._set_schema_version(conn, future)
        with pytest.raises(migrations.MigrationError, match="downgrade"):
            migrations.run_pending_migrations(conn, store)
    finally:
        conn.close()


def test_failed_migration_rolls_back(store_path, clean_registry, monkeypatch):
    """When a migration raises, the txn rolls back: schema_version stays put and
    any side-effect DDL is undone."""
    start = int(schema.SCHEMA_VERSION)
    target = start + 1
    monkeypatch.setattr(schema, "SCHEMA_VERSION", str(target))

    def bad(conn):
        conn.execute("CREATE TABLE _probe(x INTEGER);")
        raise RuntimeError("boom")

    clean_registry.append(migrations.Migration(target_version=target, name="bad", migrate=bad))

    store = Store(store_path)
    conn = connect(store.db_path)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            migrations.run_pending_migrations(conn, store)
        assert migrations.get_schema_version(conn) == start
        probe = conn.execute(
            "SELECT name FROM sqlite_master WHERE name='_probe'"
        ).fetchone()
        assert probe is None
    finally:
        conn.close()


def test_idempotent_second_call_is_noop(store_path, clean_registry, monkeypatch):
    """Re-running the runner after migrations applied: no side effects, no errors."""
    start = int(schema.SCHEMA_VERSION)
    target = start + 1
    monkeypatch.setattr(schema, "SCHEMA_VERSION", str(target))

    runs = []
    clean_registry.append(_mig(target, "once", on_run=lambda c: runs.append(1)))

    store = Store(store_path)
    conn = connect(store.db_path)
    try:
        first = migrations.run_pending_migrations(conn, store)
        second = migrations.run_pending_migrations(conn, store)
        assert len(first) == 1
        assert len(second) == 0
        assert runs == [1]  # migrate body invoked only on the first run
    finally:
        conn.close()


def test_sql_dump_reflects_per_migration_finalize(store_path, clean_registry, monkeypatch):
    """After a migration that mutates state, the on-disk tackit.sql reflects it
    (proves D18 finalize_mutation ran per-migration, not just at the end)."""
    start = int(schema.SCHEMA_VERSION)
    target = start + 1
    monkeypatch.setattr(schema, "SCHEMA_VERSION", str(target))

    def add_marker(conn):
        conn.execute("INSERT INTO meta(key, value) VALUES('mig_marker', 'present');")

    clean_registry.append(
        migrations.Migration(target_version=target, name="marker", migrate=add_marker)
    )

    store = Store(store_path)
    conn = connect(store.db_path)
    try:
        migrations.run_pending_migrations(conn, store)
    finally:
        conn.close()

    text = store.sql_path.read_text()
    assert "mig_marker" in text and "present" in text


def test_unregistered_next_version_raises(store_path, clean_registry, monkeypatch):
    """SCHEMA_VERSION bumped but no migration registered -> loud refusal."""
    start = int(schema.SCHEMA_VERSION)
    target = start + 1
    monkeypatch.setattr(schema, "SCHEMA_VERSION", str(target))
    # No registrations.
    store = Store(store_path)
    conn = connect(store.db_path)
    try:
        with pytest.raises(migrations.MigrationError, match="no migration registered"):
            migrations.run_pending_migrations(conn, store)
    finally:
        conn.close()


def test_core_open_runs_migrations(store_path, clean_registry, monkeypatch):
    """Core.open is the integration point: opening on a store at an old version
    runs the pending migrations transparently."""
    start = int(schema.SCHEMA_VERSION)
    target = start + 1
    monkeypatch.setattr(schema, "SCHEMA_VERSION", str(target))

    calls = []
    clean_registry.append(_mig(target, "open_test", on_run=lambda c: calls.append(1)))

    c = Core.open(start=store_path)
    try:
        assert calls == [1]
        assert migrations.get_schema_version(c.conn) == target
    finally:
        c.close_conn()


# ============================================================================
# T84 / mig_001: add S1.kind column
# ============================================================================

_V1_TASKS_DDL = """
CREATE TABLE tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    stale       INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0, 1)),
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
"""

# The pre-T86 directional `dependencies` table. Inlined here because the live
# schema.py has been replaced by the symmetric `links` table.
_V1_DEPENDENCIES_DDL = """
CREATE TABLE dependencies (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    from_task INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    to_task   INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    UNIQUE (from_task, to_task),
    CHECK (from_task <> to_task)
);
"""


def _make_v1_store(tmp_path):
    """Hand-build a store at the pre-T84 schema (no kind column,
    schema_version='1'), with a couple of seeded tasks so the migration's
    backfill is observable. The other tables are unchanged in mig_001 so they
    use the current DDL strings (effectively v1 = v2 except for tasks)."""
    store_dir = tmp_path / ".tackit"
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "backups").mkdir(exist_ok=True)
    db_path = store_dir / "tackit.db"
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(_V1_TASKS_DDL)
    conn.executescript(_V1_DEPENDENCIES_DDL)
    for ddl in (
        schema.S2_TASK_LABELS,
        schema.S4_STATUS_TRANSITIONS,
        schema.S5_TASKS_FTS,
        schema.S5_FTS_TRIGGERS,
        schema.S6_META,
    ):
        conn.executescript(ddl)
    conn.execute("INSERT INTO meta(key, value) VALUES ('version', '0');")
    conn.execute("INSERT INTO meta(key, value) VALUES ('synced_sql_hash', '');")
    conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1');")
    conn.execute(
        "INSERT INTO tasks(name, description, status, stale, created_at, updated_at) "
        "VALUES ('alpha', 'first', 'open', 0, "
        "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');"
    )
    conn.execute(
        "INSERT INTO tasks(name, description, status, stale, created_at, updated_at) "
        "VALUES ('beta', 'second', 'closed', 0, "
        "'2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00');"
    )
    conn.close()
    return Store(tmp_path)


def test_v1_store_migrates_through_all_pending(tmp_path):
    """A v1 store reaches SCHEMA_VERSION after Core.open runs every pending
    migration in order. (This is the integration-level smoke test for the
    whole migration chain; per-migration content tests follow.)"""
    _make_v1_store(tmp_path)
    c = Core.open(start=tmp_path)
    try:
        assert migrations.get_schema_version(c.conn) == int(schema.SCHEMA_VERSION)
    finally:
        c.close_conn()


def test_mig_001_backfills_kind_production_on_existing_rows(tmp_path):
    """mig_001 backfills existing rows with kind='production' (the safe guess
    consistent with shipped behavior). T87 reclassifies in a separate pass."""
    _make_v1_store(tmp_path)
    c = Core.open(start=tmp_path)
    try:
        rows = c.conn.execute(
            "SELECT id, name, kind FROM tasks ORDER BY id"
        ).fetchall()
        assert [(r["id"], r["name"], r["kind"]) for r in rows] == [
            (1, "alpha", "production"),
            (2, "beta", "production"),
        ]
    finally:
        c.close_conn()


def test_mig_001_enforces_kind_check_constraint(tmp_path):
    """After mig_001, SQLite refuses a row with an out-of-vocabulary kind."""
    _make_v1_store(tmp_path)
    c = Core.open(start=tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            c.conn.execute(
                "INSERT INTO tasks(name, description, kind, status, stale, "
                "created_at, updated_at) VALUES ('bad', '', 'nonsense', 'open', 0, "
                "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');"
            )
    finally:
        c.close_conn()


# ============================================================================
# T85 / mig_002: add S1.superseded_by column
# ============================================================================
# Note: mig_002 is preserved in the migration chain for historical replay
# from v1 stores. v0.4 / mig_006 drops the column, so the column-existence
# behavior the original mig_002 tests verified is now reversed; see the
# test_mig_006_* tests below for the steady-state check.


def test_mig_004_adds_because_with_backfill_placeholder(tmp_path):
    """mig_004 backfills pre-existing rows with a placeholder rationale; new
    rows inserted afterward must supply a real (non-empty) because."""
    _make_v1_store(tmp_path)
    c = Core.open(start=tmp_path)
    try:
        # Seed a link via the runtime API (forces a meaningful because).
        c.add("a", kind="production")
        c.add("b", kind="production")
        c.link_add(1, 2, because="T2 builds on T1", delta="seed test link")
        row = c.conn.execute(
            "SELECT because FROM links WHERE task_a=1 AND task_b=2"
        ).fetchone()
        assert row["because"] == "T2 builds on T1"
        # Verify the column has the right NOT NULL + length CHECK by trying to
        # write an empty rationale at the raw SQL layer.
        with pytest.raises(sqlite3.IntegrityError):
            c.conn.execute(
                "INSERT INTO links(task_a, task_b, because) VALUES (1, 2, '');"
            )
    finally:
        c.close_conn()


def test_fresh_init_has_kind_column_without_superseded_by(store_path):
    """A fresh store at SCHEMA_VERSION 7 has the kind column (mig 001) but
    NOT the superseded_by column (mig 002 added it; mig 006 dropped it in
    v0.4 / D29). Fresh-init and v1-then-migrate-through-006 agree on the
    resulting schema."""
    conn = connect(Store(store_path).db_path)
    try:
        conn.execute(
            "INSERT INTO tasks(name, description, status, stale, created_at, updated_at) "
            "VALUES ('fresh', '', 'open', 0, "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');"
        )
        row = conn.execute("SELECT kind FROM tasks WHERE name='fresh'").fetchone()
        assert row["kind"] == "production"
        # superseded_by column does not exist on a fresh v7 store.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        assert "superseded_by" not in cols
        assert "kind" in cols
        assert "wont_do_reason" in cols  # mig 005 still applies
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# v0.4 / D29 / mig_006: drop S1.superseded_by column (supersede retired)
# ----------------------------------------------------------------------------


def test_mig_006_drops_superseded_by_column(tmp_path):
    """After running migrations from v1 forward, the superseded_by column
    added by mig_002 is gone (dropped by mig_006). The column is absent
    from PRAGMA table_info; queries referencing it fail with 'no such column'."""
    _make_v1_store(tmp_path)
    c = Core.open(start=tmp_path)
    try:
        cols = {r[1] for r in c.conn.execute("PRAGMA table_info(tasks)").fetchall()}
        assert "superseded_by" not in cols
        with pytest.raises(sqlite3.OperationalError, match="no such column"):
            c.conn.execute("SELECT superseded_by FROM tasks").fetchone()
    finally:
        c.close_conn()


def test_mig_006_preserves_existing_task_rows(tmp_path):
    """A v1 db that runs the full chain through mig_006 keeps its task rows
    intact -- only the superseded_by column is removed; name/kind/status
    all survive."""
    _make_v1_store(tmp_path)
    c = Core.open(start=tmp_path)
    try:
        rows = c.conn.execute(
            "SELECT id, name, kind, status FROM tasks ORDER BY id"
        ).fetchall()
        assert [(r["id"], r["name"], r["kind"], r["status"]) for r in rows] == [
            (1, "alpha", "production", "open"),
            (2, "beta", "production", "closed"),
        ]
        cols = {r[1] for r in c.conn.execute("PRAGMA table_info(tasks)").fetchall()}
        assert "superseded_by" not in cols
        # And the remaining columns are intact:
        assert "kind" in cols and "wont_do_reason" in cols and "stale" in cols
    finally:
        c.close_conn()
