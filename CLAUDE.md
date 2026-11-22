# tackit — project-local instructions

These supplement the global `~/.claude/CLAUDE.md` and the tackit skill. Rules for every tackit user live in the skill; this file holds the rules for maintaining this repo.

## Before a commit that touches a spec, dump the spec layer

Before any commit that touches a design or schema slice, regenerate the spec-only dump and stage it with the source changes:

```bash
tackit export --specs-only > examples/specs.sql
```

`.tackit/tackit.db` is gitignored, so this dump is the only way the spec layer survives a `git clone` (M187). It holds current spec state only: slice bodies, labels, spec-to-spec links and status transitions. It leaves out the edit history, because a revision keeps the prior text of every edit and drafts would leak into the public dump (T240). Production and meta tasks stay private.

Run it by hand before such commits. There is no pre-commit hook, because intermediate drafts of a slice don't need to land in git.

## The workflow commands — the repo is the master copy

Edit `src/tackit/data/workflow/`, then copy to `~/.claude`. An edit made only in `~/.claude` drifts and never ships. Everything in that folder is public: keep private project names and vocabulary out.
