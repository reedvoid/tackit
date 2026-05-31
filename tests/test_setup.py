"""`tackit setup` emission (setup_cmd was 0% covered).

render_setup emits the three agent-driven install steps with contextual paths; it
must not edit any config (it only prints).
"""

from tackit.setup_cmd import render_setup, skill_source


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
