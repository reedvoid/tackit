"""T92 / D25 -- the supersede op: mark a task replaced by a newer one, without
auto-closing it. Supersede and close are independent decisions (T101)."""

import pytest

from tackit.errors import InvariantError, NotFoundError


def test_supersede_sets_marker(core):
    core.add("old")  # T1
    core.add("new")  # T2
    result = core.supersede(1, 2, delta="test")
    assert result.old.task.superseded_by == 2
    assert result.by.task.id == 2
    # The marker is visible on subsequent show() too.
    assert core.show(1).task.superseded_by == 2


def test_supersede_self_refused(core):
    core.add("alone")
    with pytest.raises(InvariantError, match="itself"):
        core.supersede(1, 1, delta="test")


def test_supersede_missing_old_refused(core):
    core.add("by")
    with pytest.raises(NotFoundError):
        core.supersede(999, 1, delta="test")


def test_supersede_missing_by_refused(core):
    core.add("old")
    with pytest.raises(NotFoundError):
        core.supersede(1, 999, delta="test")


def test_supersede_does_not_auto_close_old(core):
    core.add("old")
    core.add("new")
    core.supersede(1, 2, delta="test")
    assert core.get(1).status == "open"  # supersede is not close


def test_supersede_does_not_auto_close_by(core):
    core.add("old")
    core.add("new")
    core.supersede(1, 2, delta="test")
    assert core.get(2).status == "open"


def test_supersede_returns_both_slices(core):
    core.add("old")
    core.add("new")
    result = core.supersede(1, 2, delta="test")
    # Slice has task + labels + dependencies + dependents -- check both shapes.
    assert result.old.task.id == 1
    assert result.by.task.id == 2
    assert result.old.labels == []
    assert result.by.labels == []


def test_supersede_can_be_overwritten(core):
    core.add("old")
    core.add("first replacer")
    core.add("second replacer")
    core.supersede(1, 2, delta="test")
    assert core.get(1).superseded_by == 2
    # A later supersede simply overwrites the marker (no append).
    core.supersede(1, 3, delta="test")
    assert core.get(1).superseded_by == 3
