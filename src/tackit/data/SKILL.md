---
name: tackit
description: Use whenever planning, tracking, or executing work in a project that
  uses tackit (a task + dependency tracker). Covers when to record work in tackit,
  the reconcile-on-change discipline, the v0.4 verb taxonomy (edit / close /
  wont_do — supersede retired), the bounded-obligation cascade (closed-stale is
  record only), the kind classification rule (design/schema as living spec), and
  the mandatory code↔task naming convention.
---

# Working with tackit

tackit is this project's **single source of truth** for tasks, their links,
and plan/build state. It exists so that truth lives in one queryable place and
survives across sessions and context compaction — instead of scattering into files
that drift and contradict each other.

## Use tackit — don't scatter, and don't overload it
- Record tasks, dependencies, and decisions **in tackit (via its tools)** — never
  in ad-hoc markdown, scratch files, or TODO-comments-as-tracking. Loose tracking
  drifting out of sync is the exact failure tackit exists to prevent.
- If it isn't in tackit, it isn't tracked. Put it in tackit before treating it as
  written down.
- tackit is **not** a knowledge base. Durable learnings and external references
  belong to your memory, not here. Keep tackit to actionable tasks + dependencies.

## Kinds — every task is classified by what it touches

**Spec-vs-impl is the load-bearing distinction.** Under v0.5, kind is not just a
classification — it is **coupled to status by partition** (the schema CHECK
enforces it). Design/schema slices are **specifications**: they capture
decisions and live in a `spec`/`retired` partition. Production/meta tasks are
**implementations**: they realize specs in code and live in an
`open`/`closed`/`wont_do` partition. The verb you reach for follows the
partition: edit/retire on specs; close/wont_do on impl.

| kind        | partition       | meaning                                  | terminal verb  |
|-------------|-----------------|------------------------------------------|----------------|
| design      | spec / retired  | specification: captures decisions        | retire (D36)   |
| schema      | spec / retired  | specification: captures store shape      | retire (D36)   |
| production  | open / closed / wont_do | implementation: realizes specs in code | close / wont_do |
| meta        | open / closed / wont_do | bookkeeping: release tracking, exps    | close / wont_do |

Every task carries a required `kind` (set at create time, T94) from this closed
taxonomy:

- **design** — a decision slice (D# in the design doc). Captures what is decided,
  not how it is built. Editing a design changes what the system means; impl tasks
  link back to design tasks they realize. **Lives at status='spec'** (the live
  partition state) and only ever moves to **status='retired'** via `retire()`
  when the decision is 100% abandoned with no replacement. `close()` and
  `wont_do()` are refused on `status='spec'`; updating a decision is `edit()`,
  not close. The description_revisions audit table (D29) preserves prior
  verbatim state on every edit, so editing in place is recoverable. If the
  decision returns after retire, file a fresh D# (no double-decide per T132
  generalized).
- **schema** — a store-shape slice (S# in the schema doc). The DDL contract.
  Schema changes typically require migrations. **Same partition as design:**
  `status='spec'` live; `retire()` to retired when fully abandoned.
- **production** — code that **alters the running app's behavior**. Source under
  `tackit/`, tests that pin behavior contracts, and the README/SKILL.md **count
  as production** because they alter the agent's behavior of the app. Lives at
  `status='open'`; closes via `close()` (work shipped) or `wont_do()` (scope
  dropped).
- **meta** — work that does **not** alter the running app. Release bookkeeping,
  experiments, observation writeups, side-investigations, dogfood notes. Same
  open/closed/wont_do partition as production.

### The kind/status partition rule (the v0.5 invariant)

Every row obeys the partition: `kind ∈ {production, meta}` ⟹
`status ∈ {open, closed, wont_do}`; `kind ∈ {design, schema}` ⟹
`status ∈ {spec, retired}`. The boundary is enforced at two layers:

1. **Typed (Pydantic):** `Core.add()` applies the partition-correct default
   status at create time. `Core.load()` and `Core.reclassify()` do the same.
2. **Database (CHECK constraint on S1.tasks):** any UPDATE/INSERT that
   would land an illegal `(kind, status)` pair fails with an IntegrityError.

**Cross-partition reclassify auto-shifts status**: open ↔ spec lands cleanly
(the live statuses of each partition). Cross-partition reclassify with no
clean target — e.g. `closed`-production → `design` — is refused; resolve the
source state first (reopen, then reclassify; or accept the refusal and file
a fresh task).

### The classifier rule (apply to every new task)
Ask: would landing this task touch `core.py`, `models.py`, `SKILL.md`, `README.md`,
or tests-that-pin-contracts? If **yes** → production. If the task's sole output is
**editing a tackit-task's description** (a prose-update task) → meta. If it
introduces a D# or S# slice → design or schema respectively.

### The inheritance trap (named because it has bitten us)
**Classify a new task by its OWN scope, not by the parent epic's framing.** A
task spawned during a meta analysis thread that itself includes impl work is
PRODUCTION — not meta — regardless of where the discussion happened. The
v0.3.0 cascade-ergonomics overhaul (T115, kind=meta) spawned T116/T117/T118
which touched `core.py` and `models.py`; classifying them as meta because the
parent was meta required out-of-band correction later. Avoid the trap: when
adding a task, run the classifier on **this task's** scope, ignoring the
enclosing thread.

### Meta-island constraint
Meta tasks can **only** link other meta tasks. `link_add` between a meta task
and a non-meta task is refused at the boundary. This bounds the cascade: meta
work (release tracking, experiments) cannot drag spec/production tasks into a
stale review and vice versa. The four kind names (design, schema, production,
meta) are also **reserved label strings** — attaching one of them as a label is
refused, because the `kind` property already encodes that distinction.

## The verb taxonomy — when to use which

Tackit (v0.5) has **four** verbs that change a task's state. They are not
interchangeable and the mechanism enforces the distinctions via partition-
aware refusals. (v0.3's `supersede` verb was retired in v0.4 — it required
tasks to be atomic enough that "premise replaced" applied to the whole bundle,
which broke down in practice when only one of several facets was invalidated.
The v0.4+ model: edits are allowed on any status, and an append-only audit
table preserves prior verbatim state.)

| Verb                      | When                                       | Partition    | Cascade? | Reversible?      |
|---------------------------|--------------------------------------------|--------------|----------|------------------|
| `edit`                    | content change on any task (full-body)     | any          | **yes**  | n/a              |
| `edit_append`             | T179: append `content` to description      | any          | **yes**  | n/a              |
| `edit_replace_substring`  | T179: replace exact unique substring       | any          | **yes**  | n/a              |
| `close`                   | production/meta work shipped               | open/etc.    | no       | `reopen`         |
| `wont_do`                 | production/meta work dropped               | open/etc.    | no       | terminal forever |
| `retire`                  | design/schema spec 100% abandoned          | spec/retired | no       | terminal forever |

The three edit verbs (`edit`, `edit_append`, `edit_replace_substring`) are
**operationally equivalent** — same cascade, same audit row, same D31
reminder, same delta requirement. They differ only in how the new
description is computed:

- `edit` takes the full new `description` (use for sweeping rewrites or
  when small-diff doesn't help — small bodies, or when you've already
  composed the new prose in your head).
- `edit_append(id, content, delta)` — append `content` to the description.
  Diff-shaped: only `content` crosses the wire. Use for "Phase N finding"
  blocks on umbrella tasks, addenda, deltas.
- `edit_replace_substring(id, old_string, new_string, delta)` — replace
  the exact substring `old_string` with `new_string`. Match must be
  UNIQUE (non-unique refused loudly with the count; caller adds
  surrounding context to disambiguate). Empty `new_string` is a deletion.
  Use for typo fixes, phrase corrections, single-section rewrites without
  retransmitting the whole prose.

**When in doubt prefer the diff ops** — the cost difference compounds
over a long session. The v0.5 release ate ~60k tokens per fold-back
across 4 fold-backs because the diff ops weren't yet shipped; ship-on-pain
is the rule that catches this.

**The all-or-nothing rule** (D36, front and center): is the spec 100% gone,
with no replacement? → `retire`. *Any* partial change — including big
rewrites — uses `edit`. Edit's cascade IS the partial-change re-evaluation
mechanism; retire's "no cascade + open-neighbor refusal + immutable reason"
embodies the 100%-gone contract. If you find yourself wanting to migrate some
links to a new spec, you weren't retiring — you were editing.

### edit — change a task's content
Use when a task's content needs to change: typo fix, clearer wording, added
detail, corrected pointer, premise refinement. **Allowed on any status** —
open, closed, and wont_do (D29 v0.4 retires the v0.3 "no-edit-closed" rule).
The wont_do `reason` field is frozen at wont_do() time and has no edit API.

`edit` fires the staling cascade on directly linked neighbors. Required
`delta` (one sentence) describes the semantic shift so reconcilers can
FAST-filter via `delta × because`. The prior name+description+delta are
recorded in the description_revisions audit table (S7) so archaeology can
recover what the task used to say — editing in place no longer destroys
history.

If the edited task has kind in {design, schema}, the response envelope
includes a **code-check reminder** (D31) naming the slice id+name and
prompting a code drift check. The slice's D#/S# id is referenced in code by
the code↔task naming convention; grep it and verify the associated files.

### close — work done
Use when the task's deliverable has shipped. Refused if the task is stale or
shares a link with any obligation-bearing stale task (close-gate; bounded
by D28 — closed/wont_do production neighbors and retired design/schema
neighbors carrying stale=1 are record-only and do NOT trip the gate).
**Refused on `status='spec'`** (D30 + D36) — those are living specifications,
not work items; `edit()` to refine a decision, or `retire()` if 100%
abandoned. **Refused on retired** (D36 — no double-decide). On close, the
obligation payload returns one-hop neighbors so you can review whether
anything needs follow-up.

### wont_do — decided not to do
Use when the scope is dropped, not delivered. **Distinct from close** — close
means "we did this," wont_do means "we decided not to do this." Takes a
durable `reason` (persists forever in the row, no edit path) plus the
standard `delta`. **Refused on `status='spec'`** (D30 + D36) — a design
decision can't be "not done"; it either holds or is edited to reflect a
changed state. **Refused on already-closed/already-wont_do/retired tasks**
(T132 generalized: no double-decide). The change-of-mind path on a wont_do
task is to create a fresh task with the new direction — the old wont_do row
stays as historical record. wont_do does NOT fire the staling cascade
(status change, not content edit). Edit IS allowed on wont_do tasks — only
the `reason` field is frozen.

### retire — design/schema spec 100% abandoned
Use ONLY when a design or schema slice's premise is 100% gone with **no
replacement**. Partial changes — including big rewrites — use `edit()` and
let the cascade prompt link review. The all-or-nothing contract is enforced
by retire's refusal matrix; the partition guarantees only design/schema
slices ever reach this verb.

Takes a durable `reason` (persists forever in `wont_do_reason`; the column
is reused per partition — wont_do reason on production/meta, retire reason
on design/schema). Placeholder reasons refused (D33 extension):
empty / whitespace-only / `TBD` / `TODO` / `obsolete` / `no longer needed`.

**Refusal matrix (fail-fast, 6 checks):**
1. reason validation (non-empty + non-placeholder).
2. `status='spec'` — only living specs can be retired.
3. `kind ∈ {design, schema}` (redundant under partition; explicit for error
   clarity).
4. Stale gate — refused if the target is stale.
5. Linked-stale gate — refused if any obligation-bearing stale task sits
   in the transitive linked neighborhood.
6. **Open-neighbor gate** — refused if ANY linked neighbor has
   `status='open'`. The refusal lists each open neighbor with its
   `because` rationale and presents the (i)/(ii) decision tree (link_rm +
   wont_do vs link_rm alone) inline.

retire does NOT fire the staling cascade (status change, not content edit;
symmetric with close and wont_do). Terminal forever — reopen / close /
wont_do / retire all refused on a retired row. `edit()` is still allowed
on retired rows (D29 audit-table backstop); D31 code-check reminder fires
("verify no lingering code references this dead decision"). `link_add` is
refused if either endpoint has `status='retired'`.

If the retired decision later returns, file a fresh D# with the new
direction; do not reanimate the retired row.

### The decision tree

| Situation | Right verb |
|-----------|------------|
| Task's content needs to change (any status) | `edit` |
| Production/meta deliverable has shipped | `close` |
| Production/meta scope being dropped | `wont_do` |
| Design/schema spec 100% gone, no replacement | `retire` |
| Design/schema spec partially changed | `edit` (and let cascade prompt link review) |
| A premise has been replaced and the new direction is too different to re-use | **create a new task**; leave the old one as historical record. Link if coupling matters. |

## Retiring a spec — workflow walkthrough

`retire()` is uncommon. It is the right verb only when a decision is truly
dead with nowhere to go; most "spec change" scenarios are partial and use
`edit()`. The walkthrough below names when retire applies, how to clear
the open-neighbor gate, and what happens after.

**When `retire` is the right verb:**
- The decision is fully abandoned.
- There is no replacement spec the impl can move to.
- Links from production tasks cannot be migrated meaningfully — either the
  production work itself is also dead (`wont_do`) or it links to other
  living specs and can simply drop the edge.
- If *any* partial replacement exists — even "we're keeping half the rule" —
  the right verb is `edit`. Edit's cascade IS the partial-change mechanism.

**The open-neighbor gate** (D36 step 6 in the refusal matrix above) is what
forces this discipline. retire is refused if any linked neighbor has
`status='open'`; the refusal lists each open neighbor + its `because` and
presents the (i)/(ii) decision tree inline. Resolve each before retrying:

- **(i) the neighbor's work realizes ONLY this dying decision:**
  `link_rm` the edge, then `wont_do(neighbor, reason=…)` — the work is
  dead too. Now the retire can land.
- **(ii) the neighbor's work has other living-spec dependencies:**
  `link_rm` the edge only. The work continues under its remaining live
  premises. Then retry retire.

**The fresh-D# rule** (T132 generalized — no double-decide on a terminal
state): if the retired decision later returns, file a new design slice with
the new direction. Do NOT reanimate the retired row. The new slice can
reference the retired one's id in its prose if the historical coupling
matters; `link_add` to a retired endpoint is refused (see D36).

**Post-retire link behavior:**
- Existing edges to the retired row stay as historical archaeology.
  `show(retired_id)` still lists them.
- New `link_add` is refused (D36).
- `edit()` on retired is allowed (D29 backstop). The audit table records
  the prose change; D31's code-check reminder fires; cascade depth-1
  flags neighbors stale (record-only on terminal-status neighbors).

**Worked example — D25 retired in v0.4:** the supersede mechanism was
fully replaced by edit + description_revisions (S7); no partial migration
possible (every supersede link's intent collapsed into "edit the row"
under the new mechanism). The retire call:

```text
retire(D25,
       reason="supersede mechanism retired v0.4 — replaced by edit + S7 "
              "description_revisions audit table; no partial migration "
              "possible; see mig_006 + the D29 design slice",
       delta="retiring D25 — supersede gone per v0.4 D29")
```

The obligation payload returned one-hop linked neighbors (the production
tasks that had implemented supersede); each got reviewed: most were
already-closed under v0.4 and stayed put (record-only stale acceptable);
a couple linked to the D29 audit-table slice via fresh `link_add` since
that was the live replacement.

## The reconciliation discipline (the core of using tackit well)
tackit tracks not just tasks but whether they're still in sync after changes.
**The app checks for stale tasks itself, on every single call, and puts the
outstanding worklist in front of you** — this is not a reminder you can opt
out of; it is the tool telling you the plan is currently inconsistent. When
you see a stale alert, act on it.

- **Cascade-firing ops are edit and reclassify.** Each marks the directly
  linked neighbors stale + records the agent's `delta` for the cascade-
  ergonomics filter. Other status verbs (close, wont_do, reopen, reconcile)
  do NOT cascade. Link ops (link_add, link_rm) don't cascade either — they're
  structural.
- **Cascade is symmetric.** Every link is bidirectional in the cascade sense
  — editing either endpoint stales the other. There is no "depends_on
  direction" to fire the cascade only one way.
- **Bounded obligation (D28 + D36 v0.5): terminal-status tasks
  (closed / wont_do / retired) CAN be stale, but the flag is RECORD ONLY.**
  The cascade still writes stale=1 on them mechanically (depth-1, unchanged),
  but they're NOT on the worklist and they do NOT pressure the close-gate.
  The flag stays as historical signal that an upstream changed; archaeology
  can see it in `show()`. The worklist filter is now status-derived:
  **`status IN ('open', 'spec')`** — open production/meta and spec design/
  schema rows carry obligation; everything terminal is record-only.
  `reconcile()` is refused on `status IN ('closed', 'wont_do', 'retired')`
  (clearing a record-only marker would erase the signal without meaning);
  allowed on `status IN ('open', 'spec')` — what surfaces on the worklist
  is exactly what can be cleared. Reconciling a stale spec slice
  acknowledges its prose still describes truth after the upstream changed.
- **Use delta × because to FAST-filter.** Every link carries a durable
  `because` (set at link_add time) describing why the two tasks are coupled.
  Every cascade-firing op carries a `delta` describing the semantic shift.
  When reconciling, read the link's `because` alongside the upstream's
  `delta` — if the shift doesn't intersect the coupling, the stale flag is a
  false positive and you can `reconcile` without opening the downstream task.
  This is the cascade-ergonomics filter; it depends entirely on rationale
  quality.
- **Work the obligation-bearing stale set to empty.** The pass is done only
  when the worklist (`stale`) — already filtered by D28 — is empty. Drift
  propagates only where real changes happen.
- **Never treat work as done — never end your turn — while any
  obligation-bearing stale task remains.** A task left closed while something
  it depends on changed is the single worst outcome this tool exists to
  prevent: it is **wrong, and it is invisible**, so it silently corrupts
  everything downstream and no one discovers it until much later, at far
  greater cost. An empty filtered stale list is the only safe stopping point.
  (Closed-stale production/meta tasks DON'T need attention — they're
  record-only.)
- **The close-gate is bounded too.** `close` and `wont_do` are refused if the
  task is itself obligation-bearing-stale, *or* if it transitively shares a
  link with one. Closed-stale production neighbors don't trip the gate
  anymore. When the gate refuses, reconcile the named upstream first; if the
  refusal names a closed/wont_do task that's record-only, you can ignore it
  (the gate wouldn't have refused on its account).

### Edit on terminal-status rows — safe (D29 backstop)
v0.3's "no-edit-closed" rule is retired (D29 v0.4). Editing a closed,
wont_do, or **retired** task is a normal edit: it fires the cascade depth-1
(terminal-status neighbors flagged record-only per D28+D36) and records a
row in the description_revisions audit table (S7) preserving the prior
verbatim name+description+delta. Use this for prose fixes on shipped or
abandoned work — fixing a misleading description on a closed task no longer
destroys history; the audit table is the backstop. The `wont_do_reason`
field (which holds both wont_do and retire reasons per partition) is the
only column frozen post-write.

## Discovering dependencies — use the `links` op, don't just search
Search is recall-limited. For wiring a new production task to the design/schema
slices it realizes, use the deterministic `links` op:

1. **Classify the new task's `kind`** per the classifier rule above.
2. **For production tasks**: call `links` with no input → returns the anchor
   layer (all design + schema tasks). Judge each surfaced candidate — never
   skip one, but also don't force a link if the relationship isn't real.
3. **Expand one hop**: call `links(anchors_you_picked)` → returns the depth-1
   neighborhood of those anchors, **filtered to viable link targets**
   (`status IN ('open', 'spec')` — D27/D28/D36 v0.5). Closed/wont_do
   production neighbors and retired design/schema neighbors are not
   surfaced — they're not viable anchors for new realization links. Spec
   design/schema slices stay visible — that's the live spec layer.
   `link_add` to a retired endpoint is refused (D36). Iterate the same
   judge-or-skip pass.
4. **Stop when satisfied**, then `link_add` each chosen edge with a real
   `because` rationale describing the coupling.

For meta tasks: scan within the meta island only (meta-island constraint
forbids cross-kind links). For design/schema: scan within the same layer.

**Why this replaces "search before create":** keyword search misses coupling
that doesn't share vocabulary. The `links` op surfaces candidates
deterministically; the agent does the semantic judgment. Search is still
useful for finding tasks by name; it's just no longer the right primitive for
dependency discovery.

## Code ↔ task traceability — MANDATORY, not a nicety
This is the most important convention here and the easiest to under-take seriously.
**The link between the code you write and the task it implements is the only thing
that lets anyone — including a future you, in a fresh session with zero memory —
recover *why* a piece of code exists.** tackit holds the intent; the code is the
implementation; if the two can't be connected, the intent is lost the instant your
context window resets. There is **no automatic link** — tackit can't see your code,
and your code doesn't know its task. The bridge is *nothing but how you write both*.

- **Every task must be findable by keyword.** Tasks are located via full-text
  `search`. A task called "fix bug" or "update logic" is effectively invisible — it
  surfaces for no reasonable search, so its dependents and history become
  unrecoverable. Name and describe tasks with **specific, distinctive terms** — the
  component, function, table, or feature ("rotate JWT signing keys on the auth token
  endpoint") — never vague verbs.
- **Mirror that exact vocabulary in the code.** File names, function names, and
  comments should echo the task's distinctive terms, so reading the code and reading
  the task line up and a search for one finds the other. If the task says "token
  rotation," the code says "token rotation" — not "key cycling."
- **Keep both sides honest.** When code and task disagree, the task is the source
  of truth — edit it. Never let the code silently diverge. The strong version of
  this rule lives below in **Fold-backs**: implementation-time discoveries are
  the highest-value signal you'll get; they have a discipline of their own.
  Edits on design/schema slices fire the D31 code-check reminder to prompt a
  grep of the slice id — use it.

Treat a vague task title or a code↔task vocabulary mismatch as a **defect**, not a
style nit. The system's ability to recover intent across context resets depends
entirely on this.

## Fold-backs — implementation discoveries are higher signal than any plan

This is the most under-practiced and highest-leverage rule in this skill. Read it
twice.

**The premise.** No matter how thoroughly a task body is planned, implementation
reveals things planning cannot: the call site nobody listed, the partition CHECK
nobody simulated, the message-bank wording that reads wrong only after agents see
it, the sibling code path with no self-explaining name. These discoveries are not
noise — they are **the most valuable feedback the work produces**. Planning
predicts; implementation tests the prediction. Every gap is signal about what the
spec missed and what to look for next time.

**The rule (mandatory, not "if you remember").** When a commit fixes a bug or
changes behavior that the responsible task body does not describe — that is a
**scope gap**. The fix and the fold-back ship together. Never one without the
other. "I caught it in the commit message" is not a fold-back; commit messages
are not searchable from the task graph, and the next agent reading the task body
will not see them. The task body is what survives.

**What "responsible task body" means.** Trace backwards: which task's "what will
change" / "files" / "call sites" enumeration *should* have included this code
path? That task gets edited — usually with a brief appended "Phase N finding"
section naming the missed call site, the symptom, the root cause, the fix, and
why the original enumeration missed it. If the discovery invalidates the design
itself, edit the D# slice; if it just exposes an under-enumeration, edit the
impl task.

**Per-discovery format (append to the responsible task body):**

> ### Phase N finding — \<short label\>
>
> **Symptom:** \<the failing test / refusal / error / behavior, named concretely\>
>
> **Root cause:** \<the missed line / call site / predicate / assumption,
> citing file:line where useful\>
>
> **Fix:** \<one or two sentences naming what the commit changed\>
>
> **Why missed:** \<the enumeration discipline that failed — usually
> "enumerated by named verb / refusal, missed the sibling code path that
> doesn't have a self-explaining name"\>
>
> **Pinning test:** \<the test that catches a future regression\>
>
> **Status:** Fixed in commit \<hash\>; the task body's pre-existing plan is
> otherwise current. This finding records the scope gap; it does not invalidate
> the work that shipped.

**The discovery → enumeration meta-lesson.** Most fold-backs trace back to the
same root: the original task enumerated by *named verb* ("close", "wont_do",
"reconcile") and missed *sibling code paths* ("`reopen()`'s no-op guard",
"`load()`'s INSERT") that touch the same primitive without a self-explaining
name. The discipline that catches this: at the end of an enumeration sweep,
**grep for the pattern family** (`status =`, `INSERT.*status`, `WHERE status`,
the SQL or Python construct the rule is about), not for the verb names. Every
match either (a) belongs in the enumerated set or (b) is provably unaffected.
Apply this on every sweep.

**Mandatory end-of-turn fold-back report.** Every turn that produces a code
commit or a behavior change MUST include a fold-back line in the end-of-turn
summary — even if the answer is *none*. The negative case is what makes the
positive case credible:

> **Fold-backs this turn:**
> - **T173** — appended Phase 4 finding for the `reopen()` call site missed in Phase 2a's 6-call-site enumeration.
> - **T168** — appended Phase 4 finding for the `core.load()` row-creating path missed in Phase 1's default-by-kind enumeration.

Or, when no fold-back applied:

> **Fold-backs this turn:** none — verified the commits don't reveal a scope gap
> in any task body (no missed call sites; the changes match what the responsible
> task body described).

A turn whose end-of-turn summary omits this line is incomplete. Treat the
omission as a refusal-message-shaped defect — fix before declaring done.

**Why this is worth the friction.** The two bugs found in v0.5 Phase 4
(`reopen()` partition CHECK violation, `load()` hardcoded status) were exactly
the kind of discovery no upfront plan could have produced — they emerged from
the property-test machine probing combinations the human plan didn't enumerate.
The commit messages captured the bugs; nothing captured the *scope gap in the
planning task*. Without fold-back, that signal evaporates when the session ends
and the next agent re-enumerates from the same incomplete planning lens. With
fold-back, the next agent reads "Phase 4 finding: enumerated by verb, missed
sibling pattern — grep the family next time" and skips the same mistake. The
fold-back is how implementation teaches planning.

### Auto-id name prefix (D32, v0.4)
Every task carries a deterministic `<kind_letter><id>` prefix in its agent-facing
display. The letters: design→**D**, schema→**S**, production→**T**, meta→**M**;
the number is the task row id. The prefix is **synthesized by tackit** from
`kind + id` — you pass a bare name to `add()` / `edit()` ("Fix the ls() status
filter"), and every display surface emits `T157 — Fix the ls() status filter`.
The FTS index also stores the synthesized prefix, so `search("T157")` /
`search("D23")` resolves to the right row even when the user-supplied name has
no such substring.

- **Don't manually prefix.** Write the bare name; tackit adds the prefix. A
  manually-prefixed name like `"T157 — Fix..."` will double-prefix at display.
- **Reference tasks in code/conversation by the synthesized prefix.** Code
  comments saying `# T157: validates the wont_do status` or PR descriptions
  saying "fixes T157" are the cross-references the search and code↔task
  vocabulary depend on. Grep for "T157" in the codebase to find every site
  that mentions the task; grep that exact string in tackit search to confirm
  the task is what you think it is.
- **Legacy D#/S# names are grandfathered** (T160 / 2026-06-01): pre-D32
  design/schema tasks keep their manually-assigned slot prefix as part of the
  stored `name` field (e.g., T133's `name` is `"D7 — Status + stale flag"`).
  Their synthesized display becomes the doubled `D133 — D7 — Status + stale
  flag` — verbose but unambiguous; the cost of not retro-renaming the spec
  references in code, SKILL.md, etc.

## Working effectively
- **Wire links explicitly — including among tasks you add together.** A task
  building on another must declare the edge with a real `because` rationale —
  the rationale powers the cascade-ergonomics filter; placeholder rationales
  degrade the cascade to "open every downstream task." Two cases to handle, not one:
  - *Edges to existing tasks:* use `links` (or `search` if you know the
    vocabulary) for the prerequisites and wire an edge to each with a specific
    `because`.
  - *Edges within a batch:* when you add several tasks at once, the
    dependencies are frequently **among the new tasks themselves** — wire that
    internal DAG too. This is the case most easily forgotten.
- **Right-size tasks.** A task is a describable unit of work — a black-box feature —
  not one line of code, not a whole subsystem. If you can't describe it without
  listing implementation steps, it's too small; if it has many independent parts,
  split it. **The concrete signal:** if you reach for a second `###` heading to
  organize independent execution units in the body, the task has more than one
  logical unit — file separate tasks rather than separate sections. Nested detail
  within ONE unit is fine; what's forbidden is co-equal sections describing
  distinct units. (Anchor: T168 grew to 54k chars under one umbrella body before
  splitting into 8 phase tasks — every fold-back paid full-body retransmit cost
  the split would have avoided. T179's diff-edit ops mitigate but don't replace
  splitting at logical boundaries — the cascade-granularity and granular-
  description-discipline reasons remain.)
- **Write real `because` rationales.** When `link_add` requires `because`, describe
  the **coupling** between the two tasks — not the implementation, not the order
  of work. The cascade compares `because × delta` to filter stale neighbors; vague
  becauses ("test fixture", "setup") don't filter anything. Aim for one sentence
  naming *why these two tasks must be reviewed together when one changes*.

## Labels — when one earns its existence
A label groups tasks along a meaningful project axis — the kind of grouping you'd name
out loud: a **phase/milestone** (design, hardening, 0.3 release), an **epic/theme** (a
large body of work spawned by one question — "the auth overhaul," "bulk-import"), or a
**use case** (offline mode, the mobile client). These axes are illustrative, not a fixed
list. Labels are dumb tags — no behavior, no rules.

Create a new label only when it would be **distinct, sizeable, consequential, and
memorable** — a chunk of work you'd refer to *by name*. Do **not** create a label that is:
- an implementation detail of one task (`regex`, `utf8`, `ascii-text`);
- already something tackit tracks — time is `created_at`, done-ness is `status`, reopened
  is in `history` (a "closed-but-reopened" label just duplicates state that exists);
- a one-or-two-task one-off that will never group more;
- one of the four reserved `kind` names (`design`, `schema`, `production`, `meta`) —
  the kind property already encodes that distinction; label_add refuses these strings.

Before creating, run **`labels`** to see what exists and what each means (by its tasks);
**reuse** an existing label if it fits, and prefer a few broad labels over many narrow
ones.

After a **bulk `load`**, it reports the new labels it created — review them and collapse
near-duplicates in **one pass** (a migration is when sprawl floods in, and `load` is the
one path the per-creation nudge doesn't catch).

**The epic pattern:** when an under-defined question explodes into many tasks, don't write
a new doc — make the question itself a task, give the whole cluster one label, and wire
the spawned tasks to link to that anchor. The theme is grouped (label) and anchored
(link), and can't drift.

## The propagation principle — discipline lives on every agent-facing surface

A discipline rule worth enforcing teaches the agent at every moment the rule
matters. That means restating it across SKILL.md, MCP tool docstrings, CLI
help text, refusal envelopes, and the README. Each surface catches a different
moment in the agent's work:

- **SKILL.md** teaches at session start, when the agent loads context for the
  whole project.
- **MCP tool docstrings** teach at invocation moment, when the agent reads
  the tool description before calling it.
- **CLI help text** teaches at typed-command moment for the human user.
- **Refusal messages** teach at misuse moment, where the cost of skipping
  the rule was about to be paid.
- **README** teaches at install moment, for the new project / new agent.

Multi-surface restatement is **intentional, not redundant**. Each surface
reaches a different moment; an agent that skips one still gets caught by
another.

**Prior example (D33):** the placeholder-rationale refusal fires in `link_add`,
`add(deps=…)`, and `load()` because those are all the surfaces a `because`
rationale is set on. **Current example (D36):** the edit-vs-retire discipline
appears in retire's MCP docstring, edit's MCP docstring ("Use edit for ALL
partial changes"), close/wont_do refusal messages ("use edit() to refine;
retire() if 100% abandoned"), `link_add` refusal on retired endpoints,
`reconcile` refusal on retired, reclassify cross-partition refusals.

**When designing or modifying a discipline rule, ask: which surfaces does an
agent touch when this rule matters? The rule lives in all of them.** A rule
that lives only in SKILL.md but not in the tool docstring will be skipped by
agents who jump straight to the tool; a rule that lives only in the refusal
message but not in the docstring teaches by punishment, not prevention.

## The granular-description discipline (D37 — FORCEFUL, not optional)

**This section is FORCEFUL.** Read it as a contract, not a suggestion.

Task descriptions must be **implementation-ready**. On a new-session revisit,
an agent reading the description should not be confused, should not feel
the need to do additional scoping work, and should not encounter ambiguity
that could have been resolved. Under-defined descriptions force fresh-session
agents to re-derive context that should already be on the row — the exact
failure tackit exists to prevent.

**The rule:**

> A task's description must contain enough granular detail that a
> fresh-session agent — with no conversation history, no prior context,
> only the task body and its linked neighbors — can implement (or evaluate
> completion of) the task without asking the user for clarification.

**Per-kind expectations:**

- **design / schema slices**: fully specify the decision, constraints,
  implications, refusal patterns. A fresh-session agent should be able to
  edit code to align with it and verify drift via D31's code-check reminder.
- **production tasks**: describe the code change in execution-grade detail —
  files, call sites, signature snippets, predicate tables, error-message
  banks, SQL recipes, test coverage. A fresh-session agent should be able to
  sit down and write the change directly.
- **meta tasks**: describe what bookkeeping is being captured (release,
  experiment, observation) with enough context to interpret the result.

**When the discipline applies:**

- **At `add()`**: aim for full granularity from the start. add()'s docstring
  prompts you toward impl-ready granularity. Richer body now beats
  remembering it later.
- **During implementation**: if you discover an under-defined detail — an
  edge case the spec missed, a refusal-message wording question, an extra
  file affected — `edit()` is the mechanism to fold the discovery back into
  the description **BEFORE close**. Discovery is normal and expected; loss
  is forbidden. (This is the same fold-back discipline as the Fold-backs
  section above, applied at the description-granularity layer: edit the
  task body to absorb what implementation taught you.)
- **Before close**: re-read the description against what was actually
  implemented. If the description no longer captures the impl, `edit()` it
  before close. Closing with an out-of-date description destroys
  granularity for future readers; S7 audit is the safety net, not the
  primary mechanism.

**Anti-patterns this discipline forbids:**

- **Vague verbs:** "Fix bug" / "update logic" / "clean up X" — unsearchable,
  unimplementable.
- **Conversation references:** "Add the feature discussed in conversation"
  / "see chat history" / "as agreed" — references ephemeral context that
  doesn't survive a session reset.
- **Pointer-only bodies:** "See related task X for details" without
  inlining the scope — forces traversal that loses on fresh-session.
- **TBD / TODO placeholders in committed task bodies** — flag and resolve
  before commit. If a detail genuinely isn't decided, the task isn't ready
  to be tracked yet.
- **Implementation-by-conversation:** agreeing on detail in conversation
  but never folding it into the task body. Conversation is ephemeral; the
  task is durable. If a detail surfaced in conversation that's not yet on
  the task, `edit()` it before the context expires.

**This discipline propagates** (per the Propagation Principle above): SKILL.md
(this section), `add()` MCP docstring + CLI help ("impl-ready granularity at
create time"), `edit()` MCP docstring + CLI help ("Use edit for ALL partial
changes" / "fold them back BEFORE close"), README writing-tasks walkthrough.

## Ship-on-pain — don't endure friction you can fix (THE rule the others serve)

**This is the most load-bearing discipline rule here. Read it twice.**

If you're feeling friction right now from a deferred fix's absence — a
workaround eating tokens (large-body edit retransmits, "drop into Python"
bypass of MCP, repeated session restarts, manual step on the Nth iteration)
— **the workaround itself is the forcing function**. The next task is
already chosen for you: stop, ship the fix, then continue with the
friction gone.

**This rule OVERRIDES "finish the current bundle first."** Deferring the
fix means paying its avoided cost on every remaining unit of the current
work, and that compounding cost almost always exceeds the cost of pausing
to ship. The opposite of ship-on-pain is *"I'll deal with this in v(N+1)"*
dressed up as planning discipline — that is exactly the failure mode this
section exists to prevent.

**Why this is THE rule the other discipline rules serve.** Fold-backs, the
granular-description discipline, the propagation principle — all assume
ship-on-pain is operating. Without it the others are intellectualized:
patterns you can describe at session start but don't act on under release
pressure. A "discipline" that bends every time friction would force a hard
call isn't a discipline; it's a slogan.

### The anchoring incident (v0.5 / T179)

T179 (diff-edit ops `edit_append` + `edit_replace_substring`, cuts
large-body edit cost ~10×) was filed during v0.5 brainstorm and labeled
"Standalone — NOT part of the v0.5 bundle" in the first line of its body.
That phrase was load-bearing: once classed "not in this bundle" it stayed
background-deferred regardless of leverage. It sat at `status='open'`
through 8 release phases. Phases 4 / 6 / 7 / 8 each shipped fold-back
findings against the *strengthened* fold-back rule that landed mid-v0.5
— which explicitly raised the per-fold-back cost by making the
end-of-turn report mandatory. Each fold-back round-tripped a ~57k-char
T168 body, ~60k tokens per fold-back, paid in full. **The exact commit
that raised the per-fold-back cost was also the moment to recognize
T179's leverage had doubled and ship it.** Instead the higher cost was
paid four more times. T179 shipped in ~30 minutes the moment the user
explicitly said "this is a huge one, shouldn't have been deferred." The
fix had been sitting at `status='open'` the entire time.

### How to apply

1. **Mid-build pause test.** Ask: am I executing a workaround whose cost
   per occurrence × remaining occurrences > "stop and ship the fix"
   cost? If yes, the next task is the fix — the workaround itself is the
   forcing function. Don't keep absorbing the friction; the math is
   already against you.

2. **"Standalone / NOT part of this bundle" is a smell, not a category.**
   Once you write that phrase in a task body you've classed it as
   background-deferred. Either fold it into current work with an
   explicit reason for *why now*, or write the explicit "defer because X"
   reason in the body — never the passive "standalone" framing.

3. **Backlog filing IS triage.** Every backlog filing decision is *also*
   a "ship-this-release? yes/no with reason" decision, made **at filing
   time**. Default-deferred items don't get triaged at end-of-release;
   they become the v(N+1) honorable-mention list and silently grow. A
   backlog row filed without a triage decision is a graveyard plot.

4. **Bundles can grow** if the addition is higher-leverage than the slip
   cost. The released-bundle scope is not a fixed contract — "adding
   scope mid-release" is only bad when the addition is lower-leverage
   than continuing. Otherwise it's the *cheapest* way to ship the
   remainder. v0.5 + T179 would have shipped as v0.5; nothing about the
   version number required deferring T179.

5. **Watch for cost-raising signals on previously-deferred fixes.** When
   you tighten a discipline rule that uses a deferred mechanism (e.g.
   strengthening fold-backs while diff-edit ops sat unshipped), the
   leverage of the deferred fix just rose. That moment is the
   second-best time to promote the deferred task to active. The best
   time was before the rule change; second-best is now.

6. **Mandatory friction check on multi-phase releases.** Before
   declaring the next phase done, ask: "is anything I'm doing right now
   a workaround for something at `status='open'` in tackit?" If yes,
   evaluate per (1). The release-cluster pattern hides this — finishing
   the bundle feels productive even when the bundle is paying
   compounding cost to skip a fix.

**This discipline propagates** (per the Propagation Principle above):
SKILL.md (this section), README "for-agents" discipline block, global
agent guidance (`~/.claude/CLAUDE.md`), and lived in the v0.5 / T179
fold-back as the anchoring incident. The rule is general; the surfaces
catch the agent at every moment friction could be silently absorbed.

### See also: T179 (the diff-edit ops the incident produced)

`edit_append(id, content, delta)` and `edit_replace_substring(id, old, new, delta)`
are the diff-shaped descendants of `edit()` that landed from the T179
incident. Use them whenever you'd otherwise round-trip a large body:
appending a "Phase N finding" block to an umbrella task, fixing a
specific phrase in a slice without retransmitting the whole prose,
correcting a typo in a 50k-char description. Both fire the cascade
depth-1 and write the description_revisions audit row exactly like
`edit()`; the diff is in how the new description is computed, not in
what happens after. Refusal matrix: `edit_append` refuses empty /
whitespace-only content; `edit_replace_substring` refuses empty
`old_string`, not-found, or non-unique match (with count) — empty
`new_string` is allowed as a deletion; `old == new` is a no-op. Both
take the standard required `delta`.

## Always report what changed
After you create, alter, or remove tasks, report it back — but make it **scannable**, not a
wall of prose. Use a sectioned, verb-grouped layout, and for each task give just enough to
**(a) recall what it is and (b) roughly grasp what you did** — no step-by-step play-by-play.
Format:

    ━━━ changes ━━━━━━━━━━━━━━━━━━━━━━━━
    ✓ Closed
       T30 · label-discipline guidance
            what: when a new label is worth creating (anti-sprawl)
            did:  wrote the rules into SKILL.md
    + Added
       T33 · <task name> (kind=production)
            what: <one line — enough to recall the task>
            did:  <one line — roughly what happened>
    + Linked
       T127 ↔ T122
            because: "T127 measurement needs the rationales T122 backfills"
    ✗ Wont_do
       T130 · backfill primitives
            what: productized link_rationale + batch op
            reason: scope dropped per user — case-by-case backfill instead
    ━━━ state ━━━━━━━━━━━━━━━━━━━━━━━━━━
       N open · N done · ⚠ N stale (open + design/schema only — D28)
       <worry first: stale ids + what they await, refused ops, new labels>

- Group by verb: Added / Edited / Closed / Reopened / Reconciled / Linked /
  Tagged / Wont_do / Reclassified / Retired.
- Per task: one short line each for `what:` (the task — enough to recall it) and
  `did:` (the change — roughly, not blow-by-blow). A clause each, not a paragraph.
- For Wont_do / Retired entries name the durable reason; for Linked entries name
  the because rationale.
- **End with the fold-back line** (see the Fold-backs section above) — every turn
  that produces a code commit or behavior change names which task bodies were
  edited to absorb implementation discoveries, OR states explicitly that none
  applied. Silence on fold-backs is not reassurance.
- End with the state line; surface anything worrying first. If nothing is stale and
  nothing was refused, say so — silence is not reassurance.
