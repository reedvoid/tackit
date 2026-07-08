"""Engine edge cases the original suite left in the negative space (Pass 3).

These pin behaviors the happy-path tests don't touch: edit-empty refusal,
status-blind traversal, malformed FTS5 syntax, invalid filter, empty render,
edge-to-missing on both add-time and dep_rm, and add-with-deps+labels.
"""

import pytest

from tackit.errors import InvariantError, NotFoundError, ValidationError


def test_edit_empty_name_refused(core):
    core.add("spec anchor", kind="design")  # id 1 -- satisfies the D256 gate
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    with pytest.raises(ValidationError):
        core.edit(2, name="   ", delta="test")


# --- T87 / D26: meta-island constraint ------------------------------------


def _set_kind(core, task_id, kind):
    """Test helper -- change a task's kind via raw UPDATE. Originally needed
    pre-T94 when kind wasn't an add() argument; kept as a fixture-shaping
    primitive in the cross-kind-link tests below. v0.5 (D36): kind/status
    partition requires the status to be set in lockstep with kind, so this
    helper also updates status to a partition-valid default (spec for
    design/schema, open for production/meta), mirroring what reclassify()
    will do under D36 cross-partition auto-shift."""
    new_status = "spec" if kind in ("design", "schema") else "open"
    core.conn.execute(
        "UPDATE tasks SET kind = ?, status = ? WHERE id = ?",
        (kind, new_status, task_id),
    )


def test_meta_island_refuses_meta_to_production_link(core):
    core.add("spec anchor", kind="design")  # id 1
    core.add("prod task", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    core.add("meta task", kind="production", deps={1: "realizes the anchor decision"})  # id 3
    _set_kind(core, 3, "meta")
    with pytest.raises(InvariantError, match="meta-island"):
        core.link_add(2, 3, because="test fixture")


def test_meta_island_refuses_meta_to_design_link(core):
    core.add("spec anchor", kind="design")  # id 1
    core.add("design task", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    core.add("meta task", kind="production", deps={1: "realizes the anchor decision"})  # id 3
    _set_kind(core, 2, "design")
    _set_kind(core, 3, "meta")
    with pytest.raises(InvariantError, match="meta-island"):
        core.link_add(2, 3, because="test fixture")


def test_meta_to_meta_link_allowed(core):
    core.add("spec anchor", kind="design")  # id 1
    core.add("meta a", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    core.add("meta b", kind="production", deps={1: "realizes the anchor decision"})  # id 3
    _set_kind(core, 2, "meta")
    _set_kind(core, 3, "meta")
    core.link_add(2, 3, because="test fixture")  # both meta -> allowed
    n = core.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    # 2 links from the D256 gate (anchor<->meta-a, anchor<->meta-b) + the
    # explicit meta<->meta link just added.
    assert n == 3


# --- v0.4 / D29: edit allowed on closed/wont_do with audit-table backstop --


def test_edit_closed_task_refused_under_d259(core):
    """D259 reverses the v0.4 edit-on-closed behavior: a closed task is a frozen
    record -- edit is refused (reopen to change). No audit row is written."""
    core.add("spec anchor", kind="design")  # id 1
    core.add(
        "a", kind="production", description="original desc",
        deps={1: "realizes the anchor decision"},
    )  # id 2
    core.close(2)
    with pytest.raises(ValidationError, match="frozen record"):
        core.edit(2, description="updated desc", delta="prose refinement after close")
    t = core.get(2)
    assert t.description == "original desc"  # unchanged
    assert t.status == "closed"
    assert core.history(2).description_revisions == []  # no audit row


# --- T117: delta required on edit (cascade-firing) ------------------------
# Link ops carry NO delta (D213): they don't cascade, so a delta would have no
# reader. See test_link_ops_take_no_delta below.


def test_edit_empty_delta_refused(core):
    core.add("spec anchor", kind="design")  # id 1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    with pytest.raises(ValidationError, match="delta"):
        core.edit(2, description="changed", delta="")
    with pytest.raises(ValidationError, match="delta"):
        core.edit(2, description="changed", delta="   ")


def test_link_ops_take_no_delta(core):
    """D213: link_add / link_rm dropped the vestigial delta. They work with
    because only (link_rm with a/b only), and a stray delta kwarg is rejected."""
    core.add("spec anchor", kind="design")  # id 1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # id 3
    core.link_add(2, 3, because="a couples to b")
    core.link_rm(2, 3)
    with pytest.raises(TypeError):
        core.link_add(2, 3, because="x", delta="y")
    with pytest.raises(TypeError):
        core.link_rm(2, 3, delta="y")


def test_core_last_delta_set_after_edit(core):
    core.add("spec anchor", kind="design")  # id 1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    core.edit(2, description="changed", delta="shifted X to Y")
    assert core.last_delta == "shifted X to Y"


def test_link_add_empty_because_refused(core):
    core.add("spec anchor", kind="design")  # id 1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # id 3
    with pytest.raises(ValidationError, match="because"):
        core.link_add(2, 3, because="")
    with pytest.raises(ValidationError, match="because"):
        core.link_add(2, 3, because="   ")


def test_link_add_preserves_because(core):
    core.add("spec anchor", kind="design")  # id 1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # id 3
    core.link_add(2, 3, because="T2 uses T1.id as FK")
    row = core.conn.execute(
        "SELECT because FROM links WHERE task_a=2 AND task_b=3"
    ).fetchone()
    assert row["because"] == "T2 uses T1.id as FK"
    # The no-op guard on duplicate link_add does NOT overwrite the rationale.
    core.link_add(2, 3, because="different rationale")
    row = core.conn.execute(
        "SELECT because FROM links WHERE task_a=2 AND task_b=3"
    ).fetchone()
    assert row["because"] == "T2 uses T1.id as FK"


def test_cross_kind_non_meta_link_allowed(core):
    # The meta-island bounds ONLY the meta vs non-meta boundary. design <->
    # schema, schema <-> production, design <-> production are all fine.
    core.add("spec anchor", kind="design")  # id 1
    core.add("d task", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    core.add("s task", kind="production", deps={1: "realizes the anchor decision"})  # id 3
    core.add("p task", kind="production", deps={1: "realizes the anchor decision"})  # id 4
    _set_kind(core, 2, "design")
    _set_kind(core, 3, "schema")
    # p task (id 4) stays production
    core.link_add(3, 2, because="test fixture")  # schema <-> design
    core.link_add(4, 2, because="test fixture")  # production <-> design
    core.link_add(4, 3, because="test fixture")  # production <-> schema
    n = core.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    # 3 links from the D256 gate (each production creation <-> the anchor) +
    # the 3 explicit cross-kind links just added.
    assert n == 6


def test_traversal_is_status_blind(core):
    core.add("spec anchor", kind="design")  # id 1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # id 3
    core.link_add(3, 2, because="test fixture")  # b depends_on a
    core.close(2)  # close the prerequisite (a)
    # a closed neighbor is still returned in both directions (D6). The anchor
    # (id 1) is also a neighbor of both a and b via the D256 creation-gate
    # link, so it shows up in the neighbor sets alongside the intended edge.
    assert [n.id for n in core.dependents_of(2)] == [1, 3]
    deps = core.dependencies_of(3)
    assert [n.id for n in deps] == [1, 2]
    a_neighbor = next(n for n in deps if n.id == 2)
    assert a_neighbor.status == "closed"


def test_search_metachars_sanitized_not_refused(core):
    """T222: previously a query carrying FTS5 metacharacters (here an unbalanced
    quote) raised a loud refusal. Sanitization now per-token-quotes the input so
    it can no longer be malformed -- the query is handled gracefully (returns
    cleanly, no exception) instead of erroring."""
    core.add("spec anchor", kind="design")  # id 1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    # no exception, and an unmatched term simply returns nothing
    assert core.search('"unbalanced') == []


def test_ls_invalid_status_refused(core):
    with pytest.raises(ValidationError):
        core.ls(status="bogus")


def test_ls_status_filter_accepts_all_three_v04_values(core):
    """T157: D7 v0.4 has three statuses {open, closed, wont_do} -- the ls()
    filter must accept all three. Pre-T157 the validator rejected wont_do as
    a bogus value, breaking `ls --status wont_do` for listing dropped scope."""
    core.add("spec anchor", kind="design")  # id 1, spec
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # id 2, open
    core.add("b", kind="production", deps={1: "realizes the anchor decision"})  # id 3 -> closed
    core.close(3)
    core.add("c", kind="production", deps={1: "realizes the anchor decision"})  # id 4 -> wont_do
    core.wont_do(4, reason="dropped", delta="dropped")

    assert [t.id for t in core.ls(status="open")] == [2]
    assert [t.id for t in core.ls(status="closed")] == [3]
    assert [t.id for t in core.ls(status="wont_do")] == [4]


def test_render_empty_label(core):
    md = core.render("nonexistent-label")
    assert "No tasks" in md


def test_label_empty_refused(core):
    core.add("spec anchor", kind="design")  # id 1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    with pytest.raises(ValidationError):
        core.label_add(2, "   ")


def test_dep_rm_missing_task_refused(core):
    core.add("spec anchor", kind="design")  # id 1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    with pytest.raises(NotFoundError):
        core.link_rm(2, 999)


def test_add_with_deps_and_labels_at_once(core):
    # "base" is itself the interface anchor here (kind=schema): it satisfies
    # the D256 gate for "dep" directly, so no separate anchor task is needed
    # and ids stay exactly as originally asserted below.
    core.add("base", kind="schema")  # T1
    # D33 / T164: deps now require per-edge `because` rationales (dict[int, str]).
    t = core.add(
        "dep",
        kind="production",
        labels=["x", "y"],
        deps={1: "dep extends base's interface; changes to base must be reviewed here"},
    )
    assert t.id == 2
    assert core.labels_of(2) == ["x", "y"]
    assert [n.id for n in core.dependencies_of(2)] == [1]


def test_add_dep_to_missing_refused(core):
    core.add("spec anchor", kind="design")  # id 1 -- satisfies the D256 gate
    with pytest.raises(NotFoundError):
        core.add(
            "bad",
            kind="production",
            deps={1: "satisfies the D256 gate", 999: "test fixture: bad target"},
        )


def test_edit_name_change(core):
    core.add("spec anchor", kind="design")  # id 1
    core.add("old name", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    core.edit(2, name="new name", delta="test")
    assert core.get(2).name == "new name"


def test_add_empty_label_refused(core):
    with pytest.raises(ValidationError):
        core.add("x", kind="production", labels=[""])


# --- v0.5 D36: reconcile predicate parametrized over status -----------------


@pytest.mark.parametrize(
    "status,kind",
    [
        ("closed", "production"),
        ("wont_do", "production"),
        ("retired", "design"),  # partition: retired only valid on design/schema
    ],
)
def test_reconcile_refused_on_terminal_status(core, status, kind):
    """v0.5 D36: reconcile refusal is status-derived: status IN
    ('closed','wont_do','retired'). Parametrize covers all three terminal
    states. Each status is paired with a partition-valid kind (closed/wont_do
    are production/meta-only; retired is design/schema-only)."""
    if kind == "production":
        core.add("spec anchor", kind="design")  # id 1 -- satisfies the D256 gate
        core.add("a", kind=kind, deps={1: "realizes the anchor decision"})  # id 2
        task_id = 2
    else:
        core.add("a", kind=kind)  # id 1 -- design/schema is unaffected by the gate
        task_id = 1
    core.conn.execute(
        "UPDATE tasks SET status = ?, stale = 1 WHERE id = ?;",
        (status, task_id),
    )
    with pytest.raises(InvariantError, match=r"record-only|archaeology"):
        core.reconcile(task_id)


@pytest.mark.parametrize("status", ["open", "spec"])
def test_reconcile_allowed_on_live_status(core, status):
    """v0.5 D36: reconcile is allowed on the live partition: status IN
    ('open','spec'). Parametrize over both."""
    # spec only valid on design/schema; open only valid on production/meta.
    kind = "design" if status == "spec" else "production"
    if kind == "production":
        core.add("spec anchor", kind="design")  # id 1 -- satisfies the D256 gate
        core.add("a", kind=kind, deps={1: "realizes the anchor decision"})  # id 2
        task_id = 2
    else:
        core.add("a", kind=kind)  # id 1 -- design/schema is unaffected by the gate
        task_id = 1
    core.conn.execute("UPDATE tasks SET stale = 1 WHERE id = ?;", (task_id,))
    t = core.reconcile(task_id)
    assert t.stale is False
    assert t.status == status  # status preserved


def test_ls_stale_filter(core):
    core.add("spec anchor", kind="design")  # id 1
    core.add("base", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    core.add("dep", kind="production", deps={1: "realizes the anchor decision"})  # id 3
    core.link_add(3, 2, because="test fixture")
    core.edit(2, description="x", delta="test")  # stales dep(3) and, incidentally, the anchor(1)
    core.reconcile(1)  # clear the anchor's incidental staleness -- not what this test targets
    assert [t.id for t in core.ls(stale=True)] == [3]


def test_render_shows_deps_and_extra_labels(core):
    core.add("spec anchor", kind="design")  # id 1 -- no "spec" label, stays out of render("spec")
    # "design" is reserved for the kind property since T84 -- use a non-reserved label.
    core.add(
        "base", kind="production", labels=["spec"], deps={1: "realizes the anchor decision"}
    )  # id 2
    core.add(
        "feature", kind="production", labels=["spec", "core"],
        deps={1: "realizes the anchor decision"},
    )  # id 3
    core.link_add(3, 2, because="test fixture")  # feature depends_on base
    md = core.render("spec")
    assert "depends on" in md.lower()  # feature's dependency edge is rendered
    assert "core" in md  # the non-rendered extra label is listed


def test_nul_byte_in_name_refused(core):
    # A NUL byte breaks the D18 tackit.sql round-trip; the boundary must reject it.
    with pytest.raises(ValidationError):
        core.add("bad\x00name", kind="production")


def test_nul_byte_in_description_refused(core):
    with pytest.raises(ValidationError):
        core.add("ok", kind="production", description="body\x00here")


def test_nul_byte_in_label_refused(core):
    core.add("spec anchor", kind="design")  # id 1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    with pytest.raises(ValidationError):
        core.label_add(2, "tag\x00")


def test_nul_byte_on_edit_refused(core):
    core.add("spec anchor", kind="design")  # id 1
    core.add("a", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    with pytest.raises(ValidationError):
        core.edit(2, name="new\x00name", delta="test")


def test_unpaired_surrogate_in_name_refused(core):
    # \ud800 is not valid UTF-8; SQLite can't encode it -> must be refused loudly.
    with pytest.raises(ValidationError):
        core.add("\ud800", kind="production")


def test_diamond_traversal_dedup(core):
    # Under symmetric semantics (T86), the diamond exercises the `seen` dedup
    # in `_stale_linked_transitive` (the close-gate walker). T4 reaches T1 by
    # two paths in the undirected graph; the walker must not revisit T1. Cycles
    # are no longer a concept (undirected edges have no directed cycle), so
    # closing the apex is permitted and the would-be-cycle dep_add is now
    # idempotent (the canonical pair (1, 4) is created either way).
    core.add("spec anchor", kind="design")  # id 1
    core.add("base", kind="production", deps={1: "realizes the anchor decision"})  # id 2
    core.add("left", kind="production", deps={1: "realizes the anchor decision"})  # id 3
    core.link_add(3, 2, because="test fixture")  # link base <-> left
    core.add("right", kind="production", deps={1: "realizes the anchor decision"})  # id 4
    core.link_add(4, 2, because="test fixture")  # link base <-> right
    core.add("apex", kind="production", deps={1: "realizes the anchor decision"})  # id 5
    core.link_add(5, 3, because="test fixture")  # link left <-> apex
    core.link_add(5, 4, because="test fixture")  # link right <-> apex
    assert core.close(5).task.status == "closed"  # walks the diamond, nothing stale
    core.link_add(2, 5, because="test fixture")  # adds link base <-> apex (no cycle under symmetric)
    n = core.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    # 4 links from the D256 gate (each production creation <-> the anchor) +
    # 4 diamond links + 1 new closing link.
    assert n == 9
