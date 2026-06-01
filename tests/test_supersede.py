"""T92 / D25 -- the supersede op: mark a task replaced by a newer one, without
auto-closing it. Supersede and close are independent decisions (T101)."""

import pytest

from tackit.errors import InvariantError, NotFoundError


def test_supersede_sets_marker(core):
    core.add("old", kind="production")  # T1
    core.add("new", kind="production")  # T2
    result = core.supersede(1, 2, delta="test")
    assert result.old.task.superseded_by == 2
    assert result.by.task.id == 2
    # The marker is visible on subsequent show() too.
    assert core.show(1).task.superseded_by == 2


def test_supersede_self_refused(core):
    core.add("alone", kind="production")
    with pytest.raises(InvariantError, match="itself"):
        core.supersede(1, 1, delta="test")


def test_supersede_missing_old_refused(core):
    core.add("by", kind="production")
    with pytest.raises(NotFoundError):
        core.supersede(999, 1, delta="test")


def test_supersede_missing_by_refused(core):
    core.add("old", kind="production")
    with pytest.raises(NotFoundError):
        core.supersede(1, 999, delta="test")


def test_supersede_does_not_auto_close_old(core):
    core.add("old", kind="production")
    core.add("new", kind="production")
    core.supersede(1, 2, delta="test")
    assert core.get(1).status == "open"  # supersede is not close


def test_supersede_does_not_auto_close_by(core):
    core.add("old", kind="production")
    core.add("new", kind="production")
    core.supersede(1, 2, delta="test")
    assert core.get(2).status == "open"


def test_supersede_returns_both_slices(core):
    core.add("old", kind="production")
    core.add("new", kind="production")
    result = core.supersede(1, 2, delta="test")
    # Slice has task + labels + dependencies + dependents -- check both shapes.
    assert result.old.task.id == 1
    assert result.by.task.id == 2
    assert result.old.labels == []
    assert result.by.labels == []


def test_supersede_can_be_overwritten(core):
    core.add("old", kind="production")
    core.add("first replacer", kind="production")
    core.add("second replacer", kind="production")
    core.supersede(1, 2, delta="test")
    assert core.get(1).superseded_by == 2
    # A later supersede simply overwrites the marker (no append).
    core.supersede(1, 3, delta="test")
    assert core.get(1).superseded_by == 3


# --- T124: supersede() fires the staling cascade on old's links --------------

def test_supersede_no_links_returns_empty_newly_stale(core):
    """A supersede on a task with NO links cascades to nothing -- newly_stale
    is empty. The result envelope still has the field for shape consistency."""
    core.add("old", kind="production")
    core.add("new", kind="production")
    result = core.supersede(1, 2, delta="solitary supersede")
    assert result.newly_stale == []


def test_supersede_cascades_on_old_links(core):
    """supersede(old, by) marks every direct linked neighbor of OLD as stale.
    by's existing links are untouched -- by is the replacement, not the
    departed; only old's neighborhood owes the migrate-or-stay review."""
    core.add("old", kind="production")          # T1
    core.add("dep_of_old_a", kind="production") # T2
    core.add("dep_of_old_b", kind="production") # T3
    core.add("new", kind="production")          # T4
    core.add("dep_of_new", kind="production")   # T5
    core.link_add(2, 1, because="T2 consumes old", delta="setup")
    core.link_add(3, 1, because="T3 consumes old", delta="setup")
    core.link_add(5, 4, because="T5 consumes new", delta="setup")  # by's link

    result = core.supersede(1, 4, delta="replaced old with new design")
    stale_ids = sorted(n.id for n in result.newly_stale)
    assert stale_ids == [2, 3]  # old's neighbors only
    # by's neighbor T5 is NOT stale.
    assert core.get(5).stale is False
    # old's neighbors are stale.
    assert core.get(2).stale is True
    assert core.get(3).stale is True


def test_supersede_keeps_closed_neighbor_closed_per_t123(core):
    """T124 + T123 compose: a closed linked neighbor of old gets stale=True
    but stays closed -- no force-reopen. Action menu on the closed-stale
    neighbor: reconcile / supersede / link migration."""
    core.add("old", kind="production")  # T1
    core.add("closed_dep", kind="production")  # T2
    core.add("new", kind="production")  # T3
    core.link_add(2, 1, because="T2 consumes old", delta="setup")
    core.close(2)  # T2 is closed
    result = core.supersede(1, 3, delta="replaced old with new")
    assert [n.id for n in result.newly_stale] == [2]
    t2 = core.get(2)
    assert t2.stale is True
    assert t2.status == "closed"  # T123: no force-reopen


def test_supersede_empty_delta_refused(core):
    """T117 discipline: supersede is a cascade-firing op and requires a
    non-empty delta, like edit / link_add / link_rm."""
    from tackit.errors import ValidationError

    core.add("old", kind="production")
    core.add("new", kind="production")
    with pytest.raises(ValidationError, match="delta"):
        core.supersede(1, 2, delta="")


def test_supersede_close_gate_blocks_close_of_stale_neighbor(core):
    """After supersede stales an open neighbor, that neighbor cannot be
    closed until reconciled (or its own supersede fires) -- D14 close-gate
    is unchanged by T124, it just now sees more stale neighbors than before."""
    core.add("old", kind="production")  # T1
    core.add("dep", kind="production")  # T2
    core.add("new", kind="production")  # T3
    core.link_add(2, 1, because="T2 consumes old", delta="setup")
    core.supersede(1, 3, delta="replaced")  # stales T2
    with pytest.raises(InvariantError, match="stale"):
        core.close(2)


def test_supersede_migrate_links_to_by_then_reconcile(core):
    """Walk the full migrate-or-stay flow: supersede(old, by) stales old's
    neighbor; agent migrates the relationship via link_add(by, neighbor),
    optionally link_rm(old, neighbor), then reconciles the neighbor."""
    core.add("old", kind="production")          # T1
    core.add("consumer", kind="production")     # T2
    core.add("new", kind="production")          # T3
    core.link_add(2, 1, because="T2 consumes old's API", delta="setup")

    result = core.supersede(1, 3, delta="new replaces old's API surface")
    assert [n.id for n in result.newly_stale] == [2]
    assert core.get(2).stale is True

    # Migrate: link T2 to T3 (the replacement) with a fresh rationale.
    core.link_add(2, 3, because="T2 now consumes new's API (migrated from old)", delta="link migration after supersede")
    # Prune the old edge.
    core.link_rm(2, 1, delta="old edge superseded by new; remove")
    # Now the neighbor is still stale (link ops don't auto-clear stale).
    assert core.get(2).stale is True
    # Agent reviewed + decided "I've migrated; T2's content is correct under the new edge."
    core.reconcile(2)
    assert core.get(2).stale is False
