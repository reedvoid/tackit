"""D21 - the label-usage view (labels_summary).

Pins that labels are self-documenting through their tasks: count + sample names,
most-used first. Underpins reuse-before-create (label-discipline).

Also pins T84 / D26 reserved-label refusal (the four kind values cannot be used
as freeform labels because S1.kind absorbs that distinction).
"""

import pytest

from tackit.errors import ValidationError
from tackit.schema import RESERVED_LABELS


def test_labels_summary_counts_samples_and_order(core):
    core.add("alpha", kind="production", labels=["x", "y"])  # T1
    core.add("beta", kind="production", labels=["x"])  # T2
    core.add("gamma", kind="production", labels=["y", "x"])  # T3
    infos = core.labels_summary()
    # x is on 3 tasks, y on 2 -> x first (most-used)
    assert infos[0].label == "x" and infos[0].count == 3
    assert infos[0].samples == ["alpha", "beta", "gamma"]  # id order
    y = next(i for i in infos if i.label == "y")
    assert y.count == 2 and y.samples == ["alpha", "gamma"]


def test_labels_summary_empty_when_no_labels(core):
    core.add("untagged", kind="production")
    assert core.labels_summary() == []


def test_labels_summary_sample_limit(core):
    for n in range(5):
        core.add(f"task {n}", kind="production", labels=["big"])
    info = core.labels_summary(samples=2)[0]
    assert info.count == 5 and len(info.samples) == 2


# --- D23: label-discipline creation nudge ----------------------------------


def test_nudge_set_on_brand_new_label(core):
    core.add("a", kind="production", labels=["existing"])  # T1
    core.add("b", kind="production")  # T2 — no new label
    assert core.last_label_nudge is None  # reset per op
    core.label_add(2, "brandnew")
    assert core.last_label_nudge is not None
    assert "brandnew" in core.last_label_nudge
    assert "existing" in core.last_label_nudge  # lists the pre-existing label to reuse


def test_no_nudge_when_reusing_existing_label(core):
    core.add("a", kind="production", labels=["x"])  # T1
    core.add("b", kind="production")  # T2
    core.label_add(2, "x")  # reuse existing -> no nudge
    assert core.last_label_nudge is None


# --- T84 / D26: reserved-label refusal -------------------------------------


@pytest.mark.parametrize("reserved", RESERVED_LABELS)
def test_label_add_refuses_reserved(core, reserved):
    """label_add refuses each of the four kind values; the error names the kind
    property as the absorber so the agent knows where to put it instead."""
    core.add("alpha", kind="production")
    with pytest.raises(ValidationError, match="reserved"):
        core.label_add(1, reserved)


@pytest.mark.parametrize("reserved", RESERVED_LABELS)
def test_add_with_reserved_label_refused(core, reserved):
    """add() that passes a reserved label is refused; the partial insert rolls
    back so no task survives the attempt."""
    with pytest.raises(ValidationError, match="reserved"):
        core.add("alpha", kind="production", labels=[reserved])
    rows = core.conn.execute("SELECT * FROM tasks").fetchall()
    assert rows == []


@pytest.mark.parametrize("reserved", RESERVED_LABELS)
def test_load_with_reserved_label_refused_and_rollback(core, reserved):
    """load() with any reserved label rolls back the WHOLE plan (D24 fail-loud)."""
    specs = [
        {"key": "a", "name": "alpha", "kind": "production", "desc": "", "labels": [], "depends_on": []},
        {"key": "b", "name": "beta", "kind": "production", "desc": "", "labels": [reserved], "depends_on": ["a"]},
    ]
    with pytest.raises(ValidationError, match="reserved"):
        core.load(specs)
    rows = core.conn.execute("SELECT * FROM tasks").fetchall()
    assert rows == []
