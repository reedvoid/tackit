"""v0.5 / D37 / T175 - granular-description discipline: surface-presence tests.

Pins the verbatim phrases that the agent-facing surfaces (MCP docstrings + CLI
--help output) must carry for D37 (granular-description) and D36 (retire +
refusal-message bank). Substring checks so a future wording edit is deliberate
-- if the phrase moves, a test fails and forces the agent to update both the
surface and this pin together.

Two phases are deferred:
  - SKILL.md surface (Phase 6 / T169): the in-agent skill prose.
  - README.md surface (Phase 7 / T170): the install-page walkthrough.
Tests for those are marked SKIP until those phases land.
"""

import inspect
import re

import pytest


# Distinctive D37 phrases the surfaces must carry.
GRANULAR_PHRASE_ADD = "impl-ready granularity"
GRANULAR_PHRASE_EDIT_PARTIAL = "Use edit for ALL partial changes"
GRANULAR_PHRASE_EDIT_FOLDBACK = "fold them back BEFORE close"


def _normspace(text: str) -> str:
    """Collapse all whitespace runs (incl. newlines from triple-quoted
    docstrings and argparse line-wrapping) to single spaces, so substring
    matches survive prose formatting."""
    return re.sub(r"\s+", " ", text)


def _contains(haystack: str, needle: str) -> bool:
    return _normspace(needle) in _normspace(haystack)


def _mcp_tool_source(tool_name: str) -> str:
    """Return the source text of an MCP tool function via inspect.

    FastMCP wraps the underlying function and registers it on the server. We
    pull the source of the module-level function definition by string search
    (no need to introspect the registry -- the source file IS the contract)."""
    from tackit import mcp_server
    src = inspect.getsource(mcp_server)
    return src


# ============================================================================
# MCP docstring presence -- D37 granular-description guidance
# ============================================================================


def test_mcp_add_docstring_contains_granularity_guidance():
    """add() MCP docstring carries the D37 impl-ready granularity guidance,
    so an agent reading the tool description at create time gets the
    discipline inline."""
    src = _mcp_tool_source("add")
    # Locate the `def add(` block and assert the phrase appears within it
    # (not just somewhere else in the file).
    start = src.index("def add(")
    end = src.index("def show(")  # next tool def bounds add()'s scope
    add_block = src[start:end]
    assert _contains(add_block, GRANULAR_PHRASE_ADD), (
        f"MCP add() docstring must contain {GRANULAR_PHRASE_ADD!r} (D37). "
        f"This is the create-time discipline prompt -- agents read add()'s "
        f"docstring before creating tasks, so the granularity rule lives "
        f"there inline."
    )


def test_mcp_edit_docstring_contains_granularity_guidance():
    """edit() MCP docstring carries D37's edit-vs-retire-vs-close decision
    tree + the fold-back-via-edit guidance."""
    src = _mcp_tool_source("edit")
    start = src.index("def edit(")
    end = src.index("def close(")
    edit_block = src[start:end]
    assert _contains(edit_block, GRANULAR_PHRASE_EDIT_PARTIAL), (
        f"MCP edit() docstring must contain {GRANULAR_PHRASE_EDIT_PARTIAL!r} "
        f"(D37): the edit-vs-retire decision prompt."
    )
    assert _contains(edit_block, GRANULAR_PHRASE_EDIT_FOLDBACK), (
        f"MCP edit() docstring must contain {GRANULAR_PHRASE_EDIT_FOLDBACK!r} "
        f"(D37): the fold-back-before-close discipline."
    )


# ============================================================================
# CLI --help presence -- D37 granular-description guidance
# ============================================================================


def _cli_help(subcommand: str, capsys) -> str:
    """Invoke `tackit <subcommand> --help` via main(); argparse prints help
    and SystemExit(0)s. Return captured stdout."""
    from tackit.cli import main
    with pytest.raises(SystemExit):
        main([subcommand, "--help"])
    return capsys.readouterr().out


def test_cli_add_help_contains_granularity_guidance(capsys):
    """`tackit add --help` carries the D37 granularity guidance."""
    out = _cli_help("add", capsys)
    assert _contains(out, GRANULAR_PHRASE_ADD), (
        f"`tackit add --help` must contain {GRANULAR_PHRASE_ADD!r} (D37)."
    )


def test_cli_edit_help_contains_granularity_guidance(capsys):
    """`tackit edit --help` carries the D37 fold-back guidance."""
    out = _cli_help("edit", capsys)
    assert _contains(out, GRANULAR_PHRASE_EDIT_FOLDBACK), (
        f"`tackit edit --help` must contain {GRANULAR_PHRASE_EDIT_FOLDBACK!r} "
        f"(D37)."
    )


# ============================================================================
# Deferred -- SKILL.md (Phase 6 / T169) and README (Phase 7 / T170)
# ============================================================================


def test_skill_md_contains_granular_description_section():
    """SKILL.md carries a dedicated forceful section on the granular-
    description discipline (D37 / T169 Phase 6 deliverable). Pins the
    section heading + the rule statement's distinctive phrase + the
    anti-pattern enumeration so a future edit that drops or weakens any
    load-bearing piece fails here.

    The canonical SKILL.md is the packaged version under
    src/tackit/data/SKILL.md (what ships to users); the dev copies sync
    from it per test_skill_md_dev_copies_match_canonical."""
    import pathlib
    skill = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / "tackit" / "data" / "SKILL.md"
    )
    text = skill.read_text()
    # Section exists at top level (not buried).
    assert "## The granular-description discipline" in text, (
        "SKILL.md must carry a top-level `## The granular-description "
        "discipline` section -- the primary teaching surface for D37. The "
        "fresh-session-revisit rule lives here; without the section, the "
        "discipline is only reachable via tool docstrings (which an agent "
        "may skim) and not at session-start context load."
    )
    # The rule's distinctive phrase is present verbatim.
    assert "implementation-ready" in text, (
        "SKILL.md must carry the rule's distinctive phrase 'implementation-"
        "ready' in the granular-description section. This is the anchor "
        "agents grep for when looking up the discipline."
    )
    # The fresh-session-revisit framing is present.
    assert "fresh-session" in text, (
        "SKILL.md must frame D37 around fresh-session revisit -- the "
        "negative-space test for whether a description is granular enough."
    )
    # Anti-patterns are enumerated (load-bearing — agents look here for the
    # closed list of what's NOT allowed). Under the D41 format these live in
    # the section's `don't-do:` line; pin a distinctive member so the
    # enumeration can't be silently dropped.
    assert "pointer-only" in text, (
        "SKILL.md must enumerate the D37 anti-patterns explicitly (vague "
        "verbs / conversation references / pointer-only bodies / TBD-TODO). "
        "The enumeration is what makes the rule actionable -- agents "
        "reference it when reviewing their own task bodies."
    )


def test_skill_md_contains_foldback_discipline_section():
    """The canonical SKILL.md (src/tackit/data/SKILL.md, the version shipped
    in the package) carries the mandatory fold-back discipline section.
    Distinctive phrases pinned: the section heading + the mandatory end-of-
    turn report wording + the meta-lesson on enumeration by pattern.

    Pins the strengthened fold-back rule landed alongside v0.5 so a future
    edit that drops or weakens any of those three load-bearing pieces fails
    here. The discipline matters more than the wording, but the wording is
    what survives across context resets — so the pins are on phrases that
    encode the discipline."""
    import pathlib
    skill = pathlib.Path(__file__).resolve().parent.parent / "src" / "tackit" / "data" / "SKILL.md"
    text = skill.read_text()
    # The section exists and is top-level (## heading), not buried in a bullet.
    assert "## Fold-backs" in text, (
        "SKILL.md must carry a top-level `## Fold-backs` section -- the "
        "mandatory fold-back discipline. Was previously a single bullet "
        "inside the code↔task traceability section, where it got skipped "
        "in practice. Promoting it to a top-level section is load-bearing."
    )
    # The mandatory end-of-turn report requirement is present verbatim.
    assert "Mandatory end-of-turn fold-back report" in text, (
        "SKILL.md must require the end-of-turn fold-back line explicitly. "
        "The 'none -- verified' negative case is what makes the positive "
        "case credible; dropping the requirement returns the discipline to "
        "permissive 'if you remember' status."
    )
    # The enumeration meta-lesson is present (grep pattern, not verb name).
    assert "grep for the pattern family" in text, (
        "SKILL.md must carry the enumeration meta-lesson learned from the "
        "v0.5 Phase 4 bugs: enumerate by pattern (grep the family), not by "
        "named verb. Both Phase 4 bugs (reopen-on-spec, load-status) traced "
        "to the same root failure mode."
    )


def test_skill_md_contains_ship_on_pain_section():
    """The canonical SKILL.md (src/tackit/data/SKILL.md) carries the
    ship-on-pain discipline section. T179 / T182.

    Ship-on-pain is THE rule the other discipline rules serve: when
    friction NOW > cost of pausing to ship the fix, the workaround IS the
    forcing function. Without this rule, fold-backs / granular-description
    / propagation become slogans. The pin asserts the section heading,
    the overrides-bundle-first principle (so a "soften it" edit fails
    here), and the anchoring incident reference (so the lesson can't be
    sanitized out of the prose).
    """
    import pathlib
    skill = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / "tackit" / "data" / "SKILL.md"
    )
    text = skill.read_text()
    # Top-level section exists.
    assert "## Ship-on-pain" in text, (
        "SKILL.md must carry a top-level `## Ship-on-pain` section. The "
        "rule is the most load-bearing discipline in the file -- without "
        "ship-on-pain, the other rules (fold-backs, granular-description, "
        "propagation) are intellectualized patterns that bend under release "
        "pressure. The section heading is what an agent greps for."
    )
    # The overrides-bundle-first principle is present.
    assert "OVERRIDES" in text and "finish the current bundle first" in text, (
        "SKILL.md must state explicitly that ship-on-pain OVERRIDES "
        "'finish the current bundle first' -- this is the load-bearing "
        "tension the rule resolves. A softer phrasing returns the "
        "discipline to advisory status."
    )
    # The T179 anchoring incident is referenced.
    assert "T179" in text and "Standalone" in text, (
        "SKILL.md must reference the T179 anchoring incident (the diff-edit "
        "ops that sat at status='open' through 8 release phases) and the "
        "'Standalone -- NOT part of the current bundle' smell pattern. The "
        "incident is what makes the rule actionable; without it the rule "
        "reads as a slogan."
    )


def test_skill_md_verb_taxonomy_mentions_diff_edit_ops():
    """The verb taxonomy section in canonical SKILL.md mentions both T179
    diff-shaped edit ops (edit_append + edit_replace_substring). Pins the
    follow-up that T179's own body flagged for a separate task. Without
    the mention, agents reading the verb taxonomy at session start won't
    know the diff ops exist and will default to full-body edit() forever.
    """
    import pathlib
    skill = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / "tackit" / "data" / "SKILL.md"
    )
    text = skill.read_text()
    assert "edit_append" in text, (
        "SKILL.md verb taxonomy / discipline sections must mention "
        "edit_append (T179). It is operationally equivalent to edit() but "
        "diff-shaped; agents need to know it exists."
    )
    assert "edit_replace_substring" in text, (
        "SKILL.md verb taxonomy / discipline sections must mention "
        "edit_replace_substring (T179) with its unique-match contract."
    )


def test_skill_md_contains_logical_boundaries_signal():
    """The canonical SKILL.md "Right-size tasks" bullet carries the
    logical-boundaries signal phrase. Sharpens the existing "split it"
    guidance with a concrete signal an agent can detect at add() time.

    Memory [[logical-boundaries-task-split]] (2026-06-02) named the rule;
    T169 (Phase 6 SKILL.md sweep) shipped without it -- enumeration-by-
    named-target failure mode. This pin asserts the addition is present so
    a future edit that drops it fails here. Distinctive phrases pinned:
    the second-heading signal + the co-equal-sections qualifier (nested
    detail is allowed; what's forbidden is sibling sections describing
    independent units).
    """
    import pathlib
    skill = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / "tackit" / "data" / "SKILL.md"
    )
    text = skill.read_text()
    assert "second `###` heading" in text, (
        "SKILL.md `Right-size tasks` bullet must carry the second-heading "
        "signal phrase. The signal is the actionable test an agent can run "
        "at add() time -- without it, the existing 'split it' wording is "
        "too abstract to fire on real bodies. Anchor: T168 grew to 54k "
        "chars before the rule caught it."
    )
    assert "co-equal sections describing" in text, (
        "SKILL.md must carry the co-equal-sections qualifier explicitly. "
        "Without it the rule looks like 'no `###` allowed' -- nested detail "
        "within one logical unit is fine; the forbidden shape is sibling "
        "sections describing independent execution units."
    )


def test_mcp_edit_family_docstrings_carry_edit_quality_discipline():
    """All three MCP edit verbs (edit, edit_append, edit_replace_substring)
    carry the edit-quality discipline summary in their docstring -- the
    invocation-moment teaching surface where the agent reads right
    before paying the cascade cost. Per D36 propagation: the rule lives
    on every surface where the agent invokes the cost-incurring verbs.

    Pins three load-bearing phrases ("Edits aren't free" / "consequential
    and necessary" / "substantive impact") on each docstring. A future
    edit that softens or drops any phrase on any tool fails here.
    """
    from tackit import mcp_server
    import inspect
    src = inspect.getsource(mcp_server)
    boundaries = [
        ("def edit(", "def edit_append(", "edit"),
        ("def edit_append(", "def edit_replace_substring(", "edit_append"),
        ("def edit_replace_substring(", "def close(", "edit_replace_substring"),
    ]
    for start_marker, end_marker, tool_name in boundaries:
        start = src.index(start_marker)
        end = src.index(end_marker)
        block = src[start:end]
        for phrase in ("Edits aren't free", "consequential and necessary", "substantive impact"):
            assert _contains(block, phrase), (
                f"MCP {tool_name}() docstring must contain {phrase!r} per "
                f"the edit-quality discipline (SKILL.md 'Edits aren't free'). "
                f"Invocation-moment teaching surface -- the agent reads "
                f"docstrings right before paying the cascade cost; the rule "
                f"belongs here, not just in SKILL.md."
            )


def test_cli_edit_family_help_carries_edit_quality_discipline(capsys):
    """All three CLI edit subcommands (edit / edit-append / edit-replace)
    surface the edit-quality discipline in their --help output. Mirrors
    the MCP docstring pin -- the typed-command-moment teaching surface
    for human users.
    """
    for subcommand in ("edit", "edit-append", "edit-replace"):
        out = _cli_help(subcommand, capsys)
        for phrase in ("Edits aren't free", "consequential and necessary", "substantive impact"):
            assert _contains(out, phrase), (
                f"`tackit {subcommand} --help` must contain {phrase!r} per "
                f"the edit-quality discipline (SKILL.md 'Edits aren't free'). "
                f"Typed-command-moment teaching surface for humans -- "
                f"mirrors the MCP docstring rule."
            )


def test_skill_md_contains_edit_quality_discipline():
    """The canonical SKILL.md carries a forceful edit-quality discipline
    section. Every edit verb fires the cascade depth-1; the cost lands
    on neighbors and pressures the close-gate. Agents need to know
    edits aren't free at session-start context load, before they reach
    for edit / edit_append / edit_replace_substring.

    Surfaced 2026-06-03 from M181 Issue #2 discussion: the proposed
    `prose_only=True` mechanism failed under scrutiny (no honest
    "wording-only" edit category exists -- if you bothered to edit, the
    reason is the cascade signal). Real fix: edits must be substantive
    and necessary; low-quality edits train the cascade discipline into
    rubber-stamping.

    Pins: the "Edits aren't free" section heading + the "substantive
    impact" rule phrase + the "consequential and necessary" framing +
    the "trained on noise" failure-mode wording. The discipline
    matters more than the exact phrasing, but a future edit that
    softens or drops the load-bearing phrases fails here.
    """
    import pathlib
    skill = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / "tackit" / "data" / "SKILL.md"
    )
    text = skill.read_text()
    assert "## Edits aren't free" in text, (
        "SKILL.md must carry a top-level `## Edits aren't free` section. "
        "The heading is the agent-greppable anchor and the load-bearing "
        "framing -- without it, the rule reads as 'edit carefully' which "
        "is advisory; with it, the rule names the actual cost (every "
        "edit fires the cascade)."
    )
    assert "consequential and necessary" in text, (
        "SKILL.md must carry the 'consequential and necessary' rule "
        "phrase verbatim -- it is the agent-facing two-word test for "
        "whether an edit is worth its cascade cost."
    )
    assert "substantive impact" in text, (
        "SKILL.md must carry the 'substantive impact' phrase verbatim "
        "in the delta-quality rule. This is the user's specific "
        "wording from the 2026-06-03 discussion; it pairs with "
        "'consequential and necessary' to bound delta-write discipline."
    )
    assert "trained on noise" in text, (
        "SKILL.md must carry the 'trained on noise' failure-mode "
        "phrase. It names what the discipline is preventing: the "
        "FAST-filter (delta x because) becoming a rubber stamp because "
        "the cascade has been firing on low-quality edits, so genuine "
        "drift gets reconciled away with the same rubber stamp."
    )


def test_skill_md_report_template_includes_foldback_stanza():
    """The 'Always report what changed' template in SKILL.md literally
    contains the Fold-backs stanza (not just a prose mention of the
    discipline). M181 #7 fix: omitting the fold-back line was still
    possible because the template only mentioned it in prose; embedding
    the stanza literally makes omission visibly wrong rather than
    silently missing.
    """
    import pathlib
    skill = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / "tackit" / "data" / "SKILL.md"
    )
    text = skill.read_text()
    assert "━━━ Fold-backs ━━━" in text, (
        "SKILL.md report template must literally contain the "
        "`━━━ Fold-backs ━━━` stanza header. The prose 'End with the "
        "fold-back line' alone was insufficient -- agents skipped it "
        "in practice. The visual template stanza forces the agent to "
        "fill it or explicitly negate it."
    )
    assert "none — verified" in text, (
        "The Fold-backs template must include the 'none — verified' "
        "negative-case wording so an agent who had no fold-backs writes "
        "the explicit negation rather than omitting the stanza entirely."
    )


def test_skill_md_contains_release_cluster_pattern():
    """SKILL.md documents the release-cluster pattern: in multi-phase
    releases, production tasks reach 'shipped but stuck-open' state
    because the close-gate refuses on stale design/schema neighbors
    that a later phase will sweep. This is the close-gate doing its
    job; the cluster closes in a batch at the end. M181 #6 fix.
    """
    import pathlib
    skill = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / "tackit" / "data" / "SKILL.md"
    )
    text = skill.read_text()
    assert "Release-cluster pattern" in text, (
        "SKILL.md must carry a `Release-cluster pattern` subsection. "
        "Multi-phase releases hit close-gate refusals on stale neighbors "
        "until a later phase sweeps; the cluster of done-but-not-closed "
        "work is the correct intermediate state, not a bug to work around."
    )
    assert "shipped pending Phase N close" in text, (
        "SKILL.md must carry the 'shipped pending Phase N close' reporting "
        "phrase. It is the in-turn-summary tag for stuck-open production "
        "tasks so they don't get forgotten between phases."
    )


def test_skill_md_contains_findings_overflow_rule():
    """SKILL.md documents the findings-overflow-to-sibling rule in the
    Fold-backs section: when cumulative Phase N finding sections exceed
    the original task body OR you reach the 3rd substantial finding,
    file a sibling `findings` task and link to the source task. M181 #8c
    fix. T168 at 57k chars is the cautionary tale.
    """
    import pathlib
    skill = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / "tackit" / "data" / "SKILL.md"
    )
    text = skill.read_text()
    assert _contains(text, "findings outgrow the body"), (
        "SKILL.md must carry the 'findings outgrow the body' signal "
        "phrase. It is the actionable trigger -- agents detect 'cumulative "
        "Phase N finding sections > original body' and file a sibling THEN."
    )
    assert _contains(text, "3rd substantial finding"), (
        "SKILL.md must carry the '3rd substantial finding' threshold "
        "phrase. Without a concrete threshold, the rule reads as 'split "
        "when it feels too long' -- with one, agents have a count to track."
    )


def test_skill_md_contains_reactive_side_door_trigger():
    """SKILL.md carries the reactive side-door trigger (T227): net-new work
    entering outside a plan or a state-change on an existing task -- a bug
    found in use, an unplanned change, a follow-up spotted mid-task, an
    ad-hoc decision -- gets filed the moment it's recognized. Without this
    section the only trigger for such work was the narrow ship-on-pain
    friction case, so it landed in git untracked. The fork test is what
    disambiguates it from a fold-back."""
    import pathlib
    skill = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / "tackit" / "data" / "SKILL.md"
    )
    text = skill.read_text()
    assert _contains(text, "File side-door work the moment it appears"), (
        "SKILL.md must carry the 'File side-door work the moment it "
        "appears' heading -- the reactive trigger for net-new work that "
        "has no plan and no parent task to fire off (T227)."
    )
    assert _contains(text, "an OPEN task already covers this"), (
        "SKILL.md must carry the fork test ('an OPEN task already covers "
        "this? yes -> fold-back; no -> new task') -- it is what "
        "disambiguates filing a NEW task from folding into an existing one."
    )
    # T229 re-anchoring: the trigger must be an OBSERVABLE event (a write to a
    # tracked file the current task doesn't name), not the internal act of
    # "recognizing side-door work" -- that recognition is the step that fails
    # silently, so pinning the observable phrasing guards against a revert.
    assert _contains(text, "Edit/Write a version-controlled file the current task doesn't name"), (
        "SKILL.md must anchor the side-door trigger to an OBSERVABLE event -- "
        "a write to a version-controlled file the current task doesn't name -- "
        "not to recognizing 'side-door work' (the recognition is what silently "
        "fails). T229 re-anchoring; reverting it un-fixes the core failure."
    )
    assert _contains(text, "STATE the disposition out loud"), (
        "SKILL.md must require the disposition be STATED out loud (new task / "
        "fold-back / not-tracked) so a SILENT skip -- the actual failure mode "
        "-- is forbidden, not merely discouraged (T229)."
    )
    # Propagation surface (D41): the rule must also live in the add() docstring,
    # carrying both the one-line trigger AND the re-anchored observable framing.
    add_src = _mcp_tool_source("add")
    assert _contains(add_src, "Side-door work"), (
        "The add() MCP docstring must carry the one-line side-door trigger "
        "(D41 propagation) -- agents who jump straight to the tool never "
        "read SKILL.md, so dropping it there silently un-propagates T227."
    )
    assert _contains(add_src, "Edit/Write a tracked file the current task doesn't name"), (
        "The add() docstring must propagate the T229 observable-event trigger "
        "(write to a tracked file the current task doesn't name), not the old "
        "recognition-gated wording -- else the propagation surface drifts back."
    )


def test_skill_md_dev_copies_match_canonical():
    """The canonical SKILL.md (src/tackit/data/SKILL.md, ships in the
    package) and the dev copies (.claude/skills/tackit/SKILL.md, .agents/
    skills/tackit/SKILL.md, populated by `tackit setup`) must agree, so
    this session's loaded skill is the same content that ships to users.
    A drift between them is how the canonical rule reaches users while
    THIS session's agent keeps running on stale guidance."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    canonical = (root / "src" / "tackit" / "data" / "SKILL.md").read_text()
    for dev_copy in [
        root / ".claude" / "skills" / "tackit" / "SKILL.md",
        root / ".agents" / "skills" / "tackit" / "SKILL.md",
    ]:
        if not dev_copy.exists():
            # Dev copies are optional (populated by `tackit setup`); only
            # assert agreement when present.
            continue
        assert dev_copy.read_text() == canonical, (
            f"{dev_copy} drifted from the canonical "
            f"src/tackit/data/SKILL.md. Re-run `tackit setup` (or just "
            f"`cp`) to re-sync. The canonical version is what ships; the "
            f"dev copy is what loads in-session."
        )


def test_readme_contains_fail_loud_philosophy():
    """README "For agents" block carries the fail-loud philosophy as a
    top-level mindset bullet, not buried inside a tackit-specific rule.

    Pins: the rule title + the distinctive "degraded output" phrase + the
    explicit framing that connects fail-loud to tackit's purpose (preventing
    silent broken state). The philosophy is general (cross-project) but is
    a load-bearing prerequisite for tackit's worklist + close-gate to work
    as more than refusable suggestions. Lives on the README install-time
    surface so an agent installing tackit through the agent adopts it
    alongside the tool, per [[feedback-ship-on-pain]]'s propagation
    principle.
    """
    import pathlib
    readme = pathlib.Path(__file__).resolve().parent.parent / "README.md"
    text = readme.read_text()
    assert "Fail loud" in text, (
        "README must carry the fail-loud rule title verbatim in the "
        "'For agents: start here' block. Agents grep for this exact "
        "phrase when looking up the discipline; renaming it breaks the "
        "anchor that connects this surface to the global CLAUDE.md "
        "rule and to [[feedback-fail-loud]] (if/when memorialized)."
    )
    assert "degraded output" in text, (
        "README must carry the 'degraded output' distinctive phrase in "
        "the fail-loud bullet. It is the specific failure mode the rule "
        "names -- silent partial success masquerading as success -- and "
        "is what makes the rule actionable rather than abstract."
    )


def test_readme_contains_fix_broken_things_first_philosophy():
    """README "For agents" block carries the fix-broken-things-first
    philosophy as a top-level mindset bullet, paired with fail-loud as
    the entry-point variant.

    Pins: the rule title + the distinctive session-start framing + the
    explicit "what's next? = fix that" recognition pattern. The
    philosophy is the entry-point variant of fail-loud (same anti-pattern
    family: bias toward forward motion that routes around broken
    things; different moment: session-start vs mid-work). The
    recognition pattern is the load-bearing piece -- without it, the
    rule reads as advisory; with it, the rule names the specific
    misclassification (treating an inherited deferred fix as "old
    context / backlog candidate") that the rule exists to prevent.
    """
    import pathlib
    readme = pathlib.Path(__file__).resolve().parent.parent / "README.md"
    text = readme.read_text()
    assert "Fix broken things first" in text, (
        "README must carry the fix-broken-things-first rule title "
        "verbatim. This is the entry-point variant of fail-loud and "
        "needs the same agent-greppable anchor."
    )
    assert "inherited broken state" in text, (
        "README must carry the 'inherited broken state' phrase. It "
        "names the substrate the rule operates on -- state from a "
        "prior session/turn that feels like archaeology but is "
        "actually the next actionable item -- and is what makes the "
        "rule actionable at session start."
    )
    assert "enumerate the backlog" in text, (
        "README must carry the 'enumerate the backlog' anti-pattern "
        "phrase. It names the specific scan-and-list failure mode the "
        "rule prevents -- treating a named deferred fix as one option "
        "among many rather than as THE next task."
    )


def test_readme_contains_granular_description_walkthrough():
    """README has the agent-facing walkthrough for D37 granular-description
    discipline. Pins the section heading + the rule's distinctive phrasing
    + the anti-pattern enumeration so a future README edit can't drop them
    silently.

    The README is the install-time + Github-front-page surface — it's where
    a new user (and a new agent on first encounter) learns what the
    discipline is. The SKILL.md test pins the in-session teaching surface;
    this test pins the install + browsing surface."""
    import pathlib
    readme = pathlib.Path(__file__).resolve().parent.parent / "README.md"
    text = readme.read_text()
    # The section exists in the v0.5 highlights walkthrough.
    assert "Granular-description discipline" in text, (
        "README must carry the D37 granular-description discipline section "
        "in the v0.5 highlights walkthrough. The section is the install + "
        "browsing-surface anchor for the discipline."
    )
    # The rule's distinctive phrase is present.
    assert "implementation-ready" in text, (
        "README must carry the rule's distinctive phrase 'implementation-"
        "ready' alongside the SKILL.md surface (per the propagation "
        "principle: the rule lives on every agent-facing surface)."
    )
    # The fresh-session framing.
    assert "fresh-session" in text, (
        "README must frame D37 around fresh-session revisit (the negative-"
        "space test for whether a description is granular enough), "
        "matching the SKILL.md framing."
    )
    # Anti-patterns are called out.
    assert "Anti-patterns" in text or "anti-patterns" in text, (
        "README must enumerate the D37 anti-patterns so the install-time "
        "reader sees what's NOT allowed, not just the abstract rule."
    )
