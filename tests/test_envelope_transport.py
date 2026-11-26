"""T361 (D61, D211): the MCP envelope transport.

D61: every tool result is an envelope whose advisory keys appear only when set
(`result` always), serialized as compact JSON with non-ASCII kept literal, with no
`delta` echo; the full key set is one constant (`ENVELOPE_KEYS`). D211's
absent-not-null rule extends to the envelope. Every tool returns its text as the
only content (no structuredContent duplicate, no outputSchema), because the agent
reads the `content` text verbatim.

Each rule is pinned with its converse: a key that is set IS present, an unset key
is ABSENT (never null).
"""

import asyncio
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from tackit import mcp_server
from tackit.cli import main
from tackit.db import init_store


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


def _text(response):
    assert response.isError is not True, response.content[0].text
    assert len(response.content) == 1
    return response.content[0].text


# --- compact text -------------------------------------------------------------


def test_result_text_is_compact_json(store):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})
        return await s.call_tool("show", {"id": 1})

    text = _text(_mcp(scenario))
    parsed = json.loads(text)
    assert "\n" not in text
    # byte-equal to the compact re-serialization: no indentation, no ", " / ": "
    assert text == json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)


def test_non_ascii_stays_literal(store):
    async def scenario(s):
        await s.call_tool("add", {"name": "café décision", "kind": "design"})
        return await s.call_tool("show", {"id": 1})

    text = _text(_mcp(scenario))
    assert "café décision" in text
    assert "\\u00e9" not in text


def test_every_tool_has_no_output_schema(store):
    async def scenario(s):
        return (await s.list_tools()).tools

    tools = _mcp(scenario)
    assert len(tools) == 26
    for tool in tools:
        assert tool.outputSchema is None, tool.name


def test_a_result_carries_no_structured_duplicate(store):
    async def scenario(s):
        return await s.call_tool("summary", {})

    response = _mcp(scenario)
    _text(response)
    assert response.structuredContent is None


# --- keys only when set ---------------------------------------------------------


def test_envelope_keys_constant_names_the_real_keys():
    keys = mcp_server.ENVELOPE_KEYS
    assert len(keys) > 0
    assert "result" in keys
    assert "stale_alert" in keys
    assert len(set(keys)) == len(keys)
    assert "delta" not in keys


def test_a_clean_read_carries_only_result(store):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})
        return await s.call_tool("show", {"id": 1})

    env = json.loads(_text(_mcp(scenario)))
    assert set(env) == {"result"}


def test_an_empty_result_is_still_present(store):
    # `result` is always present, even empty: "only when set" means not None,
    # and an empty list is a real answer (nothing matched), not an unset field.
    async def scenario(s):
        return await s.call_tool("ls", {})

    env = json.loads(_text(_mcp(scenario)))
    assert env == {"result": []}


def test_a_set_key_is_present_and_no_key_is_ever_null(store):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})  # D1
        await s.call_tool("add", {"name": "task", "kind": "production",
                                  "deps": {1: "task realizes the anchor"}})  # T2
        # edit T2: flags D1 -> stale_alert is set
        return await s.call_tool("edit", {"id": 2, "description": "x", "delta": "d"})

    env = json.loads(_text(_mcp(scenario)))
    assert "stale_alert" in env and env["stale_alert"]["stale_task_ids"] == [1]
    assert "result" in env
    for key, value in env.items():
        assert value is not None, key
        assert key in mcp_server.ENVELOPE_KEYS, key


def test_a_new_label_sets_its_key(store):
    async def scenario(s):
        await s.call_tool("add", {"name": "spec anchor", "kind": "design"})
        return await s.call_tool("label_add", {"id": 1, "label": "brandnew"})

    env = json.loads(_text(_mcp(scenario)))
    assert "label_nudge" in env
    assert "stale_alert" not in env


# --- no delta echo ------------------------------------------------------------


@pytest.mark.parametrize("tool", ["edit", "edit_replace_substring", "edit_append", "reclassify"])
def test_no_delta_key_on_a_delta_bearing_op(store, tool):
    args = {
        "edit": {"id": 1, "description": "new body", "delta": "the shift"},
        "edit_replace_substring": {"id": 1, "old_string": "body", "new_string": "text",
                                   "delta": "the shift"},
        "edit_append": {"id": 1, "content": " more", "delta": "the shift"},
        "reclassify": {"id": 1, "kind": "schema", "delta": "the shift"},
    }[tool]
    # append is meta-only (D255); reclassify stays inside the spec partition
    kind = "meta" if tool in ("edit", "edit_replace_substring", "edit_append") else "design"

    async def scenario(s):
        await s.call_tool("add", {"name": "note", "kind": kind, "description": "body"})
        return await s.call_tool(tool, args)

    text = _text(_mcp(scenario))
    assert "delta" not in json.loads(text)
    assert "the shift" not in text


def test_cli_prints_no_delta_line(store, capsys):
    main(["add", "note", "--kind", "meta", "--desc", "body"])
    capsys.readouterr()
    assert main(["edit", "1", "--desc", "new body", "--delta", "the shift"]) == 0
    err = capsys.readouterr().err
    assert "delta:" not in err
    assert "the shift" not in err


def test_history_still_returns_the_delta(store):
    async def scenario(s):
        await s.call_tool("add", {"name": "note", "kind": "meta", "description": "body"})
        await s.call_tool("edit", {"id": 1, "description": "new body", "delta": "the shift"})
        return await s.call_tool("history", {"id": 1})

    env = json.loads(_text(_mcp(scenario)))
    assert env["result"]["description_revisions"][0]["delta"] == "the shift"
