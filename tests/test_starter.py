"""0.9.1: the pointer block in CLAUDE.md / AGENTS.md, skills where Claude Code reads them, model fallback."""

import tempfile
import unittest
from pathlib import Path

from agent_hub import config as config_mod
from agent_hub.install import BLOCK_START, fix_models, install, refresh, remove_agents_block, write_agents_block

from .helpers import make_home


class AgentsBlockTest(unittest.TestCase):
    def test_created_when_missing_with_both_files(self) -> None:
        home, wrote = Path(tempfile.mkdtemp()), []
        write_agents_block(home, wrote)
        for n in ("AGENTS.md", "CLAUDE.md"):
            self.assertIn("TOWER_LANE", (home / n).read_text(encoding="utf-8"))

    def test_existing_text_is_kept_and_block_not_doubled(self) -> None:
        home = Path(tempfile.mkdtemp())
        (home / "AGENTS.md").write_text("# Mine\n\nrules\n", encoding="utf-8")
        write_agents_block(home, [])
        write_agents_block(home, [])
        text = (home / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Mine\n\nrules\n"))
        self.assertEqual(text.count(BLOCK_START), 1)

    def test_claude_md_importing_agents_md_is_left_alone(self) -> None:
        home = Path(tempfile.mkdtemp())
        (home / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        write_agents_block(home, [])
        self.assertEqual((home / "CLAUDE.md").read_text(encoding="utf-8"), "@AGENTS.md\n")
        self.assertIn(BLOCK_START, (home / "AGENTS.md").read_text(encoding="utf-8"))

    def test_remove_restores_the_users_text(self) -> None:
        home = Path(tempfile.mkdtemp())
        (home / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
        write_agents_block(home, [])
        remove_agents_block(home)
        self.assertEqual((home / "AGENTS.md").read_text(encoding="utf-8"), "# Mine\n")
        self.assertFalse((home / "CLAUDE.md").exists())

    def test_install_and_refresh_write_it_and_claude_skills(self) -> None:
        cfg = make_home({"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"},
                         "research": {"runtime": "claude"}, "audit": {"runtime": "codex"}})
        install(cfg)
        self.assertTrue((cfg.home / "CLAUDE.md").is_file())
        self.assertTrue((cfg.home / ".claude/skills/research/SKILL.md").is_file())
        self.assertTrue((cfg.home / ".agents/skills/audit/SKILL.md").is_file())
        (cfg.home / "AGENTS.md").unlink()
        refresh(cfg)
        self.assertTrue((cfg.home / "AGENTS.md").is_file())


class ModelFallbackTest(unittest.TestCase):
    def test_fable_becomes_opus_when_refused(self) -> None:
        cfg = make_home({"hub": {"runtime": "claude"}, "audit": {"runtime": "claude", "model": "fable"}})
        notes = fix_models(cfg.home, probe=lambda r, m: False)
        self.assertEqual(len(notes), 1)
        self.assertEqual(config_mod.load(cfg.home).lanes["audit"].model, "opus")
        self.assertEqual(fix_models(cfg.home, probe=lambda r, m: False), [])


if __name__ == "__main__":
    unittest.main()
