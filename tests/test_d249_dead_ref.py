"""D249 — dead-reference soft-suggest on retire / rename / reclassify.

When a task's id stops naming a live referent (retire; an edit that changes the
§-name-convention id in its name; reclassify out of design/schema), tackit
searches its LINKED neighbors' bodies for the now-dead id -- both the synthetic
prefixed-name (D#/S#/T#/M#) and the §-name-convention id -- and emits a soft
repoint/rationalize suggestion. Detection-only: advisory, no hard gate, and the
message never says "delete"/"remove" (the lazy-delete guard).

test-audit: trigger x id-kind (retire/rename/reclassify x §-id/synthetic);
boundary (§9.4 != §9.4.2); negative (neighbor doesn't cite -> silent); and no
delete/remove language.
"""

import pytest

from tackit.errors import ValidationError


def _joined(core):
    return " ".join(core.last_deadref_suggestions or [])


# --- retire ----------------------------------------------------------------


def test_retire_emits_for_section_ref(core):
    core.add("§9.4 — foo", kind="design", description="the decision")  # D1
    core.add("§9.5 — bar", kind="design", description="see §9.4 for the contract")  # D2
    core.link_add(1, 2, because="D2's contract depends on D1's decision")
    core.retire(1, reason="fully replaced by a new premise, no migration", delta="retire")
    s = _joined(core)
    assert "§9.4" in s and "D2" in s
    assert "delete" not in s.lower() and "remove" not in s.lower()
    assert "repoint" in s.lower() or "rationalize" in s.lower()


def test_retire_emits_for_synthetic_id(core):
    core.add("a decision", kind="design", description="body")  # D1
    core.add("dependent", kind="design", description="as decided in D1, we do X")  # D2
    core.link_add(1, 2, because="D2 realizes D1")
    core.retire(1, reason="premise fully dropped, no replacement", delta="retire")
    assert "D1" in _joined(core)


def test_retire_silent_when_neighbor_does_not_cite(core):
    core.add("§9.4 — foo", kind="design", description="the decision")  # D1
    core.add("§9.5 — bar", kind="design", description="an unrelated body")  # D2
    core.link_add(1, 2, because="loosely coupled")
    core.retire(1, reason="premise dropped, nothing replaces it", delta="retire")
    assert core.last_deadref_suggestions is None


def test_retire_section_ref_boundary_no_false_match(core):
    # a neighbor citing §9.4.2 must NOT be flagged when §9.4 is retired
    core.add("§9.4 — foo", kind="design", description="the decision")  # D1
    core.add("§9.5 — bar", kind="design", description="see §9.4.2 for the sub-contract")  # D2
    core.link_add(1, 2, because="coupled on the sub-contract")
    core.retire(1, reason="premise dropped with no replacement", delta="retire")
    assert core.last_deadref_suggestions is None


# --- rename (edit changing the name's §-id) --------------------------------


def test_rename_emits_with_successor(core):
    core.add("§9.4 — foo", kind="design", description="the decision")  # D1
    core.add("dep", kind="design", description="see §9.4 for the contract")  # D2
    core.link_add(1, 2, because="D2 depends on D1's contract")
    core.edit(1, delta="renumber §9.4 -> §9.8", name="§9.8 — foo")
    s = _joined(core)
    assert "§9.4" in s  # the dead id
    assert "§9.8" in s  # the successor named
    assert "delete" not in s.lower() and "remove" not in s.lower()


def test_body_only_edit_does_not_emit(core):
    core.add("§9.4 — foo", kind="design", description="the decision")  # D1
    core.add("dep", kind="design", description="see §9.4")  # D2
    core.link_add(1, 2, because="coupled")
    core.edit(1, delta="clarify the body", description="the decision, clarified")
    assert core.last_deadref_suggestions is None


# --- reclassify out of design/schema ---------------------------------------


def test_reclassify_out_of_spec_emits_for_synthetic_id(core):
    core.add("a decision", kind="design", description="body")  # D1
    core.add("dep", kind="design", description="realizes D1 fully")  # D2
    core.link_add(1, 2, because="D2 realizes D1")
    core.reclassify(1, new_kind="production", delta="was really work, not a decision")
    s = _joined(core)
    assert "D1" in s  # old synthetic id now dead
    assert "T1" in s  # successor named


def test_reclassify_within_spec_partition_does_not_emit(core):
    # design -> schema stays in the spec partition; the D#->S# prefix changes,
    # but v1 scopes emission to leaving design/schema entirely.
    core.add("a decision", kind="design", description="body")  # D1
    core.add("dep", kind="schema", description="references D1")  # S2
    core.link_add(1, 2, because="coupled")
    core.reclassify(1, new_kind="schema", delta="was really a store-shape slice")
    assert core.last_deadref_suggestions is None
