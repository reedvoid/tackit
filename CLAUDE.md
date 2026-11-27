# tackit — project-local instructions

These supplement the global `~/.claude/CLAUDE.md` and the tackit skill. Rules for every tackit user live in the skill; this file holds the rules for maintaining this repo.

## Before a commit that touches a spec, dump the spec layer

Before any commit that touches a design or schema slice, regenerate the spec-only dump and stage it with the source changes:

```bash
tackit export --specs-only > examples/specs.sql
```

`.tackit/tackit.db` is gitignored, so this dump is the only way the spec layer survives a `git clone` (M187). It holds current spec state only: slice bodies, labels, spec-to-spec links and status transitions. It leaves out the edit history, because a revision keeps the prior text of every edit and drafts would leak into the public dump (T240). Production and meta tasks stay private.

Run it by hand before such commits. There is no pre-commit hook, because intermediate drafts of a slice don't need to land in git.

## Running the tests

`.venv/bin/python -m pytest -q` runs the whole suite in parallel, about 13s for 1,297 tests. The config sets `-n 6 --dist worksteal`: 6 workers is the measured knee on this 20-core machine, since 4 to 12 all finish in 11-13s, bounded by the ~10s Hypothesis state machine that `tests/conftest.py` schedules first. The conftest also puts pytest's temp root on `/dev/shm` when it exists, because on disk the suite waits on I/O: 658s serial on disk, 31.5s serial in RAM (M376). Pass `-n 0` for a serial one-file debug run. Planted-mutant copies pass their own `-n` share and a `/dev/shm` `--basetemp`, per the mutant procedure in /firmup, /build and test-audit.

## The workflow commands — the repo is the master copy

Edit `src/tackit/data/workflow/`, then copy to `~/.claude`. An edit made only in `~/.claude` drifts and never ships. Everything in that folder is public: keep private project names and vocabulary out.
