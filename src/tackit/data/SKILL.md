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
tackit tracks not just tasks but whether they're still in sync after changes. The
tools state your obligations in their responses — **do what they say** — and hold
this model so you never lose the thread:

- **Changing a task can invalidate everything that depends on it.** When you edit
  one, its dependents are marked **stale** = "may no longer be correct; review me."
- **Work the stale set to empty.** For each stale task, look at it, then either fix
  it (which may stale *its* dependents in turn) or reconcile it if still correct.
  Drift propagates only where real changes happen.
- **Never treat work as done while any task is stale.** A closed task silently out
  of sync with a changed dependency is the worst outcome — wrong and invisible.
  Clear the worklist before you wrap up.
- A stale task **cannot be closed** until reconciled. The tools enforce this — don't
  fight it; reconcile first.

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
- **Search before you create.** `search` for related work first — to find the tasks
  this one should depend on, and to avoid duplicating something that exists.
- **Wire dependencies explicitly.** A task building on another must declare it. The
  dependency graph is what makes reconciliation and "what does this change affect?"
  possible; an unlinked task is invisible to both.
- **Right-size tasks.** A task is a describable unit of work — a black-box feature —
  not one line of code, not a whole subsystem. If you can't describe it without
  listing implementation steps, it's too small; if it has many independent parts,
  split it.
