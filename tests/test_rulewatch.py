import re
import subprocess
import unittest
from typing import Any

from agent_hub import config as config_mod, rulewatch
from agent_hub.engine import Engine
from agent_hub.rulewatch import CHECK_SECONDS, RuleWatch
from agent_hub.store import HUMAN, INFRA, TOWER, Store
from tests.helpers import Clock, FakeRuntime, make_home

WATCH = {"files": [".tower/PROTOCOL.md", ".tower/lanes/*.md", "AGENTS.md", ".claude/skills/**", "docs/glossary.md"],
         "delay_minutes": 10}
QUIET = 600


class Repo:
    def __init__(self, home: Any):
        self.home = home
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")

    def git(self, *a: str) -> None:
        subprocess.run(["git", "-C", str(self.home), *a], check=True, capture_output=True)

    def commit(self, path: str, text: str, msg: str) -> None:
        f = self.home / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        self.git("add", path)
        self.git("commit", "-q", "-m", msg)


class RuleWatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home(rule_watch=WATCH)
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.repo = Repo(self.cfg.home)
        self.repo.commit("AGENTS.md", "start\n", "start")
        self.w = RuleWatch(self.cfg.home, self.s, self.cfg.state_dir, self.cfg.rule_watch, "en", self.clock,
                           lambda _: None, lambda k: {"hub-alice": "alice"}.get(k, k))

    def run_for(self, seconds: float) -> None:
        self.clock.tick(seconds)
        self.w.tick()

    def cards(self) -> list[dict]:
        return [it for it in self.s.items() if it["lane"] == TOWER]

    def test_first_look_notifies_nothing_then_one_card_per_memo_after_the_delay(self) -> None:
        self.repo.commit(".tower/PROTOCOL.md", "old\n", "before the first look")
        self.w.tick()
        self.run_for(QUIET + CHECK_SECONDS)
        self.assertEqual(self.cards(), [])
        mid = self.s.push(INFRA, "hub-alice", "memo", "[x] y")
        self.repo.commit(".tower/PROTOCOL.md", "rule one\nrule two\n", f"Infra: memo #{mid} item rules, request #839")
        self.repo.commit("web.py", "x\n", f"Web: memo #{mid}")
        self.run_for(CHECK_SECONDS)
        self.repo.commit(".tower/lanes/hub.md", "hub rule\n", f"Hub: memo #{mid} hub line")
        self.run_for(CHECK_SECONDS)
        self.assertEqual(self.cards(), [])
        self.run_for(QUIET)
        cards = self.cards()
        self.assertEqual(len(cards), 1)
        c = cards[0]
        self.assertEqual((c["kind"], c["audience"], c["assignee"]), ("review", HUMAN, None))
        self.assertIn(f"(memo #{mid})", c["title"])
        self.assertIn("`.tower/PROTOCOL.md` (+2 −1)", c["body"])
        self.assertIn("`.tower/lanes/hub.md`", c["body"])
        self.assertNotIn("web.py", c["body"])
        self.assertIn("hub-alice → alice", c["body"])
        self.assertIn("request #839", c["body"])
        self.assertIn("- added · rule one", c["body"])

        self.repo.commit("AGENTS.md", "start\nmore\n", f"Fix: memo #{mid} wording")
        self.run_for(CHECK_SECONDS)
        self.run_for(QUIET)
        self.assertEqual(len(self.cards()), 1)
        self.assertIn("`AGENTS.md`", self.s.item(c["id"])["body"])

    def test_delay_minutes_is_the_quiet_time(self) -> None:
        cfg = make_home(rule_watch={"files": ["AGENTS.md"], "delay_minutes": 2})
        repo = Repo(cfg.home)
        repo.commit("AGENTS.md", "a\n", "start")
        w = RuleWatch(cfg.home, self.s, cfg.state_dir, cfg.rule_watch, "en", self.clock, lambda _: None)
        w.tick()
        repo.commit("AGENTS.md", "b\n", "change")
        self.run_for(0)
        self.clock.tick(CHECK_SECONDS)
        w.tick()
        self.assertEqual(self.cards(), [])
        self.clock.tick(120)
        w.tick()
        self.assertEqual(len(self.cards()), 1)

    def test_non_rule_commits_and_daily_limit(self) -> None:
        limit = self.cfg.rule_watch["cards_per_day"]
        self.w.tick()
        self.repo.commit("docs/notes.md", "x\n", "notes")
        self.repo.commit(".claude/skills/x/SKILL.md", "a\n", "skill only")
        for i in range(limit + 1):
            self.repo.commit("docs/glossary.md", f"word {i}\n", f"glossary memo #{100 + i}")
        self.run_for(CHECK_SECONDS)
        self.run_for(QUIET)
        self.assertEqual(len(self.cards()), limit)
        self.run_for(86400)
        self.assertEqual(len(self.cards()), limit + 2)

    def test_non_ascii_file_names_are_read_as_they_are(self) -> None:
        cfg = make_home(rule_watch={"files": ["docs/*.md"]})
        repo = Repo(cfg.home)
        repo.commit("docs/start.md", "a\n", "start")
        w = RuleWatch(cfg.home, self.s, cfg.state_dir, cfg.rule_watch, "en", self.clock, lambda _: None)
        w.tick()
        repo.commit("docs/용어.md", "b\n", "glossary")
        self.clock.tick(CHECK_SECONDS)
        w.tick()
        self.clock.tick(QUIET)
        w.tick()
        self.assertIn("`docs/용어.md`", self.cards()[0]["body"])

    def test_history_rewritten_starts_over_without_a_card(self) -> None:
        self.w.tick()
        self.repo.git("reset", "-q", "--hard", "HEAD")
        self.repo.git("checkout", "-q", "--orphan", "other")
        self.repo.commit(".tower/PROTOCOL.md", "x\n", "new root")
        self.run_for(CHECK_SECONDS)
        self.run_for(QUIET)
        self.assertEqual(self.cards(), [])

    def test_korean_card(self) -> None:
        w = RuleWatch(self.cfg.home, self.s, self.cfg.state_dir, self.cfg.rule_watch, "ko", self.clock, lambda _: None)
        w.tick()
        self.repo.commit("AGENTS.md", "b\n", "memo #5 change")
        self.run_for(0)
        self.clock.tick(CHECK_SECONDS)
        w.tick()
        self.clock.tick(QUIET)
        w.tick()
        self.assertIn("공용 지침", self.cards()[0]["title"])

    def test_rule_matching(self) -> None:
        files = WATCH["files"]
        for p in (".tower/PROTOCOL.md", ".tower/lanes/infra.md", "AGENTS.md", "./AGENTS.md",
                  ".claude/skills/review/references/patterns.md", "docs/glossary.md"):
            self.assertTrue(rulewatch.is_rule(p, files), p)
        for p in ("docs/AGENTS.md", ".tower/lanes/x/y.md", ".tower/tower.json", "agents.md"):
            self.assertFalse(rulewatch.is_rule(p, files), p)
        self.assertTrue(rulewatch.touches_rules(".tower", files))
        self.assertTrue(rulewatch.touches_rules(".claude/skills/review", files))
        self.assertFalse(rulewatch.touches_rules("src", files))


class RuleWatchConfigTest(unittest.TestCase):
    def test_absent_means_off_and_the_engine_never_starts_it(self) -> None:
        cfg = make_home()
        self.assertEqual(cfg.rule_watch, {})
        repo = Repo(cfg.home)
        repo.commit("AGENTS.md", "a\n", "start")
        clock = Clock()
        s = Store(cfg.db, clock=clock)
        e = Engine(cfg, s, FakeRuntime(s), lambda n: None, clock=clock, log=lambda _: None)
        e.tick()
        repo.commit("AGENTS.md", "b\n", "change")
        clock.tick(QUIET * 2)
        e.tick()
        self.assertIsNone(e.rules)
        self.assertEqual([it for it in s.items() if it["lane"] == TOWER], [])

    def test_defaults_and_errors(self) -> None:
        cfg = make_home(rule_watch={"files": ["AGENTS.md", "./CLAUDE.md"]})
        self.assertEqual(cfg.rule_watch, {"files": ["AGENTS.md", "CLAUDE.md"], "delay_minutes": 10, "cards_per_day": 3})
        for bad in ({"files": []}, {"delay_minutes": 5}, {"files": ["a"], "delay_minutes": -1},
                    {"files": ["a"], "typo": 1}, ["a"]):
            with self.assertRaises(config_mod.ConfigError, msg=str(bad)):
                make_home(rule_watch=bad)

    def test_the_engine_posts_through_its_own_watch(self) -> None:
        cfg = make_home(rule_watch=WATCH)
        repo = Repo(cfg.home)
        repo.commit("AGENTS.md", "a\n", "start")
        clock = Clock()
        s = Store(cfg.db, clock=clock)
        e = Engine(cfg, s, FakeRuntime(s), lambda n: None, clock=clock, log=lambda _: None)
        e.tick()
        repo.commit("AGENTS.md", "b\n", "memo #9 change")
        clock.tick(CHECK_SECONDS)
        e.tick()
        clock.tick(QUIET)
        e.tick()
        self.assertEqual(len([it for it in s.items() if it["lane"] == TOWER]), 1)


if __name__ == "__main__":
    unittest.main()


class MessageLanguagesTest(unittest.TestCase):
    def test_every_language_has_the_same_keys_and_placeholders_as_english(self) -> None:
        for table in (rulewatch.TEXT, rulewatch.WORDS):
            self.assertEqual(set(table), {"en", "ko", "ja", "zh", "es"})
            for lang, entry in table.items():
                with self.subTest(lang=lang):
                    self.assertEqual(set(entry), set(table["en"]))
        for lang, entry in rulewatch.TEXT.items():
            for key, text in entry.items():
                with self.subTest(lang=lang, key=key):
                    self.assertEqual(sorted(re.findall(r"\{\w+\}", text)), sorted(re.findall(r"\{\w+\}", rulewatch.TEXT["en"][key])))

    def test_a_reference_is_labelled_in_the_cards_language(self) -> None:
        self.assertEqual(rulewatch.ref_label("memo:7", "es"), "memo #7")
        self.assertEqual(rulewatch.ref_label("task:7", "ja"), "タスク #7")
        self.assertEqual(rulewatch.ref_label("card:7", "zh"), "卡片 #7")
