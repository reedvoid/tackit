# tackit

**A deterministic task + dependency tracker built for coding agents** — with a CLI
for humans too.

One local SQLite file is the single source of truth for a project's build plan: its
tasks, their dependencies, and their reconciliation state. The agent fetches small
*slices* on demand instead of re-reading monolithic plan documents, so project truth
survives across sessions and context-window compaction, and a change to one task is
traceable to everything that depends on it. A typed boundary refuses malformed data,
and a reconcile-on-change discipline surfaces what each change invalidates.

> **Status: alpha (0.9.4).** The data model, interfaces, and sync design are settled
> and implemented, with 1,000+ tests. Expect rough edges.
>
> **Upgrading to 0.9.4: JSON results changed.** Writes return less, on MCP and in CLI
> `--json`, and `edit_append` no longer flags neighbors. Anything that reads a `close`,
> `add`, `edit` or `stale` result needs checking; `show` returns what they used to
> carry. The store format is unchanged.

**Contents:** [What it does](#what-it-does) · [Install](#install) · [Using it](#using-tackit-through-your-agent) · [For agents](#for-agents-the-rules-in-brief) · [MCP tools](#mcp-tools) · [CLI](#for-humans-the-cli) · [Migrating a project](#bringing-in-an-existing-project) · [Testing](#testing)

## What it does

Coding agents lose the plot across long projects: tracking scatters across files that
drift apart, decisions vanish when the context window compacts, large plan documents
get re-read every session, and dependencies are narrated in prose that goes stale.
tackit's job is narrowly *correctness on the dev path*, not knowledge retrieval:

- **One home per fact.** Every view (board, status, rendered narrative) is derived,
  never hand-kept in parallel, so two copies can't disagree.
- **Small slices.** The unit of access is a task plus its direct links, so context
  cost stays bounded.
- **Fail loud.** Malformed data and illegal transitions are refused at the boundary.
- **Traceable change.** Editing a task flags its directly linked neighbors stale for
  review (one hop, both directions), so a change can't silently leave the plan wrong.

**The model.** One item type, a **task**, with a required `kind` that fixes which
`status` values it can carry: design and schema slices (the decisions) live at
`spec` / `retired`; production and meta tasks (the work and the thinking) live at
`open` / `closed` / `wont_do`. One relationship, a **symmetric link** with a
`because` rationale. Freeform labels. Every view is derived from these.

## Install

Two parts: a human pulls the package, then **hands off to the coding agent**, which
wires it in. `pip install` alone does **not** make tackit usable by your agent.

**1. Human — install the package and tell your agent to set it up:**

```bash
pip install tackit
```

Then paste this to your coding agent (Claude Code, etc.):

> I've installed the `tackit` Python package. Run `tackit setup` and carry out the
> steps it prints to register it (MCP server + skill) for this project.

**2. Agent — register it.** `tackit setup` *prints* the post-install steps and edits
no config itself: the MCP registration command for the agent's own config, where to
drop the bundled `SKILL.md`, the always-on settings below, and `tackit init`. The agent
carries them out, because it knows where its own config lives.

### Load the skill at session start

`SKILL.md` is the complete discipline. Load it at the **start of every session**
instead of relying on the agent to invoke the `tackit` skill when it recognizes task
work. tackit ships no hook of its own; pick your agent's lever.

**Claude Code** — a `SessionStart` hook in `.claude/settings.json`. Its command's
stdout is injected into context before the first prompt:

```json
{
  "hooks": {
    "SessionStart": [
      { "matcher": "startup|resume|clear|compact",
        "hooks": [ { "type": "command",
          "command": "echo 'This project uses tackit. Before any task work, invoke the tackit skill to load its discipline.'" } ] }
    ]
  }
}
```

Keep the `compact` matcher — it re-fires after a context compaction, where the loaded
skill is otherwise the first thing dropped. The hook prints a one-line directive and
the agent loads the skill itself: hook output over 10,000 characters reaches the agent
as a 2,000-character preview, and the skill is longer than that.

**Codex CLI** — the same hook model, in `.codex/config.toml` (or `.codex/hooks.json`,
whose JSON mirrors Claude's block above):

```toml
[[hooks.SessionStart]]
matcher = "startup|resume|clear|compact"
[[hooks.SessionStart.hooks]]
type = "command"
command = "echo 'This project uses tackit. Before any task work, invoke the tackit skill to load its discipline.'"
```

**opencode** and **Gemini CLI** have no session-start hook: put the always-on snippet
below in `AGENTS.md` (read by Codex and opencode) or `GEMINI.md` (sent with every
Gemini prompt).

### Configure your agent's settings

Add this floor to your agent's *always-on* instructions (`CLAUDE.md` for Claude Code,
or the equivalent), so it holds even when the skill isn't loaded:

```markdown
## tackit
- tackit is this project's single source of truth for tasks + dependencies. If it
  isn't in tackit, it isn't tracked. It is not a knowledge base.
- Importing many tasks at once? Use `load`, not N `add()` calls: one atomic import
  with full descriptions, labels, and `depends_on` edges with `because` rationales.
- Code ↔ task traceability is MANDATORY: cite the task id (e.g. `T42`) in the code
  and reuse the task's distinctive vocabulary in file/function names and comments.
  A vague task title or a code↔task vocabulary mismatch is a defect.
- Search before creating; use `links` to find candidate connections; wire every
  dependency explicitly with a real `because`; right-size tasks to describable units.
- Links are for coupling ("edit one ⇒ re-check the other"), labels for membership.
  Never create a hub task or a rollup task; a relationship goes on a link, not in prose.
- Reconcile on change: a change marks linked neighbors stale (an `edit_append` note
  does not). Terminal tasks are never flagged, so every flag is an obligation. Review
  each against its neighbors, then `edit` or `reconcile`; clear a reviewed-clean set
  with `reconcile(ids=[...])`. There is no "reconcile all". Never end a turn with stale
  work outstanding.
- Reuse labels before creating new ones (run `labels` first).
- Fold-back is mandatory: a decision → its design/schema slice (never a production
  body); a correction of the current production task → rewrite its body; progress →
  git. Every turn with a code change names where each fold-back went, or says none.
- After any task change, report verb-grouped (Added/Edited/Closed): id + name, a
  `what:` and a `did:` line, then the state (N open/done/stale), worries first.
```

### Optional: the workflow commands

tackit ships the slash commands its maintainer works with: `/diagnose` proves a bug's
root cause, `/firmup` settles a plan, `/graduate` turns it into specs and tasks, `/build`
builds them test-first, and `/orient`, `/handoff` and `/trim` carry state between
sessions. The `test-audit` skill comes with them. They are opinionated and Claude Code
only. To install them, tell your agent:

> Run `tackit setup` and carry out the optional workflow-commands step.

### Readable git diffs

The store lives at `.tackit/tackit.db` (gitignored); its committed form is a
deterministic, gzip-compressed SQL dump, `.tackit/tackit.sql.gz`, re-written on every
change. For readable diffs, add `.tackit/tackit.sql.gz diff=gzip-text` to
`.gitattributes` and run `git config diff.gzip-text.textconv "gzip -dc"` once per clone.

## Using tackit (through your agent)

Day to day you tell your coding agent what you want in plain language and it drives
the tools. After any change it reports what it did and what's outstanding.

| Ask your agent… | tackit does |
|---|---|
| "Add task X (linked to Y, label Z)" | `add` with `deps={Y: "<because>"}` + `label_add` |
| "Find the task about the FTS query" | `search` (ranked keyword) |
| "Show me task 12 and what it touches" | `show` — full task + labels + lean linked tasks; `because` + `last_edit_delta` where reconciliation needs them, or on every link with `include_neighbor_because=True` |
| "Update task 12's description" | `edit` — and stales its linked tasks (cascade) |
| "Task 12 is linked to task 7" / "remove that link" | `link_add` (with `because`) / `link_rm` |
| "Wire up these N links between existing tasks" | `links_add` — atomic batch of `{a, b, because}` edges |
| "What can I link to?" | `links` — anchor layer (design+schema) or depth-1 expansion |
| "Tag task 12 `smoke-test`" / "untag it" | `label_add` / `label_rm` |
| "What's open / closed / stale?" | `ls` / `stale` / `summary` |
| "What labels exist?" | `labels` — each with its usage |
| "Close task 12" / "reopen it" | `close` (refused if stale / spec / wont_do / retired) / `reopen` (corrects a closed record; new work is a new task) |
| "Mark task 12 won't-do" | `wont_do` (production/meta only; also on a closed task) with durable reason |
| "Retire design slice D17" | `retire` (design/schema only) with durable reason + open-neighbor refusal |
| "Reclassify task 12 to design" | `reclassify` — auto-shifts status open↔spec |
| "I reviewed task 9 — still fine" | `reconcile` (clears stale, no cascade) |
| "Bulk-import a plan from this file" | `load` (atomic; per-edge `because` required) |
| "Write up the design-labelled tasks" | `render` — markdown narrative |
| "Board view of the in-flight work" | `board` — slice-per-card view, lean by default |
| "When did task 12 change status?" | `history` — status transitions + description revisions |

## For agents: the rules in brief

The **complete, canonical discipline ships as `SKILL.md`**, and each tool states your
obligations in its own results and refusals. Rely on those at work time; this is the
orientation.

- **Fail loud — never degrade silently.** When something fails, diagnose and fix the
  root cause; surface it with recovery options, never produce "degraded output".
  Without this, the worklist and close-gate are only refusable suggestions.
- **Fix broken things first.** On entering a session, scan for inherited broken state
  (unshipped fixes, deferred notes, open items a prior session named as next). That is
  the next task; "what's next?" with a deferred fix in scope does not mean
  "enumerate the backlog".
- **Single source of truth.** Tasks, dependencies and decisions go in tackit, never in
  ad-hoc markdown or TODO comments.
- **Reconcile on change.** The worklist is stale tasks with `status IN ('open',
  'spec')`, and closed, wont_do and retired tasks are never flagged: a change flags
  only live neighbors. Never end a turn while the worklist is non-empty.
- **Kinds decide verbs.** A spec is *what should be true*, a production task *the
  change between spec and code*, meta *the thinking*. A production task must link the
  spec it realizes (`add` and `load` refuse it otherwise). `edit` any partial change;
  `retire` a spec only when it is 100% gone; `close` or `wont_do` production and meta
  work. Edits are refused on closed and wont_do tasks: `reopen` a closed task to fix it.
- **Granular-description discipline.** Task bodies are implementation-ready: a
  fresh-session agent with only the body and its linked neighbors can build it. Vague
  verbs ("fix bug"), conversation references, pointer-only bodies and TBD/TODO
  placeholders are defects.
- **Fold back by type.** A decision goes to its spec slice, a correction rewrites the
  current production task, progress goes to git.
- **Code ↔ task traceability.** Nothing connects code to the task it came from except
  how you write both: distinctive task names, the task id and vocabulary in the code.

## MCP tools

```bash
tackit mcp     # serve the stdio MCP server (the agent's primary door)
```

The harness pushes typed tool schemas into the agent's tool zone, generated from the
Python type hints so they can't drift. Tool names are the bare verbs: `add`, `show`,
`search`, `edit`, `edit_append`, `edit_replace_substring`, `close`, `reopen`,
`reconcile`, `wont_do`, `retire`, `reclassify`, `links`, `link_add`, `link_rm`,
`links_add`, `label_add`, `label_rm`, `ls`, `board`, `summary`, `stale`, `labels`,
`render`, `history`, `load`. `edit_append` (meta-only, flags no neighbor) and
`edit_replace_substring` send only the change instead of the whole body.

Reads are lean: `ls` and `board` return no bodies unless asked
(`include_description=True`). `show` returns the full task with lean links, keeping
`because` / `last_edit_delta` only on open/spec stale reads;
`include_neighbor_because=True` returns them on every link. Every result is a compact
JSON envelope: `result` always, plus each of `stale_alert`, `label_nudge`,
`coherence_nudge`, `deadref_suggestions` and `missing_edge_suggestions` only when it is
set. Refusals come back as errors that state the reason and the resolve path.

## For humans: the CLI

The CLI is the human door: debugging, scripting, and a fallback for the agent.
`tackit --help` (and `tackit <cmd> --help`) is the full surface. `--json` prints the
same result the MCP tool returns; alerts and nudges go to stderr.

```bash
tackit init                                                              # create .tackit/ in this project
tackit add "FTS5 search query contract" --kind design --label search     # a design slice (D1) — the decision to realize
tackit add "parse FTS5 query" --kind production --label search \
  --dep 1::"parser realizes the FTS5 query contract"                      # production links the spec it realizes
tackit search "fts"                                                      # ranked keyword search
tackit show 2                                                            # slice: task + linked tasks + labels
tackit edit 1 --desc "tokenized MATCH contract" \
  --delta "switched from prefix to tokenized query"                      # edits the spec — marks linked tasks stale
tackit stale                                                             # the reconciliation worklist
tackit reconcile 2                                                       # reviewed-OK; clear stale
tackit close 2                                                           # refused while stale (or atop stale deps)
tackit wont-do 2 --reason "redundant with T3"                            # decided NOT to do (production/meta)
tackit retire 1 --reason "premise replaced by D99"                       # design/schema spec 100% abandoned
tackit ls --status open                                                  # query; --status accepts open/closed/wont_do/spec/retired
```

Sync between the db and the committed dump is automatic. `tackit status` / `export` /
`import` / `restore` exist only for the divergence cases auto-sync refuses to guess at.

## Bringing in an existing project

Migrate a sprawling plan doc or scattered TODOs with `tackit load`. The agent reads
the source, slices it into right-sized tasks (the judgment, and the actual work), and
writes one plan file. `kind` is required on every row, every `depends_on` edge needs a
real `because`, and every `production` row must link the design/schema slice it
realizes:

```
[auth-throttle] Redis-backed sessions + per-IP login rate-limiting
  kind: design
  desc: sessions live in Redis; the login endpoint is throttled per-IP off that store
  labels: auth

[rate-limit] Rate-limit the login endpoint
  kind: production
  desc: token bucket, per-IP
  labels: auth
  depends_on:
    auth-throttle :: realizes the per-IP throttling half of the decision
```

`tackit load plan.txt` creates everything in one atomic pass; any bad row rolls back
the whole import. Then review the labels it reports and merge near-duplicates. tackit
has no delete: the undo for a bad import is `restore` from a backup, so read the plan
first.

## Testing

Unit, adapter integration (CLI and MCP end-to-end) and Hypothesis stateful property
tests, which fuzz operation sequences against the store's invariants. From a clone:

```bash
pip install -e '.[test]'
pytest
```

## License

[MIT](LICENSE).
