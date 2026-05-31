---
name: tackit
description: Use whenever planning, tracking, or executing work in a project that
  uses tackit (a task + dependency tracker). Covers when to record work in tackit,
  the reconcile-on-change discipline, and the mandatory code↔task naming convention.
---

# Working with tackit

tackit is this project's **single source of truth** for tasks, their dependencies,
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

## The reconciliation discipline (the core of using tackit well)
tackit tracks not just tasks but whether they're still in sync after changes. **The
app checks for stale tasks itself, on every single call, and puts the outstanding
list in front of you** — this is not a reminder you can opt out of; it is the tool
telling you the plan is currently inconsistent. When you see a stale alert, act on it.

- **Changing a task can invalidate everything that depends on it.** When you edit
  one, its dependents are marked **stale** = "may no longer be correct; review me."
- **To reconcile a stale task, look at it TOGETHER WITH the tasks it `depends_on`.**
  A task is stale precisely because one of its dependencies changed under it. So
  `show` it, read it against those `depends_on` neighbors, and decide: if it is now
  wrong, `edit` it (which re-stales *its* own dependents — the cascade flows on); if
  it is still correct, `reconcile` it. Looking at the stale task alone, without its
  dependencies, defeats the entire check — you cannot tell if it is still in sync
  without seeing what moved beneath it.
- **Work the stale set to empty.** The pass is done only when the worklist (`stale`)
  is empty. Drift propagates only where real changes happen.
- **Never treat work as done — never end your turn — while any task is stale.** A
  task left closed while something it depends on changed is the single worst outcome
  this tool exists to prevent: it is **wrong, and it is invisible**, so it silently
  corrupts everything downstream and no one discovers it until much later, at far
  greater cost. An empty stale list is the only safe stopping point.
- **A stale task cannot be closed, and neither can anything that depends on a stale
  task.** The tools enforce both: `close` is refused if the task is stale, *or* if it
  transitively depends on a stale task (closing it would mark work done on top of
  drift that may still change). Don't fight the refusal — reconcile the named
  upstream first, then close.

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
- **Keep both sides honest.** If coding reveals the task/design is wrong, update the
  task (it's the source of truth); never let the code silently diverge.

Treat a vague task title or a code↔task vocabulary mismatch as a **defect**, not a
style nit. The system's ability to recover intent across context resets depends
entirely on this.

## Working effectively
- **Search before you create — targeted, not exhaustive.** `search` for the concepts
  your new task touches (its component, table, function, feature) and inspect the
  handful of ranked hits — you do **not** read every task. This is how you find the
  tasks this one should depend on and avoid duplicating something that exists. It is
  cheap only because tasks are named discoverably (see the traceability convention
  above): a vaguely named prerequisite is invisible to search, so it never surfaces
  and the link is silently lost. Discoverable naming is what keeps this from being a
  scan of the whole store.
- **Wire dependencies explicitly — including among tasks you add together.** A task
  building on another must declare the edge; the dependency graph is what makes
  reconciliation and "what does this change affect?" possible, and an unlinked task
  is invisible to both. Two cases to handle, not one:
  - *Edges to existing tasks:* `search` for the prerequisites your new task builds
    on and wire an edge to each.
  - *Edges within a batch:* when you add several tasks at once (decomposing a plan),
    the dependencies are frequently **among the new tasks themselves** — wire that
    internal DAG too, not just the edges out to pre-existing tasks. This is the case
    most easily forgotten, because `search` won't surface tasks you only just created.
- **Search is best-effort; keep wiring as you discover edges.** Keyword search is
  recall-limited: if your terms don't match how a prerequisite was worded, you will
  miss it and create an unlinked task. That is not a permanent failure — add the edge
  the moment you notice it (often while coding), and the reconciliation machinery
  takes the propagation from there. Wire what you can find now; keep wiring as you
  learn more. A missing edge you never add, though, is drift that will never be
  caught — so err toward wiring.
- **Right-size tasks.** A task is a describable unit of work — a black-box feature —
  not one line of code, not a whole subsystem. If you can't describe it without
  listing implementation steps, it's too small; if it has many independent parts,
  split it.

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
- a one-or-two-task one-off that will never group more.

Before creating, run **`labels`** to see what exists and what each means (by its tasks);
**reuse** an existing label if it fits, and prefer a few broad labels over many narrow
ones. ("fix bug," "misc," "task" are worthless — they sort nothing.)

After a **bulk `load`**, it reports the new labels it created — review them and collapse
near-duplicates in **one pass** (a migration is when sprawl floods in, and `load` is the
one path the per-creation nudge doesn't catch).

**The epic pattern:** when an under-defined question explodes into many tasks, don't write
a new doc — make the question itself a task, give the whole cluster one label, and wire the
spawned tasks to `depends_on` that anchor. The theme is then grouped (label) and anchored
(dependency), and can't drift.

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
       T33 · <task name>
            what: <one line — enough to recall the task>
            did:  <one line — roughly what happened>
    ━━━ state ━━━━━━━━━━━━━━━━━━━━━━━━━━
       N open · N done · ⚠ N stale
       <worry first: stale ids + what they await, refused ops, new labels>

- Group by verb: Added / Edited / Closed / Reopened / Reconciled / Linked / Tagged.
- Per task: one short line each for `what:` (the task — enough to recall it) and `did:`
  (the change — roughly, not blow-by-blow). A clause each, not a paragraph.
- End with the state line; surface anything worrying first. If nothing is stale and
  nothing was refused, say so — silence is not reassurance.
