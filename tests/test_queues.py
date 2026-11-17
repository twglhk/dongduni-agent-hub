import unittest

from agent_hub.engine import ALONE
from agent_hub.queues import queues_view
from agent_hub.store import HUB, INFRA, Store
from agent_hub.web import state
from tests.helpers import make_home
from tests.test_web import Board

LANES = {"hub": {"runtime": "claude"}, "infra": {"runtime": "claude", "max_instances": 2},
         "docs": {"runtime": "codex", "max_instances": 2}, "art": {"runtime": "claude"}}
H = "X-Forwarded-Email"


class QueuesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home(LANES)
        self.s = Store(self.cfg.db)

    def live(self):
        return self.s.tasks(("queued", "running", "wrapping"), limit=200)

    def test_team_lane_now_then_waiting_in_start_order(self) -> None:
        a = self.s.add_task("docs", "first", "b")
        b = self.s.add_task("docs#2", "second", "b")
        c = self.s.add_task("docs", "third", "b")
        d = self.s.add_task("docs", "fourth", "b")
        self.s.update_task(a, state="running")
        self.s.update_task(b, state="wrapping")
        q = queues_view(self.cfg, self.s, self.live())
        self.assertEqual([(x["id"], x["session"]) for x in q["docs"]["now"]], [(a, None), (b, 2)])
        self.assertEqual([x["id"] for x in q["docs"]["wait"]], [c, d])
        self.assertEqual(q["docs"]["waiting"], 2)
        self.assertEqual(q["art"], {"now": [], "wait": [], "waiting": 0, "by_person": []})
        self.assertNotIn(HUB, q)

    def test_a_waiting_task_carries_the_reason_the_tower_wrote(self) -> None:
        a = self.s.add_task("art", "one", "b")
        q = queues_view(self.cfg, self.s, self.live(), waits={str(a): "lane art is busy with another task"})
        self.assertEqual(q["art"]["wait"][0]["why"], "lane art is busy with another task")

    def test_waiting_tasks_follow_the_fair_order_when_it_is_on(self) -> None:
        cfg = make_home(LANES, fair_turns=True)
        a1 = self.s.add_task("art", "a1", "b", requester="ana")
        a2 = self.s.add_task("art", "a2", "b", requester="ana")
        b1 = self.s.add_task("art", "b1", "b", requester="bo")
        q = queues_view(cfg, self.s, self.live())["art"]
        self.assertEqual([x["id"] for x in q["wait"]], [a1, b1, a2])
        self.assertEqual(q["by_person"], [{"who": "ana", "n": 2}, {"who": "bo", "n": 1}])

    def test_infra_held_memos_then_queue_in_bump_order_with_why(self) -> None:
        m1 = self.s.push(INFRA, HUB, "memo", "lead\nfirst memo", paths="src/")
        m2 = self.s.push(INFRA, HUB, "memo", "overlaps", paths="src/web.py")
        m3 = self.s.push(INFRA, HUB, "memo", "restart", paths=ALONE)
        self.s.claim_inbox("infra", INFRA)
        self.s.bump_message(m3, INFRA)
        q = queues_view(self.cfg, self.s, [])["infra"]
        self.assertEqual([(x["id"], x["session"], x["title"]) for x in q["now"]], [(m1, 1, "lead")])
        self.assertEqual([x["id"] for x in q["wait"]], [m3, m2])
        self.assertEqual(q["waiting"], 2)
        self.assertTrue(all(x["why"] for x in q["wait"]))

    def test_one_infra_session_gives_no_reasons(self) -> None:
        cfg = make_home()
        s = Store(cfg.db)
        s.push(INFRA, HUB, "memo", "a")
        s.push(INFRA, HUB, "memo", "b")
        s.claim_inbox("infra", INFRA)
        q = queues_view(cfg, s, [])["infra"]
        self.assertEqual([x["why"] for x in q["wait"]], [None])

    def test_state_carries_queues(self) -> None:
        self.s.add_task("art", "one", "b")
        self.assertEqual(state(self.cfg, self.s, None)["queues"]["art"]["waiting"], 1)


class QueueVisibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Board(auth={"mode": "header", "header": H}, requester=True, lanes=LANES)
        s = self.b.s
        s.add_member("ana", "owner", "ana@x.com")
        s.add_member("bo", "member", "bo@x.com")
        s.add_member("cy", "member", "cy@x.com")
        self.mine = s.add_task("art", "bo's secret plan", "b", requester="bo")
        self.theirs = s.add_task("art", "cy's secret plan", "b", requester="cy")
        self.open = s.add_task("art", "nobody's plan", "b")

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def wait(self, who: str) -> list[dict]:
        _, st, _ = self.b.req("GET", "/api/state", headers={H: who})
        return st["queues"]["art"]["wait"]

    def test_a_member_sees_titles_of_their_own_and_unscoped_work_only(self) -> None:
        wait = {x["id"]: x for x in self.wait("bo@x.com")}
        self.assertEqual(wait[self.mine]["title"], "bo's secret plan")
        self.assertEqual(wait[self.open]["title"], "nobody's plan")
        self.assertEqual((wait[self.theirs]["title"], wait[self.theirs]["who"], wait[self.theirs]["hidden"]), ("", None, True))

    def test_an_owner_sees_everything(self) -> None:
        wait = {x["id"]: x for x in self.wait("ana@x.com")}
        self.assertEqual(wait[self.theirs]["title"], "cy's secret plan")
        self.assertEqual(wait[self.theirs]["who"], "cy")

    def test_per_person_counts_leave_out_other_members(self) -> None:
        _, st, _ = self.b.req("GET", "/api/state", headers={H: "bo@x.com"})
        self.assertEqual(st["queues"]["art"]["by_person"], [{"who": None, "n": 1}, {"who": "bo", "n": 1}])
        self.assertNotIn("cy", str(st["queues"]))


if __name__ == "__main__":
    unittest.main()
