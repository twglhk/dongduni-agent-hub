"""The CLI end to end in a temp workspace, the way lanes call it."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        out = self.tower("init", "--hub", "opencode", "--infra", "claude", "--lang", "ko")
        self.assertIn("PROTOCOL.md", out)
        self.tower("lane", "add", "docs", "--runtime", "codex")

    def tower(self, *args: str, lane: str | None = None, ok: bool = True, env: dict[str, str] | None = None) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(ROOT), **(env or {})}
        env.pop("TOWER_LANE", None)
        if lane:
            env["TOWER_LANE"] = lane
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.home, env=env, capture_output=True,
                           text=True)
        if ok and p.returncode:
            self.fail(f"tower {' '.join(args)} failed: {p.stderr}")
        return p.stdout + p.stderr

    def test_files_written(self) -> None:
        for rel in (".tower/tower.json", ".tower/PROTOCOL.md", ".tower/lanes/hub.md", ".tower/lanes/infra.md",
                    ".tower/lanes/docs.md", ".opencode/command/hub.md", ".claude/commands/infra.md"):
            self.assertTrue((self.home / rel).is_file(), rel)
        self.assertIn(".tower/state/", (self.home / ".gitignore").read_text())

    def test_task_item_report_flow(self) -> None:
        self.assertIn("only hub", self.tower("task", "docs", "--title", "T", "b", lane="docs", ok=False))
        self.assertIn("task #1", self.tower("task", "docs", "--title", "Write docs", "brief", lane="hub"))
        (self.home / "out.md").write_text("# hi")
        out = self.tower("item", "add", "--title", "Tone?", "--body", "## What to decide\nx\n## Options\ny",
                         "--option", "Formal=f", "--option", "Casual=c", "--attach", "out.md", lane="docs")
        self.assertIn("#1 posted", out)
        self.assertIn("for hub: 1", self.tower("status").replace("open for the hub: 1", "for hub: 1"))
        self.tower("decide", "1", "ok", "--choice", "Casual", "--basis", "style guide", lane="hub")
        self.assertIn("approved (Casual)", self.tower("show", "1"))
        self.tower("report", "done", "docs written", "--attach", "out.md", lane="docs")
        self.assertIn("files must be inside", self.tower("item", "add", "--title", "x", "--body", "y", "--attach",
                                                         "/etc/hosts", lane="docs", ok=False).replace(
            "attachments must be files inside", "files must be inside"))

    def test_memo_reply_and_forward(self) -> None:
        from agent_hub.store import Store
        s = Store(self.home / ".tower/state/tower.db")
        s.push("infra", "ana", "memo", "fix")
        s.push("infra", "ana", "memo", "feature")
        self.tower("memo", "reply", "1", "fixed", lane="infra")
        self.assertIn("→ hub", self.tower("memo", "forward", "2", lane="infra"))
        self.assertIn("only infra", self.tower("memo", "forward", "2", lane="hub", ok=False))
        self.assertIn("infra only uses", self.tower("report", "done", "x", lane="infra", ok=False))

    def test_members_and_discover(self) -> None:
        out = self.tower("member", "add", "ana", "--role", "owner", "--token")
        token = out.split("give it privately): ")[1].strip()
        self.assertNotIn(token, self.tower("member", "list"))
        self.assertIn('"agent_clis"', self.tower("discover"))

    def test_skill_install_previews_then_writes(self) -> None:
        user = Path(tempfile.mkdtemp())
        (user / ".claude").mkdir()
        (user / ".config/opencode").mkdir(parents=True)
        env = {"HOME": str(user)}
        out = self.tower("skill", "install", "--runtime", "claude", "--runtime", "opencode", env=env)
        self.assertIn("Nothing written", out)
        self.assertIn("+name: agent-hub-setup", out)
        skill = user / ".claude/skills/agent-hub-setup/SKILL.md"
        command = user / ".config/opencode/command/agent-hub-setup.md"
        self.assertFalse(skill.exists())
        self.tower("skill", "install", "--runtime", "claude", "--runtime", "opencode", "--yes", env=env)
        self.assertIn("tower guide`", skill.read_text())
        self.assertIn("$ARGUMENTS", command.read_text())
        self.assertIn("up to date", self.tower("skill", "install", "--runtime", "claude", env=env))

    def test_goals_by_humans_and_the_hub(self) -> None:
        self.assertIn("goal #1 added at priority 1", self.tower("goal", "add", "Ship v2", "--detail", "public beta"))
        self.tower("goal", "add", "Fix login", "--priority", "1")
        self.assertIn("set by humans and the hub", self.tower("goal", "add", "x", lane="docs", ok=False))
        self.assertTrue(self.tower("goal", "list").startswith("1. #2 [active] Fix login"))
        self.tower("goal", "plan", "2", "--step", "repro", "--step", "fix", "--step", "test", lane="hub")
        self.assertIn("33% (1/3)", self.tower("goal", "check", "2", "1", lane="hub"))
        self.assertIn("task #1 → docs for goal #2", self.tower("task", "docs", "--goal", "2", "--title", "T", "b",
                                                                lane="hub"))
        out = self.tower("goal", "show", "2")
        self.assertIn("1. [x] repro", out)
        self.assertIn("task #1 docs queued", out)
        self.assertIn("goals (first priority first)", self.tower("status"))
        self.tower("goal", "done", "2", "shipped", lane="hub")
        self.assertIn("not active", self.tower("task", "docs", "--goal", "2", "--title", "T", "b", lane="hub", ok=False))
        self.assertIn("#2 [done]", self.tower("goal", "list", "--all"))
        from agent_hub.store import Store
        news = [m["text"] for m in Store(self.home / ".tower/state/tower.db").pending("hub")]
        self.assertEqual(len(news), 2)  # the two goals a human added; the hub's own changes are not news to it

    def test_guide_and_workspaces(self) -> None:
        self.assertIn("# SETUP.md", self.tower("guide"))
        self.assertIn("tailscale serve", self.tower("guide", "tailscale"))
        self.assertIn("guides: setup", self.tower("guide", "nope", ok=False))
        out = self.tower("workspaces")
        self.assertIn(str(self.home.resolve()), out)
        other = Path(tempfile.mkdtemp())
        self.tower("init", "--home", str(other), "--hub", "claude", "--lang", "ja")
        import json
        ports = [json.loads((h / ".tower/tower.json").read_text())["web"]["port"] for h in (self.home, other)]
        self.assertNotEqual(ports[0], ports[1])
        self.assertIn("--lang takes", self.tower("init", "--home", str(other), "--force", "--lang", "../x", ok=False))
        slug = other.name.lower().replace("_", "-")
        self.assertIn("removed", self.tower("workspaces", "remove", slug))


if __name__ == "__main__":
    unittest.main()
