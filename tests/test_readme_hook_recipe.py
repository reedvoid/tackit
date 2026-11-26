"""T353 (D231): the README's Claude Code session-start recipe prints a one-line
directive and never suggests piping the whole skill through the hook.

Claude Code hands a hook's stdout over 10,000 characters to the agent as a
2,000-character preview (hooks reference, fetched 2026-11-25), and the shipped
SKILL.md is far larger, so a `cat` of it would deliver a preview, not the
discipline.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _claude_entry():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    start = readme.index("**Claude Code** — a `SessionStart` hook")
    end = readme.index("**Codex CLI**", start)
    return re.sub(r"\s+", " ", readme[start:end])


def test_skill_is_over_the_hook_output_cap():
    """The premise: the skill is over Claude Code's 10,000-character cap."""
    assert len((ROOT / "src" / "tackit" / "data" / "SKILL.md").read_text(encoding="utf-8")) > 10_000


def test_claude_entry_does_not_suggest_catting_the_skill():
    entry = _claude_entry()
    assert "cat" not in re.findall(r"`(\w+)", entry)
    assert "SKILL.md" not in entry


def test_claude_entry_keeps_the_echo_directive_and_says_why():
    entry = _claude_entry()
    assert "echo 'This project uses tackit." in entry
    assert "2,000-character preview" in entry
    assert "Keep the `compact` matcher" in entry
