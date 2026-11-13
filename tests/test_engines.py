import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_hub import config as config_mod
from agent_hub.config import ConfigError
from agent_hub.engine import Engine
from agent_hub.store import Store
from tests.helpers import FakeRuntime, make_home

ROOT = Path(__file__).resolve().parent.parent
LANES = {"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"},
         "story": {"runtime": "codex"}, "render": {"runtime": "codex"}, "post": {"runtime": "codex"}}
ENGINES = {"animation": {"label": "Animation engine", "dir": "engines/animation", "lanes": ["story"],
                         "library": "engines/animation/pattern-library.md", "skills": ".agents/skills",
                         "map": "engines/animation/engine.json"},
           "marketing": {"label": "Marketing"}}


class EngineConfigTest(unittest.TestCase):
    def test_old_config_loads_as_one_implicit_engine(self) -> None:
        c = make_home()
        self.assertEqual(list(c.engines), ["default"])
        e = c.engines["default"]
        self.assertTrue(e.implicit)
        self.assertEqual(e.lanes, ["docs"])
        self.assertEqual(c.lanes["docs"].engine, "default")
        self.assertEqual((c.lanes["hub"].engine, c.lanes["infra"].engine), ("", ""))

    def test_declared_engines_round_trip(self) -> None:
        c = make_home(LANES, engines=ENGINES)
        self.assertEqual(c.engines["animation"].label, "Animation engine")
        self.assertEqual(c.engines["animation"].library, "engines/animation/pattern-library.md")
        self.assertEqual(c.engines["animation"].lanes, ["story"])
        self.assertEqual(c.lanes["story"].engine, "animation")
        # lanes that name no engine fall into the implicit default, which sits beside the declared ones
        self.assertEqual(c.engines["default"].lanes, ["render", "post"])
        self.assertTrue(c.engines["default"].implicit)
        self.assertEqual(c.engines["marketing"].lanes, [])

    def test_lane_key_puts_a_lane_in_an_engine(self) -> None:
        lanes = {**LANES, "post": {"runtime": "codex", "engine": "marketing"}}
        c = make_home(lanes, engines=ENGINES)
        self.assertEqual(c.engines["marketing"].lanes, ["post"])
        self.assertEqual(c.lanes["post"].engine, "marketing")

    def test_bad_engine_config_is_refused(self) -> None:
        for lanes, engines in (
            (LANES, {"a": {"lanes": ["nope"]}}),
            (LANES, {"a": {"lanes": ["hub"]}}),
            ({**LANES, "story": {"runtime": "codex", "engine": "ghost"}}, {"a": {}}),
            ({**LANES, "story": {"runtime": "codex", "engine": "b"}}, {"a": {"lanes": ["story"]}, "b": {}}),
            ({**LANES, "hub": {"runtime": "claude", "engine": "a"}}, {"a": {}}),
            (LANES, {"a": {"dir": "../out"}}),
            (LANES, {"a": {"library": "/etc/x"}}),
            (LANES, {"bad id": {}}),
            (LANES, ["a"]),
        ):
            with self.assertRaises(ConfigError, msg=f"{lanes} {engines}"):
                make_home(lanes, engines=engines)


class EngineStoreTest(unittest.TestCase):
    def test_db_upgrade_keeps_rows_and_adds_engine_columns(self) -> None:
        path = Path(tempfile.mkdtemp()) / "old.db"
        con = sqlite3.connect(path)  # a 0.7.0 database: no engine column on tasks or lanes
        con.executescript("""
            CREATE TABLE tasks(id INTEGER PRIMARY KEY, lane TEXT NOT NULL, title TEXT NOT NULL, brief TEXT NOT NULL,
              state TEXT NOT NULL, session TEXT, created REAL NOT NULL, started REAL, closed REAL, last_prompt REAL,
              last_signal REAL, nudges INTEGER NOT NULL DEFAULT 0, respawns INTEGER NOT NULL DEFAULT 0, handoff TEXT,
              rotate_ready REAL, goal_id INTEGER);
            CREATE TABLE lanes(lane TEXT PRIMARY KEY, session TEXT, pid INTEGER, opened REAL, status TEXT, seen REAL,
              closing REAL, enabled INTEGER, active_message INTEGER, active_retries INTEGER DEFAULT 0, doing TEXT,
              doing_at REAL);
            INSERT INTO tasks(lane, title, brief, state, created) VALUES('docs', 'old', 'b', 'closed', 1);
            INSERT INTO lanes(lane, doing) VALUES('docs', 'writing');
        """)
        con.close()
        s = Store(path)
        with s.conn() as c:
            for table in ("tasks", "lanes"):
                self.assertIn("engine", {r[1] for r in c.execute(f"PRAGMA table_info({table})")})
        self.assertEqual(s.task(1)["title"], "old")
        self.assertIsNone(s.task(1)["engine"])
        with s.conn() as c:
            self.assertEqual(c.execute("SELECT doing FROM lanes WHERE lane='docs'").fetchone()[0], "writing")
        Store(path)  # opening twice must not fail on the existing column
        self.assertEqual(s.task(s.add_task("docs", "new", "b", engine="default"))["engine"], "default")

    def test_task_takes_the_engine_of_its_lane(self) -> None:
        c = make_home(LANES, engines=ENGINES)
        s = Store(c.db)
        self.assertEqual(s.task(s.add_task("story", "t", "b", engine=c.lanes["story"].engine))["engine"], "animation")
        Engine(c, s, FakeRuntime(s), lambda *a, **k: None)
        with s.conn() as con:
            got = dict(con.execute("SELECT lane, engine FROM lanes WHERE engine IS NOT NULL").fetchall())
        self.assertEqual(got, {"story": "animation", "render": "default", "post": "default"})


class EngineCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = make_home(LANES, engines=ENGINES).home

    def dah(self, *args: str, ok: bool = True, lane: str = "") -> str:
        import os
        env = {**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(ROOT)}
        env.pop("TOWER_LANE", None)
        if lane:
            env["TOWER_LANE"] = lane
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        if ok and p.returncode:
            self.fail(f"dah {' '.join(args)} failed: {p.stderr}")
        if not ok and not p.returncode:
            self.fail(f"dah {' '.join(args)} should fail")
        return p.stdout + p.stderr

    def test_engine_list_and_show(self) -> None:
        out = self.dah("engine", "list")
        self.assertIn("animation", out)
        self.assertIn("marketing", out)
        self.assertIn("default", out)
        show = self.dah("engine", "show", "animation")
        self.assertIn("engines/animation/pattern-library.md", show)
        self.assertIn("story", show)
        self.assertIn("no engine", self.dah("engine", "show", "ghost", ok=False))

    def test_engine_add_writes_config(self) -> None:
        self.dah("engine", "add", "audio", "--label", "Audio", "--dir", "engines/audio")
        data = json.loads((self.home / ".tower" / "tower.json").read_text())
        self.assertEqual(data["engines"]["audio"], {"label": "Audio", "dir": "engines/audio"})
        self.assertIn("exists", self.dah("engine", "add", "audio", ok=False))
        self.dah("engine", "add", "bad", "--dir", "../x", ok=False)  # refused by validation...
        data = json.loads((self.home / ".tower" / "tower.json").read_text())
        self.assertNotIn("bad", data["engines"])  # ...and still loadable
        config_mod.load(self.home)

    def test_lane_add_engine(self) -> None:
        self.dah("lane", "add", "sound", "--runtime", "codex", "--engine", "marketing")
        self.assertEqual(config_mod.load(self.home).lanes["sound"].engine, "marketing")
        self.assertIn("[marketing]", self.dah("lane", "list"))
        self.assertIn("no engine", self.dah("lane", "add", "x", "--runtime", "codex", "--engine", "ghost", ok=False))

    def test_dah_task_records_the_lane_engine(self) -> None:
        self.dah("task", "story", "brief", "--title", "T", lane="hub")
        c = config_mod.load(self.home)
        self.assertEqual(Store(c.db).task(1)["engine"], "animation")


if __name__ == "__main__":
    unittest.main()
