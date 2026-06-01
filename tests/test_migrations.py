"""T83 -- migration runner mechanics. Covers the registry walk, schema_version
progression, rollback on error, contiguity refusal, downgrade refusal, and the
per-migration D18 finalize (tackit.sql re-dump).

Per-migration *content* tests live alongside the migrations themselves
(T84/T85/T86 will add their own). Here we focus on the runner with fake
injected migrations so the mechanics can be exercised before any real
migration script exists.
"""

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
