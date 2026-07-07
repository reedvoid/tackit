"""T179 — edit_append + edit_replace_substring diff-edit ops.

The current ``Core.edit`` takes the full new description. For a large body
the round-trip cost is the full body in *and* out -- ~30k tokens on a 54k
description, per fold-back. These two diff-shaped ops cut that ~10x:

* ``edit_append(id, content, delta)`` -- append to the description.
* ``edit_replace_substring(id, old_string, new_string, delta)`` -- replace
  the exact ``old_string`` substring (must be unique -- non-unique matches
  refused loudly, mirroring the filesystem Edit tool's old/new pattern).

Both fire the cascade depth-1 like ``edit()`` and write a description_revisions
audit row preserving the prior verbatim name+description+delta. Both share the
underlying mutation block so cascade + audit + version-bump + D31 reminder
logic stays in one place.

Tests are driven by the test-audit four-pass (skill: test-audit):
  Pass 1 -- invariant x code x test matrix (cascade, audit, no-op, code-check,
            delta-required, edit-on-any-status, NUL/surrogate).
  Pass 2 -- degenerate inputs (empty, whitespace, missing, multi-match, deletion,
            no-op, boundary positions, nonexistent id).
  Pass 3 -- negative space (multi-line old, regex metachars, recursive replace,
            empty description, unicode).
  Pass 4 -- property-based (append-suffix invariant, replace-once invariant).
"""

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from tackit.errors import InvariantError, NotFoundError, ValidationError


# =========================================================================
#  edit_append -- Pass 1 invariants
# =========================================================================


def test_edit_append_appends_to_description(core):
    core.add("a", kind="production", description="original")
    core.edit_append(1, content=" + more", delta="extended scope note")
    assert core.get(1).description == "original + more"


def test_edit_append_returns_change_result_with_full_task(core):
    core.add("a", kind="production", description="x")
    result = core.edit_append(1, content="y", delta="seed")
    assert result.task.id == 1
    assert result.task.description == "xy"
    assert isinstance(result.newly_stale, list)


def test_edit_append_fires_cascade_depth_one(core):
    core.add("a", kind="production", description="orig")  # T1
    core.add("b", kind="production", description="dep")  # T2
    core.link_add(1, 2, because="T2 reviews T1's prose")
    result = core.edit_append(1, content=" + more", delta="extended scope")
    stale_ids = []
    for n in result.newly_stale:
        stale_ids.append(n.id)
    assert 2 in stale_ids
    assert core.get(2).stale is True


def test_edit_append_writes_description_revisions_row(core):
    core.add("a", kind="production", description="original body")
    core.edit_append(1, content=" + appended", delta="seed delta")
    revs = core.history(1).description_revisions
    assert len(revs) == 1
    assert revs[0].prev_description == "original body"
    assert revs[0].prev_name == "a"
    assert revs[0].delta == "seed delta"


def test_edit_append_bumps_updated_at(core):
    core.add("a", kind="production", description="x")
    before = core.get(1).updated_at
    core.edit_append(1, content="y", delta="seed")
    after = core.get(1).updated_at
    assert after >= before  # ISO timestamps; >= because clock resolution


def test_edit_append_on_closed_task_succeeds(core):
    """D29: edit allowed on any status. closed neighbors flagged record-only."""
    core.add("a", kind="production", description="x")
    core.close(1)
    core.edit_append(1, content=" + post-close note", delta="prose fix")
    assert core.get(1).description == "x + post-close note"
    assert core.get(1).status == "closed"
    revs = core.history(1).description_revisions
    assert len(revs) == 1


def test_edit_append_refused_on_design_replace_still_works(core):
    """D250: edit_append is refused on design/schema (a spec slice is a
    coherent current-state body, not an append log) -- even on a retired one.
    D36 + D29: the retired row can still be edited via a body-engaging op
    (edit_replace_substring), and the audit table records it."""
    core.add("d", kind="design", description="decision text")  # T1, spec
    core.retire(1, reason="fully replaced by D2 with no migration", delta="retire d")
    with pytest.raises(ValidationError, match="edit_append refused"):
        core.edit_append(1, content=" (clarification)", delta="historical note")
    core.edit_replace_substring(
        1,
        old_string="decision text",
        new_string="decision text (clarified)",
        delta="historical clarification",
    )
    assert core.get(1).description == "decision text (clarified)"
    assert core.get(1).status == "retired"


def test_edit_replace_substring_on_design_fires_code_check_reminder(core):
    """D31: design/schema edits set last_code_check_reminder. (edit_append is
    refused on design per D250, so the diff-op path is edit_replace_substring.)"""
    core.add("d", kind="design", description="decision text")
    core.last_code_check_reminder = None
    core.edit_replace_substring(
        1, old_string="decision text", new_string="decision text v2", delta="prose extension"
    )
    assert core.last_code_check_reminder is not None
    assert "D31" in core.last_code_check_reminder


def test_edit_append_on_production_kind_does_not_set_reminder(core):
    core.add("a", kind="production", description="impl")
    core.last_code_check_reminder = "leftover"
    core.edit_append(1, content=" + note", delta="extend")
    assert core.last_code_check_reminder is None


def test_edit_append_records_last_delta(core):
    core.add("a", kind="production", description="x")
    core.edit_append(1, content="y", delta="shifted X to Y")
    assert core.last_delta == "shifted X to Y"


# =========================================================================
#  edit_append -- Pass 2 degenerate inputs + refusals
# =========================================================================


def test_edit_append_refused_on_empty_content(core):
    core.add("a", kind="production", description="x")
    with pytest.raises(ValidationError, match="content"):
        core.edit_append(1, content="", delta="seed")


def test_edit_append_refused_on_whitespace_only_content(core):
    core.add("a", kind="production", description="x")
    with pytest.raises(ValidationError, match="content"):
        core.edit_append(1, content="   \n\t  ", delta="seed")


def test_edit_append_refused_on_empty_delta(core):
    core.add("a", kind="production", description="x")
    with pytest.raises(ValidationError, match="delta"):
        core.edit_append(1, content="y", delta="")


def test_edit_append_refused_on_whitespace_delta(core):
    core.add("a", kind="production", description="x")
    with pytest.raises(ValidationError, match="delta"):
        core.edit_append(1, content="y", delta="   ")


def test_edit_append_refused_on_nul_byte_in_content(core):
    core.add("a", kind="production", description="x")
    with pytest.raises(ValidationError, match="NUL"):
        core.edit_append(1, content="y\x00z", delta="seed")


def test_edit_append_refused_on_nonexistent_task(core):
    with pytest.raises(NotFoundError):
        core.edit_append(999, content="y", delta="seed")


def test_edit_append_on_task_with_empty_description_sets_it(core):
    core.add("a", kind="production", description="")
    core.edit_append(1, content="first body", delta="initial fill")
    assert core.get(1).description == "first body"


# =========================================================================
#  edit_replace_substring -- Pass 1 invariants
# =========================================================================


def test_edit_replace_substring_replaces_exact_match(core):
    core.add("a", kind="production", description="hello world")
    core.edit_replace_substring(
        1, old_string="world", new_string="universe", delta="renamed token"
    )
    assert core.get(1).description == "hello universe"


def test_edit_replace_substring_returns_change_result(core):
    core.add("a", kind="production", description="abc")
    result = core.edit_replace_substring(1, old_string="b", new_string="B", delta="seed")
    assert result.task.description == "aBc"
    assert isinstance(result.newly_stale, list)


def test_edit_replace_substring_fires_cascade(core):
    core.add("a", kind="production", description="hello world")  # T1
    core.add("b", kind="production")  # T2
    core.link_add(1, 2, because="T2 reads T1's wording")
    result = core.edit_replace_substring(
        1, old_string="world", new_string="universe", delta="renamed token"
    )
    stale_ids = []
    for n in result.newly_stale:
        stale_ids.append(n.id)
    assert 2 in stale_ids


def test_edit_replace_substring_writes_description_revisions_row(core):
    core.add("a", kind="production", description="hello world")
    core.edit_replace_substring(
        1, old_string="world", new_string="universe", delta="renamed token"
    )
    revs = core.history(1).description_revisions
    assert len(revs) == 1
    assert revs[0].prev_description == "hello world"
    assert revs[0].delta == "renamed token"


def test_edit_replace_substring_on_closed_task_succeeds(core):
    core.add("a", kind="production", description="hello world")
    core.close(1)
    core.edit_replace_substring(
        1, old_string="world", new_string="planet", delta="prose fix post-close"
    )
    assert core.get(1).description == "hello planet"
    assert core.get(1).status == "closed"


def test_edit_replace_substring_on_schema_kind_fires_code_check_reminder(core):
    core.add("s", kind="schema", description="CREATE TABLE foo")
    core.last_code_check_reminder = None
    core.edit_replace_substring(
        1, old_string="foo", new_string="bar", delta="renamed schema table"
    )
    assert core.last_code_check_reminder is not None
    assert "D31" in core.last_code_check_reminder


def test_edit_replace_substring_records_last_delta(core):
    core.add("a", kind="production", description="abc")
    core.edit_replace_substring(1, old_string="b", new_string="B", delta="caps b")
    assert core.last_delta == "caps b"


# =========================================================================
#  edit_replace_substring -- Pass 2 degenerate inputs + refusals
# =========================================================================


def test_edit_replace_substring_refused_on_missing_old_string(core):
    core.add("a", kind="production", description="hello world")
    with pytest.raises(ValidationError, match="not found"):
        core.edit_replace_substring(
            1, old_string="xyz", new_string="abc", delta="seed"
        )


def test_edit_replace_substring_refused_on_multiple_matches(core):
    core.add("a", kind="production", description="foo foo foo")
    with pytest.raises(ValidationError, match="3 times"):
        core.edit_replace_substring(
            1, old_string="foo", new_string="bar", delta="seed"
        )


def test_edit_replace_substring_refused_on_empty_old_string(core):
    core.add("a", kind="production", description="hello")
    with pytest.raises(ValidationError, match="old_string"):
        core.edit_replace_substring(
            1, old_string="", new_string="x", delta="seed"
        )


def test_edit_replace_substring_refused_on_empty_delta(core):
    core.add("a", kind="production", description="hello")
    with pytest.raises(ValidationError, match="delta"):
        core.edit_replace_substring(
            1, old_string="hello", new_string="hi", delta=""
        )


def test_edit_replace_substring_refused_on_nul_byte_in_new_string(core):
    core.add("a", kind="production", description="hello")
    with pytest.raises(ValidationError, match="NUL"):
        core.edit_replace_substring(
            1, old_string="hello", new_string="hi\x00there", delta="seed"
        )


def test_edit_replace_substring_refused_on_nonexistent_task(core):
    with pytest.raises(NotFoundError):
        core.edit_replace_substring(
            999, old_string="x", new_string="y", delta="seed"
        )


def test_edit_replace_substring_allows_empty_new_string_as_deletion(core):
    """Empty new_string is a legitimate deletion. The refusal matrix only
    refuses empty old_string -- not empty new_string."""
    core.add("a", kind="production", description="hello WORLD!")
    core.edit_replace_substring(
        1, old_string=" WORLD!", new_string="", delta="dropped exclamation"
    )
    assert core.get(1).description == "hello"


def test_edit_replace_substring_old_equals_new_is_noop(core):
    """No-op (D20): old==new resolves to the same description. No cascade,
    no audit row, no version bump."""
    core.add("a", kind="production", description="hello world")  # T1
    core.add("b", kind="production")  # T2
    core.link_add(1, 2, because="seed")
    core.reconcile(2)
    assert core.get(2).stale is False

    result = core.edit_replace_substring(
        1, old_string="world", new_string="world", delta="noop attempt"
    )
    assert result.newly_stale == []
    assert core.get(2).stale is False  # no cascade fired
    revs = core.history(1).description_revisions
    assert len(revs) == 0  # no audit row written


def test_edit_replace_substring_at_start_of_description(core):
    core.add("a", kind="production", description="START-middle-end")
    core.edit_replace_substring(
        1, old_string="START", new_string="BEGIN", delta="rename token"
    )
    assert core.get(1).description == "BEGIN-middle-end"


def test_edit_replace_substring_at_end_of_description(core):
    core.add("a", kind="production", description="start-middle-END")
    core.edit_replace_substring(
        1, old_string="END", new_string="FINISH", delta="rename token"
    )
    assert core.get(1).description == "start-middle-FINISH"


def test_edit_replace_substring_whole_description(core):
    core.add("a", kind="production", description="entire body")
    core.edit_replace_substring(
        1, old_string="entire body", new_string="new body", delta="wholesale"
    )
    assert core.get(1).description == "new body"


def test_edit_replace_substring_empty_description_refused_as_missing(core):
    """A task with empty description has no substring to match, so the op
    refuses with 'not found' rather than misleadingly accepting."""
    core.add("a", kind="production", description="")
    with pytest.raises(ValidationError, match="not found"):
        core.edit_replace_substring(
            1, old_string="anything", new_string="x", delta="seed"
        )


# =========================================================================
#  edit_replace_substring -- Pass 3 negative space
# =========================================================================


def test_edit_replace_substring_preserves_unrelated_text(core):
    """Exact-match semantics: replacing 'foo' does not touch 'foobar'."""
    core.add("a", kind="production", description="### foo\nfoobar lives here")
    core.edit_replace_substring(
        1, old_string="### foo\n", new_string="### renamed\n", delta="header rename"
    )
    assert core.get(1).description == "### renamed\nfoobar lives here"


def test_edit_replace_substring_with_multiline_old_string(core):
    """Old_string can span multiple lines -- it's a literal substring, not
    regex, so newlines match newlines."""
    desc = "line one\nline two\nline three"
    core.add("a", kind="production", description=desc)
    core.edit_replace_substring(
        1,
        old_string="line one\nline two",
        new_string="line one (revised)\nline two (revised)",
        delta="multi-line rewrite",
    )
    assert core.get(1).description == "line one (revised)\nline two (revised)\nline three"


def test_edit_replace_substring_does_not_interpret_regex_metachars(core):
    """Old_string '.' should match literal '.', not 'any char'."""
    core.add("a", kind="production", description="hello.world and helloXworld")
    core.edit_replace_substring(
        1, old_string="hello.world", new_string="HELLO.WORLD", delta="literal match"
    )
    assert core.get(1).description == "HELLO.WORLD and helloXworld"


def test_edit_replace_substring_replaces_only_first_occurrence_when_multi(core):
    """Multi-match is refused. This documents that we don't 'replace all' --
    enforced by the multi-match refusal in the test above."""
    core.add("a", kind="production", description="foo foo")
    with pytest.raises(ValidationError, match="2 times"):
        core.edit_replace_substring(
            1, old_string="foo", new_string="bar", delta="seed"
        )


def test_edit_replace_substring_new_string_containing_old_replaces_once(core):
    """If new_string contains old_string, the op replaces exactly once;
    str.replace(old, new, 1) semantics, not a recursive sweep."""
    core.add("a", kind="production", description="abc")
    core.edit_replace_substring(
        1, old_string="b", new_string="bb", delta="duplicated token"
    )
    assert core.get(1).description == "abbc"


def test_edit_replace_substring_with_unicode(core):
    core.add("a", kind="production", description="café del sol")
    core.edit_replace_substring(
        1, old_string="café", new_string="résumé", delta="unicode swap"
    )
    assert core.get(1).description == "résumé del sol"


# =========================================================================
#  Pass 4 -- property-based tests
# =========================================================================


@settings(max_examples=40, deadline=None)
@given(
    original=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
        min_size=0,
        max_size=200,
    ),
    suffix=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
        min_size=1,
        max_size=200,
    ),
)
def test_property_edit_append_concatenates(tmp_path_factory, original, suffix):
    """Property: edit_append(id, suffix) makes description = original + suffix.
    Skip whitespace-only suffix (refused by spec) and empty original-after-strip
    cases (refused by add's name policy via initial description -- description
    accepts empty, name doesn't, so original=='' is fine for description)."""
    if not suffix.strip():
        return  # whitespace-only suffix is refused; skip
    from tackit.core import Core
    from tackit.db import init_store

    d = tmp_path_factory.mktemp("prop")
    init_store(d)
    c = Core.open(start=d)
    try:
        c.add("seed", kind="production", description=original)
        c.edit_append(1, content=suffix, delta="property-test append")
        assert c.get(1).description == original + suffix
    finally:
        c.close_conn()


@settings(max_examples=40, deadline=None)
@given(
    prefix=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
        min_size=0,
        max_size=80,
    ),
    middle=st.text(
        alphabet=st.characters(
            blacklist_categories=("Cs",), blacklist_characters="\x00"
        ),
        min_size=1,
        max_size=80,
    ),
    suffix=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
        min_size=0,
        max_size=80,
    ),
    new=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
        min_size=0,
        max_size=80,
    ),
)
def test_property_edit_replace_substring_when_unique(
    tmp_path_factory, prefix, middle, suffix, new
):
    """Property: if `middle` appears exactly once in original (built so), then
    replace_substring(middle -> new) yields prefix + new + suffix. Skip cases
    where the resulting prefix+middle+suffix accidentally contains `middle`
    again (multi-match would be refused) or where the no-op case applies."""
    original = prefix + middle + suffix
    if original.count(middle) != 1:
        return  # multi-match would be refused; skip
    if middle == new:
        return  # no-op; covered separately

    from tackit.core import Core
    from tackit.db import init_store

    d = tmp_path_factory.mktemp("prop")
    init_store(d)
    c = Core.open(start=d)
    try:
        c.add("seed", kind="production", description=original)
        c.edit_replace_substring(
            1, old_string=middle, new_string=new, delta="property-test replace"
        )
        assert c.get(1).description == prefix + new + suffix
    finally:
        c.close_conn()
