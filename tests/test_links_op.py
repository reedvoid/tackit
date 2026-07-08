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
    core.add("spec anchor", kind="design")  # D1 -- creation-gate anchor
    core.add("d-slice", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("s-slice", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.add("prod task", kind="production", deps={1: "realizes the anchor decision"})  # T4
    core.add("meta task", kind="production", deps={1: "realizes the anchor decision"})  # T5
    _set_kind(core, 2, "design")
    _set_kind(core, 3, "schema")
    _set_kind(core, 5, "meta")
    anchors = core.links()
    # design + schema only: the anchor (D1) + the reclassified design/schema.
    assert [n.id for n in anchors] == [1, 2, 3]


def test_links_no_input_respects_already_seen(core):
    core.add("spec anchor", kind="design")  # D1 -- creation-gate anchor
    core.add("d1", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("d2", kind="production", deps={1: "realizes the anchor decision"})  # T3
    _set_kind(core, 2, "design")
    _set_kind(core, 3, "design")
    # already_seen covers the anchor (D1) + d1 (T2); only d2 (T3) is new.
    anchors = core.links(already_seen=[1, 2])
    assert [n.id for n in anchors] == [3]


def test_links_depth_one_returns_linked_minus_inputs(core):
    # D1 (anchor) <-> a, a <-> b, a <-> c
    core.add("spec anchor", kind="design")  # D1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.add("c", kind="production", deps={1: "realizes the anchor decision"})  # T4
    core.link_add(2, 3, because="test fixture")
    core.link_add(2, 4, because="test fixture")
    result = core.links(ids=[2])
    assert [n.id for n in result] == [1, 3, 4]  # anchor + b, c; not a itself


def test_links_depth_one_multiple_inputs_unions(core):
    # D1 (anchor) <-> {a,b,c,d}; chain a<->b, b<->c, c<->d
    core.add("spec anchor", kind="design")  # D1
    for n in "abcd":
        core.add(n, kind="production", deps={1: "realizes the anchor decision"})  # T2..T5
    core.link_add(2, 3, because="test fixture")
    core.link_add(3, 4, because="test fixture")
    core.link_add(4, 5, because="test fixture")
    # depth-1 from {a(T2), c(T4)}: linked to a = {anchor(D1), b(T3)};
    # linked to c = {anchor(D1), b(T3), d(T5)}.
    # Union = {D1, T3, T5} (D1 and T3 appear via both, dedupe via DISTINCT/UNION).
    result = core.links(ids=[2, 4])
    assert [n.id for n in result] == [1, 3, 5]


def test_links_excludes_already_seen(core):
    core.add("spec anchor", kind="design")  # D1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.add("c", kind="production", deps={1: "realizes the anchor decision"})  # T4
    core.link_add(2, 3, because="test fixture")
    core.link_add(2, 4, because="test fixture")
    # Caller has already judged b (T3); expansion from a (T2) must skip it,
    # but the anchor (D1) is still a real linked neighbor and comes through.
    result = core.links(ids=[2], already_seen=[3])
    assert [n.id for n in result] == [1, 4]


def test_links_no_neighbors_returns_empty(core):
    # design creation is unaffected by the D256 gate, so a genuinely solitary
    # (zero-link) task is still constructible directly, without an anchor.
    core.add("solitary", kind="design")  # D1 -- no links
    assert core.links(ids=[1]) == []


def test_links_anchor_layer_excludes_retired(core):
    """v0.5 D36 / T180: the anchor layer (no input) excludes retired design/
    schema rows. They're dead spec, not viable link anchors for new work --
    same predicate the expansion hop already applies under T173 (status IN
    ('open','spec')), unified across both SELECT branches in Core.links()."""
    core.add("live_d", kind="design")  # T1 -- spec by default
    core.add("retired_d", kind="design")  # T2 -- spec, then forced retired
    core.conn.execute("UPDATE tasks SET status = 'retired' WHERE id = 2;")
    anchors = core.links()  # no input -> anchor layer
    assert [n.id for n in anchors] == [1]  # only the live spec


def test_links_expansion_excludes_retired_status(core):
    """v0.5 D36: the expansion hop filters candidates by status IN
    ('open','spec'). Retired design/schema rows (mig 009 destinations for
    legacy wont_do design) are EXCLUDED -- they're not viable link targets
    for new work."""
    core.add("retired_d", kind="design")  # D1 -- spec by default
    # deps= wires the same edge the test used to build via link_add, AND
    # satisfies the D256 creation-gate for the production task below.
    core.add("anchor", kind="production", deps={1: "design realized by prod"})  # T2
    # Seed D1 retired (retire() verb arrives Phase 2b).
    core.conn.execute("UPDATE tasks SET status = 'retired' WHERE id = 1;")
    result = core.links(ids=[2])
    assert [n.id for n in result] == []  # retired excluded


def test_links_iterative_walk_pattern(core):
    """The caller-driven iteration pattern: expand layer by layer, accumulating
    `already_seen` so each next call returns only the new frontier."""
    # D1 (anchor) <-> a, plus chain a <-> b <-> c <-> d
    core.add("spec anchor", kind="design")  # D1
    for n in "abcd":
        core.add(n, kind="production", deps={1: "realizes the anchor decision"})  # T2..T5
    core.link_add(2, 3, because="test fixture")
    core.link_add(3, 4, because="test fixture")
    core.link_add(4, 5, because="test fixture")
    seen = [2]
    layer1 = core.links(ids=[2], already_seen=seen)
    assert [n.id for n in layer1] == [1, 3]  # anchor (D1) + b
    seen += [n.id for n in layer1]  # [2, 1, 3]
    layer2 = core.links(ids=[3], already_seen=seen)
    assert [n.id for n in layer2] == [4]
    seen += [n.id for n in layer2]  # [2, 1, 3, 4]
    layer3 = core.links(ids=[4], already_seen=seen)
    assert [n.id for n in layer3] == [5]
