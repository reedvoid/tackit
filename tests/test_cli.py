"""CLI adapter integration (cli.py was 0% covered).

Drives tackit.cli.main end-to-end against a temp store (cwd-discovered): output,
``--json`` stdout, exit codes on refusals, and the D19 stale banner on stderr.
"""

import json

import pytest

from tackit.cli import main


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    return tmp_path


def test_cli_init_creates_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert (tmp_path / ".tackit" / "tackit.db").exists()


def test_cli_load(cli, tmp_path, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1: satisfies D256 gate
    plan = tmp_path / "plan.txt"
    plan.write_text(
        "[a] first task\n"
        "  kind: production\n"
        "  depends_on:\n"
        "    D1 :: realizes the anchor decision\n"
        "[b] second task\n"
        "  kind: production\n"
        "  depends_on:\n"
        "    a :: test fixture: b couples to a's interface\n"
        "    D1 :: realizes the anchor decision\n"
    )
    capsys.readouterr()
    assert main(["load", str(plan)]) == 0
    out = capsys.readouterr().out
    assert "loaded 2" in out and "T2" in out and "T3" in out
    capsys.readouterr()
    main(["show", "3"])
    assert "T2" in capsys.readouterr().out  # T3 depends on T2


def test_cli_load_reports_new_labels_on_stderr(cli, tmp_path, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1: satisfies D256 gate
    plan = tmp_path / "p.txt"
    plan.write_text(
        "[a] one\n"
        "  kind: production\n"
        "  labels: freshlabel\n"
        "  depends_on:\n"
        "    D1 :: realizes the anchor decision\n"
    )
    capsys.readouterr()
    main(["load", str(plan)])
    err = capsys.readouterr().err
    assert "freshlabel" in err and "label" in err.lower()  # T67 batch summary on stderr


def test_cli_add_and_show(cli, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    assert main([
        "add", "parse FTS query", "--kind", "production", "--desc", "body",
        "--dep", "1::realizes the anchor decision",
    ]) == 0
    capsys.readouterr()
    assert main(["show", "2"]) == 0
    out = capsys.readouterr().out
    assert "T2" in out and "parse FTS query" in out


def test_cli_add_json_output(cli, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    capsys.readouterr()
    assert main([
        "add", "json task", "--kind", "production", "--json",
        "--dep", "1::realizes the anchor decision",
    ]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["task"]["name"] == "json task"


def test_cli_ls_name_prefix(cli, capsys):
    """T220: --name-prefix scopes the ls query."""
    main(["add", "§9.1 search", "--kind", "design"])
    main(["add", "§8.2 other", "--kind", "design"])
    capsys.readouterr()
    assert main(["ls", "--name-prefix", "§9.1", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [t["id"] for t in data] == [1]


def test_cli_edit_surfaces_stale_on_stderr(cli, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "base", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T2
    main(["add", "dep", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T3
    main(["link", "add", "3", "2", "--because", "test"])
    capsys.readouterr()
    assert main(["edit", "2", "--desc", "changed", "--delta", "test"]) == 0
    err = capsys.readouterr().err
    assert "STALE" in err.upper() and "T3" in err


def test_cli_close_stale_refused_exit_1(cli, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "base", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T2
    main(["add", "dep", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T3
    main(["link", "add", "3", "2", "--because", "test"])
    main(["edit", "2", "--desc", "x", "--delta", "test"])
    capsys.readouterr()
    assert main(["close", "3"]) == 1
    assert "REFUSED" in capsys.readouterr().err


def test_cli_close_clean_succeeds(cli):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "a", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T2
    assert main(["close", "2"]) == 0


def test_cli_retire_clean_succeeds(cli, capsys):
    """T175 Phase 3: `tackit retire` subcommand reaches Core.retire() end-
    to-end, status spec->retired."""
    main(["add", "d1 living spec", "--kind", "design"])
    capsys.readouterr()  # flush prior output
    exit_code = main([
        "retire", "1",
        "--reason", "premise replaced by D99",
        "--delta", "retiring D1",
    ])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "retired" in out.lower()
    # verify state
    main(["show", "1", "--json"])
    show_out = capsys.readouterr().out
    assert '"status": "retired"' in show_out


def test_cli_not_found_exit_1(cli, capsys):
    assert main(["show", "999"]) == 1
    assert "tackit:" in capsys.readouterr().err


def test_cli_empty_name_exit_1(cli):
    assert main(["add", "   ", "--kind", "production"]) == 1


def test_cli_dep_add_reverse_args_is_idempotent(cli):
    # Under v0.3.0 symmetric semantics (T86), "1 -> 2" and "2 -> 1" are the
    # same link {1, 2}; reversed arguments hit the same canonical row and the
    # second dep_add is a successful no-op (exit 0), not a cycle refusal.
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "a", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T2
    main(["add", "b", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T3
    main(["link", "add", "2", "3", "--because", "test"])
    assert main(["link", "add", "3", "2", "--because", "test"]) == 0  # idempotent, exit 0


def test_cli_ls_status_filter(cli, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "a", "--kind", "production", "--label", "x", "--dep", "1::realizes the anchor decision"])  # T2
    main(["add", "b", "--kind", "production", "--label", "y", "--dep", "1::realizes the anchor decision"])  # T3
    main(["close", "3"])
    capsys.readouterr()
    main(["ls", "--status", "open"])
    out = capsys.readouterr().out
    assert "T2" in out and "T3" not in out


def test_cli_search_ranks(cli, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "rotate JWT signing keys", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T2
    main(["add", "unrelated colour palette", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T3
    capsys.readouterr()
    main(["search", "JWT"])
    out = capsys.readouterr().out
    assert "T2" in out


def test_cli_label_add_then_rm(cli):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "a", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T2
    assert main(["label", "add", "2", "tag"]) == 0
    assert main(["label", "rm", "2", "tag"]) == 0


def test_cli_new_label_nudge_on_stderr(cli, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "a", "--kind", "production", "--label", "existing", "--dep", "1::realizes the anchor decision"])  # T2
    main(["add", "b", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T3
    capsys.readouterr()
    assert main(["label", "add", "3", "brandnew"]) == 0
    err = capsys.readouterr().err
    assert "brandnew" in err and "New label" in err  # nudge surfaced to stderr


def test_cli_reopen_then_reconcile_clears_worklist(cli, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "base", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T2
    main(["add", "dep", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T3
    main(["link", "add", "3", "2", "--because", "test"])
    main(["close", "3"])
    main(["reopen", "3"])
    main(["edit", "2", "--desc", "x", "--delta", "test"])  # stales T3 and the anchor D1
    assert main(["reconcile", "3", "1"]) == 0
    capsys.readouterr()
    assert main(["stale", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []  # worklist now empty


def test_cli_render_markdown(cli, capsys):
    # Use a non-reserved label string -- design/schema/production/meta are reserved
    # for the kind property since T84 (D26).
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main([
        "add", "thing", "--kind", "production", "--desc", "body text",
        "--label", "spec", "--dep", "1::realizes the anchor decision",
    ])
    capsys.readouterr()
    main(["render", "--label", "spec"])
    out = capsys.readouterr().out
    assert "thing" in out and "body text" in out


def test_cli_history(cli, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "a", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T2
    main(["close", "2"])
    capsys.readouterr()
    main(["history", "2"])
    assert "closed" in capsys.readouterr().out


def test_cli_status_export_import(cli, capsys):
    main(["add", "a", "--kind", "production"])
    capsys.readouterr()
    assert main(["status"]) == 0
    assert "version" in capsys.readouterr().out.lower()
    assert main(["export"]) == 0
    assert main(["import", "--force"]) == 0


def test_cli_restore_list_empty(cli, capsys):
    main(["add", "a", "--kind", "production"])
    capsys.readouterr()
    assert main(["restore", "--list"]) == 0
    assert "backup" in capsys.readouterr().out.lower()


def test_cli_setup_emits_install_steps(cli, capsys):
    capsys.readouterr()
    assert main(["setup"]) == 0
    out = capsys.readouterr().out
    assert "mcpServers" in out and "tackit init" in out


def test_cli_dep_rm(cli):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "a", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T2
    main(["add", "b", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T3
    main(["link", "add", "3", "2", "--because", "test"])
    assert main(["link", "rm", "3", "2"]) == 0


def test_cli_restore_by_index(cli, capsys):
    main(["add", "a", "--kind", "production"])
    main(["import", "--force"])  # the override path snapshots the db into backups/
    capsys.readouterr()
    main(["restore", "--list"])
    assert "[0]" in capsys.readouterr().out
    capsys.readouterr()
    assert main(["restore", "--backup", "0"]) == 0
    assert "restored" in capsys.readouterr().out


def test_cli_restore_bad_backup_exit_1(cli, capsys):
    main(["add", "a", "--kind", "production"])
    main(["import", "--force"])
    capsys.readouterr()
    assert main(["restore", "--backup", "no-such-backup"]) == 1


def test_cli_no_store_fails_loud(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no `tackit init` here
    assert main(["show", "1"]) == 1  # require_store -> NotFoundError -> exit 1


def test_cli_board_groups_and_shows_edges(cli, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "base", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T2
    main(["add", "dep", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T3
    main(["link", "add", "3", "2", "--because", "test"])
    main(["close", "2"])
    capsys.readouterr()
    assert main(["board"]) == 0
    out = capsys.readouterr().out
    assert "open" in out and "done" in out  # header counts
    assert "IN FLIGHT" in out and "DONE" in out  # both status sections
    assert "T2" in out and "T3" in out
    assert "links→" in out  # T237: single symmetric links edge list


def test_cli_board_stale_filter(cli, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "base", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T2
    main(["add", "dep", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T3
    main(["link", "add", "3", "2", "--because", "test"])
    main(["edit", "2", "--desc", "x", "--delta", "test"])  # stales T3 (and the anchor D1)
    capsys.readouterr()
    assert main(["board", "--stale"]) == 0
    out = capsys.readouterr().out
    assert "T3" in out and "STALE" in out and "IN FLIGHT" in out
    assert "DONE" not in out  # no closed tasks match -> that section is omitted


def test_cli_labels(cli, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "thing", "--kind", "production", "--label", "core", "--dep", "1::realizes the anchor decision"])  # T2
    main(["add", "other", "--kind", "production", "--label", "core", "--dep", "1::realizes the anchor decision"])  # T3
    capsys.readouterr()
    assert main(["labels"]) == 0
    out = capsys.readouterr().out
    assert "core" in out and "(2)" in out and "thing" in out


def test_cli_stale_lists_nonempty_worklist(cli, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "base", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T2
    main(["add", "dep", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T3
    main(["link", "add", "3", "2", "--because", "test"])
    main(["edit", "2", "--desc", "x", "--delta", "test"])  # stales T3 (and the anchor D1)
    capsys.readouterr()
    main(["stale"])
    out = capsys.readouterr().out
    assert "T3" in out and "worklist" in out.lower()


def test_cli_edit_no_dependents(cli, capsys):
    # kind=meta: unaffected by the D256 gate, so this stays a genuinely
    # link-free task -- the point under test is the "no linked neighbors" message,
    # which a gated production task (always linked to its anchor) could no
    # longer exercise.
    main(["add", "solo", "--kind", "meta"])
    capsys.readouterr()
    assert main(["edit", "1", "--desc", "changed", "--delta", "test"]) == 0
    assert "no linked neighbors" in capsys.readouterr().out.lower()


# --- T179: edit-append + edit-replace CLI surface ------------------------


def test_cli_edit_append_appends_and_surfaces_stale(cli, capsys):
    # D255: append is meta-only, so the edit-append fixtures use meta tasks.
    main(["add", "base", "--kind", "meta", "--desc", "original"])
    main(["add", "dep", "--kind", "meta"])
    main(["link", "add", "2", "1", "--because", "M2 reviews M1"])
    capsys.readouterr()
    assert main([
        "edit-append", "1",
        "--content", " + appended",
        "--delta", "extended scope",
    ]) == 0
    out = capsys.readouterr()
    assert "appended to" in out.out
    assert "STALE" in out.err.upper() and "M2" in out.err

    capsys.readouterr()
    main(["show", "1"])
    assert "original + appended" in capsys.readouterr().out


def test_cli_edit_append_refused_empty_content_exit_1(cli, capsys):
    main(["add", "a", "--kind", "meta"])  # D255: append is meta-only
    capsys.readouterr()
    rc = main(["edit-append", "1", "--content", "", "--delta", "x"])
    assert rc == 1


def test_cli_edit_replace_replaces_substring(cli, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main([
        "add", "a", "--kind", "production", "--desc", "hello world",
        "--dep", "1::realizes the anchor decision",
    ])  # T2
    capsys.readouterr()
    assert main([
        "edit-replace", "2",
        "--old", "world",
        "--new", "universe",
        "--delta", "renamed token",
    ]) == 0
    capsys.readouterr()
    main(["show", "2"])
    assert "hello universe" in capsys.readouterr().out


def test_cli_edit_replace_multi_match_refused_exit_1(cli, capsys):
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main([
        "add", "a", "--kind", "production", "--desc", "foo foo foo",
        "--dep", "1::realizes the anchor decision",
    ])  # T2
    capsys.readouterr()
    rc = main([
        "edit-replace", "2",
        "--old", "foo",
        "--new", "bar",
        "--delta", "seed",
    ])
    assert rc == 1


def test_cli_restore_by_filename(cli, capsys):
    import re

    main(["add", "a", "--kind", "production"])
    main(["import", "--force"])
    capsys.readouterr()
    main(["restore", "--list"])
    match = re.search(r"tackit-[\w-]+\.db", capsys.readouterr().out)
    assert match
    capsys.readouterr()
    assert main(["restore", "--backup", match.group(0)]) == 0
    assert "restored" in capsys.readouterr().out


def test_cli_search_name_only_flag(cli, capsys):
    """M181 #8d: `tackit search --name-only` scopes the FTS5 match to the
    name column. Two tasks, one with the term in name and one only in
    description -- --name-only returns the name match only."""
    main(["add", "spec anchor", "--kind", "design"])  # D1
    main(["add", "--kind", "production",
          "--desc", "this body mentions sirius once",
          "--dep", "1::realizes the anchor decision", "alpha hub"])  # T2
    main(["add", "--kind", "production",
          "--desc", "sirius appears here only in description",
          "--dep", "1::realizes the anchor decision", "beta hub"])  # T3
    capsys.readouterr()

    # Default: matches both (one via name, one via description).
    main(["search", "sirius"])
    out = capsys.readouterr().out
    assert "T2" in out and "T3" in out, (
        f"Default search must match both rows; got:\n{out!r}"
    )

    # --name-only: matches only the name hit (the description "sirius" is
    # absent from name, so it's filtered).
    main(["search", "sirius", "--name-only"])
    out = capsys.readouterr().out
    assert "(no matches)" in out, (
        f"`search sirius --name-only` should find no rows (sirius only "
        f"in descriptions, not names); got:\n{out!r}"
    )

    # Direct name match works via --name-only.
    main(["search", "alpha", "--name-only"])
    out = capsys.readouterr().out
    assert "T2" in out and "T3" not in out, (
        f"`search alpha --name-only` should find only T2; got:\n{out!r}"
    )


def test_cli_help_for_every_subcommand_has_substantive_description():
    """M181 #8a: every CLI subcommand's --help output must be non-trivially
    longer than its bare argparse usage line. Catches the class of bug where
    argparse's `description=` was omitted when registering the subcommand,
    leaving --help with only the auto-generated usage skeleton (no prose).

    Phase 3 of v0.5 (T175) fixed this for the existing subcommands by passing
    `description=help_text` alongside `help=help_text` in the `add()` helper.
    This test guards the rule prospectively -- any future subcommand
    registered without `description=` (e.g., bypassing the helper) regresses
    here, with a failure message naming the subcommand path and root cause.
    """
    import argparse
    from tackit.cli import build_parser

    parser = build_parser()

    subparsers_action = None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            subparsers_action = action
            break
    assert subparsers_action is not None, (
        "CLI parser must have a subparsers action -- something has changed "
        "in build_parser() that this test no longer recognizes."
    )

    def _walk(subparser_action, prefix=""):
        for name, subparser in subparser_action.choices.items():
            full_name = f"{prefix}{name}" if prefix else name
            usage_len = len(subparser.format_usage())
            help_len = len(subparser.format_help())
            extra = help_len - usage_len
            assert extra > 50, (
                f"CLI subcommand `tackit {full_name} --help` is missing or "
                f"too thin -- only {extra} chars beyond the bare usage line. "
                f"Likely cause: argparse `description=` was not passed when "
                f"registering this subcommand (the Phase 3 / v0.5 bug). Add "
                f"a description= matching help= when calling add_parser() "
                f"so the prose lands at typed-command moment."
            )
            # Recurse into nested subparsers (link has add/rm; label has add/rm).
            for nested in subparser._actions:
                if isinstance(nested, argparse._SubParsersAction):
                    _walk(nested, prefix=f"{full_name} ")

    _walk(subparsers_action)


def test_top_level_help_renders_without_crash(capsys):
    """Regression: `tackit --help` renders the whole subcommand list, which
    runs argparse's _expand_help (`help % params`) on every subcommand's short
    `help=` string. A literal `%` -- e.g. ``100% gone`` -- was parsed as a `%g`
    conversion against the params dict and crashed with
    `TypeError: must be real number, not dict`.

    The per-subcommand tests above missed it: a single `tackit <cmd> --help`
    only renders one description (the `description=` path, which argparse does
    NOT %-expand), so the crash only fired when the TOP-LEVEL parser rendered
    all subcommands' `help=` strings at once.

    Note the `%%`-escape trap: the SAME string is passed as both `help=` (which
    %-expands) and `description=` (which does not). Escaping as `%%` fixes the
    crash but leaks a literal `100%%` into `tackit edit --help`. The durable
    fix is to keep `%` out of these strings entirely. This test pins both: the
    top-level path must not crash, and no `%%` may leak on either path.
    """
    import argparse
    from tackit.cli import build_parser

    parser = build_parser()
    # format_help() on the top parser expands every subcommand's short help=.
    rendered = parser.format_help()
    assert "edit" in rendered
    assert "completely gone" in rendered  # reworded away from "100% gone"

    # Drive the real entry point the way a user does.
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "completely gone" in out

    # No literal `%%` may leak on the top-level path NOR any subcommand's
    # description path (guards the %%-escape regression on every subparser).
    assert "%%" not in out
    subparsers_action = None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            subparsers_action = action
            break
    assert subparsers_action is not None
    for name, subparser in subparsers_action.choices.items():
        assert "%%" not in subparser.format_help(), (
            f"`tackit {name} --help` leaks a literal `%%` -- a `%` in the "
            f"help/description string was escaped as `%%`, which the "
            f"description path renders verbatim. Reword to drop the `%`."
        )


def test_version_flag_prints_package_version(capsys):
    """T195: `tackit --version` prints the package version and exits 0 WITHOUT
    requiring a subcommand. argparse's action="version" fires during parse and
    exits before the required-subparser check -- this test pins that, rather
    than assuming it. The printed version must track tackit.__version__ (single
    source of truth), not a hardcoded literal.
    """
    import tackit

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert tackit.__version__ in out
    assert out.strip() == f"tackit {tackit.__version__}"


def test_cli_links_anchor_and_neighborhood(cli, capsys):
    """T204: `tackit links` exposes the D27 discovery op. No ids -> the
    design+schema anchor layer; an id -> its depth-1 neighborhood."""
    main(["add", "decision", "--kind", "design"])   # D1
    # D256: the production task must link a design/schema slice at creation --
    # do it via --dep on the same anchor, which is the edge this test then
    # inspects via `links`, so no separate `link add` call is needed.
    main(["add", "impl", "--kind", "production", "--dep", "1::T2 realizes D1"])  # T2
    capsys.readouterr()
    assert main(["links", "--json"]) == 0            # anchor layer
    anchors = json.loads(capsys.readouterr().out)
    assert [n["id"] for n in anchors] == [1]         # design slice only
    assert main(["links", "1", "--json"]) == 0       # D1's neighborhood
    nbrs = json.loads(capsys.readouterr().out)
    assert [n["id"] for n in nbrs] == [2]            # T2


def test_cli_links_add_bulk(cli, tmp_path, capsys):
    """T216: `tackit links-add` bulk-links existing tasks from a file/stdin,
    one edge per line as `<a> <b> :: <because>`."""
    main(["add", "spec anchor", "--kind", "design"])     # D1: satisfies D256 at creation
    main(["add", "design slice", "--kind", "design"])    # D2: the bulk-link target below
    main(["add", "impl a", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T3
    main(["add", "impl b", "--kind", "production", "--dep", "1::realizes the anchor decision"])  # T4
    edges = tmp_path / "edges.txt"
    edges.write_text(
        "T3 D2 :: T3 realizes the D2 decision\n"
        "T4 D2 :: T4 realizes the D2 decision\n"
    )
    capsys.readouterr()
    assert main(["links-add", str(edges), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["created"] == 2
    assert result["already_linked"] == 0
