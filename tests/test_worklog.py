import json
import sqlite3
import unittest

from agent_hub import worklog
from agent_hub.store import INFRA, Store
from tests.helpers import make_home


class WorkFeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.t = 1000.0
        self.s = Store(make_home().db, clock=lambda: self.t)

    def at(self, t: float) -> None:
        self.t = t

    def test_task_row_carries_brief_reports_retro_commits_and_time(self) -> None:
        tid = self.s.add_task("docs", "Write the guide", "model: opus\npaths: docs/guide/\nWrite it plainly.",
                              requester="ana")
        self.s.update_task(tid, state="running", started=1000.0)
        self.at(1600.0)
        self.s.add_report(tid, "docs", "done", "guide written, commit 1af76e3", ["docs/board/shot.png"])
        self.s.update_task(tid, state="closed", closed=1700.0)
        self.at(1700.0)
        self.s.add_report(tid, "docs", "wrapped", "guide done", [])
        self.s.add_retro(tid, "docs", "guide done", "slow: fonts", "", "check fonts first", "raw")
        [row] = self.s.history("work")
        self.assertEqual((row["src"], row["id"], row["state"], row["requester"]), ("task", tid, "closed", "ana"))
        self.assertEqual(row["brief"], "Write it plainly.")
        self.assertEqual(row["commits"], ["1af76e3"])
        self.assertEqual(row["files"], ["docs/guide/", "docs/board/shot.png"])
        self.assertEqual((row["started"], row["ended"]), (1000.0, 1700.0))
        self.assertEqual(row["reports"][1]["retro"], {"cost": "slow: fonts", "rule": "check fonts first"})
        self.assertIsNone(row["line"])

    def test_task_row_names_the_process_it_ran(self) -> None:
        a = self.s.add_task("docs", "A", "b")
        b = self.s.add_task("docs", "B", "b")
        c = self.s.add_task("docs", "C", "b")
        self.s.update_task(a, line_row="launch/teaser", line_stage="2-3", line_mode="make")
        self.s.update_task(b, line_row="B4", line_stage="1", line_mode="fix")
        rows = {r["id"]: r for r in self.s.history("work")}
        self.assertEqual(rows[a]["line"], {"row": "launch/teaser", "full": "launch", "sub": "teaser", "stage": "2-3",
                                           "mode": "make"})
        self.assertEqual(rows[b]["line"], {"row": "B4", "full": None, "sub": None, "stage": "1", "mode": "fix"})
        self.assertIsNone(rows[c]["line"])

    def test_task_row_links_the_cards_it_raised(self) -> None:
        tid = self.s.add_task("docs", "T", "b")
        iid = self.s.add_item("docs", tid, "review", "Look at this", "body", [], [])
        [row] = self.s.history("work")
        self.assertEqual(row["cards"], [{"id": iid, "title": "Look at this", "kind": "review", "state": "open"}])

    def test_infra_report_joins_its_memo_by_notice_then_by_text(self) -> None:
        m1 = self.s.push(INFRA, "hub", "memo", "[Fix the tab] request #3 ...")
        m2 = self.s.push(INFRA, "hub", "memo", "second")
        self.at(2000.0)
        self.s.add_report(None, "infra#2", "wrapped", "tab fixed, c5c3a26", [])
        self.s.push("hub", INFRA, "wrapped", f"Wrapped: infra memo #{m1}: tab fixed")
        self.at(3000.0)
        self.s.add_report(None, "infra", "wrapped", f"memo #{m2} finished", [])
        self.s.reply_memo(m2, "done it", INFRA)
        self.at(4000.0)
        self.s.add_report(None, "infra#3", "wrapped", "no number here", [])
        rows = self.s.history("work")
        self.assertEqual([(r["src"], r["id"]) for r in rows], [("report", rows[0]["id"]), ("infra", m2), ("infra", m1)])
        by = {r["id"]: r for r in rows}
        self.assertEqual((by[m1]["title"], by[m1]["lane"], by[m1]["state"]), ("Fix the tab", "infra#2", "done"))
        self.assertEqual(by[m1]["commits"], ["c5c3a26"])
        self.assertEqual([x["text"] for x in by[m2]["replies"]], ["done it"])
        self.assertEqual(self.s.history_count("work"), 3)

    def test_saved_message_id_wins_over_text(self) -> None:
        m1 = self.s.push(INFRA, "hub", "memo", "first")
        m2 = self.s.push(INFRA, "hub", "memo", "second")
        self.at(2000.0)
        self.s.add_report(None, "infra#2", "wrapped", f"memo #{m1} only talks about this", [], m2)
        rows = {r["id"]: r for r in self.s.history("work")}
        self.assertEqual(len(rows[m2]["reports"]), 1)
        self.assertEqual(rows[m1]["reports"], [])

    def test_infra_decision_joins_by_card_number(self) -> None:
        iid = self.s.add_item("infra", None, "decision", "Ship it?", "b",
                              [{"label": "A", "desc": ""}, {"label": "B", "desc": ""}], [])
        mid = self.s.push(INFRA, "tower", "decision", "Decision ...", None, iid)
        self.at(1500.0)
        self.s.add_report(None, "infra", "wrapped", f"decision #{iid} applied", [])
        [row] = self.s.history("work")
        self.assertEqual((row["id"], row["kind"], row["item_id"], len(row["reports"])), (mid, "decision", iid, 1))
        self.assertEqual([c["id"] for c in row["cards"]], [iid])

    def test_cancelled_memo_shows_its_reason_as_a_cancelled_row(self) -> None:
        a = self.s.push(INFRA, "hub", "memo", "[Same thing] again")
        self.at(1500.0)
        self.s.cancel_message(a, INFRA, "Cancelled (hub): handled together", "hub")
        [row] = self.s.history("work")
        self.assertEqual((row["id"], row["state"], row["ended"]), (a, "cancelled", 1500.0))
        self.assertIn("handled together", row["reports"][0]["text"])
        self.assertEqual(self.s.history_count("work"), 1)

    def test_cancelled_decision_follow_up_has_no_reply_but_a_reason(self) -> None:
        d = self.s.push(INFRA, "tower", "decision", "Decision: ok", item_id=5)
        self.s.cancel_message(d, INFRA, "applied elsewhere", "hub")
        [row] = self.s.history("work")
        self.assertEqual((row["state"], row["replies"]), ("cancelled", []))
        self.assertIn("applied elsewhere", row["reports"][0]["text"])

    def test_paging_and_waiting_memo(self) -> None:
        for i in range(25):
            self.at(1000.0 + i)
            self.s.push(INFRA, "hub", "memo", f"m{i}")
        page1, page2 = self.s.history("work", 0, 20), self.s.history("work", 20, 20)
        self.assertEqual((len(page1), len(page2)), (20, 5))
        self.assertEqual(page1[0]["state"], "queued")
        self.assertEqual({r["id"] for r in page1} & {r["id"] for r in page2}, set())

    def test_requests_are_what_people_see_and_infra_memos_are_work(self) -> None:
        self.s.push("hub-ana", "ana", "memo", "please do X")
        self.s.push(INFRA, "hub-ana", "memo", "infra: do X")
        self.assertEqual([m["text"] for m in self.s.history("requests")], ["please do X"])
        self.assertEqual(self.s.history_count("work"), 1)

    def test_allow_filters_before_paging_and_counting(self) -> None:
        for i in range(30):
            self.at(1000.0 + i)
            self.s.add_task("docs", f"t{i}", "b", requester="ana" if i % 3 else "bo")
        with self.s.conn() as con:
            only_bo = lambda k: k["from"] == "bo"
            self.assertEqual(worklog.count(con, only_bo), 10)
            got = worklog.page(con, 5, 20, only_bo)
            self.assertEqual(len(got), 5)
            self.assertTrue(all(r["requester"] == "bo" for r in got))


class OldDatabaseTest(unittest.TestCase):
    def test_reports_without_message_id_open_and_show(self) -> None:
        path = make_home().db
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE reports(id INTEGER PRIMARY KEY, task_id INTEGER, lane TEXT NOT NULL, kind TEXT NOT NULL, "
                    "text TEXT NOT NULL, attachments TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL)")
        con.execute("INSERT INTO reports(lane, kind, text, created) VALUES('infra', 'wrapped', 'memo #1 done', 5)")
        con.commit()
        con.close()
        s = Store(path)
        with s.conn() as c:
            self.assertIn("message_id", {r[1] for r in c.execute("PRAGMA table_info(reports)")})
        self.assertIsNone(s.reports()[0]["message_id"])
        mid = s.push(INFRA, "hub", "memo", "x")
        s.add_report(None, "infra", "wrapped", "later", [], mid)
        [row] = s.history("work")
        self.assertEqual([r["text"] for r in row["reports"]], ["memo #1 done", "later"])
        json.dumps(row)


if __name__ == "__main__":
    unittest.main()
