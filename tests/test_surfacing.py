"""D19 — built-in stale surfacing helpers, the dependency-aware close-gate (D14
extended, closed-issue #21), and the forced-open-on-stale transition (D7/D10).

Pass 1 (invariant matrix) + Pass 3 (obligations sub-lens: a stale set must be both
persisted and surfaced, and closing atop it refused).
"""

import pytest

from tackit.core import stale_alert_payload, stale_alert_text
from tackit.errors import InvariantError


def test_d19_alert_empty_when_nothing_stale():
    assert stale_alert_text([]) == ""
    assert stale_alert_payload([]) is None


def test_d19_alert_names_tasks_and_required_action(core):
    core.add("base", kind="production")  # T1
    core.add("dep", kind="production")  # T2
    core.link_add(2, 1, because="test fixture", delta="test")
    core.edit(1, description="x", delta="test")  # stales T2
    stale = core.stale_worklist()
    text = stale_alert_text(stale)
    assert "T2" in text
    assert "depends_on" in text  # instructs inspecting neighbors
    assert "STALE" in text.upper()
    payload = stale_alert_payload(stale)
    assert payload["count"] == 1
    assert payload["stale_task_ids"] == [2]
    assert "T2" in payload["message"]


def test_d19_alert_lists_all_stale_in_id_order(core):
    core.add("base", kind="production")  # T1
    core.add("d2", kind="production")
    core.link_add(2, 1, because="test fixture", delta="test")
    core.add("d3", kind="production")
    core.link_add(3, 1, because="test fixture", delta="test")
    core.edit(1, description="x", delta="test")  # stales T2 and T3
    payload = stale_alert_payload(core.stale_worklist())
    assert payload["stale_task_ids"] == [2, 3]


def test_d14_close_refused_when_upstream_transitively_stale(core):
    # chain T3 -> T2 -> T1 ; editing T1 stales its DIRECT dependent T2 only.
    core.add("base", kind="production")  # T1
    core.add("mid", kind="production")
    core.link_add(2, 1, because="test fixture", delta="test")  # T2 depends_on T1
    core.add("top", kind="production")
    core.link_add(3, 2, because="test fixture", delta="test")  # T3 depends_on T2
    core.edit(1, description="x", delta="test")  # stales T2 (one hop)
    assert core.get(3).stale is False  # T3 itself is NOT stale (non-transitive)
    with pytest.raises(InvariantError):
        core.close(3)  # but it sits on stale T2 -> refused
    core.reconcile(2)  # clear the upstream
    assert core.close(3).task.status == "closed"  # now allowed


def test_d14_close_allowed_when_upstream_clean(core):
    core.add("base", kind="production")  # T1
    core.add("dep", kind="production")
    core.link_add(2, 1, because="test fixture", delta="test")  # T2 -> T1, nothing stale
    assert core.close(2).task.status == "closed"


def test_d7_relaxed_staling_closed_dependent_stays_closed(core):
    """T123 (2026-06-01): cascade-staling a closed neighbor no longer
    force-opens it. The closed task carries stale=True with status='closed',
    and no spurious closed->open transition is logged. Action menu on
    closed-stale: reconcile / supersede / link_rm-link_add (edit still
    refused per T118)."""
    core.add("base", kind="production")  # T1
    core.add("dep", kind="production")
    core.link_add(2, 1, because="test fixture", delta="test")  # T2 linked to T1
    core.close(2)  # T2 closed
    core.edit(1, description="x", delta="test")  # stales T2 without force-reopen
    t2 = core.get(2)
    assert t2.stale is True and t2.status == "closed"  # T123: stays closed
    # Status-transitions log does NOT carry a spurious closed->open event.
    seq = [(h.from_status, h.to_status) for h in core.history(2).status_transitions]
    assert seq == [(None, "open"), ("open", "closed")]
