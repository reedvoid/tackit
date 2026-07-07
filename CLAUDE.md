# tackit — project-local instructions

This file holds project-specific rules that supplement (not replace)
the global `~/.claude/CLAUDE.md` and the `tackit` skill's SKILL.md.
Discipline rules that govern *every* tackit usage live in SKILL.md
(loaded automatically when this project is open); this file is for
rules that govern *this repo's maintenance* specifically.

## Before commits that touch a spec — dump the spec layer

Before any commit that touches a design or schema slice (`kind IN
('design','schema')`), regenerate the spec-only SQL dump and stage it
alongside the source changes:

```bash
tackit export --specs-only > examples/specs.sql
```

This is the disaster-recovery artifact (M187 design, locked
2026-06-03). The `.tackit/tackit.db` is gitignored — it's the
maintainer's private working state — so the spec layer would
otherwise be lost on `git clone`. The spec-only dump is the only
part of the dogfood that belongs in the public repo: design slices +
schema slices are durable architecture decisions worth recovering;
production / meta tasks are dev noise.

The dump carries only **current** spec state — slice bodies, labels,
spec-to-spec links, and status_transitions. It does **not** include
`description_revisions` (the edit audit trail): per T240, a revision
preserves the prior-verbatim text of every edit, so transient content
edited out of a slice draft would still leak through the public dump.
The drafting history stays in the private gitignored DB.

**Trigger discipline:** manual command before commits that touch
specs, not a pre-commit hook by default. The maintainer's discretion
is the right granularity — intermediate brainstorm states (a spec
edited 5 times before settling) don't need to land as git churn.
Pre-commit hook and CI-check are available patterns for the
paranoid; opt-in per contributor.

**Status:** the `tackit export --specs-only` subcommand shipped as
**T193** (2026-06-03; refined by T240 to exclude the
`description_revisions` audit trail from the public dump). The rule is
**ACTIVE** — run the dump before every commit that touches a spec
slice.
