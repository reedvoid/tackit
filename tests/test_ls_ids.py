"""T372 (D57 ids axis, D213 resolver): `ls` limited to a list of task refs.

D57: each ref is an int, '#id' or a prefixed name, resolved through D213's shared
resolver with the kind letter checked. Validate all first: any unknown id or wrong
kind letter refuses the whole call and names every offender. An empty list is
refused. `ids` composes by AND with the other filters; rows keep the `order_by` sort
and duplicates collapse. `ls` only.

Each rule is pinned with its converse.
"""

import asyncio
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from tackit import mcp_server
from tackit.cli import main
from tackit.core import Core
from tackit.db import init_store
from tackit.errors import ValidationError

EMPTY_REFUSAL = (
    "ids filter must be non-empty (an empty list matches nothing; omit the filter "
    "to list every task) (D57)."
)


def _seed(core):
    core.add("anchor", kind="design")  # D1
    core.add("first task", kind="production", deps={1: "realizes the anchor"})  # T2
    core.add("note", kind="meta")  # M3
    core.add("second task", kind="production", deps={1: "realizes the anchor"})  # T4
    core.close(4)


def _ids(tasks):
    out = []
    for t in tasks:
        out.append(t.id)
    return out


# --- core: accepted refs ------------------------------------------------------


def test_int_hash_and_prefixed_refs_return_exactly_those_rows(core):
    _seed(core)
    assert _ids(core.ls(ids=[1, "#3", "T4"])) == [1, 3, 4]


def test_a_digit_string_is_an_id(core):
    _seed(core)
    assert _ids(core.ls(ids=["2"])) == [2]


def test_no_ids_filter_still_lists_everything(core):
    _seed(core)
    assert _ids(core.ls()) == [1, 2, 3, 4]


# --- core: refusals -------------------------------------------------------------


def test_a_wrong_kind_letter_is_refused(core):
    _seed(core)
    with pytest.raises(ValidationError) as exc:
        core.ls(ids=["T1"])
    assert "'T1'" in str(exc.value)
    assert "D1" in str(exc.value)  # the resolver names the real kind


def test_an_unknown_id_is_refused(core):
    _seed(core)
    with pytest.raises(ValidationError) as exc:
        core.ls(ids=["M99"])
    assert "'M99'" in str(exc.value)
    assert "no task with id 99" in str(exc.value)


def test_every_bad_ref_is_named_in_one_refusal(core):
    _seed(core)
    with pytest.raises(ValidationError) as exc:
        core.ls(ids=[2, "M99", "T1", "zz"])
    text = str(exc.value)
    assert text.startswith(
        "ls refused: 3 id(s) name no task or the wrong kind -- fix every one and re-run:"
    )
    for ref in ("'M99'", "'T1'", "'zz'"):
        assert ref in text, ref


def test_a_good_ref_beside_a_bad_one_still_refuses(core):
    # validate-all: no partial result rides out beside the refusal
    _seed(core)
    with pytest.raises(ValidationError):
        core.ls(ids=[1, 2, 99])


def test_an_empty_list_is_refused_with_its_text(core):
    _seed(core)
    with pytest.raises(ValidationError) as exc:
        core.ls(ids=[])
    assert str(exc.value) == EMPTY_REFUSAL


def test_a_bool_ref_is_refused(core):
    _seed(core)
    with pytest.raises(ValidationError):
        core.ls(ids=[True])


# --- core: composition ------------------------------------------------------------


def test_duplicates_collapse_to_one_row(core):
    _seed(core)
    assert _ids(core.ls(ids=[2, "T2", "#2"])) == [2]


def test_ids_and_status_compose_by_and(core):
    # T4 is closed: listed, but filtered out by status=open
    _seed(core)
    assert _ids(core.ls(ids=["T2", "T4"], status="open")) == [2]


def test_ids_and_kind_compose_by_and(core):
    _seed(core)
    assert _ids(core.ls(ids=[1, 2, 3], kind="production")) == [2]


def test_order_by_still_applies_to_an_ids_result(core):
    _seed(core)
    # status sort: closed (T4) before open (T2), then spec (D1)
    assert _ids(core.ls(ids=[1, 2, 4], order_by="status")) == [4, 2, 1]


def test_board_and_render_are_unchanged_by_the_filter(core):
    _seed(core)
    core.label_add(2, "grp")
    assert [t.id for t in core.ls(label="grp")] == [2]
    assert "first task" in core.render("grp")


# --- MCP and CLI --------------------------------------------------------------------


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_store(tmp_path)
    c = Core.open(start=tmp_path)
    try:
        _seed(c)
    finally:
        c.close_conn()
    return tmp_path


def _mcp(args):
    async def runner():
        srv = mcp_server.build_server()
        async with connect(srv._mcp_server) as session:
            return await session.call_tool("ls", args)

    return asyncio.run(runner())


def _row_ids(rows):
    out = []
    for r in rows:
        out.append(r["id"])
    return out


def test_mcp_ls_takes_mixed_refs(store):
    response = _mcp({"ids": ["D1", 3]})
    assert response.isError is not True, response.content[0].text
    assert _row_ids(json.loads(response.content[0].text)["result"]) == [1, 3]


def test_mcp_ls_refuses_a_bad_ref_naming_it(store):
    response = _mcp({"ids": ["D1", "T1", 99]})
    assert response.isError is True
    text = response.content[0].text
    assert "ls refused: 2 id(s)" in text
    assert "'T1'" in text and "99" in text


def test_mcp_ls_refuses_an_empty_list(store):
    response = _mcp({"ids": []})
    assert response.isError is True
    assert EMPTY_REFUSAL in response.content[0].text


def test_cli_ls_ids_matches_mcp(store, capsys):
    capsys.readouterr()
    assert main(["--json", "ls", "--ids", "D1", "3"]) == 0
    cli_rows = json.loads(capsys.readouterr().out)
    mcp_rows = json.loads(_mcp({"ids": ["D1", "3"]}).content[0].text)["result"]
    assert cli_rows == mcp_rows
    assert _row_ids(cli_rows) == [1, 3]


def test_cli_ls_ids_refusal_exits_nonzero_and_names_the_ref(store, capsys):
    capsys.readouterr()
    code = main(["ls", "--ids", "M99"])
    assert code != 0
    assert "'M99'" in capsys.readouterr().err


def test_cli_ls_ids_needs_a_value(store):
    with pytest.raises(SystemExit):
        main(["ls", "--ids"])



def test_board_takes_no_ids_on_mcp_or_cli(store):
    # D57: the filter is on ls only; the strict param gate refuses it on board
    async def runner():
        srv = mcp_server.build_server()
        async with connect(srv._mcp_server) as session:
            return await session.call_tool("board", {"ids": [1]})

    response = asyncio.run(runner())
    assert response.isError is True
    assert "ids" in response.content[0].text
    with pytest.raises(SystemExit):
        main(["board", "--ids", "1"])


def test_whitespace_around_a_ref_is_tolerated(core):
    _seed(core)
    assert _ids(core.ls(ids=[" T2 "])) == [2]
