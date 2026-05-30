"""Fail-loud exception hierarchy.

design.md "Principles -> Fail loud": the typed boundary rejects malformed data;
illegal transitions are refused, not silently absorbed. Every refusal raises one
of these and carries a human-readable message that the CLI/MCP adapters surface
verbatim (so the obligation lands fresh in the agent's context).
"""


class TackitError(Exception):
    """Base for every tackit-raised error. Adapters catch this to render a clean
    failure instead of a stack trace."""


class ValidationError(TackitError):
    """D2 - a value failed the typed validation boundary (bad status, missing
    required field, wrong type). Wraps/echoes Pydantic failures at the edge."""


class NotFoundError(TackitError):
    """A referenced task/edge/backup does not exist."""


class InvariantError(TackitError):
    """D14 - an operation would leave the graph inconsistent and is refused:
    a foreign-key violation (edge to a nonexistent task), a dependency cycle, or
    the close-gate (closing a task that is still stale)."""


class SyncError(TackitError):
    """D18 - the on-disk tackit.sql and the local tackit.db diverged in a way the
    auto-sync deliberately refuses to guess at (ambiguous/older/merge collision).
    Routes the agent to `import`/`export`/`restore`."""
