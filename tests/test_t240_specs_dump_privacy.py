"""T240 - `export --specs-only` must NOT dump the description_revisions audit
trail.

The spec dump (examples/specs.sql) is committed + pushed (public). It dumped
each spec slice's description_revisions rows, which preserve the verbatim PRIOR
text of every edit. So text written into a slice draft and later edited out
still leaked via the audit row, even though the current slice body was clean
(it caused a real near-leak of a private project name into the public dump).

The recovery artifact only needs CURRENT spec state (slice bodies + labels +
links); the drafting history stays in the private gitignored DB. status_
transitions are kept (status enums + timestamps, no free text to leak).
"""

from tackit.core import Core


def test_specs_only_excludes_description_revisions(core):
    d = core.add("a decision", kind="design", description="draft with SECRET-MARKER")
    core.edit(d.id, description="clean final text", delta="removed the marker")

    dump = core.export_specs_only()

    # the CURRENT slice body is still dumped (recovery works)...
    assert "INSERT INTO tasks" in dump
    assert "clean final text" in dump
    # ...but the audit trail is NOT, so edited-out text cannot leak.
    assert "INSERT INTO description_revisions" not in dump
    assert "SECRET-MARKER" not in dump


def test_specs_only_still_keeps_current_state_tables(core):
    d = core.add("a decision", kind="design", description="body")
    core.label_add(d.id, "demo")
    s = core.add("a schema slice", kind="schema", description="ddl")
    core.link_add(d.id, s.id, because="d relates to s")

    dump = core.export_specs_only()
    # current-state tables remain (tasks, labels, links, status_transitions)
    assert "INSERT INTO tasks" in dump
    assert "INSERT INTO task_labels" in dump
    assert "INSERT INTO links" in dump
    assert "INSERT INTO status_transitions" in dump


def test_committed_spec_dump_has_no_stale_terminal_row():
    """D137: only live tasks carry the stale flag, and the committed dump is
    regenerated after migration 11, so no retired row in it is flagged. A
    spec-only dump has no schema version, so a flag left in it would come back
    on every hand-load and never clear. Non-vacuous: the dump must hold
    retired rows to check."""
    import sqlite3
    from pathlib import Path

    from tackit import schema

    dump = Path(__file__).resolve().parent.parent / "examples" / "specs.sql"
    conn = sqlite3.connect(":memory:")
    try:
        for ddl in schema.ALL_DDL:  # the dump is INSERTs only
            conn.executescript(ddl)
        conn.executescript(dump.read_text(encoding="utf-8"))
        retired = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = 'retired'"
        ).fetchone()[0]
        assert retired > 0, "the committed dump holds no retired row to check"
        flagged = conn.execute(
            "SELECT id, status FROM tasks WHERE stale = 1 "
            "AND status IN ('closed', 'wont_do', 'retired') ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert flagged == []
