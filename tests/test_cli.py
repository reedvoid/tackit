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
    plan = tmp_path / "plan.txt"
    plan.write_text(
        "[a] first task\n"
        "  kind: production\n"
        "[b] second task\n"
        "  kind: production\n"
        "  depends_on:\n"
        "    a :: test fixture: b couples to a's interface\n"
    )
    capsys.readouterr()
    assert main(["load", str(plan)]) == 0
    out = capsys.readouterr().out
    assert "loaded 2" in out and "T1" in out and "T2" in out
    capsys.readouterr()
    main(["show", "2"])
    assert "T1" in capsys.readouterr().out  # T2 depends on T1


def test_cli_load_reports_new_labels_on_stderr(cli, tmp_path, capsys):
    plan = tmp_path / "p.txt"
    plan.write_text("[a] one\n  kind: production\n  labels: freshlabel\n")
    capsys.readouterr()
    main(["load", str(plan)])
    err = capsys.readouterr().err
    assert "freshlabel" in err and "label" in err.lower()  # T67 batch summary on stderr


def test_cli_add_and_show(cli, capsys):
    assert main(["add", "parse FTS query", "--kind", "production", "--desc", "body"]) == 0
    capsys.readouterr()
    assert main(["show", "1"]) == 0
    out = capsys.readouterr().out
    assert "T1" in out and "parse FTS query" in out


def test_cli_add_json_output(cli, capsys):
    capsys.readouterr()
    assert main(["add", "json task", "--kind", "production", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["task"]["name"] == "json task"


def test_cli_edit_surfaces_stale_on_stderr(cli, capsys):
    main(["add", "base", "--kind", "production"])
    main(["add", "dep", "--kind", "production"])
    main(["link", "add", "2", "1", "--because", "test", "--delta", "test"])
    capsys.readouterr()
    assert main(["edit", "1", "--desc", "changed", "--delta", "test"]) == 0
    err = capsys.readouterr().err
    assert "STALE" in err.upper() and "T2" in err


def test_cli_close_stale_refused_exit_1(cli, capsys):
    main(["add", "base", "--kind", "production"])
    main(["add", "dep", "--kind", "production"])
    main(["link", "add", "2", "1", "--because", "test", "--delta", "test"])
    main(["edit", "1", "--desc", "x", "--delta", "test"])
    capsys.readouterr()
    assert main(["close", "2"]) == 1
    assert "REFUSED" in capsys.readouterr().err


def test_cli_close_clean_succeeds(cli):
    main(["add", "a", "--kind", "production"])
    assert main(["close", "1"]) == 0


def test_cli_not_found_exit_1(cli, capsys):
    assert main(["show", "999"]) == 1
    assert "tackit:" in capsys.readouterr().err


def test_cli_empty_name_exit_1(cli):
    assert main(["add", "   ", "--kind", "production"]) == 1


def test_cli_dep_add_reverse_args_is_idempotent(cli):
    # Under v0.3.0 symmetric semantics (T86), "1 -> 2" and "2 -> 1" are the
    # same link {1, 2}; reversed arguments hit the same canonical row and the
    # second dep_add is a successful no-op (exit 0), not a cycle refusal.
    main(["add", "a", "--kind", "production"])
    main(["add", "b", "--kind", "production"])
    main(["link", "add", "1", "2", "--because", "test", "--delta", "test"])
    assert main(["link", "add", "2", "1", "--because", "test", "--delta", "test"]) == 0  # idempotent, exit 0


def test_cli_ls_status_filter(cli, capsys):
    main(["add", "a", "--kind", "production", "--label", "x"])
    main(["add", "b", "--kind", "production", "--label", "y"])
    main(["close", "2"])
    capsys.readouterr()
    main(["ls", "--status", "open"])
    out = capsys.readouterr().out
    assert "T1" in out and "T2" not in out


def test_cli_search_ranks(cli, capsys):
    main(["add", "rotate JWT signing keys", "--kind", "production"])
    main(["add", "unrelated colour palette", "--kind", "production"])
    capsys.readouterr()
    main(["search", "JWT"])
    out = capsys.readouterr().out
    assert "T1" in out


def test_cli_label_add_then_rm(cli):
    main(["add", "a", "--kind", "production"])
    assert main(["label", "add", "1", "tag"]) == 0
    assert main(["label", "rm", "1", "tag"]) == 0


def test_cli_new_label_nudge_on_stderr(cli, capsys):
    main(["add", "a", "--kind", "production", "--label", "existing"])
    main(["add", "b", "--kind", "production"])
    capsys.readouterr()
    assert main(["label", "add", "2", "brandnew"]) == 0
    err = capsys.readouterr().err
    assert "brandnew" in err and "New label" in err  # nudge surfaced to stderr


def test_cli_reopen_then_reconcile_clears_worklist(cli, capsys):
    main(["add", "base", "--kind", "production"])
    main(["add", "dep", "--kind", "production"])
    main(["link", "add", "2", "1", "--because", "test", "--delta", "test"])
    main(["close", "2"])
    main(["reopen", "2"])
    main(["edit", "1", "--desc", "x", "--delta", "test"])  # stales T2
    assert main(["reconcile", "2"]) == 0
    capsys.readouterr()
    assert main(["stale", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []  # worklist now empty


def test_cli_render_markdown(cli, capsys):
    # Use a non-reserved label string -- design/schema/production/meta are reserved
    # for the kind property since T84 (D26).
    main(["add", "thing", "--kind", "production", "--desc", "body text", "--label", "spec"])
    capsys.readouterr()
    main(["render", "--label", "spec"])
    out = capsys.readouterr().out
    assert "thing" in out and "body text" in out


def test_cli_history(cli, capsys):
    main(["add", "a", "--kind", "production"])
    main(["close", "1"])
    capsys.readouterr()
    main(["history", "1"])
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
    main(["add", "a", "--kind", "production"])
    main(["add", "b", "--kind", "production"])
    main(["link", "add", "2", "1", "--because", "test", "--delta", "test"])
    assert main(["link", "rm", "2", "1", "--delta", "test"]) == 0


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
    main(["add", "base", "--kind", "production"])
    main(["add", "dep", "--kind", "production"])
    main(["link", "add", "2", "1", "--because", "test", "--delta", "test"])
    main(["close", "1"])
    capsys.readouterr()
    assert main(["board"]) == 0
    out = capsys.readouterr().out
    assert "open" in out and "done" in out  # header counts
    assert "IN FLIGHT" in out and "DONE" in out  # both status sections
    assert "T1" in out and "T2" in out
    assert "needs→" in out or "unblocks→" in out  # dependency edges rendered


def test_cli_board_stale_filter(cli, capsys):
    main(["add", "base", "--kind", "production"])
    main(["add", "dep", "--kind", "production"])
    main(["link", "add", "2", "1", "--because", "test", "--delta", "test"])
    main(["edit", "1", "--desc", "x", "--delta", "test"])  # stales T2
    capsys.readouterr()
    assert main(["board", "--stale"]) == 0
    out = capsys.readouterr().out
    assert "T2" in out and "STALE" in out and "IN FLIGHT" in out
    assert "DONE" not in out  # no closed tasks match -> that section is omitted


def test_cli_labels(cli, capsys):
    main(["add", "thing", "--kind", "production", "--label", "core"])
    main(["add", "other", "--kind", "production", "--label", "core"])
    capsys.readouterr()
    assert main(["labels"]) == 0
    out = capsys.readouterr().out
    assert "core" in out and "(2)" in out and "thing" in out


def test_cli_stale_lists_nonempty_worklist(cli, capsys):
    main(["add", "base", "--kind", "production"])
    main(["add", "dep", "--kind", "production"])
    main(["link", "add", "2", "1", "--because", "test", "--delta", "test"])
    main(["edit", "1", "--desc", "x", "--delta", "test"])  # stales T2
    capsys.readouterr()
    main(["stale"])
    out = capsys.readouterr().out
    assert "T2" in out and "worklist" in out.lower()


def test_cli_edit_no_dependents(cli, capsys):
    main(["add", "solo", "--kind", "production"])
    capsys.readouterr()
    assert main(["edit", "1", "--desc", "changed", "--delta", "test"]) == 0
    assert "no dependents" in capsys.readouterr().out.lower()


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
