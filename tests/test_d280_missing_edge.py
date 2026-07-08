"""D280 — missing-edge soft-suggest (the inverse of D249).

When a just-edited task's body names a LIVE, non-retired task by synthetic id
(D#/S#/T#/M# or #id) that is NOT already a linked neighbor, tackit emits an
advisory "you might be missing a link" suggestion. Synthetic-id ONLY -- §-refs
are not scanned (empirically zero recall + noise; D280 / M279). Advisory, never
a gate.

test-audit coverage:
- fires: a slice's body names an unlinked live task (edit + edit_replace + #id).
- silent (degenerate/negative): already-linked, no-mention, self-mention, no-op.
- guards: meta-island straddle (both directions), retired target, superseded /
  contrastive context; meta<->meta still fires.
- boundary: D1 must not match D12 / D1x / S5.2 (unit-level _body_id_refs).
"""

from tackit.core import _body_id_refs


def _joined(core):
    return " ".join(core.last_missing_edge_suggestions or [])


# --- fires -----------------------------------------------------------------


def test_edit_naming_unlinked_task_emits(core):
    core.add("anchor decision", kind="design", description="body")  # D1
    core.add("another decision", kind="design", description="body")  # D2
    core.edit(2, delta="record realization", description="this decision follows from D1's contract")
    s = _joined(core)
    assert "D1" in s and "D2" in s
    assert "link" in s.lower()


def test_edit_replace_path_also_emits(core):
    core.add("anchor", kind="design", description="body")  # D1
    core.add("dep", kind="design", description="placeholder text here")  # D2
    core.edit_replace_substring(
        2, old_string="placeholder text here", new_string="derived from D1", delta="cite D1"
    )
    assert "D1" in _joined(core)


def test_hash_ref_resolves_and_emits(core):
    core.add("anchor", kind="design", description="body")  # D1
    core.add("dep", kind="design", description="body")  # D2
    core.edit(2, delta="cite by hash", description="see #1 for the rationale")
    assert "D1" in _joined(core)  # #1 resolves to the design task 1


def test_meta_to_meta_emits(core):
    core.add("note one", kind="meta", description="body")  # M1
    core.add("note two", kind="meta", description="body")  # M2
    core.edit(2, delta="cross-ref sibling note", description="continues from M1's thread")
    assert "M1" in _joined(core)  # meta<->meta is a legal link, so it fires


# --- silent: degenerate / negative -----------------------------------------


def test_already_linked_is_silent(core):
    core.add("anchor", kind="design", description="body")  # D1
    core.add("dep", kind="design", description="body")  # D2
    core.link_add(1, 2, because="D2 realizes D1's decision")
    core.edit(2, delta="name the linked anchor", description="this follows from D1")
    assert core.last_missing_edge_suggestions is None


def test_no_mention_is_silent(core):
    core.add("anchor", kind="design", description="body")  # D1
    core.add("dep", kind="design", description="body")  # D2
    core.edit(2, delta="unrelated edit", description="a body with no id references at all")
    assert core.last_missing_edge_suggestions is None


def test_self_mention_is_silent(core):
    core.add("anchor", kind="design", description="body")  # D1
    core.edit(1, delta="self reference", description="this is D1 itself, naming its own id")
    assert core.last_missing_edge_suggestions is None


def test_noop_edit_does_not_emit(core):
    core.add("anchor", kind="design", description="body")  # D1
    core.add("dep", kind="design", description="mentions D1 already")  # D2
    core.last_missing_edge_suggestions = None
    core.edit(2, delta="noop", description="mentions D1 already")  # same body -> no-op
    assert core.last_missing_edge_suggestions is None


# --- guards ----------------------------------------------------------------


def test_meta_island_straddle_suppressed(core):
    core.add("a note", kind="meta", description="body")  # M1
    core.add("a decision", kind="design", description="body")  # D2
    core.edit(2, delta="mention the meta note", description="see M1 for the exploration")
    # design<->meta straddles the island -> that edge is forbidden, never suggest
    assert core.last_missing_edge_suggestions is None


def test_meta_naming_nonmeta_suppressed(core):
    core.add("a decision", kind="design", description="body")  # D1
    core.add("a note", kind="meta", description="body")  # M2
    core.edit(2, delta="note tracks the decision", description="tracking D1's rollout here")
    assert core.last_missing_edge_suggestions is None


def test_retired_target_not_suggested(core):
    core.add("dead decision", kind="design", description="body")  # D1
    core.add("dep", kind="design", description="body")  # D2
    core.retire(1, reason="premise fully gone, no replacement", delta="retire")
    core.edit(2, delta="build on the old one", description="this continues D1's line of work")
    # D1 is retired -> no new edge to a dead decision (D36); no suggestion
    assert core.last_missing_edge_suggestions is None


def test_superseded_context_suppressed(core):
    core.add("old decision", kind="design", description="body")  # D1
    core.add("new decision", kind="design", description="body")  # D2
    core.edit(2, delta="record the supersession", description="the D1 approach was dropped in favor of this")
    # "dropped" near the D1 mention -> contrastive, not a live coupling
    assert core.last_missing_edge_suggestions is None


def test_superseded_marker_after_the_id_is_caught(core):
    core.add("old", kind="design", description="body")  # D1
    core.add("new", kind="design", description="body")  # D2
    core.edit(2, delta="contrast", description="unlike D1, this takes the orthogonal route")
    assert core.last_missing_edge_suggestions is None


# --- boundary (unit-level extraction) --------------------------------------


def test_body_id_refs_extracts_all_forms():
    assert _body_id_refs("see D1, S30, T12, M4, and #3") == {
        ("D", 1),
        ("S", 30),
        ("T", 12),
        ("M", 4),
        ("", 3),
    }


def test_body_id_refs_boundary_guards():
    assert _body_id_refs("D1x") == set()      # trailing word char
    assert _body_id_refs("xD1") == set()      # leading word char
    assert _body_id_refs("S5.2") == set()     # trailing dot (§-style / decimal)
    assert _body_id_refs("D123") == {("D", 123)}   # not D1 + '23'
    assert _body_id_refs("") == set()
