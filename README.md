# tackit

**A deterministic task + dependency tracker built for coding agents** — with a CLI
for humans too.

One local SQLite file is the single source of truth for a project's build plan: its
tasks, their dependencies, and their reconciliation state. The agent fetches small
*slices* on demand instead of re-reading monolithic plan documents, so project truth
survives across sessions and context-window compaction, and a change to one task is
traceable to everything that depends on it. A typed boundary refuses malformed data,
and a reconcile-on-change discipline surfaces what each change invalidates.

> **Status: alpha (0.1.0).** Data model, interfaces, and sync design are settled and
> implemented; 98% test coverage. Expect rough edges.

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
  or sits on unreconciled work, so you can't silently mark broken work complete. (v0.4
  bounds the gate to obligation-bearing stale — closed/wont_do production neighbors
  carrying stale flags as historical record don't trip the gate.)
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
- **Wrap up.** Nothing is "done" while the worklist `stale` (filtered to open work +
  design/schema slices under v0.4) is non-empty; an empty worklist is the only safe
  stopping point. Closed-stale production tasks are *record-only* and don't pressure
  the worklist.

## For humans: the CLI

The CLI is the human door — debugging, scripting, and a fallback for the agent.
`tackit --help` (and `tackit <cmd> --help`) is the full, self-documenting surface.

```bash
tackit init                                   # create .tackit/ in this project
tackit add "parse FTS5 query" --label search  # create a task
tackit add "rank search results" --dep 1      # task 2 depends_on task 1
tackit search "fts"                           # ranked keyword search
tackit show 2                                 # slice: task + deps + dependents
tackit edit 1 --desc "tokenized MATCH"        # marks task 1's dependents stale
tackit stale                                  # the reconciliation worklist
tackit reconcile 2                            # reviewed-OK; clear stale
tackit close 2                                # refused while stale (or atop stale deps)
tackit ls --status open                       # query/board
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

**The model, in one breath.** One item type — a **task** with a `status` (open/closed)
and a `stale` bit; one relationship — `depends_on`, traversed both ways; freeform
labels. Every view (board, status, narrative) is *derived* from these, never hand-kept
in parallel.

**What `SKILL.md` holds you to** (the short version — read it there in full):

- **Single source of truth.** Everything goes in tackit, via its tools — never ad-hoc
  markdown or TODO comments. If it isn't in tackit, it isn't tracked. It is *not* a
  knowledge base; durable learnings live in your memory.
- **Reconcile on change (bounded under v0.4).** A change marks the task's directly
  linked neighbors **stale**. tackit surfaces the outstanding worklist on *every*
  call (deterministically — it's code in the app, not a reminder you can skip).
  The worklist is filtered: only stale tasks with `status='open'` OR `kind in
  {design, schema}` carry an obligation. Closed/wont_do production/meta tasks may
  still carry stale=1 as historical record but they're off the worklist and don't
  pressure the close-gate. Review each obligation-bearing stale task with its
  linked neighbors and `edit` or `reconcile` it. **Never end a turn while the
  worklist is non-empty** — a task left closed atop a changed dependency is wrong
  *and* invisible. (Edits on closed/wont_do tasks are allowed under v0.4 and
  preserved verbatim in the description_revisions audit table.)
- **Find, wire, right-size.** `search` before you create; wire dependencies explicitly
  (including among tasks you add together); keep tasks describable units of work.

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
- Code ↔ task traceability is MANDATORY. When you implement a tackit task, cite its
  id (e.g. `T42`) and reuse the task's distinctive vocabulary in file/function names
  and comments, so a future session can grep from code to intent and back. Treat a
  vague task title or a code↔task vocabulary mismatch as a defect, not a style nit.
- Search before creating; wire dependencies explicitly (including among tasks added
  together); right-size tasks to describable units of work.
- Reconcile on change: a change marks dependents stale. Review each stale task
  against its `depends_on` neighbors, then `edit` or `reconcile`. Never end a turn
  while anything is stale.
- Reuse labels before creating new ones (run `labels` first). A label must earn its
  name — a phase, epic, or use case — never an implementation detail or a one-off.
- After any task change, report back in a scannable, verb-grouped layout
  (Added/Edited/Closed/…): per task show the id + name, then two short lines —
  `what:` (enough to recall it) and `did:` (roughly what changed); end with the state
  (N open/done/stale) and any worry up front (stale ids, refused ops). Not prose, not
  a bare id.
```

### MCP tools

```bash
tackit mcp     # serve the stdio MCP server (the agent's primary door)
```

The agent's primary door is the MCP server: the harness pushes typed tool schemas
into the agent's tool zone (no `--help` round-trip, no shell quoting, can't
hallucinate a flag that doesn't exist). Tool names are the bare verbs — `add`,
`show`, `search`, `edit`, `close`, `reopen`, `reconcile`, `dep_add`, `dep_rm`,
`label_add`, `label_rm`, `ls`, `stale`, `render`, `history`. Input schemas are
generated from the Python type hints, so they can't drift from the real interface.
Every result is wrapped as `{stale_alert, result}` so the outstanding stale set rides
along on every call; refusals (e.g. closing a stale task) come back as errors that
state the reason.

## Examples: the full surface

Everything you can drive through your agent — it maps your request to tackit's verbs:

| Ask your agent… | tackit does |
|---|---|
| "Add task X (depends on Y, label Z)" | `add` + `dep_add` + `label_add` |
| "Find the task about the FTS query" | `search` (ranked keyword) |
| "Show me task 12 and what it touches" | `show` — task + dependencies + dependents + labels |
| "Update task 12's description" | `edit` — and stales its dependents |
| "Task 12 depends on task 7" / "remove that link" | `dep_add` / `dep_rm` |
| "Tag task 12 `smoke-test`" / "untag it" | `label_add` / `label_rm` |
| "What's open / closed / stale?" | `ls` / `stale` |
| "What labels exist?" | `labels` — each with its usage |
| "Close task 12" / "reopen it" | `close` (refused if stale) / `reopen` |
| "I reviewed task 9 — still fine" | `reconcile` (clears stale, no cascade) |
| "Write up the design-labelled tasks" | `render` — markdown narrative |
| "When did task 12 change status?" | `history` |

(The same verbs are available as `tackit <verb>` on the CLI — see below.)

## Bringing in an existing project

If your tracking already lives in a sprawling plan doc, scattered TODOs, or a 4000-line
file you dread re-reading, you migrate it into tackit with `tackit load`:

1. **The agent reads the source** — in sections, if it's too big to hold at once.
2. **It slices it into tasks** — what's a right-sized task, what depends on what. *This
   is the judgment, and it's the actual work* — the tool can't do it for you. A clean,
   structured doc converts almost mechanically; a messy one takes real reading.
3. **It writes one plan file** — a compact `[key] Name` + fields format (far smaller
   than the source, so you can review it before committing):

   ```
   [redis-session] Add a Redis-backed session store
     labels: auth

   [rate-limit] Rate-limit the login endpoint
     desc: token bucket, per-IP
     labels: auth
     depends_on: redis-session
   ```
4. **`tackit load plan.txt`** — creates everything in one atomic pass, resolving
   `depends_on` by key. A malformed line or an unknown key fails loud and rolls back the
   *whole* import — never a half-loaded plan.
5. **One collapse pass** — review the labels the import created (`load` reports them) and
   merge near-duplicates. A migration is exactly when label sprawl floods in.

Honest notes:
- **Every project is different.** There's no universal recipe — the threshold for "what's
  a task" is yours, and you'll feel it out as you go.
- **Prefer one plan file.** `depends_on` resolves by key *within a file*; if you split a
  huge project across several `load`s, wire the cross-file links afterward with `dep_add`.
- **It's append-mostly.** tackit has no delete (only `close`); your undo for a bad import
  is `restore` from a backup or `import` an older `tackit.sql`. Eyeball the plan first.

## Testing

98% line coverage, 110 tests: unit, adapter integration (CLI and MCP driven
end-to-end), and **property-based** (Hypothesis stateful testing). The property tests
fuzz random operation sequences against four invariants — `stale ⇒ open`,
version-monotonic, an acyclic dependency graph, and `tackit.sql` round-trip fidelity —
and have already caught a real serialization bug the example tests missed. From a
clone of the repo:

```bash
pip install -e '.[test]'
pytest
```

## License

[MIT](LICENSE).
