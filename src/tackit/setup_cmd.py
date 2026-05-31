"""`tackit setup` - the agent-driven install emission (design.md "Installation").

Settled 2026-05-30: no agent auto-detection. ``tackit setup`` does NOT edit any
config; it *emits* the post-install steps with contextualized paths -- the
install-time analog of the tool-result obligation emission. The driving agent
reads these and wires the MCP server into its own config (it knows its own
location). It emits: a portable, committable MCP registration snippet, where to
drop SKILL.md, the always-on discipline to add to the agent's own config (T22), and
the `tackit init` step.
"""

from __future__ import annotations

import json
import sys
from importlib import resources
from pathlib import Path


def skill_source() -> Path | None:
    """Locate the packaged SKILL.md (shipped under tackit/data/SKILL.md)."""
    try:
        res = resources.files("tackit").joinpath("data/SKILL.md")
        if res.is_file():
            return Path(str(res))
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    return None


def render_setup(cwd: Path) -> str:
    """Build the contextualized post-install instructions for ``cwd``."""
    python = sys.executable
    skill = skill_source()
    skill_loc = str(skill) if skill else "(packaged SKILL.md - see tackit/data/SKILL.md)"

    # Portable command (T23): rely on the `tackit` console script being on PATH so the
    # resulting .mcp.json is committable + shareable, not a machine-specific absolute
    # path. The venv-python form is offered as a fallback in the text below.
    mcp_snippet = {
        "mcpServers": {
            "tackit": {
                "command": "tackit",
                "args": ["mcp"],
            }
        }
    }

    neutral_dir = cwd / ".agents" / "skills" / "tackit"
    claude_dir = cwd / ".claude" / "skills" / "tackit"

    return "\n".join(
        [
            "tackit is installed. Four post-install steps remain — I (the driving",
            "agent) should carry these out, since I know my own config location:",
            "",
            "1. REGISTER THE MCP SERVER into my own agent config. The stdio server",
            "   launches via tackit's console entry point. Equivalent JSON config:",
            "",
            json.dumps(mcp_snippet, indent=2),
            "",
            "   (Fallback if `tackit` is not on my PATH, e.g. a venv install: set",
            f'     "command" to "{python}" and "args" to ["-m","tackit.cli","mcp"].)',
            "",
            "2. DROP THE SKILL so the discipline ships with the tools. Copy the",
            f"   packaged SKILL.md ({skill_loc}) to:",
            f"     - {neutral_dir / 'SKILL.md'}   (neutral, cross-agent)",
            f"     - {claude_dir / 'SKILL.md'}   (belt-and-suspenders for Claude Code)",
            "",
            "3. ADD THE ALWAYS-ON DISCIPLINE to my own config (e.g. CLAUDE.md), so it",
            "   holds even when the skill isn't loaded: the code<->task traceability",
            "   convention and the change-summary-reporting rule. See the 'Configure",
            "   your agent's settings' snippet in tackit's README for the exact text.",
            "",
            "4. CREATE THE STORE in this project:",
            "     tackit init",
            "",
            "After that, `tackit.sql` is the committed source of truth and the MCP",
            "tools are the primary door. The portable .mcp.json above and tackit.sql",
            "are both safe to commit; the binary tackit.db is gitignored by `init`.",
            "See `tackit --help` for the full surface.",
        ]
    )
