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
    # closed list of what's NOT allowed).
    assert "Anti-patterns this discipline forbids" in text, (
        "SKILL.md must enumerate the D37 anti-patterns explicitly. The "
        "enumeration is what makes the rule actionable -- agents reference "
        "the list when reviewing their own task bodies."
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
