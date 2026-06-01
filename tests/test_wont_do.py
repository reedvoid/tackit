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
    core.link_add(2, 1, because="setup", delta="setup")
    core.link_add(3, 1, because="setup", delta="setup")
    result = core.wont_do(1, reason="not doing this", delta="dropped")
    nbr_ids = sorted(n.id for n in result.dependencies)
    assert nbr_ids == [2, 3]


def test_wont_do_logs_transition(core):
    core.add("task", kind="production")
    core.wont_do(1, reason="dropped", delta="not doing it")
    seq = [(h.from_status, h.to_status) for h in core.history(1)]
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

def test_wont_do_edit_refused(core):
    """T118 extends to wont_do -- the task content is frozen."""
    core.add("task", kind="production")
    core.wont_do(1, reason="dropped", delta="dropped")
    with pytest.raises(InvariantError, match="terminal"):
        core.edit(1, description="trying to change it", delta="should refuse")


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


# --- supersede IS the change-of-mind path ----------------------------------

def test_supersede_a_wont_do_task_works(core):
    """supersede is content-immutable (only sets superseded_by marker); a
    wont_do task can be superseded with a new task carrying the new
    direction. T132 explicitly intends supersede as the change-of-mind path."""
    core.add("dropped_originally", kind="production")
    core.add("now_doing_it", kind="production")
    core.wont_do(1, reason="not doing", delta="dropped")
    result = core.supersede(1, 2, delta="changed our mind; new task carries the work")
    assert result.old.task.superseded_by == 2
    # T1 stays wont_do as the historical record.
    assert result.old.task.status == "wont_do"


# --- close-gate symmetric with wont_do -------------------------------------

def test_wont_do_refused_when_task_is_stale(core):
    core.add("a", kind="production")
    core.add("b", kind="production")
    core.link_add(2, 1, because="setup", delta="setup")
    core.edit(1, description="x", delta="staling T2")  # stales T2
    with pytest.raises(InvariantError, match="stale"):
        core.wont_do(2, reason="dropped", delta="dropped")


def test_wont_do_refused_when_linked_stale(core):
    """D14 close-gate symmetric: refused if any task in the linked
    neighborhood is stale."""
    core.add("a", kind="production")  # T1
    core.add("b", kind="production")  # T2
    core.add("c", kind="production")  # T3
    core.link_add(2, 1, because="setup", delta="setup")
    core.link_add(3, 2, because="setup", delta="setup")
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
    s = core.link_add(2, 1, because="historical edge to dropped task", delta="adding edge")
    assert 1 in [n.id for n in s.dependencies]


def test_wont_do_link_rm_allowed(core):
    core.add("dropped", kind="production")
    core.add("other", kind="production")
    core.link_add(2, 1, because="will be pruned", delta="setup")
    core.wont_do(1, reason="dropped", delta="dropped")
    core.link_rm(2, 1, delta="pruning edges from dropped task")
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
    core.link_add(2, 1, because="setup", delta="setup")
    core.wont_do(2, reason="dropped", delta="dropped")
    core.edit(1, description="upstream shifted", delta="upstream shift stales T2")
    t2 = core.get(2)
    assert t2.stale is True
    assert t2.status == "wont_do"  # T123 + T132: stays terminal


def test_reconcile_a_wont_do_stale_keeps_status(core):
    """reconcile on a wont_do-stale task clears stale, status stays wont_do."""
    core.add("upstream", kind="production")
    core.add("dropped", kind="production")
    core.link_add(2, 1, because="setup", delta="setup")
    core.wont_do(2, reason="dropped", delta="dropped")
    core.edit(1, description="x", delta="staling")
    core.reconcile(2)
    t2 = core.get(2)
    assert t2.stale is False
    assert t2.status == "wont_do"


# --- migration smoke ------------------------------------------------------

def test_existing_tasks_have_null_wont_do_reason(core):
    """After migration 005, existing tasks have wont_do_reason=NULL (not
    every task gets a wont_do decision)."""
    core.add("regular_task", kind="production")
    assert core.get(1).wont_do_reason is None
