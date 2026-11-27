"""D379 / S380 / S381: the incremental dump write.

A write rebuilds only the chunks holding changed rows and copies the rest from
the current file. The danger is a dump that silently differs from the db, so
every case asserts byte-identity with a full build. The converse cases pin that
the incremental path is really used, and that a stale index falls back to a full
build.
"""

import shutil

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


def _count_full_builds(monkeypatch):
    calls = []
    real = sync._write_full

    def counting(conn, store):
        calls.append(store.root)
        return real(conn, store)

    monkeypatch.setattr(sync, "_write_full", counting)
    return calls


def _import_event(store, tmp_path):
    sync.import_sql(store, force=True)


def _restore_event(store, tmp_path):
    backup = sync.backup_db(store)
    sync.restore(store, backup)


def _legacy_event(store, tmp_path):
    # An older tackit wrote a strictly newer text dump beside the gz.
    text = sync.read_dump(store.sql_path)
    newer = text.replace("INSERT INTO meta (key, value) VALUES ('version', '", "INSERT INTO meta (key, value) VALUES ('version', '9", 1)
    store.legacy_sql_path.write_bytes(newer.encode("utf-8"))
    sync.adopt_legacy_dump(store)
    sync.import_sql(store, force=True)


def _pull_event(store, tmp_path):
    # Another clone's newer dump lands on disk; the next open rebuilds from it.
    other = tmp_path / "other"
    shutil.copytree(store.root, other)
    core = Core.open(start=other)
    try:
        core.add("made in the other clone", kind="design")
    finally:
        core.close_conn()
    shutil.copyfile(Store(other).sql_path, store.sql_path)
    assert sync.startup_sync(store) == "pulled-newer-sql"


@pytest.mark.parametrize(
    "event", [_import_event, _restore_event, _legacy_event, _pull_event],
    ids=["import", "restore", "legacy-adoption", "pull"],
)
def test_a_full_build_follows_each_outside_change(big, tmp_path, monkeypatch, event):
    # D379: the index is trusted only for the file it describes, so between an
    # outside change and the end of the first write a full build must run (restore
    # runs it itself through export; the others leave it to the first write).
    store = big.store
    big.close_conn()
    calls = _count_full_builds(monkeypatch)
    event(store, tmp_path)
    core = Core.open(start=store.root)
    try:
        core.edit(2, description="first write after the outside change", delta="write after outside change")
        assert store.root in calls  # a full build of THIS store, not the other clone's
        monkeypatch.undo()
        _assert_matches_full_build(core)
        core.edit(3, description="second write, incremental again", delta="incremental again")
        _assert_matches_full_build(core)
    finally:
        core.close_conn()


def test_a_crash_after_the_file_write_before_commit_recovers_on_open(big, monkeypatch):
    real = sync.finalize_mutation

    def write_then_crash(conn, store):
        real(conn, store)
        raise RuntimeError("crash between the file write and COMMIT")

    monkeypatch.setattr(sync, "finalize_mutation", write_then_crash)
    with pytest.raises(RuntimeError, match="crash between"):
        big.add("written to the dump only", kind="design")
    monkeypatch.undo()
    store = big.store
    big.close_conn()
    # The db rolled back; the dump on disk is one version ahead.
    core = Core.open(start=store.root)
    try:
        names = [row[0] for row in core.conn.execute("SELECT name FROM tasks")]
        assert "written to the dump only" in names  # rebuilt from the newer dump
        core.edit(4, description="after recovery", delta="write after recovery")
        _assert_matches_full_build(core)
    finally:
        core.close_conn()


def test_a_body_quoting_a_dump_line_neither_cuts_a_row_nor_breaks_the_index(big):
    # Found by rehearsing the upgrade on a real store: task bodies about the dump
    # format quote "\nINSERT INTO ..." lines. A cut may fall only at a true row
    # start, outside every quoted literal, or the chunk index cannot map it.
    quoting = "a body that quotes a dump line:\nINSERT INTO tasks (id) VALUES (1);\nand more 'quoted' text"
    for i in range(16):  # several distinct texts, so some inner line would hash to a cut
        big.edit(20 + i, description=f"{quoting} {i}", delta=f"quote a dump line {i}")
        _assert_matches_full_build(big)
    data = big.store.sql_path.read_bytes()
    assert b"\nINSERT INTO tasks (id) VALUES (1);" in sync.read_dump(big.store.sql_path).encode()
    assert data  # the file was written
    text = sync.dump_text(big.conn).encode("utf-8")
    for cut in sync.chunk_cuts(text)[1:-1]:
        assert text.count(b"'", 0, cut) % 2 == 0, cut  # every cut is outside a literal


def test_a_full_build_with_quoted_dump_lines_does_not_fail(store_path):
    core = Core.open(start=store_path)
    try:
        for i in range(40):
            core.add(f"spec {i}", kind="design",
                     description=f"quotes:\nINSERT INTO links (task_a) VALUES ({i});\nend {i}")
        sync.export(core.store)
        _assert_matches_full_build(core)
    finally:
        core.close_conn()
