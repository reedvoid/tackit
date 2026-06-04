# tackit

**A deterministic task + dependency tracker built for coding agents** — with a CLI
for humans too.

One local SQLite file is the single source of truth for a project's build plan: its
tasks, their dependencies, and their reconciliation state. The agent fetches small
*slices* on demand instead of re-reading monolithic plan documents, so project truth
survives across sessions and context-window compaction, and a change to one task is
traceable to everything that depends on it. A typed boundary refuses malformed data,
and a reconcile-on-change discipline surfaces what each change invalidates.

> **Status: alpha (0.6.0).** Data model, interfaces, and sync design are settled
> and implemented; 400+ tests across unit / CLI / MCP / Hypothesis property
> suites. v0.5 added a typed status partition (design/schema slices live at
> `spec`/`retired`; production/meta at `open`/`closed`/`wont_do`) and a new
> `retire` verb for fully-abandoned design decisions — see
> [v0.5 highlights](#v05-highlights) below. Expect rough edges.

## Contents

**Everyone:**
[Why it exists](#why-it-exists) · [Install](#install) · [Using it](#using-tackit-through-your-agent) · [Workflows](#the-rhythm-a-few-real-workflows) · [Examples](#examples-the-full-surface) · [CLI for humans](#for-humans-the-cli) · [Migrating a project](#bringing-in-an-existing-project) · [Testing](#testing) · [License](#license)

**🤖 Agents — read these, especially during install (a critical juncture):**
[Start here](#for-agents-start-here) · [Configure your agent's settings](#configure-your-agents-settings) · [MCP tools](#mcp-tools)

## Why it exists

Coding agents lose the plot across long projects. They scatter task tracking across
ad-hoc files that drift apart and start contradicting each other; they lose
intermittent decisions when the context window compacts; they re-read large plan
documents every session until context cost erodes accuracy; and they narrate
dependency relationships by hand in prose that goes stale the moment something
changes. tackit's job is narrowly *correctness on the dev path* — not knowledge
retrieval — so it absorbs that scatter into one queryable, sliceable,
single-source-of-truth store:

- **One home per fact.** Every view (board, status, rendered narrative) is *derived*,
  never hand-kept in parallel — so two copies can't come to disagree.
- **Small slices.** The unit of access is a task plus its direct links, not a whole
  document, so context cost stays bounded.
- **Fail loud.** Malformed data and illegal transitions are refused at the boundary,
  not silently absorbed.
- **Traceable change.** Editing a task flags everything that depends on it for review,
  so a change can't silently leave the rest of the plan wrong.

## Install

Two parts: a human pulls the package, then **hands off to the coding agent**, which
wires it in. That second step is what actually registers the MCP server and the
skill — `pip install` alone does **not** make tackit usable by your agent.

**1. Human — pull the package, then tell your agent to set it up:**

```bash
pip install tackit
```

Then paste this to your coding agent (Claude Code, etc.):

> I've installed the `tackit` Python package. Run `tackit setup` and carry out the
> steps it prints to register it (MCP server + skill) for this project.

You don't need to explain more than that — naming `tackit setup` is enough, because
the command is self-documenting: it prints everything the agent needs.

**2. Agent — register it.**

Running `tackit setup` *prints* contextualized post-install steps (it edits no config
itself): the MCP registration snippet (a portable, committable command) to add to the
agent's own config, where to drop the bundled `SKILL.md`, a reminder to add the
always-on discipline to its config (see [Configure your agent's
settings](#configure-your-agents-settings)), and `tackit init`. The agent carries them
out — it does the wiring because it knows where its own config lives — and should read
[For agents: start here](#for-agents-start-here) while installing.

## Using tackit (through your agent)

Day to day you don't touch tackit directly — you tell your coding agent in plain
language and it drives the tools. Common asks:

- **"Add a task to rotate the JWT signing keys; it depends on the auth-token-endpoint
  task."** → the agent searches for the prerequisite, creates the task, and wires the
  dependency.
- **"What's open right now?"** / **"What's still outstanding?"** → it lists the open
  tasks and flags anything stale.
- **"I changed the token format — update that task."** → it edits the task, and tackit
  marks everything that depends on it *stale* for review.
- **"What did that change affect?"** / **"What's stale?"** → it shows the reconciliation
  worklist.
- **"Mark the parser task done."** → it closes the task — *refused* if the task is stale
  or sits on unreconciled work, so you can't silently mark broken work complete. The
  gate honors obligation-bearing stale only: closed / wont_do / retired neighbors
  carrying stale flags as historical record don't trip it.
- **"We're abandoning the spec for that legacy auth flow — retire it."** → it
  calls `retire` on the design slice — *refused* if any linked production task
  is still open (because then the work isn't actually dead), with the
  obligation list and the resolve path inline. The retired row stays as a
  graveyard marker, and `link_add` to it is refused going forward.
- **"Show me everything under the `testing` label."** → it lists that group.

After any change, the agent reports back what it did and what's outstanding, so you
rarely have to ask. For the complete set, see
[Examples: the full surface](#examples-the-full-surface).

## The rhythm: a few real workflows

Concrete loops, so the discipline reads as habits, not rules:

- **Start a piece of work.** *"Add a task to rate-limit the login endpoint; it depends
  on the redis-session task."* → the agent `search`es for the prerequisite, creates the
  task, wires the dependency, and echoes the task's vocabulary in the code it writes —
  so later, `search "rate-limit"` lands on both the task and the code.
- **Pick up after a break.** *"What's outstanding?"* → `board` / `stale` shows the open
  work and anything flagged, in one screen — **without re-reading a plan document.**
  (The whole point, if your "plan" is a 4000-line file today.)
- **A change ripples.** *"Update the auth-token task — the format changed."* → the agent
  edits it; tackit marks everything that depends on it **stale**, and you walk each one
  against what changed and either fix or reconcile it. You can't leave a downstream task
  quietly wrong — that's the core guarantee.
- **Wrap up.** Nothing is "done" while the worklist `stale` (filtered to live
  partition: `status IN ('open', 'spec')`) is non-empty; an empty worklist is the
  only safe stopping point. Stale on terminal-status rows (`closed`, `wont_do`,
  `retired`) is *record-only* archaeology — historical signal that an upstream
  changed, but it doesn't pressure the worklist or the close-gate.

## For humans: the CLI

The CLI is the human door — debugging, scripting, and a fallback for the agent.
`tackit --help` (and `tackit <cmd> --help`) is the full, self-documenting surface.

```bash
tackit init                                                              # create .tackit/ in this project
tackit add "parse FTS5 query" --kind production --label search           # create a task
tackit add "rank search results" --kind production \
  --dep 1::"ranking consumes the parsed query"                           # link task 2 to task 1 with a real because
tackit search "fts"                                                      # ranked keyword search
tackit show 2                                                            # slice: task + linked tasks + labels
tackit edit 1 --desc "tokenized MATCH" \
  --delta "switched from prefix to tokenized query"                      # marks linked tasks stale
tackit stale                                                             # the reconciliation worklist
tackit reconcile 2                                                       # reviewed-OK; clear stale
tackit close 2                                                           # refused while stale (or atop stale deps)
tackit wont-do 3 --reason "redundant with T2" --delta "dropping scope"   # decided NOT to do (production/meta)
tackit retire 7 --reason "premise replaced by D99" \
  --delta "retiring D7"                                                  # design/schema spec 100% abandoned
tackit ls --status open                                                  # query/board; --status accepts open/closed/wont_do/spec/retired
```

The store lives at `.tackit/tackit.db` (binary, gitignored). Its git-canonical form
is a deterministic SQL text dump, `.tackit/tackit.sql`, re-written on every change and
committed — so diffs and merges are reviewable text, never a binary blob. Sync is
automatic; `tackit status` / `export` / `import` / `restore` exist only for the
divergence cases auto-sync deliberately refuses to guess at.

## For agents: start here

If you are an agent installing or working in a tackit project, read this — install is
the moment to absorb it. But this is orientation, not the operating manual: the
**complete, canonical discipline ships as `SKILL.md`** (dropped into your skills path
during install and loaded whenever you work in a tackit project), and the tools state
your specific obligations in their own results. At work time, rely on those — not on
this page.

**The model, in one breath.** One item type — a **task** with a required `kind`
(design / schema / production / meta) that determines which `status` values it
can carry (the partition: design/schema get `spec`/`retired`; production/meta
get `open`/`closed`/`wont_do`), plus a `stale` bit; one relationship — a
**symmetric link** (each link is one undirected edge; traversal is the same in
both directions); freeform labels. Every view (board, status, narrative) is
*derived* from these, never hand-kept in parallel.

**The mindset these mechanisms assume.** tackit's discipline assumes two general
operating principles. They aren't tackit-specific — they apply to any work — but
they're the mindset everything below depends on. Without them, the rest of this
page is a set of patterns; with them, the patterns become a working discipline.
Both are reactions to the same default failure mode (a bias toward forward motion
that routes around broken things), at different moments.

- **Fail loud — never degrade silently.** When something fails, do not continue
  as if it did not. Diagnose the error and fix the root cause; never reason past
  it ("probably not important") and move on. Surface failures to the user with
  recovery options — do not produce "degraded output." Retry or auto-recover only
  with a specific, stated justification. tackit is built for exactly the failure
  mode this rule prevents: a stale link, an un-reconciled task, an unfinished
  bundle that quietly looks finished. Without fail-loud, tackit's worklist and
  close-gate are just refusable suggestions; with it, they're the mechanism that
  makes silent drift impossible.
- **Fix broken things first.** The entry-point variant of fail-loud. On entering
  a session or starting a task, scan for inherited broken state — unshipped fixes
  named in fold-back reports, deferred-action notes in memory, `status='open'`
  items the prior session named as "next," loose ends called out in recent
  commits. Those are the next task, not whatever forward planning the question
  seems to invite. "What's next?" with a deferred fix in scope is "fix that" —
  not "let me enumerate the backlog." Inherited state FEELS like old context
  (it's from yesterday) even when it's the most actionable item in front of you;
  the recognition pattern needs to fire deliberately. tackit's worklist is
  exactly this rule's mechanism — a deferred fix sitting at `status='open'` is
  the worklist telling you what to ship next.

**What `SKILL.md` holds you to** (the short version — read it there in full):

- **Single source of truth.** Everything goes in tackit, via its tools — never ad-hoc
  markdown or TODO comments. If it isn't in tackit, it isn't tracked. It is *not* a
  knowledge base; durable learnings live in your memory.
- **Reconcile on change (bounded by partition under v0.5).** A change marks the
  task's directly linked neighbors **stale**. tackit surfaces the outstanding
  worklist on *every* call (deterministically — it's code in the app, not a
  reminder you can skip). The worklist is status-derived: only stale tasks with
  `status IN ('open', 'spec')` carry an obligation. Closed / wont_do / retired
  rows may still carry stale=1 as historical record but they're off the worklist
  and don't pressure the close-gate. Review each obligation-bearing stale task
  with its linked neighbors and `edit` or `reconcile` it. **Never end a turn
  while the worklist is non-empty** — a task left closed atop a changed
  dependency is wrong *and* invisible. (Edits on any status — including
  closed / wont_do / retired — are allowed and preserved verbatim in the
  description_revisions audit table.)
- **Find, wire, right-size.** Use `links` (no input → anchor layer of live
  design+schema specs; with ids → depth-1 neighbors filtered to viable targets
  `status IN ('open','spec')`) to discover dependencies deterministically;
  wire links explicitly with a real `because` rationale (including among tasks
  you add together); keep tasks describable units of work.
- **Fold back what implementation reveals.** No plan is complete; the call site
  nobody listed, the partition CHECK nobody simulated, the message wording that
  reads wrong only in practice — these emerge only at implementation time, and
  they are the *highest-value* signal the work produces. When a commit fixes a
  bug or changes behavior the responsible task body doesn't describe, edit that
  task body to record the finding alongside the commit — the commit message is
  not searchable from the task graph. The end-of-turn summary must include a
  fold-back line (which task bodies were updated, or *none — verified*); silence
  on fold-backs is not reassurance. See `SKILL.md` "Fold-backs" for the per-
  discovery format and the enumeration meta-lesson (grep the pattern family,
  not the verb name).
- **Ship on pain — don't endure friction you can fix.** This is **the rule the
  other discipline rules serve**. If you're feeling friction *right now* from a
  deferred fix's absence — a workaround eating tokens (large-body retransmits,
  "drop into Python" bypass, repeated session restarts, manual step on the Nth
  iteration) — the workaround itself is the forcing function. The next task is
  already chosen for you: stop, ship the fix, then continue with the friction
  gone. This **overrides "finish the current bundle first"**: deferring the fix
  means paying its avoided cost on every remaining unit of work, and that
  compounding cost almost always exceeds the cost of pausing to ship. The
  smell: a task body that opens with "Standalone — NOT part of the current
  bundle" is classed background-deferred regardless of leverage. Either fold
  it into current work with a reason, or write the explicit "defer because X"
  reason — never passive. Backlog filing is *also* a "ship-this-release? yes/no
  with reason" decision made **at filing time**; default-deferred items become
  graveyard plots. The v0.5 release ate ~60k tokens × 4 fold-backs absorbing
  the cost the `edit_append` / `edit_replace_substring` ops would have cut ~10×
  — that fix sat at `status='open'` the entire time. See `SKILL.md`
  "Ship-on-pain" for the full anchoring incident and the six application rules.

### v0.5 highlights

v0.5 reshapes how design/schema slices relate to the lifecycle. The shape is
worth the few minutes — it changes which verb you reach for, and the wrong
choice gets refused at the boundary.

**The kind/status partition.** Every row is in one of two partitions, by kind:

| kind            | partition (status set)             | terminal verbs        | what it means          |
|-----------------|------------------------------------|-----------------------|-------------------------|
| design / schema | `spec` / `retired`                 | `retire`              | specifications: capture *decisions* |
| production / meta | `open` / `closed` / `wont_do`    | `close` / `wont_do`   | implementations: realize specs in code |

Cross-partition pairs are illegal at the schema-level CHECK constraint: you
cannot have a `kind='design'` row with `status='open'`, nor a `kind='production'`
row with `status='spec'`. The boundary refuses the impossible combinations so
the rest of the system can rely on them. (Cross-partition `reclassify`
auto-shifts status `open ↔ spec` when the source state has a clean target;
no-clean-target reclassify — e.g. closed-production → design — is refused;
resolve the source state first.)

**The verb taxonomy.** Four state-changing verbs; the right one follows the
partition:

| Verb     | When                                   | Partition  | Cascade? | Reversible?       |
|----------|----------------------------------------|------------|----------|--------------------|
| `edit`   | content change on any task             | any        | **yes**  | n/a                |
| `close`  | production/meta work shipped           | open / etc.| no       | `reopen`           |
| `wont_do`| production/meta work dropped           | open / etc.| no       | terminal forever   |
| `retire` | design/schema spec 100% abandoned      | spec / retired | no   | terminal forever   |

**The all-or-nothing rule (D36).** Is the spec 100% gone, with no replacement?
→ `retire`. *Any* partial change — including big rewrites — uses `edit`. Edit's
cascade IS the partial-change re-evaluation mechanism; retire's "no cascade +
open-neighbor refusal + immutable reason" embodies the 100%-gone contract. If
you find yourself wanting to migrate some links to a new spec, you weren't
retiring — you were editing.

**Retiring a spec — the workflow.** retire is uncommon (most "spec change"
scenarios are partial and use edit). Use retire only when the decision is
truly dead with nowhere to go. The op runs a 6-step refusal matrix (reason
validation incl. placeholder filter, `status='spec'`, kind in design/schema,
stale gate, linked-stale gate, **open-neighbor gate**); the open-neighbor
refusal is the load-bearing one — it lists each open neighbor with its
`because` rationale and presents an (i)/(ii) decision tree inline:

```text
REFUSED: D17 has 2 open linked task(s). Resolve each before retiring:
  - T42 (status=open) -- because: 'auth refresh impl realizes D17'
  - T58 (status=open) -- because: 'session expiry decision rests on D17'
For each open neighbor, decide:
  (i)  If the neighbor's work realizes ONLY this retired decision:
       link_rm + wont_do(neighbor, reason=...) -- the work is dead too.
  (ii) If the neighbor's work has other reasons to exist (linked to
       other living specs): link_rm -- work continues under remaining
       live premises.
Then re-attempt retire(D17).
```

After retire lands, the row stays as a graveyard marker. `show(retired_id)`
still lists historical edges; new `link_add` to a retired endpoint is refused.
If the decision later returns, file a fresh `D#` with the new direction — do
**not** reanimate the retired row (T132 generalized: no double-decide on a
terminal state).

**Granular-description discipline (D37).** Task descriptions must be
**implementation-ready**: a fresh-session agent — no conversation history, no
prior context, only the task body and its linked neighbors — should be able to
implement (or evaluate completion of) the task without asking for
clarification. Anti-patterns are refused as defects, not style nits:

- **Vague verbs** ("Fix bug", "update logic", "clean up X") — unsearchable,
  unimplementable.
- **Conversation references** ("as discussed", "see chat") — ephemeral context
  that doesn't survive a session reset.
- **Pointer-only bodies** ("see related task X") without inlining the scope —
  forces traversal that loses on fresh-session.
- **TBD / TODO placeholders** in committed task bodies — flag and resolve
  before commit; if a detail genuinely isn't decided, the task isn't ready.
- **Implementation-by-conversation** — agreeing on a detail in conversation
  but never folding it into the task body. The task is durable; conversation
  isn't.

The discipline applies *at create time* (write impl-ready from the start),
*during implementation* (when impl reveals an under-defined detail, `edit()`
folds it back into the body — this is the same fold-back rule below at the
description-granularity layer), and *before close* (re-read the description
against what was actually implemented; if it no longer captures the impl,
`edit()` before close).

**A concrete example of the discipline applied.** Suppose during implementation
you discover that the cascade's depth-1 marking has an edge case on retired
endpoints that the original task body said nothing about. Don't ship the fix
silently; `edit()` the task body before close:

> *Before:* "Fix cascade bug on retired endpoints."
>
> *After (edit):* "Fix cascade depth-1 marking on retired endpoints: edge
> case where mig 009 leaves stale=1 on rows that should be record-only;
> affects `core.py:_stale_linked_transitive` line 663; reproduced in
> `test_d36_retire.py::test_edit_on_retired_fires_cascade_record_only`;
> fix updates the predicate from `status='open'` to
> `status IN ('open','spec')` so retired rows are correctly excluded."

The "after" version is impl-ready in any future session: any agent reading it
can grep the named file:line, run the named test, and verify the fix.

One convention is worth explaining in full right here — it's the one people skip and
then regret, and it belongs in your always-on settings, not just in tackit:

### Code ↔ task traceability (the opinionated bit)

tackit stores *intent* — what a task is, and why. Your code is the *implementation*.
**Nothing automatically connects the two:** tackit can't see your code, and your code
doesn't know which task it came from. The instant your context window resets, that
connection is gone — unless you built it into how you wrote both. A future you, or
another agent, looking at a function with no memory of why it exists, has no path back
to the decision that created it.

So make the link yourself, on both sides:

- **Name tasks so they're findable.** Tasks are located by keyword `search`. A task
  called "fix bug" or "update logic" is effectively invisible — it surfaces for no
  reasonable search, so its history and dependents become unrecoverable. Use
  distinctive, specific terms: the component, table, function, or feature ("rotate JWT
  signing keys on the auth token endpoint"), never vague verbs.
- **Mirror that vocabulary in the code, and cite the task id.** When you implement
  task `T42`, reference `T42` in the code and comments, and echo the task's
  distinctive words in file and function names. If the task says "token rotation," the
  code says "token rotation" — not "key cycling." Now a `search` from either side
  lands on the other.

It's a small tax at write time that buys back the one thing a context reset destroys:
the ability to recover *why*. Treat a vague task title, or a code↔task vocabulary
mismatch, as a **defect**, not a style nit. Because this holds even when the tackit
skill isn't loaded, put it in your agent's always-on settings too:

### Configure your agent's settings

Add the tackit discipline to your agent's *always-on* instructions (`CLAUDE.md` for
Claude Code; the equivalent for other agents) so it holds even when the skill isn't
loaded — especially the code↔task traceability, which is global by nature. A starting
point:

```markdown
## tackit
- tackit is this project's single source of truth for tasks + dependencies. If it
  isn't in tackit, it isn't tracked. It is not a knowledge base.
- Importing many tasks at once (a plan, a backfill)? Use `load`, not N `add()` calls —
  one atomic import carrying full multi-paragraph descriptions, labels, and
  `depends_on` edges with `because` rationales (D24/D40).
- Code ↔ task traceability is MANDATORY. When you implement a tackit task, cite its
  id (e.g. `T42`) and reuse the task's distinctive vocabulary in file/function names
  and comments, so a future session can grep from code to intent and back. Treat a
  vague task title or a code↔task vocabulary mismatch as a defect, not a style nit.
- Search before creating; wire dependencies explicitly (including among tasks added
  together); right-size tasks to describable units of work. Use `links` to discover
  candidate connections deterministically — the op surfaces the viable target set
  (status IN ('open','spec')); your judgment picks among them.
- Links are for coupling, labels for membership. A link means "edit one ⇒ re-check the
  other" — that is what the cascade fires; grouping tasks under a shared theme is a
  *label*, not links to an anchor. Never create a hub task (one whose purpose is to be
  linked-to) or a rollup task (a body that re-types other tasks' status); coverage is a
  `board`/`ls`-by-label query, not a hand-typed ledger (D38).
- Reconcile on change: a change marks linked neighbors stale. The worklist filter is
  `status IN ('open','spec')` — stale on terminal-status rows
  (closed/wont_do/retired) is record-only archaeology and does NOT pressure the
  worklist or the close-gate. Review each obligation-bearing stale task against its
  linked neighbors, then `edit` or `reconcile`. Never end a turn while obligation-
  bearing stale remains. Clear a reviewed-clean set in one call with
  `reconcile(ids=[...])` (atomic, short alert) — but the list is explicit by design;
  there is no "reconcile all" shortcut, because that would automate the judgment the
  cascade depends on (D39).
- Reuse labels before creating new ones (run `labels` first). A label must earn its
  name — a phase, epic, or use case — never an implementation detail or a one-off.
- After any task change, report back in a scannable, verb-grouped layout
  (Added/Edited/Closed/…): per task show the id + name, then two short lines —
  `what:` (enough to recall it) and `did:` (roughly what changed); end with the state
  (N open/done/stale) and any worry up front (stale ids, refused ops). Not prose, not
  a bare id.
- Fold-back is mandatory and goes in the report. When a code commit fixes a bug or
  changes behavior the responsible task body doesn't describe, edit that body to
  record the finding (symptom, root cause, fix, why missed, pinning test) — the
  commit message is not searchable from the task graph. Every turn with a code
  change names the fold-backs explicitly, or states "Fold-backs: none — verified."
  Silence is not reassurance. Implementation discoveries are higher signal than
  any plan; capturing them in the task body is how the next session inherits the
  lesson.
```

### MCP tools

```bash
tackit mcp     # serve the stdio MCP server (the agent's primary door)
```

The agent's primary door is the MCP server: the harness pushes typed tool schemas
into the agent's tool zone (no `--help` round-trip, no shell quoting, can't
hallucinate a flag that doesn't exist). Tool names are the bare verbs — `add`,
`show`, `search`, `edit`, `edit_append`, `edit_replace_substring`, `close`,
`reopen`, `reconcile`, `wont_do`, `retire`, `reclassify`, `link_add`,
`link_rm`, `label_add`, `label_rm`, `ls`, `board`, `stale`, `labels`,
`render`, `history`, `load`. (T179: the two `edit_*` ops are diff-shaped
variants of `edit` — only the snippet or the `(old, new)` pair crosses the
wire — designed for the "append a Phase N finding" / "fix one phrase"
patterns that otherwise round-trip a large body. Same cascade, same audit
row, same delta requirement.) Input schemas are generated
from the Python type hints, so they can't drift from the real interface. Every
result is wrapped as `{stale_alert, label_nudge, delta, code_check_reminder,
result}` so the outstanding stale set + per-op nudges ride along on every call;
refusals (e.g. closing a stale task, retiring with an open neighbor, link_add
to a retired endpoint) come back as errors that state the reason and — where
relevant — the resolve path inline.

## Examples: the full surface

Everything you can drive through your agent — it maps your request to tackit's verbs:

| Ask your agent… | tackit does |
|---|---|
| "Add task X (linked to Y, label Z)" | `add` with `deps={Y: "<because>"}` + `label_add` |
| "Find the task about the FTS query" | `search` (ranked keyword) |
| "Show me task 12 and what it touches" | `show` — task + linked tasks + labels + because + last_edit_delta on each |
| "Update task 12's description" | `edit` — and stales its linked tasks (cascade) |
| "Task 12 is linked to task 7" / "remove that link" | `link_add` (with `because`) / `link_rm` |
| "What can I link to?" | `links` — anchor layer (design+schema) or depth-1 expansion |
| "Tag task 12 `smoke-test`" / "untag it" | `label_add` / `label_rm` |
| "What's open / closed / stale?" | `ls` / `stale` (status accepts open/closed/wont_do/spec/retired) |
| "What labels exist?" | `labels` — each with its usage |
| "Close task 12" / "reopen it" | `close` (refused if stale / spec / wont_do / retired) / `reopen` |
| "Mark task 12 won't-do" | `wont_do` (production/meta only) with durable reason |
| "Retire design slice D17" | `retire` (design/schema only) with durable reason + open-neighbor refusal |
| "Reclassify task 12 to design" | `reclassify` — auto-shifts status open↔spec |
| "I reviewed task 9 — still fine" | `reconcile` (clears stale, no cascade) |
| "Bulk-import a plan from this file" | `load` (atomic; per-edge `because` required on every depends_on entry) |
| "Write up the design-labelled tasks" | `render` — markdown narrative |
| "Board view of the in-flight work" | `board` — rich slice-per-card view |
| "When did task 12 change status?" | `history` — status transitions + description revisions audit |

(The same verbs are available as `tackit <verb>` on the CLI — see below.)

## Bringing in an existing project

If your tracking already lives in a sprawling plan doc, scattered TODOs, or a 4000-line
file you dread re-reading, you migrate it into tackit with `tackit load`:

1. **The agent reads the source** — in sections, if it's too big to hold at once.
2. **It slices it into tasks** — what's a right-sized task, what depends on what. *This
   is the judgment, and it's the actual work* — the tool can't do it for you. A clean,
   structured doc converts almost mechanically; a messy one takes real reading.
3. **It writes one plan file** — a compact `[key] Name` + fields format (far smaller
   than the source, so you can review it before committing). `kind` is required on
   every row, and every `depends_on` entry needs a real `because` rationale per edge
   (D33 placeholder-rationale refusal):

   ```
   [redis-session] Add a Redis-backed session store
     kind: production
     labels: auth

   [rate-limit] Rate-limit the login endpoint
     kind: production
     desc: token bucket, per-IP
     labels: auth
     depends_on:
       redis-session :: rate-limit uses the session store for per-IP counters
   ```
4. **`tackit load plan.txt`** — creates everything in one atomic pass, resolving
   `depends_on` by key. A malformed line, missing `kind`, empty `because`, or unknown
   dep key fails loud and rolls back the *whole* import — never a half-loaded plan.
   Design/schema rows land at status='spec' by default (per the partition);
   production/meta at status='open'.
5. **One collapse pass** — review the labels the import created (`load` reports them) and
   merge near-duplicates. A migration is exactly when label sprawl floods in.

Honest notes:
- **Every project is different.** There's no universal recipe — the threshold for "what's
  a task" is yours, and you'll feel it out as you go.
- **Prefer one plan file.** `depends_on` resolves by key *within a file*; if you split a
  huge project across several `load`s, wire the cross-file links afterward with `link_add`.
- **It's append-mostly.** tackit has no delete; the terminal verbs are `close` (work
  shipped), `wont_do` (production/meta scope dropped), and `retire` (design/schema spec
  100% abandoned). Your undo for a bad import is `restore` from a backup or `import` an
  older `tackit.sql`. Eyeball the plan first.

## Testing

400+ tests: unit, adapter integration (CLI and MCP driven end-to-end), and
**property-based** (Hypothesis stateful testing). The property machine fuzzes
random operation sequences against the always-true invariants — including
**kind/status partition holds** (D36), worklist filter
`status IN ('open', 'spec')` (D28+D36), retired-terminal-no-status-change
(D36+T132 generalized), `links()` anchor excludes retired (T180), version-
monotonic, canonical link pairs (T86), `description_revisions` append-only
(D29), `wont_do_reason` non-null on wont_do rows, and `tackit.sql` round-trip
fidelity. The machine has already caught real bugs: a serialization edge case,
a `reopen()` no-op guard that didn't honor the spec/open partition, and a
`load()` path that hardcoded `status='open'` regardless of kind — exactly the
kind of sibling-code-path miss no upfront plan would have enumerated. From a
clone of the repo:

```bash
pip install -e '.[test]'
pytest
```

## License

[MIT](LICENSE).
