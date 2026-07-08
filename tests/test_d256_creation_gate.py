"""D256 creation-gate: a production task must link at least one design/schema
slice AT CREATION (it realizes an already-made decision). Refused otherwise,
in both create paths (add + load), before any mutation. No escape flag.

Driven by the test-audit four-pass: the refusal path, the compliant paths
(design AND schema anchors), the degenerate "links only another production"
case, the unaffected kinds (design/schema/meta), and the load atomicity
(a gate violation rolls back the WHOLE plan).
"""

import pytest

from tackit.errors import ValidationError


# =========================================================================
#  add() — the gate
# =========================================================================


def test_add_bare_production_refused(core):
    with pytest.raises(ValidationError, match="D256 creation-gate"):
        core.add("build a thing", kind="production")


def test_add_production_linked_to_design_succeeds(core):
    core.add("a decision", kind="design")  # id 1
    t = core.add("build it", kind="production", deps={1: "realizes the decision"})
    assert t.kind == "production"
    assert t.id == 2


def test_add_production_linked_to_schema_succeeds(core):
    core.add("a table", kind="schema", description="CREATE TABLE t (...)")  # id 1
    t = core.add("migrate it", kind="production", deps={1: "realizes the schema"})
    assert t.id == 2


def test_add_production_linked_only_to_production_refused(core):
    core.add("d", kind="design")  # id 1
    core.add("upstream build", kind="production", deps={1: "realizes d"})  # id 2
    # linking only another production (no design/schema) does NOT satisfy the gate
    with pytest.raises(ValidationError, match="D256 creation-gate"):
        core.add("downstream build", kind="production", deps={2: "depends on upstream"})


def test_add_production_refused_leaves_no_row(core):
    core.add("d", kind="design")  # id 1
    with pytest.raises(ValidationError):
        core.add("build", kind="production")
    # only the design anchor exists; the refused production task was never created
    assert core.ls(kind="production") == []


# --- unaffected kinds -------------------------------------------------------


@pytest.mark.parametrize("kind", ["design", "schema", "meta"])
def test_add_non_production_needs_no_spec_link(core, kind):
    t = core.add("free-standing", kind=kind)
    assert t.kind == kind


# =========================================================================
#  load() — the gate, atomically
# =========================================================================


def _spec(key, name, kind, depends_on=None):
    return {
        "key": key,
        "name": name,
        "kind": kind,
        "desc": "body",
        "labels": [],
        "depends_on": depends_on or [],
    }


def test_load_production_without_spec_link_refused_and_rolls_back(core):
    plan = [
        _spec("a", "anchor", "design"),
        _spec("t", "build it", "production"),  # no depends_on -> gate violation
    ]
    with pytest.raises(ValidationError, match="D256 creation-gate"):
        core.load(plan)
    # atomic: the whole plan rolled back, including the valid design spec
    assert core.ls(kind="design") == []
    assert core.ls(kind="production") == []


def test_load_production_linked_to_batch_design_key_succeeds(core):
    plan = [
        _spec("a", "anchor", "design"),
        _spec("t", "build it", "production",
              depends_on=[{"key": "a", "because": "realizes the anchor"}]),
    ]
    keymap = core.load(plan)
    assert core.get(keymap["t"]).kind == "production"


def test_load_production_linked_to_existing_slice_ref_succeeds(core):
    core.add("a decision", kind="design")  # D1
    plan = [
        _spec("t", "build it", "production",
              depends_on=[{"key": "D1", "because": "realizes D1"}]),
    ]
    keymap = core.load(plan)
    assert core.get(keymap["t"]).kind == "production"


def test_load_production_linked_only_to_production_refused(core):
    plan = [
        _spec("d", "anchor", "design"),
        _spec("p", "upstream", "production",
              depends_on=[{"key": "d", "because": "realizes d"}]),
        _spec("q", "downstream", "production",
              depends_on=[{"key": "p", "because": "depends on upstream"}]),
    ]
    with pytest.raises(ValidationError, match="D256 creation-gate"):
        core.load(plan)
