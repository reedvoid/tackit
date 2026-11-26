---
name: tackit
description: Use whenever planning, tracking, or executing work in a project that
  uses tackit (a task + dependency tracker). Covers when to record work in tackit,
  kind classification (design/schema slices are living spec; production and meta
  tasks open and close), the edit / close / wont_do / retire verbs, reconciling
  stale tasks after a change, writing links and `because` rationales, fold-backs,
  and the code↔task naming convention.
---

# Working with tackit

tackit is the project's single record of tasks, their links, and plan and build
state. It survives sessions and context compaction, so keep that state here
rather than in files that drift. The tools refuse illegal moves with a message
that says what to do instead; this file covers the judgment calls they can't make.

## Record work in tackit

- Record tasks, dependencies and decisions in tackit, not in markdown roadmaps,
  next-steps files or TODO comments; anything outside it isn't tracked.
- Keep tackit to actionable work and its links. Learnings with no action and
  external references go to memory.
- To create more than a couple of related tasks, use one `load`: it is atomic,
  carries full bodies, labels and links, and its `depends_on` can point at
  existing tasks by prefixed name.

## File side-door work the moment it appears

Work that enters outside a plan (a bug found in use, a change no task covers, a
follow-up spotted mid-task, an ad-hoc decision) has no trigger of its own, so it
lands in git untracked. Use an observable trigger instead: when you Edit/Write a
version-controlled file the current task doesn't name, state the disposition out
loud: new task, fold-back into <id>, or not tracked because <reason>. To choose:
an open task already covers this? Yes, fold back into it; no, file a new task,
classified on its own scope. File a bug at diagnosis and an unplanned change
before or with the edit; park a follow-up rather than doing it because you're
already there.

## Kinds

A spec says what should be true, code is what is true, a production task is the
change between them, and meta is the thinking that hasn't become any of those.

- **design** (D#) and **schema** (S#, the store's shape): the decisions. Living
  spec at status `spec`; refine with `edit`, `retire` only when 100% abandoned.
- **production** (T#): one unit of change that realizes an already-made decision.
  It must link the spec it realizes. Not for deciding, progress notes or scratch
  work, and not broadened: new scope is a new task.
- **meta** (M#): the notepad for experiments, investigations, brainstorming and
  release bookkeeping. Anything goes, but it links only to other meta tasks.

Classify each task by its own scope, not the thread or parent it came from (a
meta thread once spawned production children that were wrongly filed as meta).
Would landing it change what the product does (source, tests that pin contracts,
docs or agent instructions that change behavior)? Production. Is its only output
an edit to a tackit task? Meta. Does it introduce a decision or a store shape?
Design or schema. Kind bounds the cascade, so a wrong kind misroutes staleness
without any error.

Work flows one way: meta, then spec, then production, then code. Keep unsettled
work in meta and promote it to a slice only once the decision is made (a
`provisional` label on a slice marks one promoted early). Work that realizes an
existing decision, such as a bug fix, goes straight to a production task linked
to that spec. A task that both decides and builds splits into a slice plus a
production task linked to it. Don't ship code straight from a meta task.

## Verbs

- `edit`, `edit_replace_substring`: change content; both flag every directly
  linked neighbor stale. `edit_append` does not. That is a concession to
  reality, since agents clear flags unread; when a note really changes what a
  neighbor should say, use one of the other two so the neighbor is told. Append
  works on meta only.
- For a large body, prefer the diff ops: they cut what crosses the wire, not the
  cascade.
- An edit returns `newly_stale`, the open or spec neighbors it just flagged, as
  `{id, because}`: that list is your reconcile obligation. An `edit_append`'s list
  is always empty.
- On a production task, edit only to correct or shrink scope; a repeated
  need to edit one means the spec was thin, so fix the spec. To add scope, file a
  new task; to record a decision, edit the slice.
- `close` records that work shipped and does not cascade, so don't first edit the
  body with a completion note ("done", "committed <hash>"); git and the status
  carry that. Only a fold-back earns an edit before close.
- `reopen` corrects a closed record: fix its text, or undo a close made by
  mistake. New work after a close is a new task linked to the closed one, so
  the closed row stays an honest record.
- `wont_do` records a durable decision not to do the work; write a reason a
  later reader can use. It also works on a closed task whose close should have
  been a drop.
- `retire` only when a slice is 100% gone with nothing replacing it. Any partial
  change, even a big rewrite, is an `edit`, whose cascade is how neighbors get
  re-checked. If a premise is replaced by something too different to reuse, file
  a new task and leave the old one as record.

## Edits aren't free

Every `edit` and `edit_replace_substring` flags its neighbors, and each flag asks
someone to review. Noise edits grow the worklist until reviewers clear flags
unread; the review gets trained on noise and then waves through a real change
too. So make each edit consequential and necessary, with a `delta` that names its
substantive impact: a corrected fact, a refined contract, a fold-back finding, a
fixed stale reference. Ask what a reader filtering the flag against the link's
`because` would learn. Skip cosmetic polish, "while I'm here" cleanup (file it),
alignment to a neighbor's style, defensive elaboration, and vague deltas such as
"updated for clarity". Fix a typo only if it changes meaning.

## Reconcile

`edit`, `edit_replace_substring` and `reclassify` flag directly linked neighbors
in both directions; other ops don't. Every tool result carries the stale count.

- The worklist is stale open and spec tasks. Closed, wont_do and retired tasks are
  never flagged: a change flags only live neighbors.
- For each stale task, compare the upstream `delta` with the link's `because`. If
  they don't intersect, reconcile without re-reading; otherwise re-read with that
  question in mind.
- If the upstream was a production or meta edit that settled a decision,
  reconciling the slice means writing the decision into it, not just confirming
  its prose.
- `reconcile(ids=[...])` takes the explicit set you reviewed. Don't reconcile to
  empty the list; there is deliberately no reconcile-all.
- Don't call work done while an open or spec task is stale, because a task closed
  on unreconciled drift is wrong and nobody can see it. The one exception is the
  release cluster below.

### Release-cluster pattern

In a multi-phase release, a shipped task may refuse to close because a slice it
links is stale until a later phase sweeps it. That is the gate working. Tag it
"shipped pending Phase N close" in the report and close the cluster after the
final sweep. Don't remove links or close early to get around the gate.

## Discover dependencies with `links`, not search

Search misses coupling that doesn't share vocabulary. For a new production
task, call `links()` for the design and schema layer, judge each candidate, then
`links(picked, already_seen=...)` for the next hop, and `link_add` the real
couplings. For meta, look at the meta tasks; for a slice, the same spec layer.
Don't force a link the relationship doesn't support.

## Code ↔ task traceability

Nothing connects code to its task automatically except vocabulary. Name and
describe tasks with specific terms (the component, function, table or feature)
and use the same terms in file names, function names and comments. Cite tasks
in code by their prefixed id (`# T<id>: ...`). Write task names bare: the
`D`/`S`/`T`/`M` prefix is computed, so a typed one doubles. When code and an open
task or slice disagree, fix the record; treat drift as a defect.

## Fold-backs

Implementation finds what the plan missed. Route each finding to its home rather
than into whatever task you're in:

- **A decision** (changes what's decided or the store's shape): the slice. Edit
  the governing D#/S#, or add one and link it. Never the production body.
- **Progress** (built, verified, committed): not tackit; git and status carry it.
  Learnings with no action go to memory.
- **A correction or scope cut** to the current production task: rewrite its body
  with `edit` or `edit_replace_substring`. This corrects and never adds. When a
  correction shows a systematic miss, grep for the pattern family (`status =`,
  `INSERT…status`), not the verb names.

Don't leave a settled decision only in a commit message or a production body.

**Mandatory end-of-turn fold-back report.** Every turn with a commit or behavior
change says where each finding went (decision: which slice; correction: which
body; progress: git or memory) or "none — verified no scope gap".

When findings outgrow the body, at about the 3rd substantial finding, split at
the logical boundary: a meta note gets a linked sibling `<source> — findings`
note; a slice is carved into finer slices. Don't split a body after the fact
when carrying on costs less.

## A spec slice is current-state, not a changelog

Rewrite a slice to one coherent body that integrates the change and cuts what it
supersedes; prior text stays in the revision history. A slice with dated
"SUPERSEDES" blocks asserts old and new at once. The coherence nudge only
spots markers, so the rewrite is yours to finish.

## The granular-description discipline

A body must be implementation-ready: a fresh-session agent holding only the body
and its linked neighbors can build the task, or judge it done, without asking.
Write that detail at creation. Design and schema: the decision, constraints and
refusal cases. Production: files, call sites, signatures, tables, error
messages, tests. Meta: enough to interpret the result. No vague verbs ("fix
bug"), no conversation references ("as discussed"), no pointer-only bodies ("see
task X"), no TBD. Detail agreed in conversation goes into the record, routed as
a fold-back.

## A spec slice holds decisions, not code's literals

A slice survives `git clone`; a literal or a code location copied into it goes
stale the moment the code moves. Before writing a value, ask: is this slice the
*authority* for this value, or a *copy* of one that lives in code? Authority:
state it. Copy: state the rule that derives it. The bridge runs one way: code
names the spec by its D#/S# id; a spec never names code (no paths, no `file:line`,
no "see foo.py"). A reconcile updates decisions and never re-snapshots derived
values. Keep enough concrete substance (the formula, the policy, the required
set) for the decision to be reviewable.

## Right-size tasks

A task is one describable unit of work. If you'd reach for a second `###` heading
to organize independent execution units, file separate tasks; nested detail
within one unit is fine. Don't write co-equal sections describing distinct units
in one body, and don't create a task with no deliverable or decision (see fake
tasks below).

## Wire links explicitly

The cascade's usefulness rests on each link's `because`.

- Wire every real edge, including among tasks created together (the edges most
  often forgotten). In a `load`, wire existing anchors inline with
  `depends_on: <prefixed id or #id> :: <because>`; for existing-to-existing
  wiring, one `links_add` call.
- A link is durable, so every part must still resolve in a later session. A
  `load` batch key exists only inside that call; afterwards use the prefixed name
  or `#id`. A number typed into a task's title is not its id; cite the prefixed
  id tackit shows. A `because` names the durable coupling, never session context
  ("the task I just made", "as discussed").
- Write the `because` as one sentence on why the two must be reviewed together
  when one changes: "citations' FK references `documents.id`; a column rename
  breaks the join". Not work order ("setup"), and not a restated label.

## Links are coupling, labels are membership

A link is a claim about *consequence*: if X changes, Y must be re-examined. A
label is a claim about category, with no consequence. Before each `link_add`,
ask: if I edited X now, would I need to re-open Y? Yes: link, and the `because`
names the consequence. No, just the same epic: label. A decision slice linked by
the tasks that realize it is real coupling, so do not over-correct. The fake task
family, which nothing detects for you:

- **Hub task**: exists only to be linked to; every edit flags its whole cluster.
- **Membership link**: encodes category; its `because` restates a label name.
- **Rollup task**: a body that hand-tracks other tasks' status and is wrong the
  moment one closes.

Group a cluster with one label and check completeness with `board(label=...)`
against the expected set.

## Relationships are edges, not prose

When you'd type "depends on", "supersedes" or "see also <task>" into a body, wire
a link with that sentence as its `because`. The graph can't cascade or reconcile
what it can't see. The fake-task rule above forbids the content-free *node*; this
forbids the buried *edge*.

## Labels

Run `labels` and reuse one before creating another. Create a label only for an
axis you'd name out loud (a phase, an epic, a use case) that is distinct and
sizeable. Not for implementation details, not for what tackit already tracks
(time, status, history), not for one or two tasks. After a `load`, review the
labels it created and merge near-duplicates.

## Ship-on-pain

If a deferred fix's absence costs you now (large-body retransmits, working
around the tool, repeated restarts, a manual step on its Nth run), and its cost
times the remaining uses exceeds the cost of the fix, ship the fix next. This
overrides "finish the current bundle first" (one fix once sat deferred through 8
phases while its workaround was paid again on each). Make the ship-this-release
call, with a reason, when you file anything for later. Grow a bundle when an
addition is worth more than the slip, and when you tighten a rule that leans on
a deferred mechanism, promote the mechanism. A row marked "Standalone — not part
of this bundle", or a deferral to v(N+1) with no triage call, is that call
dodged.

## Report what changed

Every turn that changes tasks ends with a verb-grouped report: per task its id
and name, a `what:` line and a `did:` line; the reason for a wont_do or retire
and the `because` for a link; then the fold-back line and a state line from
`summary`, worries first.

    Closed
       <T#> · <task name>
            what: <what the task is> · did: <what changed>
    Linked
       <T#> ↔ <D#> · because: "<coupling>"
    ━━━ Fold-backs ━━━
       <ids> · <one line each>, or none — verified <why>
    ━━━ state ━━━
       N open · N done · N stale (open and spec only)
       worries first: stale ids, refusals, new labels
