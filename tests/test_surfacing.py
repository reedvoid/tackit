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


def test_d14_close_refused_when_upstream_transitively_stale(core):
    # chain T4 -> T3 -> T2 ; editing T2 stales its DIRECT neighbors only: T3
    # AND the shared design anchor D1 (T4 is also directly linked to D1, so
    # the close-gate's transitive walk still finds D1 stale after T3 alone
    # is reconciled -- both must be cleared before close succeeds).
    core.add("spec anchor", kind="design")  # D1
    core.add("base", kind="production", deps={1: "realizes the anchor decision"})  # T2
    core.add("mid", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.link_add(3, 2, because="test fixture")  # T3 depends_on T2
    core.add("top", kind="production", deps={1: "realizes the anchor decision"})  # T4
    core.link_add(4, 3, because="test fixture")  # T4 depends_on T3
    core.edit(2, description="x", delta="test")  # stales T3 and D1 (one hop each)
    assert core.get(4).stale is False  # T4 itself is NOT stale (non-transitive)
    with pytest.raises(InvariantError):
        core.close(4)  # sits on stale T3 (and stale D1, also linked) -> refused
    core.reconcile(3)  # clear the direct upstream
    with pytest.raises(InvariantError):
        core.close(4)  # still refused: D1 (also linked to T4 via the anchor edge) remains stale
    core.reconcile(1)  # clear the anchor too
    assert core.close(4).task.status == "closed"  # now allowed


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
