---
name: test-audit
description: REQUIRED on every non-mechanical code change (with or without /build), at a meta's end, and before any billed eval spends (see "When this audit is required"); also use when writing or expanding a test suite, or auditing code for correctness and unspecified/edge-case behavior, so coverage is driven by a systematic search for gaps instead of defaulting to happy-path tests. Runs a cross-cutting "every test must be able to fail" check plus four passes — invariant×code×test matrix, degenerate-input enumeration (incl. concurrency + dependency failure), negative-space review, and (optionally) property-based testing — plus a gated pass for non-deterministic LLM behavioral evals (grader / ground-truth / prompt correctness) — so degenerate inputs, races, failures, unspecified behavior, violated invariants, and lying eval metrics are found on purpose, not by luck.
---

# Test-audit: find the bugs happy-path tests miss

Default test-writing drifts toward the happy path plus a couple of obvious errors,
and misses the cases that actually break systems: degenerate inputs, races,
dependency failures, unspecified behavior, and violated invariants. This skill is a
procedure to find those on purpose.

**Drive the test suite FROM this audit — don't write tests first and audit later.**
Each finding becomes either a *fix* (real bug → change the code) or a *test* (correct
behavior → pin it). Run the passes, collect findings, resolve fixes first, then write
the tests that lock in every case.

Pass 0 is cross-cutting — apply it to every test you write. The first three passes are
cheap and catch most real gaps; the fourth is the heavy net. Do passes 1–3 always; add
4 when the state space is rich and the invariants are clear.

## When this audit is REQUIRED — the one home of this rule
- why: the build's own test step has repeatedly let through tests that could not fail, and
  re-running or rewriting a lengthy billed test costs far more than any audit. So this skill is NOT offered — it runs.
- required:
  1. **Every non-mechanical code change, whether or not /build is running** — an ad-hoc fix
     after a "go", a gap closed from an /orient scan and a carved task built directly all count.
     Passes 1–4 (`references/deterministic-code.md`) scoped to THAT increment's NEW surface, not the existing suite (re-auditing all N tests
     each increment is the O(N²) trap; per-increment scope is linear). Order: write the tests
     → audit them → fold the findings in → THEN the first (RED) run.
  2. **Meta-end** — over every test the meta added or rewrote. Its Pass 1 IS the build's
     meta-end rule matrix (every slice rule → enforcing line → pinning test, violation
     direction included); Passes 2–4 only for invariants that are new in this meta.
  3. **Before ANY billed eval spends** — Pass 5 (`references/llm-eval.md`) over the harness,
     again for any harness changed since its last audit (a follow-up run included). Its outcome
     goes in that run's log.
- exempt: a purely mechanical increment (rename / move / delete — no new invariant).
- report: every increment's report AND its task's closing record carry exactly one line, either
  `test-audit: <what it found, or "nothing found">` or `test-audit: exempt — mechanical: <what>`.
  Silence is not an exemption: no line means the audit did not run.
- how: run it IN the session that holds the build's context — that session judges. A file that
  must not be read raw is read through the project's own safe route,
  and a question that needs its real text goes to a separate agent as ONE narrow question. Never
  hand the whole audit to a fresh agent: it pays the orientation again and judges with less.

## Which reference to load
Pass 0 below applies to EVERY test — read it always. Then load the ONE reference for your use case:
- **Deterministic code** (TDD, a bugfix, auditing an existing suite) → read `references/deterministic-code.md` — Passes 1–4 (invariant×code×test matrix, degenerate inputs, negative space, property-based).
- **LLM behavior / prompt-design evals** (a model judgment scored by a metric + ground truth) → read `references/llm-eval.md` — Pass 5 (grader / ground-truth / prompt-leak correctness).
- **Full audit of an inherited suite** → load BOTH references and run every pass.

## Pass 0 — Every test must be able to fail (apply throughout)
A test that cannot fail is worse than no test: it shows green while verifying nothing,
and the false comfort hides the gap. For every test you write, confirm it earns its
place:
- **It can actually fail.** Read the assertion (or mentally break the code): would it
  go red for the right reason? Reject assertions that recompute the implementation
  (`expected = the_same_formula_the_code_uses`) — they only prove the code ran its own
  arithmetic, never that the arithmetic is right. Assert a concrete, independently-known
  value.
- RUNNING PLANTED MUTANTS: give each mutant its own copy of the source and tests in a fresh, uniquely named scratch directory (create it new; never clear an existing one), point the interpreter at that copy and check the imported code resolves into it (Python: PYTHONPATH to the copy's src), give each run its own test temp root and turn off shared caches (pytest: `--basetemp` set inside the copy, and `-p no:cacheprovider`), and put one break in each copy, never several (grouped breaks hide which break failed which test). Launch every independent mutant at once, up to the machine's core count; run them one after another only when one mutant's result decides the next. A sweep question (which existing tests pin this behavior?) runs the full suite per mutant, because a test that builds its target set at runtime escapes a grep; proving one new test can fail runs that test's own file. Report per mutant what was broken and the failing test ids, or "0 failed": no test pins it, so the plan needs a new pin.
- **It reaches the path it names.** Confirm the input actually exercises the
  branch/handler under test, not a layer that short-circuits first (a "rejects bad
  input X" test where the router/parser 404s or 422s *before* X reaches the handler
  tests nothing about the handler). Verify the path is hit, not just the status code.
- **Drive the system through the real entry points; never fabricate unreachable
  state.** Exercise the behavior the way production reaches it — the API/UI/CLI
  handler — not a backdoor. Constructing or mutating state with raw SQL / direct
  store writes that bypass the app's own validation, ordering, and invariants
  manufactures states the system can never produce; a test (or a manual run) over
  an impossible state yields PHANTOM bugs and hides real ones. (For example,
  raw-SQL-deleting rows to shrink a fixture can produce a delete-cascade "bug" that no
  user path could ever trigger.) The line: read-only DB *assertions* are fine, and
  fixtures that seed a genuinely reachable state are fine — but never reach past the
  entry point to build, or test against, data the system's own paths could not have
  created.
- **Keep test artifacts in the test tree — never built into production code.** Test
  scaffolding (seams, hooks, mode flags, fixtures, mocks, brokers, recording shims)
  belongs in the test/ tree, not threaded into the code under test. A flag-gated branch
  advertised as "inert in prod" is *still* production code carrying a test concern: it
  couples the two and invites the flag flipping on. Prefer a seam the runtime already
  exposes — dependency injection, a config/env knob (e.g. a base-URL override), a fake
  at the boundary — over editing the production path; **default to zero production
  diff.** If you believe you genuinely must touch production code for a test, do NOT do
  it silently: **STOP and pause, surface it loudly, and explain** — *what* you want to
  add, *why* it's needed here, the *alternatives* you considered, and *why* the
  production edit beats them — then get explicit sign-off before proceeding.
- **It doesn't mock away the unit under test.** Stub the *dependencies*; never stub the
  function whose behavior you are asserting.
- **Know your oracle.** Pinning *known-correct* behavior is a real test. Pinning
  whatever the code currently emits, correctness unverified, is a *characterization*
  test — useful for catching change, but label it as such, never mistake it for proof,
  and never let it lock in a current bug.

## Output
A short findings list, each tagged **fix** or **test**. Resolve fixes first (and
update the spec for any negative-space decision), then write tests covering every
pass's cases. For each test you add, be able to name *what would make it fail* (the bug
it would catch) — if you can't, it isn't pulling its weight (Pass 0).

Report coverage before/after, and call out any layer still at 0% — adapters/entry-points
(CLI, API handlers, UI) are commonly untested even when the core engine is well covered.
Treat the coverage number with suspicion: a line covered by a tautological or
path-missing assertion is still untested, so high line-coverage with weak assertions is
false comfort, not a finish line.
