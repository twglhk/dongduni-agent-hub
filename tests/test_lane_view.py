from __future__ import annotations

import json
import re
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from agent_hub import engines
from agent_hub.store import Store
from agent_hub.web import BOARD, make_handler
from tests.test_process import make_engine_home


class LaneViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home, self.cfg = make_engine_home()
        (self.home / ".tower" / "lanes").mkdir()
        (self.home / ".tower" / "lanes" / "story.md").write_text('Run skill("layout") and skill("ghost") on every task.')
        self.s = Store(self.cfg.db)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cfg, self.s, lambda _: None))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.t1 = self.s.add_task("story", "Boards", "Line: #1 Storyboard · stage 2 · make")
        self.s.claim_task(self.t1)
        self.s.update_task(self.t1, model="opus", effort="high", line_row="1", line_stage="2", line_mode="make", stage_now="3",
                           tok_new=10, tok_cache=20, tok_out=30, turns=4, tok_at=1.0)
        self.s.set_lane("story", status="busy")

    def tearDown(self) -> None:
        self.srv.shutdown()

    def get(self, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_shape(self) -> None:
        code, d = self.get("/api/lanes/story")
        self.assertEqual(code, 200)
        for k in ("lane", "label", "kind", "runtime", "engine", "status", "skills", "task", "process", "fixing", "days", "recent"):
            self.assertIn(k, d)
        self.assertEqual(d["engine"], "anim")
        self.assertEqual(d["skills"], [{"name": "layout", "found": True}, {"name": "ghost", "found": False}])
        self.assertEqual((d["task"]["model"], d["task"]["effort"], d["task"]["stage_now"]), ("opus", "high", "3"))
        self.assertEqual(d["task"]["tokens"], {"new": 10, "cache": 20, "out": 30, "turns": 4})
        self.assertEqual((d["process"]["type"], d["process"]["now"], len(d["process"]["stages"])), ("Storyboard", "3", 3))
        self.assertIsNone(d["fixing"])
        self.assertEqual(len(d["days"]), 7)
        self.assertEqual([r["id"] for r in d["recent"]], [self.t1])

    def test_fixing_by_send_back_and_by_blame(self) -> None:
        self.s.update_task(self.t1, line_mode="fix")
        self.assertEqual(self.get("/api/lanes/story")[1]["fixing"]["by"], "send-back")
        self.s.update_task(self.t1, line_mode="make")
        self.s.add_blame(self.t1, "2", "frames were skipped", "story")
        f = self.get("/api/lanes/story")[1]["fixing"]
        self.assertEqual((f["by"], f["stage"], f["confirmed"]), ("blame", "2", False))

    def test_unknown_lane_and_hub(self) -> None:
        self.assertEqual(self.get("/api/lanes/nope")[0], 404)
        code, d = self.get("/api/lanes/hub")
        self.assertEqual((code, d["skills"], d["task"], d["process"]), (200, [], None, None))

    def test_state_lane_cards_carry_the_line(self) -> None:
        card = next(ln for ln in self.get("/api/state")[1]["lanes"] if ln["lane"] == "story")
        self.assertEqual((card["model"], card["effort"], card["process"], card["stage"], card["fixing"]),
                         ("opus", "high", "Storyboard", "3", False))

    def test_usage_days_fills_empty_days(self) -> None:
        self.s.record_usage("story", "x", "claude", 5, 6, None, None)
        days = self.s.usage_days("story", 7)
        self.assertEqual((days[-1]["tokens_in"], days[-1]["tokens_out"], days[0]["tokens_in"]), (5, 6, 0))

    def test_lane_skills_reader(self) -> None:
        rows = engines.build(self.home / "engines/anim/pattern-library.md", self.home / ".agents/skills")
        got = engines.lane_skills("", self.home / ".agents/skills", rows, "story")
        self.assertEqual([s["name"] for s in got], ["layout"])

    def test_every_language_has_the_lane_sheet_strings(self) -> None:
        text = BOARD.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"^\s+lv: \{ skills:", text, re.M)), 5)


if __name__ == "__main__":
    unittest.main()
