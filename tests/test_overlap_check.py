import json
import os
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

from agent_hub import config as config_mod, overlap
from agent_hub.engine import Engine, conflict_of, infra_waits
from agent_hub.store import HUMAN, INFRA, TOWER, Store, StoreError
from agent_hub.visibility import Gate, can_see
from tests.helpers import Clock, FakeRuntime, make_home
from tests.test_person_hubs import with_members

ROOT = Path(__file__).resolve().parents[1]
LANES = {"hub": {"runtime": "claude"}, "infra": {"runtime": "claude", "max_instances": 2}, "docs": {"runtime": "codex"}}
WATCH = {"files": [".tower/PROTOCOL.md", ".tower/lanes/*.md", "AGENTS.md", ".claude/skills/**", "docs/glossary.md"]}
SESSIONS = ["infra", "infra#2"]
OPTIONS = [{"label": "x", "desc": ""}, {"label": "y", "desc": ""}]


def build(**extra: object) -> config_mod.Config:
    return with_members(make_home(LANES, rule_watch=WATCH, overlap_check=True, **extra), "alice", "bob")


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build()
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda n: None, clock=self.clock, log=lambda _: None)

    def memo(self, sender: str, text: str, paths: str | None) -> int:
        mid = self.s.push(INFRA, sender, "memo", text, paths=paths)
        self.clock.tick(1)
        return mid

    def held(self) -> set[int]:
        return {m["id"] for m in self.s.held_messages(SESSIONS).values()}

    def cards(self, state: str = "open") -> list[dict]:
        return [it for it in self.s.items(state=state) if it["lane"] == TOWER]


class OverlapTest(Base):
    def test_other_persons_same_file_waits_for_a_card_to_both(self) -> None:
        a = self.memo("hub-alice", "[Protocol wording] fix the item rules", ".tower/PROTOCOL.md")
        self.e.tick()
        self.assertEqual(self.held(), {a})
        b = self.memo("hub-bob", "[Lane notices] add a line", ".tower/PROTOCOL.md, .tower/lanes/hub.md")
        self.e.tick()
        self.assertEqual(self.held(), {a})
        cards = self.cards()
        self.assertEqual(sorted(c["assignee"] for c in cards), ["alice", "bob"])
        card = next(c for c in cards if c["assignee"] == "bob")
        self.assertEqual((card["kind"], card["audience"]), ("decision", HUMAN))
        self.assertIn(f"memo #{a}", card["body"])
        self.assertIn("`.tower/PROTOCOL.md`", card["body"])
        self.assertEqual([o["label"] for o in card["options"]], ["Go ahead", "After the earlier one", "Merge"])
        self.assertTrue(card["options"][1].get("recommended"))
        why = dict((m["id"], w) for m, w in infra_waits(self.s, SESSIONS, self.clock()))
        self.assertEqual(why[b][0], "overlap_hold")
        self.e.tick()
        self.assertEqual(len(self.cards()), 2)

        self.s.decide(card["id"], "ok", "bob", choice="After the earlier one")
        self.e.tick()
        self.assertEqual(self.s.overlap(f"memo:{b}")["state"], "after")
        self.assertEqual(self.cards(), [])
        self.assertEqual(self.s.item(next(c["id"] for c in self.s.items(state="merged"))).get("verdict"), "merged")
        for hub in ("hub-alice", "hub-bob"):
            news = [m for m in self.s.pending(hub) if m["kind"] == "decision"]
            self.assertEqual(len(news), 1, hub)
            self.assertIn("starts once the earlier work finishes", news[0]["text"])
        self.assertEqual(self.held(), {a})
        self.s.finish_inbox("infra")
        self.e.tick()
        self.e.tick()
        self.assertIn(b, self.held())

    def test_same_person_only_meets_the_path_lock(self) -> None:
        self.memo("hub-alice", "[Protocol wording] one", ".tower/PROTOCOL.md")
        self.e.tick()
        self.memo("hub-alice", "[Protocol wording] two", ".tower/PROTOCOL.md")
        self.e.tick()
        self.assertEqual(self.cards(), [])

    def test_work_that_touches_no_shared_file_is_not_checked(self) -> None:
        self.memo("hub-alice", "[Board colours] blue", "board-app")
        self.e.tick()
        b = self.memo("hub-bob", "[Board colours] green", "board-app")
        self.e.tick()
        self.assertEqual(self.cards(), [])
        self.assertIsNone(self.s.overlap(f"memo:{b}"))

    def test_a_folder_that_holds_a_shared_file_counts(self) -> None:
        self.memo("hub-alice", "[x] one", ".tower/PROTOCOL.md")
        self.e.tick()
        self.memo("hub-bob", "[y] two", ".tower")
        self.e.tick()
        self.assertEqual(len(self.cards()), 2)

    def test_same_topic_words_and_merge_for_a_task(self) -> None:
        older = self.s.add_item("hub-alice", None, "decision", "Shared notices design", "see AGENTS.md", OPTIONS, [],
                                audience=HUMAN, assignee="alice")
        self.clock.tick(1)
        tid = self.s.add_task("docs", "Shared notices wording", "paths: .claude/skills/review-board\nwork",
                              requester="bob")
        self.e.overlaps.scan()
        card = next(c for c in self.cards() if c["assignee"] == "bob")
        self.assertIn(f"card #{older}", card["body"])
        self.assertIn("same topic words notices, shared", card["body"])
        self.assertIn("overlap", conflict_of(self.s, self.s.task(tid)))
        self.s.decide(card["id"], "ok", "bob", choice="Merge")
        self.e.tick()
        self.assertEqual(self.s.task(tid)["state"], "queued")
        self.assertIn("merge", conflict_of(self.s, self.s.task(tid)))

    def test_a_decision_card_that_names_a_file_by_its_last_part_overlaps(self) -> None:
        self.s.add_item("hub-alice", None, "decision", "Reword", "the `PROTOCOL.md` table", OPTIONS, [],
                        audience=HUMAN, assignee="alice")
        self.clock.tick(1)
        self.s.add_task("docs", "other", "paths: .tower/PROTOCOL.md\nwork", requester="bob")
        self.e.overlaps.scan()
        self.assertEqual(len(self.cards()), 2)

    def test_a_held_task_starts_after_the_earlier_task_finishes(self) -> None:
        first = self.s.add_task("docs", "one", "paths: AGENTS.md\nwork", requester="alice")
        self.s.update_task(first, state="running", started=self.clock())
        self.clock.tick(1)
        second = self.s.add_task("docs", "two", "paths: AGENTS.md\nwork", requester="bob")
        self.e.overlaps.scan()
        card = next(c for c in self.cards() if c["assignee"] == "alice")
        self.s.decide(card["id"], "ok", "alice", choice="After the earlier one")
        self.e.route_decisions()
        self.assertIn("waits for task:", overlap.held_text(self.s.overlap(f"task:{second}")))
        self.s.update_task(first, state="closed", closed=self.clock())
        self.e.overlaps.scan()
        self.assertEqual(self.s.overlap(f"task:{second}")["state"], "go")
        self.assertNotIn("overlap", conflict_of(self.s, self.s.task(second)) or "")

    def test_work_that_vanishes_while_waiting_closes_its_cards(self) -> None:
        a = self.memo("hub-alice", "[x] one", "AGENTS.md")
        self.e.tick()
        b = self.memo("hub-bob", "[y] two", "AGENTS.md")
        self.e.tick()
        self.assertEqual(len(self.cards()), 2)
        self.s.cancel_message(b, INFRA, "not needed", "hub-bob")
        self.e.tick()
        self.assertEqual(self.cards(), [])
        self.assertEqual(self.s.overlap(f"memo:{b}")["state"], "clear")
        self.assertIn(a, self.held())


class HoldIsKeptTest(Base):
    def setUp(self) -> None:
        super().setUp()
        self.a = self.memo("hub-alice", "[Shared notices wording] one", ".tower/PROTOCOL.md")
        self.e.tick()
        self.b = self.memo("hub-bob", "[Shared notices wording] two", "AGENTS.md")
        self.e.tick()
        self.card = next(c for c in self.cards() if c["assignee"] == "bob")

    def test_a_memo_with_other_paths_but_the_same_topic_words_waits_for_the_card(self) -> None:
        self.assertEqual(self.s.overlap(f"memo:{self.b}")["state"], "wait")
        self.assertEqual(self.held(), {self.a})
        self.e.tick()
        self.assertEqual(self.held(), {self.a})

    def test_it_keeps_waiting_after_the_earlier_memo_finished(self) -> None:
        self.s.finish_inbox("infra")
        self.e.tick()
        self.e.tick()
        self.assertNotIn(self.b, self.held())
        self.assertEqual(self.s.message(self.b)["delivered"], None)

    def test_a_merge_answer_does_not_start_it(self) -> None:
        self.s.decide(self.card["id"], "ok", "bob", choice="Merge")
        self.e.tick()
        self.s.finish_inbox("infra")
        self.e.tick()
        self.assertEqual(self.s.overlap(f"memo:{self.b}")["state"], "merge")
        self.assertNotIn(self.b, self.held())


class OwnAnswerTest(Base):
    def setUp(self) -> None:
        super().setUp()
        self.memo("hub-alice", "[x] one", ".tower/PROTOCOL.md")
        self.e.tick()
        self.b = self.memo("hub-bob", "[y] two", ".tower")
        self.e.tick()
        self.card = next(c for c in self.cards() if c["assignee"] == "bob")

    def cli(self, lane: str, how: str) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "TOWER_HOME": str(self.cfg.home), "PYTHONPATH": str(ROOT), "TOWER_LANE": lane}
        return subprocess.run([sys.executable, "-m", "agent_hub", "overlap", str(self.card["id"]), how],
                              cwd=self.cfg.home, env=env, capture_output=True, text=True, encoding="utf-8")

    def test_an_answer_in_own_words_keeps_waiting_until_the_hub_sets_it(self) -> None:
        self.s.decide(self.card["id"], "answer", "bob", note="start it after lunch")
        self.e.tick()
        self.assertEqual(self.s.overlap(f"memo:{self.b}")["state"], "wait")
        p = self.cli("hub-bob", "go")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.s.overlap(f"memo:{self.b}")["state"], "go")
        self.s.finish_inbox("infra")
        self.e.tick()
        self.assertIn(self.b, self.held())

    def test_the_hub_cannot_set_it_before_the_person_answers(self) -> None:
        p = self.cli("hub-bob", "go")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("still open", p.stderr)
        self.assertEqual(self.s.overlap(f"memo:{self.b}")["state"], "wait")

    def test_only_a_hub_that_sent_one_of_the_two_may_set_it(self) -> None:
        self.s.decide(self.card["id"], "answer", "bob", note="later")
        self.e.tick()
        self.assertEqual(overlap.Overlaps(self.cfg, self.s).hubs_of([f"memo:{self.b}"]), ["hub-bob"])
        with self.assertRaises(ValueError):
            overlap.decide_by_hub(self.cfg, self.s, self.card["id"], "go", "hub")
        with self.assertRaises(ValueError):
            overlap.decide_by_hub(self.cfg, self.s, 9999, "go", "hub-bob")
        self.assertEqual(overlap.decide_by_hub(self.cfg, self.s, self.card["id"], "merge", "hub-alice"), f"memo:{self.b}")


class VisibilityTest(Base):
    def test_each_card_is_for_one_of_the_two_people(self) -> None:
        self.memo("hub-alice", "[x] one", "AGENTS.md")
        self.e.tick()
        self.memo("hub-bob", "[y] two", "AGENTS.md")
        self.e.tick()
        mine = {name: [c["id"] for c in self.cards() if can_see(Gate.of(self.cfg, self.s, {"name": name, "role": "member"}), c)]
                for name in ("alice", "bob", "carol")}
        self.assertEqual([len(mine["alice"]), len(mine["bob"]), len(mine["carol"])], [1, 1, 0])
        self.assertNotEqual(mine["alice"], mine["bob"])
        owner = Gate.of(self.cfg, self.s, {"name": "root", "role": "owner"})
        self.assertEqual(len([c for c in self.cards() if can_see(owner, c)]), 2)

    def test_without_person_scoping_one_card_is_for_everyone(self) -> None:
        cfg = make_home(LANES, rule_watch=WATCH, overlap_check=True)
        s = Store(cfg.db, clock=self.clock)
        s.add_member("alice", "member", "a@x.com")
        s.add_member("bob", "member", "b@x.com")
        e = Engine(cfg, s, FakeRuntime(s), lambda n: None, clock=self.clock, log=lambda _: None)
        s.push(INFRA, "alice", "memo", "[x] one", paths="AGENTS.md")
        self.clock.tick(1)
        e.tick()
        s.push(INFRA, "bob", "memo", "[y] two", paths="AGENTS.md")
        self.clock.tick(1)
        e.tick()
        cards = [it for it in s.items(state="open") if it["lane"] == TOWER]
        self.assertEqual([c["assignee"] for c in cards], [None])


class OffByDefaultTest(unittest.TestCase):
    def test_nothing_is_checked_without_the_key(self) -> None:
        cfg = with_members(make_home(LANES, rule_watch=WATCH), "alice", "bob")
        self.assertFalse(cfg.overlap_check)
        clock = Clock()
        s = Store(cfg.db, clock=clock)
        e = Engine(cfg, s, FakeRuntime(s), lambda n: None, clock=clock, log=lambda _: None)
        self.assertIsNone(e.overlaps)
        s.push(INFRA, "hub-alice", "memo", "[x] one", paths="AGENTS.md")
        clock.tick(1)
        e.tick()
        b = s.push(INFRA, "hub-bob", "memo", "[y] two", paths="AGENTS.md")
        e.tick()
        self.assertEqual([it for it in s.items() if it["lane"] == TOWER], [])
        self.assertIsNone(s.overlap(f"memo:{b}"))

    def test_the_key_needs_the_files_it_protects(self) -> None:
        with self.assertRaises(config_mod.ConfigError):
            make_home(LANES, overlap_check=True)
        with self.assertRaises(config_mod.ConfigError):
            make_home(LANES, overlap_check="yes", rule_watch=WATCH)


class OldDatabaseTest(unittest.TestCase):
    def test_a_database_made_before_the_table_gets_it_and_keeps_its_rows(self) -> None:
        cfg = make_home(LANES)
        s = Store(cfg.db)
        tid = s.add_task("docs", "kept", "brief")
        con = sqlite3.connect(cfg.db)
        con.execute("DROP TABLE overlaps")
        con.commit()
        con.close()
        s2 = Store(cfg.db)
        self.assertIsNone(s2.overlap("task:1"))
        s2.put_overlap(f"task:{tid}", "wait", [3], ["memo:1"])
        got = s2.overlap(f"task:{tid}")
        self.assertEqual((got["state"], got["items"], got["others"]), ("wait", [3], ["memo:1"]))
        self.assertEqual(s2.overlap_of_item(3)["work"], f"task:{tid}")
        self.assertEqual(s2.task(tid)["title"], "kept")


class WordsTest(unittest.TestCase):
    def test_words_and_files(self) -> None:
        self.assertEqual(overlap.words("Fix the shared rules wording #12"), {"shared", "wording"})
        self.assertEqual(overlap.words("공용 지침 변경"), {"공용", "지침", "변경"})
        files = WATCH["files"]
        self.assertEqual(overlap.named_files("see PROTOCOL.md and `.tower/lanes/hub.md`, not web.py", files),
                         {".tower/PROTOCOL.md", ".tower/lanes/hub.md"})
        self.assertEqual(overlap.title_of("[Short title] body"), "Short title")
        self.assertEqual(overlap.title_of("\n first line\nsecond"), "first line")


if __name__ == "__main__":
    unittest.main()
