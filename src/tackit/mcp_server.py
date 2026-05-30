"""Interface - MCP (design.md "Interface - MCP", the agent's primary door).

A thin MCP server: each tool calls the same :mod:`tackit.core` op the CLI command
does and returns the same obligation payload in its result. No logic lives here.
Tool names are the bare verbs (``add``, ``show``, ``search``, ``close``,
``reconcile``, ``dep_add``, ...). The input schema is **auto-generated from the
Python type hints / Pydantic models** by FastMCP, so it cannot drift from the real
interface (design.md: single-source-of-truth applied to the tool contract). On a
refusal (e.g. the D14 close-gate) the TackitError message rides in the error
result's content. Transport: stdio.
"""

from __future__ import annotations

from contextlib import contextmanager

from mcp.server.fastmcp import FastMCP

from .core import Core, stale_alert_payload


@contextmanager
def _core():
    core = Core.open()
    try:
        yield core
    finally:
        core.close_conn()


def _wrap(core: Core, result):
    """D19 - envelope every tool result as ``{"stale_alert": ..., "result": ...}`` so
    the built-in stale check surfaces in MCP exactly as it does on the CLI (design.md
    "Enforcement" tier 2). ``stale_alert`` is None when nothing is stale; otherwise it
    carries the count, the stale task ids, and the strongly-worded obligation
    message. Reflects post-op state, so a tool that creates new stale tasks (e.g.
    ``edit``) returns them right here in the same result."""
    return {
        "stale_alert": stale_alert_payload(core.stale_worklist()),
        "result": result,
    }


def build_server() -> FastMCP:
    mcp = FastMCP("tackit")

    @mcp.tool()
    def add(
        name: str,
        description: str = "",
        labels: list[str] | None = None,
        deps: list[int] | None = None,
    ) -> dict:
        """Create a task (D3). Optionally attach labels (D4) and depends_on edges
        (D5). Returns the new task's slice (under `result`) plus any `stale_alert`."""
        with _core() as c:
            t = c.add(name, description=description, labels=labels, deps=deps)
            return _wrap(c, c.show(t.id).model_dump(mode="json"))

    @mcp.tool()
    def show(id: int) -> dict:
        """Slice fetch (D9): a task plus its dependencies, dependents, and labels."""
        with _core() as c:
            return _wrap(c, c.show(id).model_dump(mode="json"))

    @mcp.tool()
    def search(terms: str, limit: int = 20) -> dict:
        """Ranked FTS keyword search over name+description (D17). `search -> show`
        is the retrieval loop. Returns ids+titles+scores, best first."""
        with _core() as c:
            hits = []
            for h in c.search(terms, limit=limit):
                hits.append(h.model_dump(mode="json"))
            return _wrap(c, hits)

    @mcp.tool()
    def edit(id: int, name: str | None = None, description: str | None = None) -> dict:
        """Edit a task (D13). First marks its direct dependents stale (D10);
        returns the task plus the now-stale set you must review/reconcile."""
        with _core() as c:
            return _wrap(c, c.edit(id, name=name, description=description).model_dump(mode="json"))

    @mcp.tool()
    def close(id: int) -> dict:
        """Close a task (D12). REFUSED if the task is stale, or if it transitively
        depends on a stale task (reconcile that upstream first, D14). On success
        returns the task's dependencies and dependents to review."""
        with _core() as c:
            return _wrap(c, c.close(id).model_dump(mode="json"))

    @mcp.tool()
    def reopen(id: int) -> dict:
        """Move a closed task back to open (D7/D8, logged)."""
        with _core() as c:
            return _wrap(c, c.reopen(id).model_dump(mode="json"))

    @mcp.tool()
    def reconcile(id: int) -> dict:
        """Clear a task's stale flag after reviewing it as still-correct (D11).
        Does not cascade (no content changed)."""
        with _core() as c:
            return _wrap(c, c.reconcile(id).model_dump(mode="json"))

    @mcp.tool()
    def dep_add(from_task: int, to_task: int) -> dict:
        """Declare `from_task depends_on to_task` (D5). Refused on self-edge or a
        cycle (D14). Returns from_task's slice."""
        with _core() as c:
            return _wrap(c, c.dep_add(from_task, to_task).model_dump(mode="json"))

    @mcp.tool()
    def dep_rm(from_task: int, to_task: int) -> dict:
        """Remove the `from_task depends_on to_task` edge (D5)."""
        with _core() as c:
            return _wrap(c, c.dep_rm(from_task, to_task).model_dump(mode="json"))

    @mcp.tool()
    def label_add(id: int, label: str) -> dict:
        """Attach a freeform label to a task (D4)."""
        with _core() as c:
            return _wrap(c, c.label_add(id, label).model_dump(mode="json"))

    @mcp.tool()
    def label_rm(id: int, label: str) -> dict:
        """Remove a label from a task (D4)."""
        with _core() as c:
            return _wrap(c, c.label_rm(id, label).model_dump(mode="json"))

    @mcp.tool()
    def ls(
        status: str | None = None, label: str | None = None, stale: bool = False
    ) -> dict:
        """Query/board (D15): filter tasks by status, label, and/or stale."""
        with _core() as c:
            stale_filter = True if stale else None
            tasks = []
            for t in c.ls(status=status, label=label, stale=stale_filter):
                tasks.append(t.model_dump(mode="json"))
            return _wrap(c, tasks)

    @mcp.tool()
    def stale() -> dict:
        """The reconciliation worklist (D11): all stale tasks. Empty == done."""
        with _core() as c:
            tasks = []
            for t in c.stale_worklist():
                tasks.append(t.model_dump(mode="json"))
            return _wrap(c, tasks)

    @mcp.tool()
    def render(label: str) -> dict:
        """Render the tasks under a label into one markdown narrative (D16)."""
        with _core() as c:
            return _wrap(c, c.render(label))

    @mcp.tool()
    def history(id: int) -> dict:
        """The append-only status-transition history of a task (D8)."""
        with _core() as c:
            hist = []
            for h in c.history(id):
                hist.append(h.model_dump(mode="json"))
            return _wrap(c, hist)

    return mcp


def run() -> None:
    """Entry point for ``tackit mcp`` - serve over stdio (a local subprocess)."""
    build_server().run()


if __name__ == "__main__":
    run()
