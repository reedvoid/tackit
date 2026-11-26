"""T296 (D211 + D34): slice returns carry lean neighbor entries by default.

`because` and `last_edit_delta` stay on an entry only for a reconcile read: the
entry or the focal task is obligation-bearing stale (stale AND status open/spec).
An edit stales the edited task's NEIGHBORS, not the edited task, so reconciling
stale task A needs the fields on A's entry for non-stale B. That is the dangerous
direction these tests pin hardest: a reconcile read must never lose its fields.
"""

import asyncio
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from tackit import mcp_server
from tackit.cli import main
from tackit.core import LINK_BECAUSE_REMINDER
from tackit.db import init_store
from tackit.models import NeighborRef, obligation_stale, project_show_slice

LEAN_KEYS = {"id", "status", "stale", "kind", "prefixed_name"}


def _drive(tmp_path, monkeypatch, scenario):
    """Run ``scenario(session)`` against the real server over the in-memory MCP
    client protocol, the path a real agent uses."""
    monkeypatch.chdir(tmp_path)
    init_store(tmp_path)

    async def runner():
        srv = mcp_server.build_server()
        async with connect(srv._mcp_server) as session:
            return await scenario(session)

    return asyncio.run(runner())


def _envelope(call_result):
    return json.loads(call_result.content[0].text)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    return tmp_path


def _project(core, task_id, include_neighbor_because=False):
    return project_show_slice(
        core.show(task_id), include_neighbor_because=include_neighbor_because
    )


def _entry(card, neighbor_id):
    for entry in card["links"]:
        if entry["id"] == neighbor_id:
            return entry
    raise AssertionError(f"no link entry for {neighbor_id} in {card['links']}")


def _terminal_pair(core, terminal):
    """Build a reachable pair: spec focal 1 and terminal neighbor 2, then edit 1.
    The edit does not write 2 (D137). Returns nothing; ids are 1 and 2."""
    core.add("focal spec", kind="design")  # 1
    if terminal == "retired":
        core.add("neighbor spec", kind="design")  # 2
        core.link_add(1, 2, because="pair coupling")
        core.retire(2, reason="decision fully abandoned for this test scenario")
    else:
        core.add("neighbor", kind="production", deps={1: "pair coupling"})  # 2
        if terminal == "closed":
            core.close(2)
        else:
            core.wont_do(2, reason="scope dropped for this test scenario")
    core.edit(1, description="changed", delta="changed the focal spec")


# --- the predicate ----------------------------------------------------------


@pytest.mark.parametrize(
    "status,stale,kind,expected",
    [
        ("open", True, "production", True),
        ("spec", True, "design", True),
        ("closed", True, "production", False),
        ("wont_do", True, "production", False),
        ("retired", True, "design", False),
        ("open", False, "production", False),
        ("spec", False, "design", False),
        ("closed", False, "production", False),
    ],
)
def test_obligation_stale_truth_table(status, stale, kind, expected):
    ref = NeighborRef(id=1, name="n", status=status, stale=stale, kind=kind)
    assert obligation_stale(ref) is expected


# --- lean by default --------------------------------------------------------


def test_non_stale_entry_has_exactly_the_lean_keys(core):
    core.add("spec", kind="design")  # 1
    core.add("task", kind="production", deps={1: "task realizes spec"})  # 2
    entry = _project(core, 2)["links"][0]
    assert set(entry) == LEAN_KEYS
    assert entry["id"] == 1
    assert entry["prefixed_name"] == "D1 — spec"


def test_focal_task_and_labels_stay_complete(core):
    core.add("spec", kind="design")  # 1
    core.add(
        "task", kind="production", description="the body", labels=["x"],
        deps={1: "task realizes spec"},
    )  # 2
    card = _project(core, 2)
    assert card["task"]["prefixed_name"] == "T2 — task"  # D211 Names
    assert "name" not in card["task"]
    assert card["task"]["description"] == "the body"
    assert "created_at" in card["task"] and "updated_at" in card["task"]
    assert card["labels"] == ["x"]


def test_zero_links_keeps_an_empty_list(core):
    core.add("lonely", kind="design")
    assert _project(core, 1)["links"] == []


def test_projection_does_not_mutate_the_slice(core):
    core.add("spec", kind="design")  # 1
    core.add("task", kind="production", deps={1: "task realizes spec"})  # 2
    slice_ = core.show(2)
    before = slice_.model_dump(mode="json")
    project_show_slice(slice_, include_neighbor_because=False)
    assert slice_.model_dump(mode="json") == before


# --- reconcile reads keep the fields ----------------------------------------


def test_reconcile_read_of_stale_task_keeps_fields_on_the_edited_neighbor(core):
    """The dangerous direction. Edit b, then read stale a: entry b is NOT stale,
    yet it holds the because + delta needed to reconcile a."""
    core.add("b", kind="design")  # 1
    core.add("c", kind="design")  # 2
    core.add(
        "a", kind="production",
        deps={1: "a realizes b", 2: "a realizes c"},
    )  # 3
    core.edit(1, description="new", delta="changed b's output shape")
    card = _project(core, 3)
    assert card["task"]["stale"] is True
    edited = _entry(card, 1)
    assert edited["stale"] is False
    assert edited["because"] == "a realizes b"
    assert edited["last_edit_delta"] == "changed b's output shape"
    # Which neighbor staled a is not tracked, so every entry keeps them.
    other = _entry(card, 2)
    assert other["stale"] is False
    assert other["because"] == "a realizes c"
    assert other["last_edit_delta"] is None


def test_spec_focal_counts_as_obligation_bearing(core):
    core.add("a", kind="design")  # 1
    core.add("b", kind="design")  # 2
    core.link_add(1, 2, because="a extends b")
    core.edit(2, description="new", delta="changed b")
    entry = _entry(_project(core, 1), 2)
    assert entry["because"] == "a extends b"
    assert entry["last_edit_delta"] == "changed b"


def test_stale_entry_keeps_fields_and_reconciled_sibling_goes_lean(core):
    core.add("b", kind="design")  # 1
    core.add("a", kind="production", deps={1: "a realizes b"})  # 2
    core.add("c", kind="production", deps={1: "c realizes b"})  # 3
    core.edit(1, description="new", delta="changed b")
    core.reconcile(3)
    card = _project(core, 1)
    assert card["task"]["stale"] is False
    stale_entry = _entry(card, 2)
    assert stale_entry["because"] == "a realizes b"
    assert "last_edit_delta" in stale_entry and stale_entry["last_edit_delta"] is None
    assert set(_entry(card, 3)) == LEAN_KEYS


# --- terminal entries stay lean, stay listed, and are never flagged ---------


@pytest.mark.parametrize("terminal", ["closed", "wont_do", "retired"])
def test_terminal_entry_is_listed_lean_and_unflagged(core, terminal):
    _terminal_pair(core, terminal)
    entry = _entry(_project(core, 1), 2)
    assert set(entry) == LEAN_KEYS
    assert entry["stale"] is False
    assert entry["status"] == terminal


@pytest.mark.parametrize("terminal", ["closed", "wont_do", "retired"])
def test_terminal_focal_is_not_stale_and_keeps_no_fields(core, terminal):
    _terminal_pair(core, terminal)
    card = _project(core, 2)
    assert card["task"]["stale"] is False
    assert set(_entry(card, 1)) == LEAN_KEYS


def test_closed_focal_keeps_fields_only_on_its_open_stale_neighbor(core):
    """A closed focal task is never flagged (D137), so only the entry that is
    obligation-bearing stale keeps the fields."""
    core.add("spec", kind="design")  # 1
    core.add("closed one", kind="production", deps={1: "closed one realizes spec"})  # 2
    core.add("open one", kind="production", deps={1: "open one realizes spec"})  # 3
    core.link_add(2, 3, because="closed one shares a contract with open one")
    core.close(2)
    core.edit(1, description="new", delta="changed spec")
    card = _project(core, 2)
    assert card["task"]["stale"] is False
    assert _entry(card, 3)["because"] == "closed one shares a contract with open one"
    assert set(_entry(card, 1)) == LEAN_KEYS


# --- the opt-in -------------------------------------------------------------


def test_opt_in_returns_both_fields_on_every_entry(core):
    _terminal_pair(core, "closed")
    core.add("plain", kind="production", deps={1: "plain realizes focal"})  # 3
    core.reconcile(3)
    card = _project(core, 1, include_neighbor_because=True)
    terminal = _entry(card, 2)
    assert terminal["because"] == "pair coupling"
    assert "last_edit_delta" in terminal
    plain = _entry(card, 3)
    assert plain["because"] == "plain realizes focal"
    assert "last_edit_delta" in plain
    for entry in card["links"]:
        assert "name" not in entry


def test_null_delta_means_never_edited_and_absent_means_not_returned(core):
    core.add("spec", kind="design")  # 1
    core.add("task", kind="production", deps={1: "task realizes spec"})  # 2
    with_fields = _project(core, 2, include_neighbor_because=True)["links"][0]
    assert "last_edit_delta" in with_fields and with_fields["last_edit_delta"] is None
    assert "last_edit_delta" not in _project(core, 2)["links"][0]


# --- Core.show stays the full read ------------------------------------------


def test_core_show_still_returns_full_fields_on_non_stale_entries(core):
    core.add("spec", kind="design")  # 1
    core.add("task", kind="production", deps={1: "task realizes spec"})  # 2
    core.edit(1, description="new", delta="changed spec")
    core.reconcile(2)
    entry = core.show(2).links[0]
    assert entry.stale is False
    assert entry.name == "spec"
    assert entry.because == "task realizes spec"
    assert entry.last_edit_delta == "changed spec"


# --- because_reminder trigger (D34) -----------------------------------------


def test_reminder_fires_when_focal_is_open_stale_with_no_stale_neighbor(core):
    """The reconcile read of a stale task, which raw any(neighbor.stale) missed."""
    core.add("b", kind="design")  # 1
    core.add("a", kind="production", deps={1: "a realizes b"})  # 2
    core.edit(1, description="new", delta="changed b")
    slice_ = core.show(2)
    assert slice_.task.stale is True
    assert [n.stale for n in slice_.links] == [False]
    assert slice_.because_reminder == LINK_BECAUSE_REMINDER


def test_reminder_fires_on_stale_spec_focal(core):
    core.add("a", kind="design")  # 1
    core.add("b", kind="design")  # 2
    core.link_add(1, 2, because="a extends b")
    core.edit(2, description="new", delta="changed b")
    assert core.show(1).because_reminder == LINK_BECAUSE_REMINDER


def test_reminder_fires_on_open_stale_neighbor(core):
    core.add("b", kind="design")  # 1
    core.add("a", kind="production", deps={1: "a realizes b"})  # 2
    core.edit(1, description="new", delta="changed b")
    assert core.show(1).because_reminder == LINK_BECAUSE_REMINDER


@pytest.mark.parametrize("terminal", ["closed", "wont_do", "retired"])
def test_reminder_silent_when_the_only_neighbor_is_terminal(core, terminal):
    _terminal_pair(core, terminal)
    slice_ = core.show(1)
    assert [n.stale for n in slice_.links] == [False]
    assert slice_.because_reminder is None


@pytest.mark.parametrize("terminal", ["closed", "wont_do", "retired"])
def test_reminder_silent_when_focal_is_terminal(core, terminal):
    _terminal_pair(core, terminal)
    slice_ = core.show(2)
    assert slice_.task.stale is False
    assert slice_.because_reminder is None


def test_reminder_null_when_nothing_is_stale(core):
    core.add("spec", kind="design")  # 1
    core.add("task", kind="production", deps={1: "task realizes spec"})  # 2
    assert core.show(2).because_reminder is None


def test_reminder_still_fires_for_a_stale_task_with_no_links_left(core):
    """Negative space, decided by D34: a stale open task whose link was removed
    still owes a reconcile, so the trigger holds with an empty list."""
    core.add("b", kind="design")  # 1
    core.add("c", kind="design")  # 2
    core.add("a", kind="production", deps={1: "a realizes b", 2: "a realizes c"})  # 3
    core.edit(1, description="new", delta="changed b")
    core.link_rm(3, 1)
    core.link_rm(3, 2)
    slice_ = core.show(3)
    assert slice_.task.stale is True
    assert slice_.links == []
    assert slice_.because_reminder == LINK_BECAUSE_REMINDER


# --- MCP: show ---------------------------------------------------------------


def test_mcp_show_is_lean_by_default_and_opts_in(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec", "kind": "design"})
        await s.call_tool("add", {"name": "task", "kind": "production",
                                  "description": "the body",
                                  "deps": {1: "task realizes spec"}})
        lean = _envelope(await s.call_tool("show", {"id": 2}))
        full = _envelope(await s.call_tool(
            "show", {"id": 2, "include_neighbor_because": True}))
        return lean["result"], full["result"]

    lean, full = _drive(tmp_path, monkeypatch, scenario)
    assert set(lean["links"][0]) == LEAN_KEYS
    assert lean["task"]["prefixed_name"] == "T2 — task"  # D211 Names
    assert "name" not in lean["task"]
    assert lean["task"]["description"] == "the body"
    assert full["links"][0]["because"] == "task realizes spec"
    assert full["links"][0]["last_edit_delta"] is None
    assert "name" not in full["links"][0]


def test_mcp_reconcile_read_keeps_fields_reminder_and_short_alert(tmp_path, monkeypatch):
    """The dangerous direction over the real server."""
    async def scenario(s):
        await s.call_tool("add", {"name": "b", "kind": "design"})
        await s.call_tool("add", {"name": "a", "kind": "production",
                                  "deps": {1: "a realizes b"}})
        await s.call_tool("edit", {"id": 1, "description": "new",
                                   "delta": "changed b"})
        return _envelope(await s.call_tool("show", {"id": 2}))

    env = _drive(tmp_path, monkeypatch, scenario)
    entry = env["result"]["links"][0]
    assert entry["stale"] is False
    assert entry["because"] == "a realizes b"
    assert entry["last_edit_delta"] == "changed b"
    assert env["result"]["because_reminder"] == LINK_BECAUSE_REMINDER
    assert "see `stale` for the list" in env["stale_alert"]["message"]


def test_mcp_show_terminal_neighbor_stays_listed_lean_unflagged_without_reminder(tmp_path, monkeypatch):
    """D137 over MCP: the closed neighbor of an edited spec is listed lean,
    is not flagged, and raises no reminder."""
    async def scenario(s):
        await s.call_tool("add", {"name": "spec", "kind": "design"})
        await s.call_tool("add", {"name": "task", "kind": "production",
                                  "deps": {1: "task realizes spec"}})
        await s.call_tool("close", {"id": 2})
        await s.call_tool("edit", {"id": 1, "description": "new",
                                   "delta": "changed spec"})
        return _envelope(await s.call_tool("show", {"id": 1}))

    env = _drive(tmp_path, monkeypatch, scenario)
    entry = env["result"]["links"][0]
    assert set(entry) == LEAN_KEYS
    assert entry["stale"] is False and entry["status"] == "closed"
    assert env["result"]["because_reminder"] is None


# --- MCP: the other slice returns --------------------------------------------


def test_mcp_add_returns_only_the_id(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec", "kind": "design"})
        return _envelope(await s.call_tool(
            "add", {"name": "task", "kind": "production",
                    "description": "the body",
                    "deps": {1: "task realizes spec"}}))

    assert _drive(tmp_path, monkeypatch, scenario)["result"] == {"id": 2}


def test_mcp_link_rm_returns_the_pair(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec", "kind": "design"})
        await s.call_tool("add", {"name": "a", "kind": "production",
                                  "deps": {1: "a realizes spec"}})
        await s.call_tool("add", {"name": "b", "kind": "production",
                                  "deps": {1: "b realizes spec"}})
        await s.call_tool("link_add", {"a": 2, "b": 3, "because": "a calls b"})
        removed = _envelope(await s.call_tool("link_rm", {"a": 2, "b": 3}))
        shown = _envelope(await s.call_tool("show", {"id": 2}))
        return removed["result"], shown["result"]

    removed, shown = _drive(tmp_path, monkeypatch, scenario)
    assert removed == {"unlinked": {"a": 2, "b": 3}}
    assert [entry["id"] for entry in shown["links"]] == [1]


@pytest.mark.parametrize(
    "tool,args",
    [
        ("add", {"name": "x", "kind": "design"}),
        ("link_rm", {"a": 1, "b": 2}),
    ],
)
def test_mcp_only_show_takes_the_opt_in(tmp_path, monkeypatch, tool, args):
    async def scenario(s):
        return await s.call_tool(tool, {**args, "include_neighbor_because": True})

    result = _drive(tmp_path, monkeypatch, scenario)
    assert result.isError is True
    assert "include_neighbor_because" in result.content[0].text


def test_mcp_board_shape_is_unchanged(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "b", "kind": "design"})
        await s.call_tool("add", {"name": "a", "kind": "production",
                                  "deps": {1: "a realizes b"}})
        await s.call_tool("edit", {"id": 1, "description": "new",
                                   "delta": "changed b"})
        lean = _envelope(await s.call_tool("board", {}))
        full = _envelope(await s.call_tool(
            "board", {"include_neighbor_because": True}))
        return lean["result"], full["result"]

    lean, full = _drive(tmp_path, monkeypatch, scenario)
    # Lean board omits the fields even on a reconcile read; it has its own rule.
    assert set(lean[1]["links"][0]) == LEAN_KEYS
    assert full[1]["links"][0]["because"] == "a realizes b"
    assert full[1]["links"][0]["last_edit_delta"] == "changed b"


# --- stored data is untouched ------------------------------------------------


def test_lean_read_leaves_rationale_and_history_readable(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec", "kind": "design"})
        await s.call_tool("add", {"name": "task", "kind": "production",
                                  "deps": {1: "task realizes spec"}})
        await s.call_tool("edit", {"id": 1, "description": "new",
                                   "delta": "changed spec"})
        await s.call_tool("reconcile", {"ids": [2]})
        lean = _envelope(await s.call_tool("show", {"id": 2}))
        full = _envelope(await s.call_tool(
            "show", {"id": 2, "include_neighbor_because": True}))
        history = _envelope(await s.call_tool("history", {"id": 1}))
        return lean["result"], full["result"], history["result"]

    lean, full, history = _drive(tmp_path, monkeypatch, scenario)
    assert set(lean["links"][0]) == LEAN_KEYS
    assert full["links"][0]["because"] == "task realizes spec"
    assert full["links"][0]["last_edit_delta"] == "changed spec"
    assert history["description_revisions"][0]["delta"] == "changed spec"


# --- payload size ------------------------------------------------------------


def test_lean_links_are_smaller_and_carry_no_rationale(core):
    core.add("hub", kind="design")  # 1
    rationales = []
    for index in range(20):
        rationale = f"neighbor {index} realizes the hub's contract in a long way"
        rationales.append(rationale)
        core.add(f"neighbor {index}", kind="production", deps={1: rationale})
    full = json.dumps(core.show(1).model_dump(mode="json")["links"])
    lean = json.dumps(project_show_slice(
        core.show(1), include_neighbor_because=False)["links"])
    assert len(lean) < len(full)
    assert len(project_show_slice(
        core.show(1), include_neighbor_because=False)["links"]) == 20
    for rationale in rationales:
        assert rationale in full
        assert rationale not in lean


# --- CLI ---------------------------------------------------------------------


def test_cli_show_json_is_lean_by_default_and_opts_in(cli, capsys):
    main(["add", "spec", "--kind", "design"])
    main(["add", "task", "--kind", "production", "--dep", "1::task realizes spec"])
    capsys.readouterr()
    assert main(["show", "2", "--json"]) == 0
    lean = json.loads(capsys.readouterr().out)
    assert set(lean["links"][0]) == LEAN_KEYS
    assert lean["task"]["prefixed_name"] == "T2 — task"  # D211 Names
    assert "name" not in lean["task"]
    assert main(["show", "2", "--json", "--include-neighbor-because"]) == 0
    full = json.loads(capsys.readouterr().out)
    assert full["links"][0]["because"] == "task realizes spec"


def test_cli_reconcile_read_keeps_fields(cli, capsys):
    main(["add", "b", "--kind", "design", "--desc", "old text"])
    main(["add", "a", "--kind", "production", "--dep", "1::a realizes b"])
    main(["edit-replace", "1", "--old", "old", "--new", "new", "--delta", "changed b"])
    capsys.readouterr()
    assert main(["show", "2", "--json"]) == 0
    entry = json.loads(capsys.readouterr().out)["links"][0]
    assert entry["because"] == "a realizes b"
    assert entry["last_edit_delta"] == "changed b"


def test_cli_add_json_returns_only_the_id(cli, capsys):
    main(["add", "spec", "--kind", "design"])
    capsys.readouterr()
    assert main(["add", "task", "--kind", "production", "--json",
                 "--dep", "1::task realizes spec"]) == 0
    assert json.loads(capsys.readouterr().out) == {"id": 2}


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["link", "add", "2", "3", "--because", "a calls b", "--json"],
         {"linked": {"a": 2, "b": 3, "because": "a calls b"}}),
        (["link", "rm", "2", "3", "--json"], {"unlinked": {"a": 2, "b": 3}}),
    ],
)
def test_cli_link_json_is_compact(cli, capsys, argv, expected):
    main(["add", "spec", "--kind", "design"])
    main(["add", "a", "--kind", "production", "--dep", "1::a realizes spec"])
    main(["add", "b", "--kind", "production", "--dep", "1::b realizes spec"])
    if argv[1] == "rm":
        main(["link", "add", "2", "3", "--because", "a calls b"])
    capsys.readouterr()
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_cli_text_output_still_prints_neighbor_names(cli, capsys):
    main(["add", "distinctive spec name", "--kind", "design"])
    main(["add", "task", "--kind", "production", "--dep", "1::task realizes spec"])
    capsys.readouterr()
    assert main(["show", "2"]) == 0
    assert "distinctive spec name" in capsys.readouterr().out
    assert main(["show", "2", "--include-neighbor-because"]) == 0
    assert "distinctive spec name" in capsys.readouterr().out


# --- user-visible text -------------------------------------------------------


def _one_line(text):
    return " ".join(text.split())


def test_mcp_show_description_states_the_default_and_the_opt_in(tmp_path, monkeypatch):
    async def scenario(s):
        return await s.list_tools()

    listing = _drive(tmp_path, monkeypatch, scenario)
    description = None
    for tool in listing.tools:
        if tool.name == "show":
            description = _one_line(tool.description)
    assert description is not None
    assert "The task is returned in full." in description
    assert "Links are lean by default (id/prefixed_name/status/stale/kind)." in description
    assert (
        "`because` and `last_edit_delta` appear only when a link or this task is "
        "stale and open/spec, for reconciliation." in description
    )
    assert (
        "Pass include_neighbor_because=True for them on every link." in description
    )


def test_cli_show_help_states_the_opt_in(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["show", "--help"])
    assert exit_info.value.code == 0
    assert (
        "include edge rationales on every link in --json (default: only where "
        "reconciliation needs them)" in _one_line(capsys.readouterr().out)
    )


def test_readme_states_the_show_default():
    import pathlib

    readme = pathlib.Path(__file__).resolve().parent.parent / "README.md"
    text = _one_line(readme.read_text())
    assert (
        "`show` — full task + labels + lean linked tasks; `because` + "
        "`last_edit_delta` where reconciliation needs them, or on every link with "
        "`include_neighbor_because=True`" in text
    )
    assert (
        "`show` returns the full task with lean links, keeping `because` / "
        "`last_edit_delta` only on open/spec stale reads; "
        "`include_neighbor_because=True` returns them on every link." in text
    )
    # The old claim must be gone, not just outnumbered.
    assert "+ because + last_edit_delta on each" not in text
