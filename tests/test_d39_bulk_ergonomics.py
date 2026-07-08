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
    """Create a design anchor (D1) + a hub (T2, realizing the anchor) + n
    leaves linked to the hub, then edit the hub so every leaf goes stale.
    Returns the leaf ids."""
    anchor = core.add("spec anchor", kind="design")  # D1
    hub = core.add(
        "hub", kind="production",
        deps={anchor.id: "hub realizes the anchor decision"},
    )  # T2
    leaf_ids: list[int] = []
    for i in range(n):
        t = core.add(
            f"leaf{i}", kind="production",
            deps={anchor.id: f"leaf{i} realizes the anchor decision"},
        )
        core.link_add(
            t.id, hub.id, because=f"leaf{i} realizes the hub contract"
        )
        leaf_ids.append(t.id)
    core.edit(hub.id, description="hub contract shifted", delta="hub shift")
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
    anchor = core.add("spec anchor", kind="design")  # D1
    hub = core.add(
        "hub", kind="production",
        deps={anchor.id: "hub realizes the anchor decision"},
    )  # T2
    leaf = core.add(
        "leaf", kind="production",
        deps={anchor.id: "leaf realizes the anchor decision"},
    )  # T3
    # "done" gets its OWN anchor (not the hub/leaf one) so it stays isolated
    # from the hub cluster's cascade -- sharing the hub's anchor would make
    # done transitively reachable from the stale hub/leaf/anchor graph and
    # break the "done has no stale neighbors -> closes clean" premise below.
    other_anchor = core.add("other anchor", kind="design")  # D4
    done = core.add(
        "done", kind="production",
        deps={other_anchor.id: "done realizes the other anchor decision"},
    )  # T5
    core.link_add(leaf.id, hub.id, because="leaf realizes hub")
    core.edit(hub.id, description="shift", delta="hub shift")  # stales leaf (+ hub's own anchor link)
    core.close(done.id)  # done is isolated, not stale, no stale neighbors -> closes clean
    v = _v(core)
    with pytest.raises(InvariantError) as ei:
        core.reconcile_many([leaf.id, done.id])
    msg = str(ei.value)
    assert "REFUSED" in msg
    assert f"T{done.id}" in msg and "closed" in msg  # names the offender + its status
    assert "No row was changed" in msg
    assert _v(core) == v                             # atomic: nothing mutated
    assert core.get(leaf.id).stale is True           # the valid id stayed stale


def test_reconcile_many_unknown_id_refuses_and_names_it(core):
    anchor = core.add("spec anchor", kind="design")  # D1
    a = core.add(
        "a", kind="production",
        deps={anchor.id: "a realizes the anchor decision"},
    )  # T2
    with pytest.raises(InvariantError) as ei:
        core.reconcile_many([a.id, 999])
    msg = str(ei.value)
    assert "999" in msg and "no such task" in msg
    assert "No row was changed" in msg


def test_reconcile_many_all_clean_is_noop(core):
    anchor = core.add("spec anchor", kind="design")  # D1
    a = core.add(
        "a", kind="production",
        deps={anchor.id: "a realizes the anchor decision"},
    )  # T2
    b = core.add(
        "b", kind="production",
        deps={anchor.id: "b realizes the anchor decision"},
    )  # T3
    v = _v(core)
    result = core.reconcile_many([a.id, b.id])  # neither is stale
    assert _v(core) == v  # D20: an all-clean batch must not bump the version
    assert len(result) == 2
    for tid in (a.id, b.id):
        assert core.get(tid).stale is False


def test_reconcile_many_mixed_stale_and_clean_bumps_once(core):
    leaves = _hub_with_stale_leaves(core, 2)  # 2 stale leaves (anchor=D1, hub=T2)
    anchor_id = 1
    clean = core.add(
        "clean", kind="production",
        deps={anchor_id: "clean realizes the anchor decision"},
    )  # a non-stale id in the same batch
    v = _v(core)
    core.reconcile_many(leaves + [clean.id])
    assert _v(core) == v + 1  # one bump for the whole mixed batch
    for tid in leaves + [clean.id]:
        assert core.get(tid).stale is False
