"""Pass 4 — property-based / stateful testing (Hypothesis).

Instead of hand-picked examples, this fires random sequences of tackit operations
at a real store and re-checks the core invariants after EVERY step, hunting for the
interleaving that breaks one. Hypothesis shrinks any failure to a minimal repro.

Invariants asserted (the always-true laws, not the close-time-only ones):
  * version is monotonic                       (D18 ordering; D20 no-op = no decrease)
  * links stored canonical + no duplicates     (T86 / D5)
  * audit table append-only                    (D29 v0.4)
  * kind/status partition holds                (D36 v0.5)
  * worklist filter: status IN ('open','spec') (D36 v0.5)
  * links() anchor excludes retired            (T180)
  * retired is terminal forever                (D36 + T132 generalized)
  * wont_do rows have non-null reason          (T132 + D7 v0.4)
  * tackit.sql round-trips the db              (D18: dump -> rebuild reproduces state)

Note: "a closed task never sits atop stale upstream" is deliberately NOT an
invariant here — it holds at close time (the gate), but a transitive edit can leave
a closed task above a stale dependency until that dependency is reconciled. Asserting
it continuously would be wrong; this is exactly the kind of subtlety property testing
forces you to get right.
"""

import shutil
import sqlite3
import tempfile
from pathlib import Path

import hypothesis.strategies as st
from hypothesis import HealthCheck, settings
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from tackit import sync
from tackit.core import Core
from tackit.db import init_store
from tackit.errors import InvariantError, ValidationError

# names/labels: non-empty after strip; still includes quotes/newlines/unicode, which
# stresses the SQL-literal escaping in the dump (a real round-trip hazard). We restrict
# to UTF-8-encodable, non-NUL characters because the boundary now REJECTS the rest
# (NUL breaks the executescript rebuild; lone surrogates aren't valid UTF-8) — those
# rejections are pinned separately in test_engine_edges, so here we explore the VALID
# input space. Both exclusions were themselves findings from this property test.
_chars = st.characters(codec="utf-8", exclude_characters="\x00")
_names = st.text(_chars, min_size=1, max_size=12).filter(lambda s: s.strip())
_labels = st.text(_chars, min_size=1, max_size=6).filter(lambda s: s.strip())
_pick = st.integers(min_value=0, max_value=50)
# D36 v0.5: kind/status partition. Sample all four kinds; the per-kind status
# default is applied in Core.add() so the partition holds by construction.
_kinds = st.sampled_from(("design", "schema", "production", "meta"))
# D33 + D36 (v0.5): non-placeholder reasons for the wont_do / retire happy path.
_RESERVED_PLACEHOLDERS = {"tbd", "todo", "obsolete", "no longer needed"}
_reasons = st.text(_chars, min_size=1, max_size=20).filter(
    lambda s: s.strip() and s.strip().lower() not in _RESERVED_PLACEHOLDERS
)


class TackitMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.dir = Path(tempfile.mkdtemp())
        init_store(self.dir)
        self.core = Core.open(start=self.dir)
        self.ids: list[int] = []
        self.last_version = sync.get_version(self.core.conn)
        # T176: track ids that ever reached status='retired' so the
        # retired_terminal_no_status_change invariant can verify the row
        # stays retired across subsequent rules (D36 + T132 generalized).
        self._retired_ids: set[int] = set()

    def teardown(self):
        self.core.close_conn()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _id(self, i: int) -> int:
        return self.ids[i % len(self.ids)]

    # ---- operations (rules) ----

    @rule(name=_names, kind=_kinds)
    def add(self, name, kind):
        """T176: kind is randomized over all four. Core.add() sets the
        partition-correct default status (spec for design/schema, open
        otherwise) per D36.

        D256: a bare production add (no deps) is refused by the creation
        gate -- it must link a design/schema slice at creation. This rule
        doesn't manufacture a link, so the refusal is an EXPECTED outcome
        (same shape as the other rules' refusal catches); the machine
        just keeps walking without tracking a new id."""
        try:
            t = self.core.add(name, kind=kind)
        except ValidationError:
            return
        self.ids.append(t.id)

    @precondition(lambda self: self.ids)
    @rule(i=_pick, name=_names)
    def edit(self, i, name):
        try:
            self.core.edit(self._id(i), name=name, delta="property test")
        except (InvariantError, ValidationError):
            # D259: edit is REFUSED on closed / wont_do tasks (frozen records)
            # -> ValidationError; reopen to change, retired slices stay editable.
            # Other refusals (bad text, etc.) may also arise; all expected.
            pass

    @precondition(lambda self: self.ids)
    @rule(i=_pick)
    def close(self, i):
        try:
            self.core.close(self._id(i))
        except InvariantError:
            pass  # refused: task stale, upstream stale, or design/schema (D30)

    @precondition(lambda self: self.ids)
    @rule(i=_pick)
    def wont_do(self, i):
        """T132 / v0.4 - third terminal status. The machine must exercise it
        so post-wont_do invariants (no double-decide, no further status
        change without reopen, etc.) get random-coverage exposure too."""
        try:
            self.core.wont_do(
                self._id(i), reason="property test", delta="property test"
            )
        except InvariantError:
            # Refused: already-closed/wont_do (no double-decide), design/schema
            # kind (D30), or close-gate stale -- all expected.
            pass

    @precondition(lambda self: self.ids)
    @rule(i=_pick)
    def reopen(self, i):
        try:
            self.core.reopen(self._id(i))
        except InvariantError:
            pass  # refused on wont_do per v0.4 reopen rules

    @precondition(lambda self: self.ids)
    @rule(i=_pick)
    def reconcile(self, i):
        try:
            self.core.reconcile(self._id(i))
        except InvariantError:
            # v0.4 (D28 + T156): reconcile refuses iff (status in {closed,
            # wont_do}) AND (kind in {production, meta}). Closed/wont_do
            # design/schema rows ARE reconcilable (perma-spec is the
            # obligation). The property machine doesn't care which path it
            # takes; the refusal is well-defined behavior.
            pass

    @precondition(lambda self: len(self.ids) >= 2)
    @rule(i=_pick, j=_pick)
    def dep_add(self, i, j):
        try:
            self.core.link_add(self._id(i), self._id(j), because="property test")
        except InvariantError:
            pass  # refused: self-link is the only invariant under symmetric model

    @precondition(lambda self: len(self.ids) >= 2)
    @rule(i=_pick, j=_pick)
    def dep_rm(self, i, j):
        self.core.link_rm(self._id(i), self._id(j))

    @precondition(lambda self: self.ids)
    @rule(i=_pick, label=_labels)
    def label_add(self, i, label):
        try:
            self.core.label_add(self._id(i), label)
        except (InvariantError, ValidationError):
            # D26 / T110: reserved label names (design/schema/production/meta)
            # are refused at the validation boundary. The random string
            # generator can happen to produce one; that refusal is well-
            # defined behavior the machine just keeps walking past. (The
            # refusal raises ValidationError -- caught alongside
            # InvariantError so Hypothesis doesn't shrink to this case.)
            pass

    @precondition(lambda self: self.ids)
    @rule(i=_pick, reason=_reasons)
    def retire(self, i, reason):
        """T176 / D36: random retire across all ids. Refused if the row
        isn't status='spec', isn't design/schema, is stale, has a stale
        linked neighbor, has an open linked neighbor, or the reason is a
        placeholder. The machine doesn't care which refusal path fires --
        partition_holds + retired_terminal_no_status_change invariants
        cover the post-conditions whichever branch we took."""
        target = self._id(i)
        try:
            self.core.retire(target, reason=reason, delta="property test")
        except (InvariantError, ValidationError):
            return
        # Success: row is now status='retired'. Track for the terminal
        # invariant.
        self._retired_ids.add(target)

    @precondition(lambda self: self.ids)
    @rule(i=_pick, kind=_kinds)
    def reclassify_cross_partition(self, i, kind):
        """T176 / D36: random reclassify, which may cross the partition
        boundary (production/meta <-> design/schema). Core.reclassify()
        auto-shifts status (open<->spec) when the source status has a
        clean target; otherwise refuses. The machine catches both paths;
        partition_holds checks the post-condition."""
        try:
            self.core.reclassify(
                self._id(i), kind, delta="property test reclassify"
            )
        except InvariantError:
            pass

    @rule()
    def retire_with_open_neighbor(self):
        """T176 / D36: targeted rule -- create a fresh spec design + an
        open production neighbor + link them, then call retire() and
        assert refusal names the neighbor + because rationale. Validates
        the (i)/(ii) decision-tree message bank under hypothesis-driven
        scheduling."""
        d = self.core.add("prop d-target", kind="design")
        rationale = "prop test rationale linking d to p"
        # D256 creation-gate: p (production) must link a design/schema slice
        # at creation -- use d itself (already created above) as that link,
        # which is the SAME edge the old post-hoc link_add call below used
        # to create, so no separate link_add is needed.
        p = self.core.add(
            "prop p-neighbor", kind="production", deps={d.id: rationale}
        )
        self.ids.append(d.id)
        self.ids.append(p.id)
        try:
            self.core.retire(d.id, reason="trying", delta="prop test")
        except InvariantError as e:
            msg = str(e)
            assert f"T{p.id}" in msg, (
                f"retire refusal must name open neighbor T{p.id}; got: {msg}"
            )
            assert rationale in msg, (
                f"retire refusal must include the link's `because` rationale; "
                f"got: {msg}"
            )
            return
        # If retire SUCCEEDED that's wrong -- p is open and freshly linked.
        raise AssertionError(
            f"retire succeeded on D{d.id} despite open neighbor T{p.id}; "
            f"open-neighbor gate (D36 step 6) failed."
        )

    # ---- invariants (checked after every rule) ----

    @invariant()
    def stale_neighbor_was_linked_or_marked(self):
        """T123 (2026-06-01) retired the v0.2.0 'stale => open' invariant. A
        closed task may now carry stale=True (cascade-staling no longer
        force-opens closed neighbors per the relaxed D7). This invariant has
        been removed; closed-stale is a valid state. We keep an empty hook
        here as a marker so the removed invariant's intent is recorded in
        the property suite."""
        # Intentionally empty: no invariant on (stale, status) under T123.
        return

    @invariant()
    def version_never_decreases(self):
        v = sync.get_version(self.core.conn)
        assert v >= self.last_version, f"version went {self.last_version} -> {v}"
        self.last_version = v

    @invariant()
    def links_are_canonical_pairs(self):
        # T86 symmetric semantics: every link row is stored in canonical order
        # (task_a < task_b), so the same unordered pair is never duplicated and
        # self-links are impossible. The acyclicity invariant was retired with
        # the directional model -- an undirected edge has no cycle.
        edges = self.core.conn.execute(
            "SELECT task_a, task_b FROM links"
        ).fetchall()
        seen: set[tuple[int, int]] = set()
        for e in edges:
            a, b = e["task_a"], e["task_b"]
            assert a < b, f"link not canonical: ({a}, {b}) should have task_a < task_b"
            pair = (a, b)
            assert pair not in seen, f"duplicate link {pair}"
            seen.add(pair)

    @invariant()
    def audit_table_never_shrinks(self):
        """T147 / D29 v0.4 - description_revisions is append-only. Row count
        must be monotonically non-decreasing across the random op sequence.
        Catches a future regression where an edit path forgot to insert, an
        insert was rolled back without rolling the whole op back, or a
        cleanup task deleted rows."""
        if not hasattr(self, "_last_audit_count"):
            self._last_audit_count = 0
        c = self.core.conn.execute(
            "SELECT COUNT(*) FROM description_revisions"
        ).fetchone()[0]
        assert c >= self._last_audit_count, (
            f"description_revisions shrank: {self._last_audit_count} -> {c}"
        )
        self._last_audit_count = c

    @invariant()
    def worklist_filter_holds(self):
        """T176 / D36 v0.5 - the stale_worklist must only contain tasks
        where status IN ('open','spec'). Closed/wont_do production/meta
        and retired design/schema rows are record-only -- their stale
        flag does NOT pressure the worklist. This catches a future
        regression where the filter is loosened (which would re-
        introduce the v0.3-era never-emptying-worklist problem on hub-
        spec edits) OR tightened to exclude live spec (which would hide
        legitimate obligation)."""
        for t in self.core.stale_worklist():
            assert t.status in ("open", "spec"), (
                f"task {t.id} (status={t.status}, kind={t.kind}) is on the "
                f"worklist but violates D36's filter: must be status IN "
                f"('open','spec'). Closed/wont_do/retired stale is record-only."
            )

    @invariant()
    def partition_holds(self):
        """T176 / D36 v0.5 - the kind/status partition is the schema-level
        CHECK that ships in Phase 1; this invariant asserts the same shape
        at the property-machine layer so a regression in app code that
        bypasses the CHECK (e.g. a future op that updates status without
        re-validating) gets caught here even if the SQLite CHECK is
        somehow disabled."""
        rows = self.core.conn.execute(
            "SELECT id, kind, status FROM tasks"
        ).fetchall()
        for r in rows:
            kind = r["kind"]
            status = r["status"]
            if kind in ("production", "meta"):
                assert status in ("open", "closed", "wont_do"), (
                    f"task {r['id']} kind={kind} status={status} violates "
                    f"the production/meta partition (D36 v0.5)."
                )
            elif kind in ("design", "schema"):
                assert status in ("spec", "retired"), (
                    f"task {r['id']} kind={kind} status={status} violates "
                    f"the design/schema partition (D36 v0.5)."
                )
            else:
                raise AssertionError(
                    f"task {r['id']} has unknown kind={kind!r}; the closed "
                    f"taxonomy is design|schema|production|meta (D26)."
                )

    @invariant()
    def links_anchor_excludes_retired(self):
        """T176 / T180 - the links() no-input mode returns the anchor
        layer of LIVE design+schema slices. Retired specs must NOT
        surface there -- they're dead decisions, not viable link targets
        for new work. Pins the T180 anchor-query status filter."""
        for n in self.core.links():
            assert n.status != "retired", (
                f"anchor task {n.id} kind={n.kind} status=retired surfaced "
                f"from links() no-input mode; T180 anchor-query status "
                f"filter (status IN ('open','spec')) violated."
            )

    @invariant()
    def retired_terminal_no_status_change(self):
        """T176 / D36 + T132 generalized - once retired, the row's status
        is terminal forever. retire/close/wont_do/reopen all refuse on a
        retired row, and the only state mutator that touches status
        (_set_status, called by reopen) is fenced off. This invariant
        catches a regression where one of the verb refusals is loosened
        or a new verb forgets the retired-state check."""
        for tid in self._retired_ids:
            row = self.core.conn.execute(
                "SELECT status FROM tasks WHERE id = ?", (tid,)
            ).fetchone()
            assert row is not None, f"retired task {tid} disappeared"
            assert row["status"] == "retired", (
                f"task {tid} was retired but status is now {row['status']!r}; "
                f"retired is terminal forever (D36 + T132 generalized)."
            )

    @invariant()
    def wont_do_rows_have_reason(self):
        """T132 / D7 v0.4 - status='wont_do' rows MUST carry a non-null
        wont_do_reason; the op layer enforces it on wont_do() and edit()
        leaves the field alone. If a future code path lets a wont_do row
        end up reason=NULL, this catches it."""
        rows = self.core.conn.execute(
            "SELECT id, status, wont_do_reason FROM tasks WHERE status = 'wont_do'"
        ).fetchall()
        for r in rows:
            assert r["wont_do_reason"] is not None, (
                f"task {r['id']} status=wont_do but wont_do_reason is NULL"
            )

    @invariant()
    def sql_dump_round_trips(self):
        text = sync.dump_text(self.core.conn)
        mem = sqlite3.connect(":memory:")
        try:
            mem.executescript(text)
            cols = "id, name, status, stale"
            orig = self.core.conn.execute(f"SELECT {cols} FROM tasks ORDER BY id").fetchall()
            back = mem.execute(f"SELECT {cols} FROM tasks ORDER BY id").fetchall()
            assert [tuple(r) for r in orig] == [tuple(r) for r in back]
            eq = "task_a, task_b"
            oe = self.core.conn.execute(
                f"SELECT {eq} FROM links ORDER BY task_a, task_b"
            ).fetchall()
            be = mem.execute(
                f"SELECT {eq} FROM links ORDER BY task_a, task_b"
            ).fetchall()
            assert [tuple(r) for r in oe] == [tuple(r) for r in be]
        finally:
            mem.close()


# Bound the run so it stays fast in CI; deadline off because each step does file +
# db IO. Suppress the too-slow health check for the per-example store setup.
TackitMachine.TestCase.settings = settings(
    max_examples=40,
    stateful_step_count=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

TestTackitStateMachine = TackitMachine.TestCase
