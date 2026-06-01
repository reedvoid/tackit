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
Every task carries a required `kind` (set at create time, T94) from this closed
taxonomy:

- **design** — a decision slice (D# in the design doc). Captures what is decided,
  not how it is built. Editing a design changes what the system means; impl tasks
  link back to design tasks they realize. **Perma-open under v0.4 (D30):**
  `close()` and `wont_do()` are refused on design slices; updating a decision is
  edit(), not close. To retire a decision, edit the slice to reflect its current
  state — the description_revisions audit table (D29) preserves the prior
  verbatim version, so editing is recoverable.
- **schema** — a store-shape slice (S# in the schema doc). The DDL contract.
  Schema changes typically require migrations. **Same perma-open rule as design.**
- **production** — code that **alters the running app's behavior**. Source under
  `tackit/`, tests that pin behavior contracts, and the README/SKILL.md **count
  as production** because they alter the agent's behavior of the app.
- **meta** — work that does **not** alter the running app. Release bookkeeping,
  experiments, observation writeups, side-investigations, dogfood notes.

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
Tackit (v0.4) has three verbs that change a task's state. They are not
interchangeable and the mechanism enforces the distinctions. (v0.3's
`supersede` verb was retired — it required tasks to be atomic enough that
"premise replaced" applied to the whole bundle, which broke down in practice
when only one of several facets was invalidated. The simpler model: edits
are allowed on any status, and an append-only audit table preserves prior
verbatim state.)

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
shares a link with any obligation-bearing stale task (close-gate; v0.4
bounded by D28 — closed/wont_do production neighbors carrying stale=1 are
record-only and do NOT trip the gate). **Refused on kind in {design, schema}**
(D30) — those are living spec, not work items; edit() is the right verb. On
close, the obligation payload returns one-hop neighbors so you can review
whether anything needs follow-up.

### wont_do — decided not to do
Use when the scope is dropped, not delivered. **Distinct from close** — close
means "we did this," wont_do means "we decided not to do this." Takes a
durable `reason` (persists forever in the row, no edit path) plus the
standard `delta`. **Refused on kind in {design, schema}** (D30) — a design
decision can't be "not done"; it either holds or is edited to reflect a
changed state. **Refused on already-closed/already-wont_do tasks** (T132: no
double-decide). The change-of-mind path on a wont_do task is to create a
fresh task with the new direction — the old wont_do row stays as historical
record. wont_do does NOT fire the staling cascade (status change, not
content edit). Edit IS allowed on wont_do tasks under v0.4 — only the
`reason` field is frozen.

### The decision tree
Task's content needs to change (any status) → **edit**.
Task's deliverable has shipped → **close** (production/meta only;
design/schema are perma-open).
Task's scope is being dropped, we are not doing this → **wont_do**
(production/meta only).
A premise has been replaced and the new direction is too different to
re-use the task → **create a new task with the new direction**; leave the
old one as historical record. Link the two if the coupling matters.

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
- **Bounded obligation (D28 v0.4): closed and wont_do tasks CAN be stale,
  but the flag is RECORD ONLY.** The cascade still writes stale=1 on them
  mechanically (depth-1, unchanged), but they're NOT on the worklist and they
  do NOT pressure the close-gate. The flag stays as historical signal that
  an upstream changed; archaeology can see it in `show()`. The worklist
  filter: `status='open' OR kind in {design,schema}`. Closed-stale
  production/meta is acceptable and doesn't block anything; you don't have to
  reconcile it. `reconcile()` is refused on closed/wont_do tasks (clearing a
  record-only marker would erase the signal without meaning).
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

### Edit on closed/wont_do — safe under v0.4
v0.3's "no-edit-closed" rule is retired. Editing a closed or wont_do task is
now a normal edit: it fires the cascade depth-1 (closed/wont_do neighbors
flagged record-only per D28) and records a row in the description_revisions
audit table (S7) preserving the prior verbatim name+description+delta. Use
this for prose fixes on shipped work — fixing a misleading description on a
closed task no longer destroys history; the audit table is the backstop.

## Discovering dependencies — use the `links` op, don't just search
Search is recall-limited. For wiring a new production task to the design/schema
slices it realizes, use the deterministic `links` op:

1. **Classify the new task's `kind`** per the classifier rule above.
2. **For production tasks**: call `links` with no input → returns the anchor
   layer (all design + schema tasks). Judge each surfaced candidate — never
   skip one, but also don't force a link if the relationship isn't real.
3. **Expand one hop**: call `links(anchors_you_picked)` → returns the depth-1
   neighborhood of those anchors, **filtered to viable link targets**
   (status='open' OR kind in {design,schema} — D27/D28 v0.4). Closed/wont_do
   production neighbors are not surfaced; closed design/schema slices still
   are (living spec). Iterate the same judge-or-skip pass.
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
- **Keep both sides honest.** If coding reveals the task/design is wrong,
  edit the task (it's the source of truth); never let the code silently
  diverge. Edits on design/schema slices fire the D31 code-check reminder
  to prompt a grep of the slice id — use it.

Treat a vague task title or a code↔task vocabulary mismatch as a **defect**, not a
style nit. The system's ability to recover intent across context resets depends
entirely on this.

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
  split it.
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
  Tagged / Wont_do / Reclassified.
- Per task: one short line each for `what:` (the task — enough to recall it) and
  `did:` (the change — roughly, not blow-by-blow). A clause each, not a paragraph.
- For Wont_do entries name the durable reason; for Linked entries name the
  because rationale.
- End with the state line; surface anything worrying first. If nothing is stale and
  nothing was refused, say so — silence is not reassurance.
