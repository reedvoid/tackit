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
[Why it exists](#why-it-exists) · [Install](#install) · [CLI for humans](#for-humans-the-cli) · [Testing](#testing) · [License](#license)

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

Two parts. A human pulls the package; then the **driving agent** wires it in — that
second step is what actually registers the MCP server and the skill. `pip install`
alone does **not** make tackit usable by your agent.

**1. Human — pull the package:**

```bash
pip install tackit
```

**2. Agent — register it:**

```bash
tackit setup     # prints the post-install steps; does NOT edit any config
```

`tackit setup` *emits* contextualized steps for the agent to carry out: register the
stdio MCP server in the agent's own config, drop the bundled `SKILL.md` into the
skills path, and run `tackit init`. The agent does the wiring because it knows where
its own config lives. **🤖 Agents:** also do [Configure your agent's
settings](#configure-your-agents-settings), and read [For agents: start
here](#for-agents-start-here) while you install.

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
- **Reconcile on change.** A change marks the task's dependents **stale**. tackit
  surfaces the outstanding stale set on *every* call (deterministically — it's code in
  the app, not a reminder you can skip). Review each stale task *together with its
  `depends_on` neighbors*, then `edit` or `reconcile` it. **Never end a turn while
  anything is stale** — a task left closed atop a changed dependency is wrong *and*
  invisible. A stale task (or one that depends on a stale task) can't be closed.
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
