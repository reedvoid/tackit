"""D20 — no-op discipline. A mutating op that changes nothing must NOT bump the
``version`` counter (and so must not re-dump tackit.sql). Pins closed-issue #18.

Pass 2 of the test-audit (degenerate-input enumeration): for every mutating op,
call it with input equal to the current state and assert it is a true no-op, then
assert a real change still bumps the version.

D256 creation-gate: every ``production`` task below links to a shared design
anchor (id 1) at creation via ``deps``, which shifts every downstream id by +1.
"""

from tackit import sync


def _v(core) -> int:
    return sync.get_version(core.conn)


def test_d20_edit_no_field_is_noop(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("base", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("dep", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="test fixture")  # T3 depends_on T2
    v = _v(core)
    result = core.edit(2, delta="test")  # no name, no description
    assert _v(core) == v  # no version bump
    assert result.newly_stale == []  # did not stale dependents
    assert core.get(3).stale is False


def test_d20_edit_same_value_is_noop(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("base", kind="production", description="body", deps={1: "realizes the anchor decision"})  # T2
    v = _v(core)
    core.edit(2, name="base", description="body", delta="test")  # identical to stored
    assert _v(core) == v


def test_d20_edit_real_change_bumps(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("base", kind="production", deps={1: "realizes the anchor decision"})  # T2
    v = _v(core)
    core.edit(2, description="changed", delta="test")
    assert _v(core) == v + 1


def test_d20_close_already_closed_is_noop(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.close(2)
    v = _v(core)
    result = core.close(2)  # already closed
    assert _v(core) == v
    assert result.task.status == "closed"  # still returns the obligation payload


def test_d20_reopen_already_open_is_noop(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    v = _v(core)
    core.reopen(2)  # already open
    assert _v(core) == v


def test_d20_reconcile_not_stale_is_noop(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    v = _v(core)
    core.reconcile(2)  # not stale
    assert _v(core) == v


def test_d20_label_readd_is_noop(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", labels=["x"], deps={1: "realizes the anchor decision"})  # T2
    v = _v(core)
    core.label_add(2, "x")  # already present
    assert _v(core) == v


def test_d20_label_rm_absent_is_noop(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    v = _v(core)
    core.label_rm(2, "nope")  # not present
    assert _v(core) == v


def test_d20_label_real_add_bumps(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    v = _v(core)
    core.label_add(2, "x")
    assert _v(core) == v + 1


def test_d20_dep_readd_is_noop(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="test fixture")
    v = _v(core)
    core.link_add(3, 2, because="test fixture")  # duplicate edge
    assert _v(core) == v


def test_d20_dep_rm_absent_is_noop(core):
    core.add("spec anchor", kind="design")  # T1 (D1)
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # T3
    v = _v(core)
    core.link_rm(3, 2)  # no such edge
    assert _v(core) == v
