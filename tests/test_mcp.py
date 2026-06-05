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
                "[a] first\n  kind: production\n"
                "[b] second\n  kind: production\n"
                "  depends_on:\n    a :: test fixture: b couples to a's contract\n"
            )},
        )

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    keymap = env["result"]["loaded"]
    assert set(keymap) == {"a", "b"}


def test_mcp_board_returns_slices_with_edges(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "base", "kind": "production"})
        await s.call_tool("add", {"name": "dep", "kind": "production"})
        await s.call_tool("link_add", {"a": 2, "b": 1, "because": "test fixture"})
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
    assert set(env.keys()) == {
        "stale_alert",
        "label_nudge",
        "delta",
        "code_check_reminder",  # D31 (v0.4)
        "result",
    }
    assert env["stale_alert"] is None and env["label_nudge"] is None  # nothing stale, no new label
    assert env["delta"] is None  # T117: show is a read, no delta
    assert env["code_check_reminder"] is None  # D31: not a design/schema edit
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
        await s.call_tool("link_add", {"a": 2, "b": 1, "because": "test fixture"})
        return await s.call_tool("edit", {"id": 1, "description": "x", "delta": "test edit"})  # stales T2

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert env["stale_alert"]["count"] == 1
    assert env["stale_alert"]["stale_task_ids"] == [2]
    assert "STALE" in env["stale_alert"]["message"].upper()


def test_mcp_close_stale_refusal_is_error(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "base", "kind": "production"})
        await s.call_tool("add", {"name": "dep", "kind": "production"})
        await s.call_tool("link_add", {"a": 2, "b": 1, "because": "test fixture"})
        await s.call_tool("edit", {"id": 1, "description": "x", "delta": "test edit"})  # stales T2
        return await s.call_tool("close", {"id": 2})

    result = _drive(tmp_path, monkeypatch, scenario)
    assert result.isError is True
    assert "REFUSED" in result.content[0].text


def test_mcp_dependency_aware_gate(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool("add", {"name": "base", "kind": "production"})  # T1
        await s.call_tool("add", {"name": "mid", "kind": "production"})  # T2
        await s.call_tool("link_add", {"a": 2, "b": 1, "because": "test fixture"})
        await s.call_tool("add", {"name": "top", "kind": "production"})  # T3
        await s.call_tool("link_add", {"a": 3, "b": 2, "because": "test fixture"})
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
        await s.call_tool("link_add", {"a": 2, "b": 1, "because": "test fixture"})
        out["link_rm"] = _envelope(await s.call_tool("link_rm", {"a": 2, "b": 1}))
        await s.call_tool("label_add", {"id": 1, "label": "tag"})
        out["label_rm"] = _envelope(await s.call_tool("label_rm", {"id": 1, "label": "tag"}))
        await s.call_tool("close", {"id": 1})
        out["reopen"] = _envelope(await s.call_tool("reopen", {"id": 1}))
        out["reconcile"] = _envelope(await s.call_tool("reconcile", {"ids": [1]}))
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
            "add", {"name": "a", "kind": "production", "description": "original"}
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
        await s.call_tool(
            "add", {"name": "a", "kind": "production", "description": "hello world"}
        )
        await s.call_tool(
            "edit_replace_substring",
            {
                "id": 1,
                "old_string": "world",
                "new_string": "universe",
                "delta": "renamed token",
            },
        )
        return await s.call_tool("show", {"id": 1})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert env["result"]["task"]["description"] == "hello universe"


def test_mcp_edit_replace_substring_multi_match_isError(tmp_path, monkeypatch):
    async def scenario(s):
        await s.call_tool(
            "add", {"name": "a", "kind": "production", "description": "foo foo foo"}
        )
        return await s.call_tool(
            "edit_replace_substring",
            {"id": 1, "old_string": "foo", "new_string": "bar", "delta": "seed"},
        )

    result = _drive(tmp_path, monkeypatch, scenario)
    assert result.isError
    assert "3 times" in result.content[0].text


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


def test_mcp_search_name_only_parameter(tmp_path, monkeypatch):
    """M181 #8d: the MCP `search` tool exposes `name_only`. End-to-end:
    two rows, one with the term in name only, one in description only;
    `name_only=True` returns the name match; default returns both."""
    async def scenario(s):
        await s.call_tool("add", {
            "name": "polaris probe",
            "kind": "production",
            "description": "the body mentions cassiopeia once",
        })
        await s.call_tool("add", {
            "name": "vega probe",
            "kind": "production",
            "description": "polaris appears here in description only",
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
    assert nameonly_env["result"][0]["id"] == 1


def _setup_stale_scenario(s):
    """Helper for M181 #8b tests: produce a system with exactly one stale
    task. Add two production tasks, link them, edit one -> the other goes
    stale. Returns nothing; caller drives the read or write under test."""
    return [
        s.call_tool("add", {"name": "a", "kind": "production"}),
        s.call_tool("add", {"name": "b", "kind": "production"}),
        s.call_tool("link_add", {
            "a": 1, "b": 2,
            "because": "test fixture coupling",
        }),
        s.call_tool("edit", {
            "id": 1,
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
        return await s.call_tool("show", {"id": 1})

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
    assert env["stale_alert"]["count"] == 1
    assert env["stale_alert"]["stale_task_ids"] == [2]


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
        return await s.call_tool("add", {"name": "c", "kind": "production"})

    env = _envelope(_drive(tmp_path, monkeypatch, scenario))
    assert env["stale_alert"] is not None
    msg = env["stale_alert"]["message"]
    assert "STALE TASKS OUTSTANDING" in msg, (
        f"Write op `add` must emit the FULL stale_alert form per M181 #8b. "
        f"The discipline pressure ('never end a turn with worklist non-empty') "
        f"belongs at the at-cost moment; got: {msg!r}"
    )
    assert env["stale_alert"]["count"] == 1
    assert env["stale_alert"]["stale_task_ids"] == [2]


def test_mcp_link_add_returns_compact_confirmation(tmp_path, monkeypatch):
    """D39 #2: link_add returns a compact {"linked": {a,b,because}}, not `a`'s
    full slice. link_add is structural (no cascade), so bulk wiring shouldn't
    re-echo a high-degree node's whole neighborhood per edge."""
    async def scenario(s):
        await s.call_tool("add", {"name": "alpha spec", "kind": "design"})     # D1
        await s.call_tool("add", {"name": "beta impl", "kind": "production"})  # T2
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
    assert "dependencies" not in env["result"]


def test_mcp_links_add_bulk_compact(tmp_path, monkeypatch):
    """T216: links_add wires many existing<->existing edges in one call and
    returns a compact {created, already_linked, created_pairs} (no because)."""
    async def scenario(s):
        await s.call_tool("add", {"name": "design slice", "kind": "design"})  # D1
        await s.call_tool("add", {"name": "impl a", "kind": "production"})    # T2
        await s.call_tool("add", {"name": "impl b", "kind": "production"})    # T3
        return _envelope(await s.call_tool("links_add", {"edges": [
            {"a": "T2", "b": "D1", "because": "T2 realizes D1"},
            {"a": "T3", "b": "D1", "because": "T3 realizes D1"},
        ]}))

    env = _drive(tmp_path, monkeypatch, scenario)
    assert env["result"]["created"] == 2
    assert env["result"]["already_linked"] == 0
    assert sorted(env["result"]["created_pairs"]) == [["T2", "D1"], ["T3", "D1"]]
    assert "because" not in str(env["result"])  # compact: no rationale echoed


def test_mcp_reconcile_ids_compact_payload_and_short_alert(tmp_path, monkeypatch):
    """D39 #1+#6: reconcile(ids) returns {"reconciled","remaining_stale"} and a
    SHORT stale alert during the sweep, not the full obligation paragraph."""
    async def scenario(s):
        await s.call_tool("add", {"name": "hub", "kind": "production"})     # 1
        await s.call_tool("add", {"name": "leaf a", "kind": "production"})  # 2
        await s.call_tool("add", {"name": "leaf b", "kind": "production"})  # 3
        await s.call_tool("link_add", {"a": 2, "b": 1, "because": "a realizes hub"})
        await s.call_tool("link_add", {"a": 3, "b": 1, "because": "b realizes hub"})
        await s.call_tool("edit", {"id": 1, "description": "shift", "delta": "hub shift"})  # stales 2,3
        return _envelope(await s.call_tool("reconcile", {"ids": [2]}))

    env = _drive(tmp_path, monkeypatch, scenario)
    assert env["result"] == {"reconciled": [2], "remaining_stale": 1}
    # Short alert form (M181 #8b), not the full obligation paragraph.
    assert env["stale_alert"] is not None
    msg = env["stale_alert"]["message"]
    assert "see `stale` for the list" in msg
    assert "STALE TASKS OUTSTANDING" not in msg
    assert env["stale_alert"]["count"] == 1


def test_mcp_links_op_is_reachable(tmp_path, monkeypatch):
    """T204: the links discovery op (D27) is reachable via MCP. No input ->
    the design+schema anchor layer (production excluded); ids=[x] -> x's
    depth-1 neighborhood. Previously core.links() existed but no MCP tool
    exposed it, so SKILL.md's prescribed discovery workflow was unreachable."""
    async def scenario(s):
        out = {}
        await s.call_tool("add", {"name": "decision slice", "kind": "design"})  # D1
        await s.call_tool("add", {"name": "store shape", "kind": "schema"})     # S2
        await s.call_tool("add", {"name": "impl task", "kind": "production"})    # T3
        await s.call_tool("link_add", {"a": 3, "b": 1,
            "because": "T3 realizes D1's decision"})
        out["anchors"] = _envelope(await s.call_tool("links", {}))
        out["nbrs"] = _envelope(await s.call_tool("links", {"ids": [1]}))
        return out

    out = _drive(tmp_path, monkeypatch, scenario)
    assert [n["id"] for n in out["anchors"]["result"]] == [1, 2]  # design+schema only
    assert [n["id"] for n in out["nbrs"]["result"]] == [3]        # T3 at depth 1
