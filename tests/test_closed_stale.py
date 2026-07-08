"""T123 (2026-06-01) - closed-stale behavior.

Under the relaxed D7 invariant, cascade-staling a closed neighbor leaves
status='closed' + stale=True (no force-reopen). This file pins the action
menu on a closed-stale task: what's allowed, what's refused, and how the
status_transitions log stays clean of spurious open/close churn.

The complementary "stale=open" path is still covered by the existing
test_d7_relaxed_* tests; here we focus on the closed-stale case end-to-end.
"""

import pytest

from tackit.errors import InvariantError, ValidationError


def _stale_closed_setup(core):
    """Two linked production tasks, T3 closed, T2 edited to stale T3.
    T1 is a D256 creation-gate anchor (design) shared by both production
    tasks -- see D256_FIX_GUIDE. IDs shift +1 vs. the pre-D256 fixture."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("upstream", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("downstream", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="downstream consumes upstream's API")
    core.close(3)
    core.edit(2, description="changed", delta="upstream shape shifted")
    t3 = core.get(3)
    assert t3.stale is True and t3.status == "closed", (
        "precondition: cascade staled T3 without force-reopen"
    )
    return core


def test_closed_stale_reconcile_refused_under_v04(core):
    """v0.4 (D28): reconcile is REFUSED on closed/wont_do tasks. Their stale
    flag is record-only -- not on the worklist, not blocking close-gates.
    Clearing it would erase the archaeology marker without a corresponding
    meaning. The flag stays as historical signal."""
    _stale_closed_setup(core)
    with pytest.raises(InvariantError, match=r"record-only|archaeology"):
        core.reconcile(3)
    # The closed-stale state is preserved.
    t3 = core.get(3)
    assert t3.stale is True and t3.status == "closed"


def test_closed_stale_edit_refused_under_d259(core):
    """D259 reverses the v0.4 edit-on-closed behavior: a closed task is a frozen
    record, so edit is refused even when it is closed-stale (its stale flag
    stays record-only archaeology). Reopen to change. No audit row is written."""
    _stale_closed_setup(core)
    pre_desc = core.get(3).description
    with pytest.raises(ValidationError, match="frozen record"):
        core.edit(3, description="updated under d259", delta="fixing closed-stale prose")
    t3 = core.get(3)
    assert t3.description == pre_desc  # unchanged
    assert t3.status == "closed"
    assert core.history(3).description_revisions == []  # no audit row written


def test_closed_stale_link_add_allowed(core):
    """Link migration during supersede: agent can add a new link from the
    replacement to old's neighbors. Adding a link to a closed-stale task is
    structural, not content, so T118 does not refuse it."""
    _stale_closed_setup(core)
    core.add("sibling", kind="production", deps={1: "realizes the anchor decision"})  # T4
    # Link T4 to T3 (the closed-stale task) — legitimate during migration.
    s = core.link_add(4, 3, because="T4 inherits T3's relationship to upstream")
    ids = sorted(n.id for n in s.links)
    assert 3 in ids


def test_closed_stale_link_rm_allowed(core):
    """Symmetric: link_rm on an edge touching a closed-stale task is allowed
    (structural; not a T118 content edit)."""
    _stale_closed_setup(core)
    s = core.link_rm(3, 2)
    # The pair canonicalizes to (2, 3); the row should be gone.
    n = core.conn.execute("SELECT COUNT(*) FROM links WHERE task_a = 2 AND task_b = 3").fetchone()[0]
    assert n == 0


def test_cascade_staling_already_stale_closed_is_idempotent(core):
    """A second edit upstream of an already-closed-stale task does not churn
    its row's status or duplicate-log a transition."""
    _stale_closed_setup(core)
    seq_before = [(h.from_status, h.to_status) for h in core.history(3).status_transitions]
    core.edit(2, description="changed again", delta="upstream shifted further")
    t3 = core.get(3)
    assert t3.stale is True and t3.status == "closed"
    seq_after = [(h.from_status, h.to_status) for h in core.history(3).status_transitions]
    assert seq_before == seq_after  # no new transitions on the closed-stale row


def test_close_gate_does_not_trip_on_closed_stale_under_v04(core):
    """v0.4 (D28): the close-gate's 'transitively linked to stale' walk
    filters to obligation-bearing stale tasks (open OR design/schema kind).
    A closed-stale production neighbor is record-only and does NOT pressure
    the close-gate -- T4 can close even though T3 is closed+stale."""
    core.add("anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("a", kind="production", deps={1: "a realizes anchor"})  # T2
    core.add("b", kind="production", deps={1: "b realizes anchor"})  # T3
    core.add("c", kind="production", deps={1: "c realizes anchor"})  # T4
    core.link_add(3, 2, because="b consumes a")
    core.link_add(4, 3, because="c consumes b")
    core.close(3)  # T3 (b) closed
    core.edit(2, description="x", delta="a shifted")  # T3 -> closed-stale via cascade
    assert core.get(3).status == "closed" and core.get(3).stale is True
    # a's edit ALSO cascade-stales the anchor (T1, direct neighbor via the
    # D256 dep link) -- T1 is spec+stale, which IS obligation-bearing, so it
    # would otherwise pressure the close-gate on T4 through the shared graph.
    # Reconcile it: it's incidental to this test (which is about the
    # closed-stale *production* neighbor being record-only), not itself
    # under test here.
    core.reconcile(1)
    # T4 can close: T3 is closed-stale (record-only), not on the worklist.
    result = core.close(4)
    assert result.task.status == "closed"


def test_close_gate_does_not_trip_on_retired_stale_neighbor(core):
    """v0.5 D36: retired neighbors are record-only (terminal status). The
    close-gate's obligation filter excludes them under the new predicate
    status IN ('open','spec'). Closing T2 succeeds even when T1 is retired+stale."""
    core.add("d1", kind="design")  # T1 -- spec
    # T2's D256 creation-gate link IS the "prod realizes design" edge under
    # test here, so it's declared at creation via deps rather than a
    # separate link_add call.
    core.add("p1", kind="production", deps={1: "prod realizes design"})  # T2
    # Seed T1 as retired+stale (retire() verb arrives Phase 2b).
    core.conn.execute(
        "UPDATE tasks SET status = 'retired', stale = 1 WHERE id = 1;"
    )
    # T2 must be reconcilable; the cascade from above didn't stale it -- seed
    # T2 as clean and verify the gate ignores T1's record-only stale flag.
    result = core.close(2)
    assert result.task.status == "closed"
