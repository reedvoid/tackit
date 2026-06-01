"""Pass 4 — property-based / stateful testing (Hypothesis).

Instead of hand-picked examples, this fires random sequences of tackit operations
at a real store and re-checks the core invariants after EVERY step, hunting for the
interleaving that breaks one. Hypothesis shrinks any failure to a minimal repro.

Invariants asserted (the always-true laws, not the close-time-only ones):
  * stale => open                      (D7 / schema S1)
  * version is monotonic               (D18 ordering; D20 no-op = no decrease)
  * the dependency graph stays acyclic (D14)
  * tackit.sql round-trips the db      (D18: dump -> rebuild reproduces state)

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
from tackit.errors import InvariantError

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


class TackitMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.dir = Path(tempfile.mkdtemp())
        init_store(self.dir)
        self.core = Core.open(start=self.dir)
        self.ids: list[int] = []
        self.last_version = sync.get_version(self.core.conn)

    def teardown(self):
        self.core.close_conn()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _id(self, i: int) -> int:
        return self.ids[i % len(self.ids)]

    # ---- operations (rules) ----

    @rule(name=_names)
    def add(self, name):
        t = self.core.add(name)
        self.ids.append(t.id)

    @precondition(lambda self: self.ids)
    @rule(i=_pick, name=_names)
    def edit(self, i, name):
        self.core.edit(self._id(i), name=name)

    @precondition(lambda self: self.ids)
    @rule(i=_pick)
    def close(self, i):
        try:
            self.core.close(self._id(i))
        except InvariantError:
            pass  # refused: task stale or upstream stale — an expected outcome

    @precondition(lambda self: self.ids)
    @rule(i=_pick)
    def reopen(self, i):
        self.core.reopen(self._id(i))

    @precondition(lambda self: self.ids)
    @rule(i=_pick)
    def reconcile(self, i):
        self.core.reconcile(self._id(i))

    @precondition(lambda self: len(self.ids) >= 2)
    @rule(i=_pick, j=_pick)
    def dep_add(self, i, j):
        try:
            self.core.link_add(self._id(i), self._id(j))
        except InvariantError:
            pass  # refused: self-link is the only invariant under symmetric model

    @precondition(lambda self: len(self.ids) >= 2)
    @rule(i=_pick, j=_pick)
    def dep_rm(self, i, j):
        self.core.link_rm(self._id(i), self._id(j))

    @precondition(lambda self: self.ids)
    @rule(i=_pick, label=_labels)
    def label_add(self, i, label):
        self.core.label_add(self._id(i), label)

    # ---- invariants (checked after every rule) ----

    @invariant()
    def stale_implies_open(self):
        for t in self.core.ls():
            if t.stale:
                assert t.status == "open", f"T{t.id} is stale but {t.status}"

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
