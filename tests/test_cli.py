"""The CLI end to end in a temp workspace, the way lanes call it."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ShippedTemplatesTest(unittest.TestCase):
    def test_the_newest_shipped_text_of_each_template_is_the_current_file(self) -> None:
        from agent_hub.install import TEMPLATES, shipped
        for rel, versions in shipped().items():
            self.assertEqual(versions[-1], (TEMPLATES / rel).read_text(encoding="utf-8"),
                             f"templates/{rel} changed: append its new text to templates/shipped.json so "
                             f"`dah update` can tell untouched workspace copies from edited ones")


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
                           text=True, encoding="utf-8")
        if ok and p.returncode:
            self.fail(f"tower {' '.join(args)} failed: {p.stderr}")
        return p.stdout + p.stderr

    def test_files_written(self) -> None:
        for rel in (".tower/tower.json", ".tower/PROTOCOL.md", ".tower/lanes/hub.md", ".tower/lanes/infra.md",
                    ".tower/lanes/docs.md", ".opencode/command/dah-hub.md", ".claude/commands/dah-infra.md"):
            self.assertTrue((self.home / rel).is_file(), rel)
        self.assertEqual((self.home / ".gitignore").read_text().split(), [".tower/state/", ".tower/venv/"])

    def test_update_brings_an_old_workspace_up_to_date(self) -> None:
        import json
        from agent_hub.install import shipped
        versions, tower = shipped(), self.home / ".tower"
        mine = "\n## This workspace\n\n- tests: `npm test`\n"
        (tower / "lanes/hub.md").write_text(versions["lanes/hub.md"][0] + mine, encoding="utf-8")
        (tower / "lanes/infra.md").write_text(versions["lanes/infra.md"][0].replace("#", "##", 1), encoding="utf-8")
        cfg_path = tower / "tower.json"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        data["lanes"]["infra"]["args"] = ["--permission-mode", "acceptEdits"]
        cfg_path.write_text(json.dumps(data), encoding="utf-8")
        (self.home / ".claude/commands/dah-docs.md").unlink()

        out = self.tower("update", "--no-upgrade")
        hub = (tower / "lanes/hub.md").read_text(encoding="utf-8")
        self.assertEqual(hub, versions["lanes/hub.md"][-1] + mine)  # template swapped, workspace part kept
        self.assertEqual((tower / "lanes/infra.md.new").read_text(encoding="utf-8"), versions["lanes/infra.md"][-1])
        self.assertIn("asked infra to merge", out)
        self.assertEqual(json.loads(cfg_path.read_text(encoding="utf-8"))["lanes"]["infra"]["args"],
                         ["--permission-mode", "acceptEdits", "--allowedTools", "Bash(dah:*)"])
        self.assertTrue((self.home / ".claude/commands/dah-docs.md").is_file())
        self.assertFalse((tower / "lanes/docs.md.new").exists())  # untouched files stay as they are
        from agent_hub import __version__
        self.assertEqual((tower / "state/version").read_text(encoding="utf-8"), __version__)

        again = self.tower("update", "--no-upgrade")  # nothing new: no second request to infra
        self.assertNotIn("asked infra", again)
        self.assertIn("still to merge: .tower/lanes/infra.md.new", again)
        self.assertEqual((tower / "lanes/hub.md").read_text(encoding="utf-8"), hub)

    def test_every_lane_gets_a_shortcut_in_every_runtime(self) -> None:
        # docs (codex) was added after init: every lane now has /dah-<lane> in OpenCode, Claude Code and Codex
        for lane in ("hub", "infra", "docs"):
            for rel in (f".opencode/command/dah-{lane}.md", f".claude/commands/dah-{lane}.md",
                        f".agents/skills/dah-{lane}/SKILL.md"):
                text = (self.home / rel).read_text()
                self.assertIn(f".tower/lanes/{lane}.md", text, rel)
        self.assertIn("name: dah-docs", (self.home / ".agents/skills/dah-docs/SKILL.md").read_text())
        mine = self.home / ".claude/commands/dah-review.md"
        self.assertIn("/dah-review", self.tower("lane", "add", "review", "--runtime", "claude", "--agent", "reviewer"))
        mine.write_text("my own edit")  # a shortcut someone changed is theirs: removing the lane keeps it
        self.tower("lane", "remove", "review")
        self.assertEqual(mine.read_text(), "my own edit")
        self.assertFalse((self.home / ".opencode/command/dah-review.md").exists())
        self.assertFalse((self.home / ".agents/skills/dah-review").exists())

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
        env = {"HOME": str(user), "USERPROFILE": str(user)}  # USERPROFILE: the home on Windows
        out = self.tower("skill", "install", "--runtime", "claude", "--runtime", "opencode", env=env)
        self.assertIn("Nothing written", out)
        self.assertIn("+name: agent-hub-setup", out)
        skill = user / ".claude/skills/agent-hub-setup/SKILL.md"
        command = user / ".config/opencode/command/agent-hub-setup.md"
        self.assertFalse(skill.exists())
        self.tower("skill", "install", "--runtime", "claude", "--runtime", "opencode", "--yes", env=env)
        self.assertIn("dah guide`", skill.read_text())
        self.assertIn("$ARGUMENTS", command.read_text())
        self.assertIn("up to date", self.tower("skill", "install", "--runtime", "claude", env=env))

        legacy = user / ".codex/prompts/agent-hub-setup.md"  # where 0.5 and older put it; Codex 0.117 dropped prompts
        legacy.parent.mkdir(parents=True)
        legacy.write_text("---\ndescription: old\n---\nold\n")
        self.assertIn("remove (the agent no longer reads it)", self.tower("skill", "install", "--runtime", "codex", env=env))
        self.tower("skill", "install", "--runtime", "codex", "--yes", env=env)
        codex = user / ".agents/skills/agent-hub-setup/SKILL.md"
        self.assertIn("name: agent-hub-setup", codex.read_text())
        self.assertFalse(legacy.exists())

        self.assertIn("Nothing removed", self.tower("skill", "uninstall", env=env))
        self.assertTrue(codex.exists())
        self.tower("skill", "uninstall", "--yes", env=env)
        for path in (skill, command, codex):
            self.assertFalse(path.exists(), path)
        self.assertFalse((user / ".agents/skills/agent-hub-setup").exists())
        self.assertIn("no agent-hub-setup", self.tower("skill", "uninstall", env=env))

    def test_uninstall_shows_the_plan_then_takes_agent_hub_out(self) -> None:
        (self.home / ".gitignore").write_text("node_modules/\n.tower/state/\n.tower/venv/\n")
        (self.home / "AGENTS.md").write_text("# Rules\nTower lanes: see .tower/PROTOCOL.md\n")
        mine = self.home / ".claude/commands/dah-docs.md"
        mine.write_text("my own edit")
        out = self.tower("uninstall")
        self.assertIn("Nothing changed", out)
        self.assertIn("keep (edited here)", out)
        self.assertTrue((self.home / ".opencode/command/dah-hub.md").exists())
        out = self.tower("uninstall", "--yes")
        self.assertFalse((self.home / ".opencode/command/dah-hub.md").exists())
        self.assertFalse((self.home / ".agents/skills/dah-docs").exists())
        self.assertEqual(mine.read_text(), "my own edit")  # edited by someone: theirs
        self.assertEqual((self.home / ".gitignore").read_text(), "node_modules/\n")
        self.assertIn("AGENTS.md:2", out)  # a line to remove by hand
        self.assertTrue((self.home / ".tower/tower.json").exists())  # kept without --purge
        self.assertNotIn(str(self.home), self.tower("workspaces"))
        out = self.tower("uninstall", "--yes", "--purge")
        self.assertFalse((self.home / ".tower").exists(), out)

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
        slug = next(ln.split()[0] for ln in self.tower("workspaces").splitlines() if ln.endswith(str(other.resolve())))
        self.assertIn("removed", self.tower("workspaces", "remove", slug))


if __name__ == "__main__":
    unittest.main()
