"""T301 / D18: the store dump is row-chunked gzip (tackit.sql.gz).

Pins the format's reason for existing (unchanged rows stay byte-identical, which
plain gzip does not do), loud refusal of a damaged dump, and each row of D18's
legacy-adoption table.
"""

import gzip
import shutil
import subprocess
import threading
import zlib

import pytest

from tackit import cli, sync
from tackit.core import Core
from tackit.db import Store, init_store
from tackit.errors import SyncError


def _add_design(store_path, name):
    core = Core.open(start=store_path)
    try:
        return core.add(name, kind="design").id
    finally:
        core.close_conn()


def _task_name(store_path, task_id):
    core = Core.open(start=store_path)
    try:
        return core.get(task_id).name
    finally:
        core.close_conn()


def _make_legacy_only(store):
    """Put the store in its pre-T301 shape: a text dump and no gz."""
    text = sync.read_dump(store.sql_path)
    store.legacy_sql_path.write_bytes(text.encode("utf-8"))
    store.sql_path.unlink()
    return text


def _shared_prefix_length(left, right):
    count = 0
    for a, b in zip(left, right):
        if a != b:
            break
        count += 1
    return count


def _rows(count):
    lines = ["-- header", "BEGIN;"]
    for i in range(count):
        lines.append(f"INSERT INTO tasks (id, name) VALUES ({i}, 'row number {i} {i * 7919}');")
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def _shared_suffix_length(left, right):
    """Shared tail of two gzip files, ignoring the 8-byte CRC + size trailer, which
    differs whenever the text does."""
    return _shared_prefix_length(left[-9::-1], right[-9::-1])


# --- format ---------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "",
        "no row boundary at all",
        _rows(500),
        "INSERT INTO tasks VALUES ('héllo ✓ 日本語');\n",
        "INSERT INTO tasks VALUES ('carriage\r\nreturn\rkept');\n",
        "\nINSERT INTO a\nINSERT INTO a\n",  # boundary at the very start, repeated
        "INSERT INTO t VALUES ('a body quoting\nINSERT INTO fake VALUES (1);\nrow');\n",
    ],
)
def test_compress_round_trip_is_byte_identical(tmp_path, text):
    path = tmp_path / "dump.sql.gz"
    sync.write_dump(path, text)
    assert sync.read_dump(path) == text


def test_compress_is_deterministic():
    text = _rows(2000)
    assert sync.compress_dump(text) == sync.compress_dump(text)


def test_flush_points_are_row_aligned_at_about_one_row_in_eight():
    """A stream with no flushes would still round-trip, so check the flush rule
    itself: each Z_FULL_FLUSH ends in the empty stored block 00 00 FF FF, and
    everything before it decompresses to whole rows. 2000 rows at 1 in 8 is about
    250 flushes; the bounds refuse 1 in 4 (about 500) and 1 in 16 (about 125)."""
    data = sync.compress_dump(_rows(2000))
    marker = b"\x00\x00\xff\xff"
    flushes = 0
    position = data.find(marker)
    while position != -1:
        decompressor = zlib.decompressobj(31)
        so_far = decompressor.decompress(data[: position + len(marker)])
        assert so_far.startswith(b"-- header")
        assert so_far.endswith(b");") or so_far.endswith(b"BEGIN;")
        flushes += 1
        position = data.find(marker, position + 1)
    assert 180 <= flushes <= 330


def test_early_row_change_keeps_later_bytes_identical():
    """The D18 property: changing an early row leaves the rest of the file shared."""
    before = _rows(4000)
    after = before.replace("row number 3 ", "row number 3 CHANGED ")
    a = sync.compress_dump(before)
    b = sync.compress_dump(after)
    assert _shared_suffix_length(a, b) > len(a) * 0.9


def test_plain_gzip_lacks_that_property():
    """Converse: proves the test above can fail, and why plain gzip was rejected."""
    before = _rows(4000)
    after = before.replace("row number 3 ", "row number 3 CHANGED ")
    a = gzip.compress(before.encode("utf-8"), 1, mtime=0)
    b = gzip.compress(after.encode("utf-8"), 1, mtime=0)
    assert _shared_suffix_length(a, b) < len(a) * 0.1


@pytest.mark.skipif(shutil.which("gunzip") is None, reason="no system gunzip")
def test_system_gunzip_reads_the_file():
    text = _rows(2000)
    result = subprocess.run(
        ["gunzip", "-c"], input=sync.compress_dump(text), capture_output=True, check=True
    )
    assert result.stdout.decode("utf-8") == text


def test_mutation_writes_gz_and_no_text_dump(store_path):
    store = Store(store_path)
    _add_design(store_path, "anchor")
    assert store.sql_path.name == "tackit.sql.gz"
    assert store.sql_path.read_bytes()[:2] == b"\x1f\x8b"
    assert not store.legacy_sql_path.exists()


def test_synced_hash_covers_decompressed_text_not_bytes(store_path):
    """Recompressing the same text differently must not read as divergence."""
    store = Store(store_path)
    _add_design(store_path, "anchor")
    text = sync.read_dump(store.sql_path)
    store.sql_path.write_bytes(gzip.compress(text.encode("utf-8"), 9, mtime=12345))
    assert sync.status(store)["verdict"] == "in sync"
    assert sync.startup_sync(store) == "in-sync"


def test_fresh_clone_rebuilds_from_gz(store_path):
    store = Store(store_path)
    task_id = _add_design(store_path, "survives the clone")
    for suffix in ("", "-wal", "-shm"):
        path = store.db_path.with_name(store.db_path.name + suffix)
        if path.exists():
            path.unlink()
    assert _task_name(store_path, task_id) == "survives the clone"


def test_non_ascii_and_cr_survive_dump_and_rebuild(store_path):
    store = Store(store_path)
    name = "naïve ✓ 日本語"
    core = Core.open(start=store_path)
    try:
        task_id = core.add(name, kind="design", description="line one\r\nline two").id
    finally:
        core.close_conn()
    for suffix in ("", "-wal", "-shm"):
        path = store.db_path.with_name(store.db_path.name + suffix)
        if path.exists():
            path.unlink()
    core = Core.open(start=store_path)
    try:
        task = core.get(task_id)
        assert task.name == name
        assert task.description == "line one\r\nline two"
    finally:
        core.close_conn()


# --- damaged dumps refuse loudly ------------------------------------------

def _not_gzip(data):
    return b"this is not gzip at all"


def _truncated(data):
    return data[: len(data) // 2]


def _bad_crc(data):
    return data[:-8] + b"\x00" * 8


def _empty(data):
    return b""


def _not_utf8(data):
    return gzip.compress(b"\xff\xfe not utf-8", mtime=0)


DAMAGES = [_not_gzip, _truncated, _bad_crc, _empty, _not_utf8]


@pytest.mark.parametrize("damage", DAMAGES)
def test_read_dump_refuses_damage_naming_the_file(store_path, damage):
    store = Store(store_path)
    _add_design(store_path, "anchor")
    store.sql_path.write_bytes(damage(store.sql_path.read_bytes()))
    with pytest.raises(SyncError, match="tackit.sql.gz"):
        sync.read_dump(store.sql_path)


@pytest.mark.parametrize("damage", DAMAGES)
def test_open_status_import_refuse_damage_and_keep_the_db(store_path, damage):
    store = Store(store_path)
    task_id = _add_design(store_path, "must survive")
    good_dump = store.sql_path.read_bytes()
    store.sql_path.write_bytes(damage(good_dump))

    with pytest.raises(SyncError):
        Core.open(start=store_path)
    with pytest.raises(SyncError):
        sync.status(store)
    with pytest.raises(SyncError):
        sync.import_sql(store, force=True)

    assert sync.list_backups(store) == []  # no override path was entered
    store.sql_path.write_bytes(good_dump)
    assert sync.status(store)["verdict"] == "in sync"  # db hash still matches the dump
    assert _task_name(store_path, task_id) == "must survive"


@pytest.mark.parametrize("damage", DAMAGES)
def test_fresh_clone_with_damaged_dump_refuses(store_path, damage):
    """With no db to compare against, a damaged dump must not become an empty store."""
    store = Store(store_path)
    _add_design(store_path, "anchor")
    store.sql_path.write_bytes(damage(store.sql_path.read_bytes()))
    for suffix in ("", "-wal", "-shm"):
        path = store.db_path.with_name(store.db_path.name + suffix)
        if path.exists():
            path.unlink()
    with pytest.raises(SyncError):
        Core.open(start=store_path)
    assert store.sql_path.exists()


# --- legacy adoption table ------------------------------------------------

def test_legacy_only_is_converted_without_rebuild_or_backup(store_path):
    store = Store(store_path)
    task_id = _add_design(store_path, "from the text era")
    text = _make_legacy_only(store)

    assert _task_name(store_path, task_id) == "from the text era"

    assert not store.legacy_sql_path.exists()
    assert sync.read_dump(store.sql_path) == text
    assert sync.list_backups(store) == []  # hash matched, so no pull fired
    assert sync.status(store)["verdict"] == "in sync"


def test_legacy_only_fresh_clone_builds_the_db(store_path):
    store = Store(store_path)
    task_id = _add_design(store_path, "cloned from the text era")
    _make_legacy_only(store)
    for suffix in ("", "-wal", "-shm"):
        path = store.db_path.with_name(store.db_path.name + suffix)
        if path.exists():
            path.unlink()
    assert _task_name(store_path, task_id) == "cloned from the text era"
    assert not store.legacy_sql_path.exists()


def test_both_identical_removes_legacy_recovering_an_interrupted_adoption(store_path):
    """Crash after writing the gz but before removing the text leaves exactly this."""
    store = Store(store_path)
    _add_design(store_path, "anchor")
    text = sync.read_dump(store.sql_path)
    store.legacy_sql_path.write_bytes(text.encode("utf-8"))
    gz_before = store.sql_path.read_bytes()

    assert sync.adopt_legacy_dump(store) == "removed-identical-legacy"

    assert not store.legacy_sql_path.exists()
    assert store.sql_path.read_bytes() == gz_before


def test_both_with_legacy_newer_adopts_it_and_pulls(store_path):
    """An older tackit wrote a newer text dump beside the gz."""
    store = Store(store_path)
    first = _add_design(store_path, "one")
    older_gz = store.sql_path.read_bytes()
    second = _add_design(store_path, "two")
    newer_text = sync.read_dump(store.sql_path)
    store.legacy_sql_path.write_bytes(newer_text.encode("utf-8"))
    store.sql_path.write_bytes(older_gz)
    # roll the db back to the older state so the adopted dump reads as a pull
    sync.rebuild_db_from_sql(store)

    assert _task_name(store_path, second) == "two"

    assert _task_name(store_path, first) == "one"
    assert not store.legacy_sql_path.exists()
    assert sync.read_dump(store.sql_path) == newer_text


@pytest.mark.parametrize("legacy_is", ["older", "same-version-different-text"])
def test_both_conflicting_refuses_naming_both_and_changes_nothing(store_path, legacy_is):
    store = Store(store_path)
    _add_design(store_path, "one")
    older_text = sync.read_dump(store.sql_path)
    _add_design(store_path, "two")
    newer_text = sync.read_dump(store.sql_path)
    if legacy_is == "older":
        legacy_text = older_text
    else:
        legacy_text = newer_text.replace("'two'", "'not two'")
        assert legacy_text != newer_text
    store.legacy_sql_path.write_bytes(legacy_text.encode("utf-8"))
    gz_before = store.sql_path.read_bytes()

    with pytest.raises(SyncError) as excinfo:
        Core.open(start=store_path)

    assert "tackit.sql " in str(excinfo.value) and "tackit.sql.gz" in str(excinfo.value)
    assert store.legacy_sql_path.read_bytes() == legacy_text.encode("utf-8")
    assert store.sql_path.read_bytes() == gz_before


def test_damaged_gz_beside_valid_legacy_refuses_and_keeps_both(store_path):
    """A readable legacy file must not silently replace a gz we can't compare it to."""
    store = Store(store_path)
    _add_design(store_path, "anchor")
    text = sync.read_dump(store.sql_path)
    store.legacy_sql_path.write_bytes(text.encode("utf-8"))
    store.sql_path.write_bytes(b"damaged")
    with pytest.raises(SyncError, match="tackit.sql.gz"):
        Core.open(start=store_path)
    assert store.legacy_sql_path.read_bytes() == text.encode("utf-8")
    assert store.sql_path.read_bytes() == b"damaged"


def test_legacy_removed_by_another_session_after_we_read_it(store_path, monkeypatch):
    """Two sessions open during first upgrade: the other one finishes first."""
    store = Store(store_path)
    _add_design(store_path, "anchor")
    text = _make_legacy_only(store)
    real_parse = sync.parse_version_from_sql

    def other_session_removes_it(sql_text):
        store.legacy_sql_path.unlink()
        return real_parse(sql_text)

    monkeypatch.setattr(sync, "parse_version_from_sql", other_session_removes_it)
    assert sync.adopt_legacy_dump(store) == "converted-legacy"
    assert sync.read_dump(store.sql_path) == text


def test_concurrent_writers_do_not_collide_on_the_temp_file(tmp_path):
    """Before temp names were per-writer, one writer's os.replace moved the shared
    temp file out from under the other, raising FileNotFoundError."""
    path = tmp_path / "tackit.sql.gz"
    text = _rows(200)
    errors = []
    barrier = threading.Barrier(4)

    def writer():
        barrier.wait()
        try:
            for _ in range(50):
                sync.write_dump(path, text)
        except OSError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert sync.read_dump(path) == text
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_dump_write_rolls_the_mutation_back(store_path, monkeypatch):
    """Disk failure mid-dump: mutation refused, dump and db both still the old state."""
    store = Store(store_path)
    _add_design(store_path, "anchor")
    dump_before = store.sql_path.read_bytes()

    def fail(*args):
        raise OSError("disk full")

    core = Core.open(start=store_path)
    try:
        monkeypatch.setattr(sync.os, "replace", fail)
        with pytest.raises(OSError, match="disk full"):
            core.add("never lands", kind="design")
        monkeypatch.undo()
    finally:
        core.close_conn()

    assert store.sql_path.read_bytes() == dump_before
    assert list(store.dir.glob("*.tmp")) == []
    assert sync.status(store)["verdict"] == "in sync"


def test_gitignore_body_covers_temp_files_but_not_the_dump(tmp_path):
    """A crash can strand a temp file; it must not get committed, the dump must."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    store = init_store(tmp_path)
    for name, ignored in (("tackit.sql.gz.1.2.tmp", 0), ("tackit.sql.gz", 1), ("tackit.db", 0)):
        result = subprocess.run(
            ["git", "-C", str(tmp_path), "check-ignore", "-q", f".tackit/{name}"]
        )
        assert result.returncode == ignored, name
    assert store.dir.is_dir()


def test_cli_converts_legacy_and_reports_it_in_sync(store_path, monkeypatch, capsys):
    store = Store(store_path)
    _add_design(store_path, "anchor")
    _make_legacy_only(store)
    monkeypatch.chdir(store_path)

    assert cli.main(["status"]) == 0
    assert "converts" in capsys.readouterr().out
    assert store.legacy_sql_path.exists()  # status only reports

    assert cli.main(["export"]) == 0
    capsys.readouterr()
    assert not store.legacy_sql_path.exists()

    assert cli.main(["status"]) == 0
    assert "in sync" in capsys.readouterr().out


def test_cli_status_on_damaged_dump_fails_cleanly(store_path, monkeypatch, capsys):
    store = Store(store_path)
    _add_design(store_path, "anchor")
    store.sql_path.write_bytes(b"damaged")
    monkeypatch.chdir(store_path)

    assert cli.main(["status"]) != 0
    captured = capsys.readouterr()
    assert "tackit.sql.gz" in captured.err + captured.out
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("content", [b"", b"not a dump ;;;", b"CREATE TABLE unrelated (x);"])
def test_invalid_legacy_refuses_and_keeps_it(store_path, content):
    store = Store(store_path)
    _add_design(store_path, "anchor")
    store.legacy_sql_path.write_bytes(content)
    gz_before = store.sql_path.read_bytes()
    with pytest.raises(SyncError):
        Core.open(start=store_path)
    assert store.legacy_sql_path.exists()
    assert store.sql_path.read_bytes() == gz_before


def test_non_utf8_legacy_refuses_and_keeps_it(store_path):
    store = Store(store_path)
    _add_design(store_path, "anchor")
    store.legacy_sql_path.write_bytes(b"\xff\xfe nope")
    with pytest.raises(SyncError, match="tackit.sql"):
        Core.open(start=store_path)
    assert store.legacy_sql_path.exists()


def test_no_legacy_is_a_no_op(store_path):
    store = Store(store_path)
    _add_design(store_path, "anchor")
    assert sync.adopt_legacy_dump(store) == "no-legacy"


@pytest.mark.parametrize(
    "command",
    [
        lambda store: sync.export(store),
        lambda store: sync.import_sql(store),
    ],
    ids=["export", "import"],
)
def test_sync_commands_adopt_legacy_first(store_path, command):
    store = Store(store_path)
    _add_design(store_path, "anchor")
    _make_legacy_only(store)
    command(store)
    assert not store.legacy_sql_path.exists()
    assert store.sql_path.exists()


def test_restore_adopts_legacy_first(store_path):
    store = Store(store_path)
    _add_design(store_path, "anchor")
    backup = sync.backup_db(store)
    _make_legacy_only(store)
    sync.restore(store, backup)
    assert not store.legacy_sql_path.exists()
    assert store.sql_path.exists()


def test_status_reports_legacy_and_changes_nothing(store_path):
    store = Store(store_path)
    _add_design(store_path, "anchor")
    text = _make_legacy_only(store)
    info = sync.status(store)
    assert info["legacy_sql_exists"] is True
    assert "tackit.sql" in info["verdict"] and "converts" in info["verdict"]
    assert store.legacy_sql_path.read_bytes() == text.encode("utf-8")
    assert not store.sql_path.exists()


def test_status_without_legacy_reports_false(store_path):
    store = Store(store_path)
    _add_design(store_path, "anchor")
    assert sync.status(store)["legacy_sql_exists"] is False
