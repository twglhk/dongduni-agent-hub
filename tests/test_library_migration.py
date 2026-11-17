import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from agent_hub import config as config_mod, engines, line, processes, stagemodel
from agent_hub.install import refresh
from agent_hub.store import Store
from tests.helpers import make_home
from tests.test_process import PROCESS, make_engine_home

ROOT = Path(__file__).resolve().parents[1]
NEW = "processes/full/anim.md"
OLD = "engines/anim/pattern-library.md"
CARD = ".agents/skills/layout/references/process/storyboard.md"
SKILLS = ".agents/skills"
OLD_LINE = "Line: #1 Storyboard · stage 2 · process confirmed · pattern confirmed · make\nmodel: opus\neffort: high\nwork"


def tree(home: Path) -> dict[str, str]:
    return {p.relative_to(home).as_posix(): hashlib.sha1(p.read_bytes()).hexdigest()
            for p in sorted(home.rglob("*")) if p.is_file() and ".tower/state" not in p.as_posix()}


class OldWorkspace(unittest.TestCase):
    def setUp(self) -> None:
        self.home, _ = make_engine_home()
        (self.home / ".tower/lanes").mkdir(exist_ok=True)
        for lane in ("story", "render"):
            (self.home / f".tower/lanes/{lane}.md").write_text(f"# {lane} lane\n\nHand-written role.\n", encoding="utf-8")
        self.cfg = config_mod.load(self.home)

    def dah(self, *args: str, ok: bool = True, lane: str = "") -> str:
        env = {**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(ROOT)}
        env.pop("TOWER_LANE", None)
        if lane:
            env["TOWER_LANE"] = lane
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.home, env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode == 0, ok, f"dah {' '.join(args)}: {p.stdout}{p.stderr}")
        return p.stdout + p.stderr

    def update(self) -> str:
        return self.dah("update", "--no-upgrade", "--no-restart")


class ConversionTest(OldWorkspace):
    def test_update_converts_the_pattern_library_step_by_step(self) -> None:
        original = (self.home / OLD).read_text(encoding="utf-8")
        before_card = (self.home / CARD).read_text(encoding="utf-8")
        out = self.update()
        self.assertIn(f"converted: {OLD}", out)
        full = processes.parse_full((self.home / NEW).read_text(encoding="utf-8"), "anim")
        self.assertEqual([(s["n"], s["old"], s["sub"], s["model"]) for s in full["steps"]],
                         [(1, "1", "anim-storyboard", "1 sonnet/medium; 2-3 opus/high"), (2, "2", "anim-sound-cue", "sonnet/low"),
                          (3, "D5", "anim-title-card", "opus/high")])
        self.assertTrue(full["frame_exception"])
        self.assertEqual((self.home / (OLD + ".bak")).read_text(encoding="utf-8"), original)
        self.assertIn(NEW, (self.home / OLD).read_text(encoding="utf-8"))
        self.assertEqual(json.loads((self.home / ".tower/tower.json").read_text())["engines"]["anim"]["library"], NEW)
        sub = self.home / "processes/sub/anim-storyboard"
        card = (sub / "process.md").read_text(encoding="utf-8")
        self.assertIn("- Skill: `layout`", card)
        self.assertEqual(card.replace("\n- Skill: `layout`\n", ""), before_card)
        self.assertEqual((sub / "patterns/beats.md").read_text(encoding="utf-8"), "# Beats — v3\n\n## Status\n\nconfirmed\n\n## Recipe\n\nwrite them\n")
        self.assertEqual((sub / "patterns/frames.md").read_text(encoding="utf-8"), "# Frames — v1\n\n## Status\n\ntuned after two rounds\n")
        self.assertIn("processes/sub/anim-storyboard/process.md", (self.home / CARD).read_text(encoding="utf-8"))
        old_beats = self.home / ".agents/skills/layout/references/patterns/beats.md"
        self.assertIn("processes/sub/anim-storyboard/patterns/beats.md", old_beats.read_text(encoding="utf-8"))
        for lane in ("story", "render"):
            text = (self.home / f".tower/lanes/{lane}.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith(f"# {lane} lane\n\nHand-written role.\n"))
            self.assertEqual(processes.lane_uses(text), ["anim"])

    def test_the_converted_library_reads_like_the_old_one(self) -> None:
        old = engines.build(self.home / OLD, self.home / SKILLS)
        self.update()
        lib = processes.load(self.home)
        self.assertEqual(lib["problems"], ["sub-process `anim-storyboard` stage 3: pattern card "
                                           "processes/sub/anim-storyboard/patterns/missing.md is missing"])
        self.assertEqual(lib["lanes"], {"render": ["anim"], "story": ["anim"]})
        rows = engines.build(self.home / NEW, self.home / SKILLS)
        self.assertEqual([r["type"] for r in rows], [r["type"] for r in old])
        self.assertEqual([r["old"] for r in rows], ["1", "2", "D5"])
        self.assertEqual((rows[0]["process_status"], rows[0]["skill"]), (old[0]["process_status"], old[0]["skill"]))
        self.assertEqual([s["name"] for s in rows[0]["process"]["stages"]], [s["name"] for s in old[0]["process"]["stages"]])
        self.assertEqual([bool(s["card"] and s["card"]["exists"]) for s in rows[0]["process"]["stages"]], [True, True, False])
        self.assertEqual(lib["sub"]["anim-storyboard"]["status"], "confirmed")
        self.assertEqual(lib["sub"]["anim-storyboard"]["skills"], ["layout"])

    def test_a_row_without_a_card_file_becomes_a_draft_to_fill_in(self) -> None:
        self.update()
        text = (self.home / "processes/sub/anim-title-card/process.md").read_text(encoding="utf-8")
        self.assertIn("Status: draft", text)
        self.assertIn("row D5", text)
        self.assertIn("confirmed (no card file)", text)
        self.assertEqual(processes.load(self.home)["sub"]["anim-title-card"]["stages"], [])

    def test_a_second_update_changes_nothing(self) -> None:
        self.update()
        first = tree(self.home)
        out = self.update()
        self.assertNotIn("converted:", out)
        self.assertEqual(tree(self.home), first)

    def test_a_crash_before_tower_json_is_finished_by_the_next_update(self) -> None:
        self.update()
        raw = json.loads((self.home / ".tower/tower.json").read_text())
        raw["engines"]["anim"]["library"] = OLD
        (self.home / ".tower/tower.json").write_text(json.dumps(raw))
        (self.home / OLD).write_text((self.home / (OLD + ".bak")).read_text(encoding="utf-8"), encoding="utf-8")
        (self.home / NEW).unlink()
        out = self.update()
        self.assertIn(f"converted: {OLD}", out)
        self.assertTrue((self.home / NEW).is_file())

    def test_it_leaves_the_old_library_alone_when_the_new_name_is_taken(self) -> None:
        (self.home / "processes/full").mkdir(parents=True)
        (self.home / NEW).write_text("mine\n", encoding="utf-8")
        before = (self.home / OLD).read_text(encoding="utf-8")
        out = self.update()
        self.assertIn("not converted", out)
        self.assertEqual((self.home / OLD).read_text(encoding="utf-8"), before)
        self.assertFalse((self.home / (OLD + ".bak")).exists())
        self.assertEqual(json.loads((self.home / ".tower/tower.json").read_text())["engines"]["anim"]["library"], OLD)
        self.dah("task", "story", "--title", "t", OLD_LINE, lane="hub")

    def test_finished_tasks_keep_their_rating_row(self) -> None:
        s = Store(self.cfg.db)
        tid = s.add_task("story", "old task", "Line: #1 Storyboard · stage 2 · make", "anim", None)
        s.update_task(tid, line_row="1", line_stage="2", line_mode="make")
        self.update()
        self.assertEqual(Store(self.cfg.db).task(tid)["line_row"], "anim/anim-storyboard")

    def test_a_workspace_without_engines_is_untouched(self) -> None:
        cfg = make_home()
        before = tree(cfg.home)
        self.assertEqual(refresh(cfg)["converted"], [])
        self.assertFalse((cfg.home / "processes").exists())
        self.assertTrue(set(before) <= set(tree(cfg.home)))


class EngineShowAfterConversionTest(OldWorkspace):
    def test_engine_show_before_and_after(self) -> None:
        self.assertIn("#1 Storyboard", self.dah("engine", "show", "anim"))
        self.update()
        out = self.dah("engine", "show", "anim")
        self.assertIn("anim/anim-storyboard Storyboard", out)
        self.assertIn("1. Beats  →  beats.md  [confirmed v3]", out)
        self.assertIn("2. Frames  →  frames/  [tuned v1]", out)
        self.assertIn("model 1 sonnet/medium; 2-3 opus/high", out)


class OldLineAfterConversionTest(OldWorkspace):
    def test_a_task_with_the_old_line_before_and_after(self) -> None:
        self.dah("task", "story", "--title", "before", OLD_LINE, lane="hub")
        self.update()
        out = self.dah("task", "story", "--title", "old form", OLD_LINE, lane="hub")
        self.assertIn("old pattern-library row", out)
        self.dah("task", "story", "--title", "new form",
                 "Line: anim / anim-storyboard · stage 2 · make\nmodel: opus\neffort: high\nwork", lane="hub")
        rows = [(t["title"], t["line_row"], t["line_stage"], t["line_mode"]) for t in Store(self.cfg.db).tasks()]
        self.assertEqual(sorted(rows), [("before", "anim/anim-storyboard", "2", "make"),
                                        ("new form", "anim/anim-storyboard", "2", "make"),
                                        ("old form", "anim/anim-storyboard", "2", "make")])

    def test_the_old_line_keeps_the_gate_and_says_how_to_fix_what_it_cannot_map(self) -> None:
        self.update()
        bad = self.dah("task", "story", "--title", "t", "Line: #2 Sound cue · stage 1 · make\nx", ok=False, lane="hub")
        self.assertIn("`anim-sound-cue` is not confirmed", bad)
        gone = self.dah("task", "story", "--title", "t", "Line: #9 Nothing · stage 1 · make\nx", ok=False, lane="hub")
        self.assertIn("`#9` is a row of the old pattern library", gone)
        self.assertIn("#1 = anim-storyboard", gone)
        self.assertIn("Line: anim / <sub-process> · stage <n>", gone)
        self.dah("task", "story", "--title", "t", "Line: D5 Title card · prepare\nmodel: opus\neffort: high\nx", lane="hub")
        self.dah("task", "story", "--title", "t", "Line: none · chore: tidy\nmodel: opus\neffort: high\nx", lane="hub")
        self.dah("task", "story", "--title", "t", "no header", ok=False, lane="hub")

    def test_the_model_comes_from_the_converted_cell_for_both_forms(self) -> None:
        self.update()
        cfg = config_mod.load(self.home)
        table, library = stagemodel.table_for(cfg, "story"), stagemodel.library_for(cfg)
        self.assertEqual(table, self.home / NEW)
        for brief, want in [("Line: #1 Storyboard · stage 1 · make", ("sonnet", "medium")),
                            ("Line: #1 Storyboard · stage 2-3 · make", ("opus", "high")),
                            ("Line: anim / anim-storyboard · stage 1 · make", ("sonnet", "medium")),
                            ("Line: D5 Title card · stage 1 · make", ("opus", "high")),
                            ("model: haiku\nLine: #2 Sound cue · stage 1 · make", ("haiku", "low")),
                            ("Line: #9 Nothing · stage 1 · make", (None, None))]:
            self.assertEqual(stagemodel.resolve(table, brief, library), want, brief)

    def test_line_fields_map_the_old_row(self) -> None:
        self.update()
        legacy = processes.lane_full(config_mod.load(self.home), "story")
        self.assertEqual(engines.line_fields("Line: #1 Storyboard · stage 2-3 · make", legacy),
                         {"line_row": "anim/anim-storyboard", "line_stage": "2-3", "line_mode": "make"})
        self.assertIsNone(engines.line_fields("Line: #9 X · stage 1 · make", legacy)["line_row"])
        self.assertEqual(engines.line_fields("Line: #1 Storyboard · stage 2 · make")["line_row"], "1")

    def test_translate_keeps_the_rest_of_the_brief(self) -> None:
        full = ("anim", [{"old": "1", "sub": "anim-storyboard"}])
        brief = "\nLine: #1 Storyboard · stage 1-2 · process draft · prepare\nmodel: opus\nbody"
        self.assertEqual(line.translate_legacy(brief, full), "\nLine: anim / anim-storyboard · stage 1-2 · prepare\nmodel: opus\nbody")
        self.assertEqual(line.translate_legacy("Line: new Cover · prepare", full), "Line: new Cover · prepare")
        self.assertEqual(line.translate_legacy(brief, None), brief)

    def test_a_workspace_that_is_not_converted_keeps_the_old_gate(self) -> None:
        self.dah("task", "story", "--title", "t", OLD_LINE, lane="hub")
        refused = self.dah("task", "story", "--title", "t", "Line: #2 Sound cue · stage 1 · make\nx", ok=False, lane="hub")
        self.assertIn("not confirmed", refused)


class SharedPatternCardTest(unittest.TestCase):
    def test_two_stages_naming_one_pattern_card_get_one_copy(self) -> None:
        home, _ = make_engine_home()
        card = home / CARD
        card.write_text(PROCESS.replace("patterns/missing.md", "patterns/frames.md"), encoding="utf-8")
        (home / ".tower/lanes").mkdir(exist_ok=True)
        (home / ".tower/lanes/story.md").write_text('# story\n\nRun `skill("layout")` before each task.\n', encoding="utf-8")
        self.assertEqual(len(refresh(config_mod.load(home))["converted"]), 3)
        lib = processes.load(home)
        self.assertEqual(lib["problems"], [])
        self.assertEqual(lib["lanes"]["story"], ["anim"])
        self.assertEqual(sorted(p.name for p in (home / "processes/sub/anim-storyboard/patterns").iterdir()), ["beats.md", "frames.md"])
        rows = engines.build(home / NEW, home / SKILLS)
        self.assertIn("layout", [s["name"] for s in engines.lane_skills("", home / SKILLS, rows, "story")])


if __name__ == "__main__":
    unittest.main()
