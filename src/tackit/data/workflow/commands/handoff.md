---
description: End-of-session handoff — commit your own work, write your session record, add your NEXT to memory, THEN run the full /trim when no other session is active (add-only + a bloat alert when one is).
argument-hint: [optional: any emphasis for this run]
---
Run the end-of-session handoff. This is the WRITE side of the continuity loop — /orient reads back exactly what you record here, so write it for a cold session that has none of this conversation's context.

⭐ ADD-ONLY UNLESS SOLO — the load-bearing rule. Steps 1-3 may ONLY add, or edit content THIS session created; they never delete, rewrite or trim content another (or a past) session wrote. WHY: MEMORY.md is loaded into context at each session's START, so your in-context copy is STALE the moment a concurrent session writes; a rewrite/trim built from your stale copy would silently clobber its work. Only add-only + re-read-before-write composes safely across simultaneous handoffs. Step 4 runs the destructive /trim ONLY after confirming no other session is active (user ruling: "trim and handoff" are always run together, so /handoff includes the trim — guarded, never unconditional).

1. RECONCILE + COMMIT your OWN work (never another session's):
   - Clear stale flags only on tasks YOU touched; close / wont_do only what YOU resolved. Do not reconcile or alter a concurrent session's in-flight tasks.
   - `git commit` your OWN changed files BY EXPLICIT PATH — never `git add -A`/`git commit -a` (that would sweep a concurrent session's uncommitted work into your commit). The tackit store persists on every op, so the shared `.tackit` snapshot already holds both sessions' changes and git serializes commits via its lock.

2. WRITE YOUR SESSION RECORD to its OWN dated file in the project memory directory — NOT into MEMORY.md's body:
   - Create `session-YYYY-MM-DD-<slug>.md` (slug = this session's focus) holding the BULK of the handoff: what you DID (shipped + verified — commit hashes / task ids), PROVENANCE (how you got here + the ORIGINAL trigger), and the ORIENTATION detail (specs, tasks, code touchpoints, run-logs/fixtures, conventions a cold session must read). Per-session file ⇒ two sessions never write the same file.
   - It ends with a fixed `## LEFT UNVERIFIED / UNBUILT` list (in place of free-form cautions): each item = what, why it is open, and its OPEN tracker id. Something a later session must act on gets a task now; a note on a closed task or a NEXT bullet does not count. An item with no task states why no action is needed. An empty list reads NONE. A caution with no open action (a hazard to remember) goes in a `## HAZARDS` list beside it, one line each. A hazard a cold session could trip right now (e.g. a deploy that must wait) is also copied to MEMORY.md in step 3.

3. RE-READ MEMORY.md FROM DISK (do NOT trust your in-context copy — a concurrent session may have just written it), then make ONLY these ADDITIVE edits (append / your-own-lines-only; never a full-file rewrite):
   Every addition fits the shape /trim defines (/trim enforces it on old content):

   MEMORY.md SHAPE: each NEXT item ≤ 250 bytes, each session's NEXT block ≤ 600 bytes, and the merged NEXT a trim produces ≤ 1.5 KB (ids, the next action, open user calls with their first-raised date; status, numbers and paths live in the task or a topic file; lower-priority ids go on one Parked line); RECENT SESSIONS keeps the newest 3, each hook ≤ 150 bytes; a Zone 2 bucket over 10 entries becomes one pointer to an index topic file.

   - APPEND one pointer line to `🗄 RECENT SESSIONS`: `- YYYY-MM-DD [slug](session-YYYY-MM-DD-slug.md) — one-line hook` (hook ≤ 150 bytes: no hashes, costs or counts; the session file holds them).
   - Under `## ⭐ NEXT`, APPEND or edit ONLY YOUR OWN per-session sub-block: `### [session: <your-tag>] <immediate next + task ids>`. NEVER touch another session's `### [session: …]` block. (Merging the per-session NEXTs into one priority is /trim's or the next /orient's job — not yours.) Each open USER CALL in your block carries the date it was FIRST raised (carry the original date forward, never today's); a call first raised more than two sessions ago goes FIRST in the block, marked "ask now".
   - A genuinely-new DO-NOT-RE-DERIVE rule → APPEND one line (the rule + a `[[file]]`/`D#` pointer); put the detail in a topic file. Do not rewrite existing durable lines. Apply the admission test first: a line enters or stays only if no always-loaded CLAUDE.md, loaded command, the tracker skill or a design/schema slice already carries it, and current or next work needs it. A decision a slice already holds gets nothing here.
   - Each active hazard from your session file → APPEND one line under `## ⚠ HAZARDS` in Zone 1 (create the heading below `⭐ NEXT` if it is absent). Never remove a hazard line; /trim does that once the source shows it is gone.
   - New topic-file pointers → APPEND to the relevant index bucket, or to the bucket's index topic file when MEMORY.md points to one.
   - Never add task-change or done lists: tracker-action lists stay in the session file, never MEMORY.md.

4. TRIM — ONLY IF SOLO. First check for other active sessions (`ListAgents`; any other running session on this project counts — when the check itself is unavailable, treat it as NOT solo). THE SOLO CHECK (the one home; /trim uses it too):
   - A peer listed RIGHT AFTER a `git commit` / `git push` is usually NOT a session: the security-guidance plugin starts a background Agent SDK commit reviewer on every commit, and `ListAgents` lists it as a busy interactive peer (the user never opened it). Its runs last ~5 s to ~3 min.
   - So when a peer is listed, do NOT decide yet: re-check with `ListAgents` up to 3 more times, ~60 s apart (wait with a Monitor until-loop or do step 5's measurement in between — a foreground `sleep` is blocked). A peer GONE by a re-check was the reviewer → treat as solo. A peer still listed after ~3 minutes is a real concurrent session → NOT solo.
   - Name in the report each peer seen and what the re-checks showed.
   - SOLO → read `~/.claude/commands/trim.md` IN FULL and run its whole procedure now, after steps 2-3 (so your own NEXT sub-block and RECENT SESSIONS line are part of what it merges and archives). Its VERIFY-AGAINST-THE-SOURCE rule applies in full. Report it as trim.md asks (BEFORE → AFTER, what moved where, what was cut with its source state, the merged NEXT).
   - NOT SOLO → do NOT trim. Stay add-only and go to step 5; the report's FIRST line says: `Trim skipped — another session is active (<name>, still listed after the re-checks); run /trim when it has finished.`

5. ⚠ BLOAT MEASUREMENT (every time), AFTER step 4, against the budget /trim defines:

   MEMORY.md BUDGET: Zone 1 (above the index heading) ≤ 40 lines and ≤ 8 KB; the whole file ≤ 120 lines and ≤ 12 KB; every Zone 2 line ≤ 300 bytes. Measure with `wc -lc`, not by eye.

   Emit the numbers in your report so the trend is visible. If any limit is exceeded, make this the FIRST line of your report: `⚠ MEMORY.md BLOATED (N lines / N bytes; <which limit>) — run /trim (solo, no concurrent session active).`

Report the task changes (added / closed / reconciled), the trim report (or why it was skipped) + the bloat measurement at the end.

↓ Anything below is MY emphasis for this run — honor it as a priority. It does NOT replace the routine above: still do all of it (add-only steps 1-3, the trim only if solo). (Empty = standard handoff.)
$ARGUMENTS
