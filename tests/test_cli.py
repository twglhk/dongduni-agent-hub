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

    def tower(self, *args: str, lane: str | None = None, ok: bool = True) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(ROOT)}
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


if __name__ == "__main__":
    unittest.main()
