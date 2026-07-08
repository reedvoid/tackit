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
    core.add("spec anchor", kind="design")  # T1 (D1)
    t = core.add(
        "parse FTS5 query",
        kind="production",
        description="tokenize MATCH terms",
        deps={1: "realizes the anchor decision"},
    )
    assert t.id == 2 and t.status == "open" and t.stale is False
    assert core.get(2).name == "parse FTS5 query"


def test_d2_empty_name_refused(core):
    with pytest.raises(ValidationError):
        core.add("   ", kind="production")


def test_d3_get_missing_fails_loud(core):
    with pytest.raises(NotFoundError):
        core.get(999)


# --- D4 / S2: labels --------------------------------------------------------

def test_d4_labels_add_list_remove(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add(
        "rank results",
        kind="production",
        labels=["search", "core"],
        deps={1: "realizes the anchor decision"},
    )  # T2
    assert core.labels_of(2) == ["core", "search"]
    core.label_rm(2, "core")
    assert core.labels_of(2) == ["search"]


# --- D5 / D6 / D14: edges, traversal, invariants ----------------------------

def test_d5_d6_symmetric_link_traversal(core):
    # Under v0.3.0 symmetric semantics (T86), both `dependencies_of` and
    # `dependents_of` return the same linked-neighbor set; the names are kept
    # for API stability and rename in T93/T96.
    # D256 creation-gate: both production tasks link the design anchor (T1) at
    # creation, so the anchor is itself a linked neighbor of each -- included
    # in the expected neighbor sets below.
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="test fixture")  # link T2 <-> T3
    assert [n.id for n in core.dependencies_of(3)] == [1, 2]
    assert [n.id for n in core.dependents_of(2)] == [1, 3]
    # Symmetric: querying from the other endpoint returns the same neighbor set.
    assert [n.id for n in core.dependencies_of(2)] == [1, 3]
    assert [n.id for n in core.dependents_of(3)] == [1, 2]


def test_d14_self_link_refused(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    with pytest.raises(InvariantError):
        core.link_add(2, 2, because="test fixture")


def test_d5_duplicate_link_is_noop(core):
    # Under symmetric semantics there is no directed cycle; what used to be a
    # "cycle" (T1->T2 then T2->T1) is the same canonical link {T1, T2}, so the
    # second dep_add is a no-op (idempotent), not an error.
    # D256 creation-gate: both production tasks link the design anchor (T1) at
    # creation, contributing 2 rows up front; the test still pins that the
    # THIRD (a<->b) edge is not duplicated by the reversed-args re-add.
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(2, 3, because="test fixture")  # link T2 <-> T3
    core.link_add(3, 2, because="test fixture")  # same link, reversed args -> idempotent
    # Exactly 3 rows in the links table: anchor<->a, anchor<->b, a<->b (not 4).
    n = core.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    assert n == 3


def test_d14_edge_to_missing_task_refused(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    with pytest.raises(NotFoundError):
        core.link_add(2, 999, because="test fixture")


# --- D7 / D8: status, stale invariant, history ------------------------------

def test_d7_relaxed_closed_can_be_stale(core):
    """T123: cascade now stales closed neighbors without force-reopening them.
    The closed task carries stale=True + status='closed' to signal 'review for
    supersede / link migration' while remaining immutable per T118."""
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # T3 linked to T2
    core.link_add(3, 2, because="test fixture")
    core.close(3)  # T3 closed
    core.edit(2, description="changed", delta="test")  # stales T3 but no force-reopen
    t3 = core.get(3)
    assert t3.stale is True and t3.status == "closed"  # T123: stays closed


def test_d8_history_logged_through_reopen(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.close(2)
    core.reopen(2)
    seq = [(h.from_status, h.to_status) for h in core.history(2).status_transitions]
    assert seq == [(None, "open"), ("open", "closed"), ("closed", "open")]


# --- D9: slice fetch --------------------------------------------------------

def test_d9_slice_fetch(core):
    # Slice still exposes `dependencies` + `dependents` fields for API stability
    # (T93/T96 rename), but under symmetric semantics both return the same
    # linked-neighbor set.
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", labels=["x"], deps={1: "realizes the anchor decision"})  # T2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(2, 3, because="test fixture")  # link T2 <-> T3
    s = core.show(2)
    assert s.task.id == 2
    assert s.labels == ["x"]
    # T237: one symmetric links list -- includes the D256 anchor neighbor (T1).
    assert [n.id for n in s.links] == [1, 3]


# --- D10 / D13: change cascade entry, one-hop (non-transitive) --------------

def test_d10_edit_stales_direct_dependents_only(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("base", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("mid", kind="production", deps={1: "realizes the anchor decision"})  # T3 depends_on T2
    core.add("top", kind="production", deps={1: "realizes the anchor decision"})  # T4 depends_on T3
    core.link_add(3, 2, because="test fixture")
    core.link_add(4, 3, because="test fixture")
    result = core.edit(2, description="base changed", delta="test")
    # direct dependents of base (T2): the D256 anchor (T1) + mid (T3).
    assert [n.id for n in result.newly_stale] == [1, 3]
    assert core.get(3).stale is True
    assert core.get(4).stale is False  # NOT transitive (D10)


# --- D11: reconciliation worklist -------------------------------------------

def test_d11_worklist_and_reconcile(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("base", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("dep", kind="production", deps={1: "realizes the anchor decision"})  # T3 depends_on T2
    core.link_add(3, 2, because="test fixture")
    core.edit(2, description="x", delta="test")
    # edit stales both direct dependents: the D256 anchor (T1) + dep (T3).
    assert [t.id for t in core.stale_worklist()] == [1, 3]
    core.reconcile(3)
    core.reconcile(1)
    assert core.stale_worklist() == []


# --- D12 / D14: close obligation payload + close-gate -----------------------

def test_d12_close_returns_neighbors(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # T3 depends_on T2
    core.link_add(3, 2, because="test fixture")
    result = core.close(3)
    assert result.task.status == "closed"
    # neighbors of b (T3): the D256 anchor (T1) + a (T2).
    assert [n.id for n in result.links] == [1, 2]


def test_d14_close_gate_refuses_stale_then_allows_after_reconcile(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("base", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("dep", kind="production", deps={1: "realizes the anchor decision"})  # T3 depends_on T2
    core.link_add(3, 2, because="test fixture")
    core.edit(2, description="x", delta="test")  # stales T3 (and the anchor T1)
    with pytest.raises(InvariantError):
        core.close(3)
    core.reconcile(3)
    # The close-gate checks the transitive linked neighborhood (D14): the
    # anchor (T1) is still stale, so T3 still can't close until it too is
    # reconciled.
    core.reconcile(1)
    assert core.close(3).task.status == "closed"


# --- D15: query/board -------------------------------------------------------

def test_d15_filters(core):
    core.add("spec anchor", kind="design")  # T1 (D1) -- status='spec', excluded below
    core.add("a", kind="production", labels=["x"], deps={1: "realizes the anchor decision"})  # T2
    core.add("b", kind="production", labels=["y"], deps={1: "realizes the anchor decision"})  # T3
    core.close(3)
    assert [t.id for t in core.ls(status="open")] == [2]
    assert [t.id for t in core.ls(label="y")] == [3]


# --- D16: narrative render --------------------------------------------------

def test_d16_render(core):
    # "design" is reserved for the kind property since T84 -- use a non-reserved label.
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add(
        "parse query",
        kind="production",
        description="body text",
        labels=["spec"],
        deps={1: "realizes the anchor decision"},
    )  # T2
    md = core.render("spec")
    assert "T2 - parse query" in md and "body text" in md


# --- D17 / S5: full-text search ---------------------------------------------

def test_d17_search_ranks_matches(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add(
        "rotate JWT signing keys",
        kind="production",
        description="auth token endpoint",
        deps={1: "realizes the anchor decision"},
    )  # T2
    core.add(
        "unrelated colour palette",
        kind="production",
        deps={1: "realizes the anchor decision"},
    )  # T3
    hits = core.search("JWT token")
    assert hits and hits[0].id == 2
    assert all(h.id != 3 for h in hits)


def test_d17_empty_query_refused(core):
    with pytest.raises(ValidationError):
        core.search("   ")


# --- D18: serialization + safe sync -----------------------------------------

def test_d18_mutation_bumps_version_and_dumps(core, store_path):
    store = Store(store_path)
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    assert store.sql_path.exists()
    assert sync.parse_version_from_sql(store.sql_path.read_text()) >= 1


def test_d18_rebuild_on_fresh_clone(core, store_path):
    store = Store(store_path)
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("recoverable task", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.close_conn()
    # simulate a fresh clone: only tackit.sql present, no binary db
    for suffix in ("", "-wal", "-shm"):
        p = store.db_path.with_name(store.db_path.name + suffix)
        if p.exists():
            p.unlink()
    c2 = Core.open(start=store_path)
    try:
        assert c2.get(2).name == "recoverable task"
    finally:
        c2.close_conn()


def test_d18_ambiguous_divergence_refused(core, store_path):
    store = Store(store_path)
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("v1 task", kind="production", deps={1: "realizes the anchor decision"})  # T2
    sql_v1 = store.sql_path.read_text()
    core.add("v2 task", kind="production", deps={1: "realizes the anchor decision"})  # T3, db + sql now newer
    core.close_conn()
    store.sql_path.write_text(sql_v1)  # disk reverted to older, db is newer
    with pytest.raises(SyncError):
        Core.open(start=store_path)


def test_d18_pull_newer_sql_rebuilds(core, store_path):
    store = Store(store_path)
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("v1 task", kind="production", deps={1: "realizes the anchor decision"})  # T2
    sql_v1 = store.sql_path.read_text()
    core.add("v2 task", kind="production", deps={1: "realizes the anchor decision"})  # T3
    sql_v2 = store.sql_path.read_text()
    core.close_conn()
    # make the db match v1 (synced), then drop a strictly-newer v2 .sql on disk
    store.sql_path.write_text(sql_v1)
    sync.rebuild_db_from_sql(store)
    store.sql_path.write_text(sql_v2)
    c2 = Core.open(start=store_path)  # Vsql > Vdb -> pull
    try:
        assert c2.get(3).name == "v2 task"
    finally:
        c2.close_conn()


def test_d18_export_import_roundtrip(core, store_path):
    store = Store(store_path)
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("kept across import", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.close_conn()
    sync.export(store)
    msg = sync.import_sql(store, force=True)
    assert "imported" in msg
    c2 = Core.open(start=store_path)
    try:
        assert c2.get(2).name == "kept across import"
    finally:
        c2.close_conn()


def test_d18_backup_rotation(core, store_path):
    store = Store(store_path)
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    for _ in range(25):
        sync.backup_db(store)
    assert len(sync.list_backups(store)) <= sync.MAX_BACKUPS
