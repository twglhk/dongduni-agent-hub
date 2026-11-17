import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from agent_hub.config import ConfigError
from agent_hub.engine import Engine
from agent_hub.schedules import Schedule, latest_period, parse
from agent_hub.store import HUB, Store
from tests.helpers import Clock, FakeRuntime, make_home

KINDS = {"hub": "hub", "infra": "infra", "docs": "team"}
WEEKLY = {"lane": "docs", "every": "week", "day": "mon", "at": "09:00", "text": "model: sonnet\neffort: low\n\nWeekly review"}


def ts(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> float:
    return datetime(y, m, d, hh, mm).timestamp()


class ParseTest(unittest.TestCase):
    def test_nothing_configured_is_no_schedules(self) -> None:
        self.assertEqual(parse(None, KINDS), [])
        self.assertEqual(parse([], KINDS), [])
        self.assertEqual(make_home().schedules, [])

    def test_a_full_entry(self) -> None:
        (s,) = parse([WEEKLY], KINDS)
        self.assertEqual((s.lane, s.every, s.day, s.hour, s.minute, s.id), ("docs", "week", 0, 9, 0, "docs-week-mon-0900"))
        self.assertEqual(s.title, "model: sonnet")

    def test_defaults_and_explicit_title_and_id(self) -> None:
        (s,) = parse([{"lane": "hub", "every": "day", "text": "x", "title": "T", "id": "morning"}], KINDS)
        self.assertEqual((s.every, s.hour, s.minute, s.title, s.id), ("day", 9, 0, "T", "morning"))
        (w,) = parse([{"lane": "docs", "every": "week", "text": "x"}], KINDS)
        self.assertEqual(w.day, 0)

    def test_bad_values_are_config_errors(self) -> None:
        bad = [
            "nope", [3], [{"lane": "nope", "every": "day", "text": "x"}], [{"lane": "infra", "every": "day", "text": "x"}],
            [{"lane": "docs", "every": "month", "text": "x"}], [{"lane": "docs", "every": "week", "day": "funday", "text": "x"}],
            [{"lane": "docs", "every": "day", "day": "mon", "text": "x"}], [{"lane": "docs", "every": "day", "at": "25:00", "text": "x"}],
            [{"lane": "docs", "every": "day", "at": "9am", "text": "x"}], [{"lane": "docs", "every": "day", "text": "  "}],
            [{"lane": "docs", "every": "day"}], [{"lane": "docs", "every": "day", "text": "x", "when": "now"}],
            [{"lane": "docs", "every": "day", "text": "x", "id": "a b"}], [{"lane": "docs", "every": "day", "text": "x", "title": ""}],
            [{"lane": "docs", "every": "day", "text": "x"}, {"lane": "docs", "every": "day", "text": "y"}],
        ]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse(raw, KINDS)

    def test_tower_json_turns_it_into_a_config_error(self) -> None:
        with self.assertRaises(ConfigError):
            make_home(schedules=[{"lane": "ghost", "every": "day", "text": "x"}])


class PeriodTest(unittest.TestCase):
    def test_week_period_is_the_latest_occurrence(self) -> None:
        s = Schedule("w", "docs", "week", 0, 9, 0, "x", "x")
        self.assertEqual(latest_period(s, ts(2026, 11, 16, 8, 59)), "2026-11-09")
        self.assertEqual(latest_period(s, ts(2026, 11, 16, 9, 0)), "2026-11-16")
        self.assertEqual(latest_period(s, ts(2026, 11, 22, 23, 59)), "2026-11-16")

    def test_day_period_rolls_over_at_the_time(self) -> None:
        s = Schedule("d", "docs", "day", 0, 9, 30, "x", "x")
        self.assertEqual(latest_period(s, ts(2026, 11, 17, 9, 29)), "2026-11-16")
        self.assertEqual(latest_period(s, ts(2026, 11, 17, 9, 30)), "2026-11-17")

    def test_a_month_boundary(self) -> None:
        s = Schedule("w", "docs", "week", 4, 18, 0, "x", "x")
        self.assertEqual(latest_period(s, ts(2026, 12, 2, 12)), "2026-11-27")


class FiringTest(unittest.TestCase):
    def build(self, schedules: list[dict], start: float) -> None:
        self.cfg = make_home(schedules=schedules)
        self.clock = Clock(start)
        self.s = Store(self.cfg.db, clock=self.clock)
        self.start_engine()

    def start_engine(self) -> None:
        self.e = Engine(self.cfg, self.s, FakeRuntime(self.s), lambda n: None, clock=self.clock, log=lambda _: None)

    def at(self, t: float) -> None:
        self.clock.t = t
        self.e.tick()

    def tasks(self) -> list[dict]:
        return self.s.tasks(None, 100)

    def test_nothing_fires_without_schedules(self) -> None:
        self.build([], ts(2026, 11, 16, 9, 5))
        self.e.tick()
        self.assertEqual(self.tasks(), [])

    def test_fires_once_when_the_time_comes_and_again_next_week(self) -> None:
        self.build([WEEKLY], ts(2026, 11, 16, 8, 0))
        self.e.tick()
        self.assertEqual(self.tasks(), [])
        self.at(ts(2026, 11, 16, 9, 0))
        (t,) = self.tasks()
        self.assertEqual((t["lane"], t["title"], t["engine"]), ("docs", "model: sonnet", self.cfg.lanes["docs"].engine))
        self.assertIn("Weekly review", t["brief"])
        self.assertEqual(t["model"], "sonnet")
        for hh in (9, 12, 23):
            self.at(ts(2026, 11, 18, hh))
        self.assertEqual(len(self.tasks()), 1)
        self.at(ts(2026, 11, 23, 9, 0))
        self.assertEqual(len(self.tasks()), 2)

    def test_a_schedule_added_late_does_not_fire_for_a_period_already_gone(self) -> None:
        self.build([WEEKLY], ts(2026, 11, 18, 15, 0))
        self.e.tick()
        self.at(ts(2026, 11, 20))
        self.assertEqual(self.tasks(), [])
        self.at(ts(2026, 11, 23, 9, 1))
        self.assertEqual(len(self.tasks()), 1)

    def test_a_restart_never_fires_a_period_twice(self) -> None:
        self.build([WEEKLY], ts(2026, 11, 16, 8, 0))
        self.e.tick()
        self.at(ts(2026, 11, 16, 9, 5))
        self.start_engine()
        self.at(ts(2026, 11, 16, 10, 0))
        self.assertEqual(len(self.tasks()), 1)

    def test_periods_missed_while_down_fire_once_on_catch_up(self) -> None:
        self.build([WEEKLY], ts(2026, 11, 2, 8, 0))
        self.e.tick()
        self.at(ts(2026, 11, 2, 9, 5))
        self.assertEqual(len(self.tasks()), 1)
        self.start_engine()
        self.at(ts(2026, 11, 25, 14, 0))
        self.assertEqual(len(self.tasks()), 2)
        self.at(ts(2026, 11, 25, 15, 0))
        self.assertEqual(len(self.tasks()), 2)

    def test_daily_request_to_the_hub(self) -> None:
        self.build([{"lane": "hub", "every": "day", "at": "07:30", "text": "Morning check"}], ts(2026, 11, 16, 7, 0))
        self.e.tick()
        self.assertEqual(self.s.memos(HUB, 10), [])
        self.at(ts(2026, 11, 16, 7, 30))
        self.at(ts(2026, 11, 16, 12, 0))
        (m,) = self.s.memos(HUB, 10)
        self.assertEqual((m["text"], m["sender"]), ("Morning check", "schedule"))
        self.at(ts(2026, 11, 17, 7, 30))
        self.assertEqual(len(self.s.memos(HUB, 10)), 2)

    def test_two_schedules_keep_their_own_state(self) -> None:
        other = {"lane": "docs", "every": "day", "at": "10:00", "text": "Daily", "id": "daily"}
        self.build([WEEKLY, other], ts(2026, 11, 16, 8, 0))
        self.e.tick()
        self.at(ts(2026, 11, 16, 9, 0))
        self.assertEqual([t["brief"].endswith("Weekly review") for t in self.tasks()], [True])
        self.at(ts(2026, 11, 16, 10, 0))
        self.assertEqual(len(self.tasks()), 2)


class OldDatabaseTest(unittest.TestCase):
    def test_a_database_from_before_schedules_opens_and_gains_the_table(self) -> None:
        path = Path(tempfile.mkdtemp()) / "tower.db"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, lane TEXT NOT NULL, title TEXT NOT NULL, brief TEXT NOT NULL, "
                    "state TEXT NOT NULL, created REAL NOT NULL)")
        con.execute("INSERT INTO tasks(lane, title, brief, state, created) VALUES('docs','old','b','closed',1)")
        con.commit()
        con.close()
        s = Store(path)
        self.assertIsNone(s.schedule_period("weekly"))
        s.mark_schedule("weekly", "2026-11-16")
        s.mark_schedule("weekly", "2026-11-23")
        self.assertEqual(Store(path).schedule_period("weekly"), "2026-11-23")
        self.assertEqual(s.task(1)["title"], "old")


if __name__ == "__main__":
    unittest.main()
