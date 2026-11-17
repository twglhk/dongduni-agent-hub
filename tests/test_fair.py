import os
import subprocess
import sys
import unittest
from pathlib import Path

from agent_hub.engine import Engine, team_turns
from agent_hub.fair import in_turns, task_who, turns, waiting_by_person
from agent_hub.store import Store
from tests.helpers import Clock, FakeRuntime, make_home

ROOT = Path(__file__).resolve().parents[1]
LANES = {"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"}, "docs": {"runtime": "claude"},
         "web": {"runtime": "claude"}, "art": {"runtime": "claude"}}


def row(i: int, who: str) -> dict:
    return {"id": i, "requester": who}


class TurnsTest(unittest.TestCase):
    def test_each_persons_first_comes_before_anyones_second(self) -> None:
        rows = [row(1, "ana"), row(2, "ana"), row(3, "ana"), row(4, "bo")]
        self.assertEqual([r["id"] for r in in_turns(rows, task_who)], [1, 4, 2, 3])

    def test_work_already_running_counts_as_a_turn(self) -> None:
        rows = [row(2, "ana"), row(3, "bo")]
        self.assertEqual([r["id"] for r in in_turns(rows, task_who, running=[row(1, "ana")])], [3, 2])

    def test_marked_rows_stay_first_in_their_own_order(self) -> None:
        rows = [row(1, "ana"), row(2, "ana"), row(3, "bo"), row(4, "ana")]
        got = in_turns(rows, task_who, first=lambda r: r["id"] == 4)
        self.assertEqual([r["id"] for r in got], [4, 1, 3, 2])

    def test_one_person_keeps_oldest_first(self) -> None:
        rows = [row(5, "ana"), row(6, "ana"), row(7, "ana")]
        self.assertEqual([r["id"] for r in in_turns(rows, task_who)], [5, 6, 7])

    def test_a_task_without_a_requester_takes_turns_as_nobody(self) -> None:
        rows = [row(1, ""), row(2, ""), row(3, "bo")]
        self.assertEqual([r["id"] for r in in_turns(rows, task_who)], [1, 3, 2])

    def test_waiting_by_person_counts_most_first(self) -> None:
        rows = [row(1, "ana"), row(2, "bo"), row(3, "bo")]
        self.assertEqual(list(waiting_by_person(rows, task_who).items()), [("bo", 2), ("ana", 1)])

    def test_turns_returns_a_sort_key_per_id(self) -> None:
        self.assertEqual(turns([row(1, "ana"), row(2, "ana")], task_who), {1: (1, 0, 0), 2: (1, 1, 1)})


class EngineTurnsTest(unittest.TestCase):
    def build(self, max_lanes: int = 1, **extra: object) -> None:
        self.cfg = make_home(LANES, max_lanes=max_lanes, **extra)
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda _: None, clock=self.clock, log=lambda _: None)

    def started(self) -> list[str]:
        return [o[0] for o in self.rt.opened if o[0] in ("docs", "web", "art")]

    def queue_two_for_ana_and_one_for_bo(self) -> None:
        self.s.add_task("docs", "a1", "x", requester="ana")
        self.s.add_task("web", "a2", "x", requester="ana")
        self.s.add_task("art", "b1", "x", requester="bo")

    def test_with_fair_turns_the_next_free_slot_goes_to_the_person_with_nothing_running(self) -> None:
        self.build(max_lanes=2, fair_turns=True)
        self.s.add_task("docs", "a1", "x", requester="ana")
        self.e.tick()
        self.s.add_task("web", "a2", "x", requester="ana")
        self.s.add_task("art", "b1", "x", requester="bo")
        self.clock.tick(10)
        self.e.tick()
        self.assertEqual(self.started(), ["docs", "art"])

    def test_without_the_key_the_oldest_task_goes_first(self) -> None:
        self.build(max_lanes=2)
        self.s.add_task("docs", "a1", "x", requester="ana")
        self.e.tick()
        self.s.add_task("web", "a2", "x", requester="ana")
        self.s.add_task("art", "b1", "x", requester="bo")
        self.clock.tick(10)
        self.e.tick()
        self.assertEqual(self.started(), ["docs", "web"])

    def test_a_task_whose_after_task_closed_goes_first(self) -> None:
        self.build(fair_turns=True)
        first = self.s.add_task("docs", "a1", "x", requester="ana")
        self.s.update_task(first, state="closed", closed=1.0)
        self.s.add_task("art", "b1", "x", requester="bo")
        self.s.add_task("web", "a2", f"after: {first}\nx", requester="ana")
        order = team_turns(self.s, {"docs", "web", "art"})
        self.assertLess(order[3], order[2])

    def test_without_fairness_the_order_is_by_id(self) -> None:
        self.build()
        self.queue_two_for_ana_and_one_for_bo()
        order = team_turns(self.s, {"docs", "web", "art"}, fair=False)
        self.assertEqual(sorted(order, key=order.get), [1, 2, 3])


class StatusTest(unittest.TestCase):
    def test_status_names_whose_each_waiting_task_is_and_counts_per_person(self) -> None:
        cfg = make_home(LANES, fair_turns=True)
        s = Store(cfg.db)
        s.add_task("docs", "a1", "x", requester="ana")
        s.add_task("docs", "a2", "x", requester="ana")
        s.add_task("art", "b1", "x", requester="bo")
        env = {**os.environ, "TOWER_HOME": str(cfg.home), "PYTHONPATH": str(ROOT)}
        env.pop("TOWER_LANE", None)
        p = subprocess.run([sys.executable, "-m", "agent_hub", "status"], cwd=cfg.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        self.assertIn("#1 for ana", p.stdout)
        self.assertIn("#3 for bo", p.stdout)
        self.assertIn("ana 2, bo 1", p.stdout)


if __name__ == "__main__":
    unittest.main()
