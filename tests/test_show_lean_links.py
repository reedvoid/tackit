"""T296 (D211 + D34): slice returns carry lean neighbor entries by default.

`because` and `last_edit_delta` stay on an entry only for a reconcile read: the
entry or the focal task is obligation-bearing stale (stale AND status open/spec).
An edit stales the edited task's NEIGHBORS, not the edited task, so reconciling
stale task A needs the fields on A's entry for non-stale B. That is the dangerous
direction these tests pin hardest: a reconcile read must never lose its fields.
"""

import pytest

from tackit.core import LINK_BECAUSE_REMINDER
from tackit.models import NeighborRef, obligation_stale, project_show_slice

LEAN_KEYS = {"id", "status", "stale", "kind", "prefixed_name"}


def _project(core, task_id, include_neighbor_because=False):
    return project_show_slice(
        core.show(task_id), include_neighbor_because=include_neighbor_because
    )


def _entry(card, neighbor_id):
    for entry in card["links"]:
        if entry["id"] == neighbor_id:
            return entry
    raise AssertionError(f"no link entry for {neighbor_id} in {card['links']}")


def _record_only_pair(core, terminal):
    """Build a reachable pair: spec focal 1 and terminal neighbor 2, where editing
    1 leaves 2 stale but record-only (D7). Returns nothing; ids are 1 and 2."""
    core.add("focal spec", kind="design")  # 1
    if terminal == "retired":
        core.add("neighbor spec", kind="design")  # 2
        core.link_add(1, 2, because="pair coupling")
        core.retire(2, reason="decision fully abandoned for this test scenario")
    else:
        core.add("neighbor", kind="production", deps={1: "pair coupling"})  # 2
        if terminal == "closed":
            core.close(2)
        else:
            core.wont_do(2, reason="scope dropped for this test scenario")
    core.edit(1, description="changed", delta="changed the focal spec")


# --- the predicate ----------------------------------------------------------


@pytest.mark.parametrize(
    "status,stale,kind,expected",
    [
        ("open", True, "production", True),
        ("spec", True, "design", True),
        ("closed", True, "production", False),
        ("wont_do", True, "production", False),
        ("retired", True, "design", False),
        ("open", False, "production", False),
        ("spec", False, "design", False),
        ("closed", False, "production", False),
    ],
)
def test_obligation_stale_truth_table(status, stale, kind, expected):
    ref = NeighborRef(id=1, name="n", status=status, stale=stale, kind=kind)
    assert obligation_stale(ref) is expected


# --- lean by default --------------------------------------------------------


def test_non_stale_entry_has_exactly_the_lean_keys(core):
    core.add("spec", kind="design")  # 1
    core.add("task", kind="production", deps={1: "task realizes spec"})  # 2
    entry = _project(core, 2)["links"][0]
    assert set(entry) == LEAN_KEYS
    assert entry["id"] == 1
    assert entry["prefixed_name"] == "D1 — spec"


def test_focal_task_and_labels_stay_complete(core):
    core.add("spec", kind="design")  # 1
    core.add(
        "task", kind="production", description="the body", labels=["x"],
        deps={1: "task realizes spec"},
    )  # 2
    card = _project(core, 2)
    assert card["task"]["name"] == "task"
    assert card["task"]["description"] == "the body"
    assert "created_at" in card["task"] and "updated_at" in card["task"]
    assert card["labels"] == ["x"]


def test_zero_links_keeps_an_empty_list(core):
    core.add("lonely", kind="design")
    assert _project(core, 1)["links"] == []


def test_projection_does_not_mutate_the_slice(core):
    core.add("spec", kind="design")  # 1
    core.add("task", kind="production", deps={1: "task realizes spec"})  # 2
    slice_ = core.show(2)
    before = slice_.model_dump(mode="json")
    project_show_slice(slice_, include_neighbor_because=False)
    assert slice_.model_dump(mode="json") == before


# --- reconcile reads keep the fields ----------------------------------------


def test_reconcile_read_of_stale_task_keeps_fields_on_the_edited_neighbor(core):
    """The dangerous direction. Edit b, then read stale a: entry b is NOT stale,
    yet it holds the because + delta needed to reconcile a."""
    core.add("b", kind="design")  # 1
    core.add("c", kind="design")  # 2
    core.add(
        "a", kind="production",
        deps={1: "a realizes b", 2: "a realizes c"},
    )  # 3
    core.edit(1, description="new", delta="changed b's output shape")
    card = _project(core, 3)
    assert card["task"]["stale"] is True
    edited = _entry(card, 1)
    assert edited["stale"] is False
    assert edited["because"] == "a realizes b"
    assert edited["last_edit_delta"] == "changed b's output shape"
    # Which neighbor staled a is not tracked, so every entry keeps them.
    other = _entry(card, 2)
    assert other["stale"] is False
    assert other["because"] == "a realizes c"
    assert other["last_edit_delta"] is None


def test_spec_focal_counts_as_obligation_bearing(core):
    core.add("a", kind="design")  # 1
    core.add("b", kind="design")  # 2
    core.link_add(1, 2, because="a extends b")
    core.edit(2, description="new", delta="changed b")
    entry = _entry(_project(core, 1), 2)
    assert entry["because"] == "a extends b"
    assert entry["last_edit_delta"] == "changed b"


def test_stale_entry_keeps_fields_and_reconciled_sibling_goes_lean(core):
    core.add("b", kind="design")  # 1
    core.add("a", kind="production", deps={1: "a realizes b"})  # 2
    core.add("c", kind="production", deps={1: "c realizes b"})  # 3
    core.edit(1, description="new", delta="changed b")
    core.reconcile(3)
    card = _project(core, 1)
    assert card["task"]["stale"] is False
    stale_entry = _entry(card, 2)
    assert stale_entry["because"] == "a realizes b"
    assert "last_edit_delta" in stale_entry and stale_entry["last_edit_delta"] is None
    assert set(_entry(card, 3)) == LEAN_KEYS


# --- record-only stale stays lean, but stays listed --------------------------


@pytest.mark.parametrize("terminal", ["closed", "wont_do", "retired"])
def test_record_only_stale_entry_is_listed_lean_with_its_flag(core, terminal):
    _record_only_pair(core, terminal)
    entry = _entry(_project(core, 1), 2)
    assert set(entry) == LEAN_KEYS
    assert entry["stale"] is True
    assert entry["status"] == terminal


@pytest.mark.parametrize("terminal", ["closed", "wont_do", "retired"])
def test_record_only_stale_focal_does_not_keep_fields(core, terminal):
    _record_only_pair(core, terminal)
    card = _project(core, 2)
    assert card["task"]["stale"] is True
    assert set(_entry(card, 1)) == LEAN_KEYS


def test_closed_focal_keeps_fields_only_on_its_open_stale_neighbor(core):
    """A stale closed focal task earns nothing itself, so only the entry that is
    obligation-bearing stale keeps the fields."""
    core.add("spec", kind="design")  # 1
    core.add("closed one", kind="production", deps={1: "closed one realizes spec"})  # 2
    core.add("open one", kind="production", deps={1: "open one realizes spec"})  # 3
    core.link_add(2, 3, because="closed one shares a contract with open one")
    core.close(2)
    core.edit(1, description="new", delta="changed spec")
    card = _project(core, 2)
    assert card["task"]["stale"] is True
    assert _entry(card, 3)["because"] == "closed one shares a contract with open one"
    assert set(_entry(card, 1)) == LEAN_KEYS


# --- the opt-in -------------------------------------------------------------


def test_opt_in_returns_both_fields_on_every_entry(core):
    _record_only_pair(core, "closed")
    core.add("plain", kind="production", deps={1: "plain realizes focal"})  # 3
    core.reconcile(3)
    card = _project(core, 1, include_neighbor_because=True)
    record_only = _entry(card, 2)
    assert record_only["because"] == "pair coupling"
    assert "last_edit_delta" in record_only
    plain = _entry(card, 3)
    assert plain["because"] == "plain realizes focal"
    assert "last_edit_delta" in plain
    for entry in card["links"]:
        assert "name" not in entry


def test_null_delta_means_never_edited_and_absent_means_not_returned(core):
    core.add("spec", kind="design")  # 1
    core.add("task", kind="production", deps={1: "task realizes spec"})  # 2
    with_fields = _project(core, 2, include_neighbor_because=True)["links"][0]
    assert "last_edit_delta" in with_fields and with_fields["last_edit_delta"] is None
    assert "last_edit_delta" not in _project(core, 2)["links"][0]


# --- Core.show stays the full read ------------------------------------------


def test_core_show_still_returns_full_fields_on_non_stale_entries(core):
    core.add("spec", kind="design")  # 1
    core.add("task", kind="production", deps={1: "task realizes spec"})  # 2
    core.edit(1, description="new", delta="changed spec")
    core.reconcile(2)
    entry = core.show(2).links[0]
    assert entry.stale is False
    assert entry.name == "spec"
    assert entry.because == "task realizes spec"
    assert entry.last_edit_delta == "changed spec"


# --- because_reminder trigger (D34) -----------------------------------------


def test_reminder_fires_when_focal_is_open_stale_with_no_stale_neighbor(core):
    """The reconcile read of a stale task, which raw any(neighbor.stale) missed."""
    core.add("b", kind="design")  # 1
    core.add("a", kind="production", deps={1: "a realizes b"})  # 2
    core.edit(1, description="new", delta="changed b")
    slice_ = core.show(2)
    assert slice_.task.stale is True
    assert [n.stale for n in slice_.links] == [False]
    assert slice_.because_reminder == LINK_BECAUSE_REMINDER


def test_reminder_fires_on_stale_spec_focal(core):
    core.add("a", kind="design")  # 1
    core.add("b", kind="design")  # 2
    core.link_add(1, 2, because="a extends b")
    core.edit(2, description="new", delta="changed b")
    assert core.show(1).because_reminder == LINK_BECAUSE_REMINDER


def test_reminder_fires_on_open_stale_neighbor(core):
    core.add("b", kind="design")  # 1
    core.add("a", kind="production", deps={1: "a realizes b"})  # 2
    core.edit(1, description="new", delta="changed b")
    assert core.show(1).because_reminder == LINK_BECAUSE_REMINDER


@pytest.mark.parametrize("terminal", ["closed", "wont_do", "retired"])
def test_reminder_silent_when_only_record_only_neighbors_are_stale(core, terminal):
    _record_only_pair(core, terminal)
    slice_ = core.show(1)
    assert [n.stale for n in slice_.links] == [True]
    assert slice_.because_reminder is None


@pytest.mark.parametrize("terminal", ["closed", "wont_do", "retired"])
def test_reminder_silent_when_focal_is_record_only_stale(core, terminal):
    _record_only_pair(core, terminal)
    slice_ = core.show(2)
    assert slice_.task.stale is True
    assert slice_.because_reminder is None


def test_reminder_null_when_nothing_is_stale(core):
    core.add("spec", kind="design")  # 1
    core.add("task", kind="production", deps={1: "task realizes spec"})  # 2
    assert core.show(2).because_reminder is None


def test_reminder_still_fires_for_a_stale_task_with_no_links_left(core):
    """Negative space, decided by D34: a stale open task whose link was removed
    still owes a reconcile, so the trigger holds with an empty list."""
    core.add("b", kind="design")  # 1
    core.add("c", kind="design")  # 2
    core.add("a", kind="production", deps={1: "a realizes b", 2: "a realizes c"})  # 3
    core.edit(1, description="new", delta="changed b")
    core.link_rm(3, 1)
    core.link_rm(3, 2)
    slice_ = core.show(3)
    assert slice_.task.stale is True
    assert slice_.links == []
    assert slice_.because_reminder == LINK_BECAUSE_REMINDER
