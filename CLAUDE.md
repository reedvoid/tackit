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

**Trigger discipline:** manual command before commits that touch
specs, not a pre-commit hook by default. The maintainer's discretion
is the right granularity — intermediate brainstorm states (a spec
edited 5 times before settling) don't need to land as git churn.
Pre-commit hook and CI-check are available patterns for the
paranoid; opt-in per contributor.

**Pending impl (2026-06-03):** the `tackit export --specs-only`
subcommand is filed for impl as **T193** and not yet shipped. Until
it lands this rule is DORMANT — the next commit that touches a spec
slice IS the ship-on-pain trigger to ship T193 first. Do NOT skip
the rule with a verbal "I'll do it after T193 ships." Per
ship-on-pain (SKILL.md + global CLAUDE.md): an unshipped fix that's
blocking discipline IS the next task. Ship T193, then commit.
