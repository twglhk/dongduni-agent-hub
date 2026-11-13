from __future__ import annotations

import json
import re
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from agent_hub import engines
from agent_hub.store import Store
from agent_hub.web import BOARD, make_handler
from tests.test_process import make_engine_home

DAY = 86400.0


class SnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home, self.cfg = make_engine_home()
        self.skills = self.home / ".agents/skills"
        self.t0 = time.mktime((2026, 11, 4, 12, 0, 0, 0, 0, -1))  # a Wednesday

    def live(self, now: float) -> dict:
        return engines.live_snapshot(self.cfg, "anim", now)

    def test_diff_of_equal_snapshots_is_empty(self) -> None:
        self.assertEqual(engines.diff(self.live(self.t0), self.live(self.t0 + 1)), [])

    def test_diff_reports_each_kind(self) -> None:
        old = self.live(self.t0)
        (self.skills / "layout" / "SKILL.md").write_text("changed")
        (self.skills / "render").mkdir()
        (self.skills / "render" / "SKILL.md").write_text("new skill")
        card = self.skills / "layout/references/process/storyboard.md"
        card.write_text(card.read_text().replace("— v2", "— v3"))
        frames = self.skills / "layout/references/patterns/frames.md"
        frames.write_text("# Frames — v2\n\n## Status\n\nconfirmed\n")
        new = self.live(self.t0 + 7 * DAY)
        got = {(c["kind"], c["name"]): (c["from"], c["to"]) for c in engines.diff(old, new)}
        self.assertEqual(got[("skill_changed", "layout")], ("", ""))
        self.assertIn(("skill_added", "render"), got)
        self.assertEqual(got[("process", "Storyboard")], ("v2 confirmed", "v3 confirmed"))
        self.assertEqual(got[("card", "Storyboard · 2 Frames")], ("v1 tuned", "v2 confirmed"))

    def test_diff_lanes_and_rows(self) -> None:
        old = self.live(self.t0)
        new = {**old, "lanes": ["story", "sound"], "rows": {k: v for k, v in old["rows"].items() if k != "2"}}
        kinds = {(c["kind"], c["name"]) for c in engines.diff(old, new)}
        self.assertEqual(kinds, {("lane_added", "sound"), ("lane_removed", "render"), ("row_removed", "Sound cue")})

    def test_weekly_writes_once_per_week_and_keeps_old(self) -> None:
        self.assertEqual(len(engines.weekly(self.cfg, self.t0)), 1)
        self.assertEqual(engines.weekly(self.cfg, self.t0 + DAY), [])  # same ISO week
        self.assertEqual(len(engines.weekly(self.cfg, self.t0 + 7 * DAY)), 1)
        self.assertEqual([p.stem for p in engines.snap_files(self.cfg, "anim")], ["2026-W45", "2026-W46"])

    def test_baseline_is_the_last_snapshot_of_an_earlier_week(self) -> None:
        self.assertIsNone(engines.baseline(self.cfg, "anim", self.t0))
        engines.weekly(self.cfg, self.t0)
        self.assertEqual(engines.baseline(self.cfg, "anim", self.t0 + DAY)["at"], self.t0)  # only this week's: the oldest there is
        (self.skills / "layout" / "SKILL.md").write_text("x")
        engines.weekly(self.cfg, self.t0 + 7 * DAY)
        self.assertEqual(engines.baseline(self.cfg, "anim", self.t0 + 8 * DAY)["at"], self.t0)  # last week's, not this week's

    def test_broken_snapshot_file_is_ignored(self) -> None:
        engines.weekly(self.cfg, self.t0)
        (engines.snap_dir(self.cfg, "anim") / "2026-W46.json").write_text("{nope")
        self.assertEqual(engines.baseline(self.cfg, "anim", self.t0 + 7 * DAY)["at"], self.t0)

    def test_hand_written_map(self) -> None:
        p = self.home / "engines/anim/engine.json"
        self.assertEqual(engines.read_map(p), {"notes": "", "tools": [], "experiments": []})
        p.write_text(json.dumps({"notes": "n", "tools": [{"name": "ffmpeg", "note": "cuts"}, {"note": "no name"}, 3],
                                 "experiments": [{"name": "e1", "status": "running"}], "extra": 1}))
        d = engines.read_map(p)
        self.assertEqual(d["tools"], [{"name": "ffmpeg", "note": "cuts"}])
        self.assertEqual(d["experiments"], [{"name": "e1", "status": "running", "note": ""}])
        p.write_text("[1")
        self.assertEqual(engines.read_map(p)["tools"], [])


class TickHookTest(unittest.TestCase):
    def test_tick_writes_the_weekly_snapshot_once_an_hour(self) -> None:
        from agent_hub.engine import Engine
        from tests.helpers import FakeRuntime
        home, cfg = make_engine_home()
        s = Store(cfg.db)
        now = [time.mktime((2026, 11, 4, 12, 0, 0, 0, 0, -1))]
        e = Engine(cfg, s, FakeRuntime(s), lambda _: None, clock=lambda: now[0], log=lambda _: None)
        e.tick()
        self.assertEqual([p.stem for p in engines.snap_files(cfg, "anim")], ["2026-W45"])
        (home / ".agents/skills/layout/SKILL.md").write_text("later")
        now[0] += 600
        e.tick()  # inside the hour: no second look, and the same week has its file anyway
        now[0] += 7 * DAY
        e.tick()
        self.assertEqual([p.stem for p in engines.snap_files(cfg, "anim")], ["2026-W45", "2026-W46"])


class MapApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home, self.cfg = make_engine_home()
        (self.home / ".tower" / "lanes").mkdir()
        (self.home / ".tower" / "lanes" / "story.md").write_text('Use skill("layout").')
        (self.home / "engines/anim/engine.json").write_text(json.dumps({"tools": [{"name": "ffmpeg", "note": "cuts"}]}))
        self.s = Store(self.cfg.db)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cfg, self.s, lambda _: None))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self) -> None:
        self.srv.shutdown()

    def get(self, path: str, headers: dict | None = None):
        rq = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(rq, timeout=10) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_engine_list(self) -> None:
        code, body, _ = self.get("/api/engines")
        d = json.loads(body)["engines"]
        self.assertEqual((code, d[0]["id"], d[0]["rows"], d[0]["lanes"]), (200, "anim", 3, ["story", "render"]))

    def test_map_shape(self) -> None:
        code, body, _ = self.get("/api/engines/anim/map")
        d = json.loads(body)
        self.assertEqual(code, 200)
        for k in ("id", "label", "lanes", "pipelines", "skills", "tools", "experiments", "usage", "changes"):
            self.assertIn(k, d)
        p = d["pipelines"][0]
        self.assertEqual((p["row"], p["type"], p["version"], p["process_status"]), ("1", "Storyboard", 2, "confirmed"))
        self.assertEqual([s["name"] for s in p["stages"]], ["Beats", "Frames", "Review"])
        self.assertIsNone(p["stages"][2]["card"])
        self.assertEqual(p["stages"][0]["card"]["status"], "confirmed")
        self.assertEqual(d["skills"][0]["name"], "layout")
        self.assertEqual(d["lanes"][0]["skills"], [{"name": "layout", "found": True}])
        self.assertEqual(d["tools"], [{"name": "ffmpeg", "note": "cuts"}])
        self.assertEqual(d["changes"], {"since": None, "items": []})

    def test_rating_lands_on_the_stage(self) -> None:
        t = self.s.add_task("story", "Boards", "Line: #1 Storyboard · stage 1 · make")
        self.s.claim_task(t)
        self.s.update_task(t, line_row="1", line_stage="1", line_mode="make", engine="anim")
        self.s.update_task(t, state="closed")
        d = json.loads(self.get("/api/engines/anim/map")[1])
        st = d["pipelines"][0]["stages"][0]
        self.assertEqual((st["rating"]["n"], st["rating"]["state"]), (1, "new"))

    def test_304_on_an_unchanged_map_and_200_after_a_change(self) -> None:
        code, _, h = self.get("/api/engines/anim/map")
        tag = h["etag"]
        self.assertEqual(code, 200)
        code, body, _ = self.get("/api/engines/anim/map", {"If-None-Match": tag})
        self.assertEqual((code, body), (304, b""))
        (self.home / ".agents/skills/layout/SKILL.md").write_text("edited")
        code, _, h2 = self.get("/api/engines/anim/map", {"If-None-Match": tag})
        self.assertEqual(code, 200)
        self.assertNotEqual(h2["etag"], tag)

    def test_changes_since_last_weeks_snapshot(self) -> None:
        now = time.time()
        engines.weekly(self.cfg, now - 8 * DAY)
        (self.home / ".agents/skills/layout/SKILL.md").write_text("edited")
        d = json.loads(self.get("/api/engines/anim/map")[1])
        self.assertEqual([(c["kind"], c["name"]) for c in d["changes"]["items"]], [("skill_changed", "layout")])
        self.assertIsNotNone(d["changes"]["since"])

    def test_unknown_engine_404(self) -> None:
        self.assertEqual(self.get("/api/engines/ghost/map")[0], 404)


class BoardTextTest(unittest.TestCase):
    def test_engine_strings_exist_in_all_five_languages(self) -> None:
        html = BOARD.read_text(encoding="utf-8")
        used = set(re.findall(r"\bE\.(\w+)", html[html.index("// ---------- AI Engine tab"):html.index("async function loadLane")]))
        used |= {"c_" + k for k in ("lane_added", "lane_removed", "skill_added", "skill_changed", "skill_removed", "row_added", "row_removed", "process", "card")}
        used |= {"s_" + k for k in ("new", "best", "good", "fix", "rework")}
        ui = html[html.index("const UI = {"):html.index("const LANGS =")]
        blocks = re.findall(r"^    eg: \{(.*)\},$", ui, re.M)
        self.assertEqual(len(blocks), 5)
        for b in blocks:
            self.assertEqual(used - set(re.findall(r"\b(\w+): '", b)), set(), b[:30])
        for lang in ("en", "ko", "ja", "zh", "es"):
            self.assertRegex(ui, rf"\n  {lang}: \{{\n    tabs: \{{ hub: '[^']+', engine: '[^']+'")

    def test_rating_states_match_the_server_words(self) -> None:
        from agent_hub import rating
        html = BOARD.read_text(encoding="utf-8")
        for s in rating.STATES:
            self.assertIn(f"s_{s}:", html)


if __name__ == "__main__":
    unittest.main()


class TwoEnginesSkillsTest(unittest.TestCase):
    """Engines sharing one skills folder each list only their own skills; a skill both name is shared."""

    def setUp(self) -> None:
        self.home, _ = make_engine_home()
        sk = self.home / ".agents/skills"
        for n in ("sound-mix", "voice", "notes"):
            (sk / n).mkdir()
            (sk / n / "SKILL.md").write_text(n)
        (self.home / ".tower" / "lanes").mkdir(exist_ok=True)
        (self.home / ".tower/lanes/story.md").write_text('Use skill("layout") and skill("notes").')
        (self.home / ".tower/lanes/mixer.md").write_text('Use skill("sound-mix") and skill("notes").')
        raw = json.loads((self.home / ".tower/tower.json").read_text())
        raw["lanes"]["mixer"] = {"runtime": "codex"}
        raw["engines"]["audio"] = {"skills": ".agents/skills", "lanes": ["mixer"], "skill_names": ["voice"]}
        (self.home / ".tower/tower.json").write_text(json.dumps(raw))
        from agent_hub import config as config_mod
        self.cfg = config_mod.load(self.home)

    def names(self, eid: str) -> dict[str, bool]:
        return {s["name"]: s["shared"] for s in engines.engine_skills(self.cfg, eid)}

    def test_each_engine_lists_its_own_and_shared_once(self) -> None:
        self.assertEqual(self.names("anim"), {"layout": False, "notes": True})
        self.assertEqual(self.names("audio"), {"sound-mix": False, "voice": False, "notes": True})

    def test_snapshot_follows_the_engine_scope(self) -> None:
        self.assertEqual(sorted(engines.live_snapshot(self.cfg, "audio", 0.0)["skills"]), ["notes", "sound-mix", "voice"])

    def test_engine_with_its_own_folder_lists_all_of_it(self) -> None:
        raw = json.loads((self.home / ".tower/tower.json").read_text())
        raw["engines"]["audio"]["skills"] = ".agents/audio-skills"
        (self.home / ".agents/audio-skills/x").mkdir(parents=True)
        (self.home / ".agents/audio-skills/x/SKILL.md").write_text("x")
        (self.home / ".tower/tower.json").write_text(json.dumps(raw))
        from agent_hub import config as config_mod
        cfg = config_mod.load(self.home)
        self.assertEqual([s["name"] for s in engines.engine_skills(cfg, "audio")], ["x"])
