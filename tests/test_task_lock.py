import os
import subprocess
import sys
import unittest
from pathlib import Path

from agent_hub.engine import DECLARE_GRACE, Engine, conflict_of
from agent_hub.store import Store, StoreError
from tests.helpers import Clock, FakeRuntime, make_home

ROOT = Path(__file__).resolve().parents[1]
LANES = {"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"}, "docs": {"runtime": "claude"},
         "web": {"runtime": "claude"}}


class Base(unittest.TestCase):
    def build(self, **extra) -> None:
        self.cfg = make_home(LANES, **extra)
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda n: None, clock=self.clock, log=lambda _: None)

    def tick(self, n: int = 1, step: float = 5) -> None:
        for _ in range(n):
            self.e.tick()
            self.clock.tick(step)

    def running(self) -> set[int]:
        return {t["id"] for t in self.s.tasks(("running",), limit=100)}

    def wrap(self, tid: int) -> None:
        lane = self.s.task(tid)["lane"]
        self.s.update_task(tid, state="closed", closed=self.clock())
        self.s.set_lane(lane, closing=1)
        self.rt.st[lane] = "idle"

    def dah(self, *args: str, lane: str, ok: bool = True) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.cfg.home), "PYTHONPATH": str(ROOT), "TOWER_LANE": lane}
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.cfg.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        if ok and p.returncode:
            self.fail(f"dah {' '.join(args)} failed: {p.stderr}")
        if not ok and not p.returncode:
            self.fail(f"dah {' '.join(args)} should have failed")
        return p.stdout + p.stderr


class TaskLockTest(Base):
    def test_a_task_without_paths_starts_beside_a_declared_one(self) -> None:
        self.build(max_lanes=3, max_instances=2)
        a = self.s.add_task("docs", "A", "paths: src/a")
        b = self.s.add_task("docs", "B", "no line")
        self.tick(2)
        self.assertEqual(self.running(), {a, b})
        self.assertEqual(self.s.task(b)["lane"], "docs#2")
        self.assertIn("dah paths", self.rt.opened[-1][1])

    def test_an_undeclared_live_task_is_waited_for_only_the_grace(self) -> None:
        self.build(max_lanes=3, max_instances=2)
        a = self.s.add_task("docs", "A", "no line")
        b = self.s.add_task("docs", "B", "paths: src/b")
        self.tick(3)
        self.assertEqual(self.running(), {a})
        self.assertIn("declare", conflict_of(self.s, self.s.task(b)) or "")
        self.clock.tick(DECLARE_GRACE)
        self.tick()
        self.assertEqual(self.running(), {a, b})

    def test_declared_overlap_gives_the_task_back_and_closes_the_session(self) -> None:
        self.build(max_lanes=3, max_instances=2)
        a = self.s.add_task("docs", "A", "paths: src/a")
        b = self.s.add_task("docs", "B", "paths: src/b")
        self.tick(2)
        self.assertEqual(self.running(), {a, b})
        out = self.dah("paths", "src/a/x", lane="docs#2")
        self.assertIn("went back to the queue", out)
        t = self.s.task(b)
        self.assertEqual((t["state"], t["lane"], t["paths"]), ("queued", "docs", "src/a/x"))
        self.assertIsNotNone(self.s.lane("docs#2").get("closing"))
        self.rt.st["docs#2"] = "idle"
        self.tick(3)
        self.assertEqual(self.running(), {a})
        self.wrap(a)
        self.tick(3)
        self.assertIn(b, self.running())

    def test_declared_paths_replace_the_hint(self) -> None:
        self.build(max_lanes=3, max_instances=2)
        a = self.s.add_task("docs", "A", "paths: src/a")
        self.tick()
        self.assertIn("Stay inside", self.dah("paths", "src/z", "docs/n.md", lane="docs"))
        self.assertEqual(self.s.task(a)["paths"], "src/z, docs/n.md")
        self.assertIsNone(conflict_of(self.s, {"id": 99, "lane": "docs", "brief": "paths: src/a"}))
        self.assertIn("overlap", conflict_of(self.s, {"id": 99, "lane": "docs", "brief": "paths: src"}) or "")

    def test_the_hub_sets_the_paths_of_a_queued_task_and_a_lane_only_its_own(self) -> None:
        self.build(max_lanes=1)
        busy = self.s.add_task("docs", "busy", "x")
        self.tick()
        q = self.s.add_task("docs", "queued", "x")
        self.assertIn(f"task #{q} paths: src/q", self.dah("paths", "--task", str(q), "src/q", lane="hub"))
        self.assertEqual(self.s.task(q)["paths"], "src/q")
        self.dah("paths", "--task", str(q), "src/q", lane="docs", ok=False)
        self.dah("paths", "--task", str(busy), "src/z", lane="hub", ok=False)
        self.dah("paths", lane="docs", ok=False)
        self.dah("paths", "src/z", lane="web", ok=False)

    def test_one_session_lanes_get_no_lock_line(self) -> None:
        self.build(max_lanes=3)
        self.s.add_task("docs", "A", "x")
        self.tick()
        self.assertNotIn("dah paths", self.rt.opened[-1][1])

    def test_only_a_running_task_can_be_released(self) -> None:
        self.build()
        tid = self.s.add_task("docs", "A", "x")
        with self.assertRaises(StoreError):
            self.s.release_task(tid, "docs")


if __name__ == "__main__":
    unittest.main()
