import unittest

from unittest import mock

from agent_hub import visibility
from agent_hub.store import HUB, HUMAN, INFRA
from tests.test_web import POST, Board

H = "X-Forwarded-Email"
OPTS = [{"label": "A", "desc": ""}, {"label": "B", "desc": ""}]


class CardVisibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Board(auth={"mode": "header", "header": H})
        s = self.b.s
        s.add_member("ana", "owner", "ana@x.com")
        s.add_member("bo", "member", "bo@x.com")
        s.add_member("cy", "member", "cy@x.com")
        work = self.b.cfg.workdir
        for d in ("docs/board/d/bo", "docs/board/d/cy", "docs/board/d/all"):
            (work / d).mkdir(parents=True)
            (work / d / "shot.png").write_bytes(b"\x89PNG....")
            (work / d / "card.md").write_text("## body", encoding="utf-8")
        (work / "docs/plain.md").write_text("open", encoding="utf-8")
        self.bo = s.add_item(INFRA, None, "decision", "bo's", "bo secret", OPTS, ["docs/board/d/bo/shot.png"],
                             audience=HUMAN, assignee="bo")
        self.cy = s.add_item(INFRA, None, "decision", "cy's", "cy secret", OPTS, ["docs/board/d/cy/shot.png"],
                             audience=HUMAN, assignee="cy")
        self.all = s.add_item(INFRA, None, "review", "everyone's", "for all", [], ["docs/board/d/all/shot.png"],
                              audience=HUMAN)
        self.bo_done = s.add_item(INFRA, None, "review", "bo done", "old", [], [], audience=HUMAN, assignee="bo")
        self.cy_done = s.add_item(INFRA, None, "review", "cy done", "old", [], [], audience=HUMAN, assignee="cy")
        s.decide(self.bo_done, "ok", "bo")
        s.decide(self.cy_done, "ok", "cy")

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def get(self, path: str, who: str | None):
        return self.b.req("GET", path, headers={H: who} if who else {})

    def test_state_has_only_own_and_everyones_cards(self) -> None:
        code, st, _ = self.get("/api/state", "bo@x.com")
        self.assertEqual(code, 200)
        self.assertEqual(sorted(it["id"] for it in st["items"]), sorted([self.bo, self.all, self.bo_done]))
        self.assertNotIn("cy secret", str(st))
        self.assertEqual(st["counts"]["decided"], 1)

    def test_owner_sees_every_card(self) -> None:
        _, st, _ = self.get("/api/state", "ana@x.com")
        ids = {it["id"] for it in st["items"]}
        self.assertTrue({self.bo, self.cy, self.all}.issubset(ids))
        self.assertEqual(st["counts"]["decided"], 2)
        self.assertEqual(self.get(f"/api/items/{self.cy}", "ana@x.com")[0], 200)

    def test_no_header_gets_nothing(self) -> None:
        for path in ("/api/state", f"/api/items/{self.all}", "/api/history?kind=decided", "/f/docs/plain.md"):
            self.assertEqual(self.get(path, None)[0], 401, path)
        code, _, _ = self.b.req("POST", f"/api/items/{self.all}/decide", {"verdict": "ok"}, POST)
        self.assertEqual(code, 401)

    def test_direct_read_and_answer_refused(self) -> None:
        code, got, _ = self.get(f"/api/items/{self.cy}", "bo@x.com")
        self.assertEqual(code, 404)
        self.assertNotIn("cy secret", str(got))
        self.assertEqual(self.get(f"/api/items/{self.bo}", "bo@x.com")[0], 200)
        self.assertEqual(self.get(f"/api/items/{self.all}", "cy@x.com")[0], 200)
        code, _, _ = self.b.req("POST", f"/api/items/{self.cy}/decide", {"verdict": "ok", "choice": "A"},
                                {**POST, H: "bo@x.com"})
        self.assertEqual(code, 404)
        self.assertEqual(self.b.s.item(self.cy)["state"], "open")
        code, _, _ = self.b.req("POST", f"/api/items/{self.cy}/decide", {"verdict": "ok", "choice": "A"},
                                {**POST, H: "cy@x.com"})
        self.assertEqual(code, 200)

    def test_history_decided_is_filtered_and_pages(self) -> None:
        _, h, _ = self.get("/api/history?kind=decided", "bo@x.com")
        self.assertEqual([r["id"] for r in h["rows"]], [self.bo_done])
        _, h, _ = self.get("/api/history?kind=decided&offset=1", "bo@x.com")
        self.assertEqual(h["rows"], [])
        _, h, _ = self.get("/api/history?kind=decided", "ana@x.com")
        self.assertEqual(len(h["rows"]), 2)

    def test_public_history_stays(self) -> None:
        self.b.s.push(HUB, "cy", "memo", "cy asks for a thing")
        _, h, _ = self.get("/api/history?kind=requests", "bo@x.com")
        self.assertIn("cy asks for a thing", str(h["rows"]))

    def test_hub_side_cards_are_not_a_members(self) -> None:
        hub_side = self.b.s.add_item("docs", None, "review", "draft", "hub side only", [], [])
        _, st, _ = self.get("/api/state", "bo@x.com")
        self.assertNotIn(hub_side, [it["id"] for it in st["items"]])
        self.assertEqual(self.get(f"/api/items/{hub_side}", "bo@x.com")[0], 404)
        _, st, _ = self.get("/api/state", "ana@x.com")
        self.assertIn(hub_side, [it["id"] for it in st["items"]])

    def test_infra_memos_are_the_owners(self) -> None:
        self.b.s.push(INFRA, HUB, "memo", "hub tells infra")
        _, st, _ = self.get("/api/state", "bo@x.com")
        self.assertEqual(st["memos"][INFRA], [])
        self.assertEqual(st["counts"]["memos"], 0)
        self.assertEqual(self.get("/api/history?kind=memos", "bo@x.com")[1]["rows"], [])
        _, st, _ = self.get("/api/state", "ana@x.com")
        self.assertEqual(len(st["memos"][INFRA]), 1)
        self.assertEqual(st["counts"]["memos"], 1)

    def test_hub_chat_is_shared_until_a_memo_rule_is_added(self) -> None:
        self.b.s.push(HUB, "cy", "memo", "cy asks")
        self.b.s.push(HUB, "bo", "memo", "bo asks")
        _, st, _ = self.get("/api/state", "bo@x.com")
        self.assertEqual(len(st["memos"][HUB]), 2)

    def test_a_memo_rule_filters_state_history_and_counts(self) -> None:
        for i in range(30):
            self.b.s.push(HUB, "cy", "memo", f"cy {i}")
        self.b.s.push(HUB, "bo", "memo", "bo asks")

        def own(me, target, memo):
            return memo["sender"] == me["name"]

        with mock.patch.object(visibility, "MEMO_RULES", (own,)):
            _, st, _ = self.get("/api/state", "bo@x.com")
            self.assertEqual([m["text"] for m in st["memos"][HUB]], ["bo asks"])
            self.assertEqual(st["counts"]["requests"], 1)
            _, h, _ = self.get("/api/history?kind=requests", "bo@x.com")
            self.assertEqual([m["text"] for m in h["rows"]], ["bo asks"])
            _, st, _ = self.get("/api/state", "ana@x.com")
            self.assertEqual(st["counts"]["requests"], 31)

    def test_card_files_follow_the_card(self) -> None:
        self.assertEqual(self.get("/f/docs/board/d/cy/shot.png", "bo@x.com")[0], 404)
        self.assertEqual(self.get("/f/docs/board/d/cy/card.md", "bo@x.com")[0], 404)
        self.assertEqual(self.get("/f/docs/board//d/cy/shot.png", "bo@x.com")[0], 404)
        self.assertEqual(self.get("/f/docs/board/d/cy/shot.png", "cy@x.com")[0], 200)
        self.assertEqual(self.get("/f/docs/board/d/cy/shot.png", "ana@x.com")[0], 200)
        self.assertEqual(self.get("/f/docs/board/d/bo/shot.png", "bo@x.com")[0], 200)
        self.assertEqual(self.get("/f/docs/board/d/all/card.md", "cy@x.com")[0], 200)
        self.assertEqual(self.get("/f/docs/plain.md", "bo@x.com")[0], 200)

    def test_report_attachment_stays_public(self) -> None:
        self.b.s.add_report(None, INFRA, "done", "shipped", ["docs/board/d/cy/shot.png"])
        self.assertEqual(self.get("/f/docs/board/d/cy/shot.png", "bo@x.com")[0], 200)


class LocalModeSeesAllTest(unittest.TestCase):
    def test_local_owner(self) -> None:
        b = Board()
        try:
            iid = b.s.add_item(INFRA, None, "review", "x", "y", [], [], audience=HUMAN, assignee="someone")
            self.assertEqual(b.req("GET", f"/api/items/{iid}")[0], 200)
        finally:
            b.srv.shutdown()


if __name__ == "__main__":
    unittest.main()
