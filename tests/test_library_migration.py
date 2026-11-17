import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from agent_hub import config as config_mod, engines, libmigrate, line, processes, stagemodel
from agent_hub.install import refresh
from agent_hub.store import Store
from tests.helpers import make_home
from tests.test_process import FRAMES, PROCESS, make_engine_home

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


class HalfDoneTest(OldWorkspace):
    def crash_at(self, target: str) -> None:
        with mock.patch(target, side_effect=OSError("boom")), self.assertRaises(OSError):
            libmigrate.migrate(config_mod.load(self.home))

    def engine_rows(self) -> list[dict]:
        cfg = config_mod.load(self.home)
        return engines.build(self.home / cfg.engines["anim"].library, self.home / SKILLS)

    def converted_sub_folders(self) -> list[str]:
        return sorted(p.name for p in (self.home / "processes/sub").iterdir())

    def test_a_crash_before_tower_json_changes_leaves_the_engine_working_and_the_next_update_finishes(self) -> None:
        original = (self.home / OLD).read_text(encoding="utf-8")
        self.crash_at("agent_hub.install.edit_config")
        self.assertEqual((self.home / OLD).read_text(encoding="utf-8"), original)
        self.assertEqual(len(self.engine_rows()), 3)
        out = self.update()
        self.assertIn(f"converted: {OLD}", out)
        self.assertEqual(len(self.engine_rows()), 3)
        self.assertIn(NEW, (self.home / OLD).read_text(encoding="utf-8"))
        self.assertEqual(self.converted_sub_folders(), ["anim-sound-cue", "anim-storyboard", "anim-title-card"])

    def test_a_crash_after_tower_json_changed_is_finished_and_the_tasks_are_remapped(self) -> None:
        s = Store(self.cfg.db)
        tid = s.add_task("story", "old task", "Line: #1 Storyboard · stage 2 · make", "anim", None)
        s.update_task(tid, line_row="1", line_stage="2", line_mode="make")
        self.crash_at("agent_hub.store.Store.remap_line_rows")
        self.assertEqual(json.loads((self.home / ".tower/tower.json").read_text())["engines"]["anim"]["library"], NEW)
        self.assertEqual(len(self.engine_rows()), 3)
        self.assertEqual(Store(self.cfg.db).task(tid)["line_row"], "1")
        out = self.update()
        self.assertIn("converted:", out)
        self.assertEqual(Store(self.cfg.db).task(tid)["line_row"], "anim/anim-storyboard")
        self.assertIn(NEW, (self.home / OLD).read_text(encoding="utf-8"))
        self.assertIn("processes/sub/anim-storyboard/process.md", (self.home / CARD).read_text(encoding="utf-8"))
        self.assertEqual(self.converted_sub_folders(), ["anim-sound-cue", "anim-storyboard", "anim-title-card"])

    def test_a_crash_with_only_some_cards_turned_into_pointers_is_finished(self) -> None:
        Store(self.cfg.db)
        self.crash_at("agent_hub.store.Store.remap_line_rows")
        (self.home / CARD).write_text("Moved to `processes/sub/anim-storyboard/process.md` (the process library) by `dah update`. Edit it there.\n",
                                      encoding="utf-8")
        self.update()
        self.assertEqual(self.converted_sub_folders(), ["anim-sound-cue", "anim-storyboard", "anim-title-card"])
        self.assertIn(NEW, (self.home / OLD).read_text(encoding="utf-8"))
        self.assertIn("anim-storyboard", (self.home / CARD).read_text(encoding="utf-8"))
        self.assertIn("beats", (self.home / "processes/sub/anim-storyboard/patterns/beats.md").read_text(encoding="utf-8").lower())

    def test_a_full_file_written_before_the_crash_does_not_block_the_next_update(self) -> None:
        self.crash_at("agent_hub.libmigrate._add_lane_sections")
        self.assertTrue((self.home / NEW).is_file())
        out = self.update()
        self.assertNotIn("not converted", out)
        self.assertEqual(self.converted_sub_folders(), ["anim-sound-cue", "anim-storyboard", "anim-title-card"])
        self.assertIn(NEW, (self.home / OLD).read_text(encoding="utf-8"))

    def test_a_sub_folder_of_your_own_with_another_card_is_not_overwritten(self) -> None:
        mine = self.home / "processes/sub/anim-storyboard"
        mine.mkdir(parents=True)
        (mine / "process.md").write_text("# Process: mine\n", encoding="utf-8")
        self.update()
        self.assertEqual((mine / "process.md").read_text(encoding="utf-8"), "# Process: mine\n")
        self.assertIn("anim-storyboard-2", self.converted_sub_folders())

    def test_a_half_made_empty_sub_folder_is_reused(self) -> None:
        (self.home / "processes/sub/anim-storyboard").mkdir(parents=True)
        self.update()
        self.assertEqual(self.converted_sub_folders(), ["anim-sound-cue", "anim-storyboard", "anim-title-card"])

    def test_tasks_of_a_second_engine_on_the_same_library_are_remapped(self) -> None:
        raw = json.loads((self.home / ".tower/tower.json").read_text())
        raw["engines"]["anim2"] = {**raw["engines"]["anim"], "lanes": []}
        (self.home / ".tower/tower.json").write_text(json.dumps(raw))
        s = Store(self.cfg.db)
        tid = s.add_task("story", "second", "Line: #1 Storyboard · stage 2 · make", "anim2", None)
        s.update_task(tid, line_row="1", line_stage="2", line_mode="make")
        self.update()
        self.assertEqual(Store(self.cfg.db).task(tid)["line_row"], "anim/anim-storyboard")


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


SHARED_LIBRARY = """# Pattern library

| # | Type | Lane · skill | Process | Pattern | Model · effort |
|---|---|---|---|---|---|
| 1 | Storyboard | story · layout | confirmed v2 (process/storyboard.md) | confirmed | sonnet/medium |
| 2 | Animatic | story · layout | confirmed v2 (process/storyboard.md) | confirmed | opus/high |
| 3 | Sound cue | story · layout | confirmed v1 (process/cue.md) | confirmed | haiku/low |
"""


class SharedCardsTest(OldWorkspace):
    def setUp(self) -> None:
        super().setUp()
        (self.home / OLD).write_text(SHARED_LIBRARY, encoding="utf-8")
        cue = self.home / ".agents/skills/layout/references/process/cue.md"
        cue.write_text(cue.read_text(encoding="utf-8").replace("patterns/pick.md", "patterns/frames.md"), encoding="utf-8")

    def subs(self) -> list[str]:
        return sorted(p.name for p in (self.home / "processes/sub").iterdir())

    def test_rows_sharing_a_process_card_become_one_sub_process(self) -> None:
        self.update()
        self.assertEqual(self.subs(), ["anim-sound-cue", "anim-storyboard"])
        steps = processes.parse_full((self.home / NEW).read_text(encoding="utf-8"), "anim")["steps"]
        self.assertEqual([(s["old"], s["sub"], s["model"]) for s in steps],
                         [("1", "anim-storyboard", "sonnet/medium"), ("2", "anim-storyboard", "opus/high"), ("3", "anim-sound-cue", "haiku/low")])
        self.assertIn("processes/sub/anim-storyboard/process.md", (self.home / CARD).read_text(encoding="utf-8"))

    def test_each_old_row_keeps_its_sub_process_and_model(self) -> None:
        s = Store(self.cfg.db)
        tids = {n: s.add_task("story", f"row {n}", "x", "anim", None) for n in ("1", "2")}
        for n, tid in tids.items():
            s.update_task(tid, line_row=n)
        self.update()
        self.assertEqual({Store(self.cfg.db).task(t)["line_row"] for t in tids.values()}, {"anim/anim-storyboard"})
        cfg = config_mod.load(self.home)
        table, library = stagemodel.table_for(cfg, "story"), stagemodel.library_for(cfg)
        self.assertEqual(stagemodel.resolve(table, "Line: #1 Storyboard · stage 1 · make", library), ("sonnet", "medium"))
        self.assertEqual(stagemodel.resolve(table, "Line: #2 Animatic · stage 1 · make", library), ("opus", "high"))
        legacy = processes.lane_full(cfg, "story")
        for row in ("1", "2"):
            self.assertEqual(engines.line_fields(f"Line: #{row} X · stage 2 · make", legacy)["line_row"], "anim/anim-storyboard")
        out = self.dah("task", "story", "--title", "t", "Line: #2 Animatic · stage 2 · make\nx", lane="hub")
        self.assertIn("anim / anim-storyboard", out)

    def test_a_pattern_card_shared_by_two_sub_processes_is_copied_and_the_stub_names_both(self) -> None:
        out = self.update()
        frames = (self.home / ".agents/skills/layout/references/patterns/frames.md").read_text(encoding="utf-8")
        for sub in ("anim-storyboard", "anim-sound-cue"):
            self.assertEqual((self.home / f"processes/sub/{sub}/patterns/frames.md").read_text(encoding="utf-8"), FRAMES)
            self.assertIn(f"processes/sub/{sub}/patterns/frames.md", frames)
        shared = [ln for ln in out.splitlines() if "shared by 2 sub-processes" in ln]
        self.assertEqual(len(shared), 1)
        self.assertIn(".agents/skills/layout/references/patterns/frames.md", shared[0])

    def test_a_second_update_changes_nothing(self) -> None:
        self.update()
        first = tree(self.home)
        self.update()
        self.assertEqual(tree(self.home), first)


if __name__ == "__main__":
    unittest.main()
