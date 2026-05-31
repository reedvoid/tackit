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


def test_cli_add_and_show(cli, capsys):
    assert main(["add", "parse FTS query", "--desc", "body"]) == 0
    capsys.readouterr()
    assert main(["show", "1"]) == 0
    out = capsys.readouterr().out
    assert "T1" in out and "parse FTS query" in out


def test_cli_add_json_output(cli, capsys):
    capsys.readouterr()
    assert main(["add", "json task", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["task"]["name"] == "json task"


def test_cli_edit_surfaces_stale_on_stderr(cli, capsys):
    main(["add", "base"])
    main(["add", "dep"])
    main(["dep", "add", "2", "1"])
    capsys.readouterr()
    assert main(["edit", "1", "--desc", "changed"]) == 0
    err = capsys.readouterr().err
    assert "STALE" in err.upper() and "T2" in err


def test_cli_close_stale_refused_exit_1(cli, capsys):
    main(["add", "base"])
    main(["add", "dep"])
    main(["dep", "add", "2", "1"])
    main(["edit", "1", "--desc", "x"])
    capsys.readouterr()
    assert main(["close", "2"]) == 1
    assert "REFUSED" in capsys.readouterr().err


def test_cli_close_clean_succeeds(cli):
    main(["add", "a"])
    assert main(["close", "1"]) == 0


def test_cli_not_found_exit_1(cli, capsys):
    assert main(["show", "999"]) == 1
    assert "tackit:" in capsys.readouterr().err


def test_cli_empty_name_exit_1(cli):
    assert main(["add", "   "]) == 1


def test_cli_cycle_refused_exit_1(cli):
    main(["add", "a"])
    main(["add", "b"])
    main(["dep", "add", "1", "2"])
    assert main(["dep", "add", "2", "1"]) == 1  # would cycle


def test_cli_ls_status_filter(cli, capsys):
    main(["add", "a", "--label", "x"])
    main(["add", "b", "--label", "y"])
    main(["close", "2"])
    capsys.readouterr()
    main(["ls", "--status", "open"])
    out = capsys.readouterr().out
    assert "T1" in out and "T2" not in out


def test_cli_search_ranks(cli, capsys):
    main(["add", "rotate JWT signing keys"])
    main(["add", "unrelated colour palette"])
    capsys.readouterr()
    main(["search", "JWT"])
    out = capsys.readouterr().out
    assert "T1" in out


def test_cli_label_add_then_rm(cli):
    main(["add", "a"])
    assert main(["label", "add", "1", "tag"]) == 0
    assert main(["label", "rm", "1", "tag"]) == 0


def test_cli_reopen_then_reconcile_clears_worklist(cli, capsys):
    main(["add", "base"])
    main(["add", "dep"])
    main(["dep", "add", "2", "1"])
    main(["close", "2"])
    main(["reopen", "2"])
    main(["edit", "1", "--desc", "x"])  # stales T2
    assert main(["reconcile", "2"]) == 0
    capsys.readouterr()
    assert main(["stale", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []  # worklist now empty


def test_cli_render_markdown(cli, capsys):
    main(["add", "thing", "--desc", "body text", "--label", "design"])
    capsys.readouterr()
    main(["render", "--label", "design"])
    out = capsys.readouterr().out
    assert "thing" in out and "body text" in out


def test_cli_history(cli, capsys):
    main(["add", "a"])
    main(["close", "1"])
    capsys.readouterr()
    main(["history", "1"])
    assert "closed" in capsys.readouterr().out


def test_cli_status_export_import(cli, capsys):
    main(["add", "a"])
    capsys.readouterr()
    assert main(["status"]) == 0
    assert "version" in capsys.readouterr().out.lower()
    assert main(["export"]) == 0
    assert main(["import", "--force"]) == 0


def test_cli_restore_list_empty(cli, capsys):
    main(["add", "a"])
    capsys.readouterr()
    assert main(["restore", "--list"]) == 0
    assert "backup" in capsys.readouterr().out.lower()


def test_cli_setup_emits_install_steps(cli, capsys):
    capsys.readouterr()
    assert main(["setup"]) == 0
    out = capsys.readouterr().out
    assert "mcpServers" in out and "tackit init" in out


def test_cli_dep_rm(cli):
    main(["add", "a"])
    main(["add", "b"])
    main(["dep", "add", "2", "1"])
    assert main(["dep", "rm", "2", "1"]) == 0


def test_cli_restore_by_index(cli, capsys):
    main(["add", "a"])
    main(["import", "--force"])  # the override path snapshots the db into backups/
    capsys.readouterr()
    main(["restore", "--list"])
    assert "[0]" in capsys.readouterr().out
    capsys.readouterr()
    assert main(["restore", "--backup", "0"]) == 0
    assert "restored" in capsys.readouterr().out


def test_cli_restore_bad_backup_exit_1(cli, capsys):
    main(["add", "a"])
    main(["import", "--force"])
    capsys.readouterr()
    assert main(["restore", "--backup", "no-such-backup"]) == 1


def test_cli_no_store_fails_loud(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no `tackit init` here
    assert main(["show", "1"]) == 1  # require_store -> NotFoundError -> exit 1


def test_cli_stale_lists_nonempty_worklist(cli, capsys):
    main(["add", "base"])
    main(["add", "dep"])
    main(["dep", "add", "2", "1"])
    main(["edit", "1", "--desc", "x"])  # stales T2
    capsys.readouterr()
    main(["stale"])
    out = capsys.readouterr().out
    assert "T2" in out and "worklist" in out.lower()


def test_cli_edit_no_dependents(cli, capsys):
    main(["add", "solo"])
    capsys.readouterr()
    assert main(["edit", "1", "--desc", "changed"]) == 0
    assert "no dependents" in capsys.readouterr().out.lower()


def test_cli_restore_by_filename(cli, capsys):
    import re

    main(["add", "a"])
    main(["import", "--force"])
    capsys.readouterr()
    main(["restore", "--list"])
    match = re.search(r"tackit-[\w-]+\.db", capsys.readouterr().out)
    assert match
    capsys.readouterr()
    assert main(["restore", "--backup", match.group(0)]) == 0
    assert "restored" in capsys.readouterr().out
