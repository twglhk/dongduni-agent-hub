import unittest

from agent_hub.engine import (BATCH_SECONDS, GOAL_IDLE_SECONDS, HUB_STUCK_SECONDS, NUDGE_AFTER, NUDGE_LIMIT,
                              RESPAWN_LIMIT, Engine)
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

    def test_a_hub_that_keeps_closing_backs_off_and_says_so(self) -> None:
        self.s.set_lane("hub", enabled=1)
        for _ in range(12):  # the hub's session dies right after every open
            self.e.tick()
            self.rt.st["hub"] = "closed"
            self.clock.tick(5)
        opens = [o for o in self.rt.opened if o[0] == "hub"]
        self.assertEqual(len(opens), 3)
        self.assertTrue(any(n[0].get("alert") and "hub closed 3 times" in n[0]["title"] for n in self.notes))
        self.clock.tick(300)
        self.e.tick()
        self.assertEqual(len([o for o in self.rt.opened if o[0] == "hub"]), 4)  # tries again, slowly

    def test_a_lane_held_by_a_menu_in_its_window_is_announced_once(self) -> None:
        cfg = make_home({"hub": {"runtime": "claude"}, "docs": {"runtime": "claude"}}, terminal="tmux")
        s = Store(cfg.db, clock=self.clock)
        rt = FakeRuntime(s)
        notes: list[list[dict]] = []
        e = Engine(cfg, s, rt, notes.append, clock=self.clock, log=lambda _: None)
        rt.st["docs"] = "asking"
        for _ in range(3):
            e.tick()
        self.assertEqual([n[0]["title"] for n in notes], ["docs waits for an answer in its tmux window"])
        rt.st["docs"] = "busy"
        e.tick()
        rt.st["docs"] = "asking"
        e.tick()
        self.assertEqual(len(notes), 2)  # a new menu is news again

    # ----- hub -----

    def test_hub_opens_only_when_enabled(self) -> None:
        self.e.tick()
        self.assertEqual(self.rt.opened, [])
        self.s.set_lane(HUB, enabled=1)
        self.e.tick()
        self.assertEqual(self.rt.opened[0][0], HUB)
        self.assertIn(str(self.cfg.instructions(HUB)), self.rt.opened[0][1])

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
        self.assertIn("dah memo reply", text)
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

    def test_busy_hub_with_waiting_news_is_raised_once(self) -> None:
        self.s.set_lane(HUB, enabled=1)
        self.e.tick()  # opens: busy, and stays busy (a hook keeps it working)
        self.s.push(HUB, "ana", "memo", "ship it")
        self.clock.tick(HUB_STUCK_SECONDS - 10)
        self.e.tick()
        self.assertEqual(self.s.items(state="open"), [])
        self.clock.tick(20)
        self.run_ticks(3)
        alerts = self.s.items(state="open", audience=HUMAN)
        self.assertEqual(len(alerts), 1)
        self.assertIn("idle", alerts[0]["title"])
        self.assertEqual(len(self.notes), 1)
        self.s.decide(alerts[0]["id"], "ok", "ana")  # seen: no new alert for the same waiting news
        self.run_ticks(3)
        self.assertEqual(self.s.items(state="open", audience=HUMAN), [])
        self.assertEqual(self.rt.sent, [])  # nothing was forced into the busy session

    def test_idle_hub_is_told_goals_once_per_change(self) -> None:
        self.s.set_lane(HUB, enabled=1)
        self.e.tick()
        self.rt.st[HUB] = "idle"
        self.run_ticks(3, step=GOAL_IDLE_SECONDS)
        self.assertEqual(self.rt.sent, [])  # no goals, nothing to say
        g1 = self.s.add_goal("Ship v2", "", "ana")
        g2 = self.s.add_goal("Fix login", "", "ana")
        self.s.move_goal(g2, 1)
        self.run_ticks(3, step=GOAL_IDLE_SECONDS)
        self.assertEqual(len(self.rt.sent), 1)
        text = self.rt.sent[0][2]
        self.assertLess(text.index(f"goal #{g2}"), text.index(f"goal #{g1}"))
        self.assertIn("no plan yet", text)
        self.rt.st[HUB] = "idle"
        self.run_ticks(5, step=GOAL_IDLE_SECONDS)
        self.assertEqual(len(self.rt.sent), 1)  # unchanged goals: left alone
        self.s.plan_goal(g2, ["repro", "fix"])
        self.s.check_steps(g2, [1])
        t = self.s.add_task("docs", "fix", "b", goal_id=g2)
        self.run_ticks(3, step=GOAL_IDLE_SECONDS)
        self.assertEqual(len(self.rt.sent), 1)  # a task is in flight
        self.s.update_task(t, state="closed")
        self.rt.st[HUB] = "idle"
        self.run_ticks(3, step=GOAL_IDLE_SECONDS)
        self.assertEqual(len(self.rt.sent), 2)
        self.assertIn("1/2 steps · next: fix", self.rt.sent[1][2])

    def test_follow_up_task_keeps_its_goal(self) -> None:
        g = self.s.add_goal("Docs site", "", "ana")
        t = self.s.add_task("docs", "A", "b", goal_id=g)
        self.e.tick()
        self.s.decide(self.item("docs", t), "ok", HUB, basis="in scope")
        self.e.tick()
        follow = [x for x in self.s.tasks() if x["id"] != t][0]
        self.assertEqual(follow["goal_id"], g)

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

    def test_usage_is_recorded_while_working_and_before_a_wrapped_lane_closes(self) -> None:
        t = self.s.add_task("docs", "A", "b")
        self.e.tick()
        sid = self.s.lane("docs")["session"]
        self.rt.use["docs"] = {"tokens_in": 1000, "tokens_out": 10, "cost": 0.01, "ctx_k": 1}
        self.e.tick()
        self.rt.use["docs"] = {"tokens_in": 5000, "tokens_out": 50, "cost": 0.05, "ctx_k": 5}
        self.clock.tick(5)
        self.e.tick()  # busy and sampled under a minute ago: not read again
        self.assertEqual(self.s.usage(self.s.day())["docs"]["tokens_in"], 1000)
        self.s.update_task(t, state="closed")
        self.s.set_lane("docs", closing=1)
        self.rt.st["docs"] = "idle"
        self.e.tick()  # the turn ended: read once more, then the wrapped lane closes
        self.assertIn("docs", self.rt.closed)
        self.assertEqual(self.s.usage(self.s.day())["docs"],
                         {"tokens_in": 5000, "tokens_out": 50, "cost": 0.05, "sessions": 1})
        self.assertEqual(self.s.session_ctx("docs", sid), 5)

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
        self.assertIn("dah item add", self.rt.replies[0][2][0][0])


if __name__ == "__main__":
    unittest.main()
