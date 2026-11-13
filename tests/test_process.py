from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_hub import config as config_mod
from agent_hub import engines, stagemodel
from agent_hub.store import Store

ROOT = Path(__file__).resolve().parent.parent

LIBRARY = """# Pattern library

| # | Type | Lane · skill | Process | Pattern | Model · effort |
|---|---|---|---|---|---|
| 1 | Storyboard | story · layout | confirmed v2 (process/storyboard.md) | confirmed | 1 sonnet/medium; 2-3 opus/high |
| 2 | Sound cue | story · layout | draft v1 (process/cue.md) | none | sonnet/low |
| D5 | Title card | render · render | confirmed (no card file) | tuned v1 | opus/high |
"""

PROCESS = """# Process: Storyboard — v2

Status: confirmed since 2026-01-02

| # | Stage | Input | Output | Gate | Passed by | Pattern card |
|---|---|---|---|---|---|---|
| 1 | Beats | `brief.md` | `beats.md` | beats named | lane | patterns/beats.md |
| 2 | Frames | `beats.md` | `frames/` | one frame per beat | lane, hub | patterns/frames.md |
| 3 | Review | `frames/` | `board item` | human approves | human | patterns/missing.md |
"""

BEATS = "# Beats — v3\n\n## Status\n\nconfirmed\n\n## Recipe\n\nwrite them\n"
FRAMES = "# Frames — v1\n\n## Status\n\ntuned after two rounds\n"
CUE = "# Process: Sound cue — v1\n\nStatus: draft\n\n| # | Stage | Output | Pattern card |\n|---|---|---|---|\n| 1 | Pick | `cue.wav` | patterns/pick.md |\n"
PICK = "# Pick — v1\n\n## Status\n\ndraft\n"


def make_engine_home() -> tuple[Path, config_mod.Config]:
    home = Path(tempfile.mkdtemp(prefix="tower-proc-"))
    eng = home / "engines" / "anim"
    skill = home / ".agents" / "skills" / "layout" / "references"
    (skill / "process").mkdir(parents=True)
    (skill / "patterns").mkdir()
    (eng).mkdir(parents=True)
    (eng / "pattern-library.md").write_text(LIBRARY, encoding="utf-8")
    (skill / "process" / "storyboard.md").write_text(PROCESS, encoding="utf-8")
    (skill / "process" / "cue.md").write_text(CUE, encoding="utf-8")
    (skill / "patterns" / "beats.md").write_text(BEATS, encoding="utf-8")
    (skill / "patterns" / "frames.md").write_text(FRAMES, encoding="utf-8")
    (skill / "patterns" / "pick.md").write_text(PICK, encoding="utf-8")
    (home / ".tower").mkdir()
    (home / ".tower" / "tower.json").write_text(json.dumps({
        "name": "T", "lang": "en", "require_line": True,
        "lanes": {"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"}, "story": {"runtime": "codex"}, "render": {"runtime": "codex"}},
        "engines": {"anim": {"dir": "engines/anim", "library": "engines/anim/pattern-library.md", "skills": ".agents/skills",
                             "lanes": ["story", "render"]}}}))
    return home, config_mod.load(home)


class ReaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home, self.cfg = make_engine_home()
        self.lib = self.home / "engines/anim/pattern-library.md"
        self.skills = self.home / ".agents/skills"
        self.rows = engines.build(self.lib, self.skills)

    def test_library_rows_by_header(self) -> None:
        r = engines.library(self.lib)
        self.assertEqual([x["n"] for x in r], ["1", "2", "D5"])
        self.assertEqual((r[0]["type"], r[0]["lane"], r[0]["skill"]), ("Storyboard", "story", "layout"))
        self.assertEqual(r[0]["model_text"], "1 sonnet/medium; 2-3 opus/high")
        self.assertEqual(engines.library(self.home / "nope.md"), [])

    def test_process_card_and_pattern_cards(self) -> None:
        p = self.rows[0]["process"]
        self.assertEqual((p["type"], p["version"], p["status"]), ("Storyboard", 2, "confirmed"))
        self.assertEqual([s["name"] for s in p["stages"]], ["Beats", "Frames", "Review"])
        self.assertEqual((p["stages"][0]["card"]["status"], p["stages"][0]["card"]["version"]), ("confirmed", 3))
        self.assertEqual(p["stages"][1]["card"]["color"], "green")  # "tuned after two rounds"
        self.assertFalse(p["stages"][2]["card"]["exists"])
        self.assertEqual(p["stages"][2]["card"]["color"], "grey")

    def test_row_statuses(self) -> None:
        # row 1: one pattern card is missing, so the pattern is not confirmed from the cards; the table cell says confirmed
        self.assertEqual(self.rows[0]["process_status"], "confirmed")
        self.assertEqual(self.rows[1]["process_status"], "draft")
        self.assertEqual(self.rows[1]["pattern_status"], "draft")  # from the pick card
        self.assertEqual(self.rows[2]["process"], None)
        self.assertEqual((self.rows[2]["process_status"], self.rows[2]["pattern_status"]), ("confirmed", "tuned"))

    def test_pipelines_text(self) -> None:
        out = engines.pipelines(self.rows)
        self.assertIn("#1 Storyboard  [story · layout]  process confirmed v2", out)
        self.assertIn("1. Beats  →  beats.md  [confirmed v3]", out)
        self.assertIn("3. Review  →  board item  [no pattern card]", out)
        self.assertIn("(no process card found)", out)


class GateTest(unittest.TestCase):
    def setUp(self) -> None:
        home, _ = make_engine_home()
        self.rows = engines.build(home / "engines/anim/pattern-library.md", home / ".agents/skills")

    def test_accepts(self) -> None:
        self.assertEqual(engines.gate("Line: #1 Storyboard · stage 2 · process confirmed · pattern confirmed · make", self.rows), ("1", "make"))
        self.assertEqual(engines.gate("Line: D5 Title card · stage 1-2 · process confirmed · pattern tuned · make\nmodel: opus", self.rows), ("D5", "make"))
        self.assertEqual(engines.gate("Line: #2 Sound cue · prepare", self.rows), ("2", "prepare"))  # no stage needed to prepare
        self.assertEqual(engines.gate("Line: none · chore: move the exports", self.rows), (None, None))

    def test_refuses(self) -> None:
        for brief, word in [("do the thing", "does not start with `Line:`"),
                            ("Line: #1 Storyboard · stage 1", "ends without prepare"),
                            ("Line: #9 X · stage 1 · make", "row #9 is not in the library"),
                            ("Line: #1 Storyboard · make", "no stage"),
                            ("Line: #2 Sound cue · stage 1 · make", "not confirmed: process draft, pattern draft"),
                            ("Line: none", "does not start")]:
            with self.assertRaises(ValueError, msg=brief) as cm:
                engines.gate(brief, self.rows)
            self.assertIn(word, str(cm.exception), brief)

    def test_line_fields(self) -> None:
        self.assertEqual(engines.line_fields("Line: #1 Storyboard · stage 2-3 · make"), {"line_row": "1", "line_stage": "2-3", "line_mode": "make"})
        self.assertEqual(engines.line_fields("nothing")["line_row"], None)


class ModelFromLibraryTest(unittest.TestCase):
    def setUp(self) -> None:
        _, self.cfg = make_engine_home()

    def test_lane_reads_its_engines_library(self) -> None:
        t = stagemodel.table_for(self.cfg, "story")
        self.assertEqual(t, self.cfg.home / "engines/anim/pattern-library.md")
        self.assertEqual(stagemodel.resolve(t, "Line: #1 Storyboard · stage 1 · make"), ("sonnet", "medium"))
        self.assertEqual(stagemodel.resolve(t, "Line: #1 Storyboard · stage 2-3 · make"), ("opus", "high"))
        self.assertEqual(stagemodel.resolve(t, "Line: D5 Title card · stage 1 · make"), ("opus", "high"))
        self.assertEqual(stagemodel.resolve(t, "model: haiku\nLine: #2 Sound cue · stage 1 · make"), ("haiku", "low"))

    def test_without_library_falls_back_to_stage_table(self) -> None:
        self.assertIsNone(stagemodel.table_for(self.cfg, "hub"))


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home, self.cfg = make_engine_home()

    def dah(self, *args: str, ok: bool = True, lane: str = "") -> str:
        env = {**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(ROOT)}
        env.pop("TOWER_LANE", None)
        if lane:
            env["TOWER_LANE"] = lane
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.home, env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode == 0, ok, f"dah {' '.join(args)}: {p.stdout}{p.stderr}")
        return p.stdout + p.stderr

    def test_engine_show_prints_pipelines(self) -> None:
        out = self.dah("engine", "show", "anim")
        self.assertIn("pipelines:", out)
        self.assertIn("#1 Storyboard", out)
        self.assertIn("2. Frames  →  frames/  [tuned v1]", out)

    def test_task_gate_and_stage(self) -> None:
        brief = "Line: #1 Storyboard · stage 2 · process confirmed · pattern confirmed · make\nmodel: opus\neffort: high\nwork"
        bad = self.dah("task", "story", "--title", "t", "just work", ok=False, lane="hub")
        self.assertIn("Line:", bad)
        self.dah("task", "story", "--title", "t2", "Line: #2 Sound cue · stage 1 · make\nx", ok=False, lane="hub")
        self.dah("task", "story", "--title", "t3", brief, lane="hub")
        s = Store(self.cfg.db)
        t = s.next_queued("story")
        self.assertEqual((t["line_row"], t["line_stage"], t["line_mode"]), ("1", "2", "make"))
        self.dah("stage", "3", ok=False, lane="story")  # nothing running yet
        s.update_task(t["id"], state="running")
        self.assertIn("stage 3", self.dah("stage", "3", lane="story"))
        self.assertEqual(s.live_task("story")["stage_now"], "3")

    def test_gate_is_off_by_default(self) -> None:
        raw = json.loads((self.home / ".tower/tower.json").read_text())
        raw.pop("require_line")
        (self.home / ".tower/tower.json").write_text(json.dumps(raw))
        self.dah("task", "story", "--title", "t", "no header at all", lane="hub")


class TemplatesRetroTest(unittest.TestCase):
    def test_protocol_and_lane_template_ask_for_the_retro(self) -> None:
        for rel in ("PROTOCOL.md", "lanes/team.md"):
            text = (ROOT / "agent_hub/templates" / rel).read_text(encoding="utf-8")
            self.assertIn("|| retro: cost=... | corrected=... | rule=...", text, rel)
            self.assertIn("dah stage N", text, rel)
