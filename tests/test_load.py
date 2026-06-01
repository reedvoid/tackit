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


def test_parse_multiline_desc_folds_continuations():
    # Deeper-indented lines after `desc:` are continuation lines, joined with \n.
    plan = (
        "[a] Task A\n"
        "  desc: First paragraph of the body.\n"
        "    Second paragraph, its own line.\n"
        "    Third paragraph.\n"
        "  labels: x\n"  # sibling field at desc indent ends the block
    )
    specs = parse_plan(plan)
    assert specs[0]["desc"] == (
        "First paragraph of the body.\n"
        "Second paragraph, its own line.\n"
        "Third paragraph."
    )
    assert specs[0]["labels"] == ["x"]  # the sibling field still parsed


def test_parse_multiline_desc_block_ends_at_next_key():
    plan = (
        "[a] Task A\n"
        "  desc: line one\n"
        "    line two\n"
        "[b] Task B\n"
        "  desc: just me\n"
    )
    specs = parse_plan(plan)
    assert specs[0]["desc"] == "line one\nline two"
    assert specs[1]["desc"] == "just me"


def test_parse_multiline_desc_block_ends_at_blank_line():
    # A blank line terminates the desc block; an equal-indent garbage line after it
    # is no longer a continuation and so still fails loud.
    plan = "[a] Task A\n  desc: line one\n    line two\n\n  loose garbage\n"
    with pytest.raises(ValidationError):
        parse_plan(plan)


def test_parse_multiline_desc_at_eof():
    specs = parse_plan("[a] Task A\n  desc: line one\n    line two\n")
    assert specs[0]["desc"] == "line one\nline two"


def test_parse_desc_continuation_may_contain_colon_word():
    # A continuation line that looks like `word:` is desc text, NOT a field — the
    # deeper-indent check wins, so it is not mistaken for an unknown field.
    plan = "[a] Task A\n  desc: intro\n    note: this is still description\n"
    specs = parse_plan(plan)
    assert specs[0]["desc"] == "intro\nnote: this is still description"


def test_parse_empty_desc_line_then_continuation():
    # `desc:` with no inline value, then continuation lines.
    plan = "[a] Task A\n  desc:\n    only paragraph\n"
    specs = parse_plan(plan)
    assert specs[0]["desc"] == "only paragraph"


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


def test_load_mutual_depends_on_creates_single_link(core):
    # Under v0.3.0 symmetric semantics (T86), "[a] depends_on b" and
    # "[b] depends_on a" describe the SAME link {a, b}; the load just creates
    # the canonical pair once (no cycle to refuse). The plan parser still
    # accepts the v0.2.0 `depends_on:` keyword; T113 updates D24 prose to use
    # `links:` and tightens the parser.
    cyc = "[a] one\n  depends_on: b\n[b] two\n  depends_on: a\n"
    core.load(parse_plan(cyc))
    assert [t.id for t in core.ls()] == [1, 2]
    n = core.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    assert n == 1  # canonical (1, 2) — both depends_on lines collapse to it


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
