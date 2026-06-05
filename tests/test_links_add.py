"""T216 (D213): bulk-link EXISTING tasks via core.links_add — validate-all-
first, atomic, already-linked no-op, compact echo by prefixed-name."""

import pytest

from tackit import sync
from tackit.errors import ValidationError


def _seed(core, n, kind="production"):
    return [core.add(f"task {i}", kind=kind).id for i in range(n)]


def test_links_add_creates_multiple_edges(core):
    a, b, c = _seed(core, 3)
    result = core.links_add([
        {"a": a, "b": b, "because": "b consumes a's output"},
        {"a": b, "b": c, "because": "c consumes b's output"},
    ])
    assert result["created"] == 2
    assert result["already_linked"] == 0
    n = core.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    assert n == 2


def test_links_add_fan_in_and_fan_out(core):
    hub, s1, s2, t1, t2 = _seed(core, 5)
    result = core.links_add([
        {"a": s1, "b": hub, "because": "s1 feeds the hub"},     # fan-in
        {"a": s2, "b": hub, "because": "s2 feeds the hub"},     # fan-in
        {"a": hub, "b": t1, "because": "hub drives t1"},        # fan-out
        {"a": hub, "b": t2, "because": "hub drives t2"},        # fan-out
    ])
    assert result["created"] == 4
    assert len(core.dependencies_of(hub)) == 4  # symmetric: all four neighbors


def test_links_add_validate_all_first_lists_every_offender(core):
    a, b = _seed(core, 2)
    m = core.add("meta task", kind="meta").id
    with pytest.raises(ValidationError) as exc:
        core.links_add([
            {"a": a, "b": b, "because": "valid edge"},          # ok
            {"a": a, "b": a, "because": "self-link"},           # offender 1
            {"a": a, "b": "S999", "because": "unknown ref"},    # offender 2
            {"a": a, "b": b, "because": ""},                    # offender 3 (empty because)
            {"a": m, "b": a, "because": "meta to production"},  # offender 4 (meta-island)
        ])
    msg = str(exc.value)
    assert "self" in msg.lower()
    assert "S999" in msg
    assert "because" in msg.lower()
    assert "meta" in msg.lower()
    # NOTHING created — the whole batch is refused before mutating.
    assert core.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 0


def test_links_add_already_linked_is_benign_noop(core):
    a, b = _seed(core, 2)
    core.links_add([{"a": a, "b": b, "because": "first wire"}])
    result = core.links_add([{"a": a, "b": b, "because": "second attempt"}])
    assert result["created"] == 0
    assert result["already_linked"] == 1
    # re-runnable; the no-op does NOT overwrite the existing rationale.
    row = core.conn.execute(
        "SELECT because FROM links WHERE task_a=? AND task_b=?",
        tuple(sorted((a, b))),
    ).fetchone()
    assert row["because"] == "first wire"


def test_links_add_resolves_prefixed_name_and_id(core):
    d = core.add("a design slice", kind="design")     # D1
    core.add("an impl task", kind="production")        # T2
    # mix: prefixed-name on one endpoint, raw id on the other.
    result = core.links_add([{"a": "T2", "b": d.id, "because": "T2 realizes D1"}])
    assert result["created"] == 1
    assert result["created_pairs"] == [["T2", "D1"]]


def test_links_add_empty_list_is_noop(core):
    v0 = sync.get_version(core.conn)
    result = core.links_add([])
    assert result == {"created": 0, "already_linked": 0, "created_pairs": []}
    assert sync.get_version(core.conn) == v0  # no version bump on a pure no-op


def test_links_add_intra_batch_duplicate_deduped(core):
    a, b = _seed(core, 2)
    result = core.links_add([
        {"a": a, "b": b, "because": "first"},
        {"a": b, "b": a, "because": "same pair, reversed → canonical dupe"},
    ])
    assert result["created"] == 1
    assert result["already_linked"] == 1
    assert core.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 1


def test_links_add_all_already_linked_no_version_bump(core):
    a, b = _seed(core, 2)
    core.links_add([{"a": a, "b": b, "because": "wire"}])
    v0 = sync.get_version(core.conn)
    core.links_add([{"a": a, "b": b, "because": "again"}])  # all already-linked
    assert sync.get_version(core.conn) == v0  # no-op → no bump (D20)
