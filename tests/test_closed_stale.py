"""D137 - terminal tasks are never flagged stale.

The cascade flags only live neighbors (status open or spec); a closed,
wont_do or retired neighbor is not written at all: no stale flag and no
`updated_at` bump. This file pins that rule from the closed task's side, plus
the action menu on a closed task whose neighbor changed: what's allowed, what's
refused, and how the status_transitions log stays clean of open/close churn.

The converse (an open neighbor IS flagged) is pinned alongside each case, so a
cascade that stopped flagging everything would fail here too.
"""

import pytest

from tackit.errors import InvariantError, ValidationError


def _closed_neighbor_setup(core):
    """Two linked production tasks, T3 closed, then T2 edited.
    T1 is a D256 creation-gate anchor (design) shared by both production
    tasks. Returns T3's `updated_at` from before the edit."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("upstream", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("downstream", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="downstream consumes upstream's API")
    core.close(3)
    before = core.get(3).updated_at
    core.edit(2, description="changed", delta="upstream shape shifted")
    return before


def test_edit_leaves_closed_neighbor_unflagged_and_untouched(core):
    """D137: the closed neighbor is not written -- stale stays false and its
    updated_at does not move. Converse in the same run: the spec anchor, a
    live neighbor of the same edit, IS flagged."""
    before = _closed_neighbor_setup(core)
    t3 = core.get(3)
    assert t3.status == "closed"
    assert t3.stale is False
    assert t3.updated_at == before
    assert core.get(1).stale is True  # live neighbor still flagged


def test_closed_task_reconcile_refused(core):
    """Reconcile is refused on a closed task: it carries no stale flag, so
    there is nothing to clear (D56, D137)."""
    _closed_neighbor_setup(core)
    with pytest.raises(InvariantError, match=r"record-only|archaeology"):
        core.reconcile(3)
    t3 = core.get(3)
    assert t3.stale is False and t3.status == "closed"


def test_closed_task_edit_refused_under_d259(core):
    """D259: a closed task is a frozen record, so edit is refused after a
    neighbor changed too. Reopen to change. No audit row is written."""
    _closed_neighbor_setup(core)
    pre_desc = core.get(3).description
    with pytest.raises(ValidationError, match="frozen record"):
        core.edit(3, description="updated under d259", delta="fixing closed prose")
    t3 = core.get(3)
    assert t3.description == pre_desc  # unchanged
    assert t3.status == "closed"
    assert core.history(3).description_revisions == []  # no audit row written


def test_closed_task_link_add_allowed(core):
    """Adding a link to a closed task is structural, not content, so D259
    does not refuse it."""
    _closed_neighbor_setup(core)
    core.add("sibling", kind="production", deps={1: "realizes the anchor decision"})  # T4
    s = core.link_add(4, 3, because="T4 inherits T3's relationship to upstream")
    ids = sorted(n.id for n in s.links)
    assert 3 in ids


def test_closed_task_link_rm_allowed(core):
    """Symmetric: link_rm on an edge touching a closed task is allowed
    (structural; not a content edit)."""
    _closed_neighbor_setup(core)
    core.link_rm(3, 2)
    n = core.conn.execute("SELECT COUNT(*) FROM links WHERE task_a = 2 AND task_b = 3").fetchone()[0]
    assert n == 0


def test_second_upstream_edit_leaves_closed_task_untouched(core):
    """A second edit upstream of a closed task neither flags it, moves its
    updated_at, nor logs a transition."""
    _closed_neighbor_setup(core)
    before_row = core.get(3)
    seq_before = [(h.from_status, h.to_status) for h in core.history(3).status_transitions]
    core.edit(2, description="changed again", delta="upstream shifted further")
    t3 = core.get(3)
    assert t3.stale is False and t3.status == "closed"
    assert t3.updated_at == before_row.updated_at
    seq_after = [(h.from_status, h.to_status) for h in core.history(3).status_transitions]
    assert seq_before == seq_after


def test_noop_close_of_closed_task_after_neighbor_edit_succeeds(core):
    """D62 + D137: closing an already-closed task is a no-op that succeeds,
    including after a neighbor edit. Before D137 the edit flagged the closed
    task, close then refused it as stale, and reconcile refused it as closed:
    the task could never be closed again."""
    _closed_neighbor_setup(core)
    core.reconcile(1)  # the anchor is a live neighbor; clear it for the gate
    seq_before = [(h.from_status, h.to_status) for h in core.history(3).status_transitions]
    result = core.close(3)
    assert result.task.status == "closed"
    seq_after = [(h.from_status, h.to_status) for h in core.history(3).status_transitions]
    assert seq_before == seq_after  # no-op: no new transition


def test_close_gate_passes_with_closed_neighbor_after_edit(core):
    """D56 1-hop gate: T4's direct neighbor T3 is closed, so the edit that
    passed through T2 never flagged it, and T4 can close."""
    core.add("anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("a", kind="production", deps={1: "a realizes anchor"})  # T2
    core.add("b", kind="production", deps={1: "b realizes anchor"})  # T3
    core.add("c", kind="production", deps={1: "c realizes anchor"})  # T4
    core.link_add(3, 2, because="b consumes a")
    core.link_add(4, 3, because="c consumes b")
    core.close(3)
    core.edit(2, description="x", delta="a shifted")
    assert core.get(3).status == "closed" and core.get(3).stale is False
    # a's edit also flags the anchor (T1, live, a direct neighbor of T4 via
    # its D256 link); clear it -- it is not what this test is about.
    core.reconcile(1)
    result = core.close(4)
    assert result.task.status == "closed"


def test_close_gate_ignores_retired_stale_neighbor_from_old_dump(core):
    """D137 accepted residual: a spec-only dump written before schema 11 has
    no schema version, so loading one by hand can bring back stale=1 on a
    retired row, and no migration clears it. The gate keeps its status filter
    as a guard for that row: closing T2 succeeds next to it. The raw UPDATE
    stands in for that load."""
    core.add("d1", kind="design")  # T1 -- spec
    core.add("p1", kind="production", deps={1: "prod realizes design"})  # T2
    core.conn.execute(
        "UPDATE tasks SET status = 'retired', stale = 1 WHERE id = 1;"
    )
    result = core.close(2)
    assert result.task.status == "closed"
    assert 1 not in {t.id for t in core.stale_worklist()}
