"""T91 / D27 -- the link-discovery primitive that replaces search-before-create.

Two modes:
  - no input -> the anchor layer (design + schema tasks).
  - with ids -> depth-1 linked neighbors, minus inputs, minus already_seen.
"""


def _set_kind(core, task_id, kind):
    """Set kind via raw UPDATE until T94 wires it through `add` properly."""
    core.conn.execute("UPDATE tasks SET kind = ? WHERE id = ?", (kind, task_id))


def test_links_no_input_returns_anchor_layer(core):
    core.add("d-slice")  # T1
    core.add("s-slice")  # T2
    core.add("prod task")  # T3
    core.add("meta task")  # T4
    _set_kind(core, 1, "design")
    _set_kind(core, 2, "schema")
    _set_kind(core, 4, "meta")
    anchors = core.links()
    assert [n.id for n in anchors] == [1, 2]  # design + schema only


def test_links_no_input_respects_already_seen(core):
    core.add("d1")  # T1
    core.add("d2")  # T2
    _set_kind(core, 1, "design")
    _set_kind(core, 2, "design")
    anchors = core.links(already_seen=[1])
    assert [n.id for n in anchors] == [2]


def test_links_depth_one_returns_linked_minus_inputs(core):
    # T1 <-> T2, T1 <-> T3
    core.add("a")  # T1
    core.add("b")  # T2
    core.add("c")  # T3
    core.link_add(1, 2, because="test fixture")
    core.link_add(1, 3, because="test fixture")
    result = core.links(ids=[1])
    assert [n.id for n in result] == [2, 3]  # not T1 itself


def test_links_depth_one_multiple_inputs_unions(core):
    # Star: T1<->T2, T2<->T3, T3<->T4
    for n in "abcd":
        core.add(n)
    core.link_add(1, 2, because="test fixture")
    core.link_add(2, 3, because="test fixture")
    core.link_add(3, 4, because="test fixture")
    # depth-1 from {T1, T3}: linked to T1 = {T2}; linked to T3 = {T2, T4}.
    # Union = {T2, T4} (T2 appears via both, dedupe via DISTINCT/UNION).
    result = core.links(ids=[1, 3])
    assert [n.id for n in result] == [2, 4]


def test_links_excludes_already_seen(core):
    core.add("a")  # T1
    core.add("b")  # T2
    core.add("c")  # T3
    core.link_add(1, 2, because="test fixture")
    core.link_add(1, 3, because="test fixture")
    # Caller has already judged T2; expansion from T1 must skip it.
    result = core.links(ids=[1], already_seen=[2])
    assert [n.id for n in result] == [3]


def test_links_no_neighbors_returns_empty(core):
    core.add("solitary")  # T1 -- no links
    assert core.links(ids=[1]) == []


def test_links_iterative_walk_pattern(core):
    """The caller-driven iteration pattern: expand layer by layer, accumulating
    `already_seen` so each next call returns only the new frontier."""
    # Chain: T1 <-> T2 <-> T3 <-> T4
    for n in "abcd":
        core.add(n)
    core.link_add(1, 2, because="test fixture")
    core.link_add(2, 3, because="test fixture")
    core.link_add(3, 4, because="test fixture")
    seen = [1]
    layer1 = core.links(ids=[1], already_seen=seen)
    assert [n.id for n in layer1] == [2]
    seen += [n.id for n in layer1]  # [1, 2]
    layer2 = core.links(ids=[2], already_seen=seen)
    assert [n.id for n in layer2] == [3]
    seen += [n.id for n in layer2]  # [1, 2, 3]
    layer3 = core.links(ids=[3], already_seen=seen)
    assert [n.id for n in layer3] == [4]
