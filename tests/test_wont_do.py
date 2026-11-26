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


def test_wont_do_on_closed_records_the_drop_and_keeps_the_close(core):
    """D56 + D50: a close that should have been a drop is recorded directly
    with wont_do on the closed task. The reason is stored and the history
    reads open -> closed -> wont_do: the close stays on record."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("task", kind="production", deps={1: "realizes the anchor decision"})
    core.close(2)
    result = core.wont_do(2, reason="closed by mistake; the work was dropped")
    assert result.task.status == "wont_do"
    t2 = core.get(2)
    assert t2.status == "wont_do"
    assert t2.wont_do_reason == "closed by mistake; the work was dropped"
    seq = [(h.from_status, h.to_status) for h in core.history(2).status_transitions]
    assert seq == [(None, "open"), ("open", "closed"), ("closed", "wont_do")]


def test_wont_do_on_closed_still_requires_a_reason(core):
    """Same reason rule as on an open task (D56): an empty reason is refused
    and the task stays closed."""
    core.add("spec anchor", kind="design")  # T1
    core.add("task", kind="production", deps={1: "realizes the anchor decision"})
    core.close(2)
    with pytest.raises(ValidationError, match="reason"):
        core.wont_do(2, reason="   ")
    assert core.get(2).status == "closed"


def test_wont_do_on_closed_is_terminal_after(core):
    """After closed -> wont_do, the row is terminal forever: a second wont_do,
    a reopen and a close are all refused, and the history does not move."""
    core.add("spec anchor", kind="design")  # T1
    core.add("task", kind="production", deps={1: "realizes the anchor decision"})
    core.close(2)
    core.wont_do(2, reason="closed by mistake; the work was dropped")
    seq = [(h.from_status, h.to_status) for h in core.history(2).status_transitions]
    with pytest.raises(InvariantError, match="already wont_do"):
        core.wont_do(2, reason="again")
    with pytest.raises(InvariantError, match="wont_do"):
        core.reopen(2)
    with pytest.raises(InvariantError, match="wont_do"):
        core.close(2)
    assert core.get(2).status == "wont_do"
    assert [(h.from_status, h.to_status) for h in core.history(2).status_transitions] == seq


def test_wont_do_on_closed_meets_the_close_gate(core):
    """D56: the same 1-hop gate as on an open task. The closed task's open
    neighbor T3 is stale (an edit of the shared spec flagged it), so wont_do
    on closed T2 is refused and T2 stays closed. Converse: after T3 is
    reconciled the same call succeeds."""
    core.add("spec anchor", kind="design")  # T1
    core.add("done", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("live", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(2, 3, because="T3 builds on what T2 shipped")
    core.close(2)
    core.edit(1, description="changed", delta="the anchor decision shifted")
    assert core.get(3).stale is True and core.get(2).stale is False
    with pytest.raises(InvariantError, match="T3"):
        core.wont_do(2, reason="closed by mistake; the work was dropped")
    assert core.get(2).status == "closed"
    core.reconcile(3)
    assert core.wont_do(2, reason="closed by mistake; the work was dropped").task.status == "wont_do"


# --- surfaces state reopen's correction role and wont_do from closed (D259) ---

def _norm(text):
    import re
    return re.sub(r"\s+", " ", text)


def _mcp_tool_block(name, next_name):
    import inspect
    from tackit import mcp_server
    src = inspect.getsource(mcp_server)
    start = src.index(f"def {name}(")
    return _norm(src[start:src.index(f"def {next_name}(", start)])


def _cli_help(subcommand, capsys):
    from tackit.cli import main
    with pytest.raises(SystemExit):
        main([subcommand, "--help"])
    return _norm(capsys.readouterr().out)


def _skill():
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "src" / "tackit" / "data" / "SKILL.md"
    return _norm(path.read_text(encoding="utf-8"))


def test_mcp_reopen_docstring_states_the_correction_role():
    block = _mcp_tool_block("reopen", "wont_do")
    assert "corrects a closed record" in block
    assert "new task linked to the closed one" in block
    assert "wont_do on the closed task" in block


def test_mcp_wont_do_docstring_allows_a_closed_task():
    block = _mcp_tool_block("wont_do", "retire")
    assert "ALLOWED on a closed task" in block
    assert "wont_do / closed / retired" not in block  # the old refused list


def test_cli_reopen_help_states_the_correction_role(capsys):
    text = _cli_help("reopen", capsys)
    assert "corrects a closed record" in text
    assert "new task linked to the closed one" in text


def test_cli_wont_do_help_allows_a_closed_task(capsys):
    assert "Also allowed on a closed task" in _cli_help("wont-do", capsys)


def test_skill_states_reopen_corrects_and_wont_do_works_on_closed():
    skill = _skill()
    assert "`reopen` corrects a closed record" in skill
    assert "new task linked to the closed one" in skill
    assert "also works on a closed task" in skill
    assert "resumes the same work" not in skill  # the old reopen role


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

def test_wont_do_task_not_flagged_by_upstream_edit(core):
    """D137: an upstream edit does not write a wont_do neighbor -- it stays
    wont_do, unflagged, with its updated_at unchanged."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("upstream", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("dropped_downstream", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="setup")
    core.wont_do(3, reason="dropped")
    before = core.get(3).updated_at
    core.edit(2, description="upstream shifted", delta="upstream shift skips T3")
    t3 = core.get(3)
    assert t3.stale is False
    assert t3.updated_at == before
    assert t3.status == "wont_do"


def test_reconcile_refused_on_wont_do(core):
    """D56 + D137: reconcile is REFUSED on wont_do tasks (same as closed):
    they carry no stale flag, so there is nothing to clear."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("upstream", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("dropped", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="setup")
    core.wont_do(3, reason="dropped")
    core.edit(2, description="x", delta="staling")
    with pytest.raises(InvariantError, match=r"carry no stale flag"):
        core.reconcile(3)
    t3 = core.get(3)
    assert t3.stale is False
    assert t3.status == "wont_do"


# --- migration smoke ------------------------------------------------------

def test_existing_tasks_have_null_wont_do_reason(core):
    """After migration 005, existing tasks have wont_do_reason=NULL (not
    every task gets a wont_do decision)."""
    core.add("spec anchor", kind="design")  # T1 -- D256 creation-gate anchor
    core.add("regular_task", kind="production", deps={1: "realizes the anchor decision"})
    assert core.get(2).wont_do_reason is None
