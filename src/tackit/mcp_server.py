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


def _wrap(core: Core, result, short_alert: bool = False):
    """D19 - envelope every tool result as ``{"stale_alert": ..., "result": ...}`` so
    the built-in stale check surfaces in MCP exactly as it does on the CLI (design.md
    "Enforcement" tier 2). ``stale_alert`` is None when nothing is stale; otherwise it
    carries the count, the stale task ids, and the strongly-worded obligation
    message. Reflects post-op state, so a tool that creates new stale tasks (e.g.
    ``edit``) returns them right here in the same result.

    M181 #8b: read ops pass ``short_alert=True`` so the message is a compact
    one-liner ("⚠ N stale — see `stale` for the list"); writes keep the full
    obligation paragraph as the at-cost teaching moment."""
    return {
        "stale_alert": stale_alert_payload(core.stale_worklist(), short=short_alert),
        "label_nudge": core.last_label_nudge,  # D23: set iff a new label was created
        "delta": core.last_delta,  # T117: set iff a delta-bearing op just ran
        # D31 (v0.4): set iff an edit just landed on a design/schema slice.
        "code_check_reminder": core.last_code_check_reminder,
        "result": result,
    }


def build_server() -> FastMCP:
    mcp = FastMCP("tackit")

    @mcp.tool()
    def add(
        name: str,
        kind: str,
        description: str = "",
        labels: list[str] | None = None,
        deps: dict[int, str] | None = None,
    ) -> dict:
        """Create a task (D3 + T94 + D33 + D36 + D37). ``kind`` is REQUIRED --
        one of design | schema | production | meta (D26). Classify by the
        'alters running-app behavior' rule: design = a decision slice, schema
        = the store's shape, production = changes the app's behavior (source,
        tests that pin contracts, README/SKILL), meta = bookkeeping / release
        tracking / experiments. The kind boundary bounds the cascade (D26
        meta-island); a stray choice silently corrupts cascade reach, so
        refuse missing/invalid loudly via D2.

        Defaults status to 'spec' for design/schema, 'open' for production/
        meta (D36 partition rule). Optionally attach labels (D4) and
        symmetric links via ``deps``: under D33 / T164 each entry is
        ``{dep_id: because}`` where ``because`` is the per-edge one-sentence
        coupling rationale (placeholder/empty rationales are refused).

        D37 -- granular-description discipline: aim for impl-ready
        granularity at create time. A fresh-session agent should be able to
        implement the task from its description alone -- avoid vague verbs,
        conversation references, TBD/TODO placeholders, pointer-only bodies.
        If a task feels too small to describe in concrete terms, it likely
        belongs as a sub-step inside a larger named unit of work.

        D38 -- don't create a fake task (the other end of right-sizing). A
        task whose only purpose is to be linked-to, or whose body is a status
        rollup of OTHER tasks, is not a unit of work: group a cluster with a
        label, and answer "is it complete?" with board/ls(label=...), never a
        hand-typed ledger that drifts the moment a real task closes. Links are
        for coupling (edit-X => recheck-Y); labels are for membership.

        Returns the new task's slice plus any ``stale_alert``."""
        with _core() as c:
            t = c.add(name, kind=kind, description=description, labels=labels, deps=deps)
            return _wrap(c, c.show(t.id).model_dump(mode="json"))

    @mcp.tool()
    def show(id: int) -> dict:
        """Slice fetch (D9): a task plus its dependencies, dependents, and labels."""
        with _core() as c:
            return _wrap(c, c.show(id).model_dump(mode="json"), short_alert=True)

    @mcp.tool()
    def search(terms: str, limit: int = 20, name_only: bool = False) -> dict:
        """Ranked FTS keyword search over name+description (D17). `search -> show`
        is the retrieval loop. Returns ids+titles+scores, best first.

        M181 #8d: ``name_only=True`` scopes the match to the name column only
        (FTS5 ``{name}: <query>`` filter). Useful for looking up tasks by
        distinctive title phrase without description hits adding noise."""
        with _core() as c:
            hits = []
            for h in c.search(terms, limit=limit, name_only=name_only):
                hits.append(h.model_dump(mode="json"))
            return _wrap(c, hits, short_alert=True)

    @mcp.tool()
    def links(
        ids: list[int] | None = None, already_seen: list[int] | None = None
    ) -> dict:
        """Deterministic link-discovery primitive (D27) -- prefer this over
        `search` for wiring a new task to the specs/tasks it couples to.
        Two modes:

          * no ``ids`` (or empty) -> the ANCHOR LAYER: all design + schema
            slices (status IN ('open','spec')), id-sorted. Production work
            links into this spec layer.
          * ``ids=[...]`` -> every task linked at depth=1 to any input id,
            minus the inputs themselves and minus ``already_seen``. Iteration
            is caller-driven: pass your accumulated "judged" set as
            ``already_seen`` so each next hop excludes what you've handled.

        Both modes filter to viable link targets (status IN ('open','spec'));
        closed/wont_do production and retired design/schema are excluded
        (link_add to a retired endpoint is refused, D36). The per-edge
        ``because`` / ``last_edit_delta`` fields are None here -- a links()
        candidate isn't tied to one edge from the input's perspective
        (NeighborRef). Judge each candidate; `link_add` the real couplings
        with a `because` (never a membership edge -- see D38)."""
        with _core() as c:
            out = []
            for n in c.links(ids=ids, already_seen=already_seen):
                out.append(n.model_dump(mode="json"))
            return _wrap(c, out, short_alert=True)

    @mcp.tool()
    def edit(id: int, delta: str, name: str | None = None, description: str | None = None) -> dict:
        """Edit a task (D13 + T117 + D29 + D36 + D37). First marks its direct
        linked tasks stale (D10); returns the task plus the now-stale set you
        must review.

        Required ``delta`` -- one short sentence describing what changed
        semantically. The reconciler compares this against each stale link's
        `because` rationale to filter relevance, so write it for future-you:
        "shifted D5 from directed to symmetric link" beats "updated the task
        to reflect the new design." Auto-diff is worthless here -- the agent
        already knows what it did.

        Use edit for ALL partial changes including major rewrites. If a
        design/schema slice's premise is 100% gone with no replacement, use
        retire() instead (D36 all-or-nothing rule). The audit table (D29)
        preserves the verbatim prior name + description + delta, so editing
        in place is recoverable.

        D37 -- granular-description discipline: if impl reveals under-defined
        details, edit() is the mechanism to fold them back BEFORE close.
        Closing with an out-of-date description destroys granularity for
        future readers. Edit is allowed on any status (open/spec/closed/
        wont_do/retired); the wont_do_reason field on wont_do/retired rows
        is the only frozen part.

        **Edits aren't free** -- fires the cascade depth-1; make edits
        consequential and necessary and the delta a substantive impact,
        not cosmetic (see SKILL "Edits aren't free")."""
        with _core() as c:
            return _wrap(c, c.edit(id, delta=delta, name=name, description=description).model_dump(mode="json"))

    @mcp.tool()
    def edit_append(id: int, content: str, delta: str) -> dict:
        """T179 - append ``content`` to a task's description. Diff-shaped
        variant of edit(): only the snippet crosses the wire, not the full
        new description. Cuts large-body edit cost ~10x.

        Fires the cascade depth-1 like edit() and writes the description_
        revisions audit row (D29 / S7) preserving the prior verbatim
        name+description+delta.

        Required ``delta`` (T117) -- one short sentence describing what
        changed semantically. The reconciler compares it against each
        stale link's `because` rationale to filter relevance.

        Refused on empty / whitespace-only ``content`` (a whitespace
        append is almost always a typo'd no-op). D37 -- granular description
        discipline: if impl reveals under-defined details, edit_append is
        the cheap fold-back mechanism BEFORE close.

        **Edits aren't free** -- fires the cascade depth-1 exactly like
        edit(); make edits consequential and necessary and the delta a
        substantive impact; diff-shape cuts transmission, not cascade cost
        (see SKILL "Edits aren't free")."""
        with _core() as c:
            return _wrap(
                c,
                c.edit_append(id, content=content, delta=delta).model_dump(
                    mode="json"
                ),
            )

    @mcp.tool()
    def edit_replace_substring(
        id: int, old_string: str, new_string: str, delta: str
    ) -> dict:
        """T179 - replace exact substring ``old_string`` with ``new_string``
        in a task's description. Diff-shaped variant of edit(): only the
        (old, new) pair crosses the wire. Cuts large-body edit cost ~10x.

        Mirrors the filesystem Edit tool's old_string/new_string pattern:
        substring boundaries are exact (literal match, not regex) and the
        match must be UNIQUE -- non-unique matches refused loudly so the
        caller adds surrounding context to disambiguate.

        Refusal matrix:
          * empty ``old_string`` -> refused (no unambiguous match point).
          * ``old_string`` not found -> refused (caller likely typo'd).
          * ``old_string`` appears N>1 times -> refused with N.

        Empty ``new_string`` is ALLOWED -- it's a legitimate deletion.
        ``old_string == new_string`` is a no-op (D20): succeeds silently
        with no cascade.

        Required ``delta`` (T117) -- semantic shift in one sentence.

        **Edits aren't free** -- fires the cascade depth-1 exactly like
        edit(); make edits consequential and necessary and the delta a
        substantive impact; diff-shape cuts transmission, not cascade cost
        (see SKILL "Edits aren't free")."""
        with _core() as c:
            return _wrap(
                c,
                c.edit_replace_substring(
                    id,
                    old_string=old_string,
                    new_string=new_string,
                    delta=delta,
                ).model_dump(mode="json"),
            )

    @mcp.tool()
    def close(id: int) -> dict:
        """Close a task (D12 + D14 + D36). For PRODUCTION + META tasks only.
        REFUSED if the task is stale, or if it transitively depends on a
        stale task (reconcile that upstream first, D14). REFUSED if
        status='spec' -- design/schema slices are living spec, not work
        items. Use edit() to refine a decision; retire() if 100% abandoned
        with no replacement (D36). REFUSED on wont_do / retired (no double-
        decide). On success returns the task's dependencies and dependents
        to review."""
        with _core() as c:
            return _wrap(c, c.close(id).model_dump(mode="json"))

    @mcp.tool()
    def reopen(id: int) -> dict:
        """Move a closed task back to open (D7/D8, logged). REFUSED on
        wont_do tasks (T132: terminal forever; change-of-mind path is a
        fresh task with the new direction). REFUSED on retired (D36: same
        terminal rationale -- file a fresh D# if the decision returned)."""
        with _core() as c:
            return _wrap(c, c.reopen(id).model_dump(mode="json"))

    @mcp.tool()
    def wont_do(id: int, reason: str, delta: str) -> dict:
        """Mark a task as decided-not-to-do, distinct from closed=done (T132).
        For PRODUCTION + META tasks only. ``reason`` is durable (persists
        forever in wont_do_reason); ``delta`` is ephemeral per T117.

        Locked-forever per T132: reopen / close / wont_do refused on wont_do
        tasks (change-of-mind path is a fresh task with the new direction).
        REFUSED if task is stale or in a linked-stale neighborhood (D14
        close-gate). REFUSED on already-wont_do / closed / retired tasks (no
        double-decide). REFUSED if status='spec' (D36: design/schema can't
        be 'not done' -- use edit() to refine, or retire() if 100%
        abandoned). Does not fire the staling cascade -- returns one-hop
        neighbors for migrate-or-stay review like close."""
        with _core() as c:
            return _wrap(c, c.wont_do(id, reason=reason, delta=delta).model_dump(mode="json"))

    @mcp.tool()
    def retire(id: int, reason: str, delta: str) -> dict:
        """Retire a design/schema slice (D36): status spec -> retired. For
        DESIGN + SCHEMA only. Use ONLY when the decision is 100% gone with
        NO replacement -- partial-change path is edit() and let the cascade
        prompt link review.

        ``reason`` is durable (persists forever in wont_do_reason -- the
        decision rationale survives even after the slice is dead). ``delta``
        is ephemeral per T117. Placeholder reasons (empty / 'TBD' / 'TODO'
        / 'obsolete' / 'no longer needed') refused per D33 extension.

        Refusal order (fail-fast, 6 checks):
          1. reason validation (non-empty + non-placeholder).
          2. status='spec' (only living specs can be retired).
          3. kind IN ('design','schema').
          4. stale gate.
          5. linked-stale gate (transitive close-gate logic).
          6. open-neighbor gate -- refused if ANY linked neighbor has
             status='open'. Refusal lists each open neighbor with its
             `because` rationale and presents the (i)/(ii) decision tree
             (link_rm + wont_do vs link_rm alone) inline.

        Terminal-state semantics: retired is forever per T132 generalized.
        reopen / close / wont_do / retire all refused on retired rows.
        edit() IS still allowed (D29 audit-table backstop). link_add
        refused if either endpoint has status='retired'. Returns one-hop
        neighbors for migrate-or-stay review like wont_do/close."""
        with _core() as c:
            return _wrap(c, c.retire(id, reason=reason, delta=delta).model_dump(mode="json"))

    @mcp.tool()
    def reconcile(ids: list[int]) -> dict:
        """Batch-reconcile an EXPLICIT list of task ids: clear ``stale`` on
        each after reviewing them as still-correct (D11 + D28 + D36 + D39).
        Does not cascade (no content changed). One transaction, one version
        bump. Pass ``[id]`` to reconcile a single task.

        Validate-all-first: any terminal-status (closed/wont_do/retired) or
        unknown id refuses the WHOLE batch and names every offender -- stale
        on terminal rows is record-only archaeology that can't be cleared.

        D39 GUARD-RAIL: the list is explicit on purpose -- you still
        enumerate the set you judged still-correct. There is deliberately no
        'reconcile all stale' form; that would automate the judgment the
        cascade depends on (the rubber-stamp failure mode the edit-quality +
        D34 disciplines prevent). reconcile batches transport, not judgment.

        Returns ``{"reconciled": [ids], "remaining_stale": N}`` with a SHORT
        alert -- a known-clean sweep doesn't reprint the full obligation
        paragraph on every call (D39 #6)."""
        with _core() as c:
            c.reconcile_many(ids)
            return _wrap(
                c,
                {"reconciled": ids, "remaining_stale": len(c.stale_worklist())},
                short_alert=True,
            )

    @mcp.tool()
    def reclassify(id: int, kind: str, delta: str) -> dict:
        """Change a task's kind after creation (T128 + D36). Required
        ``delta`` names the semantic shift (T117). REFUSED if the new kind
        would create a cross-kind link with any current neighbor (meta-
        island, D26) -- the agent must link_rm those edges first or create
        a new task carrying the desired kind.

        Cross-partition kind change auto-shifts status (D36): production/
        meta ('open') <-> design/schema ('spec'); refused if the source
        status has no clean target (e.g. closed production -> design would
        leave the closed work-done semantics with no spec equivalent).

        Fires the staling cascade on linked neighbors (kind is a semantic
        property; the link relationship may need re-review). Closed/wont_do/
        retired neighbors stay terminal + stale per D28 (record only)."""
        with _core() as c:
            return _wrap(c, c.reclassify(id, kind, delta=delta).model_dump(mode="json"))

    @mcp.tool()
    def link_add(a: int, b: int, because: str) -> dict:
        """Add a symmetric link between tasks ``a`` and ``b`` with required
        ``because`` (durable per-edge coupling rationale, T116). Link ops do
        NOT cascade and carry NO ``delta`` (D213) -- delta exists to ride a
        cascade, so a non-cascading op produces a delta nobody reads. Argument
        order doesn't matter -- the row is stored canonically. Refused on
        self-link (D14), cross-kind meta links (D26 meta-island), or empty
        ``because``. Refused if either endpoint has
        status='retired' (D36): retired specs accept no new edges --
        there is no realization relationship to a dead decision.

        D38 -- ``because`` must name the COUPLING: the consequence that editing
        one endpoint forces re-checking the other. If the honest rationale is
        only "they're in the same epic/theme," that's MEMBERSHIP, not coupling
        -- attach a shared label instead of a link. A because that merely
        restates a cluster's label name is a membership edge wearing a coupling
        costume, and is pure cascade noise.

        D39 #2 -- returns a COMPACT confirmation ``{"linked": {"a", "b",
        "because"}}``, not ``a``'s full slice. link_add is structural (it
        does not fire the cascade), so the neighborhood echo carries no
        obligation the caller must act on; reprinting a high-degree node's
        whole neighborhood on every bulk edge was pure context tax. Use
        ``show(a)`` when you actually want the slice."""
        with _core() as c:
            c.link_add(a, b, because=because)
            return _wrap(
                c,
                {"linked": {"a": a, "b": b, "because": because}},
                short_alert=True,
            )

    @mcp.tool()
    def link_rm(a: int, b: int) -> dict:
        """Remove the symmetric link between ``a`` and ``b`` (D5/T93). Link ops
        do NOT cascade and carry NO ``delta`` (D213). Argument order doesn't
        matter (canonical lookup)."""
        with _core() as c:
            return _wrap(c, c.link_rm(a, b).model_dump(mode="json"))

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
        """Query/board (D15): filter tasks by status, label, and/or stale.
        ``status`` choices (D36 v0.5): open | closed | wont_do | spec |
        retired. spec/retired are the design/schema partition equivalents."""
        with _core() as c:
            stale_filter = True if stale else None
            tasks = []
            for t in c.ls(status=status, label=label, stale=stale_filter):
                tasks.append(t.model_dump(mode="json"))
            return _wrap(c, tasks, short_alert=True)

    @mcp.tool()
    def stale() -> dict:
        """The reconciliation worklist (D11): all stale tasks. Empty == done."""
        with _core() as c:
            tasks = []
            for t in c.stale_worklist():
                tasks.append(t.model_dump(mode="json"))
            return _wrap(c, tasks, short_alert=True)

    @mcp.tool()
    def labels() -> dict:
        """List every label with its usage -- count + a few example task titles, so
        a label's meaning is clear from its tasks (D21). RUN THIS BEFORE creating a
        new label: reuse an existing one if it fits, to avoid label sprawl."""
        with _core() as c:
            out = []
            for i in c.labels_summary():
                out.append(i.model_dump(mode="json"))
            return _wrap(c, out, short_alert=True)

    @mcp.tool()
    def render(label: str) -> dict:
        """Render the tasks under a label into one markdown narrative (D16)."""
        with _core() as c:
            return _wrap(c, c.render(label), short_alert=True)

    @mcp.tool()
    def history(id: int) -> dict:
        """The full append-only history of a task (D8 + D29 v0.4): status
        transitions and description revisions, both in chronological order.
        Description revisions preserve verbatim prior name/description
        plus the delta rationale, so archaeology can recover what the task
        used to say -- the backstop for edit-on-closed under v0.4."""
        with _core() as c:
            return _wrap(c, c.history(id).model_dump(mode="json"), short_alert=True)

    @mcp.tool()
    def load(plan: str) -> dict:
        """Bulk-import a plan (D24 + T94 + D40) given as TEXT: `[key] Name` lines
        with indented `kind:` (REQUIRED, one of design|schema|production|meta) /
        `desc:` / `labels:` / `depends_on:` (depends_on references other keys).
        Creates all tasks in one atomic pass, resolving keys -> ids; a malformed
        line, missing/invalid kind, or unknown dep key fails loud and rolls back
        the whole import (no partial plan). Returns the key->id map.

        THIS is the path for importing many tasks at once -- prefer it over N
        separate add() calls. `desc:` may span multiple paragraphs: deeper-
        indented lines continue it and blank lines between them are preserved
        as paragraph breaks (D40), so impl-ready D37-grade bodies round-trip.
        `depends_on:` is a continuation block, one edge per line as
        `<key> :: <because rationale>` (D33)."""
        with _core() as c:
            keymap = c.load(parse_plan(plan))
            return _wrap(c, {"loaded": keymap})

    @mcp.tool()
    def board(
        status: str | None = None, label: str | None = None, stale: bool = False
    ) -> dict:
        """Dependency-aware board (D22 + D36): the filtered tasks, each as a
        full slice (task + dependencies + dependents + labels), so you see
        the whole graph's structure in ONE call (richer than `ls`). Filters:
        status (open|closed|wont_do|spec|retired), label, stale."""
        with _core() as c:
            stale_filter = True if stale else None
            cards = []
            for t in c.ls(status=status, label=label, stale=stale_filter):
                cards.append(c.show(t.id).model_dump(mode="json"))
            return _wrap(c, cards, short_alert=True)

    return mcp


def run() -> None:
    """Entry point for ``tackit mcp`` - serve over stdio (a local subprocess)."""
    build_server().run()


if __name__ == "__main__":
    run()
