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
    core.add("focal", kind="production")          # 1
    core.add("n-a", kind="production")            # 2
    core.add("n-b", kind="production")            # 3
    core.link_add(1, 2, because="1 couples to 2's contract")
    core.link_add(1, 3, because="1 couples to 3's contract")
    s = core.show(1)
    assert [n.id for n in s.links] == [2, 3]      # each exactly once, id-ordered
    dumped = s.model_dump()
    assert "links" in dumped
    assert "dependencies" not in dumped and "dependents" not in dumped


# --- Pass 1: symmetry -- a links b  <=>  b links a, each once ----------------

def test_links_are_symmetric_and_unduplicated(core):
    core.add("a", kind="production")              # 1
    core.add("b", kind="production")              # 2
    core.link_add(1, 2, because="a couples to b")
    assert [n.id for n in core.show(1).links] == [2]
    assert [n.id for n in core.show(2).links] == [1]


# --- Pass 2: degenerate -- 0 and 1 links ------------------------------------

def test_zero_links_is_empty_list_key_present(core):
    core.add("lonely", kind="production")
    s = core.show(1)
    assert s.links == []
    assert "links" in s.model_dump()


def test_one_link(core):
    core.add("a", kind="production")
    core.add("b", kind="production")
    core.link_add(1, 2, because="a couples to b")
    assert len(core.show(1).links) == 1


# --- Pass 1 non-regression: D34 because + delta + reminder on the one list ---

def test_d34_because_and_reminder_survive_on_links(core):
    core.add("a", kind="production")              # 1
    core.add("b", kind="production")              # 2
    core.link_add(1, 2, because="b extends a's contract")
    # edit b -> a (its neighbour) goes stale, and b gains a last_edit_delta.
    core.edit(2, description="changed", delta="reworked b's output shape")
    # viewing a: its single link entry for b carries because + b's delta.
    a_link = core.show(1).links[0]
    assert a_link.because == "b extends a's contract"
    assert a_link.last_edit_delta == "reworked b's output shape"
    # viewing b: its link entry a is stale -> the FAST-filter reminder fires,
    # now keyed off the single `links` list (not the old dual lists).
    b_view = core.show(2)
    assert any(n.stale for n in b_view.links)
    assert b_view.because_reminder is not None
