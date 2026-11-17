import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from agent_hub.engine import ALONE, DECLARE_GRACE, NOTHING, Engine, infra_waits, message_block, message_paths
from agent_hub.store import HUB, INFRA, Store
from agent_hub.web import infra_wait_line
from tests.helpers import Clock, FakeRuntime, make_home

ROOT = Path(__file__).resolve().parents[1]
LANES = {"hub": {"runtime": "claude"}, "infra": {"runtime": "claude", "max_instances": 3}, "docs": {"runtime": "codex"}}
SESSIONS = ["infra", "infra#2", "infra#3"]


class InfraLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home(LANES)
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda n: None, clock=self.clock, log=lambda _: None)

    def memo(self, text: str, paths: str | None = None) -> int:
        return self.s.push(INFRA, HUB, "memo", text, paths=paths)

    def held(self) -> dict[str, int]:
        return {k: m["id"] for k, m in self.s.held_messages(SESSIONS).items()}

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

    def close(self, lane: str) -> None:
        self.s.set_lane(lane, closing=1.0)
        self.rt.st[lane] = "idle"

    def test_no_hint_starts_and_others_wait_only_for_the_grace(self) -> None:
        a, b, c = self.memo("no hint"), self.memo("no hint either"), self.memo("hinted", "docs/")
        self.e.tick()
        self.assertEqual(self.held(), {"infra": a})
        self.assertIn(f"dah memo paths {a}", self.rt.opened[0][1])
        why = dict((m["id"], w) for m, w in infra_waits(self.s, SESSIONS, self.clock()))
        self.assertEqual(why[b][0], "declaring")
        self.assertIn("declare", self.dah("status", lane="hub"))
        self.clock.tick(DECLARE_GRACE + 1)
        self.e.tick()
        self.assertEqual(set(self.held().values()), {a, b})
        self.assertNotIn(c, self.held().values())

    def test_declare_then_others_run_beside_and_overlap_waits(self) -> None:
        a, b, c = self.memo("a"), self.memo("b", "docs/"), self.memo("c", "src/web.py")
        self.e.tick()
        self.assertEqual(self.held(), {"infra": a})
        self.assertIn("declared by infra", self.dah("memo", "paths", str(a), "src", lane="infra"))
        self.assertEqual(message_paths(self.s.message(a)), ["src"])
        self.e.tick()
        self.assertEqual(self.held(), {"infra": a, "infra#2": b})
        why = dict((m["id"], w) for m, w in infra_waits(self.s, SESSIONS, self.clock()))
        self.assertEqual((why[c][0], why[c][1]["id"]), ("overlap", a))
        self.close("infra")
        self.e.tick()
        self.e.tick()
        self.assertIn(c, self.held().values())

    def test_declared_overlap_gives_the_message_back(self) -> None:
        a, b = self.memo("a", "src/board"), self.memo("b", "docs/")
        self.e.tick()
        self.assertEqual(self.held(), {"infra": a, "infra#2": b})
        out = self.dah("memo", "paths", str(b), "src/board/index.html", "docs", lane="infra#2")
        self.assertIn("went back to the infra queue", out)
        self.assertNotIn("infra#2", self.held())
        self.assertIsNotNone(self.s.lane("infra#2")["closing"])
        self.assertIsNone(self.s.message(b)["delivered"])
        self.assertIn(b, [m["id"] for m in self.s.inbox_queue(INFRA)])
        self.rt.st["infra#2"] = "idle"
        self.e.tick()
        self.e.tick()
        self.assertEqual(self.held(), {"infra": a})
        self.close("infra")
        self.e.tick()
        self.e.tick()
        self.assertIn(b, self.held().values())
        self.dah("memo", "paths", str(a), "x", lane="infra#3", ok=False)
        self.dah("memo", "paths", str(b), lane=[k for k, v in self.held().items() if v == b][0], ok=False)

    def test_alone_waits_for_everyone_and_holds_back_the_rest(self) -> None:
        a = self.memo("a", "docs/")
        self.e.tick()
        self.assertIn("alone", self.dah("infra", "reinstall", "--alone", lane="hub"))
        r = self.s.inbox_queue(INFRA)[0]["id"]
        self.assertEqual(message_paths(self.s.message(r)), [ALONE])
        later = self.memo("later", "other/")
        self.e.tick()
        self.assertEqual(self.held(), {"infra": a})
        why = dict((m["id"], w) for m, w in infra_waits(self.s, SESSIONS, self.clock()))
        self.assertEqual(why[r][0], "busy")
        self.assertEqual(why[later][0], "behind")
        self.close("infra")
        self.e.tick()
        self.e.tick()
        self.assertEqual(list(self.held().values()), [r])
        self.e.tick()
        self.assertEqual(list(self.held().values()), [r])
        self.assertIn("info: no --paths", self.dah("infra", "x", lane="hub"))

    def test_session_declares_alone_beside_others_and_is_given_back(self) -> None:
        a, b = self.memo("a", "docs/"), self.memo("follow-up: reinstall", "src/board")
        self.e.tick()
        self.assertEqual(self.held(), {"infra": a, "infra#2": b})
        self.assertIn("went back", self.dah("memo", "paths", str(b), "--alone", lane="infra#2"))
        self.assertEqual(message_paths(self.s.message(b)), [ALONE])

    def test_decision_inherits_the_memo_paths_and_check_only_touches_nothing(self) -> None:
        a = self.memo("board work", "src/board, tests")
        self.e.tick()
        self.dah("item", "add", "--title", "ship it?", "--body", "b", "--option", "yes=y", "--option", "no=n",
                 lane="infra")
        self.dah("item", "add", "--title", "result", "--body", "done", lane="infra")
        self.dah("item", "add", "--title", f"Result of memo #{a}", "--body", "old card", lane="infra")
        items = {it["title"]: it for it in self.s.items()}
        self.assertEqual(items["ship it?"]["message_id"], a)
        old = items[f"Result of memo #{a}"]["id"]
        with self.s.conn() as con:
            con.execute("UPDATE items SET message_id=NULL WHERE id IN (?, ?)", (old, items["result"]["id"]))
        for title, note in (("ship it?", None), ("result", None), (f"Result of memo #{a}", "x")):
            with self.s.conn() as con:
                con.execute("UPDATE items SET state='decided', verdict='ok', note=?, decided_by='hub', decided=1 WHERE id=?",
                            (note, items[title]["id"]))
        self.e.tick()
        with self.s.conn() as con:
            got = {r["item_id"]: dict(r) for r in con.execute("SELECT * FROM messages WHERE kind='decision'")}
        self.assertEqual(message_paths(got[items["ship it?"]["id"]]), ["src/board", "tests"])
        self.assertEqual(got[items["result"]["id"]]["paths"], NOTHING)
        self.assertEqual(message_paths(got[old]), ["src/board", "tests"])
        self.assertIsNone(message_block(got[items["result"]["id"]], [{"id": 9, "paths": "x"}]))

    def test_an_alone_memo_waits_for_a_session_that_never_declared_even_after_the_grace(self) -> None:
        undeclared = {"id": 7, "paths": "", "text": "no paths here", "delivered": 0.0}
        alone = {"id": 8, "paths": ALONE, "text": "x"}
        self.assertEqual(message_block(alone, [undeclared], now=DECLARE_GRACE + 1000)[0], "busy")

    def test_board_line(self) -> None:
        self.clock.t = time.time()
        a, b = self.memo("a"), self.memo("b")
        self.e.tick()
        line = infra_wait_line(self.cfg, self.s)
        self.assertIn(f"#{b}", line)
        self.assertIn("1 waiting", line)

    def test_a_single_infra_session_shows_no_wait_line(self) -> None:
        cfg = make_home()
        s = Store(cfg.db)
        s.push(INFRA, HUB, "memo", "a")
        s.push(INFRA, HUB, "memo", "b")
        self.assertIsNone(infra_wait_line(cfg, s))


if __name__ == "__main__":
    unittest.main()
