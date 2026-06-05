"""D38 — links-are-coupling / labels-are-membership: surface-presence tests.

Pins the load-bearing phrases that the agent-facing surfaces must carry for
D38 (the rollup / hub / membership-link anti-patterns), and pins the REMOVAL
of the old "epic pattern" snippet that endorsed the very hub D38 forbids.

Substring checks (whitespace-normalized) so a future wording edit is
deliberate: if a load-bearing phrase moves or the retired snippet creeps
back, a test fails and forces the agent to update the surface and this pin
together. Pins favor distinctive PHRASES over the bare `D38` id so they
survive an id-convention change.
"""

import inspect
import pathlib
import re

import pytest


def _normspace(text: str) -> str:
    """Collapse whitespace runs (incl. docstring + argparse line-wrapping) to
    single spaces, so substring matches survive prose formatting."""
    return re.sub(r"\s+", " ", text)


def _contains(haystack: str, needle: str) -> bool:
    return _normspace(needle) in _normspace(haystack)


def _mcp_tool_block(tool_name: str) -> str:
    """Return the source text of one MCP tool function, bounded by the next
    `    def ` so a phrase is asserted within THIS tool's scope, not the file
    at large."""
    from tackit import mcp_server
    src = inspect.getsource(mcp_server)
    start = src.index(f"def {tool_name}(")
    end = src.index("\n    def ", start + 1)
    return src[start:end]


def _canonical_skill() -> str:
    return (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / "tackit" / "data" / "SKILL.md"
    ).read_text()


# ============================================================================
# SKILL.md — the D38 discipline section is present and load-bearing
# ============================================================================


def test_skill_md_contains_coupling_vs_membership_section():
    """SKILL.md carries a dedicated top-level forceful section naming the
    coupling-vs-membership distinction and the anti-pattern family. This is
    the session-start teaching surface; without it the discipline is only
    reachable via tool docstrings an agent may skim."""
    text = _canonical_skill()
    assert "## Links are coupling, labels are membership" in text, (
        "SKILL.md must carry a top-level `## Links are coupling, labels are "
        "membership` section -- the primary D38 teaching surface."
    )
    # The core distinction's distinctive framing.
    assert _contains(text, "claim about *consequence*"), (
        "SKILL.md D38 section must frame a link as a claim about *consequence* "
        "(the cascade test) vs a label as a claim about category."
    )
    # The three anti-patterns are named explicitly (the closed list agents
    # reference when reviewing their own tasks).
    for phrase in ("**Hub task**", "**Membership link**", "**Rollup task**"):
        assert _contains(text, phrase), (
            f"SKILL.md D38 section must name the anti-pattern {phrase!r} "
            f"explicitly -- the names are what make the rule catchable mid-act."
        )
    assert _contains(text, "fake task"), (
        "SKILL.md D38 section must carry the umbrella term 'fake task'."
    )
    # The do-not-over-correct boundary (decision-bearing linked-to slices are
    # NOT hubs) -- load-bearing so the rule doesn't break the dep model.
    assert _contains(text, "do NOT over-correct"), (
        "SKILL.md D38 section must carry the legitimate-vs-fake boundary "
        "('do NOT over-correct') so the rule doesn't forbid decision-bearing "
        "slices that impl tasks legitimately link to."
    )


def test_skill_md_epic_anchor_snippet_is_removed():
    """The retired 'epic pattern' snippet (make the question a task; wire the
    cluster to link to that anchor) is GONE from SKILL.md. This snippet was
    the root-cause endorsement of the hub anti-pattern; its presence would
    directly contradict the D38 section."""
    text = _normspace(_canonical_skill())
    for retired in (
        "make the question itself a task",
        "wire the spawned tasks to link to that anchor",
        "The theme is grouped (label) and anchored",
    ):
        assert _normspace(retired) not in text, (
            f"SKILL.md still contains the retired epic-anchor phrase "
            f"{retired!r}. It endorses the hub D38 forbids and must stay "
            f"removed (see the Labels section's grouping guidance)."
        )


def test_skill_md_contains_relationships_are_edges_rule():
    """SKILL.md carries the 'Relationships are edges, not prose' companion to
    D38: a relationship belongs on a link + because, never narrated inside a
    body (where the cascade can't see it). D38 forbids the content-free *node*;
    this forbids the buried *edge* -- a different axis on the same hub/rollup
    failure the v0.5 dogfood resurfaced through a synonym ('umbrella')."""
    text = _canonical_skill()
    assert "## Relationships are edges, not prose" in text, (
        "SKILL.md must carry a top-level `## Relationships are edges, not "
        "prose` section -- the edge-location companion to the D38 node rule."
    )
    assert _contains(text, "can't cascade or reconcile what it can't see"), (
        "SKILL.md edges-not-prose section must carry the core consequence "
        "phrase -- a relationship buried in a body is invisible to the cascade."
    )
    assert _contains(text, "forbids the content-free *node*") and _contains(
        text, "forbids the buried *edge*"
    ), (
        "SKILL.md edges-not-prose section must carry the node-vs-edge contrast "
        "with D38 so the two companion rules stay distinguishable."
    )


# ============================================================================
# MCP docstrings — D38 guidance at invocation moment
# ============================================================================


def test_mcp_add_docstring_contains_fake_task_guidance():
    """add()'s docstring carries the too-large / hub / rollup direction (D38).
    Previously it only caught the too-SMALL direction; the hub/rollup case is
    exactly the moment an agent is about to create a fake task."""
    block = _mcp_tool_block("add")
    assert _contains(block, "status rollup of OTHER tasks"), (
        "MCP add() docstring must warn against a body that is a 'status "
        "rollup of OTHER tasks' (D38) -- the create-time fake-task prompt."
    )
    assert _contains(block, "Links are\n        for coupling") or _contains(
        block, "Links are for coupling"
    ), (
        "MCP add() docstring must state 'Links are for coupling; labels are "
        "for membership' (D38)."
    )
    # Companion to D38 (edges-not-prose): a relationship belongs on a link,
    # never narrated in the body the agent is about to write.
    assert _contains(block, "not narrated in this body"), (
        "MCP add() docstring must carry the edges-not-prose direction -- a "
        "relationship belongs on a link, 'not narrated in this body' (the "
        "cascade can't see prose)."
    )


def test_mcp_link_add_docstring_contains_coupling_not_membership():
    """link_add()'s docstring states that `because` must name the coupling
    consequence, not a membership category (D38) -- taught at the moment the
    rationale is written."""
    block = _mcp_tool_block("link_add")
    assert _contains(block, "must name the COUPLING"), (
        "MCP link_add() docstring must state the `because` 'must name the "
        "COUPLING' consequence (D38)."
    )
    assert _contains(block, "MEMBERSHIP, not coupling"), (
        "MCP link_add() docstring must name the membership-vs-coupling "
        "discriminator ('MEMBERSHIP, not coupling') and point at using a "
        "label instead (D38)."
    )
    # Companion to D38 (edges-not-prose): wire the relationship here as an
    # edge rather than burying it in a task body the cascade can't traverse.
    assert _contains(block, "belongs HERE as an edge"), (
        "MCP link_add() docstring must carry the edges-not-prose direction -- "
        "a relationship 'belongs HERE as an edge', not buried in a description."
    )
