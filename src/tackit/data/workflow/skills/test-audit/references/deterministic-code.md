# Test-audit — deterministic code (Passes 1–4)

Load this from `../SKILL.md` when building or auditing deterministic code (TDD, a bugfix, a whole-suite audit). **Pass 0 lives in `SKILL.md` and applies here too — read it first.**

## Pass 1 — Invariant × code × test matrix
Enumerate every *stated* guarantee the system claims — from specs, docs, docstrings,
type constraints, schema CHECKs, comments. For each:
- find the code path that enforces it;
- confirm a test pins it, **including a test on the *violation* path** (that the
  illegal thing is actually refused), not just the happy path.

Anything claimed-but-not-enforced, or enforced-but-not-tested, is a finding.
Invariants people forget to test: monotonic counters that must advance *only* on a
real change; "X implies Y" state couplings; idempotency; ordering/total-order
guarantees; "+1 per mutation"-style semantics; acyclicity; referential integrity;
**cross-user / cross-tenant isolation** (the violation path: user B addressing user
A's resource is refused — ideally indistinguishably from not-found, so the response
can't be used to enumerate what exists).

## Pass 2 — Degenerate-input enumeration
For EVERY operation, mechanically ask the same questions. This is a checklist, not
inspiration — go operation by operation:

1. **No-op / already-in-target-state.** Call it with input equal to the current
   state: re-close a closed thing, set a field to its current value, re-add a
   duplicate edge/label, reconcile something already clean. Does it wrongly mutate,
   bump counters, rewrite outputs, or fire side effects? (This single question is
   where a whole class of real bugs hides.)
2. **Empty.** Empty string, whitespace-only, empty list, zero, null/None.
3. **Duplicate.** The same thing twice — is it idempotent?
4. **Interrupted / partial.** If it crashes mid-operation, is the state still
   consistent? Are obligations persisted *before* the mutation they describe?
5. **Boundary / max.** Limits, rotation/retention caps, off-by-one, very large
   inputs, unicode.
6. **Concurrent / racing.** Run the operation twice *at once*, not just in sequence:
   double-submit, two writers on one row, check-then-act (TOCTOU) where the state can
   change between the check and the use, lock-acquisition order under contention. Does
   a guard that holds serially still hold under overlap? Prefer a deterministic
   interleaving (a barrier/latch) over a sleep, and add a negative control that
   *proves the race is reachable* without the fix.
7. **Dependency failure.** The downstream it calls (DB, network, LLM/API, disk)
   errors, times out, returns malformed data, or writes partially. Does the operation
   fail loud and roll back, or silently half-complete / swallow the error? Are retries
   safe (idempotent), not duplicating effects?

## Pass 3 — Negative-space review
The docs say what SHOULD happen; list the cases they DON'T mention and decide each on
purpose. **Unspecified behavior is where bugs live.** For each operation ask: what
inputs, orderings, or states does the spec simply not address? When you find one,
decide the intended behavior, encode it in the spec (or, if there is no spec, make the
test itself the explicit, durable statement of intent), then test it.

### Sub-lens — obligations & follow-through
For any state that becomes inconsistent-until-someone-acts (a dirty/stale flag, an
unreconciled marker, a pending migration, a diverged file, an unacked alert), check
three things *separately*:
- (a) **Persisted** — does the obligation survive a crash/restart?
- (b) **Surfaced** — is it shown deterministically by the system, vs. relying on
  someone to remember to ask?
- (c) **Enforced** — is it blocked/un-ignorable, or merely advisory?

A gap in any one is a finding. **(b) is the easiest to miss**: persisted-but-not-
surfaced looks fine until a fresh session never sees the outstanding work.

## Pass 4 — Property-based / stateful testing
Generate random valid operation sequences and assert the Pass-1 invariants hold
throughout (e.g. Hypothesis stateful testing). Finds the weird interleavings no human
enumerates. Use when the state space is rich and invariants are clear; skip for
simple/stateless code. Keep it deterministic: pin/seed the generator (and report the
minimal shrunk counterexample) so a failure reproduces instead of flaking.
