"""D259 (terminal-immutability) + D255 (append is meta-only) edit-path guards.

D259 — a CLOSED or WONT_DO task is a frozen record: edit / edit_append /
edit_replace_substring are refused (reopen a closed task to change it; a
wont_do task is terminal forever). RETIRED design/schema slices are EXEMPT —
edit-on-retired stays for annotating a dead decision (D29 v0.5).

D255 — append is a META-ONLY op: edit_append is refused on design/schema
(D250) and on production; full-rewrite edit() and edit_replace_substring stay
allowed on production (for correction / scope-shrink).

Driven by the test-audit four-pass: the refusal path AND the still-allowed
path for every rule, plus the degenerate "no audit row on a refused edit" and
the negative-space retired-exemption.
"""

import pytest

from tackit.errors import ValidationError


# --- helpers ---------------------------------------------------------------


def _prod_linked(core, name="impl", desc="body"):
    """Create a production task linked to a design anchor (the shape the D256
    creation-gate will require) and return its id."""
    core.add("anchor decision", kind="design", description="a decision")
    core.add(name, kind="production", description=desc, deps={1: "realizes the anchor"})
    return 2


# =========================================================================
#  D259 — edit refused on terminal (closed / wont_do)
# =========================================================================


def test_edit_refused_on_closed_production(core):
    tid = _prod_linked(core, desc="original")
    core.close(tid)
    with pytest.raises(ValidationError, match="frozen record"):
        core.edit(tid, description="sneaky post-close edit", delta="tried it")
    assert core.get(tid).description == "original"
    assert core.history(tid).description_revisions == []  # no audit row


def test_edit_refused_on_wont_do_says_terminal_forever(core):
    tid = _prod_linked(core, desc="original")
    core.wont_do(tid, reason="dropped")
    with pytest.raises(ValidationError, match="terminal forever"):
        core.edit(tid, description="post-drop edit", delta="tried it")
    assert core.get(tid).description == "original"


def test_edit_replace_substring_refused_on_wont_do(core):
    tid = _prod_linked(core, desc="hello world")
    core.wont_do(tid, reason="dropped")
    with pytest.raises(ValidationError, match="frozen record"):
        core.edit_replace_substring(
            tid, old_string="world", new_string="planet", delta="tried it"
        )


def test_edit_refused_on_closed_meta(core):
    core.add("note", kind="meta", description="scratch")
    core.close(1)
    with pytest.raises(ValidationError, match="frozen record"):
        core.edit(1, description="post-close", delta="tried it")


def test_reopen_then_edit_then_close_round_trips_for_closed(core):
    tid = _prod_linked(core, desc="v1")
    core.close(tid)
    core.reopen(tid)
    core.edit(tid, description="v2 corrected", delta="fixed a mis-forecast detail")
    # editing the prod body cascaded to its linked design anchor; reconcile it
    # so the close-gate (no closing atop a stale dependency) is satisfied.
    core.reconcile(1)
    core.close(tid)
    assert core.get(tid).description == "v2 corrected"
    assert core.get(tid).status == "closed"


# --- D259 negative space: RETIRED slices stay editable (the exemption) ------


def test_edit_still_allowed_on_retired_design_slice(core):
    """D259 exempts retired slices: edit-on-retired stays for annotating a dead
    decision (D29 v0.5), and it still writes an audit row + fires D31."""
    core.add("d", kind="design", description="a decision")
    core.retire(1, reason="fully superseded by D2, no migration")
    core.edit(1, description="a decision (retired — superseded by D2)", delta="annotate dead decision")
    assert core.get(1).status == "retired"
    assert "retired" in core.get(1).description
    assert len(core.history(1).description_revisions) == 1


def test_edit_replace_substring_still_allowed_on_retired_slice(core):
    core.add("s", kind="schema", description="CREATE TABLE foo (...)")
    core.retire(1, reason="table dropped, no replacement")
    core.edit_replace_substring(
        1, old_string="foo", new_string="foo (DROPPED)", delta="mark dead table"
    )
    assert core.get(1).description == "CREATE TABLE foo (DROPPED) (...)"
    assert core.get(1).status == "retired"


# =========================================================================
#  D255 — append is META-ONLY
# =========================================================================


def test_edit_append_allowed_on_meta(core):
    core.add("note", kind="meta", description="log")
    core.edit_append(1, content=" + entry", delta="log entry")
    assert core.get(1).description == "log + entry"


def test_edit_append_refused_on_schema(core):
    core.add("s", kind="schema", description="CREATE TABLE t (...)")
    with pytest.raises(ValidationError, match="META-ONLY"):
        core.edit_append(1, content=" -- note", delta="tried append")


def test_edit_append_refused_on_production_points_at_edit(core):
    tid = _prod_linked(core)
    with pytest.raises(ValidationError, match="META-ONLY"):
        core.edit_append(tid, content=" + progress", delta="tried append")


# --- D255 negative space: production REWRITES stay allowed ------------------


def test_edit_full_rewrite_allowed_on_open_production(core):
    """The append-ban does not freeze production — a full-rewrite edit() (for
    correction / scope-shrink) stays allowed while open."""
    tid = _prod_linked(core, desc="wrong forecast of the code path")
    core.edit(tid, description="corrected forecast", delta="fixed mis-predicted impl detail")
    assert core.get(tid).description == "corrected forecast"


def test_edit_replace_substring_allowed_on_open_production(core):
    tid = _prod_linked(core, desc="touch file_a.py")
    core.edit_replace_substring(
        tid, old_string="file_a.py", new_string="file_b.py", delta="corrected the target file"
    )
    assert core.get(tid).description == "touch file_b.py"
