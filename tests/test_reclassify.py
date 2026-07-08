"""T128 / 2026-06-01 - reclassify(task_id, new_kind, delta) op.

Once a task's kind is set at creation, there is no public op to change it --
T120 surfaced the gap when meta-anchor inheritance traps led to several impl
tasks being misclassified meta. This file pins reclassify's semantics:
required delta (cascade-firing), meta-island guard against creating
cross-kind links by changing kind, refusal on invalid kind, no-op detection,
and the standard ChangeResult-shaped envelope.
"""

import pytest

from tackit.errors import InvariantError, ValidationError


def test_reclassify_changes_kind(core):
    """Happy path: a production task can become meta when there are no links
    forcing meta-island violation."""
    core.add("misclassified", kind="meta")  # T1
    result = core.reclassify(1, "production", delta="actually impl work, not meta")
    assert result.task.kind == "production"
    assert core.get(1).kind == "production"


def test_reclassify_to_each_valid_kind(core):
    """All four kind values are valid targets."""
    core.add("task", kind="meta")
    for k in ("design", "schema", "production", "meta"):
        core.reclassify(1, k, delta=f"shifted to {k}")
        assert core.get(1).kind == k


def test_reclassify_invalid_kind_refused(core):
    core.add("task", kind="meta")
    with pytest.raises(ValidationError, match="kind"):
        core.reclassify(1, "bogus", delta="shouldn't work")
    with pytest.raises(ValidationError, match="kind"):
        core.reclassify(1, "Production", delta="case-sensitive")  # uppercase rejected


def test_reclassify_missing_task_refused(core):
    from tackit.errors import NotFoundError

    with pytest.raises(NotFoundError):
        core.reclassify(999, "production", delta="no such task")


def test_reclassify_no_op_when_kind_unchanged(core):
    """Reclassifying to the same kind is a no-op: no version bump, no
    cascade. Matches the D20 no-op discipline (edit on no-change, label_add
    on existing, etc.)."""
    from tackit import sync

    anchor = core.add("spec anchor", kind="design")  # D1
    task = core.add(
        "task", kind="production",
        deps={anchor.id: "task realizes the anchor decision"},
    )  # T2
    v0 = sync.get_version(core.conn)
    result = core.reclassify(task.id, "production", delta="same kind on purpose")
    # No version bump (D20: no-op skips finalize_mutation).
    assert sync.get_version(core.conn) == v0
    # newly_stale empty since nothing actually changed.
    assert result.newly_stale == []


def test_reclassify_fires_cascade(core):
    """A real kind change is a semantic shift on the task -- its linked
    neighbors get stale=True so the agent re-reviews whether the
    relationship still makes sense under the new classification."""
    anchor = core.add("spec anchor", kind="design")  # D1
    hub = core.add(
        "hub", kind="production",
        deps={anchor.id: "hub realizes the anchor decision"},
    )  # T2
    neighbor_a = core.add(
        "neighbor_a", kind="production",
        deps={anchor.id: "neighbor_a realizes the anchor decision"},
    )  # T3
    neighbor_b = core.add(
        "neighbor_b", kind="production",
        deps={anchor.id: "neighbor_b realizes the anchor decision"},
    )  # T4
    core.link_add(neighbor_a.id, hub.id, because="neighbor_a consumes hub")
    core.link_add(neighbor_b.id, hub.id, because="neighbor_b consumes hub")
    result = core.reclassify(hub.id, "design", delta="hub is actually a spec, not impl")
    stale_ids = sorted(n.id for n in result.newly_stale)
    # hub's OWN dep edge to the anchor (created at hub's creation) makes the
    # anchor a cascade neighbor too, so it goes stale alongside neighbor_a/b.
    assert stale_ids == sorted([neighbor_a.id, neighbor_b.id, anchor.id])
    assert core.get(neighbor_a.id).stale is True
    assert core.get(neighbor_b.id).stale is True
    assert core.get(anchor.id).stale is True


def test_reclassify_meta_island_violation_refused(core):
    """Refuse the reclassify if the new kind would create a cross-kind link
    with any current neighbor. The error names the offending neighbors so
    the agent can decide: link_rm those first / create a new task with the
    desired kind / pick a different kind."""
    anchor = core.add("spec anchor", kind="design")  # D1
    task = core.add(
        "task", kind="production",
        deps={anchor.id: "task realizes the anchor decision"},
    )  # T2
    core.add("meta_thing_a", kind="meta")  # T3
    core.add("meta_thing_b", kind="meta")  # T4
    prod_thing = core.add(
        "prod_thing", kind="production",
        deps={anchor.id: "prod_thing realizes the anchor decision"},
    )  # T5

    # Need to wire prod-meta links somehow. The current meta-island guard
    # in _add_link refuses NEW cross-kind links, so we have to set up the
    # state via direct UPDATE to simulate legacy data (or via the link
    # at-creation path that bypasses the guard? No -- _add_link enforces).
    # Instead: link task to prod_thing (both production -- legal) and
    # confirm the reclassify-to-meta is refused because prod_thing
    # (production) is currently linked to task.
    core.link_add(task.id, prod_thing.id, because="prod-prod link")

    with pytest.raises(InvariantError, match="meta-island"):
        core.reclassify(task.id, "meta", delta="trying to move to meta island")
    # State unchanged on refusal.
    assert core.get(task.id).kind == "production"


def test_reclassify_to_same_island_kind_ok_even_with_links(core):
    """Moving within an island (e.g., production -> design) is fine even
    with links, because design/schema/production all share the non-meta
    island. Only crossing the meta boundary triggers the guard."""
    # neighbor (schema) created FIRST so it can double as task's D256
    # creation-gate link -- the schema-prod link the test cares about is
    # thereby established at task's creation, no separate link_add needed.
    neighbor = core.add("neighbor", kind="schema")  # S1
    task = core.add(
        "task", kind="production",
        deps={neighbor.id: "schema-prod link"},
    )  # T2

    # production -> design: still non-meta side, no boundary crossing.
    core.reclassify(task.id, "design", delta="reclassified to design within non-meta island")
    assert core.get(task.id).kind == "design"


def test_reclassify_empty_delta_refused(core):
    """T117 discipline: reclassify carries a required non-empty delta."""
    anchor = core.add("spec anchor", kind="design")  # D1
    task = core.add(
        "task", kind="production",
        deps={anchor.id: "task realizes the anchor decision"},
    )  # T2
    with pytest.raises(ValidationError, match="delta"):
        core.reclassify(task.id, "meta", delta="")


def test_reclassify_keeps_closed_neighbor_closed_per_t123(core):
    """T123 / T128 compose: a closed linked neighbor that gets cascade-staled
    by reclassify stays closed + stale (no force-reopen)."""
    anchor = core.add("spec anchor", kind="design")  # D1
    hub = core.add(
        "hub", kind="production",
        deps={anchor.id: "hub realizes the anchor decision"},
    )  # T2
    neighbor = core.add(
        "neighbor", kind="production",
        deps={anchor.id: "neighbor realizes the anchor decision"},
    )  # T3
    core.link_add(neighbor.id, hub.id, because="setup")
    core.close(neighbor.id)
    core.reclassify(hub.id, "design", delta="moving to design")
    t2 = core.get(neighbor.id)
    assert t2.stale is True
    assert t2.status == "closed"
