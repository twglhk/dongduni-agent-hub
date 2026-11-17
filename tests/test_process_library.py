import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_hub import engines, line, processes, stagemodel

ROOT = Path(__file__).resolve().parents[1]

CARD = """# Process: {title} — v2

Status: {status}

- Skill: `.claude/skills/{skill}/SKILL.md`

| # | Stage | Input | Output | Gate | Passed by | Pattern card |{extra_head}
|---|---|---|---|---|---|---|{extra_sep}
| 1 | First | `in` | `out` | ok | lane | `patterns/one.md` |{extra1}
| 2 | Second | `out` | `final` | ok | lane | `patterns/two.md` |{extra2}
"""
PATTERN = "# {name} — v2\n\n## Status\n\n{status}\n"
FULL = """# Full process: {title}

Deliverable: {deliverable}
{frame}
| # | Frame | Sub-process | What it does | Model · effort |
|---|---|---|---|---|
{rows}
"""


def write_sub(home: Path, sid: str, title: str, status: str = "draft", pattern: str = "draft", skill: str = "craft",
              stage_skill: str = "") -> None:
    d = home / "processes" / "sub" / sid
    (d / "patterns").mkdir(parents=True, exist_ok=True)
    extra = (" Skill |", "---|", f" `{stage_skill}` |", " |") if stage_skill else ("", "", "", "")
    (d / "process.md").write_text(CARD.format(title=title, status=status, skill=skill, extra_head=extra[0], extra_sep=extra[1],
                                              extra1=extra[2], extra2=extra[3]), encoding="utf-8")
    for n in ("one", "two"):
        (d / "patterns" / f"{n}.md").write_text(PATTERN.format(name=n, status=pattern), encoding="utf-8")


def write_full(home: Path, fid: str, title: str, steps: list[tuple[str, str, str]], frame: str = "") -> None:
    d = home / "processes" / "full"
    d.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"| {i} | {fr} | `{s}` | does it | {m} |" for i, (fr, s, m) in enumerate(steps, 1))
    (d / f"{fid}.md").write_text(FULL.format(title=title, deliverable="one thing", frame=f"\n{frame}\n" if frame else "",
                                             rows=rows), encoding="utf-8")


def make_library(home: Path) -> None:
    write_sub(home, "article-planning", "Planning", "confirmed", "confirmed", skill="article-craft")
    write_sub(home, "article-production", "Production", "confirmed", "tuned", skill="article-craft", stage_skill="render-kit")
    write_sub(home, "cover-making", "Cover", "confirmed", "confirmed", skill="covers")
    write_sub(home, "article-qa", "QA", "draft", "draft", skill="article-craft")
    write_sub(home, "app-build", "App build", "draft", "draft", skill="app-kit")
    write_full(home, "article-publishing", "Article publishing",
               [("planning", "article-planning", "opus/high"), ("production", "article-production", "1 sonnet/high; 2 opus/high"),
                ("production", "cover-making", "sonnet/medium"), ("QA", "article-qa", "sonnet/medium")])
    write_full(home, "demo-app", "Demo app production", [("production", "app-build", "opus/high"),
                                                          ("production", "cover-making", "sonnet/high")],
               frame="Frame: exception — test fixture")
    lanes = home / ".tower" / "lanes"
    lanes.mkdir(parents=True, exist_ok=True)
    (lanes / "writer.md").write_text("# writer\n\n## Processes used\n\n- `article-publishing` (processes/full/article-publishing.md)\n\n## Rules\n\n- `demo-app` is not mine\n", encoding="utf-8")
    (lanes / "covers.md").write_text("# covers\n\n## Processes used\n\n- `article-publishing`\n- `demo-app`\n", encoding="utf-8")
    (lanes / "research.md").write_text("# research\n\nNo processes.\n", encoding="utf-8")


class ParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        make_library(self.home)
        self.lib = processes.load(self.home)

    def test_full_process_steps_in_order(self) -> None:
        f = self.lib["full"]["article-publishing"]
        self.assertEqual(f["title"], "Article publishing")
        self.assertEqual(f["deliverable"], "one thing")
        self.assertEqual([s["sub"] for s in f["steps"]], ["article-planning", "article-production", "cover-making", "article-qa"])
        self.assertEqual([s["frame"] for s in f["steps"]], ["planning", "production", "production", "qa"])
        self.assertEqual(f["steps"][1]["model"], "1 sonnet/high; 2 opus/high")
        self.assertTrue(all(s["known"] for s in f["steps"]))

    def test_sub_process_stages_patterns_and_skills(self) -> None:
        s = self.lib["sub"]["article-production"]
        self.assertEqual((s["title"], s["version"], s["status"], s["pattern_status"], s["confirmed"]),
                         ("Production", 2, "confirmed", "confirmed", True))
        self.assertEqual(s["skills"], ["article-craft"])
        self.assertEqual([st["name"] for st in s["stages"]], ["First", "Second"])
        self.assertEqual(s["stages"][0]["pattern"]["path"], "processes/sub/article-production/patterns/one.md")
        self.assertTrue(s["stages"][0]["pattern"]["exists"])
        self.assertEqual(s["stages"][0]["skills"], ["render-kit"])
        self.assertEqual(s["stages"][1]["skills"], ["article-craft"])
        self.assertFalse(self.lib["sub"]["article-qa"]["confirmed"])

    def test_lane_uses_reads_only_its_section(self) -> None:
        self.assertEqual(processes.lane_uses("# x\n\n## Processes used\n\n- `a-b`\n- `c` and more\n\n## Rules\n\n- `d`\n"), ["a-b", "c"])
        self.assertEqual(processes.lane_uses("## Processes used\n\nNone yet.\n"), [])
        self.assertIsNone(processes.lane_uses("# no section\n- `a`\n"))
        self.assertEqual(self.lib["lanes"], {"covers": ["article-publishing", "demo-app"], "writer": ["article-publishing"]})

    def test_card_skills_shapes(self) -> None:
        self.assertEqual(processes.card_skills("- Skill: `.claude/skills/demo-storyboard/SKILL.md`"), ["demo-storyboard"])
        self.assertEqual(processes.card_skills("- Skills: `a-kit`, `.agents/skills/b/SKILL.md`, `docs/x/skill.md`"),
                         ["a-kit", "b", "docs/x/skill.md"])


class ReverseRefTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        make_library(self.home)
        self.lib = processes.load(self.home)

    def test_shared_sub_process_names_every_full_process_and_lane(self) -> None:
        t = self.lib["sub"]["cover-making"]
        self.assertEqual(t["used_by_full"], ["article-publishing", "demo-app"])
        self.assertEqual(sorted(t["used_by_lanes"]), ["covers", "writer"])
        self.assertEqual(self.lib["sub"]["app-build"]["used_by_lanes"], ["covers"])

    def test_full_process_lanes_come_from_lane_files(self) -> None:
        self.assertEqual(self.lib["full"]["article-publishing"]["lanes"], ["covers", "writer"])
        self.assertEqual(self.lib["full"]["demo-app"]["lanes"], ["covers"])

    def test_status_of_a_full_process(self) -> None:
        self.assertEqual(self.lib["full"]["article-publishing"]["status"], "draft")
        write_sub(self.home, "article-qa", "QA", "confirmed", "confirmed")
        self.assertEqual(processes.load(self.home)["full"]["article-publishing"]["status"], "confirmed")

    def test_problems(self) -> None:
        self.assertEqual(self.lib["problems"], [])
        write_full(self.home, "broken", "Broken", [("planning", "nope", "")])
        write_sub(self.home, "orphan", "Orphan")
        (self.home / "processes/sub/orphan/patterns/two.md").unlink()
        (self.home / ".tower/lanes/x.md").write_text("## Processes used\n\n- `ghost`\n", encoding="utf-8")
        (self.home / "processes/README.md").write_text("# Library\n\n`broken`\n", encoding="utf-8")
        text = "\n".join(processes.load(self.home)["problems"])
        for word in ("lane x uses full process `ghost`", "names `nope`", "`broken` does not hold planning, production and QA",
                     "`orphan` is in no full process", "pattern card processes/sub/orphan/patterns/two.md is missing",
                     "README.md does not list the full process `demo-app`"):
            self.assertIn(word, text)
        self.assertNotIn("`demo-app` does not hold", text)

    def test_view_is_json_and_names_skills(self) -> None:
        (self.home / ".claude/skills/article-craft").mkdir(parents=True)
        v = processes.view(self.home)
        json.dumps(v)
        full = next(f for f in v["full"] if f["id"] == "article-publishing")
        self.assertEqual([(s["sub"], s["title"], s["status"]) for s in full["steps"]][2], ("cover-making", "Cover", "confirmed"))
        sub = next(s for s in v["sub"] if s["id"] == "article-planning")
        self.assertEqual(sub["skills"], [{"name": "article-craft", "found": True, "doc": None}])
        self.assertEqual(sub["used_by_full"], ["article-publishing"])
        covers = next(s for s in v["sub"] if s["id"] == "cover-making")
        self.assertEqual(covers["skills"], [{"name": "covers", "found": False, "doc": None}])

    def test_engine_rows_for_the_old_views(self) -> None:
        skills = self.home / ".claude/skills"
        rows = engines.build(self.home / "processes/full/article-publishing.md", skills)
        self.assertEqual([r["n"] for r in rows], ["article-publishing/article-planning", "article-publishing/article-production",
                                                  "article-publishing/cover-making", "article-publishing/article-qa"])
        r = rows[1]
        self.assertEqual((r["type"], r["skill"], r["process_status"], r["pattern_status"], r["lanes"]),
                         ("Production", "article-craft", "confirmed", "confirmed", ["covers", "writer"]))
        self.assertTrue(all(st["card"]["exists"] for st in r["process"]["stages"]))
        self.assertTrue(Path(os.path.normpath(skills / r["process"]["stages"][0]["card"]["path"])).is_file())
        self.assertTrue(engines.confirmed(r))
        self.assertEqual(len(engines.library(self.home / "processes/full/demo-app.md")), 2)
        view = engines.row_view(rows, "article-publishing/article-production", "2")
        self.assertEqual((view["type"], view["now"], len(view["stages"])), ("Production", "2", 2))
        self.assertIn("article-craft", [s["name"] for s in engines.lane_skills("", skills, rows, "writer")])


class LineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        make_library(self.home)
        self.lib = processes.load(self.home)

    def ok(self, brief: str) -> dict:
        return line.gate(brief, self.lib)

    def test_accepts(self) -> None:
        p = self.ok("Line: article-publishing / article-production · stage 2 · process confirmed · pattern confirmed · make\nmodel: opus")
        self.assertEqual((p["full"], p["sub"], p["stage"], p["mode"]), ("article-publishing", "article-production", "2", "make"))
        self.assertEqual(self.ok("Line: demo-app / cover-making · stage 1-2 · make")["mode"], "make")
        self.assertEqual(self.ok("Line: article-publishing / article-qa · stage 1 · fix")["mode"], "fix")
        self.assertEqual(self.ok("Line: article-publishing / article-qa · prepare")["mode"], "prepare")
        self.assertEqual(self.ok("Line: article-publishing · prepare")["sub"], None)
        self.assertTrue(self.ok("Line: none · chore: move the exports")["chore"])
        self.assertTrue(self.ok("Line: new Cover art · prepare")["full_new"])
        self.assertTrue(self.ok("Line: article-publishing / new Cover check · prepare")["sub_new"])
        self.assertFalse(self.ok("Line: new Demo trailer / cover-making · prepare")["sub_new"])
        self.assertEqual(self.ok("Line: demo-app / article-qa · prepare")["mode"], "prepare")

    def test_a_sub_process_used_twice_with_different_models_needs_a_model_line(self) -> None:
        write_full(self.home, "twice", "Twice", [("production", "cover-making", "sonnet/medium"),
                                                 ("production", "cover-making", "opus/high")], frame="Frame: exception — test fixture")
        lib = processes.load(self.home)
        with self.assertRaises(ValueError) as r:
            line.gate("Line: twice / cover-making · stage 1 · make", lib)
        self.assertIn("more than once", str(r.exception))
        self.assertIn("`model:` line", str(r.exception))
        self.assertEqual(line.gate("Line: twice / cover-making · stage 1 · make\nmodel: opus", lib)["sub"], "cover-making")
        self.assertEqual(line.gate("Line: twice / cover-making · stage 1 · make", lib, by_old_row=True)["sub"], "cover-making")

    def test_a_sub_process_used_twice_with_the_same_model_passes(self) -> None:
        write_full(self.home, "twice", "Twice", [("production", "cover-making", "sonnet/medium"),
                                                 ("production", "cover-making", "sonnet/medium")], frame="Frame: exception — test fixture")
        self.assertEqual(line.gate("Line: twice / cover-making · stage 1 · make", processes.load(self.home))["mode"], "make")

    def test_a_backwards_stage_range_is_refused(self) -> None:
        with self.assertRaises(ValueError) as r:
            self.ok("Line: demo-app / cover-making · stage 2-1 · make")
        self.assertIn("backwards", str(r.exception))

    def test_a_full_process_id_shaped_like_an_old_row_is_a_library_id(self) -> None:
        write_sub(self.home, "a1-step", "A1 step", "confirmed", "confirmed")
        write_full(self.home, "a1", "A1", [("production", "a1-step", "opus/high")], frame="Frame: exception — test fixture")
        lib = processes.load(self.home)
        self.assertTrue(line.is_library_line("Line: a1 / a1-step · stage 1 · make"))
        self.assertIsNone(line.old_row("Line: a1 / a1-step · stage 1 · make"))
        self.assertEqual(line.gate("Line: a1 / a1-step · stage 1 · make\nmodel: opus", lib)["full"], "a1")
        self.assertEqual(line.old_row("Line: D5 Title card · stage 1 · make"), "D5")

    def test_fix_on_a_sub_process_without_stages_is_refused(self) -> None:
        write_sub(self.home, "bare", "Bare", "confirmed", "confirmed")
        (self.home / "processes/sub/bare/process.md").write_text("# Process: Bare — v1\n\nStatus: confirmed\n", encoding="utf-8")
        write_full(self.home, "bare-full", "Bare", [("production", "bare", "opus/high")], frame="Frame: exception — test fixture")
        with self.assertRaises(ValueError) as r:
            line.gate("Line: bare-full / bare · stage 7 · fix", processes.load(self.home))
        self.assertIn("no stages", str(r.exception))

    def test_refuses(self) -> None:
        for brief, word in [("do the thing", "does not start with `Line:`"),
                            ("Line: article-publishing / article-planning · stage 1", "ends without prepare"),
                            ("Line: #1 Storyboard · stage 1 · make", "row of an old pattern library"),
                            ("Line: nope / article-planning · stage 1 · make", "full process `nope` is not in the library"),
                            ("Line: article-publishing / nope · stage 1 · make", "sub-process `nope` is not in the library"),
                            ("Line: Video Planning · prepare", "is not a library id"),
                            ("Line: article-publishing / article-planning · make", "no stage"),
                            ("Line: article-publishing / article-planning · stage 3 · make", "has no stage 3"),
                            ("Line: article-publishing / article-qa · stage 1 · make", "`article-qa` is not confirmed: process draft, pattern draft"),
                            ("Line: article-publishing · stage 1 · make", "runs one sub-process"),
                            ("Line: demo-app / article-planning · stage 1 · make", "is not a step of `demo-app`"),
                            ("Line: new Cover art · make", "can only be prepared"),
                            ("Line: article-publishing / new Poster · stage 1 · make", "can only be prepared"),
                            ("Line: new article publishing · prepare", "already has the full process `article-publishing`"),
                            ("Line: new Article publishing · prepare", "already has the full process `article-publishing`"),
                            ("Line: demo-app / new Cover Making · prepare", "already has the sub-process `cover-making`"),
                            ("Line: none", "ends without prepare")]:
            with self.subTest(brief=brief):
                with self.assertRaises(ValueError) as cm:
                    self.ok(brief)
                self.assertIn(word, str(cm.exception))

    def test_fields(self) -> None:
        self.assertEqual(engines.line_fields("Line: article-publishing / article-production · stage 1-2 · make"),
                         {"line_row": "article-publishing/article-production", "line_stage": "1-2", "line_mode": "make"})
        self.assertEqual(engines.line_fields("Line: article-publishing · prepare")["line_row"], "article-publishing")
        self.assertEqual(engines.line_fields("Line: new Cover art · prepare"), {"line_row": None, "line_stage": None, "line_mode": None})
        self.assertEqual(engines.line_fields("Line: #1 Storyboard · stage 2 · make")["line_row"], "1")

    def test_model_from_the_full_process(self) -> None:
        lib = self.home / "processes"
        self.assertEqual(stagemodel.resolve(None, "Line: article-publishing / article-production · stage 2 · make", lib), ("opus", "high"))
        self.assertEqual(stagemodel.resolve(None, "Line: article-publishing / article-production · stage 1 · make", lib), ("sonnet", "high"))
        self.assertEqual(stagemodel.resolve(None, "effort: low\nLine: demo-app / cover-making · stage 1 · make", lib), ("sonnet", "low"))
        self.assertEqual(stagemodel.resolve(None, "Line: new X · prepare", lib), (None, None))


class TaskGateCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        (self.home / ".tower").mkdir()
        (self.home / ".tower" / "tower.json").write_text(json.dumps({
            "name": "t", "require_line": True,
            "lanes": {"hub": {"runtime": "opencode"}, "infra": {"runtime": "opencode"}, "writer": {"runtime": "opencode"},
                      "covers": {"runtime": "opencode"}, "research": {"runtime": "opencode"}}}), encoding="utf-8")
        make_library(self.home)

    def dah(self, *args: str, ok: bool = True) -> subprocess.CompletedProcess:
        env = {**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(ROOT), "TOWER_LANE": "hub"}
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.home, env=env, capture_output=True, text=True, encoding="utf-8")
        if ok and p.returncode:
            self.fail(f"dah {' '.join(args)} failed: {p.stderr}")
        if not ok and not p.returncode:
            self.fail(f"dah {' '.join(args)} should have failed: {p.stdout}")
        return p

    def test_task_is_checked_against_the_library(self) -> None:
        self.dah("task", "writer", "--title", "t1", "Line: article-publishing / article-production · stage 2 · make\nmodel: opus\neffort: high\nwork")
        bad = self.dah("task", "writer", "--title", "t2", "Line: article-publishing / article-qa · stage 1 · make\nmodel: opus\neffort: high\nx", ok=False)
        self.assertIn("not confirmed", bad.stderr)
        self.dah("task", "writer", "--title", "t3", "Line: article-publishing / new Cover check · prepare\nmodel: opus\neffort: high\nbuild it")
        warn = self.dah("task", "writer", "--title", "t4", "Line: demo-app / cover-making · stage 1 · make\nmodel: opus\neffort: high\nx")
        self.assertIn("does not list `demo-app`", warn.stderr)
        self.dah("task", "research", "--title", "t5", "no header: this lane has no ## Processes used\nmodel: opus\neffort: high")
        old = self.dah("task", "covers", "--title", "t6", "Line: #1 Storyboard · stage 1 · make\nmodel: opus\neffort: high", ok=False)
        self.assertIn("old pattern library", old.stderr)
        out = self.dah("tasks").stdout
        self.assertIn("t1", out)
        self.assertNotIn("t2", out)

    def test_library_reader_prints_json(self) -> None:
        p = subprocess.run([sys.executable, "-m", "agent_hub.processes", str(self.home)], env={**os.environ, "PYTHONPATH": str(ROOT)},
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual({f["id"] for f in json.loads(p.stdout)["full"]}, {"demo-app", "article-publishing"})


if __name__ == "__main__":
    unittest.main()
