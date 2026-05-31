"""D21 - the label-usage view (labels_summary).

Pins that labels are self-documenting through their tasks: count + sample names,
most-used first. Underpins reuse-before-create (label-discipline).
"""


def test_labels_summary_counts_samples_and_order(core):
    core.add("alpha", labels=["x", "y"])  # T1
    core.add("beta", labels=["x"])  # T2
    core.add("gamma", labels=["y", "x"])  # T3
    infos = core.labels_summary()
    # x is on 3 tasks, y on 2 -> x first (most-used)
    assert infos[0].label == "x" and infos[0].count == 3
    assert infos[0].samples == ["alpha", "beta", "gamma"]  # id order
    y = next(i for i in infos if i.label == "y")
    assert y.count == 2 and y.samples == ["alpha", "gamma"]


def test_labels_summary_empty_when_no_labels(core):
    core.add("untagged")
    assert core.labels_summary() == []


def test_labels_summary_sample_limit(core):
    for n in range(5):
        core.add(f"task {n}", labels=["big"])
    info = core.labels_summary(samples=2)[0]
    assert info.count == 5 and len(info.samples) == 2


# --- D23: label-discipline creation nudge ----------------------------------


def test_nudge_set_on_brand_new_label(core):
    core.add("a", labels=["existing"])  # T1
    core.add("b")  # T2 — no new label
    assert core.last_label_nudge is None  # reset per op
    core.label_add(2, "brandnew")
    assert core.last_label_nudge is not None
    assert "brandnew" in core.last_label_nudge
    assert "existing" in core.last_label_nudge  # lists the pre-existing label to reuse


def test_no_nudge_when_reusing_existing_label(core):
    core.add("a", labels=["x"])  # T1
    core.add("b")  # T2
    core.label_add(2, "x")  # reuse existing -> no nudge
    assert core.last_label_nudge is None
