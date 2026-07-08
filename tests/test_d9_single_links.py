"""T237 / D9 - the slice surfaces ONE symmetric `links` list, not duplicated
`dependencies` + `dependents`.

Under D5 symmetric links, dependencies_of and dependents_of are the same set
(_linked_with), so the old Slice carried every neighbour twice -- a high-degree
node doubled its payload and the agent read each edge twice. The slice now
carries a single `links` list. Scope is show/board (the Slice); the close/
wont_do payloads are a separate task.

Pins: each neighbour appears exactly once; the old dual keys are gone; symmetry
(a links b <=> b links a); degenerate 0/1; and the D34 because/last_edit_delta +
FAST-filter reminder still surface on the single list.
"""

from tackit.core import Core


# --- Pass 1: each neighbour once; the dual keys are gone ---------------------

def test_slice_has_single_links_list_no_dual_keys(core):
    core.add("spec anchor", kind="design")        # D1
    core.add("focal", kind="production", deps={1: "realizes the anchor decision"})  # 2
    core.add("n-a", kind="production", deps={1: "realizes the anchor decision"})    # 3
    core.add("n-b", kind="production", deps={1: "realizes the anchor decision"})    # 4
    core.link_add(2, 3, because="focal couples to n-a's contract")
    core.link_add(2, 4, because="focal couples to n-b's contract")
    s = core.show(2)
    assert [n.id for n in s.links] == [1, 3, 4]   # anchor + each neighbour exactly once, id-ordered
    dumped = s.model_dump()
    assert "links" in dumped
    assert "dependencies" not in dumped and "dependents" not in dumped


# --- Pass 1: symmetry -- a links b  <=>  b links a, each once ----------------

def test_links_are_symmetric_and_unduplicated(core):
    core.add("spec anchor", kind="design")        # D1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # 2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # 3
    core.link_add(2, 3, because="a couples to b")
    assert [n.id for n in core.show(2).links] == [1, 3]
    assert [n.id for n in core.show(3).links] == [1, 2]


# --- Pass 2: degenerate -- 0 and 1 links ------------------------------------

def test_zero_links_is_empty_list_key_present(core):
    # design creation is unaffected by the D256 gate, so a genuinely
    # zero-link task is still directly constructible.
    core.add("lonely", kind="design")
    s = core.show(1)
    assert s.links == []
    assert "links" in s.model_dump()


def test_one_link(core):
    # a's single link is the anchor it was linked to at creation (D256
    # gate) -- a clean, direct way to get a task with exactly one neighbour.
    core.add("spec anchor", kind="design")  # D1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # 2
    assert len(core.show(2).links) == 1


# --- Pass 1 non-regression: D34 because + delta + reminder on the one list ---

def test_d34_because_and_reminder_survive_on_links(core):
    core.add("spec anchor", kind="design")        # D1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # 2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # 3
    core.link_add(2, 3, because="b extends a's contract")
    # edit b -> its neighbours (a AND the anchor) go stale, and b gains a
    # last_edit_delta.
    core.edit(3, description="changed", delta="reworked b's output shape")
    # viewing a: among its links (anchor + b), b's entry carries because + delta.
    a_link = next(n for n in core.show(2).links if n.id == 3)
    assert a_link.because == "b extends a's contract"
    assert a_link.last_edit_delta == "reworked b's output shape"
    # viewing b: its link entry a (and the anchor) is stale -> the FAST-filter
    # reminder fires, now keyed off the single `links` list (not the old dual
    # lists).
    b_view = core.show(3)
    assert any(n.stale for n in b_view.links)
    assert b_view.because_reminder is not None
