"""v0.4 / D28 + D30 + D31 - bounded-obligation cascade + design-schema gates.

Pins the v0.4 simplification's behavior contract: closed/wont_do stale is
record-only (D28); design/schema kinds are perma-open via close+wont_do
refusal (D30); edit on a design/schema task surfaces a code-check reminder
(D31); search returns status alongside hits so adapters can tag historical
results without opening each one.
"""

import pytest

from tackit.errors import InvariantError


# ----------------------------------------------------------------------------
# D28 - bounded-obligation worklist filter
# ----------------------------------------------------------------------------


def test_stale_worklist_excludes_closed_production(core):
    """Closed production task carrying stale=1 (cascade record) is NOT on the
    worklist. The flag stays as record but doesn't pressure the agent."""
    core.add("upstream", kind="production")  # T1
    core.add("downstream", kind="production")  # T2
    core.link_add(2, 1, because="setup", delta="setup")
    core.close(2)  # T2 closed
    core.edit(1, description="x", delta="upstream shifted")  # cascade stales T2
    # T2 is closed + stale=1 (record), but worklist excludes it.
    t2 = core.get(2)
    assert t2.status == "closed" and t2.stale is True
    worklist_ids = {t.id for t in core.stale_worklist()}
    assert 2 not in worklist_ids


def test_stale_worklist_includes_open_stale(core):
    """Open stale tasks remain on the worklist (the obligation case)."""
    core.add("upstream", kind="production")  # T1
    core.add("downstream", kind="production")  # T2
    core.link_add(2, 1, because="setup", delta="setup")
    core.edit(1, description="x", delta="upstream shifted")
    worklist_ids = {t.id for t in core.stale_worklist()}
    assert 2 in worklist_ids


def test_stale_worklist_includes_design_kind_even_if_closed(core):
    """Design slices are 'living spec' even if hand-closed -- the kind clause
    of the filter keeps them visible regardless of status. (Closing a design
    slice is refused by D30 in normal API use, but the filter is robust to
    test seeds / migration shims that might bypass it.)"""
    # Use a raw SQL UPDATE to bypass D30's refusal -- simulating the test/
    # migration seed case the kind clause defends against.
    core.add("d_slice", kind="design")  # T1
    core.add("p_slice", kind="production")  # T2
    core.link_add(2, 1, because="impl realizes design", delta="setup")
    # Forcibly close T1 (bypassing D30 — only possible in test fixtures).
    core.conn.execute(
        "UPDATE tasks SET status = 'closed', stale = 1 WHERE id = 1;"
    )
    worklist_ids = {t.id for t in core.stale_worklist()}
    # D30/D28 belt-and-suspenders: design+closed+stale still surfaces.
    assert 1 in worklist_ids


# ----------------------------------------------------------------------------
# D28 - close-gate filter (transitive walk skips closed-stale production)
# ----------------------------------------------------------------------------


def test_close_gate_ignores_closed_stale_production_neighbors(core):
    """T3 can close even when T2 (linked, closed, stale) sits in the
    neighborhood. Closed-stale production is record-only per D28."""
    core.add("a", kind="production")  # T1
    core.add("b", kind="production")  # T2
    core.add("c", kind="production")  # T3
    core.link_add(2, 1, because="setup", delta="setup")
    core.link_add(3, 2, because="setup", delta="setup")
    core.close(2)
    core.edit(1, description="x", delta="upstream shifted")  # T2 -> closed-stale
    # Close T3 succeeds; closed-stale T2 doesn't pressure the gate.
    result = core.close(3)
    assert result.task.status == "closed"


def test_close_gate_still_refuses_when_open_stale_in_neighborhood(core):
    """The bounded filter doesn't lift the gate -- it just filters which stale
    neighbors count. An OPEN stale neighbor still blocks close."""
    core.add("a", kind="production")  # T1
    core.add("b", kind="production")  # T2
    core.add("c", kind="production")  # T3
    core.link_add(2, 1, because="setup", delta="setup")
    core.link_add(3, 2, because="setup", delta="setup")
    core.edit(1, description="x", delta="upstream shifted")  # T2 -> open-stale
    with pytest.raises(InvariantError, match="stale"):
        core.close(3)


# ----------------------------------------------------------------------------
# D27 / D28 - links op candidate filter
# ----------------------------------------------------------------------------


def test_links_expansion_excludes_closed_production(core):
    """links(ids=[anchor]) returns viable link targets only -- excluding
    closed/wont_do production neighbors. The anchor layer (no input) is
    already design+schema-only and unchanged."""
    core.add("anchor", kind="design")  # T1
    core.add("live_neighbor", kind="production")  # T2 (linked, open)
    core.add("closed_neighbor", kind="production")  # T3 (linked, closed)
    core.link_add(2, 1, because="setup", delta="setup")
    core.link_add(3, 1, because="setup", delta="setup")
    core.close(3)
    out = core.links(ids=[1])
    ids = {n.id for n in out}
    assert 2 in ids  # open prod -> viable target
    assert 3 not in ids  # closed prod -> excluded


def test_links_expansion_keeps_closed_design_slices(core):
    """Closed design slices (rare; only via test seed) still surface in the
    expansion hop -- the kind clause of the candidate filter keeps living
    spec visible regardless of status."""
    core.add("anchor", kind="production")  # T1
    core.add("design_nbr", kind="design")  # T2
    core.link_add(2, 1, because="setup", delta="setup")
    # Bypass D30 via raw UPDATE to simulate a force-closed design slice.
    core.conn.execute("UPDATE tasks SET status = 'closed' WHERE id = 2;")
    out = core.links(ids=[1])
    ids = {n.id for n in out}
    assert 2 in ids  # design kind keeps it visible even though closed


# ----------------------------------------------------------------------------
# D28 - reconcile refuses on closed/wont_do
# ----------------------------------------------------------------------------


def test_reconcile_refused_on_closed_task(core):
    """Reconcile on a closed task -- regardless of stale=1 or 0 -- is refused.
    Their stale flag is record-only; clearing it has no defined meaning."""
    core.add("a", kind="production")
    core.close(1)
    with pytest.raises(InvariantError, match="terminal"):
        core.reconcile(1)


# ----------------------------------------------------------------------------
# D30 - design/schema kind gates: close + wont_do refused
# ----------------------------------------------------------------------------


def test_close_refused_on_design_kind(core):
    """close() refuses on kind=design. Living spec; the change-of-mind path
    is edit()."""
    core.add("d1", kind="design")
    with pytest.raises(InvariantError, match="LIVING SPEC|living spec|design"):
        core.close(1)


def test_close_refused_on_schema_kind(core):
    core.add("s1", kind="schema")
    with pytest.raises(InvariantError, match="LIVING SPEC|living spec|schema"):
        core.close(1)


def test_wont_do_refused_on_design_kind(core):
    """wont_do() refuses on kind=design. A design decision can't be 'not
    done' -- it either holds or is edited to reflect a changed state."""
    core.add("d1", kind="design")
    with pytest.raises(InvariantError, match="LIVING SPEC|living spec|design"):
        core.wont_do(1, reason="trying to retire", delta="testing")


def test_wont_do_refused_on_schema_kind(core):
    core.add("s1", kind="schema")
    with pytest.raises(InvariantError, match="LIVING SPEC|living spec|schema"):
        core.wont_do(1, reason="trying to retire", delta="testing")


def test_close_allowed_on_production_and_meta(core):
    """The kind gate is design/schema-only. Production and meta close
    normally."""
    core.add("p1", kind="production")
    core.add("m1", kind="meta")
    core.close(1)
    core.close(2)
    assert core.get(1).status == "closed"
    assert core.get(2).status == "closed"


# ----------------------------------------------------------------------------
# D31 - code-check reminder on design/schema edits
# ----------------------------------------------------------------------------


def test_edit_design_sets_code_check_reminder(core):
    """Editing a design task surfaces last_code_check_reminder on Core, so
    adapters (CLI stderr / MCP envelope) can nudge the agent to check
    associated code for drift."""
    core.add("d1", kind="design", description="initial")
    assert core.last_code_check_reminder is None
    core.edit(1, description="updated", delta="refining the slice")
    assert core.last_code_check_reminder is not None
    assert "T1" in core.last_code_check_reminder
    assert "design" in core.last_code_check_reminder.lower()


def test_edit_schema_sets_code_check_reminder(core):
    core.add("s1", kind="schema", description="initial")
    core.edit(1, description="updated", delta="refining the slice")
    assert core.last_code_check_reminder is not None
    assert "schema" in core.last_code_check_reminder.lower()


def test_edit_production_does_not_set_code_check_reminder(core):
    """Production edits don't trigger the D31 nudge -- it's specifically for
    living spec edits where the agent might forget to grep the slice id."""
    core.add("p1", kind="production", description="initial")
    core.edit(1, description="updated", delta="refining")
    assert core.last_code_check_reminder is None


def test_edit_meta_does_not_set_code_check_reminder(core):
    core.add("m1", kind="meta", description="initial")
    core.edit(1, description="updated", delta="refining")
    assert core.last_code_check_reminder is None


def test_code_check_reminder_resets_between_ops(core):
    """Like label_nudge / delta, the reminder reflects only the current op."""
    core.add("d1", kind="design")
    core.edit(1, description="updated", delta="refining")
    assert core.last_code_check_reminder is not None
    # A subsequent non-design op clears the reminder.
    core.add("p1", kind="production")  # not an edit, but next op
    # The next edit on a non-design task should clear and not re-set.
    core.edit(2, description="updated", delta="refining")
    assert core.last_code_check_reminder is None


# ----------------------------------------------------------------------------
# D28 - search inline tag (status field on SearchHit)
# ----------------------------------------------------------------------------


def test_search_hit_carries_status(core):
    """SearchHit includes the task's status so adapters can visually
    distinguish live work from historical record without opening each hit."""
    core.add("alpha target", kind="production")
    core.add("beta target", kind="production")
    core.close(2)
    hits = core.search("target")
    by_id = {h.id: h for h in hits}
    assert by_id[1].status == "open"
    assert by_id[2].status == "closed"


def test_search_hit_includes_wont_do_reason(core):
    """A wont_do hit carries its durable reason inline so search results
    show why the scope was dropped without an extra fetch."""
    core.add("dropped target", kind="production")
    core.wont_do(1, reason="redundant with X", delta="dropping")
    hits = core.search("dropped target")
    assert len(hits) == 1
    assert hits[0].status == "wont_do"
    assert hits[0].wont_do_reason == "redundant with X"


def test_search_hit_wont_do_reason_null_on_non_wont_do(core):
    """wont_do_reason is only populated for wont_do hits."""
    core.add("live target", kind="production")
    hits = core.search("live target")
    assert hits[0].status == "open"
    assert hits[0].wont_do_reason is None
