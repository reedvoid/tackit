BEGIN TRANSACTION;
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (37, 'S1 — tasks', 'The atomic item. One row per task; this is the single source of truth a task''s every view is derived from.

Columns:
- id (INTEGER, PK, monotonic auto-increment, stable and agent/human-friendly).
- name (TEXT, NOT NULL, short title).
- description (TEXT, the detail/body, stored in the DB — no external detail files).
- kind (TEXT, NOT NULL, CHECK in (design, schema, production, meta) — required at create, classifies the task by whether it alters the running app; D26).
- status (TEXT, NOT NULL, CHECK in (open, closed, wont_do), default open — lifecycle, informational, never gates traversal). v0.4 added wont_do as a distinct end state (decided not to do, with durable reason), separating it from closed (work shipped).
- stale (BOOLEAN, NOT NULL, default false — dirty bit; carried regardless of status under v0.4 bounded-obligation D28: closed/wont_do stale=True is record-only historical signal, not on the worklist).
- wont_do_reason (TEXT, NULL when status != wont_do, NOT NULL when status=''wont_do'' — durable reason captured at wont_do() time; no edit API, persists forever).
- created_at (TIMESTAMP, NOT NULL).
- updated_at (TIMESTAMP, NOT NULL).

The v0.3.0 superseded_by column (FK → tasks.id) was added in mig 005 and DROPPED in mig 006 (v0.4). The supersede verb was retired (D29) because it required tasks to be atomic enough that "premise replaced" applied to the whole bundle, which broke down in practice when only one of several facets was invalidated. The description_revisions audit table (S7) — append-only rows recording the prior verbatim name+description+delta on every edit — replaces the marker mechanism: any edit (including edits to closed/wont_do tasks per D29) preserves prior state in S7, so editing in place no longer destroys history.

Backs: D1, D3, D7 (status + stale flag), D9, D12 (close), D13 (edit/cascade entry), D15 (queries), D26 (kind taxonomy), D29 (audit + edit-on-closed), D30 (perma-open for design/schema — enforced at the status-change verbs, not at the S1 level).

## v0.5 update (D35+D36 partition)

The status CHECK constraint is now **5 values**, not 3:

```sql
status TEXT NOT NULL CHECK (status IN (''open'',''closed'',''wont_do'',''spec'',''retired''))
```

And an additional **partition CHECK** couples kind to status:

```sql
CHECK (
    (kind IN (''production'',''meta'') AND status IN (''open'',''closed'',''wont_do''))
    OR
    (kind IN (''design'',''schema'')   AND status IN (''spec'',''retired''))
)
```

**Partition consequence — one terminal verb per row:**
- production/meta rows can be terminated by `close()` (status→closed) or `wont_do()` (status→wont_do).
- design/schema rows can be terminated by `retire()` (status→retired); they do NOT close or wont_do (those are not partition-valid targets).

**wont_do_reason column dual role (D36):**
- On a wont_do row (production/meta), `wont_do_reason` holds the durable rationale captured at `wont_do()` time.
- On a retired row (design/schema), `wont_do_reason` holds the durable rationale captured at `retire()` time (column is reused — semantically still "why is this terminal" but the verb differs by partition).
- NULL on open/closed/spec rows.
- No edit API on either case; both are immutable post-write.

Migration 009 (shipped commit 3a92904) is what extends the CHECK + partitions
existing rows + backfills the partition for the schema upgrade from v9→v10.
Existing kind=design/schema rows with status=''open'' migrate to ''spec'';
existing kind=design/schema rows with status=''wont_do'' migrate to ''retired''
(with the wont_do_reason column preserved).

The `kind` column is now ACTIVE constraint material, not just classification —
its value determines which status set a row may be in. Phase 1 (T168)
implemented the partition CHECK; Phase 2a (T173) made every status-derived
predicate in core.py honor it; Phase 2b (T174) added the retire() verb.

Backs (v0.5 additions): D35 (T167 — spec status value), D36 (T171 — retired
status + retire() verb + partition + propagation principle), T168 (Phase 1
realization), T173 (Phase 2a predicates), T174 (Phase 2b retire verb).', 'spec', 0, '2026-05-31T04:11:47.872506+00:00', '2026-06-03T08:35:07.091894+00:00', 'schema', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (38, 'S2 — task_labels', 'Freeform many-to-many tags. No separate labels dimension table — labels carry no attributes or state, so a join table of plain strings is the whole thing (avoids over-normalization).
Reserved label names (added v0.3.0; D26): the four kind values (design, schema, production, meta) are reserved — label_add and load refuse a label with any of these strings. The kind property on tasks (S1) absorbs that distinction; a stray label of the same string would silently disagree. Enforced in logic at the D14 boundary, not in DDL (to allow migration-time backfill).
Columns: task_id (INTEGER, FK → tasks.id); label (TEXT, NOT NULL, the tag string, e.g. release, smoke-test); PRIMARY KEY (task_id, label) — a task can''t carry the same label twice.
Backs: D4, D9, D15, D16.', 'spec', 0, '2026-05-31T04:11:47.874201+00:00', '2026-06-03T02:49:05.522265+00:00', 'schema', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (39, 'S3 — links', 'The single edge type. Symmetric — one row per unordered pair, stored in canonical order (lower id first). Queries return the same row regardless of which endpoint you start from (D6). Renamed from `dependencies` in v0.3.0; see migration 003 (T86).
Columns: id (INTEGER, PK); task_a (INTEGER, NOT NULL, FK → tasks.id — canonical endpoint, lower id); task_b (INTEGER, NOT NULL, FK → tasks.id — canonical endpoint, higher id); CHECK (task_a < task_b) — symmetric pair, canonical order (also prevents self-link); UNIQUE (task_a, task_b) — a pair can be linked at most once.
Note: under symmetric semantics there is no cycle invariant; an undirected edge has no cycle in the directed sense.
Backs: D5, D6, D9, D10, D12, D13, D14, D27.', 'spec', 0, '2026-05-31T04:11:47.876369+00:00', '2026-06-03T02:49:05.536142+00:00', 'schema', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (40, 'S4 — status_transitions', 'Append-only history of status changes, so reopening a closed task never erases the fact that it was completed earlier. The live status on tasks is current working state; this is the record of how it got there.
Columns: id (INTEGER, PK); task_id (INTEGER, FK → tasks.id); from_status (TEXT, NULL allowed on first transition/creation); to_status (TEXT, NOT NULL); changed_at (TIMESTAMP, NOT NULL); append-only — rows are never edited or deleted.
Backs: D8.', 'spec', 0, '2026-05-31T04:11:47.876437+00:00', '2026-06-03T02:49:05.549960+00:00', 'schema', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (41, 'S5 — tasks_fts', 'An FTS5 virtual table indexing tasks for ranked keyword search (D17). Kept in sync with tasks by triggers (insert/update/delete). No external dependency — FTS5 ships with SQLite.

Columns: rowid (INTEGER, = tasks.id); name (TEXT, indexed); description (TEXT, indexed).

The indexed `name` carries the synthesized D32 prefix (T161, v0.4): the INSERT and UPDATE triggers store `<kind_letter><id> — <name>` (design→D, schema→S, production→T, meta→M) into the FTS row, not the bare stored `tasks.name`. The DELETE trigger is unchanged. This lets `search("T238")` and `search("D23")` find the right row by id-prefix regardless of whether the stored name carries such a substring. Migration 008 rebuilt the FTS for all pre-existing rows; the `tasks.name` column itself is untouched.

Backs: D17, D32.', 'spec', 0, '2026-05-31T04:11:47.876487+00:00', '2026-06-03T02:49:05.563369+00:00', 'schema', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (42, 'S6 — meta', 'Small key/value table for CLI bookkeeping. Holds version (monotonic generation counter, +1 per mutation — the ordering signal for sync, D18), synced_sql_hash (content hash of the last-produced tackit.sql — the integrity signal), and schema_version (for migrations).
Columns: key (TEXT, PK); value (TEXT, NOT NULL).
Backs: D18.', 'spec', 0, '2026-05-31T04:11:47.876525+00:00', '2026-05-31T04:11:47.965635+00:00', 'schema', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (43, 'D1 — Persistent task store', 'tackit keeps all state in a single local SQLite file (WAL mode). A project''s truth lives in one place, outside the agent''s context window, and survives across sessions and compaction.', 'spec', 0, '2026-05-31T04:11:47.876559+00:00', '2026-06-03T02:49:05.577439+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (44, 'D2 — Typed validation boundary', 'Every read and write passes through Pydantic models. Malformed data — a bad status, a missing required field, an edge pointing at a nonexistent task — is rejected at the boundary as a loud error, never stored. This is the "deterministic" guarantee in practice.
Un-storable text is also refused (2026-05-30, closed-issue #22): a stored name/description/label may not contain a NUL byte (it breaks the D18 executescript rebuild from tackit.sql) or an unpaired UTF-16 surrogate (not valid UTF-8, so SQLite can''t encode it). Both are rejected loudly so the store always stays storable and rebuildable. (Both were found by the property-based round-trip test, not by example tests — see Testing.)', 'spec', 0, '2026-05-31T04:11:47.876596+00:00', '2026-06-03T08:35:08.723321+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (45, 'D3 — Task create / read / update', 'Create a task (auto-assigned monotonic id, name, description, kind), read it back, and edit its name/description. **`kind` (design|schema|production|meta, D26) is REQUIRED at create time** (T94) and determines the row''s partition under D36 (kind ∈ {design,schema} ⟹ status ∈ {spec,retired}; kind ∈ {production,meta} ⟹ status ∈ {open,closed,wont_do}). The atomic unit of work; everything else attaches to it.', 'spec', 0, '2026-05-31T04:11:47.876630+00:00', '2026-06-03T08:35:11.073138+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (46, 'D4 — Labels', 'Attach zero or more freeform text labels to a task and list tasks by label. Labels are dumb tags — no status, no rules, no behavior — used only for human grouping/scoping (e.g. design, smoke-test). A task may wear several.', 'spec', 0, '2026-05-31T04:11:47.876663+00:00', '2026-06-03T08:35:12.641800+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (47, 'D5 — Symmetric link', 'Declare "task A is linked with task B" as a single symmetric edge type — no other relationship kinds, no direction. Each link is stored once in canonical order (lower id first, S3); the two argument orderings link_add(a, b) and link_add(b, a) produce the same row and are indistinguishable thereafter. Replaces the v0.2.0 directed depends_on edge; see migration 003 (T86).', 'spec', 0, '2026-05-31T04:11:47.876699+00:00', '2026-06-03T08:35:15.787313+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (48, 'D6 — Linked-tasks traversal', 'Given a task, return its linked tasks — every task it shares a link with — in one call, as a single set (not partitioned into "dependencies" vs "dependents"; symmetric links have no such partition). This is the primitive the slice fetch (D9), the close-review (D12), and the change-cascade (D13) are built on. Traversal is status-blind — a closed neighbor is still returned.', 'spec', 0, '2026-05-31T04:11:47.876732+00:00', '2026-06-03T08:35:17.521705+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (49, 'D7 — Status + stale flag', 'A task''s status is one of open, closed, or wont_do (v0.4 added wont_do as a distinct terminal state for "decided not to do" work, separate from closed which means "work shipped") — informational only; it never gates traversal. A separate stale flag marks a task as having an upstream that changed under it.

Under D28 (v0.4 bounded obligation) the stale flag rides on tasks of ANY status, but the WORKLIST is filtered: `status=''open'' OR kind in {design, schema}`. Closed/wont_do production/meta tasks carrying stale=True are RECORD-ONLY historical signal — an upstream changed, but the terminal task is not on the worklist and does not trip the close-gate. The v0.3-era invariant `stale ⇒ open` (cascade force-reopens) is retired (T123): the cascade no longer touches status. open-because-new-work and open-because-stale are still queryable apart.', 'spec', 0, '2026-05-31T04:11:47.876765+00:00', '2026-06-03T08:35:19.375916+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (50, 'D8 — Status transition history', 'Every status change is recorded with a timestamp. Reopening a closed task therefore does not erase the fact that it was completed earlier: the live status is current working state; the log is the history.

## v0.5 update (D35+D36 — extended status enum + mig 009 backfill)

`status_transitions.to_status` now accepts **5 values** — open, closed,
wont_do, spec, retired — matching the v0.5 extended status enum on S1
(see S1 v0.5 update for the partition CHECK).

**Mig 009 backfill (commit 3a92904 Phase 1):**
- Every legacy design/schema row that migrated from `status=''open''` to
  `status=''spec''` gets a backfilled `status_transitions` row recording the
  migration moment (`from_status=''open'', to_status=''spec''`).
- Every legacy design/schema row that migrated from `status=''wont_do''` to
  `status=''retired''` gets a backfilled row (`from_status=''wont_do'',
  to_status=''retired''`).
- The backfilled rows carry `changed_at = (the migration timestamp)` so
  the audit log makes clear the transition was a schema migration, not a
  user-driven verb call.

**New verb that writes to_status=''retired'':** Core.retire() (D36 / T174
Phase 2b). Same shape as close() / wont_do() at the audit layer — writes
exactly one status_transitions row per call.

**No-op transitions still skipped:** reopen on an already-live row (open
for production/meta; spec for design/schema) is a no-op and does NOT
append a row (D20 no-op guard, partition-aware per Phase 4 fix in
d0f1cf7).

Backs (v0.5 additions): D35 (T167), D36 (T171), T168 (mig 009 backfill),
T174 (retire verb writes the new to_status).', 'spec', 0, '2026-05-31T04:11:47.876797+00:00', '2026-06-03T02:49:05.661959+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (51, 'D9 — Slice fetch', 'Fetch a single task plus its directly-linked context — its dependencies, dependents, and labels — as one small payload. This is what lets the agent read only what a task needs (a step-sized slice) instead of loading a whole monolithic document. The core anti-context-bloat feature.', 'spec', 0, '2026-05-31T04:11:47.876833+00:00', '2026-06-03T08:35:21.038243+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (52, 'D10 — Stale propagation (mark-before-change)', 'Before a task is edited, mark its linked tasks (both endpoints'' view; D6) stale, leaving status untouched. Recording the obligation before the mutation makes an interrupted reconciliation crash-safe: the marks survive a dead session. Propagation is one hop — it does not cascade transitively on its own; it flows only where a real change actually happens (D13).

Bounded obligation (D28 v0.4): the staling WRITE is unchanged — depth-1, both endpoints, regardless of neighbor status. What changed is which stale flags are obligation-bearing. The worklist filter restricts the obligation set to status=''open'' OR kind in {design, schema}; closed and wont_do production/meta neighbors with stale=True are RECORD ONLY (D7 v0.4) and not on the worklist. reconcile() is refused on closed/wont_do — clearing a record-only marker would erase the signal without meaning.

Cascade also bounded by kind (D26): the cascade fires both directions of any link, but the meta-island constraint (D26: a link_add between a meta task and a non-meta task is refused at D14) means the propagation cannot bleed between meta work and spec/production work — there are no links across the kind boundary to traverse. Under D28 the kind boundary still matters for keeping the meta bookkeeping graph clean of production coupling; the cascade-reach-bounding role it played in v0.3.0 is now shared with the worklist filter.

No-op discipline (see D20): staling here fires only on an actual content change — edit field-diffs against the stored value and, if nothing differs, does not stale linked tasks (and does not bump version). This is D10''s instance of the general no-op rule defined in D20.

Edits to closed/wont_do tasks (D29 v0.4): edit() is allowed on any status. The cascade fires depth-1 as usual; closed/wont_do neighbors get stale=True record-only per D28. The description_revisions audit table (S7) records the prior verbatim name+description+delta on every edit, so editing closed prose is recoverable. v0.3.0''s no-edit-closed convention and the supersede verb are retired.', 'spec', 0, '2026-05-31T04:11:47.876865+00:00', '2026-06-05T08:48:30.911092+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (53, 'D11 — Reconciliation worklist', 'List all obligation-bearing stale tasks — the resumable worklist for a reconciliation pass that may outlive a session or context window: a new session asks "what''s stale?" and resumes exactly where the last left off. The pass is done when the list is empty (termination marker).

Under D28 (v0.4 bounded obligation) the worklist is FILTERED: it returns tasks where `status=''open'' OR kind in {design, schema}`. Closed/wont_do production/meta tasks carrying stale=True are RECORD ONLY (D7 v0.4) — the cascade still wrote the flag, but the task is not on the worklist and does not pressure the close-gate. This bounds the obligation so terminal work doesn''t permanently appear unreconciled when its upstream edits later.', 'spec', 0, '2026-05-31T04:11:47.876896+00:00', '2026-06-02T04:47:03.305195+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (54, 'D12 — Close operation with obligation payload', 'Closing a task sets status=closed and returns, in the result, the task''s directly linked tasks (D6) — the one-hop set the agent is obliged to review on close. Under v0.3.0+ symmetric-link semantics there is no "dependencies vs dependents" partition; both directions collapse to a single set of linked neighbors. The obligation rides in the operation''s response, not in a separate instruction the agent might have forgotten.', 'spec', 0, '2026-05-31T04:11:47.876928+00:00', '2026-06-03T08:35:53.334153+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (55, 'D13 — Change operation with cascade entry', 'Editing a task that has links first marks its linked tasks stale (D10), then applies the edit, then returns the now-stale set in the cascade envelope. This is the entry point of the change-time cascade: reconciling each stale task may, if it too changes, mark its own linked neighbors — propagation flows only where real changes occur, and bounded by the kind boundary (D10/D26).

The cascade-entry mechanics are unchanged in v0.4. What v0.4 added is the bounded-obligation worklist filter (D28): the obligation set returned to the caller — and the close-gate''s "transitively linked stale" walk (D14 v0.4) — is restricted to status=''open'' OR kind in {design, schema}. Closed and wont_do production/meta neighbors still get stale=True written mechanically by D10, but they are record-only (D7 v0.4): not on the worklist, not on the close-gate, not reconcilable.

The edit''s required `delta` (one sentence describing the semantic shift) is compared against each surfaced stale link''s `because` rationale (T116) by the agent to filter false positives during reconciliation — the cascade-ergonomics filter. Auto-diff is worthless here; the agent already knows what it did.

Edits to closed/wont_do are allowed under D29 v0.4 — the description_revisions audit table (S7) records the prior verbatim row on every edit, so editing the prose of shipped work is safe and recoverable. v0.3.0''s no-edit-closed convention and the supersede verb are retired. If the edited task has kind in {design, schema}, the envelope includes a code-check reminder (D31) naming the slice id+name and prompting a grep against the code that realizes it (via the code↔task naming convention).', 'spec', 0, '2026-05-31T04:11:47.877035+00:00', '2026-06-05T08:48:30.911092+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (56, 'D14 — Invariant enforcement', 'Refuse operations that would leave the graph inconsistent and surface the violation loudly.

Graph integrity:
- FK: a link to a nonexistent task is rejected.
- No self-link: link_add(a, a) refused by S3 CHECK task_a < task_b.
- No duplicate link: S3 UNIQUE on the canonical pair.

Kind taxonomy (D26):
- Reserved-label refusal: label_add or load with a label string equal to a reserved kind name (design/schema/production/meta) is refused; those four are reserved for the kind property on S1.
- Meta-island constraint: link_add between a meta task and a non-meta task is refused. Keeps the meta bookkeeping graph clean of production/spec coupling (under v0.4 the cascade-reach-bounding role is shared with the worklist filter D28).

Status-change refusals:
- Close-gate (v0.4 bounded per D28): close and wont_do are refused if the task is itself obligation-bearing-stale, OR if it transitively shares a link with any obligation-bearing-stale task. The "transitively linked stale" walk filters to status=''open'' OR kind in {design, schema} — closed/wont_do record-only stale neighbors (D7/D10 v0.4) do NOT trip the gate. The agent must reconcile the named obligation-bearing stale task(s) before closing.
- Design/schema perma-open (D30 v0.4): close() and wont_do() are refused on any task with kind in {design, schema}. Those are living spec, not work items; the verb for updating them is edit() (D29). To "retire" a decision, edit the slice to reflect its current state — the description_revisions audit table (S7) preserves the prior verbatim version.
- No double-decide (T132): close() refused if status is already closed or wont_do; wont_do() refused if status is already closed or wont_do. The change-of-mind path on a wont_do task is to create a fresh task with the new direction.

reconcile refusal (D7 v0.4 + T156 v0.4): reconcile() is refused iff (status closed/wont_do) AND (kind in {production, meta}). The filter mirrors D28''s worklist filter exactly so what surfaces as obligation-bearing is what can be reconciled. Closed/wont_do production/meta stale is record-only historical signal; clearing it would erase the signal without meaning. Closed/wont_do design/schema (legacy pre-D30 rows) IS reconcilable, since design/schema is living spec by kind and reconciling acknowledges the slice still describes truth after the upstream changed.

Under symmetric link semantics (D5) there is no dependency cycle to detect — an undirected edge has no cycle in the directed sense. The v0.2.0 acyclicity check is retired.

## v0.5 update (D35+D36 — status-derived refusals + 5 new refusal classes)

**Pattern shift:** the v0.4 refusal patterns enumerated as
`(status, kind)` predicates are now **status-derived** under D36''s partition.
The kind clause is redundant because kind and status are coupled by the
partition CHECK. Refusal predicates that used `kind IN (''design'',''schema'')`
now use `status=''spec''` (or `status IN (''spec'',''retired'')` for the broader
"design/schema row" cases). See T173 Phase 2a for the 6 call sites swept
in `core.py`; T180 for the `Core.links()` anchor-query residual.

**5 NEW refusal classes added by D36:**

| Op | When refused | Refusal message gist |
|---|---|---|
| `retire(id)` | `status != ''spec''` | "retire is only valid on living specs (status=''spec''); if the decision returned, file a fresh D# instead of reanimating." |
| `retire(id)` | ANY linked neighbor has `status=''open''` | Lists each open neighbor with its `because` rationale and presents the (i)/(ii) decision tree (link_rm+wont_do vs link_rm alone) inline. |
| `link_add(a, b, …)` | Either endpoint has `status=''retired''` | "Retired specs accept no new edges — there is no realization relationship to a dead decision (D36)." |
| `reconcile(id)` | `status IN (''closed'',''wont_do'',''retired'')` | "Reconcile is not allowed on terminal tasks; their stale flag is record-only archaeology (D28 + D36); clearing would erase the historical signal that an upstream changed." |
| `reclassify(id, new_kind)` | Cross-partition kind change with no clean status target | When source status has no partition-valid equivalent on the new kind side (e.g. closed-production → design has no clean target since ''closed'' isn''t in the design partition). |

**Reason-validation refusal (D33 extension, applies to retire):**
- `retire(id, reason='''')` / `''TBD''` / `''TODO''` / `''obsolete''` / `''no longer
  needed''` is refused at the validation boundary — placeholder strings
  carry no signal. See D33''s v0.5 update for the placeholder list.

**Terminal-state generalized (T132 generalization under D36):**
- `reopen / close / wont_do / retire` are ALL refused on a `status=''retired''`
  row — no double-decide on a terminal state. The v0.4 lock on wont_do
  generalizes to wont_do + retired uniformly.
- `Core.reopen()`''s no-op guard ALSO had to be partition-aware: a spec
  row''s "live" status is `spec`, not `open`, so reopen on spec is a no-op
  (was a partition CHECK violation pre-d0f1cf7; pinned by
  test_reopen_on_spec_is_noop). See T176 Phase 4 findings + T173 Phase 4
  fold-back for the scope-gap analysis.

Backs (v0.5 additions): D35 (T167), D36 (T171), T173 (Phase 2a sweep),
T174 (Phase 2b retire verb + new refusals), T176 Phase 4 (the
partition_holds property invariant catches regressions).', 'spec', 0, '2026-05-31T04:11:47.877070+00:00', '2026-06-03T02:49:05.689772+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (57, 'D15 — Task query / board (derived views)', 'List and filter tasks by status, by label, and by stale — e.g. "open tasks in the smoke-test label" or "everything currently stale." The work-queue and any grouping view are queries over the fields, not separately-maintained lists (single source of truth).', 'spec', 0, '2026-05-31T04:11:47.877102+00:00', '2026-06-03T08:35:56.810283+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (58, 'D16 — Narrative render', 'Render the tasks carrying a given label (e.g. design), in id order, into a single readable markdown document — recovering a human-reviewable narrative from the decomposed slices without that narrative becoming a second source of truth. (This very document is the kind of thing it would regenerate.)', 'spec', 0, '2026-05-31T04:11:47.877134+00:00', '2026-06-03T08:35:58.334520+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (59, 'D17 — Full-text search', 'Find tasks by keyword over name + description, ranked, via SQLite FTS5 — in-process, no external service or GPU. Returns matching task ids + titles + scores. This is the agent''s entry point for finding tasks (e.g. the ids to depend on) before fetching a slice; search → show is tackit''s retrieval loop, replacing grep-over-a-monolith. Keyword only; semantic/vector search is explicitly out of scope (a recall optimization with heavy deps). FTS5 is strictly better than grep for this (tokenized, ranked, indexed), but it is only as good as how discoverably tasks are written — see the skill-pack note under "Deferred."

The FTS index carries the synthesized D32 prefix (T161, v0.4): the indexed `name` column is `<kind_letter><id> — <name>` (design→D, schema→S, production→T, meta→M), so `search(''T238'')` and `search(''D23'')` find the right row by id-prefix regardless of whether the stored name carries such a substring. Migration 008 rebuilt the FTS for pre-existing rows.', 'spec', 0, '2026-05-31T04:11:47.877166+00:00', '2026-06-03T08:36:00.027977+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (60, 'D18 — Git-tracked text serialization + safe DB↔SQL sync', 'The git-canonical form of the store is a deterministic SQL text dump (tackit.sql, committed); the binary tackit.db is gitignored and local. Two markers in meta (S6) govern sync: a monotonic version (generation counter, +1 per mutation, embedded in both .db and the dump) gives ordering — "which is newer"; synced_sql_hash gives integrity / exact-identity. A different hash alone does not imply newer — that''s what version is for.
After every mutation: bump version, re-dump tackit.sql (it embeds the new version + content), record synced_sql_hash. The committed file is thus always current — nothing to remember before committing.
On startup, sync decision (db has Vdb + last hash; disk tackit.sql has embedded Vsql + computed hash): no .db yet (fresh clone) → build it from tackit.sql; hash(tackit.sql) == last synced_sql_hash → in sync, trust the .db (covers the normal case and a crash before a dump completes); else the .sql changed externally, decide by version: Vsql > Vdb → strictly newer (a pull) → snapshot .db to a rotating backup (last ~20), then rebuild it from tackit.sql; Vsql < Vdb, or Vsql == Vdb with differing content → ambiguous (older checkout, local .db work not yet exported, or a merge collision) → do not auto-clobber: refuse and direct the agent to resolve explicitly (tackit import to adopt the .sql, or tackit export to write the .db out). version is what makes these dangerous cases detectable rather than guessed.
Net: auto-override only on unambiguous-newer; never a blind diff; backups guard the override path; merges route through import. Commits/PRs are reviewable text and mergeable — resolves issue #9.', 'spec', 0, '2026-05-31T04:11:47.877198+00:00', '2026-05-31T04:11:48.053769+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (61, 'D19 — Built-in stale surfacing (added 2026-05-30)', 'The stale check is code in the app, not advice: every invocation queries the outstanding stale set (D11) and emits it deterministically, before the requested op and again after if the op changed it. CLI emits to stderr (so --json stdout stays clean); MCP wraps every tool result as {stale_alert, result}. The wording is single-sourced in core.stale_alert_text / stale_alert_payload and names the required action (review each stale task against its depends_on neighbors, then edit-or-reconcile) plus the negative fallout. This is the "surface" tier of Enforcement — stronger than a skill reminder (cannot be skipped or compacted away), short of blocking. (Resolves closed-issue #19.)', 'spec', 0, '2026-05-31T04:11:47.877231+00:00', '2026-06-05T08:48:30.911092+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (62, 'D20 — No-op discipline (mutate only on a real change) (added 2026-05-30)', 'A mutating op enters the D18 finalize path (version bump + tackit.sql re-dump) only when the requested end-state actually differs from what is stored — a field-level comparison against the current row, no diff machinery. Applies to every mutating op: edit (no field differs), close/reopen (already in target status), reconcile (already not stale), label_add/label_rm (label already present/absent), dep_add/dep_rm (edge already present/absent). Keeps version (D18''s ordering signal) advancing only on genuine mutations, so redundant calls don''t churn the committed dump or produce a false "newer" signal. (Resolves closed-issue #18.)', 'spec', 0, '2026-05-31T04:11:47.877263+00:00', '2026-05-31T04:11:48.063369+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (63, 'D21 — Label-usage view (added 2026-05-30)', 'labels (CLI tackit labels + MCP tool) lists every label with its usage: task count + a few example task names, most-used first. A label is thus self-documenting through its tasks — its meaning is derived from usage, so there is no label-description to maintain or drift (S2 stays a plain join table; a labels-dimension table with descriptions was deliberately rejected as a second source of truth). This is the "what exists and what does it mean" primitive that makes reuse-before-create possible; it underpins the label-discipline epic (the creation-time anti-sprawl nudge and the skill guidance build on it).', 'spec', 0, '2026-05-31T04:11:47.877302+00:00', '2026-06-01T01:57:16.212740+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (64, 'D22 — Board view (CLI, human-readable) (added 2026-05-30)', 'tackit board renders the state as a human-readable, dependency-aware board in the terminal — grouped (In Flight / Done), colored (open=accent, stale=alert, done=dim; color only on a TTY), each task showing its labels and its needs→ / unblocks→ edges. Filter MODES cover the common questions: default (everything), --status open ("what''s outstanding," the #1 ask), --label X (a group), --stale (after big changes). --json emits the structured slices. CLI-only (the agent uses the structured ls/stale/show tools); it''s the human''s at-a-glance view, the visual cousin of D16 render. (HTML export was considered and dropped — the CLI board + the agent''s change-summaries cover the need.)', 'spec', 0, '2026-05-31T04:11:47.877336+00:00', '2026-06-01T01:57:16.186947+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (65, 'D23 — Label-creation nudge (added 2026-05-30)', 'When an op (add / label_add) creates a brand-new label (one no task carried before), it records an anti-sprawl nudge — the new label plus the labels that already exist — surfaced deterministically by the adapters (CLI stderr; MCP envelope label_nudge), the same un-skippable channel as the stale alert. This puts reuse-before-create in the agent''s face at the exact moment sprawl happens, rather than relying on the skill being remembered. The nudge reflects only the latest op (reset each add/label_add); the meaning of each existing label is recoverable via labels (D21). Completes the label-discipline mechanism (D21 view + D23 nudge + the SKILL guidance).', 'spec', 0, '2026-05-31T04:11:47.877369+00:00', '2026-05-31T04:11:48.078577+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (66, 'D24 — Bulk plan import (added 2026-05-30)', 'tackit load <file> ingests a lightweight, no-dependency plan format (tackit.plan): [key] Name lines with indented kind: / desc: / labels: / depends_on: fields, where depends_on references other keys in the same plan. kind is REQUIRED per row (D26: design | schema | production | meta, T94); a row missing it is refused with the [key] named, and the whole import rolls back. core.load creates all tasks in one transaction (a single version bump), resolving keys→ids in a second pass to wire links; each depends_on line creates a symmetric link in canonical order (D5/T86) — under symmetric semantics there is no cycle in the directed sense, so the v0.2.0 cycle check is retired (T86). Any error — a malformed line, a missing or invalid kind, a duplicate key, an unknown dep key, a self-edge, a reserved-label collision (the four kind names are reserved labels, D26), or a meta-island violation (cross-kind link between meta and non-meta, D26) — fails loud and rolls back the whole import (never a partial plan). This removes the one-call-per-task tax when backfilling an existing project, and is the path the design docs themselves are loaded through (D-slice format nearly conforms; feeds the design-doc backfill).', 'spec', 0, '2026-05-31T04:11:47.877402+00:00', '2026-06-03T08:35:59.286215+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (109, 'D25 — Supersede marker (added v0.3.0)', 'Mark an existing task superseded_by a newer task that replaces its premise. A nullable FK on tasks (S1) — not a separate edge type — because the relationship is one-to-one (a task points at at most one successor) and the marker itself does not participate in close-gate or graph traversal. The supersede(old, by) op sets the marker, refuses self-supersede, and does not auto-close old — supersede and close are independent decisions (close old separately if retiring it; reopen old later without un-superseding).
Why a marker, not just close: a closed task remains in full-text search results (D17). When a later task inverts or replaces the closed task''s premise, a search hit on the closed task silently misleads the reader. The marker tags the hit so search can flag "superseded by T<new>" inline and show can display both directions (this task''s superseder + tasks superseded by this one via reverse lookup). It surfaces the displacement that close alone can''t.
Fires the cascade on old''s links (T124, 2026-06-01): supersede is now a cascade-firing op. Replacing a task''s premise means every linked neighbor must walk the migrate-or-stay decision: link_add(by, neighbor) to migrate the relationship to the replacement (with a fresh because rationale describing the new coupling); link_rm(old, neighbor) if the relationship is fully replaced (or leave both, treating old''s edge as historical record). Closed neighbors stay closed + stale=True per T123''s relaxed D7 — no force-reopen. The cascade obligation rides in the op''s result as newly_stale, mirroring ChangeResult.newly_stale from edit. Empty delta is refused per T117 like edit/link_add/link_rm.
Surfacing: superseded_by rides in the slice envelope of show, ls, board, and search results (not search-only). Search flags each superseded hit with the superseder''s id+name; show includes both directions; ls/board show the marker as a small annotation. Resolves T69.', 'retired', 1, '2026-06-01T01:51:35.831916+00:00', '2026-06-03T08:33:36.697017+00:00', 'design', 'v0.4 retires supersede entirely; the description_revisions audit table (D29 / T138) replaces the marker''s archaeology role under the same task id. The marker mechanism tagged search hits with a superseder pointer to prevent misleading hits on closed tasks whose premise was inverted; v0.4 addresses the same concern by allowing edit-on-closed with verbatim prior-version preservation in an append-only audit table. Simpler model, same archaeology capability. T109''s prose stays as historical record of what v0.3.0 supersede was.');
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (110, 'D26 — Task kind taxonomy (added v0.3.0)', 'Every task carries a required kind in {design, schema, production, meta}, set at create and refused if missing or invalid. The taxonomy splits tasks by whether they alter the running app''s behavior (the classifier rule):

- design: a design.md slice (D#). Decisions, not code. Perma-open under D30 v0.4 (close/wont_do refused on this kind; edit() updates the slice and the description_revisions audit table S7 preserves the prior verbatim version).
- schema: a schema.md table (S#). The store''s shape. Same perma-open rule as design.
- production: code that alters the running app''s behavior, including source under tackit/, the README and SKILL.md (those alter the agent''s behavior of the app), and test code that pins behavior contracts.
- meta: work that does NOT alter the running app — release bookkeeping, experiments, dogfood notes, side-investigations.

Inheritance trap (T122): classify a NEW task by its OWN scope, not by the parent epic''s framing. A task spawned during a meta thread that itself includes impl work is production — not meta — regardless of where the discussion happened.

Meta-island constraint: a link_add between a meta-kind task and a non-meta-kind task is refused (D14). Under v0.4 the load-bearing role of this constraint is NARROWER than v0.3.0 framed: the worklist filter (D28) bounds the OBLIGATION cascade — closed/wont_do production/meta stale is record-only and the worklist excludes non-{open|design|schema} — regardless of which kinds are linked. What the meta-island still does is keep the meta bookkeeping graph clean of production/spec coupling: meta tasks (release notes, observation writeups, dogfood logs) stay structurally separate from the app''s substance, so a release-tracking task can''t show up wired to a production source change. A meta task may link other meta tasks freely; a non-meta task may link any other non-meta task.

Worklist filter scope (D28 v0.4): the worklist''s "status=''open'' OR kind in {design, schema}" rule means design and schema slices stay on the worklist when they go stale even though they don''t close (perma-open D30). Legacy pre-D30 closed design/schema rows that pre-date D30 still surface on the worklist for the same reason.

Reserved label names: the four kind names are reserved as label strings (S2): label_add and load refuse a label with the same string. This prevents the v0.2.0 convention where design/schema were labels — the kind property absorbs that distinction and a stray label would silently disagree.

## v0.5 update (D35+D36 — kind/status partition coupling)

Under v0.4, `kind` was a CLASSIFICATION column — it told you what KIND of
task a row represented (design / schema / production / meta) but it
didn''t directly constrain other columns. Under v0.5, **kind is also
constraint material** for the `status` column via the partition CHECK
on S1 (see S1''s v0.5 update):

| Kind | Permitted status values |
|---|---|
| production, meta | open, closed, wont_do |
| design, schema   | spec, retired |

**Consequence for reclassify (T128):** under v0.5, `Core.reclassify(id,
new_kind, delta)` is a cross-partition move when the kind change crosses
the production/meta ↔ design/schema boundary. The status auto-shifts to
keep the row partition-valid:

- production/meta `open` → design/schema → status becomes `spec`.
- design/schema `spec` → production/meta → status becomes `open`.
- Refused when source status has no clean target (e.g. production
  `closed` → design has no design equivalent of work-done; the agent
  must wont_do/retire first or accept that the closure semantics
  don''t carry across partitions).

See D14''s v0.5 update for the "cross-partition no-clean-target" refusal
class; T168 Phase 1 (commit 3a92904) implemented the auto-shift; T176
Phase 4 (commit d0f1cf7) added the `partition_holds` property invariant
that catches any future regression.

**Meta-island constraint (D26 v0.4) is unchanged:** meta only links meta;
the partition layer is orthogonal. A meta row''s status is open/closed/
wont_do — meta is in the production/meta partition.

Backs (v0.5 additions): D35 (T167), D36 (T171), T168 (Phase 1 — partition
CHECK + reclassify auto-shift), T176 (Phase 4 — property invariant).', 'spec', 0, '2026-06-01T01:51:47.643672+00:00', '2026-06-03T08:36:00.696021+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (111, 'D27 — Link discovery via `links` op (added v0.3.0)', 'Replaces the v0.2.0 "search-before-create" workflow with a deterministic candidate-surfacing loop. The links op: with no input → returns all design + schema kind tasks (the anchor layer — the spec tasks that production work should link to); with a list of task ids → returns every task linked at depth=1 to any input id, filtered to viable link targets, minus the input ids themselves and minus any "already-seen" set the caller passes back. Iteration is caller-driven — tackit holds no state between calls; the agent loops links(anchors), links(next_layer), … until satisfied.

Candidate filter (D27 v0.4 / D28): the expansion-hop output restricts to status=''open'' OR kind in {design, schema}. Closed/wont_do production/meta neighbors are NOT surfaced as candidates — they cannot be productive link targets going forward (the work is done or dropped; coupling to them would be archaeology, not coupling). The anchor-layer query (links() with no input) is unchanged — design/schema slices are perma-open under D30 so the question doesn''t arise there. This filter aligns the surface with the worklist-filter scope (D28) so what you''d reconcile and what you''d link to share the same boundary.

Why this replaces "search-before-create": the dep-discovery experiments (docs/plan/dep-discovery-experiments.md) found that no single retrieval method dominates and that the agent must do the semantic judgment about which surfaced candidates are real links. Search is recall-limited (the J experiment); enumerating the whole tree is context-bloating. The links op surfaces depth-1 anchors deterministically and the agent judges each one — never skipping a surfaced candidate. The semantics live in the agent; the deterministic surfacing lives in tackit.

Discovery flow (per SKILL.md): (1) classify the new task''s kind; (2) for production, call links with no input → judge the design+schema anchor layer; (3) call links(judged_anchors) for depth-1 expansion; (4) judge that layer; (5) iterate or stop; (6) wire via link_add with a real because rationale describing the coupling. For meta, scan within the meta-island only. For design/schema, scan within the same layer.

## v0.5 update (D35+D36 — status-derived candidate filter; retired excluded)

The `links()` op''s candidate filter — which rows are surfaced as viable
link targets — has been **rewritten from kind-conditional to
status-derived** under D35+D36:

| Era | Candidate filter (expansion hop) |
|---|---|
| v0.4 | `status=''open'' OR kind IN (''design'',''schema'')` |
| v0.5 | `status IN (''open'',''spec'')` |

The two forms are equivalent on live data (design/schema live at spec
under D36), but the v0.5 form correctly **excludes retired design/schema
rows** — they''re dead specs, not viable anchors for new realization links.

**Anchor layer (no-input mode) also filtered (T180):**
- T173 Phase 2a updated only the expansion-hop predicate; the anchor
  query (`links()` with no input → returns all design+schema rows) was
  left as a kind-only filter, allowing retired rows to surface as
  "anchors for new work" — an inconsistency.
- T180 (commit 895679f) added `AND status IN (''open'',''spec'')` to the
  anchor query so both branches apply the same predicate.
- The anchor query still retains the `kind IN (''design'',''schema'')` clause
  as an EXPLICIT semantic — the anchor layer IS the live spec layer for
  production work to link to.

**`link_add` retired-endpoint refusal (D36):**
- `Core.link_add(a, b, …)` is refused if either endpoint has
  `status=''retired''`. Retired specs accept no new edges — there is no
  realization relationship to a dead decision. Prior content lives in
  description_revisions (D29).

See T173 Phase 2a (expansion-hop predicate), T180 (anchor-query
residual), T174 (link_add retired-endpoint refusal), and T176 Phase 4
property invariant `links_anchor_excludes_retired` which catches any
regression where retired rows leak into the anchor layer.

Backs (v0.5 additions): D35 (T167), D36 (T171), T173 (Phase 2a sweep),
T180 (anchor residual), T174 (link_add retired refusal), T176 (Phase 4
invariant).', 'spec', 0, '2026-06-01T01:52:06.009742+00:00', '2026-06-03T02:49:05.731373+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (133, 'D7 — Status + stale flag (v0.4 bounded-obligation)', 'A task''s status is one of {open, closed, wont_do} — informational only; it never gates traversal. A separate stale flag marks a task as one whose linked neighbors changed under it.

Bounded obligation (D28 v0.4): the cascade still writes stale=True on neighbors mechanically (depth-1, both endpoints per D6 symmetric semantics, regardless of status). What v0.4 changed is which stale flags carry an obligation. The obligation worklist filter restricts to status=''open'' OR kind in {design, schema}.

Closed and wont_do production/meta tasks carrying stale=True are RECORD ONLY: the flag stays as historical signal that an upstream changed (visible via show()), but they are not on the worklist and do not pressure the close-gate (D14 v0.4). reconcile() is refused on closed/wont_do PRODUCTION/META — clearing a record-only marker would erase the signal without meaning.

Closed and wont_do DESIGN/SCHEMA tasks (T156 v0.4 refinement, 2026-06-02): are obligation-bearing per D28''s kind clause AND reconcilable. The original v0.4 D28 spec admitted them onto the worklist (kind∈{design,schema}) but refused reconcile on terminal status across all kinds — leaving legacy pre-D30 closed-design/schema rows pinned on the worklist with no exit path. T156 resolves the contradiction by mirroring the reconcile-refusal filter to the worklist filter: reconcile is refused iff (status closed/wont_do) AND (kind in {production, meta}). Design/schema slices stay reconcilable regardless of status — consistent with their "living spec" framing — so the worklist can drain.

Status semantics: open = work outstanding; closed = work shipped; wont_do = scope dropped with durable reason. Status transitions are recorded in status_transitions (S4). edit() is allowed on any status under D29 v0.4 — the description_revisions audit table (S7) preserves the prior verbatim name+description+delta on every edit, so editing closed prose for fix-ups no longer destroys history. v0.3.0''s no-edit-closed convention is retired; the supersede verb is gone.

The no-op rule (D20) and the close-gate (D14 v0.4: close/wont_do refused if the task is itself obligation-bearing-stale, or transitively shares a link with one — closed/wont_do record-only stale neighbors do not trip the gate) are unchanged in their respective scopes.

## v0.5 update (D35+D36 — 5-value taxonomy + partition rule)

D7''s three-value taxonomy (open, closed, wont_do) is **extended to five
values under v0.5**: open, closed, wont_do, spec, retired. The new values
are partitioned by kind:

| Kind | Live status | Terminal-shipped | Terminal-dropped |
|---|---|---|---|
| production / meta | `open` | `closed` (work done) | `wont_do` (scope dropped) |
| design / schema   | `spec` (living decision) | — | `retired` (decision 100% gone) |

**The partition is enforced by the schema-level CHECK** on S1 (see the v0.5
update on S1/T37): a production row cannot have status=''spec''/''retired'';
a design row cannot have status=''open''/''closed''/''wont_do''. Core.add(),
Core.load(), and Core.reclassify() apply the partition-default at row
creation / kind change so a fresh design slice lands at ''spec'' by default
(commit 3a92904 Phase 1).

**Perma-open framing (the v0.4 D30 mechanism) is retired by D35+D36:**
- Pre-v0.5, design/schema slices used the ''open'' status with a refusal at
  close/wont_do that called them "perma-open" — i.e. status was the same
  shape as production/meta but the verbs were blocked at the boundary.
- Under v0.5, design/schema slices use the dedicated `spec` status —
  perma-openness IS encoded by the partition. The close/wont_do refusal
  is now a status-derived predicate (`status=''spec''` is refused), and a
  spec row can be retired via the new `retire()` verb (D36) when 100%
  abandoned with no replacement.
- The historical D30 framing is retained (see D30''s own v0.5 update for
  the redirect note) but the canonical statement of the rule lives in
  D35+D36 going forward.

**stale flag (unchanged shape, new partition semantics):**
- The flag itself is still a single BOOLEAN on every row regardless of
  status. The bounded-obligation D28 framing under v0.5 says: stale
  carries obligation iff `status IN (''open'',''spec'')`. Stale on
  closed/wont_do/retired rows is **record-only archaeology** — the
  worklist filter excludes them; the close-gate doesn''t trip on them.
- See D28''s v0.5 update for the worklist-filter predicate change
  (status=''open'' OR kind IN (design,schema)  →  status IN (''open'',''spec'')).

Backs (v0.5 additions): D35 (T167), D36 (T171), T168 (Phase 1 schema +
partition CHECK), T173 (Phase 2a worklist/refusal predicates).', 'spec', 0, '2026-06-01T06:06:38.590496+00:00', '2026-06-03T02:49:05.745344+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (137, 'D28 — Bounded-obligation cascade: closed-stale as record-only', 'Cascade still writes stale=True on linked neighbors depth-1 on edit/reclassify (unchanged). What changes is OBLIGATION: closed and wont_do tasks carrying stale=True are RECORD ONLY — visible in show, not on the stale() worklist, not triggering the close-gate. Worklist filter: status=''open'' OR kind in {design,schema}. Close-gate''s transitive ''linked to stale'' walk uses the same filter. Reason: T120''s pre-ergonomics data (7 stale / 5 rubber-stamps / 1 true-positive / 0% FAST) showed cascade-through-closed was paying its cost without delivering its payoff. The bounded model preserves the dependency-check value (catches schema-mediated coupling) while eliminating recursive-supersede-through-history.

## v0.5 update (D35+D36 — status-derived worklist filter + retired record-only)

**Worklist-filter predicate change** (the central D28 invariant):

| Era | Worklist predicate (stale + obligation-bearing) |
|---|---|
| v0.4 | `stale=1 AND (status=''open'' OR kind IN (''design'',''schema''))` |
| v0.5 | `stale=1 AND status IN (''open'',''spec'')` |

The two predicates are **equivalent for live data** under the D36 partition
(design/schema rows live at status=''spec''; production/meta at ''open''), but
the v0.5 form correctly **excludes retired design/schema rows** which the
v0.4 form would have wrongly kept on the worklist via the kind clause.

**Record-only stale set is now 3 statuses (was 2):**

| Era | Statuses where stale=true is record-only (NOT obligation) |
|---|---|
| v0.4 | closed, wont_do (production/meta only) |
| v0.5 | closed, wont_do, **retired** |

A retired row that gets cascade-staled by a downstream edit keeps stale=1
as historical signal that an upstream changed — but the flag does NOT
pressure the worklist (the agent is not obliged to revisit a retired
decision) and does NOT trip the close-gate on linked neighbors (a closed
production task with a linked retired-design neighbor can still close).

**Close-gate transitive walk (D14) honors the same filter:**
- `_stale_linked_transitive()` collects obligation-bearing stale tasks in
  the neighborhood: `status IN (''open'',''spec'')` only. Closed-stale-production
  and retired-stale-design are walked-through but not collected. The
  close-gate refuses iff the collected list is non-empty.

**`reconcile()` follows the same partition** (D28 + T156 v0.4 + D36 v0.5):
- Refused when `status IN (''closed'',''wont_do'',''retired'')` — clearing the
  flag on those rows would erase the historical archaeology.
- Allowed on `status IN (''open'',''spec'')` — these are the rows for which
  stale carries obligation and reconcile clears it after review.

See T173 Phase 2a for the implementation sweep (the 6 call sites that
realize this filter in core.py) and T180 for the `Core.links()` anchor-
query residual. T176 Phase 4 added a property invariant
(`worklist_filter_holds`) that re-checks `status IN (''open'',''spec'')` after
every randomized op so a future regression on the filter shape gets caught
in CI.

Backs (v0.5 additions): D35 (T167), D36 (T171), T173 (Phase 2a sweep),
T180 (anchor-query residual), T176 Phase 4 (property invariant).', 'spec', 0, '2026-06-01T22:22:03.373143+00:00', '2026-06-03T02:49:05.759524+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (138, 'D29 — Description revisions audit table', 'Append-only audit table capturing every successful edit''s prior name and description plus the delta rationale. Backstop for the ''edit on closed/wont_do is allowed'' decision: descriptions can be updated in place but verbatim prior state is recoverable for archaeology. Replaces the v0.3.0 supersede marker (D25) — the marker addressed ''don''t get misled by old prose'' via inline-tagging hits with a superseder id; the audit table addresses it by preserving the prior verbatim version under the SAME task id. Simpler model, same archaeology capability. history() op extends to return description_revisions alongside status_transitions.

## v0.5 update (D35+D36 — retire interaction with audit table)

`retire()` is a **status-change verb**, not a content-edit verb — same
shape as `close()` and `wont_do()` at the audit layer:

- **No `description_revisions` row is appended** on retire. The audit
  table tracks edits to the verbatim `name` + `description` columns;
  retire doesn''t touch those. (An edit() called BEFORE retire() to
  refine the slice''s content WOULD append a revision row — but that''s
  the edit''s work, not retire''s.)
- **The durable retire `reason` is written to the `wont_do_reason`
  column**, NOT to description_revisions. The column is reused under
  D36''s partition semantics (see S1 v0.5 update): wont_do_reason holds
  "why is this row terminal" — the verb that wrote it is implied by the
  row''s status (`status=''wont_do''` → wont_do reason; `status=''retired''`
  → retire reason). One column, partition-disambiguated.
- The retire reason is immutable post-write — no edit API on
  wont_do_reason regardless of which verb populated it.

**edit-on-retired is still allowed** (D29 v0.4 backstop, unchanged under
v0.5):
- Editing a retired row''s name/description appends a description_revisions
  row exactly as for any other edit.
- The cascade fires depth-1 (record-only on retired neighbors per D28''s
  v0.5 update).
- D31''s code-check reminder also fires on retired edits (see D31 v0.5
  update — "verify no lingering code references this dead decision").

Backs (v0.5 additions): D36 (T171 — retire verb shape), T174 (Phase 2b
retire impl writes wont_do_reason without touching description_revisions),
S1 (T37 v0.5 — wont_do_reason dual-role partition semantics).', 'spec', 0, '2026-06-01T22:22:03.373244+00:00', '2026-06-03T02:49:05.773391+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (139, 'D30 — Design/schema as perma-open: kind-based close + wont_do refusal', 'close() and wont_do() refuse on any task with kind in {design, schema}, structured error directing the user to edit() (audit table preserves prior state). Design and schema slices are LIVING SPEC — they represent decisions in effect; updating a decision is edit, not close. Retiring a decision means editing the slice to reflect ''no longer in effect''; the audit table preserves the prior version. Belt-and-suspenders: the worklist filter (D28) is robust to any design/schema task that somehow ends up closed (via tests or migration shim) — the kind clause keeps them visible regardless of status.

## v0.5 update (D35+D36 — redirect to canonical framing)

**D30''s perma-open framing has been superseded by D35+D36** as the canonical
statement of the rule. The principle ("design/schema slices are living
spec, not work items — they don''t ''finish''") is unchanged. The
**mechanism** has shifted from "refuse close/wont_do on kind in
{design,schema}" to a **typed status partition**:

- **D35 (T167)** — design/schema slices live at status=''spec''. The status
  IS the perma-open property, encoded in the partition rather than as a
  refusal at the boundary.
- **D36 (T171)** — adds status=''retired'' as the terminal state for fully
  abandoned spec, the `retire()` verb to reach it, the kind/status
  partition CHECK, and the all-or-nothing edit/retire discipline.

**What D30 still tells you (and what to read instead):**

| If you came here for… | Read this slice instead |
|---|---|
| "Why design/schema can''t be closed/wont_done" | D35 + D36 (partition; close/wont_do refused on status=''spec'') |
| "What replaces close for a design decision" | D36 retire() verb (only valid on status=''spec'', refused on open neighbor, etc.) |
| "What happens to legacy wont_do design rows" | Mig 009 (T168 Phase 1, commit 3a92904) migrates them to status=''retired'' with wont_do_reason preserved |
| "How is perma-open enforced now" | Schema-level partition CHECK on S1 (see S1 v0.5 update); core.py status-derived refusals (T173 Phase 2a) |

**D30''s role going forward:** historical anchor + navigation pointer. The
principle statement still holds; the implementation now lives elsewhere.
Future edits to the perma-open RULE belong in D35/D36; D30 stays put as
the spec-graph entry point that explains "why we ended up with a typed
partition for design/schema status".

Backs (v0.5 additions): D35 (T167), D36 (T171), T168 (Phase 1 — partition
+ mig 009), T173 (Phase 2a — status-derived refusals).', 'spec', 0, '2026-06-01T22:22:03.373312+00:00', '2026-06-03T02:49:05.787429+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (140, 'D31 — Code-check reminder on design/schema edit', 'When edit() succeeds on a task with kind in {design, schema}, the response envelope includes a structured reminder: ''this slice''s number (D#/S#) is referenced in code by convention; double-check the associated files for drift.'' Replaces agent-instinct (which fails — SKILL.md guidance alone doesn''t reliably fire) with tool-side mechanical nudge. No code-introspection; tackit doesn''t know which files reference D# — the reminder names the slice id+name and trusts the agent to grep. Sibling concept to label_nudge and stale_alert.

## v0.5 update (D35+D36 — reminder fires on retired edits too)

D31''s trigger predicate is `kind IN (''design'',''schema'')`, which under
D36''s partition includes BOTH `status=''spec''` (live spec) AND
`status=''retired''` (dead spec). The reminder fires on edits to either —
unchanged at the predicate level, but the FRAMING differs by status:

| Edited row''s status | Framing for the agent |
|---|---|
| `spec` | "The slice''s D#/S# id is referenced in code by convention (SKILL.md code↔task naming rule). Grep for the id and check the associated files for drift." |
| `retired` | Same grep nudge, but the cleanup question: "this is a dead decision — verify no lingering code references it. If references remain, either the references are wrong and need rewriting, or the decision wasn''t actually 100% gone and retire was premature (consider supersede with a fresh D#)." |

Both cases use the SAME reminder text in the response envelope today (the
text doesn''t branch on status); the agent applies the right interpretation
based on the slice''s status. A future refinement could specialize the
wording, but the deterministic grep step is what carries the value either
way — the framing is for the human reading the slice, not the trigger
logic.

**Why retired edits still fire:**
- Description revisions ARE allowed on retired rows (D29 backstop) so
  archaeology of the retired prose stays recoverable.
- A retired row that gets re-edited may indicate cleanup work (renaming
  references, removing dead paths) — the grep nudge is helpful for that.
- A retired row that gets re-edited may indicate the agent is mistakenly
  trying to "reanimate" — D31 + the surrounding context surface that
  the slice''s status is retired, so the agent can recognize the mistake.

Backs (v0.5 additions): D35 (T167), D36 (T171), D29 (edit-on-retired
allowed), T173 (Phase 2a — D31 predicate unchanged, noted in T173''s
6-call-site table as "No change needed").', 'spec', 0, '2026-06-01T22:22:03.373374+00:00', '2026-06-03T02:49:05.801742+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (141, 'S7 — description_revisions', 'Append-only audit table backing D29. Columns: id (INTEGER, PK); task_id (INTEGER, FK -> tasks.id); prev_name (TEXT, NULL — pre-edit name, NULL if unchanged); prev_description (TEXT, NULL — pre-edit description, NULL if unchanged); delta (TEXT, NOT NULL — rationale from the edit op); edited_at (TIMESTAMP, NOT NULL); rows never updated or deleted. Written by core.edit() on every edit that actually changes name or description (no-op edits skipped per D20). Read by core.history(). Migration 007 adds this table.', 'spec', 0, '2026-06-01T22:22:03.373434+00:00', '2026-06-03T02:49:05.816435+00:00', 'schema', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (156, 'v0.4 gap: legacy closed/wont_do design/schema slices stuck on worklist (no reconcile path)', '**RESOLVED** by reconcile()-mirrors-worklist refinement (v0.4 cluster commit 583df90, code lives at core.py:820-852).

**Gap** (surfaced 2026-06-01): D28''s worklist filter admitted a stale task as obligation-bearing when `status=''open'' OR kind in {design, schema}`, which pulled closed/wont_do design/schema slices onto the worklist. But reconcile() refused unconditionally on all closed/wont_do tasks. The refusal message claimed "(not on the worklist)" while the worklist explicitly listed them. The two rules contradicted, leaving 5 legacy rows pinned on the worklist with no exit path (T37, T44, T49, T59, T109 in the dogfood DB at the time).

**Resolution** (option B from the original three-candidate analysis): reconcile()''s refusal condition was tightened to mirror D28''s worklist filter exactly:

> `reconcile()` is REFUSED iff (status in {closed, wont_do}) AND (kind in {production, meta}).

Equivalently: reconcile is allowed iff `status=''open'' OR kind in {design, schema}` — the same predicate D28 uses to define the worklist. So what surfaces as obligation-bearing is exactly what can be reconciled, by construction.

For closed/wont_do **design/schema** (perma-open in spirit per D30 but legacy-closed pre-D30), the slice IS the obligation: reconciling it acknowledges the slice still describes truth after the upstream change. For closed/wont_do **production/meta**, the original D28 rationale stands: their stale flag is record-only archaeology — clearing it would erase the historical signal that an upstream changed.

**Why not (A)** tighten the worklist filter to just `status=''open''`: would hide legitimate spec drift on closed design/schema slices, which are still the live contract under D30. The legacy 5-row population would silently carry stale=True forever and the agent would never review it.

**Why not (C)** data migration to reopen all legacy closed design/schema rows: mutates historical close timestamps and adds noise to status_transitions; option B handles the same population without rewriting history.

D30 prevents NEW closed design/schema rows going forward; the reconcile mirror handles the legacy population. The contradiction between alert and reconcile is gone — they share one predicate.

## v0.5 update (D35+D36 — D156 exception obviated by partition)

D156''s reconcile-refusal mirror carved out a kind-exception for legacy
design/schema rows that were `closed` or `wont_do` (pre-D30 history): those
rows COULD still be reconciled because the spec slice IS the obligation,
even from a terminal status. The exception was a v0.4 workaround for the
"legacy population predates the perma-open rule" problem.

**Under D35+D36, the exception is OBVIATED for two reasons:**

1. **Migration 009 (T168 Phase 1, commit 3a92904) migrates the legacy
   population out** of the closed/wont_do design partition: every legacy
   `kind=''design'' AND status=''open''` row moves to `status=''spec''`; every
   legacy `kind=''design'' AND status=''wont_do''` row moves to
   `status=''retired''`. After mig 009, no closed-design / wont_do-design
   rows exist.
2. **The schema-level partition CHECK on S1 refuses creating new
   closed/wont_do design rows.** Any future attempt to set
   `kind=''design'' AND status=''closed''` at the DDL layer fails with an
   IntegrityError. The state D156 carved an exception for cannot recur.

**Reconcile refusal predicate under v0.5 (D28 + D36 + T156 carried forward):**

| Era | Predicate (refused iff true) |
|---|---|
| v0.4 (D156) | `(status IN (''closed'',''wont_do'')) AND (kind IN (''production'',''meta''))` — the kind clause was D156''s carve-out |
| v0.5 (T173 Phase 2a) | `status IN (''closed'',''wont_do'',''retired'')` — partition makes the kind clause redundant; retired added |

The new predicate has the SAME effective behavior for live data (D156''s
carve-out applied to a population that no longer exists post-mig-009) but
the form is simpler and correctly refuses on retired status.

**Test impact:** the 5 D156-mirror tests in test_v04_bounded.py were
skipped during Phase 1 because the scenarios they exercised (closed-design
on the worklist via raw UPDATE) cannot exist under v0.5. T176 Phase 4
rewrote them with the new spec-only semantics — see T176 task body for
the rewrite list.

Backs (v0.5 additions): D35 (T167), D36 (T171), T168 (mig 009), T173
(Phase 2a — predicate simplified to status-derived form), T176 (Phase 4
— 5 D156-mirror tests rewritten).', 'spec', 0, '2026-06-01T23:30:39.270432+00:00', '2026-06-03T02:48:30.168180+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (160, 'D32 — Auto-id task name prefix (deterministic display + FTS)', 'Every task carries a deterministic `kind_letter + task_id` prefix in its agent-facing identifier. Letters: design→D, schema→S, production→T, meta→M. The id is the task row''s primary key (auto-increment), not a separately-managed slot.

The prefix is **synthesized by tackit**, not stored in the user-supplied `name` field:
- User passes a bare name to add() / edit(): "Fix ls() status filter to accept wont_do".
- Tackit''s display layer (show / ls / board / render / change-report formatters) emits `<kind_letter><id> — <name>`: "T157 — Fix ls() status filter to accept wont_do".
- Tackit''s FTS index stores the synthesized prefix as part of the indexed text so that `search("T238")` and `search("D23")` resolve to the right row even when the user-supplied name doesn''t contain that string.

Why deterministic-synthesis instead of stored prefix in `name`:
- Keeps the `name` column small and free-form (no edit-time rules about preserving a prefix).
- Avoids two sources of truth for the prefix (it''s computed from kind+id, both of which already exist).
- Makes the convention bulletproof — no user mistake can produce a name with the wrong prefix or a duplicate prefix; the prefix is derived, not stored.

Why id-based for all four kinds (not slot-based for D/S):
- The slot-vs-id duality on D/S today (D7 lives at task id 133) is historical — slot numbers were manually assigned in spec-doc order. Going forward, the row id IS the identifier; one number, one rule.
- Eliminates the entire collision-on-spec-slot category (cf. T141 named "S6 — description_revisions" colliding with T42 "S6 — meta").
- One sentence rule: "name display is `<kind_letter><id> — <name>`".

What this does NOT do:
- **Does NOT retroactively rename existing D#/S# tasks** (per user directive 2026-06-01: too disruptive; would break every D7/S1/D26 reference in code comments, SKILL.md, etc.). Existing tasks keep their manually-assigned slot prefix as part of the stored `name` field. Their synthesized display becomes the doubled form (e.g., T133 displays "D133 — D7 — Status + stale flag (v0.4 bounded-obligation)") — verbose but unambiguous; the cost of grandfathering.
- Does NOT enforce that the synthesized prefix is *prepended* to the user-supplied name at write time. The stored name stays bare; the prefix is applied at display + index time only.

Edge cases:
- User-supplied name that already looks prefixed (e.g., user writes "T157 — Fix ls() …" out of habit): allowed as raw text, but display will double-prefix to "T157 — T157 — Fix ls() …". A future refinement could refuse or strip; v0.4 just lets users learn the convention.
- FTS rebuild for legacy rows: the search-by-synthesized-prefix property requires the FTS index to carry the prefix even for rows inserted before this slice landed. Migration 008 rebuilds tasks_fts with the synthesized prefix included.

Backs: D17 (search) — search must find by synthesized prefix. D26 (kind taxonomy) — the kind letter comes from kind.', 'spec', 0, '2026-06-02T01:23:31.673423+00:00', '2026-06-03T05:12:38.596578+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (163, 'D33 — Link creation requires explicit `because`: refuse placeholder rationales at all creation paths', 'Every link-creation path in tackit must require a real, caller-supplied `because` rationale. Placeholder/convenience-default rationales (e.g. `"(established at task creation)"`, `"(established via bulk load)"`) are refused at the boundary.

**Why:** the cascade-ergonomics filter (T116) compares `delta × because` to FAST-skip a stale neighbor without re-reading it. A placeholder rationale carries zero signal — it filters nothing, so every cascade hit through such an edge must take the slow path. Convenience shortcuts that ship placeholders silently corrupt the SNR of the entire cascade system: the link LOOKS wired but is functionally dead-weight to the reconciler. Concretely: of 312 links in this DB at the time of writing, 269 (86%) carry a placeholder or pre-T116 marker; the 36 add-deps placeholders are the recurring failure mode this rule prevents.

**Scope of the rule:**
- `link_add(a, b, because, delta)` — already refused on empty `because` (T116). Unchanged.
- `add(name, kind, deps=...)` shortcut — currently hardcodes `because="(established at task creation)"` for each dep edge (core.py:336). Must refuse OR change the API to take per-dep rationales (e.g. `deps: list[tuple[int, str]]` or a parallel `because_per_dep` map).
- `load(plan)` bulk-import — currently hardcodes `because="(established via bulk load)"` (core.py:397). The plan format must carry a per-link `because`; refuse the import if any link entry omits one. Bulk-load is a high-leverage placeholder source: one import can wire dozens of meaningless edges in a single op.
- Any future op that creates links must go through the same contract.

**Out of scope:**
- Retroactively re-rationalizing the 233 pre-T116 marker links + 36 add-deps placeholders + (n) bulk-load placeholders. Existing markers stay as historical record of "we don''t know why this was linked"; a separate backfill task can address the high-value subset (e.g. the 89 edges touching design/schema + an open endpoint). Editing an old link''s `because` is a future API question.
- Detecting "vague" but non-placeholder rationales (e.g. "setup", "test fixture"). That''s a rationale-quality judgement, not a refusal rule — a vague rationale is worse than a placeholder for the human reading it but is at least caller-asserted intent.

**Why not just delete the shortcuts?** The shortcuts are ergonomically valuable when their rationales are real — adding a task and wiring its links in one call is a common pattern. Keep the shortcuts; tighten their contract.

Backs: D14 (invariant enforcement — D33 extends the refusal taxonomy from graph-integrity / kind-taxonomy / status-change refusals to rationale-quality refusal), T116 (which established the per-edge `because` field but didn''t lock down all creation paths).

## v0.5 update (D36 — extends to retire() reason field)

D33''s placeholder-rationale refusal now covers **three persistent
rationale fields** uniformly:

| Field | Set by | Refused placeholders |
|---|---|---|
| `links.because` | link_add (T164) | empty, whitespace-only |
| `tasks.wont_do_reason` (wont_do verb) | wont_do (T132) | empty, whitespace-only |
| `tasks.wont_do_reason` (retire verb) | retire (T174) | empty, whitespace-only, ''TBD'', ''TODO'', ''obsolete'', ''no longer needed'' (case-insensitive) |

**Why retire''s list is longer than wont_do''s:**
- The wont_do reason carries information that''s often pragmatic ("redundant
  with T_other", "blocked by missing dependency"). The empty/whitespace
  check is enough for signal-carrying rationale.
- The retire reason persists FOREVER on a decision that''s been declared
  100% gone. Placeholder strings (''obsolete'', ''TBD'') signal that the
  agent didn''t actually have a real reason and was just trying to clear
  the slice — exactly the failure mode retire''s all-or-nothing discipline
  exists to refuse. The expanded blocklist catches the common
  placeholders empirically observed during dogfooding.

**Implementation:** see T174 Phase 2b — Core._RETIRE_PLACEHOLDERS is the
frozenset constant; the check is `reason.strip().lower() in
_RETIRE_PLACEHOLDERS`. Pinned by 10 parametrized test cases in
`tests/test_d36_retire.py::test_retire_refused_on_placeholder_reason`.

Backs (v0.5 additions): D36 (T171 — defines the retire reason
requirement), T174 (Phase 2b retire impl with the placeholder check).', 'spec', 0, '2026-06-02T03:07:55.251363+00:00', '2026-06-03T02:49:05.844236+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (165, 'D34 — Surface cascade-ergonomics rationales (link `because` + upstream edit `delta`) with a DRY FAST-filter reminder', 'The cascade-ergonomics FAST filter (T116/T117) compares an upstream''s `delta` (semantic shift) against a link''s `because` (coupling axis) to skip re-reading a stale dependent when the two don''t intersect. Both inputs are persisted today — `because` on `links` (T116), edit `delta` in S7 `description_revisions` (D29 v0.4) — but **neither is surfaced** in the show/board envelopes or in the stale_alert payload. So the FAST filter is operationally unreachable: the data exists; the agent can''t see it during a reconciliation walk.

**The rule:**
On any envelope that includes one or more dependency entries (currently `show`, `board`) where at least one of those entries has `stale=True`, the envelope MUST surface:

1. **Per dep entry: the link''s `because`** — read from `links.because` for the canonical (a, b) pair.
2. **Per dep entry: the upstream''s most-recent edit `delta`** — read as the `delta` from the most recent `description_revisions` row whose `task_id` is the dependency''s id. Null if the upstream has never been edited.
3. **Top-level `because_reminder` field** — a single DRY-sourced string explaining what those two fields are for. Same envelope position as `code_check_reminder` / `label_nudge` / `stale_alert` / `delta` (the per-op fields). Null when the trigger condition (≥1 stale dep entry) is not met.

**The reminder string (single source, exported constant — orientation-first, post-empirical revision 2026-06-02):**
> *"Each link''s `because` describes WHY the two tasks are coupled. Each cascade-firing op''s `delta` describes the upstream''s semantic shift. Read both BEFORE opening a stale dependent — they tell you the specific aspect to check. In the rare case where the shift doesn''t intersect the coupling axis at all, you can `reconcile` without re-reading the dependent (FAST path); otherwise re-read and edit-or-reconcile (SLOW path with the question pre-formed)."*

**Why orientation-first, not skip-first** (2026-06-02 empirical finding from the in-session cascade walk + the 89/7-edge backfills): in practice, on hub-spec edits (the dominant case), the delta lands in the coupling axis the because names — so the FAST skip rarely applies (~6% of events in our sample). The dominant value is **orientation**: the agent gets handed a specific question to evaluate against the dependent''s prose, rather than having to derive it. Even when the answer is "no, the prose is generic enough," the SLOW path is faster because the question is pre-formed. The reminder is written to put orientation as the primary action and FAST-skip as the rare exception, matching the empirical pattern.

DRY: the reminder text is defined exactly once (`core.LINK_BECAUSE_REMINDER`); referenced by every emission site. No duplication, no per-site drift.

**Trigger** — emit the reminder iff the result envelope includes at least one stale dependency entry (either direction; under symmetric-link semantics dependencies == dependents). Not on every show (noisy on read-only browsing); not gated on top-level `stale_alert` non-null (misses cases where the global worklist is empty but this particular task has a stale neighbor).

**Surfaces in scope:**
- `show` envelope — both `dependencies` and `dependents` lists get the two new fields per entry; top-level `because_reminder` per trigger.
- `board` envelope — routes through `show` per-slice (mcp_server.py), so it inherits the same fields automatically.
- `stale_alert` payload — for each stale task listed, ideally also include its upstream(s) + because + delta. But this requires schema-level work to identify "which upstream(s) staled me" (currently not tracked). **Out of scope for this slice**; pin to a follow-up.

**Known partial coverage:**
`reclassify()` is a cascade-firing op (per skill) that does not write S7 (it doesn''t change name/description). Its `delta` is ephemeral — gone after the op''s response envelope drops from the agent''s context. Same for any future cascade-firing op that doesn''t go through edit''s S7-writing path. In those cases, the surfaced `last_edit_delta` field is null; the agent falls back to SLOW-path re-reading without orientation. Acceptable, because (a) reclassify is rare in normal work, (b) the FAST filter still works for the common edit() case which is the majority of cascade events.

**Non-edge contexts:** the `links()` op (D27) returns candidates not tied to a single edge from the input ids'' perspective, so the per-edge fields (`because`, `last_edit_delta`) are None on those NeighborRefs. Documented on the model.

**Why this isn''t D33''s job:**
D33 enforces rationale QUALITY at link creation. D34 surfaces the rationales AND deltas so the FAST/orientation pipeline can actually run. They''re complementary: D33 makes the data worth surfacing; D34 makes it accessible.

Backs: D9 (slice fetch — adds two fields to its dep entries + one top-level field), D11 (reconciliation worklist — same trigger condition''s home concept), D29 (description_revisions, the source of persisted deltas), T116 (per-edge because), T117 (delta concept).', 'spec', 0, '2026-06-02T03:19:44.306327+00:00', '2026-06-05T20:34:22.863021+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (167, 'D35 — Spec status value: design/schema slices use status=''spec'' (retires the kind-conditional perma-open framing)', '**The problem D35 fixes** (surfaced 2026-06-02): the current model conflates two orthogonal things in the `status` column — lifecycle (open/closed/wont_do) and kind-driven liveness (design and schema are perma-spec, not work items). Counts of "open" mislead because they include perma-spec slices that aren''t work items, and every report has to caveat: "of N open, only M are actual work items." That caveat is a structural tax on every count, every dogfood pass, every report. The mechanics work correctly — worklist filter, close-gate, reconcile refusal all do the right thing — but the framing is confusing and propagates user-visible noise.

**The decision:** add a new status value `spec`. Design and schema slices live at `status=''spec''` permanently. `open` regains its plain meaning ("work item to do"). The kind-conditional clauses in the worklist filter, close-gate, wont_do refusal, and reconcile refusal collapse into clean status-derived predicates.

**Scope split (2026-06-02 brainstorming):** D35 covers the spec-status changeover only. The retired status, retire() verb, kind/status partition rule, all-or-nothing edit/retire discipline, and propagation principle live under NEW sibling D36 — split out because D35 originally didn''t account for fully-abandoned specs (the gap surfaced when D109/D25 was reviewed for mig 009). D35 + D36 together complete the design/schema lifecycle.

**Predicate simplifications (D35 spec half):**

- **Worklist filter** (D28): from `stale=1 AND (status=''open'' OR kind IN (''design'',''schema''))` to `stale=1 AND status IN (''open'',''spec'')`. Kind clause removed.
- **close/wont_do refusal** (D30): from "refused if kind IN (''design'',''schema'')" to "refused if status=''spec''". Same set in practice; framing matches the column.
- **reconcile refusal** (T156/D156): collapses to `status IN (''closed'',''wont_do'',''retired'')`. The exception for closed/wont_do design/schema collapses because mig 009 reassigns those rows to spec or retired (D36''s half covers retired).
- **add()''s default status**: when `kind IN (''design'',''schema'')`, the new row''s status defaults to ''spec''; otherwise ''open''. Determinable from kind at create time.
- **links() candidate filter**: from `(status=''open'' OR kind IN (''design'',''schema''))` to `status IN (''open'',''spec'')` for the design/schema layer of candidates. (D36 narrows further by excluding retired.)
- **reclassify cross-partition auto-shift**: production/meta `open` → design/schema auto-shifts to `spec`; design/schema `spec` → production/meta auto-shifts to `open`. A `status_transitions` row is appended documenting the auto-shift. Cross-partition with no clean target (e.g., production `closed` → design) is refused — see D36 for the full partition rule.

**What stays unchanged under D35:**

- Production and meta tasks use `open/closed/wont_do` exactly as today.
- The lifecycle semantics for production/meta are unchanged.
- D29 description_revisions writes still fire on edit regardless of status.
- D31 code-check reminder still fires on design/schema edits (and now equivalently on `status IN (''spec'',''retired'')`, since they''re the same set under partition).
- D32 prefix convention is status-agnostic.

**Migration 009 (D35 spec portion)** (SCHEMA_VERSION 9→10):

- Bumps the S1 status CHECK to `(open, closed, wont_do, spec, retired)` — the full 5-value enum lands here. D36 covers the partition CHECK that arrives in the same mig transaction.
- `UPDATE tasks SET status=''spec'' WHERE kind IN (''design'',''schema'') AND status IN (''open'',''closed'')` — both the currently-open and historically-closed design/schema populations migrate to spec. Current dogfood: 30 open + 13 closed = 43 rows migrate to spec.
- The wont_do design/schema population (1 row in current dogfood: D109/D25) migrates to status=''retired'' under D36''s mig portion — same mig 009 transaction.
- Appends a `status_transitions` row for each reassigned task documenting the migration-time transition.

**Existing slices that need prose edits under D35:**

- D7 (status taxonomy gains ''spec''; full 5-value taxonomy lands jointly with D36''s ''retired'').
- D14 (refusal predicates change to status-derived for the spec half; D36 adds the retire/link-to-retired/reconcile-on-retired refusal patterns).
- D28 (worklist filter simplifies; D36 adds retired to record-only set).
- D30 (reframed: the perma-open rule is now spelled status=''spec''; mechanism shifts from kind to status. D30''s body redirects to D35+D36 as the canonical framing).
- S1 (status CHECK extended; partition CHECK joint with D36).
- D8 (transition history gains ''spec'' as a valid to_status value; ''retired'' joins via D36).
- D156 (legacy stuck-rows resolution: notes that D35+D36 obviate the kind exception entirely).

**Out of scope for D35:**

- The retired status, retire() verb, kind/status partition rule, all-or-nothing discipline, propagation principle — all live under D36.
- Any change to open/closed/wont_do semantics for production/meta tasks.
- Any change to D32 prefix conventions or D33 placeholder refusal (extended under D36 to cover retire''s reason field).
- Renaming legacy D#/S# slot prefixes (per the existing D32 grandfathering rule).

**Why not Path 1 (display-layer fix):**

Path 1 (default filter in ls()/board() to kind in {production, meta}; split counts as "X work items, Y spec slices"; reword docs) is cheaper but doesn''t fix the underlying model. Anyone reading the DB directly or building a new surface re-encounters the confusion. Path 2 (this slice) fixes it at the column.

Backs: D7, D14, D28, D30, S1, D8 (prose edits required to align with new framing); D26 (cross-references the partition rule that arrives under D36); D156 (kind exception now obviated).', 'spec', 0, '2026-06-02T04:54:12.306635+00:00', '2026-06-03T02:49:05.858056+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (171, 'D36 — Retired status + retire() verb + kind/status partition + all-or-nothing edit/retire discipline', '**The problem D36 fixes** (surfaced 2026-06-02 during D35 impl brainstorming): D35''s framing — "design/schema = perma-living spec" — works for decisions that remain TRUE about the system. It does NOT account for design decisions that are fully abandoned with no replacement. The gap surfaced when D109/D25 (supersede marker, retired in v0.4) was reviewed for the mig 009 plan: D25 is not a living spec; the decision was tried and dropped. Under D30''s perma-open framing it can''t be close()''d or wont_do()''d, but calling it status=''spec'' falsely promotes a retired decision back into the living-spec set.

The fix: add a second terminal status for design/schema slices — ''retired'' — with a new verb retire() to reach it, plus the kind/status partition invariant that arrives jointly with this addition.

**The 5-status model + kind/status partition** (joint with D35; D36 makes the partition explicit and adds ''retired''):

| value      | who uses it          | meaning                                              | terminal? |
|------------|----------------------|------------------------------------------------------|-----------|
| `open`     | production, meta     | work item to do                                      | no        |
| `closed`   | production, meta     | work was completed                                   | no (reopen)|
| `wont_do`  | production, meta     | work considered, decided against                     | yes       |
| `spec`     | design, schema       | living decision/contract — describes current truth   | no        |
| `retired`  | design, schema       | decision considered, fully abandoned (100% gone)     | yes       |

**Kind/status partition rule (the new D26-grade invariant):**

- `kind IN (''production'',''meta'')` → `status IN (''open'',''closed'',''wont_do'')` only. spec/retired refused.
- `kind IN (''design'',''schema'')` → `status IN (''spec'',''retired'')` only. open/closed/wont_do refused.

Enforced in two places:
- Pydantic `Task` validator (D2 boundary) refuses cross-partition states.
- S1 CHECK constraint as DB-layer backstop.

**The retire() verb:**

- Signature: `retire(id: int, reason: str, delta: str) → ObligationPayload`
- `reason` is durable, persisted in the row''s `wont_do_reason` column. The partition rule guarantees one terminal verb per row (wont_do for production/meta, retire for design/schema), so the column name stays; S1''s prose updated to document the dual role.
- `delta` is the ephemeral per-edit rationale (D34).
- **Refusal order (fail-fast):**
  1. Task exists check.
  2. `status=''spec''` check — refused with "retire refused: only living specs (status=''spec'') can be retired; this row is status=''{actual}''. If the decision returned, file a fresh D# instead of reanimating this row."
  3. `kind IN (''design'',''schema'')` check — redundant with status under partition but kept for error clarity.
  4. Stale-or-linked-stale gate — same close-gate logic as close/wont_do.
  5. **Refused if ANY linked neighbor has status=''open''.** Forces the agent to evaluate each open neighbor right at retirement time. The refusal message lists each open neighbor with its `because` rationale and presents the decision tree:
     ```
     retire refused: D### has N open linked tasks. Resolve each before retiring:
       - T123 (status=open) — because: "T123 realizes D### in code"
       - T124 (status=open) — because: "..."
     For each open neighbor, decide:
       (i)  If the neighbor''s work realizes ONLY this retired decision:
            link_rm + wont_do(neighbor, reason=...) — the work is dead too.
       (ii) If the neighbor''s work has other reasons to exist (linked to
            other living specs):
            link_rm — work continues under remaining live premises.
     Then re-attempt retire(D###).
     ```
     Living specs (status=''spec'') and terminal-state neighbors (closed, wont_do, retired) do NOT trip the refusal — they''re either still valid or already resolved.
  6. Reason validation — D33 placeholder refusal extends to retire''s reason field. Empty / "obsolete" / "no longer needed" / etc. rejected; demand specific abandonment rationale.

- **Cascade**: retire() does NOT fire the cascade (mirrors wont_do/close). The retirement IS the alert. Returns the one-hop obligation payload so the agent reviews linked neighbors and decides per-link whether to link_rm (no longer meaningful) or leave (historical record).
- **No description_revisions row** — retire is a status change, not a content edit. The durable reason lives in wont_do_reason; archaeology reads status_transitions to see the spec→retired event.
- **Terminal**: reopen/close/wont_do/retire all refused on retired rows (T132 generalized — no double-decide). If the decision returns, file a fresh D#; do not reanimate the old row.

**The all-or-nothing rule for retire vs edit (THE discipline):**

| Verb   | When                                                          | Link re-eval                                         |
|--------|---------------------------------------------------------------|------------------------------------------------------|
| edit   | spec is changing (any partial change, including big rewrites) | yes — cascade fires, agent reviews 1-hop neighbors  |
| retire | spec is 100% dead, no replacement                             | no — links stay as historical graveyard              |

If you find yourself wanting to migrate some links to a new spec, you weren''t retiring — you were editing. Edit''s cascade IS the partial-change re-eval mechanism; retire''s "no cascade + open-neighbor refusal" embodies the 100%-gone contract.

This discipline lives on every agent-facing surface (the propagation principle below).

**Link behavior to retired endpoints:**

- Existing links survive — rows in the links table aren''t deleted; rationales persist; show(retired_id) still returns its neighbors. Symmetrically, a production task''s show() payload still includes the retired spec, just badged `status=''retired''`.
- Forward-looking surfaces filter retired out:
  - `links()` candidate filter narrows from `(status=''open'' OR kind IN (''design'',''schema''))` (D28-aligned) to `status=''spec''` for design/schema — excludes retired.
  - `stale()` worklist (D28) uses `status IN (''open'',''spec'')` — retired is off the obligation list.
  - `link_add()` refused if either endpoint has status=''retired'' — no realization relationship to a dead decision. Refusal message: "link_add refused: endpoint {id} is retired. Retired specs accept no new edges — there is no realization relationship to a dead decision. Prior content lives in description_revisions."
- Cascade behavior on retired: edits to a neighbor mark a retired endpoint stale at depth-1, but it''s **record-only** (D28 generalizes — stale-record-only applies to status NOT IN (''open'',''spec''), which covers closed/wont_do/retired).
- `reconcile()` refused on retired (same logic as closed/wont_do — flag is record-only archaeology; clearing it would erase the historical signal). Message: "reconcile refused on retired tasks. stale on these is record-only archaeology — clearing it would erase the historical signal that an upstream changed."

**Refusal-message bank (the agent-facing teaching surface):**

```
close()/wont_do() on status=''spec'':
  "close|wont_do refused on design/schema slices. Use edit() to refine
   a decision; retire() if the decision is 100% abandoned with no
   replacement."

retire() on status != ''spec'':
  "retire refused: only living specs (status=''spec'') can be retired;
   this row is status=''{actual}''. If the decision returned, file a
   fresh D# instead of reanimating this row."

retire() on status=''spec'' with open linked neighbors:
  [structured workflow message above — lists each open neighbor + the
   (i)/(ii) decision tree]

link_add() to retired endpoint:
  "link_add refused: endpoint {id} is retired. Retired specs accept no
   new edges — there is no realization relationship to a dead decision.
   Prior content lives in description_revisions."

reopen() on status=''retired'':
  "reopen refused: retired is terminal. Create a new design slice if the
   decision returned."

reconcile() on status IN (''closed'',''wont_do'',''retired''):
  "reconcile refused on {status} tasks. stale on these is record-only
   archaeology — clearing it would erase the historical signal that an
   upstream changed."

reclassify() cross-partition with no clean target:
  "reclassify refused: source status ''{src}'' has no clean target in the
   destination partition. Resolve the state first (edit + reopen if
   ''closed'', wont_do for permanent abandonment) before reclassifying."

Pydantic partition validator (D2 boundary):
  "task kind/status partition violation: kind=''{kind}'' requires status
   in {open,closed,wont_do} (production/meta) or {spec,retired} (design/
   schema); got status=''{status}''."
```

**The propagation principle:**

Discipline rules belong on every surface the agent touches them:
- SKILL.md (session-start context).
- MCP tool docstrings (per-invocation context — agent reads tool descriptions when choosing).
- CLI `--help` (developer/human context).
- Refusal envelopes (the at-misuse teaching moment — highest leverage because they fire exactly when confusion happens).
- README (newcomer/audit context).

D33''s placeholder-rationale refusal exemplifies this: the rule fires in `link_add` AND `add(deps=...)` AND `load()` because those are all the surfaces the rule applies to. D36 follows the same pattern: retire/edit discipline appears in every op whose contract touches it.

**Migration 009 (D36 retired portion)** (joint with D35''s mig 009; one transaction):

- The kind/status partition CHECK on tasks (joins the status CHECK widening from D35):
  ```
  (kind IN (''production'',''meta'') AND status IN (''open'',''closed'',''wont_do''))
  OR
  (kind IN (''design'',''schema'') AND status IN (''spec'',''retired''))
  ```
- `UPDATE tasks SET status=''retired'' WHERE kind IN (''design'',''schema'') AND status=''wont_do''` — the legacy wont_do design population (D109/D25 in current dogfood) migrates to retired.
- Append a status_transitions row for each.

(D35 covers the open/closed → spec portion; the two halves run in the same mig 009 transaction.)

**Out of scope for D36:**

- The spec status itself, mig 009''s spec migration, the open↔spec reclassify auto-shift — all D35.
- Any change to production/meta lifecycle semantics.
- D32 prefix conventions (status-agnostic).
- An "un-retire" mechanism — T132 generalized: retire is permanent. If a decision returns, file a fresh D#.

**Affected slices that need prose edits under D36** (joint with D35''s list, D36-specific additions):

- D14 (refusal taxonomy — new refusals: retire on non-spec, retire on open-neighbor, link_add on retired, reconcile on retired, reclassify cross-partition).
- D26 (kind taxonomy — adds cross-reference to the partition rule).
- D27 (link discovery — candidate filter narrows to `status=''spec''`).
- D28 (worklist filter — joint with D35; D36 adds retired to record-only set).
- D29 (description_revisions — note retire() does NOT append a revision row; durable reason lives in wont_do_reason column).
- D30 (perma-open framing — replaced by D35+D36 jointly; D30 redirects to the new canonical framing).
- D31 (code-check reminder — fires on retired edits too as "verify no lingering code references this dead decision").
- D33 (placeholder rationale refusal — extends to retire''s reason field).
- S1 (partition CHECK joint with D35; wont_do_reason column documented as terminal-verb-reason regardless of which verb wrote it).

Backs: D7 (status taxonomy joint with D35), D14, D26, D27, D28, D29, D30, D31, D33, S1.', 'spec', 0, '2026-06-02T07:22:12.812393+00:00', '2026-06-03T07:13:22.827154+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (172, 'D37 — Granular-description discipline: task bodies must be impl-ready on fresh-session revisit; edit() before close to absorb impl-time discoveries', '**The granular-description discipline** (added 2026-06-02): task descriptions must be implementation-ready. On a new-session revisit, an agent reading the description should not be confused, should not feel the need to do additional scoping work, and should not encounter ambiguity that could be resolved. Under-defined task descriptions force fresh-session agents to re-derive context that should already be on the row — which is exactly the failure tackit exists to prevent.

**The rule (forceful):**

> A task''s `description` must contain enough granular detail that a fresh-session agent — with no conversation history, no prior context, only the task body and its linked neighbors — can implement the task (or evaluate its completion) without asking the user for clarification.

**Per-kind expectations:**

- **design / schema slices** (status=''spec''): the slice must fully specify the decision, its constraints, its implications, and the refusal patterns it implies, such that a fresh-session agent can edit code to align with it (and verify drift via D31''s code-check reminder).
- **production tasks**: the body must describe what code change is being made, in what files, at what call sites, with what test coverage and refusal-message wording, such that a fresh-session agent can sit down and write the change directly. Include SQL recipes, signature snippets, predicate tables, error-message banks — execution-grade detail.
- **meta tasks**: the body must describe what bookkeeping is being captured (a release, an experiment, an observation), with enough context to interpret the result. For observation experiments, include procedure + raw findings + verdict; for release tracking, include the version + scope + sign-off conditions.

**When the discipline applies:**

- **At create time (`add()`)** — aim for full granularity from the start. add()''s docstring SHOULD prompt the agent to write at impl-ready granularity. The bias is "richer body now beats remembering it later."
- **During implementation** — if an agent discovers an under-defined detail while implementing (an edge case the spec missed, a refusal-message wording question, an extra file affected), `edit()` is the mechanism to fold the discovery back into the description BEFORE close. Under-definition discovered during impl is a normal and expected outcome — the response is to edit, not to lose the detail.
- **Before close** — the author should re-read the description against what was actually implemented. If the description no longer fully captures the impl, edit() it before close. Closing a task with an out-of-date description destroys the granularity for future readers — the audit trail (S7 description_revisions) is the safety net, but relying on archaeology should be the exception, not the rule.

**Anti-patterns this discipline forbids:**

- **Vague verbs**: "Fix bug" / "update logic" / "clean up X" — unsearchable, unimplementable, kind-conditional context loss.
- **Conversation references**: "Add the feature discussed in conversation" / "see chat history" / "as agreed" — references ephemeral context the durable task can''t recover.
- **Pointer-only bodies**: "See related task X for details" without inlining the actual scope — forces traversal that loses on a fresh-session pass when X has also been edited or removed.
- **TBD/TODO placeholders** in committed task bodies — flag and resolve before commit. If a detail genuinely isn''t decided yet, the task isn''t ready to be tracked as a discrete unit; either decide it or split.
- **Implementation-by-conversation**: agreeing on detail in conversation but never folding it into the task body — the conversation is ephemeral, the task is durable. The bias should always be: if a detail surfaced in conversation that''s not yet on the task, edit() it onto the task before the conversation context expires.

**Surfaces this discipline lives on** (per D36''s propagation principle):

- **SKILL.md** (T169): forceful instruction in the writing-tasks section. The primary surface — agents read it at session start and the rule shapes every subsequent add() / edit() invocation.
- **MCP `add()` docstring** (T168): "Aim for impl-ready granularity at create time. A fresh-session agent should be able to implement the task from its description alone."
- **MCP `edit()` docstring** (T168): "If impl reveals under-defined details, edit() is the mechanism to fold them back BEFORE close. Closing with an out-of-date description destroys granularity for future readers."
- **CLI `tackit add` / `tackit edit --help`** (T168): same wording.
- **README** (T170): the writing-tasks workflow walkthrough demonstrates the discipline — show a vague task being refined into an impl-ready one via edit().

**Out of scope:**

- A typed-boundary refusal for "description too short" or "contains TBD strings" — quality is not programmatically detectable in any robust way. The discipline lives at the author-time conscience layer, surfaced via the multi-surface guidance above, not at the typed boundary.
- LLM-judged quality grading of task descriptions — out of scope for D37 v1; could be a future experiment as a meta task.
- Renaming legacy under-defined task descriptions retroactively — flag and refine opportunistically; no bulk-migration sweep.

**Why this matters (concrete example):**

The 2026-06-02 D35/D36/T168/T169/T170 persistence pass demonstrated the failure mode this discipline prevents. The original T168 (filed before brainstorming) had a 7-bullet scope outline. During brainstorming, the scope grew to: (a) joint mig 009 with D36; (b) full SQL recipe; (c) per-call-site predicate sweep table; (d) retire() verb impl detail; (e) surface-propagation checklist including a verbatim refusal-message bank; (f) prose-sweep order across 12 affected slices; (g) test matrix placeholder. None of those details were in the original T168 body. Without the persistence pass, a fresh-session agent picking up T168 would face exactly the scoping work this discipline forbids — re-deriving scope from D35 + dogfood data + a brainstorm transcript that no longer exists. The persistence pass IS the discipline being applied; D37 names the principle so future tasks arrive at this granularity from the start (or at least before close).

**Test strategy:**

D37 is a writing-time discipline, not a runtime mechanism — there are no refusal predicates to pin. The "tests" that matter are surface-presence tests: assert that SKILL.md contains the forceful instruction; assert that the add() and edit() MCP docstrings contain the granularity guidance; assert that the README''s writing-tasks walkthrough includes the discipline demonstration. These are documentation-presence tests in tests/test_docs.py or similar.

Backs: D3 (task create/read/update — discipline applies at create time), D26 (kind taxonomy — granular descriptions are how each kind achieves its purpose), D29 (description_revisions — audit trail of edits captures granularity improvements over time and is the safety net for missed pre-close refinements), D36 (propagation principle — D37 follows the same multi-surface pattern: SKILL + MCP docstrings + CLI help + README).', 'spec', 0, '2026-06-02T07:26:37.592198+00:00', '2026-06-03T06:57:18.262544+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (197, 'D38 — Links are coupling, labels are membership: the rollup / hub / membership-link anti-patterns', 'Names and forbids the "fake task" family that the dogfood surfaced, and removes the SKILL.md endorsement (the "epic pattern" snippet) that caused it.

## The core distinction (the thing that''s easy to miss)
A **link** is a claim about *consequence*: "if X''s content changes, Y must be re-examined." That is literally what the cascade fires. A **label** is a claim about *category*: "X and Y belong to the same grouping." Categories have no consequence — editing one sibling does not invalidate another. Links carry cascade semantics; labels are dumb tags.

**The test, applied at every `link_add`:** "If I edited X''s body right now, would I genuinely need to re-open Y and check it still holds?" Yes → coupling → link. "No, they''re just both part of the same epic/theme" → membership → label.

**The `because` is the discriminator** (extends D34): a coupling `because` names a *consequence* ("citations FK references documents.id; a column rename here breaks the join"). A membership `because` restates a *category* ("part of the plan-import epic", "schema-ingest cluster"). When the `because` you are about to write is just the cluster''s label name reworded, the edge is a membership link masquerading as coupling — drop it and attach the label instead.

## The anti-patterns (named, for recognition + review)
- **Hub task** — a task whose purpose is to be linked-to (a membership magnet). Cost: accumulates membership links; over the cluster''s life every edit to the hub stales N bystanders and every edit to any member stales the hub — ~N² false-positive stale flags, all zero-value.
- **Membership link** — an edge encoding category, not consequence. Cost: each is a permanent false-positive stale generator that trains the FAST filter into rubber-stamping (the "Edits aren''t free" failure mode), so the cascade stops catching *real* drift.
- **Rollup task** — a task whose body is a hand-maintained status ledger of *other* tasks. Cost: (1) duplicates state tackit already tracks (status/labels), and the hand-typed copy drifts the instant a real task closes — the exact drift tackit exists to prevent, reintroduced inside tackit; (2) gets edited as a side-effect of *dependents* finishing, firing the full neighbor sweep at moments unrelated to most neighbors (backwards cascade).
- Umbrella term: **fake task** — a task that is not a unit of work (no deliverable, no decision) and exists only to be a link target or to hold a rollup.

## The positive patterns (what to do instead — lead with these)
- **Group a cluster with a shared label, never with links to an anchor.** This replaces the deleted "epic pattern" snippet, which told agents to BOTH label the cluster AND link the members to an anchor — encoding membership twice and creating the cascade hub.
- **Coverage is a query, not a task.** To answer "is this cluster complete?", run `board(label=X)` / `ls(label=X)` for the live membership and compare against the expected set. The expected set (the denominator) lives in the design/schema slice — or memory — that *defines* it, never in a hand-typed status table inside a task body.

## Legitimate vs fake — the boundary (do NOT over-apply)
A design/schema slice that captures a *decision* and is linked by the impl tasks that *realize* it is NOT a hub — those are coupling links (edit the decision ⇒ re-review the realizing impl). The entire `links` / dep-discovery model depends on decision-bearing slices being linked-to. What is forbidden is a *content-free* task that exists only for membership or rollup. The separating variable is semantic (does the node carry a decision/contract?), not structural (degree, because-similarity).

## Detection: considered and REJECTED as brittle (2026-06-04, user decision)
Heuristic auto-detection at add/edit/link_add (rollup-body regex; high-degree + near-duplicate-because hub detector) was evaluated and rejected. High-precision separation of a fake hub from a legitimately-central decision slice is semantic, not structural — degree + because-similarity fire on exactly the legitimate slices we want to keep (e.g. D36, linked by every realizing impl task with similar becauses). A warn-level heuristic would add noise to the very FAST-filter SNR this rule protects; refuse-level would block legitimate work. tackit''s established philosophy (P1 dep-discovery reframe — deterministic surface, agent judges) already covers it: the agent holding the tool IS the semantic judge; no separate detector belongs in the MCP.

## The lesson behind the lesson (prompt-engineering, why removal beats addition)
The failure that produced this slice was NOT a missing prohibition — the agent already had "don''t scatter to-dos" / "not a knowledge base" and built the hub anyway, because a *contradictory positive instruction* (the epic-anchor snippet) endorsed it. A wrong "do" beats a right "don''t" every time. Hence: the highest-leverage fix is *removing the contradictory endorsement*, then *leading with the positive pattern* (label to group, query for coverage). The anti-pattern names serve *recognition* (catch yourself mid-act) and *review*, not generation.

## Surfaces (propagation per the propagation principle)
SKILL.md (replace the epic snippet in the Labels section + add this discipline section + tighten the Right-size and Write-real-because bullets), README for-agents discipline block, MCP `add()` docstring (the missing too-large / hub / rollup direction — currently only catches too-small) + `link_add()` docstring (because = consequence not category), and a presence-pinning test mirroring test_d37_docstrings.py.

Backs/refines: D34 (because semantics + FAST filter) — coupling link to it.

## Phase 2 fold-back (2026-06-05, T219) — the synonym leak + the edge axis
- **Symptom.** A dogfood session''s agent rebuilt the forbidden hub/roster pattern, naming it an "umbrella slice," despite this slice forbidding hubs. It even proposed "§X-as-umbrella (mirror §Y)" as a repeatable move.
- **Root cause.** T198''s removal pass deleted the *named* "epic pattern" snippet but a *synonymous* blessed-parent noun — "umbrella" — survived in SKILL''s `When findings outgrow the body` rule (a DIFFERENT section than the Labels section this slice''s surface-list named). A surviving positive use of the banned concept under a synonym re-licensed it: this slice''s own thesis (a wrong "do" beats a right "don''t") leaking through a word the removal never swept.
- **Fix (T219).** (a) Reworded findings-overflow `<umbrella>` -> `<source-task>` (behavior unchanged). (b) Added a companion SKILL rule `## Relationships are edges, not prose` — a SECOND AXIS to this slice: D38 forbids the content-free *node* (hub/rollup); the companion forbids a relationship *narrated in body prose* instead of wired as an *edge* (the cascade traverses links, never prose). (c) Vocabulary-collision fix: "anchor" was overloaded — it is the term-of-art for the GOOD design/schema layer that `links()` surfaces, yet the grouping heading + README reused it for the BAD node. Renamed those usages to "hub" (the name this slice''s body already uses), reserving "anchor" for the good layer. (d) Propagated the companion to README + `add()` / `link_add()` docstrings.
- **Why missed.** T198''s removal targeted the snippet by name/content with no synonym sweep; the "anchor" overload was never flagged as a collision.
- **Pinning.** `test_d38_docstrings::test_skill_md_contains_relationships_are_edges_rule` + extended `add()`/`link_add()` docstring pins. SKILL.md "umbrella" count is now 0.
- **Status.** SKILL new rule + reword + first pins in 3076890; the remaining surfaces (anchor→hub, README, docstrings, extra pins) in the T219 commit. 525 tests pass.', 'spec', 0, '2026-06-04T16:44:45.843164+00:00', '2026-06-05T20:34:01.060691+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (199, 'D39 — Bulk-sweep ergonomics: compact link_add return, batch reconcile(ids), short alert on reconcile', 'Cuts the per-op token tax that makes bulk/sweep operations expensive — the T179 large-body pattern resurfacing on the RESPONSE and SWEEP side. From dogfood friction on a large schema backfill (#2/#1/#6 of that report). tackit was built interactive-single-op-first; its origin use case (import a large plan) and the messy-backfill reality are bulk.

## #2 — link_add returns a compact confirmation, not the full slice
Today MCP `link_add` returns `a`''s full slice (task body + dependencies + dependents, each neighbor listed twice). Wiring N edges through a high-degree node (e.g. a schema hub with 8 neighbors) reprints its whole neighborhood on every edge — pure context tax.

**Decision:** MCP `link_add` returns `{"linked": {"a": <id>, "b": <id>, "because": <str>}}` + the standard envelope with `short_alert=True`. NOT the full slice. Rationale: link_add is STRUCTURAL — it does not cascade (skill: link ops don''t fire the cascade), so the neighborhood echo carries no obligation the caller must act on; confirmation that the edge landed is all that''s needed. Use `show(a)` for the slice when actually wanted. No `verbose` flag (YAGNI — show() is the escape hatch). `core.link_add` is UNCHANGED (it returns the Task; only the MCP wrapper''s payload shrinks).

## #1 — batch reconcile via an EXPLICIT id list
Today `reconcile(id)` is single. One edit that stales 7 genuinely-fine neighbors costs 7 round trips (and 7 D18 version bumps).

**Decision:** add `core.reconcile_many(ids: list[int]) -> list[Task]` and expose MCP `reconcile(ids: list[int])` (CLI: `tackit reconcile <id>...`, nargs="+"). `core.reconcile(task_id)` stays UNCHANGED (≈80 test call sites + the atomic single primitive). reconcile_many: **validate-all-first, fail-loud** — collect every terminal/invalid id and raise listing all of them BEFORE any mutation (no partial sweep), then clear stale per-row (D20 no-op guard each), in ONE `_mutate()` (one version bump, not N).

**THE GUARD-RAIL (load-bearing — do not relax):** the batch form takes an **explicit id list**. The agent still enumerates the set it judged clean. There is deliberately **NO** `reconcile_all_stale()` / "reconcile every neighbor matching rationale X" auto-clear form. That would automate the *judgment*, which is exactly the rubber-stamp the edit-quality + D34 disciplines exist to prevent — and a cascade trained on noise stops catching real drift. reconcile_many batches *transport*, never *judgment*: making 7 identical calls was never 7 acts of review, it was one review executed 7×; collapse the transport, keep the review. A future "let''s just clear all stale" convenience is the anti-feature this slice forbids.

## #6 — short alert on the reconcile sweep
Today MCP `reconcile` emits the full forceful stale_alert on every call; across a known-clean N-task sweep that''s the long message repeated N×.

**Decision:** MCP `reconcile` uses `short_alert=True` (the M181 #8b mechanism already built for read ops) — the compact form still surfaces the remaining worklist count, which is the only live signal during a drain. Returns `{"reconciled": [<ids>], "remaining_stale": <worklist count>}` + short_alert, not N full slices.

## Surfaces (propagation principle)
- `core.py`: new `reconcile_many`.
- `mcp_server.py`: `link_add` compact return; `reconcile(ids)` → reconcile_many + short_alert.
- `cli.py`: `reconcile` takes nargs="+" ids; help text.
- MCP docstrings (link_add return note; reconcile batch + guard-rail) + CLI --help.
- SKILL.md: the reconciliation-discipline section gains "batch via explicit ids; no auto-clear-all (the guard-rail)"; a note that link_add returns compact.
- README for-agents block: one-liner.
- Tests: reconcile_many (atomic validate-all-first, one version bump, no-op rows, terminal-id refusal lists all); link_add compact-return shape; reconcile short_alert + compact payload; update the 3 MCP + 2 CLI reconcile call sites to the list form.

Couples to: the edit-quality / reconciliation discipline (the guard-rail is why this isn''t a pure ergonomic change) and M181 #8b (short_alert, reused for #6).', 'spec', 0, '2026-06-04T17:17:46.466807+00:00', '2026-06-04T17:17:46.466807+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (202, 'D40 — Bulk import is first-class: load is the documented path + the plan format carries D37-grade multi-paragraph bodies', 'Resolves the load-vs-D37 tension surfaced by dogfood feedback #5: tackit''s origin use case is importing a large plan, yet `load` was (a) undiscoverable — agents make N `add()` calls instead — and (b) UNUSABLE for the rich bodies the granular-description discipline (D37) demands.

## The bug (verified 2026-06-04)
`parse_plan` treats a blank line as the end of a `desc:` block. A D37-style body with blank-line-separated paragraphs therefore doesn''t truncate — it HARD-FAILS: the line after the blank is neither a `[key]` nor a `field:`, so the parser raises `cannot parse` and the whole atomic import rolls back. Consecutive deeper-indented lines (no blanks) work; blank-separated paragraphs do not. So the documented import path cannot carry the bodies the documented description discipline requires — two of tackit''s own rules in direct conflict.

## Decision A — blank lines inside a desc block are paragraph breaks
A blank line encountered while collecting a `desc:` is DEFERRED, not terminal: it becomes a paragraph break (`\n\n`) iff a desc continuation line (deeper-indented than the `desc:` keyword) follows; if a `field:` / `[key]` / EOF follows instead, the trailing blanks are discarded and the desc block simply ends (unchanged behavior). This preserves the existing "blank line separates tasks" convention exactly — a blank followed by a dedented structural line still ends the block — while letting multi-paragraph bodies round-trip. `depends_on:` blocks are unaffected (a dep entry is one line; a blank still ends a deps block). This is the load-bearing format contract: a future "simplify the parser back to single-block desc" would re-break D37 import and is the regression this slice forbids.

## Decision B — load is THE documented bulk-import path
The workflow surfaces (SKILL.md, README, load docstring/CLI help) name `load` as the first-class way to import N tasks at once, with the plan format''s full capability (kind/desc-multiparagraph/labels/depends_on-with-becauses, atomic rollback). Discoverability was the gap: nothing pointed an agent at `load`, so the origin use case (import a large plan) defaulted to N individual `add()` calls.

## Rejected (for now): a structured JSON bulk-import at the MCP
`core.load(specs: list[dict])` already accepts arbitrary `desc` strings, so a JSON MCP entry would sidestep the text parser entirely. Deferred — Decision A makes the documented text format sufficient for rich bodies; a second import surface is YAGNI until a concrete need appears (e.g. programmatic import where composing text is awkward). Recorded so the option isn''t re-litigated from scratch.

## Surfaces
- `plan.py`: defer blank lines inside a desc (Decision A).
- SKILL.md + README + `load` MCP docstring + CLI help: name load as the bulk-import path; note multi-paragraph desc support.
- Tests: blank-line paragraph round-trip; the previously-failing sample now parses; trailing-blank-before-field still ends the block; consecutive-line continuation unchanged; deps block still ends on blank.', 'spec', 0, '2026-06-04T17:36:52.807216+00:00', '2026-06-04T17:36:52.807216+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (207, 'D41 — SKILL.md is instructions, not documentation: why/do/don''t-do format, cut standalone narrative, cite-don''t-narrate, MCP harmony', 'SKILL.md has drifted into documentation-with-a-narrative-arc; it should be a strict list of behavioral instructions. This slice is the standard the rewrite (and all future SKILL edits) must obey. Subsumes the Q4 pattern/anti-pattern request (the format below IS the pattern/anti-pattern, generalized).

## The format — every BEHAVIORAL instruction
```
why:        <the rationale: what it solves / what breaks without it. This is
            the encapsulated context — reading it tells the agent WHY the rule
            exists. ALWAYS present.>
do:         <the directive. ALWAYS present.>
don''t-do:   <the concrete anti-pattern. Present ONLY when there''s a real one;
            omit when it would be vacuous (Q4 discretion — `show` has no
            meaningful "wrong example").>
```
why+do are mandatory; don''t-do is conditional. An instruction whose `why:` isn''t clear is, by definition, poorly written — fix it, don''t leave the why implicit.

## What is NOT an instruction (stays as reference, NOT forced into the format)
Pure mechanics/definitions: the kind/status partition table, what a read op returns, the auto-id prefix synthesis rule, the report-format template. These are reference; leave them as tables/definitions.

## Cut standalone narrative
do: delete war stories, worked-example retellings, version archaeology (the v0.3→v0.4→v0.5 parentheticals), and motivational preambles.
don''t-do: don''t keep a beginning/middle/end arc; SKILL.md is not a story.

## Cite-don''t-narrate (the key move)
The full incident/example already lives in the tackit task body it''s about (T179''s anchoring incident, D25''s retire story, T115''s inheritance-trap case). The instruction cites it in a clause ("see T179") instead of re-telling it. tackit IS the archive — the skill must not duplicate what the task records.

## No duplication, demote superlatives
- No paragraph appears verbatim twice (today: "Edits aren''t free" pasted 3× in the MCP edit docstrings; the T179 story in 2–3 places).
- At most ONE rule may be framed as "the most important" (today four sections each claim it). Pick one (ship-on-pain) or none.

## Intro
3–4 lines max: what tackit is + why it exists. No more standalone context than that.

## MCP harmony (indirect, not 1:1)
The skill and MCP are different surfaces (propagation principle): SKILL teaches cross-cutting disciplines at session-start; a docstring describes ONE operation at call-moment. Harmony rule: each discipline''s FULL statement lives once, in SKILL; the docstring carries the op''s contract (params/refusals/return) + the one-line sharp edge of any discipline that bites at THAT call + a cite to the SKILL section — never the full discipline paragraph. Docstrings get shorter + scoped, NOT reformatted into why/do/don''t (that genre is SKILL''s). This preserves each-surface-teaches-at-its-moment without verbatim duplication.

## Test + sync impact
Presence-pinning tests (test_d37_docstrings, test_d38_docstrings) assert phrases that will move — update them with the rewrite. Re-sync the 3 dev SKILL copies (dev-copies-match test).

## Target
~35–40% reduction (~1060 → ~620–680 lines) with ZERO loss of any behavioral directive. Every cut is narrative/duplication/archaeology, never a rule.

## Measured at T208/T209 (fold-back)
The rewrite compressed SKILL.md 1083 → 282 lines (63.6k → 27.9k chars, ~56%), NOT the ~35–40% estimated above — the why/do/don''t format is far denser than projected, so that target was wrong. The presence-pin tests (test_d37/d38_docstrings) proved to be the rewrite''s safety net: a wholesale reformat moved or dropped 6 load-bearing phrases (ship-on-pain "OVERRIDES", "trained on noise", "substantive impact", "consequential and necessary", "findings outgrow the body", the D37 heading) — every one caught by a failing test and restored verbatim. Lesson for any future skill/docstring reformat: expect a deeper cut than intuition suggests, and lean on the presence pins as the guardrail against silent directive loss.', 'spec', 0, '2026-06-04T20:46:55.466445+00:00', '2026-06-04T21:25:47.137175+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (211, 'Lean read-projection default for ls and board', 'Decision: `ls` and `board` return a LEAN projection by default — task scalars only (id, prefixed_name, name, kind, status, stale, created_at, updated_at, labels), NOT `description`. `board` additionally drops each neighbor''s `because` + `last_edit_delta` by default, keeping the graph SHAPE (NeighborRef id/prefixed_name/status/stale/kind). Full body and full neighbor prose are opt-in.

Trigger: a ~150-slice plan ingest called board(label=…, status=''spec'') and got 228 KB (full D37 impl-ready bodies × full dep/dependent `because` text), blowing the token budget and forcing a fallback to grepping a dumped file. `ls` is also heavy because `core.ls()` returns the full `Task` including `description`.

Consistency rationale: `search` already proves the idiom — `SearchHit` carries no `description` and `search` has `name_only=True`. ls/board were the ONLY read ops without a lean mode; this propagates the existing lean-projection idiom to the filter-based reads, not a new paradigm.

INVARIANT (forceful): `description` is include-or-OMIT, NEVER truncated. A half-shown impl-ready body looks complete and an agent acts on the wrong half — include/exclude is honest, truncation isn''t. `show(id)` is the only full-body path; ls/board are never it.

kind filter: add a `kind` filter to ls/board. status=''spec'' returns design+schema both; `kind` disambiguates (the ingest wanted design-only and had to mentally filter every result). Composes with status/label/stale.

Synced adapters (decision A, 2026-06-05): MCP and CLI share IDENTICAL default behavior — no per-adapter altitude split. Maintainer does not use the CLI; one mental model.

No size guard (decision B, 2026-06-05): a byte-threshold refusal was REJECTED — lean defaults remove the blowup, so a guard would only add surface for a footgun the defaults already close.

Opt-in mechanism: a boolean to include the full `description`, and on board a second boolean to include neighbor `because`/`last_edit_delta`. Two booleans + the kind filter; resist a general `fields` whitelist — search''s `name_only` idiom + the lean default already cover the documented pain.

Deferred (triaged, revisit ONLY if lean defaults prove insufficient — not a graveyard backlog): general `fields` whitelist; pagination (limit/offset); total / count-only. Each was considered and set aside because the lean default + show(id) cover the 95% case (one body at a time = show).', 'spec', 0, '2026-06-05T08:04:29.435065+00:00', '2026-06-05T08:04:29.435065+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (213, 'Bulk external-link + delta removal from link operations', 'Decision: three changes, one theme — make link creation bulk-friendly and stop link ops from carrying a vestigial `delta`.

(1) DELTA REMOVAL (decision A, 2026-06-05): `link_add`/`link_rm` drop the required `delta`. Source-verified vestigial — link ops do NOT fire the cascade, so the delta has no reader; `link_add` persists via `_add_link(a, b, because)` (the delta never reaches the row), the `links` table (S3) has no delta column, and `load` already creates links with `because` only. `delta` exists to ride a cascade (reconcile compares the upstream edit''s `delta` × the link''s `because`); a non-cascading op produces a delta nobody reads. Applies UNIFORMLY: single ops + new bulk ops + load all agree on because-only. Folds back as a correction to T117, which over-applied `delta` to edge-mutating ops.

(2) LOAD-EXTENSION (the primary win): `load`''s `depends_on` resolves EXISTING tasks, not just batch-local keys. A ref matching `^[DSTM]\d+$` (or a bare `#\d+`) is an existing-task ref; anything else is a batch-local key — reserve the prefixed-id pattern (a batch-local key may not look like one, a parse-time collision guard). The kind-letter is VALIDATED against the resolved task''s actual kind (typo-catch: `S30` must be schema id 30; a mismatch fails loud). Collapses the ingest pattern "one load + 8–13 external link_adds" into a single atomic import, riding load''s existing all-or-nothing rollback.

(3) LINKS_ADD (bulk existing↔existing): a new op `links_add(edges=[{a, b, because}, ...])` for wiring tasks that ALREADY exist (load can''t — it only creates new tasks). Flat edge list; per-edge `because` MANDATORY; NO batch-wide default because (a shared because IS the membership-link anti-pattern — the shape forbids it). Endpoints accept id or prefixed-name (shared resolver). Validate-all-first + atomic + name-every-offender (mirrors reconcile/load): one structural bad edge — self-link (D14), cross-kind meta (D26 meta-island), retired endpoint (D36), unknown ref — rejects the WHOLE batch and lists all offenders. `already_linked` is a benign no-op (re-runnable), counted not rejected. Compact echo: counts + the created [a, b] pairs by prefixed-name, never the because text or neighborhoods (link ops are structural — the result carries no obligation to act on).

Shared primitive: a ref-resolver (prefixed-name/id → task, with kind-letter validation) underlies BOTH (2) and (3) — build it once, expose it on both surfaces.

Out of scope: the read-projection booleans / pagination belong to D211, not here.', 'spec', 0, '2026-06-05T08:26:46.942108+00:00', '2026-06-05T08:26:46.942108+00:00', 'design', NULL);
INSERT INTO task_labels (task_id, label) VALUES (37, 'core');
INSERT INTO task_labels (task_id, label) VALUES (37, 'schema');
INSERT INTO task_labels (task_id, label) VALUES (38, 'schema');
INSERT INTO task_labels (task_id, label) VALUES (39, 'core');
INSERT INTO task_labels (task_id, label) VALUES (39, 'schema');
INSERT INTO task_labels (task_id, label) VALUES (40, 'core');
INSERT INTO task_labels (task_id, label) VALUES (40, 'schema');
INSERT INTO task_labels (task_id, label) VALUES (41, 'core');
INSERT INTO task_labels (task_id, label) VALUES (41, 'schema');
INSERT INTO task_labels (task_id, label) VALUES (42, 'infra');
INSERT INTO task_labels (task_id, label) VALUES (42, 'schema');
INSERT INTO task_labels (task_id, label) VALUES (43, 'core');
INSERT INTO task_labels (task_id, label) VALUES (43, 'design');
INSERT INTO task_labels (task_id, label) VALUES (43, 'infra');
INSERT INTO task_labels (task_id, label) VALUES (44, 'core');
INSERT INTO task_labels (task_id, label) VALUES (44, 'design');
INSERT INTO task_labels (task_id, label) VALUES (44, 'infra');
INSERT INTO task_labels (task_id, label) VALUES (45, 'core');
INSERT INTO task_labels (task_id, label) VALUES (45, 'design');
INSERT INTO task_labels (task_id, label) VALUES (46, 'core');
INSERT INTO task_labels (task_id, label) VALUES (46, 'design');
INSERT INTO task_labels (task_id, label) VALUES (47, 'core');
INSERT INTO task_labels (task_id, label) VALUES (47, 'design');
INSERT INTO task_labels (task_id, label) VALUES (48, 'core');
INSERT INTO task_labels (task_id, label) VALUES (48, 'design');
INSERT INTO task_labels (task_id, label) VALUES (49, 'core');
INSERT INTO task_labels (task_id, label) VALUES (49, 'design');
INSERT INTO task_labels (task_id, label) VALUES (50, 'core');
INSERT INTO task_labels (task_id, label) VALUES (50, 'design');
INSERT INTO task_labels (task_id, label) VALUES (51, 'core');
INSERT INTO task_labels (task_id, label) VALUES (51, 'design');
INSERT INTO task_labels (task_id, label) VALUES (52, 'core');
INSERT INTO task_labels (task_id, label) VALUES (52, 'design');
INSERT INTO task_labels (task_id, label) VALUES (53, 'core');
INSERT INTO task_labels (task_id, label) VALUES (53, 'design');
INSERT INTO task_labels (task_id, label) VALUES (54, 'core');
INSERT INTO task_labels (task_id, label) VALUES (54, 'design');
INSERT INTO task_labels (task_id, label) VALUES (55, 'core');
INSERT INTO task_labels (task_id, label) VALUES (55, 'design');
INSERT INTO task_labels (task_id, label) VALUES (56, 'core');
INSERT INTO task_labels (task_id, label) VALUES (56, 'design');
INSERT INTO task_labels (task_id, label) VALUES (57, 'core');
INSERT INTO task_labels (task_id, label) VALUES (57, 'design');
INSERT INTO task_labels (task_id, label) VALUES (58, 'core');
INSERT INTO task_labels (task_id, label) VALUES (58, 'design');
INSERT INTO task_labels (task_id, label) VALUES (59, 'core');
INSERT INTO task_labels (task_id, label) VALUES (59, 'design');
INSERT INTO task_labels (task_id, label) VALUES (60, 'core');
INSERT INTO task_labels (task_id, label) VALUES (60, 'design');
INSERT INTO task_labels (task_id, label) VALUES (60, 'infra');
INSERT INTO task_labels (task_id, label) VALUES (61, 'core');
INSERT INTO task_labels (task_id, label) VALUES (61, 'design');
INSERT INTO task_labels (task_id, label) VALUES (62, 'core');
INSERT INTO task_labels (task_id, label) VALUES (62, 'design');
INSERT INTO task_labels (task_id, label) VALUES (63, 'core');
INSERT INTO task_labels (task_id, label) VALUES (63, 'design');
INSERT INTO task_labels (task_id, label) VALUES (64, 'core');
INSERT INTO task_labels (task_id, label) VALUES (64, 'design');
INSERT INTO task_labels (task_id, label) VALUES (65, 'core');
INSERT INTO task_labels (task_id, label) VALUES (65, 'design');
INSERT INTO task_labels (task_id, label) VALUES (66, 'core');
INSERT INTO task_labels (task_id, label) VALUES (66, 'design');
INSERT INTO task_labels (task_id, label) VALUES (109, 'core');
INSERT INTO task_labels (task_id, label) VALUES (109, 'design');
INSERT INTO task_labels (task_id, label) VALUES (110, 'core');
INSERT INTO task_labels (task_id, label) VALUES (110, 'design');
INSERT INTO task_labels (task_id, label) VALUES (111, 'core');
INSERT INTO task_labels (task_id, label) VALUES (111, 'design');
INSERT INTO task_labels (task_id, label) VALUES (137, 'v0.4');
INSERT INTO task_labels (task_id, label) VALUES (138, 'v0.4');
INSERT INTO task_labels (task_id, label) VALUES (139, 'v0.4');
INSERT INTO task_labels (task_id, label) VALUES (140, 'v0.4');
INSERT INTO task_labels (task_id, label) VALUES (141, 'v0.4');
INSERT INTO task_labels (task_id, label) VALUES (156, 'v0.4');
INSERT INTO task_labels (task_id, label) VALUES (160, 'v0.4');
INSERT INTO task_labels (task_id, label) VALUES (163, 'v0.4');
INSERT INTO task_labels (task_id, label) VALUES (165, 'v0.4');
INSERT INTO task_labels (task_id, label) VALUES (167, 'v0.5');
INSERT INTO task_labels (task_id, label) VALUES (171, 'v0.5');
INSERT INTO task_labels (task_id, label) VALUES (172, 'v0.5');
INSERT INTO task_labels (task_id, label) VALUES (197, 'skill');
INSERT INTO task_labels (task_id, label) VALUES (199, 'mcp');
INSERT INTO task_labels (task_id, label) VALUES (199, 'skill');
INSERT INTO task_labels (task_id, label) VALUES (202, 'cli');
INSERT INTO task_labels (task_id, label) VALUES (202, 'skill');
INSERT INTO task_labels (task_id, label) VALUES (207, 'docs');
INSERT INTO task_labels (task_id, label) VALUES (207, 'skill');
INSERT INTO links (id, task_a, task_b, because) VALUES (60, 43, 44, 'D2 (typed Pydantic validation at the read/write boundary) is the mechanism that gates writes into D1''s persistent store, so any change to D2''s validation rules (e.g. NUL/surrogate refusal) or to D1''s store contract forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (61, 44, 45, 'D3''s create/read/update operations are the surface that pushes input through D2''s Pydantic validation boundary, so any change to D3''s CRUD signatures or to D2''s validation rules forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (63, 45, 46, 'D4 labels attach to D3 tasks (label_add takes a task id and a label string), so any change to D3''s task identity/CRUD or to D4''s attach-and-list contract forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (64, 38, 46, 'S2 is the join table that realizes D4''s freeform labels-on-tasks contract, so any change to D4 (label semantics, attaching/listing rules) or to S2 (columns, PK shape, reserved-name enforcement) forces the other to be revisited.');
INSERT INTO links (id, task_a, task_b, because) VALUES (65, 45, 47, 'D3 (atomic create) is the substrate D5 (symmetric link) couples tasks on; changing either reshapes how the agent thinks about a single task and its relationships.');
INSERT INTO links (id, task_a, task_b, because) VALUES (66, 39, 47, 'D5 names symmetric coupling; S3 is the canonical (task_a, task_b) row shape that encodes it. A shift in semantics (directed→symmetric, T86) lands as a schema migration.');
INSERT INTO links (id, task_a, task_b, because) VALUES (67, 47, 48, 'D5 (the link itself, S3 row) and D6 (one-hop traversal) are the same primitive split into ''what exists'' and ''how to walk it''; a change to one almost always reshapes the other''s contract.');
INSERT INTO links (id, task_a, task_b, because) VALUES (68, 45, 49, 'D7''s status + stale flag are columns that D3''s CRUD reads and writes, so any change to D7''s status set (e.g. adding wont_do) or to D3''s update behavior forces the other to be re-checked.');
INSERT INTO links (id, task_a, task_b, because) VALUES (70, 40, 50, 'S4 is the append-only history table that realizes D8''s promise that reopening a closed task doesn''t erase prior completion — any change to D8''s history-preservation contract or to S4''s append-only columns forces the other to be re-checked.');
INSERT INTO links (id, task_a, task_b, because) VALUES (71, 45, 51, 'D9''s slice fetch returns one D3 task plus its directly-linked context, so any change to D9''s payload shape or to D3''s task identity/fields forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (72, 46, 51, 'D9''s slice fetch returns the labels (D4) attached to the task as part of the slice payload, so any change to D4''s label semantics or to D9''s payload shape forces the other to be re-checked.');
INSERT INTO links (id, task_a, task_b, because) VALUES (73, 48, 51, 'D9''s slice fetch uses D6''s linked-tasks traversal to populate the slice''s dependencies/dependents, so any change to D6''s traversal contract or to D9''s payload shape forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (74, 48, 52, 'D6 (linked-neighbors traversal) is what D10 (cascade) iterates over to mark stale; a shape change in traversal directly changes who gets cascade-staled.');
INSERT INTO links (id, task_a, task_b, because) VALUES (75, 49, 52, 'D7 (status + stale invariant) is the rule D10 enforces — T123 retired the force-open branch precisely because D7''s invariant got relaxed.');
INSERT INTO links (id, task_a, task_b, because) VALUES (77, 45, 54, 'D12''s close operation is a status-changing variant of D3''s update path that returns the obligation payload, so any change to D12''s close semantics or to D3''s update mechanism forces the other to be re-checked.');
INSERT INTO links (id, task_a, task_b, because) VALUES (78, 48, 54, 'D12 (close-with-obligation) returns D6 (linked neighbors) as the review payload; the obligation shape is whatever D6 walks.');
INSERT INTO links (id, task_a, task_b, because) VALUES (79, 45, 55, 'D13 cascade entry exists specifically because D3 (edit) is the verb that should fire it on an open task; changing D3''s surface changes when the cascade fires.');
INSERT INTO links (id, task_a, task_b, because) VALUES (80, 52, 55, 'D10 is the substrate (mark linked stale) D13 (edit cascade-entry) invokes; D13''s behavior is essentially ''call D10 then apply edit''.');
INSERT INTO links (id, task_a, task_b, because) VALUES (81, 44, 56, 'D14 invariants surface as ValidationError/InvariantError at the D2 boundary on every op; both describe the loud-refusal discipline.');
INSERT INTO links (id, task_a, task_b, because) VALUES (82, 47, 56, 'D14 invariants include the self-link refusal (CHECK task_a<task_b) and the meta-island constraint, both encoded on the D5 link table.');
INSERT INTO links (id, task_a, task_b, because) VALUES (83, 49, 56, 'D14 close-gate enforces ''stale ⇒ ...'' rules and the meta-island bound; D7 is the status taxonomy those rules quantify over.');
INSERT INTO links (id, task_a, task_b, because) VALUES (84, 45, 57, 'D15''s queries return filtered sets of D3 tasks by status/label/stale, so any change to D15''s filter modes or to D3''s task fields forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (85, 46, 57, 'D15 supports filtering tasks by D4 labels (`tasks under a label`), so any change to D4''s label-attach contract or to D15''s filter modes forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (87, 45, 58, 'D16''s narrative render reads D3 task bodies under a given label and concatenates them as markdown, so any change to D3''s name/description fields or to D16''s render contract forces the other to be re-checked.');
INSERT INTO links (id, task_a, task_b, because) VALUES (88, 46, 58, 'D16''s narrative render groups D3 tasks by D4 label to produce the rendered document, so any change to D4''s label contract or to D16''s per-label grouping forces the other to be re-checked.');
INSERT INTO links (id, task_a, task_b, because) VALUES (89, 45, 59, 'D17''s FTS search retrieves D3 tasks by keyword over name + description, so any change to D3''s text fields or to D17''s ranking/index contract forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (90, 41, 59, 'S5 is the FTS5 virtual table that backs D17''s keyword search — any change to D17''s search contract (ranking, indexed fields, ID-prefix lookup) or to S5''s trigger-maintained index forces the other to be revisited.');
INSERT INTO links (id, task_a, task_b, because) VALUES (91, 43, 60, 'D18''s git-tracked text serialization round-trips D1''s SQLite store through tackit.sql, so any change to D1''s store layout or to D18''s dump/sync rules forces the other to be re-checked for compatibility.');
INSERT INTO links (id, task_a, task_b, because) VALUES (96, 46, 63, 'D21''s label-usage view is the human-readable surface over D4''s labels, deriving meaning from usage rather than a label-description column, so any change to D4''s freeform-label stance or to D21''s view shape forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (97, 38, 63, 'D21''s label-usage view queries S2 to count tasks per label and surface examples, so any change to S2''s join shape or to D21''s view contract forces the query layer between them to be re-checked.');
INSERT INTO links (id, task_a, task_b, because) VALUES (98, 48, 64, 'D22''s CLI board uses D6''s linked-tasks traversal to render the needs→/unblocks→ edges per task, so any change to D6''s traversal contract or to D22''s display rules forces the other to be re-checked.');
INSERT INTO links (id, task_a, task_b, because) VALUES (99, 51, 64, 'D22''s CLI board renders per-task views composed of D9 slices (labels + dependencies + dependents), so any change to D9''s slice payload or to D22''s per-task display forces the other to be re-checked.');
INSERT INTO links (id, task_a, task_b, because) VALUES (100, 57, 64, 'D22''s CLI board is a human-readable view over the same filtered task set D15 returns programmatically, so any change to D15''s filter modes or to D22''s grouping rules forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (101, 46, 65, 'D23''s creation-time nudge fires when a new D4 label is introduced, surfacing the existing labels for reuse, so any change to D4''s label set or to D23''s nudge envelope forces the other to be re-checked.');
INSERT INTO links (id, task_a, task_b, because) VALUES (102, 63, 65, 'D23''s creation-time nudge surfaces the existing labels (the same data D21 lists) at the moment a new label is born, so any change to D21''s view shape or to D23''s nudge contents forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (103, 44, 66, 'D24''s bulk-import refuses malformed rows loudly via D2''s typed boundary (missing kind, bad reserved label, unknown dep key), so any change to D2''s validation rules or to D24''s loud-fail roll-back contract forces the other to be re-checked.');
INSERT INTO links (id, task_a, task_b, because) VALUES (104, 45, 66, 'D24''s bulk-import calls D3''s create path (one transaction, key→id resolution) so any change to D3''s create signature or to D24''s batched-create rules forces the other to be re-checked.');
INSERT INTO links (id, task_a, task_b, because) VALUES (105, 47, 66, 'D24''s plan format expresses task relationships via a depends_on key that the loader writes as D5 symmetric links, so any change to D5''s link semantics or to D24''s plan-format rules forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (208, 45, 109, 'D25''s supersede op produces a new task pointing at old via superseded_by — the relationship is created from a D3 (create) plus a metadata mark on the existing row.');
INSERT INTO links (id, task_a, task_b, because) VALUES (209, 37, 109, 'D25 stores its ''superseded_by'' marker as a nullable FK column on S1 tasks; the marker is a tasks-row column, not a separate edge type.');
INSERT INTO links (id, task_a, task_b, because) VALUES (210, 45, 110, 'D26''s required kind classification is a property D3''s create path must demand and reject when missing/invalid, so any change to D26''s kind enum or to D3''s create signature forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (212, 47, 111, 'D27''s link-discovery `links` op walks the D5 symmetric edges to surface candidate neighbors, so any change to D5''s link semantics or to D27''s traversal/filter rules forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (213, 48, 111, 'D27''s link-discovery op is built on D6''s linked-tasks traversal — D27 iterates D6''s depth-1 expansion under a kind/status filter, so any change to D6''s traversal contract or to D27''s candidate-filter forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (214, 59, 111, 'D27''s link-discovery deliberately replaces the v0.2.0 search-before-create workflow that D17''s FTS used to anchor, so any change to D17''s search role or to D27''s deterministic candidate-surfacing forces the other to be re-checked.');
INSERT INTO links (id, task_a, task_b, because) VALUES (215, 110, 111, 'D27''s expansion-hop candidate filter (status=''open'' OR kind in {design,schema}) and meta-island scanning rule are directly defined by D26''s kind taxonomy, so any change to D26''s kind set or boundary forces D27''s filter to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (216, 52, 110, 'D10''s cascade traversal is bounded by D26''s kind taxonomy — the meta-island constraint keeps cascade reach from bleeding between meta and spec/production work — so any change to D26''s kind boundary or to D10''s propagation rules forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (217, 56, 110, 'D26''s kind taxonomy adds the reserved-label and meta-island constraints that D14 must refuse at the boundary, so any change to D26''s kind rules or to D14''s enforcement set forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (275, 137, 138, 'D29''s description_revisions audit table is the backstop that makes D28''s edit-on-closed (stale-as-record-only) safe by preserving prior verbatim state, so any change to D28''s record-only stance or to D29''s audit contract forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (276, 137, 139, 'D30''s design/schema perma-open rule is what keeps the D28 worklist filter (status=''open'' OR kind in {design,schema}) coherent — without D30, closed design/schema would silently drop off the worklist — so any change to either''s scope forces the other to be re-checked.');
INSERT INTO links (id, task_a, task_b, because) VALUES (277, 139, 140, 'D31''s edit-time code-check reminder fires precisely on the kind∈{design,schema} tasks that D30 declares perma-open — both rules together formalize design/schema as living spec — so any change to either kind-clause forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (278, 138, 141, 'S7 is the schema table that realizes D29''s audit-table design, so any change to D29''s audit contract (what''s captured, when) or to S7''s column shape forces the other to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (307, 110, 160, 'D32''s synthesized id-prefix uses the D26 kind letter (design→D, schema→S, production→T, meta→M) as the first character of every displayed/indexed task id, so any change to D26''s kind enum or kind names forces D32''s letter-mapping to be re-evaluated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (315, 56, 163, 'D33 extends D14''s refusal taxonomy from graph-integrity / kind-taxonomy / status-change refusals to rationale-quality refusal at link creation. A change to D14''s overall refusal philosophy (e.g. moving from refuse-loudly to warn-and-record) needs D33 to either align with the new philosophy or carve out an explicit exception.');
INSERT INTO links (id, task_a, task_b, because) VALUES (316, 47, 163, 'D33 is a refusal rule applied at link creation; D5 is the symmetric-link primitive D33 constrains. A change to D5 (e.g. moving to directed edges or splitting links by type) reshapes what "a rationale" attaches to and could invalidate D33''s "every link" universal quantifier.');
INSERT INTO links (id, task_a, task_b, because) VALUES (320, 51, 165, 'D34 extends D9''s slice envelope: it adds two per-dep-entry fields (`because`, `last_edit_delta`) and one top-level field (`because_reminder`). A change to D9''s envelope shape, dep-entry shape, or slice-fetch semantics directly affects D34''s surface additions; conversely D34''s additions are part of D9''s contract once shipped.');
INSERT INTO links (id, task_a, task_b, because) VALUES (321, 53, 165, 'D34''s reminder trigger (≥1 stale dep entry) lives in the same conceptual space as D11''s worklist filter — both ask "is there obligation-bearing stale work near here?". A change to D11''s filter (e.g. broadening to include record-only stale) may want D34 to align its trigger so the reminder appears whenever a reconcile decision is pending.');
INSERT INTO links (id, task_a, task_b, because) VALUES (325, 37, 38, 'S2.task_id is an FK into S1, and S2''s reserved-label refusal is enforced against the kind values declared on S1, so any change to S1''s id type or its kind enum forces a review of S2''s join semantics and label-vs-kind boundary.');
INSERT INTO links (id, task_a, task_b, because) VALUES (326, 37, 39, 'S3.task_a/task_b are FKs into S1 forming the symmetric link pair, so any change to S1''s id semantics or to its per-row status/kind values changes which S3 rows are valid and how cascade traversal joins them back to S1.');
INSERT INTO links (id, task_a, task_b, because) VALUES (327, 37, 40, 'S4 is the append-only log of S1.status transitions, so any addition or rename of a status value on S1 (e.g. wont_do) widens what S4 must store and what its history can replay.');
INSERT INTO links (id, task_a, task_b, because) VALUES (328, 37, 41, 'S5 is the FTS mirror of S1.name+description maintained by triggers and (per T161) of the kind+id synthesized prefix, so any change to those S1 columns or to the prefix rule forces the FTS triggers and indexed text to be revisited.');
INSERT INTO links (id, task_a, task_b, because) VALUES (329, 37, 43, 'D1 promises that all state lives in a single SQLite store, and S1 is the table that embodies that promise for the atomic-task entity, so any column added or removed on S1 is where D1''s "one persistent store" contract is realized or violated.');
INSERT INTO links (id, task_a, task_b, because) VALUES (330, 37, 45, 'D3''s create/read/update operations read and write S1''s columns directly, so any change to S1''s column set or constraints changes what D3 must accept, validate, and return.');
INSERT INTO links (id, task_a, task_b, because) VALUES (331, 37, 110, 'D26 defines the kind taxonomy that S1''s required `kind` column enforces via CHECK + reserved-label rule, so any change to the kind enum or to the meta-island/reserved-label semantics forces S1''s column constraint to change.');
INSERT INTO links (id, task_a, task_b, because) VALUES (333, 42, 60, 'S6 stores the `version` (ordering) and `synced_sql_hash` (integrity) keys that D18''s sync decision reads — any change to D18''s sync signals requires S6''s key set to follow, and any change to S6''s columns changes what signals D18 can use.');
INSERT INTO links (id, task_a, task_b, because) VALUES (334, 49, 50, 'D8 records every transition across the status taxonomy D7 defines — adding, renaming, or retiring a D7 status (e.g. v0.4''s introduction of wont_do) requires D8''s log to handle the new transition class so reopen-after-close history is still recoverable.');
INSERT INTO links (id, task_a, task_b, because) VALUES (335, 49, 53, 'D11''s worklist filter (`status=''open'' OR kind in {design,schema}`) is the operational form of D7''s bounded-obligation rule — any change to D7''s stale semantics or the record-only set shifts what D11 must surface as obligation-bearing.');
INSERT INTO links (id, task_a, task_b, because) VALUES (336, 49, 57, 'D15''s filter axes (status, stale) are public queries over the fields D7 defines — adding a status value or shifting what stale means changes the answers D15 returns for board/work-queue filters.');
INSERT INTO links (id, task_a, task_b, because) VALUES (337, 49, 61, 'D19 emits the stale signal D7 defines on every invocation — any change to D7''s stale semantics (e.g. v0.4 record-only vs. obligation-bearing) shifts what D19 must put in the stale_alert envelope and what it must hold back.');
INSERT INTO links (id, task_a, task_b, because) VALUES (338, 53, 61, 'D19 is the surfacing mechanism for D11''s worklist — the filter D11 defines is exactly what D19 emits in the stale_alert envelope, so a change to either''s filter semantics or wording forces the other to follow.');
INSERT INTO links (id, task_a, task_b, because) VALUES (339, 60, 62, 'D20 gates entry into D18''s finalize path so the `version` counter advances only on genuine mutations — without D20, redundant calls would churn the dump and corrupt the ordering signal D18''s sync decision relies on.');
INSERT INTO links (id, task_a, task_b, because) VALUES (340, 133, 167, 'D35 extends D7''s status taxonomy by adding ''spec'' as a fourth value, permanent for design/schema slices. A change to D7''s other states or their semantics directly affects D35''s scope: e.g. if ''wont_do'' were merged with ''closed'', D35''s refusal predicates would need to follow.');
INSERT INTO links (id, task_a, task_b, because) VALUES (341, 56, 167, 'D35 changes D14''s refusal predicates from kind-conditional to status-derived (close/wont_do refused on status=''spec'' instead of kind in design/schema). A change to D14''s centralized refusal patterns (error envelopes, refusal-message conventions, kind vs status framing) forces D35 to align.');
INSERT INTO links (id, task_a, task_b, because) VALUES (342, 137, 167, 'D35 simplifies D28''s worklist filter from `status=''open'' OR kind in {design,schema}` to `status in {open,spec}`. Same set in practice; D35''s whole point is making the kind clause unnecessary. A change to D28''s bounded-obligation reach affects D35''s filter expression.');
INSERT INTO links (id, task_a, task_b, because) VALUES (343, 139, 167, 'D35 reframes D30''s "perma-open via kind-based close/wont_do refusal" as "perma-spec via status-derived refusal". The set of refused tasks is unchanged; the mechanism shifts from kind to status. A change to D30''s living-spec concept directly affects whether D35''s ''spec'' status is the right semantic match.');
INSERT INTO links (id, task_a, task_b, because) VALUES (344, 37, 167, 'D35 extends S1''s status CHECK constraint to include ''spec'' as a fourth allowed value. A change to S1''s status column shape, default, or NOT NULL constraint directly affects D35''s migration 009 and the runtime model agreement between Pydantic and DDL.');
INSERT INTO links (id, task_a, task_b, because) VALUES (345, 50, 167, 'D35 introduces ''spec'' as a valid to_status value in D8''s transition history, and migration 009 backfills a transition row for every reassigned design/schema task. A change to D8''s append-only invariant or its from/to nullability affects how D35 records the migration event.');
INSERT INTO links (id, task_a, task_b, because) VALUES (359, 167, 171, 'D35 and D36 are sibling slices completing the design/schema lifecycle: D35 adds status=''spec'' for living specs; D36 adds status=''retired'' for fully abandoned specs + the kind/status partition rule that constrains both halves + the retire() verb to reach ''retired''. They ship together via the same mig 009 transaction; changes to either slice''s scope (e.g., redefining what counts as ''spec'' or ''retired'') affect the other''s framing.');
INSERT INTO links (id, task_a, task_b, because) VALUES (363, 171, 172, 'D37 follows D36''s propagation principle pattern — discipline rules belong on every agent-facing surface (SKILL.md, MCP docstrings, CLI help, README). D37''s surface enumeration is a direct application of D36''s principle. Changes to D36''s propagation framing (e.g., adding a new surface category) affect D37''s surface list and vice versa.');
INSERT INTO links (id, task_a, task_b, because) VALUES (395, 165, 197, 'D38 refines what a valid `because` expresses (a coupling consequence, not a membership category) — that IS the axis D34 surfaces for the FAST filter. If D34''s because/delta surfacing or filter framing changes, D38''s discriminator rule must be re-checked, and vice versa.');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (80, 37, NULL, 'open', '2026-05-31T04:11:47.873715+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (81, 38, NULL, 'open', '2026-05-31T04:11:47.876334+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (82, 39, NULL, 'open', '2026-05-31T04:11:47.876420+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (83, 40, NULL, 'open', '2026-05-31T04:11:47.876464+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (84, 41, NULL, 'open', '2026-05-31T04:11:47.876512+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (85, 42, NULL, 'open', '2026-05-31T04:11:47.876548+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (86, 43, NULL, 'open', '2026-05-31T04:11:47.876582+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (87, 44, NULL, 'open', '2026-05-31T04:11:47.876617+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (88, 45, NULL, 'open', '2026-05-31T04:11:47.876651+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (89, 46, NULL, 'open', '2026-05-31T04:11:47.876687+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (90, 47, NULL, 'open', '2026-05-31T04:11:47.876721+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (91, 48, NULL, 'open', '2026-05-31T04:11:47.876754+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (92, 49, NULL, 'open', '2026-05-31T04:11:47.876786+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (93, 50, NULL, 'open', '2026-05-31T04:11:47.876818+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (94, 51, NULL, 'open', '2026-05-31T04:11:47.876854+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (95, 52, NULL, 'open', '2026-05-31T04:11:47.876886+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (96, 53, NULL, 'open', '2026-05-31T04:11:47.876917+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (97, 54, NULL, 'open', '2026-05-31T04:11:47.877023+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (98, 55, NULL, 'open', '2026-05-31T04:11:47.877059+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (99, 56, NULL, 'open', '2026-05-31T04:11:47.877091+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (100, 57, NULL, 'open', '2026-05-31T04:11:47.877123+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (101, 58, NULL, 'open', '2026-05-31T04:11:47.877154+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (102, 59, NULL, 'open', '2026-05-31T04:11:47.877187+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (103, 60, NULL, 'open', '2026-05-31T04:11:47.877219+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (104, 61, NULL, 'open', '2026-05-31T04:11:47.877252+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (105, 62, NULL, 'open', '2026-05-31T04:11:47.877289+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (106, 63, NULL, 'open', '2026-05-31T04:11:47.877325+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (107, 64, NULL, 'open', '2026-05-31T04:11:47.877358+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (108, 65, NULL, 'open', '2026-05-31T04:11:47.877391+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (109, 66, NULL, 'open', '2026-05-31T04:11:47.877424+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (110, 37, 'open', 'closed', '2026-05-31T04:11:47.939696+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (111, 38, 'open', 'closed', '2026-05-31T04:11:47.944720+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (112, 39, 'open', 'closed', '2026-05-31T04:11:47.949502+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (113, 40, 'open', 'closed', '2026-05-31T04:11:47.954147+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (114, 41, 'open', 'closed', '2026-05-31T04:11:47.961015+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (115, 42, 'open', 'closed', '2026-05-31T04:11:47.965662+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (116, 43, 'open', 'closed', '2026-05-31T04:11:47.970903+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (117, 44, 'open', 'closed', '2026-05-31T04:11:47.975721+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (118, 45, 'open', 'closed', '2026-05-31T04:11:47.980570+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (119, 46, 'open', 'closed', '2026-05-31T04:11:47.985405+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (120, 47, 'open', 'closed', '2026-05-31T04:11:47.990356+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (121, 48, 'open', 'closed', '2026-05-31T04:11:47.995193+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (122, 49, 'open', 'closed', '2026-05-31T04:11:47.999893+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (123, 50, 'open', 'closed', '2026-05-31T04:11:48.004938+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (124, 51, 'open', 'closed', '2026-05-31T04:11:48.009649+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (125, 52, 'open', 'closed', '2026-05-31T04:11:48.015263+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (126, 53, 'open', 'closed', '2026-05-31T04:11:48.020091+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (127, 54, 'open', 'closed', '2026-05-31T04:11:48.025322+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (128, 55, 'open', 'closed', '2026-05-31T04:11:48.030030+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (129, 56, 'open', 'closed', '2026-05-31T04:11:48.034768+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (130, 57, 'open', 'closed', '2026-05-31T04:11:48.039664+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (131, 58, 'open', 'closed', '2026-05-31T04:11:48.044357+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (132, 59, 'open', 'closed', '2026-05-31T04:11:48.049354+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (133, 60, 'open', 'closed', '2026-05-31T04:11:48.053797+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (134, 61, 'open', 'closed', '2026-05-31T04:11:48.058559+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (135, 62, 'open', 'closed', '2026-05-31T04:11:48.063398+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (136, 63, 'open', 'closed', '2026-05-31T04:11:48.068523+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (137, 64, 'open', 'closed', '2026-05-31T04:11:48.073517+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (138, 65, 'open', 'closed', '2026-05-31T04:11:48.078611+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (139, 66, 'open', 'closed', '2026-05-31T04:11:48.083638+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (194, 48, 'closed', 'open', '2026-06-01T01:44:19.385148+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (195, 56, 'closed', 'open', '2026-06-01T01:44:19.385311+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (196, 66, 'closed', 'open', '2026-06-01T01:44:19.385404+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (198, 51, 'closed', 'open', '2026-06-01T01:46:39.102050+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (199, 52, 'closed', 'open', '2026-06-01T01:46:39.102195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (200, 54, 'closed', 'open', '2026-06-01T01:46:39.102270+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (201, 64, 'closed', 'open', '2026-06-01T01:46:39.102321+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (202, 55, 'closed', 'open', '2026-06-01T01:47:17.318934+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (207, 38, 'closed', 'open', '2026-06-01T01:50:21.506146+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (208, 39, 'closed', 'open', '2026-06-01T01:50:21.506286+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (209, 40, 'closed', 'open', '2026-06-01T01:50:21.506357+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (210, 41, 'closed', 'open', '2026-06-01T01:50:21.506412+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (211, 43, 'closed', 'open', '2026-06-01T01:50:21.506456+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (212, 45, 'closed', 'open', '2026-06-01T01:50:21.506493+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (213, 46, 'closed', 'open', '2026-06-01T01:50:27.061211+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (214, 63, 'closed', 'open', '2026-06-01T01:50:27.061336+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (215, 47, 'closed', 'open', '2026-06-01T01:50:33.295486+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (216, 109, NULL, 'open', '2026-06-01T01:51:35.832168+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (217, 110, NULL, 'open', '2026-06-01T01:51:47.643876+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (218, 111, NULL, 'open', '2026-06-01T01:52:06.009897+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (253, 44, 'closed', 'open', '2026-06-01T04:38:50.056068+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (259, 44, 'open', 'closed', '2026-06-01T04:41:42.686958+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (283, 133, NULL, 'open', '2026-06-01T06:06:38.591368+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (306, 137, NULL, 'open', '2026-06-01T22:22:03.373227+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (307, 138, NULL, 'open', '2026-06-01T22:22:03.373297+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (308, 139, NULL, 'open', '2026-06-01T22:22:03.373360+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (309, 140, NULL, 'open', '2026-06-01T22:22:03.373421+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (310, 141, NULL, 'open', '2026-06-01T22:22:03.374346+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (325, 109, 'open', 'wont_do', '2026-06-01T22:32:35.455703+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (339, 156, NULL, 'open', '2026-06-01T23:30:39.270649+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (343, 160, NULL, 'open', '2026-06-02T01:23:31.674418+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (346, 163, NULL, 'open', '2026-06-02T03:07:55.252209+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (348, 165, NULL, 'open', '2026-06-02T03:19:44.307155+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (359, 167, NULL, 'open', '2026-06-02T04:54:12.306883+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (372, 171, NULL, 'open', '2026-06-02T07:22:12.812688+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (373, 172, NULL, 'open', '2026-06-02T07:26:37.592470+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (381, 37, 'closed', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (382, 38, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (383, 39, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (384, 40, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (385, 41, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (386, 42, 'closed', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (387, 43, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (388, 44, 'closed', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (389, 45, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (390, 46, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (391, 47, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (392, 48, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (393, 49, 'closed', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (394, 50, 'closed', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (395, 51, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (396, 52, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (397, 53, 'closed', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (398, 54, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (399, 55, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (400, 56, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (401, 57, 'closed', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (402, 58, 'closed', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (403, 59, 'closed', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (404, 60, 'closed', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (405, 61, 'closed', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (406, 62, 'closed', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (407, 63, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (408, 64, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (409, 65, 'closed', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (410, 66, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (411, 109, 'wont_do', 'retired', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (412, 110, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (413, 111, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (414, 133, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (415, 137, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (416, 138, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (417, 139, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (418, 140, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (419, 141, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (420, 156, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (421, 160, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (422, 163, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (423, 165, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (424, 167, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (425, 171, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (426, 172, 'open', 'spec', '2026-06-02T21:24:49.984195+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (477, 197, NULL, 'spec', '2026-06-04T16:44:45.843384+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (480, 199, NULL, 'spec', '2026-06-04T17:17:46.467614+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (485, 202, NULL, 'spec', '2026-06-04T17:36:52.807447+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (494, 207, NULL, 'spec', '2026-06-04T20:46:55.467284+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (501, 211, NULL, 'spec', '2026-06-05T08:04:29.435795+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (503, 213, NULL, 'spec', '2026-06-05T08:26:46.942767+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (1, 133, 'D7 (relaxed) — Status + stale flag (T123)', 'A task''s status is open or closed -- informational only; it never gates traversal. A separate stale flag marks a task as one whose linked neighbors changed under it and must be reviewed. T123 (2026-06-01) RETIRED the v0.2.0 invariant ''stale => open'' that the original D7 (T49, superseded) carried: cascade-staling a closed neighbor now leaves status=''closed'' + stale=True, signalling ''the upstream changed; review for supersede / link migration'' while keeping the closed task immutable per T118 (no-edit-closed). The action menu on closed-stale: reconcile (clear stale, status untouched), supersede (replace the premise with a new task), link_rm / link_add (migrate edges), label_add / label_rm. edit() is STILL refused on any closed task -- T118 is unchanged. The no-op rule (D20) and the close-gate (D14: close refused if stale or linked-stale, applies regardless of status) are also unchanged.', 'rewrote D7 for v0.4 bounded-obligation: closed/wont_do stale is record-only per D28; reconcile refused on closed/wont_do; edit-on-closed safe per D29; supersede action menu removed', '2026-06-01T23:23:55.536042+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (2, 52, 'D10 — Stale propagation (mark-before-change)', 'Before a task is edited, mark its linked tasks (both endpoints'' view; D6) stale, leaving status untouched (T123: closed neighbors stay closed + stale=True per the relaxed D7 invariant). Recording the obligation before the mutation makes an interrupted reconciliation crash-safe: the marks survive a dead session. Propagation is one hop — it does not cascade transitively on its own; it flows only where a real change actually happens (D13).
Cascade bounded by kind (D26): the cascade fires both directions of any link, but the meta-island constraint (D26: a link_add between a meta task and a non-meta task is refused at D14) means the propagation cannot bleed between meta work and spec/production work — there are no links across the kind boundary to traverse.
No-op discipline (see D20): staling here fires only on an actual content change — edit field-diffs against the stored value and, if nothing differs, does not stale linked tasks (and does not bump version). This is D10''s instance of the general no-op rule defined in D20.
Closed-stale action menu (T123): a closed task carrying stale=True is reviewed via reconcile (still accurate; clear stale), supersede (premise replaced; create new task), or link_rm + link_add (migrate edges to a replacement). edit() is still refused on closed (T118).', 'rewrote D10 for v0.4 bounded-obligation: worklist filter D28 restricts obligation set; closed/wont_do stale record-only and reconcile refused; meta-island cascade-bounding role shared with D28; supersede references removed; edit-on-closed safe per D29', '2026-06-01T23:24:15.001272+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (3, 55, 'D13 — Change operation with cascade entry', 'Editing a task that has links first marks its linked tasks stale (D10/T123), then applies the edit, then returns the now-stale set. T123 (2026-06-01) retired the v0.2.0 ''force-open on stale'' rule: closed neighbors stay closed + stale=True and the agent reviews them via reconcile / supersede / link migration rather than reopen-and-edit. This is the entry point of the change-time cascade: reconciling each stale task may, if it too changes, mark its own linked neighbors — propagation flowing only where real changes occur, and bounded by the kind boundary (D10/D26).', 'rewrote D13 for v0.4: cascade mechanics unchanged but obligation set filtered by D28; supersede references removed; edit-on-closed safe per D29 with S7 audit table; code-check reminder per D31 on design/schema edits', '2026-06-01T23:25:27.259522+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (4, 56, 'D14 — Invariant enforcement', 'Refuse operations that would leave the graph inconsistent and surface the violation loudly: FK (a link to a nonexistent task is rejected); no self-link (link_add(a, a) refused by S3 CHECK task_a < task_b); no duplicate link (S3 UNIQUE on the canonical pair); reserved-label refusal (label_add or load with a label string equal to a reserved kind name — design/schema/production/meta — is refused; those four are reserved for the kind property on S1, per D26); meta-island constraint (link_add between a meta task and a non-meta task is refused: the kind boundary bounds the cascade so that meta work cannot drag spec/production tasks into a stale review and vice versa, per D26); close-gate (ratified 2026-05-29; extended 2026-05-30; updated v0.3.0): close is refused while the task itself is stale, or while it transitively shares a link with any stale task — a task whose linked neighborhood is unreconciled cannot be marked done, because that neighborhood may still change beneath it ("transitively linked" walks the symmetric link graph; in practice the meta-island constraint keeps the reach bounded). The agent must reconcile the named stale task(s) before closing.
Note: under symmetric semantics there is no dependency cycle to detect — an undirected edge has no cycle in the directed sense. The v0.2.0 acyclicity check is retired.', 'rewrote D14 for v0.4: close-gate "transitively linked stale" walk uses D28 bounded filter; close+wont_do refused on design/schema per D30; no double-decide per T132; reconcile refused on closed/wont_do; supersede references removed', '2026-06-01T23:25:46.364988+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (5, 110, 'D26 — Task kind taxonomy (added v0.3.0)', 'Every task carries a required kind in {design, schema, production, meta}, set at create and refused if missing or invalid. The taxonomy splits tasks by whether they alter the running app''s behavior (the classifier rule):
- design: a design.md slice (D#). Decisions, not code.
- schema: a schema.md table (S#). The store''s shape.
- production: code that alters the running app''s behavior, including source under tackit/, the README and SKILL.md (those alter the agent''s behavior of the app), test code that pins behavior contracts.
- meta: work that does not alter the running app — release bookkeeping, experiments, dogfood notes, side-investigations.
Meta-island constraint: a link_add between a meta-kind task and a non-meta-kind task is refused (D14). The intent: meta work is separate from the app''s substance, so the cascade bounded above (D10) cannot bleed from meta into production-or-spec or vice versa. A meta task may link other meta tasks freely; a non-meta task may link any other non-meta task.
Reserved label names: the four kind names are reserved as label strings (S2): label_add and load refuse a label with the same string. This prevents the previous v0.2.0 convention where design/schema were labels — the kind property absorbs that distinction and a stray label would silently disagree.', 'rewrote D26 for v0.4: reframed meta-island (now keeps meta bookkeeping graph clean; cascade-reach-bounding role shared with D28 worklist filter); added perma-open D30 + inheritance trap T122 + worklist scope notes', '2026-06-01T23:26:13.494064+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (6, 111, 'D27 — Link discovery via `links` op (added v0.3.0)', 'Replaces the v0.2.0 "search-before-create" workflow with a deterministic candidate-surfacing loop. The links op: with no input → returns all design + schema kind tasks (the anchor layer — the spec tasks that production work should link to); with a list of task ids → returns every task linked at depth=1 to any input id, minus the input ids themselves and minus any "already-seen" set the caller passes back. Iteration is caller-driven — tackit holds no state between calls; the agent loops links(anchors), links(next_layer), … until satisfied.
Why this replaces "search-before-create": the dep-discovery experiments (docs/plan/dep-discovery-experiments.md) found that no single retrieval method dominates and that the agent must do the semantic judgment about which surfaced candidates are real links. Search is recall-limited (the J experiment); enumerating the whole tree is context-bloating. The links op surfaces depth-1 anchors deterministically and the agent judges each one — never skipping a surfaced candidate. The semantics live in the agent; the deterministic surfacing lives in tackit.
Discovery flow (per SKILL.md, T99/T72): (1) classify the new task''s kind; (2) for production, call links with no input → judge the design+schema anchor layer; (3) call links(judged_anchors) for depth-1 expansion; (4) judge that layer; (5) iterate or stop; (6) wire via link_add. For meta, scan within the meta-island only. For design/schema, scan within the same layer.', 'rewrote D27 for v0.4: expansion-hop output filtered to status=''open'' OR kind in {design,schema} per D28; closed/wont_do production/meta excluded as candidates; anchor-layer query unchanged (design/schema perma-open)', '2026-06-01T23:26:31.682332+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (7, 37, 'S1 — tasks', 'The atomic item. One row per task; this is the single source of truth a task''s every view is derived from.
Columns: id (INTEGER, PK, monotonic auto-increment, stable and agent/human-friendly); name (TEXT, NOT NULL, short title); description (TEXT, the detail/body, stored in the DB — no external detail files); kind (TEXT, NOT NULL, CHECK in (design, schema, production, meta) — required at create, classifies the task by whether it alters the running app; D26); status (TEXT, NOT NULL, CHECK in (open, closed), default open — lifecycle, informational, never gates traversal); stale (BOOLEAN, NOT NULL, default false — dirty bit; invariant: stale=true ⇒ status=''open'', enforced in logic, D7/D14); superseded_by (INTEGER, NULL allowed, FK → tasks.id, CHECK superseded_by <> id — marks this task as replaced by a newer one; D25); created_at (TIMESTAMP, NOT NULL); updated_at (TIMESTAMP, NOT NULL).
Backs: D1, D3, D7, D9, D12, D13, D15, D25, D26.', 'rewrote S1 for v0.4: dropped superseded_by column (mig 006); added wont_do status + wont_do_reason column; status CHECK now (open/closed/wont_do); described S7 description_revisions as the supersede-marker replacement', '2026-06-01T23:26:51.906828+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (8, 133, 'D7 — Status + stale flag (v0.4 bounded-obligation)', 'A task''s status is one of {open, closed, wont_do} — informational only; it never gates traversal. A separate stale flag marks a task as one whose linked neighbors changed under it.

Bounded obligation (D28 v0.4): the cascade still writes stale=True on neighbors mechanically (depth-1, both endpoints per D6 symmetric semantics, regardless of status). What v0.4 changed is which stale flags carry an obligation. The obligation worklist filter restricts to status=''open'' OR kind in {design, schema}. Closed and wont_do production/meta tasks carrying stale=True are RECORD ONLY: the flag stays as historical signal that an upstream changed (visible via show()), but they are not on the worklist, do not pressure the close-gate (D14 v0.4), and cannot be reconciled — reconcile() is refused on closed/wont_do because clearing a record-only marker would erase the signal without meaning.

Status semantics: open = work outstanding; closed = work shipped; wont_do = scope dropped with durable reason. Status transitions are recorded in status_transitions (S4). edit() is allowed on any status under D29 v0.4 — the description_revisions audit table (S7) preserves the prior verbatim name+description+delta on every edit, so editing closed prose for fix-ups no longer destroys history. v0.3.0''s no-edit-closed convention is retired; the supersede verb is gone.

The no-op rule (D20) and the close-gate (D14 v0.4: close/wont_do refused if the task is itself obligation-bearing-stale, or transitively shares a link with one — closed/wont_do record-only stale neighbors do not trip the gate) are unchanged in their respective scopes.', 'T156 refinement: reconcile refusal now mirrors D28 worklist filter — refused only on closed/wont_do production/meta; design/schema reconcilable regardless of status, so legacy pre-D30 rows can drain off the worklist', '2026-06-02T01:51:35.116000+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (9, 56, 'D14 — Invariant enforcement', 'Refuse operations that would leave the graph inconsistent and surface the violation loudly.

Graph integrity:
- FK: a link to a nonexistent task is rejected.
- No self-link: link_add(a, a) refused by S3 CHECK task_a < task_b.
- No duplicate link: S3 UNIQUE on the canonical pair.

Kind taxonomy (D26):
- Reserved-label refusal: label_add or load with a label string equal to a reserved kind name (design/schema/production/meta) is refused; those four are reserved for the kind property on S1.
- Meta-island constraint: link_add between a meta task and a non-meta task is refused. Keeps the meta bookkeeping graph clean of production/spec coupling (under v0.4 the cascade-reach-bounding role is shared with the worklist filter D28).

Status-change refusals:
- Close-gate (v0.4 bounded per D28): close and wont_do are refused if the task is itself obligation-bearing-stale, OR if it transitively shares a link with any obligation-bearing-stale task. The "transitively linked stale" walk filters to status=''open'' OR kind in {design, schema} — closed/wont_do record-only stale neighbors (D7/D10 v0.4) do NOT trip the gate. The agent must reconcile the named obligation-bearing stale task(s) before closing.
- Design/schema perma-open (D30 v0.4): close() and wont_do() are refused on any task with kind in {design, schema}. Those are living spec, not work items; the verb for updating them is edit() (D29). To "retire" a decision, edit the slice to reflect its current state — the description_revisions audit table (S7) preserves the prior verbatim version.
- No double-decide (T132): close() refused if status is already closed or wont_do; wont_do() refused if status is already closed or wont_do. The change-of-mind path on a wont_do task is to create a fresh task with the new direction.

reconcile refusal (D7 v0.4): reconcile() is refused on closed/wont_do tasks. Closed/wont_do stale is record-only historical signal; clearing it would erase the signal without meaning.

Under symmetric link semantics (D5) there is no dependency cycle to detect — an undirected edge has no cycle in the directed sense. The v0.2.0 acyclicity check is retired.', 'T156: refined reconcile refusal rule — refused iff (closed/wont_do) AND kind in {production, meta}; design/schema always reconcilable. Mirrors D28 worklist filter', '2026-06-02T01:51:45.571561+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (10, 141, 'S6 — description_revisions', 'Append-only audit table backing D29. Columns: id (INTEGER, PK); task_id (INTEGER, FK -> tasks.id); prev_name (TEXT, NULL — pre-edit name, NULL if unchanged); prev_description (TEXT, NULL — pre-edit description, NULL if unchanged); delta (TEXT, NOT NULL — rationale from the edit op); edited_at (TIMESTAMP, NOT NULL); rows never updated or deleted. Written by core.edit() on every edit that actually changes name or description (no-op edits skipped per D20). Read by core.history(). Migration 007 adds this table.', 'renumber from S6 to S7 — collides with T42 (''S6 — meta''); the audit table is S7 per src/tackit/schema.py:114 (S7_DESCRIPTION_REVISIONS)', '2026-06-02T01:53:31.478588+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (11, 49, 'D7 — Status + stale flag', 'A task''s status is open or closed — informational only; it never gates traversal. A separate stale flag marks a task as open specifically because something it depends on changed under it. Setting stale also forces open (invariant: stale ⇒ open). open-because-new-work and open-because-stale are queryable apart.', 'Updated status taxonomy from open/closed to open/closed/wont_do (v0.4) and retired the stale⇒open force-reopen invariant per D28 bounded obligation + T123 closed-stale relax.', '2026-06-02T02:32:17.421299+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (12, 59, 'D17 — Full-text search', 'Find tasks by keyword over name + description, ranked, via SQLite FTS5 — in-process, no external service or GPU. Returns matching task ids + titles + scores. This is the agent''s entry point for finding tasks (e.g. the ids to depend on) before fetching a slice; search → show is tackit''s retrieval loop, replacing grep-over-a-monolith. Keyword only; semantic/vector search is explicitly out of scope (a recall optimization with heavy deps). FTS5 is strictly better than grep for this (tokenized, ranked, indexed), but it is only as good as how discoverably tasks are written — see the skill-pack note under "Deferred."', 'Added D32 FTS prefix-indexing: indexed name now carries the synthesized `<kind_letter><id>` prefix so search by id-prefix (e.g. ''T238'', ''D23'') works (T161 v0.4).', '2026-06-02T02:32:21.839368+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (13, 53, 'D11 — Reconciliation worklist', 'List all stale tasks. This is the resumable worklist for a reconciliation pass that may outlive a session or context window: a new session asks "what''s stale?" and resumes exactly where the last left off. The pass is done when the list is empty (termination marker).', 'Documented D28 worklist filter (status=''open'' OR kind in {design,schema}) — what was "all stale tasks" is now the filtered set; closed/wont_do production/meta stale is record-only.', '2026-06-02T02:34:57.955571+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (14, 41, 'S5 — tasks_fts', 'An FTS5 virtual table indexing tasks.name + tasks.description for ranked keyword search (D17). Kept in sync with tasks by triggers (insert/update/delete). No external dependency — FTS5 ships with SQLite.
Columns: rowid (INTEGER, = tasks.id); name (TEXT, indexed); description (TEXT, indexed).
Backs: D17.', 'Documented D32 prefix-indexing: INSERT/UPDATE triggers store `<kind_letter><id> — <name>` into FTS rather than bare tasks.name (T161 v0.4 + migration 008).', '2026-06-02T02:35:01.779775+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (15, 156, 'v0.4 gap: legacy closed/wont_do design/schema slices stuck on worklist (no reconcile path)', 'Surfaced 2026-06-01 while reconciling the v0.4 slice-edit cascade. The v0.4 spec is internally inconsistent for legacy closed/wont_do design/schema rows:

- D28 worklist filter admits a task as obligation-bearing when status=''open'' OR kind in {design, schema}. This pulls closed/wont_do design/schema slices onto the worklist when an upstream edits.
- D7 v0.4 (and the reconcile op) unconditionally refuses reconcile() on closed/wont_do tasks: "REFUSED: T<id> is closed -- reconcile is not allowed on terminal tasks (D28). Their stale flag is record-only (not on the worklist, not blocking close-gates); it stays as historical signal."
- D30 forbids new close/wont_do on design/schema (perma-open going forward) but does nothing about LEGACY closed/wont_do design/schema rows that pre-date D30.

Net effect on the dogfood DB right now: after the v0.4 slice-edit pass (T148–T154), 5 legacy closed/wont_do design/schema rows are pinned on the worklist with no exit path:
- T37 (closed schema, S1 — was closed in v0.3.0 pre-D30; I just edited it for v0.4 prose, so it''s accurate)
- T44 (closed design, D2 typed validation boundary — content unchanged in v0.4)
- T49 (closed design, D7 v0.2.0 predecessor of T133 — content is stale: describes the v0.2.0 stale=>open invariant retired in T123 + supersede mechanism dropped in v0.4)
- T59 (closed design, D17 full-text search — content unchanged in v0.4)
- T109 (wont_do design, D25 supersede marker — the supersede decision was wont_do''d when v0.4 retired supersede; content is correctly stale)

The reconcile error message *claims* "(not on the worklist)" but the worklist explicitly lists them. The error and the worklist alert disagree.

Three candidate fixes (one or a combination):

(A) **Tighten the worklist filter to status=''open''**. Drops the "design/schema is living spec so include closed" intent. Justified under D30 perma-open if we accept that legacy closed design/schema rows are historical-only and should not surface for reconcile. Side effect: a closed-design downstream of an edited slice wouldn''t get reviewed; under D30 there should be no such case going forward, but legacy rows would silently carry stale=True forever.

(B) **Loosen reconcile to mirror the worklist filter**. Allow reconcile on (status=''open'' OR kind in {design, schema}). Matches the alert behavior exactly. Clean spec consistency. Side effect: reconciling a closed-design row that was "still correct" clears the historical signal of "upstream changed under this" — but for legacy rows that signal is mostly noise.

(C) **Data migration: reopen() all legacy closed/wont_do design/schema rows** to bring the DB into D30-compliant state, then reconcile freely. Pros: single one-time fix; afterwards D28 and D7 v0.4 are consistent because no closed design/schema rows exist. Cons: a one-time DB write that mutates historical status; existing close timestamps in status_transitions get a follow-on open transition.

Recommendation will depend on whether we view "closed design/schema" as a legitimate state to preserve (then go B + maybe C as a one-time backfill) or a v0.3.0 mistake to retroactively undo (then C). The worklist filter (D28) intent and the reconcile refusal (D7 v0.4) intent need to be aligned either way — current state is contradictory.

Until this is decided, the dogfood worklist will never empty after a v0.4 slice edit, even when the edits are correct and downstream prose is accurate.', 'Documented shipped resolution: reconcile() refusal now mirrors D28''s worklist filter (option B from the original three-candidate analysis). Was an open question with three candidates and recommendation pending; now a resolved-and-shipped record.', '2026-06-02T04:23:19.692425+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (16, 165, 'D34 — Surface cascade-ergonomics rationales (link `because` + upstream edit `delta`) with a DRY FAST-filter reminder', 'The cascade-ergonomics FAST filter (T116/T117) compares an upstream''s `delta` (semantic shift) against a link''s `because` (coupling axis) to skip re-reading a stale dependent when the two don''t intersect. Both inputs are persisted today — `because` on `links` (T116), edit `delta` in S7 `description_revisions` (D29 v0.4) — but **neither is surfaced** in the show/board envelopes or in the stale_alert payload. So the FAST filter is operationally unreachable: the data exists; the agent can''t see it during a reconciliation walk.

**The rule:**
On any envelope that includes one or more dependency entries (currently `show`, `board`) where at least one of those entries has `stale=True`, the envelope MUST surface:

1. **Per dep entry: the link''s `because`** — read from `links.because` for the canonical (a, b) pair.
2. **Per dep entry: the upstream''s most-recent edit `delta`** — read as the `delta` from the most recent `description_revisions` row whose `task_id` is the dependency''s id. Null if the upstream has never been edited.
3. **Top-level `because_reminder` field** — a single DRY-sourced string (B-tier verbosity) explaining what those two fields are for and how the FAST filter uses them. Same envelope position as `code_check_reminder` / `label_nudge` / `stale_alert` / `delta` (the per-op fields). Null when the trigger condition (≥1 stale dep entry) is not met.

**The reminder string (single source, exported constant):**
> *"Each link''s `because` describes WHY the two tasks are coupled. Each cascade-firing op''s `delta` describes the upstream''s semantic shift. When reviewing a stale dependent, compare the upstream''s `delta` against the link''s `because`: if the semantic shift doesn''t intersect the coupling axis, `reconcile` without re-reading the dependent (FAST path); otherwise re-read and edit-or-reconcile (SLOW path)."*

DRY: defined exactly once (e.g. `core.LINK_BECAUSE_REMINDER`); referenced by every emission site. No duplication, no per-site drift.

**Trigger** — emit the reminder iff the result envelope includes at least one stale dependency entry. Not on every show (noisy on read-only browsing); not gated on top-level `stale_alert` non-null (misses cases where the global worklist is empty but this particular task has a stale neighbor).

**Surfaces in scope:**
- `show` envelope — both `dependencies` and `dependents` lists get the two new fields per entry; top-level `because_reminder` per trigger.
- `board` envelope — same.
- `stale_alert` payload — for each stale task listed, ideally also include its upstream(s) + because + delta. But this requires schema-level work to identify "which upstream(s) staled me" (currently not tracked). **Out of scope for this slice**; pin to a follow-up.

**Known partial coverage:**
`reclassify()` is a cascade-firing op (per skill) that does not write S7 (it doesn''t change name/description). Its `delta` is ephemeral — gone after the op''s response envelope drops from the agent''s context. Same for any future cascade-firing op that doesn''t go through edit''s S7-writing path. In those cases, the surfaced `delta` field is null; the agent falls back to SLOW-path re-reading. Acceptable, because (a) reclassify is rare in normal work, (b) the FAST filter still works for the common edit() case which is the majority of cascade events.

**Why this isn''t D33''s job:**
D33 enforces rationale QUALITY at link creation. D34 surfaces the rationales AND deltas so the FAST filter can actually run. They''re complementary: D33 makes the data worth surfacing; D34 makes it accessible.

Backs: D9 (slice fetch — adds two fields to its dep entries + one top-level field), D11 (reconciliation worklist — same trigger condition''s home concept), D29 (description_revisions, the source of persisted deltas), T116 (per-edge because), T117 (delta concept).', 'Updated reminder text to orientation-first phrasing (read both BEFORE opening; FAST-skip is the rare exception) per the 2026-06-02 empirical finding that on hub-spec edits FAST-skip rarely applies but orientation always does. Added documentation of the non-edge-context behavior (`links()` op leaves the per-edge fields None) and noted that board() inherits the surfaces via show.', '2026-06-02T04:46:42.847302+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (17, 167, 'D35 — Spec status value: design/schema slices use status=''spec'' (retires the kind-conditional perma-open framing)', '**The problem D35 fixes** (surfaced 2026-06-02): the current model conflates two orthogonal things in the `status` column — lifecycle (open / closed / wont_do) and kind-driven liveness (design and schema are perma-spec, not work items). Counts of "open" mislead because they include perma-spec slices that aren''t work items, and every report has to caveat: "of N open, only M are actual work items." That caveat is a structural tax on every count, every dogfood pass, every report. The mechanics work correctly — worklist filter, close-gate, reconcile refusal all do the right thing — but the framing is confusing and propagates user-visible noise.

**The decision:** add a new status value `spec`. Design and schema slices live at `status=''spec''` permanently. `open` regains its plain meaning ("work item to do"). The kind-conditional clauses in the worklist filter, close-gate, wont_do refusal, and reconcile refusal collapse into clean status-derived predicates.

**Predicate simplifications under D35:**

- **Worklist filter** (D28): from `stale=1 AND (status=''open'' OR kind IN (''design'',''schema''))` to `stale=1 AND status IN (''open'',''spec'')`. Kind clause removed.
- **close/wont_do refusal** (D30): from "refused if kind IN (''design'',''schema'')" to "refused if status=''spec''". Same set in practice; framing matches the column.
- **reconcile refusal** (T156): from "refused iff (status closed/wont_do AND kind production/meta)" to "refused iff status IN (''closed'',''wont_do'')". The exception for closed/wont_do design/schema collapses because design/schema rows won''t have those statuses going forward; the legacy population gets migrated to ''spec'' (mig 009).
- **Add()''s default status**: when `kind IN (''design'',''schema'')`, the new row''s status defaults to ''spec''; otherwise ''open''. Determinable from kind at create time.

**What stays unchanged:**

- Production and meta tasks use `open/closed/wont_do` exactly as today.
- The lifecycle semantics for production/meta are unchanged.
- D29 description_revisions writes still fire on edit regardless of status.
- D31 code-check reminder still fires on design/schema edits (and now equivalently on status=''spec'' edits, since they''re the same set).
- D32 prefix convention is status-agnostic.

**Migration 009** (SCHEMA_VERSION 9→10): bumps the S1 status CHECK to `(open, closed, wont_do, spec)`. `UPDATE tasks SET status=''spec'' WHERE kind IN (''design'',''schema'')` — including the legacy closed/wont_do design/schema rows that D156''s reconcile-mirror handled at the predicate level (under D35, the population goes through the new status instead of the kind exception). Adds a status_transitions row for each reclassified task documenting the migration-time transition.

**Existing D# / S# prose that needs editing** under D35: D7 (status taxonomy adds ''spec''), D14 (refusal predicates change), D28 (worklist filter simplifies), D30 (refusal becomes status-derived), S1 (status CHECK extended), D8 (transition history needs to handle ''spec'' transitions). The mechanics are unchanged; the prose is. D156 (the legacy stuck-rows resolution) can be re-edited to note that D35 obviates option B by making the kind clause unnecessary.

**Out of scope:**
- Any change to the open/closed/wont_do semantics for production/meta tasks.
- Any change to D32 prefix conventions, D33 placeholder refusal, D34 reminder surfacing — these are status-agnostic.
- Renaming legacy D#/S# slot prefixes (per the existing D32 grandfathering rule).

**Why not Path 1 (display-layer fix):**
Path 1 (default filter in ls()/board() to kind in {production, meta}; split counts as "X work items, Y spec slices"; reword docs) is cheaper but doesn''t fix the underlying model. Anyone reading the DB directly or building a new surface re-encounters the confusion. Path 2 (this slice) fixes it at the column.

**Cost:** roughly 2-3 days of focused work — migration 009, every status-checking call site in core.py, CLI + MCP surface updates, status filter handling, a sweep of D7/D14/D28/D30/D8/S1 prose, SKILL.md updates, and the test sweep. The user explicitly flagged "shipping v0.4 first, revisit Path 2 with cross-project experience informing the design" — so D35 is filed but parked for a deliberate next-version effort (v0.5 candidate), not for in-line execution.

Backs: D7 (status taxonomy — D35 extends it), D14 (refusal taxonomy — D35 makes refusals status-derived), D28 (worklist filter — D35 simplifies it), D30 (perma-open rule — D35 reframes it as status-based), S1 (tasks table schema — D35 changes the CHECK constraint), D8 (status transition history — D35 adds ''spec'' transitions including the migration-time backfill).', 'Tightened D35 to spec-status-only scope: retired status, retire() verb, kind/status partition rule, and all-or-nothing discipline split out to NEW sibling D36 — D35 originally didn''t account for fully-abandoned specs (gap surfaced during impl brainstorming reviewing D109/D25''s wont_do design state).', '2026-06-02T07:21:24.857968+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (31, 37, 'S1 — tasks', 'The atomic item. One row per task; this is the single source of truth a task''s every view is derived from.

Columns:
- id (INTEGER, PK, monotonic auto-increment, stable and agent/human-friendly).
- name (TEXT, NOT NULL, short title).
- description (TEXT, the detail/body, stored in the DB — no external detail files).
- kind (TEXT, NOT NULL, CHECK in (design, schema, production, meta) — required at create, classifies the task by whether it alters the running app; D26).
- status (TEXT, NOT NULL, CHECK in (open, closed, wont_do), default open — lifecycle, informational, never gates traversal). v0.4 added wont_do as a distinct end state (decided not to do, with durable reason), separating it from closed (work shipped).
- stale (BOOLEAN, NOT NULL, default false — dirty bit; carried regardless of status under v0.4 bounded-obligation D28: closed/wont_do stale=True is record-only historical signal, not on the worklist).
- wont_do_reason (TEXT, NULL when status != wont_do, NOT NULL when status=''wont_do'' — durable reason captured at wont_do() time; no edit API, persists forever).
- created_at (TIMESTAMP, NOT NULL).
- updated_at (TIMESTAMP, NOT NULL).

The v0.3.0 superseded_by column (FK → tasks.id) was added in mig 005 and DROPPED in mig 006 (v0.4). The supersede verb was retired (D29) because it required tasks to be atomic enough that "premise replaced" applied to the whole bundle, which broke down in practice when only one of several facets was invalidated. The description_revisions audit table (S7) — append-only rows recording the prior verbatim name+description+delta on every edit — replaces the marker mechanism: any edit (including edits to closed/wont_do tasks per D29) preserves prior state in S7, so editing in place no longer destroys history.

Backs: D1, D3, D7 (status + stale flag), D9, D12 (close), D13 (edit/cascade entry), D15 (queries), D26 (kind taxonomy), D29 (audit + edit-on-closed), D30 (perma-open for design/schema — enforced at the status-change verbs, not at the S1 level).', 'v0.5 D35+D36 update: extended status CHECK from 3 to 5 values; added partition CHECK (production/meta gets open/closed/wont_do; design/schema gets spec/retired); documented wont_do_reason''s dual role (wont_do reason on production/meta; retire reason on design/schema — the partition guarantees one terminal verb per row).', '2026-06-03T02:46:14.932707+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (32, 133, 'D7 — Status + stale flag (v0.4 bounded-obligation)', 'A task''s status is one of {open, closed, wont_do} — informational only; it never gates traversal. A separate stale flag marks a task as one whose linked neighbors changed under it.

Bounded obligation (D28 v0.4): the cascade still writes stale=True on neighbors mechanically (depth-1, both endpoints per D6 symmetric semantics, regardless of status). What v0.4 changed is which stale flags carry an obligation. The obligation worklist filter restricts to status=''open'' OR kind in {design, schema}.

Closed and wont_do production/meta tasks carrying stale=True are RECORD ONLY: the flag stays as historical signal that an upstream changed (visible via show()), but they are not on the worklist and do not pressure the close-gate (D14 v0.4). reconcile() is refused on closed/wont_do PRODUCTION/META — clearing a record-only marker would erase the signal without meaning.

Closed and wont_do DESIGN/SCHEMA tasks (T156 v0.4 refinement, 2026-06-02): are obligation-bearing per D28''s kind clause AND reconcilable. The original v0.4 D28 spec admitted them onto the worklist (kind∈{design,schema}) but refused reconcile on terminal status across all kinds — leaving legacy pre-D30 closed-design/schema rows pinned on the worklist with no exit path. T156 resolves the contradiction by mirroring the reconcile-refusal filter to the worklist filter: reconcile is refused iff (status closed/wont_do) AND (kind in {production, meta}). Design/schema slices stay reconcilable regardless of status — consistent with their "living spec" framing — so the worklist can drain.

Status semantics: open = work outstanding; closed = work shipped; wont_do = scope dropped with durable reason. Status transitions are recorded in status_transitions (S4). edit() is allowed on any status under D29 v0.4 — the description_revisions audit table (S7) preserves the prior verbatim name+description+delta on every edit, so editing closed prose for fix-ups no longer destroys history. v0.3.0''s no-edit-closed convention is retired; the supersede verb is gone.

The no-op rule (D20) and the close-gate (D14 v0.4: close/wont_do refused if the task is itself obligation-bearing-stale, or transitively shares a link with one — closed/wont_do record-only stale neighbors do not trip the gate) are unchanged in their respective scopes.', 'v0.5 D35+D36 update: extended status taxonomy from 3 to 5 values; documented kind/status partition; retired the v0.4 perma-open framing as superseded by D35+D36''s partition rule (perma-open IS now the spec status, with retired as its terminal counterpart).', '2026-06-03T02:46:14.951265+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (33, 56, 'D14 — Invariant enforcement', 'Refuse operations that would leave the graph inconsistent and surface the violation loudly.

Graph integrity:
- FK: a link to a nonexistent task is rejected.
- No self-link: link_add(a, a) refused by S3 CHECK task_a < task_b.
- No duplicate link: S3 UNIQUE on the canonical pair.

Kind taxonomy (D26):
- Reserved-label refusal: label_add or load with a label string equal to a reserved kind name (design/schema/production/meta) is refused; those four are reserved for the kind property on S1.
- Meta-island constraint: link_add between a meta task and a non-meta task is refused. Keeps the meta bookkeeping graph clean of production/spec coupling (under v0.4 the cascade-reach-bounding role is shared with the worklist filter D28).

Status-change refusals:
- Close-gate (v0.4 bounded per D28): close and wont_do are refused if the task is itself obligation-bearing-stale, OR if it transitively shares a link with any obligation-bearing-stale task. The "transitively linked stale" walk filters to status=''open'' OR kind in {design, schema} — closed/wont_do record-only stale neighbors (D7/D10 v0.4) do NOT trip the gate. The agent must reconcile the named obligation-bearing stale task(s) before closing.
- Design/schema perma-open (D30 v0.4): close() and wont_do() are refused on any task with kind in {design, schema}. Those are living spec, not work items; the verb for updating them is edit() (D29). To "retire" a decision, edit the slice to reflect its current state — the description_revisions audit table (S7) preserves the prior verbatim version.
- No double-decide (T132): close() refused if status is already closed or wont_do; wont_do() refused if status is already closed or wont_do. The change-of-mind path on a wont_do task is to create a fresh task with the new direction.

reconcile refusal (D7 v0.4 + T156 v0.4): reconcile() is refused iff (status closed/wont_do) AND (kind in {production, meta}). The filter mirrors D28''s worklist filter exactly so what surfaces as obligation-bearing is what can be reconciled. Closed/wont_do production/meta stale is record-only historical signal; clearing it would erase the signal without meaning. Closed/wont_do design/schema (legacy pre-D30 rows) IS reconcilable, since design/schema is living spec by kind and reconciling acknowledges the slice still describes truth after the upstream changed.

Under symmetric link semantics (D5) there is no dependency cycle to detect — an undirected edge has no cycle in the directed sense. The v0.2.0 acyclicity check is retired.', 'v0.5 D35+D36 update: replaced kind-conditional refusal patterns with status-derived ones; documented 5 new refusals introduced by D36 (retire on non-spec; retire on open neighbor with (i)/(ii) decision tree; link_add on retired endpoint; reconcile on retired; reclassify cross-partition with no clean target).', '2026-06-03T02:46:14.970009+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (34, 137, 'D28 — Bounded-obligation cascade: closed-stale as record-only', 'Cascade still writes stale=True on linked neighbors depth-1 on edit/reclassify (unchanged). What changes is OBLIGATION: closed and wont_do tasks carrying stale=True are RECORD ONLY — visible in show, not on the stale() worklist, not triggering the close-gate. Worklist filter: status=''open'' OR kind in {design,schema}. Close-gate''s transitive ''linked to stale'' walk uses the same filter. Reason: T120''s pre-ergonomics data (7 stale / 5 rubber-stamps / 1 true-positive / 0% FAST) showed cascade-through-closed was paying its cost without delivering its payoff. The bounded model preserves the dependency-check value (catches schema-mediated coupling) while eliminating recursive-supersede-through-history.', 'v0.5 D35+D36 update: worklist filter predicate shifted from kind-conditional (status=''open'' OR kind IN design/schema) to status-derived (status IN (''open'',''spec'')); retired added to the record-only stale set (its stale flag does NOT pressure the worklist, exactly like closed/wont_do production/meta).', '2026-06-03T02:46:14.985866+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (35, 139, 'D30 — Design/schema as perma-open: kind-based close + wont_do refusal', 'close() and wont_do() refuse on any task with kind in {design, schema}, structured error directing the user to edit() (audit table preserves prior state). Design and schema slices are LIVING SPEC — they represent decisions in effect; updating a decision is edit, not close. Retiring a decision means editing the slice to reflect ''no longer in effect''; the audit table preserves the prior version. Belt-and-suspenders: the worklist filter (D28) is robust to any design/schema task that somehow ends up closed (via tests or migration shim) — the kind clause keeps them visible regardless of status.', 'v0.5 D35+D36 update: appended a redirect/navigation-anchor section noting that D30''s perma-open framing is now realized canonically by D35 (spec status) + D36 (retired status + partition + retire verb). D30 remains as the historical statement of the principle; D35+D36 are the operational mechanism.', '2026-06-03T02:47:21.796306+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (36, 50, 'D8 — Status transition history', 'Every status change is recorded with a timestamp. Reopening a closed task therefore does not erase the fact that it was completed earlier: the live status is current working state; the log is the history.', 'v0.5 D35+D36 update: ''spec'' and ''retired'' are now valid to_status values in status_transitions rows; mig 009 backfilled a transition row for every design/schema row migrated from ''open''→''spec'' or ''wont_do''→''retired'' so the audit log carries the migration moment.', '2026-06-03T02:47:21.812744+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (37, 110, 'D26 — Task kind taxonomy (added v0.3.0)', 'Every task carries a required kind in {design, schema, production, meta}, set at create and refused if missing or invalid. The taxonomy splits tasks by whether they alter the running app''s behavior (the classifier rule):

- design: a design.md slice (D#). Decisions, not code. Perma-open under D30 v0.4 (close/wont_do refused on this kind; edit() updates the slice and the description_revisions audit table S7 preserves the prior verbatim version).
- schema: a schema.md table (S#). The store''s shape. Same perma-open rule as design.
- production: code that alters the running app''s behavior, including source under tackit/, the README and SKILL.md (those alter the agent''s behavior of the app), and test code that pins behavior contracts.
- meta: work that does NOT alter the running app — release bookkeeping, experiments, dogfood notes, side-investigations.

Inheritance trap (T122): classify a NEW task by its OWN scope, not by the parent epic''s framing. A task spawned during a meta thread that itself includes impl work is production — not meta — regardless of where the discussion happened.

Meta-island constraint: a link_add between a meta-kind task and a non-meta-kind task is refused (D14). Under v0.4 the load-bearing role of this constraint is NARROWER than v0.3.0 framed: the worklist filter (D28) bounds the OBLIGATION cascade — closed/wont_do production/meta stale is record-only and the worklist excludes non-{open|design|schema} — regardless of which kinds are linked. What the meta-island still does is keep the meta bookkeeping graph clean of production/spec coupling: meta tasks (release notes, observation writeups, dogfood logs) stay structurally separate from the app''s substance, so a release-tracking task can''t show up wired to a production source change. A meta task may link other meta tasks freely; a non-meta task may link any other non-meta task.

Worklist filter scope (D28 v0.4): the worklist''s "status=''open'' OR kind in {design, schema}" rule means design and schema slices stay on the worklist when they go stale even though they don''t close (perma-open D30). Legacy pre-D30 closed design/schema rows that pre-date D30 still surface on the worklist for the same reason.

Reserved label names: the four kind names are reserved as label strings (S2): label_add and load refuse a label with the same string. This prevents the v0.2.0 convention where design/schema were labels — the kind property absorbs that distinction and a stray label would silently disagree.', 'v0.5 D35+D36 update: documented that kind is now COUPLED to status via the partition CHECK on S1 — kind no longer just classifies; it ALSO determines which status values a row may carry. Production/meta partition: open/closed/wont_do; design/schema partition: spec/retired. Cross-partition kind change (reclassify) auto-shifts status open↔spec, or refuses if no clean target.', '2026-06-03T02:47:21.828691+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (38, 111, 'D27 — Link discovery via `links` op (added v0.3.0)', 'Replaces the v0.2.0 "search-before-create" workflow with a deterministic candidate-surfacing loop. The links op: with no input → returns all design + schema kind tasks (the anchor layer — the spec tasks that production work should link to); with a list of task ids → returns every task linked at depth=1 to any input id, filtered to viable link targets, minus the input ids themselves and minus any "already-seen" set the caller passes back. Iteration is caller-driven — tackit holds no state between calls; the agent loops links(anchors), links(next_layer), … until satisfied.

Candidate filter (D27 v0.4 / D28): the expansion-hop output restricts to status=''open'' OR kind in {design, schema}. Closed/wont_do production/meta neighbors are NOT surfaced as candidates — they cannot be productive link targets going forward (the work is done or dropped; coupling to them would be archaeology, not coupling). The anchor-layer query (links() with no input) is unchanged — design/schema slices are perma-open under D30 so the question doesn''t arise there. This filter aligns the surface with the worklist-filter scope (D28) so what you''d reconcile and what you''d link to share the same boundary.

Why this replaces "search-before-create": the dep-discovery experiments (docs/plan/dep-discovery-experiments.md) found that no single retrieval method dominates and that the agent must do the semantic judgment about which surfaced candidates are real links. Search is recall-limited (the J experiment); enumerating the whole tree is context-bloating. The links op surfaces depth-1 anchors deterministically and the agent judges each one — never skipping a surfaced candidate. The semantics live in the agent; the deterministic surfacing lives in tackit.

Discovery flow (per SKILL.md): (1) classify the new task''s kind; (2) for production, call links with no input → judge the design+schema anchor layer; (3) call links(judged_anchors) for depth-1 expansion; (4) judge that layer; (5) iterate or stop; (6) wire via link_add with a real because rationale describing the coupling. For meta, scan within the meta-island only. For design/schema, scan within the same layer.', 'v0.5 D35+D36 update: links() candidate filter shifted from kind-conditional to status-derived (status IN (''open'',''spec'')); retired design/schema rows are EXCLUDED from both the anchor layer (no-input mode) and the expansion hop. T180 closed the anchor-query residual that Phase 2a''s enumeration missed.', '2026-06-03T02:47:21.843351+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (39, 138, 'D29 — Description revisions audit table', 'Append-only audit table capturing every successful edit''s prior name and description plus the delta rationale. Backstop for the ''edit on closed/wont_do is allowed'' decision: descriptions can be updated in place but verbatim prior state is recoverable for archaeology. Replaces the v0.3.0 supersede marker (D25) — the marker addressed ''don''t get misled by old prose'' via inline-tagging hits with a superseder id; the audit table addresses it by preserving the prior verbatim version under the SAME task id. Simpler model, same archaeology capability. history() op extends to return description_revisions alongside status_transitions.', 'v0.5 D35+D36 update: clarified retire() does NOT append a description_revisions row (status change, not content edit — same as close/wont_do); the durable retire reason lives in wont_do_reason column (column reused per partition semantics, see S1 v0.5 update).', '2026-06-03T02:48:30.120652+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (40, 140, 'D31 — Code-check reminder on design/schema edit', 'When edit() succeeds on a task with kind in {design, schema}, the response envelope includes a structured reminder: ''this slice''s number (D#/S#) is referenced in code by convention; double-check the associated files for drift.'' Replaces agent-instinct (which fails — SKILL.md guidance alone doesn''t reliably fire) with tool-side mechanical nudge. No code-introspection; tackit doesn''t know which files reference D# — the reminder names the slice id+name and trusts the agent to grep. Sibling concept to label_nudge and stale_alert.', 'v0.5 D35+D36 update: documented that the D31 reminder fires on edits to retired slices too (since the kind clause is unchanged — both spec and retired are kind in design/schema). For retired edits the framing shifts from ''check live code references'' to cleanup: ''verify no lingering code references this dead decision''.', '2026-06-03T02:48:30.138920+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (41, 163, 'D33 — Link creation requires explicit `because`: refuse placeholder rationales at all creation paths', 'Every link-creation path in tackit must require a real, caller-supplied `because` rationale. Placeholder/convenience-default rationales (e.g. `"(established at task creation)"`, `"(established via bulk load)"`) are refused at the boundary.

**Why:** the cascade-ergonomics filter (T116) compares `delta × because` to FAST-skip a stale neighbor without re-reading it. A placeholder rationale carries zero signal — it filters nothing, so every cascade hit through such an edge must take the slow path. Convenience shortcuts that ship placeholders silently corrupt the SNR of the entire cascade system: the link LOOKS wired but is functionally dead-weight to the reconciler. Concretely: of 312 links in this DB at the time of writing, 269 (86%) carry a placeholder or pre-T116 marker; the 36 add-deps placeholders are the recurring failure mode this rule prevents.

**Scope of the rule:**
- `link_add(a, b, because, delta)` — already refused on empty `because` (T116). Unchanged.
- `add(name, kind, deps=...)` shortcut — currently hardcodes `because="(established at task creation)"` for each dep edge (core.py:336). Must refuse OR change the API to take per-dep rationales (e.g. `deps: list[tuple[int, str]]` or a parallel `because_per_dep` map).
- `load(plan)` bulk-import — currently hardcodes `because="(established via bulk load)"` (core.py:397). The plan format must carry a per-link `because`; refuse the import if any link entry omits one. Bulk-load is a high-leverage placeholder source: one import can wire dozens of meaningless edges in a single op.
- Any future op that creates links must go through the same contract.

**Out of scope:**
- Retroactively re-rationalizing the 233 pre-T116 marker links + 36 add-deps placeholders + (n) bulk-load placeholders. Existing markers stay as historical record of "we don''t know why this was linked"; a separate backfill task can address the high-value subset (e.g. the 89 edges touching design/schema + an open endpoint). Editing an old link''s `because` is a future API question.
- Detecting "vague" but non-placeholder rationales (e.g. "setup", "test fixture"). That''s a rationale-quality judgement, not a refusal rule — a vague rationale is worse than a placeholder for the human reading it but is at least caller-asserted intent.

**Why not just delete the shortcuts?** The shortcuts are ergonomically valuable when their rationales are real — adding a task and wiring its links in one call is a common pattern. Keep the shortcuts; tighten their contract.

Backs: D14 (invariant enforcement — D33 extends the refusal taxonomy from graph-integrity / kind-taxonomy / status-change refusals to rationale-quality refusal), T116 (which established the per-edge `because` field but didn''t lock down all creation paths).', 'v0.5 D36 update: D33''s placeholder-rationale refusal extends to retire()''s reason field — empty / whitespace-only / ''TBD'' / ''TODO'' / ''obsolete'' / ''no longer needed'' are refused at the validation boundary. Same shape as link_add because + wont_do reason; the rationale must carry signal because it''s persisted forever in wont_do_reason.', '2026-06-03T02:48:30.153929+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (42, 156, 'v0.4 gap: legacy closed/wont_do design/schema slices stuck on worklist (no reconcile path)', '**RESOLVED** by reconcile()-mirrors-worklist refinement (v0.4 cluster commit 583df90, code lives at core.py:820-852).

**Gap** (surfaced 2026-06-01): D28''s worklist filter admitted a stale task as obligation-bearing when `status=''open'' OR kind in {design, schema}`, which pulled closed/wont_do design/schema slices onto the worklist. But reconcile() refused unconditionally on all closed/wont_do tasks. The refusal message claimed "(not on the worklist)" while the worklist explicitly listed them. The two rules contradicted, leaving 5 legacy rows pinned on the worklist with no exit path (T37, T44, T49, T59, T109 in the dogfood DB at the time).

**Resolution** (option B from the original three-candidate analysis): reconcile()''s refusal condition was tightened to mirror D28''s worklist filter exactly:

> `reconcile()` is REFUSED iff (status in {closed, wont_do}) AND (kind in {production, meta}).

Equivalently: reconcile is allowed iff `status=''open'' OR kind in {design, schema}` — the same predicate D28 uses to define the worklist. So what surfaces as obligation-bearing is exactly what can be reconciled, by construction.

For closed/wont_do **design/schema** (perma-open in spirit per D30 but legacy-closed pre-D30), the slice IS the obligation: reconciling it acknowledges the slice still describes truth after the upstream change. For closed/wont_do **production/meta**, the original D28 rationale stands: their stale flag is record-only archaeology — clearing it would erase the historical signal that an upstream changed.

**Why not (A)** tighten the worklist filter to just `status=''open''`: would hide legitimate spec drift on closed design/schema slices, which are still the live contract under D30. The legacy 5-row population would silently carry stale=True forever and the agent would never review it.

**Why not (C)** data migration to reopen all legacy closed design/schema rows: mutates historical close timestamps and adds noise to status_transitions; option B handles the same population without rewriting history.

D30 prevents NEW closed design/schema rows going forward; the reconcile mirror handles the legacy population. The contradiction between alert and reconcile is gone — they share one predicate.', 'v0.5 D35+D36 update: noted that D35+D36 obviate D156''s kind-conditional reconcile exception entirely — mig 009 migrates the legacy closed/wont_do design population to spec/retired, and the partition CHECK refuses creating new closed/wont_do design rows. The exception D156 carved out for legacy design rows in the closed/wont_do set no longer applies to any v0.5-shape row.', '2026-06-03T02:48:30.168156+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (56, 54, 'D12 — Close operation with obligation payload', 'Closing a task sets status=closed and returns, in the result, the task''s direct dependencies and dependents — the one-hop set the agent is obliged to review on close. The obligation rides in the operation''s response, not in a separate instruction the agent might have forgotten.', 'M112 fix: D12 prose updated for v0.3.0+ symmetric semantics — "direct dependencies and dependents" → "directly linked tasks (D6)"; no contract change, prose alignment with the underlying mechanism.', '2026-06-03T08:33:31.138622+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (57, 45, 'D3 — Task create / read / update', 'Create a task (auto-assigned monotonic id, name, description), read it back, and edit its name/description. The atomic unit of work; everything else attaches to it.', 'M121 fix: D3 prose updated to reflect kind as required-at-create (T94 ergonomics gap landed v0.3.0 but D3''s prose never caught up) — added kind to the create signature + the D26/D36 partition coupling; no contract change, prose alignment.', '2026-06-03T08:33:36.698108+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (59, 207, 'D41 — SKILL.md is instructions, not documentation: why/do/don''t-do format, cut standalone narrative, cite-don''t-narrate, MCP harmony', 'SKILL.md has drifted into documentation-with-a-narrative-arc; it should be a strict list of behavioral instructions. This slice is the standard the rewrite (and all future SKILL edits) must obey. Subsumes the Q4 pattern/anti-pattern request (the format below IS the pattern/anti-pattern, generalized).

## The format — every BEHAVIORAL instruction
```
why:        <the rationale: what it solves / what breaks without it. This is
            the encapsulated context — reading it tells the agent WHY the rule
            exists. ALWAYS present.>
do:         <the directive. ALWAYS present.>
don''t-do:   <the concrete anti-pattern. Present ONLY when there''s a real one;
            omit when it would be vacuous (Q4 discretion — `show` has no
            meaningful "wrong example").>
```
why+do are mandatory; don''t-do is conditional. An instruction whose `why:` isn''t clear is, by definition, poorly written — fix it, don''t leave the why implicit.

## What is NOT an instruction (stays as reference, NOT forced into the format)
Pure mechanics/definitions: the kind/status partition table, what a read op returns, the auto-id prefix synthesis rule, the report-format template. These are reference; leave them as tables/definitions.

## Cut standalone narrative
do: delete war stories, worked-example retellings, version archaeology (the v0.3→v0.4→v0.5 parentheticals), and motivational preambles.
don''t-do: don''t keep a beginning/middle/end arc; SKILL.md is not a story.

## Cite-don''t-narrate (the key move)
The full incident/example already lives in the tackit task body it''s about (T179''s anchoring incident, D25''s retire story, T115''s inheritance-trap case). The instruction cites it in a clause ("see T179") instead of re-telling it. tackit IS the archive — the skill must not duplicate what the task records.

## No duplication, demote superlatives
- No paragraph appears verbatim twice (today: "Edits aren''t free" pasted 3× in the MCP edit docstrings; the T179 story in 2–3 places).
- At most ONE rule may be framed as "the most important" (today four sections each claim it). Pick one (ship-on-pain) or none.

## Intro
3–4 lines max: what tackit is + why it exists. No more standalone context than that.

## MCP harmony (indirect, not 1:1)
The skill and MCP are different surfaces (propagation principle): SKILL teaches cross-cutting disciplines at session-start; a docstring describes ONE operation at call-moment. Harmony rule: each discipline''s FULL statement lives once, in SKILL; the docstring carries the op''s contract (params/refusals/return) + the one-line sharp edge of any discipline that bites at THAT call + a cite to the SKILL section — never the full discipline paragraph. Docstrings get shorter + scoped, NOT reformatted into why/do/don''t (that genre is SKILL''s). This preserves each-surface-teaches-at-its-moment without verbatim duplication.

## Test + sync impact
Presence-pinning tests (test_d37_docstrings, test_d38_docstrings) assert phrases that will move — update them with the rewrite. Re-sync the 3 dev SKILL copies (dev-copies-match test).

## Target
~35–40% reduction (~1060 → ~620–680 lines) with ZERO loss of any behavioral directive. Every cut is narrative/duplication/archaeology, never a rule.', 'Fold-back: corrected the ~35-40% estimate to the measured ~56%; recorded that the presence-pin tests are the rewrite safety net (caught 6 moved/dropped load-bearing phrases).', '2026-06-04T21:25:47.137138+00:00');
INSERT INTO description_revisions (id, task_id, prev_name, prev_description, delta, edited_at) VALUES (61, 197, 'D38 — Links are coupling, labels are membership: the rollup / hub / membership-link anti-patterns', 'Names and forbids the "fake task" family that the dogfood surfaced, and removes the SKILL.md endorsement (the "epic pattern" snippet) that caused it.

## The core distinction (the thing that''s easy to miss)
A **link** is a claim about *consequence*: "if X''s content changes, Y must be re-examined." That is literally what the cascade fires. A **label** is a claim about *category*: "X and Y belong to the same grouping." Categories have no consequence — editing one sibling does not invalidate another. Links carry cascade semantics; labels are dumb tags.

**The test, applied at every `link_add`:** "If I edited X''s body right now, would I genuinely need to re-open Y and check it still holds?" Yes → coupling → link. "No, they''re just both part of the same epic/theme" → membership → label.

**The `because` is the discriminator** (extends D34): a coupling `because` names a *consequence* ("citations FK references documents.id; a column rename here breaks the join"). A membership `because` restates a *category* ("part of the plan-import epic", "schema-ingest cluster"). When the `because` you are about to write is just the cluster''s label name reworded, the edge is a membership link masquerading as coupling — drop it and attach the label instead.

## The anti-patterns (named, for recognition + review)
- **Hub task** — a task whose purpose is to be linked-to (a membership magnet). Cost: accumulates membership links; over the cluster''s life every edit to the hub stales N bystanders and every edit to any member stales the hub — ~N² false-positive stale flags, all zero-value.
- **Membership link** — an edge encoding category, not consequence. Cost: each is a permanent false-positive stale generator that trains the FAST filter into rubber-stamping (the "Edits aren''t free" failure mode), so the cascade stops catching *real* drift.
- **Rollup task** — a task whose body is a hand-maintained status ledger of *other* tasks. Cost: (1) duplicates state tackit already tracks (status/labels), and the hand-typed copy drifts the instant a real task closes — the exact drift tackit exists to prevent, reintroduced inside tackit; (2) gets edited as a side-effect of *dependents* finishing, firing the full neighbor sweep at moments unrelated to most neighbors (backwards cascade).
- Umbrella term: **fake task** — a task that is not a unit of work (no deliverable, no decision) and exists only to be a link target or to hold a rollup.

## The positive patterns (what to do instead — lead with these)
- **Group a cluster with a shared label, never with links to an anchor.** This replaces the deleted "epic pattern" snippet, which told agents to BOTH label the cluster AND link the members to an anchor — encoding membership twice and creating the cascade hub.
- **Coverage is a query, not a task.** To answer "is this cluster complete?", run `board(label=X)` / `ls(label=X)` for the live membership and compare against the expected set. The expected set (the denominator) lives in the design/schema slice — or memory — that *defines* it, never in a hand-typed status table inside a task body.

## Legitimate vs fake — the boundary (do NOT over-apply)
A design/schema slice that captures a *decision* and is linked by the impl tasks that *realize* it is NOT a hub — those are coupling links (edit the decision ⇒ re-review the realizing impl). The entire `links` / dep-discovery model depends on decision-bearing slices being linked-to. What is forbidden is a *content-free* task that exists only for membership or rollup. The separating variable is semantic (does the node carry a decision/contract?), not structural (degree, because-similarity).

## Detection: considered and REJECTED as brittle (2026-06-04, user decision)
Heuristic auto-detection at add/edit/link_add (rollup-body regex; high-degree + near-duplicate-because hub detector) was evaluated and rejected. High-precision separation of a fake hub from a legitimately-central decision slice is semantic, not structural — degree + because-similarity fire on exactly the legitimate slices we want to keep (e.g. D36, linked by every realizing impl task with similar becauses). A warn-level heuristic would add noise to the very FAST-filter SNR this rule protects; refuse-level would block legitimate work. tackit''s established philosophy (P1 dep-discovery reframe — deterministic surface, agent judges) already covers it: the agent holding the tool IS the semantic judge; no separate detector belongs in the MCP.

## The lesson behind the lesson (prompt-engineering, why removal beats addition)
The failure that produced this slice was NOT a missing prohibition — the agent already had "don''t scatter to-dos" / "not a knowledge base" and built the hub anyway, because a *contradictory positive instruction* (the epic-anchor snippet) endorsed it. A wrong "do" beats a right "don''t" every time. Hence: the highest-leverage fix is *removing the contradictory endorsement*, then *leading with the positive pattern* (label to group, query for coverage). The anti-pattern names serve *recognition* (catch yourself mid-act) and *review*, not generation.

## Surfaces (propagation per the propagation principle)
SKILL.md (replace the epic snippet in the Labels section + add this discipline section + tighten the Right-size and Write-real-because bullets), README for-agents discipline block, MCP `add()` docstring (the missing too-large / hub / rollup direction — currently only catches too-small) + `link_add()` docstring (because = consequence not category), and a presence-pinning test mirroring test_d37_docstrings.py.

Backs/refines: D34 (because semantics + FAST filter) — coupling link to it.', 'Phase 2 fold-back (T219): record that the v0.5 removal pass (T198) missed the "umbrella" synonym; add the edge-vs-prose companion axis and the anchor→hub vocabulary-collision fix.', '2026-06-05T20:34:01.060649+00:00');
COMMIT;
