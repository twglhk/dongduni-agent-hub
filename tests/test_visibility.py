import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from agent_hub import config as config_mod
from agent_hub import visibility
from agent_hub.store import HUB, HUMAN, INFRA, TOWER, Store
from agent_hub.web import make_handler
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

        def own(gate, target, memo):
            return memo["sender"] == gate.name

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
        self.assertEqual(self.get("/f/docs/plain.md", "bo@x.com")[0], 404)
        self.assertEqual(self.get("/f/docs/plain.md", "ana@x.com")[0], 200)

    def test_report_attachment_stays_public(self) -> None:
        self.b.s.add_report(None, INFRA, "done", "shipped", ["docs/board/d/cy/shot.png"])
        self.assertEqual(self.get("/f/docs/board/d/cy/shot.png", "bo@x.com")[0], 200)


def scoped_board(requester: bool, person_hubs: bool) -> Board:
    b = Board(auth={"mode": "header", "header": H})
    for name, role in (("ana", "owner"), ("bo", "member"), ("cy", "member")):
        b.s.add_member(name, role, f"{name}@x.com")
    path = b.cfg.home / ".tower" / "tower.json"
    data = json.loads(path.read_text())
    data["requester"] = requester
    if person_hubs:
        data["person_hubs"] = {"max": 3}
    path.write_text(json.dumps(data))
    cfg = config_mod.load(b.cfg.home)
    b.srv.shutdown()
    b.srv.server_close()
    b.cfg, b.s = cfg, Store(cfg.db)
    b.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg, b.s, lambda _: None))
    threading.Thread(target=b.srv.serve_forever, daemon=True).start()
    b.base = f"http://127.0.0.1:{b.srv.server_address[1]}"
    return b


class ScopeCase(unittest.TestCase):
    requester, person_hubs = True, True

    def setUp(self) -> None:
        self.b = scoped_board(self.requester, self.person_hubs)
        self.seed()

    def seed(self) -> None:
        s = self.b.s
        work = self.b.cfg.workdir
        for who in ("bo", "cy"):
            (work / f"docs/board/d/{who}").mkdir(parents=True)
            (work / f"docs/board/d/{who}/shot.png").write_bytes(b"\x89PNG....")
        self.task = {w: s.add_task("docs", f"{w} task", "brief", requester=w, hub=f"hub-{w}") for w in ("bo", "cy")}
        self.hub_card = {w: s.add_item("docs", self.task[w], "review", f"{w} triage", f"{w} secret", [],
                                       [f"docs/board/d/{w}/shot.png"], audience=HUB) for w in ("bo", "cy")}
        self.shared_card = s.add_item("docs", None, "review", "no owner", "triage", [], [], audience=HUB)
        self.human_card = {w: s.add_item(f"hub-{w}", None, "decision", f"{w} asks", f"{w} ask", OPTS, [],
                                         audience=HUMAN, assignee=w) for w in ("bo", "cy")}
        self.everyone = s.add_item(INFRA, None, "review", "for all", "all", [], [], audience=HUMAN)
        s.push(HUB, "bo", "memo", "bo asks shared")
        s.push(HUB, "cy", "memo", "cy asks shared")
        s.push(HUB, TOWER, "memo", "tower notice")
        s.push("hub-bo", "bo", "memo", "bo asks own hub")
        s.push("hub-bo", TOWER, "memo", "notice in bo hub")
        s.push("hub-cy", "cy", "memo", "cy asks own hub")
        s.push(INFRA, "hub-bo", "memo", "bo hub tells infra")
        s.push(INFRA, "hub-cy", "memo", "cy hub tells infra")
        s.push(INFRA, HUB, "memo", "shared hub tells infra")

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def get(self, path: str, who: str):
        return self.b.req("GET", path, headers={H: f"{who}@x.com"})

    def card_ids(self, who: str) -> set[int]:
        return {it["id"] for it in self.get("/api/state", who)[1]["items"]}

    def chat(self, who: str) -> set[str]:
        return {m["text"] for m in self.get("/api/history?kind=requests", who)[1]["rows"]}

    def infra(self, who: str) -> set[str]:
        return {m["text"] for m in self.get("/api/history?kind=memos", who)[1]["rows"]}


class RequesterScopeTest(ScopeCase):
    def test_a_member_sees_their_own_hub_cards_and_addressed_cards(self) -> None:
        self.assertEqual(self.card_ids("bo"), {self.hub_card["bo"], self.human_card["bo"], self.everyone})
        self.assertEqual(self.card_ids("cy"), {self.hub_card["cy"], self.human_card["cy"], self.everyone})

    def test_the_owner_sees_every_card(self) -> None:
        got = self.card_ids("ana")
        self.assertTrue({*self.hub_card.values(), *self.human_card.values(), self.shared_card, self.everyone} <= got)

    def test_another_members_card_is_not_fetched_or_answered(self) -> None:
        for cid in (self.hub_card["cy"], self.human_card["cy"], self.shared_card):
            self.assertEqual(self.get(f"/api/items/{cid}", "bo")[0], 404, cid)
            code, _, _ = self.b.req("POST", f"/api/items/{cid}/decide", {"verdict": "ok", "choice": "x"},
                                    {**POST, H: "bo@x.com"})
            self.assertEqual(code, 404, cid)
            self.assertEqual(self.b.s.item(cid)["state"], "open")
        self.assertEqual(self.get(f"/api/items/{self.hub_card['bo']}", "bo")[0], 200)
        self.assertEqual(self.get(f"/api/items/{self.hub_card['cy']}", "ana")[0], 200)

    def test_a_card_for_a_task_the_member_requested_is_theirs_even_in_the_shared_hub(self) -> None:
        tid = self.b.s.add_task("docs", "t", "brief", requester="bo")
        cid = self.b.s.add_item("docs", tid, "review", "shared hub triage", "x", [], [], audience=HUB)
        self.assertIn(cid, self.card_ids("bo"))
        self.assertNotIn(cid, self.card_ids("cy"))

    def test_decided_history_pages_and_counts_follow_the_same_rule(self) -> None:
        for cid in (*self.hub_card.values(), self.shared_card):
            self.b.s.decide(cid, "ok", "ana")
        for cid in self.human_card.values():
            self.b.s.decide(cid, "ok", "ana", "A")
        _, st, _ = self.get("/api/state", "bo")
        self.assertEqual(st["counts"]["decided"], 2)
        rows = self.get("/api/history?kind=decided", "bo")[1]["rows"]
        self.assertEqual({r["id"] for r in rows}, {self.hub_card["bo"], self.human_card["bo"]})
        self.assertEqual(self.get("/api/history?kind=decided&offset=2", "bo")[1]["rows"], [])
        self.assertEqual(self.get("/api/state", "ana")[1]["counts"]["decided"], 5)

    def test_attachments_follow_their_card(self) -> None:
        self.assertEqual(self.get("/f/docs/board/d/cy/shot.png", "bo")[0], 404)
        self.assertEqual(self.get("/f/docs/board/d/bo/shot.png", "bo")[0], 200)
        self.assertEqual(self.get("/f/docs/board/d/cy/shot.png", "ana")[0], 200)

    def test_chat_is_the_members_own_requests_and_their_own_hub(self) -> None:
        self.assertEqual(self.chat("bo"), {"bo asks shared", "bo asks own hub", "notice in bo hub"})
        self.assertEqual(self.chat("cy"), {"cy asks shared", "cy asks own hub"})
        self.assertEqual(len(self.chat("ana")), 6)
        _, st, _ = self.get("/api/state", "bo")
        self.assertEqual({m["text"] for m in st["memos"][HUB]}, {"bo asks own hub", "notice in bo hub"})
        self.assertEqual(st["counts"]["requests"], 3)
        self.assertEqual(self.get("/api/state", "ana")[1]["counts"]["requests"], 6)

    def test_chat_pages_after_filtering(self) -> None:
        for i in range(25):
            self.b.s.push(HUB, "cy", "memo", f"cy more {i}")
        self.assertEqual(self.chat("bo"), {"bo asks shared", "bo asks own hub", "notice in bo hub"})
        self.assertEqual(self.get("/api/history?kind=requests&offset=20", "bo")[1]["rows"], [])

    def test_infra_memos_are_the_ones_the_members_hub_caused(self) -> None:
        self.assertEqual(self.infra("bo"), {"bo hub tells infra"})
        self.assertEqual(self.infra("cy"), {"cy hub tells infra"})
        self.assertEqual(len(self.infra("ana")), 3)
        _, st, _ = self.get("/api/state", "bo")
        self.assertEqual([m["text"] for m in st["memos"][INFRA]], ["bo hub tells infra"])
        self.assertEqual((st["counts"]["memos"], self.get("/api/state", "ana")[1]["counts"]["memos"]), (1, 3))


class RequesterWithoutPersonHubsTest(ScopeCase):
    person_hubs = False

    def seed(self) -> None:
        s = self.b.s
        self.task = s.add_task("docs", "t", "brief", requester="bo")
        self.hub_card = s.add_item("docs", self.task, "review", "triage", "x", [], [], audience=HUB)
        s.push(HUB, "bo", "memo", "bo asks")
        s.push(HUB, "cy", "memo", "cy asks")
        s.push(INFRA, HUB, "memo", "hub tells infra")

    def test_hub_side_cards_stay_hidden_from_members(self) -> None:
        self.assertNotIn(self.hub_card, self.card_ids("bo"))
        self.assertEqual(self.get(f"/api/items/{self.hub_card}", "bo")[0], 404)
        self.assertIn(self.hub_card, self.card_ids("ana"))

    def test_chat_is_the_members_own_requests(self) -> None:
        self.assertEqual(self.chat("bo"), {"bo asks"})
        self.assertEqual(self.chat("ana"), {"bo asks", "cy asks"})
        self.assertEqual(self.get("/api/state", "cy")[1]["counts"]["requests"], 1)

    def test_infra_memos_stay_the_owners(self) -> None:
        self.assertEqual(self.infra("bo"), set())
        self.assertEqual(self.infra("ana"), {"hub tells infra"})

    def test_a_member_cannot_fetch_the_hub_side_card_through_state_history_or_files(self) -> None:
        self.b.s.decide(self.hub_card, "ok", "ana")
        self.assertEqual(self.get("/api/history?kind=decided", "bo")[1]["rows"], [])


class PersonHubsWithoutRequesterTest(ScopeCase):
    requester, person_hubs = False, True

    def test_a_member_sees_their_own_hub_cards_but_not_anothers(self) -> None:
        self.assertEqual(self.card_ids("bo"), {self.hub_card["bo"], self.human_card["bo"], self.everyone})
        for cid in (self.hub_card["cy"], self.human_card["cy"], self.shared_card):
            self.assertEqual(self.get(f"/api/items/{cid}", "bo")[0], 404, cid)
            code, _, _ = self.b.req("POST", f"/api/items/{cid}/decide", {"verdict": "ok", "choice": "A"},
                                    {**POST, H: "bo@x.com"})
            self.assertEqual(code, 404, cid)
            self.assertEqual(self.b.s.item(cid)["state"], "open")
        self.assertEqual(self.get(f"/api/items/{self.hub_card['bo']}", "bo")[0], 200)
        self.assertEqual(self.get(f"/api/items/{self.hub_card['cy']}", "ana")[0], 200)

    def test_a_card_for_a_task_of_the_shared_hub_stays_hidden_without_requester(self) -> None:
        tid = self.b.s.add_task("docs", "t", "brief", requester="bo")
        cid = self.b.s.add_item("docs", tid, "review", "shared hub triage", "x", [], [], audience=HUB)
        self.assertNotIn(cid, self.card_ids("bo"))

    def test_decided_history_pages_and_counts_follow_the_same_rule(self) -> None:
        for cid in (*self.hub_card.values(), self.shared_card):
            self.b.s.decide(cid, "ok", "ana")
        for cid in self.human_card.values():
            self.b.s.decide(cid, "ok", "ana", "A")
        self.assertEqual(self.get("/api/state", "bo")[1]["counts"]["decided"], 2)
        rows = self.get("/api/history?kind=decided", "bo")[1]["rows"]
        self.assertEqual({r["id"] for r in rows}, {self.hub_card["bo"], self.human_card["bo"]})
        self.assertEqual(self.get("/api/history?kind=decided&offset=2", "bo")[1]["rows"], [])

    def test_attachments_follow_their_card(self) -> None:
        self.assertEqual(self.get("/f/docs/board/d/cy/shot.png", "bo")[0], 404)
        self.assertEqual(self.get("/f/docs/board/d/bo/shot.png", "bo")[0], 200)
        self.assertEqual(self.get("/f/docs/board/d/cy/shot.png", "ana")[0], 200)

    def test_another_persons_hub_chat_is_never_sent_but_the_shared_chat_stays(self) -> None:
        self.assertEqual(self.chat("bo"), {"bo asks shared", "cy asks shared", "tower notice", "bo asks own hub",
                                           "notice in bo hub"})
        self.assertEqual(len(self.chat("ana")), 6)
        _, st, _ = self.get("/api/state", "bo")
        self.assertEqual({m["text"] for m in st["memos"][HUB]}, {"bo asks own hub", "notice in bo hub"})
        self.assertEqual(st["counts"]["requests"], 5)
        self.assertEqual(self.get("/api/state", "ana")[1]["counts"]["requests"], 6)

    def test_chat_pages_after_filtering(self) -> None:
        for i in range(25):
            self.b.s.push("hub-cy", "cy", "memo", f"cy more {i}")
        self.assertEqual(len(self.chat("bo")), 5)
        self.assertEqual(self.get("/api/history?kind=requests&offset=20", "bo")[1]["rows"], [])

    def test_infra_memos_are_the_ones_the_members_hub_sent(self) -> None:
        self.assertEqual(self.infra("bo"), {"bo hub tells infra"})
        self.assertEqual(self.infra("cy"), {"cy hub tells infra"})
        self.assertEqual(len(self.infra("ana")), 3)
        _, st, _ = self.get("/api/state", "bo")
        self.assertEqual([m["text"] for m in st["memos"][INFRA]], ["bo hub tells infra"])
        self.assertEqual(st["counts"]["memos"], 1)


class FlagsOffTest(ScopeCase):
    requester, person_hubs = False, False

    def seed(self) -> None:
        ScopeCase.seed(self)

    def test_a_member_sees_the_human_side_cards_and_all_hub_chat_but_no_infra_memos(self) -> None:
        self.assertEqual(self.card_ids("bo"), {self.human_card["bo"], self.everyone})
        self.assertEqual(self.chat("bo"), {"bo asks shared", "cy asks shared", "tower notice", "bo asks own hub",
                                           "notice in bo hub", "cy asks own hub"})
        self.assertEqual(self.infra("bo"), set())
        self.assertEqual(len(self.infra("ana")), 3)

    def test_hub_side_cards_stay_with_the_owner(self) -> None:
        for cid in (*self.hub_card.values(), self.shared_card):
            self.assertEqual(self.get(f"/api/items/{cid}", "bo")[0], 404)
            self.assertEqual(self.get(f"/api/items/{cid}", "ana")[0], 200)


class WorkFeedScopeTest(ScopeCase):
    def seed(self) -> None:
        ScopeCase.seed(self)
        s = self.b.s
        for w in ("bo", "cy"):
            s.add_item("docs", self.task[w], "review", f"{w} triage 2", f"{w} secret", [], [], audience=HUB)
        s.push(INFRA, "tower", "decision", "Decision follow-up", item_id=self.human_card["cy"])
        s.add_report(None, "infra", "wrapped", "unlinked report", [])

    def work(self, who: str, offset: int = 0) -> list[dict]:
        return self.get(f"/api/history?kind=work&offset={offset}", who)[1]["rows"]

    def titles(self, who: str) -> set[str]:
        return {r["title"] for r in self.work(who)}

    def test_a_member_sees_only_their_own_work(self) -> None:
        self.assertEqual(self.titles("bo"), {"bo task", "bo hub tells infra"})
        self.assertEqual(self.titles("cy"), {"cy task", "cy hub tells infra", "cy asks"})
        self.assertNotIn("cy secret", str(self.work("bo")))
        self.assertNotIn("unlinked report", str(self.work("bo")))

    def test_the_owner_sees_every_row(self) -> None:
        self.assertEqual(len(self.work("ana")), 7)
        self.assertEqual(self.get("/api/state", "ana")[1]["counts"]["work"], 7)

    def test_counts_and_paging_follow_the_same_rule(self) -> None:
        self.assertEqual(self.get("/api/state", "bo")[1]["counts"]["work"], 2)
        self.assertEqual(self.work("bo", 2), [])
        for i in range(25):
            self.b.s.add_task("docs", f"cy more {i}", "brief", requester="cy", hub="hub-cy")
        self.assertEqual(len(self.work("bo")), 2)
        self.assertEqual(len(self.work("cy")), 20)
        self.assertEqual(len(self.work("cy", 20)), 8)

    def test_cards_inside_a_row_follow_card_visibility(self) -> None:
        row = next(r for r in self.work("bo") if r["title"] == "bo task")
        self.assertEqual({c["title"] for c in row["cards"]}, {"bo triage", "bo triage 2"})
        owner = next(r for r in self.work("ana") if r["title"] == "cy task")
        self.assertEqual(len(owner["cards"]), 2)

    def test_the_shared_hub_row_is_for_the_owner_only(self) -> None:
        self.assertIn("shared hub tells infra", self.titles("ana"))
        self.assertNotIn("shared hub tells infra", self.titles("bo"))

    def test_a_task_of_my_requests_in_the_shared_hub_is_mine(self) -> None:
        self.b.s.add_task("docs", "mine via shared hub", "brief", requester="bo")
        self.assertIn("mine via shared hub", self.titles("bo"))
        self.assertNotIn("mine via shared hub", self.titles("cy"))


class WorkFeedFlagsOffTest(ScopeCase):
    requester, person_hubs = False, False

    def test_without_scoping_a_member_sees_the_feed(self) -> None:
        self.assertEqual(len(self.get("/api/history?kind=work", "bo")[1]["rows"]), 5)


class OthersWorkHiddenTest(unittest.TestCase):
    FLAGS = ((True, True), (True, False), (False, True))

    def build(self, requester: bool, person_hubs: bool) -> None:
        self.b = scoped_board(requester, person_hubs)
        s = self.b.s
        self.t = {}
        for w in ("bo", "cy"):
            tid = s.add_task("docs", f"{w} title", f"{w} brief", requester=w, hub=f"hub-{w}" if person_hubs else "hub")
            s.update_task(tid, state="closed", closed=5.0, paths=f"{w}/path", handoff=f"{w} handoff")
            self.t[w] = tid
            s.add_report(tid, "docs", "done", f"{w} report text", [])
        self.live = s.add_task("docs", "cy live title", "cy live brief", requester="cy",
                               hub="hub-cy" if person_hubs else "hub")
        s.claim_task(self.live)
        s.set_lane("docs", status="busy", session="s1")
        s.set_lane("hub-cy", status="busy", session="s2", doing="cy private doing", doing_at=9e9, opened=1)
        self.mid = s.push(INFRA, "hub-cy" if person_hubs else HUB, "memo", "cy infra secret memo")
        s.claim_inbox("infra")
        s.set_lane("infra", status="busy", session="s3")
        s.add_report(None, "infra", "wrapped", "cy orphan wrapped", [])

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def get(self, path: str, who: str):
        return self.b.req("GET", path, headers={H: f"{who}@x.com"})

    def test_a_member_never_gets_anothers_text_on_any_route(self) -> None:
        secrets = ("cy title", "cy brief", "cy report text", "cy live", "cy private doing", "cy infra secret",
                   "cy handoff", "cy/path", "cy orphan")
        for flags in self.FLAGS:
            self.build(*flags)
            try:
                paths = ["/api/state", "/api/history?kind=closed", "/api/history?kind=reports",
                         "/api/history?kind=work", "/api/lanes/docs", "/api/sessions/docs", "/api/sessions/infra"]
                for path in paths:
                    code, body, _ = self.get(path, "bo")
                    for text in secrets:
                        self.assertNotIn(text, json.dumps(body, ensure_ascii=False), (flags, path, text))
            finally:
                self.b.srv.shutdown()

    def test_hidden_rows_keep_count_and_layout(self) -> None:
        self.build(True, True)
        _, st, _ = self.get("/api/state", "bo")
        mine = next(t for t in st["tasks"] if t["id"] == self.t["bo"])
        other = next(t for t in st["tasks"] if t["id"] == self.t["cy"])
        self.assertEqual(mine["title"], "bo title")
        self.assertEqual((other["hidden"], other["lane"]), (True, "docs"))
        self.assertNotIn("title", other)
        self.assertEqual(len(st["tasks"]), len(self.b.s.tasks(("queued", "running", "wrapping"), 200)) + 2)
        rep = [r for r in st["reports"] if r.get("hidden")]
        self.assertTrue(rep)
        lane = next(x for x in st["lanes"] if x["lane"] == "docs")
        self.assertEqual((lane["hidden"], lane["task_id"], lane["task_title"], lane["requester"]),
                         (True, self.live, None, None))
        hub = next(x for x in st["lanes"] if x["lane"] == "hub-cy")
        self.assertEqual((hub["hidden"], hub["doing"]), (True, None))
        infra = next(x for x in st["lanes"] if x["lane"] == "infra")
        self.assertEqual((infra["hidden"], infra["doing"], infra["memo"]), (True, None, None))

    def test_the_owner_and_the_owner_of_the_work_see_everything(self) -> None:
        for flags in self.FLAGS:
            self.build(*flags)
            try:
                _, st, _ = self.get("/api/state", "ana")
                self.assertIn("cy title", json.dumps(st))
                self.assertIn("cy private doing" if flags[1] else "cy infra secret", json.dumps(st))
                _, st, _ = self.get("/api/state", "cy")
                self.assertIn("cy title", json.dumps(st))
                _, h, _ = self.get("/api/history?kind=closed", "cy")
                self.assertTrue(all("hidden" not in r or r["id"] == self.t["bo"] for r in h["rows"]))
                self.assertEqual(self.get("/api/lanes/docs", "cy")[1]["task"]["title"], "cy live title")
            finally:
                self.b.srv.shutdown()

    def test_flags_off_is_as_before(self) -> None:
        self.build(False, False)
        _, st, _ = self.get("/api/state", "bo")
        self.assertIn("cy title", json.dumps(st))
        self.assertNotIn("hidden", json.dumps(st["tasks"]))
        _, h, _ = self.get("/api/history?kind=reports", "bo")
        self.assertIn("cy report text", json.dumps(h))

    def test_lane_sheet_of_another_persons_hub_is_refused(self) -> None:
        self.build(True, True)
        self.assertEqual(self.get("/api/lanes/hub-cy", "bo")[0], 404)
        self.assertEqual(self.get("/api/lanes/hub-cy", "cy")[0], 200)
        self.assertEqual(self.get("/api/lanes/hub-cy", "ana")[0], 200)

    def test_session_report_is_the_one_of_the_shown_task(self) -> None:
        self.build(True, False)
        s = self.b.s
        s.update_task(self.live, state="closed", closed=9.0)
        mine = s.add_task("docs", "bo now", "b", requester="bo", hub="hub")
        s.claim_task(mine)
        d = self.get("/api/sessions/docs", "bo")[1]
        self.assertEqual((d["hidden"], d["work"]["id"], d["report"]), (False, mine, None))
        s.add_report(mine, "docs", "done", "bo own report", [])
        self.assertEqual(self.get("/api/sessions/docs", "bo")[1]["report"]["text"], "bo own report")

    def test_shared_hub_infra_memo_follows_the_memo_rule(self) -> None:
        self.build(True, False)
        code, d, _ = self.get("/api/sessions/infra", "bo")
        self.assertEqual(code, 404)
        self.assertEqual(self.get("/api/sessions/infra", "ana")[1]["work"]["id"], self.mid)


class FilesForMembersTest(unittest.TestCase):
    def build(self, requester: bool = False, person_hubs: bool = False) -> None:
        self.b = scoped_board(requester, person_hubs)
        self.work = self.b.cfg.workdir
        (self.work / "docs").mkdir(exist_ok=True)
        (self.work / "docs/plain.md").write_text("open", encoding="utf-8")
        self.addCleanup(self.b.srv.shutdown)

    def get(self, path: str, who: str):
        return self.b.req("GET", path, headers={H: f"{who}@x.com"})

    def gate(self, who: str) -> visibility.Gate:
        return visibility.Gate.of(self.b.cfg, self.b.s, {"name": who, "role": "owner" if who == "ana" else "member"})

    def upload(self, rid: int, name: str = "1-a.png") -> str:
        key = f"uploads/2026-11-16/{rid}/{name}"
        (self.work / key).parent.mkdir(parents=True, exist_ok=True)
        (self.work / key).write_bytes(b"\x89PNG")
        return key

    def test_a_file_nothing_visible_holds_is_the_owners(self) -> None:
        self.build()
        self.assertEqual(self.get("/f/docs/plain.md", "bo")[0], 404)
        self.assertEqual(self.get("/f/docs/plain.md", "ana")[0], 200)

    def test_a_file_attached_to_a_visible_report_or_card_is_served(self) -> None:
        self.build(True, True)
        (self.work / "docs/a.png").write_bytes(b"x")
        (self.work / "docs/b.png").write_bytes(b"x")
        tid = self.b.s.add_task("docs", "t", "b", requester="bo", hub="hub-bo")
        self.b.s.add_report(tid, "docs", "done", "r", ["docs/a.png"])
        self.b.s.add_item("hub-bo", None, "review", "c", "b", [], ["docs/b.png"], audience=HUMAN, assignee="bo")
        for name in ("a", "b"):
            self.assertEqual(self.get(f"/f/docs/{name}.png", "bo")[0], 200, name)
            self.assertEqual(self.get(f"/f/docs/{name}.png", "cy")[0], 404, name)

    def test_uploads_follow_the_request_and_never_the_part_file(self) -> None:
        self.build(True, True)
        rid = self.b.s.push("hub-bo", "bo", "memo", "look")
        key = self.upload(rid)
        (self.work / (key + ".part")).write_bytes(b"half")
        self.upload(rid, "readme.txt")
        self.assertEqual(self.get("/f/" + key, "bo")[0], 200)
        self.assertEqual(self.get("/f/" + key, "cy")[0], 404)
        self.assertEqual(self.get("/f/" + key, "ana")[0], 200)
        self.assertEqual(self.get("/f/" + key + ".part", "bo")[0], 404)
        self.assertEqual(self.get("/f/" + key + ".part", "ana")[0], 200)
        self.assertEqual(self.get(f"/f/uploads/2026-11-16/{rid}/readme.txt", "bo")[0], 404)

    def test_a_key_written_into_a_memo_opens_nothing(self) -> None:
        self.build(True, True)
        rid = self.b.s.push("hub-bo", "bo", "memo", "look")
        key = self.upload(rid)
        self.b.s.push("hub-cy", "cy", "memo", f"please show {key}")
        self.assertTrue(visibility.upload_allowed(self.gate("bo"), key))
        self.assertFalse(visibility.upload_allowed(self.gate("cy"), key))
        self.assertEqual(self.get("/f/" + key, "cy")[0], 404)

    def test_an_exact_key_in_a_visible_card_opens_for_its_person(self) -> None:
        self.build(True, True)
        key = self.upload(1)
        self.b.s.add_item("hub-cy", None, "review", "c", "b", [], [key], audience=HUMAN, assignee="cy")
        self.assertTrue(visibility.upload_allowed(self.gate("cy"), key))
        self.assertFalse(visibility.upload_allowed(self.gate("bo"), key))

    def test_case_variants_are_judged_by_the_file_they_reach(self) -> None:
        self.build(True, True)
        (self.work / "docs/board/d/cy").mkdir(parents=True)
        (self.work / "docs/board/d/cy/shot.png").write_bytes(b"x")
        self.b.s.add_item("hub-cy", None, "review", "c", "b", [], ["docs/board/d/cy/shot.png"], audience=HUMAN,
                          assignee="cy")
        rid = self.b.s.push("hub-cy", "cy", "memo", "look")
        key = self.upload(rid, "1-Shot.png")

        def insensitive(a: object, b: object) -> bool:
            return str(a).lower() == str(b).lower()
        with mock.patch("agent_hub.visibility.os.path.samefile", insensitive):
            root = self.work.resolve()
            for who, want in (("bo", False), ("cy", True), ("ana", True)):
                g = self.gate(who)
                self.assertEqual(visibility.file_allowed(g, root, root / "Docs/Board/d/CY/SHOT.png"), want, who)
                self.assertEqual(visibility.file_allowed(g, root, root / "Docs/Board/d/cy/card.md"), want, who)
                self.assertEqual(visibility.file_allowed(g, root, root / key.replace("uploads", "Uploads").replace(".png", ".PNG")),
                                 want, who)
                self.assertFalse(visibility.file_allowed(g, root, root / (key + ".PART")) and who != "ana", who)

    def test_a_file_of_another_case_is_not_the_attached_one(self) -> None:
        self.build(True, True)
        (self.work / "docs").mkdir(exist_ok=True)
        (self.work / "docs/Secret.png").write_bytes(b"x")
        (self.work / "docs/secret.png").write_bytes(b"y")
        self.b.s.add_item("hub-bo", None, "review", "c", "b", [], ["docs/secret.png"], audience=HUMAN, assignee="bo")
        self.assertEqual(self.get("/f/docs/secret.png", "bo")[0], 200)
        self.assertEqual(self.get("/f/docs/Secret.png", "bo")[0], 404)

    def test_more_secret_names_are_refused_even_to_the_owner(self) -> None:
        from agent_hub.web import blocked_name
        for name in ("prod.env", "app.env", "client_secret_123.json", "rclone.conf", "credentials-prod.txt", "vault.kdbx",
                     "id_ed25519", "id_rsa.pub", "secrets.json.", "Secrets.JSON "):
            self.assertTrue(blocked_name(name), name)
        for name in ("id_map.png", "notes.md", "shot.png"):
            self.assertFalse(blocked_name(name), name)

    def test_with_no_members_the_board_serves_the_workspace_as_before(self) -> None:
        b = Board(auth={"mode": "header", "header": H})
        try:
            (b.cfg.workdir / "docs").mkdir(exist_ok=True)
            (b.cfg.workdir / "docs/plain.md").write_text("open", encoding="utf-8")
            self.assertEqual(b.req("GET", "/f/docs/plain.md", headers={H: "solo@x.com"})[0], 200)
        finally:
            b.srv.shutdown()


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
