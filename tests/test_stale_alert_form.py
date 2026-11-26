"""T362 (D61, D199 #6): which stale-alert form a call gets.

D61: the full obligation paragraph rides only a call that ADDED ids to the
worklist; any other call with stale work outstanding gets the one-line form; with
nothing stale there is no alert. One core function decides; MCP and the CLI both
call it. The CLI prints the one-line form at entry, and at exit the full paragraph
("[after this change] ") only when the command added ids, or the "✓" line when it
emptied the list.

The dangerous direction is pinned hardest: a call that creates a new obligation
must carry the full paragraph. Each case has its converse beside it.
"""

import asyncio
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from tackit import mcp_server
from tackit.cli import main
from tackit.core import stale_alert_for_call, stale_alert_short_text
from tackit.db import init_store
from tackit.errors import InvariantError

FULL = "STALE TASKS OUTSTANDING"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_store(tmp_path)
    return tmp_path


def _mcp(scenario):
    async def runner():
        srv = mcp_server.build_server()
        async with connect(srv._mcp_server) as session:
            return await scenario(session)

    return asyncio.run(runner())


def _env(response):
    assert response.isError is not True, response.content[0].text
    return json.loads(response.content[0].text)


async def _seed(s):
    """D1 anchor; T2 and T3 both realize it. Editing T2 then flags D1 (new)."""
    await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # D1
    await s.call_tool("add", {"name": "two", "kind": "production",
                              "deps": {1: "two realizes the anchor"}})  # T2
    await s.call_tool("add", {"name": "three", "kind": "production",
                              "deps": {1: "three realizes the anchor"}})  # T3


# --- the core rule -------------------------------------------------------------


def test_rule_none_when_nothing_is_stale():
    assert stale_alert_for_call(frozenset(), []) is None
    assert stale_alert_for_call(frozenset({4}), []) is None  # emptied: no alert


def test_rule_full_when_the_call_added_an_id(core):
    core.add("spec anchor", kind="design")
    core.add("two", kind="production", deps={1: "two realizes the anchor"})
    core.edit(2, description="x", delta="d")  # flags D1
    after = core.stale_worklist()
    payload = stale_alert_for_call(frozenset(), after)
    assert FULL in payload["message"]
    assert payload["count"] == 1 and payload["stale_task_ids"] == [1]


def test_rule_short_when_the_list_is_unchanged_or_shrunk(core):
    core.add("spec anchor", kind="design")
    core.add("two", kind="production", deps={1: "two realizes the anchor"})
    core.edit(2, description="x", delta="d")  # flags D1
    after = core.stale_worklist()
    unchanged = stale_alert_for_call(frozenset({1}), after)
    shrunk = stale_alert_for_call(frozenset({1, 9}), after)
    for payload in (unchanged, shrunk):
        assert payload["message"] == "⚠ 1 stale — see `stale` for the list."
        assert payload["stale_task_ids"] == [1]


def test_rule_full_when_one_id_is_added_beside_old_ones(core):
    core.add("spec anchor", kind="design")
    core.add("two", kind="production", deps={1: "two realizes the anchor"})
    core.add("three", kind="production", deps={1: "three realizes the anchor"})
    core.link_add(2, 3, because="three builds on two")
    core.edit(2, description="x", delta="d")  # flags D1 and T3
    payload = stale_alert_for_call(frozenset({1}), core.stale_worklist())
    assert FULL in payload["message"]


def test_short_text_is_the_one_line_form():
    assert stale_alert_short_text(3) == "⚠ 3 stale — see `stale` for the list."


def test_reading_the_entry_ids_before_marking_the_call_fails_loud(core):
    with pytest.raises(InvariantError):
        core.call_entry_stale_ids


# --- MCP -------------------------------------------------------------------


def test_mcp_edit_that_flags_a_new_neighbor_gets_the_full_paragraph(store):
    async def scenario(s):
        await _seed(s)
        return await s.call_tool("edit", {"id": 2, "description": "x", "delta": "d"})

    env = _env(_mcp(scenario))
    assert FULL in env["stale_alert"]["message"]
    assert env["stale_alert"]["stale_task_ids"] == [1]


def test_mcp_edit_whose_neighbor_is_already_stale_gets_the_short_form(store):
    async def scenario(s):
        await _seed(s)
        await s.call_tool("edit", {"id": 2, "description": "x", "delta": "d"})  # flags D1
        return await s.call_tool("edit", {"id": 3, "description": "y", "delta": "d"})

    env = _env(_mcp(scenario))
    assert env["stale_alert"]["message"] == "⚠ 1 stale — see `stale` for the list."


def test_mcp_add_while_stale_gets_the_short_form(store):
    async def scenario(s):
        await _seed(s)
        await s.call_tool("edit", {"id": 2, "description": "x", "delta": "d"})
        return await s.call_tool("add", {"name": "four", "kind": "production",
                                         "deps": {1: "four realizes the anchor"}})

    env = _env(_mcp(scenario))
    assert FULL not in env["stale_alert"]["message"]
    assert env["stale_alert"]["count"] == 1


def test_mcp_reclassify_that_flags_gets_the_full_paragraph(store):
    async def scenario(s):
        await _seed(s)
        return await s.call_tool("reclassify", {"id": 1, "kind": "schema", "delta": "d"})

    env = _env(_mcp(scenario))
    assert FULL in env["stale_alert"]["message"]
    assert env["stale_alert"]["stale_task_ids"] == [2, 3]


def test_mcp_reconcile_gets_the_short_form(store):
    async def scenario(s):
        await _seed(s)
        await s.call_tool("reclassify", {"id": 1, "kind": "schema", "delta": "d"})  # T2, T3
        return await s.call_tool("reconcile", {"ids": [2]})

    env = _env(_mcp(scenario))
    assert env["stale_alert"]["message"] == "⚠ 1 stale — see `stale` for the list."


def test_mcp_reconcile_that_empties_the_list_has_no_alert(store):
    async def scenario(s):
        await _seed(s)
        await s.call_tool("edit", {"id": 2, "description": "x", "delta": "d"})
        return await s.call_tool("reconcile", {"ids": [1]})

    env = _env(_mcp(scenario))
    assert "stale_alert" not in env


# --- CLI -------------------------------------------------------------------


def _cli_seed():
    main(["add", "spec anchor", "--kind", "design"])
    main(["add", "two", "--kind", "production", "--dep", "1::two realizes the anchor"])
    main(["add", "three", "--kind", "production", "--dep", "1::three realizes the anchor"])


def test_cli_nothing_stale_prints_nothing(store, capsys):
    _cli_seed()
    capsys.readouterr()
    assert main(["ls"]) == 0
    assert capsys.readouterr().err == ""


def test_cli_an_adding_command_prints_the_full_paragraph_at_exit(store, capsys):
    _cli_seed()
    capsys.readouterr()
    assert main(["edit", "2", "--desc", "x", "--delta", "d"]) == 0
    err = capsys.readouterr().err
    assert "[after this change] ⚠ " + FULL in err
    assert "⚠ 1 stale — see" not in err  # nothing was stale at entry


def test_cli_entry_line_is_the_one_line_form(store, capsys):
    _cli_seed()
    main(["edit", "2", "--desc", "x", "--delta", "d"])
    capsys.readouterr()
    assert main(["ls"]) == 0
    assert capsys.readouterr().err == "⚠ 1 stale — see `stale` for the list.\n"


def test_cli_a_command_adding_nothing_prints_no_exit_line(store, capsys):
    _cli_seed()
    main(["edit", "2", "--desc", "x", "--delta", "d"])  # flags D1
    capsys.readouterr()
    assert main(["edit", "3", "--desc", "y", "--delta", "d"]) == 0  # D1 already stale
    err = capsys.readouterr().err
    assert FULL not in err
    assert "[after this change]" not in err
    assert err.startswith("⚠ 1 stale — see `stale` for the list.\n")


def test_cli_a_partial_reconcile_prints_only_the_entry_line(store, capsys):
    _cli_seed()
    main(["reclassify", "1", "--kind", "schema", "--delta", "d"])  # flags T2, T3
    capsys.readouterr()
    assert main(["reconcile", "2"]) == 0
    err = capsys.readouterr().err
    assert err == "⚠ 2 stale — see `stale` for the list.\n"


def test_cli_a_reconcile_that_empties_the_list_prints_the_done_line(store, capsys):
    _cli_seed()
    main(["edit", "2", "--desc", "x", "--delta", "d"])  # flags D1
    capsys.readouterr()
    assert main(["reconcile", "1"]) == 0
    err = capsys.readouterr().err
    assert "✓ stale worklist now empty — reconciliation complete." in err
    assert FULL not in err
