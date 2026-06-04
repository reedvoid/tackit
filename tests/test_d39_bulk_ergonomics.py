"""D39 — bulk-sweep ergonomics (core layer): batch reconcile_many.

Pins core.reconcile_many's contract:
  * validate-all-first / fail-loud — a terminal-status or unknown id refuses
    the WHOLE batch and names every offender; nothing is cleared (atomic);
  * one D18 version bump for the whole batch, not N;
  * per-row D20 no-op guard — non-stale ids change nothing, and an all-clean
    batch skips the transaction entirely (no empty bump).

The explicit-list guard-rail (D39: no 'reconcile all stale' form) is
structural — its contract is the ABSENCE of any such method, so there is
nothing to call here; reconcile_many takes an explicit list and that is the
only batch surface.
"""

import pytest

from tackit import sync
from tackit.errors import InvariantError


def _v(core) -> int:
    return sync.get_version(core.conn)


def _hub_with_stale_leaves(core, n: int) -> list[int]:
    """Create a hub (T1) + n leaves linked to it, then edit the hub so every
    leaf goes stale. Returns the leaf ids."""
    core.add("hub", kind="production")  # T1
    leaf_ids: list[int] = []
    for i in range(n):
        t = core.add(f"leaf{i}", kind="production")
        core.link_add(
            t.id, 1, because=f"leaf{i} realizes the hub contract", delta="wire"
        )
        leaf_ids.append(t.id)
    core.edit(1, description="hub contract shifted", delta="hub shift")
    for tid in leaf_ids:
        assert core.get(tid).stale is True
    return leaf_ids


def test_reconcile_many_one_version_bump_for_batch(core):
    leaves = _hub_with_stale_leaves(core, 3)  # 3 stale leaves
    v = _v(core)
    result = core.reconcile_many(leaves)
    assert _v(core) == v + 1  # ONE bump for the batch, not 3
    assert len(result) == 3
    for tid in leaves:
        assert core.get(tid).stale is False


def test_reconcile_many_terminal_id_refuses_whole_batch(core):
    core.add("hub", kind="production")   # 1
    core.add("leaf", kind="production")  # 2
    core.add("done", kind="production")  # 3
    core.link_add(2, 1, because="leaf realizes hub", delta="wire")
    core.edit(1, description="shift", delta="hub shift")  # stales 2 only
    core.close(3)  # 3 is open, not stale, no stale neighbors -> closes clean
    v = _v(core)
    with pytest.raises(InvariantError) as ei:
        core.reconcile_many([2, 3])
    msg = str(ei.value)
    assert "REFUSED" in msg
    assert "T3" in msg and "closed" in msg        # names the offender + its status
    assert "No row was changed" in msg
    assert _v(core) == v                          # atomic: nothing mutated
    assert core.get(2).stale is True              # the valid id stayed stale


def test_reconcile_many_unknown_id_refuses_and_names_it(core):
    core.add("a", kind="production")  # 1
    with pytest.raises(InvariantError) as ei:
        core.reconcile_many([1, 999])
    msg = str(ei.value)
    assert "999" in msg and "no such task" in msg
    assert "No row was changed" in msg


def test_reconcile_many_all_clean_is_noop(core):
    core.add("a", kind="production")  # 1
    core.add("b", kind="production")  # 2
    v = _v(core)
    result = core.reconcile_many([1, 2])  # neither is stale
    assert _v(core) == v  # D20: an all-clean batch must not bump the version
    assert len(result) == 2
    for tid in (1, 2):
        assert core.get(tid).stale is False


def test_reconcile_many_mixed_stale_and_clean_bumps_once(core):
    leaves = _hub_with_stale_leaves(core, 2)  # 2 stale leaves (hub is T1)
    core.add("clean", kind="production")      # a non-stale id in the same batch
    clean_id = 4
    v = _v(core)
    core.reconcile_many(leaves + [clean_id])
    assert _v(core) == v + 1  # one bump for the whole mixed batch
    for tid in leaves + [clean_id]:
        assert core.get(tid).stale is False
