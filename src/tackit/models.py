"""D2 - Typed validation boundary.

design.md D2: "Every read and write passes through Pydantic models. Malformed
data ... is rejected at the boundary as a loud error, never stored."

These models are also the single source the MCP tool schema is generated from
(design.md "Interface - MCP": schema auto-generated from the type hints / Pydantic
models, so it cannot drift). Field shapes mirror schema.md S1-S4 exactly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

# design.md D7 / schema.md S1: status is informational. T132 added 'wont_do' as
# a third terminal status distinct from 'closed' -- 'closed' = work done;
# 'wont_do' = decided not to do, locked forever per T118 semantics.
#
# v0.5 (D35 + D36): two more statuses added for design/schema slices. 'spec' is
# the living-decision status (perma-living until retired or edited); 'retired' is
# the terminal status for fully-abandoned decisions (no replacement). The
# kind/status partition (enforced by Task.partition_validator below) maps
# production/meta to {open, closed, wont_do} and design/schema to {spec, retired}.
Status = Literal["open", "closed", "wont_do", "spec", "retired"]

# Frozen sets for the partition validator (D36) and any caller that needs to
# test partition membership. Kept module-level so add(), reclassify(), and the
# Pydantic validator share one definition.
_PROD_META_STATUSES = frozenset({"open", "closed", "wont_do"})
_DESIGN_SCHEMA_STATUSES = frozenset({"spec", "retired"})
_PROD_META_KINDS = frozenset({"production", "meta"})
_DESIGN_SCHEMA_KINDS = frozenset({"design", "schema"})


def default_status_for_kind(kind: str) -> str:
    """D35 + D36 — return the partition-valid default status for ``kind``:
    'spec' for design/schema; 'open' for production/meta. Single source of
    truth used by Core.add() and any caller that needs to insert a partition-
    valid row without explicitly passing status."""
    if kind in _DESIGN_SCHEMA_KINDS:
        return "spec"
    return "open"

# design.md D26 / schema.md S1: kind partitions the graph by "alters running app
# behavior." Required at create (T94); the default here lets existing call sites
# omit it until T94 lands and to preserve the DB-level safety net for any caller
# that slips past the op-layer check.
Kind = Literal["design", "schema", "production", "meta"]

# D32 (v0.4) - kind -> letter map for the synthesized auto-id name prefix.
# Every agent-facing display of a task renders as `<letter><id> — <name>` so
# the row's identifier is self-describing and code/conversation/FTS share one
# vocabulary. The prefix is COMPUTED from kind+id, never stored: the stored
# `name` column stays bare ("Fix ls() ..."), the prefix is appended at the
# display boundary (computed_field below) and at the FTS-index boundary
# (schema.py triggers). Legacy D#/S# task names from before D32 are
# grandfathered (per user 2026-06-01, T160) -- the synthesized prefix layers
# on top, producing e.g. "D133 — D7 — Status + stale flag" until those rows
# are someday renamed.
_KIND_LETTERS: dict[str, str] = {
    "design": "D",
    "schema": "S",
    "production": "T",
    "meta": "M",
}


def kind_letter(kind: str) -> str:
    """D32 - return the single-letter prefix for a kind. Single source of truth
    used by both Task.prefixed_name and the FTS triggers (the SQL CASE in
    schema.py must agree with this map)."""
    return _KIND_LETTERS[kind]


def synthesize_prefixed_name(kind: str, task_id: int, name: str) -> str:
    """D32 - the canonical display form of a task: `<letter><id> — <name>`.
    Shared between the model's computed_field and any non-Task call site that
    needs the same string (search hits, neighbor refs, render output)."""
    return f"{kind_letter(kind)}{task_id} — {name}"


def prefixed_id(kind: str, task_id: int) -> str:
    """D32 + T162 - the short id-only prefixed form: `<kind_letter><id>`.
    Used in agent-facing messages (stale_alert, refusals, error envelopes)
    where the full `prefixed_name` (with task name appended) would be too
    verbose. Single source; every message-construction site references this
    rather than hardcoding `T<id>` (which is kind-blind and breaks the D32
    convention for design/schema/meta tasks)."""
    return f"{kind_letter(kind)}{task_id}"


class Task(BaseModel):
    """S1 `tasks` row. The atomic item; every view is derived from it.

    T123 (2026-06-01): the v0.2.0 invariant ``stale=True => status='open'`` is
    retired. Cascade-staling no longer force-opens closed neighbors -- a closed
    task can carry ``stale=True`` to signal "an upstream changed; review for
    link migration" while remaining closed. v0.4 (D28): closed/wont_do stale is
    record only -- not on the worklist, not blocking close-gates.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str = Field(min_length=1)  # S1: NOT NULL, short title
    description: str = ""
    kind: Kind = "production"
    status: Status = "open"
    stale: bool = False
    wont_do_reason: Optional[str] = None  # T132: non-null iff status='wont_do'
                                          # v0.5 (D36): also non-null iff
                                          # status='retired' — partition rule
                                          # guarantees one terminal verb per row.
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _check_kind_status_partition(self) -> "Task":
        """D36 (v0.5) — enforce the kind/status partition at the D2 boundary.
        Refuses cross-partition combinations: production/meta must hold
        open/closed/wont_do; design/schema must hold spec/retired. The SQL
        CHECK constraint on S1 is the DB-layer backstop; this validator
        catches the violation earlier with a clearer error message."""
        if self.kind in _PROD_META_KINDS:
            if self.status not in _PROD_META_STATUSES:
                raise ValueError(
                    f"task kind/status partition violation: kind='{self.kind}' "
                    f"requires status in {{open, closed, wont_do}} "
                    f"(production/meta) or {{spec, retired}} (design/schema); "
                    f"got status='{self.status}'."
                )
        elif self.kind in _DESIGN_SCHEMA_KINDS:
            if self.status not in _DESIGN_SCHEMA_STATUSES:
                raise ValueError(
                    f"task kind/status partition violation: kind='{self.kind}' "
                    f"requires status in {{open, closed, wont_do}} "
                    f"(production/meta) or {{spec, retired}} (design/schema); "
                    f"got status='{self.status}'."
                )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def prefixed_name(self) -> str:
        """D32 - canonical agent-facing identifier: `<kind_letter><id> — <name>`.
        Computed from kind+id+name; not stored. See models.py.kind_letter."""
        return synthesize_prefixed_name(self.kind, self.id, self.name)


class NeighborRef(BaseModel):
    """A directly-linked task as it appears in a slice/obligation payload -- a
    small summary (id + name + status + stale + kind), not the full row. Keeps
    the slice small (design.md D9: "a step-sized slice"). `kind` (D32, v0.4)
    is carried so the neighbor render can synthesize its auto-id prefix
    (`prefixed_name`) without a second lookup.

    D34 / T166 (v0.4): in slice contexts where there is a specific edge from
    the focal task to this neighbor (show, board, close/wont_do payloads),
    the `because` (link's coupling rationale, T116) and `last_edit_delta`
    (neighbor's most recent edit delta from S7, T117/D29) are populated so
    the agent can compare delta x because to orient reconciliation of a
    stale neighbor without re-reading the dependent's full description.
    Both are None in non-slice contexts like the `links()` op output where
    a candidate isn't tied to a single edge from the input ids' perspective."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    status: Status
    stale: bool
    kind: Kind = "production"  # D32: defaulted for backward compat at the model
                               # boundary; populated at every construction site.
    because: Optional[str] = None  # D34/T166: link's coupling rationale; None in
                                    # non-edge contexts (links() op candidates).
    last_edit_delta: Optional[str] = None  # D34/T166: this neighbor's most-recent
                                            # edit delta from S7; None if never
                                            # edited or in non-edge contexts.

    @computed_field  # type: ignore[prop-decorator]
    @property
    def prefixed_name(self) -> str:
        """D32 - canonical display form for this neighbor reference."""
        return synthesize_prefixed_name(self.kind, self.id, self.name)


class Slice(BaseModel):
    """D9 - slice fetch payload: one task plus its directly-linked context
    (dependencies it points at, dependents that point at it, and its labels).
    The core anti-context-bloat unit of access.

    D34 / T166 (v0.4): when at least one dep entry has stale=True, the
    `because_reminder` field carries the FAST-filter discipline reminder
    pointing the agent at the per-entry `because` + `last_edit_delta` they
    should use to orient reconciliation. None when no dep entry is stale."""

    model_config = ConfigDict(extra="forbid")

    task: Task
    labels: list[str]
    dependencies: list[NeighborRef]  # D6: what this task points at (prerequisites)
    dependents: list[NeighborRef]  # D6: what points at this task
    because_reminder: Optional[str] = None  # D34/T166


class CloseResult(BaseModel):
    """D12 - close obligation payload. Closing returns the one-hop set the agent
    is obliged to review, riding in the operation's own result."""

    model_config = ConfigDict(extra="forbid")

    task: Task
    dependencies: list[NeighborRef]
    dependents: list[NeighborRef]


class WontDoResult(BaseModel):
    """T132 / 2026-06-01 - wont_do obligation payload. Mirrors CloseResult
    shape (task + one-hop neighbors) since both are terminal-status verbs
    returning the same review obligation. wont_do does NOT fire the staling
    cascade (status change, not content edit; symmetric with close)."""

    model_config = ConfigDict(extra="forbid")

    task: Task
    dependencies: list[NeighborRef]
    dependents: list[NeighborRef]


class ChangeResult(BaseModel):
    """D13 - change cascade-entry payload. Editing a task that others depend on
    marks those dependents stale+open (D10) and returns the now-stale set."""

    model_config = ConfigDict(extra="forbid")

    task: Task
    newly_stale: list[NeighborRef]  # direct dependents just marked stale


class StatusTransition(BaseModel):
    """S4 `status_transitions` row (D8). Append-only history of status changes."""

    model_config = ConfigDict(extra="forbid")

    id: int
    task_id: int
    from_status: Optional[Status]  # NULL allowed on first transition
    to_status: Status
    changed_at: datetime


class DescriptionRevision(BaseModel):
    """S7 `description_revisions` row (D29, v0.4). One append-only entry per
    successful edit() that actually changed name or description (no-op edits
    skipped per D20). Records the VERBATIM prior name and description, plus
    the agent's delta rationale, so archaeology can recover what the task
    used to say. Backstop for v0.4 edit-on-closed: edits no longer destroy
    history under the same task id."""

    model_config = ConfigDict(extra="forbid")

    id: int
    task_id: int
    prev_name: str
    prev_description: str
    delta: str
    edited_at: datetime


class History(BaseModel):
    """D8 + D29 (v0.4): full history payload for one task. Status transitions
    and description revisions live in separate append-only logs; History
    returns both in chronological order so callers can reconstruct the
    task's life. Replaces the v0.3 list[StatusTransition] return shape of
    core.history()."""

    model_config = ConfigDict(extra="forbid")

    status_transitions: list[StatusTransition]
    description_revisions: list[DescriptionRevision]


class SearchHit(BaseModel):
    """D17 + D28 (v0.4) + D32 - one ranked FTS5 result. ``status`` lets the
    adapter visually distinguish live work (open) from historical record
    (closed/wont_do) without opening each hit. ``wont_do_reason`` is set only
    for wont_do hits, so search results carry their dropped-scope rationale
    inline. ``kind`` (D32) lets the hit render its auto-id prefix without a
    follow-up show()."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    score: float
    status: Status
    wont_do_reason: Optional[str] = None
    kind: Kind = "production"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def prefixed_name(self) -> str:
        """D32 - canonical display form for this search hit."""
        return synthesize_prefixed_name(self.kind, self.id, self.name)


class LabelUsage(BaseModel):
    """D21 - one label with its usage: how many tasks wear it + a few example task
    names. Labels are self-documenting through their tasks (no description field;
    meaning is derived from usage, so it can't drift)."""

    model_config = ConfigDict(extra="forbid")

    label: str
    count: int
    samples: list[str]  # a few example task names, to disambiguate the label's meaning


# --- D211: lean read-projection for ls/board ------------------------------
# Shared by the MCP and CLI adapters so the two cannot drift (D211 decision A:
# identical projection on every surface). NEVER truncates -- a field is
# included or omitted, never half-shown; show() is the full-body path.


def project_task(task: "Task", *, include_description: bool) -> dict:
    """D211: lean projection of a Task for ls/board. Drops `description` unless
    asked. Keeps every scalar + the computed `prefixed_name`."""
    exclude = None if include_description else {"description"}
    return task.model_dump(mode="json", exclude=exclude)


def project_slice(
    slice_: "Slice", *, include_description: bool, include_neighbor_because: bool
) -> dict:
    """D211: lean projection of a board Slice. By default drops the task's
    `description` AND each neighbor's `because` + `last_edit_delta` (keeping the
    graph SHAPE: id/prefixed_name/status/stale/kind). Each is opt-in
    independently. Never truncates."""
    exclude: dict = {}
    if not include_description:
        exclude["task"] = {"description"}
    if not include_neighbor_because:
        nbr = {"__all__": {"because", "last_edit_delta"}}
        exclude["dependencies"] = nbr
        exclude["dependents"] = nbr
    return slice_.model_dump(mode="json", exclude=exclude or None)
