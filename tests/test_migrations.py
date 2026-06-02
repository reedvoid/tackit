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

# The pre-D32 (v<9) bare-name FTS sync triggers. Inlined here because the live
# schema.py S5_FTS_TRIGGERS now references new.kind for the synthesized prefix,
# which doesn't exist on the v1 tasks table. mig_008 (v8->v9) is what replaces
# these with the prefix-aware versions.
_V1_FTS_TRIGGERS = """
CREATE TRIGGER tasks_fts_ai AFTER INSERT ON tasks BEGIN
    INSERT INTO tasks_fts(rowid, name, description)
    VALUES (new.id, new.name, new.description);
END;
CREATE TRIGGER tasks_fts_ad AFTER DELETE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, name, description)
    VALUES ('delete', old.id, old.name, old.description);
END;
CREATE TRIGGER tasks_fts_au AFTER UPDATE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, name, description)
    VALUES ('delete', old.id, old.name, old.description);
    INSERT INTO tasks_fts(rowid, name, description)
    VALUES (new.id, new.name, new.description);
END;
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
        _V1_FTS_TRIGGERS,  # pre-D32 bare-name triggers (mig_008 swaps them later)
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


# ----------------------------------------------------------------------------
# v0.4 / D29 / mig_007: add description_revisions audit table (S7)
# ----------------------------------------------------------------------------


def test_mig_007_creates_description_revisions_table(tmp_path):
    """After the full chain, the description_revisions table exists with the
    expected column shape -- the v0.4 audit backstop for edit-on-closed."""
    _make_v1_store(tmp_path)
    c = Core.open(start=tmp_path)
    try:
        cols = {
            r[1]
            for r in c.conn.execute("PRAGMA table_info(description_revisions)").fetchall()
        }
        assert cols == {
            "id",
            "task_id",
            "prev_name",
            "prev_description",
            "delta",
            "edited_at",
        }
    finally:
        c.close_conn()


def test_mig_007_table_starts_empty_on_existing_data(tmp_path):
    """Migration creates the table empty; pre-migration edits leave no audit
    rows (audit is forward-looking by design)."""
    _make_v1_store(tmp_path)
    c = Core.open(start=tmp_path)
    try:
        count = c.conn.execute(
            "SELECT COUNT(*) FROM description_revisions"
        ).fetchone()[0]
        assert count == 0
    finally:
        c.close_conn()


def test_mig_007_empty_delta_rejected_by_table_check(tmp_path):
    """The S7 DDL CHECK refuses an empty delta at the DB layer (defense in
    depth alongside the op-layer _require_delta)."""
    _make_v1_store(tmp_path)
    c = Core.open(start=tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            c.conn.execute(
                "INSERT INTO description_revisions("
                "  task_id, prev_name, prev_description, delta, edited_at"
                ") VALUES (1, 'x', 'y', '', '2026-01-01T00:00:00+00:00');"
            )
    finally:
        c.close_conn()


# ============================================================================
# v0.5 / D35 + D36 / mig_009: extend status to 5 values + kind/status partition
# CHECK + data migration (open/closed design/schema -> spec; wont_do
# design/schema -> retired) + status_transitions backfill
# ============================================================================

# The pre-v10 (v9-state) tasks DDL: 3-value status CHECK, no partition CHECK,
# wont_do_reason column already present (from mig 005). Inlined here so the
# test stays correct after schema.py advances to the v10 DDL.
_V9_TASKS_DDL = """
CREATE TABLE tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    kind            TEXT    NOT NULL DEFAULT 'production' CHECK (kind IN ('design', 'schema', 'production', 'meta')),
    status          TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'wont_do')),
    stale           INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0, 1)),
    wont_do_reason  TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);
"""

# Pre-v10 FTS sync triggers (D32, same shape as mig_008's). Inlined because
# schema.py S5_FTS_TRIGGERS won't be re-runnable on a clean v9 (triggers may
# not yet exist with the prefix CASE) and so the test stays correct across
# future trigger refactors.
_V9_FTS_TRIGGERS = """
CREATE TRIGGER tasks_fts_ai AFTER INSERT ON tasks BEGIN
    INSERT INTO tasks_fts(rowid, name, description)
    VALUES (
        new.id,
        CASE new.kind
            WHEN 'design'     THEN 'D'
            WHEN 'schema'     THEN 'S'
            WHEN 'production' THEN 'T'
            WHEN 'meta'       THEN 'M'
        END || new.id || ' — ' || new.name,
        new.description
    );
END;
CREATE TRIGGER tasks_fts_ad AFTER DELETE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, name, description)
    VALUES (
        'delete',
        old.id,
        CASE old.kind
            WHEN 'design'     THEN 'D'
            WHEN 'schema'     THEN 'S'
            WHEN 'production' THEN 'T'
            WHEN 'meta'       THEN 'M'
        END || old.id || ' — ' || old.name,
        old.description
    );
END;
CREATE TRIGGER tasks_fts_au AFTER UPDATE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, name, description)
    VALUES (
        'delete',
        old.id,
        CASE old.kind
            WHEN 'design'     THEN 'D'
            WHEN 'schema'     THEN 'S'
            WHEN 'production' THEN 'T'
            WHEN 'meta'       THEN 'M'
        END || old.id || ' — ' || old.name,
        old.description
    );
    INSERT INTO tasks_fts(rowid, name, description)
    VALUES (
        new.id,
        CASE new.kind
            WHEN 'design'     THEN 'D'
            WHEN 'schema'     THEN 'S'
            WHEN 'production' THEN 'T'
            WHEN 'meta'       THEN 'M'
        END || new.id || ' — ' || new.name,
        new.description
    );
END;
"""


def _make_v9_store(tmp_path, seed_rows=None):
    """Build a v9-state store (post-mig_008, pre-mig_009): tasks table has the
    3-value status CHECK + no partition CHECK + the wont_do_reason column +
    D32 prefix-aware FTS triggers. ``seed_rows`` is a list of dicts:
    ``{name, kind, status, wont_do_reason?}``. The migration's effect on
    these seeds is what each test asserts."""
    store_dir = tmp_path / ".tackit"
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "backups").mkdir(exist_ok=True)
    db_path = store_dir / "tackit.db"
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(_V9_TASKS_DDL)
    for ddl in (
        schema.S2_TASK_LABELS,
        schema.S3_LINKS,
        schema.S4_STATUS_TRANSITIONS,
        schema.S5_TASKS_FTS,
        _V9_FTS_TRIGGERS,
        schema.S6_META,
        schema.S7_DESCRIPTION_REVISIONS,
    ):
        conn.executescript(ddl)
    conn.execute("INSERT INTO meta(key, value) VALUES ('version', '0');")
    conn.execute("INSERT INTO meta(key, value) VALUES ('synced_sql_hash', '');")
    conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '9');")
    for row in seed_rows or []:
        conn.execute(
            "INSERT INTO tasks(name, description, kind, status, stale, "
            "wont_do_reason, created_at, updated_at) "
            "VALUES (?, '', ?, ?, 0, ?, "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (row["name"], row["kind"], row["status"], row.get("wont_do_reason")),
        )
    conn.close()
    return Store(tmp_path)


def _run_only_mig_009(store):
    """Apply only mig_009 to a v9 store (no other migrations, no D18 finalize).
    Returns the open connection so the caller can introspect post-mig state.
    Used by tests that want to isolate mig_009's effect without running the
    entire chain."""
    conn = connect(store.db_path)
    conn.execute("BEGIN")
    try:
        migrations._mig_009_status_extend_and_partition_check(conn)
        migrations._set_schema_version(conn, 10)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        conn.close()
        raise
    return conn


def test_mig_009_open_design_schema_migrates_to_spec(tmp_path):
    """v0.5 / D35: open design/schema rows migrate to status='spec' (the
    living-decision status) and get a status_transitions row from='open'
    to='spec' documenting the migration event."""
    seed = [
        {"name": "D1", "kind": "design", "status": "open"},
        {"name": "D2", "kind": "design", "status": "open"},
        {"name": "S1", "kind": "schema", "status": "open"},
    ]
    store = _make_v9_store(tmp_path, seed_rows=seed)
    conn = _run_only_mig_009(store)
    try:
        rows = conn.execute(
            "SELECT name, kind, status FROM tasks ORDER BY id"
        ).fetchall()
        assert [(r["name"], r["kind"], r["status"]) for r in rows] == [
            ("D1", "design", "spec"),
            ("D2", "design", "spec"),
            ("S1", "schema", "spec"),
        ]
        transitions = conn.execute(
            "SELECT task_id, from_status, to_status "
            "FROM status_transitions ORDER BY task_id"
        ).fetchall()
        assert [(t["task_id"], t["from_status"], t["to_status"]) for t in transitions] == [
            (1, "open", "spec"),
            (2, "open", "spec"),
            (3, "open", "spec"),
        ]
    finally:
        conn.close()


def test_mig_009_closed_design_schema_migrates_to_spec(tmp_path):
    """v0.5 / D35: legacy closed design/schema rows (pre-D30 closures) also
    migrate to status='spec'. D156's kind-conditional reconcile mirror is
    obviated -- the legacy population now lives at a partition-valid status."""
    seed = [
        {"name": "D1", "kind": "design", "status": "closed"},
        {"name": "S1", "kind": "schema", "status": "closed"},
    ]
    store = _make_v9_store(tmp_path, seed_rows=seed)
    conn = _run_only_mig_009(store)
    try:
        rows = conn.execute(
            "SELECT name, status FROM tasks ORDER BY id"
        ).fetchall()
        assert [(r["name"], r["status"]) for r in rows] == [
            ("D1", "spec"),
            ("S1", "spec"),
        ]
        transitions = conn.execute(
            "SELECT from_status, to_status FROM status_transitions ORDER BY task_id"
        ).fetchall()
        assert [(t["from_status"], t["to_status"]) for t in transitions] == [
            ("closed", "spec"),
            ("closed", "spec"),
        ]
    finally:
        conn.close()


def test_mig_009_wont_do_design_migrates_to_retired(tmp_path):
    """v0.5 / D36: wont_do design rows (e.g. D25 supersede retired in v0.4)
    migrate to status='retired' (the terminal status for abandoned specs).
    The original wont_do_reason column value persists on the row."""
    seed = [
        {
            "name": "D25",
            "kind": "design",
            "status": "wont_do",
            "wont_do_reason": "supersede mechanism retired v0.4",
        },
    ]
    store = _make_v9_store(tmp_path, seed_rows=seed)
    conn = _run_only_mig_009(store)
    try:
        row = conn.execute(
            "SELECT name, status, wont_do_reason FROM tasks WHERE id=1"
        ).fetchone()
        assert row["name"] == "D25"
        assert row["status"] == "retired"
        assert row["wont_do_reason"] == "supersede mechanism retired v0.4"
        t = conn.execute(
            "SELECT from_status, to_status FROM status_transitions WHERE task_id=1"
        ).fetchone()
        assert (t["from_status"], t["to_status"]) == ("wont_do", "retired")
    finally:
        conn.close()


def test_mig_009_wont_do_schema_migrates_to_retired(tmp_path):
    """v0.5 / D36: wont_do schema rows are symmetric with design -- they also
    migrate to retired (the partition rule applies to both kinds)."""
    seed = [
        {
            "name": "S99",
            "kind": "schema",
            "status": "wont_do",
            "wont_do_reason": "schema retired",
        },
    ]
    store = _make_v9_store(tmp_path, seed_rows=seed)
    conn = _run_only_mig_009(store)
    try:
        row = conn.execute(
            "SELECT status, wont_do_reason FROM tasks WHERE id=1"
        ).fetchone()
        assert row["status"] == "retired"
        assert row["wont_do_reason"] == "schema retired"
    finally:
        conn.close()


def test_mig_009_production_meta_unchanged(tmp_path):
    """v0.5 / D35+D36: production/meta rows are NOT touched by mig 009. Their
    statuses (open/closed/wont_do) stay; no transition rows added for them
    (only design/schema rows get the backfill)."""
    seed = [
        {"name": "T1", "kind": "production", "status": "open"},
        {"name": "T2", "kind": "production", "status": "closed"},
        {
            "name": "T3",
            "kind": "production",
            "status": "wont_do",
            "wont_do_reason": "dropped",
        },
        {"name": "M1", "kind": "meta", "status": "open"},
        {"name": "M2", "kind": "meta", "status": "closed"},
    ]
    store = _make_v9_store(tmp_path, seed_rows=seed)
    conn = _run_only_mig_009(store)
    try:
        rows = conn.execute(
            "SELECT name, kind, status FROM tasks ORDER BY id"
        ).fetchall()
        assert [(r["name"], r["kind"], r["status"]) for r in rows] == [
            ("T1", "production", "open"),
            ("T2", "production", "closed"),
            ("T3", "production", "wont_do"),
            ("M1", "meta", "open"),
            ("M2", "meta", "closed"),
        ]
        # NO transition rows -- only design/schema rows get backfill.
        count = conn.execute(
            "SELECT COUNT(*) FROM status_transitions"
        ).fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_mig_009_wont_do_reason_preserved_on_retired_migration(tmp_path):
    """v0.5 / D36: when a wont_do design/schema row migrates to retired, the
    wont_do_reason column value persists unchanged. Under the partition rule
    the column now serves both terminal verbs (wont_do for prod/meta, retire
    for design/schema); one terminal verb writes per row, so reusing the
    column is partition-safe."""
    seed = [
        {
            "name": "D25",
            "kind": "design",
            "status": "wont_do",
            "wont_do_reason": "verbatim reason preserved",
        },
    ]
    store = _make_v9_store(tmp_path, seed_rows=seed)
    conn = _run_only_mig_009(store)
    try:
        row = conn.execute(
            "SELECT wont_do_reason FROM tasks WHERE id=1"
        ).fetchone()
        assert row["wont_do_reason"] == "verbatim reason preserved"
    finally:
        conn.close()


def test_mig_009_partition_check_active_post_migration(tmp_path):
    """v0.5 / D36: after mig 009 lands, the kind/status partition CHECK refuses
    cross-partition INSERTs at the DB layer (defense in depth alongside the
    Pydantic Task partition validator)."""
    store = _make_v9_store(tmp_path)
    conn = _run_only_mig_009(store)
    try:
        # design + open is partition-invalid (design must be spec/retired).
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tasks(name, description, kind, status, stale, "
                "created_at, updated_at) "
                "VALUES ('bad', '', 'design', 'open', 0, "
                "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');"
            )
        # production + spec is also partition-invalid.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tasks(name, description, kind, status, stale, "
                "created_at, updated_at) "
                "VALUES ('bad', '', 'production', 'spec', 0, "
                "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');"
            )
    finally:
        conn.close()


def test_mig_009_status_check_active_post_migration(tmp_path):
    """v0.5 / D35+D36: after mig 009 lands, the status CHECK accepts the
    5-value enum (open, closed, wont_do, spec, retired) and refuses anything
    else. The partition CHECK still applies on top, so 'spec' on a production
    row would fail (covered by the partition test); here we just verify the
    enum widening."""
    store = _make_v9_store(tmp_path)
    conn = _run_only_mig_009(store)
    try:
        # bogus status is refused.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tasks(name, description, kind, status, stale, "
                "created_at, updated_at) "
                "VALUES ('bad', '', 'production', 'bogus', 0, "
                "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');"
            )
        # spec on design is accepted (partition-valid).
        conn.execute(
            "INSERT INTO tasks(name, description, kind, status, stale, "
            "created_at, updated_at) "
            "VALUES ('good_design', '', 'design', 'spec', 0, "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');"
        )
        # retired on schema is accepted (partition-valid).
        conn.execute(
            "INSERT INTO tasks(name, description, kind, status, stale, "
            "created_at, updated_at) "
            "VALUES ('good_schema', '', 'schema', 'retired', 0, "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');"
        )
    finally:
        conn.close()


def test_mig_009_fts_triggers_recreated(tmp_path):
    """v0.5: mig 009 rebuilds the tasks table; FTS triggers (D17 + D32 prefix
    indexing) must be recreated so search() still works post-migration."""
    seed = [
        {"name": "searchable", "kind": "design", "status": "open"},
    ]
    store = _make_v9_store(tmp_path, seed_rows=seed)
    conn = _run_only_mig_009(store)
    try:
        # The D32 prefix lookup should still resolve.
        hits = conn.execute(
            "SELECT rowid FROM tasks_fts WHERE tasks_fts MATCH 'D1'"
        ).fetchall()
        assert len(hits) == 1
        # Search for the literal task name should resolve too.
        hits = conn.execute(
            "SELECT rowid FROM tasks_fts WHERE tasks_fts MATCH 'searchable'"
        ).fetchall()
        assert len(hits) == 1
    finally:
        conn.close()


def test_mig_009_d32_prefix_indexing_preserved(tmp_path):
    """v0.5 / D32: post-mig 009, the synthesized auto-id prefix (D1, T2, etc.)
    is still indexed in the FTS index -- search by prefix resolves to the
    right row even after the table rebuild."""
    seed = [
        {"name": "design slice", "kind": "design", "status": "open"},
        {"name": "production task", "kind": "production", "status": "open"},
    ]
    store = _make_v9_store(tmp_path, seed_rows=seed)
    conn = _run_only_mig_009(store)
    try:
        # 'D1' should find the design row (kind='design' + id=1 -> prefix 'D1').
        hits = conn.execute(
            "SELECT rowid FROM tasks_fts WHERE tasks_fts MATCH 'D1'"
        ).fetchall()
        assert [h["rowid"] for h in hits] == [1]
        # 'T2' should find the production row (kind='production' + id=2 -> 'T2').
        hits = conn.execute(
            "SELECT rowid FROM tasks_fts WHERE tasks_fts MATCH 'T2'"
        ).fetchall()
        assert [h["rowid"] for h in hits] == [2]
    finally:
        conn.close()


def test_mig_009_fk_indexes_recreated(tmp_path):
    """v0.5: FK references on tasks (from task_labels, links,
    status_transitions, description_revisions) survive the table rebuild --
    child rows can still be inserted with valid task_id FKs, and orphan-FK
    INSERTs are refused."""
    seed = [
        {"name": "parent", "kind": "production", "status": "open"},
    ]
    store = _make_v9_store(tmp_path, seed_rows=seed)
    conn = _run_only_mig_009(store)
    try:
        # Re-enable FK enforcement on this connection (PRAGMA is per-connection
        # in SQLite and was left ON by _make_v9_store; the migration disables
        # FK pragma during the table swap but doesn't persist that setting).
        conn.execute("PRAGMA foreign_keys=ON;")
        # FK ref to existing task should work.
        conn.execute(
            "INSERT INTO task_labels(task_id, label) VALUES (1, 'test_label');"
        )
        # FK ref to nonexistent task should fail.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO task_labels(task_id, label) VALUES (999, 'bad');"
            )
    finally:
        conn.close()


def test_mig_009_foreign_key_check_clean(tmp_path):
    """v0.5: PRAGMA foreign_key_check returns empty after mig 009 (no orphan
    rows post-rebuild)."""
    seed = [
        {"name": "a", "kind": "production", "status": "open"},
        {"name": "b", "kind": "design", "status": "open"},
    ]
    store = _make_v9_store(tmp_path, seed_rows=seed)
    conn = _run_only_mig_009(store)
    try:
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        assert violations == []
    finally:
        conn.close()


def test_mig_009_idempotent_on_v10_database(tmp_path):
    """v0.5: running migrations again after mig 009 has applied is a no-op
    (handled by the runner's version-gated dispatch). The mig_009 function
    itself is not idempotent (the second tasks_new CREATE would fail), but
    the runner guarantees single-shot per database upgrade."""
    seed = [
        {"name": "a", "kind": "design", "status": "open"},
    ]
    _make_v9_store(tmp_path, seed_rows=seed)
    # First Core.open advances v9 -> v10 (runs mig_009).
    c = Core.open(start=tmp_path)
    try:
        assert migrations.get_schema_version(c.conn) >= 10
        row1 = c.conn.execute("SELECT status FROM tasks WHERE id=1").fetchone()
        assert row1["status"] == "spec"
        count1 = c.conn.execute(
            "SELECT COUNT(*) FROM status_transitions"
        ).fetchone()[0]
        assert count1 == 1
    finally:
        c.close_conn()
    # Second Core.open finds current >= target -> no migs run, no side-effects.
    c2 = Core.open(start=tmp_path)
    try:
        row2 = c2.conn.execute("SELECT status FROM tasks WHERE id=1").fetchone()
        assert row2["status"] == "spec"
        count2 = c2.conn.execute(
            "SELECT COUNT(*) FROM status_transitions"
        ).fetchone()[0]
        # No duplicate transition row.
        assert count2 == 1
    finally:
        c2.close_conn()


def test_mig_009_does_not_touch_description_revisions(tmp_path):
    """v0.5: mig 009 is a status migration, not a content edit. The S7
    description_revisions table is not written to (no spurious audit rows)."""
    seed = [
        {"name": "D1", "kind": "design", "status": "open"},
        {
            "name": "D2",
            "kind": "design",
            "status": "wont_do",
            "wont_do_reason": "dropped",
        },
    ]
    store = _make_v9_store(tmp_path, seed_rows=seed)
    conn = _run_only_mig_009(store)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM description_revisions"
        ).fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_mig_009_row_count_preserved(tmp_path):
    """v0.5: mig 009 does not lose or add task rows -- the row count is preserved."""
    seed = [
        {"name": "t_prod_open", "kind": "production", "status": "open"},
        {"name": "t_design_open", "kind": "design", "status": "open"},
        {"name": "t_design_closed", "kind": "design", "status": "closed"},
        {
            "name": "t_schema_wont_do",
            "kind": "schema",
            "status": "wont_do",
            "wont_do_reason": "dropped",
        },
        {"name": "t_meta_closed", "kind": "meta", "status": "closed"},
    ]
    store = _make_v9_store(tmp_path, seed_rows=seed)
    conn = _run_only_mig_009(store)
    try:
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        assert count == 5
    finally:
        conn.close()


def test_mig_009_full_v9_to_v10_round_trip_via_core_open(tmp_path):
    """v0.5: a v9-state DB opened via Core.open runs mig 009 transparently and
    reaches schema_version=10 (or higher if future migrations land). Integration
    smoke test for the full v9 -> v10 path."""
    seed = [
        {"name": "D1", "kind": "design", "status": "open"},
        {
            "name": "D25",
            "kind": "design",
            "status": "wont_do",
            "wont_do_reason": "supersede retired",
        },
        {"name": "T1", "kind": "production", "status": "open"},
    ]
    _make_v9_store(tmp_path, seed_rows=seed)
    c = Core.open(start=tmp_path)
    try:
        assert migrations.get_schema_version(c.conn) >= 10
        rows = c.conn.execute(
            "SELECT name, status FROM tasks ORDER BY id"
        ).fetchall()
        assert [(r["name"], r["status"]) for r in rows] == [
            ("D1", "spec"),
            ("D25", "retired"),
            ("T1", "open"),
        ]
    finally:
        c.close_conn()
