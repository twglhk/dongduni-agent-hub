import unittest

from agent_hub.store import HUB, HUMAN, INFRA, Store, StoreError
from tests.helpers import make_home


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Store(make_home().db)

    def decision(self, multi: bool = False) -> int:
        return self.s.add_item("docs", None, "decision", "Pick", "## What to decide\nx",
                               [{"label": "A", "desc": ""}, {"label": "B", "desc": ""}], [], multi=multi)

    def test_decision_needs_two_options_and_review_none(self) -> None:
        with self.assertRaises(StoreError):
            self.s.add_item("docs", None, "decision", "t", "b", [{"label": "A", "desc": ""}], [])
        with self.assertRaises(StoreError):
            self.s.add_item("docs", None, "review", "t", "b", [{"label": "A", "desc": ""}], [])

    def test_multi_choice_joined_in_option_order(self) -> None:
        iid = self.decision(multi=True)
        it = self.s.decide(iid, "ok", "ana", ["B", "A"])
        self.assertEqual(it["choice"], "A + B")
        self.assertEqual(it["decided_by"], "ana")

    def test_single_choice_rejects_two(self) -> None:
        with self.assertRaises(StoreError):
            self.s.decide(self.decision(), "ok", "ana", ["A", "B"])

    def test_answer_needs_text_and_no_choice(self) -> None:
        iid = self.decision()
        with self.assertRaises(StoreError):
            self.s.decide(iid, "answer", "ana", None, "")
        with self.assertRaises(StoreError):
            self.s.decide(iid, "answer", "ana", "A", "text")
        self.assertEqual(self.s.decide(iid, "answer", "ana", None, "do C")["verdict"], "answer")

    def test_hub_needs_basis_and_cannot_decide_escalated(self) -> None:
        iid = self.decision()
        with self.assertRaises(StoreError):
            self.s.decide(iid, "ok", HUB, "A")
        self.s.escalate(iid, "taste call")
        with self.assertRaises(StoreError):
            self.s.decide(iid, "ok", HUB, "A", basis="rule 3")
        self.assertEqual(self.s.item(iid)["audience"], HUMAN)

    def test_decided_once(self) -> None:
        iid = self.decision()
        self.s.decide(iid, "hold", "ana")
        with self.assertRaises(StoreError):
            self.s.decide(iid, "hold", "bo")

    def test_memo_reply_only_by_its_target(self) -> None:
        mid = self.s.push(INFRA, "ana", "memo", "fix the tunnel")
        with self.assertRaises(StoreError):
            self.s.reply_memo(mid, "done", HUB)
        self.s.reply_memo(mid, "done", INFRA)
        memo = self.s.memos(INFRA)[0]
        self.assertEqual(memo["replies"][0]["text"], "done")

    def test_inbox_claims_oldest_and_holds_it(self) -> None:
        a = self.s.push(INFRA, "ana", "memo", "one")
        self.s.push(INFRA, "ana", "memo", "two")
        self.assertEqual(self.s.claim_inbox(INFRA)["id"], a)
        self.assertEqual(self.s.claim_inbox(INFRA)["id"], a)
        self.s.finish_inbox(INFRA)
        self.assertEqual(self.s.claim_inbox(INFRA)["text"], "two")

    def test_members_token_and_ident(self) -> None:
        token = self.s.add_member("ana", "owner", "Ana@x.com", with_token=True)
        assert token
        self.assertEqual(self.s.member_by_token(token)["name"], "ana")
        self.assertIsNone(self.s.member_by_token("nope"))
        self.assertEqual(self.s.member_by_ident("ana@X.com")["name"], "ana")
        self.assertNotIn(token, str(self.s.members()))

    def test_history_pages(self) -> None:
        for _ in range(3):
            self.s.decide(self.decision(), "hold", "ana")
        self.assertEqual(self.s.history_count("decided"), 3)
        self.assertEqual(len(self.s.history("decided", 1, 20)), 2)
        with self.assertRaises(StoreError):
            self.s.history("nope")


if __name__ == "__main__":
    unittest.main()
