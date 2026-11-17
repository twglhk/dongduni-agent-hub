"""Leftover lane processes: found, stopped on the second sighting, the hub told."""

import os
import subprocess
import sys
import time
import unittest
from unittest import mock

from agent_hub import leftovers
from agent_hub.engine import LEFTOVER_SECONDS, Engine
from agent_hub.leftovers import LaneProc
from agent_hub.store import Store
from tests.helpers import Clock, FakeRuntime, make_home

S1, S2 = "11111111-aaaa", "22222222-bbbb"


def P(pid, ppid, lane, start, cmd="sh -c x"):
    return LaneProc(pid, ppid, lane, start, cmd)


class StaleTest(unittest.TestCase):
    def pick(self, procs, current):
        return {t[0].pid: (sorted(p.pid for p in t), why) for t, why in leftovers.stale(procs, current)}

    def test_live_session_and_its_children_stay(self) -> None:
        procs = [P(10, 1, "hub", 5, f"claude --session-id {S1} go"), P(11, 10, "hub", 6), P(12, 11, "hub", 7)]
        self.assertEqual(self.pick(procs, {"hub": S1}), {})

    def test_old_session_tree_goes(self) -> None:
        procs = [P(10, 1, "hub", 5, f"claude --session-id {S1} go"), P(11, 10, "hub", 6),
                 P(20, 1, "hub", 9, f"claude --session-id {S2} go")]
        got = self.pick(procs, {"hub": S2})
        self.assertEqual(list(got), [10])
        self.assertEqual(got[10][0], [10, 11])
        self.assertIn("old session", got[10][1])

    def test_closed_lane_and_removed_lane(self) -> None:
        procs = [P(10, 1, "docs", 5, f"claude --resume {S1} x"), P(30, 1, "old-lane", 5, "node server.js")]
        got = self.pick(procs, {"docs": None})
        self.assertEqual(set(got), {10, 30})
        self.assertIn("closed", got[10][1])
        self.assertIn("tower.json", got[30][1])

    def test_orphan_job_older_than_the_live_session_goes_newer_stays(self) -> None:
        procs = [P(40, 1, "docs", 3, "python serve.py"),  # left by the session before
                 P(10, 1, "docs", 5, f"claude --session-id {S2} x"),
                 P(41, 1, "docs", 8, "python serve.py")]  # started by this session, its parent died since
        self.assertEqual(list(self.pick(procs, {"docs": S2})), [40])

    def test_orphan_without_live_root_stays(self) -> None:  # e.g. an opencode server: no session id in argv
        self.assertEqual(self.pick([P(40, 1, "docs", 3, "opencode serve --port 4300")], {"docs": "ses_x"}), {})


class ScanTest(unittest.TestCase):
    @unittest.skipUnless(os.path.isdir("/proc"), "Linux /proc")
    def test_finds_own_child_by_env_only_for_this_home(self) -> None:
        home = make_home({"hub": {"runtime": "claude"}}).home
        env = {**os.environ, "TOWER_HOME": str(home), "TOWER_LANE": "docs"}
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], env=env)
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 env={**env, "TOWER_HOME": "/elsewhere"})
        try:
            for _ in range(50):
                found = [p for p in leftovers.scan(home) if p.pid == child.pid]
                if found:
                    break
                time.sleep(0.05)
            self.assertEqual([p.lane for p in found], ["docs"])
            self.assertFalse([p for p in leftovers.scan(home) if p.pid == other.pid])
        finally:
            for p in (child, other):
                p.kill()
                p.wait()


class EngineLeftoverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home({"hub": {"runtime": "claude"}, "docs": {"runtime": "claude"}}, leftover_sweep=True)
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda n: None, clock=self.clock, log=lambda _: None)
        self.procs: list[LaneProc] = []
        self.stopped: list[int] = []
        self.e.proc_scan = lambda home: list(self.procs)
        self.e.proc_stop = self.stopped.append

    def news(self) -> list[str]:
        return [m["text"] for m in self.s.pending("hub") if m["kind"] == "leftover"]

    def test_stopped_on_second_check_and_hub_told(self) -> None:
        self.procs = [P(10, 1, "docs", 5, f"claude --session-id {S1} x"), P(11, 10, "docs", 6)]
        self.e._leftovers()
        self.assertEqual(self.stopped, [])  # first sighting
        self.clock.tick(LEFTOVER_SECONDS - 1)
        self.e._leftovers()
        self.assertEqual(self.stopped, [])  # not due yet
        self.clock.tick(2)
        self.e._leftovers()
        self.assertEqual(self.stopped, [11, 10])  # children first
        news = self.news()
        self.assertEqual(len(news), 1)
        self.assertIn("docs: pid 10 and 1 child", news[0])
        self.assertIn("session closed", news[0])

    def test_gone_before_second_check_nothing(self) -> None:
        self.procs = [P(10, 1, "docs", 5, f"claude --session-id {S1} x")]
        self.e._leftovers()
        self.procs = []
        self.clock.tick(LEFTOVER_SECONDS)
        self.e._leftovers()
        self.procs = [P(10, 1, "docs", 5, f"claude --session-id {S1} x")]  # back: a first sighting again
        self.clock.tick(LEFTOVER_SECONDS)
        self.e._leftovers()
        self.assertEqual(self.stopped, [])

    def test_off_by_default(self) -> None:
        cfg = make_home({"hub": {"runtime": "claude"}, "docs": {"runtime": "claude"}})
        self.assertFalse(cfg.leftover_sweep)
        s = Store(cfg.db, clock=self.clock)
        e = Engine(cfg, s, FakeRuntime(s), lambda n: None, clock=self.clock, log=lambda _: None)
        scans: list[object] = []
        e.proc_scan = lambda home: scans.append(home) or []
        e._leftovers()
        self.assertEqual(scans, [])

    def test_the_default_stop_goes_through_procs(self) -> None:
        with mock.patch("agent_hub.engine.procs.terminate") as term:
            self.e.__class__(self.cfg, self.s, self.rt, lambda n: None, clock=self.clock, log=lambda _: None).proc_stop(77)
        term.assert_called_once_with(77, group=False)

    def test_live_session_left_alone(self) -> None:
        self.s.set_lane("docs", session=S1)
        self.procs = [P(10, 1, "docs", 5, f"claude --session-id {S1} x")]
        for _ in range(3):
            self.e._leftovers()
            self.clock.tick(LEFTOVER_SECONDS)
        self.assertEqual(self.stopped, [])
        self.assertEqual(self.news(), [])


if __name__ == "__main__":
    unittest.main()
