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
    core.link_add(2, 1, because="downstream consumes upstream's API", delta="setup")
    core.close(2)
    core.edit(1, description="changed", delta="upstream shape shifted")
    t2 = core.get(2)
    assert t2.stale is True and t2.status == "closed", (
        "precondition: cascade staled T2 without force-reopen"
    )
    return core


def test_closed_stale_reconcile_clears_stale_keeps_closed(core):
    """Reconcile on a closed-stale task clears stale but preserves status=closed."""
    _stale_closed_setup(core)
    t2 = core.reconcile(2)
    assert t2.stale is False
    assert t2.status == "closed"  # T123: reconcile does NOT touch status
    # Reconcile should not have logged a spurious transition.
    seq = [(h.from_status, h.to_status) for h in core.history(2)]
    assert seq == [(None, "open"), ("open", "closed")]


def test_closed_stale_edit_still_refused(core):
    """T118 (no-edit-closed) is unchanged by T123. The relaxed D7 just lets
    closed tasks be stale; it does not unlock editing them. Drift must still
    be addressed via supersede / link migration."""
    _stale_closed_setup(core)
    with pytest.raises(InvariantError, match="closed"):
        core.edit(2, description="trying to fix it", delta="should be refused")
    # State unchanged.
    t2 = core.get(2)
    assert t2.stale is True and t2.status == "closed"


def test_closed_stale_supersede_allowed(core):
    """Supersede is the right action when a closed-stale task's premise has
    been replaced. T119 cascade obligation is a different question (T124);
    here we just verify supersede works on the closed-stale state."""
    _stale_closed_setup(core)
    core.add("replacement", kind="production")  # T3
    result = core.supersede(2, 3, delta="downstream replaced by new design")
    assert result.old.task.superseded_by == 3
    # T2 still closed (supersede does not auto-close/open).
    assert result.old.task.status == "closed"


def test_closed_stale_link_add_allowed(core):
    """Link migration during supersede: agent can add a new link from the
    replacement to old's neighbors. Adding a link to a closed-stale task is
    structural, not content, so T118 does not refuse it."""
    _stale_closed_setup(core)
    core.add("sibling", kind="production")  # T3
    # Link T3 to T2 (the closed-stale task) — legitimate during migration.
    s = core.link_add(3, 2, because="T3 inherits T2's relationship to upstream", delta="link migration")
    ids = sorted(n.id for n in s.dependencies)
    assert 2 in ids


def test_closed_stale_link_rm_allowed(core):
    """Symmetric: link_rm on an edge touching a closed-stale task is allowed
    (structural; not a T118 content edit)."""
    _stale_closed_setup(core)
    s = core.link_rm(2, 1, delta="pruning old coupling during migration")
    # The pair canonicalizes to (1, 2); the row should be gone.
    n = core.conn.execute("SELECT COUNT(*) FROM links WHERE task_a = 1 AND task_b = 2").fetchone()[0]
    assert n == 0


def test_cascade_staling_already_stale_closed_is_idempotent(core):
    """A second edit upstream of an already-closed-stale task does not churn
    its row's status or duplicate-log a transition."""
    _stale_closed_setup(core)
    seq_before = [(h.from_status, h.to_status) for h in core.history(2)]
    core.edit(1, description="changed again", delta="upstream shifted further")
    t2 = core.get(2)
    assert t2.stale is True and t2.status == "closed"
    seq_after = [(h.from_status, h.to_status) for h in core.history(2)]
    assert seq_before == seq_after  # no new transitions on the closed-stale row


def test_close_gate_refuses_when_linked_closed_stale(core):
    """A task in a linked neighborhood with a closed-stale member is still
    blocked from closing. The closed-stale neighbor signals 'might still be
    superseded or relinked' — its review obligation gates close on its
    neighbors too. T123 changes force-reopen, not close-gate semantics."""
    core.add("a", kind="production")  # T1
    core.add("b", kind="production")  # T2
    core.add("c", kind="production")  # T3
    core.link_add(2, 1, because="b consumes a", delta="setup")
    core.link_add(3, 2, because="c consumes b", delta="setup")
    core.close(2)  # T2 closed
    core.edit(1, description="x", delta="a shifted")  # T2 -> closed-stale via cascade
    assert core.get(2).status == "closed" and core.get(2).stale is True
    with pytest.raises(InvariantError, match="stale"):
        core.close(3)  # refused: linked-stale-via-T2 in neighborhood
