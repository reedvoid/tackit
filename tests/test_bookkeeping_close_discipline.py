"""T223 — bookkeeping completion belongs in close(), not an edit: presence pins.

A completion note ("done" / "committed <hash>") appended to a body fires a
pointless cascade that stales the design slices the task implements; close()
already records "done" (and does NOT cascade) and git carries the commit. These
pin the guidance on its two surfaces — the SKILL.md "Edits aren't free" section
(session-start) and the MCP close() docstring (invocation-time). Only the
canonical src/tackit/data/SKILL.md is checked — the .claude/.agents skill copies
are gitignored per-machine installs, not committed state.

Whitespace-normalized substring checks so a future wording edit is deliberate.
"""

import inspect
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
CANONICAL = REPO / "src" / "tackit" / "data" / "SKILL.md"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_skill_md_says_bookkeeping_is_a_close_not_an_edit():
    text = _norm(CANONICAL.read_text())
    # the rule lives in the `### close` do-bullet: completion is a close(), not
    # an edit (close does NOT cascade); only a fold-back earns a pre-close edit
    assert "completion note" in text
    assert "does NOT cascade" in text
    assert "fold-back" in text


def test_mcp_close_docstring_carries_the_sharp_edge():
    from tackit import mcp_server

    src = inspect.getsource(mcp_server)
    start = src.index("def close(")
    end = src.index("\n    def ", start + 1)
    block = _norm(src[start:end])
    assert "does NOT cascade" in block
    assert "bookkeeping note" in block
    assert "fold-back" in block
