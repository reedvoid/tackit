"""D21 - the label-usage view (labels_summary).

Pins that labels are self-documenting through their tasks: count + sample names,
most-used first. Underpins reuse-before-create (label-discipline).

Also pins T84 / D26 reserved-label refusal (the four kind values cannot be used
as freeform labels because S1.kind absorbs that distinction).

D256 creation-gate note: every `production` add below is wired at creation to
a shared `design` anchor (`deps={1: ...}`), so the anchor lands as id 1 and
every production task shifts to id 2, 3, ... -- ids in this file are adjusted
accordingly.
"""

import pytest

from tackit.errors import ValidationError
from tackit.schema import RESERVED_LABELS


def test_labels_summary_counts_samples_and_order(core):
    core.add("spec anchor", kind="design")  # D1 -- D256 anchor
    core.add("alpha", kind="production", labels=["x", "y"], deps={1: "realizes the anchor decision"})  # T2
    core.add("beta", kind="production", labels=["x"], deps={1: "realizes the anchor decision"})  # T3
    core.add("gamma", kind="production", labels=["y", "x"], deps={1: "realizes the anchor decision"})  # T4
    infos = core.labels_summary()
    # x is on 3 tasks, y on 2 -> x first (most-used)
    assert infos[0].label == "x" and infos[0].count == 3
    assert infos[0].samples == ["alpha", "beta", "gamma"]  # id order
    y = next(i for i in infos if i.label == "y")
    assert y.count == 2 and y.samples == ["alpha", "gamma"]


def test_labels_summary_empty_when_no_labels(core):
    core.add("spec anchor", kind="design")  # D1 -- D256 anchor
    core.add("untagged", kind="production", deps={1: "realizes the anchor decision"})
    assert core.labels_summary() == []


def test_labels_summary_sample_limit(core):
    core.add("spec anchor", kind="design")  # D1 -- D256 anchor
    for n in range(5):
        core.add(f"task {n}", kind="production", labels=["big"], deps={1: "realizes the anchor decision"})
    info = core.labels_summary(samples=2)[0]
    assert info.count == 5 and len(info.samples) == 2


# --- D23: label-discipline creation nudge ----------------------------------


def test_nudge_set_on_brand_new_label(core):
    core.add("spec anchor", kind="design")  # D1 -- D256 anchor
    core.add("a", kind="production", labels=["existing"], deps={1: "realizes the anchor decision"})  # T2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # T3 — no new label
    assert core.last_label_nudge is None  # reset per op
    core.label_add(3, "brandnew")
    assert core.last_label_nudge is not None
    assert "brandnew" in core.last_label_nudge
    # D65: "existing" is not similar to "brandnew", so no label is listed
    assert core.last_label_nudge.endswith("No existing label looks similar; `labels` lists them all.")


def test_no_nudge_when_reusing_existing_label(core):
    core.add("spec anchor", kind="design")  # D1 -- D256 anchor
    core.add("a", kind="production", labels=["x"], deps={1: "realizes the anchor decision"})  # T2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # T3
    core.label_add(3, "x")  # reuse existing -> no nudge
    assert core.last_label_nudge is None


# --- T84 / D26: reserved-label refusal -------------------------------------


@pytest.mark.parametrize("reserved", RESERVED_LABELS)
def test_label_add_refuses_reserved(core, reserved):
    """label_add refuses each of the four kind values; the error names the kind
    property as the absorber so the agent knows where to put it instead."""
    core.add("spec anchor", kind="design")  # D1 -- D256 anchor
    core.add("alpha", kind="production", deps={1: "realizes the anchor decision"})  # T2
    with pytest.raises(ValidationError, match="reserved"):
        core.label_add(2, reserved)


@pytest.mark.parametrize("reserved", RESERVED_LABELS)
def test_add_with_reserved_label_refused(core, reserved):
    """add() that passes a reserved label is refused; the partial insert rolls
    back so no task survives the attempt.

    D256 note: the production add is wired to a pre-existing design anchor so
    the creation-gate passes and the reserved-label check (the thing this test
    actually pins) is what fires. The anchor is created BEFORE the failing
    call and is not itself part of the rolled-back attempt, so it legitimately
    survives -- the rollback assertion below is narrowed to "no task beyond
    the anchor survived" rather than "zero tasks total"."""
    core.add("spec anchor", kind="design")  # D1 -- survives; not part of the failed add
    with pytest.raises(ValidationError, match="reserved"):
        core.add(
            "alpha",
            kind="production",
            labels=[reserved],
            deps={1: "realizes the anchor decision"},
        )
    rows = core.conn.execute("SELECT * FROM tasks").fetchall()
    assert len(rows) == 1  # only the anchor; the reserved-label add fully rolled back


@pytest.mark.parametrize("reserved", RESERVED_LABELS)
def test_load_with_reserved_label_refused_and_rollback(core, reserved):
    """load() with any reserved label rolls back the WHOLE plan (D24 fail-loud).

    D256 note: the plan now includes a batch-local design anchor, and both
    production specs link to it (satisfying the creation-gate) in addition to
    the original a<-b coupling this test pins. All three specs are created in
    ONE transaction, so when 'beta's reserved label triggers rollback, the
    anchor and 'alpha' are rolled back too -- the original "zero rows survive"
    assertion still holds exactly as before."""
    specs = [
        {"key": "anchor", "name": "spec anchor", "kind": "design", "desc": "", "labels": [], "depends_on": []},
        {
            "key": "a",
            "name": "alpha",
            "kind": "production",
            "desc": "",
            "labels": [],
            "depends_on": [{"key": "anchor", "because": "a realizes the anchor decision"}],
        },
        {
            "key": "b",
            "name": "beta",
            "kind": "production",
            "desc": "",
            "labels": [reserved],
            "depends_on": [
                {"key": "anchor", "because": "b realizes the anchor decision"},
                {"key": "a", "because": "test fixture: b couples to a's contract"},
            ],
        },
    ]
    with pytest.raises(ValidationError, match="reserved"):
        core.load(specs)
    rows = core.conn.execute("SELECT * FROM tasks").fetchall()
    assert rows == []


# --- T366 (D65): the nudge lists up to 5 SIMILAR existing labels ------------

from tackit.core import label_nudge_text  # noqa: E402

NONE_SIMILAR = "No existing label looks similar; `labels` lists them all."


def test_nudge_lists_a_similar_label_with_the_exact_wording():
    assert label_nudge_text(["release-0.9.5"], ["release", "docs"]) == (
        "🏷 New label created: release-0.9.5. Similar existing labels: release. "
        "If one fits, use it instead; `labels` lists them all."
    )


def test_nudge_with_no_similar_label_has_its_own_wording():
    assert label_nudge_text(["brandnew"], ["existing", "docs"]) == (
        "🏷 New label created: brandnew. " + NONE_SIMILAR
    )


def test_first_label_wording_is_unchanged():
    assert label_nudge_text(["x"], []) == "🏷 New label created: x (the first label in this store)."


def test_no_nudge_when_nothing_was_created():
    assert label_nudge_text([], ["a"]) is None


@pytest.mark.parametrize("new,existing", [
    ("Search", "search"),            # case
    ("AB", "ab"),                    # case only: raw ratio 0
    ("q-3-x", "q3x"),                # separators only: raw ratio 0.75
    ("lean_returns", "lean-returns"),  # separators
    ("testing", "test"),             # containment, shorter has 4 characters
    ("labeldiscipline", "label-discipline"),  # normalized equality
    ("contextcots", "context-cost"),  # close spelling only (ratio about 0.91, no containment)
])
def test_similar_pairs_are_listed(new, existing):
    assert f"Similar existing labels: {existing}." in label_nudge_text([new], [existing])


@pytest.mark.parametrize("new,existing", [
    ("cli", "client"),     # containment needs the shorter at 4+ characters; ratio 0.67
    ("search", "schema"),  # unrelated
])
def test_dissimilar_pairs_are_not_listed(new, existing):
    assert label_nudge_text([new], [existing]).endswith(NONE_SIMILAR)


def test_at_most_five_ordered_by_score_then_name():
    existing = [f"found-in:m100{i}" for i in range(8, 0, -1)] + ["unrelated"]
    msg = label_nudge_text(["found-in:m1009"], existing)
    listed = msg.split("Similar existing labels: ", 1)[1].split(". If one fits", 1)[0]
    assert listed == ", ".join(f"found-in:m100{i}" for i in range(1, 6))


def test_several_new_labels_pool_their_matches():
    msg = label_nudge_text(["release-x", "docs2"], ["release", "docs", "infra"])
    assert msg == (
        "🏷 New label created: release-x, docs2. Similar existing labels: docs, release. "
        "If one fits, use it instead; `labels` lists them all."
    )


def test_a_higher_score_comes_before_a_lower_one_whatever_the_names():
    # "reports" contains "report" (score 1.0); "aeport" is close spelling only
    # (ratio about 0.83). Name order would put "aeport" first.
    msg = label_nudge_text(["report"], ["aeport", "reports"])
    assert "Similar existing labels: reports, aeport." in msg
