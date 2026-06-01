"""Test suite, organized by design slice (D#) / schema table (S#).

Each test names the slice it pins so a failure points straight at the design
item it protects.
"""

import pytest

from tackit import sync
from tackit.core import Core
from tackit.db import Store, init_store
from tackit.errors import InvariantError, NotFoundError, SyncError, ValidationError


@pytest.fixture
def store_path(tmp_path):
    init_store(tmp_path)
    return tmp_path


@pytest.fixture
def core(store_path):
    c = Core.open(start=store_path)
    yield c
    c.close_conn()


# --- D1 / S1-S6: store + schema ---------------------------------------------

def test_d1_init_creates_store_and_gitignore(tmp_path):
    store = init_store(tmp_path)
    assert store.db_path.exists()
    gi = (store.dir / ".gitignore").read_text()
    assert "tackit.db" in gi and "backups/" in gi


# --- D3 / D2: task CRUD + typed boundary ------------------------------------

def test_d3_add_and_get(core):
    t = core.add("parse FTS5 query", description="tokenize MATCH terms")
    assert t.id == 1 and t.status == "open" and t.stale is False
    assert core.get(1).name == "parse FTS5 query"


def test_d2_empty_name_refused(core):
    with pytest.raises(ValidationError):
        core.add("   ")


def test_d3_get_missing_fails_loud(core):
    with pytest.raises(NotFoundError):
        core.get(999)


# --- D4 / S2: labels --------------------------------------------------------

def test_d4_labels_add_list_remove(core):
    core.add("rank results", labels=["search", "core"])
    assert core.labels_of(1) == ["core", "search"]
    core.label_rm(1, "core")
    assert core.labels_of(1) == ["search"]


# --- D5 / D6 / D14: edges, traversal, invariants ----------------------------

def test_d5_d6_symmetric_link_traversal(core):
    # Under v0.3.0 symmetric semantics (T86), both `dependencies_of` and
    # `dependents_of` return the same linked-neighbor set; the names are kept
    # for API stability and rename in T93/T96.
    core.add("a")  # T1
    core.add("b")  # T2
    core.link_add(2, 1, because="test fixture", delta="test")  # link T1 <-> T2
    assert [n.id for n in core.dependencies_of(2)] == [1]
    assert [n.id for n in core.dependents_of(1)] == [2]
    # Symmetric: querying from the other endpoint returns the same neighbor set.
    assert [n.id for n in core.dependencies_of(1)] == [2]
    assert [n.id for n in core.dependents_of(2)] == [1]


def test_d14_self_link_refused(core):
    core.add("a")
    with pytest.raises(InvariantError):
        core.link_add(1, 1, because="test fixture", delta="test")


def test_d5_duplicate_link_is_noop(core):
    # Under symmetric semantics there is no directed cycle; what used to be a
    # "cycle" (T1->T2 then T2->T1) is the same canonical link {T1, T2}, so the
    # second dep_add is a no-op (idempotent), not an error.
    core.add("a")  # T1
    core.add("b")  # T2
    core.link_add(1, 2, because="test fixture", delta="test")  # link T1 <-> T2
    core.link_add(2, 1, because="test fixture", delta="test")  # same link, reversed args -> idempotent
    # Exactly one row in the links table.
    n = core.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    assert n == 1


def test_d14_edge_to_missing_task_refused(core):
    core.add("a")
    with pytest.raises(NotFoundError):
        core.link_add(1, 999, because="test fixture", delta="test")


# --- D7 / D8: status, stale invariant, history ------------------------------

def test_d7_invariant_stale_implies_open(core):
    core.add("a")  # T1
    core.add("b")  # T2 depends_on T1
    core.link_add(2, 1, because="test fixture", delta="test")
    core.close(2)  # T2 closed
    core.edit(1, description="changed", delta="test")  # stales T2 -> forces it back open
    t2 = core.get(2)
    assert t2.stale is True and t2.status == "open"


def test_d8_history_logged_through_reopen(core):
    core.add("a")
    core.close(1)
    core.reopen(1)
    seq = [(h.from_status, h.to_status) for h in core.history(1)]
    assert seq == [(None, "open"), ("open", "closed"), ("closed", "open")]


# --- D9: slice fetch --------------------------------------------------------

def test_d9_slice_fetch(core):
    # Slice still exposes `dependencies` + `dependents` fields for API stability
    # (T93/T96 rename), but under symmetric semantics both return the same
    # linked-neighbor set.
    core.add("a", labels=["x"])  # T1
    core.add("b")  # T2
    core.link_add(1, 2, because="test fixture", delta="test")  # link T1 <-> T2
    s = core.show(1)
    assert s.task.id == 1
    assert s.labels == ["x"]
    assert [n.id for n in s.dependencies] == [2]
    assert [n.id for n in s.dependents] == [2]  # symmetric: same set


# --- D10 / D13: change cascade entry, one-hop (non-transitive) --------------

def test_d10_edit_stales_direct_dependents_only(core):
    core.add("base")  # T1
    core.add("mid")  # T2 depends_on T1
    core.add("top")  # T3 depends_on T2
    core.link_add(2, 1, because="test fixture", delta="test")
    core.link_add(3, 2, because="test fixture", delta="test")
    result = core.edit(1, description="base changed", delta="test")
    assert [n.id for n in result.newly_stale] == [2]  # direct dependent only
    assert core.get(2).stale is True
    assert core.get(3).stale is False  # NOT transitive (D10)


# --- D11: reconciliation worklist -------------------------------------------

def test_d11_worklist_and_reconcile(core):
    core.add("base")  # T1
    core.add("dep")  # T2 depends_on T1
    core.link_add(2, 1, because="test fixture", delta="test")
    core.edit(1, description="x", delta="test")
    assert [t.id for t in core.stale_worklist()] == [2]
    core.reconcile(2)
    assert core.stale_worklist() == []


# --- D12 / D14: close obligation payload + close-gate -----------------------

def test_d12_close_returns_neighbors(core):
    core.add("a")  # T1
    core.add("b")  # T2 depends_on T1
    core.link_add(2, 1, because="test fixture", delta="test")
    result = core.close(2)
    assert result.task.status == "closed"
    assert [n.id for n in result.dependencies] == [1]


def test_d14_close_gate_refuses_stale_then_allows_after_reconcile(core):
    core.add("base")  # T1
    core.add("dep")  # T2 depends_on T1
    core.link_add(2, 1, because="test fixture", delta="test")
    core.edit(1, description="x", delta="test")  # stales T2
    with pytest.raises(InvariantError):
        core.close(2)
    core.reconcile(2)
    assert core.close(2).task.status == "closed"


# --- D15: query/board -------------------------------------------------------

def test_d15_filters(core):
    core.add("a", labels=["x"])  # T1
    core.add("b", labels=["y"])  # T2
    core.close(2)
    assert [t.id for t in core.ls(status="open")] == [1]
    assert [t.id for t in core.ls(label="y")] == [2]


# --- D16: narrative render --------------------------------------------------

def test_d16_render(core):
    # "design" is reserved for the kind property since T84 -- use a non-reserved label.
    core.add("parse query", description="body text", labels=["spec"])
    md = core.render("spec")
    assert "T1 - parse query" in md and "body text" in md


# --- D17 / S5: full-text search ---------------------------------------------

def test_d17_search_ranks_matches(core):
    core.add("rotate JWT signing keys", description="auth token endpoint")
    core.add("unrelated colour palette")
    hits = core.search("JWT token")
    assert hits and hits[0].id == 1
    assert all(h.id != 2 for h in hits)


def test_d17_empty_query_refused(core):
    with pytest.raises(ValidationError):
        core.search("   ")


# --- D18: serialization + safe sync -----------------------------------------

def test_d18_mutation_bumps_version_and_dumps(core, store_path):
    store = Store(store_path)
    core.add("a")
    assert store.sql_path.exists()
    assert sync.parse_version_from_sql(store.sql_path.read_text()) >= 1


def test_d18_rebuild_on_fresh_clone(core, store_path):
    store = Store(store_path)
    core.add("recoverable task")
    core.close_conn()
    # simulate a fresh clone: only tackit.sql present, no binary db
    for suffix in ("", "-wal", "-shm"):
        p = store.db_path.with_name(store.db_path.name + suffix)
        if p.exists():
            p.unlink()
    c2 = Core.open(start=store_path)
    try:
        assert c2.get(1).name == "recoverable task"
    finally:
        c2.close_conn()


def test_d18_ambiguous_divergence_refused(core, store_path):
    store = Store(store_path)
    core.add("v1 task")
    sql_v1 = store.sql_path.read_text()
    core.add("v2 task")  # db + sql now newer
    core.close_conn()
    store.sql_path.write_text(sql_v1)  # disk reverted to older, db is newer
    with pytest.raises(SyncError):
        Core.open(start=store_path)


def test_d18_pull_newer_sql_rebuilds(core, store_path):
    store = Store(store_path)
    core.add("v1 task")
    sql_v1 = store.sql_path.read_text()
    core.add("v2 task")
    sql_v2 = store.sql_path.read_text()
    core.close_conn()
    # make the db match v1 (synced), then drop a strictly-newer v2 .sql on disk
    store.sql_path.write_text(sql_v1)
    sync.rebuild_db_from_sql(store)
    store.sql_path.write_text(sql_v2)
    c2 = Core.open(start=store_path)  # Vsql > Vdb -> pull
    try:
        assert c2.get(2).name == "v2 task"
    finally:
        c2.close_conn()


def test_d18_export_import_roundtrip(core, store_path):
    store = Store(store_path)
    core.add("kept across import")
    core.close_conn()
    sync.export(store)
    msg = sync.import_sql(store, force=True)
    assert "imported" in msg
    c2 = Core.open(start=store_path)
    try:
        assert c2.get(1).name == "kept across import"
    finally:
        c2.close_conn()


def test_d18_backup_rotation(core, store_path):
    store = Store(store_path)
    core.add("a")
    for _ in range(25):
        sync.backup_db(store)
    assert len(sync.list_backups(store)) <= sync.MAX_BACKUPS
