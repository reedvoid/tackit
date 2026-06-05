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
    DescriptionRevision,
    History,
    LabelUsage,
    NeighborRef,
    SearchHit,
    Slice,
    StatusTransition,
    Task,
    WontDoResult,
    default_status_for_kind,
    prefixed_id,
)
from .schema import KIND_VALUES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_delta(delta: str | None, op: str) -> None:
    """T117 / cascade-ergonomics B - every mutating op that fires the cascade
    (edit, reclassify, link_add, link_rm) requires the agent to provide a short
    semantic delta. Empty / whitespace / missing is refused loudly so the agent
    can't slip past the rationale-comparison discipline. Keep it one sentence;
    "shifted D5 from directed to symmetric link" is the right shape -- you are
    writing it for future-you to compare against every edge-rationale on the
    stale list, so don't write a treatise."""
    if delta is None or not delta.strip():
        raise ValidationError(
            f"{op} requires a non-empty `delta` describing what changed "
            f"semantically (T117). One sentence; describe the semantic shift, "
            f"not the field bytes. Future-you will compare this against every "
            f"linked task's `because` rationale to decide relevance."
        )
    _validate_text(delta, f"{op} delta")


def _require_kind(kind: str | None, op: str) -> None:
    """T94 / D26 - kind is required at create-time and must be one of the four
    taxonomy values. Missing or invalid fails loud at the op boundary (D2) with
    a message that names the values, so a slipped/typo'd call never silently
    falls through to the schema's NOT NULL DEFAULT."""
    if kind is None or (isinstance(kind, str) and not kind.strip()):
        raise ValidationError(
            f"{op} requires a `kind` ∈ {{{', '.join(KIND_VALUES)}}} (D26 / T94). "
            f"Classify by 'alters running app behavior': design = decision slice, "
            f"schema = store shape, production = changes the app's behavior, "
            f"meta = bookkeeping / experiments / release tracking."
        )
    if kind not in KIND_VALUES:
        raise ValidationError(
            f"{op}: kind {kind!r} is not valid; must be one of "
            f"{{{', '.join(KIND_VALUES)}}} (D26 / T94)."
        )


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

LINK_BECAUSE_REMINDER = (
    "Each link's `because` describes WHY the two tasks are coupled. Each "
    "cascade-firing op's `delta` describes the upstream's semantic shift. "
    "Read both BEFORE opening a stale dependent -- they tell you the "
    "specific aspect to check. In the rare case where the shift doesn't "
    "intersect the coupling axis at all, you can `reconcile` without "
    "re-reading the dependent (FAST path); otherwise re-read and edit-or-"
    "reconcile (SLOW path with the question pre-formed)."
)
"""D34 / T166 - the single-source reminder string. Emitted on show / board
envelopes that contain at least one stale dep entry, alongside the per-entry
`because` + `last_edit_delta` fields the agent uses to orient reconciliation."""


def stale_alert_text(stale_tasks: list[Task]) -> str:
    """The strongly-worded stale-obligation banner; empty string when nothing is
    stale. Names the tasks, the required action (review each AGAINST its depends_on
    neighbors, then edit-or-reconcile), and the negative fallout of ignoring it."""
    if not stale_tasks:
        return ""
    ids = []
    for t in stale_tasks:
        ids.append(prefixed_id(t.kind, t.id))
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


def stale_alert_payload(stale_tasks: list[Task], short: bool = False) -> dict | None:
    """Structured form of the stale alert for the MCP result envelope; None when
    nothing is stale. Carries the same wording as :func:`stale_alert_text` by
    default; when ``short=True``, emits a compact one-line message instead
    (M181 #8b — cuts ~2k tokens per call on browse-heavy sessions; reads use
    this, writes keep the full obligation paragraph as the at-cost teaching
    moment). ``count`` and ``stale_task_ids`` are unchanged either way."""
    if not stale_tasks:
        return None
    ids = []
    for t in stale_tasks:
        ids.append(t.id)
    if short:
        message = f"⚠ {len(stale_tasks)} stale — see `stale` for the list."
    else:
        message = stale_alert_text(stale_tasks)
    return {
        "count": len(stale_tasks),
        "stale_task_ids": ids,
        "message": message,
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
        # T117 / cascade-ergonomics B: set by the mutating ops that fire the
        # cascade (edit, reclassify, link_add, link_rm) so adapters can surface
        # the agent's semantic-delta string alongside the stale_alert. Ephemeral
        # -- one op's lifetime only; not stored anywhere.
        self.last_delta: str | None = None
        # D31 (v0.4): set when edit() succeeds on a design or schema task, so
        # the adapter can surface a "this slice's number (D#/S#) is referenced
        # in code by convention; check associated files for drift" reminder.
        # Sibling to label_nudge / stale_alert: structured envelope field.
        # Ephemeral.
        self.last_code_check_reminder: str | None = None

    # --- T124: shared prelude for cascade-firing / delta-bearing ops --------
    def _record_delta(self, delta: str, op_name: str) -> None:
        """T117/T124 shared prelude - validate the required delta and record
        it on self for envelope surfacing. Used by every op that takes a
        required ``delta``: edit, reclassify (cascade-firing), wont_do (status
        verb that carries a delta too). Link ops are NOT in this list (D213):
        they don't cascade, so a delta they carried would have no reader.
        Naming the helper makes "this op carries a delta" explicit at the
        call site."""
        _require_delta(delta, op_name)
        self.last_delta = delta.strip()

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
            wont_do_reason=row["wont_do_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _require_row(self, task_id: int) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"no task with id {task_id}.")
        return row

    def _neighbor_from_row(
        self,
        row: sqlite3.Row,
        *,
        because: str | None = None,
        last_edit_delta: str | None = None,
    ) -> NeighborRef:
        # D32: include kind so the neighbor render can synthesize its auto-id
        # prefix without a second lookup.
        # D34/T166: in slice/edge contexts, the caller supplies the link's
        # because and the neighbor's most-recent edit delta from S7 so the
        # FAST-filter inputs ride in the slice envelope. Non-edge contexts
        # (links() candidates) leave both None.
        return NeighborRef(
            id=row["id"],
            name=row["name"],
            status=row["status"],
            stale=bool(row["stale"]),
            kind=row["kind"],
            because=because,
            last_edit_delta=last_edit_delta,
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
        *,
        kind: str,
        description: str = "",
        labels: list[str] | None = None,
        deps: dict[int, str] | None = None,
    ) -> Task:
        """D3 + T94 + D33 (T164) - create a task (auto monotonic id). ``kind``
        is required (D26 taxonomy: design | schema | production | meta) and
        refused as missing/invalid via D2. Optionally attach labels (D4) and
        declare symmetric links via ``deps`` (D5).

        v0.4 (D33 / T164): ``deps`` is ``{dep_id: because}`` -- each edge
        wired at creation MUST carry a real, caller-supplied ``because``
        rationale describing the coupling. Empty/whitespace because is
        refused at the boundary (same rule as ``link_add``). The pre-T164
        ``list[int]`` form with a hardcoded placeholder rationale is
        retired: a placeholder carries zero signal for the cascade-
        ergonomics filter, so every link created this way silently
        corrupts the SNR. Callers wanting graph-only wiring without a
        real rationale must now state that intent explicitly per edge."""
        _require_kind(kind, "add")
        if not name or not name.strip():
            raise ValidationError("task name must be a non-empty string (D3/S1).")
        _validate_text(name, "task name")
        _validate_text(description, "task description")
        # D33 / T164: validate per-dep rationales BEFORE mutating so the whole
        # add fails loud if any dep lacks a real because (no partial creation).
        for dep_id, because in (deps or {}).items():
            if not because or not because.strip():
                raise ValidationError(
                    f"add(deps=...) requires a real `because` rationale for each "
                    f"dep edge -- dep_id={dep_id} got empty/whitespace. The pre-T164 "
                    f"placeholder shortcut is retired (D33): a vague rationale "
                    f"carries no signal for the cascade-ergonomics filter and "
                    f"silently corrupts SNR. Pass `deps={{<id>: '<one-sentence "
                    f"coupling rationale>', ...}}` instead."
                )
        self.last_label_nudge = None  # D23: reflect only this op
        new_labels = self._new_labels(labels or [])  # D23: detect before they exist
        ts = _now()
        # v0.5 (D35 + D36): default status is partition-conditional on kind:
        # design/schema slices live at 'spec' (the living-decision status);
        # production/meta tasks live at 'open' (the work-item status). The DB
        # CHECK and Pydantic partition validator both refuse cross-partition
        # writes, so add() MUST choose the partition-valid default.
        default_status = default_status_for_kind(kind)
        with self._mutate():
            cur = self.conn.execute(
                "INSERT INTO tasks(name, description, kind, status, stale, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 0, ?, ?)",
                (name.strip(), description, kind, default_status, ts, ts),
            )
            task_id = int(cur.lastrowid)
            self._record_transition(task_id, None, default_status)  # D8: creation event
            for label in labels or []:
                self._attach_label(task_id, label)
            for dep_id, because in (deps or {}).items():
                self._add_link(task_id, dep_id, because=because)
        if new_labels:
            self._set_label_nudge(new_labels)  # D23 anti-sprawl nudge
        return self.get(task_id)

    def load(self, specs: list[dict]) -> dict[str, int]:
        """D24 + T94 - bulk-create tasks from parsed plan specs (see
        :mod:`tackit.plan`) in ONE transaction, resolving ``depends_on`` by key.
        Returns ``{key: task_id}``. Every spec must carry a valid ``kind`` (D26),
        enforced by the parser; re-checked here so a hand-built specs list can't
        slip past. Atomic: any error (bad name, missing/invalid kind, unknown
        dep key, self-edge) rolls the whole import back -- never a partial plan
        -- and is a single version bump."""
        keys: set[str] = set()
        for s in specs:
            keys.add(s["key"])
        # Pre-validate kind on every spec BEFORE mutating (fail loud, no partial).
        for s in specs:
            _require_kind(s.get("kind"), f"load: task '{s['key']}'")
        # Validate all depends_on resolve within the plan BEFORE mutating (fail loud).
        # D33 / T164: each dep entry is {"key": str, "because": str}; missing
        # or empty because is refused (no placeholder rationale path).
        for s in specs:
            for dep_entry in s["depends_on"]:
                dep_key = dep_entry["key"]
                because = dep_entry.get("because", "")
                if dep_key not in keys:
                    raise ValidationError(
                        f"task '{s['key']}' depends_on unknown key '{dep_key}' "
                        f"(not defined in this plan)."
                    )
                if not because or not because.strip():
                    raise ValidationError(
                        f"task '{s['key']}' depends_on '{dep_key}' is missing a "
                        f"real `because` rationale. Under D33 (T164), every "
                        f"link-creation path requires an explicit one-sentence "
                        f"rationale describing the coupling -- the pre-T164 "
                        f"placeholder shortcut is retired. Use the multi-line "
                        f"`depends_on:` block with `<key> :: <rationale>` "
                        f"per entry (see plan.py docstring)."
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
                # D36 v0.5: kind/status partition default. design/schema land
                # at 'spec' (the living-spec layer); production/meta land at
                # 'open' (the active-work layer). Mirrors Core.add()'s
                # default so the parser path obeys the same partition rule.
                default_status = "spec" if s["kind"] in ("design", "schema") else "open"
                cur = self.conn.execute(
                    "INSERT INTO tasks(name, description, kind, status, stale, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 0, ?, ?)",
                    (s["name"].strip(), s["desc"], s["kind"], default_status, ts, ts),
                )
                tid = int(cur.lastrowid)
                self._record_transition(tid, None, default_status)
                keymap[s["key"]] = tid
                for label in s["labels"]:
                    self._attach_label(tid, label)
            for s in specs:  # pass 2: edges (all keys now have ids)
                frm = keymap[s["key"]]
                for dep_entry in s["depends_on"]:
                    # D33 / T164: per-edge rationale comes from the plan; the
                    # pre-T164 placeholder shortcut was retired. Pre-validation
                    # above already refused any empty/missing because.
                    self._add_link(
                        frm,
                        keymap[dep_entry["key"]],
                        because=dep_entry["because"],
                    )
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

    def _add_link(self, a: int, b: int, because: str) -> None:
        """D5 + D14 + T116 - add a symmetric link between ``a`` and ``b`` with
        a required ``because`` rationale describing WHY the two tasks are
        coupled. Invariants: both endpoints exist (FK), distinct (CHECK
        task_a < task_b prevents self-link), the **meta-island constraint**
        (D26 / T87), and a non-empty ``because``. Cascade-ergonomics
        discipline (per [[T116]]): describe the coupling, not the
        implementation -- rationales stay stable when implementations
        change."""
        if a == b:
            raise InvariantError(f"a task cannot link to itself (T{a}).")
        if not because or not because.strip():
            raise ValidationError(
                "link `because` rationale must be a non-empty string (T116). "
                "Describe the coupling between the two tasks specifically -- "
                "the cascade compares this rationale against the change delta "
                "to filter relevance."
            )
        _validate_text(because, "link because")
        row_a = self._require_row(a)
        row_b = self._require_row(b)
        # D36 (v0.5): retired endpoints accept no new edges. Retired specs are
        # dead decisions; new realization links to them are nonsensical. Prior
        # content lives in description_revisions if archaeology is needed.
        kind_a = row_a["kind"]
        kind_b = row_b["kind"]
        if row_a["status"] == "retired":
            raise InvariantError(
                f"REFUSED: link_add endpoint {prefixed_id(kind_a, a)} is "
                f"retired. Retired specs accept no new edges -- there is no "
                f"realization relationship to a dead decision (D36). Prior "
                f"content lives in description_revisions."
            )
        if row_b["status"] == "retired":
            raise InvariantError(
                f"REFUSED: link_add endpoint {prefixed_id(kind_b, b)} is "
                f"retired. Retired specs accept no new edges -- there is no "
                f"realization relationship to a dead decision (D36). Prior "
                f"content lives in description_revisions."
            )
        # Meta-island constraint (D26 / T87): refuse cross-kind links between
        # meta and non-meta. Same-kind links are always allowed (meta<->meta,
        # production<->production, design<->schema, etc.).
        if (kind_a == "meta") != (kind_b == "meta"):
            raise InvariantError(
                f"REFUSED: meta-island constraint (D26). {prefixed_id(kind_a, a)} "
                f"and {prefixed_id(kind_b, b)} cannot be linked because exactly one is meta. "
                f"Meta tasks may only link other meta tasks; the boundary bounds the "
                f"cascade so meta work (release tracking, experiments) cannot drag "
                f"spec/production tasks into a stale review and vice versa."
            )
        ta, tb = self._canonical(a, b)
        try:
            self.conn.execute(
                "INSERT INTO links(task_a, task_b, because) VALUES (?, ?, ?)",
                (ta, tb, because.strip()),
            )
        except sqlite3.IntegrityError:
            # UNIQUE(task_a, task_b): the link already exists -- idempotent.
            pass

    def _stale_linked_transitive(self, task_id: int) -> list[tuple[int, str]]:
        """All tasks transitively linked to ``task_id`` (in the symmetric graph)
        that carry an obligation-bearing stale flag, excluding ``task_id``
        itself, id-sorted. Underpins the symmetric close-gate (D14): closing
        a task whose linked neighborhood is unreconciled is refused.

        v0.5 (D28 + D36): obligation iff status IN ('open','spec'). The
        kind/status partition makes spec the open-equivalent for design/
        schema; closed/wont_do production/meta and retired design/schema
        neighbors carrying stale=1 are record-only -- they do NOT pressure
        the close-gate. The walk itself still traverses all neighbors (so
        an obligation-bearing task on the far side of a terminal-stale
        neighbor is still found), but only obligation-bearing stale tasks
        end up in the return list.

        Returns list of (id, kind) tuples (T162) so callers can synthesize
        the D32 `<kind_letter><id>` prefix for refusal messages without a
        second lookup."""
        seen: set[int] = {task_id}
        stack: list[int] = [task_id]
        stale_found: list[tuple[int, str]] = []
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
                    "SELECT stale, status, kind FROM tasks WHERE id = ?", (nxt,)
                ).fetchone()
                if row is not None and bool(row["stale"]):
                    # D28 + D36 (v0.5): obligation iff status IN ('open','spec').
                    # The kind/status partition makes this equivalent to the
                    # v0.4 `open OR kind IN (design,schema)` clause for live
                    # rows, while correctly excluding 'retired' (which has
                    # kind=design/schema but is terminal -- record-only).
                    if row["status"] in ("open", "spec"):
                        stale_found.append((nxt, row["kind"]))
                stack.append(nxt)
        stale_found.sort()  # tuples sort by id, then kind
        return stale_found

    def link_add(self, a: int, b: int, because: str) -> Slice:
        """D5 (T93) + T116 - add a symmetric link between ``a`` and ``b`` with
        a required ``because`` rationale (durable coupling). Link ops do NOT
        cascade and carry NO ``delta`` (D213): ``delta`` exists to ride a
        cascade so a reconciler can compare it against a link's ``because``;
        a non-cascading op produces a delta nobody reads. The canonicalized
        row is stored once; no-op on duplicate; no-op does NOT overwrite the
        existing rationale."""
        ta, tb = self._canonical(a, b)
        existing = self.conn.execute(
            "SELECT 1 FROM links WHERE task_a = ? AND task_b = ?", (ta, tb)
        ).fetchone()
        if existing is None:
            with self._mutate():
                self._add_link(a, b, because)
        return self.show(a)

    def link_rm(self, a: int, b: int) -> Slice:
        """D5 (T93) - remove the symmetric link between ``a`` and ``b``. Link
        ops do NOT cascade and carry NO ``delta`` (D213). Canonical lookup;
        no-op if absent."""
        self._require_row(a)
        self._require_row(b)
        ta, tb = self._canonical(a, b)
        existing = self.conn.execute(
            "SELECT 1 FROM links WHERE task_a = ? AND task_b = ?", (ta, tb)
        ).fetchone()
        if existing is not None:
            with self._mutate():
                self.conn.execute(
                    "DELETE FROM links WHERE task_a = ? AND task_b = ?", (ta, tb)
                )
        return self.show(a)

    def _linked_with(self, task_id: int) -> list[NeighborRef]:
        """D6 - every task that shares a link with ``task_id``, id-sorted. Single
        set under symmetric semantics: there is no "dependencies vs dependents"
        partition. Status-blind (closed neighbors still returned).

        D34/T166: the result carries per-entry `because` (the link's
        coupling rationale) and `last_edit_delta` (the neighbor's most-
        recent edit delta from S7) so the FAST-filter inputs are reachable
        in the slice envelope without a second lookup."""
        rows = self.conn.execute(
            "SELECT t.*, l.because AS link_because, "
            "  (SELECT delta FROM description_revisions dr "
            "   WHERE dr.task_id = t.id ORDER BY dr.id DESC LIMIT 1) "
            "  AS last_edit_delta "
            "FROM links l "
            "JOIN tasks t ON t.id = CASE WHEN l.task_a = ? THEN l.task_b ELSE l.task_a END "
            "WHERE l.task_a = ? OR l.task_b = ? ORDER BY t.id",
            (task_id, task_id, task_id),
        ).fetchall()
        return [
            self._neighbor_from_row(
                r, because=r["link_because"], last_edit_delta=r["last_edit_delta"]
            )
            for r in rows
        ]

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
    # T128 - reclassify (change a task's kind after creation)
    # ====================================================================
    def reclassify(self, task_id: int, new_kind: str, delta: str) -> ChangeResult:
        """T128 - change a task's kind after creation. Kind is foundational
        (D26: it bounds the cascade via meta-island and decides where the
        task fits in the design/schema/production/meta taxonomy), so the
        v2 'kind required at create' rule (T94) leaves no way to recover
        from a creation-time misclassification. reclassify is the explicit
        recovery verb.

        Discipline: rare in practice -- it should mostly fix a wrong call
        at creation, not be used as routine ergonomic shuffling. The meta-
        island guard refuses any reclassify that would create a cross-kind
        link with an existing neighbor; the agent must link_rm the offending
        edges (or create a fresh task carrying the desired kind) before
        crossing the meta boundary.

        Fires the cascade: a kind change is a semantic shift, and every
        linked neighbor must re-review whether the relationship still makes
        sense under the new classification. Closed neighbors stay closed +
        stale=True per T123."""
        self._record_delta(delta, "reclassify")
        _require_kind(new_kind, "reclassify")
        row = self._require_row(task_id)
        # D20 no-op guard: same kind = nothing to do.
        if row["kind"] == new_kind:
            return ChangeResult(task=self.get(task_id), newly_stale=[])
        # Meta-island guard (D26 / T128): would any existing link become
        # cross-kind under the new kind? If so, refuse loudly with the
        # offending neighbors named.
        offenders = []
        for n in self._linked_with(task_id):
            neighbor_row = self._require_row(n.id)
            neighbor_kind = neighbor_row["kind"]
            if (new_kind == "meta") != (neighbor_kind == "meta"):
                offenders.append((n.id, neighbor_kind))
        if offenders:
            id_list = ", ".join(prefixed_id(k, tid) for tid, k in offenders)
            raise InvariantError(
                f"REFUSED: reclassifying {prefixed_id(row['kind'], task_id)} to "
                f"kind={new_kind!r} would create cross-kind link(s) with: "
                f"{id_list}. The meta-island "
                f"constraint (D26) refuses cross-kind links between meta and "
                f"non-meta. Either link_rm the offending edges first, or "
                f"create a new task with the desired kind (the old links stay "
                f"on the historical row)."
            )
        # v0.5 (D36): cross-partition reclassify auto-shifts status to keep
        # the kind/status partition valid. open<->spec is the clean translation;
        # cross-partition with no clean target (closed/wont_do -> design/schema,
        # or retired -> production/meta) is refused so the caller resolves the
        # state first rather than losing it in an auto-coercion. Same-partition
        # reclassify (production<->meta, design<->schema) leaves status alone.
        current_status = row["status"]
        src_is_design_schema = row["kind"] in ("design", "schema")
        dst_is_design_schema = new_kind in ("design", "schema")
        new_status: str | None = None
        if src_is_design_schema != dst_is_design_schema:
            if dst_is_design_schema:
                # production/meta -> design/schema: open auto-shifts to spec.
                if current_status == "open":
                    new_status = "spec"
                else:
                    raise InvariantError(
                        f"REFUSED: cross-partition reclassify of "
                        f"{prefixed_id(row['kind'], task_id)} at "
                        f"status='{current_status}' to kind={new_kind!r} "
                        f"has no clean status target in the destination "
                        f"partition (spec/retired). Resolve the state first: "
                        f"reopen() if closed; if the work is dropped, leave "
                        f"as wont_do (production/meta) -- don't reclassify a "
                        f"terminal-state task across the partition."
                    )
            else:
                # design/schema -> production/meta: spec auto-shifts to open.
                if current_status == "spec":
                    new_status = "open"
                else:
                    raise InvariantError(
                        f"REFUSED: cross-partition reclassify of "
                        f"{prefixed_id(row['kind'], task_id)} at "
                        f"status='{current_status}' to kind={new_kind!r} "
                        f"has no clean status target in the destination "
                        f"partition (open/closed/wont_do). A retired slice "
                        f"cannot become a production work item -- if the "
                        f"decision returned, file a fresh D# under the new "
                        f"premise; leave the retired row as historical record."
                    )
        with self._mutate():
            newly_stale = self._mark_linked_stale(task_id)
            if new_status is not None:
                self.conn.execute(
                    "UPDATE tasks SET kind = ?, status = ?, updated_at = ? "
                    "WHERE id = ?",
                    (new_kind, new_status, _now(), task_id),
                )
                self._record_transition(task_id, current_status, new_status)
            else:
                self.conn.execute(
                    "UPDATE tasks SET kind = ?, updated_at = ? WHERE id = ?",
                    (new_kind, _now(), task_id),
                )
        return ChangeResult(task=self.get(task_id), newly_stale=newly_stale)

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

        v0.5 (D28 + D27 + D36 + T180): both branches filter to viable link
        targets -- status IN ('open','spec'). Spec is the open-equivalent
        for design/schema; closed/wont_do production/meta and retired
        design/schema are excluded across both the anchor layer (no input)
        and the expansion hop. The anchor layer additionally constrains
        kind IN ('design','schema') to express its semantic (the anchor IS
        the live spec layer that production work links to).
        """
        excluded: set[int] = set(already_seen or [])
        if not ids:
            rows = self.conn.execute(
                "SELECT * FROM tasks WHERE kind IN ('design', 'schema') "
                "AND status IN ('open', 'spec') "
                "ORDER BY id"
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
            f") AND status IN ('open', 'spec') "
            f"ORDER BY id"
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
        """D7/D8 + T132 + D36 - move a closed task back to open (logged).
        Does not set stale; the history log keeps the earlier 'closed' fact.
        REFUSED on wont_do tasks -- wont_do is terminal forever per T132
        design (the change-of-mind path is a fresh task with the new
        direction). REFUSED on retired tasks (D36 v0.5: same terminal
        rationale -- file a fresh D# instead of reanimating)."""
        row = self._require_row(task_id)
        if row["status"] == "wont_do":
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} is wont_do -- reopen is not allowed "
                f"(T132: wont_do is terminal forever). If the decision has "
                f"changed, create a new task with the new direction."
            )
        if row["status"] == "retired":
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} is retired -- "
                f"reopen is not allowed (D36: retired is terminal forever, "
                f"same rationale as wont_do per T132 generalized). If the "
                f"decision has returned, file a fresh D# with the new "
                f"direction; do not reanimate this row."
            )
        # No-op guard (D20 + D36 v0.5): reopening a row already in its
        # kind's LIVE status changes nothing -- skip the UPDATE to avoid
        # spurious version bumps AND a partition CHECK violation (setting
        # status='open' on a design/schema row is illegal under D36).
        # Live status is 'open' for production/meta, 'spec' for design/
        # schema; both are no-ops.
        if row["status"] not in ("open", "spec"):
            with self._mutate():
                self._set_status(task_id, "open", clear_stale=False)
        return self.get(task_id)

    def _mark_linked_stale(self, task_id: int) -> list[NeighborRef]:
        """D10 + T123 - mark the DIRECT linked neighbors of ``task_id`` stale,
        leaving status untouched. One hop only; non-transitive. Recorded
        *before* the change so an interrupted reconciliation is crash-safe.
        Symmetric since T86: fires for both endpoints of any link, bounded by
        the meta-island constraint (D26). Closed neighbors stay closed +
        stale=True per T123's relaxed D7. Under v0.4 (D28) closed/wont_do
        stale is record only -- the worklist filter excludes it."""
        linked = self._linked_with(task_id)
        for n in linked:
            self.conn.execute(
                "UPDATE tasks SET stale = 1, updated_at = ? WHERE id = ?",
                (_now(), n.id),
            )
        # re-read so the returned refs show stale=True
        return [self._neighbor_from_row(self._require_row(n.id)) for n in linked]

    def stale_worklist(self) -> list[Task]:
        """D11 + D28 (v0.4) + D36 (v0.5) - the resumable reconciliation
        worklist: stale tasks that carry an OBLIGATION. Filters to status IN
        ('open','spec'). Closed/wont_do production/meta and retired design/
        schema tasks may still carry stale=1 as a record-only marker, but
        they're not on the worklist and don't pressure the agent. Empty list
        == reconciliation pass complete."""
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE stale = 1 "
            "AND status IN ('open', 'spec') "
            "ORDER BY id"
        ).fetchall()
        return [self._task_from_row(r) for r in rows]

    def reconcile(self, task_id: int) -> Task:
        """D11 + D28 (v0.4) + D36 (v0.5) + T156 - clear ``stale`` on a task
        reviewed and found still-correct, with no content change (so it does
        NOT cascade to dependents).

        REFUSED when status IN ('closed','wont_do','retired') -- the
        terminal states. This mirrors the worklist filter (allowed iff
        status IN ('open','spec')), so what surfaces on the worklist is
        exactly what can be reconciled. Stale on terminal rows is record-
        only archaeology (D28 + D36); clearing it would erase the historical
        signal that an upstream changed. Open and spec rows are the live
        partition -- reconcile clears stale and confirms the row still
        describes truth after the upstream shift."""
        row = self._require_row(task_id)
        if row["status"] in ("closed", "wont_do", "retired"):
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} has "
                f"status={row['status']!r} -- reconcile is not allowed on "
                f"terminal tasks (closed/wont_do/retired). Their stale flag "
                f"is record-only archaeology (D28 + D36 + T156); clearing it "
                f"would erase the historical signal that an upstream changed. "
                f"No action needed."
            )
        # No-op guard (D20): reconciling a task that isn't stale changes nothing.
        if bool(row["stale"]):
            with self._mutate():
                self.conn.execute(
                    "UPDATE tasks SET stale = 0, updated_at = ? WHERE id = ?",
                    (_now(), task_id),
                )
        return self.get(task_id)

    def reconcile_many(self, ids: list[int]) -> list[Task]:
        """D39 #1 - batch reconcile via an EXPLICIT id list: clear ``stale``
        on every id in ONE transaction (one D18 version bump, not N).

        Validate-all-first, fail loud: every id must exist and have status IN
        ('open','spec'). Any terminal-status or unknown id refuses the WHOLE
        batch (no partial sweep) and the error names ALL offending ids -- the
        batch analog of reconcile()'s single-row refusal, so a skipped id
        can't hide behind a partial success.

        THE GUARD-RAIL (D39): this takes an explicit list -- the caller still
        enumerates the set it judged still-correct. There is deliberately NO
        'reconcile all stale' / 'reconcile everything matching rationale X'
        form; that would automate the *judgment* the cascade depends on (the
        edit-quality + D34 rubber-stamp failure mode). reconcile_many batches
        transport, never judgment.

        Per-row D20 no-op guard: an id that isn't stale is accepted and
        changes nothing; if NO id is stale the transaction is skipped
        entirely (no empty version bump)."""
        # Validate every id BEFORE mutating; collect ALL offenders (fail loud).
        rows: dict[int, sqlite3.Row] = {}
        bad: list[str] = []
        for task_id in ids:
            row = self.conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                bad.append(f"{task_id} (no such task)")
                continue
            rows[task_id] = row
            if row["status"] in ("closed", "wont_do", "retired"):
                bad.append(
                    f"{prefixed_id(row['kind'], task_id)} (status={row['status']!r})"
                )
        if bad:
            raise InvariantError(
                "REFUSED: reconcile is only allowed on status IN "
                "('open','spec') -- terminal-status stale is record-only "
                "archaeology (D28 + D36). These ids cannot be reconciled: "
                + "; ".join(bad)
                + ". No row was changed -- the whole batch is refused so a "
                "partial sweep can't hide a skipped id."
            )
        # Only the genuinely-stale ids need an UPDATE (D20 no-op guard).
        to_clear: list[int] = []
        for task_id in ids:
            if bool(rows[task_id]["stale"]):
                to_clear.append(task_id)
        if to_clear:
            with self._mutate():
                ts = _now()
                for task_id in to_clear:
                    self.conn.execute(
                        "UPDATE tasks SET stale = 0, updated_at = ? WHERE id = ?",
                        (ts, task_id),
                    )
        result: list[Task] = []
        for task_id in ids:
            result.append(self.get(task_id))
        return result

    # ====================================================================
    # D9 - slice fetch
    # ====================================================================
    def show(self, task_id: int) -> Slice:
        """D9 - one task plus its directly-linked context (deps, dependents,
        labels): the small, step-sized unit of access.

        D34/T166: when at least one dep entry is stale, surface the FAST-
        filter reminder so the agent knows to compare the upstream's
        `last_edit_delta` (on the dep entry) against the link's `because`
        (also on the dep entry) before opening the dependent."""
        task = self.get(task_id)
        deps = self.dependencies_of(task_id)
        dependents = self.dependents_of(task_id)
        # D34/T166 trigger: any stale neighbor reachable from this slice.
        # Dependencies and dependents are the same set under symmetric
        # semantics (D5), so checking either is equivalent; both for clarity.
        has_stale_neighbor = any(n.stale for n in deps) or any(n.stale for n in dependents)
        return Slice(
            task=task,
            labels=self.labels_of(task_id),
            dependencies=deps,
            dependents=dependents,
            because_reminder=LINK_BECAUSE_REMINDER if has_stale_neighbor else None,
        )

    # ====================================================================
    # D12 - close (with obligation payload) / D14 close-gate
    # ====================================================================
    def close(self, task_id: int) -> CloseResult:
        """D12 + D14 + T132 + D30 + D36 (v0.5) - close a task and return the
        one-hop set to review. REFUSED while the task is stale (close-gate,
        ratified 2026-05-29). REFUSED on wont_do tasks (T132: already
        terminal in a different sense -- closed = done; wont_do = decided
        not to do). REFUSED on status='spec' (design/schema slices, post-
        partition: living spec; updating a decision is edit(), or retire()
        if 100% abandoned)."""
        row = self._require_row(task_id)
        if row["status"] == "spec":
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} has "
                f"status='spec' -- design and schema slices are LIVING SPEC, "
                f"not work items (D30 + D36). They are perma-open by design: "
                f"use edit() to refine the decision; use retire() (D36) if "
                f"the decision is 100% abandoned. The description_revisions "
                f"audit table (D29) preserves the prior verbatim state on "
                f"any edit, so editing in place is recoverable."
            )
        if row["status"] == "wont_do":
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} is wont_do -- cannot be closed (T132). "
                f"Closed and wont_do are distinct terminal states; the task is "
                f"already terminal in the 'decided not to do' sense. If the "
                f"decision has changed and the work IS being done, create a "
                f"new task carrying the new direction and close that one."
            )
        if row["status"] == "retired":
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} is retired -- "
                f"cannot be closed (D36: closed/wont_do/retired are distinct "
                f"terminal states; no double-decide). The decision is dead; "
                f"close() is for work-done. File a fresh D# if a new "
                f"decision needs to be made and tracked."
            )
        if bool(row["stale"]):
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} is stale -- it has unreconciled upstream "
                f"changes. Reconcile (review + `reconcile`) first, then close (D14)."
            )
        # Close-gate (D14 extended, T86 symmetric): refuse to mark T done while
        # any task in its transitive linked neighborhood is stale. That
        # unreconciled drift may still change and re-invalidate T. Reach is
        # bounded in practice by the meta-island constraint (D26).
        linked_stale = self._stale_linked_transitive(task_id)
        if linked_stale:
            id_list = ", ".join(prefixed_id(k, uid) for uid, k in linked_stale)
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} is in a linked neighborhood with unreconciled "
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
    # T132 - wont_do (terminal "decided not to do" status, distinct from close)
    # ====================================================================
    def wont_do(self, task_id: int, reason: str, delta: str) -> WontDoResult:
        """T132 / 2026-06-01 + D36 (v0.5) - mark a task as decided-not-to-do,
        distinct from closed (which means work done). Requires ``reason``
        (durable, persists in wont_do_reason column -- the rationale
        survives forever) and ``delta`` (ephemeral per T117). Locked-forever
        per T132 pattern: reopen / close / wont_do all REFUSED on wont_do
        tasks (the change-of-mind path is a fresh task with the new
        direction). v0.4 allows edit on wont_do (P2 retires T118). REFUSED
        on status='spec' (design/schema slices: living spec; use edit() or
        retire()). REFUSED if the task is stale or in a linked-stale
        neighborhood (same gate as close, D14). REFUSED if already wont_do
        (no double-decide). Does NOT fire the cascade (status change, not
        content edit; symmetric with close). Returns the standard
        CloseResult-shaped payload of one-hop neighbors for migrate-or-stay
        review."""
        self._record_delta(delta, "wont_do")
        if not reason or not reason.strip():
            raise ValidationError(
                "wont_do requires a non-empty `reason` -- the durable rationale "
                "for the decision not to do this task (persisted forever in "
                "wont_do_reason). One sentence is enough; future-you will read "
                "it to understand why this scope was dropped."
            )
        _validate_text(reason, "wont_do reason")
        row = self._require_row(task_id)
        if row["status"] == "spec":
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} has "
                f"status='spec' -- design and schema slices are LIVING SPEC, "
                f"not work items (D30 + D36). wont_do is for dropped work; a "
                f"design decision can't be 'not done' -- it either holds, or "
                f"it's edited to reflect a changed state. Use edit() to refine; "
                f"use retire() (D36) if the decision is 100% abandoned."
            )
        if row["status"] == "wont_do":
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} is already wont_do (T132). The decision "
                f"is locked; if it has changed, create a new task with the new "
                f"direction."
            )
        if row["status"] == "closed":
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} is closed (work done) -- it cannot be "
                f"reclassified as wont_do (decided not to do) (T132). The two "
                f"are distinct terminal states. If the close was a mistake "
                f"and the work shouldn't have been done, create a new task "
                f"explaining the wont_do decision."
            )
        if row["status"] == "retired":
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} is retired -- "
                f"cannot be marked wont_do (D36: closed/wont_do/retired are "
                f"distinct terminal states; no double-decide). The decision "
                f"is already dead via retire()."
            )
        if bool(row["stale"]):
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} is stale -- it has unreconciled upstream "
                f"changes. Reconcile (review + `reconcile`) first, then wont_do (T132)."
            )
        linked_stale = self._stale_linked_transitive(task_id)
        if linked_stale:
            id_list = ", ".join(prefixed_id(k, uid) for uid, k in linked_stale)
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} is in a linked neighborhood with "
                f"unreconciled stale task(s) {id_list}. Reconcile {id_list} "
                f"first, then wont_do (T132)."
            )
        with self._mutate():
            self.conn.execute(
                "UPDATE tasks SET status = 'wont_do', wont_do_reason = ?, "
                "updated_at = ? WHERE id = ?",
                (reason.strip(), _now(), task_id),
            )
            self._record_transition(task_id, row["status"], "wont_do")
        return WontDoResult(
            task=self.get(task_id),
            dependencies=self.dependencies_of(task_id),
            dependents=self.dependents_of(task_id),
        )

    # ====================================================================
    # D36 (v0.5) - retire (terminal "decision 100% gone" verb for design/schema)
    # ====================================================================
    _RETIRE_PLACEHOLDERS = frozenset(
        {"tbd", "todo", "obsolete", "no longer needed"}
    )

    def retire(self, task_id: int, reason: str, delta: str) -> WontDoResult:
        """D36 (v0.5) - retire a design/schema slice: status spec -> retired.

        Use ONLY when the slice's premise is 100% gone with no replacement.
        Partial-change path is edit() + let the cascade prompt link review.
        Mirrors wont_do() shape (durable ``reason`` in wont_do_reason; no
        cascade fire; no description_revisions row; returns one-hop
        obligation payload).

        Refusal order (fail-fast, 6 checks):
          1. Reason validation: non-empty + non-placeholder (D33 extension).
          2. status='spec' (the only valid source state).
          3. kind IN ('design','schema') (redundant under partition; kept
             for error clarity on the misuse path).
          4. Stale gate (D14): refused if the target is stale.
          5. Linked-stale gate: refused if any obligation-bearing stale
             task sits in the transitive linked neighborhood.
          6. Open-neighbor gate: refused if any linked neighbor has
             status='open'. The refusal lists each open neighbor with its
             `because` rationale and presents the (i)/(ii) decision tree
             (link_rm + wont_do vs link_rm alone).

        Terminal state: reopen/close/wont_do/retire all refused on retired
        rows (T132 generalized -- no double-decide). Edit IS still allowed
        per D29 (audit-table backstop)."""
        self._record_delta(delta, "retire")
        if not reason or not reason.strip():
            raise ValidationError(
                "retire requires a non-empty `reason` -- the durable "
                "rationale for retiring this decision (persisted forever in "
                "wont_do_reason). One sentence is enough; future-you will "
                "read it to understand why this decision was 100% dropped."
            )
        _validate_text(reason, "retire reason")
        placeholder = reason.strip().lower()
        if placeholder in self._RETIRE_PLACEHOLDERS:
            raise ValidationError(
                f"retire reason must be a real rationale, not a placeholder "
                f"({reason!r}). Describe WHY the decision is 100% gone -- "
                f"the replaced premise, the dropped product direction, the "
                f"schema collapse, etc. Placeholders refused per D33 "
                f"extension."
            )
        row = self._require_row(task_id)
        if row["status"] != "spec":
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} has "
                f"status={row['status']!r} -- retire is only valid on living "
                f"specs (status='spec'). If the decision returned, file a "
                f"fresh D# instead of reanimating this row (T132 generalized: "
                f"no double-decide)."
            )
        if row["kind"] not in ("design", "schema"):
            # Defensive: under partition this is unreachable (spec only on
            # design/schema), but the explicit refusal keeps the error self-
            # explaining if the partition CHECK is ever bypassed.
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} has "
                f"kind={row['kind']!r} -- retire is for design/schema "
                f"slices only. For production/meta tasks, use wont_do() to "
                f"drop scope."
            )
        if bool(row["stale"]):
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} is stale -- "
                f"it has unreconciled upstream changes. Reconcile (review + "
                f"`reconcile`) first, then retire (D36)."
            )
        linked_stale = self._stale_linked_transitive(task_id)
        if linked_stale:
            id_list = ", ".join(prefixed_id(k, uid) for uid, k in linked_stale)
            raise InvariantError(
                f"REFUSED: {prefixed_id(row['kind'], task_id)} is in a "
                f"linked neighborhood with unreconciled stale task(s) "
                f"{id_list}. Reconcile {id_list} first, then retire (D36)."
            )
        linked = self._linked_with(task_id)
        open_neighbors = [n for n in linked if n.status == "open"]
        if open_neighbors:
            details = "\n".join(
                f"  - {prefixed_id(n.kind, n.id)} (status={n.status}) -- "
                f"because: {n.because!r}"
                for n in open_neighbors
            )
            n = len(open_neighbors)
            self_id = prefixed_id(row["kind"], task_id)
            raise InvariantError(
                f"REFUSED: {self_id} has {n} open linked task(s). Resolve "
                f"each before retiring:\n{details}\n"
                f"For each open neighbor, decide:\n"
                f"  (i)  If the neighbor's work realizes ONLY this retired "
                f"decision: link_rm + wont_do(neighbor, reason=...) -- the "
                f"work is dead too.\n"
                f"  (ii) If the neighbor's work has other reasons to exist "
                f"(linked to other living specs): link_rm -- work continues "
                f"under remaining live premises.\n"
                f"Then re-attempt retire({self_id})."
            )
        with self._mutate():
            self.conn.execute(
                "UPDATE tasks SET status = 'retired', wont_do_reason = ?, "
                "updated_at = ? WHERE id = ?",
                (reason.strip(), _now(), task_id),
            )
            self._record_transition(task_id, row["status"], "retired")
        return WontDoResult(
            task=self.get(task_id),
            dependencies=self.dependencies_of(task_id),
            dependents=self.dependents_of(task_id),
        )

    # ====================================================================
    # D13 - change (with cascade entry) -> D10
    # ====================================================================
    def edit(
        self,
        task_id: int,
        delta: str,
        name: str | None = None,
        description: str | None = None,
    ) -> ChangeResult:
        """D13 + T117 + D29 - edit a task: first mark its direct linked tasks
        stale (D10), record the prior name+description as a description_revisions
        audit row (D29 / S7), then apply the edit, then return the now-stale set.
        ``delta`` (T117) is required and surfaces in the stale_alert envelope so
        reconcilers compare it against each link's `because` rationale.

        v0.4 (D29): edit is allowed on any status -- closed, wont_do, open.
        The audit table preserves the verbatim prior state, so edit no longer
        destroys history. (T118's "no-edit-closed" rule is retired.) The
        wont_do reason field is not edited via this op -- it's set once at
        wont_do() time and is immutable thereafter.

        D31 (v0.4): if the edited task is kind in {design,schema}, the
        adapter envelope includes a code-check reminder pointing the agent
        at code referencing the slice's D#/S# id."""
        self._record_delta(delta, "edit")
        self.last_code_check_reminder = None  # D31: reflect only this op
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
            # D29 / S7: record verbatim prior name + description before the
            # UPDATE overwrites them. Always full prior state (both fields),
            # not just the changed one -- archaeology can then read a single
            # revision row to recover the task's state at a point in time.
            self.conn.execute(
                "INSERT INTO description_revisions("
                "  task_id, prev_name, prev_description, delta, edited_at"
                ") VALUES (?, ?, ?, ?, ?);",
                (task_id, row["name"], row["description"], delta.strip(), _now()),
            )
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
        # D31 (v0.4): code-check reminder on design/schema edits. tackit
        # can't introspect which files reference D#/S# -- the agent does the
        # grep -- so the reminder just names the slice id+name + nudges.
        if row["kind"] in ("design", "schema"):
            edited = self.get(task_id)
            self.last_code_check_reminder = (
                f"D31: {prefixed_id(row['kind'], task_id)} was edited -- "
                f"{edited.name!r}. The slice's D#/S# id is referenced in "
                f"code by convention (SKILL.md code↔task naming rule). "
                f"Grep for the id and check the associated files for drift."
            )
        return ChangeResult(task=self.get(task_id), newly_stale=newly_stale)

    # --- T179: diff-shaped description edits (append + substring replace) ---
    #
    # Why these exist: ``edit()`` takes the full new description string. For a
    # 50k-char body the round-trip cost is the full body in *and* the full body
    # out -- ~30k tokens per fold-back. Diff-shaped ops cut that ~10x: only the
    # snippet being added or the (old, new) substring pair crosses the wire.
    #
    # Both ops share ``_commit_description_edit`` for the post-validation tail
    # (no-op short-circuit, cascade depth-1, audit row, UPDATE, D31 reminder),
    # so the contract -- one cascade per real change, one audit row per real
    # change, identical D31 wording -- is enforced in one place.

    def _commit_description_edit(
        self,
        row: sqlite3.Row,
        new_description: str,
        delta: str,
    ) -> ChangeResult:
        """T179 shared tail for description-only edits (edit_append +
        edit_replace_substring). Caller has: validated ``delta`` via
        ``_record_delta``, fetched ``row``, computed ``new_description``,
        and validated it via ``_validate_text``.

        Handles: no-op short-circuit (D20 -- equal description -> no cascade,
        no audit row, no version bump), cascade depth-1 (D10), description_
        revisions audit row preserving prior verbatim name+description+delta
        (D29 / S7), UPDATE description+updated_at, D31 code-check reminder on
        design/schema edits."""
        task_id = row["id"]
        if new_description == row["description"]:
            return ChangeResult(task=self.get(task_id), newly_stale=[])
        with self._mutate():
            newly_stale = self._mark_linked_stale(task_id)
            self.conn.execute(
                "INSERT INTO description_revisions("
                "  task_id, prev_name, prev_description, delta, edited_at"
                ") VALUES (?, ?, ?, ?, ?);",
                (task_id, row["name"], row["description"], delta.strip(), _now()),
            )
            self.conn.execute(
                "UPDATE tasks SET description = ?, updated_at = ? WHERE id = ?",
                (new_description, _now(), task_id),
            )
        if row["kind"] in ("design", "schema"):
            edited = self.get(task_id)
            self.last_code_check_reminder = (
                f"D31: {prefixed_id(row['kind'], task_id)} was edited -- "
                f"{edited.name!r}. The slice's D#/S# id is referenced in "
                f"code by convention (SKILL.md code↔task naming rule). "
                f"Grep for the id and check the associated files for drift."
            )
        return ChangeResult(task=self.get(task_id), newly_stale=newly_stale)

    def edit_append(
        self, task_id: int, content: str, delta: str
    ) -> ChangeResult:
        """T179 - append ``content`` to the task's description. Fires the
        cascade depth-1 like ``edit()``; writes a description_revisions
        audit row preserving the prior verbatim name+description+delta.

        Diff-shaped vs ``edit()``: only ``content`` crosses the wire, not
        the full new description. Cuts large-body edit cost ~10x.

        Refused on empty / whitespace-only ``content`` -- a whitespace-only
        append is almost always a typo'd no-op the caller didn't mean."""
        self._record_delta(delta, "edit_append")
        self.last_code_check_reminder = None
        row = self._require_row(task_id)
        if not content or not content.strip():
            raise ValidationError(
                "edit_append refused: content must be non-empty (no "
                "whitespace-only -- a whitespace append is almost always "
                "a typo'd no-op)."
            )
        _validate_text(content, "edit_append content")
        new_description = (row["description"] or "") + content
        _validate_text(new_description, "task description")
        return self._commit_description_edit(row, new_description, delta)

    def edit_replace_substring(
        self,
        task_id: int,
        old_string: str,
        new_string: str,
        delta: str,
    ) -> ChangeResult:
        """T179 - replace exact substring ``old_string`` with ``new_string``
        in the task's description. Fires the cascade depth-1 like ``edit()``;
        writes the description_revisions audit row preserving the prior
        verbatim state.

        Diff-shaped vs ``edit()``: only the (old, new) substring pair crosses
        the wire, not the full new description. Cuts large-body edit cost ~10x.

        Refusal matrix (fail loud, mirroring the filesystem Edit tool's
        old_string/new_string pattern):
          * empty ``old_string`` -> refused (no unambiguous match point).
          * ``old_string`` not found in description -> refused (caller likely
            typo'd the substring).
          * ``old_string`` appears N>1 times -> refused with N; caller adds
            surrounding context to disambiguate.

        Empty ``new_string`` is ALLOWED -- it is a legitimate deletion of
        the matched substring. ``old_string == new_string`` is a no-op (D20)
        and succeeds silently with no cascade.

        Matching is literal -- not regex. '.' matches '.', not 'any char'.
        ``new_string`` containing ``old_string`` replaces exactly once
        (str.replace count=1 semantics), not a recursive sweep."""
        self._record_delta(delta, "edit_replace_substring")
        self.last_code_check_reminder = None
        row = self._require_row(task_id)
        if not old_string:
            raise ValidationError(
                "edit_replace_substring refused: old_string must be "
                "non-empty (an empty substring has no unique match point)."
            )
        _validate_text(old_string, "edit_replace_substring old_string")
        _validate_text(new_string, "edit_replace_substring new_string")
        current = row["description"] or ""
        count = current.count(old_string)
        if count == 0:
            raise ValidationError(
                "edit_replace_substring refused: old_string not found in "
                "description. Verify the exact substring (check whitespace, "
                "case, hidden characters)."
            )
        if count > 1:
            raise ValidationError(
                f"edit_replace_substring refused: old_string appears "
                f"{count} times in description; substring must be unique. "
                f"Add surrounding context to disambiguate."
            )
        new_description = current.replace(old_string, new_string, 1)
        _validate_text(new_description, "task description")
        return self._commit_description_edit(row, new_description, delta)

    # ====================================================================
    # D15 - query / board (derived views)
    # ====================================================================
    def ls(
        self,
        status: str | None = None,
        label: str | None = None,
        stale: bool | None = None,
    ) -> list[Task]:
        """D15 + T157 + D36 (v0.5) - list/filter tasks by status, label, and/
        or stale. The work queue and any board are *queries over the fields*,
        not maintained lists. Status filter accepts the full v0.5 set
        (open, closed, wont_do for production/meta; spec, retired for
        design/schema) per D7+D36's five-status partitioned taxonomy."""
        if status is not None and status not in (
            "open", "closed", "wont_do", "spec", "retired"
        ):
            raise ValidationError(
                "status filter must be one of open, closed, wont_do (production/"
                "meta) or spec, retired (design/schema) per D7 + D36 v0.5."
            )
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
            out.append(f"## {prefixed_id(t.kind, t.id)} - {t.name}  ({', '.join(flags)})")
            deps = self.dependencies_of(t.id)
            if deps:
                dep_str = ", ".join(prefixed_id(d.kind, d.id) for d in deps)
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
    def search(self, query: str, limit: int = 20, name_only: bool = False) -> list[SearchHit]:
        """D17 + D28 (v0.4) - ranked keyword search over name+description via
        FTS5. Returns ids+titles+scores+status (and wont_do_reason for
        wont_do hits), best first. The status field lets adapters tag
        historical hits inline so the agent doesn't have to open each
        result to know whether it's live work or record. ``search -> show``
        is tackit's retrieval loop. Score is -bm25 (higher = more relevant).

        M181 #8d: ``name_only=True`` scopes the match to the name column
        only (FTS5 column-filter syntax ``{name}: <query>``). Useful for
        looking up tasks by distinctive title phrase without description
        hits adding noise."""
        if not query or not query.strip():
            raise ValidationError("search query must be non-empty (D17).")
        if name_only:
            query = "{name}: " + query
        try:
            rows = self.conn.execute(
                "SELECT t.id AS id, t.name AS name, t.status AS status, t.kind AS kind, "
                "t.wont_do_reason AS wont_do_reason, bm25(tasks_fts) AS bm25 "
                "FROM tasks_fts JOIN tasks t ON t.id = tasks_fts.rowid "
                "WHERE tasks_fts MATCH ? ORDER BY bm25 LIMIT ?",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            # malformed FTS5 query syntax -> fail loud at the boundary (D2)
            raise ValidationError(f"invalid search query (FTS5 syntax): {exc}") from exc
        return [
            SearchHit(
                id=r["id"],
                name=r["name"],
                score=-float(r["bm25"]),
                status=r["status"],
                kind=r["kind"],  # D32: carried for prefixed_name synthesis
                wont_do_reason=r["wont_do_reason"],
            )
            for r in rows
        ]

    # ====================================================================
    # D8 - read status history
    # ====================================================================
    def history(self, task_id: int) -> History:
        """D8 + D29 (v0.4) - the full append-only history for a task: status
        transitions and description revisions. Two separate logs, both
        chronological. Reconstructs the task's life: how it changed status
        and what its prior name/description used to say."""
        self._require_row(task_id)
        st_rows = self.conn.execute(
            "SELECT * FROM status_transitions WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        dr_rows = self.conn.execute(
            "SELECT * FROM description_revisions WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        return History(
            status_transitions=[
                StatusTransition(
                    id=r["id"],
                    task_id=r["task_id"],
                    from_status=r["from_status"],
                    to_status=r["to_status"],
                    changed_at=r["changed_at"],
                )
                for r in st_rows
            ],
            description_revisions=[
                DescriptionRevision(
                    id=r["id"],
                    task_id=r["task_id"],
                    prev_name=r["prev_name"],
                    prev_description=r["prev_description"],
                    delta=r["delta"],
                    edited_at=r["edited_at"],
                )
                for r in dr_rows
            ],
        )

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

    # ====================================================================
    # M187 + T193 - spec-only export (disaster-recovery artifact)
    # ====================================================================
    def export_specs_only(self) -> str:
        """T193 / M187 — emit a SQL dump of only the spec layer (design +
        schema tasks, their labels, spec-to-spec links, and their audit
        rows from status_transitions + description_revisions). Production
        and meta rows are excluded; the FTS index (S5) and meta table (S6)
        are excluded — both are derived/managed by `tackit init` + the
        migration path.

        The output is consumable by `tackit import` against a freshly
        initialized store: the schema already exists, this dump appends
        only the spec-layer INSERT statements. Wrapped in a BEGIN/COMMIT
        transaction so a partial failure rolls back.

        Per M187 design: this is the disaster-recovery artifact for the
        tackit-on-tackit dogfood. The `.tackit/tackit.db` is gitignored
        on a public tool repo; the spec-only dump goes to a committed
        `examples/specs.sql` so design + schema decisions survive
        `git clone`. Production + meta task state stays private."""
        spec_id_rows = self.conn.execute(
            "SELECT id FROM tasks WHERE kind IN ('design','schema') ORDER BY id"
        ).fetchall()
        spec_ids: list[int] = []
        for row in spec_id_rows:
            spec_ids.append(row["id"])

        out: list[str] = ["BEGIN TRANSACTION;"]

        for row in self.conn.execute(
            "SELECT * FROM tasks WHERE kind IN ('design','schema') ORDER BY id"
        ).fetchall():
            out.append(_sql_insert("tasks", row))

        # task_labels, links (both endpoints), status_transitions,
        # description_revisions — only when we have spec rows.
        if spec_ids:
            placeholders = ",".join("?" * len(spec_ids))

            for row in self.conn.execute(
                f"SELECT * FROM task_labels WHERE task_id IN ({placeholders}) "
                f"ORDER BY task_id, label",
                spec_ids,
            ).fetchall():
                out.append(_sql_insert("task_labels", row))

            for row in self.conn.execute(
                f"SELECT * FROM links WHERE task_a IN ({placeholders}) "
                f"AND task_b IN ({placeholders}) ORDER BY id",
                spec_ids + spec_ids,
            ).fetchall():
                out.append(_sql_insert("links", row))

            for row in self.conn.execute(
                f"SELECT * FROM status_transitions WHERE task_id IN ({placeholders}) "
                f"ORDER BY id",
                spec_ids,
            ).fetchall():
                out.append(_sql_insert("status_transitions", row))

            for row in self.conn.execute(
                f"SELECT * FROM description_revisions WHERE task_id IN ({placeholders}) "
                f"ORDER BY id",
                spec_ids,
            ).fetchall():
                out.append(_sql_insert("description_revisions", row))

        out.append("COMMIT;")
        return "\n".join(out) + "\n"


def _sql_insert(table: str, row) -> str:
    """Build a literal `INSERT INTO {table} (cols) VALUES (vals);` statement
    from a sqlite3.Row. Used by export_specs_only (T193 / M187)."""
    cols: list[str] = []
    vals: list[str] = []
    for col in row.keys():
        cols.append(col)
        vals.append(_sql_literal(row[col]))
    return f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(vals)});"


def _sql_literal(v) -> str:
    """Format a Python value as a SQLite literal, escaping single quotes."""
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, bytes):
        return "X'" + v.hex() + "'"
    s = str(v)
    return "'" + s.replace("'", "''") + "'"
