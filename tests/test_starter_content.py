"""0.9.3 starter content: neutral subagents and skills in every workspace, kept by `dah update` like PROTOCOL.md."""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_hub import starter
from agent_hub.install import GUIDE, TEMPLATES, _refresh_file, install, refresh, shipped

from .helpers import make_home

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ("explore", "librarian", "looker", "general")
SKILLS = ("humanizer", "media-reading", "review-board", "project-audit")


def dah(home: Path, *args: str) -> str:
    env = {**os.environ, "TOWER_HOME": str(home), "PYTHONPATH": str(ROOT), "DAH_SKIP_MODEL_CHECK": "1"}
    env.pop("TOWER_LANE", None)
    p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=home, env=env, capture_output=True, text=True,
                       encoding="utf-8")
    if p.returncode:
        raise AssertionError(f"dah {' '.join(args)} failed: {p.stderr}")
    return p.stdout + p.stderr


def empty_report() -> dict[str, list[str]]:
    return {"created": [], "updated": [], "merge": [], "pending": [], "config": [], "shortcuts": []}


class FreshInitTest(unittest.TestCase):
    def test_claude_workspace_gets_subagents_and_skills(self) -> None:
        home = Path(tempfile.mkdtemp())
        out = dah(home, "init", "--hub", "claude", "--infra", "claude")
        for name in AGENTS:
            path = home / ".claude/agents" / f"{name}.md"
            text = path.read_text(encoding="utf-8")
            model, effort = starter.SUBAGENTS[name]
            self.assertIn(f"\nname: {name}\n", text)
            self.assertIn(f"\nmodel: {model}\n", text)
            self.assertIn(f"\neffort: {effort}\n", text)
            self.assertNotIn("{model}", text)
            self.assertIn(f".claude/agents/{name}.md", out)
        for name in SKILLS:
            self.assertTrue((home / ".claude/skills" / name / "SKILL.md").is_file(), name)
            self.assertFalse((home / ".agents/skills" / name).exists(), name)
        self.assertTrue((home / ".claude/skills/humanizer/LICENSE").is_file())
        self.assertTrue((home / ".claude/skills/humanizer/references/patterns-staging.md").is_file())
        self.assertTrue((home / ".claude/skills/review-board/references/before-done.md").is_file())

    def test_codex_and_opencode_get_agents_skills_and_no_subagents(self) -> None:
        home = Path(tempfile.mkdtemp())
        dah(home, "init", "--hub", "codex", "--infra", "opencode")
        self.assertFalse((home / ".claude/agents").exists())
        self.assertFalse((home / ".claude/skills/humanizer").exists())
        for name in SKILLS:
            self.assertTrue((home / ".agents/skills" / name / "SKILL.md").is_file(), name)

    def test_mixed_runtimes_get_both_skill_folders_but_opencode_alone_with_claude_only_one(self) -> None:
        cfg = make_home({"hub": {"runtime": "claude"}, "infra": {"runtime": "opencode"}, "docs": {"runtime": "codex"}})
        self.assertEqual(starter.skill_dirs(cfg), [".claude/skills", ".agents/skills"])
        cfg = make_home({"hub": {"runtime": "claude"}, "infra": {"runtime": "opencode"}})
        self.assertEqual(starter.skill_dirs(cfg), [".claude/skills"])  # OpenCode reads .claude/skills too: no double

    def test_skills_pass_skill_lint(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML is not installed")
        cfg = make_home({"hub": {"runtime": "claude"}, "infra": {"runtime": "codex"}})
        starter.install(cfg, False, [])
        p = subprocess.run([sys.executable, str(ROOT / "tools" / "skill_lint.py"), str(cfg.home)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("scanned 8 skills", p.stdout)
        self.assertIn("WARN 0, FAIL 0", p.stdout)


class KeepAndRefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home({"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"}})

    def test_install_keeps_the_workspaces_own_files(self) -> None:
        mine = self.cfg.home / ".claude/agents/explore.md"
        mine.parent.mkdir(parents=True)
        mine.write_text("my own explorer\n", encoding="utf-8")
        skill = self.cfg.home / ".claude/skills/humanizer/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("my own humanizer\n", encoding="utf-8")
        install(self.cfg)
        self.assertEqual(mine.read_text(encoding="utf-8"), "my own explorer\n")
        self.assertEqual(skill.read_text(encoding="utf-8"), "my own humanizer\n")
        self.assertTrue((self.cfg.home / ".claude/agents/looker.md").is_file())

    def test_update_recreates_a_deleted_file_and_leaves_current_ones(self) -> None:
        install(self.cfg)
        gone = self.cfg.home / ".claude/skills/media-reading/SKILL.md"
        gone.unlink()
        report = refresh(self.cfg)
        self.assertIn(".claude/skills/media-reading/SKILL.md", report["created"])
        self.assertTrue(gone.is_file())
        self.assertEqual(report["merge"], [])
        self.assertFalse(any(p.name.endswith(".new") for p in (self.cfg.home / ".claude").rglob("*")))

    def _with_versions(self, key: str, old: str, dest: Path) -> dict[str, list[str]]:
        versions = shipped()
        versions[key] = [old, versions[key][-1]]
        dest.parent.mkdir(parents=True, exist_ok=True)
        return versions

    def test_untouched_old_copy_is_swapped_and_appended_text_kept(self) -> None:
        key, dest = "starter/skills/review-board/SKILL.md", self.cfg.home / ".claude/skills/review-board/SKILL.md"
        versions = self._with_versions(key, "old review board text\n", dest)
        dest.write_text("old review board text\n\n## This workspace\n\nmine\n", encoding="utf-8")
        report = empty_report()
        starter.refresh(self.cfg, versions, report, _refresh_file)
        text = dest.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(versions[key][-1]))
        self.assertTrue(text.endswith("## This workspace\n\nmine\n"))
        self.assertIn(".claude/skills/review-board/SKILL.md", report["updated"])

    def test_edited_copy_is_kept_with_the_new_text_beside_it(self) -> None:
        key, dest = "starter/skills/review-board/SKILL.md", self.cfg.home / ".claude/skills/review-board/SKILL.md"
        old = "---\nname: review-board\n---\n\n# Review board\n\nPost items when a human decides.\n"
        versions = self._with_versions(key, old, dest)
        dest.write_text(old.replace("Post items", "We post items"), encoding="utf-8")
        report = empty_report()
        starter.refresh(self.cfg, versions, report, _refresh_file)
        self.assertEqual(dest.read_text(encoding="utf-8"), old.replace("Post items", "We post items"))
        self.assertEqual(dest.with_name("SKILL.md.new").read_text(encoding="utf-8"), versions[key][-1])
        self.assertIn(".claude/skills/review-board/SKILL.md", report["merge"])

    def test_a_subagent_whose_model_was_changed_still_counts_as_untouched(self) -> None:
        key, dest = "starter/agents/explore.md", self.cfg.home / ".claude/agents/explore.md"
        old = "---\nname: explore\nmodel: {model}\neffort: {effort}\n---\nOld body.\n"
        versions = self._with_versions(key, old, dest)
        dest.write_text(starter.fill(old, "opus", "low"), encoding="utf-8")
        report = empty_report()
        starter.refresh(self.cfg, versions, report, _refresh_file)
        text = dest.read_text(encoding="utf-8")
        self.assertEqual(text, starter.fill(versions[key][-1], "opus", "low"))
        self.assertFalse(dest.with_name("explore.md.new").exists())
        self.assertIn(".claude/agents/explore.md", report["updated"])


class ModelFallbackTest(unittest.TestCase):
    def test_a_refused_model_becomes_inherit_once_and_probes_each_model_once(self) -> None:
        cfg = make_home({"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"}})
        install(cfg)
        mine = cfg.home / ".claude/agents/general.md"
        mine.write_text(mine.read_text(encoding="utf-8").replace("model: sonnet", "model: opus"), encoding="utf-8")
        asked: list[tuple[str, str]] = []

        def probe(runtime: str, model: str) -> bool:
            asked.append((runtime, model))
            return model != "haiku"

        notes = starter.fix_models(cfg, probe)
        self.assertEqual(sorted(asked), [("claude", "haiku"), ("claude", "sonnet")])
        self.assertEqual(len(notes), 2)
        for name in ("explore", "librarian"):
            self.assertIn("\nmodel: inherit\n", (cfg.home / f".claude/agents/{name}.md").read_text(encoding="utf-8"))
        self.assertIn("\nmodel: sonnet\n", (cfg.home / ".claude/agents/looker.md").read_text(encoding="utf-8"))
        self.assertIn("\nmodel: opus\n", mine.read_text(encoding="utf-8"))  # the workspace's own choice is left
        self.assertEqual(starter.fix_models(cfg, probe), [])
        report = refresh(cfg)  # an `inherit` copy is still untouched for dah update
        self.assertEqual(report["merge"], [])

    def test_models_follow_the_models_guide(self) -> None:
        guide = (GUIDE / "MODELS.md").read_text(encoding="utf-8")
        for name, (model, effort) in starter.SUBAGENTS.items():
            row = next((ln for ln in guide.splitlines() if ln.startswith("|") and f"`{model}`" in ln), "")
            self.assertTrue(row, f"{name}: {model} is not in the lineup of MODELS.md")
            self.assertIn(f"default `{effort}`", row, f"{name}: {effort} is not {model}'s default in MODELS.md")
            self.assertIn(f"`{name}`", guide)


class TemplatesTest(unittest.TestCase):
    def test_every_starter_file_is_in_shipped_json(self) -> None:
        missing = [k for k in starter.template_keys() if k not in shipped()]
        self.assertEqual(missing, [], "append these templates to templates/shipped.json")
        self.assertEqual(sorted(Path(k).stem for k in starter.template_keys() if k.startswith("starter/agents/")), sorted(AGENTS))

    def test_templates_are_neutral_english(self) -> None:
        for key in starter.template_keys():
            text = (TEMPLATES / key).read_text(encoding="utf-8")
            with self.subTest(key=key):
                self.assertIsNone(re.search(r"godot|blender|hero|boss|arkham|orca|roam|founder", text, re.I))
                self.assertIsNone(re.search(r"[ㄱ-ㆎ가-힣぀-ヿ]", text))  # no Korean or Japanese
                self.assertNotIn("AGENTS.md", text)
                self.assertNotIn("Apple Vision", text)
                self.assertNotIn("mlx", text)
        general = (TEMPLATES / "starter/agents/general.md").read_text(encoding="utf-8")
        self.assertIn("Follow the project instructions.", general)
        self.assertIn("where those are connected", (TEMPLATES / "starter/agents/librarian.md").read_text(encoding="utf-8"))

    def test_protocol_and_harness_carry_the_new_lines(self) -> None:
        proto = (TEMPLATES / "PROTOCOL.md").read_text(encoding="utf-8")
        self.assertIn("Never `git stash`, `git reset --hard` or `git clean` in the shared tree", proto)
        self.assertIn("git worktree add", proto)
        self.assertIn("subagent calls fail: do that piece yourself", proto)
        harness = (ROOT / "docs" / "lane-harness-claude.md").read_text(encoding="utf-8")
        self.assertIn("Read images, screenshots and PDFs with the Read tool.", harness)
        self.assertIn("subagent calls fail: do that piece yourself", harness)


if __name__ == "__main__":
    unittest.main()
