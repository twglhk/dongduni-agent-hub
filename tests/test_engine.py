import unittest

from agent_hub.config import base_lane
from agent_hub.engine import (BATCH_SECONDS, CHAIN_CONTEXT_K, HUB_STUCK_SECONDS, NUDGE_AFTER, NUDGE_LIMIT,
                              RESPAWN_LIMIT, SETTLE_SECONDS, Engine, conflict_of, paths_overlap, task_paths)
from agent_hub.memwatch import POLL_SECONDS, MemWatch, Proc, parse_ps
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

    def test_idle_hub_is_never_prompted_without_news(self) -> None:
        self.s.set_lane(HUB, enabled=1)
        self.e.tick()
        self.rt.st[HUB] = "idle"
        self.run_ticks(5, step=60)
        self.assertEqual(self.rt.sent, [])  # nothing queued, nothing to say: the hub is left alone

    def test_follow_up_task_is_queued_without_goal_state(self) -> None:
        t = self.s.add_task("docs", "A", "b")
        self.e.tick()
        self.s.decide(self.item("docs", t), "ok", HUB, basis="in scope")
        self.e.tick()
        follow = [x for x in self.s.tasks() if x["id"] != t][0]
        self.assertIsNone(follow["goal_id"])

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

    def test_a_cancelled_task_held_at_a_prompt_still_closes(self) -> None:
        t = self.s.add_task("docs", "A", "brief")
        self.e.tick()
        self.rt.st["docs"] = "asking"  # its window waits at a permission prompt
        self.s.update_task(t, state="cancelled")
        self.s.set_lane("docs", closing=1)
        self.e.tick()
        self.assertIn("docs", self.rt.closed)

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


# ----- P4: scheduler -----

LANES = {"hub": {"runtime": "opencode"}, "infra": {"runtime": "opencode"},
         "docs": {"runtime": "claude"}, "web": {"runtime": "claude"}}


class Base(unittest.TestCase):
    def build(self, **extra):
        self.cfg = make_home(LANES, **extra)
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda n: None, clock=self.clock, log=lambda _: None)

    def tick(self, n: int = 1, step: float = 5) -> None:
        for _ in range(n):
            self.e.tick()
            self.clock.tick(step)

    def started(self) -> list[str]:
        return [lane for lane, _ in self.rt.opened if lane not in ("hub", "infra")]


class PathsTest(unittest.TestCase):
    def test_paths_line_parsing_and_overlap(self) -> None:
        self.assertEqual(task_paths({"brief": "x\npaths: `src/a/`, docs\ny"}), ["src/a", "docs"])
        self.assertIsNone(task_paths({"brief": "no line"}))
        self.assertTrue(paths_overlap(["src"], ["src/a"]))
        self.assertFalse(paths_overlap(["src/a"], ["src/ab"]))

    def test_base_lane(self) -> None:
        self.assertEqual((base_lane("docs#2"), base_lane("docs")), ("docs", "docs"))


class ConflictTest(Base):
    def test_overlap_and_missing_paths_block_beside_a_live_task_of_the_lane(self) -> None:
        self.build(max_lanes=3, max_instances=2)
        a = self.s.add_task("docs", "A", "paths: src/a")
        self.s.claim_task(a)
        ok = {"id": 90, "lane": "docs", "brief": "paths: src/b"}
        self.assertIsNone(conflict_of(self.s, ok))
        self.assertIn("overlap", conflict_of(self.s, {"id": 91, "lane": "docs", "brief": "paths: src/a/x"}) or "")
        self.assertIn("no paths", conflict_of(self.s, {"id": 92, "lane": "docs", "brief": "no line"}) or "")
        self.assertIsNone(conflict_of(self.s, {"id": 93, "lane": "web", "brief": "no line"}))  # another lane

    def test_after_waits_for_the_named_task_unless_it_does_not_exist(self) -> None:
        self.build()
        a = self.s.add_task("web", "A", "x")
        self.assertIn("after", conflict_of(self.s, {"id": 9, "lane": "docs", "brief": f"after: #{a}"}) or "")
        self.assertIsNone(conflict_of(self.s, {"id": 9, "lane": "docs", "brief": "after: 4242"}))
        self.s.update_task(a, state="closed")
        self.assertIsNone(conflict_of(self.s, {"id": 9, "lane": "docs", "brief": f"after: {a}"}))

    def test_blocked_task_stays_queued_and_a_later_one_starts_on_a_parallel_session(self) -> None:
        self.build(max_lanes=3, max_instances=2)
        a = self.s.add_task("docs", "A", "paths: src/a")
        b = self.s.add_task("docs", "B", "paths: src/a/deep")  # overlaps A
        c = self.s.add_task("docs", "C", "paths: src/c")       # does not
        self.tick()
        self.assertEqual(self.started(), ["docs"])
        self.assertEqual(self.s.task(a)["state"], "running")
        self.tick()
        self.assertEqual(self.started(), ["docs", "docs#2"])
        self.assertEqual((self.s.task(b)["state"], self.s.task(b)["lane"]), ("queued", "docs"))
        self.assertEqual((self.s.task(c)["state"], self.s.task(c)["lane"]), ("running", "docs#2"))
        self.tick(3)
        self.assertEqual(len(self.started()), 2)  # B keeps waiting while A runs
        self.s.update_task(a, state="closed")
        self.s.set_lane("docs", closing=1)
        self.rt.st["docs"] = "idle"
        self.tick(2)
        self.assertEqual(self.s.task(b)["state"], "running")  # A finished, the overlap is gone

    def test_without_max_instances_nothing_runs_beside_a_lane(self) -> None:
        self.build(max_lanes=3)
        self.s.add_task("docs", "A", "paths: a")
        self.s.add_task("docs", "B", "paths: b")
        self.tick(3)
        self.assertEqual(self.started(), ["docs"])
        self.assertEqual(self.cfg.instances, {})

    def test_instance_config_copies_the_lane(self) -> None:
        self.build(max_instances=3)
        self.assertEqual(sorted(self.cfg.instances), ["docs#2", "docs#3", "web#2", "web#3"])
        ln = self.cfg.instances["docs#2"]
        self.assertEqual((ln.base, ln.runtime, ln.instructions), ("docs", "claude", self.cfg.lanes["docs"].instructions))

    def test_head_of_line_skip_on_a_free_lane(self) -> None:
        self.build(max_lanes=3)
        dep = self.s.add_task("web", "dep", "x")
        first = self.s.add_task("docs", "first", f"after: {dep}")
        second = self.s.add_task("docs", "second", "x")
        self.tick()
        self.assertEqual(self.s.task(first)["state"], "queued")  # still waiting for dep
        self.assertEqual(self.s.task(second)["state"], "running")  # ... and does not hold the lane up

    def test_lowering_max_instances_sends_queued_work_back_to_the_base_lane(self) -> None:
        self.build(max_lanes=3, max_instances=2)
        t = self.s.add_task("docs", "A", "x")
        self.s.update_task(t, lane="docs#3")
        self.tick()
        self.assertNotEqual(self.s.task(t)["lane"], "docs#3")


class SlotTest(Base):
    def test_light_tasks_have_their_own_slots(self) -> None:
        self.build(max_lanes=1, light_lanes=1, heavy_paths=["game"])
        h1 = self.s.add_task("docs", "h1", "paths: game/a")
        h2 = self.s.add_task("web", "h2", "paths: game/b")
        self.tick()
        self.assertEqual([self.s.task(h1)["state"], self.s.task(h2)["state"]], ["running", "queued"])
        light = self.s.add_task("web", "l", "paths: notes")
        self.s.update_task(h2, state="cancelled")
        self.tick()
        self.assertEqual(self.s.task(light)["state"], "running")  # the light slot, while the heavy one is full

    def test_without_light_lanes_one_pool(self) -> None:
        self.build(max_lanes=1, heavy_paths=["game"])
        self.s.add_task("docs", "a", "paths: notes")
        b = self.s.add_task("web", "b", "paths: more")
        self.tick()
        self.assertEqual(self.s.task(b)["state"], "queued")


class ChainTest(Base):
    def wrap(self, lane: str, tid: int) -> None:
        self.s.update_task(tid, state="closed", closed=self.clock())
        self.s.set_lane(lane, closing=1)
        self.rt.st[lane] = "idle"

    def test_linked_task_goes_into_the_same_session(self) -> None:
        self.build(max_lanes=2)
        a = self.s.add_task("docs", "A", "x")
        b = self.s.add_task("docs", "B", f"after: #{a}")
        self.tick()
        sid = self.s.task(a)["session"]
        self.wrap("docs", a)
        self.tick()
        self.assertEqual(self.rt.closed, [])
        self.assertEqual(self.rt.sent[-1][:2], ("docs", sid))
        self.assertIn(f"task #{b}", self.rt.sent[-1][2])
        t = self.s.task(b)
        self.assertEqual((t["state"], t["session"]), ("running", sid))

    def test_a_long_context_or_an_unlinked_task_opens_fresh(self) -> None:
        self.build(max_lanes=2)
        a = self.s.add_task("docs", "A", "x")
        b = self.s.add_task("docs", "B", f"after: {a}")
        self.tick()
        self.rt.ctx["docs"] = CHAIN_CONTEXT_K
        self.wrap("docs", a)
        self.tick(2)
        self.assertIn("docs", self.rt.closed)
        self.assertEqual(self.s.task(b)["state"], "running")
        self.assertEqual(len([o for o in self.rt.opened if o[0] == "docs"]), 2)


class MemoryGuardTest(Base):
    def test_fat_session_is_asked_for_a_handoff_once_then_renewed(self) -> None:
        self.build(max_lanes=2, memory_limit_mb=1000)
        t = self.s.add_task("docs", "A", "x")
        self.tick()
        self.rt.st["docs"] = "idle"
        self.e.heavy = {"docs": 2048}
        self.tick(2, step=SETTLE_SECONDS + 1)
        asks = [m for m in self.s.pending("docs") if m["kind"] == "rotate"] + [x for x in self.rt.sent if "2048 MB" in x[2]]
        self.assertEqual(len(asks), 1)
        self.rt.st["docs"] = "idle"
        self.tick(2, step=SETTLE_SECONDS + 1)
        self.assertEqual(len([x for x in self.rt.sent if "2048 MB" in x[2]]), 1)  # asked once
        self.s.update_task(t, handoff="[now] x", rotate_ready=self.clock())
        self.rt.st["docs"] = "idle"
        self.tick()
        self.assertIn("docs", self.rt.closed)
        self.assertEqual(len([o for o in self.rt.opened if o[0] == "docs"]), 2)
        self.assertNotIn("docs", self.e.heavy)

    def test_a_fat_idle_session_without_a_task_is_closed(self) -> None:
        self.build(max_lanes=2)
        self.rt.st["docs"] = "idle"
        self.e.heavy = {"docs": 5000}
        self.tick(2, step=SETTLE_SECONDS + 1)
        self.assertIn("docs", self.rt.closed)

    def test_a_fat_session_is_not_chained(self) -> None:
        self.build(max_lanes=2)
        a = self.s.add_task("docs", "A", "x")
        b = self.s.add_task("docs", "B", f"after: {a}")
        self.tick()
        self.e.heavy = {"docs": 5000}
        self.s.update_task(a, state="closed", closed=self.clock())
        self.s.set_lane("docs", closing=1)
        self.rt.st["docs"] = "idle"
        self.tick()
        self.assertIn("docs", self.rt.closed)
        self.assertEqual(self.s.task(b)["state"], "running")


class MemWatchTest(unittest.TestCase):
    def test_parse_ps(self) -> None:
        procs = parse_ps("  12  2048 /bin/claude --session-id s1 hi\nbad line\n  13  10 x\n")
        self.assertEqual([(p.pid, p.rss_mb) for p in procs], [(12, 2), (13, 0)])

    def test_heavy_lanes_by_marker_polled_once_a_minute(self) -> None:
        clock = Clock(0.0)
        table = [Proc(1, 5000, "claude --session-id s-docs"), Proc(2, 100, "claude --session-id s-web"),
                 Proc(3, 4000, "opencode serve --port 4301")]
        calls = []

        def ps() -> list[Proc]:
            calls.append(1)
            return table

        logs: list[str] = []
        w = MemWatch(1000, lambda: {"docs": ["s-docs"], "web": ["s-web"], "hub": ["", "--port 4301"]}, logs.append, ps, clock)
        w.poll()
        self.assertEqual(w.heavy, {"docs": 5000, "hub": 4000})
        self.assertEqual(len(logs), 2)
        w.poll()
        self.assertEqual(len(calls), 1)  # inside the minute: no second read
        table[0] = Proc(1, 10, "claude --session-id s-docs")
        clock.t += POLL_SECONDS
        w.poll()
        self.assertEqual(w.heavy, {"hub": 4000})
        self.assertEqual(len(logs), 2)  # hub was already announced

    def test_limit_zero_is_off(self) -> None:
        w = MemWatch(0, lambda: {"docs": ["s"]}, ps=lambda: [Proc(1, 9999, "s")])
        w.poll()
        self.assertEqual(w.heavy, {})


if __name__ == "__main__":
    unittest.main()
