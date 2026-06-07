"""T220 - ls/board name_prefix filter.

Scopes a query to tasks whose stored name begins with a LITERAL, case-sensitive
prefix, so a project can pull one section of a large layer (e.g. '§9.1') instead
of the whole kind. These pin: the happy filter, composition with kind, the
literal (no LIKE/GLOB wildcard) and case-sensitive semantics, that it matches the
bare name and NOT the synthesized prefixed_name, and the empty-prefix refusal.
"""

import pytest

from tackit.errors import ValidationError


def test_name_prefix_filters_to_matching_names(core):
    core.add("§9.1 search", kind="design")
    core.add("§9.1.4 fetch", kind="design")
    core.add("§8.2 other", kind="design")
    ids = {t.id for t in core.ls(name_prefix="§9.1")}
    assert ids == {1, 2}


def test_name_prefix_composes_with_kind(core):
    core.add("§9.1 spec", kind="design")
    core.add("§9.1 impl", kind="production")
    ids = {t.id for t in core.ls(kind="design", name_prefix="§9.1")}
    assert ids == {1}


def test_name_prefix_is_literal_not_a_like_wildcard(core):
    # if '%' were a LIKE wildcard, 'a%' would also match 'axb'
    core.add("a%b literal", kind="production")
    core.add("axb other", kind="production")
    assert [t.id for t in core.ls(name_prefix="a%")] == [1]


def test_name_prefix_is_case_sensitive(core):
    core.add("Alpha one", kind="production")
    core.add("alpha two", kind="production")
    assert [t.id for t in core.ls(name_prefix="Alpha")] == [1]


def test_name_prefix_matches_bare_name_not_synthesized_prefix(core):
    # the row's prefixed_name is 'T1 — widget' but its stored name is 'widget';
    # filtering on the synthesized prefix must find nothing
    core.add("widget", kind="production")
    assert core.ls(name_prefix="T1") == []
    assert [t.id for t in core.ls(name_prefix="widget")] == [1]


def test_name_prefix_no_match_returns_empty(core):
    core.add("alpha", kind="production")
    assert core.ls(name_prefix="zzz") == []


def test_empty_name_prefix_refused(core):
    with pytest.raises(ValidationError):
        core.ls(name_prefix="")
