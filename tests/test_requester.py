import os
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

from agent_hub.engine import Engine
from agent_hub.requester import visible_to_requester
from agent_hub.store import HUB, HUMAN, INFRA, SCHEMA, Store
from tests.helpers import Clock, FakeRuntime, make_home

ROOT = Path(__file__).resolve().parents[1]
BRIEF = "model: sonnet\neffort: low\ndo it"
OPTIONS = [{"label": "x", "desc": ""}, {"label": "y", "desc": ""}]


class RequesterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home(requester=True)
        self.home = self.cfg.home
        self.s = Store(self.cfg.db)
        for name in ("ana", "bo"):
            self.s.add_member(name)

    def dah(self, *args: str, lane: str | None = None, ok: bool = True) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(ROOT)}
        env.pop("TOWER_LANE", None)
        if lane:
            env["TOWER_LANE"] = lane
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        if ok and p.returncode:
            self.fail(f"dah {' '.join(args)} failed: {p.stderr}")
        return p.stdout + p.stderr

    def newest_task(self) -> dict:
        return self.s.tasks()[0]

    def newest_item(self) -> dict:
        return self.s.items()[0]

    def test_a_task_from_a_request_records_who_asked(self) -> None:
        mid = self.s.push(HUB, "bo", "memo", "please")
        self.dah("task", "docs", "--title", "t", "--request", str(mid), BRIEF, lane="hub")
        self.assertEqual(self.newest_task()["requester"], "bo")
        self.dah("task", "docs", "--title", "weekly", BRIEF, lane="hub")
        self.assertIsNone(self.newest_task()["requester"])
        self.dah("task", "docs", "--title", "t2", "--for", "ana", BRIEF, lane="hub")
        self.assertEqual(self.newest_task()["requester"], "ana")
        self.dah("task", "docs", "--title", "t3", "--for", "all", BRIEF, lane="hub")
        self.assertIsNone(self.newest_task()["requester"])
        self.assertIn("no member", self.dah("task", "docs", "--title", "t", "--for", "zed", BRIEF, lane="hub", ok=False))
        self.assertIn("not a person", self.dah("task", "docs", "--title", "t", "--request",
                                               str(self.s.push(INFRA, "hub", "memo", "x")), BRIEF, lane="hub", ok=False))

    def test_a_lane_card_goes_to_its_tasks_requester(self) -> None:
        tid = self.s.add_task("docs", "t", "brief", requester="ana")
        self.s.update_task(tid, state="running")
        self.dah("item", "add", "--title", "look", "--body", "## a", lane="docs")
        self.assertEqual((self.newest_item()["assignee"], self.newest_item()["audience"]), ("ana", "hub"))
        self.dah("item", "add", "--title", "other", "--body", "## a", "--to", "bo", lane="docs")
        self.assertEqual(self.newest_item()["assignee"], "bo")
        self.dah("item", "add", "--title", "all", "--body", "## a", "--to", "all", lane="docs")
        self.assertIsNone(self.newest_item()["assignee"])
        self.assertIn("no member", self.dah("item", "add", "--title", "x", "--body", "## a", "--to", "zed",
                                            lane="docs", ok=False))

    def test_hub_cards_go_to_the_sender_of_the_request_they_answer(self) -> None:
        mid = self.s.push(HUB, "ana", "memo", "please")
        self.dah("item", "add", "--title", "result", "--body", "## a", "--request", str(mid), lane="hub")
        self.assertEqual(self.newest_item()["assignee"], "ana")
        self.dah("item", "add", "--title", "alert", "--body", "## a", lane="hub")
        self.assertIsNone(self.newest_item()["assignee"])

    def test_escalating_keeps_the_person_and_all_widens_it(self) -> None:
        tid = self.s.add_task("docs", "t", "brief", requester="ana")
        self.s.update_task(tid, state="running")
        self.dah("item", "add", "--title", "look", "--body", "## a", lane="docs")
        iid = self.newest_item()["id"]
        self.dah("escalate", str(iid), "--why", "human eye", lane="hub")
        self.assertEqual(self.s.item(iid)["assignee"], "ana")
        self.dah("item", "add", "--title", "again", "--body", "## a", lane="docs")
        again = self.newest_item()["id"]
        self.dah("escalate", str(again), "--why", "x", "--to", "all", lane="hub")
        self.assertIsNone(self.s.item(again)["assignee"])

    def test_the_hubs_reply_card_is_for_the_person_who_asked(self) -> None:
        mid = self.s.push(HUB, "bo", "memo", "please")
        self.dah("memo", "reply", str(mid), "done", lane="hub")
        self.assertEqual(self.newest_item()["assignee"], "bo")

    def test_a_follow_up_task_keeps_the_requester(self) -> None:
        tid = self.s.add_task("docs", "t", "brief", requester="ana")
        self.s.update_task(tid, state="running")
        iid = self.s.add_item("docs", tid, "decision", "pick", "## a", OPTIONS, [], audience=HUMAN, assignee="ana")
        self.s.decide(iid, "ok", "ana", "x")
        clock = Clock()
        Engine(self.cfg, Store(self.cfg.db, clock=clock), FakeRuntime(self.s), lambda _: None, clock=clock,
               log=lambda _: None).tick()
        follow = self.newest_task()
        self.assertNotEqual(follow["id"], tid)
        self.assertEqual(follow["requester"], "ana")

    def test_infra_answers_and_wraps_reach_the_hub(self) -> None:
        mid = self.s.push(INFRA, HUB, "memo", "fix it")
        self.s.claim_inbox(INFRA)
        self.dah("memo", "reply", str(mid), "done · checked", lane="infra")
        news = self.s.pending(HUB)
        self.assertEqual([m["kind"] for m in news], ["infra_reply"])
        self.assertIn("done · checked", news[0]["text"])
        self.dah("report", "wrapped", "fixed", lane="infra")
        self.assertEqual(len(self.s.pending(HUB)), 1)
        self.s.finish_inbox(INFRA)
        card = self.s.add_item(INFRA, None, "review", "ok?", "## a", [], [], audience=HUMAN, assignee="ana")
        self.s.push(INFRA, "tower", "decision", "#1 ok", None, card)
        self.s.claim_inbox(INFRA)
        self.dah("report", "wrapped", "reinstalled", lane="infra")
        self.assertEqual([m["kind"] for m in self.s.pending(HUB)], ["infra_reply", "wrapped"])
        self.assertIn("reinstalled", self.s.pending(HUB)[1]["text"])

    def test_infra_card_goes_to_the_person_behind_the_decision(self) -> None:
        card = self.s.add_item(INFRA, None, "review", "first", "## a", [], [], audience=HUMAN, assignee="bo")
        self.s.push(INFRA, "tower", "decision", "#1 ok", None, card)
        self.s.claim_inbox(INFRA)
        self.dah("item", "add", "--title", "second", "--body", "## a", lane="infra")
        self.assertEqual(self.newest_item()["assignee"], "bo")


class RequesterOffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home()
        self.s = Store(self.cfg.db)
        self.s.add_member("ana")

    def dah(self, *args: str, lane: str) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.cfg.home), "PYTHONPATH": str(ROOT), "TOWER_LANE": lane}
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.cfg.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_cards_and_infra_news_stay_as_they_were(self) -> None:
        tid = self.s.add_task("docs", "t", "brief", requester="ana")
        self.s.update_task(tid, state="running")
        self.dah("item", "add", "--title", "look", "--body", "## a", lane="docs")
        self.assertIsNone(self.s.items()[0]["assignee"])
        mid = self.s.push(INFRA, HUB, "memo", "fix it")
        self.s.claim_inbox(INFRA)
        self.dah("memo", "reply", str(mid), "done", lane="infra")
        self.dah("report", "wrapped", "fixed", lane="infra")
        self.assertEqual([m["kind"] for m in self.s.pending(HUB)], ["item"])

    def test_the_requester_is_still_recorded(self) -> None:
        self.dah("task", "docs", "--title", "t", "--for", "ana", BRIEF, lane="hub")
        self.assertEqual(self.s.tasks()[0]["requester"], "ana")


class VisibilityRuleTest(unittest.TestCase):
    def test_a_card_for_one_person_shows_only_to_them(self) -> None:
        card = {"assignee": "ana"}
        self.assertTrue(visible_to_requester(card, "ana", local=False))
        self.assertFalse(visible_to_requester(card, "bo", local=False))

    def test_a_card_for_nobody_and_a_local_board_show_to_everyone(self) -> None:
        self.assertTrue(visible_to_requester({"assignee": None}, "bo", local=False))
        self.assertTrue(visible_to_requester({"assignee": "ana"}, "bo", local=True))


class MigrationTest(unittest.TestCase):
    def test_an_old_database_gets_the_column_and_keeps_its_rows(self) -> None:
        path = make_home().db
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
        con.execute("INSERT INTO tasks(lane, title, brief, state, created) VALUES('docs', 'old', 'b', 'closed', 1)")
        con.commit()
        con.close()
        s = Store(path)
        self.assertIsNone(s.task(1)["requester"])
        self.assertIsNone(s.requester_of_task(1))
        self.assertEqual(s.requester_of_task(s.add_task("docs", "t", "b", requester="ana")), "ana")


if __name__ == "__main__":
    unittest.main()
