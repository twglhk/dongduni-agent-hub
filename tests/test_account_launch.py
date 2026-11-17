import builtins
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_hub import accounthold, accounts
from agent_hub.engine import Engine, infra_waits
from agent_hub.runtimes import Runtimes
from agent_hub.session_view import session_view
from agent_hub.store import HUB, HUMAN, INFRA, Store
from agent_hub.visibility import Gate
from agent_hub.web import lanes_view
from tests.helpers import Clock, FakeRuntime, make_home
from tests.test_person_hubs import ROOT, with_members

SECRET = "sk-ant-oat01-NEVER-READ-THIS"
LANES = {"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"}, "docs": {"runtime": "claude"},
         "web": {"runtime": "codex"}}
ON = {"claude_accounts": True, "auth": {"mode": "token"}}


class AccountRuntime(FakeRuntime):
    def __init__(self, store: Store):
        super().__init__(store)
        self.accounts: dict[str, Path | None] = {}

    def open(self, lane: str, prompt: str, model: str | None = None, effort: str | None = None,
             account: Path | None = None) -> str:
        self.accounts[lane] = account
        return super().open(lane, prompt, model, effort)


class Folders:
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="accounts-"))
        self.logged_out: set[str] = set()

    def connect(self, member: str) -> Path:
        d = self.root / accounts.folder_name(member)
        d.mkdir(exist_ok=True)
        (d / accounts.CREDENTIALS).write_text(json.dumps({"token": SECRET}))
        self.logged_out.discard(member)
        accounts._probed.pop(str(d), None)
        return d

    def end(self, member: str) -> None:
        (self.root / accounts.folder_name(member) / accounts.CREDENTIALS).unlink()

    def probe(self, d: Path) -> bool:
        return d.name not in self.logged_out


class Base(unittest.TestCase):
    extra: dict = ON

    def setUp(self) -> None:
        self.cfg = with_members(make_home(dict(LANES), **self.extra), "ana", "bo")
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.f = Folders()
        self.rt = AccountRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda _: None, clock=self.clock, log=lambda _: None)
        self.e.accounts = accounthold.Gate(self.cfg, self.s, lambda _: None, root=self.f.root, probe=self.f.probe)
        for h in self.cfg.hubs():
            self.s.set_lane(h, enabled=1)

    def launch(self, lane: str, **kw: object) -> accounts.Launch:
        return accounts.launch_for(self.cfg, self.s, lane, root=self.f.root, probe=self.f.probe, **kw)

    def cards(self, member: str) -> list[dict]:
        return [it for it in self.s.items() if it["assignee"] == member and it["audience"] == HUMAN]


class WhoResolvesTest(Base):
    def test_each_session_resolves_to_its_persons_folder_or_the_shared_account(self) -> None:
        ana = self.f.connect("ana")
        self.assertEqual(self.launch("hub-ana"), accounts.Launch("ana", ana))
        self.assertEqual(self.launch(HUB), accounts.Launch())
        self.assertEqual(self.launch("hub-bo"), accounts.Launch())
        self.assertEqual(self.launch("docs", task={"requester": "ana", "hub": None}).config_dir, ana)
        self.assertEqual(self.launch("docs", task={"requester": None, "hub": "hub-ana"}).config_dir, ana)
        self.assertEqual(self.launch("docs", task={"requester": None, "hub": None}), accounts.Launch())
        self.assertEqual(self.launch("web", task={"requester": "ana", "hub": None}), accounts.Launch())
        mid = self.s.push(INFRA, "hub-ana", "memo", "please")
        self.assertEqual(self.launch("infra#2", msg=self.s.message(mid)).config_dir, ana)

    def test_a_requester_beats_the_hub_that_queued_the_task(self) -> None:
        ana, bo = self.f.connect("ana"), self.f.connect("bo")
        task = {"requester": "bo", "hub": "hub-ana"}
        self.assertEqual(self.launch("docs", task=task).config_dir, bo)
        self.assertNotEqual(self.launch("docs", task=task).config_dir, ana)

    def test_a_decision_runs_on_the_account_of_the_cards_person(self) -> None:
        self.f.connect("bo")
        iid = self.s.add_item("docs", None, "decision", "t", "## a", [{"label": "A"}, {"label": "B"}], [], "human", assignee="bo")
        self.s.decide(iid, "ok", "ana", "A", basis="test", hub_may_decide=True)
        mid = self.s.push(INFRA, "tower", "decision", "line", None, iid)
        self.assertEqual(self.launch(INFRA, msg=self.s.message(mid)).member, "bo")

    def test_a_login_that_ended_is_held_never_shared(self) -> None:
        self.f.connect("ana")
        self.assertEqual(accounts.state("ana", self.s, self.f.root, self.f.probe), accounts.CONNECTED)
        self.f.logged_out.add("ana")
        accounts._probed.clear()
        self.assertEqual(self.launch("hub-ana"), accounts.Launch("ana", None, True))
        self.f.end("ana")
        self.assertEqual(self.launch("hub-ana"), accounts.Launch("ana", None, True))
        self.assertEqual(accounts.state("bo", self.s, self.f.root, self.f.probe), accounts.NONE)

    def test_a_cli_that_cannot_tell_counts_as_signed_in(self) -> None:
        self.f.connect("ana")
        self.assertEqual(accounts.state("ana", self.s, self.f.root, lambda d: None), accounts.CONNECTED)

    def test_a_member_with_an_odd_name_gets_a_folder_of_their_own(self) -> None:
        cfg = with_members(make_home(dict(LANES), **ON), "홍길동", "Ana")
        s = Store(cfg.db)
        d = self.f.connect("홍길동")
        hub = cfg.hub_of("홍길동")
        self.assertEqual(accounts.launch_for(cfg, s, hub, root=self.f.root, probe=self.f.probe).config_dir, d)
        self.assertEqual(accounts.launch_for(cfg, s, cfg.hub_of("Ana"), root=self.f.root, probe=self.f.probe), accounts.Launch())


class OffIsToday(Base):
    extra: dict = {}

    def test_a_connected_folder_is_ignored_and_nothing_is_recorded(self) -> None:
        self.f.connect("ana")
        self.assertEqual(self.launch("hub-ana"), accounts.Launch())
        self.s.add_task("docs", "for ana", "brief", requester="ana")
        self.e.tick()
        self.assertEqual(set(self.rt.accounts.values()), {None})
        self.assertEqual(self.s.account_holds(), [])
        with self.s.conn() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM session_accounts").fetchone()[0], 0)

    def test_a_login_that_ended_holds_nothing(self) -> None:
        self.f.connect("ana")
        accounts.state("ana", self.s, self.f.root, self.f.probe)
        self.f.end("ana")
        t1 = self.s.add_task("docs", "for ana", "brief", requester="ana")
        self.e.tick()
        self.assertEqual(self.s.task(t1)["state"], "running")
        self.assertEqual(self.cards("ana"), [])

    def test_the_board_and_the_sessions_carry_no_account_fields(self) -> None:
        self.f.connect("ana")
        self.e.tick()
        for row in lanes_view(self.cfg, self.s, {}):
            self.assertNotIn("account", row)
            self.assertNotIn("held", row)
        self.assertNotIn("account", session_view(self.cfg, self.s, "docs"))


class EngineLaunchTest(Base):
    def test_connected_hubs_and_tasks_open_on_their_own_folder(self) -> None:
        ana = self.f.connect("ana")
        t1 = self.s.add_task("docs", "for ana", "brief", requester="ana")
        self.e.tick()
        self.assertEqual(self.rt.accounts, {"docs": ana, HUB: None, "hub-ana": ana, "hub-bo": None})
        self.assertEqual(self.s.session_account("hub-ana", self.s.lane("hub-ana")["session"]), "ana")
        self.assertIsNone(self.s.session_account(HUB, self.s.lane(HUB)["session"]))
        self.assertEqual(self.s.task(t1)["state"], "running")

    def test_never_connected_and_nobodys_work_use_the_shared_account(self) -> None:
        self.s.add_task("docs", "for bo", "brief", requester="bo")
        self.e.tick()
        self.assertEqual(set(self.rt.accounts.values()), {None})
        self.assertEqual(self.cards("bo"), [])

    def test_an_ended_login_holds_everything_with_one_card_and_reconnecting_starts_it(self) -> None:
        self.f.connect("ana")
        accounts.state("ana", self.s, self.f.root, self.f.probe)
        self.f.end("ana")
        t1 = self.s.add_task("docs", "first for ana", "brief", requester="ana")
        self.s.add_task("docs", "second for ana", "brief", requester="ana")
        mid = self.s.push(INFRA, "hub-ana", "memo", "[fix the board] please")
        for _ in range(3):
            self.e.tick()
            self.clock.tick(5)
        self.assertEqual({lane for lane, _ in self.rt.opened}, {HUB, "hub-bo"})
        self.assertEqual(self.s.task(t1)["state"], "queued")
        cards = self.cards("ana")
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["lane"], "hub-ana")
        body = cards[0]["body"]
        self.assertIn(f"task #{t1}", body)
        self.assertIn(f"infra memo #{mid}", body)
        self.assertIn("ana's hub", body)
        self.assertEqual(accounthold.lane_line(self.s, "docs"), "waiting for ana to sign in again · 2 held")
        self.assertEqual(accounthold.lane_line(self.s, "hub-ana"), "waiting for ana to sign in again")
        self.assertEqual(self.e.conflict(self.s.task(t1)), "waiting for ana to sign in again")
        why = dict((m["id"], b) for m, b in infra_waits(self.s, self.cfg.infra_sessions(), self.clock()))[mid]
        self.assertEqual(why[0], "signin_hold")
        ana = self.f.connect("ana")
        self.e.tick()
        self.clock.tick(5)
        self.e.tick()
        self.assertEqual(self.rt.accounts.get("hub-ana"), ana)
        self.assertEqual(self.rt.accounts.get("docs"), ana)
        self.assertEqual(self.rt.accounts.get(INFRA), ana)
        self.assertEqual(self.s.task(t1)["state"], "running")
        self.assertEqual(self.s.account_holds(), [])
        it = self.s.item(cards[0]["id"])
        self.assertEqual(it["state"], "decided")
        self.assertIsNotNone(it["delivered"])
        self.assertEqual(len(self.cards("ana")), 1)

    def test_a_held_memo_is_not_taken_again_every_tick_and_others_pass_it(self) -> None:
        self.f.connect("ana")
        accounts.state("ana", self.s, self.f.root, self.f.probe)
        self.f.end("ana")
        held = self.s.push(INFRA, "hub-ana", "memo", "[ana's] please")
        other = self.s.push(INFRA, "hub-bo", "memo", "[bo's] please")
        for _ in range(3):
            self.e.tick()
            self.clock.tick(5)
        self.assertIsNone(self.s.message(held)["delivered"])
        self.assertIsNotNone(self.s.message(other)["delivered"])
        works = [r["work"] for r in self.s.account_holds()]
        self.assertIn(f"memo:{held}", works)
        self.assertNotIn(f"memo:{other}", works)

    def test_a_disconnect_noticed_by_the_poll_alone_still_holds(self) -> None:
        self.f.connect("ana")
        self.e.accounts.poll()
        self.f.end("ana")
        self.s.add_task("docs", "for ana", "brief", requester="ana")
        self.e.tick()
        self.assertNotIn("docs", self.rt.accounts)
        self.assertEqual(len(self.cards("ana")), 1)

    def test_a_running_session_keeps_the_account_it_opened_with(self) -> None:
        ana = self.f.connect("ana")
        tid = self.s.add_task("docs", "for ana", "brief", requester="ana")
        self.e.tick()
        self.f.end("ana")
        self.rt.st["docs"] = "busy"
        for _ in range(3):
            self.clock.tick(5)
            self.e.tick()
        self.assertNotIn("docs", self.rt.closed)
        self.assertEqual(self.s.task(tid)["state"], "running")
        self.rt.st["docs"] = "closed"
        self.e.tick()
        self.assertEqual([lane for lane, _ in self.rt.opened].count("docs"), 1)
        self.assertEqual(self.s.task(tid)["respawns"], 0)
        self.f.connect("ana")
        self.e.tick()
        self.clock.tick(5)
        self.e.tick()
        self.assertEqual([lane for lane, _ in self.rt.opened].count("docs"), 2)
        self.assertEqual(self.rt.accounts["docs"], ana)

    def test_a_linked_task_of_another_person_gets_a_fresh_session(self) -> None:
        self.f.connect("ana")
        first = self.s.add_task("docs", "for ana", "brief", requester="ana")
        self.e.tick()
        self.s.update_task(first, state="closed", closed=self.clock())
        self.s.set_lane("docs", closing=self.clock())
        nxt = self.s.add_task("docs", "for bo", f"after: #{first}\nbrief", requester="bo")
        self.rt.st["docs"] = "idle"
        self.clock.tick(60)
        self.e.tick()
        self.assertEqual(self.rt.sent, [s for s in self.rt.sent if "continues it" not in s[2]])
        self.clock.tick(5)
        self.e.tick()
        self.assertEqual(self.s.task(nxt)["state"], "running")
        self.assertIsNone(self.rt.accounts["docs"])


class RuntimeEnvTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cfg = make_home({"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"},
                              "win": {"runtime": "claude", "terminal": "tmux"}})
        self.s = Store(self.cfg.db)
        self.rt = Runtimes(self.cfg, self.s)
        self.folder = Path(tempfile.mkdtemp()) / "ana"

    def test_a_member_session_gets_their_folder_and_no_other_sign_in(self) -> None:
        a = self.rt.adapters["hub"]
        shared = {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "a", "CLAUDE_CODE_OAUTH_TOKEN": "t",
                  "CLAUDE_CODE_USE_BEDROCK": "1"}
        with mock.patch.dict(os.environ, shared):
            a.set_opts(None, None, self.folder)
            env = a.env()
            self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(self.folder))
            for k in shared:
                self.assertNotIn(k, env)
            self.assertNotEqual(os.environ.get("CLAUDE_CONFIG_DIR"), str(self.folder))
            self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "k")
            a.set_opts("opus", None)
            env = a.env()
            self.assertNotIn("CLAUDE_CONFIG_DIR", env)
            self.assertEqual(env["ANTHROPIC_API_KEY"], "k")

    def test_a_lane_env_sign_in_variable_is_dropped_for_a_member_session_too(self) -> None:
        a = self.rt.adapters["hub"]
        a.lane.env["ANTHROPIC_API_KEY"] = "lane-key"
        a.set_opts(None, None, self.folder)
        self.assertNotIn("ANTHROPIC_API_KEY", a.env())
        a.set_opts(None, None)
        self.assertEqual(a.env()["ANTHROPIC_API_KEY"], "lane-key")

    def test_the_process_a_member_session_starts_gets_the_folder_in_its_environment_only(self) -> None:
        a = self.rt.adapters["hub"]
        a.set_opts(None, None, self.folder)
        seen: list[tuple[list[str], dict[str, str]]] = []

        class Child:
            pid = 1

            def poll(self) -> None:
                return None

        def spawn(argv: list[str], **kw: object) -> Child:
            seen.append((argv, dict(kw["env"])))
            return Child()

        with mock.patch("agent_hub.runtimes.adapter.subprocess.Popen", spawn):
            a._spawn(["claude", "-p", "do it"])
        argv, env = seen[0]
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(self.folder))
        self.assertNotIn(str(self.folder), " ".join(argv))

    def test_a_window_launcher_exports_the_folder_and_drops_sign_in_variables(self) -> None:
        a = self.rt.adapters["win"]
        a.set_opts(None, None, self.folder)
        with mock.patch.object(type(a), "_host") as host:
            host.return_value.open.return_value = "h"
            a._launch(["claude"])
        text = (self.cfg.state_dir / "term" / "win.sh").read_text()
        self.assertIn(f"export CLAUDE_CONFIG_DIR={shlex.quote(str(self.folder))}", text)
        self.assertIn("unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN", text)
        a.set_opts(None, None)
        with mock.patch.object(type(a), "_host") as host:
            host.return_value.open.return_value = "h"
            a._launch(["claude"])
        text = (self.cfg.state_dir / "term" / "win.sh").read_text()
        self.assertNotIn("CLAUDE_CONFIG_DIR", text)
        self.assertNotIn("unset", text)

    def test_a_lane_env_key_in_a_member_window_never_reaches_the_launcher(self) -> None:
        a = self.rt.adapters["win"]
        a.lane.env["ANTHROPIC_API_KEY"] = SECRET
        a.set_opts(None, None, self.folder)
        with mock.patch.object(type(a), "_host") as host:
            host.return_value.open.return_value = "h"
            a._launch(["claude"])
        term = self.cfg.state_dir / "term"
        for p in term.iterdir():
            self.assertNotIn(SECRET, p.read_text(encoding="utf-8", errors="replace"), p.name)

    def test_the_transcript_is_read_from_the_account_folder(self) -> None:
        a = self.rt.adapters["win"]
        self.s.set_lane("win", session="sid1")
        a.set_opts(None, None, self.folder)
        self.assertTrue(a._transcript_path().startswith(str(self.folder / "projects")))
        a.set_opts(None, None)
        self.assertFalse(a._transcript_path().startswith(str(self.folder)))


class RunInfoTest(unittest.TestCase):
    def test_the_model_a_member_session_runs_with_is_read_from_their_account_folder(self) -> None:
        from agent_hub import sessinfo
        from tests.test_sessinfo import turn
        cfg = make_home({"hub": {"runtime": "claude"}, "docs": {"runtime": "claude"}})
        s = Store(cfg.db)
        folder = Path(tempfile.mkdtemp(prefix="member-"))
        (folder / "projects" / "-p").mkdir(parents=True)
        (folder / "projects" / "-p" / "sid9.jsonl").write_text(turn("claude-opus-5-5", "high") + "\n", encoding="utf-8")
        rt = Runtimes(cfg, s)
        rt.adapters["docs"].set_opts("opus", None, folder)
        got = sessinfo.for_lane(cfg, s, "docs", "claude", "opus", "sid9")
        self.assertEqual((got["model"], got["effort"]), ("Opus 5.5", "high"))


class NoCredentialReadTest(Base):
    def test_no_sign_in_file_is_opened_and_no_token_is_written_anywhere(self) -> None:
        self.f.connect("ana")
        opened: list[str] = []
        real_open, real_io_open = builtins.open, io.open

        def spy(file, *a, **kw):
            opened.append(str(file))
            return real_open(file, *a, **kw)

        def spy_io(file, *a, **kw):
            opened.append(str(file))
            return real_io_open(file, *a, **kw)

        with mock.patch("builtins.open", spy), mock.patch("io.open", spy_io):
            self.s.add_task("docs", "for ana", "brief", requester="ana")
            self.e.tick()
            self.f.end("ana")
            self.s.add_task("docs", "again", "brief", requester="ana")
            self.clock.tick(5)
            self.e.tick()
            dump = json.dumps([self.s.items(), self.s.tasks(), self.rt.opened, self.s.account_holds()], default=str)
        self.assertEqual([p for p in opened if p.endswith(accounts.CREDENTIALS)], [])
        self.assertNotIn(SECRET, dump)
        tower = self.cfg.home / ".tower"
        for p in tower.rglob("*"):
            if p.is_file() and p.suffix != ".db":
                self.assertNotIn(SECRET, p.read_text(encoding="utf-8", errors="replace"), p)
        self.assertNotIn(SECRET.encode(), self.cfg.db.read_bytes())

    def test_the_cli_probe_keeps_only_logged_in(self) -> None:
        d = self.f.connect("ana")
        with mock.patch("agent_hub.claudelogin.status_json", return_value={"loggedIn": False, "email": "a@x"}) as st:
            self.assertFalse(accounts.cli_probe(d))
            st.assert_called_once_with(d, d.parent)
        with mock.patch("agent_hub.claudelogin.status_json", return_value={}):
            self.assertIsNone(accounts.cli_probe(d))


class BoardFieldsTest(Base):
    def setUp(self) -> None:
        super().setUp()
        self.f.connect("ana")
        self.e.tick()
        self.s.hold_account_work("task:99", "ana", "docs", "held for ana")
        self.s.set_lane("hub-ana", status="idle")

    def gate(self, name: str, role: str = "member") -> Gate:
        return Gate.of(self.cfg, self.s, {"name": name, "role": role})

    def rows(self, g: Gate | None) -> dict:
        return {r["lane"]: r for r in lanes_view(self.cfg, self.s, {}, g)}

    def test_the_person_and_the_owner_see_the_account_a_session_runs_on(self) -> None:
        for g in (self.gate("ana"), self.gate("bo", "owner"), None):
            with self.subTest(viewer=g and g.name):
                self.assertEqual(self.rows(g)["hub-ana"]["account"], "ana")

    def test_another_member_learns_only_that_it_is_someone_elses_account(self) -> None:
        row = self.rows(self.gate("bo"))["hub-ana"]
        self.assertEqual(row["account"], "")
        self.assertIsNone(self.rows(self.gate("bo"))[HUB]["account"])

    def test_held_work_shows_to_its_person_and_the_owner_only(self) -> None:
        self.assertIn("ana", self.rows(self.gate("ana"))["docs"]["held"])
        self.assertIn("ana", self.rows(self.gate("cy", "owner"))["docs"]["held"])
        self.assertIsNone(self.rows(self.gate("bo"))["docs"]["held"])
        self.assertIsNone(session_view(self.cfg, self.s, "docs", self.gate("bo"))["held"])
        self.assertIn("ana", session_view(self.cfg, self.s, "docs", self.gate("ana"))["held"])

    def test_the_session_sheet_names_the_account_of_a_live_session_for_its_person_only(self) -> None:
        v = session_view(self.cfg, self.s, "hub-ana", self.gate("ana"))
        self.assertEqual((v["account"], v["runtime"]), ("ana", "claude"))
        self.assertIsNone(session_view(self.cfg, self.s, "hub-ana", self.gate("bo")))
        self.s.set_lane(HUB, status="idle")
        self.assertIsNone(session_view(self.cfg, self.s, HUB, self.gate("bo"))["account"])


class StatusLineTest(Base):
    def test_dah_status_says_who_the_held_work_waits_for(self) -> None:
        self.s.hold_account_work("task:1", "ana", "docs", "t")
        env = {**os.environ, "TOWER_HOME": str(self.cfg.home), "PYTHONPATH": str(ROOT)}
        p = subprocess.run([sys.executable, "-m", "agent_hub", "status"], cwd=self.cfg.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        line = next(x for x in p.stdout.splitlines() if x.startswith("docs "))
        self.assertIn("waiting for ana to sign in again", line)

    def test_nothing_is_added_when_the_option_is_off(self) -> None:
        cfg = with_members(make_home(dict(LANES)), "ana", "bo")
        Store(cfg.db).hold_account_work("task:1", "ana", "docs", "t")
        env = {**os.environ, "TOWER_HOME": str(cfg.home), "PYTHONPATH": str(ROOT)}
        p = subprocess.run([sys.executable, "-m", "agent_hub", "status"], cwd=cfg.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        self.assertNotIn("sign in again", p.stdout)


if __name__ == "__main__":
    unittest.main()
