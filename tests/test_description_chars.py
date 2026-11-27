"""T373 (D211): `ls` and `stale` rows carry `description_chars`.

D211: the body's length in characters (the unit the body caps use), present on
every `ls` and `stale` row whether or not the body is included; `board` cards and
`show` do not carry it. MCP equals CLI --json.
"""

import asyncio
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from tackit import mcp_server
from tackit.cli import main
from tackit.core import Core
from tackit.db import init_store

NON_ASCII_BODY = "é漢字 ok"  # 6 characters, 11 bytes in UTF-8


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_store(tmp_path)
    c = Core.open(start=tmp_path)
    try:
        c.add("anchor", kind="design", description=NON_ASCII_BODY)  # D1
        c.add("task", kind="production", description="",
              deps={1: "realizes the anchor"})  # T2
        c.edit(2, delta="d", description="abc")  # flags D1 stale
    finally:
        c.close_conn()
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
    assert main(["--json"] + argv) == 0
    return json.loads(capsys.readouterr().out)


def _by_id(rows):
    out = {}
    for r in rows:
        out[r["id"]] = r
    return out


def test_ls_rows_count_characters_not_bytes(store):
    rows = _by_id(_mcp("ls", {}))
    assert len(NON_ASCII_BODY.encode("utf-8")) == 11
    assert rows[1]["description_chars"] == 6
    assert rows[2]["description_chars"] == 3


def test_an_empty_body_counts_zero(store, tmp_path):
    c = Core.open(start=tmp_path)
    try:
        c.add("empty note", kind="meta")  # M3
    finally:
        c.close_conn()
    assert _by_id(_mcp("ls", {}))[3]["description_chars"] == 0


def test_the_field_rides_with_the_body_too(store):
    rows = _by_id(_mcp("ls", {"include_description": True}))
    assert rows[1]["description"] == NON_ASCII_BODY
    assert rows[1]["description_chars"] == 6


def test_stale_rows_carry_it(store):
    rows = _mcp("stale", {})
    assert [r["id"] for r in rows] == [1]
    assert rows[0]["description_chars"] == 6


def test_cli_json_rows_equal_mcp(store, capsys):
    assert _cli(capsys, ["ls"]) == _mcp("ls", {})
    assert _cli(capsys, ["stale"]) == _mcp("stale", {})


def test_ls_row_keys_are_exactly_the_lean_set(store):
    # the first pin on ls row keys: a new or dropped field fails by name
    for row in _mcp("ls", {}):
        assert set(row) == {"id", "prefixed_name", "kind", "status", "stale",
                            "wont_do_reason", "description_chars"}


def test_board_cards_and_show_do_not_carry_it(store):
    cards = _mcp("board", {})
    assert cards
    for card in cards:
        assert "description_chars" not in card["task"]
    shown = _mcp("show", {"id": 1})
    assert "description_chars" not in shown["task"]
