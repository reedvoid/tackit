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
    core.add("a", kind="production")  # 1
    core.add("b", kind="production")  # 2
    core.link_add(1, 2, because="a couples to b")


# --- close ------------------------------------------------------------------

def test_close_result_single_links(core):
    _two_linked(core)
    r = core.close(1)
    assert [n.id for n in r.links] == [2]            # each neighbour once
    dumped = r.model_dump()
    assert "links" in dumped
    assert "dependencies" not in dumped and "dependents" not in dumped


# --- wont_do ----------------------------------------------------------------

def test_wont_do_result_single_links(core):
    _two_linked(core)
    r = core.wont_do(1, reason="dropped", delta="scope dropped")
    assert [n.id for n in r.links] == [2]
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
    core.add("lonely", kind="production")
    r = core.close(1)
    assert r.links == []
    assert "links" in r.model_dump()


def test_close_links_match_the_review_obligation_set(core):
    # the one-hop review set must be unchanged in CONTENT, just de-duplicated
    core.add("focal", kind="production")              # 1
    core.add("x", kind="production")                  # 2
    core.add("y", kind="production")                  # 3
    core.link_add(1, 2, because="focal couples to x")
    core.link_add(1, 3, because="focal couples to y")
    r = core.close(1)
    assert sorted(n.id for n in r.links) == [2, 3]
