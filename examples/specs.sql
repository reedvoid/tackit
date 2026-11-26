BEGIN TRANSACTION;
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (37, 'S1 — tasks', 'The atomic item. One row per task; this is the single source of truth a task''s every view is derived from.

Columns:
- id (INTEGER, PK, monotonic auto-increment, stable and agent/human-friendly).
- name (TEXT, NOT NULL, short title).
- description (TEXT, the detail/body, stored in the DB — no external detail files).
- kind (TEXT, NOT NULL, CHECK in (design, schema, production, meta) — required at create, classifies the task by whether it alters the running app; D26).
- status (TEXT, NOT NULL, CHECK in (open, closed, wont_do), default open — lifecycle, informational, never gates traversal). v0.4 added wont_do as a distinct end state (decided not to do, with durable reason), separating it from closed (work shipped).
- stale (BOOLEAN, NOT NULL, default false — dirty bit, set only on live rows (status open or spec) and cleared by reconcile, D137; schema version 11 cleared it on every closed, wont_do and retired row).
- wont_do_reason (TEXT, NULL when status != wont_do, NOT NULL when status=''wont_do'' — durable reason captured at wont_do() time; no edit API, persists forever).
- created_at (TIMESTAMP, NOT NULL).
- updated_at (TIMESTAMP, NOT NULL).

The v0.3.0 superseded_by column (FK → tasks.id) was added in mig 005 and DROPPED in mig 006 (v0.4). The supersede verb was retired (D29) because it required tasks to be atomic enough that "premise replaced" applied to the whole bundle, which broke down in practice when only one of several facets was invalidated. The description_revisions audit table (S7) — append-only rows recording the prior verbatim name+description+delta on every edit — replaces the marker mechanism: any edit (edits to closed and wont_do tasks are refused, D259) preserves prior state in S141, so editing in place no longer destroys history.

Backs: D1, D3, D7 (status + stale flag), D9, D12 (close), D13 (edit/cascade entry), D15 (queries), D26 (kind taxonomy), D29 (audit), D259 (closed tasks immutable), D30 (perma-open for design/schema — enforced at the status-change verbs, not at the S1 level).

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
realization), T173 (Phase 2a predicates), T174 (Phase 2b retire verb).', 'spec', 0, '2026-05-31T04:11:47.872506+00:00', '2026-11-26T04:34:06.443452+00:00', 'schema', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (38, 'S2 — task_labels', 'Freeform many-to-many tags. No separate labels dimension table — labels carry no attributes or state, so a join table of plain strings is the whole thing (avoids over-normalization).
Reserved label names (added v0.3.0; D26): the four kind values (design, schema, production, meta) are reserved — label_add and load refuse a label with any of these strings. The kind property on tasks (S1) absorbs that distinction; a stray label of the same string would silently disagree. Enforced in logic at the D14 boundary, not in DDL (to allow migration-time backfill).
Columns: task_id (INTEGER, FK → tasks.id); label (TEXT, NOT NULL, the tag string, e.g. release, smoke-test); PRIMARY KEY (task_id, label) — a task can''t carry the same label twice.
Backs: D4, D9, D15, D16.', 'spec', 0, '2026-05-31T04:11:47.874201+00:00', '2026-11-26T04:22:47.673074+00:00', 'schema', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (39, 'S3 — links', 'The single edge type. Symmetric — one row per unordered pair, stored in canonical order (lower id first). Queries return the same row regardless of which endpoint you start from (D6). Renamed from `dependencies` in v0.3.0; see migration 003 (T86).
Columns: id (INTEGER, PK); task_a (INTEGER, NOT NULL, FK → tasks.id — canonical endpoint, lower id); task_b (INTEGER, NOT NULL, FK → tasks.id — canonical endpoint, higher id); CHECK (task_a < task_b) — symmetric pair, canonical order (also prevents self-link); UNIQUE (task_a, task_b) — a pair can be linked at most once.
Note: under symmetric semantics there is no cycle invariant; an undirected edge has no cycle in the directed sense.
Backs: D5, D6, D9, D10, D12, D13, D14, D27.', 'spec', 0, '2026-05-31T04:11:47.876369+00:00', '2026-11-26T04:22:47.673074+00:00', 'schema', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (40, 'S4 — status_transitions', 'Append-only history of status changes, so reopening a closed task never erases the fact that it was completed earlier. The live status on tasks is current working state; this is the record of how it got there.
Columns: id (INTEGER, PK); task_id (INTEGER, FK → tasks.id); from_status (TEXT, NULL allowed on first transition/creation); to_status (TEXT, NOT NULL); changed_at (TIMESTAMP, NOT NULL); append-only — rows are never edited or deleted.
Backs: D8.', 'spec', 0, '2026-05-31T04:11:47.876437+00:00', '2026-11-26T04:22:47.673074+00:00', 'schema', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (41, 'S5 — tasks_fts', 'An FTS5 virtual table indexing tasks for ranked keyword search (D17). Kept in sync with tasks by triggers (insert/update/delete). No external dependency — FTS5 ships with SQLite.

Columns: rowid (INTEGER, = tasks.id); name (TEXT, indexed); description (TEXT, indexed).

The indexed `name` carries the synthesized D32 prefix (T161, v0.4): the INSERT and UPDATE triggers store `<kind_letter><id> — <name>` (design→D, schema→S, production→T, meta→M) into the FTS row, not the bare stored `tasks.name`. The DELETE trigger is unchanged. This lets `search("T238")` and `search("D23")` find the right row by id-prefix regardless of whether the stored name carries such a substring. Migration 008 rebuilt the FTS for all pre-existing rows; the `tasks.name` column itself is untouched.

Backs: D17, D32.', 'spec', 0, '2026-05-31T04:11:47.876487+00:00', '2026-11-26T04:22:47.673074+00:00', 'schema', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (42, 'S6 — meta', 'Small key/value table for CLI bookkeeping. Holds version (monotonic generation counter, +1 per mutation — the ordering signal for sync, D18), synced_sql_hash (content hash of the decompressed text of the last-produced tackit.sql.gz — the integrity signal), and schema_version (for migrations).
Columns: key (TEXT, PK); value (TEXT, NOT NULL).
Backs: D18.', 'spec', 0, '2026-05-31T04:11:47.876525+00:00', '2026-11-04T02:46:58.356852+00:00', 'schema', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (43, 'D1 — Persistent task store', 'tackit keeps all state in a single local SQLite file (WAL mode). A project''s truth lives in one place, outside the agent''s context window, and survives across sessions and compaction.', 'spec', 0, '2026-05-31T04:11:47.876559+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (44, 'D2 — Typed validation boundary', 'Every read and write passes through Pydantic models. Malformed data — a bad status, a missing required field, an edge pointing at a nonexistent task — is rejected at the boundary as a loud error, never stored. This is the "deterministic" guarantee in practice.
Un-storable text is also refused (2026-05-30, closed-issue #22): a stored name/description/label may not contain a NUL byte (it breaks the D18 executescript rebuild from tackit.sql) or an unpaired UTF-16 surrogate (not valid UTF-8, so SQLite can''t encode it). Both are rejected loudly so the store always stays storable and rebuildable. (Both were found by the property-based round-trip test, not by example tests — see Testing.)

Adapter param gate (T236, 2026-06-23): the fail-loud boundary extends to the MCP/CLI ADAPTER''s parameter surface, not just core-model data. A tool call carrying an UNRECOGNISED parameter (e.g. ls(query=...) when ls accepts status/label/kind/stale/name_prefix) is REJECTED loudly — the tool input schemas set additionalProperties:false and the error names the offending param plus the valid set — rather than silently dropped. Rationale: a silently-ignored unknown param ran the op UNFILTERED and masked the agent''s mistake (a dogfooding session: ls(query=...) returned all 525 tasks, read as "the filter is broken"), so the agent never learned the right syntax and resorted to a Python workaround. Loud rejection forces in-session self-correction. Value-level validation was already loud (status=''status:open'' errors helpfully); this closes specifically the unknown-param-NAME hole.', 'spec', 0, '2026-05-31T04:11:47.876596+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (45, 'D3 — Task create / read / update', 'Create a task (auto-assigned monotonic id, name, description, kind), read it back, and edit its name/description. **`kind` (design|schema|production|meta, D26) is REQUIRED at create time** (T94) and determines the row''s partition under D36 (kind ∈ {design,schema} ⟹ status ∈ {spec,retired}; kind ∈ {production,meta} ⟹ status ∈ {open,closed,wont_do}). The atomic unit of work; everything else attaches to it.', 'spec', 0, '2026-05-31T04:11:47.876630+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (46, 'D4 — Labels', 'Attach zero or more freeform text labels to a task and list tasks by label. Labels are dumb tags — no status, no rules, no behavior — used only for human grouping/scoping (e.g. design, smoke-test). A task may wear several.', 'spec', 0, '2026-05-31T04:11:47.876663+00:00', '2026-06-23T06:16:02.558639+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (47, 'D5 — Symmetric link', 'Declare "task A is linked with task B" as a single symmetric edge type — no other relationship kinds, no direction. Each link is stored once in canonical order (lower id first, S3); the two argument orderings link_add(a, b) and link_add(b, a) produce the same row and are indistinguishable thereafter. Replaces the v0.2.0 directed depends_on edge; see migration 003 (T86).', 'spec', 0, '2026-05-31T04:11:47.876699+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (48, 'D6 — Linked-tasks traversal', 'Given a task, return its linked tasks — every task it shares a link with — in one call, as a single set (not partitioned into "dependencies" vs "dependents"; symmetric links have no such partition). This is the primitive the slice fetch (D9), the close gate (D14), and the change-cascade (D13) are built on. Traversal is status-blind — a closed neighbor is still returned.', 'spec', 0, '2026-05-31T04:11:47.876732+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (49, 'D7 — Status + stale flag', 'A task''s status is informational only; it never gates traversal. Production and meta tasks use open, closed or wont_do (wont_do: decided not to do, with a durable reason; closed: work shipped). Design and schema slices use spec or retired (D171). A separate stale flag marks a task whose linked neighbor changed under it.

Only live tasks (status open or spec) carry the flag (D137): the cascade never flags a closed, wont_do or retired task, so every stale task is on the worklist. The v0.3-era invariant `stale ⇒ open` (the cascade force-reopening closed tasks) stays retired (T123): the cascade never changes status. Open-because-new-work and open-because-stale stay queryable apart.', 'spec', 0, '2026-05-31T04:11:47.876765+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (50, 'D8 — Status transition history', 'Every status change is recorded with a timestamp. Reopening a closed task therefore does not erase the fact that it was completed earlier: the live status is current working state; the log is the history. Valid production/meta transitions include closed to open (reopen, a record correction, D259) and closed to wont_do (a close that should have been a drop, D56); the log keeps the close in both.

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
T174 (retire verb writes the new to_status).', 'spec', 0, '2026-05-31T04:11:47.876797+00:00', '2026-11-26T04:34:06.443452+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (51, 'D9 — Slice fetch', 'Fetch a single task plus its directly-linked context — its dependencies, dependents, and labels — as one small payload. This is what lets the agent read only what a task needs (a step-sized slice) instead of loading a whole monolithic document. The core anti-context-bloat feature.

Neighbour projection — a single `links` list (T237, 2026-06-23): under D5 symmetric links a task''s dependencies and dependents are the IDENTICAL set, so the slice surfaces them as ONE `links` field, not two duplicated `dependencies`/`dependents` fields. (The "its dependencies, dependents, and labels" phrasing above predates this collapse; under it an agent read every edge twice and a high-degree slice doubled its payload — the show/board self-demonstration: "depends on" and "depended on by" print identical lists.) The single symmetric `links` list is the honest shape and halves the neighbour payload.', 'spec', 0, '2026-05-31T04:11:47.876833+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (52, 'D10 — Stale propagation (mark-before-change)', 'Before a task is edited, mark its linked tasks (both endpoints'' view; D6) stale, leaving status untouched. Recording the obligation before the mutation makes an interrupted reconciliation crash-safe: the marks survive a dead session. Propagation is one hop: it does not cascade transitively on its own; it flows only where a real change actually happens (D13).

Bounded to live neighbors (D137): the staling write is depth-1, both endpoints, and only on neighbors whose status is open or spec. Closed, wont_do and retired neighbors are not written (no flag, no `updated_at` bump), so every stale flag is an obligation. What an edit RETURNS is narrower than what it writes: only the neighbors it moved from clear to set (D55, D211).

`edit_append` does not stale neighbors. This is a concession to reality, not necessarily the optimal decision. Agents clear flags unread whether forced or not (97% of 2,276 cleared flags had no MCP edit or show of the neighbor first, measured over 20 real sessions), and a cleared flag reads as a review that did not happen, so flagging on every saved note bought little and misled. Append is overwhelmingly note-taking: 136 of 149 observed calls recorded a note, and 103 of 214 reconciles directly followed one. The known cost: an appended ruling can make a neighbor wrong with nobody told. The agent is relied on to use `edit` or `edit_replace_substring` when something really changes, and that reliance is not expected to be perfect. If agents start reading flags, reopen this. T224 rejected an edit-type-aware cascade to protect the fold-back path from implementation to design; that path is unaffected, because append is meta-only (D255) and meta links stay within meta (D26), so an append could only ever have flagged other metas. `edit_append` still writes its audit row and requires its delta (D284).

Cascade also bounded by kind (D26): the cascade fires both directions of any link, but the meta-island constraint (a link between a meta task and a non-meta task is refused at D14) means propagation cannot bleed between meta work and spec/production work: there are no links across the kind boundary to traverse. The kind boundary keeps the meta bookkeeping graph clean of production coupling; the cascade-reach-bounding role it once played is now shared with the worklist filter.

No-op discipline (see D20): staling here fires only on an actual content change. Edit field-diffs against the stored value and, if nothing differs, does not stale linked tasks and does not bump version. This is D10''s instance of the general no-op rule defined in D20.

Edits to closed and wont_do tasks are refused (D259). Edits to retired slices are allowed and cascade depth-1 as usual; the description_revisions audit table (S7) records the prior verbatim name+description+delta on every edit, so the edit is recoverable.', 'spec', 0, '2026-05-31T04:11:47.876865+00:00', '2026-11-26T04:34:06.443452+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (53, 'D11 — Reconciliation worklist', 'List every stale task: the resumable worklist for a reconciliation pass that may outlive a session or context window. A new session asks "what''s stale?" and resumes exactly where the last left off. The pass is done when the list is empty. Rows are the short form of D211, with no bodies; `show` opens one.

The worklist is stale=1 AND status IN (''open'',''spec''). Only live tasks carry the flag (D137), so in a migrated store the status clause excludes nothing; it stays as a guard for flags loaded from an old spec-only dump.', 'spec', 0, '2026-05-31T04:11:47.876896+00:00', '2026-11-26T04:34:06.443452+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (54, 'D12 — Close operation', 'Closing a task sets status=closed and returns `{id, status}` (D211). The obligation that matters is enforced before the close, not printed after it: the close gate (D14) refuses while the task or a direct neighbor is obligation-bearing stale, so a successful close has no live stale neighbor to review. Closing does not cascade, so it creates no new obligation either. A neighbor list on success, which earlier versions returned as a review prompt, cost tokens with no next action attached; agents treated it as having no bearing on their next step in 66 of 68 observed close-type calls. `show` returns the neighbors when an agent wants them. `wont_do`, `retire` and `reopen` return the same shape.', 'spec', 0, '2026-05-31T04:11:47.876928+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (55, 'D13 — Change operation with cascade entry', 'Editing a task that has links first marks its linked tasks stale (D10), then applies the edit, then returns the neighbors this op newly flagged. This is the entry point of the change-time cascade: reconciling each stale task may, if it too changes, mark its own linked neighbors. Propagation flows only where real changes occur, and is bounded by the kind boundary (D10/D26). The cascade-entry ops are `edit`, `edit_replace_substring` and `reclassify`. `edit_append` is not one (D10).

The returned set is the new obligation: a neighbor appears only when this op moved its flag from clear to set and it is live (status IN (''open'',''spec'')), as `{id, because}` (D211). Closed, wont_do and retired neighbors are not flagged at all (D137), so they are never returned. A neighbor already stale stays on the worklist and in the alert but is not listed again, so an agent editing one task several times, or several tasks sharing a neighbor, reads each obligation once. Which task flagged a neighbor is not stored (D34), so a neighbor flagged by one task and then by another shows only the first note in edit results; `show` on that neighbor returns every note.

The edit''s required `delta` (one sentence describing the semantic shift) is compared against each surfaced link''s `because` rationale (T116) by the agent to filter false positives during reconciliation. Auto-diff is worthless here; the agent already knows what it did.

Edits to closed and wont_do tasks are refused (D259); edits to retired slices are allowed, and the description_revisions audit table (S7) records the prior verbatim row on every edit. If the edited task has kind in {design, schema}, the envelope includes a code-check reminder (D31) naming the slice id+name and prompting a grep against the code that realizes it (via the code↔task naming convention).', 'spec', 0, '2026-05-31T04:11:47.877035+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (56, 'D14 — Invariant enforcement', 'Refuse operations that would leave the graph inconsistent, and surface the violation loudly.

GRAPH INTEGRITY
- A link to a nonexistent task is rejected.
- No self-link and no duplicate link: the links table''s CHECK (task_a < task_b) and UNIQUE on the canonical pair.
- No cycle check: links are symmetric (D47), so there is no directed cycle to detect.

KIND TAXONOMY (D110)
- A label equal to a kind name (design, schema, production, meta) is refused at label_add and load.
- Meta-island: link_add between a meta task and a non-meta task is refused, keeping meta bookkeeping out of the spec/production graph.
- Partition (D171): production/meta rows hold open, closed or wont_do; design/schema rows hold spec or retired. A schema CHECK and the model validator both enforce it.

STATUS-CHANGE REFUSALS
- Close gate (1-hop): close, wont_do and retire are refused while the task itself is stale or a DIRECT linked neighbor is stale. Reach is symmetric with the cascade, which flags at 1 hop only (D52): a flag further away was raised about its own neighbors and must not block this task. Only live tasks carry the flag (D137); the gate keeps its status filter (open, spec) as a guard. The neighbor refusal names each stale neighbor with its link''s `because` and says to show it, read it against that coupling, then edit or reconcile it before retrying. The own-flag refusal points to `show` on the task, whose links then carry each neighbor''s `because` and latest delta (D165). Wording is fixed in D171''s refusal bank.
- close and wont_do are refused on status=''spec'': a living decision is edited, or retired if 100% abandoned.
- No double-decide (T132): close is refused on wont_do and retired and is a no-op on closed (D62). wont_do is refused on wont_do and retired. wont_do on a CLOSED task is allowed: a close that should have been a drop is recorded as dropped, with the same reason rule and the same close gate as on an open task, and the close stays in status_transitions (D50). reopen is refused on wont_do and retired; all four status verbs are refused on retired.
- reconcile is refused on closed, wont_do and retired tasks: they carry no stale flag (D137), so a list naming one is wrong. A batch containing any such id is refused whole and names every offender (D199).
- retire is refused unless status=''spec'', and while any linked neighbor is open; that refusal lists each open neighbor with its `because` and the link_rm / wont_do decision tree (D171).
- link_add is refused when either endpoint is retired.
- reclassify across the partition is refused when the source status has no clean target (for example closed production to design).
- Placeholder retire reasons are refused (D163).', 'spec', 0, '2026-05-31T04:11:47.877070+00:00', '2026-11-26T04:34:06.443452+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (57, 'D15 — Task query / board (derived views)', 'List and filter tasks by status, by label, and by stale — e.g. "open tasks in the smoke-test label" or "everything currently stale." The work-queue and any grouping view are queries over the fields, not separately-maintained lists (single source of truth).

T220 adds a `name_prefix` filter axis: a LITERAL, case-sensitive prefix match on the stored name, so a query can be scoped to one section of a large layer (e.g. `name_prefix=''§9.1''`) instead of pulling the whole kind. It matches the bare name, NOT the synthesized prefixed_name (which begins with the kind-letter + id). An empty prefix is refused (it would match everything — omit the filter instead).', 'spec', 0, '2026-05-31T04:11:47.877102+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (58, 'D16 — Narrative render', 'Render the tasks carrying a given label (e.g. design), in id order, into a single readable markdown document — recovering a human-reviewable narrative from the decomposed slices without that narrative becoming a second source of truth. (This very document is the kind of thing it would regenerate.)', 'spec', 0, '2026-05-31T04:11:47.877134+00:00', '2026-06-03T08:35:58.334520+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (59, 'D17 — Full-text search', 'Find tasks by keyword over name + description, ranked, via SQLite FTS5 — in-process, no external service or GPU. Returns matching task ids + titles + scores. This is the agent''s entry point for finding tasks (e.g. the ids to depend on) before fetching a slice; search → show is tackit''s retrieval loop, replacing grep-over-a-monolith. Keyword only; semantic/vector search is explicitly out of scope (a recall optimization with heavy deps). FTS5 is strictly better than grep for this (tokenized, ranked, indexed), but it is only as good as how discoverably tasks are written — see the skill-pack note under "Deferred."

The FTS index carries the synthesized D32 prefix (T161, v0.4): the indexed `name` column is `<kind_letter><id> — <name>` (design→D, schema→S, production→T, meta→M), so `search(''T238'')` and `search(''D23'')` find the right row by id-prefix regardless of whether the stored name carries such a substring. Migration 008 rebuilt the FTS for pre-existing rows.

Query sanitization (T222): raw queries are per-token double-quoted before MATCH, so dotted/punctuated keys (section ids like 4.2.4, hyphenated terms, apostrophes) match literally instead of erroring as FTS5 syntax. This makes the keyword-only contract robust — intentional FTS operators (prefix *, AND/OR/NEAR) are NOT exposed by default. The MATCH value is a bound parameter, so the sanitization is a usability measure (natural queries must not error), not a security boundary.

Section-id retrieval — route, don''t re-tokenize (T238, 2026-06-23): the "match literally" claim in the T222 paragraph above is too strong. T222 stopped the FTS5 SYNTAX ERROR on dotted keys, but the default unicode61 tokenizer still SHREDS a section id at INDEX time — "§9.4" indexes as the bare tokens 9,4, indistinguishable from "§8.9.4" (8,9,4) — so FTS cannot rank a section lookup correctly (it ranked §8.9.4 ABOVE the literal §9.4 on a dogfooded all-§ corpus). DECISION (option b, chosen over a global tokenizer change because "." is not categorically a section separator, so redefining it for all indexed text is wrong): core.search DETECTS a section-id-shaped query — the whole trimmed query matches ^§?\d+(\.\d+)*$ AND carries a § or a "." (so a bare "11" stays a normal keyword search) — and ROUTES it to an anchored substring match on the raw `name` column instead of FTS. The leading § is the left-anchor that excludes §8.9.4; a right-boundary (the char after the id must be a non-digit) excludes siblings §9.40/§9.41 while including descendants §9.4.x. No schema/migration change — S5''s FTS index is untouched and still serves every non-section query (including the D32 id-prefix lookups).', 'spec', 0, '2026-05-31T04:11:47.877166+00:00', '2026-06-23T07:03:50.512846+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (60, 'D18 — Git-tracked compressed serialization + safe DB↔dump sync', 'The git-canonical form of the store is a deterministic SQL dump, gzip-compressed and committed as tackit.sql.gz; the binary tackit.db is gitignored and local. Two markers in meta (S6) govern sync: a monotonic version (generation counter, +1 per mutation, embedded in both .db and the dump) gives ordering — "which is newer"; synced_sql_hash gives integrity / exact-identity. A different hash alone does not imply newer — that''s what version is for. synced_sql_hash covers the DECOMPRESSED dump text, never the compressed bytes, so compression settings or a zlib version can change without reading as divergence.
After every mutation: bump version, re-dump tackit.sql.gz (it embeds the new version + content), record synced_sql_hash. The committed file is thus always current — nothing to remember before committing.

Compression format. The dump exists mainly as a git-hosted backup, and git''s storage cost depends on delta compression between versions, so the format must keep unchanged rows byte-identical across versions. Plain gzip fails this: one early change rewrites all later bytes, measured at about 14.5x the git pack and about 1850x the per-version push of plain text on a 66 MB dump. Therefore the stream is a standard gzip file with a zlib full flush at content-defined row boundaries (a row boundary is a flush point when the crc32 of that row is divisible by 8), so runs of unchanged rows compress to identical bytes whatever precedes them. Compression level is 1 because the dump is written inside the mutating transaction, so per-mutation latency outweighs file size. Measured on the same dump over 40 real consecutive versions: 23.8 MB file, about 1.9x the git pack of text, about 0.6 s added per mutation. Denser flushing stops shrinking the pack and grows the file; sparser flushing grows the pack. Any standard gunzip reads the file.

Trade-off accepted: commits and PRs no longer show reviewable dump text unless a clone configures a decompressing git diff driver, which is optional. Merges still route through import. This covers the full store dump only; the spec-only public dump stays plain text because being read is its purpose. Existing git history is not rewritten.

Legacy adoption. A tackit.sql from before this format is adopted on open and by the sync commands, before any sync decision, and removed only after tackit.sql.gz is durably written. With only the legacy file: convert it. With both: identical text → remove the legacy file; legacy strictly newer by version → adopt it; otherwise refuse and name both files, because neither can be safely discarded by guesswork. This also covers an older tackit writing a text dump beside the compressed one. A plain open must not change files it then refuses over, so a lone legacy file that the sync decision below would refuse (not the db''s last dump and not strictly newer, as after checking out a branch from before this format) is refused untouched, keeping the checkout reversible. The explicit sync commands still convert it, since running them is the resolution, except that an import refused for discarding a newer db also changes nothing. Adoption also adds the ignore rule for the dump''s temp files to the store''s existing .gitignore, because a store created before this format lacks it.

On startup, sync decision (db has Vdb + last hash; disk dump has embedded Vsql + computed hash): no .db yet (fresh clone) → build it from the dump; hash(dump text) == last synced_sql_hash → in sync, trust the .db (covers the normal case and a crash before a dump completes); else the dump changed externally, decide by version: Vsql > Vdb → strictly newer (a pull) → snapshot .db to a rotating backup (last ~20), then rebuild it from the dump; Vsql < Vdb, or Vsql == Vdb with differing content → ambiguous (older checkout, local .db work not yet exported, or a merge collision) → do not auto-clobber: refuse and direct the agent to resolve explicitly (tackit import to adopt the dump, or tackit export to write the .db out). version is what makes these dangerous cases detectable rather than guessed. A dump that cannot be decompressed or decoded is refused loudly, never treated as absent or empty.

Net: auto-override only on unambiguous-newer; never a blind diff; backups guard the override path; merges route through import.', 'spec', 0, '2026-05-31T04:11:47.877198+00:00', '2026-11-04T02:46:58.356852+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (61, 'D19 — Built-in stale surfacing (added 2026-05-30)', 'The stale check is code in the app, not advice: every invocation queries the outstanding stale set (D11) and emits it deterministically, before the requested op and again after if the op changed it. CLI emits to stderr (so --json stdout stays clean); MCP wraps every tool result as {stale_alert, result}. The wording is single-sourced in core.stale_alert_text / stale_alert_payload and names the required action (review each stale task against its depends_on neighbors, then edit-or-reconcile) plus the negative fallout. This is the "surface" tier of Enforcement — stronger than a skill reminder (cannot be skipped or compacted away), short of blocking. (Resolves closed-issue #19.)', 'spec', 0, '2026-05-31T04:11:47.877231+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (62, 'D20 — No-op discipline (mutate only on a real change) (added 2026-05-30)', 'A mutating op enters the D18 finalize path (version bump + tackit.sql re-dump) only when the requested end-state actually differs from what is stored — a field-level comparison against the current row, no diff machinery. Applies to every mutating op: edit (no field differs), close/reopen (already in target status), reconcile (already not stale), label_add/label_rm (label already present/absent), dep_add/dep_rm (edge already present/absent). Keeps version (D18''s ordering signal) advancing only on genuine mutations, so redundant calls don''t churn the committed dump or produce a false "newer" signal. (Resolves closed-issue #18.)', 'spec', 0, '2026-05-31T04:11:47.877263+00:00', '2026-11-26T04:34:06.443452+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (63, 'D21 — Label-usage view (added 2026-05-30)', 'labels (CLI tackit labels + MCP tool) lists every label with its usage: task count + a few example task names, most-used first. A label is thus self-documenting through its tasks — its meaning is derived from usage, so there is no label-description to maintain or drift (S2 stays a plain join table; a labels-dimension table with descriptions was deliberately rejected as a second source of truth). This is the "what exists and what does it mean" primitive that makes reuse-before-create possible; it underpins the label-discipline epic (the creation-time anti-sprawl nudge and the skill guidance build on it).', 'spec', 0, '2026-05-31T04:11:47.877302+00:00', '2026-06-01T01:57:16.212740+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (64, 'D22 — Board view (CLI, human-readable) (added 2026-05-30)', 'tackit board renders the state as a human-readable, dependency-aware board in the terminal: grouped by status (In Flight for open, Spec, Done for closed, Won''t Do, Retired), colored (open and spec=accent, stale=alert, done, wont_do and retired=dim; color only on a TTY), each task showing its labels and a single `links` edge list, matching the slice''s single `links` projection (D9, D5). A section with no rows is not printed. Every status has a section, so `board` never reports "no matching tasks" for tasks that matched. The header counts the whole store by every status plus the obligation-bearing stale count. Filter MODES cover the common questions: default (everything), --status open ("what''s outstanding," the #1 ask), --label X (a group), --stale (after big changes). --json emits the structured slices. CLI-only (the agent uses the structured ls/stale/show tools); it''s the human''s at-a-glance view, the visual cousin of D16 render. (HTML export was considered and dropped — the CLI board + the agent''s change-summaries cover the need.)', 'spec', 0, '2026-05-31T04:11:47.877336+00:00', '2026-11-05T00:28:23.405401+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (65, 'D23 — Label-creation nudge (added 2026-05-30)', 'When an op (add / label_add) creates a brand-new label (one no task carried before), it records an anti-sprawl nudge — the new label plus the labels that already exist — surfaced deterministically by the adapters (CLI stderr; MCP envelope label_nudge), the same un-skippable channel as the stale alert. This puts reuse-before-create in the agent''s face at the exact moment sprawl happens, rather than relying on the skill being remembered. The nudge reflects only the latest op (reset each add/label_add); the meaning of each existing label is recoverable via labels (D21). Completes the label-discipline mechanism (D21 view + D23 nudge + the SKILL guidance).', 'spec', 0, '2026-05-31T04:11:47.877369+00:00', '2026-05-31T04:11:48.078577+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (66, 'D24 — Bulk plan import (added 2026-05-30)', 'tackit load <file> ingests a lightweight, no-dependency plan format (tackit.plan): [key] Name lines with indented kind: / desc: / labels: / depends_on: fields, where depends_on references other keys in the same plan. kind is REQUIRED per row (D26: design | schema | production | meta, T94); a row missing it is refused with the [key] named, and the whole import rolls back. core.load creates all tasks in one transaction (a single version bump), resolving keys→ids in a second pass to wire links; each depends_on line creates a symmetric link in canonical order (D5/T86) — under symmetric semantics there is no cycle in the directed sense, so the v0.2.0 cycle check is retired (T86). Any error — a malformed line, a missing or invalid kind, a duplicate key, an unknown dep key, a self-edge, a reserved-label collision (the four kind names are reserved labels, D26), or a meta-island violation (cross-kind link between meta and non-meta, D26) — fails loud and rolls back the whole import (never a partial plan). This removes the one-call-per-task tax when backfilling an existing project, and is the path the design docs themselves are loaded through (D-slice format nearly conforms; feeds the design-doc backfill).', 'spec', 0, '2026-05-31T04:11:47.877402+00:00', '2026-06-23T07:07:40.700381+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (109, 'D25 — Supersede marker (added v0.3.0)', 'Mark an existing task superseded_by a newer task that replaces its premise. A nullable FK on tasks (S1) — not a separate edge type — because the relationship is one-to-one (a task points at at most one successor) and the marker itself does not participate in close-gate or graph traversal. The supersede(old, by) op sets the marker, refuses self-supersede, and does not auto-close old — supersede and close are independent decisions (close old separately if retiring it; reopen old later without un-superseding).
Why a marker, not just close: a closed task remains in full-text search results (D17). When a later task inverts or replaces the closed task''s premise, a search hit on the closed task silently misleads the reader. The marker tags the hit so search can flag "superseded by T<new>" inline and show can display both directions (this task''s superseder + tasks superseded by this one via reverse lookup). It surfaces the displacement that close alone can''t.
Fires the cascade on old''s links (T124, 2026-06-01): supersede is now a cascade-firing op. Replacing a task''s premise means every linked neighbor must walk the migrate-or-stay decision: link_add(by, neighbor) to migrate the relationship to the replacement (with a fresh because rationale describing the new coupling); link_rm(old, neighbor) if the relationship is fully replaced (or leave both, treating old''s edge as historical record). Closed neighbors stay closed + stale=True per T123''s relaxed D7 — no force-reopen. The cascade obligation rides in the op''s result as newly_stale, mirroring ChangeResult.newly_stale from edit. Empty delta is refused per T117 like edit/link_add/link_rm.
Surfacing: superseded_by rides in the slice envelope of show, ls, board, and search results (not search-only). Search flags each superseded hit with the superseder''s id+name; show includes both directions; ls/board show the marker as a small annotation. Resolves T69.', 'retired', 1, '2026-06-01T01:51:35.831916+00:00', '2026-11-26T04:22:38.504819+00:00', 'design', 'v0.4 retires supersede entirely; the description_revisions audit table (D29 / T138) replaces the marker''s archaeology role under the same task id. The marker mechanism tagged search hits with a superseder pointer to prevent misleading hits on closed tasks whose premise was inverted; v0.4 addresses the same concern by allowing edit-on-closed with verbatim prior-version preservation in an append-only audit table. Simpler model, same archaeology capability. T109''s prose stays as historical record of what v0.3.0 supersede was.');
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (110, 'D26 — Task kind taxonomy (added v0.3.0)', 'Every task carries a required kind in {design, schema, production, meta}, set at create and refused if missing or invalid. The taxonomy splits tasks by whether they alter the running app''s behavior (the classifier rule):

- design: a design.md slice (D#). Decisions, not code. Perma-open under D30 v0.4 (close/wont_do refused on this kind; edit() updates the slice and the description_revisions audit table S7 preserves the prior verbatim version).
- schema: a schema.md table (S#). The store''s shape. Same perma-open rule as design.
- production: code that alters the running app''s behavior, including source under tackit/, the README and SKILL.md (those alter the agent''s behavior of the app), and test code that pins behavior contracts.
- meta: work that does NOT alter the running app — release bookkeeping, experiments, dogfood notes, side-investigations.

Inheritance trap (T122): classify a NEW task by its OWN scope, not by the parent epic''s framing. A task spawned during a meta thread that itself includes impl work is production — not meta — regardless of where the discussion happened.

Meta-island constraint: a link_add between a meta-kind task and a non-meta-kind task is refused (D14). Under v0.4 the load-bearing role of this constraint is NARROWER than v0.3.0 framed: the cascade itself flags only live tasks (D137), regardless of which kinds are linked. What the meta-island still does is keep the meta bookkeeping graph clean of production/spec coupling: meta tasks (release notes, observation writeups, dogfood logs) stay structurally separate from the app''s substance, so a release-tracking task can''t show up wired to a production source change. A meta task may link other meta tasks freely; a non-meta task may link any other non-meta task.

Worklist scope (D53, D137): the worklist is stale=1 AND status IN (''open'',''spec''). Design and schema slices stay on it when they go stale, because spec is a live status; they never close (D171).

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
CHECK + reclassify auto-shift), T176 (Phase 4 — property invariant).', 'spec', 0, '2026-06-01T01:51:47.643672+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
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
invariant).', 'spec', 0, '2026-06-01T01:52:06.009742+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (133, 'D7 — Status + stale flag (v0.4 bounded-obligation)', 'STATUS. Production and meta tasks use open (work outstanding), closed (shipped) and wont_do (dropped, with a durable reason). Design and schema slices use spec (a living decision) and retired (100% abandoned). The partition is enforced by a schema CHECK and the model validator (D171); add, load and reclassify apply the partition default. Status is informational and never gates traversal. Every transition is recorded in status_transitions (S40, D50).

STALE FLAG. A single boolean on every row, set by the cascade (D52) and cleared by reconcile. Only live rows carry it (D137): the cascade flags open and spec neighbors only, so a stale task is always live, every stale task is on the worklist (D53), the close gate (D56) sees every flag, and reconcile can clear every flag. Reconcile on a closed, wont_do or retired task is refused: there is nothing to clear.

EDITS. edit() is refused on closed and wont_do tasks (D259) and allowed on open, spec and retired rows; the description_revisions audit table (S141) keeps the prior verbatim name, description and delta on every edit. The supersede verb is gone.

The no-op rule (D62) and the 1-hop close gate (D56) apply in their own scopes.', 'spec', 0, '2026-06-01T06:06:38.590496+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (137, 'D28 — Bounded-obligation cascade: only live tasks carry the stale flag', 'RULE. The cascade flags only live neighbors: status IN (''open'',''spec''). A closed, wont_do or retired neighbor is never written: no stale flag and no `updated_at` bump. So stale=1 implies status IN (''open'',''spec''), and every stale flag is an obligation: it is on the worklist (D53), the close gate sees it (D56), and reconcile can clear it.

WHY. Terminal tasks used to keep the flag as record-only history. It saturated (downstream, 1,202 of the 1,287 closed tasks linked to a live spec carried it), named neither the neighbor nor the change, never cleared (reconcile refuses terminal tasks), and duplicates facts the store already keeps in status_transitions and description_revisions. It also broke the no-op close (D62): a flagged closed task could not be closed again, because close refuses a stale task and reconcile refuses a closed one. Earlier data (T120) had already shown cascade-through-closed paying its cost without a payoff.

EXISTING FLAGS. Schema version 11 clears stale on every closed, wont_do and retired row, leaving `updated_at` untouched. A store rebuilt from an older dump keeps that dump''s schema version, so the migration runs at the next open. Accepted residual: a spec-only dump written before version 11 has no schema version, so loading one by hand can bring back flags on retired rows; readers keep their status filters for that case.

ENFORCEMENT. Code only, plus a property-test invariant (stale implies a live status) checked after every operation. The invariant rests on two rules holding together: the cascade never writes a terminal row, and close, wont_do and retire each refuse a task that is itself stale (D56), so no flagged task can reach a terminal status. Weakening either one breaks it. No database trigger: no raw-SQL write path was observed in use.

PREDICATE. The worklist, the close gate''s 1-hop check and reconcile''s allowed set all use stale=1 AND status IN (''open'',''spec''). Reconcile on a terminal task is refused because it has nothing to clear (D56). A task returns to a live status only by reopen (closed to open, D259), and it arrives unflagged.', 'spec', 0, '2026-06-01T22:22:03.373143+00:00', '2026-11-26T04:34:06.443452+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (138, 'D29 — Description revisions audit table', 'Append-only audit table capturing every successful edit''s prior name and description plus the delta rationale. Backstop for edit archaeology: while a task is OPEN its description can be updated in place with the verbatim prior state recoverable. (D259 REVERSES D29''s original v0.4 stance that also permitted editing CLOSED and wont_do tasks in place — the three edit ops now refuse on status IN (''closed'', ''wont_do''); reopen to change. The audit table therefore backstops open-edits and retired-slice edits, not closed tasks.) Replaces the v0.3.0 supersede marker (D25) — the marker addressed ''don''t get misled by old prose'' via inline-tagging hits with a superseder id; the audit table addresses it by preserving the prior verbatim version under the SAME task id. Simpler model, same archaeology capability. history() op extends to return description_revisions alongside status_transitions.

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
- The cascade fires depth-1 on the retired slice''s open and spec
  neighbors (D137).
- D31''s code-check reminder also fires on retired edits (see D31 v0.5
  update — "verify no lingering code references this dead decision").

Backs (v0.5 additions): D36 (T171 — retire verb shape), T174 (Phase 2b
retire impl writes wont_do_reason without touching description_revisions),
S1 (T37 v0.5 — wont_do_reason dual-role partition semantics).', 'spec', 0, '2026-06-01T22:22:03.373244+00:00', '2026-11-26T04:34:06.443452+00:00', 'design', NULL);
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
+ mig 009), T173 (Phase 2a — status-derived refusals).', 'spec', 0, '2026-06-01T22:22:03.373312+00:00', '2026-11-26T04:34:06.443452+00:00', 'design', NULL);
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
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (141, 'S7 — description_revisions', 'Append-only audit table backing D29. Columns: id (INTEGER, PK); task_id (INTEGER, FK -> tasks.id); prev_name (TEXT, NULL — pre-edit name, NULL if unchanged); prev_description (TEXT, NULL — pre-edit description, NULL if unchanged); delta (TEXT, NOT NULL — rationale from the edit op); edited_at (TIMESTAMP, NOT NULL); rows never updated or deleted. Written by core.edit() on every edit that actually changes name or description (no-op edits skipped per D20). Read by core.history(). Migration 007 adds this table.', 'spec', 0, '2026-06-01T22:22:03.373434+00:00', '2026-11-26T04:22:47.673074+00:00', 'schema', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (156, 'v0.4 gap: legacy closed/wont_do design/schema slices stuck on worklist (no reconcile path)', '**RESOLVED** by reconcile()-mirrors-worklist refinement (v0.4 cluster commit 583df90, code lives at core.py:820-852).

**Gap** (surfaced 2026-06-01): D28''s worklist filter admitted a stale task as obligation-bearing when `status=''open'' OR kind in {design, schema}`, which pulled closed/wont_do design/schema slices onto the worklist. But reconcile() refused unconditionally on all closed/wont_do tasks. The refusal message claimed "(not on the worklist)" while the worklist explicitly listed them. The two rules contradicted, leaving 5 legacy rows pinned on the worklist with no exit path (T37, T44, T49, T59, T109 in the dogfood DB at the time).

**Resolution** (option B from the original three-candidate analysis): reconcile()''s refusal condition was tightened to mirror D28''s worklist filter exactly:

> `reconcile()` is REFUSED iff (status in {closed, wont_do}) AND (kind in {production, meta}).

Equivalently: reconcile is allowed iff `status=''open'' OR kind in {design, schema}` — the same predicate D28 uses to define the worklist. So what surfaces as obligation-bearing is exactly what can be reconciled, by construction.

For closed/wont_do **design/schema** (perma-open in spirit per D30 but legacy-closed pre-D30), the slice IS the obligation: reconciling it acknowledges the slice still describes truth after the upstream change. For closed/wont_do **production/meta**, reconcile stays refused; since schema version 11 they carry no stale flag at all (D137).

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
— 5 D156-mirror tests rewritten).', 'spec', 0, '2026-06-01T23:30:39.270432+00:00', '2026-11-26T04:20:50.690606+00:00', 'design', NULL);
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

Backs: D17 (search) — search must find by synthesized prefix. D26 (kind taxonomy) — the kind letter comes from kind.', 'spec', 0, '2026-06-02T01:23:31.673423+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
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
requirement), T174 (Phase 2b retire impl with the placeholder check).', 'spec', 0, '2026-06-02T03:07:55.251363+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (165, 'D34 — Surface cascade-ergonomics rationales (link `because` + upstream edit `delta`) with a DRY FAST-filter reminder', 'The cascade-ergonomics FAST filter (T116/T117) compares an upstream''s `delta` (semantic shift) against a link''s `because` (coupling axis) to orient the reconciliation of a stale task. Both inputs are persisted: `because` on `links` (T116), edit `delta` in S7 `description_revisions` (D29). This slice decides how they reach the agent during a reconciliation walk.

**The rule:**
Reads of a slice surface, on each neighbor entry where the read is a reconcile read:

1. **The link''s `because`** — read from `links.because` for the canonical (a, b) pair.
2. **`last_edit_delta`, the neighbor''s most-recent edit `delta`** — the `delta` from the most recent `description_revisions` row whose `task_id` is the neighbor''s id. Null if that neighbor has never been edited.

A reconcile read means the entry or the focal task is OBLIGATION-BEARING STALE: stale=1 AND status IN (''open'',''spec'') (D28/D36). Both conditions are needed because an edit stales the edited task''s neighbors, not the edited task. Reconciling stale task A uses A''s entry for the edited neighbor B, and B is not stale, so a stale-entry test alone would strip the fields from that read; the focal condition keeps them. Viewing B, the entry for stale A carries the coupling axis, and B''s own delta is what the agent just wrote. Which neighbor staled a task is not tracked, so a stale focal task keeps the fields on every entry.

Outside a reconcile read the two fields are absent, per the lean default in D211, and `show` returns them on every entry on request. Closed, wont_do and retired tasks carry no stale flag (D137), so they never make a read a reconcile read.

3. **Top-level `because_reminder` field** — a single DRY-sourced string explaining what those two fields are for. Same envelope position as `code_check_reminder` / `label_nudge` / `stale_alert` / `delta` (the per-op fields). Set iff the focal task or at least one neighbor is obligation-bearing stale; otherwise null. The lean `board` projection drops the two fields from every entry, so it returns the reminder as null, since there is nothing for it to explain. A stale task with no links left still gets the reminder on `show`: it still owes a reconcile. Not on every show (noisy on read-only browsing); not gated on top-level `stale_alert` (which misses a stale task viewed while reading other work, and has no per-slice view).

**The reminder string (single source, exported constant — orientation-first, post-empirical revision 2026-06-02):**
> *"Each link''s `because` describes WHY the two tasks are coupled. Each cascade-firing op''s `delta` describes the upstream''s semantic shift. Read both BEFORE opening a stale linked neighbor -- they tell you the specific aspect to check. In the rare case where the shift doesn''t intersect the coupling axis at all, you can `reconcile` without re-reading the linked neighbor (FAST path); otherwise re-read and edit-or-reconcile (SLOW path with the question pre-formed)."*

**Why orientation-first, not skip-first** (2026-06-02 empirical finding from the in-session cascade walk + the 89/7-edge backfills): in practice, on hub-spec edits (the dominant case), the delta lands in the coupling axis the because names — so the FAST skip rarely applies (~6% of events in our sample). The dominant value is **orientation**: the agent gets handed a specific question to evaluate against the dependent''s prose, rather than having to derive it. Even when the answer is "no, the prose is generic enough," the SLOW path is faster because the question is pre-formed. The reminder is written to put orientation as the primary action and FAST-skip as the rare exception, matching the empirical pattern.

DRY: the reminder text is defined exactly once (`core.LINK_BECAUSE_REMINDER`); referenced by every emission site. No duplication, no per-site drift.

**Surfaces:**
- `show`: the fields per the rule above, projected per D211; `because_reminder` per its trigger.
- Edit results: each newly flagged neighbor carries its `because` at flag time (D211), since the edited task is known then. `last_edit_delta` is not on these entries; the agent wrote the delta. No `because_reminder` here.
- `board`: routes through the same slice read, so it shares the trigger. Its lean default (D211) omits the fields unless `include_neighbor_because` is passed, so it returns the reminder only with that opt-in. A lean card''s `stale` flags and the envelope''s `stale_alert` remain the signal that reconciliation is pending.
- `stale_alert` payload: not enriched. Listing each stale task''s upstream(s) + because + delta needs "which upstream(s) staled me", which is not tracked.

**Known partial coverage:**
`edit_append` writes the audit row without flagging anyone (D10), so after an edit and then an append on the same task, `last_edit_delta` on that entry shows the append''s delta, not the edit''s. Accepted: `history` keeps both.

`reclassify()` is a cascade-firing op that does not write S7 (it doesn''t change name/description). Its `delta` is ephemeral — gone after the op''s response envelope drops from the agent''s context. Same for any future cascade-firing op that doesn''t go through edit''s S7-writing path. In those cases `last_edit_delta` is null and the agent falls back to SLOW-path re-reading without orientation. Acceptable, because (a) reclassify is rare in normal work, (b) the filter still works for the common edit() case which is the majority of cascade events.

**Non-edge contexts:** the `links()` op (D27) returns candidates not tied to a single edge from the input ids'' perspective, so the per-edge fields (`because`, `last_edit_delta`) are None on those entries.

**Why this isn''t D33''s job:**
D33 enforces rationale QUALITY at link creation. D34 surfaces the rationales AND deltas so the FAST/orientation pipeline can actually run. They''re complementary: D33 makes the data worth surfacing; D34 makes it accessible.

Backs: D9 (slice fetch), D11 (reconciliation worklist), D28/D36 (the obligation predicate), D29 (description_revisions, the source of persisted deltas), D211 (the lean projection that governs when the fields are returned), T116 (per-edge because), T117 (delta concept).', 'spec', 0, '2026-06-02T03:19:44.306327+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
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
- D28 (worklist filter simplifies; retired is off the worklist).
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

Backs: D7, D14, D28, D30, S1, D8 (prose edits required to align with new framing); D26 (cross-references the partition rule that arrives under D36); D156 (kind exception now obviated).', 'spec', 0, '2026-06-02T04:54:12.306635+00:00', '2026-11-26T04:34:06.443452+00:00', 'design', NULL);
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

- Signature: `retire(id: int, reason: str)`, returning `{id, status}` (D211).
- `reason` is durable, persisted in the row''s `wont_do_reason` column. The partition rule guarantees one terminal verb per row (wont_do for production/meta, retire for design/schema), so the column name stays; S1''s prose updated to document the dual role.
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

- **Cascade**: retire() does NOT fire the cascade (mirrors wont_do/close). The retirement IS the alert. The open-neighbor refusal above forces the per-neighbor decision before retirement; afterwards `show` lists the neighbors if the agent wants to link_rm any that are no longer meaningful.
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
- Cascade behavior on retired: a retired slice is never flagged by a neighbor''s edit (D137).
- `reconcile()` refused on retired, as on closed and wont_do: none of them carries a stale flag (D137). Message: see the refusal bank below.

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
  "REFUSED: {X} is {status}; closed, wont_do and retired tasks carry no
   stale flag, so there is nothing to reconcile. Drop it from the list
   (D137)."  The batch form names every offender and ends "No row was
   changed."

close()/wont_do()/retire() on a stale task (own flag):
  "REFUSED: {X} is stale: a task it links to changed since it was last
   reviewed. Run `show {X}`: on a stale task each link carries its
   `because` (why that neighbor matters here) and the neighbor''s latest
   delta. Read them against {X}; `edit` it if it is now wrong, or
   `reconcile` it if it still holds; then {verb} (D56)."

close()/wont_do()/retire() with stale direct neighbors:
  "REFUSED: {verb}ing {X} would decide on top of a change nobody has
   reviewed. Its directly linked stale neighbors:
     - {Y} -- because: "{because}"
   Show each and read it against that coupling; `edit` it if it is now
   wrong, or `reconcile` it if it still holds; then {verb} {X} (D56)."

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
- D28 (worklist filter — joint with D35; retired is off the worklist).
- D29 (description_revisions — note retire() does NOT append a revision row; durable reason lives in wont_do_reason column).
- D30 (perma-open framing — replaced by D35+D36 jointly; D30 redirects to the new canonical framing).
- D31 (code-check reminder — fires on retired edits too as "verify no lingering code references this dead decision").
- D33 (placeholder rationale refusal — extends to retire''s reason field).
- S1 (partition CHECK joint with D35; wont_do_reason column documented as terminal-verb-reason regardless of which verb wrote it).

Backs: D7 (status taxonomy joint with D35), D14, D26, D27, D28, D29, D30, D31, D33, S1.', 'spec', 0, '2026-06-02T07:22:12.812393+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (172, 'D37 — Granular-description discipline: task bodies must be impl-ready on fresh-session revisit; impl-time discoveries are routed as fold-backs', '**The granular-description discipline** (added 2026-06-02): task descriptions must be implementation-ready. On a new-session revisit, an agent reading the description should not be confused, should not feel the need to do additional scoping work, and should not encounter ambiguity that could be resolved. Under-defined task descriptions force fresh-session agents to re-derive context that should already be on the row — which is exactly the failure tackit exists to prevent.

**The rule (forceful):**

> A task''s `description` must contain enough granular detail that a fresh-session agent — with no conversation history, no prior context, only the task body and its linked neighbors — can implement the task (or evaluate its completion) without asking the user for clarification.

**Per-kind expectations:**

- **design / schema slices** (status=''spec''): the slice must fully specify the decision, its constraints, its implications, and the refusal patterns it implies, such that a fresh-session agent can edit code to align with it (and verify drift via D31''s code-check reminder).
- **production tasks**: the body must describe what code change is being made, in what files, at what call sites, with what test coverage and refusal-message wording, such that a fresh-session agent can sit down and write the change directly. Include SQL recipes, signature snippets, predicate tables, error-message banks — execution-grade detail.
- **meta tasks**: the body must describe what bookkeeping is being captured (a release, an experiment, an observation), with enough context to interpret the result. For observation experiments, include procedure + raw findings + verdict; for release tracking, include the version + scope + sign-off conditions.

**When the discipline applies:**

- **At create time (`add()`)** — aim for full granularity from the start. add()''s docstring SHOULD prompt the agent to write at impl-ready granularity. The bias is "richer body now beats remembering it later."
- **During implementation** — an under-defined detail discovered while implementing (an edge case the spec missed, a refusal-message wording question, an extra file affected) is a normal and expected outcome; the response is to route it as a fold-back (D258), not to lose it. A decision goes into the governing spec slice (D245). A correction to the current production task''s forecast (a wrong code path, a missed call site) rewrites that body with `edit` / `edit_replace_substring` (D255). Progress goes to git and the status, not the body.
- **Before close** — no separate re-read step. The body stays accurate because each discovery is routed as it is found; the revision audit (S7 description_revisions) is the safety net.

**Anti-patterns this discipline forbids:**

- **Vague verbs**: "Fix bug" / "update logic" / "clean up X" — unsearchable, unimplementable, kind-conditional context loss.
- **Conversation references**: "Add the feature discussed in conversation" / "see chat history" / "as agreed" — references ephemeral context the durable task can''t recover.
- **Pointer-only bodies**: "See related task X for details" without inlining the actual scope — forces traversal that loses on a fresh-session pass when X has also been edited or removed.
- **TBD/TODO placeholders** in committed task bodies — flag and resolve before commit. If a detail genuinely isn''t decided yet, the task isn''t ready to be tracked as a discrete unit; either decide it or split.
- **Implementation-by-conversation**: agreeing on detail in conversation but never folding it into the task body — the conversation is ephemeral, the task is durable. The bias should always be: if a detail surfaced in conversation that''s not yet on the task, edit() it onto the task before the conversation context expires.

**Surfaces this discipline lives on** (per D36''s propagation principle):

- **SKILL.md** (T169): forceful instruction in the writing-tasks section. The primary surface — agents read it at session start and the rule shapes every subsequent add() / edit() invocation.
- **MCP `add()` docstring** (T168): "Aim for impl-ready granularity at create time. A fresh-session agent should be able to implement the task from its description alone."
- **MCP `edit()` docstring** (T168): "If implementation reveals an under-defined detail, route it as a fold-back: a decision to its spec slice, a correction of this task''s forecast by rewriting the body."
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

Backs: D3 (task create/read/update — discipline applies at create time), D26 (kind taxonomy — granular descriptions are how each kind achieves its purpose), D29 (description_revisions — audit trail of edits captures granularity improvements over time and is the safety net for missed pre-close refinements), D36 (propagation principle — D37 follows the same multi-surface pattern: SKILL + MCP docstrings + CLI help + README).', 'spec', 0, '2026-06-02T07:26:37.592198+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
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
- **Status.** SKILL new rule + reword + first pins in 3076890; the remaining surfaces (anchor→hub, README, docstrings, extra pins) in the T219 commit. 525 tests pass.', 'spec', 0, '2026-06-04T16:44:45.843164+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (199, 'D39 — Bulk-sweep ergonomics: compact link_add return, batch reconcile(ids), short alert on reconcile', 'Cuts the per-op token tax that makes bulk/sweep operations expensive — the T179 large-body pattern resurfacing on the RESPONSE and SWEEP side. From dogfood friction on a large schema backfill (#2/#1/#6 of that report). tackit was built interactive-single-op-first; its origin use case (import a large plan) and the messy-backfill reality are bulk.

## #2 — link_add returns a compact confirmation, not the full slice
Today MCP `link_add` returns `a`''s full slice (task body + dependencies + dependents, each neighbor listed twice). Wiring N edges through a high-degree node (e.g. a schema hub with 8 neighbors) reprints its whole neighborhood on every edge — pure context tax.

**Decision:** `link_add` returns `{"linked": {"a": <id>, "b": <id>, "because": <str>}}`, on MCP with the standard envelope and `short_alert=True`, and identically on CLI `--json` (D211). NOT the full slice. `because` is the STORED rationale: a duplicate link is a no-op that keeps its first rationale, so the return reports that one, not the argument, and a stored rationale is stripped of surrounding whitespace. Rationale: link_add is STRUCTURAL — it does not cascade (skill: link ops don''t fire the cascade), so the neighborhood echo carries no obligation the caller must act on; confirmation that the edge landed is all that''s needed. Use `show(a)` for the slice when actually wanted. No `verbose` flag (YAGNI — show() is the escape hatch). `core.link_add` is UNCHANGED; only the adapter payload shrinks.

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

Couples to: the edit-quality / reconciliation discipline (the guard-rail is why this isn''t a pure ergonomic change) and M181 #8b (short_alert, reused for #6).', 'spec', 0, '2026-06-04T17:17:46.466807+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
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
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (207, 'D41 — SKILL.md is self-contained behavioral instructions: plain register, each rule carries its reason, no task-id citations, MCP harmony', 'SKILL.md ships to every tackit user. It is a self-contained list of behavioral instructions: not documentation, and not a pointer into the maintainer''s store.

Form. Each instruction states the directive and the reason it exists, in plain prose; it names the concrete anti-pattern only when a real one exists. No emphasis markers (capitals for stress, FORCEFUL, star or "most important" framing). Keep each rule''s scope words ("every", "only", "open or spec"): on the 5.5 model generation, adherence held when emphasis was cut and dropped where a scope word was cut. Short mechanics may stay as reference.

What stays. The judgment calls the code cannot make: classification, when to file, how to write a `because`, what to reconcile, where a finding goes. A move the code refuses with a message that says what to do instead is taught by that refusal and the op''s docstring, not restated here.

Self-contained. The skill cites no task ids from this store: in another user''s store those numbers name their own rows, and the reader cannot open ours. An incident that motivates a rule is stated in one clause. Id-shaped strings appear only as syntax examples.

Cut. War stories, version archaeology, motivational preambles, and any paragraph that appears twice.

Intro. Three or four lines: what tackit is and why it exists.

MCP harmony. A discipline''s full statement lives once, in the skill. A docstring carries its op''s contract, the one-line sharp edge of any discipline that bites at that call, and the skill section''s heading. Headings cited by docstrings, CLI help or refusals are a contract: renaming one updates every citer.

Pins. Presence-pin tests assert the skill''s load-bearing phrases. A reword updates the pin to the new phrase in the same change and never drops it silently.', 'spec', 0, '2026-06-04T20:46:55.466445+00:00', '2026-11-26T03:04:45.959514+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (211, 'Lean projection default for every adapter return, identical on MCP and CLI', 'Decision: every adapter return is LEAN by default. A return carries what the agent''s next step needs and nothing it already has; the rest stays available on request. Nothing stored is removed, only what a call returns.

**`ls`, `board` and `stale`:** task scalars only: id, prefixed_name, kind, status, stale, wont_do_reason. No `description`, no bare `name` (the computed prefixed_name `<L><id> — <name>` carries it), no `created_at`/`updated_at`. `show(id)` is the path for one row''s full name and timestamps. `board` neighbors are the graph SHAPE: id, prefixed_name, status, stale, kind. `stale` returns the worklist rows in this short form, with no bodies and no opt-in: an overflowing worklist is useless at the moment there is something to review, and the alert already carries the ids.

**`show`:** the focal task is returned in full, with its name, timestamps and body. Each neighbor entry is the same graph SHAPE, plus `because` and `last_edit_delta` iff the entry or the focal task is OBLIGATION-BEARING STALE: stale=1 AND status IN (''open'',''spec''), the worklist predicate (D28/D36). That is the reconcile read D34 serves. An edit stales the edited task''s neighbors, not the edited task, so reconciling a stale task needs the fields on its entries for neighbors that are not themselves stale; hence the focal condition. Closed, wont_do and retired tasks carry no stale flag (D137), so they never earn the fields. Neighbors are never filtered out of `show` by status: closed, wont_do and retired entries stay, lean, with their `stale` field (always false for them), because a reader wants to see everything the task links to.

**Edit results (`edit`, `edit_append`, `edit_replace_substring`, `reclassify`):** the focal task''s scalars without its body, plus `newly_stale`, the obligation this op just created. An entry appears only when this op moved the neighbor''s flag from clear to set and the neighbor is live (status IN (''open'',''spec'')). Each entry is exactly `{id, because}`: `because` is the field built for deciding how much attention a flagged task needs, and the agent knows its own delta. Already-stale neighbors are not relisted and terminal neighbors are never listed; the former stay on the worklist and in the alert, the latter are never flagged (D137). There is no size cap on the list. A neighbor whose flag is cleared and then set again by a later edit is first-time again and is listed again. `id` is an integer because `reconcile` takes integers. `edit_append` never flags (D10), so its `newly_stale` is always empty. The edit verbs take `include_description` to return the body; `reclassify` does not.

Measured basis for the entry rule: across 20 real sessions, 27% of printed entries were terminal and 31% were already flagged, so 57% carried no new obligation, and `because` never reached the agent at decision time.

**Other write results:** `close`, `wont_do`, `retire`, `reopen` return `{id, status}`. `add` returns `{id}`. `label_add`, `label_rm` return `{id, labels}`. `link_add` returns `{linked: {a, b, because}}`; `link_rm` returns `{unlinked: {a, b}}`. `links_add`, `load` and `reconcile` return their compact forms (D213, D24, D39). The read core keeps its full return types; projection happens at the adapter boundary, in one shared place both adapters call.

**MCP and CLI match at all times.** For every operation both expose, the MCP `result` and the CLI `--json` output are identical. Where they ever differ, MCP''s shape wins, as the agent''s primary door. The envelope is not part of this: MCP wraps results with the alert and nudges, and the CLI prints those to stderr so `--json` stays clean (D19). A test drives every registered MCP tool through both adapters and compares results, and fails on a tool it has no row for, so a new tool cannot escape the rule. CLI-only administrative commands (init, status, export, import, restore, setup, mcp) have no MCP twin and are outside it.

**CLI text** has its own formatter and is outside the projection, but its wording is decided here. Flagged neighbors print as `⚠ newly STALE (review/reconcile):` followed by `- <prefixed id>: <because>`; when none print, `no newly flagged neighbors.` `close`, `wont_do` and `retire` print the task line without a neighbor list. `add` prints the created slice.

Trigger: a plan ingest called board(label=…, status=''spec'') and got 228 KB (full impl-ready bodies × full neighbor `because` text), blowing the token budget and forcing a fallback to grepping a dumped file. Slice returns went lean after a start-of-session orientation run spent about half its link-heavy `show` payload on neighbor fields. Write results went lean after a retrospective of 20 sessions found edit results were 36% of tracker tokens, three quarters of that flag text, and that `close` and `add` echoed bodies the agent had just written or would not use.

Consistency rationale: `search` proves the idiom: `SearchHit` carries no `description` and `search` has `name_only=True`. This propagates an existing projection idiom, not a new paradigm.

INVARIANT (forceful): `description` is include-or-OMIT, NEVER truncated. A half-shown impl-ready body looks complete and an agent acts on the wrong half; include/exclude is honest, truncation isn''t. `show(id)` is the only full-body path for one task; `ls`/`board` provide bodies only on opt-in. The same applies to neighbor fields: an omitted field is an ABSENT key, never null. Null `last_edit_delta` keeps its D34 meaning, "this neighbor was never edited".

kind filter: `ls` and `board` take a `kind` filter. status=''spec'' returns design+schema both; `kind` disambiguates. Composes with status/label/stale.

No size guard: a byte-threshold refusal was REJECTED, since lean defaults remove the blowup, so a guard would only add surface for a footgun the defaults already close.

Opt-in mechanism: `include_description` on `ls`, `board` and the edit verbs; `include_neighbor_because` on `board` and `show`, returning both fields on every neighbor entry. Other returns take no opt-in; `show` is the escape hatch. Resist a general `fields` whitelist: search''s `name_only` idiom and the lean default already cover the documented pain.

Audit paths: `board` with both opt-ins reads the full graph; `board(stale=True)` lists every stale task, all of them live (D137); `history(id)` returns a task''s status changes and prior text with each edit''s delta.

Deferred (triaged, revisit ONLY if lean defaults prove insufficient, not a graveyard backlog): general `fields` whitelist; pagination (limit/offset); total / count-only. Each was considered and set aside because the lean default + show(id) cover the 95% case (one body at a time = show).', 'spec', 0, '2026-06-05T08:04:29.435065+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (213, 'Bulk external-link + delta removal from link operations', 'Decision: three changes, one theme — make link creation bulk-friendly and stop link ops from carrying a vestigial `delta`.

(1) DELTA REMOVAL (decision A, 2026-06-05): `link_add`/`link_rm` drop the required `delta`. Source-verified vestigial — link ops do NOT fire the cascade, so the delta has no reader; `link_add` persists via `_add_link(a, b, because)` (the delta never reaches the row), the `links` table (S3) has no delta column, and `load` already creates links with `because` only. `delta` exists to ride a cascade (reconcile compares the upstream edit''s `delta` × the link''s `because`); a non-cascading op produces a delta nobody reads. Applies UNIFORMLY: single ops + new bulk ops + load all agree on because-only. Folds back as a correction to T117, which over-applied `delta` to edge-mutating ops.

(2) LOAD-EXTENSION (the primary win): `load`''s `depends_on` resolves EXISTING tasks, not just batch-local keys. A ref matching `^[DSTM]\d+$` (or a bare `#\d+`) is an existing-task ref; anything else is a batch-local key — reserve the prefixed-id pattern (a batch-local key may not look like one, a parse-time collision guard). The kind-letter is VALIDATED against the resolved task''s actual kind (typo-catch: `S30` must be schema id 30; a mismatch fails loud). Collapses the ingest pattern "one load + 8–13 external link_adds" into a single atomic import, riding load''s existing all-or-nothing rollback.

(3) LINKS_ADD (bulk existing↔existing): a new op `links_add(edges=[{a, b, because}, ...])` for wiring tasks that ALREADY exist (load can''t — it only creates new tasks). Flat edge list; per-edge `because` MANDATORY; NO batch-wide default because (a shared because IS the membership-link anti-pattern — the shape forbids it). Endpoints accept id or prefixed-name (shared resolver). Validate-all-first + atomic + name-every-offender (mirrors reconcile/load): one structural bad edge — self-link (D14), cross-kind meta (D26 meta-island), retired endpoint (D36), unknown ref — rejects the WHOLE batch and lists all offenders. `already_linked` is a benign no-op (re-runnable), counted not rejected. Compact echo: counts + the created [a, b] pairs by prefixed-name, never the because text or neighborhoods (link ops are structural — the result carries no obligation to act on).

Shared primitive: a ref-resolver (prefixed-name/id → task, with kind-letter validation) underlies BOTH (2) and (3) — build it once, expose it on both surfaces.

Out of scope: the read-projection booleans / pagination belong to D211, not here.', 'spec', 0, '2026-06-05T08:26:46.942108+00:00', '2026-11-05T00:28:47.130899+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (231, 'Enforcing reactive side-door tracking: observable-event triggers over recognition-gated rules', 'PROBLEM (recurring). The side-door tracking discipline (## File side-door work, SKILL.md) keeps failing in practice — net-new work lands in git and never the graph. The cause is structural, not effort: the rule''s trigger is INTERNAL RECOGNITION ("the moment you recognize side-door work"), and recognition is exactly the faculty that goes quiet at the reactive moment. Every OTHER tackit trigger fires off an OBSERVABLE in-system event (closing -> close-gate, editing -> cascade, loading a plan -> file the tasks); this one alone fires off a self-classification the agent must perform BEFORE the rule engages — and that classification is the silent-failure step. Louder/FORCEFUL text cannot fix it, because the text is only consulted AFTER recognition has already fired.

DECISION (current solution — realized by T229). Re-anchor the trigger from the internal category to an OBSERVABLE event: "did I Edit/Write a version-controlled file the current task doesn''t name?" — answerable by looking, not by recognizing. Separate the TRIGGER (mechanical, fires reliably) from the VERDICT (judgment, forced OUT LOUD so it cannot silently evaporate). Forbid the silent skip explicitly (the "felt like housekeeping" skip). The verdict still uses judgment (new task / fold-back / not-tracked); the fix is that a disposition must be STATED, not that a task must always be filed.

GENERALIZES. This is the pattern for ANY recognition-gated discipline, not just side-door: if a rule''s trigger is "when you notice X, do Y," the hidden first step "classify this situation AS X" is where it silently fails. Re-anchor such triggers to observable events wherever one exists.

DESIGN PRINCIPLES (constraints on every future solution).
(1) Enforce the CHECKABLE thing (a disposition was stated), not the JUDGMENT thing (the disposition was correct) — the latter cannot be mechanized, and trying to over-fires.
(2) Over-forceful enforcement is self-defeating: a mechanism that cries wolf gets rubber-stamped (the same failure as false-stale flags eroding the cascade). Prefer mechanisms SILENT on the common case.
(3) Match enforcement cost to leverage: a deterministic, always-on mechanism (a harness hook) is a cross-cutting architectural commitment, not a per-rule patch.

FUTURE DIRECTION (under review — candidate T230). A harness-level Stop hook as a deterministic backstop the HARNESS fires regardless of whether recognition fired — the one mechanism that escapes the recognition loop entirely. Deferred: a hook is deterministically expensive (fires every turn on the critical path) and cross-cutting, so it is GATED on a tackit-wide hooks-architecture review (see T230) that must decide how/where hooks fit as a SYSTEM before any single hook lands.

LIVING SLICE. This decision is expected to evolve as enforcement improves (SKILL text -> observable re-anchoring [current] -> possibly hooks -> ...). Update this slice when the enforcement strategy advances; it is the durable home for "how we make reactive / recognition-gated disciplines actually fire," filed because the problem has re-surfaced constantly.', 'spec', 0, '2026-06-13T01:14:50.143537+00:00', '2026-07-20T03:52:36.160873+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (234, 'A spec slice holds decisions, not code''s literals: the authority-vs-copy litmus', 'Decision: a design/schema slice body holds the DECISION + rationale + derivation rule — never a literal, NOR a code LOCATION (file path, line number, `see foo.py`), whose authoritative home is code. Both a copied value and a code-location pointer rot the instant code changes; a durable spec must carry neither. Operationalizes the `## Kinds` line-59 promise ("design = what is decided, not how it''s built") at the level of body content.

THE PROBLEM. design/schema kinds promise durability — they ARE the spec that survives `git clone` once `.tackit/tackit.db` is gitignored (M187 / examples/specs.sql). A slice that mirrors a code-owned literal — a default (`pool_size=30`), a full enumerated value list (e.g. all 39 env vars with their defaults) — breaks that promise: the literal goes stale the instant code changes, and the slice becomes a stale duplicate of a value whose real home is code. A decision and its rationale change only when the *design* changes; a copied literal changes on any tuning edit.

THE LITMUS (authority-vs-copy). Before typing a literal into a slice, ask: is this slice the AUTHORITY for the value, or a COPY of one that lives in code? Authority → state it, it''s a decision (D238''s "three networks, these isolation roles" is a topology decision — stays). Copy → replace the literal with the derivation rule ALONE ("pool sized K×parallel×2, raise in lockstep") and STOP — do NOT append a `see config.py:pool_size` pointer; a code-location pointer is itself a spec→code reference that rots on any rename/move, the very anti-pattern this rule forbids. The reusable move: pin the rule/formula/policy; never name where the code lives.

THE BRIDGE IS ONE-DIRECTIONAL (the general principle). The spec↔code connection runs code→spec ONLY: code names the spec by its id (D#/§/T#, grep-bridged via the D32 prefix + the code↔task naming convention); a spec NEVER names code. A reader reaches the implementation by grepping the slice''s id, not by following a pointer the slice carries — because code churns continuously while the slice is durable, so any code reference the slice holds (a line number, a path, a `see foo.py`) is a decaying liability. Verify against the code when you WRITE the decision; then anchor the durable text to the stable thing (a symbol name, a decision-id), never the volatile location.

THE RECONCILE TRAP (why this needs enforcing, not just stating). The failure was recognition-gated: a reconcile of D50 RE-INTRODUCED the anti-pattern — re-listing all 39 vars with exact defaults to make the slice "correct," which made it correct-but-fragile. "Correct" pulled toward snapshot-completeness when the durable target was correct DECISIONS. So the rule carries a reconcile-specific clause: a reconcile updates a slice''s decisions/reasoning to match reality, and must NEVER import the current literal values. Spec accuracy = accurate decisions, not a fresh snapshot of derived numbers. (Same recognition-gate shape as the side-door miss — see D231 link.)

THE GUARD (don''t over-correct into vagueness). Pointing to code instead of stating values must not hollow the slice into "we use pooling, see code" — that strips the decision of reviewable substance. Keep enough concreteness to EVALUATE the decision: the formula, the policy (hard-fail on missing required), the required set''s membership rationale. Drop only the resolved snapshot literal. (Mirror of D231''s "over-forceful enforcement trains dismissal" principle, applied to content.)

RELATION. Sibling to the no-hub / fake-task rules (D38) and the granular-description discipline (D37): D37 says have ENOUGH detail; this says don''t have the WRONG, stale-prone detail. Shared ancestor with no-hub: each artifact owns only what''s authoritative to it, nothing it merely mirrors. Kept distinct (no-hub = task decomposition; this = slice content).

SURFACE (where it ships). Canonical SKILL.md as a standalone `##` immediately after the granular-description discipline (D37), form = positive recipe (state what the body IS), not a prohibition list — per writing-skills "match the form to the failure" (baseline failure is wrong-SHAPED output, for which prohibition wording backfires). Pinned by a presence-pin test on the litmus phrase + the reconcile clause so a revert fails CI. SKILL.md-only for now (not propagated to a per-op docstring — it''s a content discipline, not a single op''s sharp edge); revisit if edit/reconcile misuse recurs.', 'spec', 0, '2026-06-17T19:38:06.618392+00:00', '2026-11-26T03:05:52.786920+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (245, 'a decision homes in a spec slice, not a production body', 'INVARIANT: a design/schema-grade decision — one that alters what is decided, or the store''s shape — homes in a design/schema slice, never in a production/meta task body. A production/meta task may reference a decision through a link; it is not the decision''s home.

WHY: when a decision surfaces during production work, the fold-back reflex records it in the active task — which during build work is `production`. The decision then strands: the governing spec slice silently goes stale, the decision is not discoverable from the spec layer, and the existing link + a routine `reconcile` make the graph LOOK maintained. Observed pattern: an architectural decision (a locked layout) lived only in the production task that built it, while the design slices it linked to still described the superseded design, and a reconcile had cleared their stale flag without ever porting the decision in.

ENFORCED AT FOUR SKILL.md SURFACES (each one-purpose, each stating the rule in its own words; the skill cites no task ids, D207):
- create: a task that BOTH settles a design/schema-grade decision AND builds it is split — decision → design/schema slice, build → production task linking to it.
- fold: a fold-back that alters the spec lands in the governing design/schema slice (edit it, or add + link), not the production body you are in.
- report: the end-of-turn fold-back report tags each discovery decision-vs-impl; a decision recorded in a production body is a defect, not a satisfied fold-back.
- reconcile: reconciling a spec staled by a production/meta edit means PORTING the decision into the slice, not just acknowledging the prose still holds.

DETECTION of an ALREADY-stranded decision is semantic (agent judgment), not deterministically computable; its fallback net is the stranded-decision audit (linked, provisional).

ANTI-PATTERN: appending a decision to the nearest production task because that is where you are working; clearing a spec''s stale flag via reconcile when a decision still needs porting into it.', 'spec', 0, '2026-07-07T05:08:16.215200+00:00', '2026-11-26T04:34:06.443452+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (247, 'stranded-decision audit — surface production/meta tasks harboring spec-grade decisions', 'PROVISIONAL (label: provisional) — design NOT settled; do not implement (see the linked provisional impl task). Fallback net for D245 (the decision-homing invariant): an after-the-fact, human-or-agent-triggered audit that surfaces production/meta tasks possibly harboring a design/schema-grade decision that belongs in — and is missing from — a spec slice.

DIVISION OF LABOR: the tool NARROWS deterministically; the agent JUDGES. The tool never classifies prose or emits a verdict — "is there a stranded decision here?" is semantic. No accurate deterministic locator exists (tested: phrasing-grep is brittle and domain-polluted — "locked"/"canonical" are ordinary domain words; edit-graph churn has poor precision AND recall).

WHAT IT ANALYZES — three ORTHOGONAL yes/no axes per production/meta task (independent, so tripping several = real corroboration, not a set-containment artifact):
- Accretion: body-revision count >= threshold.
- Volume: body length >= threshold.
- Isolation: design/schema link-count ~ 0.
(thresholds tuned at impl, deliberately not fixed here.)

SURFACING: rank by intersection depth — tasks tripping all 3 axes first, then 2, then 1; each candidate labeled with which axes fired. Non-orthogonal/weak signals (spec-neighbor edit-lag; a window''s prod-vs-spec edit-kind ratio) are per-candidate ANNOTATIONS / header context, NOT ranking axes. Recall-biased: it is a human-skimmed review queue, so a false positive costs one glance and a false negative is the whole point; tiering improves skim ORDER, not precision (a legitimately-isolated churny task will sit next to a real stranding — the agent judges each).

GROUNDING MESSAGE (returned WITH the candidate list so the deterministic output is actionable):
- purpose: these may harbor decisions that belong in a spec slice.
- interpret: the axes are proxies, not verdicts — read the body and its linked specs.
- act: is there a decision here that alters the spec and is absent from a design/schema slice? yes → promote it (edit the governing slice, or add kind=design/schema + link; reclassify/split if the task conflates decision+build). no → leave it.

OUT OF SCOPE v1: detecting a spec slice whose CONTENT is stale (describes superseded design) — needs to know the architecture changed, not deterministically computable; possible later facet.

OPEN (why provisional): exact axis thresholds; new MCP tool vs an extension of ls/board; whether the grounding message lives in the return payload or a cited skill.', 'retired', 1, '2026-07-07T05:09:02.038867+00:00', '2026-11-26T03:05:42.227126+00:00', 'design', 'Premature promotion — audit design was not settled; relocated to the M266 meta scratchpad until firm. Retiring the prematurely-created spec slice.');
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (249, 'dead-reference soft-suggest on retire / rename / reclassify', 'DECISION: when a task''s identifier stops naming a live referent, tackit searches for references to it in its LINKED neighbors and EMITS A SOFT SUGGESTION on any that still cite it. Detection-and-suggest only — no hard gate.

TRIGGERS — the only three events that kill a reference target:
1. retire(T) — T''s id dies.
2. an edit(T) that changes the id in T''s NAME (a rename/renumber) — the OLD id dies.
3. reclassify(T) out of design/schema — T is no longer a spec, its id leaves the spec liveness set.

DETECTION — in each linked neighbor''s body, search for BOTH of T''s identifiers:
- the SYNTHETIC prefixed-name (D32): D345 / T123 / S12 / M7 — always available, tackit-owned.
- the NAME-CONVENTION id: a project token embedded in the name, e.g. `§9.3`, regex-extracted from T''s name. Convention-dependent / configurable (the `§`-style is one project''s scheme); best-effort, not universal.
For a rename, search the OLD id (from prev_name); for retire/reclassify, T''s current id.

OUTPUT — a soft SUGGESTION surfaced on the neighbor, worded toward REPOINT / RATIONALIZE / resolve-into-the-logic (for a rename, name the successor id). It must NEVER say ''delete'' or ''remove'': a bare token deletion strands the dependent''s logic AND converts a deterministically-detectable dead ref into an undetectable dangling one (the lazy-delete failure). And NO hard gate — a gate manufactures the lazy-delete incentive by making token-absence the pass condition.

SCOPE (narrow, deliberately): LINKED neighbors only (rides the cascade reach). Store-wide / UNLINKED dead refs, and the liveness ambiguity the buried-decision anti-pattern injects (measured: ~half of dead-ref candidates become unresolvable without human judgment when section ids are smeared across spec slices and production task names), are DEFERRED to the agent-driven audit sweep — not this inline mechanism.

DEPENDENCY: accuracy is gated on D245. In a clean, D245-compliant store the three triggers above are the complete cause list and detection is exact; with buried decisions the liveness set has no single source of truth, so the clean deterministic version requires the D245/D247 cleanup first.

REALIZATION: SHIPPED in T252 (commit 9a9831e) — id-search + soft repoint/rationalize suggestion on retire / rename-edit / reclassify over the cascade''s linked-neighbor set, surfaced as deadref_suggestions in the envelope. v1 SCOPE NOTE: reclassify-out-of-spec emits on the SYNTHETIC id only (D#->T#, unambiguously dead); the §-name-convention token is NOT emitted on a reclassify, because a reclassified section MOVED to production rather than died — that moved-vs-died ambiguity is the buried-decision ambiguity (D245-gated) and belongs to the audit sweep (D247), not this inline mechanism.', 'spec', 0, '2026-07-07T18:05:54.321891+00:00', '2026-11-26T04:34:06.443452+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (250, 'a spec slice is a coherent current-state body — rewrite, never append', 'DECISION: an update to a design/schema slice must yield ONE coherent current-state body. The slice is the CURRENT answer, not a changelog — superseded prose is removed and integrated, not stacked under a dated "SUPERSEDES ... above" block. Edit history lives in the description_revisions audit (D29), never inline.

WHY: a spec slice that accretes dated blocks self-contradicts — it asserts the old and the new at once, and a reader can''t tell which sentence is authoritative without diffing dates. That is worse than a merely-stale slice (which is at least internally consistent). Measured: ~28% of spec slices in a real store carry the append fingerprint. Driver: the agent optimizes "is the new info now present" (which gets a completion signal) over "is the old, contradictory info now absent" (which nothing scores), and imitates the slice''s own established append house-style.

ENFORCEMENT — three surfaces:
1. STRUCTURAL — refuse edit_append on design/schema kinds. edit_append is the ONLY op that tacks text on without engaging the existing body — the mechanical driver of the anti-pattern. Removing it forces edit / edit_replace_substring (which make you read or anchor the body). Append stays legal on production/meta, where chronological Phase-N logs are correct.
2. REAL-TIME FEEDBACK — after an edit / edit_replace_substring of a design/schema slice, run a deterministic check on the RESULTING body and emit a soft, non-blocking nudge if it still reads as an append: the append fingerprint (recurring dates, "SUPERSEDES ... above", stacked stamp bars, IMPLEMENTED/RECONCILED/REVISED markers) OR a dangling-reference shape (a trailing preposition/citation left by a token deletion: "from .", "see ,", bare "§"). This is what catches "the rewrite that appended anyway," and the lazy-delete that D249''s dead-ref suggestions could otherwise induce.
3. DISCIPLINE — a SKILL.md pattern/anti-pattern pair (the appended anti-pattern vs the rewritten current-state) + the rule: updating a spec slice, the deliverable is one coherent current-state body; rewrite, don''t append; history is in the audit.

CHARACTER: non-destructive and PREVENTIVE — the inline half of the response. It prevents the anti-pattern at write-time without forcing any risky action (unlike a hard gate). Existing appended slices (the ~28% backlog) are remediated by the agent-driven audit sweep, not by this.

BUILD ORDER: FIRST. It is the coherence quality-bar the dead-ref work (D249) depends on — a dead-ref suggestion resolved by a lazy deletion is a coherence failure this catches.', 'spec', 0, '2026-07-07T18:08:39.247451+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (254, 'The kind ontology — spec / production / meta as deciding / doing / thinking', 'Three tenses of one system: a SPEC is what should be true; CODE is what is true; a PRODUCTION task is the change between them; META is thinking that has not become any of those yet.

- spec (design D# / schema S#) — THE DECISIONS. What should be true, durably: design = a decision, schema = the store-shape contract. The system of record for every design-grade decision. Never "completes" — edit to refine, retire only when 100% abandoned. Not a checklist, not a task.
- production (T#) — THE DOING. A single unit of change that realizes an ALREADY-MADE decision in code ("to realize D-x, do these things"). A forecast while open (rewrite to correct/shrink as code clarifies it); a record once closed. Must link the spec it realizes. Not for deciding (→ spec), not for progress (git + status carry it), not a notepad (→ meta), not broadenable (spawn a new task).
- meta (M#) — THE THINKING. A notepad: experiments, investigations, brainstorming, release bookkeeping — where a decision gets REACHED before it is distilled into a spec. Links nothing outside the island (the meta-island constraint, D110); anything goes.

DISCRIMINATOR when a task blurs: is it DECIDING, DOING, or THINKING? Deciding → spec; doing (settled) → production; thinking / not-yet-settled → meta.

WORKFLOW DIRECTION, never a shortcut: meta → spec → production → code.

Grounding: a sweep of the dogfood store found "production" actually fused three species — pure build/checklist (the clean case), reconciliation/decision tasks (pose a spec↔code gap and adjudicate it — design-nature), and investigations (meta-nature). Only the first is truly production; this ontology draws the line so the other two route to spec/meta. Realized in SKILL.md as the conceptual anchor above the existing status-partition reference table.', 'spec', 0, '2026-07-08T01:32:03.124580+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (255, 'Production editing contract — no edit_append; rewrite only, for correction or scope-shrink', 'A production task is a forecast while open and a record once closed (see the kind ontology). Its body must not ACCRETE — accretion is how design decisions and progress-noise strand into production bodies (dogfood sweep: decisions ride in on progress appends; ~91% of production fold-backs are additive, ~47% of those are design decisions).

DECIDED: edit_append is REFUSED on kind=production, extending the D250 / T251 refusal that already covers design/schema. Append becomes a META-ONLY operation — meta is the notepad; specs and production are rewrite-only.

edit() full-rewrite REMAINS allowed on production, for exactly two purposes:
- CORRECT — fix an error in the task''s own transcription of its spec, INCLUDING a wrong prediction of how the code would actually go. A production task is a forecast; code + tests falsify forecasts; correcting the forecast is the task doing its job, not misuse. There is wide latitude in a task''s granularity, so "I mis-predicted the implementation" is a legitimate correction, not a smuggled decision.
- SHRINK — narrow or remove scope.

NOT allowed via edit: adding scope/features (spawn a NEW production task), recording progress (git + status carry it — it does not belong in tackit at all), or recording a design decision (→ a design/schema slice).

BOUNDARY: a correction fixes the task''s COPY of a decision; it never ORIGINATES one. If the spec itself turns out wrong, that is a slice edit + reconcile, not a production correction.

Realized by (production tasks, later): extend the edit_append kind-guard in core.py to include ''production''; update SKILL surfaces + the fold-back discipline to route additive learnings away from production.', 'spec', 0, '2026-07-08T01:32:03.125626+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (256, 'Production creation gate — a production task must link a design/schema slice at creation', 'A production task realizes an ALREADY-MADE decision (see the kind ontology). DECIDED: add() and load() REFUSE creating a kind=production task with zero design/schema links — a hard gate, no escape flag.

WHY: it removes the "no slice to promote into" excuse that lets a fully-formed decision strand at creation. Dogfood sweep: ~40% of confirmed strands had ZERO edits — the decision was dumped complete at creation, which the append-ban cannot catch; only a creation-time gate can.

NO ESCAPE HATCH: genuinely spec-less mechanical work (typo fix, dependency bump) is NOT a production task — it implements no design unit, so it does not belong in tackit any more than progress does (an untracked commit, or a one-line meta note). A pure gate avoids a loophole that would be abused.

ACCEPTED RISK: the gate ensures a slice EXISTS and is linked; it cannot force the decision to live IN the slice rather than the (append-frozen) body — a stub slice plus a dumped body still satisfies it. Mitigated by workflow discipline (author the slice first — the plan-first control batch produced zero strands across 29 tasks) and by the D247 audit as backstop. The gate makes the good workflow the path of least resistance; it is not a perfect seal.

ERGONOMIC CONSEQUENCE: linking a production task to its design/schema anchor makes that anchor a cascade NEIGHBOR — editing the production task stales the anchor, and the close-gate walks the connected graph transitively, so a stale anchor can block closing the task until it is reconciled. Reconcile the anchor after such an edit. This is inherent link-cascade mechanics, surfaced (not caused) by the gate making every production task carry a spec link. Realized by T263: a kind-guard in add()/load() (core.py) refusing production creation with zero design/schema links; the error names the rule and the fix (author/link the governing slice).', 'spec', 0, '2026-07-08T01:32:03.126365+00:00', '2026-11-26T03:05:52.786920+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (257, 'Meta is a notepad — island boundary kept; no shortcut from meta to code', 'Meta is THE THINKING layer (see the kind ontology): experiments, investigations, brainstorming, release bookkeeping — the mutable playground where a decision gets REACHED. Refines the meta-island constraint (D110) with meta''s ROLE plus two guards.

1. LINK BOUNDARY (D110 unchanged): meta links only within the island. meta↔meta is ALLOWED — a multi-part note may cross-reference itself and cannot contaminate the spec graph. meta↔non-meta is REFUSED. Decision: keep meta↔meta legal — forbidding it adds friction with zero integrity benefit, since the meta-island''s purpose is bounding the cascade OUT of meta, already served by the existing rule. Appending to a meta flags no neighbor (D10); `edit` and `edit_replace_substring` on a meta still do, so use those when a note really changes what a neighbor should say.

2. NO SHORTCUT TO CODE (SKILL discipline, NOT a tackit gate — tackit has no working-tree/commit view, so it cannot refuse "you shipped code with only a meta task open"): code never ships straight from meta. A decision distills from meta into a spec slice; the spec spawns a production task; the task ships. Investigations and experiments live in meta, NOT production — much of what the dogfood store currently mis-files as production (reconciliation + investigation tasks) is meta-nature.

Realized by (later): SKILL.md meta-guard text. The link boundary needs no code change — D110 already enforces it.', 'spec', 0, '2026-07-08T01:32:03.126458+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (258, 'Fold-back retargets by learning-type — decision → slice, progress → out, correction → production rewrite', 'The fold-back discipline (capture learnings as they surface) currently routes ~64% of learnings into production bodies and only ~6% into slices (dogfood sweep) — it is the primary delivery vehicle for stranded decisions. This is H1 promoted into a standing habit: the active task at discovery-time is production, so "fold it into the task I''m in" lands the learning in the wrong layer.

Fold-back is RE-AIMED, not removed. A captured learning is exactly one of three things, each with a different home:
- DECISION (design/schema-grade) → fold into an EXISTING design/schema slice, or (more often) create a NEW one. NEVER the production body.
- TRANSIENT PROGRESS (built X, verified Y, committed Z) → NOT tackit at all (build-book / memory); git + status already carry it.
- CORRECTION / SHRINK → a production rewrite (per the production editing contract) — the only fold-back that may touch a production body, and it corrects, never adds.

RULE: a fold-back into a production task is ONLY for correction/shrink — never additive. Additive capture goes to a slice (if a decision) or nowhere in tackit (if progress). Meta is exempt — it is a notepad; fold anything.

Realized by (later): rewrite the fold-back discipline text in SKILL.md and the mandatory end-of-turn fold-back report to route by learning-type; global CLAUDE.md + README carry the same discipline and move in lockstep.', 'spec', 0, '2026-07-08T01:32:03.126539+00:00', '2026-11-26T03:05:52.786920+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (259, 'Closed tasks are immutable — no edit on a closed task; reopen only to correct its record', 'A closed task is a RECORD of a completed unit of work, and a wont_do task is a RECORD of a dropped one (kind ontology D254: a production/meta task in a terminal state is a frozen record). Editing such a record in place is how design decisions launder in and how a settled task silently acquires new content: the dogfood store showed ~67% of production edits landing on already-closed tasks, ~35 of them folding a design decision into a closed body.

DECIDED: the content-edit ops (edit, edit_append, edit_replace_substring) are REFUSED on any terminal production/meta status, status IN (''closed'', ''wont_do''). No exceptions, no in-place-edit escape flag.

REOPEN corrects a closed record; it does not resume work. Its two uses: fixing a closed body''s text (reopen, edit while open, close again; there is no separate verb), and undoing a premature close. New work after a close is a NEW task linked to the closed one, so the closed record stays an honest account of what was done. A close that should have been a drop is recorded directly with wont_do on the closed task (D56). A reopened task arrives unflagged, because closed tasks carry no stale flag (D137). Measured basis: downstream, 66 reopens against 1,843 closes; 38 were one wording sweep, 6 went straight on to wont_do, 3 looked like resumed work.

A WONT_DO task is terminal forever (reopen refused, D56): its body never changes; if the work revives, create a new task.

This replaces D29''s (D138) v0.4 stance that editing closed and wont_do tasks in place was allowed, and restores the pre-v0.4 no-edit-on-terminal refusal (M115). It KEEPS D29''s description_revisions audit table (S141), whose role narrows to backstopping edits made while a task is open and edits to retired slices.

SCOPE: the refusal covers status IN (''closed'', ''wont_do''). Design/schema RETIRED slices are excluded and stay editable, preserving the annotate-a-dead-decision use (D138 + the D140 code-check); retire-then-edit is rare enough that its archaeology value outweighs the laundering risk there.

Surfaces: the refusal names the rule and the fix (reopen first); the skill, the reopen and wont_do docstrings, CLI help and README state reopen''s correction role.', 'spec', 0, '2026-07-08T01:51:40.804368+00:00', '2026-11-26T04:34:06.443452+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (276, 'The meta→spec→production promotion discipline — start in meta, promote only when sure (guidance, not gated)', 'DECISION. Unsettled / exploratory work STARTS in a meta scratchpad and is promoted to a spec slice (design/schema) — and only then to a production task — ONLY when the decision is settled ("sure"). This extends D254''s one-way meta→spec→production→code flow with (a) a start point (meta) and (b) a promotion-timing rule.

DELIBERATELY GUIDANCE, NOT AN ENFORCED GATE. "Is this settled enough to promote?" is heavy semantic judgment; a code gate there would be costly and low-value. Contrast D256, whose gate is cheap precisely because "does this production task link a spec?" is STRUCTURAL. So this discipline lives as instructions on the agent-facing surfaces; the agent and the user hold each other to it. There is no refusal.

WHY: promoting before sure pushes not-yet-decided content into the spec + production layers, where it reads as a made decision that isn''t. The tell is a `provisional`-labeled SPEC slice — a contradiction (a spec asserts a decision; `provisional` says there isn''t one). The cost is churn: prematurely-created tasks must be superseded once the real shape emerges. (Anchor: D247/T248/T253 were promoted early and had to be relocated back to meta scratchpads.)

THE REALIZATION-vs-NEW-DECISION NUANCE (scope). The three tiers are traversed IN FULL only for a NEW decision. Work that REALIZES an already-made decision — a bug fix, a doc propagation, a mechanical correction — does NOT mint a new design slice: it flows meta→production, LINKING the existing governing spec (which satisfies D256''s creation-gate). "Promote to design" means "ensure a spec slice exists for the decision"; for an existing decision that is already true. So this is NOT "invent a design slice for every task" — it is "a NEW decision homes in a spec slice before it is built" (see D245). The litmus: does this task DECIDE something new (→ needs a spec slice first) or REALIZE something already decided (→ link the existing spec)?

SURFACES (guidance to propagate — production step, later): SKILL (meta is the first home for unsettled work; promote when sure; the realization-vs-new litmus); add/load docstrings (for exploratory work create kind=meta first; author a spec only once the decision is settled).', 'spec', 0, '2026-07-08T21:56:13.503735+00:00', '2026-11-04T22:48:32.215884+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (277, 'A production-task edit is a smell of thin prep — discouraged though allowed for correction / scope-shrink', 'DECISION (a stance layered on D255). Modifying a production task is highly DISCOURAGED. It is NOT forbidden — D255 already scopes production edits to correction / scope-shrink (append refused, scope-addition refused) — but even those allowed edits are a SIGNAL that up-front prep was thin. This slice adds the normative stance; it introduces no new refusal.

CALIBRATION. A single mechanical correction (e.g. a wrong forecast of the code path) is tolerable and stays legitimate per D255. RECURRING edits to a production body are the smell: they mean the governing spec was underspecified, or the task was not impl-ready (D37). The correct response to the signal is to fix the PREP — refine the spec / re-derive the task — NOT to keep iterating the production body.

WHY: a production task is the forecast of ONE settled unit of change. If it keeps moving, the thing that was supposed to be settled — the decision, which lives in the spec — wasn''t. Framing production edits as routine hides that; framing them as a smell routes attention back up a tier (spec / meta) to the real gap.

NOT FROZEN — SCOPE BOUNDARY. This is about OPEN production tasks. It does not freeze them (contrast D259, which DOES freeze editing on closed/wont_do terminal rows). Mechanical correction remains allowed; this slice changes the STANCE toward it, not the mechanics.

SURFACES (guidance to propagate — production step, later): SKILL §edit + §fold-backs (a production edit, though allowed, signals thin prep; recurring ones mean fix the spec); edit / edit_replace_substring docstrings (editing a production task is a smell of thin spec prep).', 'spec', 0, '2026-07-08T21:56:20.178669+00:00', '2026-11-26T03:05:52.786920+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (280, 'Missing-edge soft-suggest — a body naming a live unlinked task/slice surfaces the missing link (inverse of D249)', 'DECISION. A deterministic, ADVISORY detector: when a task or slice body textually names another LIVE task/slice by its SYNTHETIC ID (e.g. T641 / D261 / S15 / #id) and that target is NOT a current linked neighbor, surface it as a candidate MISSING link. Agent judges; never a gate (detection-over-gating, per D249''s rationale — a gate would incentivize lazy token-deletion). This is the INVERSE of D249: D249 flags a body naming a DEAD (retired/renamed) target; this flags a body naming a LIVE-but-UNLINKED one. Reuses T252''s boundary-guarded synthetic-id extraction.

SCOPE — SYNTHETIC-ID ONLY; §-refs are NOT scanned. Empirically refuted (read-only test vs a reference project): adding §-ref scanning gave ZERO extra recall over synthetic-id alone and introduced fan-out + collision noise (a §-family fanning to many sub-slices; markers shared by multiple slices). §-refs are a legacy pre-tackit convention carried by one long-lived project; the synthetic id is the reliable, deterministic, go-forward addressing scheme. Do not scan §-refs.

WHAT IT CATCHES / LIMITS. Primarily the fold-back-forgot-edge case (D245): a spec slice ports a decision from a task and NAMES the task, but the link edge was never drawn (slice→task). On the observed cluster it recovered ~half the real gaps. It does NOT catch production→spec UNDER-linking (task→slice): production bodies name their governing slice by NO token at all — semantic description only ("the loader", a table name) — so no token scan recovers them. That remainder is IRREDUCIBLY SEMANTIC and belongs to the periodic semantic audit (the agent-driven audit-playbook thread, currently maturing in meta); cheapen that audit by content-RANKING what the judge reviews, not a blind full sweep. Accepted: this cannot eliminate 100% of link gaps.

PRECISION GUARDS. (1) EXCLUDE any pair straddling the meta-island boundary (a production body naming an M#: that edge is forbidden by construction — never suggest it). (2) DOWN-WEIGHT / skip id mentions in superseded / past-tense / contrastive contexts ("X was dropped in T…", "unlike T50…") — the token appears without a live coupling.

DELIVERY. Advisory soft-suggestion via an envelope field, mirroring D249''s deadref_suggestions — fired reactively on edit (the fold-back moment, primary), and available as a batch/store-wide scan. Never blocks.

RELATED (NOT built here). The higher-leverage lever for the task→slice direction is PREVENTION at creation-time: rank the design/schema anchor layer by content-overlap with a new production task and nudge "which of these does this realize?" — surface+prompt, agent judges, at the moment the semantic signal is freshest. That is a separate future thread; this slice is the detection side only.', 'spec', 0, '2026-07-08T22:44:01.789476+00:00', '2026-11-26T03:05:52.786920+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (283, 'The task surface is read-and-judge, not pattern-and-mutate', 'Decision: tackit''s task surface is READ-AND-JUDGE, not PATTERN-AND-MUTATE. Operations address structured metadata and whole bodies; none pattern-match, query, or bulk-rewrite body CONTENT. The agent reads and semantically interprets a slice body; any op that lets it grep/replace over bodies instead is refused, because it re-opens the exact lazy path the surface exists to prevent.

THREE CONSEQUENCES OF ONE LINE:
1. Search stays FTS keyword (extends D17''s "keyword only; semantic/vector out of scope"): NO regex / substring / content-pattern search over bodies. The need it seems to serve — "find every body matching a code-location pattern" — is precisely the scan-don''t-read move; the agent must open and judge candidates surfaced deterministically (D27).
2. Edit stays whole-substring + unique-match: NO replace_all / bulk body-content rewrite. The uniqueness-required friction is load-bearing — it forces the caller to confront each site and the blast radius before mutating a decision body.
3. Aggregation/sort IS allowed but firewalled to STRUCTURED COLUMNS ONLY (kind, status, label, created_at, updated_at, link-degree, stale) — NEVER a body-derived key. Enforce as a TYPE CONSTRAINT (an enum of column names, fail-loud on anything else), not a guideline. Metadata aggregation is triage: it tells you WHICH bodies to read and structurally cannot substitute for reading (a count/group forces a drill-down to act on). This is the SAME line as (1)/(2), drawn from the safe side.

WHY A SLICE (not a passing note): the regex / replace_all / content-query request recurs; without a durable decision it gets re-litigated every time an op feels "merely mechanical." The surface deliberately sacrifices the rare genuinely-mechanical bulk case to keep the common lazy case impossible.

RELATION: extends D17 (search is keyword-only) and D27 (candidates surfaced deterministically, agent judges). The maintenance need that motivates a body-content scan (e.g. a one-off citation audit) is a conscious admin query outside the work-tracking loop — not a first-class tackit op.', 'spec', 0, '2026-07-20T03:51:50.818202+00:00', '2026-07-20T03:51:50.818202+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (284, 'delta is required iff the op fires the cascade or writes an audit row — terminal status verbs carry reason only', 'Decision: a `delta` is required on an op IFF that op FIRES THE STALING CASCADE OR WRITES AN EDIT-HISTORY ROW (S7). A delta has two readers: reconcile compares the upstream edit''s delta against the link''s `because` to decide whether a staled neighbor needs re-reading, and `history` and `last_edit_delta` read it from the audit row. An op with neither produces a delta no one reads.

D213 already established this and applied it to link ops (link_add/link_rm carry `because` only, no delta). This slice extends the SAME principle to the terminal STATUS verbs: wont_do and retire do NOT fire the cascade (a status change, symmetric with close) — so they must NOT require a delta. They carry `reason` only (the durable rationale, persisted forever). close is the proof the surface already accepts this: close is the sibling non-cascading terminal verb and requires no delta.

SCOPE: the delta-bearing ops remain edit / edit_replace_substring / reclassify (cascade-firing) and edit_append (writes the audit row, does not cascade, D10). wont_do / retire drop the required delta; `reason` stays required and non-placeholder. This is a fold-back correction to T117, which over-applied delta to non-cascading ops — the same correction D213 made for edge ops, now completed for status verbs.

RATIONALE beyond consistency: requiring TWO prose fields (reason + delta) on one terminal call is an ergonomic hazard — it invites front-loading one and dropping the other. Carrying only `reason` removes it.', 'spec', 0, '2026-07-20T03:52:06.056461+00:00', '2026-11-26T04:22:47.673074+00:00', 'design', NULL);
INSERT INTO tasks (id, name, description, status, stale, created_at, updated_at, kind, wont_do_reason) VALUES (306, 'The workflow command set ships as plain files, installed by the agent on request', 'Decision: tackit ships its maintainer''s workflow as plain files inside the package, beside the tackit skill, and an agent copies them on the human''s request. Origin: most of the commands operate entirely on tackit''s metas, slices and production tasks, so they are only useful with it, and shipping them keeps tracker and workflow in step.

The set: the /diagnose, /firmup, /graduate, /build, /orient, /handoff and /trim commands, and the test-audit skill with its reference files, which /firmup, /graduate, /build and /orient depend on. /handoff, /orient and /trim are in the set even though they mainly serve the agent''s memory file, because they carry tracker state between sessions and /handoff and /orient are two halves of one loop. Commands with no tracker coupling stay out.

Plain files, not a plugin. The commands already run as plain files from the agent''s user-level commands and skills folders, so a plugin adds packaging and no behavior. The accepted cost is no namespace: /build keeps its generic name.

Claude Code only. The commands use its argument syntax, its context command and its memory file. Claude Code substitutes `$`-placeholders in command and skill text before the agent sees it, so shipped text carries `$ARGUMENTS` exactly where the user''s argument goes and never another `$` followed by a letter or digit (a literal meaning "zero-cost" reached the agent as the user''s first argument word). The set depends on no plugin: a plain-file install cannot enforce a dependency, and a user can turn a plugin off, so each command states in its own words the method it uses (/diagnose carries its debugging method, including tracing a bad value back to its origin, rather than pointing at an outside skill).

The opinions stay. Billed-eval gates, the context cutoff and the rest are the point of the set and are not softened for a wider audience. Being optional is how a tracker-only user is protected: the tracker install is unchanged, and the set is installed only when the human asks.

Install. The setup emission prints the step and edits nothing, as it does for the rest of the install. The destination is user level, `~/.claude/commands/` and `~/.claude/skills/test-audit/`, because the commands apply across projects. A destination file that exists and differs is never silently overwritten: the agent shows the human the diff and asks, because these files drive every session and get edited locally. An upgrade does not refresh installed copies; re-running setup restates the step so they can be compared.

Self-contained text. Shipped text does not cite the maintainer''s personal configuration: it states each rule it relies on. The memory layout is defined by /trim, so nothing extra ships for it. Each project supplies its own test, deploy and billed-eval commands in its CLAUDE.md, which /build reads. Citations of tackit''s own slices by id stay, because those specs ship publicly.

The repo holds the master copy, and copies in `~/.claude` are deployed from it. An edit made only in a deployed copy drifts and never ships.

Public by default. Everything shipped goes to a public package, so private project names, vocabulary, store ids and dated personal rulings stay out. A repo test checks only generic patterns, since a list of private names in a public test would itself leak them; checking names is the release scan''s job.', 'spec', 0, '2026-11-04T03:25:44.769116+00:00', '2026-11-26T03:05:52.786920+00:00', 'design', NULL);
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
INSERT INTO task_labels (task_id, label) VALUES (247, 'provisional');
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
INSERT INTO links (id, task_a, task_b, because) VALUES (427, 231, 234, 'this rule is a fresh instance of the recognition-gated class D231 generalizes — the failure (a reconcile re-importing the 39 var defaults) was a SILENT recognition miss, and the fix borrows D231''s move: anchor the reconcile clause to an observable trigger (typing a literal that has a code home) rather than to noticing you''re mirroring');
INSERT INTO links (id, task_a, task_b, because) VALUES (449, 245, 247, 'This audit is the deterministic fallback net for D245''s invariant; if D245''s definition of a spec-grade decision changes, the audit''s purpose and axes change, and vice-versa.');
INSERT INTO links (id, task_a, task_b, because) VALUES (451, 172, 245, 'D245''s fold/report surfaces refine D37''s ''absorb impl-time discoveries via edit before close'': D37 routes a discovery into the responsible task body, D245 carves out that a DECISION-grade discovery routes to the spec slice, not the production body. Editing either''s notion of where a discovery lands forces re-checking the other.');
INSERT INTO links (id, task_a, task_b, because) VALUES (452, 234, 245, 'Sibling boundary rules on spec-slice CONTENT: D234 says a slice holds decisions not code literals; D245 says decisions belong in slices not production bodies. Editing either''s definition of what a spec slice is the authority for forces re-checking the other.');
INSERT INTO links (id, task_a, task_b, because) VALUES (453, 171, 245, 'D245''s create-split routes the decision to the design/schema (spec) partition and the build to the open partition — it depends on D36''s kind/status partition; changing the partition changes what ''home a decision in a spec slice'' structurally means.');
INSERT INTO links (id, task_a, task_b, because) VALUES (454, 110, 245, 'D245''s create-split classifies by whether a task settles a design/schema-grade DECISION vs builds it — it leans on D26''s kind taxonomy + the ''alters running-app behavior'' classifier; a change to the kind set or that rule changes the split.');
INSERT INTO links (id, task_a, task_b, because) VALUES (455, 137, 245, 'D245''s reconcile surface refines D28''s bounded-obligation reconcile: reconciling a spec staled by a production/meta edit that SETTLED a decision means porting the decision in, not just acknowledging prose holds. Editing D28''s reconcile semantics forces re-checking D245''s port rule.');
INSERT INTO links (id, task_a, task_b, because) VALUES (456, 137, 249, 'This soft-suggest rides D28''s depth-1 cascade to reach the changed task''s LINKED neighbors; a change to cascade reach or semantics changes which neighbors receive the suggestion.');
INSERT INTO links (id, task_a, task_b, because) VALUES (457, 160, 249, 'Detection searches the changed task''s SYNTHETIC prefixed-name (D32: D#/S#/T#/M#); a change to how that id is formed changes what is searched for.');
INSERT INTO links (id, task_a, task_b, because) VALUES (458, 171, 249, 'Two of the three triggers are retire() and cross-partition reclassify (both governed by D36); changing those ops'' behavior changes when this mechanism fires.');
INSERT INTO links (id, task_a, task_b, because) VALUES (459, 245, 249, 'The dead-reference liveness signal is only clean if decisions live in spec slices; buried decisions corrupt it, so this mechanism''s determinism is GATED on D245 holding.');
INSERT INTO links (id, task_a, task_b, because) VALUES (460, 247, 249, 'Sibling detection: the store-wide / UNLINKED dead-ref scan and the buried-decision liveness ambiguity are handled by the agent-driven audit sweep D247 seeds, NOT by this inline linked-only mechanism.');
INSERT INTO links (id, task_a, task_b, because) VALUES (461, 165, 250, 'Rewrite-vs-append is an edit-quality concern under D34 (edits aren''t free); refusing edit_append on spec + the coherence nudge extend D34''s ''make every edit consequential'' to spec bodies.');
INSERT INTO links (id, task_a, task_b, because) VALUES (462, 172, 250, 'D37''s impl-ready + fold-back-into-the-body discipline is exactly where append-not-rewrite bites (a fold-back appended rather than integrated); this refines D37 so a fold-back yields a coherent body, not a stacked note.');
INSERT INTO links (id, task_a, task_b, because) VALUES (463, 234, 250, 'Sibling spec-content hygiene: D234 says a slice holds decisions not code literals; this says a slice is coherent current-state not a changelog. Both define what a well-formed spec body is.');
INSERT INTO links (id, task_a, task_b, because) VALUES (464, 245, 250, 'Sibling spec-content-quality invariant: D245 governs the KIND that holds a decision; this governs whether the decision is recorded COHERENTLY. Editing either''s notion of a well-formed spec slice forces re-checking the other.');
INSERT INTO links (id, task_a, task_b, because) VALUES (465, 249, 250, 'This slice''s dangling-shape feedback is what keeps D249''s dead-ref suggestion from being resolved by a lazy token deletion; the two must agree on what a coherent post-edit body looks like.');
INSERT INTO links (id, task_a, task_b, because) VALUES (468, 245, 254, 'D245 (a decision homes in a spec slice) is the special case this ontology generalizes — spec=deciding, production=doing; change the ontology and D245''s meaning moves, and vice-versa.');
INSERT INTO links (id, task_a, task_b, because) VALUES (469, 254, 255, 'the append-ban follows from production being a forecast-then-record; if production''s definition changes, this editing contract changes.');
INSERT INTO links (id, task_a, task_b, because) VALUES (470, 250, 255, 'extends D250 / T251''s edit_append refusal (design/schema) to production — one shared kind-guard, one shared rationale (no accretion into the durable + record layers).');
INSERT INTO links (id, task_a, task_b, because) VALUES (471, 245, 255, 'this is the mechanism that stops the additive stranding D245 forbids; move either and the other moves.');
INSERT INTO links (id, task_a, task_b, because) VALUES (472, 247, 255, 'the audit is the backstop for a design decision that slips into production via a full REWRITE (which this contract still permits).');
INSERT INTO links (id, task_a, task_b, because) VALUES (473, 254, 256, 'the gate follows from production realizing an already-made decision; a task still MAKING the decision is meta, not production.');
INSERT INTO links (id, task_a, task_b, because) VALUES (474, 247, 256, 'the gate shrinks what the stranded-decision audit must catch (born-at-creation strands the append-ban misses); D247 backstops what the stub-slice hole still lets through.');
INSERT INTO links (id, task_a, task_b, because) VALUES (475, 245, 256, 'creation-time enforcement of D245''s decision-homing invariant.');
INSERT INTO links (id, task_a, task_b, because) VALUES (476, 254, 257, 'meta is the thinking layer of the ontology; this slice gives it its role + guards.');
INSERT INTO links (id, task_a, task_b, because) VALUES (477, 254, 258, 'fold-back routing follows the three-layer ontology — decision→spec, progress→out, doing-correction→production.');
INSERT INTO links (id, task_a, task_b, because) VALUES (478, 245, 258, 'fold-back mis-routing is the concrete act D245 forbids; this re-aims the exact discipline that keeps violating it.');
INSERT INTO links (id, task_a, task_b, because) VALUES (479, 255, 258, 'the correction/shrink route is precisely the rewrite the production editing contract permits; the two must agree on what may touch a production body.');
INSERT INTO links (id, task_a, task_b, because) VALUES (480, 110, 254, 'D254 is the conceptual ontology (deciding/doing/thinking + the meta→spec→production→code workflow direction) that deepens D110''s mechanical kind taxonomy; a change to D110''s kind set, classifier rule, or meta-island moves the ontology, and a reframing of the ontology forces D110''s definitions to realign.');
INSERT INTO links (id, task_a, task_b, because) VALUES (481, 110, 257, 'D257 refines D110''s meta-island constraint with meta''s notepad ROLE + the no-shortcut-to-code guard; a change to D110''s meta-island boundary changes what D257 refines, and vice-versa.');
INSERT INTO links (id, task_a, task_b, because) VALUES (482, 254, 259, 'closed = frozen record is the ontology principle this hard-enforces; a change to what "closed production/meta" means moves this rule.');
INSERT INTO links (id, task_a, task_b, because) VALUES (483, 255, 259, 'the production editing contract bans APPEND regardless of status; this adds the orthogonal STATUS dimension — once closed, ALL edits are refused, not just appends. The two together define what may touch a task body and when.');
INSERT INTO links (id, task_a, task_b, because) VALUES (484, 138, 259, 'supersedes D29''s v0.4 "edit-on-closed allowed" stance; D29''s audit table survives with a narrowed role (open-edits + retired-edits), so a change to either forces the other to re-align.');
INSERT INTO links (id, task_a, task_b, because) VALUES (485, 137, 259, 'D28 makes closed-stale record-only (you don''t reconcile a closed task); this makes closed CONTENT immutable — together they define "closed is a settled record you neither re-verify nor rewrite."');
INSERT INTO links (id, task_a, task_b, because) VALUES (508, 254, 276, 'This slice extends D254''s one-way meta→spec→production flow with a start point (meta) and a promotion-timing rule; a change to the ontology''s flow changes this discipline.');
INSERT INTO links (id, task_a, task_b, because) VALUES (509, 256, 276, 'This slice''s realization nuance says work realizing an existing decision satisfies D256''s creation-gate by LINKING the existing spec (no new slice), and contrasts its own guidance-only stance with D256''s structural gate.');
INSERT INTO links (id, task_a, task_b, because) VALUES (510, 257, 276, 'This slice makes D257''s meta-notepad the mandatory first home for unsettled work; if D257''s meta scope changes, this start-in-meta rule changes.');
INSERT INTO links (id, task_a, task_b, because) VALUES (511, 172, 277, 'This slice reads recurring production edits as a violated D37 impl-ready-body bar (the task wasn''t prepped enough); if D37''s granularity bar changes, this signal''s threshold changes.');
INSERT INTO links (id, task_a, task_b, because) VALUES (512, 255, 277, 'This slice layers a normative stance (production edits signal thin prep) onto D255''s mechanical production-editing contract; if D255''s allowed-edit rules change, this stance''s scope changes.');
INSERT INTO links (id, task_a, task_b, because) VALUES (513, 258, 277, 'This slice routes the response to a production-edit smell back up a tier (fix the spec) — D258''s fold-back-by-learning-type in the correction case; a change to D258''s routing changes this.');
INSERT INTO links (id, task_a, task_b, because) VALUES (518, 245, 280, 'The primary case this detector catches is D245''s fold-back done half-way — a decision ported into a slice that NAMES the source task, but the link edge never drawn; if D245''s decision-homing changes, what this should catch changes.');
INSERT INTO links (id, task_a, task_b, because) VALUES (519, 249, 280, 'Inverse sibling: D249 suggests a repoint when a body names a DEAD target; this suggests a LINK when a body names a LIVE UNLINKED target — same body-id-scan machinery (T252), opposite target-liveness; a change to D249''s scan/guards changes this one.');
INSERT INTO links (id, task_a, task_b, because) VALUES (521, 59, 283, 'extends D17''s keyword-only search stance (semantic/vector out of scope) to also exclude regex/content-pattern search over bodies; a change to D17''s search contract re-checks this constraint');
INSERT INTO links (id, task_a, task_b, because) VALUES (522, 111, 283, 'D27 surfaces link candidates deterministically for the agent to judge; this slice is the same read-and-judge principle applied to search/edit/aggregation — if D27''s judge-not-enumerate stance changes, re-check this');
INSERT INTO links (id, task_a, task_b, because) VALUES (523, 213, 284, 'extends D213''s ''a non-cascading op produces a delta nobody reads'' principle from link ops to the terminal status verbs wont_do/retire; if D213''s cascade-reader rationale changes, this extension re-checks');
INSERT INTO links (id, task_a, task_b, because) VALUES (531, 165, 211, 'D211''s lean projection decides when D34''s because and last_edit_delta are returned, so changing either rule''s obligation-bearing stale condition forces re-checking the other.');
INSERT INTO links (id, task_a, task_b, because) VALUES (552, 245, 306, '/graduate cites and restates this rule by id, so changing where a decision homes means re-checking the shipped text.');
INSERT INTO links (id, task_a, task_b, because) VALUES (553, 250, 306, '/graduate tells the agent to rewrite a slice rather than append, citing this rule by id, so changing it means re-checking the shipped text.');
INSERT INTO links (id, task_a, task_b, because) VALUES (554, 256, 306, '/graduate and /build rely on the production creation gate by id, so changing the gate means re-checking the shipped text.');
INSERT INTO links (id, task_a, task_b, because) VALUES (555, 172, 306, '/graduate and /build require impl-ready bodies, citing this rule by id, so changing it means re-checking the shipped text.');
INSERT INTO links (id, task_a, task_b, because) VALUES (556, 257, 306, '/graduate''s bridge rule depends on the meta island, so changing the island means re-checking the shipped text.');
INSERT INTO links (id, task_a, task_b, because) VALUES (573, 55, 211, 'D13 defines which neighbors an edit returns and D211 defines their exact shape, so changing the set or the entry fields changes what the other states.');
INSERT INTO links (id, task_a, task_b, because) VALUES (574, 54, 211, 'D12 says close returns id and status and D211 holds that return in its write-result table; a change to the close return must agree in both.');
INSERT INTO links (id, task_a, task_b, because) VALUES (575, 53, 211, 'The worklist''s rows are D211''s short form, so changing that projection changes what the stale worklist returns.');
INSERT INTO links (id, task_a, task_b, because) VALUES (576, 199, 211, 'D39''s compact link_add shape is one row of D211''s write-result table and is bound by its MCP and CLI parity rule.');
INSERT INTO links (id, task_a, task_b, because) VALUES (577, 171, 211, 'Retire''s return is set by D211''s write-result table, so a change to that table changes what D36 states retire returns.');
INSERT INTO links (id, task_a, task_b, because) VALUES (578, 52, 284, 'D10''s edit_append exception is why D284''s delta rule also covers edit-history writes; reversing the exception changes which clause keeps edit_append''s delta required.');
INSERT INTO links (id, task_a, task_b, because) VALUES (579, 52, 255, 'D10''s edit_append exception is safe for fold-backs only because D255 makes append meta-only; widening append to another kind reopens the exception.');
INSERT INTO links (id, task_a, task_b, because) VALUES (665, 172, 258, 'D172 routes impl-time discoveries through D258''s fold-back routing (decision to slice, correction rewrites the body, progress to git); a change to those routes changes what D172 tells an implementer to do.');
INSERT INTO links (id, task_a, task_b, because) VALUES (666, 172, 255, 'D172 sends a correction of a production task''s forecast to an edit/edit_replace_substring rewrite, which exists because D255 makes production bodies correction-only and refuses append; a change to D255''s production-edit rule changes that route.');
INSERT INTO links (id, task_a, task_b, because) VALUES (667, 207, 245, 'D245''s four skill surfaces take the form D207 sets for the skill (plain rules, no task-id citations); a change to D207''s form changes how those surfaces must read.');
INSERT INTO links (id, task_a, task_b, because) VALUES (692, 53, 137, 'D53''s worklist predicate is the stale-implies-live rule D137 sets; if D137 let a terminal row be flagged again, D53''s status clause would stop being a mere guard.');
INSERT INTO links (id, task_a, task_b, because) VALUES (693, 56, 137, 'D56''s close gate and reconcile refusal rest on D137''s rule that only live tasks are flagged; the reconcile refusal''s reason (terminal tasks carry no flag) is D137''s rule.');
INSERT INTO links (id, task_a, task_b, because) VALUES (694, 62, 137, 'D137 makes the no-op close D62 promises reachable for a closed task after a neighbor edit; if terminal rows were flagged again, close on a closed task would be refused as stale.');
INSERT INTO links (id, task_a, task_b, because) VALUES (695, 56, 259, 'D259 sends a mistaken close to wont_do directly and D56 is where wont_do on a closed task is allowed; a change to either changes how a closed record is corrected.');
INSERT INTO links (id, task_a, task_b, because) VALUES (696, 56, 171, 'D56 states what the close-gate and reconcile refusals must say and D171''s refusal bank holds their exact wording; changing either changes the other.');
INSERT INTO links (id, task_a, task_b, because) VALUES (715, 137, 138, 'D137 drops the stale flag on terminal tasks partly because the audit trail already records what changed after a close; D138''s description_revisions table is half of that trail, so narrowing D138''s audit contract weakens D137''s reason for not flagging terminal rows.');
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
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (537, 231, NULL, 'spec', '2026-06-13T01:14:50.143767+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (541, 234, NULL, 'spec', '2026-06-17T19:38:06.618628+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (563, 245, NULL, 'spec', '2026-07-07T05:08:16.215378+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (565, 247, NULL, 'spec', '2026-07-07T05:09:02.039054+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (568, 249, NULL, 'spec', '2026-07-07T18:05:54.322673+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (569, 250, NULL, 'spec', '2026-07-07T18:08:39.248206+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (576, 254, NULL, 'spec', '2026-07-08T01:32:03.125166+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (577, 255, NULL, 'spec', '2026-07-08T01:32:03.126345+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (578, 256, NULL, 'spec', '2026-07-08T01:32:03.126446+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (579, 257, NULL, 'spec', '2026-07-08T01:32:03.126529+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (580, 258, NULL, 'spec', '2026-07-08T01:32:03.126607+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (581, 259, NULL, 'spec', '2026-07-08T01:51:40.804547+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (596, 247, 'spec', 'retired', '2026-07-08T17:04:14.304323+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (609, 276, NULL, 'spec', '2026-07-08T21:56:13.504639+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (610, 277, NULL, 'spec', '2026-07-08T21:56:20.178859+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (613, 280, NULL, 'spec', '2026-07-08T22:44:01.789685+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (625, 283, NULL, 'spec', '2026-07-20T03:51:50.818565+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (626, 284, NULL, 'spec', '2026-07-20T03:52:06.056675+00:00');
INSERT INTO status_transitions (id, task_id, from_status, to_status, changed_at) VALUES (663, 306, NULL, 'spec', '2026-11-04T03:25:44.770106+00:00');
COMMIT;
