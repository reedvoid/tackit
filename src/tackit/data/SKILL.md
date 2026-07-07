---
name: tackit
description: Use whenever planning, tracking, or executing work in a project that
  uses tackit (a task + dependency tracker). Covers when to record work in tackit,
  the reconcile-on-change discipline, the v0.5 verb taxonomy (edit / close /
  wont_do / retire) over the kind/status partition (design+schema live at
  status='spec'; production+meta at open/closed/wont_do), the bounded-obligation
  cascade (terminal-status stale is record-only), the kind classification rule
  (design/schema as living spec), and the mandatory code↔task naming convention.
---

# Working with tackit

tackit is this project's single source of truth for tasks, their links, and
plan/build state — a queryable store that survives sessions and context
compaction, so truth doesn't scatter into files that drift and contradict each
other.

This file is **behavioral instructions, not documentation** — `why:` / `do:` /
`don't-do:` per rule (the `why` is the context you need, encapsulated in the
rule itself). Sections marked **reference** are mechanics/tables, not rules.
Full incidents/examples live in the tackit task they're about (cited as `T###`),
not retold here.

## Use tackit — record, don't scatter; don't overload

**Record in tackit, not in prose.**
- why: loose tracking in markdown / scratch files / TODO-comments drifts out of sync — the exact failure tackit prevents. If it isn't in tackit, it isn't tracked.
- do: record tasks, dependencies, and decisions via tackit's tools before treating them as written down.
- don't-do: keep a roadmap, next-steps file, or status list outside tackit.

**Keep tackit to tasks + dependencies.**
- why: tackit is not a knowledge base.
- do: put actionable tasks + dependencies here; put durable learnings and external references in your memory.

**Import many tasks with `load`, not N `add()` calls.**
- why: `load` is one atomic import (rolls back entirely on any bad row) and carries the full task — kind, multi-paragraph `desc` (blank-line paragraph breaks preserved, so impl-ready bodies round-trip), labels, and `depends_on` edges with `because` rationales. `depends_on` references a batch-local key OR an EXISTING task by prefixed-name (`S30`) or `#id` (T215, kind-letter validated), so a new slice and its links to existing anchors land in ONE call — no separate `link_add` round-trips.
- do: reach for `load` whenever you're creating more than a couple of related tasks.
- don't-do: hand-loop `add()` for a known set of tasks.

## File side-door work the moment it appears (the reactive trigger)
- why: every other trigger fires off something already in tackit — a plan you're loading, a task you're closing or folding back into. Net-new work that enters another way — a bug found in use, a change no task covers, a follow-up spotted mid-task, an ad-hoc decision — has no such trigger, so it lands in git and never the graph, right at the reactive moment where tracking is skipped and code↔task traceability breaks. The trap: the one trigger left is "recognize side-door work" — an internal judgment that goes quiet at exactly that moment — so anchor to an OBSERVABLE event instead.
- do: the observable trigger is a write you can't miss — the moment you Edit/Write a version-controlled file the current task doesn't name, run the test and STATE the disposition out loud (new task / fold-back into <id> / not-tracked because <reason>); never silently skip it. File at diagnosis for a bug, before/with the edit for an unplanned change, parked for a follow-up, a D# for a decision — kind classified on its OWN scope. Same reflex as filing during planning.
- don't-do: fix-first-track-maybe; inline an unrelated follow-up because you're "already here"; fold into whatever task you're in; skip stating the call because the change "felt like housekeeping." The test: an OPEN task already covers this? yes → fold-back; no → new task. (Actionless learning → memory, not a task.)

## Kinds — every task is classified by what it touches

**Reference.** Every task carries a required `kind` (set at create, T94); kind is
coupled to status by partition, enforced both typed (`Core.add` / `load` /
`reclassify` default the partition-correct status) and by a DB CHECK on S1:

| kind        | partition       | meaning                                   | terminal verb   |
|-------------|-----------------|-------------------------------------------|-----------------|
| design      | spec / retired  | specification: captures decisions         | retire (D36)    |
| schema      | spec / retired  | specification: captures store shape       | retire (D36)    |
| production  | open / closed / wont_do | implementation: realizes specs in code | close / wont_do |
| meta        | open / closed / wont_do | bookkeeping: release tracking, exps    | close / wont_do |

- **design** — a decision slice (D#): what is decided, not how it's built. Lives at `status='spec'`; `retire()` only when 100% abandoned. `close`/`wont_do` are refused on spec — update a decision with `edit()` (the D29/S7 audit table preserves prior verbatim).
- **schema** — a store-shape slice (S#); the DDL contract. Same spec/retired partition; changes typically need migrations.
- **production** — code that alters the running app: source under `tackit/`, contract-pinning tests, **and README/SKILL.md** (they alter the agent's behavior). Lives `open` → `close`/`wont_do`.
- **meta** — work that does NOT alter the app: release bookkeeping, experiments, observations. Same open/closed/wont_do partition.

The partition invariant: `kind ∈ {production,meta} ⟹ status ∈ {open,closed,wont_do}`; `kind ∈ {design,schema} ⟹ status ∈ {spec,retired}`. Cross-partition `reclassify` auto-shifts open↔spec; a move with no clean target (e.g. closed-production → design) is refused — resolve the source state first.

### Classify every new task by what it touches
- why: a stray kind silently corrupts cascade reach (kind bounds the cascade via the meta-island).
- do: ask "would landing this touch `core.py` / `models.py` / `SKILL.md` / `README.md` / contract tests?" → yes = **production**; sole output is editing a tackit task's description = **meta**; introduces a D#/S# slice = **design**/**schema**.
- do: split a task that BOTH settles a design/schema-grade decision AND builds it — the decision is a design/schema slice, the build a production task that links to it (D245); don't let the decision ride inside the production body.

### The inheritance trap
- why: kind bounds the cascade, so a misclassified task silently corrupts cascade reach — and a task tends to inherit the kind of the thread it was spawned in (a meta thread once spawned production children mis-filed as meta; see T115).
- do: run the classifier on **this task's own** scope at `add()` time.
- don't-do: infer kind from the parent epic or the thread it was discussed in.

### Meta-island constraint
- why: it bounds the cascade — meta work (release tracking, experiments) must not drag spec/production tasks into stale review, or vice versa.
- do: link meta tasks only to other meta tasks.
- don't-do: `link_add` between a meta and a non-meta task (refused); attach a kind name (`design`/`schema`/`production`/`meta`) as a label (refused — kind already encodes it).

## The verb taxonomy — which verb changes state

**Reference.** Four verb families change a task's content or terminal state —
`edit` (three diff-shaped variants), `close`, `wont_do`, `retire`. (`reopen` /
`reconcile` / `reclassify` also change state; see their own rules.) Partition-
aware refusals enforce the distinctions; each verb's full **refusal matrix
lives in its MCP docstring**.

| Verb                     | When                                   | Partition    | Cascade? | Reversible?      |
|--------------------------|----------------------------------------|--------------|----------|------------------|
| `edit` (+ append / replace_substring) | content change on any task    | any          | **yes**  | n/a              |
| `close`                  | production/meta work shipped           | open/etc.    | no       | `reopen`         |
| `wont_do`                | production/meta work dropped           | open/etc.    | no       | terminal forever |
| `retire`                 | design/schema spec 100% abandoned      | spec/retired | no       | terminal forever |

The three edit variants are operationally equivalent (same cascade, same S7 audit row, same `delta` requirement); they differ only in how the new description is computed:
- `edit(description=…)` — full new body; for sweeping rewrites or small bodies.
- `edit_append(content, delta)` — append; only the snippet crosses the wire.
- `edit_replace_substring(old, new, delta)` — replace an exact UNIQUE substring (non-unique refused with the count; empty `new` = deletion; `old == new` = no-op).
- do: prefer the diff ops for large bodies — the transmission cost compounds over a session (see T179). They cut transmission, not cascade.
- return is lean by default (T242): all three echo back the now-stale neighbor set (your reconcile obligation) but NOT the focal body you just wrote — pass `include_description=True` only when you need to re-read the reconstructed body to verify the edit landed.

### edit vs retire — the all-or-nothing rule (D36)
- why: edit's cascade IS the partial-change re-evaluation mechanism; retire's no-cascade + open-neighbor refusal + immutable reason embodies a "100% gone" contract.
- do: use `retire` ONLY when a design/schema spec is 100% gone with no replacement; use `edit` for ANY partial change, including big rewrites.
- don't-do: retire when you actually want to migrate some links to a new spec — that's an edit. (If a premise is replaced by a too-different direction, file a fresh task and leave the old as record.)

### edit — change content
- why: keeps the task the source of truth; the D29/S7 audit preserves prior verbatim, so editing in place is safe and recoverable.
- do: edit for any content change on any status (open/spec/closed/wont_do/retired) with a `delta` naming the real semantic shift. Edits on design/schema fire the D31 code-check reminder — grep the slice id and verify the code.
- don't-do: try to edit the frozen `wont_do`/`retire` reason (no API). Before any cosmetic edit, see *Edits aren't free*.

### close — production/meta work shipped
- why: close means "we did this" (distinct from wont_do = "we decided not to").
- do: close a production/meta task when its deliverable shipped — that records "done" and does NOT cascade, so don't *also* edit the body with a completion note (`"done"` / `"committed <hash>"`); git carries the commit. Only a **fold-back** (a decision/constraint a future reader needs, not in git or the closed status) earns a pre-close edit.
- don't-do: close a design/schema spec (refused — `edit` to refine, `retire` if 100% gone) or a stale / linked-stale task (the close-gate — reconcile the upstream first). Full refusal matrix: `close()` docstring.

### reopen — resume closed work
- why: reopen is for the SAME work resuming; a NEW direction is a fresh task, so the closed row stays an honest record of what was done.
- do: reopen a closed production/meta task when there's more to do under the same premise.
- don't-do: reopen to mean something new (file a fresh task); reopen a wont_do/retired row (refused — terminal forever).

### wont_do — scope dropped
- why: distinct from close; records a durable decision *not* to do something.
- do: `wont_do` a production/meta task whose scope is dropped, with a durable `reason`.
- don't-do: `wont_do` a spec (refused — `edit` or `retire`) or an already-terminal task (no double-decide). Change-of-mind path is a fresh task; the wont_do row stays as record.

### retire — design/schema spec 100% abandoned
- why: a terminal "this decision is dead and nothing replaces it" (see edit-vs-retire).
- do: retire a design/schema spec only when 100% gone with no replacement, with a durable non-placeholder `reason`. Clear the open-neighbor gate first — `link_rm` + `wont_do` the neighbor if its work is also dead, or `link_rm` alone if it has other live specs.
- don't-do: retire when any partial replacement exists (`edit` instead). Full refusal matrix (6 checks) + the (i)/(ii) decision tree: `retire()` docstring. A worked retire: see D25.

### Decision tree (reference)

| Situation | Verb |
|-----------|------|
| Task's content needs to change (any status) | `edit` |
| Production/meta deliverable shipped | `close` |
| Closed production/meta work resuming (same premise) | `reopen` |
| Production/meta scope dropped | `wont_do` |
| Design/schema spec 100% gone, no replacement | `retire` |
| Design/schema spec partially changed | `edit` (cascade prompts link review) |
| Premise replaced, new direction too different to re-use | **new task**; leave the old as record |

## Edits aren't free (FORCEFUL)

- why: every edit fires the cascade depth-1; open/spec neighbors land on the worklist and pressure the close-gate. Low-quality edits grow the worklist → pressure to bulk-reconcile → eroded per-row review → the `delta × because` filter becomes a rubber stamp, and then a *real* edit's cascade gets reconciled away the same way. The discipline that catches genuine drift dies by being **trained on noise**; the cascade is only as useful as the signal you feed it.
- do: make every edit **consequential and necessary**; the `delta` must name a **substantive impact** — a real semantic shift (corrected fact, refined contract, fold-back finding, fixed stale reference). The test before editing: "what would a downstream reader, FAST-filtering this stale flag against the link's `because`, learn that justifies opening their task?" If "nothing" — don't edit.
- don't-do: cosmetic polish (tone/flow, meaning unchanged); "while I'm here" cleanup (file a separate task); stylistic alignment to a neighbor; defensive elaboration with no observed misread; vague deltas (`"small fix"` / `"updated for clarity"` — same cascade cost, zero filter signal). Typo fix only if the wrong character impedes meaning. (Recording that work shipped is a `close()`, not an edit — see the `### close` rule.)

## Reconciliation — keep the plan in sync

**Reference.** Cascade-firing ops are the edit family (`edit` / `edit_append` /
`edit_replace_substring`) and `reclassify`; they mark directly-linked neighbors
stale and record the `delta`. `close` / `wont_do` / `reopen` / `reconcile` and
the link ops do NOT cascade. The cascade is symmetric — editing either endpoint
stales the other. The app surfaces the stale worklist on every call; that's the
tool telling you the plan is currently inconsistent.

**Bounded obligation (D28 + D36).**
- why: terminal tasks (closed/wont_do/retired) CAN be stale, but the flag is record-only archaeology — "fixing" it would erase the signal that an upstream changed.
- do: treat the worklist as `status IN ('open','spec')` only, and reconcile those. Reconciling a stale spec acknowledges its prose still holds after the upstream shift — but when the upstream was a production/meta edit that settled a decision, reconciling means PORTING that decision into the slice, not just acknowledging (D245).
- don't-do: reconcile a terminal-status row (refused); chase closed-stale production/meta tasks.

**Orient with `delta × because`.**
- why: the link's `because` (coupling) and the upstream's `delta` (shift) tell you the specific aspect to check before opening a stale dependent.
- do: read both; if the shift doesn't intersect the coupling, reconcile without re-reading (FAST path) — otherwise re-read with the question pre-formed.

**Batch-reconcile a reviewed-clean set — `reconcile(ids=[...])` (D39).**
- why: one edit can stale several fine neighbors; N separate calls is redundant transport (one review executed N×) — but auto-clearing *all* stale would automate the judgment the cascade depends on, the rubber-stamp that kills it.
- do: pass the explicit list of ids you reviewed; it's atomic (one version bump) and emits a short alert.
- don't-do: reconcile to empty the worklist without reading each `because × delta` — there is deliberately no "reconcile all" for this reason.

**Never end a turn with obligation-bearing stale work.**
- why: a task left closed while something it depends on changed is wrong AND invisible — it silently corrupts everything downstream until discovered later at far greater cost.
- do: work the open/spec stale set to empty before declaring done; an empty filtered worklist is the only safe stopping point.
- don't-do: end a turn or close work while an open/spec stale task remains. (Closed-stale production/meta is record-only — ignore it.)

**The close-gate (reference).** `close`/`wont_do` are refused if the task is itself obligation-bearing-stale, or transitively shares a link with one — reconcile the named upstream first. A named closed/wont_do/retired neighbor is record-only and won't trip the gate.

**Edit on terminal rows is safe (D29).** Editing a closed/wont_do/retired task fires the cascade (terminal neighbors flagged record-only) and writes the S7 audit row; only the `wont_do`/`retire` reason is frozen.

## Discover dependencies with `links`, not search
- why: keyword search misses coupling that doesn't share vocabulary; `links` surfaces candidates deterministically and you do the semantic judgment.
- do: classify the new task's kind; for production, call `links` (no input) → the design+schema anchor layer, judge each, then `links(picked)` → the depth-1 neighborhood (filtered to open/spec targets), passing your judged set as `already_seen`; `link_add` each real coupling. For meta, scan the meta island; for design/schema, the same layer.
- don't-do: use search for dependency discovery (recall-limited); force a link where the relationship isn't real. (`link_add` returns a compact confirmation, not the slice — `show` the endpoint if you want it.)

## Code ↔ task traceability (MANDATORY)
- why: there is no automatic link between code and the task it implements — tackit holds the intent, the code is the implementation, and if the two can't be connected the intent is lost the instant your context resets. The bridge is only how you write both.
- do: name + describe every task with specific, distinctive terms (the component / function / table / feature), and mirror that exact vocabulary in file names, function names, and comments, so a search for one finds the other. When code and task disagree, edit the task — it's the source of truth.
- don't-do: vague titles (`"fix bug"`, `"update logic"`) — unsearchable, intent unrecoverable; let code↔task vocabulary drift (treat a mismatch as a defect, not a style nit).

## Fold-backs — implementation teaches planning
- why: no plan is complete — implementation reveals the missed call site, the unsimulated CHECK, the wording that reads wrong only in practice. That gap is the highest-value feedback the work produces; the commit captures the bug, but only the task body survives to the next agent. (The v0.5 `reopen()`/`load()` partition bugs were exactly this — see T168.)
- do: when a commit fixes a bug or changes behavior the responsible task body doesn't describe, edit that body the SAME turn — append a "Phase N finding" (symptom / root cause / fix / why-missed / pinning test). Trace which task's enumeration should have caught it: edit the D# if the design was wrong, the impl task if it was under-enumerated. At an enumeration sweep's end, grep for the pattern family (`status =`, `INSERT…status`), not the verb names.
- don't-do: leave the fix only in the commit message (not searchable from the task graph); ship a fix without its fold-back.

**Mandatory end-of-turn fold-back report.** Every turn with a code commit or behavior change states which task bodies absorbed discoveries — or "none — verified no scope gap." Tag each discovery **decision** (name the spec slice it landed in) or **impl** (production body); a decision recorded in a production body is a defect to fix, not a satisfied fold-back (D245). Silence is an incomplete turn.

### When findings outgrow the body — fold them out to a sibling
- why: cumulative Phase N findings can dwarf the original scope and make every edit expensive (T168 hit 57k chars unsplit).
- do: when about to add the 3rd substantial finding (or findings exceed the original body), file a sibling `<source-task> — findings` task, link it (`because: "absorbs Phase N+ findings from <source-task>"`), and put further findings there.
- don't-do: retroactively split a body that already grew (the cost usually exceeds continuing).

**Per-discovery format (reference):** `### Phase N finding — <label>` then **Symptom / Root cause / Fix / Why missed / Pinning test / Status (fixed in <hash>)**.

## A decision homes in a spec slice, not a production body (D245)
- why: a design/schema-grade decision folded into the production/meta task that surfaced it strands there — the governing spec slice silently goes stale, the decision isn't discoverable from the spec layer, and the link + a routine `reconcile` make the graph *look* maintained. Fold-back is what routes it wrong: during build work the active task is `production`, so "fold it into the task I'm in" lands a decision in the wrong layer.
- do: when work settles something that alters what's decided or the store's shape, record it in the design/schema slice — `edit` the governing one, or `add kind=design`/`schema` + link. A production/meta body may *reference* a decision through its link; it is not the decision's home.
- don't-do: append a decision to the production/meta task you're in because it's nearest; leave a settled decision living only in a production body with no spec slice.

## Auto-id name prefix (D32) — reference
Every task displays a synthesized `<kind-letter><id>` prefix (design→**D**, schema→**S**, production→**T**, meta→**M**), computed from kind+id, never stored, and indexed in FTS (so `search("T157")` resolves).
- do: write a bare name; reference tasks in code / PRs / conversation by the synthesized prefix (`# T157: …`) so grep bridges code↔task.
- don't-do: manually prefix the name (it double-prefixes at display). (Legacy pre-D32 design/schema names keep a manual slot prefix and display doubled — grandfathered, see T160.)

## Right-size tasks
- why: a task is a describable unit of work — a black-box feature. Too small (can't describe without listing impl steps) bloats the graph; too big (many independent parts) defeats clean cascade + granular description (T168 reached 54k chars unsplit before becoming 8 tasks).
- do: file separate tasks when you'd reach for a second `###` heading to organize INDEPENDENT execution units; nested detail within one unit is fine.
- don't-do: co-equal sections describing distinct units in one body; create a task with no deliverable or decision — a pure link-target or a status rollup of other tasks is a **fake task** (see *Links are coupling*).

## Wire links explicitly — including within a batch
- why: the cascade-ergonomics filter runs on the `because`; an unwired or placeholder edge degrades the cascade to "open every downstream."
- do: wire each edge with a specific `because` — to existing tasks (found via `links`/`search`) AND among tasks you add together (the internal DAG is the case most forgotten). In a `load`, wire external anchors inline via `depends_on: <S30|#id> :: <because>` (T215) instead of a follow-up `link_add` pass. For a pure existing↔existing wiring pass (no new tasks), `links_add(edges=[{a, b, because}, …])` creates many links in one atomic, validate-all-first call (T216) — endpoints are id or prefixed-name, already-linked edges are benign no-ops.
- don't-do: put an **ephemeral reference** anywhere in an edge — a link is durable graph structure, so every part of it must still resolve in a later session. Two traps: (1) the **ref token** — never reuse a *prior* `load`'s batch-local key (`g1`, `t5`) in a later load; that key existed only inside its own `load()` call and vanished when that call committed, so address the task by its now-persistent prefixed-name (`D532`) or `#id`. Use the canonical prefixed-name, not a legacy display slot-number written into a name (the slice titled `D38 — …` is task **D197**; `D38` resolves to the wrong row or none). (2) the **because** — name the durable coupling, never session-relative context (`"the task I just made"`, `"see Stage C"`, `"as discussed"`); a rationale the next reader can't resolve is a dead edge.

## Write real `because` rationales
- why: the cascade compares `because × delta`; a vague because filters nothing.
- do: one sentence naming why the two tasks must be reviewed together when one changes — e.g. "citations' FK references `documents.id`; a column rename breaks the join."
- don't-do: describe the implementation or work order (`"test fixture"`, `"setup"`); write a because that just restates a shared label — that's membership, attach a label, not a link (see *Links are coupling*).

**`ls` vs `board` (reference):** `ls` for a quick filtered id/title list; `board` for each matching task as a slice (deps + dependents + labels) in one call. Both are **lean by default** (D211): no `description`, and `board` shows the neighbor graph SHAPE without `because`/`last_edit_delta`. Both take a `kind` filter. Opt into bodies with `include_description` (and `board`'s `include_neighbor_because` for edge rationales); for ONE full body use `show`. Projection is include-or-omit — never truncated.

## Labels — when one earns its existence
- why: a label groups tasks along a meaningful axis you'd name out loud (a phase/milestone, an epic/theme, a use case); it's a dumb tag with no behavior.
- do: run `labels` and reuse before creating; create one only when it's distinct, sizeable, consequential, and memorable; prefer a few broad labels. After a bulk `load`, review the new labels it reports and collapse near-duplicates in one pass.
- don't-do: create a label that's an implementation detail (`regex`, `utf8`), something tackit already tracks (time = `created_at`, done = `status`, reopened = `history`), a one/two-task one-off, or a kind name (refused).

## Group a cluster with a label, never a hub task
- why: membership is the label's job; a task created to be linked-to by its whole cluster is a **hub**, and the membership edges into it are pure cascade noise.
- do: give the cluster one shared label; answer "is it complete?" with `board(label=X)` / `ls(label=X)` against the expected set (which lives in the slice that defines it).
- don't-do: make "the question" a task for the others to link to; hand-maintain a status table of other tasks inside a body (it's wrong the moment one closes).

## Links are coupling, labels are membership (D38 — FORCEFUL)
- why: a **link** is a claim about *consequence* — "if X's content changes, Y must be re-examined" — which is literally what the cascade fires. A **label** is a claim about *category* ("same grouping"), which carries no consequence: editing one sibling doesn't invalidate another. Confusing them poisons the cascade with false-positive stale flags that train the filter into rubber-stamping.
- do: at every `link_add` ask — "if I edited X right now, would I genuinely need to re-open Y?" Yes → **link** (the `because` names the consequence). "No, just same epic" → **label**. A decision-bearing slice linked by the impl tasks that realize it is NOT a hub — those are real coupling links, so do NOT over-correct.
- don't-do (the **fake task** family):
  - **Hub task** — exists to be linked-to; ~N² false-positive stale flags over its life.
  - **Membership link** — encodes category, not consequence; a permanent false-positive generator. Tell: the `because` just restates the cluster's label name.
  - **Rollup task** — a body that's a hand-typed status ledger of other tasks; duplicates state tackit tracks and drifts the instant one closes.
- (No auto-detector: a fake hub and a central decision slice are structurally identical — high degree, similar becauses — so the agent is the judge.)

## Relationships are edges, not prose (companion to D38)
- why: a link is structure the cascade traverses and `links`/`board`/reconcile act on; the `because` only supports it. A relationship narrated in a body is invisible to all of that — the graph can't cascade or reconcile what it can't see. A body holds the task's own work, not how it relates to others.
- do: when you'd type "depends on / supersedes / see also <other>" into a body, wire it instead — `link_add` (or `depends_on:` in a load) with that sentence as the `because`.
- don't-do: bury a relationship in prose, hand-list other tasks in a body (rollup), or name a parent for children to point at (hub). (D38 forbids the content-free *node*; this forbids the buried *edge*.)

## Propagation — a discipline lives on every surface it's needed
- why: each surface catches a different moment — SKILL.md at session-start, MCP docstrings at invocation, CLI help at the typed command, refusal messages at misuse, README at install. A rule in only one surface is skipped by agents who touch a different one.
- do: when designing or modifying a discipline, restate it (scoped to each surface's altitude) everywhere an agent meets the rule. Per D41 the FULL discipline lives once in SKILL; docstrings carry the op's sharp edge + a cite.
- don't-do: leave a rule only in SKILL (skipped by agents who jump straight to the tool) or only in a refusal message (that teaches by punishment, not prevention).

## The granular-description discipline (D37 — FORCEFUL)
- why: task descriptions must be **implementation-ready** — a fresh-session agent with only the task body + its linked neighbors must be able to implement (or judge completion of) the task without asking for clarification; re-deriving context that should be on the row is the failure tackit prevents.
- do: write impl-ready detail at `add()` — design/schema: the decision + constraints + refusal patterns; production: files, call sites, signatures, predicate tables, error-message banks, SQL recipes, tests; meta: enough to interpret the result. Fold discovered detail back via `edit()` BEFORE close; re-read the body against what shipped before closing.
- don't-do: vague verbs (`"fix bug"`); conversation references (`"as discussed"`); pointer-only bodies (`"see task X"`); TBD/TODO in a committed body (then it isn't ready to track); agreeing detail in conversation but never folding it into the task.

## A spec slice holds decisions, not code's literals (D234)
- why: design/schema kinds promise durability — they ARE the spec that survives `git clone` once the db is gitignored. A literal whose authoritative home is code (a default like `pool_size=30`, a full enumerated value list) breaks that promise: it goes stale the instant code changes, turning the slice into a stale duplicate. A decision and its rationale change only when the *design* changes; a copied literal changes on any tuning edit.
- do: write the decision + rationale + derivation rule (e.g. "pool sized K×parallel×2, raise in lockstep") and point to the code home for the resolved value (`see config.py:pool_size`). Litmus before typing a literal: is this slice the *authority* for this value, or a *copy* of one that lives in code? Authority → state it, it's a decision. Copy → replace with the rule + a pointer. Keep enough concreteness to evaluate the decision (the formula, the policy, the required set).
- don't-do: mirror code-owned values into a slice (defaults, full enumerated value lists); reconcile a slice by importing current literals — a reconcile updates the *decisions*, never re-snapshots derived values (this is how the anti-pattern sneaks back in); over-correct into vagueness ("we use pooling, see code") that strips the decision of reviewable substance.

## Ship-on-pain — don't endure friction you can fix (the load-bearing rule)
- why: if a deferred fix's absence is costing you NOW (large-body retransmits, "drop into Python" MCP bypass, repeated restarts, a manual step on the Nth iteration), the workaround compounds on every remaining unit and almost always exceeds the cost of pausing to ship the fix. It **OVERRIDES "finish the current bundle first"** — deferring pays the avoided cost on every remaining unit of the current work. This is the rule the others assume — without it, fold-backs / granular-description / propagation are patterns you can describe but won't act on under pressure. (Anchor: T179's diff ops sat open through 8 v0.5 phases, ~60k tokens re-paid per fold-back ×4, then shipped in ~30 min once named — see T179.)
- do: when a workaround's cost × remaining occurrences > the cost to ship the fix, ship the fix NOW — it's the next task. Make the ship-this-release yes/no call (with a reason) at filing time. Grow a bundle when the addition is higher-leverage than the slip. When you tighten a rule that leans on a deferred mechanism, promote that mechanism.
- don't-do: write "Standalone — NOT part of this bundle" (a smell — it classes the item background-deferred regardless of leverage); defer with "I'll deal with it in v(N+1)" dressed as planning; file a backlog row without a triage decision (a graveyard plot).

## Report what changed (every turn that touches tasks)
- do: report in a scannable, verb-grouped layout — per task an id + name, then one `what:` line (recall it) + one `did:` line (roughly what changed); name the `reason` for wont_do/retire and the `because` for links; end with the fold-back line and a state line (N open / done / stale, worry first).
- don't-do: a wall of prose, or a bare id.

**Format (reference):**

    ━━━ changes ━━━━━━━━━━━━━━━━━━━━━━━━
    ✓ Closed
       T30 · label-discipline guidance
            what: when a new label is worth creating
            did:  wrote the rules into SKILL.md
    + Added
       T33 · <name> (kind=production)
            what: <one line> · did: <one line>
    + Linked
       T127 ↔ T122 · because: "<coupling rationale>"
    ✗ Wont_do
       T130 · <name> · reason: <durable reason>
    ━━━ Fold-backs ━━━━━━━━━━━━━━━━━━━━
       <task ids edited> · <one line each> — OR — none — verified <why>.
    ━━━ state ━━━━━━━━━━━━━━━━━━━━━━━━━━
       N open · N done · ⚠ N stale (open + spec only — D28)
       <worry first: stale ids + what they await, refused ops, new labels>

### Release-cluster pattern
- why: in multi-phase releases, production tasks reach "shipped, can't close" because the close-gate refuses on stale design/schema neighbors a later phase will sweep — that's the gate working, not a bug.
- do: tag each "shipped pending Phase N close" in the turn summary; close the cluster after the final sweep clears the worklist (the v0.5 D35+D36+D37 batch ran this way).
- don't-do: fight the gate with `link_rm` workarounds or premature closes; add a "close --acknowledge-stale" escape (rejected — it erodes the gate's guarantee that close-atop-unreconciled-drift can't happen).
