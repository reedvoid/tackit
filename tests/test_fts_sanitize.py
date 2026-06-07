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
    core.add("foo bar widget", kind="production")
    hits = core.search("foo:bar")
    assert [h.id for h in hits] == [1]


def test_hyphenated_term_is_searchable(core):
    # raw "read-projection" used to raise "no such column: projection"
    core.add("lean read-projection default", kind="production")
    hits = core.search("read-projection")
    assert [h.id for h in hits] == [1]


def test_apostrophe_term_is_searchable(core):
    # raw "didn't" used to raise a syntax error near "'"
    core.add("didn't ship yet", kind="production")
    hits = core.search("didn't")
    assert [h.id for h in hits] == [1]


# --- semantics preserved ----------------------------------------------------

def test_multi_word_query_is_implicit_and(core):
    core.add("alpha beta widget", kind="production")   # has both
    core.add("alpha only", kind="production")          # has one
    core.add("beta only", kind="production")           # has the other
    ids = {h.id for h in core.search("alpha beta")}
    assert ids == {1}


def test_per_token_not_phrase_adjacency(core):
    """The decisive distinction: per-token quoting matches a row whose words are
    NON-adjacent; a single wrapping phrase ("alpha beta") would miss it."""
    core.add("alpha gamma beta", kind="production")  # alpha..beta non-adjacent
    assert [h.id for h in core.search("alpha beta")] == [1]


def test_name_only_filter_still_scopes_to_name(core):
    core.add("§9.1 the title", kind="design", description="body has no marker")
    core.add("plain title", kind="design", description="§9.1 only in the body")
    # name_only must match only the row whose NAME carries the dotted key
    name_hits = core.search("§9.1", name_only=True)
    assert [h.id for h in name_hits] == [1]
    # default (name+body) finds both
    assert {h.id for h in core.search("§9.1")} == {1, 2}


def test_prefixed_name_lookup_still_resolves(core):
    # D32: the synthesized "T<id>" prefix is FTS-indexed; sanitization must not
    # break search("T<id>")
    core.add("rotate signing keys", kind="production")  # -> T1
    hits = core.search("T1")
    assert hits and hits[0].id == 1


# --- the refusal that survives ----------------------------------------------

@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_empty_query_still_refused(core, blank):
    with pytest.raises(ValidationError):
        core.search(blank)
