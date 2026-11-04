---
description: End-of-session handoff — ADD-ONLY. Commit your own work, append your session record + NEXT block to memory, alert if MEMORY.md is bloated. Never trims (that's /trim).
argument-hint: [optional: any emphasis for this run]
---
Run the end-of-session handoff. This is the WRITE side of the continuity loop — /orient reads back exactly what you record here, so write it for a cold session that has none of this conversation's context.

⭐ ADD-ONLY — the load-bearing rule. This command may ONLY add, or edit content THIS session created. It must NEVER delete, rewrite, or trim content another (or a past) session wrote. WHY: MEMORY.md is loaded into context at each session's START, so your in-context copy is STALE the moment a concurrent session writes; a rewrite/trim built from your stale copy would silently clobber its work. Only add-only + re-read-before-write composes safely across simultaneous handoffs. Global trimming/consolidation is a SEPARATE, solo-only job — **/trim**.

1. RECONCILE + COMMIT your OWN work (never another session's):
   - Clear stale flags only on tasks YOU touched; close / wont_do only what YOU resolved. Do not reconcile or alter a concurrent session's in-flight tasks.
   - `git commit` your OWN changed files BY EXPLICIT PATH — never `git add -A`/`git commit -a` (that would sweep a concurrent session's uncommitted work into your commit). The tackit store persists on every op, so the shared `.tackit` snapshot already holds both sessions' changes and git serializes commits via its lock.

2. WRITE YOUR SESSION RECORD to its OWN dated file in the project memory directory — NOT into MEMORY.md's body:
   - Create `session-YYYY-MM-DD-<slug>.md` (slug = this session's focus) holding the BULK of the handoff: what you DID (shipped + verified — commit hashes / task ids), PROVENANCE (how you got here + the ORIGINAL trigger), and the ORIENTATION detail (specs, tasks, code touchpoints, run-logs/fixtures, conventions a cold session must read). Per-session file ⇒ two sessions never write the same file.
   - It ends with a fixed `## LEFT UNVERIFIED / UNBUILT` list (in place of free-form cautions): each item = what, why it is open, and its OPEN tracker id. Something a later session must act on gets a task now; a note on a closed task or a NEXT bullet does not count. An item with no task states why no action is needed. An empty list reads NONE. A caution with no open action (a hazard to remember) goes in a `## HAZARDS` list beside it, one line each.

3. RE-READ MEMORY.md FROM DISK (do NOT trust your in-context copy — a concurrent session may have just written it), then make ONLY these ADDITIVE edits (append / your-own-lines-only; never a full-file rewrite):
   - APPEND one pointer line to `🗄 RECENT SESSIONS`: `- YYYY-MM-DD [slug](session-YYYY-MM-DD-slug.md) — one-line hook`.
   - Under `## ⭐ NEXT`, APPEND or edit ONLY YOUR OWN per-session sub-block: `### [session: <your-tag>] <immediate next + task ids>`. NEVER touch another session's `### [session: …]` block. (Merging the per-session NEXTs into one priority is /trim's or the next /orient's job — not yours.) Each open USER CALL in your block carries the date it was FIRST raised (carry the original date forward, never today's); a call first raised more than two sessions ago goes FIRST in the block, marked "ask now".
   - A genuinely-new DO-NOT-RE-DERIVE rule → APPEND one line (the rule + a `[[file]]`/`D#` pointer); put the detail in a topic file. Do not rewrite existing durable lines.
   - New topic-file pointers → APPEND to the relevant index bucket.

4. ⚠ BLOAT ALERT (measure + warn — NEVER trim here): `wc -l` MEMORY.md and estimate its tokens. Emit the number in your report every time so the trend is visible. If it exceeds budget — the always-on core (top `# ⬅ ACTIVE` zone) over ~40 lines / ~3k tokens, OR the whole file over ~120 lines — make this the FIRST line of your report: `⚠ MEMORY.md BLOATED (N lines / ~Xk tok) — run /trim (solo, no concurrent session active).` Trimming is NEVER done in /handoff.

Report the task changes (added / closed / reconciled) + the bloat measurement at the end.

↓ Anything below is MY emphasis for this run — honor it as a priority. It does NOT replace the routine above: still do all of it (add-only). (Empty = standard handoff.)
$ARGUMENTS
