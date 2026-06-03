"""T193 / M187 - spec-only export tests.

`Core.export_specs_only()` emits an SQL dump of just the spec layer
(design + schema tasks + their labels + spec-to-spec links + audit rows).
The dump must be:

- ROUND-TRIPPABLE: applying it to a freshly initialized store rebuilds
  the spec layer bit-equal to the source.
- FILTERED: production / meta task rows, cross-partition links, the FTS
  index, and the meta table must NOT be present in the dump.
- WELL-FORMED SQL: a BEGIN/COMMIT transaction with valid INSERT
  statements that survive `executescript()` against a fresh store.

The empty-corpus case (no specs in the source) emits just BEGIN/COMMIT
and is a no-op on import.
"""

import pytest

from tackit.core import Core
from tackit.db import init_store


def _fresh_store(tmp_path) -> Core:
    """Open a brand-new Core in tmp_path/sub/ (init_store creates the
    schema)."""
    sub = tmp_path / "sub"
    sub.mkdir()
    init_store(sub)
    return Core.open(start=sub)


def _apply_dump(target: Core, sql: str) -> None:
    """Apply the SQL dump to target's connection via executescript()."""
    target.conn.executescript(sql)


def _spec_snapshot(core: Core) -> dict:
    """Return a serializable snapshot of a Core's spec layer for equality
    comparison: spec rows (kind in design,schema) + their labels + spec-to-
    spec links + their audit rows. Excludes derived (FTS) and meta."""
    spec_ids = []
    rows = core.conn.execute(
        "SELECT * FROM tasks WHERE kind IN ('design','schema') ORDER BY id"
    ).fetchall()
    tasks = []
    for r in rows:
        tasks.append(dict(r))
        spec_ids.append(r["id"])

    labels = []
    links = []
    transitions = []
    revisions = []
    if spec_ids:
        ph = ",".join("?" * len(spec_ids))
        for r in core.conn.execute(
            f"SELECT * FROM task_labels WHERE task_id IN ({ph}) "
            f"ORDER BY task_id, label",
            spec_ids,
        ).fetchall():
            labels.append(dict(r))
        for r in core.conn.execute(
            f"SELECT * FROM links WHERE task_a IN ({ph}) AND task_b IN ({ph}) "
            f"ORDER BY id",
            spec_ids + spec_ids,
        ).fetchall():
            links.append(dict(r))
        for r in core.conn.execute(
            f"SELECT * FROM status_transitions WHERE task_id IN ({ph}) "
            f"ORDER BY id",
            spec_ids,
        ).fetchall():
            transitions.append(dict(r))
        for r in core.conn.execute(
            f"SELECT * FROM description_revisions WHERE task_id IN ({ph}) "
            f"ORDER BY id",
            spec_ids,
        ).fetchall():
            revisions.append(dict(r))

    return {
        "tasks": tasks,
        "labels": labels,
        "links": links,
        "transitions": transitions,
        "revisions": revisions,
    }


def test_export_specs_only_round_trip(core, tmp_path):
    """T193 happy path: source has spec rows + non-spec rows; export
    specs-only; apply to a fresh store; spec layer is bit-equal,
    production / meta rows are absent in the target.
    """
    # Set up source: 2 specs + 1 production + 1 meta.
    core.add("design slice A", kind="design", description="design body A",
             labels=["arch"])
    core.add("schema slice B", kind="schema", description="schema body B")
    core.add("production task C", kind="production",
             description="prod body C", labels=["arch"])
    core.add("meta task D", kind="meta", description="meta body D")
    # Wire a spec-to-spec link and a spec-to-production link.
    core.link_add(1, 2, because="design + schema couple here",
                  delta="seed link")
    core.link_add(1, 3, because="spec realized by production task",
                  delta="seed cross-partition link")
    # Edit a spec to populate description_revisions.
    core.edit(1, delta="post-seed edit to populate audit",
              description="design body A v2")

    source_snapshot = _spec_snapshot(core)
    assert len(source_snapshot["tasks"]) == 2
    # Spec-to-spec link present; spec-production link present in source.
    assert len(source_snapshot["links"]) == 1, (
        "source spec-spec links should be exactly 1 (the design-schema "
        f"coupling); got: {source_snapshot['links']!r}"
    )
    assert len(source_snapshot["revisions"]) == 1, (
        "source should have 1 description_revision from the edit"
    )

    # Export.
    sql = core.export_specs_only()
    assert sql.startswith("BEGIN TRANSACTION;\n")
    assert sql.endswith("COMMIT;\n")
    # Sanity: production and meta task names must NOT be in the dump text.
    assert "production task C" not in sql, (
        "production rows must NOT appear in the spec-only dump"
    )
    assert "meta task D" not in sql, (
        "meta rows must NOT appear in the spec-only dump"
    )
    # And the cross-partition link's because phrase must not appear.
    assert "spec realized by production task" not in sql, (
        "cross-partition links must NOT appear in the spec-only dump"
    )

    # Apply to a fresh store.
    target = _fresh_store(tmp_path)
    try:
        _apply_dump(target, sql)
        target_snapshot = _spec_snapshot(target)
    finally:
        target.close_conn()

    # Spec layer is bit-equal.
    assert target_snapshot == source_snapshot, (
        f"round-trip drift: source={source_snapshot!r} vs "
        f"target={target_snapshot!r}"
    )


def test_export_specs_only_empty_corpus(core, tmp_path):
    """An empty source (no spec rows) emits just BEGIN/COMMIT and is a
    no-op on import. Catches the placeholder-list edge case (sqlite3
    refuses an empty IN clause)."""
    sql = core.export_specs_only()
    assert sql == "BEGIN TRANSACTION;\nCOMMIT;\n", (
        f"empty corpus must produce a no-op transaction; got: {sql!r}"
    )
    # Applying to a fresh store does nothing.
    target = _fresh_store(tmp_path)
    try:
        _apply_dump(target, sql)
        snap = _spec_snapshot(target)
    finally:
        target.close_conn()
    assert snap == {
        "tasks": [], "labels": [], "links": [],
        "transitions": [], "revisions": [],
    }


def test_export_specs_only_excludes_retired_audit_only_of_non_specs(core, tmp_path):
    """description_revisions on production tasks must NOT appear in the
    dump even when the production task has been edited (the audit row
    exists in the source). This pins the task_id filter on the
    description_revisions query."""
    core.add("design slice", kind="design", description="design body")
    core.add("production task", kind="production", description="prod body")
    # Edit BOTH to generate revisions on both.
    core.edit(1, delta="spec edit", description="design body v2")
    core.edit(2, delta="prod edit", description="prod body v2")

    sql = core.export_specs_only()
    # The production task's description revision text must not appear.
    assert "prod body" not in sql, (
        "production task description must not leak into the spec-only "
        "dump via description_revisions"
    )

    # Apply to fresh store and verify target has only the spec's revision.
    target = _fresh_store(tmp_path)
    try:
        _apply_dump(target, sql)
        snap = _spec_snapshot(target)
    finally:
        target.close_conn()
    assert len(snap["revisions"]) == 1, (
        f"target should have exactly 1 revision (the spec edit); got: "
        f"{snap['revisions']!r}"
    )
    assert snap["revisions"][0]["task_id"] == 1


def test_cli_export_specs_only_to_stdout(tmp_path, monkeypatch, capsys):
    """End-to-end CLI: `tackit export --specs-only` emits the SQL dump
    to stdout. Smoke test — no round-trip, just verify the path works
    and the envelope wrapping is absent (stdout SQL should be raw, not
    wrapped in an _emit envelope)."""
    from tackit.cli import main
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert main(["add", "--kind", "design", "--desc", "body", "alpha"]) == 0
    capsys.readouterr()  # drain
    assert main(["export", "--specs-only"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("BEGIN TRANSACTION;\n"), (
        f"`tackit export --specs-only` stdout must start with BEGIN "
        f"TRANSACTION; got: {out[:80]!r}"
    )
    assert "alpha" in out
    assert out.rstrip().endswith("COMMIT;"), (
        f"stdout must end with COMMIT;; got tail: {out[-80:]!r}"
    )
