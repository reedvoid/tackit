"""D60 / D379 / S42: the open fast path. When the dump file's size, mtime_ns and
inode equal what this db recorded when it last wrote or verified it, startup sync
is in sync without reading the file. Any difference runs the full check, which
still detects a newer or diverged dump. `tackit status` always reads the file.
"""

import gzip
import os
import shutil

import pytest

from tackit import sync
from tackit.core import Core
from tackit.db import Store, connect


def _write_something(store_path, name="anchor"):
    core = Core.open(start=store_path)
    try:
        core.add(name, kind="design", description=f"body of {name}")
    finally:
        core.close_conn()


@pytest.fixture
def reads(monkeypatch):
    """Count read_dump calls without changing what it returns."""
    calls = []
    real = sync.read_dump

    def counting(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(sync, "read_dump", counting)
    return calls


def _stored_stat(store_path):
    conn = connect(Store(store_path).db_path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'synced_sql_stat'").fetchone()
        return None if row is None else row[0]
    finally:
        conn.close()


def test_an_unchanged_file_is_in_sync_without_being_read(store_path, reads):
    _write_something(store_path)
    reads.clear()
    assert sync.startup_sync(Store(store_path)) == "in-sync"
    assert reads == []


def test_a_recompressed_file_is_read_found_in_sync_and_recorded(store_path, reads):
    _write_something(store_path)
    store = Store(store_path)
    text = sync.read_dump(store.sql_path)
    store.sql_path.write_bytes(gzip.compress(text.encode("utf-8"), 9, mtime=7))
    reads.clear()
    assert sync.startup_sync(store) == "in-sync"
    assert len(reads) == 1  # size changed: the full check ran
    reads.clear()
    assert sync.startup_sync(store) == "in-sync"
    assert reads == []  # the verified stat was recorded


def test_an_mtime_change_alone_runs_the_full_check(store_path, reads):
    _write_something(store_path)
    store = Store(store_path)
    info = store.sql_path.stat()
    os.utime(store.sql_path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
    reads.clear()
    assert sync.startup_sync(store) == "in-sync"
    assert len(reads) == 1


def test_an_inode_change_alone_runs_the_full_check(store_path, reads):
    _write_something(store_path)
    store = Store(store_path)
    info = store.sql_path.stat()
    copy = store.sql_path.with_name("copy.gz")
    shutil.copyfile(store.sql_path, copy)
    os.utime(copy, ns=(info.st_atime_ns, info.st_mtime_ns))
    os.replace(copy, store.sql_path)
    after = store.sql_path.stat()
    assert (after.st_size, after.st_mtime_ns) == (info.st_size, info.st_mtime_ns)
    assert after.st_ino != info.st_ino
    reads.clear()
    assert sync.startup_sync(store) == "in-sync"
    assert len(reads) == 1


def test_a_newer_dump_from_a_pull_is_still_detected(tmp_path):
    from tackit.db import init_store

    origin = tmp_path / "origin"
    clone = tmp_path / "clone"
    init_store(origin)
    _write_something(origin, "first")
    shutil.copytree(origin, clone)
    _write_something(origin, "second")  # the origin moves ahead
    shutil.copyfile(Store(origin).sql_path, Store(clone).sql_path)  # the pull
    assert sync.startup_sync(Store(clone)) == "pulled-newer-sql"
    core = Core.open(start=clone)
    try:
        names = [row[0] for row in core.conn.execute("SELECT name FROM tasks ORDER BY id")]
    finally:
        core.close_conn()
    assert names == ["first", "second"]


def test_every_writer_records_the_stat(store_path):
    store = Store(store_path)
    _write_something(store_path)
    assert _stored_stat(store_path) == sync._stat_key(store.sql_path)
    sync.export(store)
    assert _stored_stat(store_path) == sync._stat_key(store.sql_path)
    sync.rebuild_db_from_sql(store)
    assert _stored_stat(store_path) == sync._stat_key(store.sql_path)


def test_status_always_reads_the_file(store_path, reads):
    _write_something(store_path)
    reads.clear()
    assert sync.status(Store(store_path))["verdict"] == "in sync"
    assert len(reads) == 1
