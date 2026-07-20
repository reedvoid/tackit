"""D19 — built-in stale surfacing helpers, the dependency-aware close-gate (D14
extended, closed-issue #21), and the forced-open-on-stale transition (D7/D10).

Pass 1 (invariant matrix) + Pass 3 (obligations sub-lens: a stale set must be both
persisted and surfaced, and closing atop it refused).

D256 creation-gate note: every `production` add below is wired at creation to
a shared `design` anchor (`deps={1: ...}`), so the anchor lands as id 1 and
every production task shifts to id 2, 3, .... Because the anchor becomes a
cascade neighbor of any production task linked to it, editing one of those
tasks now ALSO stales the anchor; tests that assert an EXACT stale set/count
`core.reconcile()` the anchor back to clean afterward so the original,
narrower invariant under test stays isolated.
"""

import pytest

from tackit.core import stale_alert_payload, stale_alert_text
from tackit.errors import InvariantError


def test_d19_alert_empty_when_nothing_stale():
    assert stale_alert_text([]) == ""
    assert stale_alert_payload([]) is None


def test_d19_alert_names_tasks_and_required_action(core):
    core.add("spec anchor", kind="design")  # D1 -- D256 anchor
    core.add("base", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("dep", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="test fixture")
    core.edit(2, description="x", delta="test")  # stales T3 (direct dep) and D1 (anchor, also a direct neighbor)
    core.reconcile(1)  # clear the anchor -- isolate T3 as the sole stale worklist item under test
    stale = core.stale_worklist()
    text = stale_alert_text(stale)
    assert "T3" in text
    assert "linked neighbors" in text  # instructs inspecting neighbors
    assert "STALE" in text.upper()
    payload = stale_alert_payload(stale)
    assert payload["count"] == 1
    assert payload["stale_task_ids"] == [3]
    assert "T3" in payload["message"]


def test_d19_alert_lists_all_stale_in_id_order(core):
    core.add("spec anchor", kind="design")  # D1 -- D256 anchor
    core.add("base", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("d2", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="test fixture")
    core.add("d3", kind="production", deps={1: "realizes the anchor decision"})  # T4
    core.link_add(4, 2, because="test fixture")
    core.edit(2, description="x", delta="test")  # stales T3, T4, and D1 (anchor, also a direct neighbor)
    core.reconcile(1)  # clear the anchor -- isolate T3/T4 as the id-ordered stale set under test
    payload = stale_alert_payload(core.stale_worklist())
    assert payload["stale_task_ids"] == [3, 4]


def test_d56_close_gate_ignores_two_hop_stale(core):
    """D56: the close-gate reaches 1-hop only (symmetric with the cascade).
    A stale task TWO hops away -- not a direct neighbor -- does NOT block
    close. (Prior behavior walked the whole transitive component, so one
    stale hub could freeze an entire connected set of otherwise-done work.)"""
    core.add("spec anchor", kind="design")  # D1
    core.add("x", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("y", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.add("z", kind="production", deps={1: "realizes the anchor decision"})  # T4
    core.link_add(2, 3, because="x-y direct")  # T2 <-> T3
    core.link_add(3, 4, because="y-z direct")  # T3 <-> T4
    # Force ONLY z (T4) stale, leaving its neighbors (y=T3, anchor D1) clean,
    # so z is a genuine 2-hop-from-x stale node with no direct edge to x.
    core.conn.execute("UPDATE tasks SET stale = 1 WHERE id = 4")
    # x's (T2) direct neighbors are y (T3) and the anchor D1 -- both clean;
    # z (T4) is two hops away (via y). The 1-hop gate lets x close.
    assert core.close(2).task.status == "closed"


def test_d56_close_gate_blocks_one_hop_stale(core):
    """D56 converse: a DIRECT (1-hop) stale neighbor DOES still block close."""
    core.add("spec anchor", kind="design")  # D1
    core.add("x", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("y", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(2, 3, because="x-y direct")  # T2 <-> T3
    # y (T3) is a direct neighbor of x (T2); force it stale (anchor stays clean).
    core.conn.execute("UPDATE tasks SET stale = 1 WHERE id = 3")
    with pytest.raises(InvariantError):
        core.close(2)  # direct stale neighbor y -> refused


def test_d14_close_allowed_when_upstream_clean(core):
    core.add("spec anchor", kind="design")  # D1 -- D256 anchor
    core.add("base", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("dep", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="test fixture")  # T3 -> T2, nothing stale
    assert core.close(3).task.status == "closed"


def test_d7_relaxed_staling_closed_dependent_stays_closed(core):
    """T123 (2026-06-01): cascade-staling a closed neighbor no longer
    force-opens it. The closed task carries stale=True with status='closed',
    and no spurious closed->open transition is logged. Action menu on
    closed-stale: reconcile / supersede / link_rm-link_add (edit still
    refused per T118)."""
    core.add("spec anchor", kind="design")  # D1 -- D256 anchor
    core.add("base", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("dep", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="test fixture")  # T3 linked to T2
    core.close(3)  # T3 closed
    core.edit(2, description="x", delta="test")  # stales T3 (and D1) without force-reopen
    t3 = core.get(3)
    assert t3.stale is True and t3.status == "closed"  # T123: stays closed
    # Status-transitions log does NOT carry a spurious closed->open event.
    seq = [(h.from_status, h.to_status) for h in core.history(3).status_transitions]
    assert seq == [(None, "open"), ("open", "closed")]
