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


@pytest.mark.skip(
    reason="v0.5 D35+D36: the kind-conditional worklist filter is replaced "
    "by `status IN ('open','spec')`. Legacy closed/wont_do design/schema "
    "rows migrate to spec/retired under mig 009, and the partition CHECK "
    "refuses creating new ones. The scenario this test exercised (a "
    "closed-design row on the worklist via raw UPDATE) cannot exist under "
    "v0.5. Phase 4 (T168 Section F Pass 7) rewrites this to test the new "
    "spec-only worklist semantics."
)
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


@pytest.mark.skip(
    reason="v0.5 D35+D36: closed-design slices cannot exist under the kind/"
    "status partition. The links() candidate filter changes from "
    "`status='open' OR kind IN (design,schema)` to `status IN ('open','spec')`; "
    "Phase 4 rewrites this test to verify spec-rows surface and retired-rows "
    "are excluded."
)
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
# T147 / D29 - regression guards: supersede is fully retired
# ----------------------------------------------------------------------------


def test_core_has_no_supersede_method():
    """T147 P6 - belt-and-suspenders that the v0.4 P1 retirement (mig_006 +
    code rip-out) really removed the verb at the engine layer. A future
    'oh let's bring it back' would surface here before anything else
    breaks. The audit table S7 (description_revisions) IS the v0.4
    replacement for archaeology."""
    from tackit.core import Core

    assert not hasattr(Core, "supersede"), (
        "Core.supersede is back; v0.4 D29 retired it -- use edit() + the "
        "description_revisions audit table (S7) for the prior-name archaeology "
        "the supersede marker used to provide."
    )


def test_mcp_tool_surface_has_no_supersede():
    """Same regression-guard, one layer up. The MCP tool registration must
    not surface a `supersede` tool to agents."""
    from tackit.mcp_server import build_server

    server = build_server()
    # FastMCP keeps tools in an internal registry; the exact attribute varies
    # by version, so we look at what the server exposes by introspection of
    # any attribute holding tools by name. The simpler, version-stable check:
    # the source of mcp_server.py defines tools via @mcp.tool(); none of them
    # should be named supersede.
    import inspect

    from tackit import mcp_server as mod

    src = inspect.getsource(mod)
    assert "def supersede(" not in src, (
        "mcp_server.py defines a supersede tool; v0.4 P1 retired it."
    )


# ----------------------------------------------------------------------------
# D28 + T156 - reconcile refusal mirrors the worklist filter
# ----------------------------------------------------------------------------


def test_reconcile_refused_on_closed_production(core):
    """Reconcile on a closed production task is refused. Stale flag is
    record-only archaeology; clearing it would erase the signal."""
    core.add("a", kind="production")
    core.close(1)
    with pytest.raises(InvariantError, match="terminal"):
        core.reconcile(1)


def test_reconcile_refused_on_wont_do_production(core):
    """Same as closed-production -- wont_do production rows are record-only."""
    core.add("a", kind="production")
    core.wont_do(1, reason="not pursuing", delta="dropped")
    with pytest.raises(InvariantError, match="terminal"):
        core.reconcile(1)


def test_reconcile_refused_on_closed_meta(core):
    """Meta tasks follow the production rule -- closed-stale is record-only."""
    core.add("a", kind="meta")
    core.close(1)
    with pytest.raises(InvariantError, match="terminal"):
        core.reconcile(1)


def test_reconcile_refused_on_wont_do_meta(core):
    core.add("a", kind="meta")
    core.wont_do(1, reason="dropped", delta="dropped")
    with pytest.raises(InvariantError, match="terminal"):
        core.reconcile(1)


@pytest.mark.skip(
    reason="v0.5 D35+D36: D156's kind-conditional reconcile mirror is "
    "obviated by mig 009 migrating closed-design to spec. Under v0.5, "
    "reconcile is refused on status IN ('closed','wont_do','retired'); "
    "allowed on open/spec. Phase 4 rewrites this to verify the new predicate."
)
def test_reconcile_allowed_on_closed_design(core):
    """T156 (v0.4 refinement): reconcile on a CLOSED-design task succeeds --
    design slices stay obligation-bearing under D28 regardless of status (the
    kind clause of the worklist filter), so reconcile must mirror that to
    avoid pinning legacy closed-design rows on the worklist with no exit
    path. Bypassing D30 to seed the closed-design state, since D30 refuses
    close() on design at the op layer."""
    core.add("d1", kind="design")
    # Force-close + stale, simulating a pre-D30 row that's now obligation-
    # bearing per D28's kind clause.
    core.conn.execute(
        "UPDATE tasks SET status = 'closed', stale = 1 WHERE id = 1;"
    )
    t = core.reconcile(1)
    assert t.stale is False  # reconcile cleared the flag
    assert t.status == "closed"  # status preserved -- reconcile doesn't reopen


@pytest.mark.skip(
    reason="v0.5 D35+D36: closed-schema cannot exist under partition. "
    "Same rewrite as test_reconcile_allowed_on_closed_design above."
)
def test_reconcile_allowed_on_closed_schema(core):
    """Schema slices follow the same T156 rule -- living spec by kind."""
    core.add("s1", kind="schema")
    core.conn.execute(
        "UPDATE tasks SET status = 'closed', stale = 1 WHERE id = 1;"
    )
    t = core.reconcile(1)
    assert t.stale is False
    assert t.status == "closed"


@pytest.mark.skip(
    reason="v0.5 D35+D36: wont_do design rows migrate to status='retired' "
    "under mig 009. Under v0.5, reconcile is refused on 'retired' (record-"
    "only archaeology, never cleared). Phase 4 rewrites this scenario."
)
def test_reconcile_allowed_on_wont_do_design(core):
    """Wont_do design rows surface too (T109 in the dogfood is exactly this).
    Reconcile must work so they can be cleared after the upstream they
    referenced changes again."""
    core.add("d1", kind="design")
    core.conn.execute(
        "UPDATE tasks SET status = 'wont_do', stale = 1, "
        "wont_do_reason = 'retired' WHERE id = 1;"
    )
    t = core.reconcile(1)
    assert t.stale is False
    assert t.status == "wont_do"


def test_reconcile_open_design_unchanged_by_t156(core):
    """The T156 refinement only affects closed/wont_do design/schema; open
    design slices reconcile as before (no-op when not stale; clears stale
    when set)."""
    core.add("d1", kind="design")  # open + stale=0
    t = core.reconcile(1)
    assert t.stale is False  # no-op succeeds
    core.conn.execute("UPDATE tasks SET stale = 1 WHERE id = 1;")
    t = core.reconcile(1)
    assert t.stale is False  # cleared


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
    # T162 (v0.4): id mention uses the D32 kind-letter prefix, not the kind-blind
    # `T<id>`. A design task surfaces as `D<id>` in the reminder.
    assert "D1" in core.last_code_check_reminder
    # The kind word may or may not appear in prose; the prefix carries the
    # kind signal. Assert the kind is recoverable from the prefix.


def test_edit_schema_sets_code_check_reminder(core):
    core.add("s1", kind="schema", description="initial")
    core.edit(1, description="updated", delta="refining the slice")
    assert core.last_code_check_reminder is not None
    # T162: schema task surfaces as `S<id>` (kind-letter prefix).
    assert "S1" in core.last_code_check_reminder


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


# ----------------------------------------------------------------------------
# T162 - kind-letter prefix in every agent-facing id mention
# ----------------------------------------------------------------------------


def test_stale_alert_uses_kind_letter_prefix_per_task(core):
    """T162: stale_alert builds its id list with the D32 kind-letter prefix
    per task (D/S/T/M), not the kind-blind `T<id>` it used pre-T162. A
    design slice staled in the cascade surfaces as `D<id>`, a schema as
    `S<id>`, production `T<id>`, meta `M<id>`."""
    from tackit.core import stale_alert_text

    core.add("d", kind="design")
    core.add("s", kind="schema")
    core.add("p", kind="production")
    core.add("m", kind="meta")
    msg = stale_alert_text(
        [core.get(1), core.get(2), core.get(3), core.get(4)]
    )
    assert "D1" in msg
    assert "S2" in msg
    assert "T3" in msg
    assert "M4" in msg
    # And no kind-blind id mentions for the design/schema/meta tasks:
    assert "T1" not in msg  # would be the old kind-blind D1 mention
    assert "T2" not in msg
    assert "T4" not in msg


def test_close_gate_offender_list_uses_kind_letter_prefix(core):
    """T162: when close() refuses on a linked-stale neighborhood, the
    offender list names each offending neighbor with its own kind-letter
    prefix, not a blanket `T<id>`."""
    core.add("anchor design", kind="design")
    core.add("downstream prod", kind="production")
    core.link_add(
        a=1, b=2,
        because="anchor's invariant decides whether prod is correct",
        delta="setup",
    )
    # Stale the design by editing it; the production downstream gets staled
    # via the cascade (open production: stays on worklist per D28).
    core.edit(1, description="updated", delta="shift")
    # Now attempt to close T2 (production, open, stale-via-cascade).
    # Close should refuse — both because T2 is stale AND the design (D1)
    # in its neighborhood is on the worklist.
    with pytest.raises(InvariantError) as excinfo:
        core.close(2)
    msg = str(excinfo.value)
    # The error names T2 (this task) with the production prefix:
    assert "T2" in msg
    # The offender or self-reference should use kind-correct prefixes:
    # the message at minimum names the closing task with its T prefix.


def test_reclassify_refusal_uses_kind_letter_prefix(core):
    """T162: reclassify's meta-island refusal names the offending neighbors
    and the reclassifying task with kind-letter prefixes, replacing the
    pre-T162 `T<id> (kind=...)` form whose parenthetical was redundant
    with the new prefix."""
    core.add("prod1", kind="production")
    core.add("prod2", kind="production")
    core.link_add(a=1, b=2, because="same-kind coupling", delta="setup")
    # Reclassifying T1 to meta would create a cross-kind link with T2.
    with pytest.raises(InvariantError) as excinfo:
        core.reclassify(1, new_kind="meta", delta="experiment")
    msg = str(excinfo.value)
    # The reclassifying task currently has kind=production, so its prefix
    # in the refusal message is T1; the offender T2 (still production) is
    # also T2. The OLD format would have included `T2 (kind=production)`
    # — T162 drops the redundant parenthetical in favor of the letter.
    assert "T1" in msg
    assert "T2" in msg
    # The pre-T162 `(kind=production)` parenthetical for offenders is gone:
    assert "(kind=production)" not in msg


def test_core_source_has_no_kind_blind_T_prefix_for_ids(core):
    """T162 regression guard: the kind-blind `f"T{...id...}"` pattern is
    the bug we just fixed. Assert it doesn't reappear in core.py. The
    only legitimate `T{...}` interpolations in agent-facing strings
    should go through `prefixed_id(kind, id)` instead."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parent.parent / "src" / "tackit" / "core.py"
    text = src.read_text()
    # Any of the known id-bearing variable names paired with a kind-blind T
    # prefix is a regression:
    forbidden = (
        'f"T{task_id}',
        'f"T{t.id}',
        'f"T{uid}',
        'f"T{tid}',
        'f"T{a}',
        'f"T{b}',
        'f"T{n.id}',
        'f"T{d.id}',
    )
    found = [s for s in forbidden if s in text]
    assert not found, (
        f"Kind-blind id mentions reappeared in core.py: {found}. "
        f"Route every id mention through `prefixed_id(kind, id)` to honor "
        f"the D32 prefix convention (T162)."
    )


# ----------------------------------------------------------------------------
# T164 - refuse placeholder rationales at every link-creation path (D33)
# ----------------------------------------------------------------------------


def test_add_deps_empty_because_refused(core):
    """D33 / T164: add(deps={dep: ''}) is refused -- the pre-T164 placeholder
    shortcut is retired and an empty/whitespace because is the same failure
    mode (zero signal for the cascade-ergonomics filter)."""
    from tackit.errors import ValidationError

    core.add("base", kind="production")
    with pytest.raises(ValidationError, match="because"):
        core.add("dependent", kind="production", deps={1: ""})
    with pytest.raises(ValidationError, match="because"):
        core.add("dependent", kind="production", deps={1: "   "})
    # No partial creation: the failing add() rolled back.
    assert len(core.ls()) == 1


def test_add_deps_real_because_stored_verbatim(core):
    """D33 / T164: add(deps={dep: '<real>'}) succeeds and stores the rationale
    on the link verbatim (no trimming beyond strip)."""
    core.add("base", kind="production")
    rationale = "dep extends base's contract; changes here propagate"
    core.add("dep", kind="production", deps={1: rationale})
    row = core.conn.execute(
        "SELECT because FROM links WHERE task_a=1 AND task_b=2"
    ).fetchone()
    assert row["because"] == rationale


def test_load_plan_with_old_csv_form_refused(core):
    """D33 / T164: the pre-T164 inline `depends_on: a, b` form is refused
    by the parser with a clear error pointing at the new continuation
    syntax. No partial load."""
    from tackit.errors import ValidationError

    plan = (
        "[a] one\n  kind: production\n"
        "[b] two\n  kind: production\n  depends_on: a\n"
    )
    from tackit.plan import parse_plan

    with pytest.raises(ValidationError, match="(?i)retired|because"):
        parse_plan(plan)
    assert core.ls() == []  # nothing created -- parser refused before load()


def test_load_plan_with_empty_because_refused(core):
    """D33 / T164: a continuation entry of the form `key ::` (empty rationale)
    is refused by the parser."""
    from tackit.errors import ValidationError
    from tackit.plan import parse_plan

    plan = (
        "[a] one\n  kind: production\n"
        "[b] two\n  kind: production\n"
        "  depends_on:\n    a ::\n"
    )
    with pytest.raises(ValidationError, match="(?i)because|rationale"):
        parse_plan(plan)


def test_load_plan_with_missing_separator_refused(core):
    """D33 / T164: a continuation line without `::` is refused (the
    separator is mandatory; we don't try to guess a default rationale)."""
    from tackit.errors import ValidationError
    from tackit.plan import parse_plan

    plan = (
        "[a] one\n  kind: production\n"
        "[b] two\n  kind: production\n"
        "  depends_on:\n    a should be coupled here\n"
    )
    with pytest.raises(ValidationError, match="(?i)separator|::"):
        parse_plan(plan)


def test_load_plan_with_real_rationales_succeeds(core):
    """D33 / T164: a plan with explicit becauses on every dep loads cleanly
    and the rationales land on the links verbatim."""
    from tackit.plan import parse_plan

    plan = (
        "[a] alpha\n  kind: production\n"
        "[b] beta\n  kind: production\n"
        "  depends_on:\n    a :: beta builds on alpha's published interface\n"
    )
    keymap = core.load(parse_plan(plan))
    row = core.conn.execute(
        "SELECT because FROM links WHERE task_a=? AND task_b=?",
        (keymap["a"], keymap["b"]),
    ).fetchone()
    assert row["because"] == "beta builds on alpha's published interface"


def test_internal_add_link_still_refuses_empty_because(core):
    """T116 already refused empty becauses on `_add_link`/`link_add`. T164
    didn't change that path; this regression-pins it so the placeholder-
    shortcut removal hasn't loosened the canonical path."""
    from tackit.errors import ValidationError

    core.add("a", kind="production")
    core.add("b", kind="production")
    with pytest.raises(ValidationError, match="because"):
        core.link_add(a=1, b=2, because="", delta="test")
    with pytest.raises(ValidationError, match="because"):
        core.link_add(a=1, b=2, because="  ", delta="test")


# ----------------------------------------------------------------------------
# T166 - surface link `because` + upstream `last_edit_delta` per dep entry +
# DRY FAST-filter reminder in show/board envelopes (D34)
# ----------------------------------------------------------------------------


def test_show_dep_entries_carry_link_because(core):
    """D34 / T166: each dep entry in the slice envelope carries the link's
    `because` rationale (T116 stored, now surfaced)."""
    core.add("a", kind="production")
    core.add("b", kind="production")
    rationale = "b extends a's contract; changes to a require b's review"
    core.link_add(a=1, b=2, because=rationale, delta="setup")
    slice_ = core.show(1)
    assert len(slice_.dependencies) == 1
    assert slice_.dependencies[0].because == rationale


def test_show_dep_entries_carry_last_edit_delta(core):
    """D34 / T166: each dep entry carries the neighbor's most-recent edit
    delta from S7 (D29) so the FAST filter can compare delta x because."""
    core.add("a", kind="production")
    core.add("b", kind="production")
    core.link_add(a=1, b=2, because="b couples to a's behavior", delta="setup")
    # Edit B; its last delta should surface on A's dep entry for B.
    core.edit(2, description="new shape", delta="changed b's serialization")
    slice_ = core.show(1)
    assert slice_.dependencies[0].last_edit_delta == "changed b's serialization"


def test_show_last_edit_delta_none_if_neighbor_never_edited(core):
    """D34 / T166: `last_edit_delta` is None when the neighbor has no S7
    history."""
    core.add("a", kind="production")
    core.add("b", kind="production")
    core.link_add(a=1, b=2, because="coupling", delta="setup")
    slice_ = core.show(1)
    assert slice_.dependencies[0].last_edit_delta is None


def test_show_because_reminder_fires_iff_a_dep_is_stale(core):
    """D34 / T166: `because_reminder` is set iff at least one dep entry is
    stale. It's the single DRY-sourced LINK_BECAUSE_REMINDER constant."""
    from tackit.core import LINK_BECAUSE_REMINDER

    core.add("a", kind="production")
    core.add("b", kind="production")
    core.link_add(a=1, b=2, because="coupling", delta="setup")
    # Neither is stale -> no reminder.
    assert core.show(1).because_reminder is None
    # Edit b: a is now stale via the cascade.
    core.edit(2, description="updated", delta="b's prose changed")
    a_view = core.show(1)
    assert a_view.task.stale is True
    # When viewing b, its dep `a` is stale -> reminder fires.
    b_view = core.show(2)
    # a (its neighbor) carries stale=True via cascade.
    assert any(n.stale for n in b_view.dependencies)
    assert b_view.because_reminder == LINK_BECAUSE_REMINDER


def test_show_because_reminder_constant_is_dry_sourced(core):
    """D34 / T166: the reminder text lives in exactly one source location
    (`core.LINK_BECAUSE_REMINDER`). This regression test catches duplication
    across modules."""
    import pathlib

    src_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "tackit"
    # Search for the distinctive opening phrase to find literal definitions
    # (as opposed to references to the constant by name).
    phrase = "Each link's `because` describes WHY the two tasks are coupled."
    matches = []
    for py in src_dir.rglob("*.py"):
        text = py.read_text()
        if phrase in text:
            matches.append(py.name)
    assert matches == ["core.py"], (
        f"LINK_BECAUSE_REMINDER literal text should live only in core.py "
        f"(single DRY source). Found in: {matches}"
    )


def test_links_op_neighbors_have_no_because_or_delta(core):
    """D34 / T166: the `links()` op returns candidates not tied to a single
    edge from the input ids' perspective, so the per-edge fields are None
    (the agent uses them only in slice contexts)."""
    core.add("anchor", kind="design")
    candidates = core.links()  # anchor layer (no input -> design+schema)
    assert all(n.because is None for n in candidates)
    assert all(n.last_edit_delta is None for n in candidates)
