"""Interface - MCP (design.md "Interface - MCP", the agent's primary door).

A thin MCP server: each tool calls the same :mod:`tackit.core` op the CLI command
does and returns the same result, projected in :mod:`tackit.models` (D211). No logic
lives here.
Tool names are the bare verbs (``add``, ``show``, ``search``, ``close``,
``reconcile``, ``link_add``, ...). The input schema is **auto-generated from the
Python type hints / Pydantic models** by FastMCP, so it cannot drift from the real
interface (design.md: single-source-of-truth applied to the tool contract). On a
refusal (e.g. the D14 close-gate) the TackitError message rides in the error
result's content. Transport: stdio.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities import func_metadata as _func_metadata
from pydantic import ValidationError as _PydanticValidationError

from .core import Core, stale_alert_payload
from .models import (
    project_add_result,
    project_change_result,
    project_label_result,
    project_link_add_result,
    project_link_rm_result,
    project_show_slice,
    project_slice,
    project_status_result,
    project_task,
)
from .plan import parse_plan


@contextmanager
def _core():
    core = Core.open()
    try:
        yield core
    finally:
        core.close_conn()


# D61: the full envelope key set, in emission order. `_wrap` builds from it and the
# README guard reads it, so a new key is added here once (and listed in README).
ENVELOPE_KEYS = (
    "stale_alert",
    "label_nudge",
    "code_check_reminder",
    "coherence_nudge",
    "deadref_suggestions",
    "missing_edge_suggestions",
    "result",
)


def _wrap(core: Core, result, short_alert: bool = False) -> str:
    """D61 - envelope every tool result and return it as compact JSON text.

    `result` is always present; every advisory key appears ONLY when it is set,
    never as null (D61, extending D211's absent-not-null rule):
      * ``stale_alert`` -- the outstanding stale set (count, ids, message), post-op,
        so an op that flags new tasks returns them in the same result;
      * ``label_nudge`` (D65), ``code_check_reminder``, ``coherence_nudge`` (D250),
        ``deadref_suggestions`` (D249), ``missing_edge_suggestions`` (D280) -- the
        per-op advisories, each set by Core only for the op that produced it.
    The op's delta is not echoed: the agent wrote it (D61; stored per D284).

    Serialized once, compactly and with non-ASCII kept literal, because the agent
    reads this text verbatim; every tool is registered with
    ``structured_output=False`` so the text is the only content.

    M181 #8b: read ops pass ``short_alert=True`` so the message is a compact
    one-liner ("⚠ N stale — see `stale` for the list"); writes keep the full
    obligation paragraph as the at-cost teaching moment."""
    values = {
        "stale_alert": stale_alert_payload(core.stale_worklist(), short=short_alert),
        "label_nudge": core.last_label_nudge,
        "code_check_reminder": core.last_code_check_reminder,
        "coherence_nudge": core.last_coherence_nudge,
        "deadref_suggestions": core.last_deadref_suggestions,
        "missing_edge_suggestions": core.last_missing_edge_suggestions,
        "result": result,
    }
    if set(values) != set(ENVELOPE_KEYS):
        raise RuntimeError(
            f"envelope keys {sorted(values)} differ from ENVELOPE_KEYS "
            f"{sorted(ENVELOPE_KEYS)} (D61): add a new key to both."
        )
    envelope = {}
    for key in ENVELOPE_KEYS:
        if key == "result" or values[key] is not None:
            envelope[key] = values[key]
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False, default=str)


def _enforce_strict_param_gate() -> None:
    """T236 / D2 - make every FastMCP tool REJECT an unrecognised parameter
    loudly instead of silently dropping it (the fail-loud validation boundary,
    extended to the adapter's param surface).

    FastMCP builds each tool's argument validator as a pydantic model
    subclassing ``ArgModelBase``; by default extras are IGNORED, so a guessed
    param (e.g. ``ls(query=...)``) was dropped and the op ran UNFILTERED --
    returning everything, which masked the agent's mistake as 'the filter is
    broken'. Setting ``extra='forbid'`` on that shared base makes the validator
    raise (and stamps ``additionalProperties:false`` into the advertised
    schema), so the offending param name is surfaced back to the agent and it
    can self-correct in-session.

    This mutates an mcp-library internal (``ArgModelBase.model_config``); it is
    deliberately pinned-version-coupled (mcp 1.27.x) and GUARDED by
    ``_assert_param_gate`` below -- if the internal moves, ``build_server``
    fails loud rather than silently losing the gate."""
    _func_metadata.ArgModelBase.model_config["extra"] = "forbid"
    _install_unknown_param_guidance()


def _install_unknown_param_guidance() -> None:
    """T236 / D2 - turn the bare rejection into actionable guidance: name the
    unrecognised param(s) AND list the tool's valid params, so the agent is
    pushed onto the right syntax in-session rather than just told 'no'.

    pydantic's default ``extra_forbidden`` message names the bad param but not
    the valid set; that set is the tool's ``arg_model.model_fields``. We wrap
    ``FuncMetadata.call_fn_with_arg_validation`` -- the point where the raw
    ``ValidationError`` is raised, before FastMCP's ``Tool.run`` stringifies it
    into ``Error executing tool <name>: <msg>`` -- and re-raise extra-param
    errors with the field list. Non-extra validation errors pass through
    untouched. Idempotent and signature-agnostic (``*args``); pinned mcp
    1.27.x, verified by the param-gate test asserting valid params appear in
    the error text."""
    fm = _func_metadata.FuncMetadata
    if getattr(fm.call_fn_with_arg_validation, "_tackit_enriched", False):
        return
    _orig = fm.call_fn_with_arg_validation

    async def _enriched(self, *args, **kwargs):
        try:
            return await _orig(self, *args, **kwargs)
        except _PydanticValidationError as exc:
            unknown = [
                str(e["loc"][0])
                for e in exc.errors()
                if e.get("type") == "extra_forbidden" and e.get("loc")
            ]
            if not unknown:
                raise  # a real validation error on a known field -- leave it
            valid = ", ".join(self.arg_model.model_fields)
            raise ValueError(
                f"unrecognised parameter(s) {unknown}; this tool accepts only: "
                f"{valid} (T236 -- params are validated strictly: tackit tools "
                f"take typed filters, not a free-form query string)."
            ) from exc

    _enriched._tackit_enriched = True
    fm.call_fn_with_arg_validation = _enriched


def _assert_param_gate(mcp: FastMCP) -> None:
    """Fail-loud guard for T236: confirm ``extra='forbid'`` actually propagated
    into every registered tool's advertised schema. A silently-degraded gate is
    exactly the failure mode this fix removes (D2), so a missed propagation must
    break the build, not pass quietly."""
    offenders = [
        t.name
        for t in mcp._tool_manager.list_tools()
        if t.parameters.get("additionalProperties") is not False
    ]
    if offenders:
        raise RuntimeError(
            f"T236 strict-param gate did not take effect for tools {offenders}: "
            "the mcp ArgModelBase internal may have moved (pinned mcp 1.27.x). "
            "Re-establish extra='forbid' on the tool argument models before "
            "shipping -- the adapter MUST fail loud on unknown params, never "
            "silently ignore them (D2)."
        )


def build_server() -> FastMCP:
    # T236: patch BEFORE any @mcp.tool() runs, so every tool's arg model inherits
    # extra='forbid'. Idempotent across repeated build_server() calls.
    _enforce_strict_param_gate()
    mcp = FastMCP("tackit")

    @mcp.tool(structured_output=False)
    def add(
        name: str,
        kind: str,
        description: str = "",
        labels: list[str] | None = None,
        deps: dict[int, str] | None = None,
    ) -> str:
        """Create a task (D3 + T94 + D33 + D36 + D37). ``kind`` is REQUIRED --
        one of design | schema | production | meta (D26). Classify by the
        'alters running-app behavior' rule: design = a decision slice, schema
        = the store's shape, production = changes the app's behavior (source,
        tests that pin contracts, README/SKILL), meta = bookkeeping / release
        tracking / experiments. The kind boundary bounds the cascade (D26
        meta-island); a stray choice silently corrupts cascade reach, so
        refuse missing/invalid loudly via D2.

        D245 -- if a task would BOTH settle a design/schema-grade decision AND
        build it, split it: the decision is a design/schema slice, the build a
        production task linking to it; never let the decision ride inside the
        production body.

        D256 -- a production task is REFUSED at creation if it links zero
        design/schema slices: it must link the spec it realizes (pass it in
        ``deps``). D276 -- create the spec + production tasks only once the
        decision is SETTLED; keep unsettled/exploratory work in a meta
        scratchpad first. (A production task realizing an ALREADY-made
        decision just links the existing spec -- it mints no new slice.)

        Defaults status to 'spec' for design/schema, 'open' for production/
        meta (D36 partition rule). Optionally attach labels (D4) and
        symmetric links via ``deps``: under D33 / T164 each entry is
        ``{dep_id: because}`` where ``because`` is the per-edge one-sentence
        coupling rationale (an empty or whitespace-only rationale is refused).

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
        for coupling (edit-X => recheck-Y); labels are for membership. And a
        relationship belongs on a link, not narrated in this body -- the
        cascade can't see prose; wire it with link_add (or depends_on).

        Side-door work -- a bug found in use, a change no task covers, a
        follow-up spotted mid-task, an ad-hoc decision -- has no trigger of its
        own but an OBSERVABLE one: the moment you Edit/Write a tracked file the
        current task doesn't name, state the disposition (new task / fold-back /
        not-tracked because X) -- don't skip it because the change "felt like
        housekeeping". The test vs. a fold-back: is there an OPEN task whose
        scope already covers this? yes -> edit that one; no -> add() this. See
        SKILL.md "File side-door work the moment it appears".

        Returns ``{id}`` (D211) plus any ``stale_alert``; use `show` to read
        the task back."""
        with _core() as c:
            t = c.add(name, kind=kind, description=description, labels=labels, deps=deps)
            return _wrap(c, project_add_result(t))

    @mcp.tool(structured_output=False)
    def show(id: int, include_neighbor_because: bool = False) -> str:
        """Slice fetch (D9): a task plus its `links` (the single symmetric neighbour set) and labels. The task is returned in full. Links are lean by default (id/prefixed_name/status/stale/kind). `because` and `last_edit_delta` appear only when a link or this task is stale and open/spec, for reconciliation. Pass include_neighbor_because=True for them on every link.

        D211 + D34 + T294."""
        with _core() as c:
            payload = project_show_slice(
                c.show(id), include_neighbor_because=include_neighbor_because
            )
            return _wrap(c, payload, short_alert=True)

    @mcp.tool(structured_output=False)
    def search(terms: str, limit: int = 20, name_only: bool = False) -> str:
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

    @mcp.tool(structured_output=False)
    def links(
        ids: list[int] | None = None, already_seen: list[int] | None = None
    ) -> str:
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

    @mcp.tool(structured_output=False)
    def edit(id: int, delta: str, name: str | None = None, description: str | None = None, include_description: bool = False) -> str:
        """Edit a task (D13 + T117 + D29 + D36 + D37). First marks its direct
        linked tasks stale (D10); returns the task plus the neighbors this edit
        newly flagged that are open or spec, each as `{id, because}`.

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

        D37 -- granular-description discipline: if implementation reveals an
        under-defined detail, route it as a fold-back: a decision to its spec
        slice, a correction of this task's forecast by rewriting the body
        (SKILL "Fold-backs"). Edit is allowed on OPEN, SPEC, and RETIRED rows;
        REFUSED on closed / wont_do (D259 -- reopen a closed task to edit it,
        then re-close; a wont_do task is terminal). The wont_do/retire reason
        is never editable.

        D277 -- editing a production task is discouraged as a smell of thin
        prep: on production, edit is correction / scope-shrink only (D255),
        and a recurring need to edit signals the spec was underspecified --
        fix the spec / re-derive rather than iterate the body.

        **Edits aren't free** -- fires the cascade depth-1; make edits
        consequential and necessary and the delta a substantive impact,
        not cosmetic (see SKILL "Edits aren't free").

        D211 -- the return is LEAN by default: the focal task's
        ``description`` (the body you just wrote) is NOT echoed back.
        ``newly_stale`` lists each neighbor this edit moved from clear to
        flagged that is open or spec, as ``{id, because}``. Terminal and
        already-flagged neighbors still get the flag but are not listed; the
        latter stay on the worklist and in the alert. Pass
        ``include_description=True`` to get the full reconstructed body
        (e.g. to verify the edit landed as intended)."""
        with _core() as c:
            return _wrap(
                c,
                project_change_result(
                    c.edit(id, delta=delta, name=name, description=description),
                    include_description=include_description,
                ),
            )

    @mcp.tool(structured_output=False)
    def edit_append(id: int, content: str, delta: str, include_description: bool = False) -> str:
        """T179 - append ``content`` to a task's description. Diff-shaped
        variant of edit(): only the snippet crosses the wire, not the full
        new description. Cuts large-body edit cost ~10x.

        D10 -- flags NO neighbor, a concession to reality, not necessarily the
        optimal decision: agents clear flags unread, and a cleared flag reads
        as a review that did not happen. So an appended ruling can make a
        neighbor wrong with nobody told. When a note really changes what a
        neighbor should say, use edit() or edit_replace_substring(), which do
        flag.

        Writes the description_revisions audit row (D29 / S7) preserving the
        prior verbatim name+description+delta.

        Required ``delta`` (D284) -- one short sentence describing what
        changed semantically. `history` and a neighbor's `last_edit_delta`
        read it back as the record of this note.

        edit_append is META-ONLY. REFUSED on design/schema (D250 -- a spec
        slice is a coherent current-state body, not an append log; use edit()
        to rewrite or edit_replace_substring() for a targeted change) AND on
        production (D255 -- a production task is forecast-then-record and must
        not accrete; use edit()/edit_replace_substring() to correct). Only
        meta, the notepad, appends. Also refused on closed / wont_do (D259,
        terminal rows are frozen).

        Refused on empty / whitespace-only ``content`` (a whitespace
        append is almost always a typo'd no-op). D37 -- on META, edit_append
        is the cheap fold-back mechanism; on production/spec the append is
        refused, so use the edit-family to fold detail back.

        **Edits aren't free** -- every append stays in this note's audit trail
        and is read by whoever opens it next; make edits consequential and
        necessary and the delta a substantive impact (see SKILL "Edits aren't
        free").

        D211 -- lean-by-default return: the focal body is NOT echoed (you
        just appended to it). ``newly_stale`` is always empty. Pass
        ``include_description=True`` to see the full reconstructed body and
        verify the append landed where intended / didn't duplicate."""
        with _core() as c:
            return _wrap(
                c,
                project_change_result(
                    c.edit_append(id, content=content, delta=delta),
                    include_description=include_description,
                ),
            )

    @mcp.tool(structured_output=False)
    def edit_replace_substring(
        id: int, old_string: str, new_string: str, delta: str, include_description: bool = False
    ) -> str:
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
          * closed / wont_do task -> refused (D259 terminal-immutability;
            reopen a closed task first). Retired slices stay editable.

        Empty ``new_string`` is ALLOWED -- it's a legitimate deletion.
        ``old_string == new_string`` is a no-op (D20): succeeds silently
        with no cascade. Allowed on production for correction / scope-shrink
        (D255) -- but a recurring need to edit a production body is a smell
        of thin prep (D277); fix the spec instead.

        Required ``delta`` (T117) -- semantic shift in one sentence.

        **Edits aren't free** -- fires the cascade depth-1 exactly like
        edit(); make edits consequential and necessary and the delta a
        substantive impact; diff-shape cuts transmission, not cascade cost
        (see SKILL "Edits aren't free").

        D211 -- lean-by-default return: the focal body is NOT echoed;
        ``newly_stale`` lists first-time open or spec flags as
        ``{id, because}``. Pass ``include_description=True`` to see
        the full reconstructed body and verify the replacement landed."""
        with _core() as c:
            return _wrap(
                c,
                project_change_result(
                    c.edit_replace_substring(
                        id,
                        old_string=old_string,
                        new_string=new_string,
                        delta=delta,
                    ),
                    include_description=include_description,
                ),
            )

    @mcp.tool(structured_output=False)
    def close(id: int) -> str:
        """Close a task (D12 + D14 + D36). For PRODUCTION + META tasks only.
        REFUSED if the task is stale, or if a DIRECT (1-hop) linked neighbor
        is stale (D56). The refusal names each stale neighbor with its link's
        ``because``: show it, read it against that coupling, then edit or
        reconcile it before retrying. REFUSED if
        status='spec' -- design/schema slices are living spec, not work
        items. Use edit() to refine a decision; retire() if 100% abandoned
        with no replacement (D36). REFUSED on wont_do / retired (no double-
        decide). On success returns ``{id, status}`` (D12, D211): the gate
        already refused any live stale neighbor, so no review is owed. `show`
        returns the neighbors if you want them.

        close() records "done" and does NOT cascade -- so DON'T first edit the
        body with a bookkeeping note ("done", "committed <hash>"); that fires a
        pointless cascade and the status + git already carry it. Only a
        fold-back (a discovered constraint/decision a reader needs) earns a
        pre-close edit. See SKILL.md "Edits aren't free"."""
        with _core() as c:
            return _wrap(c, project_status_result(c.close(id).task))

    @mcp.tool(structured_output=False)
    def reopen(id: int) -> str:
        """Move a closed task back to open (D7/D8, logged). Reopen corrects a
        closed record: fix its text (reopen, edit, close again; D259) or
        undo a premature close. New work after a close is a new task linked
        to the closed one. A close that should have been a drop: use
        wont_do on the closed task directly. REFUSED on wont_do tasks
        (T132: terminal forever; change-of-mind path is a fresh task with
        the new direction). REFUSED on retired (D36: file a fresh D# if the
        decision returned). Returns ``{id, status}`` (D211)."""
        with _core() as c:
            return _wrap(c, project_status_result(c.reopen(id)))

    @mcp.tool(structured_output=False)
    def wont_do(id: int, reason: str) -> str:
        """Mark a task as decided-not-to-do, distinct from closed=done (T132).
        For PRODUCTION + META tasks only. ``reason`` is durable (persists
        forever in wont_do_reason). No ``delta`` (D284): wont_do does not
        fire the cascade, so a delta would have no reader -- terminal status
        verbs carry ``reason`` only, symmetric with close.

        Locked-forever per T132: reopen / close / wont_do refused on wont_do
        tasks (change-of-mind path is a fresh task with the new direction).
        REFUSED if task is stale or has a directly-linked (1-hop) stale
        neighbor (D14 / D56 close-gate). REFUSED on already-wont_do / retired
        tasks (no double-decide). ALLOWED on a closed task (D56): a close
        that should have been a drop is recorded as dropped, and the close
        stays in the status history. REFUSED if status='spec' (D36: design/schema can't
        be 'not done' -- use edit() to refine, or retire() if 100%
        abandoned). Does not fire the staling cascade. Returns
        ``{id, status}`` (D211); `show` returns the neighbors."""
        with _core() as c:
            return _wrap(c, project_status_result(c.wont_do(id, reason=reason).task))

    @mcp.tool(structured_output=False)
    def retire(id: int, reason: str) -> str:
        """Retire a design/schema slice (D36): status spec -> retired. For
        DESIGN + SCHEMA only. Use ONLY when the decision is 100% gone with
        NO replacement -- partial-change path is edit() and let the cascade
        prompt link review.

        ``reason`` is durable (persists forever in wont_do_reason -- the
        decision rationale survives even after the slice is dead). No
        ``delta`` (D284): retire does not fire the cascade, so a delta would
        have no reader. Placeholder reasons (empty / 'TBD' / 'TODO'
        / 'obsolete' / 'no longer needed') refused per D33 extension.

        Refusal order (fail-fast, 6 checks):
          1. reason validation (non-empty + non-placeholder).
          2. status='spec' (only living specs can be retired).
          3. kind IN ('design','schema').
          4. stale gate.
          5. linked-stale gate (1-hop close-gate, D56).
          6. open-neighbor gate -- refused if ANY linked neighbor has
             status='open'. Refusal lists each open neighbor with its
             `because` rationale and presents the (i)/(ii) decision tree
             (link_rm + wont_do vs link_rm alone) inline.

        Terminal-state semantics: retired is forever per T132 generalized.
        reopen / close / wont_do / retire all refused on retired rows.
        edit() IS still allowed (D29 audit-table backstop). link_add
        refused if either endpoint has status='retired'. Returns
        ``{id, status}`` (D211); `show` returns the neighbors."""
        with _core() as c:
            return _wrap(c, project_status_result(c.retire(id, reason=reason).task))

    @mcp.tool(structured_output=False)
    def reconcile(ids: list[int]) -> str:
        """Batch-reconcile an EXPLICIT list of task ids: clear ``stale`` on
        each after reviewing them as still-correct (D11 + D28 + D36 + D39).
        Does not cascade (no content changed). One transaction, one version
        bump. Pass ``[id]`` to reconcile a single task.

        Validate-all-first: any terminal-status (closed/wont_do/retired) or
        unknown id refuses the WHOLE batch and names every offender --
        terminal tasks carry no stale flag (D137), so naming one is a mistake.

        D39 GUARD-RAIL: the list is explicit on purpose -- you still
        enumerate the set you judged still-correct. There is deliberately no
        'reconcile all stale' form; that would automate the judgment the
        cascade depends on (the rubber-stamp failure mode the edit-quality +
        D34 disciplines prevent). reconcile batches transport, not judgment.

        D245 -- when a task's stale flag came from a production/meta edit that
        SETTLED a decision, reconciling means PORTING that decision into this
        spec slice first, not just confirming its prose still holds.

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

    @mcp.tool(structured_output=False)
    def reclassify(id: int, kind: str, delta: str) -> str:
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
        property; the link relationship may need re-review). Only live
        neighbors are flagged; closed, wont_do and retired ones are untouched
        (D137).

        D211 -- returns the task's scalars without its body, plus
        ``newly_stale``: first-time open or spec flags as ``{id, because}``."""
        with _core() as c:
            return _wrap(
                c,
                project_change_result(
                    c.reclassify(id, kind, delta=delta), include_description=False
                ),
            )

    @mcp.tool(structured_output=False)
    def link_add(a: int, b: int, because: str) -> str:
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
        costume, and is pure cascade noise. Conversely: a relationship you'd
        otherwise describe in a task body belongs HERE as an edge -- the
        cascade traverses links, never prose buried in a description.

        D39 #2 -- returns a COMPACT confirmation ``{"linked": {"a", "b",
        "because"}}``, not ``a``'s full slice; ``because`` is the stored
        rationale, so a duplicate link reports its first one. link_add is structural (it
        does not fire the cascade), so the neighborhood echo carries no
        obligation the caller must act on; reprinting a high-degree node's
        whole neighborhood on every bulk edge was pure context tax. Use
        ``show(a)`` when you actually want the slice."""
        with _core() as c:
            return _wrap(
                c,
                project_link_add_result(c.link_add(a, b, because=because), a, b),
                short_alert=True,
            )

    @mcp.tool(structured_output=False)
    def link_rm(a: int, b: int) -> str:
        """Remove the symmetric link between ``a`` and ``b`` (D5/T93). Link ops
        do NOT cascade and carry NO ``delta`` (D213). Argument order doesn't
        matter (canonical lookup). Returns ``{"unlinked": {"a", "b"}}`` (D211)."""
        with _core() as c:
            c.link_rm(a, b)
            return _wrap(c, project_link_rm_result(a, b))

    @mcp.tool(structured_output=False)
    def links_add(edges: list[dict]) -> str:
        """Bulk-create links between EXISTING tasks (D213) -- the existing<->
        existing wiring pass that `load` can't cover (load only creates NEW
        tasks). Each edge is `{"a", "b", "because"}`: `a`/`b` are an id or a
        prefixed-name (e.g. "S30", kind-letter validated against the target),
        `because` is the per-edge coupling rationale (T116). There is
        deliberately NO batch-wide because (a shared because is the membership-
        link anti-pattern, D38 -- the flat list with mandatory per-edge because
        forbids it) and NO `delta` (link ops don't cascade, D213).

        Validate-all-first: any structural offender -- self-link (D14), cross-
        kind meta (D26), retired endpoint (D36), unknown/malformed ref, or empty
        because -- refuses the WHOLE batch and names EVERY offender so you fix
        them in one pass. An already-linked edge (or intra-batch duplicate) is a
        benign no-op (counted, re-runnable), NOT a rejection. One transaction.

        Returns a COMPACT `{"created", "already_linked", "created_pairs"}`
        (pairs by prefixed-name); never the because text or neighborhoods (link
        ops are structural -- the result carries no obligation to act on)."""
        with _core() as c:
            return _wrap(c, c.links_add(edges), short_alert=True)

    @mcp.tool(structured_output=False)
    def label_add(id: int, label: str) -> str:
        """Attach a freeform label to a task (D4). Returns ``{id, labels}``,
        the task's labels after the change (D211)."""
        with _core() as c:
            c.label_add(id, label)
            return _wrap(c, project_label_result(id, c.labels_of(id)))

    @mcp.tool(structured_output=False)
    def label_rm(id: int, label: str) -> str:
        """Remove a label from a task (D4). Returns ``{id, labels}``, the task's
        labels after the change (D211)."""
        with _core() as c:
            c.label_rm(id, label)
            return _wrap(c, project_label_result(id, c.labels_of(id)))

    @mcp.tool(structured_output=False)
    def ls(
        status: str | None = None,
        label: str | None = None,
        stale: bool = False,
        kind: str | None = None,
        name_prefix: str | None = None,
        include_description: bool = False,
        order_by: str | None = None,
    ) -> str:
        """Query/board (D15 + D211 + T220 + D283): filter tasks by status, label,
        stale, kind, and/or name_prefix, optionally sorted by a structured column.
        Returns a LEAN projection by default -- task scalars only, NO
        `description` (D211); pass include_description=True for full bodies. For
        ONE full body use `show`. `status` choices (D36 v0.5): open | closed |
        wont_do | spec | retired; `kind`: design | schema | production | meta.
        `name_prefix` (T220) scopes to tasks whose name begins with a LITERAL
        case-sensitive prefix -- use it to pull one section of a large layer
        (e.g. name_prefix='§9.1') instead of the whole kind. It matches the bare
        name, NOT the synthesized prefixed_name (so filter on '§9.1', not 'D39').
        `order_by` (D283) sorts by a STRUCTURED column only -- one of id / kind /
        status / created_at / updated_at / stale / degree (link count); never
        body content. Defaults to id; a disallowed value is refused loudly."""
        with _core() as c:
            stale_filter = True if stale else None
            tasks = []
            for t in c.ls(
                status=status, label=label, stale=stale_filter,
                kind=kind, name_prefix=name_prefix, order_by=order_by,
            ):
                tasks.append(project_task(t, include_description=include_description))
            return _wrap(c, tasks, short_alert=True)

    @mcp.tool(structured_output=False)
    def summary() -> str:
        """Structured-column rollup (D283) for situational awareness -- serves
        the mandated end-of-turn state line. Returns counts by status, by kind,
        by (status × kind), the total, and the obligation-bearing stale count
        (stale AND status IN open/spec). A PURE read over structured columns; it
        never touches body content, and a count forces a drill-down (ls / show /
        board) to act on, so it can't substitute for reading a slice. The counts
        are situational awareness, NOT a target to drive to zero."""
        with _core() as c:
            return _wrap(c, c.summary(), short_alert=True)

    @mcp.tool(structured_output=False)
    def stale() -> str:
        """The reconciliation worklist (D11): all stale open/spec tasks, as short
        rows with no bodies (D211); `show` opens one. Empty == done."""
        with _core() as c:
            tasks = []
            for t in c.stale_worklist():
                tasks.append(project_task(t, include_description=False))
            return _wrap(c, tasks, short_alert=True)

    @mcp.tool(structured_output=False)
    def labels() -> str:
        """List every label with its usage -- count + a few example task titles, so
        a label's meaning is clear from its tasks (D21). RUN THIS BEFORE creating a
        new label: reuse an existing one if it fits, to avoid label sprawl."""
        with _core() as c:
            out = []
            for i in c.labels_summary():
                out.append(i.model_dump(mode="json"))
            return _wrap(c, out, short_alert=True)

    @mcp.tool(structured_output=False)
    def render(label: str) -> str:
        """Render the tasks under a label into one markdown narrative (D16)."""
        with _core() as c:
            return _wrap(c, c.render(label), short_alert=True)

    @mcp.tool(structured_output=False)
    def history(id: int) -> str:
        """The full append-only history of a task (D8 + D29 v0.4): status
        transitions and description revisions, both in chronological order.
        Description revisions preserve verbatim prior name/description
        plus the delta rationale, so archaeology can recover what the task
        used to say -- the record for terminal (closed/wont_do) rows, which
        D259 freezes against edits (reopen a closed task to change it)."""
        with _core() as c:
            return _wrap(c, c.history(id).model_dump(mode="json"), short_alert=True)

    @mcp.tool(structured_output=False)
    def load(plan: str) -> str:
        """Bulk-import a plan (D24 + T94 + D40) given as TEXT: `[key] Name` lines
        with indented `kind:` (REQUIRED, one of design|schema|production|meta) /
        `desc:` / `labels:` / `depends_on:` (references batch keys OR existing
        tasks, T215). Creates all tasks in one atomic pass, resolving keys ->
        ids; a malformed line, missing/invalid kind, or unknown dep ref fails
        loud and rolls back the whole import (no partial plan). A production
        task with zero design/schema `depends_on` links is REFUSED and rolls
        back the whole plan (D256 creation-gate). Returns the key->id map.

        D276 -- a plan of design/schema + production tasks presumes SETTLED
        decisions; keep still-exploring work in a meta scratchpad instead.

        THIS is the path for importing many tasks at once -- prefer it over N
        separate add() calls. `desc:` may span multiple paragraphs: deeper-
        indented lines continue it and blank lines between them are preserved
        as paragraph breaks (D40), so impl-ready D37-grade bodies round-trip.
        `depends_on:` is a continuation block, one edge per line as
        `<ref> :: <because rationale>` (D33), where `<ref>` is a batch-local key
        OR an EXISTING task by prefixed-name (`S30`) or `#id` (T215); a
        prefixed-name's kind-letter is validated against the target.

        T243 -- a batch-local key is valid ONLY within THIS call and vanishes
        when it commits. To depend on a task created by a PRIOR load, use its
        now-persistent prefixed-name / `#id`, NOT that load's old batch key (an
        ephemeral ref in a durable edge; see SKILL 'Wire links explicitly')."""
        with _core() as c:
            keymap = c.load(parse_plan(plan))
            return _wrap(c, {"loaded": keymap})

    @mcp.tool(structured_output=False)
    def board(
        status: str | None = None,
        label: str | None = None,
        stale: bool = False,
        kind: str | None = None,
        name_prefix: str | None = None,
        include_description: bool = False,
        include_neighbor_because: bool = False,
        order_by: str | None = None,
    ) -> str:
        """Dependency-aware board (D22 + D36 + D211 + T220 + D283): the filtered
        tasks, each as a slice (task + links + labels, the symmetric neighbour set), so you see
        the whole graph's structure in ONE call (richer than `ls`). LEAN by
        default: NO task `description` and NO neighbor `because`/`last_edit_delta`
        -- just the graph SHAPE (ids/prefixed_names/status/stale/kind). Opt in per
        axis: include_description (full bodies), include_neighbor_because (edge
        rationales). Filters: status, label, stale, kind, name_prefix (T220:
        literal case-sensitive name prefix, e.g. '§9.1', matching the bare name
        not the synthesized prefixed_name). `order_by` (D283) sorts by a
        structured column (id/kind/status/created_at/updated_at/stale/degree),
        never body content; degree surfaces hubs first."""
        with _core() as c:
            stale_filter = True if stale else None
            cards = []
            for t in c.ls(
                status=status, label=label, stale=stale_filter,
                kind=kind, name_prefix=name_prefix, order_by=order_by,
            ):
                cards.append(project_slice(
                    c.show(t.id),
                    include_description=include_description,
                    include_neighbor_because=include_neighbor_because,
                ))
            return _wrap(c, cards, short_alert=True)

    _assert_param_gate(mcp)  # T236: fail loud if the strict-param gate didn't take
    return mcp


def run() -> None:
    """Entry point for ``tackit mcp`` - serve over stdio (a local subprocess)."""
    build_server().run()


if __name__ == "__main__":
    run()
