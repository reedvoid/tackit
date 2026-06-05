"""T132 / 2026-06-01 - wont_do terminal status + verb.

wont_do is a third terminal status distinct from closed: closed = "we did this";
wont_do = "we decided not to do this". The verb takes a durable reason (persists
forever in wont_do_reason column) + ephemeral delta (T117). Locked-forever per
T118 pattern: edit / reopen / close / wont_do all refused on wont_do tasks;
supersede is the only path to change the decision (with a new task carrying the
new direction).
"""

import pytest

from tackit.errors import InvariantError, ValidationError, NotFoundError


# --- happy path -------------------------------------------------------------

def test_wont_do_sets_status_and_reason(core):
    core.add("scope_drop_candidate", kind="production")
    result = core.wont_do(1, reason="redundant with T_other", delta="dropped scope")
    assert result.task.status == "wont_do"
    assert result.task.wont_do_reason == "redundant with T_other"
    # Persists across re-read.
    assert core.get(1).status == "wont_do"
    assert core.get(1).wont_do_reason == "redundant with T_other"


def test_wont_do_returns_one_hop_neighbors(core):
    """Like close, wont_do returns the linked neighbors for migrate-or-stay review."""
    core.add("dropped", kind="production")
    core.add("nbr_a", kind="production")
    core.add("nbr_b", kind="production")
    core.link_add(2, 1, because="setup")
    core.link_add(3, 1, because="setup")
    result = core.wont_do(1, reason="not doing this", delta="dropped")
    nbr_ids = sorted(n.id for n in result.dependencies)
    assert nbr_ids == [2, 3]


def test_wont_do_logs_transition(core):
    core.add("task", kind="production")
    core.wont_do(1, reason="dropped", delta="not doing it")
    seq = [(h.from_status, h.to_status) for h in core.history(1).status_transitions]
    assert seq == [(None, "open"), ("open", "wont_do")]


# --- input validation ------------------------------------------------------

def test_wont_do_empty_reason_refused(core):
    core.add("task", kind="production")
    with pytest.raises(ValidationError, match="reason"):
        core.wont_do(1, reason="", delta="missing reason")


def test_wont_do_whitespace_reason_refused(core):
    core.add("task", kind="production")
    with pytest.raises(ValidationError, match="reason"):
        core.wont_do(1, reason="   ", delta="missing reason")


def test_wont_do_empty_delta_refused(core):
    core.add("task", kind="production")
    with pytest.raises(ValidationError, match="delta"):
        core.wont_do(1, reason="ok", delta="")


def test_wont_do_missing_task_refused(core):
    with pytest.raises(NotFoundError):
        core.wont_do(999, reason="nope", delta="nope")


# --- locked-forever (T118 pattern extends to wont_do) ----------------------

def test_wont_do_edit_allowed_under_v04(core):
    """v0.4 / D29 retires the T118 lock on terminal tasks. edit is allowed
    on wont_do (and closed) tasks; the description_revisions audit table
    preserves the verbatim prior state. The wont_do_reason field is NOT
    edited via this op (it's set once at wont_do() time and is immutable)."""
    core.add("task", kind="production", description="initial")
    core.wont_do(1, reason="dropped because X", delta="initial drop")
    core.edit(1, description="updated rationale prose", delta="refining the wont_do context")
    t = core.get(1)
    assert t.status == "wont_do"
    assert t.description == "updated rationale prose"
    # wont_do_reason is immutable -- still the original string.
    assert t.wont_do_reason == "dropped because X"
    revs = core.history(1).description_revisions
    assert len(revs) == 1
    assert revs[0].prev_description == "initial"
    assert revs[0].delta == "refining the wont_do context"


def test_wont_do_reopen_refused(core):
    """wont_do is terminal forever; the change-of-mind path is supersede."""
    core.add("task", kind="production")
    core.wont_do(1, reason="dropped", delta="dropped")
    with pytest.raises(InvariantError, match="wont_do"):
        core.reopen(1)


def test_wont_do_close_refused(core):
    """closed and wont_do are distinct terminal states; can't move between."""
    core.add("task", kind="production")
    core.wont_do(1, reason="dropped", delta="dropped")
    with pytest.raises(InvariantError, match="wont_do"):
        core.close(1)


def test_wont_do_already_wont_do_refused(core):
    """No double-decide; the decision is locked."""
    core.add("task", kind="production")
    core.wont_do(1, reason="dropped", delta="dropped")
    with pytest.raises(InvariantError, match="already wont_do"):
        core.wont_do(1, reason="dropped again", delta="redundant")


def test_wont_do_on_closed_refused(core):
    """closed=done and wont_do=decided-not-to-do are distinct; can't reclassify
    a done task as not-done. If close was a mistake, supersede with a new task."""
    core.add("task", kind="production")
    core.close(1)
    with pytest.raises(InvariantError, match="closed"):
        core.wont_do(1, reason="changing my mind", delta="this should refuse")


# --- change-of-mind path is a fresh task -----------------------------------

def test_wont_do_then_fresh_task_for_changed_mind(core):
    """v0.4 retires supersede. The change-of-mind path on a wont_do task is
    simply to create a new task with the new direction; the old wont_do row
    stays as historical record. No FK marker linking the two; if a coupling
    matters, link_add records it."""
    core.add("dropped_originally", kind="production")
    core.wont_do(1, reason="not doing", delta="dropped")
    # Change of mind: spawn a new task with the new direction.
    core.add("now_doing_it", kind="production")
    # The wont_do row is untouched.
    assert core.get(1).status == "wont_do"
    assert core.get(1).wont_do_reason == "not doing"
    # The new task exists alongside.
    assert core.get(2).status == "open"


# --- close-gate symmetric with wont_do -------------------------------------

def test_wont_do_refused_when_task_is_stale(core):
    core.add("a", kind="production")
    core.add("b", kind="production")
    core.link_add(2, 1, because="setup")
    core.edit(1, description="x", delta="staling T2")  # stales T2
    with pytest.raises(InvariantError, match="stale"):
        core.wont_do(2, reason="dropped", delta="dropped")


def test_wont_do_refused_when_linked_stale(core):
    """D14 close-gate symmetric: refused if any task in the linked
    neighborhood is stale."""
    core.add("a", kind="production")  # T1
    core.add("b", kind="production")  # T2
    core.add("c", kind="production")  # T3
    core.link_add(2, 1, because="setup")
    core.link_add(3, 2, because="setup")
    core.edit(1, description="x", delta="stales T2")
    with pytest.raises(InvariantError, match="stale"):
        core.wont_do(3, reason="dropped", delta="dropped")


# --- structural ops still allowed on wont_do tasks -------------------------

def test_wont_do_link_add_allowed(core):
    """Like closed tasks, wont_do tasks accept link operations (structural,
    not content) -- agent can migrate edges during supersede."""
    core.add("dropped", kind="production")
    core.add("other", kind="production")
    core.wont_do(1, reason="dropped", delta="dropped")
    s = core.link_add(2, 1, because="historical edge to dropped task")
    assert 1 in [n.id for n in s.dependencies]


def test_wont_do_link_rm_allowed(core):
    core.add("dropped", kind="production")
    core.add("other", kind="production")
    core.link_add(2, 1, because="will be pruned")
    core.wont_do(1, reason="dropped", delta="dropped")
    core.link_rm(2, 1)
    n = core.conn.execute("SELECT COUNT(*) FROM links WHERE task_a = 1 AND task_b = 2").fetchone()[0]
    assert n == 0


def test_wont_do_label_ops_allowed(core):
    core.add("dropped", kind="production")
    core.wont_do(1, reason="dropped", delta="dropped")
    core.label_add(1, "historical")
    assert "historical" in core.labels_of(1)
    core.label_rm(1, "historical")
    assert "historical" not in core.labels_of(1)


# --- cascade-stale interaction (T123 compose) -------------------------------

def test_wont_do_task_cascade_staled_stays_wont_do(core):
    """A wont_do task that gets cascade-staled by an upstream edit stays
    wont_do + stale=True (per T123's relaxed D7, no force-reopen on any
    terminal status)."""
    core.add("upstream", kind="production")
    core.add("dropped_downstream", kind="production")
    core.link_add(2, 1, because="setup")
    core.wont_do(2, reason="dropped", delta="dropped")
    core.edit(1, description="upstream shifted", delta="upstream shift stales T2")
    t2 = core.get(2)
    assert t2.stale is True
    assert t2.status == "wont_do"  # T123 + T132: stays terminal


def test_reconcile_refused_on_wont_do_stale_under_v04(core):
    """v0.4 (D28): reconcile is REFUSED on wont_do tasks (same as closed).
    Their stale flag is record-only and stays as historical signal."""
    core.add("upstream", kind="production")
    core.add("dropped", kind="production")
    core.link_add(2, 1, because="setup")
    core.wont_do(2, reason="dropped", delta="dropped")
    core.edit(1, description="x", delta="staling")
    with pytest.raises(InvariantError, match=r"record-only|archaeology"):
        core.reconcile(2)
    t2 = core.get(2)
    assert t2.stale is True  # preserved as record
    assert t2.status == "wont_do"


# --- migration smoke ------------------------------------------------------

def test_existing_tasks_have_null_wont_do_reason(core):
    """After migration 005, existing tasks have wont_do_reason=NULL (not
    every task gets a wont_do decision)."""
    core.add("regular_task", kind="production")
    assert core.get(1).wont_do_reason is None
