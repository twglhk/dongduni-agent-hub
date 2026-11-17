import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_hub import accounts
from agent_hub.config import ConfigError
from agent_hub.runtimes import claude_base
from agent_hub.store import HUB, INFRA, SCHEMA, Store
from tests.helpers import make_home
from tests.test_person_hubs import with_members

TABLES = ("account_holds", "account_asks", "account_seen", "session_accounts")


class AccountRuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.c = with_members(make_home(), "ana", "bo")
        self.s = Store(self.c.db)

    def task(self, **kw: object) -> dict:
        return {"requester": None, "hub": None, **kw}

    def card(self, assignee: str | None, decided_by: str | None) -> int:
        iid = self.s.add_item("docs", None, "decision", "t", "## a", [{"label": "A"}, {"label": "B"}], [],
                              "human", assignee=assignee)
        if decided_by:
            self.s.decide(iid, "ok", decided_by, "A", basis="test", hub_may_decide=True)
        return iid

    def test_hubs_belong_to_their_owner_and_the_default_hub_to_nobody(self) -> None:
        self.assertEqual(accounts.for_session(self.c, self.s, "hub-ana"), "ana")
        self.assertIsNone(accounts.for_session(self.c, self.s, HUB))

    def test_a_task_goes_requester_then_hub_owner_then_default(self) -> None:
        f = lambda **kw: accounts.for_session(self.c, self.s, "docs", task=self.task(**kw))
        self.assertEqual(f(requester="bo", hub="hub-ana"), "bo")
        self.assertEqual(f(hub="hub-ana"), "ana")
        self.assertIsNone(f())
        self.assertIsNone(f(requester="gone"))
        self.assertEqual(accounts.for_session(self.c, self.s, "docs#2", task=self.task(requester="ana")), "ana")
        self.assertIsNone(accounts.for_session(self.c, self.s, "docs"))

    def test_an_infra_memo_belongs_to_the_owner_of_the_hub_that_sent_it(self) -> None:
        for sender, want in (("hub-bo", "bo"), (HUB, None), ("ana@x.com", "ana"), ("docs", None)):
            mid = self.s.push(INFRA, sender, "memo", "please")
            with self.subTest(sender=sender):
                self.assertEqual(accounts.for_session(self.c, self.s, "infra#2", msg=self.s.message(mid)), want)
        self.assertIsNone(accounts.for_session(self.c, self.s, INFRA))

    def test_a_decision_goes_assignee_then_decider_by_name_or_email_then_default(self) -> None:
        cases = (("bo", "ana@x.com", "bo"), (None, "ana@x.com", "ana"), (None, "bo", "bo"),
                 (None, "hub-ana", "ana"), (None, "stranger@y.com", None), (None, None, None))
        for assignee, decider, want in cases:
            iid = self.card(assignee, decider)
            mid = self.s.push(INFRA, "tower", "decision", "line", None, iid)
            with self.subTest(assignee=assignee, decider=decider):
                self.assertEqual(accounts.for_message(self.c, self.s, self.s.message(mid)), want)
        mid = self.s.push(INFRA, "tower", "decision", "line", None, 9999)
        self.assertIsNone(accounts.for_message(self.c, self.s, self.s.message(mid)))

    def test_only_a_connected_account_folder_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "ana").mkdir()
            (root / "bo").mkdir()
            (root / "ana" / ".credentials.json").write_text("{}")
            self.assertEqual(accounts.config_dir("ana", root), root / "ana")
            self.assertIsNone(accounts.config_dir("bo", root))
            self.assertIsNone(accounts.config_dir("cy", root))
            self.assertIsNone(accounts.config_dir(None, root))


class ConfigKeyTest(unittest.TestCase):
    def test_off_by_default(self) -> None:
        self.assertFalse(make_home().claude_accounts)

    def test_on_with_header_or_token_sign_in(self) -> None:
        for mode in ({"mode": "header", "header": "X-User"}, {"mode": "token"}):
            with self.subTest(mode=mode["mode"]):
                self.assertTrue(make_home(claude_accounts=True, auth=mode).claude_accounts)

    def test_local_mode_refuses_it_with_a_reason(self) -> None:
        for extra in ({}, {"auth": {"mode": "local"}}):
            with self.subTest(extra=extra), self.assertRaisesRegex(ConfigError, "claude_accounts needs auth.mode header or token"):
                make_home(claude_accounts=True, **extra)

    def test_a_string_is_not_a_boolean(self) -> None:
        with self.assertRaises(ConfigError):
            make_home(claude_accounts="true", auth={"mode": "token"})


class ClaudeBaseTest(unittest.TestCase):
    def test_an_account_folder_wins_over_the_environment_and_the_default(self) -> None:
        self.assertEqual(claude_base("/a/ana"), Path("/a/ana"))
        self.assertEqual(claude_base(), claude_base(None))


class StateTablesTest(unittest.TestCase):
    def old_db(self) -> Path:
        path = make_home().db
        path.parent.mkdir(parents=True, exist_ok=True)
        old = SCHEMA
        for t in TABLES:
            old = re.sub(rf"CREATE TABLE IF NOT EXISTS {t}\(.*?\n\);\n", "", old, flags=re.S)
        con = sqlite3.connect(path)
        con.executescript(old)
        con.execute("INSERT INTO tasks(lane, title, brief, state, created) VALUES('docs', 'old', 'b', 'queued', 1)")
        con.commit()
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        self.assertTrue(tables.isdisjoint(TABLES))
        return path

    def test_an_old_database_gets_the_account_tables_and_keeps_its_rows(self) -> None:
        s = Store(self.old_db())
        self.assertEqual(s.task(1)["title"], "old")
        self.assertEqual(s.account_holds(), [])
        self.assertFalse(s.account_seen("ana"))
        s.mark_account_seen("ana")
        self.assertTrue(s.account_seen("ana"))

    def test_holds_asks_and_session_accounts_round_trip(self) -> None:
        s = Store(self.old_db())
        self.assertTrue(s.hold_account_work("task:1", "ana", "docs", "t"))
        self.assertFalse(s.hold_account_work("task:1", "ana", "docs", "t"))
        self.assertEqual(s.account_hold("task:1")["member"], "ana")
        s.drop_account_holds(["task:1"])
        self.assertEqual(s.account_holds(), [])
        s.set_account_ask("ana", 7)
        s.set_account_ask("ana", 8)
        self.assertEqual(s.account_ask("ana"), 8)
        s.drop_account_ask("ana")
        self.assertIsNone(s.account_ask("ana"))
        s.set_session_account("docs", "s1", "ana")
        self.assertEqual(s.session_account("docs", "s1"), "ana")
        self.assertIsNone(s.session_account("docs", "s2"))
        self.assertIsNone(s.session_account("docs", None))

    def test_removing_a_member_forgets_their_account_rows(self) -> None:
        s = Store(self.old_db())
        s.add_member("ana", "member", "ana@x.com")
        s.mark_account_seen("ana")
        s.hold_account_work("hub:hub-ana", "ana", "hub-ana", "")
        s.set_account_ask("ana", 3)
        s.remove_member("ana")
        self.assertFalse(s.account_seen("ana"))
        self.assertEqual(s.account_holds(), [])
        self.assertIsNone(s.account_ask("ana"))


if __name__ == "__main__":
    unittest.main()
