"""MCP adapter integration (mcp_server.py was 0% covered).

Drives the FastMCP server through the real in-memory client<->server protocol (the
same path a real agent uses), so it exercises the D19 ``{stale_alert, result}``
envelope, refusals surfaced as ``isError`` content, and schema auto-discovery.

Tests are plain sync functions that run the async protocol exchange via
``asyncio.run`` — no async-pytest plugin needed.
"""

import asyncio
import json

from mcp.shared.memory import create_connected_server_and_client_session as connect

from tackit import mcp_server
from tackit.db import init_store


def _drive(tmp_path, monkeypatch, scenario):
    """Init a store, chdir to it (the tools discover the store from cwd), connect a
    client to the server, and run ``scenario(session)`` to completion."""
    monkeypatch.chdir(tmp_path)
    init_store(tmp_path)

    async def runner():
        srv = mcp_server.build_server()
        async with connect(srv._mcp_server) as session:
            return await scenario(session)

    return asyncio.run(runner())


def _envelope(call_result):
    return json.loads(call_result.content[0].text)


def test_mcp_registers_all_tools(tmp_path, monkeypatch):
    async def scenario(s):
        listing = await s.list_tools()
        return [t.name for t in listing.tools]

    names = _drive(tmp_path, monkeypatch, scenario)
    assert len(names) == 25  # +2 T179 (edit_append/replace); +1 T204 (links); +1 T216 (links_add)
    expected = {"add", "show", "search", "links", "edit", "edit_append",
                "edit_replace_substring", "close", "reconcile", "link_add",
                "links_add", "stale", "labels", "load", "board", "reclassify",
                "wont_do", "retire"}
    assert expected <= set(names)


def test_mcp_load(tmp_path, monkeypatch):
    async def scenario(s):
        return await s.call_tool(
            "load",
            {"plan": (
                "[anchor] Spec anchor\n  kind: design\n  desc: governing decision\n"
                "[a] first\n  kind: production\n"
                "  depends_on:\n    anchor :: realizes the anchor\n"
                "[b] second\n  kind: production\n"
                "  depends_on:\n    a :: test fixture: b couples to a's contract\n"
                "    anchor :: realizes the anchor\n"
            )},
        )

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    keymap = env["result"]["loaded"]
    assert set(keymap) == {"anchor", "a", "b"}


def test_mcp_board_returns_slices_with_edges(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # 1
        await s.call_tool("add", {"name": "base", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # 2
        await s.call_tool("add", {"name": "dep", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # 3
        await s.call_tool("link_add", {"a": 3, "b": 2, "because": "test fixture"})
        return await s.call_tool("board", {"status": "open"})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    cards = env["result"]
    assert len(cards) == 2  # base + dep (anchor is status=spec, filtered by status=open)
    t3 = next(c for c in cards if c["task"]["id"] == 3)
    # board carries each task's edges -- anchor D1 (D256 gate) + dep's own edge to base T2
    assert sorted(n["id"] for n in t3["links"]) == [1, 2]


def test_mcp_ls_and_board_name_prefix(tmp_path, monkeypatch):
    """T220: name_prefix flows through both ls and board over MCP."""
    async def scenario(s):
        await s.call_tool("add", {"name": "§9.1 search", "kind": "design"})
        await s.call_tool("add", {"name": "§8.2 other", "kind": "design"})
        ls = await s.call_tool("ls", {"name_prefix": "§9.1"})
        board = await s.call_tool("board", {"name_prefix": "§9.1"})
        return ls, board

    ls_res, board_res = _drive(tmp_path, monkeypatch, scenario)
    assert [t["id"] for t in _envelope(ls_res)["result"]] == [1]
    assert [c["task"]["id"] for c in _envelope(board_res)["result"]] == [1]


def test_mcp_success_wraps_result_in_envelope(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # 1
        await s.call_tool("add", {"name": "base", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # 2
        return await s.call_tool("show", {"id": 2})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert set(env.keys()) == {
        "stale_alert",
        "label_nudge",
        "delta",
        "code_check_reminder",  # D31 (v0.4)
        "coherence_nudge",  # D250
        "deadref_suggestions",  # D249
        "result",
    }
    assert env["stale_alert"] is None and env["label_nudge"] is None  # nothing stale, no new label
    assert env["delta"] is None  # T117: show is a read, no delta
    assert env["code_check_reminder"] is None  # D31: not a design/schema edit
    assert env["result"]["task"]["id"] == 2


def test_mcp_label_nudge_on_new_label(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # 1
        await s.call_tool("add", {"name": "a", "kind": "production", "labels": ["existing"],
                                   "deps": {1: "realizes the anchor"}})  # 2
        await s.call_tool("add", {"name": "b", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # T3
        return await s.call_tool("label_add", {"id": 3, "label": "brandnew"})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert env["label_nudge"] is not None
    assert "brandnew" in env["label_nudge"] and "existing" in env["label_nudge"]


def test_mcp_stale_alert_rides_in_envelope(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # 1
        await s.call_tool("add", {"name": "base", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # 2
        await s.call_tool("add", {"name": "dep", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # 3
        await s.call_tool("link_add", {"a": 3, "b": 2, "because": "test fixture"})
        # stales dep(3) AND the anchor(1) -- base links directly to both (D256 gate
        # cascade caution: the anchor is a cascade neighbor of every production task
        # that realizes it).
        return await s.call_tool("edit", {"id": 2, "description": "x", "delta": "test edit"})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert env["stale_alert"]["count"] == 2
    assert env["stale_alert"]["stale_task_ids"] == [1, 3]
    assert "STALE" in env["stale_alert"]["message"].upper()


# -------------------------------------------------------------------------
#  T242 -- lean-by-default return for the edit ops: the focal body the caller
#  just wrote is NOT echoed back; the newly_stale obligation always is;
#  include_description=True opts the body back in.
# -------------------------------------------------------------------------

def test_mcp_edit_return_is_lean_by_default(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # 1
        await s.call_tool("add", {"name": "base", "kind": "production",
                                   "description": "the original body",
                                   "deps": {1: "realizes the anchor"}})  # 2
        await s.call_tool("add", {"name": "dep", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # 3
        await s.call_tool("link_add", {"a": 3, "b": 2, "because": "test fixture"})
        return await s.call_tool("edit", {"id": 2, "description": "rewritten body", "delta": "test edit"})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert "description" not in env["result"]["task"]   # body dropped
    assert env["result"]["task"]["id"] == 2
    # obligation kept: dep(3) AND the anchor(1) -- both are base's direct link
    # neighbors (D256 gate cascade caution).
    assert [n["id"] for n in env["result"]["newly_stale"]] == [1, 3]


def test_mcp_edit_include_description_restores_body(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # 1
        await s.call_tool("add", {"name": "base", "kind": "production",
                                   "description": "the original body",
                                   "deps": {1: "realizes the anchor"}})  # 2
        return await s.call_tool(
            "edit",
            {"id": 2, "description": "rewritten body", "delta": "test edit", "include_description": True},
        )

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert env["result"]["task"]["description"] == "rewritten body"


def test_mcp_edit_append_return_is_lean_by_default(tmp_path, monkeypatch):
    async def scenario(s):
        # D255: append is meta-only, so the edit_append fixtures use meta tasks.
        await s.call_tool("add", {"name": "base", "kind": "meta", "description": "original"})
        await s.call_tool("add", {"name": "dep", "kind": "meta"})
        await s.call_tool("link_add", {"a": 2, "b": 1, "because": "test fixture"})
        return await s.call_tool("edit_append", {"id": 1, "content": " appended", "delta": "test append"})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert "description" not in env["result"]["task"]
    assert env["result"]["newly_stale"][0]["id"] == 2


def test_mcp_edit_append_include_description_restores_body(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "base", "kind": "meta", "description": "original"})
        return await s.call_tool(
            "edit_append",
            {"id": 1, "content": " appended", "delta": "test append", "include_description": True},
        )

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert env["result"]["task"]["description"] == "original appended"


def test_mcp_edit_replace_substring_return_is_lean_by_default(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # 1
        await s.call_tool("add", {"name": "base", "kind": "production", "description": "hello world",
                                   "deps": {1: "realizes the anchor"}})  # 2
        return await s.call_tool(
            "edit_replace_substring",
            {"id": 2, "old_string": "world", "new_string": "there", "delta": "test replace"},
        )

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert "description" not in env["result"]["task"]
    assert "newly_stale" in env["result"]


def test_mcp_edit_replace_substring_include_description_restores_body(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # 1
        await s.call_tool("add", {"name": "base", "kind": "production", "description": "hello world",
                                   "deps": {1: "realizes the anchor"}})  # 2
        return await s.call_tool(
            "edit_replace_substring",
            {"id": 2, "old_string": "world", "new_string": "there", "delta": "test replace", "include_description": True},
        )

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert env["result"]["task"]["description"] == "hello there"


def test_mcp_close_stale_refusal_is_error(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # 1
        await s.call_tool("add", {"name": "base", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # 2
        await s.call_tool("add", {"name": "dep", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # 3
        await s.call_tool("link_add", {"a": 3, "b": 2, "because": "test fixture"})
        await s.call_tool("edit", {"id": 2, "description": "x", "delta": "test edit"})  # stales T3 (+ anchor D1)
        return await s.call_tool("close", {"id": 3})

    result = _drive(tmp_path, monkeypatch, scenario)
    assert result.isError is True
    assert "REFUSED" in result.content[0].text


def test_mcp_dependency_aware_gate(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # D1
        await s.call_tool("add", {"name": "base", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # T2
        await s.call_tool("add", {"name": "mid", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # T3
        await s.call_tool("link_add", {"a": 3, "b": 2, "because": "test fixture"})
        await s.call_tool("add", {"name": "top", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # T4
        await s.call_tool("link_add", {"a": 4, "b": 3, "because": "test fixture"})
        await s.call_tool("edit", {"id": 2, "description": "x", "delta": "test edit"})  # stales T3 (+ anchor D1)
        return await s.call_tool("close", {"id": 4})  # T4 depends on stale T3

    result = _drive(tmp_path, monkeypatch, scenario)
    assert result.isError is True
    assert "REFUSED" in result.content[0].text


def test_mcp_search_returns_ranked_hits(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # 1
        await s.call_tool("add", {"name": "rotate JWT signing keys", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # 2
        await s.call_tool("add", {"name": "unrelated palette", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # 3
        return await s.call_tool("search", {"terms": "JWT"})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    hits = env["result"]
    assert hits and hits[0]["id"] == 2


def test_mcp_remaining_tools_all_work(tmp_path, monkeypatch):
    # Exercises the tool bodies not hit above: dep_rm, label_add/rm, reopen,
    # reconcile, ls, stale, render, history — each through the envelope.
    async def scenario(s):
        out = {}
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # D1
        await s.call_tool("add", {"name": "alpha widget", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # T2
        await s.call_tool("add", {"name": "beta widget", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})  # T3
        await s.call_tool("link_add", {"a": 3, "b": 2, "because": "test fixture"})
        out["link_rm"] = _envelope(await s.call_tool("link_rm", {"a": 3, "b": 2}))
        await s.call_tool("label_add", {"id": 2, "label": "tag"})
        out["label_rm"] = _envelope(await s.call_tool("label_rm", {"id": 2, "label": "tag"}))
        await s.call_tool("close", {"id": 2})
        out["reopen"] = _envelope(await s.call_tool("reopen", {"id": 2}))
        out["reconcile"] = _envelope(await s.call_tool("reconcile", {"ids": [2]}))
        out["ls"] = _envelope(await s.call_tool("ls", {}))
        out["stale"] = _envelope(await s.call_tool("stale", {}))
        out["render"] = _envelope(await s.call_tool("render", {"label": "design"}))
        out["history"] = _envelope(await s.call_tool("history", {"id": 2}))
        return out

    out = _drive(tmp_path, monkeypatch, scenario)
    assert out["reopen"]["result"]["status"] == "open"
    assert [t["id"] for t in out["ls"]["result"]] == [1, 2, 3]
    assert out["stale"]["result"] == []  # nothing stale
    assert isinstance(out["render"]["result"], str)
    assert len(out["history"]["result"]) >= 1


def test_mcp_retire_smoke(tmp_path, monkeypatch):
    """T175 Phase 3: the retire MCP tool reaches Core.retire() end-to-end.
    Verifies status spec->retired + reason persisted + envelope shape."""
    async def scenario(s):
        await s.call_tool("add", {"name": "d1 living spec", "kind": "design"})
        return _envelope(await s.call_tool(
            "retire",
            {"id": 1, "reason": "premise replaced by D99", "delta": "retiring"},
        ))

    env = _drive(tmp_path, monkeypatch, scenario)
    assert env["result"]["task"]["status"] == "retired"
    assert env["result"]["task"]["wont_do_reason"] == "premise replaced by D99"


# --- T179: edit_append + edit_replace_substring MCP wiring --------------


def test_mcp_edit_append_appends_through_protocol(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool(
            "add", {"name": "a", "kind": "meta", "description": "original"}
        )
        await s.call_tool(
            "edit_append",
            {"id": 1, "content": " + appended", "delta": "extend scope"},
        )
        return await s.call_tool("show", {"id": 1})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert env["result"]["task"]["description"] == "original + appended"


def test_mcp_edit_replace_substring_through_protocol(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # 1
        await s.call_tool(
            "add", {"name": "a", "kind": "production", "description": "hello world",
                     "deps": {1: "realizes the anchor"}}
        )
        await s.call_tool(
            "edit_replace_substring",
            {
                "id": 2,
                "old_string": "world",
                "new_string": "universe",
                "delta": "renamed token",
            },
        )
        return await s.call_tool("show", {"id": 2})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert env["result"]["task"]["description"] == "hello universe"


def test_mcp_edit_replace_substring_multi_match_isError(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # 1
        await s.call_tool(
            "add", {"name": "a", "kind": "production", "description": "foo foo foo",
                     "deps": {1: "realizes the anchor"}}
        )
        return await s.call_tool(
            "edit_replace_substring",
            {"id": 2, "old_string": "foo", "new_string": "bar", "delta": "seed"},
        )

    result = _drive(tmp_path, monkeypatch, scenario)
    assert result.isError
    assert "3 times" in result.content[0].text


def test_mcp_labels(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # 1
        await s.call_tool("add", {"name": "a", "kind": "production", "labels": ["core"],
                                   "deps": {1: "realizes the anchor"}})
        await s.call_tool("add", {"name": "b", "kind": "production", "labels": ["core", "docs"],
                                   "deps": {1: "realizes the anchor"}})
        return await s.call_tool("labels", {})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    names = [l["label"] for l in env["result"]]
    assert "core" in names and "docs" in names
    core_info = next(l for l in env["result"] if l["label"] == "core")
    assert core_info["count"] == 2 and len(core_info["samples"]) >= 1


def test_mcp_search_name_only_parameter(tmp_path, monkeypatch):
    """M181 #8d: the MCP `search` tool exposes `name_only`. End-to-end:
    two rows, one with the term in name only, one in description only;
    `name_only=True` returns the name match; default returns both."""
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # 1
        await s.call_tool("add", {
            "name": "polaris probe",
            "kind": "production",
            "description": "the body mentions cassiopeia once",
            "deps": {1: "realizes the anchor"},
        })
        await s.call_tool("add", {
            "name": "vega probe",
            "kind": "production",
            "description": "polaris appears here in description only",
            "deps": {1: "realizes the anchor"},
        })
        default = await s.call_tool("search", {"terms": "polaris"})
        nameonly = await s.call_tool(
            "search", {"terms": "polaris", "name_only": True}
        )
        return (default, nameonly)

    default, nameonly = _drive(tmp_path, monkeypatch, scenario)
    default_env = _envelope(default)
    nameonly_env = _envelope(nameonly)
    assert len(default_env["result"]) == 2, (
        f"Default search must match both rows; got: {default_env['result']!r}"
    )
    assert len(nameonly_env["result"]) == 1, (
        f"name_only search must match only the name row; got: "
        f"{nameonly_env['result']!r}"
    )
    assert nameonly_env["result"][0]["id"] == 2


def _setup_stale_scenario(s):
    """Helper for M181 #8b tests: produce a system with a stale worklist. Add
    a design anchor (D256 gate) + two production tasks realizing it, link the
    two, edit one -> the other (plus the anchor, its direct link neighbor per
    the D256 gate cascade caution) goes stale. Returns nothing; caller drives
    the read or write under test."""
    return [
        s.call_tool("add", {"name": "spec anchor", "kind": "design"}),  # 1
        s.call_tool("add", {"name": "a", "kind": "production",
                             "deps": {1: "realizes the anchor"}}),  # 2
        s.call_tool("add", {"name": "b", "kind": "production",
                             "deps": {1: "realizes the anchor"}}),  # 3
        s.call_tool("link_add", {
            "a": 2, "b": 3,
            "because": "test fixture coupling",
        }),
        s.call_tool("edit", {
            "id": 2,
            "delta": "test fixture edit to stale neighbor b",
            "description": "edited body",
        }),
    ]


def test_mcp_stale_alert_short_form_on_reads(tmp_path, monkeypatch):
    """M181 #8b: read MCP ops emit a SHORT stale_alert message instead of
    the strongly-worded obligation paragraph. `count` and `stale_task_ids`
    are unchanged. Cuts envelope size ~10× on browse-heavy sessions while
    preserving the signal (caller can `stale()` for the full list when
    they want to act).
    """
    async def scenario(s):
        for coro in _setup_stale_scenario(s):
            await coro
        return await s.call_tool("show", {"id": 2})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert env["stale_alert"] is not None
    msg = env["stale_alert"]["message"]
    assert "⚠" in msg and "see `stale` for the list" in msg, (
        f"Read op `show` must emit the short stale_alert form per M181 #8b "
        f"(M181 #8b: cuts ~2k tokens/call on browse-heavy sessions); got: "
        f"{msg!r}"
    )
    assert "STALE TASKS OUTSTANDING" not in msg, (
        "Read op must NOT emit the verbose long-form alert -- the "
        "teaching moment is at-write, not at-browse."
    )
    # stale worklist = dep(3) AND the anchor(1) -- both are a's direct link
    # neighbors (D256 gate cascade caution).
    assert env["stale_alert"]["count"] == 2
    assert env["stale_alert"]["stale_task_ids"] == [1, 3]


def test_mcp_stale_alert_full_form_on_writes(tmp_path, monkeypatch):
    """M181 #8b: write MCP ops emit the FULL strongly-worded stale_alert
    paragraph -- the at-cost teaching moment is preserved. Without this,
    the discipline rule ("never end a turn with worklist non-empty")
    loses its teeth at exactly the moment it's supposed to fire.
    """
    async def scenario(s):
        for coro in _setup_stale_scenario(s):
            await coro
        # Add another task -- write op -- envelope should carry full alert.
        return await s.call_tool("add", {"name": "c", "kind": "production",
                                          "deps": {1: "realizes the anchor"}})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert env["stale_alert"] is not None
    msg = env["stale_alert"]["message"]
    assert "STALE TASKS OUTSTANDING" in msg, (
        f"Write op `add` must emit the FULL stale_alert form per M181 #8b. "
        f"The discipline pressure ('never end a turn with worklist non-empty') "
        f"belongs at the at-cost moment; got: {msg!r}"
    )
    # stale worklist = dep(3) AND the anchor(1) -- both are a's direct link
    # neighbors (D256 gate cascade caution).
    assert env["stale_alert"]["count"] == 2
    assert env["stale_alert"]["stale_task_ids"] == [1, 3]


def test_mcp_link_add_returns_compact_confirmation(tmp_path, monkeypatch):
    """D39 #2: link_add returns a compact {"linked": {a,b,because}}, not `a`'s
    full slice. link_add is structural (no cascade), so bulk wiring shouldn't
    re-echo a high-degree node's whole neighborhood per edge."""
    async def scenario(s):
        await s.call_tool("add", {"name": "alpha spec", "kind": "design"})     # D1
        await s.call_tool("add", {"name": "beta impl", "kind": "production",  # T2
                                   "deps": {1: "T2 realizes D1's contract"}})
        return _envelope(await s.call_tool(
            "link_add",
            {"a": 2, "b": 1, "because": "T2 realizes D1's contract"},
        ))

    env = _drive(tmp_path, monkeypatch, scenario)
    assert env["result"] == {
        "linked": {"a": 2, "b": 1, "because": "T2 realizes D1's contract"}
    }
    # NOT a slice — no task body or neighbor lists echoed back.
    assert "task" not in env["result"]
    assert "links" not in env["result"]


def test_mcp_links_add_bulk_compact(tmp_path, monkeypatch):
    """T216: links_add wires many existing<->existing edges in one call and
    returns a compact {created, already_linked, created_pairs} (no because)."""
    async def scenario(s):
        # A separate D256-gate anchor (D1), distinct from "design slice" (D2)
        # which is the target of the links_add edges under test below -- so
        # those edges are genuinely NEW, not already-linked from creation.
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})    # D1
        await s.call_tool("add", {"name": "design slice", "kind": "design"})  # D2
        await s.call_tool("add", {"name": "impl a", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})        # T3
        await s.call_tool("add", {"name": "impl b", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})        # T4
        return _envelope(await s.call_tool("links_add", {"edges": [
            {"a": "T3", "b": "D2", "because": "T3 realizes D2"},
            {"a": "T4", "b": "D2", "because": "T4 realizes D2"},
        ]}))

    env = _drive(tmp_path, monkeypatch, scenario)
    assert env["result"]["created"] == 2
    assert env["result"]["already_linked"] == 0
    assert sorted(env["result"]["created_pairs"]) == [["T3", "D2"], ["T4", "D2"]]
    assert "because" not in str(env["result"])  # compact: no rationale echoed


def test_mcp_ls_lean_default_kind_and_opt_in(tmp_path, monkeypatch):
    """T212/D211: MCP ls is lean by default (no description), takes a kind
    filter, and include_description opts into full bodies."""
    async def scenario(s):
        await s.call_tool("add", {"name": "d", "kind": "design", "description": "design body"})
        await s.call_tool("add", {"name": "p", "kind": "production", "description": "prod body",
                                   "deps": {1: "p realizes d"}})
        lean = _envelope(await s.call_tool("ls", {}))
        full = _envelope(await s.call_tool("ls", {"include_description": True}))
        designs = _envelope(await s.call_tool("ls", {"kind": "design"}))
        return lean["result"], full["result"], designs["result"]

    lean, full, designs = _drive(tmp_path, monkeypatch, scenario)
    assert all("description" not in t for t in lean)            # lean default
    assert all("prefixed_name" in t for t in lean)             # scalars kept
    assert {t["description"] for t in full} == {"design body", "prod body"}
    assert [t["id"] for t in designs] == [1]                   # kind filter


def test_mcp_board_lean_default_and_opt_in(tmp_path, monkeypatch):
    """T212/D211: MCP board is lean by default — no task description; neighbors
    keep the graph shape but not because/last_edit_delta; flags opt in."""
    async def scenario(s):
        await s.call_tool("add", {"name": "design", "kind": "design"})  # D1
        await s.call_tool("add", {"name": "impl", "kind": "production",
                                  "description": "impl body",
                                  "deps": {1: "T2 realizes D1"}})       # T2
        await s.call_tool("link_add", {"a": 2, "b": 1, "because": "T2 realizes D1"})
        lean = _envelope(await s.call_tool("board", {}))
        full = _envelope(await s.call_tool("board",
                         {"include_description": True, "include_neighbor_because": True}))
        return lean["result"], full["result"]

    lean, full = _drive(tmp_path, monkeypatch, scenario)
    t2_lean = lean[1]  # ls orders by id: [D1, T2]
    assert t2_lean["task"]["id"] == 2
    assert "description" not in t2_lean["task"]
    nbr = t2_lean["links"][0]
    assert "because" not in nbr and "last_edit_delta" not in nbr
    assert nbr["prefixed_name"].startswith("D1")  # graph shape kept
    t2_full = full[1]
    assert t2_full["task"]["description"] == "impl body"
    assert t2_full["links"][0]["because"] == "T2 realizes D1"


def test_mcp_reconcile_ids_compact_payload_and_short_alert(tmp_path, monkeypatch):
    """D39 #1+#6: reconcile(ids) returns {"reconciled","remaining_stale"} and a
    SHORT stale alert during the sweep, not the full obligation paragraph."""
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # 1
        await s.call_tool("add", {"name": "hub", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})      # 2
        await s.call_tool("add", {"name": "leaf a", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})      # 3
        await s.call_tool("add", {"name": "leaf b", "kind": "production",
                                   "deps": {1: "realizes the anchor"}})      # 4
        await s.call_tool("link_add", {"a": 3, "b": 2, "because": "a realizes hub"})
        await s.call_tool("link_add", {"a": 4, "b": 2, "because": "b realizes hub"})
        await s.call_tool("edit", {"id": 2, "description": "shift", "delta": "hub shift"})  # stales anchor(1),3,4
        return _envelope(await s.call_tool("reconcile", {"ids": [3]}))

    env = _drive(tmp_path, monkeypatch, scenario)
    # anchor(1) + leaf b(4) remain stale after leaf a(3) is reconciled.
    assert env["result"] == {"reconciled": [3], "remaining_stale": 2}
    # Short alert form (M181 #8b), not the full obligation paragraph.
    assert env["stale_alert"] is not None
    msg = env["stale_alert"]["message"]
    assert "see `stale` for the list" in msg
    assert "STALE TASKS OUTSTANDING" not in msg
    assert env["stale_alert"]["count"] == 2


def test_mcp_links_op_is_reachable(tmp_path, monkeypatch):
    """T204: the links discovery op (D27) is reachable via MCP. No input ->
    the design+schema anchor layer (production excluded); ids=[x] -> x's
    depth-1 neighborhood. Previously core.links() existed but no MCP tool
    exposed it, so SKILL.md's prescribed discovery workflow was unreachable."""
    async def scenario(s):
        out = {}
        await s.call_tool("add", {"name": "decision slice", "kind": "design"})  # D1
        await s.call_tool("add", {"name": "store shape", "kind": "schema"})     # S2
        await s.call_tool("add", {"name": "impl task", "kind": "production",    # T3
                                   "deps": {1: "T3 realizes D1's decision"}})
        await s.call_tool("link_add", {"a": 3, "b": 1,
            "because": "T3 realizes D1's decision"})
        out["anchors"] = _envelope(await s.call_tool("links", {}))
        out["nbrs"] = _envelope(await s.call_tool("links", {"ids": [1]}))
        return out

    out = _drive(tmp_path, monkeypatch, scenario)
    assert [n["id"] for n in out["anchors"]["result"]] == [1, 2]  # design+schema only
    assert [n["id"] for n in out["nbrs"]["result"]] == [3]        # T3 at depth 1
