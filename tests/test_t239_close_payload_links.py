"""T239 / D12 - the close / wont_do / retire obligation payloads carry ONE
symmetric `links` list, not duplicated `dependencies` + `dependents`.

Sibling of T237 (which fixed the show/board Slice). CloseResult and WontDoResult
populated both fields from `_linked_with` (identical under D5 symmetric links),
so each one-hop review neighbour was emitted twice. They now carry a single
`links` list, same as the slice.

Pins: each verb's payload emits each neighbour exactly once; the dual keys are
gone; the review-obligation set is unchanged in content; degenerate 0/1.
"""

import pytest

from tackit.core import Core


def _two_linked(core):
    core.add("spec anchor", kind="design")  # 1
    core.add("a", kind="production", deps={1: "realizes the anchor"})  # 2
    core.add("b", kind="production", deps={1: "realizes the anchor"})  # 3
    core.link_add(2, 3, because="a couples to b")


# --- close ------------------------------------------------------------------

def test_close_result_single_links(core):
    _two_linked(core)
    r = core.close(2)
    # 'a' (2) is linked to both the anchor (1, from its D256 creation-gate
    # dep) and 'b' (3, explicit link_add) -- each neighbour appears once.
    assert [n.id for n in r.links] == [1, 3]
    dumped = r.model_dump()
    assert "links" in dumped
    assert "dependencies" not in dumped and "dependents" not in dumped


# --- wont_do ----------------------------------------------------------------

def test_wont_do_result_single_links(core):
    _two_linked(core)
    r = core.wont_do(2, reason="dropped", delta="scope dropped")
    assert [n.id for n in r.links] == [1, 3]
    assert "dependents" not in r.model_dump()


# --- retire (returns the WontDoResult shape) --------------------------------

def test_retire_result_single_links(core):
    d = core.add("a design slice", kind="design")     # D1, status spec
    core.add("b design slice", kind="design")         # D2
    # link, then drop the neighbour so retire's open-neighbour gate is clear
    core.link_add(1, 2, because="d1 relates to d2")
    core.link_rm(1, 2)
    r = core.retire(d.id, reason="abandoned", delta="decision dead")
    assert [n.id for n in r.links] == []              # no neighbours, one list
    assert "dependencies" not in r.model_dump()


# --- degenerate + symmetry --------------------------------------------------

def test_close_zero_links(core):
    # kind="meta" is unaffected by the D256 creation-gate (which only
    # constrains "production"), so this stays a true zero-link degenerate
    # case rather than being forced to carry a spec-anchor link.
    core.add("lonely", kind="meta")
    r = core.close(1)
    assert r.links == []
    assert "links" in r.model_dump()


def test_close_links_match_the_review_obligation_set(core):
    # the one-hop review set must be unchanged in CONTENT, just de-duplicated
    core.add("spec anchor", kind="design")             # 1
    core.add("focal", kind="production", deps={1: "realizes the anchor"})  # 2
    core.add("x", kind="production", deps={1: "realizes the anchor"})      # 3
    core.add("y", kind="production", deps={1: "realizes the anchor"})      # 4
    core.link_add(2, 3, because="focal couples to x")
    core.link_add(2, 4, because="focal couples to y")
    r = core.close(2)
    # focal's neighbours: the anchor (1, from its creation-gate dep) plus
    # x (3) and y (4) from the explicit links -- unchanged in content,
    # just de-duplicated.
    assert sorted(n.id for n in r.links) == [1, 3, 4]
