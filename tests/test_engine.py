import unittest

from agent_hub.engine import BATCH_SECONDS, NUDGE_AFTER, NUDGE_LIMIT, RESPAWN_LIMIT, Engine
from agent_hub.store import HUB, HUMAN, INFRA, Store
from tests.helpers import Clock, FakeRuntime, make_home


class EngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home({"hub": {"runtime": "opencode"}, "infra": {"runtime": "opencode"},
                              "docs": {"runtime": "claude"}, "web": {"runtime": "codex"}}, max_lanes=1)
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.notes: list[list[dict]] = []
        self.e = Engine(self.cfg, self.s, self.rt, self.notes.append, clock=self.clock, log=lambda _: None)

    def run_ticks(self, n: int = 1, step: float = 5) -> None:
        for _ in range(n):
            self.e.tick()
            self.clock.tick(step)

    # ----- hub -----

    def test_hub_opens_only_when_enabled(self) -> None:
        self.e.tick()
        self.assertEqual(self.rt.opened, [])
        self.s.set_lane(HUB, enabled=1)
        self.e.tick()
        self.assertEqual(self.rt.opened[0][0], HUB)
        self.assertIn(".tower/lanes/hub.md", self.rt.opened[0][1])

    def test_hub_gets_batched_news_when_idle(self) -> None:
        self.s.set_lane(HUB, enabled=1)
        self.e.tick()
        self.rt.st[HUB] = "idle"
        self.s.push(HUB, "ana", "memo", "ship the docs")
        self.s.push(HUB, "docs", "done", "Done: docs")
        self.run_ticks(1)
        self.assertEqual(self.rt.sent, [])  # still inside the batch window
        self.clock.tick(BATCH_SECONDS)
        self.run_ticks(2)
        self.assertEqual(len(self.rt.sent), 1)
        text = self.rt.sent[0][2]
        self.assertIn("Request #", text)
        self.assertIn("tower memo reply", text)
        self.assertIn("Done: docs", text)
        self.assertEqual(self.s.pending(HUB), [])

    def test_hub_rotates_when_context_large(self) -> None:
        self.s.set_lane(HUB, enabled=1)
        self.e.tick()
        self.rt.st[HUB] = "idle"
        self.rt.ctx[HUB] = 999
        self.run_ticks(10, step=5)
        self.assertIn(HUB, self.rt.closed)
        self.assertEqual(sum(1 for lane, _ in self.rt.opened if lane == HUB), 2)

    # ----- team lanes -----

    def test_tasks_respect_max_lanes_and_fresh_session(self) -> None:
        a = self.s.add_task("docs", "A", "brief A")
        b = self.s.add_task("web", "B", "brief B")
        self.e.tick()
        self.assertEqual([lane for lane, _ in self.rt.opened], ["docs"])
        self.assertIn("task #%d" % a, self.rt.opened[0][1])
        self.assertEqual(self.s.task(a)["state"], "running")
        self.assertEqual(self.s.task(b)["state"], "queued")
        # docs wraps -> its slot frees -> web starts
        self.s.update_task(a, state="closed")
        self.s.set_lane("docs", closing=1)
        self.rt.st["docs"] = "idle"
        self.e.tick()
        self.assertIn("docs", self.rt.closed)
        self.assertEqual(self.rt.opened[-1][0], "web")

    def test_tell_waits_for_idle_and_settle(self) -> None:
        t = self.s.add_task("docs", "A", "b")
        self.e.tick()
        self.s.push("docs", HUB, "tell", "fix the typo", t)
        self.e.tick()
        self.assertEqual(self.rt.sent, [])  # busy
        self.rt.st["docs"] = "idle"
        self.run_ticks(3)
        self.assertEqual(self.rt.sent[0][2], "[tower]\nfix the typo")

    def test_silent_lane_is_nudged_then_reported_to_hub(self) -> None:
        self.s.add_task("docs", "A", "b")
        self.e.tick()
        self.rt.st["docs"] = "idle"
        self.e.tick()  # first idle sighting starts the idle clock
        for _ in range(NUDGE_LIMIT + 1):
            self.clock.tick(NUDGE_AFTER + 1)
            self.e.tick()
            self.rt.st["docs"] = "idle"
            self.clock.tick(5)
            self.e.tick()  # delivers the nudge
            self.rt.st["docs"] = "idle"
        kinds = [m["kind"] for m in self.s.pending(HUB)]
        self.assertIn("stalled", kinds)

    def test_closed_lane_is_revived_then_given_up(self) -> None:
        t = self.s.add_task("docs", "A", "b")
        self.e.tick()
        for _ in range(RESPAWN_LIMIT + 1):
            self.rt.st["docs"] = "closed"
            self.e.tick()
        self.assertEqual(sum(1 for lane, _ in self.rt.opened if lane == "docs"), 1 + RESPAWN_LIMIT)
        self.assertIn("continued", self.rt.opened[-1][1])
        self.assertTrue(any(m["kind"] == "stalled" for m in self.s.pending(HUB)))
        self.assertEqual(self.s.task(t)["respawns"], RESPAWN_LIMIT + 1)

    # ----- decisions -----

    def item(self, lane: str, task_id: int | None, audience: str = HUB) -> int:
        return self.s.add_item(lane, task_id, "review", "Check", "## a\nb", [], [], audience=audience)

    def test_team_decision_becomes_one_followup_task(self) -> None:
        t = self.s.add_task("docs", "A", "brief A")
        self.e.tick()
        iid = self.item("docs", t)
        self.s.escalate(iid, "taste")
        self.s.decide(iid, "ok", "ana")
        self.e.tick()
        self.e.tick()
        follow = [x for x in self.s.tasks(("queued",)) if x["lane"] == "docs"]
        self.assertEqual(len(follow), 1)
        self.assertIn(f"#{iid}", follow[0]["brief"])
        self.assertIn("brief A", follow[0]["brief"])
        self.assertTrue(any("follow-up task" in m["text"] for m in self.s.pending(HUB)))

    def test_hold_goes_to_hub_only(self) -> None:
        t = self.s.add_task("docs", "A", "b")
        self.e.tick()
        iid = self.item("docs", t)
        self.s.decide(iid, "hold", HUB, basis="later")
        self.e.tick()
        self.assertEqual([x for x in self.s.tasks(("queued",)) if x["lane"] == "docs"], [])
        self.assertTrue(any(m["kind"] == "decision" for m in self.s.pending(HUB)))

    def test_infra_decision_goes_to_infra_inbox(self) -> None:
        iid = self.item(INFRA, None, HUMAN)
        self.s.decide(iid, "ok", "ana")
        self.e.tick()
        self.assertEqual(self.rt.opened[-1][0], INFRA)
        self.assertIn(f"Decision #{iid}", self.rt.opened[-1][1])

    def test_humans_notified_once(self) -> None:
        self.item(INFRA, None, HUMAN)
        self.e.tick()
        self.e.tick()
        self.assertEqual(len(self.notes), 1)

    # ----- infra inbox -----

    def test_infra_one_memo_per_session(self) -> None:
        self.s.push(INFRA, "ana", "memo", "first")
        self.s.push(INFRA, "ana", "memo", "second")
        self.e.tick()
        self.assertEqual(len(self.rt.opened), 1)
        self.assertIn("first", self.rt.opened[0][1])
        self.rt.st[INFRA] = "idle"
        self.e.tick()
        self.assertEqual(len(self.rt.opened), 1)  # idle but not wrapped: no second memo
        self.s.set_lane(INFRA, closing=1)
        self.e.tick()  # closes
        self.e.tick()  # opens for the next memo
        self.assertIn("second", self.rt.opened[-1][1])

    def test_infra_gives_up_after_repeated_closes(self) -> None:
        self.s.push(INFRA, "ana", "memo", "boom")
        for _ in range(RESPAWN_LIMIT + 2):
            self.rt.st[INFRA] = "closed"
            self.e.tick()
        self.assertEqual(len(self.rt.opened), RESPAWN_LIMIT + 1)
        self.assertIsNone(self.s.lane(INFRA)["active_message"])
        self.assertTrue(any(n[0].get("alert") for n in self.notes))

    # ----- live asks -----

    def test_hub_ask_round_trips_through_the_board(self) -> None:
        self.rt.st[HUB] = "idle"
        self.rt.qs[HUB] = [{"id": "q1", "questions": [
            {"question": "Which port?", "header": "Port", "options": [{"label": "80"}, {"label": "8080"}]}]}]
        self.e.tick()
        it = [x for x in self.s.items(state="open") if x["question"]][0]
        self.assertEqual(it["audience"], HUMAN)
        self.s.decide(it["id"], "ok", "ana", "8080", "fine")
        self.e.tick()
        self.assertEqual(self.rt.replies, [(HUB, "q1", [["8080", "note: fine"]])])

    def test_vanished_ask_is_closed_on_the_board(self) -> None:
        self.rt.st[HUB] = "idle"
        self.rt.qs[HUB] = [{"id": "q1", "questions": [{"question": "?", "options": []}]}]
        self.e.tick()
        self.rt.qs[HUB] = []
        self.e.tick()
        self.assertEqual(self.s.items(state="open"), [])

    def test_team_ask_is_bounced(self) -> None:
        self.s.add_task("docs", "A", "b")
        self.e.tick()
        self.rt.qs["docs"] = [{"id": "q9", "questions": [{"question": "?"}]}]
        self.e.tick()
        self.assertEqual(self.rt.replies[0][1], "q9")
        self.assertIn("tower item add", self.rt.replies[0][2][0][0])


if __name__ == "__main__":
    unittest.main()
