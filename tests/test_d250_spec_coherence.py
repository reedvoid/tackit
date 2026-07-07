"""D250 — a spec slice is a coherent current-state body.

Two enforcement surfaces exercised here:
  * STRUCTURAL: edit_append is refused on design/schema kinds (append is for
    chronological production/meta logs); edit / edit_replace_substring, which
    engage the body, remain the spec-edit path.
  * REAL-TIME FEEDBACK: after an edit / edit_replace_substring on a
    design/schema slice, a coherence_nudge is surfaced when the resulting body
    reads as an append/changelog (stacked dates / SUPERSEDES-style markers) or
    carries a dangling reference (a token deleted leaving a fragment). Advisory
    only -- never blocks the edit.

test-audit: refusal x kind matrix; nudge fire/silent x trigger x kind; and no
"delete"/"remove" language in any surfaced message (the lazy-delete guard).
"""

import pytest

from tackit.errors import ValidationError


# --- STRUCTURAL: edit_append refusal on spec kinds --------------------------


@pytest.mark.parametrize("kind", ["design", "schema"])
def test_edit_append_refused_on_spec_kind(core, kind):
    core.add("s", kind=kind, description="body")
    with pytest.raises(ValidationError, match="edit_append refused"):
        core.edit_append(1, content=" more", delta="d")


@pytest.mark.parametrize("kind", ["production", "meta"])
def test_edit_append_allowed_on_non_spec_kind(core, kind):
    core.add("t", kind=kind, description="body")
    core.edit_append(1, content=" + note", delta="d")
    assert core.get(1).description == "body + note"


def test_edit_append_refusal_message_has_no_delete_language(core):
    core.add("s", kind="design", description="body")
    with pytest.raises(ValidationError) as exc:
        core.edit_append(1, content=" more", delta="d")
    msg = str(exc.value).lower()
    assert "delete" not in msg and "remove" not in msg
    assert "d250" in msg


# --- REAL-TIME FEEDBACK: coherence_nudge on spec edits ----------------------


def test_nudge_fires_on_changelog_markers(core):
    core.add("s", kind="design", description="clean body")
    core.edit(1, delta="d", description="new decision. SUPERSEDES the old one above.")
    assert core.last_coherence_nudge is not None
    assert "D250" in core.last_coherence_nudge


def test_nudge_fires_on_multiple_dated_blocks(core):
    core.add("s", kind="design", description="clean")
    core.edit(1, delta="d", description="v1 on 2026-01-01 then changed on 2026-02-02.")
    assert core.last_coherence_nudge is not None


def test_nudge_fires_on_dangling_reference(core):
    core.add("s", kind="design", description="clean")
    core.edit(1, delta="d", description="sections are instantiated from .")
    assert core.last_coherence_nudge is not None


def test_nudge_fires_on_bare_section_marker(core):
    core.add("s", kind="design", description="clean")
    core.edit(1, delta="d", description="see the section at § for details.")
    assert core.last_coherence_nudge is not None


def test_no_nudge_on_clean_rewrite(core):
    core.add("s", kind="design", description="old")
    core.edit(
        1,
        delta="d",
        description="Sections load positionally from each slice's own asset folder.",
    )
    assert core.last_coherence_nudge is None


def test_nudge_fires_via_edit_replace_substring(core):
    core.add("s", kind="design", description="the body mentions xyz")
    core.edit_replace_substring(
        1, old_string="xyz", new_string="a change SUPERSEDES prior text above", delta="d"
    )
    assert core.last_coherence_nudge is not None


def test_no_nudge_on_production_edit(core):
    # production tasks legitimately hold chronological logs -> no nudge
    core.add("t", kind="production", description="impl")
    core.edit(
        1,
        delta="d",
        description="phase 1 2026-01-01; phase 2 2026-02-02; SUPERSEDES above.",
    )
    assert core.last_coherence_nudge is None


def test_nudge_reset_on_next_clean_edit(core):
    core.add("s", kind="design", description="clean")
    core.edit(1, delta="d", description="SUPERSEDES old text above.")
    assert core.last_coherence_nudge is not None
    core.edit(1, delta="d2", description="One coherent current-state sentence.")
    assert core.last_coherence_nudge is None


def test_nudge_message_has_no_delete_language(core):
    core.add("s", kind="design", description="clean")
    core.edit(1, delta="d", description="SUPERSEDES the old on 2026-01-01 and 2026-02-02.")
    msg = core.last_coherence_nudge.lower()
    assert "delete" not in msg and "remove" not in msg
