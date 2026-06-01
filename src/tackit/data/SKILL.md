---
name: tackit
description: Use whenever planning, tracking, or executing work in a project that
  uses tackit (a task + dependency tracker). Covers when to record work in tackit,
  the reconcile-on-change discipline, the verb taxonomy (edit / supersede / close /
  wont_do), the kind classification rule, and the mandatory code↔task naming convention.
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
  link back to design tasks they realize.
- **schema** — a store-shape slice (S# in the schema doc). The DDL contract.
  Schema changes typically require migrations.
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
Tackit has four verbs that change a task's state. They are not interchangeable
and the mechanism enforces the distinctions.

### edit — sharpen an OPEN task
Use when a task's premise is unchanged but the description needs sharpening:
typo fix, clearer wording, added detail, corrected pointer. **Refused on closed
and wont_do tasks** — for those, supersede instead. `edit` fires the staling
cascade on linked neighbors. Required `delta` (one sentence) describes the
semantic shift so reconcilers can FAST-filter via `delta × because`.

### supersede — replace the premise with a new task
Use when a task's premise has been replaced or inverted. Create a new task
with the new direction; supersede the old with the new. The old task stays in
the graph as historical record; its `superseded_by` marker tags search hits so
the displaced premise can't mislead silently. Supersede fires the staling
cascade on **old's** linked neighbors — every link must be walked through the
migrate-or-stay decision (`link_add(by, neighbor)` to migrate; `link_rm(old,
neighbor)` if fully replaced; leave both if historical). Required `delta`.
Allowed on any status (open, closed, wont_do).

### close — work done
Use when the task's deliverable has shipped. Refused if the task is stale or
shares a link with any stale task (the close-gate). Closed tasks are immutable
(no edit; use supersede if drift surfaces). On close, the obligation payload
returns one-hop neighbors so you can review whether anything needs follow-up.

### wont_do — decided not to do
Use when the scope is dropped, not delivered. **Distinct from close** — close
means "we did this," wont_do means "we decided not to do this." Takes a
durable `reason` (persists forever in the row) plus the standard `delta`.
**Locked forever** — edit, reopen, close, and wont_do are all refused on a
wont_do task. The change-of-mind path is supersede with a new task carrying
the new direction. wont_do does NOT fire the staling cascade (status change,
not content edit).

### The decision tree
Task is in flight, content needs sharpening, premise unchanged → **edit**.
Task's premise has been replaced/inverted → **supersede** (with a new task
carrying the replacement).
Task's deliverable has shipped → **close**.
Task's scope is being dropped, we are not doing this → **wont_do**.
Closed task's prose is drifting → **supersede** (never reopen+edit+close, that
logs a misleading history transition).

## The reconciliation discipline (the core of using tackit well)
tackit tracks not just tasks but whether they're still in sync after changes. **The
app checks for stale tasks itself, on every single call, and puts the outstanding
list in front of you** — this is not a reminder you can opt out of; it is the tool
telling you the plan is currently inconsistent. When you see a stale alert, act on it.

- **Cascade-firing ops are edit, supersede, and reclassify.** Each marks the
  directly linked neighbors stale + records the agent's `delta` for the
  cascade-ergonomics filter. Other status verbs (close, wont_do, reopen,
  reconcile) do NOT cascade. Link ops (link_add, link_rm) don't cascade either
  — they're structural.
- **Cascade is symmetric.** Every link is bidirectional in the cascade sense —
  editing either endpoint stales the other. There is no "depends_on direction"
  to fire the cascade only one way.
- **Closed and wont_do tasks CAN be stale.** The cascade still marks them
  stale=True when an upstream changes, but it does NOT force-reopen them.
  Closed-stale and wont_do-stale mean "the upstream changed; review whether
  the historical record (or the dropped-scope rationale) still holds." The
  action menu on terminal-stale tasks: **reconcile** (still correct as
  record), **supersede** (premise replaced, create successor), or **link_rm
  + link_add** (relink edges to a replacement). `edit` is refused.
- **Use delta × because to FAST-filter.** Every link carries a durable `because`
  (set at link_add time) describing why the two tasks are coupled. Every
  cascade-firing op carries a `delta` describing the semantic shift. When
  reconciling, read the link's `because` alongside the upstream's `delta` — if
  the shift doesn't intersect the coupling, the stale flag is a false positive
  and you can `reconcile` without opening the downstream task. This is the
  cascade-ergonomics filter; it depends entirely on rationale quality.
- **Work the stale set to empty.** The pass is done only when the worklist
  (`stale`) is empty. Drift propagates only where real changes happen.
- **Never treat work as done — never end your turn — while any task is stale.**
  A task left closed while something it depends on changed is the single worst
  outcome this tool exists to prevent: it is **wrong, and it is invisible**, so
  it silently corrupts everything downstream and no one discovers it until much
  later, at far greater cost. An empty stale list is the only safe stopping point.
- **A stale task cannot be closed (or wont_do'd), and neither can anything in
  its linked neighborhood.** The tools enforce both: `close` and `wont_do` are
  refused if the task is stale, *or* if it transitively shares a link with any
  stale task. Don't fight the refusal — reconcile the named upstream first,
  then close (or wont_do).

## Discovering dependencies — use the `links` op, don't just search
Search is recall-limited. For wiring a new production task to the design/schema
slices it realizes, use the deterministic `links` op:

1. **Classify the new task's `kind`** per the classifier rule above.
2. **For production tasks**: call `links` with no input → returns the anchor
   layer (all design + schema tasks). Judge each surfaced candidate — never
   skip one, but also don't force a link if the relationship isn't real.
3. **Expand one hop**: call `links(anchors_you_picked)` → returns the depth-1
   neighborhood of those anchors. Iterate the same judge-or-skip pass.
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
- **Keep both sides honest.** If coding reveals the task/design is wrong, supersede
  the task (it's the source of truth); never let the code silently diverge.

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
    ~ Superseded
       T49 · old D7 prose
            what: D7 status+stale slice
            did:  replaced by T133 (relaxed-invariant version); old kept as history
    + Linked
       T127 ↔ T122
            because: "T127 measurement needs the rationales T122 backfills"
    ✗ Wont_do
       T130 · backfill primitives
            what: productized link_rationale + batch op
            reason: scope dropped per user — case-by-case backfill instead
    ━━━ state ━━━━━━━━━━━━━━━━━━━━━━━━━━
       N open · N done · ⚠ N stale
       <worry first: stale ids + what they await, refused ops, new labels>

- Group by verb: Added / Edited / Closed / Reopened / Reconciled / Linked / Tagged /
  Superseded / Wont_do / Reclassified.
- Per task: one short line each for `what:` (the task — enough to recall it) and
  `did:` (the change — roughly, not blow-by-blow). A clause each, not a paragraph.
- For Superseded entries name the successor task id; for Wont_do entries name the
  durable reason; for Linked entries name the because rationale.
- End with the state line; surface anything worrying first. If nothing is stale and
  nothing was refused, say so — silence is not reassurance.
