"""T238 / D17 - section-id search routing.

The default unicode61 FTS tokenizer shreds a section id at index time ('§9.4'
-> tokens 9,4, indistinguishable from '§8.9.4' -> 8,9,4), so FTS ranked the
WRONG near-miss above the literal section (in dogfooding: §8.9.4 above §9.4).
T222 only stopped the *syntax error*; this is the deeper fix. core.search now
DETECTS a section-id-shaped query and ROUTES it to an anchored substring match
on the raw `name` column, bypassing FTS (option b in D17). FTS is untouched for
every other query; no schema/migration change.

These pin: the detector's coverage/exclusion matrix, the disambiguation that
FTS got wrong, the right-boundary (siblings out, descendants in), and that
ordinary keyword + D32 id-prefix search still go through FTS unchanged.
"""

import pytest

from tackit.core import Core, _is_section_query


# --- Pass 1/3: detection matrix (the pure predicate) ------------------------

@pytest.mark.parametrize("q", ["§9.4", "9.4", "4.2.7.13", "§11", "11.4", " §9.4 "])
def test_detects_section_shaped_query(q):
    assert _is_section_query(q) is True


@pytest.mark.parametrize(
    "q",
    [
        "11",            # bare integer -> too broad, stays a keyword search
        "v0.5",          # leading letter -> version, not a section
        "9.4 citation",  # whitespace -> not a *pure* id
        "T238",          # D32 id-prefix -> FTS handles it
        "step2",         # letter token
        "§",             # no digits
        "",              # empty
        "   ",           # whitespace only
    ],
)
def test_rejects_non_section_query(q):
    assert _is_section_query(q) is False


# --- Pass 1: disambiguation -- the exact failure FTS produced ---------------

@pytest.fixture
def section_corpus(core):
    # §8.9.4 is the near-miss FTS ranked ABOVE the literal §9.4
    core.add("§9.4 — Step 3 citation existence check", kind="design")      # id 1
    core.add("§8.9.4 — Generation history", kind="design")                 # id 2
    core.add("§9.9.4 — Impact analysis failure handling", kind="design")   # id 3
    return core


def test_section_query_returns_exactly_the_literal_section(section_corpus):
    for q in ("§9.4", "9.4"):
        hits = section_corpus.search(q)
        ids = [h.id for h in hits]
        assert ids == [1], f"query {q!r} should return only §9.4 (id 1), got {ids}"


# --- Pass 2/3: right-boundary -- siblings out, descendants in ----------------

def test_boundary_excludes_digit_siblings_includes_descendants(core):
    core.add("§9.4 — the node itself", kind="design")        # id 1
    core.add("§9.40 — a digit sibling", kind="design")       # id 2  (excluded)
    core.add("§9.41 — another sibling", kind="design")       # id 3  (excluded)
    core.add("§9.4.1 — a descendant", kind="design")         # id 4  (included)
    hits = core.search("§9.4")
    ids = sorted(h.id for h in hits)
    assert ids == [1, 4], f"want node+descendant only, got {ids}"


# --- Pass 3: degenerate -- detected section with no match -> empty -----------

def test_section_query_with_no_match_is_empty(core):
    core.add("§7.1 — something else", kind="design")
    assert core.search("§9.4") == []


# --- Pass 1: non-regression -- ordinary FTS path unchanged ------------------

def test_plain_keyword_still_uses_fts(core):
    core.add("§9.4 — citation existence check", kind="design")  # id 1
    core.add("unrelated palette work", kind="design")           # id 2
    hits = core.search("citation")          # NOT section-shaped -> FTS
    assert hits and hits[0].id == 1
    assert all(h.id != 2 for h in hits)


def test_d32_id_prefix_search_still_works(core):
    core.add("some design slice", kind="design")  # id 1 -> prefixed D1
    hits = core.search("D1")                       # D32 prefix lookup via FTS
    assert any(h.id == 1 for h in hits)
