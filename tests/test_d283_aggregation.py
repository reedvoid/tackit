"""D283 - structured-column aggregation: summary rollup + order_by firewall.

summary() counts tasks by status/kind + the obligation-bearing stale count;
order_by sorts ls/board over STRUCTURED COLUMNS ONLY (never body content --
the firewall that keeps sort from re-opening the no-content-query rule). Each
allowed column is exercised, and disallowed/body-derived keys are refused
loudly (the converse).
"""

import pytest

from tackit.errors import ValidationError


# --- summary rollup ---------------------------------------------------------

def test_summary_empty_store(core):
    s = core.summary()
    assert s["total"] == 0
    assert s["by_status"] == {}
    assert s["by_kind"] == {}
    assert s["by_status_kind"] == {}
    assert s["stale_open_spec"] == 0


def test_summary_counts_by_status_and_kind(core):
    core.add("d1", kind="design")                              # id1 spec/design
    core.add("s1", kind="schema")                              # id2 spec/schema
    core.add("m1", kind="meta")                                # id3 open/meta
    core.add("p1", kind="production", deps={1: "realizes d1"})  # id4 open/production
    core.add("p2", kind="production", deps={1: "realizes d1"})  # id5 open/production
    core.close(5)                                              # p2 -> closed
    s = core.summary()
    assert s["total"] == 5
    assert s["by_status"] == {"spec": 2, "open": 2, "closed": 1}
    assert s["by_kind"] == {"design": 1, "schema": 1, "meta": 1, "production": 2}
    assert s["by_status_kind"]["spec"] == {"design": 1, "schema": 1}
    assert s["by_status_kind"]["open"] == {"meta": 1, "production": 1}
    assert s["by_status_kind"]["closed"] == {"production": 1}


def test_summary_stale_counts_only_obligation_bearing(core):
    core.add("d1", kind="design")                              # id1 spec
    core.add("p1", kind="production", deps={1: "realizes d1"})  # id2 open
    core.add("p2", kind="production", deps={1: "realizes d1"})  # id3 open
    core.close(3)                                              # p2 -> closed
    # Force id2 (open) AND id3 (closed) stale. Only the open one is
    # obligation-bearing; closed-stale is record-only (D28/D36).
    core.conn.execute("UPDATE tasks SET stale = 1 WHERE id IN (2, 3)")
    s = core.summary()
    assert s["stale_open_spec"] == 1


# --- order_by over structured columns ---------------------------------------

def test_order_by_defaults_to_id(core):
    core.add("d1", kind="design")
    core.add("p1", kind="production", deps={1: "realizes d1"})
    ids = [t.id for t in core.ls()]
    assert ids == sorted(ids)


def test_order_by_status(core):
    core.add("d1", kind="design")                              # spec
    core.add("p1", kind="production", deps={1: "realizes d1"})  # open
    tasks = core.ls(order_by="status")
    # 'open' sorts before 'spec' alphabetically.
    assert [t.status for t in tasks] == ["open", "spec"]


def test_order_by_created_at(core):
    core.add("d1", kind="design")
    core.add("p1", kind="production", deps={1: "realizes d1"})
    tasks = core.ls(order_by="created_at")
    assert [t.id for t in tasks] == [1, 2]


def test_order_by_kind(core):
    core.add("d1", kind="design")
    core.add("p1", kind="production", deps={1: "realizes d1"})
    kinds = [t.kind for t in core.ls(order_by="kind")]
    assert kinds == sorted(kinds)


def test_order_by_degree_surfaces_hubs_first(core):
    core.add("anchor", kind="design")                          # id1
    core.add("hub", kind="production", deps={1: "realizes anchor"})  # id2
    core.add("a", kind="production", deps={1: "realizes anchor"})    # id3
    core.add("b", kind="production", deps={1: "realizes anchor"})    # id4
    core.link_add(2, 3, because="hub links a")
    core.link_add(2, 4, because="hub links b")
    tasks = core.ls(order_by="degree")
    degrees = []
    for t in tasks:
        d = core.conn.execute(
            "SELECT COUNT(*) FROM links WHERE task_a = ? OR task_b = ?",
            (t.id, t.id),
        ).fetchone()[0]
        degrees.append(d)
    # degree is the descending sort key.
    assert degrees == sorted(degrees, reverse=True)


def test_order_by_with_filter(core):
    core.add("d1", kind="design")
    core.add("p1", kind="production", deps={1: "realizes d1"})
    tasks = core.ls(kind="production", order_by="id")
    assert [t.id for t in tasks] == [2]


# --- the firewall: no body-derived / arbitrary order_by (converse) ----------

def test_order_by_rejects_body_column_description(core):
    with pytest.raises(ValidationError, match="order_by"):
        core.ls(order_by="description")


def test_order_by_rejects_name_column(core):
    # `name` is content, not a structured triage column -- refused (D283).
    with pytest.raises(ValidationError, match="order_by"):
        core.ls(order_by="name")


def test_order_by_rejects_arbitrary_sql(core):
    with pytest.raises(ValidationError, match="order_by"):
        core.ls(order_by="id; DROP TABLE tasks")
