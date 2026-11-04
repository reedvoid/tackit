---
description: Build a graduated meta's production tasks as committed, verified TDD increments — uncertain piece first, mandatory test-audit per non-mechanical increment, liberal unit + full integration and a rule matrix at meta-end, drive to a runnable end state (context-budget-gated), billed evals user-gated. Executes what /graduate carved.
argument-hint: <meta or production task id> [optional: which task / phase / emphasis]
---
⛔ PRECONDITION — the work must be GRADUATED (run /graduate first). Verify the target has: production task(s) with impl-ready bodies (tackit D37 — buildable from the body + linked slices alone), ≥1 design/schema slice link each (tackit D256), and a build order. If any body is pointer-only / TBD, or a design-grade decision is still open, STOP and say so — building from an unsettled plan bakes the wrong thing into code that is expensive to unwind. Do NOT invent decisions /graduate didn't carve.

The project's concrete commands (unit / integration / image rebuild / deploy gate / billed-eval run pattern / files never committed) live in its CLAUDE.md; this command names the step, the project file names the command.

Target (+ any emphasis): $ARGUMENTS

1. ORDER — build the UNCERTAIN / load-bearing piece FIRST and PROVE it before building on it (unverified work must not cross a boundary; a piece built on an unproven one takes the stack down). The LLM-uncertain piece is built LAST and does NOT close on hermetic green — its carved BILLED eval gates it (step 7). Read each production task body + its linked slice(s) FIRST-HAND before touching code; re-verify every `file:line` the plan flagged "re-verify at build" — a task body OVER-CLAIMS blast radius, so the plan is a lead and the code is the authority.

2. TDD EACH INCREMENT — RED before GREEN.
   - Before writing code, NAME the slice rule each new behavior implements. A behavior no rule covers (a new lockout, a new message, new UI text) is a question for me: ask, don't build.
   - Write the failing test, watch it fail for the RIGHT reason, then the implementation, then GREEN. The RED run covers test-audit's Pass 0 for a NEW single-target test only. A test that loops over a set (authz sweeps, registry/guard scans, golden key-sets) must ALSO assert the set is non-empty and contains the real target, proven with one mutant (break one real member → RED). A test the change makes vacuous (it asserts on data or text that no longer exists) is a REWRITE — the firm-up impact sweep should have listed it.
   - A UI / API gate is backend-enforced AND direct-API tested on EVERY write path to the gated state (FE-disabled ≠ backend-open).

2a. TEST-AUDIT — MANDATORY on every non-mechanical increment, run in-session; scope, exemption and routing are defined once in the `test-audit` skill ("When this audit is required"). Each finding is a FIX (real bug → change the code, resolve first) or a TEST (pin correct behavior). The increment's report carries the one `test-audit:` line that skill defines.

3. TEST CADENCE — LIBERAL UNIT, FULL INTEGRATION AT META-END.
   - Run the fast UNIT suite liberally — every increment, and MANDATORY on any prompt / LLM-call change.
   - Reserve the FULL integration suite for the END of the meta's build, BEFORE any billed eval — EXCEPT: if a genuinely-uncertain load-bearing task has dependents, run integration ONCE to prove it before building on it (step 1). Rebuild the test image FIRST after any app-code / migration change when tests run against a built image — a stale image is a SILENT false pass/fail.
   - Run the LOCAL DEPLOY GATE (the e2e run against the deployed build) after any migration, constraint or fixture-affecting change — not only at the end; a new constraint breaks fixture replay that the integration suite never exercises.
   - Committed increments make an end-suite red git-bisectable.

3a. META-END RULE MATRIX — with the full integration run, before any billed eval: every rule of the slice(s) the meta realizes → the `file:line` that enforces it + the test that pins it, the VIOLATION direction included. A rule with no enforcing line or no pinning test is a finding (fix or test). This matrix IS the meta-end test-audit's Pass 1 — not a second pass.

4. A NEW TEST THAT BREAKS EXISTING TESTS is a DISAGREEMENT, not a verdict — it proves two artifacts conflict, not which is wrong. Find the AUTHORITY (the settled slice, a ruling from me, real production behavior) and align everything to it: conform the past + update the spec, OR fix the new code — never quiet whichever is NEAREST or "less important." Fail loud: if a required check can't run, say what failed + what I must do as the FIRST thing in the response, before proceeding.
   - A bug found mid-build is one MEMBER of a class (the same query shape, the same rule, the same missing check). Enumerate the class with a names-only search across every spelling, list the members in the report, and fix or file each in the same increment (filed with the project's fix-provenance convention, if it has one).

5. COMMIT the increment — explicit paths, NEVER `git add -A` (the project CLAUDE.md lists the files that are never mine to commit). Verified + committed before the next piece — never carry unverified / uncommitted work across a boundary. Deploy a requested app change through the project's deploy script (never a bare container restart); FE / visual work = LOOK in a real browser, not code-blind.

6. DRIVE TO A RUNNABLE END STATE — components are NOT a feature; the ORCHESTRATION WIRING that assembles them into a working flow is itself a task. Build it in the SAME pass — the ONLY thing that may defer it is CONTEXT BUDGET. ⭐ I cannot read my own context window; PROMPT me to run `/context` at /build START and at every pause, and gate the cutoff on the MEASURED number I paste, NEVER your own spend estimate (yours runs ~3× conservative — bias toward continuing). Default: keep building while the last reading is < ~85% used; the remaining ~15% (less the ~3% auto-compact buffer) is the handoff reserve. On a budget stop → flag the keystone-next LOUDLY + run /handoff (every increment is already committed, so nothing is lost). NEVER report "done" over unwired components.

7. BILLED EVAL SHIP-GATE (for any load-bearing model-behavior claim) — runs ONLY on my explicit go, EACH time. BEFORE any spend: run test-audit's Pass 5 (`references/llm-eval.md`) over the harness — mandatory. Make sure the code under test is the CURRENT code (a running container built before the last app-code commit measures the OLD behavior; the project CLAUDE.md has the run pattern). Then: a $0 scorer-can-fail self-test FIRST, a canary-first hard `--budget` cap, run DETACHED (a foreground timeout BILLS and loses output), a leak guard (probe ≠ the prompt few-shot, gold never in the payload), grade PER-CASE and read the RAW emissions (never the blended rate). A build does NOT close on hermetic green when this gate exists — that is exactly what the carved eval catches.

8. CLOSE + FOLD BACK + REPORT — a task isn't done until its fix is committed + verified AND the task is closed.
   - Make the task's SLICE FOLD-BACK at ship (amend the big slice the graduate plan deferred to ship-time; clear any DRY duplication the build introduced).
   - If the increment MOVED or RENAMED code, correct now the still-open task bodies that cite it (a correction edit).
   - A spec contradiction found while building is fixed this session, or filed as a task naming the decision needed — never carried only as a NEXT bullet.
   - Log a write-once dated run entry per the project's run-log rule in its CLAUDE.md; with no such rule, only for a run that DISCOVERED something. Report task changes verb-grouped (Added / Edited / Closed) — per task the id + name, a `what:` line and a `did:` line — closing with the state (N open / done / stale) and any worry UP FRONT.
