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
    LabelUsage,
    NeighborRef,
    SearchHit,
    Slice,
    StatusTransition,
    Task,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_text(value: str | None, field: str) -> None:
    """D2 fail-loud: refuse text that cannot be stored AND round-tripped. Two cases
    the property-based test surfaced, both rejected loudly at the boundary so the
    store stays storable and rebuildable:

      * a **NUL byte** (``\\x00``) survives into the SQLite TEXT value but breaks the
        D18 serialization -- ``sqlite3.executescript`` raises on an embedded NUL when
        rebuilding tackit.db from tackit.sql (fresh clone / import / pull);
      * an **unpaired UTF-16 surrogate** (``\\ud800``-``\\udfff``) is not valid UTF-8,
        so SQLite cannot even encode it on insert (UnicodeEncodeError).
    """
    if value is None:
        return
    if "\x00" in value:
        raise ValidationError(
            f"{field} contains a NUL byte (\\x00), which is not allowed because it "
            f"breaks the tackit.sql serialization (D2/D18)."
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValidationError(
            f"{field} contains characters that are not valid UTF-8 (e.g. an unpaired "
            f"surrogate) and cannot be stored (D2/D18)."
        ) from exc


# --- D19: built-in stale obligation surfacing (design.md "Enforcement" tier 2) ---
# The stale check is CODE IN THE APP, not advice: every invocation surfaces the
# outstanding stale set through both adapters (CLI stderr, MCP result envelope),
# deterministically, before the requested op and again after. These two helpers are
# the single source of the warning's wording, so it reads identically everywhere.

def stale_alert_text(stale_tasks: list[Task]) -> str:
    """The strongly-worded stale-obligation banner; empty string when nothing is
    stale. Names the tasks, the required action (review each AGAINST its depends_on
    neighbors, then edit-or-reconcile), and the negative fallout of ignoring it."""
    if not stale_tasks:
        return ""
    ids = []
    for t in stale_tasks:
        ids.append(f"T{t.id}")
    id_list = ", ".join(ids)
    n = len(stale_tasks)
    if n == 1:
        noun = "task is"
    else:
        noun = "tasks are"
    return (
        f"⚠ STALE TASKS OUTSTANDING — {n} {noun} unreconciled: {id_list}.\n"
        f"Each was marked stale because something it depends on changed and it has "
        f"NOT been re-verified. For each: read it together with its depends_on "
        f"neighbors (`show <id>`), then `edit` it if it is now wrong (which re-stales "
        f"its own dependents) or `reconcile` it if it is still correct. Until this "
        f"list is empty the plan is KNOWN to be internally inconsistent, and a task "
        f"left closed on top of an unreconciled dependency is WRONG AND INVISIBLE — "
        f"the exact failure tackit exists to prevent. Do not treat any work as done "
        f"while this list is non-empty. (Full worklist: `stale`.)"
    )


def stale_alert_payload(stale_tasks: list[Task]) -> dict | None:
    """Structured form of the stale alert for the MCP result envelope; None when
    nothing is stale. Carries the same wording as :func:`stale_alert_text`."""
    if not stale_tasks:
        return None
    ids = []
    for t in stale_tasks:
        ids.append(t.id)
    return {
        "count": len(stale_tasks),
        "stale_task_ids": ids,
        "message": stale_alert_text(stale_tasks),
    }


# --- D23: label-discipline creation nudge ------------------------------------
def label_nudge_text(created: list[str], existing: list[str]) -> str | None:
    """Anti-sprawl nudge, surfaced when a brand-new label is created: name it and list
    the labels that already exist, so reuse-before-create is in the agent's face (not
    only in the skill). None when nothing new was created (label-discipline)."""
    if not created:
        return None
    new = ", ".join(created)
    if not existing:
        return f"🏷 New label created: {new} (the first label in this store)."
    ex = ", ".join(existing)
    return (
        f"🏷 New label created: {new}. {len(existing)} label(s) already exist: {ex}. "
        f"If one of those fits, prefer it — a label should earn its name (a phase, "
        f"epic, or use case), not multiply. Run `labels` to see their usage."
    )


class Core:
    """The operation surface. Open via :meth:`open` for normal use (runs the D18
    startup sync first); construct directly only in tests with a ready store."""

    def __init__(self, store: Store, conn: sqlite3.Connection):
        self.store = store
        self.conn = conn
        # D23 label-discipline nudge: set when an op creates a brand-new label, so the
        # adapters can surface the anti-sprawl nudge (CLI stderr / MCP envelope).
        # Lives for the single op's lifetime (each command/tool call is a fresh Core).
        self.last_label_nudge: str | None = None

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
        _validate_text(name, "task name")
        _validate_text(description, "task description")
        self.last_label_nudge = None  # D23: reflect only this op
        new_labels = self._new_labels(labels or [])  # D23: detect before they exist
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
        if new_labels:
            self._set_label_nudge(new_labels)  # D23 anti-sprawl nudge
        return self.get(task_id)

    def load(self, specs: list[dict]) -> dict[str, int]:
        """D24 - bulk-create tasks from parsed plan specs (see :mod:`tackit.plan`) in
        ONE transaction, resolving ``depends_on`` by key. Returns ``{key: task_id}``.
        Atomic: any error (bad name, unknown dep key, self-edge, cycle) rolls the
        whole import back -- never a partial plan -- and is a single version bump."""
        keys: set[str] = set()
        for s in specs:
            keys.add(s["key"])
        # Validate all depends_on resolve within the plan BEFORE mutating (fail loud).
        for s in specs:
            for dep in s["depends_on"]:
                if dep not in keys:
                    raise ValidationError(
                        f"task '{s['key']}' depends_on unknown key '{dep}' "
                        f"(not defined in this plan)."
                    )
        keymap: dict[str, int] = {}
        with self._mutate():
            for s in specs:  # pass 1: tasks + labels
                if not s["name"] or not s["name"].strip():
                    raise ValidationError(f"task '{s['key']}' has an empty name.")
                _validate_text(s["name"], "task name")
                _validate_text(s["desc"], "task description")
                ts = _now()
                cur = self.conn.execute(
                    "INSERT INTO tasks(name, description, status, stale, created_at, updated_at) "
                    "VALUES (?, ?, 'open', 0, ?, ?)",
                    (s["name"].strip(), s["desc"], ts, ts),
                )
                tid = int(cur.lastrowid)
                self._record_transition(tid, None, "open")
                keymap[s["key"]] = tid
                for label in s["labels"]:
                    self._attach_label(tid, label)
            for s in specs:  # pass 2: edges (all keys now have ids)
                frm = keymap[s["key"]]
                for dep in s["depends_on"]:
                    self._add_edge(frm, keymap[dep])
        return keymap

    def get(self, task_id: int) -> Task:
        """D3 - read a task back."""
        return self._task_from_row(self._require_row(task_id))

    # ====================================================================
    # D4 - Labels (dumb freeform tags)
    # ====================================================================
    def _attach_label(self, task_id: int, label: str) -> None:
        if not label or not label.strip():
            raise ValidationError("label must be a non-empty string (D4/S2).")
        _validate_text(label, "label")
        self.conn.execute(
            "INSERT OR IGNORE INTO task_labels(task_id, label) VALUES (?, ?)",
            (task_id, label.strip()),
        )

    def label_add(self, task_id: int, label: str) -> Task:
        """D4 - tag a task. (Pure tagging is not a content change, so it does NOT
        stale dependents -- D10 fires only on edits that can invalidate them.)
        Creating a brand-new label sets the D23 anti-sprawl nudge."""
        self._require_row(task_id)
        if not label or not label.strip():
            raise ValidationError("label must be a non-empty string (D4/S2).")
        _validate_text(label, "label")
        self.last_label_nudge = None  # D23: reflect only this op
        clean = label.strip()
        new_labels = self._new_labels([clean])  # D23: [clean] iff brand-new, else []
        # No-op guard (D20): re-adding a label the task already carries changes nothing,
        # so it must not bump version / re-dump tackit.sql.
        on_this_task = self.conn.execute(
            "SELECT 1 FROM task_labels WHERE task_id = ? AND label = ?", (task_id, clean)
        ).fetchone()
        if on_this_task is None:
            with self._mutate():
                self._attach_label(task_id, clean)
        if new_labels:
            self._set_label_nudge(new_labels)  # D23 anti-sprawl nudge
        return self.get(task_id)

    def label_rm(self, task_id: int, label: str) -> Task:
        """D4 - untag a task."""
        self._require_row(task_id)
        clean = label.strip() if label else ""
        # No-op guard (D20): removing a label the task doesn't carry changes nothing.
        existing = self.conn.execute(
            "SELECT 1 FROM task_labels WHERE task_id = ? AND label = ?", (task_id, clean)
        ).fetchone()
        if existing is not None:
            with self._mutate():
                self.conn.execute(
                    "DELETE FROM task_labels WHERE task_id = ? AND label = ?",
                    (task_id, clean),
                )
        return self.get(task_id)

    def _new_labels(self, labels: list[str]) -> list[str]:
        """D23 - of ``labels``, the cleaned ones that don't yet exist on ANY task
        (i.e. are about to be created). De-duplicated, order-preserving."""
        out: list[str] = []
        for lab in labels:
            clean = lab.strip() if lab else ""
            if not clean or clean in out:
                continue
            exists = self.conn.execute(
                "SELECT 1 FROM task_labels WHERE label = ?", (clean,)
            ).fetchone()
            if exists is None:
                out.append(clean)
        return out

    def _set_label_nudge(self, created: list[str]) -> None:
        """D23 - record the anti-sprawl nudge for ``created`` new labels, listing the
        labels that already exist (everything except the just-created ones)."""
        existing: list[str] = []
        for usage in self.labels_summary():
            if usage.label not in created:
                existing.append(usage.label)
        self.last_label_nudge = label_nudge_text(created, existing)

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

    def _stale_upstream(self, task_id: int) -> list[int]:
        """All tasks ``task_id`` transitively depends_on that are currently stale
        (excluding itself), id-sorted. Underpins the dependency-aware close-gate
        (D14 extended): closing a task that sits on unreconciled upstream drift is
        refused, since that drift may still change and re-invalidate it."""
        seen: set[int] = set()
        stack: list[int] = []
        rows = self.conn.execute(
            "SELECT to_task FROM dependencies WHERE from_task = ?", (task_id,)
        ).fetchall()
        for r in rows:
            stack.append(r["to_task"])
        stale_found: list[int] = []
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            row = self.conn.execute(
                "SELECT stale FROM tasks WHERE id = ?", (node,)
            ).fetchone()
            if row is not None and bool(row["stale"]):
                stale_found.append(node)
            nxt = self.conn.execute(
                "SELECT to_task FROM dependencies WHERE from_task = ?", (node,)
            ).fetchall()
            for r in nxt:
                stack.append(r["to_task"])
        stale_found.sort()
        return stale_found

    def dep_add(self, from_task: int, to_task: int) -> Slice:
        """D5 - declare ``from_task depends_on to_task``; return from_task's slice."""
        # No-op guard (D20): an edge that already exists is idempotent, so it must not
        # bump version. (A new edge still runs the full D14 validation in
        # ``_add_edge``: self-edge, FK, and cycle checks.)
        existing = self.conn.execute(
            "SELECT 1 FROM dependencies WHERE from_task = ? AND to_task = ?",
            (from_task, to_task),
        ).fetchone()
        if existing is None:
            with self._mutate():
                self._add_edge(from_task, to_task)
        return self.show(from_task)

    def dep_rm(self, from_task: int, to_task: int) -> Slice:
        """D5 - remove the edge ``from_task depends_on to_task``."""
        self._require_row(from_task)
        self._require_row(to_task)
        # No-op guard (D20): removing an edge that isn't there changes nothing.
        existing = self.conn.execute(
            "SELECT 1 FROM dependencies WHERE from_task = ? AND to_task = ?",
            (from_task, to_task),
        ).fetchone()
        if existing is not None:
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
        row = self._require_row(task_id)
        # No-op guard (D20): reopening an already-open task changes nothing, so it must
        # not bump version / re-dump tackit.sql (which would be spurious git churn
        # and a false "newer" signal for D18 ordering).
        if row["status"] != "open":
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
        row = self._require_row(task_id)
        # No-op guard (D20): reconciling a task that isn't stale changes nothing.
        if bool(row["stale"]):
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
        # Dependency-aware close-gate (D14 extended): even if T itself is not stale,
        # refuse to mark it done while anything it transitively depends_on is stale
        # -- that upstream drift is unreconciled and may still change under T.
        upstream = self._stale_upstream(task_id)
        if upstream:
            labels = []
            for uid in upstream:
                labels.append(f"T{uid}")
            id_list = ", ".join(labels)
            raise InvariantError(
                f"REFUSED: T{task_id} transitively depends on unreconciled stale "
                f"task(s) {id_list} -- closing it would mark work done on top of "
                f"drift that may still change. Reconcile {id_list} first, then "
                f"close (D14)."
            )
        # No-op guard (D20): closing an already-closed task changes nothing -- still
        # return the obligation payload (deps/dependents), but don't bump version
        # or re-dump tackit.sql. (A closed task is never stale, so the gate above
        # always passes on this path.)
        if row["status"] != "closed":
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
        row = self._require_row(task_id)
        if name is not None and not name.strip():
            raise ValidationError("task name cannot be set empty (D3/S1).")
        _validate_text(name, "task name")
        _validate_text(description, "task description")
        # No-op guard (D20): D10 staling and the version bump must fire ONLY on a
        # real content change. "Actual change" = a provided field differs from
        # what is already stored -- a field-level comparison against the current
        # row (no diff machinery needed; the row is already in hand). A bare
        # `edit ID` with no differing field stales nothing and bumps nothing.
        new_name = name.strip() if name is not None else None
        name_changes = new_name is not None and new_name != row["name"]
        desc_changes = description is not None and description != row["description"]
        if not name_changes and not desc_changes:
            return ChangeResult(task=self.get(task_id), newly_stale=[])
        with self._mutate():
            newly_stale = self._mark_dependents_stale(task_id)  # D10, before change
            sets, params = [], []
            if name_changes:
                sets.append("name = ?")
                params.append(new_name)
            if desc_changes:
                sets.append("description = ?")
                params.append(description)
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

    # ====================================================================
    # D21 - label usage view (label-discipline)
    # ====================================================================
    def labels_summary(self, samples: int = 3) -> list[LabelUsage]:
        """D21 - every label with its usage: task count + a few example task names,
        so a label is self-documenting through its tasks (its meaning is DERIVED
        from usage -- there is no description column to drift). Ordered most-used
        first. This is the 'what labels exist and what do they mean' primitive that
        makes reuse-before-create possible (label-discipline)."""
        rows = self.conn.execute(
            "SELECT label, COUNT(*) AS n FROM task_labels "
            "GROUP BY label ORDER BY n DESC, label"
        ).fetchall()
        out: list[LabelUsage] = []
        for r in rows:
            sample_rows = self.conn.execute(
                "SELECT t.name FROM task_labels l JOIN tasks t ON t.id = l.task_id "
                "WHERE l.label = ? ORDER BY t.id LIMIT ?",
                (r["label"], samples),
            ).fetchall()
            names: list[str] = []
            for sr in sample_rows:
                names.append(sr["name"])
            out.append(LabelUsage(label=r["label"], count=r["n"], samples=names))
        return out
