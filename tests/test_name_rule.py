"""T367 (D211 Names): no return carries the bare `name` beside `prefixed_name`.

`prefixed_name` (`<L><id> — <name>`) holds the same untruncated name, so the bare
field is a duplicate. Pinned on the four returns that carried both before T367
(edit results, show's focal task, search hits, links candidates), through MCP and
CLI --json alike, with the converse beside each: the name is still there, inside
prefixed_name.
"""

import asyncio
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from tackit import mcp_server
from tackit.cli import main
from tackit.db import init_store

NAME = "café search spec"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_store(tmp_path)
    main(["add", NAME, "--kind", "design", "--desc", "a body"])  # D1
    main(["add", "task", "--kind", "production", "--dep", "1::task realizes the spec"])  # T2
    return tmp_path


def _mcp(tool, args):
    async def runner():
        srv = mcp_server.build_server()
        async with connect(srv._mcp_server) as session:
            return await session.call_tool(tool, args)

    response = asyncio.run(runner())
    assert response.isError is not True, response.content[0].text
    return json.loads(response.content[0].text)["result"]


def _cli(capsys, argv):
    capsys.readouterr()
    assert main(argv) == 0
    return json.loads(capsys.readouterr().out)


def _assert_prefixed_only(row, expected_prefixed):
    assert "name" not in row
    assert row["prefixed_name"] == expected_prefixed


def test_edit_result_has_no_bare_name(store, capsys):
    mcp = _mcp("edit", {"id": 1, "description": "new body", "delta": "d"})
    cli = _cli(capsys, ["edit", "1", "--desc", "newer body", "--delta", "d", "--json"])
    for result in (mcp, cli):
        _assert_prefixed_only(result["task"], f"D1 — {NAME}")


def test_show_focal_task_has_no_bare_name(store, capsys):
    mcp = _mcp("show", {"id": 1})
    cli = _cli(capsys, ["show", "1", "--json"])
    for result in (mcp, cli):
        _assert_prefixed_only(result["task"], f"D1 — {NAME}")
        assert result["task"]["description"] == "a body"  # the rest stays
        assert "created_at" in result["task"]


def test_search_hits_have_no_bare_name(store, capsys):
    mcp = _mcp("search", {"terms": "café"})
    cli = _cli(capsys, ["search", "café", "--json"])
    for hits in (mcp, cli):
        assert len(hits) == 1
        _assert_prefixed_only(hits[0], f"D1 — {NAME}")


def test_links_candidates_have_no_bare_name(store, capsys):
    mcp = _mcp("links", {})
    cli = _cli(capsys, ["links", "--json"])
    for rows in (mcp, cli):
        assert [r["id"] for r in rows] == [1]
        _assert_prefixed_only(rows[0], f"D1 — {NAME}")


def test_cli_text_still_shows_the_name(store, capsys):
    capsys.readouterr()
    assert main(["search", "café"]) == 0
    assert NAME in capsys.readouterr().out
