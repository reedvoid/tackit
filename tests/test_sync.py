"""D18 sync state machine — the branches the engine suite doesn't already cover:
status() verdicts, import-without-force refusal, export/restore, error paths, and
the 'db exists but tackit.sql missing' startup branch.
"""

import pytest

from tackit import sync
from tackit.core import Core
from tackit.db import Store
from tackit.errors import SyncError


def _seed(store_path, n=1):
    c = Core.open(start=store_path)
    try:
        for i in range(n):
            c.add(f"task {i}")
    finally:
        c.close_conn()


def test_status_in_sync(store_path):
    _seed(store_path)
    info = sync.status(Store(store_path))
    assert info["verdict"] == "in sync"
    assert info["db_version"] == info["sql_version"]


def test_status_no_sql_directs_to_export(store_path):
    store = Store(store_path)
    if store.sql_path.exists():
        store.sql_path.unlink()
    info = sync.status(store)  # db exists (init), no tackit.sql yet
    assert info["db_exists"] is True and info["sql_exists"] is False
    assert "export" in info["verdict"]


def test_status_diverged_ambiguous(store_path):
    store = Store(store_path)
    _seed(store_path, 1)
    sql_v1 = store.sql_path.read_text()
    _seed(store_path, 1)  # db now newer than v1
    store.sql_path.write_text(sql_v1)  # disk reverted -> ambiguous (db newer)
    info = sync.status(store)
    assert "DIVERGED" in info["verdict"] or "ambiguous" in info["verdict"]


def test_import_refused_when_local_db_newer(store_path):
    store = Store(store_path)
    _seed(store_path, 1)
    sql_v1 = store.sql_path.read_text()
    _seed(store_path, 1)  # more mutations -> db strictly newer than v1
    store.sql_path.write_text(sql_v1)
    with pytest.raises(SyncError):
        sync.import_sql(store, force=False)


def test_import_force_adopts_the_file(store_path):
    store = Store(store_path)
    _seed(store_path, 1)
    msg = sync.import_sql(store, force=True)
    assert "imported" in msg


def test_export_then_restore_roundtrip(store_path):
    store = Store(store_path)
    _seed(store_path, 1)
    backup = sync.backup_db(store)
    assert backup is not None
    msg = sync.restore(store, backup)
    assert "restored" in msg
    assert store.db_path.exists()


def test_restore_missing_backup_fails_loud(store_path):
    store = Store(store_path)
    _seed(store_path, 1)
    with pytest.raises(SyncError):
        sync.restore(store, store.backups_dir / "tackit-does-not-exist.db")


def test_parse_version_from_bad_sql_fails_loud():
    with pytest.raises(SyncError):
        sync.parse_version_from_sql("this is not a valid tackit dump ;;;")


def test_startup_reexports_when_sql_missing(store_path):
    store = Store(store_path)
    _seed(store_path, 1)
    store.sql_path.unlink()  # delete the committed dump; binary db remains
    c = Core.open(start=store_path)  # startup_sync -> exported-missing-sql
    try:
        assert store.sql_path.exists()  # re-emitted
    finally:
        c.close_conn()


def test_list_backups_empty_then_one(store_path):
    store = Store(store_path)
    _seed(store_path, 1)
    assert sync.list_backups(store) == []
    sync.backup_db(store)
    assert len(sync.list_backups(store)) == 1
