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
        self.assertEqual(self.get("/f/docs/plain.md", "bo@x.com")[0], 200)

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
