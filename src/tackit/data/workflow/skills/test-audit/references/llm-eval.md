# Test-audit — LLM behavioral evals (Pass 5)

Load this from `../SKILL.md` when the thing under test is a non-deterministic model *judgment* scored by a metric + a ground truth you built (a grader, classifier, router, extractor). **Pass 0 lives in `SKILL.md` and applies here too.**

## Pass 5 — LLM behavioral-eval correctness
Add when the thing under test is a non-deterministic model *judgment* scored by a
metric + a ground truth you built, not a deterministic oracle. Here the bug is usually
in the **grader, the ground truth, or the prompt** — not the code under test — and it
never shows in the aggregate rate. The rate is a claim; the raw outputs are the evidence.

- **The prompt must never contain the answer — the leak that INFLATES, the deadliest bug
  here.** Every other failure on this list pushes the number DOWN and reads as "needs
  work"; a leak pushes it UP and reads as SUCCESS — so you ship a capability the model
  does not have. A few-shot example that IS a test input, an instruction that NAMES the
  target, any surrounding context that hints the expected answer — *any* overlap between
  what the model sees and what it is supposed to figure out, verbatim OR by meaning, voids
  the WHOLE run, not a few samples. Reading the outputs will NOT catch this (a leaked run
  looks correct); the tells are a suspiciously clean rate on a hard task, and the check is
  to DIFF the prompt/context against your test inputs and targets. It disguises itself as
  "helpful" prompt engineering. Teach the mechanic with content-free stand-ins that
  coincide with no test input or target; the same binds the production prompt the eval
  reproduces.
- **ONE factor per arm, and record what each seam was actually fed.** An arm differs from
  its control by exactly one named factor (one setting, one prompt change, one model).
  Moving two at once (e.g. a thinking mode AND an effort level) makes any difference
  unattributable. Store, per case and arm, the inputs AS BUILT — system + user content and
  every setting sent — so a difference can be traced to its cause. A difference not
  attributable to the single varied factor is a CONFOUND: report it as one, never as a finding.
- **Show that each case's INPUT can satisfy its gold — before any spend.** A gold the input
  cannot reach (too little material for the expected label, the fact absent, a count the
  seam's own rules forbid) makes the case measure the gold, not the model. For each case,
  write one line on why the input can satisfy the gold, re-read the gold against the seam's
  own rules, and save per-case grades to a committed log — never only to a scratch directory.
- **Read the raw outputs before trusting the rate.** Every grader/ground-truth bug
  hides in the aggregate and shows in the raw. Persist the full model output per sample;
  read every miss (and a few passes) and re-score offline. If you can't say *why* each
  miss missed, you don't have a result yet.
- **Validate the answer key, not just the score.** A generated ground truth can name the
  wrong answer — a keyword that built the key matches a passing mention in a different
  item, and a model that coincidentally agrees turns the run into a measurement of the
  coincidence. Confirm the key names the actually-correct answer for *this* sample, by
  hand, every run the key is generated.
- **Grade a fuzzy output leniently; a correct answer must not fail on formatting.** An
  exact-string match reds a correct answer the model merely reformatted (a copied list
  marker, bold, whitespace). Normalize the markup away and match by containment in both
  directions, erring toward accepting a faithful answer — a grader that can't *pass* a
  correct output is the mirror of a test that can't fail.
- **Make the metric discriminate; feed it a known failure and watch it go red.** A
  "captured it" check that a content-free or trivially-true output also satisfies is the
  failure dressed as success. State what a genuine pass emits vs what the failure emits,
  and confirm the metric separates them.
