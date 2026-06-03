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


@pytest.mark.skip(
    reason="Phase 6 / T169 -- SKILL.md prose update is a separate phase. "
    "Flip this test to active when T169 lands the granular-description "
    "section in SKILL.md."
)
def test_skill_md_contains_granular_description_section():
    """SKILL.md carries a dedicated section on granular-description
    discipline (Phase 6 / T169 deliverable)."""
    import pathlib
    skill = pathlib.Path(__file__).resolve().parent.parent / "SKILL.md"
    text = skill.read_text()
    assert "granular-description" in text.lower() or "impl-ready" in text


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


@pytest.mark.skip(
    reason="Phase 7 / T170 -- README walkthrough is a separate phase."
)
def test_readme_contains_granular_description_walkthrough():
    """README has the agent-facing install walkthrough that includes
    granular-description guidance."""
    import pathlib
    readme = pathlib.Path(__file__).resolve().parent.parent / "README.md"
    text = readme.read_text()
    assert "granular-description" in text.lower() or "impl-ready" in text
