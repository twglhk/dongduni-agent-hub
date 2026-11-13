"""`dah engine add NAME --from KIND`, the engine templates, and the optional usage cap."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_hub import config as config_mod, engines as engines_mod
from agent_hub.engine import Engine
from agent_hub.install import engine_kinds
from agent_hub.store import Store
from tests.helpers import Clock, FakeRuntime, make_home

ROOT = Path(__file__).resolve().parents[1]


class ScaffoldTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.tower("init", "--hub", "claude", "--infra", "claude")

    def tower(self, *args: str, ok: bool = True) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(ROOT)}
        env.pop("TOWER_LANE", None)
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        if ok and p.returncode:
            self.fail(f"dah {' '.join(args)} failed: {p.stderr}")
        if not ok and not p.returncode:
            self.fail(f"dah {' '.join(args)} should have failed")
        return p.stdout + p.stderr

    def test_the_three_kinds_exist(self) -> None:
        self.assertEqual(engine_kinds(), ["animation", "blank", "marketing"])

    def test_each_kind_scaffolds_a_valid_engine(self) -> None:
        for kind in engine_kinds():
            with self.subTest(kind=kind):
                eid = f"{kind}-one"
                self.tower("engine", "add", eid, "--from", kind)
                shown = self.tower("engine", "show", eid)
                cfg = config_mod.load(self.home)
                e = cfg.engines[eid]
                rows = engines_mod.build(cfg.home / e.library, cfg.home / e.skills)
                self.assertTrue(rows, shown)
                self.assertIn(f"{eid}-review", e.lanes)
                for r in rows:
                    self.assertEqual((r["process_status"], r["pattern_status"]), ("draft", "draft"), r["type"])
                    self.assertTrue(r["process"] and r["process"]["stages"], r["type"])
                    for s in r["process"]["stages"]:
                        self.assertTrue(s["card"]["exists"], f"{r['type']} stage {s['n']} has no pattern card")
                    self.assertIn(r["lane"], e.lanes)
                    self.assertTrue((cfg.home / e.skills / r["skill"] / "SKILL.md").is_file())
                    self.assertIn(f'skill("{r["skill"]}")', cfg.instructions(r["lane"]).read_text(encoding="utf-8"))
                self.assertIn("pipelines:", shown)
                self.assertNotIn("no process card found", shown)
                for lid in e.lanes:
                    self.assertTrue(cfg.instructions(lid).is_file(), lid)
                    self.assertEqual(cfg.lanes[lid].engine, eid)

    def test_every_engine_gets_research_and_audit(self) -> None:
        for kind in engine_kinds():
            eid = f"{kind}-req"
            self.tower("engine", "add", eid, "--from", kind, "--runtime", "claude")
            shown = self.tower("engine", "show", eid)
            cfg = config_mod.load(self.home)
            e = cfg.engines[eid]
            for role, model in (("research", "opus"), ("audit", "fable")):
                lid = f"{eid}-{role}"
                self.assertIn(lid, e.lanes)
                self.assertEqual(cfg.lanes[lid].model, model)
                self.assertTrue((cfg.home / e.skills / lid / "SKILL.md").is_file(), lid)
                self.assertIn(f'skill("{lid}")', cfg.instructions(lid).read_text(encoding="utf-8"))
            rows = engines_mod.build(cfg.home / e.library, cfg.home / e.skills)
            row = next(r for r in rows if r["type"] == "Research note")
            self.assertEqual(row["lane"], f"{eid}-research")
            self.assertEqual(len(row["process"]["stages"]), 4)
            for s in row["process"]["stages"]:
                self.assertTrue(s["card"]["exists"], s)
            self.assertIn(f"{eid}-research", shown)
            self.assertIn(f"{eid}-audit", shown)

    def test_init_creates_plain_research_and_audit(self) -> None:
        cfg = config_mod.load(self.home)
        for lid in ("research", "audit"):
            self.assertIn(lid, cfg.lanes)
            self.assertTrue(cfg.instructions(lid).is_file())
            where = ".claude/skills" if cfg.lanes[lid].runtime == "claude" else ".agents/skills"
            self.assertTrue((self.home / where / lid / "SKILL.md").is_file())
            self.assertIn(f'skill("{lid}")', cfg.instructions(lid).read_text(encoding="utf-8"))

    def test_models_guide_is_shipped(self) -> None:
        out = self.tower("guide", "models")
        for word in ("fable", "opus", "sonnet", "haiku", "xhigh"):
            self.assertIn(word, out)

    def test_task_warns_without_model_lines(self) -> None:
        self.tower("lane", "add", "docs", "--runtime", "claude")
        env = {"TOWER_LANE": "hub"}
        warn = subprocess.run([sys.executable, "-m", "agent_hub", "task", "docs", "--title", "T", "just do it"], cwd=self.home,
                              env={**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(ROOT), **env},
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(warn.returncode, 0, warn.stderr)
        self.assertIn("no model and no effort line", warn.stderr)

    def test_scaffolded_skills_pass_skill_lint(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML is not installed")
        for kind in engine_kinds():
            self.tower("engine", "add", f"{kind}-two", "--from", kind, "--label", f"{kind.title()} two")
        p = subprocess.run([sys.executable, str(ROOT / "tools" / "skill_lint.py"), str(self.home)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("scanned 16 skills", p.stdout)  # blank 1, animation 2, marketing 1, research and audit in each of the 3 engines and in the init workspace, 4 starter skills
        self.assertIn("FAIL 0", p.stdout)

    def test_two_engines_do_not_collide_and_an_existing_one_is_refused(self) -> None:
        self.tower("engine", "add", "alpha", "--from", "animation")
        self.tower("engine", "add", "beta", "--from", "animation", "--runtime", "codex")
        cfg = config_mod.load(self.home)
        self.assertEqual(cfg.lanes["beta-story"].runtime, "codex")
        self.assertIn("alpha-storyboard", {p.name for p in (self.home / (".claude/skills" if cfg.lanes["alpha-story"].runtime == "claude" else ".agents/skills")).iterdir()})
        self.assertIn("beta-storyboard", {p.name for p in (self.home / ".agents/skills").iterdir()})
        self.assertIn("exists", self.tower("engine", "add", "alpha", "--from", "blank", ok=False))
        self.assertIn("one of", self.tower("engine", "add", "gamma", "--from", "nope", ok=False))
        self.assertIn("lowercase", self.tower("engine", "add", "Bad_Name", "--from", "blank", ok=False))

    def test_a_clash_writes_nothing(self) -> None:
        (self.home / "engines/delta").mkdir(parents=True)
        (self.home / "engines/delta/pattern-library.md").write_text("mine\n", encoding="utf-8")
        before = (self.home / ".tower/tower.json").read_text(encoding="utf-8")
        self.assertIn("would overwrite", self.tower("engine", "add", "delta", "--from", "blank", ok=False))
        self.assertEqual((self.home / ".tower/tower.json").read_text(encoding="utf-8"), before)
        self.assertEqual([p.name for p in (self.home / ".agents/skills").glob("delta-*")], [])

    def test_cap_and_label_flags_and_the_guide(self) -> None:
        self.tower("engine", "add", "capped", "--from", "blank", "--cap", "tokens-day:5000")
        self.assertEqual(config_mod.load(self.home).engines["capped"].cap, "tokens-day:5000")
        self.assertIn("dah engine add NAME --from", self.tower("guide", "engines"))

    def test_package_data_covers_every_template_file(self) -> None:
        import re
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        globs = re.findall(r'"(templates/engines/[^"]+)"', text)
        pkg = ROOT / "agent_hub"
        for f in (pkg / "templates" / "engines").rglob("*"):
            if f.is_file():
                rel = f.relative_to(pkg).as_posix()
                self.assertTrue(any(len(g.split("/")) == len(rel.split("/")) and Path(rel).match(g) for g in globs), rel)


class CapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home({"hub": {"runtime": "opencode"}, "infra": {"runtime": "opencode"},
                              "a1": {"runtime": "claude", "engine": "a"}, "b1": {"runtime": "claude", "engine": "b"}},
                             engines={"a": {"cap": "tokens-day:1000"}, "b": {"cap": "plan"}}, max_lanes=4)
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda _n: None, clock=self.clock, log=lambda _: None)

    def test_tokens_day_holds_new_tasks_of_that_engine_only(self) -> None:
        self.s.record_usage("a1", "x", "claude", 700, 400, None, None)
        ta = self.s.add_task("a1", "t", "Line: none · chore: x", "a")
        tb = self.s.add_task("b1", "t", "Line: none · chore: x", "b")
        self.e.tick()
        self.assertEqual(self.s.task(ta)["state"], "queued")
        self.assertEqual(self.s.task(tb)["state"], "running")  # `plan` names no registered provider: no hold

    def test_under_the_cap_the_task_starts(self) -> None:
        self.s.record_usage("a1", "x", "claude", 300, 100, None, None)
        ta = self.s.add_task("a1", "t", "Line: none · chore: x", "a")
        self.e.tick()
        self.assertEqual(self.s.task(ta)["state"], "running")

    def test_an_extension_cap_provider_holds_and_a_failing_one_does_not(self) -> None:
        ext = self.cfg.home / ".tower" / "ext"
        ext.mkdir()
        (ext / "caps.py").write_text("seen = []\ndef plan(ctx):\n    seen.append(ctx.engine)\n    return 'quota left: 3%'\ncaps = {'plan': plan}\n")
        tb = self.s.add_task("b1", "t", "Line: none · chore: x", "b")
        self.e.tick()
        self.assertEqual(self.s.task(tb)["state"], "queued")
        (ext / "caps.py").write_text("def plan(ctx):\n    raise RuntimeError('boom')\ncaps = {'plan': plan}\n")
        self.e.tick()
        self.assertEqual(self.s.task(tb)["state"], "running")
