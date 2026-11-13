"""`dah update` on a workspace as 0.7.0 left it: old template texts, an old tower.json, a database with goals in it."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V070 = {"PROTOCOL.md": 5, "lanes/hub.md": 3, "lanes/infra.md": 6, "lanes/team.md": 1}  # index of the 0.7.0 text in shipped.json


class UpdateFrom070Test(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="dah-070-"))
        self.tower("init", "--hub", "claude", "--infra", "claude")
        self.tower("lane", "add", "docs", "--runtime", "codex")
        from agent_hub.install import shipped
        self.v = shipped()
        t = self.home / ".tower"
        (t / "PROTOCOL.md").write_text(self.v["PROTOCOL.md"][V070["PROTOCOL.md"]], encoding="utf-8")
        self.mine = "\n## This workspace\n\n- decisions live in docs/decisions.md\n"
        (t / "lanes/hub.md").write_text(self.v["lanes/hub.md"][V070["lanes/hub.md"]] + self.mine, encoding="utf-8")
        (t / "lanes/infra.md").write_text(self.v["lanes/infra.md"][V070["lanes/infra.md"]], encoding="utf-8")
        (t / "lanes/docs.md").write_text(self.v["lanes/team.md"][V070["lanes/team.md"]].replace("{lane}", "docs"), encoding="utf-8")
        data = json.loads((t / "tower.json").read_text(encoding="utf-8"))
        data.update({"require_model_lines": True, "stage_table": "docs/process-table.md", "max_lanes": 3})
        (t / "tower.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.before = data
        (t / "state").mkdir(exist_ok=True)
        (t / "state/version").write_text("0.7.0", encoding="utf-8")
        self.tower("status")  # opens the database
        db = next((t / "state").glob("*.db"))
        with sqlite3.connect(db) as con:
            con.execute("CREATE TABLE IF NOT EXISTS goals(id INTEGER PRIMARY KEY, title TEXT)")
            con.executemany("INSERT INTO goals(title) VALUES (?)", [("Launch",), ("Docs",)])
        self.db = db

    def tower(self, *args: str) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(ROOT)}
        env.pop("TOWER_LANE", None)
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, f"dah {' '.join(args)}: {p.stderr}")
        return p.stdout + p.stderr

    def test_fixture_is_really_the_070_text(self) -> None:
        """If a later release rewrites shipped.json history, this fixture must be rebuilt."""
        for rel, i in V070.items():
            old = subprocess.run(["git", "show", f"v0.7.0:agent_hub/templates/{rel}"], cwd=ROOT, capture_output=True,
                                 text=True, encoding="utf-8")
            if old.returncode == 0:  # a clone without tags skips the comparison
                self.assertEqual(self.v[rel][i], old.stdout, rel)

    def test_update_migrates_a_070_workspace(self) -> None:
        t = self.home / ".tower"
        out = self.tower("update", "--no-upgrade", "--no-restart")
        self.assertIn("0.7.0 →", out)
        self.assertEqual((t / "PROTOCOL.md").read_text(encoding="utf-8"), self.v["PROTOCOL.md"][-1])
        self.assertIn("fix: stage N", (t / "PROTOCOL.md").read_text(encoding="utf-8"))
        self.assertEqual((t / "lanes/hub.md").read_text(encoding="utf-8"), self.v["lanes/hub.md"][-1] + self.mine)
        self.assertEqual((t / "lanes/infra.md").read_text(encoding="utf-8"), self.v["lanes/infra.md"][-1])
        self.assertEqual((t / "lanes/docs.md").read_text(encoding="utf-8"), self.v["lanes/team.md"][-1].replace("{lane}", "docs"))
        self.assertFalse(list((t / "lanes").glob("*.new")))
        self.assertEqual(json.loads((t / "tower.json").read_text(encoding="utf-8")), self.before)  # additive: nothing rewritten
        self.assertIn("note: goals are gone: the 2 goal row(s) stay in the database", out)
        self.assertIn("note: engines: tower.json declares none", out)
        self.assertNotIn("does not forward infra work", out)
        status = self.tower("status")
        self.assertNotIn("goal", status.lower())
        self.assertIn("default", self.tower("engine", "list"))
        with sqlite3.connect(self.db) as con:  # the old rows are still there: a downgrade to 0.7.x opens the file
            self.assertEqual(con.execute("SELECT count(*) FROM goals").fetchone()[0], 2)
        from agent_hub import __version__
        self.assertEqual((t / "state/version").read_text(encoding="utf-8"), __version__)

    def test_an_edited_hub_file_gets_the_new_template_beside_it_and_a_warning(self) -> None:
        t = self.home / ".tower"
        hub = self.v["lanes/hub.md"][V070["lanes/hub.md"]].replace("#", "##", 1) + self.mine
        (t / "lanes/hub.md").write_text(hub, encoding="utf-8")
        out = self.tower("update", "--no-upgrade", "--no-restart")
        self.assertEqual((t / "lanes/hub.md").read_text(encoding="utf-8"), hub)
        self.assertEqual((t / "lanes/hub.md.new").read_text(encoding="utf-8"), self.v["lanes/hub.md"][-1])
        self.assertIn("the board has no Infra tab now and the hub is the only door to infra", out)
        self.assertIn("asked infra to merge them", out)
