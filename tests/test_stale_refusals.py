"""D56 + D171 refusal bank (T357): stale refusals carry why each neighbor matters.

close, wont_do and retire refuse a stale task (own flag) and a task with a stale
direct neighbor. The own-flag refusal points to `show`; the neighbor refusal
names every stale neighbor with its link's `because`. reconcile on a terminal
task says terminal tasks carry no flag (D137).

Expected strings are written out by hand from D171's bank, never rebuilt from
the code's helpers, so a dropped `because`, a missing neighbor or a reworded
instruction fails.
"""

import asyncio

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from tackit import mcp_server
from tackit.db import init_store
from tackit.errors import InvariantError


def _own_flag(x, verb):
    return (
        f"REFUSED: {x} is stale: a task it links to changed since it was last "
        f"reviewed. Run `show {x}`: on a stale task each link carries its "
        f"`because` (why that neighbor matters here) and the neighbor's latest "
        f"delta. Read them against {x}; `edit` it if it is now wrong, or "
        f"`reconcile` it if it still holds; then {verb} (D56)."
    )


def _neighbors(lead, x, verb, lines):
    return (
        f"REFUSED: {lead} would decide on top of a change nobody has reviewed. "
        f"Its directly linked stale neighbors:\n"
        + "\n".join(lines)
        + f"\nShow each and read it against that coupling; `edit` it if it is "
        f"now wrong, or `reconcile` it if it still holds; then {verb} {x} (D56)."
    )


# --- own flag -----------------------------------------------------------------


def _stale_open_task(core):
    core.add("spec", kind="design")  # D1
    core.add("task", kind="production", deps={1: "T2 realizes D1"})  # T2
    core.edit(1, description="changed", delta="D1 shifted")
    assert core.get(2).stale is True


def test_close_refuses_its_own_flag_and_points_to_show(core):
    _stale_open_task(core)
    with pytest.raises(InvariantError) as exc:
        core.close(2)
    assert str(exc.value) == _own_flag("T2", "close")
    assert core.get(2).status == "open"


def test_wont_do_refuses_its_own_flag_and_points_to_show(core):
    _stale_open_task(core)
    with pytest.raises(InvariantError) as exc:
        core.wont_do(2, reason="dropped for this test")
    assert str(exc.value) == _own_flag("T2", "wont_do")
    assert core.get(2).status == "open"


def test_retire_refuses_its_own_flag_and_points_to_show(core):
    core.add("first", kind="design")  # D1
    core.add("second", kind="design")  # D2
    core.link_add(1, 2, because="D2 refines D1")
    core.edit(2, description="changed", delta="D2 shifted")
    assert core.get(1).stale is True
    with pytest.raises(InvariantError) as exc:
        core.retire(1, reason="premise replaced for this test")
    assert str(exc.value) == _own_flag("D1", "retire")
    assert core.get(1).status == "spec"


# --- stale direct neighbors -----------------------------------------------------


def _task_with_two_stale_neighbors(core):
    """T2 is clean; its direct neighbors T3 and T4 are stale. D1 is the shared
    anchor whose edit flagged all three; T2 is then reconciled."""
    core.add("spec", kind="design")  # D1
    core.add("focal", kind="production", deps={1: "T2 realizes D1"})  # T2
    core.add("reader", kind="production", deps={1: "T3 realizes D1"})  # T3
    core.add("writer", kind="production", deps={1: "T4 realizes D1"})  # T4
    core.link_add(2, 3, because="T3 reads the file T2 writes")
    core.link_add(2, 4, because="T4 and T2 share the config format")
    core.edit(1, description="changed", delta="D1 shifted")
    core.reconcile(2)
    assert [core.get(i).stale for i in (1, 2, 3, 4)] == [False, False, True, True]


TWO_NEIGHBOR_LINES = [
    '  - T3 -- because: "T3 reads the file T2 writes"',
    '  - T4 -- because: "T4 and T2 share the config format"',
]


def test_close_names_each_stale_neighbor_with_its_because(core):
    _task_with_two_stale_neighbors(core)
    with pytest.raises(InvariantError) as exc:
        core.close(2)
    assert str(exc.value) == _neighbors("closing T2", "T2", "close", TWO_NEIGHBOR_LINES)
    assert core.get(2).status == "open"


def test_wont_do_names_each_stale_neighbor_with_its_because(core):
    _task_with_two_stale_neighbors(core)
    with pytest.raises(InvariantError) as exc:
        core.wont_do(2, reason="dropped for this test")
    assert str(exc.value) == _neighbors(
        "marking T2 wont_do", "T2", "wont_do", TWO_NEIGHBOR_LINES
    )
    assert core.get(2).status == "open"


def test_retire_names_each_stale_neighbor_with_its_because(core):
    core.add("dying", kind="design")  # D1
    core.add("middle", kind="design")  # D2
    core.add("far", kind="design")  # D3
    core.link_add(1, 2, because="D2 refines D1")
    core.link_add(2, 3, because="D3 constrains D2")
    core.edit(3, description="changed", delta="D3 shifted")
    assert [core.get(i).stale for i in (1, 2, 3)] == [False, True, False]
    with pytest.raises(InvariantError) as exc:
        core.retire(1, reason="premise replaced for this test")
    assert str(exc.value) == _neighbors(
        "retiring D1", "D1", "retire", ['  - D2 -- because: "D2 refines D1"']
    )
    assert core.get(1).status == "spec"


def test_the_neighbor_refusal_quotes_the_link_to_the_focal_task(core):
    """The because quoted is the one on the X-Y link, not some other link of
    Y's: T3's own link to D1 ("T3 realizes D1") must not appear."""
    _task_with_two_stale_neighbors(core)
    with pytest.raises(InvariantError) as exc:
        core.close(2)
    assert "T3 realizes D1" not in str(exc.value)
    assert "T4 realizes D1" not in str(exc.value)


# --- reconcile on a terminal task --------------------------------------------


def _closed_and_dropped(core):
    core.add("spec", kind="design")  # D1
    core.add("done", kind="production", deps={1: "T2 realizes D1"})  # T2
    core.add("dropped", kind="production", deps={1: "T3 realizes D1"})  # T3
    core.close(2)
    core.wont_do(3, reason="dropped for this test")


def test_reconcile_on_a_closed_task_says_it_carries_no_flag(core):
    _closed_and_dropped(core)
    with pytest.raises(InvariantError) as exc:
        core.reconcile(2)
    assert str(exc.value) == (
        "REFUSED: T2 is closed; closed, wont_do and retired tasks carry no "
        "stale flag, so there is nothing to reconcile. Drop it from the list "
        "(D137)."
    )


def test_reconcile_on_a_retired_slice_says_it_carries_no_flag(core):
    core.add("dead", kind="design")  # D1
    core.retire(1, reason="premise replaced for this test")
    with pytest.raises(InvariantError) as exc:
        core.reconcile(1)
    assert str(exc.value) == (
        "REFUSED: D1 is retired; closed, wont_do and retired tasks carry no "
        "stale flag, so there is nothing to reconcile. Drop it from the list "
        "(D137)."
    )


def test_batch_reconcile_names_every_offender_and_changes_nothing(core):
    _closed_and_dropped(core)
    core.add("live", kind="production", deps={1: "T4 realizes D1"})  # T4
    core.edit(1, description="changed", delta="D1 shifted")
    assert core.get(4).stale is True
    with pytest.raises(InvariantError) as exc:
        core.reconcile_many([4, 2, 99, 3])
    assert str(exc.value) == (
        "REFUSED: these ids cannot be reconciled: T2 (closed); 99 (no such "
        "task); T3 (wont_do). Closed, wont_do and retired tasks carry no stale "
        "flag, so there is nothing to reconcile; an unknown id names no task. "
        "Drop them from the list (D137). No row was changed."
    )
    assert core.get(4).stale is True  # the valid id was not cleared either


# --- the MCP surface carries the same text -----------------------------------


def test_mcp_close_shows_the_neighbor_because(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_store(tmp_path)

    async def scenario(s):
        await s.call_tool("add", {"name": "spec", "kind": "design"})
        await s.call_tool("add", {"name": "focal", "kind": "production",
                                  "deps": {1: "T2 realizes D1"}})
        await s.call_tool("add", {"name": "reader", "kind": "production",
                                  "deps": {1: "T3 realizes D1"}})
        await s.call_tool("link_add", {"a": 2, "b": 3,
                                       "because": "T3 reads the file T2 writes"})
        await s.call_tool("edit", {"id": 1, "description": "x", "delta": "D1 shifted"})
        await s.call_tool("reconcile", {"ids": [2]})
        return await s.call_tool("close", {"id": 2})

    async def runner():
        srv = mcp_server.build_server()
        async with connect(srv._mcp_server) as session:
            return await scenario(session)

    response = asyncio.run(runner())
    assert response.isError is True
    assert _neighbors(
        "closing T2", "T2", "close", ['  - T3 -- because: "T3 reads the file T2 writes"']
    ) in response.content[0].text


# --- no surface still teaches the record-only stale flag (D137) -------------

RETIRED_PHRASES = (
    "record-only",
    "record only",
    "terminal-stale",
    "closed-stale",
    "record of upstream change",
    "stale flags as historical record",
)


def _shipped_text_files():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    files = [root / "README.md", root / "src" / "tackit" / "data" / "SKILL.md"]
    files += sorted((root / "src" / "tackit" / "data" / "workflow" / "commands").glob("*.md"))
    files += sorted((root / "src" / "tackit").glob("*.py"))
    return root, files


def test_no_shipped_surface_still_calls_terminal_stale_record_only():
    """D137: terminal tasks carry no stale flag, so no README, skill, workflow
    command, docstring, help or refusal text may still describe one as a
    record. "archaeology" alone is allowed: it also names the audit table."""
    root, files = _shipped_text_files()
    names = {str(f.relative_to(root)) for f in files}
    assert "README.md" in names and "src/tackit/core.py" in names
    assert "src/tackit/data/workflow/commands/trim.md" in names
    hits = []
    for path in files:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            low = line.lower()
            for phrase in RETIRED_PHRASES:
                if phrase in low:
                    hits.append(f"{path.relative_to(root)}:{n}: {phrase}")
    assert hits == []
