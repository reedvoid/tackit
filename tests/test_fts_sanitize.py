"""T222 - FTS5 query sanitization.

Raw user input used to be handed straight to ``MATCH``, so any token carrying an
FTS5 metacharacter ('.', ':', '-', "'") was interpreted as query syntax and the
search errored. ``core.search`` now per-token-quotes the query so those keys are
searchable literally. These tests pin: the previously-erroring inputs now match,
the implicit-AND (not phrase/adjacency) semantics are preserved, the name_only
column filter still scopes correctly, and the empty-query refusal still holds.
"""

import pytest

from tackit.core import Core, _fts_sanitize
from tackit.errors import ValidationError


# --- the sanitizer itself ---------------------------------------------------

def test_sanitize_quotes_each_token():
    # per-token quoting, NOT one wrapping phrase (preserves implicit-AND)
    assert _fts_sanitize("lean projection") == '"lean" "projection"'


def test_sanitize_escapes_embedded_quote_by_doubling():
    # the only metacharacter inside a quoted phrase is '"' itself -> doubled
    assert _fts_sanitize('x"y') == '"x""y"'


def test_sanitize_collapses_whitespace_runs():
    assert _fts_sanitize("  a   b ") == '"a" "b"'


def test_sanitize_empty_is_empty():
    assert _fts_sanitize("   ") == ""


# --- previously-erroring inputs now match -----------------------------------

@pytest.mark.parametrize("query", ["§4.2.4", "4.2.4"])
def test_dotted_section_id_is_searchable(core, query):
    core.add("§4.2.4 GET skills endpoint", kind="design")
    core.add("unrelated palette", kind="design")
    hits = core.search(query)
    assert hits and hits[0].id == 1
    assert all(h.id != 2 for h in hits)


def test_colon_term_does_not_become_a_column_filter(core):
    # raw "foo:bar" used to raise "no such column: foo"
    anchor = core.add("spec anchor", kind="design")  # D1
    task = core.add(
        "foo bar widget", kind="production",
        deps={anchor.id: "task realizes the anchor decision"},
    )  # T2
    hits = core.search("foo:bar")
    assert [h.id for h in hits] == [task.id]


def test_hyphenated_term_is_searchable(core):
    # raw "read-projection" used to raise "no such column: projection"
    anchor = core.add("spec anchor", kind="design")  # D1
    task = core.add(
        "lean read-projection default", kind="production",
        deps={anchor.id: "task realizes the anchor decision"},
    )  # T2
    hits = core.search("read-projection")
    assert [h.id for h in hits] == [task.id]


def test_apostrophe_term_is_searchable(core):
    # raw "didn't" used to raise a syntax error near "'"
    anchor = core.add("spec anchor", kind="design")  # D1
    task = core.add(
        "didn't ship yet", kind="production",
        deps={anchor.id: "task realizes the anchor decision"},
    )  # T2
    hits = core.search("didn't")
    assert [h.id for h in hits] == [task.id]


# --- semantics preserved ----------------------------------------------------

def test_multi_word_query_is_implicit_and(core):
    anchor = core.add("spec anchor", kind="design")  # D1
    both = core.add(
        "alpha beta widget", kind="production",
        deps={anchor.id: "realizes the anchor decision"},
    )  # has both
    core.add(
        "alpha only", kind="production",
        deps={anchor.id: "realizes the anchor decision"},
    )  # has one
    core.add(
        "beta only", kind="production",
        deps={anchor.id: "realizes the anchor decision"},
    )  # has the other
    ids = {h.id for h in core.search("alpha beta")}
    assert ids == {both.id}


def test_per_token_not_phrase_adjacency(core):
    """The decisive distinction: per-token quoting matches a row whose words are
    NON-adjacent; a single wrapping phrase ("alpha beta") would miss it."""
    anchor = core.add("spec anchor", kind="design")  # D1
    task = core.add(
        "alpha gamma beta", kind="production",
        deps={anchor.id: "realizes the anchor decision"},
    )  # alpha..beta non-adjacent
    assert [h.id for h in core.search("alpha beta")] == [task.id]


def test_name_only_filter_still_scopes_to_name(core):
    # A punctuated key that is NOT whole-section-shaped (leading letter), so it
    # still goes through the sanitized FTS path. Whole-section ids ('§9.1') now
    # route to an anchored name-substring lookup instead (T238) and would bypass
    # this FTS column filter -- hence 'v0.5' here, not a §-id.
    core.add("v0.5 the title", kind="design", description="body has no marker")
    core.add("plain title", kind="design", description="v0.5 only in the body")
    # name_only must match only the row whose NAME carries the dotted key
    name_hits = core.search("v0.5", name_only=True)
    assert [h.id for h in name_hits] == [1]
    # default (name+body) finds both
    assert {h.id for h in core.search("v0.5")} == {1, 2}


def test_prefixed_name_lookup_still_resolves(core):
    # D32: the synthesized "T<id>" prefix is FTS-indexed; sanitization must not
    # break search("T<id>")
    anchor = core.add("spec anchor", kind="design")  # D1
    task = core.add(
        "rotate signing keys", kind="production",
        deps={anchor.id: "realizes the anchor decision"},
    )  # -> T2
    hits = core.search(f"T{task.id}")
    assert hits and hits[0].id == task.id


# --- the refusal that survives ----------------------------------------------

@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_empty_query_still_refused(core, blank):
    with pytest.raises(ValidationError):
        core.search(blank)
