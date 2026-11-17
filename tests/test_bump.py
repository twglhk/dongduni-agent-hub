import os
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

from agent_hub.engine import Engine, team_turns
from agent_hub.store import HUB, INFRA, SCHEMA, Store, StoreError
from tests.helpers import Clock, FakeRuntime, make_home

ROOT = Path(__file__).resolve().parents[1]
LANES = {"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"}, "docs": {"runtime": "claude"},
         "web": {"runtime": "claude"}, "art": {"runtime": "claude"}, "ops": {"runtime": "claude"}}
TEAM = {"docs", "web", "art", "ops"}


class Base(unittest.TestCase):
    def build(self, max_lanes: int = 1, **extra: object) -> None:
        self.cfg = make_home(LANES, max_lanes=max_lanes, **extra)
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda _: None, clock=self.clock, log=lambda _: None)

    def dah(self, *args: str, lane: str, ok: bool = True) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.cfg.home), "PYTHONPATH": str(ROOT), "TOWER_LANE": lane}
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.cfg.home, env=env,
                           capture_output=True, text=True, encoding="utf-8")
        if ok and p.returncode:
            self.fail(f"dah {' '.join(args)} failed: {p.stderr}")
        if not ok and not p.returncode:
            self.fail(f"dah {' '.join(args)} should have failed")
        return p.stdout + p.stderr

    def running(self) -> set[str]:
        return {t["lane"] for t in self.s.tasks(("running",))}


class TaskBumpTest(Base):
    def test_a_bumped_task_starts_first_without_fair_turns(self) -> None:
        self.build()
        self.s.add_task("docs", "a", "x")
        self.s.add_task("web", "b", "x")
        late = self.s.add_task("art", "c", "x")
        self.assertIn("first in the queue", self.dah("task", "bump", str(late), lane="hub"))
        self.e.tick()
        self.assertEqual(self.running(), {"art"})

    def test_the_latest_bump_goes_in_front_of_an_earlier_one(self) -> None:
        self.build()
        a = self.s.add_task("docs", "a", "x")
        b = self.s.add_task("web", "b", "x")
        c = self.s.add_task("art", "c", "x")
        self.s.bump_task(b)
        self.s.bump_task(c)
        order = team_turns(self.s, TEAM, fair=False)
        self.assertEqual(sorted(order, key=order.get), [c, b, a])

    def test_a_bump_wins_over_fairness_once_then_turns_resume(self) -> None:
        self.build(max_lanes=2, fair_turns=True)
        self.s.add_task("docs", "a1", "x", requester="ana")
        self.e.tick()
        self.s.add_task("web", "a2", "x", requester="ana")
        self.s.add_task("art", "b1", "x", requester="bo")
        urgent = self.s.add_task("ops", "a3", "x", requester="ana")
        order = team_turns(self.s, TEAM, fair=True)
        self.assertEqual(sorted(order, key=order.get), [3, 2, urgent])
        self.s.bump_task(urgent)
        self.clock.tick(10)
        self.e.tick()
        self.assertEqual(self.running(), {"docs", "ops"})
        order = team_turns(self.s, TEAM, fair=True)
        self.assertEqual(sorted(order, key=order.get), [3, 2])

    def test_a_bumped_task_stays_ahead_of_a_chained_one(self) -> None:
        self.build(fair_turns=True)
        done = self.s.add_task("docs", "a", "x", requester="ana")
        self.s.update_task(done, state="closed", closed=1.0)
        chained = self.s.add_task("web", "b", f"after: {done}\nx", requester="ana")
        bumped = self.s.add_task("art", "c", "x", requester="bo")
        self.s.bump_task(bumped)
        order = team_turns(self.s, TEAM, fair=True)
        self.assertLess(order[bumped], order[chained])

    def test_the_lane_takes_its_queued_tasks_in_bump_order(self) -> None:
        self.build()
        first = self.s.add_task("docs", "a", "x")
        second = self.s.add_task("docs", "b", "x")
        self.s.bump_task(second)
        self.assertEqual(self.s.next_queued("docs")["id"], second)
        self.assertEqual([t["id"] for t in self.s.queued_for("docs")], [second, first])

    def test_only_a_queued_task_can_be_bumped(self) -> None:
        self.build()
        tid = self.s.add_task("docs", "A", "x")
        self.s.update_task(tid, state="closed", closed=1)
        with self.assertRaises(StoreError):
            self.s.bump_task(tid)
        with self.assertRaises(StoreError):
            self.s.bump_task(999)

    def test_only_the_hub_bumps(self) -> None:
        self.build()
        q = self.s.add_task("docs", "B", "x")
        self.dah("task", "bump", str(q), lane="docs", ok=False)
        self.assertIsNone(self.s.task(q)["rank"])


class MemoBumpTest(Base):
    def memo(self, text: str) -> int:
        return self.s.push(INFRA, HUB, "memo", text)

    def test_a_bumped_memo_is_taken_first_and_the_latest_bump_leads(self) -> None:
        self.build()
        a, b, c = self.memo("big"), self.memo("small"), self.memo("urgent")
        self.assertIn("first in the infra queue", self.dah("memo", "bump", str(c), lane="hub"))
        self.assertEqual([m["id"] for m in self.s.inbox_queue(INFRA)], [c, a, b])
        self.dah("memo", "bump", str(b), lane="hub")
        self.assertEqual([m["id"] for m in self.s.inbox_queue(INFRA)], [b, c, a])
        self.assertEqual(self.s.claim_inbox(INFRA)["id"], b)

    def test_the_status_lists_the_infra_queue_in_that_order(self) -> None:
        self.build()
        a, b = self.memo("big"), self.memo("small")
        self.s.bump_message(b, INFRA)
        out = self.dah("status", lane="hub")
        self.assertLess(out.index(f"#{b} "), out.index(f"#{a} "))

    def test_only_the_hub_bumps_and_only_a_waiting_memo(self) -> None:
        self.build()
        a = self.memo("x")
        self.dah("memo", "bump", str(a), lane="infra", ok=False)
        self.assertIsNone(self.s.message(a)["rank"])
        self.s.claim_inbox(INFRA)
        self.dah("memo", "bump", str(a), lane="hub", ok=False)
        self.dah("memo", "bump", "9999", lane="hub", ok=False)


class OldDatabaseTest(unittest.TestCase):
    def test_an_old_database_gets_both_rank_columns(self) -> None:
        path = make_home().db
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
        con.execute("INSERT INTO tasks(lane, title, brief, state, created) VALUES('docs', 'old', 'b', 'queued', 1)")
        con.execute("INSERT INTO messages(target, sender, kind, text, created) VALUES('infra', 'hub', 'memo', 'old', 1)")
        con.commit()
        con.close()
        s = Store(path)
        self.assertIsNone(s.task(1)["rank"])
        self.assertIsNone(s.message(1)["rank"])
        s.bump_task(1)
        s.bump_message(1, INFRA)
        self.assertEqual(s.task(1)["rank"], 0.0)
        self.assertEqual(s.message(1)["rank"], 0.0)


if __name__ == "__main__":
    unittest.main()
