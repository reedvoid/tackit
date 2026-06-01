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
        """Resolve the store (D1 walk-up); run the D18 sync verdict BEFORE
        migrating (so a newer .sql gets pulled, a divergent state is refused);
        then open the conn, run any pending schema migrations (T83), and
        finally export if there is still no .sql. Migration must follow sync
        because the dump emitted by ``finalize_mutation`` uses the current
        schema -- emitting it over a pre-migration db would corrupt the file."""
        from . import migrations

        store = require_store(start)
        # D18 sync verdict. We call startup_sync only when at least one of the
        # two files exists; we also avoid the "no .sql" branch (which would
        # export a pre-migration db) by handling that case after migration
        # ourselves.
        if store.db_path.exists() and store.sql_path.exists():
            sync.startup_sync(store)  # may rebuild .db on Vsql > Vdb; may raise SyncError
        elif store.sql_path.exists():
            sync.rebuild_db_from_sql(store)  # fresh clone with only .sql
        # else: no .sql; will export after migration so the dump uses current schema

        conn = connect(store.db_path)
        migrations.run_pending_migrations(conn, store)

        # Post-migration: if we still have no .sql (db-only case), export now
        # over the just-migrated db so the file matches the current schema.
        if not store.sql_path.exists():
            sync.export(store)
        return cls(store, conn)

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
            kind=row["kind"],
            status=row["status"],
            stale=bool(row["stale"]),
            superseded_by=row["superseded_by"],
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
                self._add_link(task_id, dep)  # new task depends_on dep
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
        # D23: which labels the import will newly create (before they exist), for the
        # post-load anti-sprawl summary (T67 — bulk load is the one path the per-op
        # creation-nudge misses, and a migration is when sprawl floods in).
        batch_labels: list[str] = []
        for s in specs:
            for lab in s["labels"]:
                if lab not in batch_labels:
                    batch_labels.append(lab)
        new_labels = self._new_labels(batch_labels)
        self.last_label_nudge = None
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
                    self._add_link(frm, keymap[dep])
        if new_labels:  # T67: surface the new labels so the agent can collapse in one pass
            self.last_label_nudge = (
                f"🏷 Bulk load created {len(new_labels)} new label(s): "
                f"{', '.join(new_labels)}. Review (`labels`) and collapse near-duplicates "
                f"in ONE pass — a migration is when label sprawl floods in."
            )
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
        clean = label.strip()
        # D14 / D26: the four kind values are reserved label strings. The kind
        # column on S1 absorbs that distinction; a stray label of the same string
        # would silently disagree. Refused on every label-attach path -- add(),
        # label_add(), and load() all route through here.
        from .schema import RESERVED_LABELS

        if clean in RESERVED_LABELS:
            raise ValidationError(
                f"label {clean!r} is reserved for the kind property (S1/D26) and "
                f"cannot be attached as a label. Reserved: "
                f"{', '.join(RESERVED_LABELS)}."
            )
        self.conn.execute(
            "INSERT OR IGNORE INTO task_labels(task_id, label) VALUES (?, ?)",
            (task_id, clean),
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
    # D5 - Symmetric link / D6 - linked-tasks traversal (T86 / D5 / D6)
    # ====================================================================
    @staticmethod
    def _canonical(a: int, b: int) -> tuple[int, int]:
        """Canonical (lower, higher) order for the symmetric link pair (S3)."""
        return (a, b) if a < b else (b, a)

    def _add_link(self, a: int, b: int) -> None:
        """D5 + D14 - add a symmetric link between ``a`` and ``b``. Invariants:
        both endpoints exist (FK), distinct (CHECK task_a < task_b prevents
        self-link), and the **meta-island constraint** (D26 / T87): a link
        between a ``meta``-kind task and a non-``meta``-kind task is refused
        so the cascade cannot bleed across the kind boundary."""
        if a == b:
            raise InvariantError(f"a task cannot link to itself (T{a}).")
        row_a = self._require_row(a)
        row_b = self._require_row(b)
        # Meta-island constraint (D26 / T87): refuse cross-kind links between
        # meta and non-meta. Same-kind links are always allowed (meta<->meta,
        # production<->production, design<->schema, etc.).
        kind_a = row_a["kind"]
        kind_b = row_b["kind"]
        if (kind_a == "meta") != (kind_b == "meta"):
            raise InvariantError(
                f"REFUSED: meta-island constraint (D26). T{a} (kind={kind_a}) and "
                f"T{b} (kind={kind_b}) cannot be linked because exactly one is meta. "
                f"Meta tasks may only link other meta tasks; the boundary bounds the "
                f"cascade so meta work (release tracking, experiments) cannot drag "
                f"spec/production tasks into a stale review and vice versa."
            )
        ta, tb = self._canonical(a, b)
        try:
            self.conn.execute(
                "INSERT INTO links(task_a, task_b) VALUES (?, ?)", (ta, tb)
            )
        except sqlite3.IntegrityError:
            # UNIQUE(task_a, task_b): the link already exists -- idempotent.
            pass

    def _stale_linked_transitive(self, task_id: int) -> list[int]:
        """All tasks transitively linked to ``task_id`` (in the symmetric graph)
        that are currently stale, excluding ``task_id`` itself, id-sorted.
        Underpins the symmetric close-gate (D14): closing a task whose linked
        neighborhood is unreconciled is refused. Reach is bounded in practice by
        the meta-island constraint (D26)."""
        seen: set[int] = {task_id}
        stack: list[int] = [task_id]
        stale_found: list[int] = []
        while stack:
            node = stack.pop()
            rows = self.conn.execute(
                "SELECT CASE WHEN task_a = ? THEN task_b ELSE task_a END AS other "
                "FROM links WHERE task_a = ? OR task_b = ?",
                (node, node, node),
            ).fetchall()
            for r in rows:
                nxt = int(r["other"])
                if nxt in seen:
                    continue
                seen.add(nxt)
                row = self.conn.execute(
                    "SELECT stale FROM tasks WHERE id = ?", (nxt,)
                ).fetchone()
                if row is not None and bool(row["stale"]):
                    stale_found.append(nxt)
                stack.append(nxt)
        stale_found.sort()
        return stale_found

    def dep_add(self, from_task: int, to_task: int) -> Slice:
        """D5 - add a symmetric link between ``from_task`` and ``to_task``;
        return ``from_task``'s slice. Argument order is preserved in the slice
        result, but the stored row is canonicalized (T86); both orderings
        produce the same row. The public name will become `link_add` in T93/T96."""
        ta, tb = self._canonical(from_task, to_task)
        # No-op guard (D20): a link that already exists is idempotent. Look up
        # via the canonical pair so both argument orderings hit the same row.
        existing = self.conn.execute(
            "SELECT 1 FROM links WHERE task_a = ? AND task_b = ?", (ta, tb)
        ).fetchone()
        if existing is None:
            with self._mutate():
                self._add_link(from_task, to_task)
        return self.show(from_task)

    def dep_rm(self, from_task: int, to_task: int) -> Slice:
        """D5 - remove the symmetric link between ``from_task`` and ``to_task``."""
        self._require_row(from_task)
        self._require_row(to_task)
        ta, tb = self._canonical(from_task, to_task)
        existing = self.conn.execute(
            "SELECT 1 FROM links WHERE task_a = ? AND task_b = ?", (ta, tb)
        ).fetchone()
        if existing is not None:
            with self._mutate():
                self.conn.execute(
                    "DELETE FROM links WHERE task_a = ? AND task_b = ?", (ta, tb)
                )
        return self.show(from_task)

    def _linked_with(self, task_id: int) -> list[NeighborRef]:
        """D6 - every task that shares a link with ``task_id``, id-sorted. Single
        set under symmetric semantics: there is no "dependencies vs dependents"
        partition. Status-blind (closed neighbors still returned)."""
        rows = self.conn.execute(
            "SELECT t.* FROM links l "
            "JOIN tasks t ON t.id = CASE WHEN l.task_a = ? THEN l.task_b ELSE l.task_a END "
            "WHERE l.task_a = ? OR l.task_b = ? ORDER BY t.id",
            (task_id, task_id, task_id),
        ).fetchall()
        return [self._neighbor_from_row(r) for r in rows]

    def dependencies_of(self, task_id: int) -> list[NeighborRef]:
        """D6 -- backward-compatible alias for ``_linked_with``. Under v0.3.0
        symmetric semantics there is no separate "dependencies" set; both
        methods return the same linked-neighbor set. T93/T96 rename the public
        API; for now the directional names are preserved so existing callers
        don't break."""
        return self._linked_with(task_id)

    def dependents_of(self, task_id: int) -> list[NeighborRef]:
        """D6 -- backward-compatible alias for ``_linked_with``. See
        ``dependencies_of`` for the rationale."""
        return self._linked_with(task_id)

    # ====================================================================
    # D27 - Link discovery via `links` op (T91)
    # ====================================================================
    def links(
        self,
        ids: list[int] | None = None,
        already_seen: list[int] | None = None,
    ) -> list[NeighborRef]:
        """D27 - the link-discovery primitive that replaces v0.2.0
        search-before-create. Two modes:

        * ``ids is None`` (or empty) -> return the ANCHOR LAYER: all design +
          schema kind tasks, id-sorted. This is the spec layer that production
          work should link to.
        * ``ids = [...]`` -> return every task linked at depth=1 to any input
          id, minus the inputs themselves and minus ``already_seen``. Iteration
          is caller-driven: the caller passes its accumulated "judged" set as
          ``already_seen`` so each next layer excludes what it has handled.

        Status-blind in both modes (closed neighbors still returned).
        """
        excluded: set[int] = set(already_seen or [])
        if not ids:
            rows = self.conn.execute(
                "SELECT * FROM tasks WHERE kind IN ('design', 'schema') ORDER BY id"
            ).fetchall()
            return [
                self._neighbor_from_row(r) for r in rows if r["id"] not in excluded
            ]
        excluded.update(ids)
        placeholders = ",".join("?" * len(ids))
        sql = (
            f"SELECT * FROM tasks WHERE id IN ("
            f"  SELECT task_b FROM links WHERE task_a IN ({placeholders}) "
            f"  UNION "
            f"  SELECT task_a FROM links WHERE task_b IN ({placeholders})"
            f") ORDER BY id"
        )
        rows = self.conn.execute(sql, tuple(ids) * 2).fetchall()
        return [self._neighbor_from_row(r) for r in rows if r["id"] not in excluded]

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

    def _mark_linked_stale(self, task_id: int) -> list[NeighborRef]:
        """D10 - mark the DIRECT linked neighbors of ``task_id`` stale + open
        (invariant stale=>open, D7). One hop only; non-transitive. Recorded
        *before* the change so an interrupted reconciliation is crash-safe.
        Symmetric since T86: fires for both endpoints of any link, bounded by
        the meta-island constraint (D26)."""
        linked = self._linked_with(task_id)
        for n in linked:
            row = self._require_row(n.id)
            if row["status"] == "closed":
                self._record_transition(n.id, "closed", "open")  # forced open by stale
            self.conn.execute(
                "UPDATE tasks SET stale = 1, status = 'open', updated_at = ? WHERE id = ?",
                (_now(), n.id),
            )
        # re-read so the returned refs show stale=True
        return [self._neighbor_from_row(self._require_row(n.id)) for n in linked]

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
        # Close-gate (D14 extended, T86 symmetric): refuse to mark T done while
        # any task in its transitive linked neighborhood is stale. That
        # unreconciled drift may still change and re-invalidate T. Reach is
        # bounded in practice by the meta-island constraint (D26).
        linked_stale = self._stale_linked_transitive(task_id)
        if linked_stale:
            id_list = ", ".join(f"T{uid}" for uid in linked_stale)
            raise InvariantError(
                f"REFUSED: T{task_id} is in a linked neighborhood with unreconciled "
                f"stale task(s) {id_list} -- closing it would mark work done on top "
                f"of drift that may still change. Reconcile {id_list} first, then "
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
            newly_stale = self._mark_linked_stale(task_id)  # D10, before change
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
