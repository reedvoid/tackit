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
    assert len(names) == 20  # T128: + reclassify; T132: + wont_do
    expected = {"add", "show", "search", "edit", "close", "reconcile", "link_add",
                "stale", "labels", "load", "board", "reclassify", "wont_do"}
    assert expected <= set(names)


def test_mcp_load(tmp_path, monkeypatch):
    async def scenario(s):
        return await s.call_tool(
            "load",
            {"plan": "[a] first\n  kind: production\n[b] second\n  kind: production\n  depends_on: a\n"},
        )

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    keymap = env["result"]["loaded"]
    assert set(keymap) == {"a", "b"}


def test_mcp_board_returns_slices_with_edges(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "base", "kind": "production"})
        await s.call_tool("add", {"name": "dep", "kind": "production"})
        await s.call_tool("link_add", {"a": 2, "b": 1, "because": "test fixture", "delta": "test"})
        return await s.call_tool("board", {"status": "open"})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    cards = env["result"]
    assert len(cards) == 2
    t2 = next(c for c in cards if c["task"]["id"] == 2)
    assert [n["id"] for n in t2["dependencies"]] == [1]  # board carries each task's edges


def test_mcp_success_wraps_result_in_envelope(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "base", "kind": "production"})
        return await s.call_tool("show", {"id": 1})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert set(env.keys()) == {"stale_alert", "label_nudge", "delta", "result"}
    assert env["stale_alert"] is None and env["label_nudge"] is None  # nothing stale, no new label
    assert env["delta"] is None  # T117: show is a read, no delta
    assert env["result"]["task"]["id"] == 1


def test_mcp_label_nudge_on_new_label(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "a", "kind": "production", "labels": ["existing"]})
        await s.call_tool("add", {"name": "b", "kind": "production"})  # T2
        return await s.call_tool("label_add", {"id": 2, "label": "brandnew"})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert env["label_nudge"] is not None
    assert "brandnew" in env["label_nudge"] and "existing" in env["label_nudge"]


def test_mcp_stale_alert_rides_in_envelope(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "base", "kind": "production"})
        await s.call_tool("add", {"name": "dep", "kind": "production"})
        await s.call_tool("link_add", {"a": 2, "b": 1, "because": "test fixture", "delta": "test"})
        return await s.call_tool("edit", {"id": 1, "description": "x", "delta": "test edit"})  # stales T2

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert env["stale_alert"]["count"] == 1
    assert env["stale_alert"]["stale_task_ids"] == [2]
    assert "STALE" in env["stale_alert"]["message"].upper()


def test_mcp_close_stale_refusal_is_error(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "base", "kind": "production"})
        await s.call_tool("add", {"name": "dep", "kind": "production"})
        await s.call_tool("link_add", {"a": 2, "b": 1, "because": "test fixture", "delta": "test"})
        await s.call_tool("edit", {"id": 1, "description": "x", "delta": "test edit"})  # stales T2
        return await s.call_tool("close", {"id": 2})

    result = _drive(tmp_path, monkeypatch, scenario)
    assert result.isError is True
    assert "REFUSED" in result.content[0].text


def test_mcp_dependency_aware_gate(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "base", "kind": "production"})  # T1
        await s.call_tool("add", {"name": "mid", "kind": "production"})  # T2
        await s.call_tool("link_add", {"a": 2, "b": 1, "because": "test fixture", "delta": "test"})
        await s.call_tool("add", {"name": "top", "kind": "production"})  # T3
        await s.call_tool("link_add", {"a": 3, "b": 2, "because": "test fixture", "delta": "test"})
        await s.call_tool("edit", {"id": 1, "description": "x", "delta": "test edit"})  # stales T2
        return await s.call_tool("close", {"id": 3})  # T3 depends on stale T2

    result = _drive(tmp_path, monkeypatch, scenario)
    assert result.isError is True
    assert "REFUSED" in result.content[0].text


def test_mcp_search_returns_ranked_hits(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "rotate JWT signing keys", "kind": "production"})
        await s.call_tool("add", {"name": "unrelated palette", "kind": "production"})
        return await s.call_tool("search", {"terms": "JWT"})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    hits = env["result"]
    assert hits and hits[0]["id"] == 1


def test_mcp_remaining_tools_all_work(tmp_path, monkeypatch):
    # Exercises the tool bodies not hit above: dep_rm, label_add/rm, reopen,
    # reconcile, ls, stale, render, history — each through the envelope.
    async def scenario(s):
        out = {}
        await s.call_tool("add", {"name": "alpha widget", "kind": "production"})  # T1
        await s.call_tool("add", {"name": "beta widget", "kind": "production"})  # T2
        await s.call_tool("link_add", {"a": 2, "b": 1, "because": "test fixture", "delta": "test"})
        out["link_rm"] = _envelope(await s.call_tool("link_rm", {"a": 2, "b": 1, "delta": "test"}))
        await s.call_tool("label_add", {"id": 1, "label": "tag"})
        out["label_rm"] = _envelope(await s.call_tool("label_rm", {"id": 1, "label": "tag"}))
        await s.call_tool("close", {"id": 1})
        out["reopen"] = _envelope(await s.call_tool("reopen", {"id": 1}))
        out["reconcile"] = _envelope(await s.call_tool("reconcile", {"id": 1}))
        out["ls"] = _envelope(await s.call_tool("ls", {}))
        out["stale"] = _envelope(await s.call_tool("stale", {}))
        out["render"] = _envelope(await s.call_tool("render", {"label": "design"}))
        out["history"] = _envelope(await s.call_tool("history", {"id": 1}))
        return out

    out = _drive(tmp_path, monkeypatch, scenario)
    assert out["reopen"]["result"]["status"] == "open"
    assert [t["id"] for t in out["ls"]["result"]] == [1, 2]
    assert out["stale"]["result"] == []  # nothing stale
    assert isinstance(out["render"]["result"], str)
    assert len(out["history"]["result"]) >= 1


def test_mcp_labels(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "a", "kind": "production", "labels": ["core"]})
        await s.call_tool("add", {"name": "b", "kind": "production", "labels": ["core", "docs"]})
        return await s.call_tool("labels", {})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    names = [l["label"] for l in env["result"]]
    assert "core" in names and "docs" in names
    core_info = next(l for l in env["result"] if l["label"] == "core")
    assert core_info["count"] == 2 and len(core_info["samples"]) >= 1
