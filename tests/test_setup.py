"""`tackit setup` emission (setup_cmd was 0% covered).

render_setup emits the agent-driven install steps with contextual paths, plus the
optional workflow-commands step (T308, D306); it must not edit any config (it only
prints).
"""

from pathlib import Path

from tackit import cli
from tackit.setup_cmd import render_setup, skill_source, workflow_source


def test_setup_emits_install_steps_with_contextual_paths(tmp_path):
    import sys

    out = render_setup(tmp_path)
    # 1. MCP registration -- PORTABLE command (so .mcp.json is committable), + fallback
    assert "REGISTER THE MCP SERVER" in out
    assert "mcpServers" in out and '"command": "tackit"' in out and '"mcp"' in out
    assert sys.executable in out  # the venv-python fallback is offered for not-on-PATH
    # 2. drop the skill, at both the neutral and Claude-specific locations
    assert "DROP THE SKILL" in out
    assert str(tmp_path / ".agents" / "skills" / "tackit") in out
    assert str(tmp_path / ".claude" / "skills" / "tackit") in out
    # 3. add the always-on discipline to the agent's own config (T22)
    assert "ALWAYS-ON DISCIPLINE" in out
    # 4. create the store
    assert "tackit init" in out


def test_setup_locates_packaged_skill():
    src = skill_source()
    assert src is not None
    assert src.name == "SKILL.md"
    assert "Working with tackit" in src.read_text()


COMMANDS = ("/diagnose", "/firmup", "/graduate", "/build", "/orient", "/handoff", "/trim")


def _render_with_home(tmp_path, monkeypatch):
    """render_setup with a throwaway home; returns (raw output, whitespace-normalized
    output, home), because the locked sentences wrap across printed lines."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    out = render_setup(tmp_path)
    return out, " ".join(out.split()), home


def test_workflow_source_finds_the_packaged_folder():
    src = workflow_source()
    assert src is not None
    assert src.is_dir()
    assert (src / "commands" / "firmup.md").is_file()
    assert (src / "skills" / "test-audit" / "SKILL.md").is_file()


def test_step_names_the_real_source_and_user_level_destinations(tmp_path, monkeypatch):
    out, _, home = _render_with_home(tmp_path, monkeypatch)
    assert f"Copy from {workflow_source()}:" in out
    assert f"commands/*.md to {home / '.claude' / 'commands'}/" in out
    assert f"skills/test-audit/ to {home / '.claude' / 'skills' / 'test-audit'}/" in out


def test_step_is_optional_and_never_overwrites_silently(tmp_path, monkeypatch):
    """The converses that matter: a tracker-only user isn't pushed into the workflow,
    and a locally edited command is never clobbered."""
    _, text, _ = _render_with_home(tmp_path, monkeypatch)
    assert "Four post-install steps remain, plus one optional" in text
    assert "5. OPTIONAL: INSTALL THE WORKFLOW COMMANDS, only if the human asks for them." in text
    assert "If a destination file already exists and differs, show the human the diff and ask." in text
    assert "Never overwrite it silently." in text


def test_step_names_every_command_and_each_dependency(tmp_path, monkeypatch):
    _, text, _ = _render_with_home(tmp_path, monkeypatch)
    start = text.index("5. OPTIONAL")
    step = text[start:text.index("After that")]
    for command in COMMANDS:
        assert command in step
    assert "/plain" not in text
    assert "the test-audit skill they rely on" in step
    assert "superpowers@claude-plugins-official" in step
    assert "/build reads a project's test, deploy and billed-eval commands from its CLAUDE.md." in step
    assert "An upgrade does not refresh copied files; re-run `tackit setup` to compare." in step


def test_required_steps_stay_in_order_with_the_optional_one_last(tmp_path, monkeypatch):
    _, text, _ = _render_with_home(tmp_path, monkeypatch)
    positions = [
        text.index("1. REGISTER THE MCP SERVER"),
        text.index("2. DROP THE SKILL"),
        text.index("3. ADD THE ALWAYS-ON DISCIPLINE"),
        text.index("4. CREATE THE STORE"),
        text.index("5. OPTIONAL"),
        text.index("After that,"),
    ]
    assert positions == sorted(positions)


def test_render_setup_writes_nothing(tmp_path, monkeypatch):
    _, _, home = _render_with_home(tmp_path, monkeypatch)
    assert list(home.iterdir()) == []
    assert [path.name for path in tmp_path.iterdir()] == ["home"]


def test_missing_workflow_folder_says_so_instead_of_a_bad_path(tmp_path, monkeypatch):
    monkeypatch.setattr("tackit.setup_cmd.workflow_source", lambda: None)
    out, _, _ = _render_with_home(tmp_path, monkeypatch)
    assert "Copy from (packaged workflow files - see tackit/data/workflow):" in out


def test_cli_setup_prints_the_step(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["setup"]) == 0
    captured = capsys.readouterr().out
    assert "5. OPTIONAL: INSTALL THE WORKFLOW COMMANDS" in captured
    assert str(Path(tmp_path) / ".claude" / "commands") in captured
