"""Who a `dah` call is: inside a tower session TOWER_LANE wins. A parallel session (docs#2) writes as itself even when
it passes `--as <base lane>`, so a wrapped lets go of that session's own task; `--as` another lane is refused."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

from agent_hub.store import Store
from tests.helpers import make_home

ROOT = Path(__file__).resolve().parents[1]
LANES = {"hub": {"runtime": "claude"}, "docs": {"runtime": "codex"},
         "web": {"runtime": "claude"}}


class SessionIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home(LANES, max_instances=3)
        self.s = Store(self.cfg.db)

    def dah(self, *args: str, lane: str | None = None, ok: bool = True) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.cfg.home), "PYTHONPATH": str(ROOT)}
        env.pop("TOWER_LANE", None)
        if lane:
            env["TOWER_LANE"] = lane
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.cfg.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        if ok and p.returncode:
            self.fail(f"dah {' '.join(args)} failed: {p.stderr}")
        if not ok and not p.returncode:
            self.fail(f"dah {' '.join(args)} should have failed: {p.stdout}")
        return p.stdout + p.stderr

    def running(self) -> dict[str, int]:
        out = {}
        for lane in ("docs", "docs#2", "docs#3"):
            out[lane] = self.s.add_task(lane, f"t {lane}", "brief")
            self.s.update_task(out[lane], state="running")
        return out

    def test_instance_wraps_as_itself_even_with_as_base_lane(self) -> None:
        tids = self.running()
        out = self.dah("--as", "docs", "report", "wrapped", "ok", lane="docs#3")
        self.assertIn("this session is docs#3", out)
        self.assertEqual(self.s.task(tids["docs#3"])["state"], "closed")
        self.assertEqual(self.s.task(tids["docs"])["state"], "running")
        self.assertEqual(self.s.task(tids["docs#2"])["state"], "running")
        self.assertIsNone(self.s.lane("docs").get("closing"))

    def test_as_its_own_lane_is_just_itself(self) -> None:
        tids = self.running()
        out = self.dah("--as", "docs#2", "report", "wrapped", "ok", lane="docs#2")
        self.assertNotIn("ignored", out)
        self.assertEqual(self.s.task(tids["docs#2"])["state"], "closed")

    def test_as_another_lane_is_refused_inside_a_session(self) -> None:
        tids = self.running()
        self.dah("--as", "docs#2", "report", "wrapped", "x", lane="docs#3", ok=False)
        self.dah("--as", "hub", "task", "web", "--title", "t", "brief", lane="docs#2", ok=False)
        self.assertEqual(len(self.s.tasks(("queued",), limit=50)), 0)
        self.dah("--as", "web", "report", "wrapped", "x", lane="docs", ok=False)
        self.assertTrue(all(self.s.task(t)["state"] == "running" for t in tids.values()))
        self.dah("--as", "docs#2", "status")  # by hand (no TOWER_LANE) --as still picks the lane


if __name__ == "__main__":
    unittest.main()
