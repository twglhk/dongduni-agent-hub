"""Task ledger: model/effort per task, tokens and turns from stand-in readings, retros, `dah stats` golden output."""

import io
import json
import unittest
from contextlib import redirect_stdout

from agent_hub import stats
from agent_hub.engine import Engine
from agent_hub.store import Store
from tests.helpers import Clock, FakeRuntime, make_home


class LedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home({"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"},
                              "docs": {"runtime": "claude"}, "web": {"runtime": "claude"}}, max_lanes=2)
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda _: None, clock=self.clock, log=lambda _: None)

    def tick(self, step: float = 70) -> None:
        self.e.tick()
        self.clock.tick(step)

    def test_a_task_records_the_model_and_effort_it_started_with(self) -> None:
        a = self.s.add_task("docs", "A", "model: opus\neffort: high\nwrite")
        b = self.s.add_task("web", "B", "no lines here")
        self.tick()
        self.assertEqual((self.s.task(a)["model"], self.s.task(a)["effort"]), ("opus", "high"))
        self.assertEqual((self.s.task(b)["model"], self.s.task(b)["effort"]), (None, None))

    def test_tokens_and_turns_land_on_the_task_and_never_shrink(self) -> None:
        t = self.s.add_task("docs", "A", "x")
        self.tick()
        self.assertIsNone(self.s.task(t)["tok_at"])
        self.rt.ledger_use["docs"] = {"new": 1000, "cache": 40000, "out": 300, "turns": 4}
        self.tick()
        got = self.s.task(t)
        self.assertEqual((got["tok_new"], got["tok_cache"], got["tok_out"], got["turns"]), (1000, 40000, 300, 4))
        self.rt.ledger_use["docs"] = {"new": 10, "cache": 10, "out": 10, "turns": 1}  # a transcript that lost its head
        self.tick()
        self.assertEqual(self.s.task(t)["tok_new"], 1000)
        self.rt.ledger_use["docs"] = {"new": 1500, "cache": 50000, "out": 400, "turns": 6}
        self.tick()
        self.assertEqual((self.s.task(t)["tok_cache"], self.s.task(t)["turns"]), (50000, 6))

    def test_a_handoff_session_adds_to_the_task(self) -> None:
        t = self.s.add_task("docs", "A", "x")
        self.tick()
        first = self.s.task(t)["session"]
        self.s.record_task_usage(t, first, 100, 1000, 10, 2)
        self.s.update_task(t, session="second")
        self.s.record_task_usage(t, "second", 50, 500, 5, 1)
        got = self.s.task(t)
        self.assertEqual((got["tok_new"], got["tok_cache"], got["tok_out"], got["turns"]), (150, 1500, 15, 3))
        self.assertEqual(self.s.task_rotations(), {t: 1})

    def test_tasks_chained_in_one_session_split_its_totals(self) -> None:
        a = self.s.add_task("docs", "A", "x")
        b = self.s.add_task("docs", "B", "y")
        self.s.record_task_usage(a, "s1", 100, 1000, 10, 2)
        self.s.record_task_usage(b, "s1", 130, 1900, 25, 5)  # the session total after B ran too
        self.assertEqual((self.s.task(a)["tok_new"], self.s.task(b)["tok_new"], self.s.task(b)["tok_cache"]), (100, 30, 900))
        self.assertEqual(self.s.task(b)["turns"], 3)

    def test_a_runtime_without_turns_keeps_the_last_count(self) -> None:
        t = self.s.add_task("docs", "A", "x")
        self.s.record_task_usage(t, "s", 10, 0, 1, 3)
        self.s.record_task_usage(t, "s", 20, 0, 2, None)
        self.assertEqual((self.s.task(t)["tok_new"], self.s.task(t)["turns"]), (20, 3))

    def test_old_databases_get_the_ledger_columns(self) -> None:
        import sqlite3
        path = self.cfg.home / "old.db"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, lane TEXT NOT NULL, title TEXT NOT NULL, brief TEXT NOT NULL, "
                    "state TEXT NOT NULL, session TEXT, created REAL NOT NULL, started REAL, closed REAL, last_prompt REAL, "
                    "last_signal REAL, nudges INTEGER NOT NULL DEFAULT 0, respawns INTEGER NOT NULL DEFAULT 0, handoff TEXT, "
                    "rotate_ready REAL, goal_id INTEGER)")
        con.execute("INSERT INTO tasks(lane, title, brief, state, created) VALUES('docs','old','b','closed',1)")
        con.commit()
        con.close()
        s = Store(path)
        row = s.task(1)
        self.assertEqual((row["title"], row["tok_new"], row["turns"], row["model"]), ("old", 0, 0, None))

    # ----- retros -----

    def test_split_retro(self) -> None:
        line, r = stats.split_retro("Docs done || retro: cost=the setup guide | corrected=none | rule=docs-skill: link check")
        self.assertEqual(line, "Docs done")
        self.assertEqual((r["cost"], r["corrected"], r["rule"]), ("the setup guide", "none", "docs-skill: link check"))
        self.assertEqual(stats.split_retro("plain line"), ("plain line", None))
        _, r = stats.split_retro("x || retro: free text a | b")
        self.assertEqual((r["cost"], r["raw"]), ("", "free text a | b"))

    def test_a_wrapped_report_stores_its_retro(self) -> None:
        from agent_hub import cli
        self.s.add_task("docs", "A", "x")
        self.tick()
        import os
        os.environ["TOWER_HOME"], os.environ["TOWER_LANE"] = str(self.cfg.home), "docs"
        try:
            with redirect_stdout(io.StringIO()):
                cli.main(["report", "wrapped", "All done || retro: cost=the links | corrected=none | rule=none"])
        finally:
            del os.environ["TOWER_LANE"]
        rep = self.s.reports(1)[-1]
        self.assertEqual((rep["kind"], rep["text"]), ("wrapped", "All done"))
        r = self.s.retros()[0]
        self.assertEqual((r["task_id"], r["lane"], r["summary"], r["cost"]), (1, "docs", "All done", "the links"))
        self.assertIn("docs task #1 - All done", stats.render_retros(self.s.retros()).replace("[", "", 1).split("] ", 1)[1])

    # ----- stats -----

    def seed(self) -> None:
        for lane, brief, tok, state in (("docs", "model: opus\neffort: high", (1_200_000, 90_000_000, 300_000, 120), "closed"),
                                        ("docs", "model: sonnet\neffort: low", (40_000, 2_000_000, 9_000, 8), "closed"),
                                        ("web", "model: sonnet\neffort: low", (5_000, 100, 700, 3), "running")):
            t = self.s.add_task(lane, lane, brief)
            self.s.claim_task(t)
            m, e = (v.split(": ")[1] for v in brief.split("\n"))
            self.s.update_task(t, state=state, model=m, effort=e)
            self.s.record_task_usage(t, f"s{t}", tok[0], tok[1], tok[2], tok[3])
        q = self.s.add_task("web", "never ran", "x")
        self.s.update_task(q, state="cancelled")
        self.s.push("docs", "hub", "tell", "fix this", 1)
        self.s.push("docs", "hub", "nudge", "ping", 1)
        self.s.push("docs", "hub", "nudge", "ping", 1)

    def test_stats_by_lane_golden(self) -> None:
        self.seed()
        out = stats.render(stats.summarize(stats.collect(self.s), "lane"))
        self.assertEqual(out, "\n".join([
            "ledger · by lane",
            "group  tasks  closed  cancelled    new   cache    out  turns  tells  nudges  rotations",
            "-----  -----  ------  ---------  -----  ------  -----  -----  -----  ------  ---------",
            "docs       2       2          0  1.24M  92.00M  0.31M    128      1       2          0",
            "web        2       0          1     5k     100    700      3      0       0          0",
            "total      4       2          1  1.25M  92.00M  0.31M    131      1       2          0",
        ]))

    def test_stats_by_model_and_filters(self) -> None:
        self.seed()
        by = {g["group"]: g for g in stats.summarize(stats.collect(self.s), "model")["rows"]}
        self.assertEqual((by["opus"]["tasks"], by["opus"]["new"]), (1, 1_200_000))
        self.assertEqual((by["sonnet"]["tasks"], by["sonnet"]["turns"]), (2, 11))
        self.assertEqual(by["-"]["cancelled_early"], 1)  # the cancelled task has no model line and never ran
        self.assertEqual([g["group"] for g in stats.summarize(stats.collect(self.s, "web"), "lane")["rows"]], ["web"])
        self.assertEqual(stats.collect(self.s, since=self.clock() + 1), [])
        self.assertEqual(stats.summarize(stats.collect(self.s), "week")["rows"][0]["group"], "1970-W03")
        with self.assertRaises(ValueError):
            stats.summarize([], "color")


if __name__ == "__main__":
    unittest.main()
