import json
import os
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

from agent_hub import config as config_mod
from agent_hub.config import ConfigError
from agent_hub.engine import ALONE, Engine, message_conflict, message_paths
from agent_hub.store import HUB, INFRA, SCHEMA, Store
from tests.helpers import Clock, FakeRuntime, make_home
from tests.test_web import POST, Board

ROOT = Path(__file__).resolve().parents[1]
LANES = {"hub": {"runtime": "claude"}, "infra": {"runtime": "claude", "max_instances": 3},
         "docs": {"runtime": "codex"}}


class InfraParallelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home(LANES)
        self.s = Store(self.cfg.db)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda n: None, clock=Clock(), log=lambda _: None)

    def memo(self, text: str, paths: str | None = None) -> int:
        return self.s.push(INFRA, HUB, "memo", text, paths=paths)

    def held(self) -> dict[str, int]:
        return {k: m["id"] for k, m in self.s.held_messages(self.cfg.infra_sessions()).items()}

    def test_config(self) -> None:
        self.assertEqual(self.cfg.infra_sessions(), ["infra", "infra#2", "infra#3"])
        self.assertEqual(self.cfg.all_lanes["infra#2"].kind, INFRA)
        self.assertEqual(make_home().infra_sessions(), ["infra"])
        self.assertEqual(make_home(max_instances=3).infra_sessions(), ["infra"])
        with self.assertRaises(ConfigError):
            make_home({"hub": {"runtime": "claude", "max_instances": 2}})

    def test_memos_with_separate_paths_run_at_once_overlapping_ones_wait(self) -> None:
        a = self.memo("big one", "src/")
        b = self.memo("overlaps a", "src/app/web.py")
        c = self.memo("swap\npaths: docs/ops.md")
        d = self.memo("alone", ALONE)
        self.e.tick()
        self.assertEqual(self.held(), {"infra": a, "infra#2": c})
        self.assertIn("infra#2", self.rt.opened[1][1])
        self.s.set_lane("infra", closing=1.0)
        self.rt.st["infra"] = "idle"
        self.e.tick()
        self.assertEqual(self.held(), {"infra#2": c, "infra#3": b})
        for k in ("infra#2", "infra#3"):
            self.s.set_lane(k, closing=1.0)
            self.rt.st[k] = "idle"
        self.e.tick()
        self.e.tick()
        self.assertEqual(self.held(), {"infra": d})
        self.memo("later", "x/")
        self.e.tick()
        self.assertEqual(self.held(), {"infra": d})

    def test_a_session_that_closed_without_wrapping_takes_its_memo_again(self) -> None:
        a = self.memo("a", "a/")
        self.memo("b", "b/")
        self.e.tick()
        self.rt.st["infra"] = "closed"
        self.e.tick()
        self.assertEqual(self.held()["infra"], a)
        self.assertEqual([x[0] for x in self.rt.opened], ["infra", "infra#2", "infra"])

    def test_a_single_infra_session_takes_memos_one_by_one_as_before(self) -> None:
        cfg = make_home({"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"}})
        s = Store(cfg.db)
        rt = FakeRuntime(s)
        e = Engine(cfg, s, rt, lambda n: None, clock=Clock(), log=lambda _: None)
        first = s.push(INFRA, HUB, "memo", "one", paths="a/")
        s.push(INFRA, HUB, "memo", "two", paths="b/")
        e.tick()
        e.tick()
        self.assertEqual({k: m["id"] for k, m in s.held_messages(["infra"]).items()}, {"infra": first})
        self.assertEqual([o[0] for o in rt.opened], ["infra"])

    def test_paths(self) -> None:
        self.assertEqual(message_paths({"paths": "a/, `b.md`", "text": ""}), ["a", "b.md"])
        self.assertEqual(message_paths({"paths": None, "text": "x\npaths: c/"}), ["c"])
        self.assertIsNone(message_paths({"paths": None, "text": "x"}))
        self.assertIsNone(message_conflict({"id": 1, "paths": "a"}, []))
        self.assertIsNotNone(message_conflict({"id": 1, "paths": "a"}, [{"id": 2, "paths": "a/b"}]))

    def dah(self, *args: str, lane: str | None = None, ok: bool = True) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.cfg.home), "PYTHONPATH": str(ROOT)}
        env.pop("TOWER_LANE", None)
        if lane:
            env["TOWER_LANE"] = lane
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.cfg.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        if ok and p.returncode:
            self.fail(f"dah {' '.join(args)} failed: {p.stderr}")
        if not ok and not p.returncode:
            self.fail(f"dah {' '.join(args)} should have failed")
        return p.stdout + p.stderr

    def test_cli(self) -> None:
        self.assertIn("info: no --paths", self.dah("infra", "do x", lane="hub"))
        self.dah("infra", "do y", "--paths", "a/, b.md", lane="hub")
        x, y = [m["id"] for m in self.s.inbox_queue(INFRA)]
        self.assertEqual(self.s.message(y)["paths"], "a/, b.md")
        self.dah("memo", "paths", str(x), "docs/", "c.md", lane="hub")
        self.assertEqual(message_paths(self.s.message(x)), ["docs", "c.md"])
        self.assertIn("paths: docs, c.md", self.dah("status", lane="hub"))
        self.dah("memo", "paths", str(x), "y", lane="infra", ok=False)
        self.e.tick()
        self.assertEqual(self.held(), {"infra": x, "infra#2": y})
        self.assertIn(f"message #{y}", self.dah("status", lane="hub"))
        out = self.dah("memo", "reply", str(y), "done", lane="infra#2")
        self.assertIn("it shows on the board", out)
        with self.s.conn() as con:
            self.assertEqual(con.execute("SELECT sender FROM messages WHERE kind='memo_reply' AND item_id=?", (y,)).fetchone()[0],
                             INFRA)
        self.dah("report", "wrapped", "ok", lane="infra#2")
        self.assertIsNotNone(self.s.lane("infra#2")["closing"])
        self.assertIsNone(self.s.lane("infra")["closing"])
        self.dah("report", "done", "x", lane="infra#2", ok=False)

    def test_a_card_posted_by_a_parallel_session_reaches_infra_when_decided(self) -> None:
        iid = self.s.add_item("infra#2", None, "decision", "Which", "## What to decide\nx",
                              [{"label": "A", "desc": ""}, {"label": "B", "desc": ""}], [], audience="human")
        self.s.decide(iid, "ok", "person", "A")
        self.e.tick()
        held = self.s.held_messages(self.cfg.infra_sessions())
        self.assertEqual([(k, m["kind"], m["item_id"]) for k, m in held.items()], [("infra", "decision", iid)])


class OldDatabaseTest(unittest.TestCase):
    def test_a_database_made_before_message_paths_gets_the_column(self) -> None:
        path = make_home().db
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
        con.execute("INSERT INTO messages(target, sender, kind, text, created) VALUES('infra', 'hub', 'memo', 'old', 1)")
        con.commit()
        con.close()
        s = Store(path)
        self.assertIsNone(s.message(1)["paths"])
        s.set_message_paths(1, "a/, b.md")
        self.assertEqual(message_paths(s.message(1)), ["a", "b.md"])
        self.assertEqual(Store(path).message(1)["paths"], "a/, b.md")


class BoardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Board(lanes=LANES)

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def lanes(self) -> dict[str, dict]:
        return {ln["lane"]: ln for ln in self.b.req("GET", "/api/state")[1]["lanes"]}

    def test_sessions_and_infra_working_on_a_memo(self) -> None:
        ls = self.lanes()
        self.assertEqual((ls["hub"]["sessions"], ls["infra"]["sessions"], ls["docs"]["sessions"]), (None, 3, 1))
        mid = self.b.s.push(INFRA, HUB, "memo", "First line of the request.\nSecond line")
        self.b.s.set_lane("infra#2", active_message=mid, status="idle", session="x")
        ls = self.lanes()
        self.assertIn("infra#2", ls)
        self.assertEqual((ls["infra#2"]["status"], ls["infra#2"]["memo"]["id"], ls["infra#2"]["memo"]["title"]),
                         ("busy", mid, "First line of the request."))
        self.assertEqual(ls["infra"]["memo"], None)

    def test_infra_sessions_from_the_board(self) -> None:
        self.b.s.set_lane("infra#3", active_message=self.b.s.push(INFRA, HUB, "memo", "m"))
        self.assertEqual(self.b.req("POST", "/api/settings", {"lane": "infra", "sessions": 2}, POST)[0], 409)
        self.assertEqual(self.b.req("POST", "/api/settings", {"lane": "infra", "sessions": 3}, POST)[0], 200)
        raw = json.loads((self.b.cfg.home / ".tower" / "tower.json").read_text())
        self.assertEqual(config_mod.load(self.b.cfg.home).instance_cap("infra"), 3)
        self.assertEqual(raw["lanes"]["infra"]["max_instances"], 3)


class InfraTurnsTest(unittest.TestCase):
    def build(self, **extra: object) -> None:
        self.cfg = make_home({**LANES, "infra": {"runtime": "claude", "max_instances": 2}}, **extra)
        self.s = Store(self.cfg.db)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda n: None, clock=Clock(), log=lambda _: None)

    def send(self, who: str, text: str, paths: str) -> int:
        return self.s.push(INFRA, who, "memo", text, paths=paths)

    def held_ids(self) -> list[int]:
        return sorted(m["id"] for m in self.s.held_messages(self.cfg.infra_sessions()).values())

    def test_with_fair_turns_each_sender_gets_a_session_before_a_second(self) -> None:
        self.build(fair_turns=True)
        a1, a2, b1 = self.send("hub-ann", "a1", "a/"), self.send("hub-ann", "a2", "b/"), self.send("hub-bob", "b1", "c/")
        self.e.tick()
        self.assertEqual(self.held_ids(), sorted([a1, b1]))
        self.assertEqual([m["id"] for m in self.s.inbox_queue(INFRA, True)], [a2])

    def test_without_fair_turns_the_oldest_two_run(self) -> None:
        self.build()
        a1, a2, b1 = self.send("hub-ann", "a1", "a/"), self.send("hub-ann", "a2", "b/"), self.send("hub-bob", "b1", "c/")
        self.e.tick()
        self.assertEqual(self.held_ids(), sorted([a1, a2]))
        self.assertEqual([m["id"] for m in self.s.inbox_queue(INFRA)], [b1])

    def test_a_bumped_memo_still_goes_first_with_fair_turns(self) -> None:
        self.build(fair_turns=True)
        self.send("hub-ann", "a1", "a/")
        self.send("hub-bob", "b1", "c/")
        late = self.send("hub-ann", "a2", "b/")
        self.s.bump_message(late, INFRA)
        self.assertEqual(self.s.inbox_queue(INFRA, True)[0]["id"], late)


if __name__ == "__main__":
    unittest.main()
