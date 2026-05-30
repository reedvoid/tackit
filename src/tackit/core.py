"""Core operations - slices D3 through D17.

design.md "Interface" sections: MCP, CLI, and the skill are all **thin adapters**
over this one module. "No logic lives [in the adapters] -- determinism,
invariants, and obligations all stay in core, so neither door can bypass them."

Every public method is tagged with the design slice it implements; every mutating
op runs inside ``_mutate`` so the D18 version-bump + tackit.sql re-dump happens
atomically with the data change. Obligation payloads (D12/D13) are returned from
the ops, never left implicit.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import sync
from .db import Store, connect, require_store
from .errors import InvariantError, NotFoundError, ValidationError
from .models import (
    ChangeResult,
    CloseResult,
    NeighborRef,
    SearchHit,
    Slice,
    StatusTransition,
    Task,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Core:
    """The operation surface. Open via :meth:`open` for normal use (runs the D18
    startup sync first); construct directly only in tests with a ready store."""

    def __init__(self, store: Store, conn: sqlite3.Connection):
        self.store = store
        self.conn = conn

    @classmethod
    def open(cls, start: Path | None = None) -> "Core":
        """Resolve the store (D1 walk-up), run the D18 startup sync (build on a
        fresh clone, refuse on ambiguous divergence), then open the connection."""
        store = require_store(start)
        sync.startup_sync(store)  # may raise SyncError; may rebuild the db
        return cls(store, connect(store.db_path))

    def close_conn(self) -> None:
        self.conn.close()

    # --- transaction wrapper (D18 finalize on every mutation) ---------------
    @contextmanager
    def _mutate(self):
        """Wrap a mutating op: BEGIN, run it, finalize (bump version + re-dump
        tackit.sql), COMMIT. Roll back loudly on any error."""
        self.conn.execute("BEGIN")
        try:
            yield
            sync.finalize_mutation(self.conn, self.store)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    # --- row -> model helpers (D2 validation boundary) ----------------------
    def _task_from_row(self, row: sqlite3.Row) -> Task:
        # Pydantic re-validates on the way out: a malformed row fails loud.
        return Task(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            status=row["status"],
            stale=bool(row["stale"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _require_row(self, task_id: int) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"no task with id {task_id} (T{task_id}).")
        return row

    def _neighbor_from_row(self, row: sqlite3.Row) -> NeighborRef:
        return NeighborRef(
            id=row["id"], name=row["name"], status=row["status"], stale=bool(row["stale"])
        )

    def _record_transition(self, task_id: int, from_status, to_status: str) -> None:
        # D8 - append-only status history.
        self.conn.execute(
            "INSERT INTO status_transitions(task_id, from_status, to_status, changed_at) "
            "VALUES (?, ?, ?, ?)",
            (task_id, from_status, to_status, _now()),
        )

    # ====================================================================
    # D3 - Task create / read / update
    # ====================================================================
    def add(
        self,
        name: str,
        description: str = "",
        labels: list[str] | None = None,
        deps: list[int] | None = None,
    ) -> Task:
        """D3 - create a task (auto monotonic id). Optionally attach labels (D4)
        and declare dependencies (D5; the new task depends_on each id). Cycle and
        FK checks (D14) apply to the deps."""
        if not name or not name.strip():
            raise ValidationError("task name must be a non-empty string (D3/S1).")
        ts = _now()
        with self._mutate():
            cur = self.conn.execute(
                "INSERT INTO tasks(name, description, status, stale, created_at, updated_at) "
                "VALUES (?, ?, 'open', 0, ?, ?)",
                (name.strip(), description, ts, ts),
            )
            task_id = int(cur.lastrowid)
            self._record_transition(task_id, None, "open")  # D8: creation event
            for label in labels or []:
                self._attach_label(task_id, label)
            for dep in deps or []:
                self._add_edge(task_id, dep)  # new task depends_on dep
        return self.get(task_id)

    def get(self, task_id: int) -> Task:
        """D3 - read a task back."""
        return self._task_from_row(self._require_row(task_id))

    # ====================================================================
    # D4 - Labels (dumb freeform tags)
    # ====================================================================
    def _attach_label(self, task_id: int, label: str) -> None:
        if not label or not label.strip():
            raise ValidationError("label must be a non-empty string (D4/S2).")
        self.conn.execute(
            "INSERT OR IGNORE INTO task_labels(task_id, label) VALUES (?, ?)",
            (task_id, label.strip()),
        )

    def label_add(self, task_id: int, label: str) -> Task:
        """D4 - tag a task. (Pure tagging is not a content change, so it does NOT
        stale dependents -- D10 fires only on edits that can invalidate them.)"""
        self._require_row(task_id)
        with self._mutate():
            self._attach_label(task_id, label)
        return self.get(task_id)

    def label_rm(self, task_id: int, label: str) -> Task:
        """D4 - untag a task."""
        self._require_row(task_id)
        with self._mutate():
            self.conn.execute(
                "DELETE FROM task_labels WHERE task_id = ? AND label = ?",
                (task_id, label.strip()),
            )
        return self.get(task_id)

    def labels_of(self, task_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT label FROM task_labels WHERE task_id = ? ORDER BY label", (task_id,)
        ).fetchall()
        return [r["label"] for r in rows]

    # ====================================================================
    # D5 - Dependency edges / D6 - bidirectional traversal
    # ====================================================================
    def _add_edge(self, from_task: int, to_task: int) -> None:
        """D5 + D14 - add ``from_task depends_on to_task`` with invariant checks:
        both endpoints exist (FK), not self (CHECK), and no cycle (logic)."""
        if from_task == to_task:
            raise InvariantError(f"a task cannot depend on itself (T{from_task}).")
        self._require_row(from_task)
        self._require_row(to_task)
        # D14 acyclicity: adding from->to is a cycle iff `to` can already reach
        # `from` by following depends_on edges (i.e. `to` transitively depends on
        # `from`).
        if self._reaches(to_task, from_task):
            raise InvariantError(
                f"refusing edge T{from_task} depends_on T{to_task}: it would create "
                f"a dependency cycle (T{to_task} already depends on T{from_task})."
            )
        try:
            self.conn.execute(
                "INSERT INTO dependencies(from_task, to_task) VALUES (?, ?)",
                (from_task, to_task),
            )
        except sqlite3.IntegrityError:
            # UNIQUE(from_task,to_task): the edge already exists -- idempotent.
            pass

    def _reaches(self, start: int, target: int) -> bool:
        """Can ``start`` reach ``target`` by following depends_on edges
        (from_task -> to_task)? Iterative DFS; underpins cycle detection (D14)."""
        seen: set[int] = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node in seen:
                continue
            seen.add(node)
            rows = self.conn.execute(
                "SELECT to_task FROM dependencies WHERE from_task = ?", (node,)
            ).fetchall()
            for r in rows:
                stack.append(r["to_task"])
        return False

    def dep_add(self, from_task: int, to_task: int) -> Slice:
        """D5 - declare ``from_task depends_on to_task``; return from_task's slice."""
        with self._mutate():
            self._add_edge(from_task, to_task)
        return self.show(from_task)

    def dep_rm(self, from_task: int, to_task: int) -> Slice:
        """D5 - remove the edge ``from_task depends_on to_task``."""
        self._require_row(from_task)
        self._require_row(to_task)
        with self._mutate():
            self.conn.execute(
                "DELETE FROM dependencies WHERE from_task = ? AND to_task = ?",
                (from_task, to_task),
            )
        return self.show(from_task)

    def dependencies_of(self, task_id: int) -> list[NeighborRef]:
        """D6 - what ``task_id`` points at (its prerequisites)."""
        rows = self.conn.execute(
            "SELECT t.* FROM dependencies d JOIN tasks t ON t.id = d.to_task "
            "WHERE d.from_task = ? ORDER BY t.id",
            (task_id,),
        ).fetchall()
        return [self._neighbor_from_row(r) for r in rows]

    def dependents_of(self, task_id: int) -> list[NeighborRef]:
        """D6 - what points at ``task_id`` (the tasks that depend on it).
        Status-blind: a closed dependent is still returned."""
        rows = self.conn.execute(
            "SELECT t.* FROM dependencies d JOIN tasks t ON t.id = d.from_task "
            "WHERE d.to_task = ? ORDER BY t.id",
            (task_id,),
        ).fetchall()
        return [self._neighbor_from_row(r) for r in rows]

    # ====================================================================
    # D7 - status + stale / D8 - transition history / D10/D11 reconciliation
    # ====================================================================
    def _set_status(self, task_id: int, new_status: str, *, clear_stale: bool) -> None:
        row = self._require_row(task_id)
        old_status = row["status"]
        stale_clause = ", stale = 0" if clear_stale else ""
        self.conn.execute(
            f"UPDATE tasks SET status = ?{stale_clause}, updated_at = ? WHERE id = ?",
            (new_status, _now(), task_id),
        )
        if old_status != new_status:
            self._record_transition(task_id, old_status, new_status)  # D8

    def reopen(self, task_id: int) -> Task:
        """D7/D8 - move a closed task back to open (logged). Does not set stale;
        the history log keeps the earlier 'closed' fact."""
        with self._mutate():
            self._set_status(task_id, "open", clear_stale=False)
        return self.get(task_id)

    def _mark_dependents_stale(self, task_id: int) -> list[NeighborRef]:
        """D10 - mark the DIRECT dependents of ``task_id`` stale + open (invariant
        stale=>open, D7). One hop only; non-transitive. Recorded *before* the
        change so an interrupted reconciliation is crash-safe."""
        dependents = self.dependents_of(task_id)
        for dep in dependents:
            row = self._require_row(dep.id)
            if row["status"] == "closed":
                self._record_transition(dep.id, "closed", "open")  # forced open by stale
            self.conn.execute(
                "UPDATE tasks SET stale = 1, status = 'open', updated_at = ? WHERE id = ?",
                (_now(), dep.id),
            )
        # re-read so the returned refs show stale=True
        return [self._neighbor_from_row(self._require_row(d.id)) for d in dependents]

    def stale_worklist(self) -> list[Task]:
        """D11 - the resumable reconciliation worklist: all stale tasks, id order.
        Empty list == reconciliation pass complete (termination marker)."""
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE stale = 1 ORDER BY id"
        ).fetchall()
        return [self._task_from_row(r) for r in rows]

    def reconcile(self, task_id: int) -> Task:
        """D11 - clear ``stale`` on a task reviewed and found still-correct, with
        no content change (so it does NOT cascade to dependents)."""
        self._require_row(task_id)
        with self._mutate():
            self.conn.execute(
                "UPDATE tasks SET stale = 0, updated_at = ? WHERE id = ?",
                (_now(), task_id),
            )
        return self.get(task_id)

    # ====================================================================
    # D9 - slice fetch
    # ====================================================================
    def show(self, task_id: int) -> Slice:
        """D9 - one task plus its directly-linked context (deps, dependents,
        labels): the small, step-sized unit of access."""
        task = self.get(task_id)
        return Slice(
            task=task,
            labels=self.labels_of(task_id),
            dependencies=self.dependencies_of(task_id),
            dependents=self.dependents_of(task_id),
        )

    # ====================================================================
    # D12 - close (with obligation payload) / D14 close-gate
    # ====================================================================
    def close(self, task_id: int) -> CloseResult:
        """D12 + D14 - close a task and return the one-hop set to review. REFUSED
        while the task is stale (close-gate, ratified 2026-05-29): a task with
        unreconciled upstream changes cannot be marked done."""
        row = self._require_row(task_id)
        if bool(row["stale"]):
            raise InvariantError(
                f"REFUSED: T{task_id} is stale -- it has unreconciled upstream "
                f"changes. Reconcile (review + `reconcile`) first, then close (D14)."
            )
        with self._mutate():
            self._set_status(task_id, "closed", clear_stale=False)
        return CloseResult(
            task=self.get(task_id),
            dependencies=self.dependencies_of(task_id),
            dependents=self.dependents_of(task_id),
        )

    # ====================================================================
    # D13 - change (with cascade entry) -> D10
    # ====================================================================
    def edit(
        self, task_id: int, name: str | None = None, description: str | None = None
    ) -> ChangeResult:
        """D13 - edit a task: first mark its direct dependents stale+open (D10),
        then apply the edit, then return the now-stale set. Entry point of the
        change-time cascade (reconciling each may, if it too changes, stale its
        own dependents)."""
        self._require_row(task_id)
        if name is not None and not name.strip():
            raise ValidationError("task name cannot be set empty (D3/S1).")
        with self._mutate():
            newly_stale = self._mark_dependents_stale(task_id)  # D10, before change
            sets, params = [], []
            if name is not None:
                sets.append("name = ?")
                params.append(name.strip())
            if description is not None:
                sets.append("description = ?")
                params.append(description)
            if sets:
                sets.append("updated_at = ?")
                params.append(_now())
                params.append(task_id)
                self.conn.execute(
                    f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", tuple(params)
                )
        return ChangeResult(task=self.get(task_id), newly_stale=newly_stale)

    # ====================================================================
    # D15 - query / board (derived views)
    # ====================================================================
    def ls(
        self,
        status: str | None = None,
        label: str | None = None,
        stale: bool | None = None,
    ) -> list[Task]:
        """D15 - list/filter tasks by status, label, and/or stale. The work queue
        and any board are *queries over the fields*, not maintained lists."""
        if status is not None and status not in ("open", "closed"):
            raise ValidationError("status filter must be 'open' or 'closed' (D7).")
        clauses, params = [], []
        join = ""
        if label is not None:
            join = "JOIN task_labels l ON l.task_id = t.id"
            clauses.append("l.label = ?")
            params.append(label)
        if status is not None:
            clauses.append("t.status = ?")
            params.append(status)
        if stale is not None:
            clauses.append("t.stale = ?")
            params.append(1 if stale else 0)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.conn.execute(
            f"SELECT DISTINCT t.* FROM tasks t {join} {where} ORDER BY t.id", tuple(params)
        ).fetchall()
        return [self._task_from_row(r) for r in rows]

    # ====================================================================
    # D16 - narrative render
    # ====================================================================
    def render(self, label: str) -> str:
        """D16 - render the tasks carrying ``label`` (id order) into one readable
        markdown document, recovering a human narrative from the slices without
        that narrative becoming a second source of truth."""
        tasks = self.ls(label=label)
        out = [f"# tackit narrative - label `{label}`", ""]
        if not tasks:
            out.append(f"_No tasks carry the label `{label}`._")
            return "\n".join(out) + "\n"
        for t in tasks:
            flags = [t.status]
            if t.stale:
                flags.append("STALE")
            out.append(f"## T{t.id} - {t.name}  ({', '.join(flags)})")
            deps = self.dependencies_of(t.id)
            if deps:
                dep_str = ", ".join(f"T{d.id}" for d in deps)
                out.append(f"*depends on:* {dep_str}")
            labels = [lab for lab in self.labels_of(t.id) if lab != label]
            if labels:
                out.append(f"*labels:* {', '.join(labels)}")
            out.append("")
            out.append(t.description.strip() if t.description.strip() else "_(no description)_")
            out.append("")
        return "\n".join(out) + "\n"

    # ====================================================================
    # D17 - full-text search (FTS5)
    # ====================================================================
    def search(self, query: str, limit: int = 20) -> list[SearchHit]:
        """D17 - ranked keyword search over name+description via FTS5. Returns
        ids+titles+scores, best first. ``search -> show`` is tackit's retrieval
        loop. Score is -bm25 (higher = more relevant)."""
        if not query or not query.strip():
            raise ValidationError("search query must be non-empty (D17).")
        try:
            rows = self.conn.execute(
                "SELECT t.id AS id, t.name AS name, bm25(tasks_fts) AS bm25 "
                "FROM tasks_fts JOIN tasks t ON t.id = tasks_fts.rowid "
                "WHERE tasks_fts MATCH ? ORDER BY bm25 LIMIT ?",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            # malformed FTS5 query syntax -> fail loud at the boundary (D2)
            raise ValidationError(f"invalid search query (FTS5 syntax): {exc}") from exc
        return [SearchHit(id=r["id"], name=r["name"], score=-float(r["bm25"])) for r in rows]

    # ====================================================================
    # D8 - read status history
    # ====================================================================
    def history(self, task_id: int) -> list[StatusTransition]:
        """D8 - the append-only status-transition log for a task."""
        self._require_row(task_id)
        rows = self.conn.execute(
            "SELECT * FROM status_transitions WHERE task_id = ? ORDER BY id", (task_id,)
        ).fetchall()
        return [
            StatusTransition(
                id=r["id"],
                task_id=r["task_id"],
                from_status=r["from_status"],
                to_status=r["to_status"],
                changed_at=r["changed_at"],
            )
            for r in rows
        ]
