---
description: Start-of-session orientation — read the two-zone memory (active core + index), the latest session file(s), tackit, and the pointed-to specs/code/runlogs, report what's next, and close with a plain-terms big-picture recap. STRICTLY READ-ONLY.
argument-hint: [optional: any emphasis for this run]
---
⛔ DO NOT ACT UNILATERALLY. This is READ-ONLY: orient yourself and report back — take NO action (no edits, no builds, no commits, no task changes, no running billed things) until I EXPLICITLY tell you to proceed. Even if you're confident you know the next step, STOP and report it — do not start it. "Figuring out what's next" is the whole job here; doing it is a separate command.

Get oriented:
1. Read MEMORY.md (two zones — the layout is defined in /trim):
   - ZONE 1 — the top `# ⬅ ACTIVE` core: read IN FULL — the `⭐ NEXT` block(s), the `## ⚠ HAZARDS` lines (each overrides any general rule it contradicts; report them first) + the one-line DO-NOT-RE-DERIVE rules. ⚠ `⭐ NEXT` may hold SEVERAL `### [session: tag]` sub-blocks left by concurrent sessions — read ALL of them.
   - ZONE 2 — the index buckets: a MENU of one-line pointers, NOT content. ⛔ Follow a Zone-2 (or `[[…]]`) pointer ONLY when the current next task needs that file (decided in step 2); a pointer the task doesn't touch stays UNREAD — "read everything linked" is a bug.
   - ⭐ THE MAP is the per-session `session-YYYY-MM-DD-<slug>.md` file(s) /handoff wrote: read the MOST-RECENT one(s) pointed to from `🗄 RECENT SESSIONS` (the latest, plus any newer than your last orient or that a `### [session: …]` NEXT block references) — what it DID, PROVENANCE, ORIENTATION, and its LEFT UNVERIFIED / UNBUILT list.
   - Load the tracker (the tackit skill; find the open / next tasks).
   - ⚠ Check MEMORY.md against the budget /trim defines, or note that the last handoff logged a bloat alert. Over → NOTE it and recommend a SOLO /trim. Do NOT trim (read-only).
     MEMORY.md BUDGET: Zone 1 (above the index heading) ≤ 40 lines and ≤ 8 KB; the whole file ≤ 120 lines and ≤ 12 KB; every Zone 2 line ≤ 300 bytes. Measure with `wc -lc`, not by eye.
2. First BUILD THE READING LIST, then read every item on it IN FULL — this is the step I keep under-doing, so the list is explicit and each item gets checked off. A 40-line window around a cited line is NOT reading the file; grep is not reading. From the immediate next task(s) plus a layer or two UP (drawing on the session file + the Zone-2 pointers as your menu), enumerate and read:
   - every design/schema slice it links (D#/S#) — the WHOLE slice, not just the one rule referenced;
   - every memory topic-file it links (`[[…]]`) or that a Zone-2 pointer names as relevant;
   - every code touchpoint — the WHOLE function/section it sits in, plus the callers/callees the design leans on — NEVER just a ±20-line window around the cited line;
   - the run-logs, fixtures, and test scaffolding named as covering it.
   ⚠ Reading a window to CONFIRM a `file:line` is a DIFFERENT act from reading a file to UNDERSTAND the task — do the second, not just the first. Read THROUGH; never keyword-skim.
3. Report back, precisely and concisely:
   - the reading list — the Zone-1 core, the session file(s), and the specs / code / run-logs / topic-files you actually opened IN FULL (so a skipped one is VISIBLE); flag anything you could not read, or chose to skip, and why. This makes a bounded sweep impossible to pass off as full orientation.
   - what's next — the immediate task, plus a layer or two UP the stack (why it's next / what it serves). ⚠ If `⭐ NEXT` held MULTIPLE per-session blocks, say which is the real immediate next and whether they need merging (a job for me or a solo /trim, not for you).
   - the GAP SCAN of the immediate next task — step 2's reading is the evidence; report each check's hits, or "none found" per check:
     (a) the task body against the code you read (a claim, path or `file:line` that no longer holds; a sibling entry point the body misses);
     (b) rules that touch the same request or state but were never traced together;
     (c) the firm-up COVERAGE MATRIX still holds (every changed component → a verifying task);
     (d) for a test/eval task: Pass 5's one-factor-per-arm rule holds and every seam's inputs are known (test-audit `references/llm-eval.md`);
     (e) other open task bodies that cite code which has since moved.
     Findings are REPORTED only; they go into the tracker after my go.
   - anything inherited-but-broken to fix first (fold-back notes, the last session's LEFT UNVERIFIED / UNBUILT list, stale/open items a prior session flagged as "next"); an open user call marked "ask now" goes in the decisions below.
   - what you'd do first, and any decision you'd need from me before starting. A choice I already leaned on or reserved goes here as a question, never in a list of defaults (check the record for what I said).
4. FINALLY, close the report with a short **BIG PICTURE** recap — the last thing I read, set off under its own heading. This is NOT a restatement of the precise report above (that stays as-is, jargon and all); it is the human-readable STORY that reorients me. Write it:
   - in SIMPLE terms — NO code identifiers, D#/S#/§-refs, T# ids, or internal nouns; gloss them the way you'd explain it to someone who wasn't in the room. (If a reader who never opened the code couldn't parse a sentence, rewrite it.)
   - as a short arc, not a list: what we've been working on over the last session or two → how we got to right here (the turns/decisions that led in) → how that connects to the thing we're about to pick up now, and why it's the next move.
   - SUCCINCT — a few plain sentences, not a wall; the point of the paragraph must survive being skimmed.
   The session files' own `## PROVENANCE (how we got here)` and `## ORIENTATION` narratives are the model for altitude and tone — mine them, don't re-derive from scratch.

Then WAIT for my go. Do not proceed past reporting.

↓ Anything below is MY emphasis for this run — honor it as a priority, whatever it is. It does NOT replace the instructions above: still follow all of them (read-only, read memory/session-file/tackit/specs, report + wait). (Empty = just run the routine.)
$ARGUMENTS
