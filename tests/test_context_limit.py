import contextlib
import io
import unittest

from agent_hub.config import ConfigError
from agent_hub.engine import COMPACT_TYPED_GAP_SECONDS, HUB_HANDOFF_WAIT, HUB_ROTATE_K, SETTLE_SECONDS, Engine, handoff_mode
from agent_hub.hooks import pending_urgent
from agent_hub.store import Store
from tests.helpers import Clock, FakeRuntime, make_home

LANES = {"hub": {"runtime": "opencode"}, "infra": {"runtime": "opencode"},
         "docs": {"runtime": "claude"}, "web": {"runtime": "codex", "context_limit_k": 50}}


class Base(unittest.TestCase):
    def build(self, lanes=None, **extra):
        self.cfg = make_home(lanes or LANES, **extra)
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda n: None, clock=self.clock, log=lambda _: None)

    def tick(self, n: int = 1, step: float = SETTLE_SECONDS + 1) -> None:
        for _ in range(n):
            self.e.tick()
            self.clock.tick(step)

    def ctx(self, lane: str, k: int) -> None:
        self.s.record_usage(lane, self.s.lane(lane)["session"], "claude", k * 1000, 0, None, k)

    def hub_news(self) -> list[str]:
        return [m["text"] for m in self.s.pending("hub") if m["kind"] == "handoff"]

    def opens(self, lane: str) -> int:
        return len([o for o in self.rt.opened if o[0] == lane])


class ConfigTest(Base):
    def test_off_by_default_and_per_lane_limits(self) -> None:
        self.build(max_lanes=2)
        self.assertEqual(self.cfg.context_limit("docs"), 0)
        self.assertEqual(self.cfg.context_limit("web"), 50)
        self.build(context_limit_k=120)
        self.assertEqual(self.cfg.context_limit("docs"), 120)
        self.assertEqual(self.cfg.context_limit("web"), 50)
        self.assertEqual(self.cfg.context_limit("hub"), 0)
        self.assertEqual(self.cfg.hub_context_limit("hub"), 0)
        self.assertEqual(self.cfg.hub_context_limit("docs"), 0)
        self.assertEqual(make_home({"hub": {"runtime": "claude", "context_limit_k": 10}}).hub_context_limit("hub"), 10)
        with self.assertRaises(ConfigError):
            make_home({"hub": {"runtime": "claude"}, "infra": {"runtime": "claude", "context_limit_k": 10}})
        with self.assertRaises(ConfigError):
            make_home(LANES, context_limit_k="lots")

    def test_per_model_limits(self) -> None:
        lanes = {"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"},
                 "ads": {"runtime": "claude", "model": "sonnet"}, "game": {"runtime": "claude", "model": "opus"},
                 "long": {"runtime": "claude", "model": "claude-fable-5-2"}, "plain": {"runtime": "claude"},
                 "own": {"runtime": "claude", "model": "opus", "context_limit_k": 50}}
        self.build(lanes, context_limit_k={"default": 400, "Sonnet": 300, "opus": 400, "fable": 500})
        c = self.cfg
        self.assertEqual([c.context_limit(x) for x in ("ads", "game", "long", "plain", "own")], [300, 400, 500, 400, 50])
        self.assertEqual(c.context_limit("game", "claude-sonnet-5-5"), 300)
        self.assertEqual(c.context_limit("own", "sonnet"), 50)
        self.assertEqual(c.context_limit("hub"), 0)
        self.assertEqual(make_home(lanes, context_limit_k={"opus": 1}).context_limit("plain"), 0)
        for bad in ({"opus": "lots"}, {"default": -1}, {" ": 10}):
            with self.assertRaises(ConfigError):
                make_home(lanes, context_limit_k=bad)

    def test_task_model_limit_asks_handoff(self) -> None:
        self.build({"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"}, "docs": {"runtime": "claude", "model": "opus"}},
                   context_limit_k={"default": 400, "sonnet": 300})
        t = self.s.add_task("docs", "A", "x")
        self.tick()
        self.ctx("docs", 350)
        self.tick()
        self.assertEqual([m for m in self.s.pending("docs") if m["kind"] == "rotate"], [])
        self.s.update_task(t, model="sonnet")
        self.tick()
        rot = [m["text"] for m in self.s.pending("docs") if m["kind"] == "rotate"]
        self.assertEqual(len(rot), 1)
        self.assertIn("300k", rot[0])

    def test_mode(self) -> None:
        self.assertEqual(handoff_mode("[mode] compact [now] x"), "compact")
        self.assertEqual(handoff_mode("[MODE] Compact"), "compact")
        self.assertEqual(handoff_mode("[now] x"), "fresh")
        self.assertEqual(handoff_mode(None), "fresh")


class ContextLimitTest(Base):
    def test_no_limit_no_ask(self) -> None:
        self.build(max_lanes=2)
        self.s.add_task("docs", "A", "x")
        self.tick()
        self.ctx("docs", 900)
        self.tick(3)
        self.assertEqual([m for m in self.s.pending("docs") if m["kind"] == "rotate"], [])

    def test_over_limit_asks_once_then_fresh_session_and_hub_hears(self) -> None:
        self.build(max_lanes=2, context_limit_k=120)
        t = self.s.add_task("docs", "A", "x")
        self.tick()
        self.ctx("docs", 119)
        self.tick()
        self.assertEqual([m for m in self.s.pending("docs") if m["kind"] == "rotate"], [])
        self.ctx("docs", 130)
        self.tick()
        self.assertEqual(pending_urgent(self.cfg.home, "docs"), 1)
        self.assertEqual(len([m for m in self.s.pending("docs") if m["kind"] == "rotate"]), 1)
        self.rt.st["docs"] = "idle"
        self.tick(3)
        asks = [x for x in self.rt.sent if "130k" in x[2]]
        self.assertEqual(len(asks), 1)
        self.assertIn("[mode] fresh", asks[0][2])
        self.rt.st["docs"] = "idle"
        self.tick(2)
        self.assertEqual(len([x for x in self.rt.sent if "130k" in x[2]]), 1)
        self.s.update_task(t, handoff="[mode] fresh [now] x", rotate_ready=self.clock())
        self.rt.st["docs"] = "idle"
        self.tick()
        self.assertIn("docs", self.rt.closed)
        self.assertEqual(self.opens("docs"), 2)
        self.assertIn("[now] x", self.rt.opened[-1][1])
        self.assertEqual(pending_urgent(self.cfg.home, "docs"), 0)
        news = self.hub_news()
        self.assertEqual(len(news), 1)
        self.assertIn(f"task #{t}", news[0])
        self.assertIn("new session", news[0])
        self.assertIn("130k over the 120k limit", news[0])

    def test_compact_keeps_the_session(self) -> None:
        self.build(max_lanes=2, context_limit_k=120)
        t = self.s.add_task("docs", "A", "x")
        self.tick()
        self.ctx("docs", 130)
        self.rt.st["docs"] = "idle"
        self.tick(2)
        self.s.update_task(t, handoff="[mode] compact [now] mid-edit", rotate_ready=self.clock())
        self.rt.st["docs"] = "idle"
        self.tick()
        self.assertEqual(self.rt.closed, [])
        self.assertEqual(self.opens("docs"), 1)
        self.assertEqual(len(self.rt.compacts), 1)
        self.assertIn("mid-edit", self.rt.compacts[0][2])
        self.assertIsNone(self.s.task(t)["rotate_ready"])
        self.assertEqual(self.s.session_ctx("docs", self.s.lane("docs")["session"]), 0)
        self.assertIn("compacted", self.hub_news()[0])
        self.ctx("docs", 125)
        self.rt.st["docs"] = "idle"
        self.tick(2)
        self.assertEqual(len([x for x in self.rt.sent if "125k" in x[2]]), 1)

    def test_compact_pending_sends_the_continue_message_when_idle(self) -> None:
        self.build(max_lanes=2)
        t = self.s.add_task("docs", "A", "x")
        self.tick()
        self.rt.compact_how = "pending"
        self.s.update_task(t, handoff="[mode] compact [now] y", rotate_ready=self.clock())
        self.rt.st["docs"] = "idle"
        self.tick()
        self.assertEqual(self.rt.sent, [])
        self.rt.st["docs"] = "idle"
        self.tick(2)
        cont = [x for x in self.rt.sent if "compacted" in x[2]]
        self.assertEqual(len(cont), 1)
        self.assertIn("[now] y", cont[0][2])

    def test_compact_typed_sends_the_continue_message_after_the_gap_even_while_busy(self) -> None:
        self.build(max_lanes=2)
        t = self.s.add_task("docs", "A", "x")
        self.tick()
        self.rt.compact_how = "typed"
        self.s.update_task(t, handoff="[mode] compact [now] z", rotate_ready=self.clock())
        self.rt.st["docs"] = "idle"
        self.tick(step=COMPACT_TYPED_GAP_SECONDS - 1)
        self.assertEqual(self.rt.sent, [])
        self.assertEqual(self.rt.st["docs"], "busy")
        self.tick(step=1)
        self.assertEqual(self.rt.sent, [])
        self.tick()
        cont = [x for x in self.rt.sent if "compacted" in x[2]]
        self.assertEqual(len(cont), 1)
        self.assertIn("[now] z", cont[0][2])
        self.tick(3)
        self.assertEqual(len([x for x in self.rt.sent if "compacted" in x[2]]), 1)

    def test_compact_not_possible_falls_back_to_a_fresh_session(self) -> None:
        self.build(max_lanes=2)
        t = self.s.add_task("web", "W", "x")
        self.tick()
        self.rt.compact_how = None
        self.s.update_task(t, handoff="[mode] compact", rotate_ready=self.clock())
        self.rt.st["web"] = "idle"
        self.tick()
        self.assertIn("web", self.rt.closed)
        self.assertEqual(self.opens("web"), 2)
        self.assertIn("cannot", self.hub_news()[0])

    def test_lane_limit_and_no_urgent_for_other_runtimes(self) -> None:
        self.build(max_lanes=2)
        self.s.add_task("web", "W", "x")
        self.tick()
        self.ctx("web", 60)
        self.tick()
        self.assertEqual(pending_urgent(self.cfg.home, "web"), 0)
        self.assertEqual(len([m for m in self.s.pending("web") if m["kind"] == "rotate"]), 1)


class HubHandoffTest(Base):

    def setUp(self) -> None:
        self.build({"hub": {"runtime": "claude", "context_limit_k": 100}, "infra": {"runtime": "claude"},
                    "docs": {"runtime": "claude"}})
        self.s.set_lane("hub", enabled=1)
        self.tick()
        self.rt.st["hub"] = "idle"

    def asks(self) -> list[str]:
        return [x[2] for x in self.rt.sent if x[0] == "hub" and "dah report handoff" in x[2]]

    def test_default_is_hub_rotate_k_not_the_team_limit(self) -> None:
        self.build({"hub": {"runtime": "claude"}, "docs": {"runtime": "claude"}}, context_limit_k=100)
        self.s.set_lane("hub", enabled=1)
        self.tick()
        self.rt.st["hub"] = "idle"
        self.rt.ctx["hub"] = 150
        self.tick(8, step=5)
        self.assertEqual(self.asks(), [])
        self.rt.ctx["hub"] = HUB_ROTATE_K
        self.tick(8, step=5)
        self.assertEqual(len(self.asks()), 1)

    def test_under_limit_nothing(self) -> None:
        self.rt.ctx["hub"] = 99
        self.tick(12, step=5)
        self.assertEqual(self.asks(), [])
        self.assertEqual(self.opens("hub"), 1)

    def test_not_while_news_waits(self) -> None:
        self.rt.ctx["hub"] = 150
        self.s.push("hub", "docs", "done", "Done: docs")
        self.tick(4, step=5)
        self.assertEqual(self.asks(), [])
        self.assertTrue(any("Done: docs" in x[2] for x in self.rt.sent))

    def test_ask_then_handoff_then_fresh_session_with_the_handoff(self) -> None:
        self.rt.ctx["hub"] = 150
        self.tick(8, step=5)
        asks = self.asks()
        self.assertEqual(len(asks), 1)
        self.assertIn("150k", asks[0])
        self.rt.st["hub"] = "idle"
        self.tick(8, step=5)
        self.assertEqual(len(self.asks()), 1)
        self.assertEqual(self.opens("hub"), 1)
        sid = self.s.lane("hub")["session"]
        self.s.set_lane("hub", handoff="[now] x [open] requests #7", handoff_session=sid)
        self.tick(8, step=5)
        self.assertIn("hub", self.rt.closed)
        self.assertEqual(self.opens("hub"), 2)
        first = [p for lane, p in self.rt.opened if lane == "hub"][-1]
        self.assertIn("requests #7", first)
        self.assertIn("dah status", first)
        self.assertIsNone(self.s.lane("hub")["handoff"])

    def test_no_handoff_in_time_renews_anyway(self) -> None:
        self.rt.ctx["hub"] = 150
        self.tick(8, step=5)
        self.assertEqual(len(self.asks()), 1)
        self.rt.st["hub"] = "idle"
        self.clock.tick(HUB_HANDOFF_WAIT)
        self.tick(8, step=5)
        self.assertEqual(self.opens("hub"), 2)

    def test_handoff_of_an_older_session_is_ignored(self) -> None:
        self.s.set_lane("hub", handoff="old", handoff_session="gone")
        self.rt.ctx["hub"] = 10
        self.tick(8, step=5)
        self.assertEqual(self.opens("hub"), 1)

    def test_cli_hub_handoff(self) -> None:
        import argparse
        import os
        from unittest import mock
        from agent_hub import cli
        sid = self.s.lane("hub")["session"]
        args = argparse.Namespace(kind="handoff", text="[now] y", attach=[], as_="hub")
        with mock.patch.dict(os.environ, {"TOWER_HOME": str(self.cfg.home), "TOWER_LANE": "hub"}), \
                mock.patch.object(cli, "cfg", return_value=self.cfg):
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_report(args)
            args.kind = "done"
            with self.assertRaises(cli.TowerError):
                cli.cmd_report(args)
        lane = Store(self.cfg.db).lane("hub")
        self.assertEqual((lane["handoff"], lane["handoff_session"]), ("[now] y", sid))


class StoreMigrationTest(unittest.TestCase):
    def test_an_older_database_gets_the_hub_handoff_columns(self) -> None:
        import sqlite3
        import tempfile
        from pathlib import Path
        db = Path(tempfile.mkdtemp()) / "old.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE lanes(lane TEXT PRIMARY KEY, session TEXT)")
        con.execute("INSERT INTO lanes(lane, session) VALUES('hub', 's1')")
        con.commit()
        con.close()
        s = Store(db)
        s.set_lane("hub", handoff="h", handoff_session="s1")
        self.assertEqual((s.lane("hub")["session"], s.lane("hub")["handoff"]), ("s1", "h"))


if __name__ == "__main__":
    unittest.main()
