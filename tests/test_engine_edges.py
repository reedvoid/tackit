"""Engine edge cases the original suite left in the negative space (Pass 3).

These pin behaviors the happy-path tests don't touch: edit-empty refusal,
status-blind traversal, malformed FTS5 syntax, invalid filter, empty render,
edge-to-missing on both add-time and dep_rm, and add-with-deps+labels.
"""

import pytest

from tackit.errors import InvariantError, NotFoundError, ValidationError


def test_edit_empty_name_refused(core):
    core.add("a")
    with pytest.raises(ValidationError):
        core.edit(1, name="   ", delta="test")


# --- T87 / D26: meta-island constraint ------------------------------------


def _set_kind(core, task_id, kind):
    """Test helper -- set a task's kind via raw UPDATE. Once T94 ships, kind
    will be a required `add` argument and this becomes the proper code path;
    until then this is the only way to vary kind in tests."""
    core.conn.execute("UPDATE tasks SET kind = ? WHERE id = ?", (kind, task_id))


def test_meta_island_refuses_meta_to_production_link(core):
    core.add("prod task")  # T1 -- default kind production
    core.add("meta task")  # T2
    _set_kind(core, 2, "meta")
    with pytest.raises(InvariantError, match="meta-island"):
        core.link_add(1, 2, because="test fixture", delta="test")


def test_meta_island_refuses_meta_to_design_link(core):
    core.add("design task")  # T1
    core.add("meta task")  # T2
    _set_kind(core, 1, "design")
    _set_kind(core, 2, "meta")
    with pytest.raises(InvariantError, match="meta-island"):
        core.link_add(1, 2, because="test fixture", delta="test")


def test_meta_to_meta_link_allowed(core):
    core.add("meta a")  # T1
    core.add("meta b")  # T2
    _set_kind(core, 1, "meta")
    _set_kind(core, 2, "meta")
    core.link_add(1, 2, because="test fixture", delta="test")  # both meta -> allowed
    n = core.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    assert n == 1


# --- T118: edit refused on closed tasks (use supersede) --------------------


def test_edit_closed_task_refused(core):
    core.add("a")
    core.close(1)
    with pytest.raises(InvariantError, match="closed"):
        core.edit(1, description="changed", delta="trying to edit a closed")


def test_edit_closed_task_error_names_supersede(core):
    core.add("a")
    core.add("b")  # potential successor
    core.close(1)
    try:
        core.edit(1, description="changed", delta="testing the message")
    except InvariantError as e:
        assert "supersede" in str(e).lower()
    else:
        raise AssertionError("expected InvariantError")


# --- T117: delta required on edit / supersede / link_add / link_rm --------


def test_edit_empty_delta_refused(core):
    core.add("a")
    with pytest.raises(ValidationError, match="delta"):
        core.edit(1, description="changed", delta="")
    with pytest.raises(ValidationError, match="delta"):
        core.edit(1, description="changed", delta="   ")


def test_link_add_empty_delta_refused(core):
    core.add("a")
    core.add("b")
    with pytest.raises(ValidationError, match="delta"):
        core.link_add(1, 2, because="real reason", delta="")


def test_link_rm_empty_delta_refused(core):
    core.add("a")
    core.add("b")
    core.link_add(1, 2, because="real", delta="seed")
    with pytest.raises(ValidationError, match="delta"):
        core.link_rm(1, 2, delta="")


def test_supersede_empty_delta_refused(core):
    core.add("a")
    core.add("b")
    with pytest.raises(ValidationError, match="delta"):
        core.supersede(1, 2, delta="")


def test_core_last_delta_set_after_edit(core):
    core.add("a")
    core.edit(1, description="changed", delta="shifted X to Y")
    assert core.last_delta == "shifted X to Y"


def test_link_add_empty_because_refused(core):
    core.add("a")
    core.add("b")
    with pytest.raises(ValidationError, match="because"):
        core.link_add(1, 2, because="", delta="test")
    with pytest.raises(ValidationError, match="because"):
        core.link_add(1, 2, because="   ", delta="test")


def test_link_add_preserves_because(core):
    core.add("a")
    core.add("b")
    core.link_add(1, 2, because="T2 uses T1.id as FK", delta="test")
    row = core.conn.execute(
        "SELECT because FROM links WHERE task_a=1 AND task_b=2"
    ).fetchone()
    assert row["because"] == "T2 uses T1.id as FK"
    # The no-op guard on duplicate link_add does NOT overwrite the rationale.
    core.link_add(1, 2, because="different rationale", delta="test")
    row = core.conn.execute(
        "SELECT because FROM links WHERE task_a=1 AND task_b=2"
    ).fetchone()
    assert row["because"] == "T2 uses T1.id as FK"


def test_cross_kind_non_meta_link_allowed(core):
    # The meta-island bounds ONLY the meta vs non-meta boundary. design <->
    # schema, schema <-> production, design <-> production are all fine.
    core.add("d task")  # T1
    core.add("s task")  # T2
    core.add("p task")  # T3
    _set_kind(core, 1, "design")
    _set_kind(core, 2, "schema")
    # T3 stays production
    core.link_add(2, 1, because="test fixture", delta="test")  # schema <-> design
    core.link_add(3, 1, because="test fixture", delta="test")  # production <-> design
    core.link_add(3, 2, because="test fixture", delta="test")  # production <-> schema
    n = core.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    assert n == 3


def test_traversal_is_status_blind(core):
    core.add("a")  # T1
    core.add("b")
    core.link_add(2, 1, because="test fixture", delta="test")  # T2 depends_on T1
    core.close(1)  # close the prerequisite
    # a closed neighbor is still returned in both directions (D6)
    assert [n.id for n in core.dependents_of(1)] == [2]
    deps = core.dependencies_of(2)
    assert [n.id for n in deps] == [1]
    assert deps[0].status == "closed"


def test_search_malformed_fts_syntax_refused(core):
    core.add("a")
    with pytest.raises(ValidationError):
        core.search('"unbalanced')  # malformed FTS5 MATCH -> loud refusal, not raw error


def test_ls_invalid_status_refused(core):
    with pytest.raises(ValidationError):
        core.ls(status="bogus")


def test_render_empty_label(core):
    md = core.render("nonexistent-label")
    assert "No tasks" in md


def test_label_empty_refused(core):
    core.add("a")
    with pytest.raises(ValidationError):
        core.label_add(1, "   ")


def test_dep_rm_missing_task_refused(core):
    core.add("a")
    with pytest.raises(NotFoundError):
        core.link_rm(1, 999, delta="test")


def test_add_with_deps_and_labels_at_once(core):
    core.add("base")  # T1
    t = core.add("dep", labels=["x", "y"], deps=[1])
    assert t.id == 2
    assert core.labels_of(2) == ["x", "y"]
    assert [n.id for n in core.dependencies_of(2)] == [1]


def test_add_dep_to_missing_refused(core):
    with pytest.raises(NotFoundError):
        core.add("bad", deps=[999])


def test_edit_name_change(core):
    core.add("old name")
    core.edit(1, name="new name", delta="test")
    assert core.get(1).name == "new name"


def test_add_empty_label_refused(core):
    with pytest.raises(ValidationError):
        core.add("x", labels=[""])


def test_ls_stale_filter(core):
    core.add("base")  # T1
    core.add("dep")
    core.link_add(2, 1, because="test fixture", delta="test")
    core.edit(1, description="x", delta="test")  # stales T2
    assert [t.id for t in core.ls(stale=True)] == [2]


def test_render_shows_deps_and_extra_labels(core):
    # "design" is reserved for the kind property since T84 -- use a non-reserved label.
    core.add("base", labels=["spec"])  # T1
    core.add("feature", labels=["spec", "core"])  # T2
    core.link_add(2, 1, because="test fixture", delta="test")  # T2 depends_on T1
    md = core.render("spec")
    assert "depends on" in md.lower()  # T2's dependency edge is rendered
    assert "core" in md  # the non-rendered extra label is listed


def test_nul_byte_in_name_refused(core):
    # A NUL byte breaks the D18 tackit.sql round-trip; the boundary must reject it.
    with pytest.raises(ValidationError):
        core.add("bad\x00name")


def test_nul_byte_in_description_refused(core):
    with pytest.raises(ValidationError):
        core.add("ok", description="body\x00here")


def test_nul_byte_in_label_refused(core):
    core.add("a")
    with pytest.raises(ValidationError):
        core.label_add(1, "tag\x00")


def test_nul_byte_on_edit_refused(core):
    core.add("a")
    with pytest.raises(ValidationError):
        core.edit(1, name="new\x00name", delta="test")


def test_unpaired_surrogate_in_name_refused(core):
    # \ud800 is not valid UTF-8; SQLite can't encode it -> must be refused loudly.
    with pytest.raises(ValidationError):
        core.add("\ud800")


def test_diamond_traversal_dedup(core):
    # Under symmetric semantics (T86), the diamond exercises the `seen` dedup
    # in `_stale_linked_transitive` (the close-gate walker). T4 reaches T1 by
    # two paths in the undirected graph; the walker must not revisit T1. Cycles
    # are no longer a concept (undirected edges have no directed cycle), so
    # closing the apex is permitted and the would-be-cycle dep_add is now
    # idempotent (the canonical pair (1, 4) is created either way).
    core.add("base")  # T1
    core.add("left")
    core.link_add(2, 1, because="test fixture", delta="test")  # link T1 <-> T2
    core.add("right")
    core.link_add(3, 1, because="test fixture", delta="test")  # link T1 <-> T3
    core.add("apex")
    core.link_add(4, 2, because="test fixture", delta="test")  # link T2 <-> T4
    core.link_add(4, 3, because="test fixture", delta="test")  # link T3 <-> T4
    assert core.close(4).task.status == "closed"  # walks the diamond, nothing stale
    core.link_add(1, 4, because="test fixture", delta="test")  # adds link T1 <-> T4 (no cycle under symmetric)
    n = core.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    assert n == 5  # 4 original + 1 new
