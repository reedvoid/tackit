"""T123 (2026-06-01) - closed-stale behavior.

Under the relaxed D7 invariant, cascade-staling a closed neighbor leaves
status='closed' + stale=True (no force-reopen). This file pins the action
menu on a closed-stale task: what's allowed, what's refused, and how the
status_transitions log stays clean of spurious open/close churn.

The complementary "stale=open" path is still covered by the existing
test_d7_relaxed_* tests; here we focus on the closed-stale case end-to-end.
"""

import pytest

from tackit.errors import InvariantError


def _stale_closed_setup(core):
    """Two linked production tasks, T2 closed, T1 edited to stale T2."""
    core.add("upstream", kind="production")  # T1
    core.add("downstream", kind="production")  # T2
    core.link_add(2, 1, because="downstream consumes upstream's API")
    core.close(2)
    core.edit(1, description="changed", delta="upstream shape shifted")
    t2 = core.get(2)
    assert t2.stale is True and t2.status == "closed", (
        "precondition: cascade staled T2 without force-reopen"
    )
    return core


def test_closed_stale_reconcile_refused_under_v04(core):
    """v0.4 (D28): reconcile is REFUSED on closed/wont_do tasks. Their stale
    flag is record-only -- not on the worklist, not blocking close-gates.
    Clearing it would erase the archaeology marker without a corresponding
    meaning. The flag stays as historical signal."""
    _stale_closed_setup(core)
    with pytest.raises(InvariantError, match=r"record-only|archaeology"):
        core.reconcile(2)
    # The closed-stale state is preserved.
    t2 = core.get(2)
    assert t2.stale is True and t2.status == "closed"


def test_closed_stale_edit_allowed_under_v04(core):
    """v0.4 / D29 retires T118: edit is allowed on a closed-stale task. The
    description_revisions audit table preserves the verbatim prior name +
    description, so updating a closed task's prose no longer destroys
    history. Status stays 'closed' across the edit."""
    _stale_closed_setup(core)
    pre_name = core.get(2).name
    pre_desc = core.get(2).description
    core.edit(2, description="updated under v0.4", delta="fixing closed-stale prose")
    t2 = core.get(2)
    assert t2.description == "updated under v0.4"
    assert t2.status == "closed"  # edit doesn't change status
    # Audit row recorded the prior state verbatim.
    revs = core.history(2).description_revisions
    assert len(revs) == 1
    assert revs[0].prev_name == pre_name
    assert revs[0].prev_description == pre_desc
    assert revs[0].delta == "fixing closed-stale prose"


def test_closed_stale_link_add_allowed(core):
    """Link migration during supersede: agent can add a new link from the
    replacement to old's neighbors. Adding a link to a closed-stale task is
    structural, not content, so T118 does not refuse it."""
    _stale_closed_setup(core)
    core.add("sibling", kind="production")  # T3
    # Link T3 to T2 (the closed-stale task) — legitimate during migration.
    s = core.link_add(3, 2, because="T3 inherits T2's relationship to upstream")
    ids = sorted(n.id for n in s.dependencies)
    assert 2 in ids


def test_closed_stale_link_rm_allowed(core):
    """Symmetric: link_rm on an edge touching a closed-stale task is allowed
    (structural; not a T118 content edit)."""
    _stale_closed_setup(core)
    s = core.link_rm(2, 1)
    # The pair canonicalizes to (1, 2); the row should be gone.
    n = core.conn.execute("SELECT COUNT(*) FROM links WHERE task_a = 1 AND task_b = 2").fetchone()[0]
    assert n == 0


def test_cascade_staling_already_stale_closed_is_idempotent(core):
    """A second edit upstream of an already-closed-stale task does not churn
    its row's status or duplicate-log a transition."""
    _stale_closed_setup(core)
    seq_before = [(h.from_status, h.to_status) for h in core.history(2).status_transitions]
    core.edit(1, description="changed again", delta="upstream shifted further")
    t2 = core.get(2)
    assert t2.stale is True and t2.status == "closed"
    seq_after = [(h.from_status, h.to_status) for h in core.history(2).status_transitions]
    assert seq_before == seq_after  # no new transitions on the closed-stale row


def test_close_gate_does_not_trip_on_closed_stale_under_v04(core):
    """v0.4 (D28): the close-gate's 'transitively linked to stale' walk
    filters to obligation-bearing stale tasks (open OR design/schema kind).
    A closed-stale production neighbor is record-only and does NOT pressure
    the close-gate -- T3 can close even though T2 is closed+stale."""
    core.add("a", kind="production")  # T1
    core.add("b", kind="production")  # T2
    core.add("c", kind="production")  # T3
    core.link_add(2, 1, because="b consumes a")
    core.link_add(3, 2, because="c consumes b")
    core.close(2)  # T2 closed
    core.edit(1, description="x", delta="a shifted")  # T2 -> closed-stale via cascade
    assert core.get(2).status == "closed" and core.get(2).stale is True
    # T3 can close: T2 is closed-stale (record-only), not on the worklist.
    result = core.close(3)
    assert result.task.status == "closed"


def test_close_gate_does_not_trip_on_retired_stale_neighbor(core):
    """v0.5 D36: retired neighbors are record-only (terminal status). The
    close-gate's obligation filter excludes them under the new predicate
    status IN ('open','spec'). Closing T2 succeeds even when T1 is retired+stale."""
    core.add("d1", kind="design")  # T1 -- spec
    core.add("p1", kind="production")  # T2
    core.link_add(2, 1, because="prod realizes design")
    # Seed T1 as retired+stale (retire() verb arrives Phase 2b).
    core.conn.execute(
        "UPDATE tasks SET status = 'retired', stale = 1 WHERE id = 1;"
    )
    # T2 must be reconcilable; the cascade from above didn't stale it -- seed
    # T2 as clean and verify the gate ignores T1's record-only stale flag.
    result = core.close(2)
    assert result.task.status == "closed"
