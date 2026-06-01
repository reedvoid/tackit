"""Interface - MCP (design.md "Interface - MCP", the agent's primary door).

A thin MCP server: each tool calls the same :mod:`tackit.core` op the CLI command
does and returns the same obligation payload in its result. No logic lives here.
Tool names are the bare verbs (``add``, ``show``, ``search``, ``close``,
``reconcile``, ``link_add``, ...). The input schema is **auto-generated from the
Python type hints / Pydantic models** by FastMCP, so it cannot drift from the real
interface (design.md: single-source-of-truth applied to the tool contract). On a
refusal (e.g. the D14 close-gate) the TackitError message rides in the error
result's content. Transport: stdio.
"""

from __future__ import annotations

from contextlib import contextmanager

from mcp.server.fastmcp import FastMCP

from .core import Core, stale_alert_payload
from .plan import parse_plan


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
        "label_nudge": core.last_label_nudge,  # D23: set iff a new label was created
        "delta": core.last_delta,  # T117: set iff a delta-bearing op just ran
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
    def edit(id: int, delta: str, name: str | None = None, description: str | None = None) -> dict:
        """Edit a task (D13) + T117. First marks its direct linked tasks stale
        (D10); returns the task plus the now-stale set you must review.

        Required ``delta`` -- one short sentence describing what changed
        semantically. The reconciler compares this against each stale link's
        `because` rationale to filter relevance, so write it for future-you:
        "shifted D5 from directed to symmetric link" beats "updated the task
        to reflect the new design." Auto-diff is worthless here -- the agent
        already knows what it did."""
        with _core() as c:
            return _wrap(c, c.edit(id, delta=delta, name=name, description=description).model_dump(mode="json"))

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
    def link_add(a: int, b: int, because: str, delta: str) -> dict:
        """Add a symmetric link between tasks ``a`` and ``b`` with required
        ``because`` (durable per-edge rationale, T116) and ``delta``
        (ephemeral one-sentence description of this change, T117). Argument
        order doesn't matter -- the row is stored canonically. Refused on
        self-link (D14), cross-kind meta links (D26 meta-island), empty
        ``because``, or empty ``delta``. Returns ``a``'s slice."""
        with _core() as c:
            return _wrap(
                c,
                c.link_add(a, b, because=because, delta=delta).model_dump(mode="json"),
            )

    @mcp.tool()
    def link_rm(a: int, b: int, delta: str) -> dict:
        """Remove the symmetric link between ``a`` and ``b`` (D5/T93) with
        required ``delta`` (T117). Argument order doesn't matter (canonical
        lookup)."""
        with _core() as c:
            return _wrap(c, c.link_rm(a, b, delta=delta).model_dump(mode="json"))

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
    def labels() -> dict:
        """List every label with its usage -- count + a few example task titles, so
        a label's meaning is clear from its tasks (D21). RUN THIS BEFORE creating a
        new label: reuse an existing one if it fits, to avoid label sprawl."""
        with _core() as c:
            out = []
            for i in c.labels_summary():
                out.append(i.model_dump(mode="json"))
            return _wrap(c, out)

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

    @mcp.tool()
    def load(plan: str) -> dict:
        """Bulk-import a plan (D24) given as TEXT: `[key] Name` lines with indented
        `desc:` / `labels:` / `depends_on:` (depends_on references other keys). Creates
        all tasks in one atomic pass, resolving keys -> ids; a malformed line or unknown
        key fails loud and rolls back the whole import. Returns the key->id map."""
        with _core() as c:
            keymap = c.load(parse_plan(plan))
            return _wrap(c, {"loaded": keymap})

    @mcp.tool()
    def board(
        status: str | None = None, label: str | None = None, stale: bool = False
    ) -> dict:
        """Dependency-aware board (D22): the filtered tasks, each as a full slice (task +
        dependencies + dependents + labels), so you see the whole graph's structure in
        ONE call (richer than `ls`). Filters: status (open/closed), label, stale."""
        with _core() as c:
            stale_filter = True if stale else None
            cards = []
            for t in c.ls(status=status, label=label, stale=stale_filter):
                cards.append(c.show(t.id).model_dump(mode="json"))
            return _wrap(c, cards)

    return mcp


def run() -> None:
    """Entry point for ``tackit mcp`` - serve over stdio (a local subprocess)."""
    build_server().run()


if __name__ == "__main__":
    run()
