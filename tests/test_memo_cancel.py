import os
import subprocess
import sys
import unittest
from pathlib import Path

from agent_hub.engine import Engine
from agent_hub.requester import cancelled_text, covered_text
from agent_hub.store import HUB, HUMAN, INFRA, Store, StoreError
from tests.helpers import Clock, FakeRuntime, make_home
from tests.test_person_hubs import with_members

ROOT = Path(__file__).resolve().parents[1]
LANES = {"hub": {"runtime": "claude"}, "infra": {"runtime": "claude", "max_instances": 2}}


class MemoCancelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home(LANES)
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda n: None, clock=self.clock, log=lambda _: None)

    def dah(self, *args: str, lane: str, ok: bool = True) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.cfg.home), "PYTHONPATH": str(ROOT), "TOWER_LANE": lane}
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.cfg.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        if ok and p.returncode:
            self.fail(f"dah {' '.join(args)} failed: {p.stderr}")
        if not ok:
            self.assertNotEqual(p.returncode, 0, p.stdout)
        return p.stdout + p.stderr

    def queue(self) -> list[int]:
        return [m["id"] for m in self.s.inbox_queue(INFRA)]

    def test_hub_cancels_a_waiting_memo_with_a_reason(self) -> None:
        a = self.s.push(INFRA, "hub", "memo", "already done by another memo")
        b = self.s.push(INFRA, HUB, "memo", "real work")
        self.assertIn("out of the infra queue", self.dah("memo", "cancel", str(a), "--why", "done in #389", lane="hub"))
        self.assertEqual(self.queue(), [b])
        thread = self.s.memos(INFRA)
        self.assertEqual(next(m for m in thread if m["id"] == a)["replies"][0]["text"], "Cancelled (hub): done in #389")
        with self.s.conn() as con:
            notes = [r["text"] for r in con.execute("SELECT text FROM reports WHERE lane='infra' AND kind='note'")]
        self.assertEqual(notes, [f"#{a}: Cancelled (hub): done in #389"])
        self.e.tick()
        self.assertEqual({k: m["id"] for k, m in self.s.held_messages(["infra", "infra#2"]).items()}, {"infra": b})

    def test_the_sending_hub_hears_why(self) -> None:
        self.cfg = with_members(make_home(LANES), "alice")
        self.s = Store(self.cfg.db, clock=self.clock)
        a = self.s.push(INFRA, "hub-alice", "memo", "already done")
        self.dah("memo", "cancel", str(a), "--why", "done in #389", lane="hub")
        news = [m for m in self.s.pending("hub-alice") if m["kind"] == "memo_cancelled"]
        self.assertEqual(len(news), 1)
        self.assertIn("done in #389", news[0]["text"])

    def test_infra_report_saves_the_message_the_session_held(self) -> None:
        a = self.s.push(INFRA, HUB, "memo", "first")
        b = self.s.push(INFRA, HUB, "memo", "second")
        self.s.claim_inbox("infra#2", INFRA)
        self.s.claim_inbox("infra", INFRA)
        self.dah("report", "wrapped", f"memo #{a} was mentioned only", lane="infra")
        with self.s.conn() as con:
            row = con.execute("SELECT lane, message_id FROM reports ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(tuple(row), ("infra", b))
        rows = {r["id"]: r for r in self.s.history("work")}
        self.assertEqual((len(rows[a]["reports"]), len(rows[b]["reports"])), (0, 1))

    def test_refuses_held_unknown_and_non_hubs(self) -> None:
        a = self.s.push(INFRA, HUB, "memo", "taken")
        self.s.claim_inbox("infra", INFRA)
        self.assertIn("already held by infra", self.dah("memo", "cancel", str(a), "--why", "x", lane="hub", ok=False))
        self.dah("memo", "cancel", "9999", "--why", "x", lane="hub", ok=False)
        b = self.s.push(INFRA, HUB, "memo", "waiting")
        self.dah("memo", "cancel", str(b), "--why", "x", lane="infra", ok=False)
        self.dah("memo", "cancel", str(b), "--why", "  ", lane="hub", ok=False)
        self.assertEqual(self.queue(), [b])
        self.dah("memo", "cancel", str(b), "--why", "x", lane="hub")
        self.dah("memo", "cancel", str(b), "--why", "again", lane="hub", ok=False)

    def test_decision_follow_up_can_be_cancelled(self) -> None:
        d = self.s.push(INFRA, "tower", "decision", "Decision #5: ok", item_id=5)
        self.dah("memo", "cancel", str(d), "--why", "applied in memo #9", lane="hub")
        self.assertEqual(self.queue(), [])
        with self.s.conn() as con:
            notes = [r["text"] for r in con.execute("SELECT text FROM reports WHERE lane='infra' AND kind='note'")]
        self.assertEqual(notes, [f"#{d}: Cancelled (hub): applied in memo #9"])

    def test_infra_reply_covers_other_waiting_memos(self) -> None:
        a = self.s.push(INFRA, "hub", "memo", "the real one")
        b = self.s.push(INFRA, "hub", "memo", "same thing again")
        c = self.s.push(INFRA, HUB, "memo", "other")
        self.s.claim_inbox("infra", INFRA)
        out = self.dah("memo", "reply", str(a), "fixed, and #b too", "--covers", str(b), str(a), lane="infra")
        self.assertIn(f"#{a} not taken out", out)
        self.assertEqual(self.queue(), [c])
        why = next(m for m in self.s.memos(INFRA) if m["id"] == b)["replies"][0]["text"]
        self.assertIn(f"handled together in #{a}", why)
        self.assertTrue(any(m["kind"] == "memo_cancelled" for m in self.s.pending("hub")))
        self.dah("memo", "reply", str(c), "x", "--covers", str(a), lane="hub", ok=False)

    def test_a_parallel_infra_session_may_cover_too(self) -> None:
        a = self.s.push(INFRA, "hub", "memo", "one")
        b = self.s.push(INFRA, "hub", "memo", "two")
        self.s.claim_inbox("infra#2", INFRA)
        self.assertEqual(self.s.lane("infra#2")["active_message"], a)
        self.dah("memo", "reply", str(a), "done", "--covers", str(b), lane="infra#2")
        self.assertEqual(self.queue(), [])

    def test_store_refuses_other_targets(self) -> None:
        m = self.s.push(HUB, HUMAN, "memo", "for the hub")
        with self.assertRaises(StoreError):
            self.s.cancel_message(m, INFRA, "x", HUB)

    def test_texts_follow_the_board_language_and_fall_back_to_english(self) -> None:
        self.assertEqual(cancelled_text("en", "hub", "why"), "Cancelled (hub): why")
        self.assertEqual(cancelled_text("ko", "hub", "why"), "취소됨 (hub): why")
        self.assertEqual(cancelled_text("xx", "hub", "why"), "Cancelled (hub): why")
        self.assertEqual(covered_text("en", 7), "handled together in #7")


if __name__ == "__main__":
    unittest.main()
