"""D32 (v0.4) - auto-id name prefix: synthesized display + FTS-indexed prefix.

Pins the convention: every task carries a deterministic <kind_letter><id>
prefix in its agent-facing display and in the FTS index, so search("T<id>") /
search("D<id>") finds the row even when the user-supplied name has no such
substring. The stored name column stays bare.

Covers:
  * kind_letter / synthesize_prefixed_name helpers (all four kinds; rejection
    of unknown kind).
  * Task.prefixed_name, NeighborRef.prefixed_name, SearchHit.prefixed_name
    computed_fields (right shape; participate in model_dump).
  * FTS search by synthesized prefix on a fresh store (new triggers index the
    prefixed form, even though `name` doesn't contain the prefix).
  * Migration 008 round-trip: a pre-D32 (v=8) database with un-prefixed FTS
    entries gets rebuilt so search by prefix works.
"""

import sqlite3

import pytest

from tackit.core import Core
from tackit.db import init_store
from tackit.models import (
    NeighborRef,
    SearchHit,
    Task,
    kind_letter,
    synthesize_prefixed_name,
)


# --- helpers -------------------------------------------------------------


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


# --- 1. helpers ----------------------------------------------------------


def test_kind_letter_all_four_kinds():
    assert kind_letter("design") == "D"
    assert kind_letter("schema") == "S"
    assert kind_letter("production") == "T"
    assert kind_letter("meta") == "M"


def test_kind_letter_rejects_unknown_kind():
    with pytest.raises(KeyError):
        kind_letter("frobnitz")


def test_synthesize_prefixed_name_shape():
    # design + id=23 + name -> "D23 — name"
    assert synthesize_prefixed_name("design", 23, "the slice") == "D23 — the slice"
    assert synthesize_prefixed_name("schema", 1, "tasks") == "S1 — tasks"
    assert (
        synthesize_prefixed_name("production", 157, "fix ls() filter")
        == "T157 — fix ls() filter"
    )
    assert synthesize_prefixed_name("meta", 148, "epic") == "M148 — epic"


# --- 2. model computed_field ---------------------------------------------


def _ts():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def test_task_prefixed_name_uses_kind_and_id():
    t = Task(
        id=42,
        name="freeform title",
        kind="production",
        status="open",
        stale=False,
        created_at=_ts(),
        updated_at=_ts(),
    )
    assert t.prefixed_name == "T42 — freeform title"


def test_task_prefixed_name_in_model_dump():
    """The computed_field must be included in serialization so MCP
    responses carry it without explicit packing."""
    t = Task(
        id=7,
        name="slice content",
        kind="design",
        status="open",
        stale=False,
        created_at=_ts(),
        updated_at=_ts(),
    )
    dumped = t.model_dump()
    assert dumped["prefixed_name"] == "D7 — slice content"


def test_neighborref_prefixed_name():
    n = NeighborRef(id=11, name="another", status="closed", stale=False, kind="meta")
    assert n.prefixed_name == "M11 — another"
    assert n.model_dump()["prefixed_name"] == "M11 — another"


def test_searchhit_prefixed_name():
    h = SearchHit(
        id=5,
        name="indexed",
        score=1.23,
        status="open",
        kind="schema",
    )
    assert h.prefixed_name == "S5 — indexed"


# --- 3. FTS index carries the prefix -------------------------------------


def test_search_by_synthesized_prefix_finds_row(core: Core):
    # Create a production task. Its name has NO "T" string in it.
    core.add("frobnitz the gizmo", kind="production")  # id should be 1
    # Search for the synthesized prefix.
    hits = core.search("T1")
    assert len(hits) == 1
    assert hits[0].id == 1
    assert hits[0].name == "frobnitz the gizmo"
    # prefixed_name on the hit is the synthesized form.
    assert hits[0].prefixed_name == "T1 — frobnitz the gizmo"


def test_search_prefix_distinguishes_kinds(core: Core):
    # Two tasks at adjacent ids; different kinds.
    core.add("alpha bravo", kind="production")  # T1
    core.add("charlie delta", kind="design")  # D2
    # T1 finds only the production task.
    t_hits = core.search("T1")
    assert [h.id for h in t_hits] == [1]
    # D2 finds only the design task.
    d_hits = core.search("D2")
    assert [h.id for h in d_hits] == [2]


def test_search_by_bare_name_still_works(core: Core):
    """The literal user-supplied name is still in the indexed text (it's
    appended after the prefix), so existing search-by-keyword behavior is
    preserved alongside the new search-by-prefix capability."""
    core.add("rotate the JWT signing keys", kind="production")
    hits = core.search("JWT")
    assert len(hits) == 1
    assert hits[0].id == 1


def test_search_updated_name_reindexes_with_prefix(core: Core):
    """After edit() changes a task's name, the FTS row is replaced (the AU
    trigger fires delete-then-insert with the new prefixed name)."""
    core.add("old phrasing", kind="production")  # T1
    core.edit(1, name="new phrasing", delta="renamed for clarity")
    # Old name no longer indexed.
    assert core.search("old") == []
    # New name indexed.
    new_hits = core.search("new")
    assert len(new_hits) == 1
    # And prefix lookup still works.
    prefix_hits = core.search("T1")
    assert len(prefix_hits) == 1
    assert prefix_hits[0].name == "new phrasing"


# --- 4. Migration 008 round-trip -----------------------------------------


def test_migration_008_rebuilds_fts_for_preexisting_rows(tmp_path):
    """Simulate a v=8 store with rows already inserted under the old
    (un-prefixed) FTS triggers, then run migrations forward and assert
    search by synthesized prefix works on the carried-over rows."""
    # Step 1: open a fresh store at the current target. (Sets up at v=9 today.)
    init_store(tmp_path)

    # Step 2: roll the schema_version back to 8 and rebuild the FTS index in
    # the OLD un-prefixed shape, simulating a db captured before D32 landed.
    db_path = tmp_path / ".tackit" / "tackit.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Drop the prefix-aware triggers + FTS, recreate the bare-name versions.
        conn.execute("DROP TRIGGER IF EXISTS tasks_fts_ai;")
        conn.execute("DROP TRIGGER IF EXISTS tasks_fts_ad;")
        conn.execute("DROP TRIGGER IF EXISTS tasks_fts_au;")
        conn.execute("DROP TABLE IF EXISTS tasks_fts;")
        conn.executescript(
            "CREATE VIRTUAL TABLE tasks_fts USING fts5("
            "  name, description, content='tasks', content_rowid='id');"
            "CREATE TRIGGER tasks_fts_ai AFTER INSERT ON tasks BEGIN"
            "  INSERT INTO tasks_fts(rowid, name, description)"
            "  VALUES (new.id, new.name, new.description);"
            "END;"
        )
        # Insert a row directly so it's indexed under the OLD trigger.
        ts = _now_iso()
        conn.execute(
            "INSERT INTO tasks(name, description, kind, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy row", "", "production", "open", ts, ts),
        )
        # Roll meta.schema_version back to 8.
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', '8')"
        )
        conn.commit()
    finally:
        conn.close()

    # Step 3: open Core -- run_pending_migrations runs mig_008 because
    # schema.SCHEMA_VERSION is now 9.
    core = Core.open(start=tmp_path)
    try:
        # Search by synthesized prefix must now find the carried-over row.
        hits = core.search("T1")
        assert len(hits) == 1
        assert hits[0].id == 1
        assert hits[0].name == "legacy row"
        assert hits[0].prefixed_name == "T1 — legacy row"
    finally:
        core.close_conn()


def test_migration_008_post_migration_inserts_use_new_triggers(tmp_path):
    """After mig_008 runs, NEW inserts (via Core.add) are also indexed with
    the prefix -- confirming the triggers were correctly recreated by the
    migration."""
    init_store(tmp_path)
    core = Core.open(start=tmp_path)
    try:
        core.add("inserted post-migration", kind="design")  # D1
        hits = core.search("D1")
        assert len(hits) == 1
        assert hits[0].id == 1
        assert hits[0].kind == "design"
    finally:
        core.close_conn()
