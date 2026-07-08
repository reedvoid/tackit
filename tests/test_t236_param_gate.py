"""T236 / D2 - strict adapter param gate.

Regression: ls(query='open') used to run UNFILTERED and dump every task -- the
FastMCP arg validator silently DROPPED the unknown `query` kwarg, so the agent
read 'returns everything regardless of status' as 'the filter is broken' and
never learned the real `status=` param. The gate sets extra='forbid' on the
shared FastMCP argument model, so an unrecognised parameter is rejected loudly
and the offending name is surfaced back to the agent (D2 fail-loud, extended to
the adapter param surface).

Driven through the real in-memory MCP protocol (same path an agent uses).
"""

import asyncio
import json

import pytest

from mcp.shared.memory import create_connected_server_and_client_session as connect

from tackit import mcp_server
from tackit.db import init_store


def _drive(tmp_path, monkeypatch, scenario):
    monkeypatch.chdir(tmp_path)
    init_store(tmp_path)

    async def runner():
        srv = mcp_server.build_server()
        async with connect(srv._mcp_server) as session:
            # D256 creation-gate: a production task must link a design/schema
            # slice at creation, so seed a spec anchor (id 1) first and link
            # the production task (id 2) to it.
            await session.call_tool("add", {"name": "spec anchor", "kind": "design"})
            await session.call_tool(
                "add",
                {
                    "name": "a task",
                    "kind": "production",
                    "deps": {"1": "realizes the anchor"},
                },
            )
            return await scenario(session)

    return asyncio.run(runner())


# --- Pass 1: the gate is advertised on every tool schema --------------------

def test_every_tool_schema_forbids_extra_params(tmp_path, monkeypatch):
    async def scenario(s):
        return [
            (t.name, t.inputSchema.get("additionalProperties"))
            for t in (await s.list_tools()).tools
        ]

    pairs = _drive(tmp_path, monkeypatch, scenario)
    assert pairs
    bad = [name for name, ap in pairs if ap is not False]
    assert not bad, f"tools not forbidding extras: {bad}"


# --- Pass 3 (negative space): THE regression --------------------------------

def test_ls_unknown_param_rejected_not_run_unfiltered(tmp_path, monkeypatch):
    async def scenario(s):
        return await s.call_tool("ls", {"query": "open"})

    r = _drive(tmp_path, monkeypatch, scenario)
    assert r.isError is True                     # loud, NOT a silent full dump
    msg = r.content[0].text
    assert "query" in msg                        # names the offending param
    # ...AND lists the valid params (the in-session guidance, T236)
    assert "status" in msg and "kind" in msg and "name_prefix" in msg


# --- Pass 2 (degenerate): the correct call still works ----------------------

def test_ls_valid_status_filter_still_works(tmp_path, monkeypatch):
    async def scenario(s):
        return await s.call_tool("ls", {"status": "open"})

    r = _drive(tmp_path, monkeypatch, scenario)
    assert r.isError is False
    assert len(json.loads(r.content[0].text)["result"]) == 1


def test_no_arg_call_still_works(tmp_path, monkeypatch):
    async def scenario(s):
        return await s.call_tool("ls", {})

    r = _drive(tmp_path, monkeypatch, scenario)
    assert r.isError is False


# --- Pass 2: unknown ALONGSIDE valid is rejected, not partially honoured -----

def test_unknown_param_alongside_valid_is_rejected(tmp_path, monkeypatch):
    async def scenario(s):
        return await s.call_tool("ls", {"status": "open", "bogus": 1})

    r = _drive(tmp_path, monkeypatch, scenario)
    assert r.isError is True


# --- Pass 1: the gate spans tools, not just ls ------------------------------

@pytest.mark.parametrize(
    "tool,args,bad,valid_param",
    [
        ("show", {"id": 1, "nope": 1}, "nope", "id"),
        ("search", {"terms": "x", "weird": 2}, "weird", "terms"),
        ("board", {"status": "open", "huh": 3}, "huh", "name_prefix"),
        ("edit", {"id": 1, "description": "y", "delta": "d", "oops": 1}, "oops", "description"),
        ("close", {"id": 1, "bad": 1}, "bad", "id"),
    ],
)
def test_gate_applies_across_tools(tmp_path, monkeypatch, tool, args, bad, valid_param):
    async def scenario(s):
        return await s.call_tool(tool, args)

    r = _drive(tmp_path, monkeypatch, scenario)
    assert r.isError is True                  # every tool fails loud...
    msg = r.content[0].text
    assert bad in msg                         # ...names the offending param...
    assert "accepts only:" in msg             # ...and the valid-param guidance ran...
    assert valid_param in msg                 # ...listing THIS tool's real params
