"""T132 / 2026-06-01 - wont_do terminal status + verb.

wont_do is a third terminal status distinct from closed: closed = "we did this";
wont_do = "we decided not to do this". The verb takes a durable reason (persists
forever in wont_do_reason column). No delta (D284): wont_do doesn't fire the
cascade, so a delta would have no reader. Locked-forever per
T118 pattern: edit / reopen / close / wont_do all refused on wont_do tasks;
supersede is the only path to change the decision (with a new task carrying the
new direction).
"""

import pytest

from tackit.errors import InvariantError, ValidationError, NotFoundError


# --- happy path -------------------------------------------------------------

def test_wont_do_sets_status_and_reason(core):
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("scope_drop_candidate", kind="production", deps={1: "realizes the anchor decision"})
    result = core.wont_do(2, reason="redundant with T_other")
    assert result.task.status == "wont_do"
    assert result.task.wont_do_reason == "redundant with T_other"
    # Persists across re-read.
    assert core.get(2).status == "wont_do"
    assert core.get(2).wont_do_reason == "redundant with T_other"


def test_wont_do_returns_one_hop_neighbors(core):
    """Like close, wont_do returns the linked neighbors for migrate-or-stay review."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("dropped", kind="production", deps={1: "realizes the anchor decision"})
    core.add("nbr_a", kind="production", deps={1: "realizes the anchor decision"})
    core.add("nbr_b", kind="production", deps={1: "realizes the anchor decision"})
    core.link_add(3, 2, because="setup")
    core.link_add(4, 2, because="setup")
    result = core.wont_do(2, reason="not doing this")
    nbr_ids = sorted(n.id for n in result.links)
    # dropped(2) is linked to the anchor (D256, T1) plus nbr_a(3)/nbr_b(4).
    assert nbr_ids == [1, 3, 4]


def test_wont_do_logs_transition(core):
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("task", kind="production", deps={1: "realizes the anchor decision"})
    core.wont_do(2, reason="dropped")
    seq = [(h.from_status, h.to_status) for h in core.history(2).status_transitions]
    assert seq == [(None, "open"), ("open", "wont_do")]


# --- input validation ------------------------------------------------------

def test_wont_do_empty_reason_refused(core):
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("task", kind="production", deps={1: "realizes the anchor decision"})
    with pytest.raises(ValidationError, match="reason"):
        core.wont_do(2, reason="")


def test_wont_do_whitespace_reason_refused(core):
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("task", kind="production", deps={1: "realizes the anchor decision"})
    with pytest.raises(ValidationError, match="reason"):
        core.wont_do(2, reason="   ")


def test_wont_do_succeeds_without_delta(core):
    """D284: wont_do carries reason only -- no delta required (it does not
    fire the cascade, so a delta would have no reader; symmetric with close)."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("task", kind="production", deps={1: "realizes the anchor decision"})
    result = core.wont_do(2, reason="scope dropped")
    assert result.task.status == "wont_do"
    assert result.task.wont_do_reason == "scope dropped"


def test_wont_do_missing_task_refused(core):
    with pytest.raises(NotFoundError):
        core.wont_do(999, reason="nope")


# --- locked-forever (T118 pattern extends to wont_do) ----------------------

def test_wont_do_edit_refused_under_d259(core):
    """D259 reverses the v0.4 edit-on-terminal behavior: a wont_do task is a
    frozen record. Because wont_do is terminal-forever (reopen refused, D36),
    its body can NEVER change -- edit is refused with no recovery path but a
    new task. No audit row is written."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("task", kind="production", description="initial", deps={1: "realizes the anchor decision"})
    core.wont_do(2, reason="dropped because X")
    with pytest.raises(ValidationError, match="frozen record"):
        core.edit(2, description="updated rationale prose", delta="refining the wont_do context")
    t = core.get(2)
    assert t.status == "wont_do"
    assert t.description == "initial"  # unchanged
    assert t.wont_do_reason == "dropped because X"
    assert core.history(2).description_revisions == []  # no audit row


def test_wont_do_reopen_refused(core):
    """wont_do is terminal forever; the change-of-mind path is supersede."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("task", kind="production", deps={1: "realizes the anchor decision"})
    core.wont_do(2, reason="dropped")
    with pytest.raises(InvariantError, match="wont_do"):
        core.reopen(2)


def test_wont_do_close_refused(core):
    """closed and wont_do are distinct terminal states; can't move between."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("task", kind="production", deps={1: "realizes the anchor decision"})
    core.wont_do(2, reason="dropped")
    with pytest.raises(InvariantError, match="wont_do"):
        core.close(2)


def test_wont_do_already_wont_do_refused(core):
    """No double-decide; the decision is locked."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("task", kind="production", deps={1: "realizes the anchor decision"})
    core.wont_do(2, reason="dropped")
    with pytest.raises(InvariantError, match="already wont_do"):
        core.wont_do(2, reason="dropped again")


def test_wont_do_on_closed_refused(core):
    """closed=done and wont_do=decided-not-to-do are distinct; can't reclassify
    a done task as not-done. If close was a mistake, supersede with a new task."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("task", kind="production", deps={1: "realizes the anchor decision"})
    core.close(2)
    with pytest.raises(InvariantError, match="closed"):
        core.wont_do(2, reason="changing my mind")


# --- change-of-mind path is a fresh task -----------------------------------

def test_wont_do_then_fresh_task_for_changed_mind(core):
    """v0.4 retires supersede. The change-of-mind path on a wont_do task is
    simply to create a new task with the new direction; the old wont_do row
    stays as historical record. No FK marker linking the two; if a coupling
    matters, link_add records it."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("dropped_originally", kind="production", deps={1: "realizes the anchor decision"})
    core.wont_do(2, reason="not doing")
    # Change of mind: spawn a new task with the new direction.
    core.add("now_doing_it", kind="production", deps={1: "realizes the anchor decision"})
    # The wont_do row is untouched.
    assert core.get(2).status == "wont_do"
    assert core.get(2).wont_do_reason == "not doing"
    # The new task exists alongside.
    assert core.get(3).status == "open"


# --- close-gate symmetric with wont_do -------------------------------------

def test_wont_do_refused_when_task_is_stale(core):
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="setup")
    core.edit(2, description="x", delta="staling T3")  # stales T3
    with pytest.raises(InvariantError, match="stale"):
        core.wont_do(3, reason="dropped")


def test_wont_do_refused_when_linked_stale(core):
    """D14 close-gate symmetric: refused if any task in the linked
    neighborhood is stale."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.add("c", kind="production", deps={1: "realizes the anchor decision"})  # T4
    core.link_add(3, 2, because="setup")
    core.link_add(4, 3, because="setup")
    core.edit(2, description="x", delta="stales T3")
    with pytest.raises(InvariantError, match="stale"):
        core.wont_do(4, reason="dropped")


# --- structural ops still allowed on wont_do tasks -------------------------

def test_wont_do_link_add_allowed(core):
    """Like closed tasks, wont_do tasks accept link operations (structural,
    not content) -- agent can migrate edges during supersede."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("dropped", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("other", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.wont_do(2, reason="dropped")
    s = core.link_add(3, 2, because="historical edge to dropped task")
    assert 2 in [n.id for n in s.links]


def test_wont_do_link_rm_allowed(core):
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("dropped", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("other", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="will be pruned")
    core.wont_do(2, reason="dropped")
    core.link_rm(3, 2)
    n = core.conn.execute("SELECT COUNT(*) FROM links WHERE task_a = 2 AND task_b = 3").fetchone()[0]
    assert n == 0


def test_wont_do_label_ops_allowed(core):
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("dropped", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.wont_do(2, reason="dropped")
    core.label_add(2, "historical")
    assert "historical" in core.labels_of(2)
    core.label_rm(2, "historical")
    assert "historical" not in core.labels_of(2)


# --- cascade-stale interaction (T123 compose) -------------------------------

def test_wont_do_task_cascade_staled_stays_wont_do(core):
    """A wont_do task that gets cascade-staled by an upstream edit stays
    wont_do + stale=True (per T123's relaxed D7, no force-reopen on any
    terminal status)."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("upstream", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("dropped_downstream", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="setup")
    core.wont_do(3, reason="dropped")
    core.edit(2, description="upstream shifted", delta="upstream shift stales T3")
    t3 = core.get(3)
    assert t3.stale is True
    assert t3.status == "wont_do"  # T123 + T132: stays terminal


def test_reconcile_refused_on_wont_do_stale_under_v04(core):
    """v0.4 (D28): reconcile is REFUSED on wont_do tasks (same as closed).
    Their stale flag is record-only and stays as historical signal."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("upstream", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("dropped", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="setup")
    core.wont_do(3, reason="dropped")
    core.edit(2, description="x", delta="staling")
    with pytest.raises(InvariantError, match=r"record-only|archaeology"):
        core.reconcile(3)
    t3 = core.get(3)
    assert t3.stale is True  # preserved as record
    assert t3.status == "wont_do"


# --- migration smoke ------------------------------------------------------

def test_existing_tasks_have_null_wont_do_reason(core):
    """After migration 005, existing tasks have wont_do_reason=NULL (not
    every task gets a wont_do decision)."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("regular_task", kind="production", deps={1: "realizes the anchor decision"})
    assert core.get(2).wont_do_reason is None
