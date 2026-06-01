"""T94 / D26 - kind is required at create time.

Pins the refusal paths the bulk add-call sweep doesn't exercise: missing kind,
invalid kind, empty/whitespace kind, valid kind (each value), and the bulk-load
parser's per-row refusals. The op-layer and parser-layer checks are independent
(load specs could be hand-built, bypassing the parser), so both are tested.
"""

import pytest

from tackit.errors import ValidationError
from tackit.plan import parse_plan
from tackit.schema import KIND_VALUES


# --- core.add() -------------------------------------------------------------

def test_add_without_kind_raises_type_error(core):
    """kind is a keyword-only required parameter; omitting it is a Python
    TypeError -- the loudest refusal we can produce at the op signature."""
    with pytest.raises(TypeError):
        core.add("missing kind")


@pytest.mark.parametrize("bad", ["bogus", "Production", "DESIGN", "prod", "", "  "])
def test_add_invalid_kind_refused(core, bad):
    """kind values are case-sensitive and must be in the closed taxonomy.
    Empty/whitespace are refused as missing."""
    with pytest.raises(ValidationError):
        core.add("invalid kind", kind=bad)


@pytest.mark.parametrize("kind", list(KIND_VALUES))
def test_add_all_four_kinds_accepted(core, kind):
    t = core.add(f"a {kind} task", kind=kind)
    assert t.kind == kind
    assert core.get(t.id).kind == kind  # persisted, not just on the returned model


def test_add_none_kind_refused(core):
    """Explicit None is just as invalid as omitted -- the Optional[str] shape
    isn't allowed for a required field."""
    with pytest.raises(ValidationError):
        core.add("none kind", kind=None)


# --- plan.parse_plan() ------------------------------------------------------

def test_parse_plan_missing_kind_field_refused():
    """A [key] block without `kind:` is refused at parse time, naming the key."""
    with pytest.raises(ValidationError, match="task 'a'"):
        parse_plan("[a] name\n")


def test_parse_plan_missing_kind_message_lists_valid_values():
    with pytest.raises(ValidationError, match="design|schema|production|meta"):
        parse_plan("[a] name\n")


@pytest.mark.parametrize("bad", ["bogus", "Production", "PROD"])
def test_parse_plan_invalid_kind_refused(bad):
    """Per-row kind value is validated at parse, not deferred to the op layer."""
    with pytest.raises(ValidationError, match="not valid"):
        parse_plan(f"[a] name\n  kind: {bad}\n")


def test_parse_plan_one_missing_kind_among_many_refused():
    """A multi-task plan with ANY row missing kind is refused -- D24 atomicity."""
    plan = (
        "[a] first\n  kind: production\n"
        "[b] second\n"  # missing kind
        "[c] third\n  kind: production\n"
    )
    with pytest.raises(ValidationError, match="task 'b'"):
        parse_plan(plan)


@pytest.mark.parametrize("kind", list(KIND_VALUES))
def test_parse_plan_each_kind_accepted(kind):
    specs = parse_plan(f"[a] name\n  kind: {kind}\n")
    assert specs[0]["kind"] == kind


# --- core.load() defense in depth -------------------------------------------

def test_load_hand_built_spec_missing_kind_refused(core):
    """A hand-built specs list (bypassing the parser) is still refused at the
    op layer -- defense in depth, since the parser isn't the only entry point."""
    specs = [{"key": "a", "name": "alpha", "desc": "", "labels": [], "depends_on": []}]
    with pytest.raises(ValidationError):
        core.load(specs)
    assert core.ls() == []  # rolled back / never started


def test_load_hand_built_spec_invalid_kind_refused(core):
    specs = [{"key": "a", "name": "alpha", "kind": "bogus", "desc": "", "labels": [], "depends_on": []}]
    with pytest.raises(ValidationError):
        core.load(specs)
    assert core.ls() == []


def test_load_one_spec_missing_kind_rolls_back_whole_plan(core):
    """If any spec is missing kind, the whole load is refused -- never a partial
    plan (D24 atomicity, reaffirmed)."""
    specs = [
        {"key": "a", "name": "first", "kind": "production", "desc": "", "labels": [], "depends_on": []},
        {"key": "b", "name": "second", "desc": "", "labels": [], "depends_on": []},  # missing kind
    ]
    with pytest.raises(ValidationError):
        core.load(specs)
    assert core.ls() == []  # rolled back


# --- meta-island still composes with kind required --------------------------

def test_add_meta_then_link_to_production_refused(core):
    """T94 doesn't change the meta-island constraint, but pins the new ergonomics:
    you must say kind=meta at add time (rather than relying on a default), and the
    cross-kind refusal still fires at link_add."""
    from tackit.errors import InvariantError

    core.add("a meta thing", kind="meta")
    core.add("a prod thing", kind="production")
    with pytest.raises(InvariantError, match="meta-island"):
        core.link_add(1, 2, because="cross-kind", delta="test")
