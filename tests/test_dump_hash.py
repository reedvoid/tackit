"""D379 / D60: the integrity hash is a tagged chain of per-chunk digests of the
decompressed dump text; an untagged stored value is the older whole-text sha256.

Pins both directions: any text change changes the hash, a recompression does
not, and a store whose meta still holds an untagged hash opens in sync (the
first open after the upgrade must not be refused as divergence).
"""

import hashlib

from tackit import sync
from tackit.core import Core
from tackit.db import Store


def _rows(count, changed=None):
    lines = ["-- header", "BEGIN;"]
    for i in range(count):
        text = f"row number {i} {i * 7919}"
        if i == changed:
            text += " CHANGED"
        lines.append(f"INSERT INTO tasks (id, name) VALUES ({i}, '{text}');")
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def test_the_hash_is_tagged_and_stable():
    text = _rows(500)
    first = sync.dump_hash(text)
    assert first.startswith("c1:")
    assert len(first) == len("c1:") + 64
    assert sync.dump_hash(text) == first


def test_any_one_row_change_changes_the_hash():
    base = sync.dump_hash(_rows(500))
    for row in (0, 250, 499):
        assert sync.dump_hash(_rows(500, changed=row)) != base, row


def test_the_chained_hash_is_not_the_whole_text_sha256():
    text = _rows(500)
    assert sync.dump_hash(text)[len("c1:"):] != hashlib.sha256(text.encode()).hexdigest()


def test_an_untagged_stored_hash_is_compared_as_a_whole_text_sha256():
    text = _rows(500)
    legacy = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert sync.hash_matches(legacy, text)
    assert not sync.hash_matches(legacy, _rows(500, changed=3))


def test_a_tagged_stored_hash_is_compared_chained():
    text = _rows(500)
    assert sync.hash_matches(sync.dump_hash(text), text)
    assert not sync.hash_matches(sync.dump_hash(text), _rows(500, changed=3))
    assert not sync.hash_matches("", text)


def _stored_hash(store_path):
    from tackit.db import connect

    conn = connect(Store(store_path).db_path)
    try:
        return conn.execute("SELECT value FROM meta WHERE key = 'synced_sql_hash'").fetchone()[0]
    finally:
        conn.close()


def test_export_and_rebuild_store_the_tagged_hash(store_path):
    # The untagged compare would hide a writer that kept the old hash, so pin each.
    core = Core.open(start=store_path)
    try:
        core.add("anchor", kind="design")
    finally:
        core.close_conn()
    store = Store(store_path)
    sync.export(store)
    assert _stored_hash(store_path).startswith("c1:")
    sync.rebuild_db_from_sql(store)
    assert _stored_hash(store_path).startswith("c1:")
    assert _stored_hash(store_path) == sync.dump_hash(sync.read_dump(store.sql_path))


def test_a_store_holding_an_untagged_hash_opens_in_sync_and_retags_on_write(store_path):
    core = Core.open(start=store_path)
    try:
        core.add("anchor", kind="design")
        text = sync.read_dump(core.store.sql_path)
        core.conn.execute(
            "UPDATE meta SET value = ? WHERE key = 'synced_sql_hash'",
            (hashlib.sha256(text.encode("utf-8")).hexdigest(),),
        )
    finally:
        core.close_conn()
    assert sync.startup_sync(Store(store_path)) == "in-sync"
    core = Core.open(start=store_path)
    try:
        core.add("second", kind="design")
        stored = core.conn.execute(
            "SELECT value FROM meta WHERE key = 'synced_sql_hash'"
        ).fetchone()[0]
    finally:
        core.close_conn()
    assert stored.startswith("c1:")
    assert sync.status(Store(store_path))["verdict"] == "in sync"
