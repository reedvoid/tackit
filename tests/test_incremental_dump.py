"""D379 / S380 / S381: the incremental dump write.

A write rebuilds only the chunks holding changed rows and copies the rest from
the current file. The danger is a dump that silently differs from the db, so
every case asserts byte-identity with a full build. The converse cases pin that
the incremental path is really used, and that a stale index falls back to a full
build.
"""

import pytest

from tackit import migrations, schema, sync
from tackit.core import Core
from tackit.db import Store, connect, init_store


def _assert_matches_full_build(core):
    text = sync.dump_text(core.conn)
    assert core.store.sql_path.read_bytes() == sync.compress_dump(text)
    stored = core.conn.execute(
        "SELECT value FROM meta WHERE key = 'synced_sql_hash'"
    ).fetchone()[0]
    assert stored == sync.dump_hash(text)
    assert core.conn.execute("SELECT COUNT(*) FROM dump_changes").fetchone()[0] == 0


@pytest.fixture
def big(store_path):
    """A store with enough rows for many chunks: 60 specs with bodies, links and labels."""
    core = Core.open(start=store_path)
    specs = []
    for i in range(60):
        specs.append({
            "key": f"s{i}", "name": f"spec number {i}", "kind": "design",
            "desc": f"body of spec {i} " * 20, "labels": [f"group{i % 5}"],
            "depends_on": [],
        })
    for i in range(1, 60):
        specs[i]["depends_on"].append({"key": f"s{i - 1}", "because": f"spec {i} builds on {i - 1}"})
    core.load(specs)
    core.add("a note", kind="meta", description="notes")
    yield core
    core.close_conn()


def _chunk_count(core):
    return core.conn.execute("SELECT COUNT(*) FROM dump_chunks").fetchone()[0]


def test_the_store_spans_many_chunks(big):
    # Non-vacuous: the cases below only test copying if there is something to copy.
    assert _chunk_count(big) > 20


def test_every_kind_of_write_matches_a_full_build(big):
    _assert_matches_full_build(big)
    big.edit(1, description="early row changed", delta="edit an early row")
    _assert_matches_full_build(big)
    big.edit(30, description="middle row changed", delta="edit a middle row")
    _assert_matches_full_build(big)
    big.edit(60, description="last spec changed", delta="edit the last spec")
    _assert_matches_full_build(big)
    big.label_add(10, "brand-new")
    _assert_matches_full_build(big)
    big.label_rm(10, "group0")
    _assert_matches_full_build(big)
    big.link_add(5, 50, because="a late coupling between 5 and 50")
    _assert_matches_full_build(big)
    big.link_rm(5, 50)
    _assert_matches_full_build(big)
    big.reconcile_many([t.id for t in big.stale_worklist()])
    _assert_matches_full_build(big)
    big.edit_append(61, content="\nmore notes", delta="append to the note")
    _assert_matches_full_build(big)
    big.add("a new spec", kind="design", description="appended at the end")
    _assert_matches_full_build(big)


def test_edits_that_flip_a_rows_cut_decision_still_match(big):
    # About 1 in 8 row texts starts a chunk, so 24 different bodies on one row
    # flip its cut decision in both directions with near certainty.
    for i in range(24):
        big.edit(25, description=f"variant {i} " * (i + 1), delta=f"variant {i}")
        _assert_matches_full_build(big)


def test_a_raw_sql_write_on_another_connection_reaches_the_next_dump(big):
    other = connect(big.store.db_path)
    try:
        other.execute("UPDATE tasks SET name = 'renamed by raw sql' WHERE id = 40")
    finally:
        other.close()
    big.label_add(3, "unrelated-op")
    _assert_matches_full_build(big)
    assert "renamed by raw sql" in sync.read_dump(big.store.sql_path)


def test_a_write_with_a_valid_index_does_not_rebuild_everything(big, monkeypatch):
    def no_full_build(*args):
        raise AssertionError("full build used with a valid index")

    monkeypatch.setattr(sync, "_write_full", no_full_build)
    big.edit(30, description="incremental only", delta="edit through the index")
    monkeypatch.undo()
    _assert_matches_full_build(big)


def test_a_file_changed_underneath_falls_back_to_a_full_build(big, monkeypatch):
    # Same text, different bytes: the index no longer describes the file.
    text = sync.read_dump(big.store.sql_path)
    import gzip

    big.store.sql_path.write_bytes(gzip.compress(text.encode("utf-8"), 9, mtime=1))
    calls = []
    real = sync._write_full

    def counting(conn, store):
        calls.append(1)
        return real(conn, store)

    monkeypatch.setattr(sync, "_write_full", counting)
    big.edit(30, description="after an outside rewrite", delta="edit after outside rewrite")
    assert calls == [1]
    monkeypatch.undo()
    _assert_matches_full_build(big)


def test_loading_a_dump_records_no_changes_and_the_next_write_is_full(big):
    store = big.store
    big.close_conn()
    sync.rebuild_db_from_sql(store)
    core = Core.open(start=store.root)
    try:
        assert core.conn.execute("SELECT COUNT(*) FROM dump_changes").fetchone()[0] == 0
        assert _chunk_count(core) == 0
        core.edit(2, description="first write after a rebuild", delta="write after rebuild")
        _assert_matches_full_build(core)
        assert _chunk_count(core) > 20
        # The dump's own tail recreated the capture triggers, so the next write,
        # now incremental, still sees what changed.
        triggers = core.conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'dump_change_%'"
        ).fetchone()[0]
        assert triggers == 15
        core.edit(45, description="second write after a rebuild", delta="incremental after rebuild")
        _assert_matches_full_build(core)
    finally:
        core.close_conn()


def test_migration_12_adds_the_tables_and_triggers_to_a_version_11_store(tmp_path):
    init_store(tmp_path)
    core = Core.open(start=tmp_path)
    try:
        core.add("anchor", kind="design", description="before the migration")
        # The literal shape of a version-11 store: none of D379's objects.
        for statement in schema.POST_ROW_DDL:
            name = statement.split()[5]
            core.conn.execute(f"DROP TRIGGER {name}")
        core.conn.execute("DROP TABLE dump_chunks")
        core.conn.execute("DROP TABLE dump_changes")
        migrations._set_schema_version(core.conn, 11)
        sync.export(core.store)
    finally:
        core.close_conn()
    core = Core.open(start=tmp_path)
    try:
        assert migrations.get_schema_version(core.conn) == 12
        triggers = core.conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'dump_change_%'"
        ).fetchone()[0]
        assert triggers == 15
        assert core.get(1).description == "before the migration"
        _assert_matches_full_build(core)
        core.edit(1, description="after", delta="write after migrating")
        _assert_matches_full_build(core)
    finally:
        core.close_conn()


def test_local_meta_keys_never_reach_the_dump(big):
    text = sync.dump_text(big.conn)
    for key in sync.LOCAL_META_KEYS:
        assert f"'{key}'" not in text, key
    assert "'version'" in text  # the dumped keys are still there
