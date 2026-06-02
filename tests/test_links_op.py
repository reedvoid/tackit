"""T91 / D27 -- the link-discovery primitive that replaces search-before-create.

Two modes:
  - no input -> the anchor layer (design + schema tasks).
  - with ids -> depth-1 linked neighbors, minus inputs, minus already_seen.
"""


def _set_kind(core, task_id, kind):
    """Set kind via raw UPDATE. v0.5 (D36): kind/status partition requires
    status to be set in lockstep with kind, so the helper updates status to
    a partition-valid default (spec for design/schema, open for
    production/meta) -- mirrors reclassify()'s cross-partition auto-shift."""
    new_status = "spec" if kind in ("design", "schema") else "open"
    core.conn.execute(
        "UPDATE tasks SET kind = ?, status = ? WHERE id = ?",
        (kind, new_status, task_id),
    )


def test_links_no_input_returns_anchor_layer(core):
    core.add("d-slice", kind="production")  # T1
    core.add("s-slice", kind="production")  # T2
    core.add("prod task", kind="production")  # T3
    core.add("meta task", kind="production")  # T4
    _set_kind(core, 1, "design")
    _set_kind(core, 2, "schema")
    _set_kind(core, 4, "meta")
    anchors = core.links()
    assert [n.id for n in anchors] == [1, 2]  # design + schema only


def test_links_no_input_respects_already_seen(core):
    core.add("d1", kind="production")  # T1
    core.add("d2", kind="production")  # T2
    _set_kind(core, 1, "design")
    _set_kind(core, 2, "design")
    anchors = core.links(already_seen=[1])
    assert [n.id for n in anchors] == [2]


def test_links_depth_one_returns_linked_minus_inputs(core):
    # T1 <-> T2, T1 <-> T3
    core.add("a", kind="production")  # T1
    core.add("b", kind="production")  # T2
    core.add("c", kind="production")  # T3
    core.link_add(1, 2, because="test fixture", delta="test")
    core.link_add(1, 3, because="test fixture", delta="test")
    result = core.links(ids=[1])
    assert [n.id for n in result] == [2, 3]  # not T1 itself


def test_links_depth_one_multiple_inputs_unions(core):
    # Star: T1<->T2, T2<->T3, T3<->T4
    for n in "abcd":
        core.add(n, kind="production")
    core.link_add(1, 2, because="test fixture", delta="test")
    core.link_add(2, 3, because="test fixture", delta="test")
    core.link_add(3, 4, because="test fixture", delta="test")
    # depth-1 from {T1, T3}: linked to T1 = {T2}; linked to T3 = {T2, T4}.
    # Union = {T2, T4} (T2 appears via both, dedupe via DISTINCT/UNION).
    result = core.links(ids=[1, 3])
    assert [n.id for n in result] == [2, 4]


def test_links_excludes_already_seen(core):
    core.add("a", kind="production")  # T1
    core.add("b", kind="production")  # T2
    core.add("c", kind="production")  # T3
    core.link_add(1, 2, because="test fixture", delta="test")
    core.link_add(1, 3, because="test fixture", delta="test")
    # Caller has already judged T2; expansion from T1 must skip it.
    result = core.links(ids=[1], already_seen=[2])
    assert [n.id for n in result] == [3]


def test_links_no_neighbors_returns_empty(core):
    core.add("solitary", kind="production")  # T1 -- no links
    assert core.links(ids=[1]) == []


def test_links_expansion_excludes_retired_status(core):
    """v0.5 D36: the expansion hop filters candidates by status IN
    ('open','spec'). Retired design/schema rows (mig 009 destinations for
    legacy wont_do design) are EXCLUDED -- they're not viable link targets
    for new work."""
    core.add("anchor", kind="production")  # T1
    core.add("retired_d", kind="design")  # T2 -- spec by default
    core.link_add(2, 1, because="design realized by prod", delta="setup")
    # Seed T2 retired (retire() verb arrives Phase 2b).
    core.conn.execute("UPDATE tasks SET status = 'retired' WHERE id = 2;")
    result = core.links(ids=[1])
    assert [n.id for n in result] == []  # retired excluded


def test_links_iterative_walk_pattern(core):
    """The caller-driven iteration pattern: expand layer by layer, accumulating
    `already_seen` so each next call returns only the new frontier."""
    # Chain: T1 <-> T2 <-> T3 <-> T4
    for n in "abcd":
        core.add(n, kind="production")
    core.link_add(1, 2, because="test fixture", delta="test")
    core.link_add(2, 3, because="test fixture", delta="test")
    core.link_add(3, 4, because="test fixture", delta="test")
    seen = [1]
    layer1 = core.links(ids=[1], already_seen=seen)
    assert [n.id for n in layer1] == [2]
    seen += [n.id for n in layer1]  # [1, 2]
    layer2 = core.links(ids=[2], already_seen=seen)
    assert [n.id for n in layer2] == [3]
    seen += [n.id for n in layer2]  # [1, 2, 3]
    layer3 = core.links(ids=[3], already_seen=seen)
    assert [n.id for n in layer3] == [4]
