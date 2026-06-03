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
