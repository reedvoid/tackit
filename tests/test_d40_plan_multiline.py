"""D40 — the plan format carries D37-grade multi-paragraph desc bodies.

parse_plan previously HARD-FAILED on a `desc:` containing blank-line-separated
paragraphs (the line after the blank was neither a `[key]` nor a `field:`, so
it raised `cannot parse` and rolled the whole import back). D40 defers a blank
line inside a desc block: it becomes a paragraph break iff a continuation line
follows, and is discarded (block ends) iff a field/[key]/EOF follows.

These pin the new behavior AND the unchanged old behavior (blank between tasks,
trailing blank before a field, consecutive-line continuation, deps-ends-on-blank).
"""

from tackit.plan import parse_plan


def test_blank_line_separated_paragraphs_round_trip():
    """The exact shape that used to raise `cannot parse` now parses, with the
    blank lines preserved as `\\n\\n` paragraph breaks."""
    plan = (
        "[slice-x] S99 — example schema slice\n"
        "  kind: schema\n"
        "  desc: First paragraph describing the table.\n"
        "\n"
        "    Second paragraph after a blank line.\n"
        "\n"
        "    ## A markdown subsection\n"
        "    - a bullet\n"
        "  labels: demo\n"
    )
    specs = parse_plan(plan)
    assert len(specs) == 1
    assert specs[0]["desc"] == (
        "First paragraph describing the table.\n\n"
        "Second paragraph after a blank line.\n\n"
        "## A markdown subsection\n"
        "- a bullet"
    )
    assert specs[0]["labels"] == ["demo"]


def test_multiple_consecutive_blank_lines_preserved():
    plan = (
        "[a] n\n  kind: production\n"
        "  desc: p1\n"
        "\n"
        "\n"
        "    p2\n"
    )
    assert parse_plan(plan)[0]["desc"] == "p1\n\n\np2"


def test_blank_then_next_task_still_separates_tasks():
    """A blank line followed by a new [key] still ends the desc and starts the
    next task (the long-standing task-separator convention is unchanged)."""
    plan = (
        "[a] first\n  kind: production\n  desc: body of a.\n"
        "\n"
        "[b] second\n  kind: production\n"
    )
    specs = parse_plan(plan)
    assert specs[0]["desc"] == "body of a."  # trailing blank not absorbed
    assert specs[1]["key"] == "b" and specs[1]["kind"] == "production"


def test_trailing_blank_before_field_ends_desc():
    """A blank line followed by another field (not a continuation) ends the
    desc; the blank is discarded, not folded into the description."""
    plan = "[a] n\n  kind: production\n  desc: only line.\n\n  labels: x\n"
    spec = parse_plan(plan)[0]
    assert spec["desc"] == "only line."
    assert spec["labels"] == ["x"]


def test_consecutive_continuation_unchanged_single_newline():
    """No blank line => consecutive deeper-indented lines join with a single
    `\\n` (regression guard — the pre-D40 behavior must be untouched)."""
    plan = "[a] n\n  kind: production\n  desc: l1\n    l2\n    l3\n"
    assert parse_plan(plan)[0]["desc"] == "l1\nl2\nl3"


def test_deps_block_still_ends_at_blank_line():
    """D40 only defers blanks inside a desc; a depends_on block still ends at a
    blank line, and a following [key] parses cleanly."""
    plan = (
        "[a] first\n  kind: production\n"
        "[b] second\n  kind: production\n  depends_on:\n"
        "    a :: b couples to a's contract\n"
        "\n"
        "[c] third\n  kind: production\n"
    )
    specs = parse_plan(plan)
    assert specs[1]["depends_on"] == [
        {"key": "a", "because": "b couples to a's contract"}
    ]
    assert specs[2]["key"] == "c"
