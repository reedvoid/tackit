"""D24 - bulk plan import: the parser (tackit.plan) and core.load (atomic create +
key-resolved depends_on)."""

import pytest

from tackit import sync
from tackit.errors import InvariantError, ValidationError
from tackit.plan import parse_plan

PLAN = """\
# a small plan
[base] Build the base thing
  labels: core

[mid] Build the middle thing
  desc: sits on the base
  labels: core, feature
  depends_on: base

[top] Build the top thing
  depends_on: mid, base
"""


def test_parse_plan_ok():
    specs = parse_plan(PLAN)
    assert [s["key"] for s in specs] == ["base", "mid", "top"]
    mid = specs[1]
    assert mid["name"] == "Build the middle thing"
    assert mid["desc"] == "sits on the base"
    assert mid["labels"] == ["core", "feature"]
    assert mid["depends_on"] == ["base"]
    assert specs[2]["depends_on"] == ["mid", "base"]


def test_parse_field_before_key():
    with pytest.raises(ValidationError):
        parse_plan("  labels: x\n[a] name\n")


def test_parse_unknown_field():
    with pytest.raises(ValidationError):
        parse_plan("[a] name\n  priority: high\n")


def test_parse_duplicate_key():
    with pytest.raises(ValidationError):
        parse_plan("[a] one\n[a] two\n")


def test_parse_empty_plan():
    with pytest.raises(ValidationError):
        parse_plan("# just a comment\n\n")


def test_load_creates_tasks_and_edges(core):
    keymap = core.load(parse_plan(PLAN))
    assert set(keymap) == {"base", "mid", "top"}
    mid_deps = [n.id for n in core.dependencies_of(keymap["mid"])]
    assert keymap["base"] in mid_deps
    top_deps = sorted(n.id for n in core.dependencies_of(keymap["top"]))
    assert top_deps == sorted([keymap["mid"], keymap["base"]])
    assert core.labels_of(keymap["mid"]) == ["core", "feature"]


def test_load_unknown_dep_key_fails_before_mutating(core):
    with pytest.raises(ValidationError):
        core.load(parse_plan("[a] one\n  depends_on: nope\n"))
    assert core.ls() == []  # nothing created (validated before the transaction)


def test_load_cycle_fails_atomically(core):
    cyc = "[a] one\n  depends_on: b\n[b] two\n  depends_on: a\n"
    with pytest.raises(InvariantError):
        core.load(parse_plan(cyc))
    assert core.ls() == []  # rolled back — no partial import


def test_load_is_a_single_version_bump(core):
    v0 = sync.get_version(core.conn)
    core.load(parse_plan(PLAN))  # 3 tasks + 3 edges
    assert sync.get_version(core.conn) == v0 + 1  # atomic: one bump for the whole plan


def test_load_reports_new_labels(core):  # T67 anti-sprawl summary
    core.add("seed", labels=["existing"])
    core.last_label_nudge = None
    core.load(parse_plan("[a] one\n  labels: existing, brandnew\n[b] two\n  labels: another\n"))
    assert core.last_label_nudge is not None
    assert "brandnew" in core.last_label_nudge and "another" in core.last_label_nudge
    assert "existing" not in core.last_label_nudge  # already existed -> not reported as new
