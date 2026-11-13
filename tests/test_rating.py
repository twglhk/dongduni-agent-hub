"""Rating math (bars, refactor rule, blame, human rounds, tokens), the store additions and `dah ratings` on a fixture engine."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

from agent_hub import engines, rating
from agent_hub.store import Store, StoreError, valid_key
from tests.helpers import Clock
from tests.test_process import ROOT, make_engine_home


class BarsTest(unittest.TestCase):
    def test_new_below_five(self) -> None:
        self.assertEqual(rating.tier(4, 4)[0], "new")
        self.assertEqual(rating.tier(0, 0)[0], "new")
        self.assertEqual(rating.tier(5, 5)[0], "best")

    def test_best_at_85_percent(self) -> None:
        self.assertEqual(rating.pct_of(20, 17), 85)
        self.assertEqual(rating.tier(20, 17)[0], "best")
        self.assertEqual(rating.tier(20, 16)[0], "good")  # 80 %
        self.assertEqual(rating.pct_of(7, 6), 85)  # rounded down: 85.7
        self.assertEqual(rating.pct_of(13, 11), 84)  # 84.6 -> 84, so good, not best
        self.assertEqual(rating.tier(13, 11)[0], "good")

    def test_good_and_fix_edges(self) -> None:
        self.assertEqual(rating.tier(20, 13)[0], "good")  # 65 %
        self.assertEqual(rating.tier(20, 12)[0], "fix")  # 60 %

    def test_rework_needs_stuck_n10_and_under_50(self) -> None:
        self.assertEqual(rating.tier(10, 4, stuck=True)[0], "rework")
        self.assertEqual(rating.tier(10, 4, stuck=False)[0], "fix")
        self.assertEqual(rating.tier(9, 3, stuck=True)[0], "fix")  # n < 10
        self.assertEqual(rating.tier(10, 5, stuck=True)[0], "fix")  # 50 % is not under 50
        self.assertEqual(rating.tier(10, 7, stuck=True)[0], "good")

    def test_candidate_chip(self) -> None:
        human = {"resolved": 10, "adopted": 9}
        self.assertEqual(rating.tier(10, 9, human), ("best", True))
        self.assertEqual(rating.tier(10, 9, {"resolved": 10, "adopted": 8}), ("best", False))
        self.assertEqual(rating.tier(10, 9, {"resolved": 9, "adopted": 9}), ("best", False))
        self.assertEqual(rating.tier(9, 9, human), ("best", False))  # n < candidate_min_n


class RefactorRuleTest(unittest.TestCase):
    A = [(1.0, True), (2.0, False), (3.0, False), (6.0, False), (7.0, False), (11.0, False)]

    def test_needs_two_fixes_and_three_nonempty_parts(self) -> None:
        self.assertFalse(rating.stuck_after_fixes(self.A, []))
        self.assertFalse(rating.stuck_after_fixes(self.A, [5.0]))
        self.assertFalse(rating.stuck_after_fixes(self.A, [10.0, 20.0]))  # nothing after the second fix

    def test_flat_or_falling_score_is_stuck(self) -> None:
        # before 1/3 · between 0/2 · after 0/1
        self.assertTrue(rating.stuck_after_fixes(self.A, [5.0, 10.0]))
        flat = [(1.0, False), (6.0, False), (11.0, False)]
        self.assertTrue(rating.stuck_after_fixes(flat, [5.0, 10.0]))

    def test_a_rising_score_is_not_stuck(self) -> None:
        rising = [(1.0, False), (2.0, False), (6.0, True), (7.0, False), (11.0, True)]
        self.assertFalse(rating.stuck_after_fixes(rising, [5.0, 10.0]))  # 0 -> .5 -> 1
        dip = [(1.0, False), (6.0, True), (7.0, True), (11.0, False)]
        self.assertFalse(rating.stuck_after_fixes(dip, [5.0, 10.0]))  # 0 -> 1 -> 0: between rose

    def test_only_the_last_two_fixes_count(self) -> None:
        att = [(0.5, True), (2.0, False), (6.0, False), (11.0, False)]
        self.assertTrue(rating.stuck_after_fixes(att, [1.0, 5.0, 10.0]))

    def test_rework_state_through_compute(self) -> None:
        tasks = [{"id": i, "engine": "e", "line_row": "1", "line_stage": "1", "line_mode": "make", "state": "closed",
                  "started": float(i), "deliverable": None, "model": None, "effort": None, "tok_at": None} for i in range(1, 13)]
        raw = {"tasks": tasks, "fixes": {i: 1 for i in range(2, 13)}, "events": {}, "rejects": {}, "blames": {}}  # 1 of 12 passes
        g = rating.compute(raw, set(), fixes_at={("e", "1", "1"): [4.5, 8.5]})[("e", "1", "1")]
        self.assertEqual((g["n"], g["pass"], g["pct"], g["state"], g["fixes"]), (12, 1, 8, "rework", 2))
        g = rating.compute(raw, set(), fixes_at={("e", "1", "1"): [8.5]})[("e", "1", "1")]
        self.assertEqual(g["state"], "fix")  # one fix is not enough


class StoreAdditionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home, self.cfg = make_engine_home()
        self.s = Store(self.cfg.db, clock=Clock())

    def test_keys(self) -> None:
        self.assertEqual(valid_key(" ui:shop "), "ui:shop")
        for bad in ("T12", "#4", "12", "has space", ""):
            with self.assertRaises(StoreError, msg=bad):
                valid_key(bad)

    def test_outcome_needs_a_known_key_and_the_last_one_counts(self) -> None:
        with self.assertRaises(StoreError):
            self.s.add_outcome("k:1", "adopted", "hub")
        t = self.s.add_task("story", "A", "x", "anim", "k:1")
        self.assertEqual(self.s.task(t)["deliverable"], "k:1")
        with self.assertRaises(StoreError):
            self.s.add_outcome("k:1", "maybe", "hub")
        self.s.add_outcome("k:1", "redirected", "hub", "not like this")
        self.s.add_outcome("k:1", "adopted", "hub")
        self.assertEqual([e["outcome"] for e in self.s.outcome_events("k:1")], ["redirected", "adopted"])

    def test_blame_confirm_wins(self) -> None:
        t = self.s.add_task("story", "A", "x")
        self.s.add_blame(t, "2", "missed", "story")
        self.assertEqual(self.s.blames()[t]["effective"], "2")
        self.s.confirm_blame(t, "hub", "1")
        b = self.s.blames()[t]
        self.assertEqual((b["effective"], b["stage"], b["is_confirmed"]), ("1", "2", True))
        with self.assertRaises(StoreError):
            self.s.confirm_blame(t + 99, "hub")


def mk(s: Store, clock: Clock, state: str = "closed", stage: str = "1", mode: str = "make", key: str | None = None,
       tokens: tuple[int, int] | None = None, model: str = "sonnet", fixes: int = 0, row: str = "1") -> int:
    t = s.add_task("story", f"t{stage}", "x", "anim", key)
    clock.tick(10)
    s.update_task(t, line_row=row, line_stage=stage, line_mode=mode, state=state, started=clock(), closed=clock(), model=model, effort="medium")
    if tokens:
        s.update_task(t, tok_new=tokens[0], tok_out=tokens[1], tok_at=clock())
    for _ in range(fixes):
        s.push("story", "hub", "tell", "From the hub: fix: the frames are off", t)
    return t


class ComputeStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home, self.cfg = make_engine_home()
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)

    def rate(self, gated: set[tuple[str, str, str]] | None = None):
        return rating.compute(rating.collect(self.s), gated or set())

    def test_fix_tell_cancelled_and_sent_back_break_the_first_pass(self) -> None:
        mk(self.s, self.clock)
        mk(self.s, self.clock, fixes=1)  # the hub sent it back
        mk(self.s, self.clock, state="cancelled")  # cancelled while running
        mk(self.s, self.clock, state="queued")  # never ran: not counted
        g = self.rate()[("anim", "1", "1")]
        self.assertEqual((g["n"], g["pass"]), (3, 1))
        self.assertEqual(g["blame_missing"], 2)

    def test_redo_and_fix_task_mark_earlier_attempts(self) -> None:
        mk(self.s, self.clock, key="d:1")
        mk(self.s, self.clock, key="d:1", mode="fix")  # a later fix task for the same key, row and stage
        g = self.rate()[("anim", "1", "1")]
        self.assertEqual((g["n"], g["pass"]), (2, 1))  # the first was sent back, the fix task itself passes

    def test_human_stage_needs_first_round_adoption(self) -> None:
        gated = {("anim", "1", "3")}
        mk(self.s, self.clock, stage="3", key="a:1")
        mk(self.s, self.clock, stage="3", key="a:2")
        mk(self.s, self.clock, stage="3", key="a:3")
        mk(self.s, self.clock, stage="3", key="a:4")  # no outcome yet: pending, left out of n
        self.s.add_outcome("a:1", "adopted", "hub")
        self.s.add_outcome("a:2", "redirected", "hub", "again")
        self.s.add_outcome("a:2", "adopted", "hub")  # adopted later: still not a first-round pass
        self.s.add_outcome("a:3", "dropped", "hub")
        g = self.rate(gated)[("anim", "1", "3")]
        self.assertEqual((g["n"], g["pass"], g["pending"]), (3, 1, 1))
        self.assertEqual((g["human"]["resolved"], g["human"]["adopted"]), (3, 2))

    def test_blame_moves_the_failure_to_the_stage_that_should_have_caught_it(self) -> None:
        t = mk(self.s, self.clock, stage="2-3", fixes=1)
        self.s.add_blame(t, "2", "", "story")
        r = self.rate()
        self.assertEqual((r[("anim", "1", "2")]["n"], r[("anim", "1", "2")]["pass"]), (1, 0))
        self.assertEqual((r[("anim", "1", "3")]["n"], r[("anim", "1", "3")]["pass"]), (1, 1))
        self.assertEqual(r[("anim", "1", "2")]["blamed"], 1)
        self.s.confirm_blame(t, "hub", "1")  # the hub moves it outside the task's own stages
        r = self.rate()
        self.assertEqual((r[("anim", "1", "2")]["n"], r[("anim", "1", "2")]["pass"]), (1, 1))
        self.assertEqual((r[("anim", "1", "1")]["n"], r[("anim", "1", "1")]["pass"]), (1, 0))

    def test_tokens_per_attempt_and_per_adopted(self) -> None:
        for key, toks in (("t:1", (900, 100)), ("t:2", (2000, 0)), ("t:3", (3000, 1000))):
            mk(self.s, self.clock, stage="1", key=key, tokens=toks)
        mk(self.s, self.clock, stage="1")  # not measured: left out of the cost
        mk(self.s, self.clock, stage="2-3", tokens=(1000, 1000))  # 2000 shared by two stages
        self.s.add_outcome("t:1", "adopted", "hub")
        self.s.add_outcome("t:3", "adopted", "hub")
        r = self.rate()
        g = r[("anim", "1", "1")]
        self.assertEqual((g["n"], g["tok_n"], g["tok_median"]), (4, 3, 2000))
        self.assertEqual((g["adopted_n"], g["tok_per_adopted"]), (2, (1000 + 2000 + 4000) // 2))
        self.assertEqual(r[("anim", "1", "2")]["tok_median"], 1000)

    def test_prepare_is_its_own_stage_and_models_split(self) -> None:
        mk(self.s, self.clock, stage="", mode="prepare")
        mk(self.s, self.clock, model="opus")
        r = self.rate()
        self.assertEqual(r[("anim", "1", "prepare")]["n"], 1)
        self.assertEqual(r[("anim", "1", "1")]["models"], {"opus/medium": {"n": 1, "pass": 1}})


class RatingsCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home, self.cfg = make_engine_home()
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)

    def dah(self, *args: str, ok: bool = True, lane: str = "") -> str:
        env = {**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(ROOT)}
        env.pop("TOWER_LANE", None)
        if lane:
            env["TOWER_LANE"] = lane
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.home, env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode == 0, ok, f"dah {' '.join(args)}: {p.stdout}{p.stderr}")
        return p.stdout + p.stderr

    def fixture(self) -> None:
        for i in range(10):  # stage 1: 8 of 10 pass
            mk(self.s, self.clock, stage="1", fixes=int(i < 2), tokens=(4000, 1000))
        for i in range(5):  # stage 3 is passed by a human: 3 of 5 adopted first round
            k = f"sb:{i}"
            mk(self.s, self.clock, stage="3", key=k, model="opus", tokens=(8000, 2000))
            self.s.add_outcome(k, "adopted" if i < 3 else "dropped", "hub")
        mk(self.s, self.clock, stage="2", mode="make", state="cancelled", model="opus")

    def test_ratings_output(self) -> None:
        self.fixture()
        out = self.dah("ratings")
        self.assertIn("Ratings (first-pass rate)", out)
        self.assertIn("[anim] #1 Storyboard — 11/16 · 68% · good", out)
        self.assertIn("stage 1 Beats: 8/10 · 80% · good", out)
        self.assertIn("stage 3 Review: 3/5 · 60% · needs fixing", out)
        self.assertIn("median tokens 5.0K/attempt (measured 10) · per adopted deliverable -", out)
        self.assertIn("median tokens 10.0K/attempt (measured 5) · per adopted deliverable 16.7K (3)", out)
        self.assertNotIn("#1 Storyboard", self.dah("ratings", "--lane", "render"))
        self.assertIn("No process needs fixing", self.dah("ratings", "--weak"))  # the row is good as a whole

    def test_ratings_in_other_languages(self) -> None:
        self.fixture()
        for lang, word in (("ko", "양호"), ("ja", "良好"), ("zh", "良好"), ("es", "bueno")):
            for lab in rating.STATES.values():
                self.assertTrue(lab[0].get(lang))
            self.assertTrue(all(w.get(lang) for w in rating.WORDS.values()), lang)
            self.assertIn(word, rating.label("good", lang))

    def test_empty_ledger(self) -> None:
        self.assertIn("No rated tasks yet", self.dah("ratings"))

    def test_deliverable_and_blame_commands(self) -> None:
        self.dah("task", "story", "--title", "t", "Line: #1 Storyboard · stage 2 · process confirmed · pattern confirmed · make\nx",
                 "--deliverable", "sb:1", lane="hub")
        self.dah("deliverable", "sb:1", "redirected", ok=False, lane="hub")  # needs the human's words
        self.dah("deliverable", "sb:1", "adopted", lane="story", ok=False)  # lanes do not write results
        self.dah("deliverable", "sb:1", "redirected", "--note", "make it darker", lane="hub")
        out = self.dah("deliverable", "sb:1")
        self.assertIn("sb:1 · redirected · 1 task(s)", out)
        self.assertIn("make it darker", out)
        t = self.s.next_queued("story")
        self.s.update_task(t["id"], state="running")
        self.assertIn("stage 2", self.dah("blame", "stage 2", "--cause", "frames", lane="story"))
        self.assertEqual(self.s.blames()[t["id"]]["effective"], "2")
        self.dah("blame", "--confirm", "--task", str(t["id"]), "1", ok=False, lane="story")  # only the hub confirms
        self.assertIn("stage 1 (the lane wrote 2)", self.dah("blame", "--confirm", "--task", str(t["id"]), "1", lane="hub"))
        self.dah("blame", "1", ok=False, lane="hub")  # the hub names a task
        self.dah("deliverable", "nope:1", ok=False, lane="hub")


class EngineIntegrationTest(unittest.TestCase):
    def test_human_stage_read_from_the_card(self) -> None:
        home, cfg = make_engine_home()
        rows = {"anim": engines.build(home / "engines/anim/pattern-library.md", home / ".agents/skills")}
        rated = rating.attach(rows, Store(cfg.db))
        r = next(x for x in rated if x["row"] == "1")
        self.assertEqual([s["n"] for s in r["stages"]], ["1", "2", "3"])
        self.assertEqual(r["process"]["state"], "new")


if __name__ == "__main__":
    unittest.main()
