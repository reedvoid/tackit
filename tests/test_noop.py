"""D20 — no-op discipline. A mutating op that changes nothing must NOT bump the
``version`` counter (and so must not re-dump tackit.sql). Pins closed-issue #18.

Pass 2 of the test-audit (degenerate-input enumeration): for every mutating op,
call it with input equal to the current state and assert it is a true no-op, then
assert a real change still bumps the version.
"""

from tackit import sync


def _v(core) -> int:
    return sync.get_version(core.conn)


def test_d20_edit_no_field_is_noop(core):
    core.add("base")  # T1
    core.add("dep")  # T2
    core.link_add(2, 1)  # T2 depends_on T1
    v = _v(core)
    result = core.edit(1)  # no name, no description
    assert _v(core) == v  # no version bump
    assert result.newly_stale == []  # did not stale dependents
    assert core.get(2).stale is False


def test_d20_edit_same_value_is_noop(core):
    core.add("base", description="body")
    v = _v(core)
    core.edit(1, name="base", description="body")  # identical to stored
    assert _v(core) == v


def test_d20_edit_real_change_bumps(core):
    core.add("base")
    v = _v(core)
    core.edit(1, description="changed")
    assert _v(core) == v + 1


def test_d20_close_already_closed_is_noop(core):
    core.add("a")
    core.close(1)
    v = _v(core)
    result = core.close(1)  # already closed
    assert _v(core) == v
    assert result.task.status == "closed"  # still returns the obligation payload


def test_d20_reopen_already_open_is_noop(core):
    core.add("a")
    v = _v(core)
    core.reopen(1)  # already open
    assert _v(core) == v


def test_d20_reconcile_not_stale_is_noop(core):
    core.add("a")
    v = _v(core)
    core.reconcile(1)  # not stale
    assert _v(core) == v


def test_d20_label_readd_is_noop(core):
    core.add("a", labels=["x"])
    v = _v(core)
    core.label_add(1, "x")  # already present
    assert _v(core) == v


def test_d20_label_rm_absent_is_noop(core):
    core.add("a")
    v = _v(core)
    core.label_rm(1, "nope")  # not present
    assert _v(core) == v


def test_d20_label_real_add_bumps(core):
    core.add("a")
    v = _v(core)
    core.label_add(1, "x")
    assert _v(core) == v + 1


def test_d20_dep_readd_is_noop(core):
    core.add("a")
    core.add("b")
    core.link_add(2, 1)
    v = _v(core)
    core.link_add(2, 1)  # duplicate edge
    assert _v(core) == v


def test_d20_dep_rm_absent_is_noop(core):
    core.add("a")
    core.add("b")
    v = _v(core)
    core.link_rm(2, 1)  # no such edge
    assert _v(core) == v
