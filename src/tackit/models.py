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

from pydantic import BaseModel, ConfigDict, Field, field_validator

# design.md D7 / schema.md S1: status is informational, open|closed only.
Status = Literal["open", "closed"]


class Task(BaseModel):
    """S1 `tasks` row. The atomic item; every view is derived from it.

    Invariant (D7/D14): ``stale=True`` implies ``status="open"``. Enforced in
    core logic and re-checked here so a malformed row can never round-trip.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str = Field(min_length=1)  # S1: NOT NULL, short title
    description: str = ""
    status: Status = "open"
    stale: bool = False
    created_at: datetime
    updated_at: datetime

    @field_validator("stale")
    @classmethod
    def _stale_implies_open(cls, v: bool, info):  # D7 invariant: stale => open
        if v and info.data.get("status") == "closed":
            raise ValueError("invariant violated: stale=True requires status='open' (D7)")
        return v


class NeighborRef(BaseModel):
    """A directly-linked task as it appears in a slice/obligation payload -- a
    small summary (id + name + status + stale), not the full row. Keeps the
    slice small (design.md D9: "a step-sized slice")."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    status: Status
    stale: bool


class Slice(BaseModel):
    """D9 - slice fetch payload: one task plus its directly-linked context
    (dependencies it points at, dependents that point at it, and its labels).
    The core anti-context-bloat unit of access."""

    model_config = ConfigDict(extra="forbid")

    task: Task
    labels: list[str]
    dependencies: list[NeighborRef]  # D6: what this task points at (prerequisites)
    dependents: list[NeighborRef]  # D6: what points at this task


class CloseResult(BaseModel):
    """D12 - close obligation payload. Closing returns the one-hop set the agent
    is obliged to review, riding in the operation's own result."""

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


class SearchHit(BaseModel):
    """D17 - one ranked FTS5 result: task id + title + relevance score."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    score: float
