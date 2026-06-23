"""T212 (D211): lean read-projection for ls/board — scalars by default, no
`description`; board drops neighbor `because`/`last_edit_delta` (graph shape
kept); `kind` filter; include-or-omit (never truncated); independent opt-in
flags. Core projection helpers + CLI; the MCP side lives in test_mcp.py and
shares the SAME project_task/project_slice helpers (parity by construction)."""

import json

import pytest

from tackit.cli import main
from tackit.errors import ValidationError
from tackit.models import project_slice, project_task


# --- core projection helpers -----------------------------------------------


def test_project_task_lean_omits_description(core):
    core.add("a task", kind="production", description="a long body")
    t = core.ls()[0]
    lean = project_task(t, include_description=False)
    assert "description" not in lean
    assert lean["id"] == t.id and lean["prefixed_name"] == t.prefixed_name
    assert project_task(t, include_description=True)["description"] == "a long body"


def test_project_task_drops_redundant_name_and_timestamps(core):
    """T221: the bare `name` (carried by prefixed_name) and the created_at/
    updated_at timestamps are dropped from the list projection; the canonical
    scalars stay."""
    core.add("a task", kind="production")
    t = core.ls()[0]
    lean = project_task(t, include_description=False)
    assert "name" not in lean
    assert "created_at" not in lean and "updated_at" not in lean
    # canonical id/handle + the scalars worth scanning remain
    assert lean["prefixed_name"] == t.prefixed_name
    assert lean["id"] == t.id and lean["kind"] == "production" and lean["status"] == "open"
    assert "wont_do_reason" in lean  # kept: a dropped row's reason is useful inline


def test_show_remains_the_full_scalar_path(core):
    """T221: trimming the list projection must not touch show() -- it still
    carries name + timestamps for one row."""
    core.add("a task", kind="production")
    full = core.show(1).model_dump(mode="json")
    assert full["task"]["name"] == "a task"
    assert "created_at" in full["task"] and "updated_at" in full["task"]


def test_project_slice_drops_focal_and_neighbor_name(core):
    """T221: board cards drop the focal task's name+timestamps and each
    neighbor's bare name (prefixed_name carries it)."""
    d = core.add("design", kind="design")  # D1
    t = core.add("impl", kind="production")  # T2
    core.link_add(t.id, d.id, because="T2 realizes D1")
    card = project_slice(
        core.show(t.id), include_description=False, include_neighbor_because=False
    )
    assert "name" not in card["task"]
    assert "created_at" not in card["task"] and "updated_at" not in card["task"]
    assert card["task"]["prefixed_name"].startswith("T2")
    nbr = card["links"][0]
    assert "name" not in nbr and nbr["prefixed_name"].startswith("D1")


def test_project_task_empty_description_omitted_not_truncated(core):
    core.add("a task", kind="production")  # description defaults to ""
    t = core.ls()[0]
    assert "description" not in project_task(t, include_description=False)
    # opt-in returns the verbatim empty string — include-or-omit, never partial.
    assert project_task(t, include_description=True)["description"] == ""


def test_project_slice_lean_drops_body_and_neighbor_because(core):
    d = core.add("design", kind="design")  # D1
    t = core.add("impl", kind="production", description="impl body")  # T2
    core.link_add(t.id, d.id, because="T2 realizes D1")
    lean = project_slice(
        core.show(t.id), include_description=False, include_neighbor_because=False
    )
    assert "description" not in lean["task"]
    nbr = lean["links"][0]
    assert "because" not in nbr and "last_edit_delta" not in nbr
    # graph SHAPE preserved
    assert nbr["id"] == d.id and nbr["prefixed_name"].startswith("D1") and nbr["status"] == "spec"


def test_project_slice_opt_in_axes_independent(core):
    d = core.add("design", kind="design")
    t = core.add("impl", kind="production", description="impl body")
    core.link_add(t.id, d.id, because="T2 realizes D1")
    sl = core.show(t.id)
    only_desc = project_slice(sl, include_description=True, include_neighbor_because=False)
    assert only_desc["task"]["description"] == "impl body"
    assert "because" not in only_desc["links"][0]
    only_nbr = project_slice(sl, include_description=False, include_neighbor_because=True)
    assert "description" not in only_nbr["task"]
    assert only_nbr["links"][0]["because"] == "T2 realizes D1"


# --- core.ls kind filter ----------------------------------------------------


def test_ls_kind_filter_and_composition(core):
    core.add("d", kind="design")      # D1 spec
    core.add("s", kind="schema")      # S2 spec
    core.add("p", kind="production")  # T3 open
    assert [t.id for t in core.ls(kind="design")] == [1]
    assert [t.id for t in core.ls(kind="schema")] == [2]
    # status='spec' returns design+schema both; kind disambiguates.
    assert [t.id for t in core.ls(status="spec", kind="design")] == [1]
    assert [t.id for t in core.ls(status="spec", kind="schema")] == [2]


def test_ls_kind_filter_zero_matches(core):
    core.add("p", kind="production")
    assert core.ls(kind="design") == []


def test_ls_invalid_kind_refused(core):
    with pytest.raises(ValidationError, match="kind"):
        core.ls(kind="bogus")


# --- CLI lean default + flags ----------------------------------------------


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    return tmp_path


def test_cli_ls_lean_default_and_opt_in(cli, capsys):
    main(["add", "a task", "--kind", "production", "--desc", "the body"])
    capsys.readouterr()
    assert main(["ls", "--json"]) == 0
    lean = json.loads(capsys.readouterr().out)
    assert "description" not in lean[0]
    capsys.readouterr()
    assert main(["ls", "--include-description", "--json"]) == 0
    full = json.loads(capsys.readouterr().out)
    assert full[0]["description"] == "the body"


def test_cli_ls_kind_filter(cli, capsys):
    main(["add", "d", "--kind", "design"])       # D1
    main(["add", "p", "--kind", "production"])    # T2
    capsys.readouterr()
    assert main(["ls", "--kind", "design", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [t["id"] for t in out] == [1]


def test_cli_board_lean_default_and_opt_in(cli, capsys):
    main(["add", "design", "--kind", "design"])  # D1
    main(["add", "impl", "--kind", "production", "--desc", "impl body"])  # T2
    main(["link", "add", "2", "1", "--because", "T2 realizes D1"])
    capsys.readouterr()
    assert main(["board", "--json"]) == 0
    lean = json.loads(capsys.readouterr().out)
    t2 = lean[1]  # ls orders by id: [D1, T2]
    assert t2["task"]["id"] == 2
    assert "description" not in t2["task"]
    assert "because" not in t2["links"][0]
    assert t2["links"][0]["prefixed_name"].startswith("D1")  # shape kept
    capsys.readouterr()
    assert main(["board", "--include-description", "--include-neighbor-because", "--json"]) == 0
    full = json.loads(capsys.readouterr().out)
    t2f = full[1]
    assert t2f["task"]["description"] == "impl body"
    assert t2f["links"][0]["because"] == "T2 realizes D1"
