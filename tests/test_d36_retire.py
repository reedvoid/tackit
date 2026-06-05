"""v0.5 / D36 / T174 - retire() verb + link_add retired-endpoint refusal.

Pins the retire() mechanism: the 6-step refusal matrix (status, kind, stale,
linked-stale, open-neighbor, placeholder-reason), the state transition
(spec->retired + reason write + status_transitions row, NO cascade, NO
description_revisions), terminal-state refusals (no double-decide), and
link_add refusal when either endpoint is retired.

retire() is the all-or-nothing decision-retirement verb for design/schema
slices: 100% gone, no replacement. Partial-change path is edit() + let the
cascade prompt link review.
"""

import pytest

from tackit.errors import InvariantError, ValidationError


def _force_status(core, task_id, status):
    """Raw UPDATE for partition-aware seeding when no public verb reaches the
    target state (e.g. status='retired' for some refusal tests). Mirrors the
    helpers in test_engine_edges.py / test_links_op.py."""
    core.conn.execute(
        "UPDATE tasks SET status = ? WHERE id = ?", (status, task_id)
    )


# ============================================================================
# State transitions -- happy paths
# ============================================================================


def test_retire_spec_design_to_retired(core):
    """retire() on a spec design slice moves status spec->retired and writes
    the reason to wont_do_reason."""
    core.add("d1", kind="design")
    assert core.get(1).status == "spec"
    result = core.retire(1, reason="premise replaced by D99", delta="retiring D1")
    t = core.get(1)
    assert t.status == "retired"
    assert t.wont_do_reason == "premise replaced by D99"
    assert result.task.id == 1
    assert result.task.status == "retired"


def test_retire_spec_schema_to_retired(core):
    """Schema slices retire identically to design (same partition)."""
    core.add("s1", kind="schema")
    core.retire(1, reason="table dropped in mig 099", delta="retiring S1")
    assert core.get(1).status == "retired"
    assert core.get(1).wont_do_reason == "table dropped in mig 099"


def test_retire_appends_status_transition_spec_to_retired(core):
    """A status_transitions row is appended (D8)."""
    core.add("d1", kind="design")
    core.retire(1, reason="legit reason", delta="retiring")
    transitions = core.history(1).status_transitions
    last = transitions[-1]
    assert last.from_status == "spec"
    assert last.to_status == "retired"


def test_retire_does_not_stale_neighbors(core):
    """retire() does NOT fire the cascade (status change, not content edit;
    symmetric with close + wont_do)."""
    core.add("d1", kind="design")
    core.add("p1", kind="production")
    core.link_add(1, 2, because="prod realizes design")
    core.close(2)  # close prod so it isn't an open neighbor
    core.retire(1, reason="premise replaced", delta="retiring")
    assert core.get(2).stale is False


def test_retire_does_not_write_description_revision(core):
    """retire() does NOT add a description_revisions row (no content edit)."""
    core.add("d1", kind="design")
    core.retire(1, reason="legit", delta="retiring")
    revs = core.history(1).description_revisions
    assert revs == []


def test_retire_returns_linked_neighbors_in_payload(core):
    """The WontDoResult payload carries the one-hop linked set as of
    retirement (mirrors close/wont_do)."""
    core.add("d1", kind="design")
    core.add("d2", kind="design")
    core.link_add(1, 2, because="related decisions")
    result = core.retire(1, reason="d2 superseded d1", delta="retiring")
    nbr_ids = {n.id for n in result.dependencies}
    assert 2 in nbr_ids


def test_retire_allowed_on_zero_neighbors(core):
    core.add("d1", kind="design")
    core.retire(1, reason="legit", delta="retiring")
    assert core.get(1).status == "retired"


def test_retire_allowed_on_only_spec_neighbors(core):
    """All neighbors are spec (other living specs) -- not blocked by the
    open-neighbor check, which targets status='open' specifically."""
    core.add("d1", kind="design")
    core.add("d2", kind="design")  # spec by default
    core.link_add(1, 2, because="related")
    core.retire(1, reason="legit", delta="retiring")
    assert core.get(1).status == "retired"


def test_retire_allowed_on_only_terminal_neighbors(core):
    """Neighbors that are closed/wont_do/retired don't block retire."""
    core.add("d1", kind="design")
    core.add("p1", kind="production")
    core.add("p2", kind="production")
    core.link_add(1, 2, because="prod realizes design")
    core.link_add(1, 3, because="prod realizes design")
    core.close(2)
    core.wont_do(3, reason="dropped", delta="dropped")
    core.retire(1, reason="legit", delta="retiring")
    assert core.get(1).status == "retired"


# ============================================================================
# Refusal: non-spec status
# ============================================================================


@pytest.mark.parametrize(
    "status,kind",
    [
        ("open", "production"),
        ("closed", "production"),
        ("wont_do", "production"),
        ("retired", "design"),
    ],
)
def test_retire_refused_on_non_spec_status(core, status, kind):
    """retire() refused unless status='spec'. Parametrize covers each non-
    spec status with a partition-valid kind."""
    core.add("x", kind=kind)
    if status != "open":
        _force_status(core, 1, status)
    with pytest.raises(InvariantError, match=r"status=|spec"):
        core.retire(1, reason="legit", delta="trying to retire non-spec")


def test_retire_refused_on_production_kind(core):
    """retire() on a production task is refused (status check fires first --
    production can't have status='spec')."""
    core.add("p1", kind="production")
    with pytest.raises(InvariantError, match=r"spec|status"):
        core.retire(1, reason="legit", delta="trying")


def test_retire_refused_on_meta_kind(core):
    core.add("m1", kind="meta")
    with pytest.raises(InvariantError, match=r"spec|status"):
        core.retire(1, reason="legit", delta="trying")


# ============================================================================
# Refusal: stale + linked-stale gate (same close-gate logic)
# ============================================================================


def test_retire_refused_on_stale_target(core):
    core.add("d1", kind="design")
    core.conn.execute("UPDATE tasks SET stale = 1 WHERE id = 1;")
    with pytest.raises(InvariantError, match=r"stale"):
        core.retire(1, reason="legit", delta="trying")


def test_retire_refused_on_linked_stale_neighbor(core):
    """A spec target whose linked neighbor is open+stale (obligation-bearing)
    is refused by the close-gate logic."""
    core.add("d1", kind="design")
    core.add("p1", kind="production")
    core.add("p2", kind="production")
    core.link_add(1, 2, because="prod realizes design")
    core.link_add(2, 3, because="p1 consumes p2")
    core.edit(3, description="x", delta="p2 shifted")  # stales p1 (T2)
    assert core.get(2).stale is True and core.get(2).status == "open"
    with pytest.raises(InvariantError, match=r"unreconciled|stale"):
        core.retire(1, reason="legit", delta="trying")


# ============================================================================
# Refusal: open-neighbor check (the distinguishing refusal)
# ============================================================================


def test_retire_refused_on_open_neighbor_lists_each_with_because(core):
    """retire() refusal when a linked neighbor is status='open' lists each
    such neighbor with its `because` rationale."""
    core.add("d1", kind="design")
    core.add("p1", kind="production")
    core.add("p2", kind="production")
    core.link_add(1, 2, because="p1 realizes d1 contract")
    core.link_add(1, 3, because="p2 realizes d1 sub-contract")
    with pytest.raises(InvariantError) as excinfo:
        core.retire(1, reason="legit", delta="trying")
    msg = str(excinfo.value)
    assert "T2" in msg and "T3" in msg
    assert "p1 realizes d1 contract" in msg
    assert "p2 realizes d1 sub-contract" in msg


def test_retire_open_neighbor_refusal_contains_decision_tree(core):
    """The refusal message presents the (i)/(ii) decision tree (link_rm +
    wont_do vs link_rm alone) so the agent has the workflow inline."""
    core.add("d1", kind="design")
    core.add("p1", kind="production")
    core.link_add(1, 2, because="setup")
    with pytest.raises(InvariantError) as excinfo:
        core.retire(1, reason="legit", delta="trying")
    msg = str(excinfo.value)
    assert "(i)" in msg
    assert "(ii)" in msg
    assert "link_rm" in msg
    assert "wont_do" in msg


# ============================================================================
# Refusal: placeholder reason (D33 extension)
# ============================================================================


@pytest.mark.parametrize(
    "reason",
    [
        "",
        "   ",
        "TBD",
        "TODO",
        "obsolete",
        "no longer needed",
        "tbd",
        "todo",
        "Obsolete",
        "NO LONGER NEEDED",
    ],
)
def test_retire_refused_on_placeholder_reason(core, reason):
    """Reason is durable -- placeholder strings refused per D33 extension."""
    core.add("d1", kind="design")
    with pytest.raises(ValidationError, match=r"reason|rationale|placeholder"):
        core.retire(1, reason=reason, delta="trying")


def test_retire_writes_reason_to_wont_do_reason_column(core):
    """The reason persists in the wont_do_reason column (durable; no edit
    path -- D36 spec mirrors wont_do's locked reason)."""
    core.add("d1", kind="design")
    core.retire(1, reason="premise replaced by D99 + S42", delta="retiring")
    row = core.conn.execute(
        "SELECT wont_do_reason FROM tasks WHERE id = 1"
    ).fetchone()
    assert row["wont_do_reason"] == "premise replaced by D99 + S42"


# ============================================================================
# Terminal-state refusals -- no double-decide
# ============================================================================


def test_retire_refused_on_already_retired(core):
    core.add("d1", kind="design")
    core.retire(1, reason="legit", delta="retiring")
    with pytest.raises(InvariantError, match=r"retired|already|status"):
        core.retire(1, reason="trying again", delta="re-retiring")


def test_close_refused_on_retired(core):
    """closed/wont_do/retired are distinct terminal states (T132 + D36)."""
    core.add("d1", kind="design")
    core.retire(1, reason="legit", delta="retiring")
    with pytest.raises(InvariantError):
        core.close(1)


def test_wont_do_refused_on_retired(core):
    core.add("d1", kind="design")
    core.retire(1, reason="legit", delta="retiring")
    with pytest.raises(InvariantError):
        core.wont_do(1, reason="trying", delta="trying")


def test_reopen_on_spec_is_noop(core):
    """v0.5 D36: reopen on a row already at its kind's live status (spec
    for design/schema, open for production/meta) is a no-op -- the row
    stays put, no version bump, no partition CHECK violation. Reopen()
    used to assume the only live status was 'open', which under D36
    would have triggered an UPDATE setting status='open' on a design
    row -- a partition CHECK violation. The no-op guard now includes
    'spec'. Caught by the T176 property machine."""
    core.add("d1", kind="design")
    assert core.get(1).status == "spec"
    t = core.reopen(1)
    assert t.status == "spec"  # still spec, not 'open'
    assert core.get(1).status == "spec"


def test_reopen_refused_on_retired_with_fresh_D_message(core):
    """Reopen on a retired row is refused; message names the 'fresh D#'
    path as the right move when the decision returns."""
    core.add("d1", kind="design")
    core.retire(1, reason="legit", delta="retiring")
    with pytest.raises(InvariantError) as excinfo:
        core.reopen(1)
    msg = str(excinfo.value)
    assert "retired" in msg.lower()
    assert "fresh" in msg.lower() or "new" in msg.lower()


# ============================================================================
# link_add retired-endpoint refusal
# ============================================================================


def test_link_add_refused_when_a_is_retired(core):
    core.add("d1", kind="design")
    core.add("p1", kind="production")
    core.retire(1, reason="legit", delta="retiring")
    with pytest.raises(InvariantError, match=r"retired"):
        core.link_add(1, 2, because="trying to link")


def test_link_add_refused_when_b_is_retired(core):
    core.add("p1", kind="production")
    core.add("d1", kind="design")
    core.retire(2, reason="legit", delta="retiring")
    with pytest.raises(InvariantError, match=r"retired"):
        core.link_add(1, 2, because="trying to link")


def test_link_add_refused_when_both_retired(core):
    core.add("d1", kind="design")
    core.add("d2", kind="design")
    core.retire(1, reason="legit a", delta="retiring")
    core.retire(2, reason="legit b", delta="retiring")
    with pytest.raises(InvariantError, match=r"retired"):
        core.link_add(1, 2, because="trying to link")


def test_link_add_retired_refusal_message_format(core):
    """The refusal message names the retired endpoint with its kind-letter
    prefix and explains that retired specs accept no new edges."""
    core.add("d1", kind="design")
    core.add("p1", kind="production")
    core.retire(1, reason="legit", delta="retiring")
    with pytest.raises(InvariantError) as excinfo:
        core.link_add(1, 2, because="trying to link")
    msg = str(excinfo.value)
    assert "D1" in msg
    assert "retired" in msg


# ============================================================================
# Edit on retired -- still allowed per D29 (audit-table backstop)
# ============================================================================


def test_edit_on_retired_allowed(core):
    """v0.4 D29 allows edit on any status -- retired is no exception. The
    description_revisions row preserves the verbatim prior state."""
    core.add("d1", kind="design", description="initial")
    core.retire(1, reason="legit", delta="retiring")
    core.edit(1, description="updated archaeology", delta="post-retire fix")
    t = core.get(1)
    assert t.description == "updated archaeology"
    assert t.status == "retired"


def test_edit_on_retired_fires_cascade_record_only(core):
    """Edit on retired fires the cascade depth-1; closed neighbors get
    flagged stale (record-only per D28)."""
    core.add("d1", kind="design")
    core.add("p1", kind="production")
    core.link_add(1, 2, because="prod realizes design")
    core.close(2)  # close so retire isn't blocked by open neighbor
    core.retire(1, reason="legit", delta="retiring")
    core.edit(1, description="updated", delta="post-retire prose fix")
    assert core.get(2).stale is True  # cascade fired
    # But T2 is closed-stale, record-only -- not on the worklist:
    worklist_ids = {t.id for t in core.stale_worklist()}
    assert 2 not in worklist_ids


def test_edit_on_retired_fires_code_check_reminder(core):
    """D31: edit on a retired design/schema row STILL fires the code-check
    reminder. The kind clause is unchanged -- both spec and retired slices
    are kind in design/schema."""
    core.add("d1", kind="design")
    core.retire(1, reason="legit", delta="retiring")
    core.edit(1, description="updated", delta="post-retire edit")
    assert core.last_code_check_reminder is not None
    assert "D1" in core.last_code_check_reminder
