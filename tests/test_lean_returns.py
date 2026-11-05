"""Lean adapter returns and stale flags (D211, D13, D10), MCP and CLI.

Every scenario runs through the real MCP server, over the in-memory client
protocol, and through `main`. The dangerous direction is pinned hardest: a live
neighbor flagged for the first time must ALWAYS be listed with its exact `because`,
because that list is how an agent learns about a new obligation. The converses are
pinned beside it: terminal and already-stale neighbors are never listed, yet the
flag is still written on them.

Expected values are written out by hand from D211's rules, never recomputed.
"""

import asyncio
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from tackit import mcp_server
from tackit.cli import main
from tackit.core import Core
from tackit.db import init_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_store(tmp_path)
    return tmp_path


def _mcp(scenario):
    """Run ``scenario(session)`` against the real server over the MCP protocol."""

    async def runner():
        srv = mcp_server.build_server()
        async with connect(srv._mcp_server) as session:
            return await scenario(session)

    return asyncio.run(runner())


def _call(tool, args):
    """One MCP tool call; returns its parsed `result`, failing on a refusal."""

    async def scenario(session):
        return await session.call_tool(tool, args)

    response = _mcp(scenario)
    assert response.isError is not True, response.content[0].text
    return json.loads(response.content[0].text)["result"]


def _core_call(operation):
    """Run ``operation(core)`` on a freshly opened Core, the same layer both
    adapters sit on, for seeding reachable state and reading flags back."""
    core = Core.open()
    try:
        return operation(core)
    finally:
        core.close_conn()


def _stale_flag(task_id):
    return _core_call(lambda core: core.get(task_id).stale)


def _worklist_ids():
    ids = []
    for task in _core_call(lambda core: core.stale_worklist()):
        ids.append(task.id)
    return ids


def _cli_json(capsys, argv):
    capsys.readouterr()
    assert main(argv) == 0
    return json.loads(capsys.readouterr().out)


def _cli_lines(capsys, argv):
    capsys.readouterr()
    assert main(argv) == 0
    return capsys.readouterr().out.splitlines()


def _seed_hub_with_every_neighbor_state():
    """A design hub (1) with one neighbor in each state an edit can meet:

    2 open and 3 spec, both unflagged: live and first-time, so they get listed.
    4 closed, 5 wont_do, 6 retired: terminal, record-only.
    8 open and already stale, flagged earlier through its other spec, 7.
    """

    def seed(core):
        core.add("hub", kind="design", description="hub body")  # 1
        core.add("live open", kind="production",
                 deps={1: "T2 realizes the hub"})  # 2
        core.add("live spec", kind="design", deps={1: "D3 refines the hub"})  # 3
        core.add("closed", kind="production", deps={1: "T4 realized the hub"})  # 4
        core.add("dropped", kind="production", deps={1: "T5 was for the hub"})  # 5
        core.add("dead spec", kind="design", deps={1: "D6 refined the hub"})  # 6
        core.add("other spec", kind="design")  # 7
        core.add("flagged", kind="production",
                 deps={1: "T8 realizes the hub", 7: "T8 realizes D7"})  # 8
        core.close(4)
        core.wont_do(5, reason="dropped for this fixture")
        core.retire(6, reason="premise replaced for this fixture")
        core.edit(7, description="changed", delta="flags T8 first")

    _core_call(seed)


FIRST_TIME_LIVE = [
    {"id": 2, "because": "T2 realizes the hub"},
    {"id": 3, "because": "D3 refines the hub"},
]

MCP_FLAGGING_OPS = [
    ("edit", {"id": 1, "description": "new body", "delta": "changed"}),
    ("edit_replace_substring",
     {"id": 1, "old_string": "hub body", "new_string": "new body", "delta": "changed"}),
    ("reclassify", {"id": 1, "kind": "schema", "delta": "changed"}),
]

CLI_FLAGGING_OPS = [
    ["edit", "1", "--desc", "new body", "--delta", "changed"],
    ["edit-replace", "1", "--old", "hub body", "--new", "new body",
     "--delta", "changed"],
    ["reclassify", "1", "--kind", "schema", "--delta", "changed"],
]


def _assert_every_neighbor_flagged_but_only_live_first_time_listed(newly_stale):
    assert newly_stale == FIRST_TIME_LIVE
    # The flag is still written on every neighbor (D10, D28): only what is
    # printed changes.
    for neighbor in (2, 3, 4, 5, 6, 8):
        assert _stale_flag(neighbor) is True, f"neighbor {neighbor} lost its flag"
    # The already-stale one stays on the worklist; the terminal ones never join.
    assert _worklist_ids() == [2, 3, 8]


# --- D211 entry rule: first-time live neighbors, exactly {id, because} ---------


@pytest.mark.parametrize("tool,args", MCP_FLAGGING_OPS)
def test_mcp_lists_only_first_time_live_neighbors(store, tool, args):
    _seed_hub_with_every_neighbor_state()
    result = _call(tool, args)
    _assert_every_neighbor_flagged_but_only_live_first_time_listed(
        result["newly_stale"]
    )


@pytest.mark.parametrize("argv", CLI_FLAGGING_OPS)
def test_cli_json_lists_only_first_time_live_neighbors(store, capsys, argv):
    _seed_hub_with_every_neighbor_state()
    result = _cli_json(capsys, argv + ["--json"])
    _assert_every_neighbor_flagged_but_only_live_first_time_listed(
        result["newly_stale"]
    )


def test_listed_ids_are_integers_that_reconcile_accepts(store):
    _seed_hub_with_every_neighbor_state()
    tool, args = MCP_FLAGGING_OPS[0]
    entries = _call(tool, args)["newly_stale"]
    ids = []
    for entry in entries:
        assert type(entry["id"]) is int
        ids.append(entry["id"])
    assert ids == [2, 3]
    assert _call("reconcile", {"ids": ids})["reconciled"] == [2, 3]
    assert _worklist_ids() == [8]


def test_there_is_no_size_cap_on_the_list(store):
    def seed(core):
        core.add("hub", kind="design")
        for index in range(30):
            core.add(f"neighbor {index}", kind="production",
                     deps={1: f"neighbor {index} realizes the hub"})

    _core_call(seed)
    entries = _call("edit", {"id": 1, "description": "new", "delta": "changed"})[
        "newly_stale"
    ]
    assert len(entries) == 30
    assert entries[29] == {"id": 31, "because": "neighbor 29 realizes the hub"}


def test_a_name_only_edit_lists_neighbors_too(store):
    _core_call(lambda core: core.add("hub", kind="design"))
    _core_call(lambda core: core.add("a", kind="production", deps={1: "a realizes"}))
    result = _call("edit", {"id": 1, "name": "renamed hub", "delta": "renamed"})
    assert result["newly_stale"] == [{"id": 2, "because": "a realizes"}]


# --- first-time is per flag, not per neighbor ----------------------------------


def test_a_second_edit_does_not_relist_an_unreconciled_neighbor(store):
    _core_call(lambda core: core.add("hub", kind="design"))
    _core_call(lambda core: core.add("a", kind="production", deps={1: "a realizes"}))
    first = _call("edit", {"id": 1, "description": "one", "delta": "first"})
    second = _call("edit", {"id": 1, "description": "two", "delta": "second"})
    assert first["newly_stale"] == [{"id": 2, "because": "a realizes"}]
    assert second["newly_stale"] == []
    assert _worklist_ids() == [2]


def test_reconcile_then_edit_lists_the_neighbor_again(store):
    _core_call(lambda core: core.add("hub", kind="design"))
    _core_call(lambda core: core.add("a", kind="production", deps={1: "a realizes"}))
    _call("edit", {"id": 1, "description": "one", "delta": "first"})
    _call("reconcile", {"ids": [2]})
    again = _call("edit", {"id": 1, "description": "two", "delta": "second"})
    assert again["newly_stale"] == [{"id": 2, "because": "a realizes"}]


def test_a_neighbor_flagged_by_a_second_task_keeps_both_notes_on_show(store):
    def seed(core):
        core.add("spec a", kind="design")  # 1
        core.add("spec b", kind="design")  # 2
        core.add("shared", kind="production",
                 deps={1: "shared realizes a", 2: "shared realizes b"})  # 3

    _core_call(seed)
    first = _call("edit", {"id": 1, "description": "new a", "delta": "changed a"})
    second = _call("edit", {"id": 2, "description": "new b", "delta": "changed b"})
    assert first["newly_stale"] == [{"id": 3, "because": "shared realizes a"}]
    assert second["newly_stale"] == []
    becauses = []
    for entry in _call("show", {"id": 3})["links"]:
        becauses.append(entry["because"])
    assert becauses == ["shared realizes a", "shared realizes b"]


def test_a_terminal_neighbor_reopened_with_its_flag_stays_on_the_worklist(store):
    def seed(core):
        core.add("hub", kind="design")  # 1
        core.add("a", kind="production", deps={1: "a realizes"})  # 2
        core.close(2)

    _core_call(seed)
    while_closed = _call("edit", {"id": 1, "description": "one", "delta": "first"})
    assert while_closed["newly_stale"] == []
    assert _worklist_ids() == []
    _call("reopen", {"id": 2})
    # Its flag survives the reopen, so the next edit is not its first flag: it is
    # surfaced by the worklist, not the edit's list.
    assert _worklist_ids() == [2]
    after = _call("edit", {"id": 1, "description": "two", "delta": "second"})
    assert after["newly_stale"] == []
    assert _worklist_ids() == [2]


# --- no-ops (D20) --------------------------------------------------------------


@pytest.mark.parametrize(
    "tool,args",
    [
        ("edit", {"id": 1, "description": "hub body", "delta": "no change"}),
        ("edit_replace_substring",
         {"id": 1, "old_string": "hub", "new_string": "hub", "delta": "no change"}),
        ("reclassify", {"id": 1, "kind": "design", "delta": "no change"}),
    ],
)
def test_a_noop_lists_nothing_and_flags_nothing(store, tool, args):
    _core_call(lambda core: core.add("hub", kind="design", description="hub body"))
    _core_call(lambda core: core.add("a", kind="production", deps={1: "a realizes"}))
    assert _call(tool, args)["newly_stale"] == []
    assert _stale_flag(2) is False


# --- the core keeps its full refs; only the adapters project (D211) -------------


def test_core_refs_carry_the_new_flag_and_the_because(core):
    core.add("hub", kind="design")
    core.add("a", kind="production", deps={1: "a realizes"})
    refs = core.edit(1, description="new", delta="changed").newly_stale
    assert len(refs) == 1
    assert refs[0].id == 2
    assert refs[0].stale is True
    assert refs[0].because == "a realizes"
    assert refs[0].name == "a"


# --- the focal task's body: omitted unless asked (D211) ------------------------


@pytest.mark.parametrize("tool,args", MCP_FLAGGING_OPS)
def test_mcp_results_omit_the_body_by_default(store, tool, args):
    _seed_hub_with_every_neighbor_state()
    task = _call(tool, args)["task"]
    assert task["id"] == 1
    assert "description" not in task


@pytest.mark.parametrize("argv", CLI_FLAGGING_OPS)
def test_cli_json_omits_the_body_by_default(store, capsys, argv):
    _seed_hub_with_every_neighbor_state()
    task = _cli_json(capsys, argv + ["--json"])["task"]
    assert task["id"] == 1
    assert "description" not in task


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["edit", "1", "--desc", "new", "--delta", "d"], "new"),
        (["edit-replace", "1", "--old", "body", "--new", "text", "--delta", "d"],
         "note text"),
        (["edit-append", "1", "--content", " more", "--delta", "d"], "note body more"),
    ],
)
def test_cli_edit_verbs_take_the_body_opt_in(store, capsys, argv, expected):
    _core_call(lambda core: core.add("note", kind="meta", description="note body"))
    result = _cli_json(capsys, argv + ["--json", "--include-description"])
    assert result["task"]["description"] == expected


def test_mcp_reclassify_refuses_the_body_opt_in(store):
    _core_call(lambda core: core.add("hub", kind="design"))

    async def scenario(session):
        return await session.call_tool(
            "reclassify",
            {"id": 1, "kind": "schema", "delta": "d", "include_description": True},
        )

    response = _mcp(scenario)
    assert response.isError is True
    assert "include_description" in response.content[0].text


def test_cli_reclassify_refuses_the_body_opt_in(store):
    _core_call(lambda core: core.add("hub", kind="design"))
    with pytest.raises(SystemExit):
        main(["reclassify", "1", "--kind", "schema", "--delta", "d",
              "--include-description"])


# --- CLI text wording (D211) ---------------------------------------------------


@pytest.mark.parametrize("argv", CLI_FLAGGING_OPS)
def test_cli_text_prints_each_listed_neighbor_with_its_note(store, capsys, argv):
    _seed_hub_with_every_neighbor_state()
    lines = _cli_lines(capsys, argv)
    header = lines.index("  ⚠ newly STALE (review/reconcile):")
    assert lines[header + 1:header + 3] == [
        "    - T2: T2 realizes the hub",
        "    - D3: D3 refines the hub",
    ]
    assert lines[header + 3:] == []


@pytest.mark.parametrize(
    "argv",
    [
        ["edit", "1", "--desc", "new", "--delta", "d"],
        ["edit-replace", "1", "--old", "body", "--new", "text", "--delta", "d"],
        ["reclassify", "1", "--kind", "schema", "--delta", "d"],
    ],
)
def test_cli_text_says_so_when_nobody_was_newly_flagged(store, capsys, argv):
    """The neighbor exists and is flagged already, so the old "no linked neighbors"
    wording would be false here."""

    def seed(core):
        core.add("hub", kind="design", description="hub body")  # 1
        core.add("other", kind="design")  # 2
        core.add("a", kind="production", deps={1: "a realizes", 2: "a realizes 2"})  # 3
        core.edit(2, description="changed", delta="flags a first")

    _core_call(seed)
    lines = _cli_lines(capsys, argv)
    assert "  no newly flagged neighbors." in lines
    assert "  ⚠ newly STALE (review/reconcile):" not in lines
    assert _worklist_ids() == [3]


def test_cli_text_honors_the_body_opt_in(store, capsys):
    """A flag accepted but ignored would be a silent failure (D2), as `ls` already
    prints the body in text under the same flag."""
    _core_call(lambda core: core.add("note", kind="meta", description="old"))
    lines = _cli_lines(capsys, ["edit", "1", "--desc", "the new body", "--delta", "d",
                                "--include-description"])
    assert "  the new body" in lines
    lines = _cli_lines(capsys, ["edit", "1", "--desc", "another body", "--delta", "d"])
    assert "  another body" not in lines


# --- D10: edit_append flags nobody; it keeps its delta and audit row ------------


def _seed_linked_notes():
    """Note 1 links notes 2 (unflagged) and 3 (flagged earlier through note 4)."""

    def seed(core):
        core.add("note one", kind="meta", description="body")  # 1
        core.add("note two", kind="meta", deps={1: "two reads one"})  # 2
        core.add("note three", kind="meta", deps={1: "three reads one"})  # 3
        core.add("note four", kind="meta", deps={3: "three reads four"})  # 4
        core.edit(4, description="changed", delta="flags note three")

    _core_call(seed)


def test_mcp_edit_append_flags_nobody_and_leaves_flags_alone(store):
    _seed_linked_notes()
    result = _call("edit_append", {"id": 1, "content": " more", "delta": "noted"})
    assert result["newly_stale"] == []
    assert _stale_flag(2) is False
    assert _stale_flag(3) is True  # an append clears nothing either
    assert _worklist_ids() == [3]


def test_cli_edit_append_flags_nobody_and_leaves_flags_alone(store, capsys):
    _seed_linked_notes()
    result = _cli_json(capsys, ["edit-append", "1", "--content", " more",
                                "--delta", "noted", "--json"])
    assert result["newly_stale"] == []
    assert _stale_flag(2) is False
    assert _stale_flag(3) is True
    assert _worklist_ids() == [3]


def test_cli_edit_append_text_says_nobody_was_flagged(store, capsys):
    _seed_linked_notes()
    lines = _cli_lines(capsys, ["edit-append", "1", "--content", " more",
                                "--delta", "noted"])
    assert "  no newly flagged neighbors." in lines


@pytest.mark.parametrize(
    "tool,args",
    [
        ("edit", {"id": 1, "description": "rewritten", "delta": "ruled"}),
        ("edit_replace_substring",
         {"id": 1, "old_string": "body", "new_string": "text", "delta": "ruled"}),
    ],
)
def test_the_other_edit_verbs_still_flag_on_a_meta(store, tool, args):
    """The way to tell a neighbor when a note really changes what it should say."""
    _seed_linked_notes()
    result = _call(tool, args)
    assert result["newly_stale"] == [{"id": 2, "because": "two reads one"}]
    assert _stale_flag(2) is True
    assert _worklist_ids() == [2, 3]


def test_an_append_does_not_block_closing_a_neighbor(store):
    """The converse of the close gate: no flag was written, so nothing blocks."""
    _core_call(lambda core: core.add("note one", kind="meta", description="body"))
    _core_call(lambda core: core.add("note two", kind="meta",
                                     deps={1: "two reads one"}))
    _call("edit_append", {"id": 1, "content": " more", "delta": "noted"})
    assert _call("close", {"id": 2}) is not None
    assert _core_call(lambda core: core.get(2).status) == "closed"


def test_an_append_still_writes_its_audit_row_with_its_delta(store):
    _seed_linked_notes()
    _call("edit_append", {"id": 1, "content": " more", "delta": "noted a thing"})
    revisions = _call("history", {"id": 1})["description_revisions"]
    assert len(revisions) == 1
    assert revisions[0]["delta"] == "noted a thing"
    assert revisions[0]["prev_description"] == "body"
    assert _core_call(lambda core: core.get(1).description) == "body more"


@pytest.mark.parametrize("delta", ["", "   "])
def test_an_append_with_no_delta_is_refused_and_changes_nothing(store, delta):
    _seed_linked_notes()

    async def scenario(session):
        return await session.call_tool(
            "edit_append", {"id": 1, "content": " more", "delta": delta}
        )

    response = _mcp(scenario)
    assert response.isError is True
    assert "edit_append requires a non-empty `delta`" in response.content[0].text
    assert _core_call(lambda core: core.get(1).description) == "body"


def test_the_delta_refusal_for_append_makes_no_cascade_claim(store):
    """D284: an append's delta is read from the audit row; no reconcile compares it
    against a neighbor's because, so its refusal must not say one does."""
    _seed_linked_notes()

    async def scenario(session):
        return await session.call_tool(
            "edit_append", {"id": 1, "content": " more", "delta": ""}
        )

    text = _mcp(scenario).content[0].text
    assert "history" in text
    assert "because" not in text


def test_the_delta_refusal_for_edit_still_explains_the_reconcile_read(store):
    _seed_linked_notes()

    async def scenario(session):
        return await session.call_tool(
            "edit", {"id": 1, "description": "x", "delta": ""}
        )

    text = _mcp(scenario).content[0].text
    assert "edit requires a non-empty `delta`" in text
    assert "because" in text


def test_missing_edge_suggestions_still_fire_on_an_append(store):
    _core_call(lambda core: core.add("note one", kind="meta"))
    _core_call(lambda core: core.add("note two", kind="meta"))

    async def scenario(session):
        return await session.call_tool(
            "edit_append", {"id": 1, "content": " see M2", "delta": "noted"}
        )

    envelope = json.loads(_mcp(scenario).content[0].text)
    assert len(envelope["missing_edge_suggestions"]) == 1
    assert "M2" in envelope["missing_edge_suggestions"][0]


def test_after_an_edit_then_an_append_show_reports_the_append_delta(store):
    """D34's accepted partial coverage: the entry shows the newest revision's delta,
    and `history` keeps both."""
    _core_call(lambda core: core.add("note one", kind="meta", description="body"))
    _core_call(lambda core: core.add("note two", kind="meta",
                                     deps={1: "two reads one"}))
    _call("edit", {"id": 1, "description": "ruled", "delta": "the edit delta"})
    _call("edit_append", {"id": 1, "content": " more", "delta": "the append delta"})
    entry = _call("show", {"id": 2})["links"][0]
    assert entry["id"] == 1
    assert entry["last_edit_delta"] == "the append delta"
    deltas = []
    for revision in _call("history", {"id": 1})["description_revisions"]:
        deltas.append(revision["delta"])
    assert deltas == ["the edit delta", "the append delta"]


# --- D211's write-result table, on both adapters --------------------------------


def _seed_spec_and_task():
    def seed(core):
        core.add("spec", kind="design", description="spec body")  # 1
        core.add("task", kind="production", description="task body",
                 deps={1: "task realizes spec"})  # 2

    _core_call(seed)


def _seed_closed_task():
    _seed_spec_and_task()
    _core_call(lambda core: core.close(2))


def _seed_labelled_task():
    _seed_spec_and_task()
    _core_call(lambda core: core.label_add(2, "alpha"))


def _seed_lone_spec():
    _core_call(lambda core: core.add("lone spec", kind="design"))


RESULT_TABLE = [
    ("close", _seed_spec_and_task, "close", {"id": 2},
     ["close", "2"], {"id": 2, "status": "closed"}),
    ("close, already closed (D20)", _seed_closed_task, "close", {"id": 2},
     ["close", "2"], {"id": 2, "status": "closed"}),
    ("wont_do", _seed_spec_and_task, "wont_do", {"id": 2, "reason": "dropped it"},
     ["wont-do", "2", "--reason", "dropped it"], {"id": 2, "status": "wont_do"}),
    ("retire", _seed_lone_spec, "retire", {"id": 1, "reason": "premise is gone"},
     ["retire", "1", "--reason", "premise is gone"], {"id": 1, "status": "retired"}),
    ("reopen", _seed_closed_task, "reopen", {"id": 2},
     ["reopen", "2"], {"id": 2, "status": "open"}),
    ("reopen, already open (D20)", _seed_spec_and_task, "reopen", {"id": 2},
     ["reopen", "2"], {"id": 2, "status": "open"}),
    ("reopen, a spec (D20)", _seed_spec_and_task, "reopen", {"id": 1},
     ["reopen", "1"], {"id": 1, "status": "spec"}),
    ("add", _seed_spec_and_task, "add", {"name": "new", "kind": "meta"},
     ["add", "new", "--kind", "meta"], {"id": 3}),
    ("label_add", _seed_labelled_task, "label_add", {"id": 2, "label": "beta"},
     ["label", "add", "2", "beta"], {"id": 2, "labels": ["alpha", "beta"]}),
    ("label_add, already on it (D20)", _seed_labelled_task, "label_add",
     {"id": 2, "label": "alpha"},
     ["label", "add", "2", "alpha"], {"id": 2, "labels": ["alpha"]}),
    ("label_rm", _seed_labelled_task, "label_rm", {"id": 2, "label": "alpha"},
     ["label", "rm", "2", "alpha"], {"id": 2, "labels": []}),
    ("label_rm, not on it (D20)", _seed_labelled_task, "label_rm",
     {"id": 2, "label": "nope"},
     ["label", "rm", "2", "nope"], {"id": 2, "labels": ["alpha"]}),
]


def _table_id(row):
    return row[0]


@pytest.mark.parametrize("row", RESULT_TABLE, ids=_table_id)
def test_mcp_write_results_match_the_table(store, row):
    _label, seed, tool, args, _argv, expected = row
    seed()
    assert _call(tool, args) == expected


@pytest.mark.parametrize("row", RESULT_TABLE, ids=_table_id)
def test_cli_json_write_results_match_the_table(store, capsys, row):
    _label, seed, _tool, _args, argv, expected = row
    seed()
    assert _cli_json(capsys, argv + ["--json"]) == expected


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"a": 2, "b": 3}, {"unlinked": {"a": 2, "b": 3}}),
        ({"a": 3, "b": 2}, {"unlinked": {"a": 3, "b": 2}}),
        ({"a": 1, "b": 3}, {"unlinked": {"a": 1, "b": 3}}),  # never linked (D20)
    ],
)
def test_mcp_link_rm_returns_the_pair_and_removes_the_edge(store, args, expected):
    def seed(core):
        core.add("spec", kind="design")  # 1
        core.add("a", kind="production", deps={1: "a realizes spec"})  # 2
        core.add("b", kind="design")  # 3
        core.link_add(2, 3, because="a calls b")

    _core_call(seed)
    assert _call("link_rm", args) == expected
    if expected["unlinked"]["a"] != 1:
        neighbors = []
        for entry in _core_call(lambda core: core.show(2)).links:
            neighbors.append(entry.id)
        assert neighbors == [1]


def test_add_still_stores_what_it_no_longer_returns(store):
    _call("add", {"name": "spec", "kind": "design"})
    assert _call("add", {
        "name": "task", "kind": "production", "description": "the body",
        "labels": ["alpha"], "deps": {1: "task realizes spec"},
    }) == {"id": 2}
    shown = _call("show", {"id": 2})
    assert shown["task"]["description"] == "the body"
    assert shown["labels"] == ["alpha"]
    assert shown["links"][0]["id"] == 1


def test_retire_and_wont_do_still_store_their_reason(store):
    _seed_spec_and_task()
    _call("wont_do", {"id": 2, "reason": "dropped it"})
    _call("add", {"name": "lone", "kind": "design"})
    _call("retire", {"id": 3, "reason": "premise is gone"})
    assert _core_call(lambda core: core.get(2).wont_do_reason) == "dropped it"
    assert _core_call(lambda core: core.get(3).wont_do_reason) == "premise is gone"


def test_the_label_nudge_still_rides_the_envelope_on_a_lean_result(store):
    _seed_spec_and_task()

    async def scenario(session):
        return await session.call_tool("label_add", {"id": 2, "label": "brandnew"})

    envelope = json.loads(_mcp(scenario).content[0].text)
    assert envelope["result"] == {"id": 2, "labels": ["brandnew"]}
    assert "brandnew" in envelope["label_nudge"]


# --- `stale`: short rows, no bodies, no opt-in (D211, D11) ----------------------


SHORT_ROW_KEYS = {"id", "prefixed_name", "kind", "status", "stale", "wont_do_reason"}
LONG_BODY = "a long body line " * 500


def _seed_stale_task_with_a_long_body():
    def seed(core):
        core.add("spec", kind="design")  # 1
        core.add("task", kind="production", description=LONG_BODY,
                 deps={1: "task realizes spec"})  # 2
        core.edit(1, description="changed", delta="flags the task")

    _core_call(seed)


def _assert_short_worklist(rows):
    assert len(rows) == 1
    assert set(rows[0]) == SHORT_ROW_KEYS
    assert rows[0]["id"] == 2
    assert rows[0]["stale"] is True
    assert rows[0]["prefixed_name"] == "T2 — task"
    assert "a long body line" not in json.dumps(rows)


def test_mcp_stale_returns_short_rows(store):
    _seed_stale_task_with_a_long_body()
    _assert_short_worklist(_call("stale", {}))


def test_cli_json_stale_returns_short_rows(store, capsys):
    _seed_stale_task_with_a_long_body()
    _assert_short_worklist(_cli_json(capsys, ["stale", "--json"]))


def test_mcp_stale_takes_no_opt_in(store):
    async def scenario(session):
        return await session.call_tool("stale", {"include_description": True})

    response = _mcp(scenario)
    assert response.isError is True
    assert "include_description" in response.content[0].text


# --- the close gate is untouched by the lean return (D14, D12) ------------------


def _seed_task_with_stale_neighbor():
    def seed(core):
        core.add("spec", kind="design")  # 1
        core.add("task", kind="production", deps={1: "task realizes spec"})  # 2
        core.edit(2, description="changed", delta="flags the spec")

    _core_call(seed)


def test_mcp_close_gate_still_refuses_a_stale_neighbor(store):
    _seed_task_with_stale_neighbor()

    async def scenario(session):
        return await session.call_tool("close", {"id": 2})

    response = _mcp(scenario)
    assert response.isError is True
    assert "REFUSED" in response.content[0].text
    assert _core_call(lambda core: core.get(2).status) == "open"


def test_cli_close_gate_still_refuses_a_stale_neighbor(store, capsys):
    _seed_task_with_stale_neighbor()
    capsys.readouterr()
    assert main(["close", "2", "--json"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "REFUSED" in captured.err
    assert _core_call(lambda core: core.get(2).status) == "open"


# --- CLI text for the same verbs (D211) -----------------------------------------


def test_cli_text_close_prints_the_task_and_no_neighbors(store, capsys):
    _seed_spec_and_task()
    assert _cli_lines(capsys, ["close", "2"]) == ["closed T2 [closed] task"]


def test_cli_text_wont_do_keeps_its_reason_and_drops_neighbors(store, capsys):
    _seed_spec_and_task()
    assert _cli_lines(capsys, ["wont-do", "2", "--reason", "dropped it"]) == [
        "marked wont_do T2 [wont_do] task",
        "  reason: dropped it",
    ]


def test_cli_text_retire_keeps_its_reason_and_drops_neighbors(store, capsys):
    def seed(core):
        core.add("spec", kind="design")  # 1
        core.add("task", kind="production", deps={1: "task realizes spec"})  # 2
        core.close(2)

    _core_call(seed)
    lines = _cli_lines(capsys, ["retire", "1", "--reason", "premise is gone"])
    assert len(lines) == 2
    assert lines[0].startswith("retired ") and "[retired] spec" in lines[0]
    assert lines[1] == "  reason: premise is gone"


def test_cli_text_add_still_prints_the_created_slice(store, capsys):
    _seed_spec_and_task()
    lines = _cli_lines(capsys, ["add", "new one", "--kind", "production",
                                "--desc", "the body", "--dep",
                                "1::new realizes spec", "--label", "alpha"])
    text = "\n".join(lines)
    assert lines[0] == "created T3 [open] new one"
    assert "the body" in text
    assert "labels: alpha" in text
    assert "spec" in text
