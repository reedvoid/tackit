"""D24 - bulk plan import: the parser (tackit.plan) and core.load (atomic create +
key-resolved depends_on)."""

import pytest

from tackit import sync
from tackit.errors import InvariantError, NotFoundError, ValidationError
from tackit.plan import parse_plan

PLAN = """\
# a small plan
[anchor] Spec anchor
  kind: design

[base] Build the base thing
  kind: production
  labels: core
  depends_on:
    anchor :: base realizes the anchor decision

[mid] Build the middle thing
  kind: production
  desc: sits on the base
  labels: core, feature
  depends_on:
    base :: mid sits on base's API; base interface changes need mid to follow
    anchor :: mid realizes the anchor decision

[top] Build the top thing
  kind: production
  depends_on:
    mid :: top composes mid's behavior end-to-end
    base :: top also reaches through directly for the foundational types
    anchor :: top realizes the anchor decision
"""


def test_parse_plan_ok():
    specs = parse_plan(PLAN)
    assert [s["key"] for s in specs] == ["anchor", "base", "mid", "top"]
    mid = specs[2]
    assert mid["name"] == "Build the middle thing"
    assert mid["desc"] == "sits on the base"
    assert mid["labels"] == ["core", "feature"]
    # D33 / T164: depends_on is list[{"key", "because"}], not list[str].
    assert mid["depends_on"] == [
        {"key": "base", "because": "mid sits on base's API; base interface changes need mid to follow"},
        {"key": "anchor", "because": "mid realizes the anchor decision"},
    ]
    assert [d["key"] for d in specs[3]["depends_on"]] == ["mid", "base", "anchor"]


def test_parse_multiline_desc_folds_continuations():
    # Deeper-indented lines after `desc:` are continuation lines, joined with \n.
    plan = (
        "[a] Task A\n"
        "  kind: production\n"
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
        "  kind: production\n"
        "  desc: line one\n"
        "    line two\n"
        "[b] Task B\n"
        "  kind: production\n"
        "  desc: just me\n"
    )
    specs = parse_plan(plan)
    assert specs[0]["desc"] == "line one\nline two"
    assert specs[1]["desc"] == "just me"


def test_parse_multiline_desc_block_ends_at_blank_line():
    # A blank line terminates the desc block; an equal-indent garbage line after it
    # is no longer a continuation and so still fails loud.
    plan = (
        "[a] Task A\n"
        "  kind: production\n"
        "  desc: line one\n"
        "    line two\n"
        "\n"
        "  loose garbage\n"
    )
    with pytest.raises(ValidationError):
        parse_plan(plan)


def test_parse_multiline_desc_at_eof():
    specs = parse_plan(
        "[a] Task A\n  kind: production\n  desc: line one\n    line two\n"
    )
    assert specs[0]["desc"] == "line one\nline two"


def test_parse_desc_continuation_may_contain_colon_word():
    # A continuation line that looks like `word:` is desc text, NOT a field — the
    # deeper-indent check wins, so it is not mistaken for an unknown field.
    plan = (
        "[a] Task A\n"
        "  kind: production\n"
        "  desc: intro\n"
        "    note: this is still description\n"
    )
    specs = parse_plan(plan)
    assert specs[0]["desc"] == "intro\nnote: this is still description"


def test_parse_empty_desc_line_then_continuation():
    # `desc:` with no inline value, then continuation lines.
    plan = "[a] Task A\n  kind: production\n  desc:\n    only paragraph\n"
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
    assert set(keymap) == {"anchor", "base", "mid", "top"}
    mid_deps = [n.id for n in core.dependencies_of(keymap["mid"])]
    assert keymap["base"] in mid_deps
    top_deps = sorted(n.id for n in core.dependencies_of(keymap["top"]))
    assert top_deps == sorted([keymap["mid"], keymap["base"], keymap["anchor"]])
    assert core.labels_of(keymap["mid"]) == ["core", "feature"]


def test_load_unknown_dep_key_fails_before_mutating(core):
    with pytest.raises(ValidationError):
        core.load(parse_plan(
            "[a] one\n  kind: production\n  depends_on:\n    nope :: missing target\n"
        ))
    assert core.ls() == []  # nothing created (validated before the transaction)


def test_load_mutual_depends_on_creates_single_link(core):
    # Under v0.3.0 symmetric semantics (T86), "[a] depends_on b" and
    # "[b] depends_on a" describe the SAME link {a, b}; the load just creates
    # the canonical pair once (no cycle to refuse).
    cyc = (
        "[anchor] spec anchor\n  kind: design\n"
        "[a] one\n  kind: production\n  depends_on:\n"
        "    b :: a couples to b's shape\n"
        "    anchor :: a realizes the anchor decision\n"
        "[b] two\n  kind: production\n  depends_on:\n"
        "    a :: b also references a's shape\n"
        "    anchor :: b realizes the anchor decision\n"
    )
    core.load(parse_plan(cyc))
    assert [t.id for t in core.ls()] == [1, 2, 3]
    n = core.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    assert n == 3  # canonical (a, b) collapses to 1 + a-anchor + b-anchor


def test_load_is_a_single_version_bump(core):
    v0 = sync.get_version(core.conn)
    core.load(parse_plan(PLAN))  # 4 tasks (incl. anchor) + 6 edges
    assert sync.get_version(core.conn) == v0 + 1  # atomic: one bump for the whole plan


def test_load_design_and_schema_land_at_spec_status(core):
    """v0.5 D36 / T176: bulk plan import honors the kind/status partition
    default per row -- design/schema rows land at status='spec', not
    'open'. Pins the parser+loader path (separate from the direct add()
    path tested in test_kind_required) so a regression that hardcodes
    status='open' at load time gets caught here."""
    plan = (
        "[d] a design slice\n  kind: design\n"
        "[s] a schema slice\n  kind: schema\n"
        "[p] a production task\n  kind: production\n  depends_on:\n"
        "    d :: p realizes the design decision\n"
        "[m] a meta task\n  kind: meta\n"
    )
    keymap = core.load(parse_plan(plan))
    assert core.get(keymap["d"]).status == "spec"
    assert core.get(keymap["s"]).status == "spec"
    assert core.get(keymap["p"]).status == "open"
    assert core.get(keymap["m"]).status == "open"


def test_load_reports_new_labels(core):  # T67 anti-sprawl summary
    anchor = core.add("spec anchor", kind="design")  # D1
    core.add(
        "seed", kind="production", labels=["existing"],
        deps={anchor.id: "seed realizes the anchor decision"},
    )
    core.last_label_nudge = None
    core.load(parse_plan(
        "[a] one\n  kind: production\n  labels: existing, brandnew\n"
        "  depends_on:\n    D1 :: a realizes the anchor decision\n"
        "[b] two\n  kind: production\n  labels: another\n"
        "  depends_on:\n    D1 :: b realizes the anchor decision\n"
    ))
    assert core.last_label_nudge is not None
    assert "brandnew" in core.last_label_nudge and "another" in core.last_label_nudge
    assert "existing" not in core.last_label_nudge  # already existed -> not reported as new


# --- T215: depends_on resolves EXISTING tasks (prefixed-name / #id) --------


def test_load_depends_on_existing_task_by_prefixed_name(core):
    anchor = core.add("anchors table", kind="schema")  # S1
    keymap = core.load(parse_plan(
        "[impl] concept extraction\n"
        "  kind: design\n"
        "  depends_on:\n"
        "    S1 :: concept fields persist to the anchors table S1 defines\n"
    ))
    deps = [n.id for n in core.dependencies_of(keymap["impl"])]
    assert anchor.id in deps  # edge to the pre-existing schema task


def test_load_depends_on_existing_by_hash_id(core):
    anchor = core.add("anchors table", kind="schema")  # id 1
    keymap = core.load(parse_plan(
        "[impl] thing\n  kind: production\n  depends_on:\n"
        "    #1 :: realizes the anchors contract\n"
    ))
    assert anchor.id in [n.id for n in core.dependencies_of(keymap["impl"])]


def test_load_mixed_batch_and_existing_refs(core):
    anchor = core.add("existing schema", kind="schema")  # S1
    keymap = core.load(parse_plan(
        "[a] first\n  kind: production\n  depends_on:\n"
        "    S1 :: a realizes the existing schema anchor\n"
        "[b] second\n  kind: production\n  depends_on:\n"
        "    a :: b builds on batch-local a\n"
        "    S1 :: b also reaches the existing schema anchor\n"
    ))
    b_deps = sorted(n.id for n in core.dependencies_of(keymap["b"]))
    assert b_deps == sorted([keymap["a"], anchor.id])


def test_load_existing_ref_kind_letter_mismatch_refused(core):
    core.add("a design slice", kind="design")  # D1, NOT T1
    with pytest.raises(ValidationError, match="kind-letter"):
        core.load(parse_plan(
            "[impl] thing\n  kind: production\n  depends_on:\n"
            "    T1 :: wrong letter -- id 1 is a design task\n"
        ))
    assert len(core.ls()) == 1  # impl not created (refused before mutate)


def test_load_unknown_existing_ref_rolls_back(core):
    with pytest.raises(NotFoundError):  # no task id 999
        core.load(parse_plan(
            "[impl] thing\n  kind: production\n  depends_on:\n"
            "    S999 :: references a task that does not exist\n"
        ))
    assert core.ls() == []  # whole import rolled back, no partial


def test_load_reserved_key_shaped_like_prefixed_id_refused(core):
    with pytest.raises(ValidationError, match="reserved"):
        parse_plan("[S30] looks like an existing ref\n  kind: schema\n")


def test_load_existing_ref_to_retired_endpoint_refused(core):
    dead = core.add("dead decision", kind="design")  # D1, spec
    core.retire(dead.id, reason="100% gone with no replacement", delta="retire for test")
    with pytest.raises(InvariantError):  # _add_link refuses a retired endpoint (D36)
        core.load(parse_plan(
            "[impl] thing\n  kind: production\n  depends_on:\n"
            "    D1 :: links to a retired decision -- refused\n"
        ))
    assert len(core.ls()) == 1  # only the retired D1 remains; impl rolled back


def test_load_existing_ref_to_closed_task_allowed(core):
    anchor = core.add("spec anchor", kind="design")  # D1
    done = core.add(
        "done prereq", kind="production",
        deps={anchor.id: "done realizes the anchor decision"},
    )  # T2
    core.close(done.id)
    keymap = core.load(parse_plan(
        "[impl] follow-on\n  kind: production\n  depends_on:\n"
        "    T2 :: builds on the closed prereq's shipped contract\n"
        "    D1 :: impl also realizes the anchor decision\n"
    ))
    assert done.id in [n.id for n in core.dependencies_of(keymap["impl"])]
