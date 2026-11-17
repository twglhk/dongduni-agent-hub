import unittest
import urllib.parse

from agent_hub.session_view import session_view
from agent_hub.store import HUB, INFRA, Store
from agent_hub.visibility import Gate
from tests.helpers import make_home
from tests.test_person_hubs import with_members
from tests.test_web import Board

LANES = {"hub": {"runtime": "claude"}, "infra": {"runtime": "claude", "max_instances": 2}, "docs": {"runtime": "claude"}}
H = "X-Forwarded-Email"


class SessionViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Board(lanes=LANES)
        self.s = self.b.s

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def get(self, key: str) -> tuple[int, dict]:
        code, body, _ = self.b.req("GET", "/api/sessions/" + urllib.parse.quote(key, safe=""))
        return code, body

    def test_infra_memo_now_then_last(self) -> None:
        mid = self.s.push(INFRA, HUB, "memo", "[Sheet] request #775 (ann): open a sheet.\n\nbody line")
        self.s.claim_inbox("infra")
        self.s.set_lane("infra", status="busy", session="s1")
        code, d = self.get("infra")
        self.assertEqual(code, 200)
        w = d["work"]
        self.assertEqual((w["kind"], w["id"], w["title"], w["request"], w["live"]), ("memo", mid, "Sheet", 775, True))
        self.assertIn("body line", w["body"])
        self.s.add_report(None, "infra", "wrapped", "done it || retro: cost=x", [])
        self.s.finish_inbox("infra")
        self.s.set_lane("infra", status="closed")
        d = self.get("infra")[1]
        self.assertEqual((d["live"], d["work"]["id"], d["work"]["live"]), (False, mid, False))
        self.assertIsNotNone(d["work"]["ended"])
        self.assertEqual((d["report"]["kind"], d["report"]["text"]), ("wrapped", "done it"))

    def test_a_parallel_infra_session_holds_its_own_memo(self) -> None:
        a = self.s.push(INFRA, HUB, "memo", "first", paths="a/")
        b = self.s.push(INFRA, HUB, "memo", "second", paths="b/")
        self.s.claim_inbox("infra", INFRA)
        self.s.claim_inbox("infra#2", INFRA)
        self.s.set_lane("infra#2", status="busy")
        self.assertEqual(self.get("infra#2")[1]["work"]["id"], b)
        self.assertEqual(self.get("infra#3")[0], 404)
        self.assertNotEqual(a, b)

    def test_team_task_and_unknown(self) -> None:
        t = self.s.add_task("docs", "Boards", "Write the guide.\nFor request #9.", requester="ann")
        self.s.claim_task(t)
        self.s.set_lane("docs", status="busy", session="s2")
        w = self.get("docs")[1]["work"]
        self.assertEqual((w["kind"], w["id"], w["from"], w["request"], w["live"]), ("task", t, "ann", 9, True))
        self.assertEqual(self.get("nope")[0], 404)
        d = self.get("hub")[1]
        self.assertEqual((d["work"], d["report"]), (None, None))

    def test_a_resting_team_session_shows_its_last_task(self) -> None:
        t = self.s.add_task("docs", "Old", "brief")
        self.s.claim_task(t)
        self.s.update_task(t, state="closed", closed=5.0)
        w = self.get("docs")[1]["work"]
        self.assertEqual((w["id"], w["live"], w["ended"]), (t, False, 5.0))

    def test_a_database_made_before_last_message_gets_the_column(self) -> None:
        import sqlite3
        from agent_hub.store import SCHEMA
        path = make_home().db
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
        con.commit()
        con.close()
        s = Store(path)
        s.push(INFRA, HUB, "memo", "x")
        s.claim_inbox("infra")
        s.finish_inbox("infra")
        self.assertEqual(s.lane("infra")["last_message"], 1)


class SessionVisibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Board(auth={"mode": "header", "header": H}, requester=True, lanes=LANES)
        s = self.b.s
        s.add_member("ana", "owner", "ana@x.com")
        s.add_member("bo", "member", "bo@x.com")
        s.add_member("cy", "member", "cy@x.com")
        self.task = s.add_task("docs", "cy's plan", "cy's secret brief", requester="cy")
        s.claim_task(self.task)
        s.set_lane("docs", status="busy")
        self.memo = s.push(INFRA, "cy", "memo", "cy's secret memo")
        s.claim_inbox("infra", INFRA)
        s.set_lane("infra", status="busy")

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def get(self, key: str, who: str) -> tuple[int, dict]:
        code, body, _ = self.b.req("GET", "/api/sessions/" + urllib.parse.quote(key, safe=""), headers={H: who})
        return code, body

    def test_a_member_cannot_open_a_session_working_for_someone_else(self) -> None:
        for key in ("docs", "infra"):
            code, body = self.get(key, "bo@x.com")
            self.assertEqual(code, 404, key)
            self.assertNotIn("secret", str(body))

    def test_the_requester_and_owners_can(self) -> None:
        self.assertEqual(self.get("docs", "cy@x.com")[1]["work"]["body"], "cy's secret brief")
        self.assertEqual(self.get("docs", "ana@x.com")[1]["work"]["id"], self.task)
        self.assertEqual(self.get("infra", "ana@x.com")[1]["work"]["id"], self.memo)

    def test_a_resting_session_hides_the_last_work_of_someone_else(self) -> None:
        self.b.s.update_task(self.task, state="closed", closed=3.0)
        self.b.s.add_report(self.task, "docs", "done", "cy's secret result", [])
        self.b.s.set_lane("docs", status="closed")
        code, d = self.get("docs", "bo@x.com")
        self.assertEqual((code, d["hidden"], d["work"], d["report"]), (200, True, None, None))
        self.assertNotIn("secret", str(d))
        self.assertEqual(self.get("docs", "cy@x.com")[1]["work"]["id"], self.task)

    def test_work_nobody_is_scoped_to_is_open_to_members(self) -> None:
        t = self.b.s.add_task("docs", "open plan", "open brief")
        self.b.s.update_task(self.task, state="closed", closed=3.0)
        self.b.s.claim_task(t)
        self.assertEqual(self.get("docs", "bo@x.com")[1]["work"]["body"], "open brief")


class PersonHubSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = with_members(make_home(LANES), "ana", "bo", "cy")
        self.s = Store(self.cfg.db)

    def gate(self, name: str, role: str = "member") -> Gate:
        return Gate.of(self.cfg, self.s, {"name": name, "role": role})

    def test_a_person_hub_session_belongs_to_its_owner(self) -> None:
        self.assertIn("hub-cy", self.cfg.person_hubs)
        self.assertIsNone(session_view(self.cfg, self.s, "hub-cy", self.gate("bo")))
        self.assertEqual(session_view(self.cfg, self.s, "hub-cy", self.gate("cy"))["key"], "hub-cy")
        self.assertEqual(session_view(self.cfg, self.s, "hub-cy", self.gate("ana", "owner"))["key"], "hub-cy")

    def test_the_default_hub_is_open_to_members(self) -> None:
        self.assertEqual(session_view(self.cfg, self.s, "hub", self.gate("bo"))["key"], "hub")

    def test_work_for_another_persons_hub_is_hidden_from_members(self) -> None:
        t = self.s.add_task("docs", "cy's plan", "secret brief", hub="hub-cy")
        self.s.claim_task(t)
        self.s.set_lane("docs", status="busy")
        self.assertIsNone(session_view(self.cfg, self.s, "docs", self.gate("bo")))
        self.assertEqual(session_view(self.cfg, self.s, "docs", self.gate("cy"))["work"]["id"], t)


if __name__ == "__main__":
    unittest.main()
