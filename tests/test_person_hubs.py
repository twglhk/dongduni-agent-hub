import json
import os
import sqlite3
import subprocess
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from agent_hub import config as config_mod
from agent_hub.cli import close_changed
from agent_hub.engine import BATCH_SECONDS, HUB_STUCK_SECONDS, Engine
from agent_hub.store import HUB, HUMAN, INFRA, SCHEMA, Store, StoreError, is_hub_name
from agent_hub.web import make_handler
from tests.helpers import Clock, FakeRuntime, make_home
from tests.test_web import POST, Board

ROOT = Path(__file__).resolve().parents[1]
OPTIONS = [{"label": "x", "desc": ""}, {"label": "y", "desc": ""}]


def with_members(cfg: config_mod.Config, *names: str, cap: int | None = 3, **options: object) -> config_mod.Config:
    s = Store(cfg.db)
    for n in names:
        s.add_member(n, "member", f"{n.lower().replace(' ', '.')}@x.com")
    path = cfg.home / ".tower" / "tower.json"
    data = json.loads(path.read_text())
    if cap is not None:
        data["person_hubs"] = {"max": cap, **options}
    path.write_text(json.dumps(data))
    return config_mod.load(cfg.home)


class HubIdTest(unittest.TestCase):
    def test_ids_are_lane_safe_whatever_the_member_is_called(self) -> None:
        pid = config_mod.person_hub_id
        self.assertEqual(pid("Bo Kim"), "hub-bo-kim")
        self.assertEqual(pid("a.b@c"), "hub-a-b-c")
        self.assertEqual(pid("../x/../y"), "hub-x-y")
        self.assertEqual(pid(""), "")
        self.assertEqual(pid("  "), "")

    def test_a_name_with_no_ascii_letter_gets_a_stable_hashed_id(self) -> None:
        a, b = config_mod.person_hub_id("홍길동"), config_mod.person_hub_id("김철수")
        self.assertRegex(a, r"^hub-u[0-9a-f]{8}$")
        self.assertNotEqual(a, b)
        self.assertEqual(a, config_mod.person_hub_id("홍길동"))

    def test_every_id_passes_the_lane_id_rule(self) -> None:
        for name in ("Ana", "bo kim", "홍길동", "x" * 100, "a/b\\c", "ünï", "-_-", "hub"):
            lid = config_mod.person_hub_id(name)
            if lid:
                self.assertTrue(lid.replace("-", "").replace("_", "").isalnum(), lid)
                self.assertLessEqual(len(lid), len(config_mod.PERSON_HUB_PREFIX) + config_mod.HUB_SLUG_MAX)
                self.assertTrue(is_hub_name(lid))

    def test_auto_names_stay_unique_by_name_and_by_hub_id(self) -> None:
        name = config_mod.auto_member_name
        self.assertEqual(name("John.Shin@rollingseeds.com", []), "john.shin")
        self.assertEqual(name("john.shin@other.com", ["john.shin"]), "john.shin-2")
        self.assertEqual(name("john-shin@x.com", ["john.shin"]), "john-shin-2")
        self.assertEqual(name("hub@x.com", []), "hub-2")


class ConfigTest(unittest.TestCase):
    def test_one_hub_per_member_up_to_the_cap(self) -> None:
        cfg = with_members(make_home(), "ana", "Bo Kim", "cy", cap=2)
        self.assertEqual(cfg.hubs(), ["hub", "hub-ana", "hub-bo-kim"])
        self.assertEqual(cfg.hub_overflow, ["cy"])
        ln = cfg.all_lanes["hub-ana"]
        self.assertEqual((ln.kind, ln.owner, ln.runtime), ("hub", "ana", cfg.lanes[HUB].runtime))
        self.assertEqual(cfg.instructions("hub-ana"), cfg.instructions(HUB))
        self.assertEqual((cfg.hub_of("ana"), cfg.hub_of("cy"), cfg.hub_of("nobody")), ("hub-ana", HUB, HUB))
        self.assertNotIn("hub-ana", cfg.team())

    def test_two_members_whose_names_make_one_id_each_get_a_hub(self) -> None:
        cfg = with_members(make_home(), "Ana", "ana ")
        ids = [h for h in cfg.hubs() if h != HUB]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len({cfg.hub_of("Ana"), cfg.hub_of("ana ")}), 2)

    def test_a_person_hub_works_in_the_workspace(self) -> None:
        cfg = with_members(make_home(), "ana")
        self.assertEqual(cfg.lane_dir("hub-ana"), cfg.workdir)

    def test_a_person_hub_has_its_own_opencode_port(self) -> None:
        cfg = with_members(make_home({"hub": {"runtime": "opencode"}, "infra": {"runtime": "opencode"},
                                      "docs": {"runtime": "opencode"}}), "ana", "bo")
        ports = [ln.port for ln in cfg.all_lanes.values() if ln.port]
        self.assertEqual(len(ports), len(set(ports)))

    def test_a_person_hub_has_the_hub_context_limit(self) -> None:
        cfg = make_home({"hub": {"runtime": "claude", "context_limit_k": 90}, "infra": {"runtime": "claude"},
                         "docs": {"runtime": "claude"}})
        cfg = with_members(cfg, "ana")
        self.assertEqual((cfg.hub_context_limit(HUB), cfg.hub_context_limit("hub-ana")), (90, 90))

    def test_off_by_default_and_when_false(self) -> None:
        cfg = with_members(make_home(), "ana", cap=None)
        self.assertEqual((cfg.hubs(), cfg.hub_cap), (["hub"], 0))
        path = cfg.home / ".tower" / "tower.json"
        data = json.loads(path.read_text())
        data["person_hubs"] = False
        path.write_text(json.dumps(data))
        self.assertEqual(config_mod.load(cfg.home).hubs(), ["hub"])

    def test_true_turns_it_on_with_the_default_cap(self) -> None:
        cfg = make_home()
        path = cfg.home / ".tower" / "tower.json"
        data = json.loads(path.read_text())
        data["person_hubs"] = True
        path.write_text(json.dumps(data))
        self.assertEqual(config_mod.load(cfg.home).hub_cap, config_mod.DEFAULT_HUB_CAP)

    def test_bad_options_are_refused(self) -> None:
        for bad in ({"max": 0}, {"max": True}, {"max": "many"}, {"auto": "yes"}, {"domains": [1]}, {"cap": 2}, "on", 3):
            cfg = make_home()
            path = cfg.home / ".tower" / "tower.json"
            data = json.loads(path.read_text())
            data["person_hubs"] = bad
            path.write_text(json.dumps(data))
            with self.assertRaises(config_mod.ConfigError, msg=repr(bad)):
                config_mod.load(cfg.home)

    def test_a_lane_id_that_looks_like_a_person_hub_is_refused(self) -> None:
        cfg = make_home({"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"}, "hub-docs": {"runtime": "claude"}})
        path = cfg.home / ".tower" / "tower.json"
        data = json.loads(path.read_text())
        data["person_hubs"] = True
        path.write_text(json.dumps(data))
        with self.assertRaises(config_mod.ConfigError):
            config_mod.load(cfg.home)

    def test_a_member_named_like_a_hub_is_refused(self) -> None:
        s = Store(make_home().db)
        for name in ("hub", "hub-ana"):
            with self.assertRaises(StoreError):
                s.add_member(name)


class EngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = with_members(make_home(), "ana", "bo")
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda _: None, clock=self.clock, log=lambda _: None)
        for h in self.cfg.hubs():
            self.s.set_lane(h, enabled=1)

    def test_every_hub_is_kept_open_and_reopened(self) -> None:
        self.e.tick()
        self.assertEqual(sorted(lane for lane, _ in self.rt.opened), ["hub", "hub-ana", "hub-bo"])
        self.assertIn("the hub of ana", dict(self.rt.opened)["hub-ana"])
        self.assertNotIn("the hub of", dict(self.rt.opened)[HUB])
        self.rt.st["hub-ana"] = "closed"
        self.clock.tick(5)
        self.e.tick()
        self.assertEqual([lane for lane, _ in self.rt.opened].count("hub-ana"), 2)

    def test_a_hub_that_keeps_dying_backs_off_on_its_own(self) -> None:
        for _ in range(8):
            self.e.tick()
            self.rt.st["hub-bo"] = "closed"
            self.clock.tick(5)
        self.assertEqual([lane for lane, _ in self.rt.opened].count("hub-bo"), 3)
        self.assertEqual([lane for lane, _ in self.rt.opened].count("hub-ana"), 1)

    def test_task_news_goes_to_the_hub_that_queued_it(self) -> None:
        tid = self.s.add_task("docs", "t", "brief", hub="hub-ana")
        self.assertEqual(self.s.hub_for_task(tid), "hub-ana")
        self.assertEqual(self.s.hub_for_task(self.s.add_task("docs", "u", "brief")), HUB)
        iid = self.s.add_item("docs", tid, "decision", "pick", "## a", OPTIONS, [], audience=HUMAN)
        self.s.update_task(tid, state="running")
        self.s.decide(iid, "hold", "ana")
        self.e.route_decisions()
        self.assertEqual([m["kind"] for m in self.s.pending("hub-ana")], ["decision"])
        self.assertEqual(self.s.pending(HUB), [])

    def test_a_follow_up_task_keeps_the_hub_and_its_news_goes_there(self) -> None:
        tid = self.s.add_task("docs", "t", "brief", hub="hub-ana", requester="ana")
        self.s.update_task(tid, state="running")
        iid = self.s.add_item("docs", tid, "decision", "pick", "## a", OPTIONS, [], audience=HUMAN)
        self.s.decide(iid, "ok", "ana", "x")
        self.e.route_decisions()
        follow = self.s.tasks()[0]
        self.assertNotEqual(follow["id"], tid)
        self.assertEqual((follow["hub"], follow["requester"]), ("hub-ana", "ana"))
        self.assertEqual([m["kind"] for m in self.s.pending("hub-ana")], ["decision"])
        self.assertEqual(self.s.pending(HUB), [])

    def test_a_hubs_own_card_decision_goes_back_to_that_hub_unless_it_decided_it(self) -> None:
        mine = self.s.add_item("hub-ana", None, "decision", "pick", "## a", OPTIONS, [], audience=HUMAN)
        self.s.decide(mine, "ok", "ana", "x")
        self.e.route_decisions()
        self.assertEqual([m["kind"] for m in self.s.pending("hub-ana")], ["decision"])
        own = self.s.add_item("hub-bo", None, "decision", "pick", "## a", OPTIONS, [], audience=HUB)
        self.s.decide(own, "ok", "hub-bo", "x", basis="rule")
        self.e.route_decisions()
        self.assertEqual(self.s.pending("hub-bo"), [])

    def test_news_batches_per_hub(self) -> None:
        self.e.tick()
        for h in self.cfg.hubs():
            self.rt.st[h] = "idle"
        self.s.push("hub-bo", "bo", "memo", "make a poster")
        self.clock.tick(BATCH_SECONDS + 5)
        self.e.tick()
        self.clock.tick(5)
        self.e.tick()
        self.assertEqual([(lane, "make a poster" in text) for lane, _, text in self.rt.sent], [("hub-bo", True)])

    def test_a_busy_hub_is_reported_on_its_own_lane(self) -> None:
        self.e.tick()
        self.s.push("hub-ana", "ana", "memo", "ask")
        self.rt.st["hub-ana"] = "busy"
        self.clock.tick(HUB_STUCK_SECONDS + 1)
        self.e.tick()
        items = self.s.items(state="open", audience=HUMAN)
        self.assertEqual([(it["lane"], "(hub-ana)" in it["title"]) for it in items], [("hub-ana", True)])

    def test_a_person_hub_hands_over_at_its_own_context_limit(self) -> None:
        cfg = with_members(make_home({"hub": {"runtime": "claude", "context_limit_k": 50}, "infra": {"runtime": "claude"},
                                      "docs": {"runtime": "claude"}}), "ana")
        s = Store(cfg.db, clock=self.clock)
        rt = FakeRuntime(s)
        e = Engine(cfg, s, rt, lambda _: None, clock=self.clock, log=lambda _: None)
        for h in cfg.hubs():
            s.set_lane(h, enabled=1)
        e.tick()
        rt.st["hub-ana"] = "idle"
        s.set_lane("hub-ana", session="s1")
        rt.ctx["hub-ana"] = 60
        for _ in range(8):
            self.clock.tick(5)
            e.tick()
        self.assertEqual([lane for lane, _, _ in rt.sent], ["hub-ana"])
        self.assertIn("handoff", rt.sent[0][2])

    def test_a_removed_members_news_moves_to_the_default_hub(self) -> None:
        self.s.push("hub-gone", "tower", "done", "Done: docs")
        self.e.tick()
        self.assertEqual([m["text"] for m in self.s.pending(HUB)], ["Done: docs"])

    def test_a_removed_members_hub_session_is_closed(self) -> None:
        self.rt.st["hub-ana"] = "idle"
        self.s.remove_member("ana")
        new = config_mod.load(self.cfg.home)
        said: list[str] = []
        close_changed(self.rt, self.cfg, new, said.append)
        self.assertEqual(self.rt.closed, ["hub-ana"])
        self.assertTrue(said)

    def test_members_over_the_cap_are_told_once_and_the_line_clears(self) -> None:
        cfg = with_members(self.cfg, "cy", "dee", cap=2)
        e = Engine(cfg, self.s, self.rt, lambda _: None, clock=self.clock, log=lambda _: None)
        for _ in range(3):
            e.tick()
        items = [it for it in self.s.items(state="open", audience=HUMAN) if "hub of their own" in it["title"]]
        self.assertEqual(len(items), 1)
        self.assertIn("cy, dee", items[0]["body"])
        self.s.remove_member("cy")
        self.s.remove_member("dee")
        self.s.remove_member("ana")
        e2 = Engine(config_mod.load(cfg.home), self.s, self.rt, lambda _: None, clock=self.clock, log=lambda _: None)
        e2.overflow_seen = ("cy", "dee")
        e2.tick()
        self.assertEqual([it for it in self.s.items(state="open", audience=HUMAN) if "hub of their own" in it["title"]], [])


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = with_members(make_home(), "ana")
        self.home = self.cfg.home

    def dah(self, *args: str, lane: str | None = None, ok: bool = True) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(ROOT)}
        env.pop("TOWER_LANE", None)
        if lane:
            env["TOWER_LANE"] = lane
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        if ok and p.returncode:
            self.fail(f"dah {' '.join(args)} failed: {p.stderr}")
        if not ok and not p.returncode:
            self.fail(f"dah {' '.join(args)} should have failed")
        return p.stdout + p.stderr

    def test_a_person_hub_runs_the_team_and_hears_back(self) -> None:
        self.dah("task", "docs", "--title", "write", "model: sonnet\neffort: low\ndo it", lane="hub-ana")
        s = Store(self.cfg.db)
        task = s.tasks(("queued",))[0]
        self.assertEqual((task["hub"], task["requester"]), ("hub-ana", "ana"))
        s.update_task(task["id"], state="running")
        self.dah("report", "done", "written", lane="docs")
        self.assertEqual([m["kind"] for m in s.pending("hub-ana")], ["done"])
        self.assertEqual(s.pending(HUB), [])
        self.dah("doing", "reviewing docs", lane="hub-ana")
        self.assertEqual(s.lane("hub-ana")["doing"], "reviewing docs")
        self.dah("infra", "add a lane", "--approved", "request #1", lane="hub-ana")
        self.assertEqual(s.memos("infra")[0]["sender"], "hub-ana")
        self.assertIn("does not report", self.dah("report", "note", "x", lane="hub-ana", ok=False))

    def test_the_default_hub_sends_a_requesters_task_to_the_requesters_hub(self) -> None:
        s = Store(self.cfg.db)
        mid = s.push(HUB, "ana", "memo", "please")
        out = self.dah("task", "docs", "--title", "t", "--request", str(mid), "model: sonnet\neffort: low\ndo it", lane="hub")
        self.assertIn("news to hub-ana", out)
        self.assertEqual(s.tasks()[0]["hub"], "hub-ana")
        s.update_task(s.tasks()[0]["id"], state="running")
        self.dah("report", "done", "written", lane="docs")
        self.assertEqual([m["kind"] for m in s.pending("hub-ana")], ["done"])

    def test_a_sign_in_email_names_the_member(self) -> None:
        s = Store(self.cfg.db)
        mid = s.push(HUB, "ana@x.com", "memo", "please")
        self.dah("task", "docs", "--title", "t", "--request", str(mid), "model: sonnet\neffort: low\ndo it", lane="hub")
        self.assertEqual(s.tasks()[0]["requester"], "ana")
        self.dah("item", "add", "--title", "r", "--body", "## a", "--to", "ana@x.com", lane="hub")
        self.assertEqual(s.items()[0]["assignee"], "ana")

    def test_infra_answers_reach_the_asking_person_hub(self) -> None:
        cfg = with_members(make_home(requester=True), "ana")
        self.home, self.cfg = cfg.home, cfg
        s = Store(cfg.db)
        mid = s.push(INFRA, "hub-ana", "memo", "fix it")
        s.claim_inbox(INFRA)
        self.dah("memo", "reply", str(mid), "done", lane="infra")
        self.assertEqual([m["kind"] for m in s.pending("hub-ana")], ["infra_reply"])
        self.assertEqual(s.pending(HUB), [])
        self.dah("item", "add", "--title", "ok?", "--body", "## a", lane="infra")
        self.assertEqual(s.items()[0]["assignee"], "ana")
        s.finish_inbox(INFRA)
        later = s.push(INFRA, "hub-ana", "memo", "again")
        s.claim_inbox(INFRA)
        self.dah("report", "wrapped", "fixed", lane="infra")
        self.assertEqual([m["kind"] for m in s.pending("hub-ana")], ["infra_reply", "wrapped"])
        self.assertEqual(s.pending(HUB), [])
        self.assertGreater(later, mid)

    def test_member_add_brings_a_hub_and_list_shows_it(self) -> None:
        out = self.dah("member", "add", "bo", "--ident", "bo@x.com")
        self.assertIn("person hubs: hub-ana, hub-bo", out)
        self.assertIn("hub-bo", self.dah("member", "list"))
        self.assertIn("is reserved for hubs", self.dah("member", "add", "hub-x", ok=False))

    def test_member_remove_gives_the_place_to_the_first_in_line(self) -> None:
        cfg = with_members(make_home(), "ana", "bo", cap=1)
        self.home = cfg.home
        out = self.dah("member", "remove", "ana")
        self.assertIn("person hubs: hub-bo", out)

    def test_member_list_has_no_hub_column_when_the_feature_is_off(self) -> None:
        cfg = with_members(make_home(), "ana", cap=None)
        self.home = cfg.home
        self.assertNotIn("hub", self.dah("member", "list"))


class MigrationTest(unittest.TestCase):
    def test_an_old_database_gets_the_hub_column(self) -> None:
        path = make_home().db
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.executescript(SCHEMA)
        con.execute("INSERT INTO tasks(lane, title, brief, state, created) VALUES('docs', 'old', 'b', 'queued', 1)")
        con.commit()
        con.close()
        s = Store(path)
        self.assertIsNone(s.task(1)["hub"])
        self.assertEqual(s.hub_for_task(1), HUB)
        tid = s.add_task("docs", "t", "b", hub="hub-ana")
        self.assertEqual(s.hub_for_task(tid), "hub-ana")
        self.assertEqual(s.task_hubs(), {tid: "hub-ana"})


class WebTest(unittest.TestCase):
    H = "X-Forwarded-Email"

    def setUp(self) -> None:
        self.b = Board(auth={"mode": "header", "header": self.H})
        cfg = with_members(self.b.cfg, "ana", "bo")
        self.b.srv.shutdown()
        self.b.srv.server_close()
        self.b.cfg, self.b.s = cfg, Store(cfg.db)
        self.b.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg, self.b.s, lambda _: None))
        threading.Thread(target=self.b.srv.serve_forever, daemon=True).start()
        self.b.base = f"http://127.0.0.1:{self.b.srv.server_address[1]}"

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def test_requests_go_to_the_senders_hub_and_each_sees_their_own(self) -> None:
        code, _, _ = self.b.req("POST", "/api/memo", {"to": "hub", "text": "ana's ask"}, {**POST, self.H: "ana@x.com"})
        self.assertEqual(code, 200)
        self.assertEqual([m["text"] for m in self.b.s.pending("hub-ana")], ["ana's ask"])
        self.b.s.add_item("hub-ana", None, "review", "for ana", "## a", [], [], audience=HUMAN)
        _, st, _ = self.b.req("GET", "/api/state", headers={self.H: "ana@x.com"})
        self.assertEqual(st["my_hub"], "hub-ana")
        self.assertEqual([m["text"] for m in st["memos"]["hub"]], ["ana's ask"])
        self.assertEqual([it["hub"] for it in st["items"]], ["hub-ana"])
        self.assertIn("hub-ana", [ln["lane"] for ln in st["lanes"]])
        self.assertEqual({ln["lane"]: ln["owner"] for ln in st["lanes"]}["hub-ana"], "ana")
        _, st, _ = self.b.req("GET", "/api/state", headers={self.H: "bo@x.com"})
        self.assertEqual((st["my_hub"], st["memos"]["hub"]), ("hub-bo", []))
        _, hist, _ = self.b.req("GET", "/api/history?kind=requests", headers={self.H: "bo@x.com"})
        self.assertNotIn("ana's ask", json.dumps(hist))
        _, hist, _ = self.b.req("GET", "/api/history?kind=requests", headers={self.H: "ana@x.com"})
        self.assertIn("ana's ask", json.dumps(hist))

    def test_a_card_belongs_to_the_hub_of_its_task(self) -> None:
        tid = self.b.s.add_task("docs", "t", "b", hub="hub-bo")
        self.b.s.add_item("docs", tid, "review", "from docs", "## a", [], [], audience=HUMAN)
        self.b.s.add_item("docs", None, "review", "no task", "## a", [], [], audience=HUMAN)
        _, st, _ = self.b.req("GET", "/api/state", headers={self.H: "ana@x.com"})
        self.assertEqual({it["title"]: it["hub"] for it in st["items"]}, {"from docs": "hub-bo", "no task": "hub"})

    def test_a_person_hubs_lane_sheet_opens(self) -> None:
        code, view, _ = self.b.req("GET", "/api/lanes/hub-ana", headers={self.H: "ana@x.com"})
        self.assertEqual(code, 200, view)
        code, _, _ = self.b.req("GET", "/api/lanes/hub-nobody", headers={self.H: "ana@x.com"})
        self.assertEqual(code, 404)

    def test_the_history_counts_the_members_own_hub_and_the_shared_hub(self) -> None:
        self.b.s.push("hub-ana", "ana", "memo", "one")
        self.b.s.push("hub-bo", "bo", "memo", "two")
        self.b.s.push(HUB, "cy", "memo", "three")
        _, st, _ = self.b.req("GET", "/api/state", headers={self.H: "ana@x.com"})
        self.assertEqual(st["counts"]["requests"], 2)


def auto_board(cap: int = 2, domains: list[str] | None = None) -> Board:
    b = Board(auth={"mode": "header", "header": "X-Forwarded-Email"})
    path = b.cfg.home / ".tower" / "tower.json"
    data = json.loads(path.read_text())
    data["person_hubs"] = {"max": cap, "auto": True, **({"domains": domains} if domains else {})}
    path.write_text(json.dumps(data))
    cfg = config_mod.load(b.cfg.home)
    b.srv.shutdown()
    b.srv.server_close()
    b.cfg, b.s = cfg, Store(cfg.db)
    b.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg, b.s, lambda _: None))
    threading.Thread(target=b.srv.serve_forever, daemon=True).start()
    b.base = f"http://127.0.0.1:{b.srv.server_address[1]}"
    return b


class AutoJoinTest(unittest.TestCase):
    H = "X-Forwarded-Email"

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def state(self, email: str) -> dict:
        code, st, _ = self.b.req("GET", "/api/state", headers={self.H: email})
        self.assertEqual(code, 200, st)
        return st

    def test_the_options_are_read(self) -> None:
        self.b = auto_board(domains=["@Team.Example"])
        self.assertEqual(self.b.cfg.hub_domains, ["team.example"])
        self.assertTrue(self.b.cfg.hub_auto)

    def test_first_visit_makes_a_member_a_hub_and_a_wait_screen(self) -> None:
        self.b = auto_board()
        st = self.state("Ana.Lee@team.example")
        self.assertEqual(st["me"]["name"], "ana.lee")
        self.assertEqual(st["me"]["role"], "owner")
        self.assertEqual(st["my_hub"], "hub-ana-lee")
        self.assertEqual((st["my_hub_state"]["state"], st["my_hub_state"]["step"]), ("starting", "opening"))
        self.assertEqual([m["name"] for m in self.b.s.members()], ["ana.lee"])
        self.assertIn("ana.lee", self.b.s.pending(HUB)[0]["text"])
        st = self.state("ana.lee@team.example")
        self.assertEqual(len(self.b.s.members()), 1)
        self.assertEqual(st["me"]["role"], "owner")
        code, _, _ = self.b.req("POST", "/api/memo", {"to": "hub", "text": "first ask"}, {**POST, self.H: "ana.lee@team.example"})
        self.assertEqual(code, 200)
        self.assertEqual([m["text"] for m in self.b.s.pending("hub-ana-lee")], ["first ask"])
        self.b.s.set_lane("hub-ana-lee", status="busy", opened=100.0, session="s1")
        self.assertEqual(self.state("ana.lee@team.example")["my_hub_state"]["step"], "reading")
        self.b.s.mark_delivered(m["id"] for m in self.b.s.pending("hub-ana-lee"))
        self.assertEqual(self.state("ana.lee@team.example")["my_hub_state"]["state"], "ready")
        self.b.s.set_lane("hub-ana-lee", status="idle")
        self.assertEqual(self.state("ana.lee@team.example")["my_hub_state"], {"state": "ready", "hub": "hub-ana-lee"})

    def test_past_the_cap_people_wait_in_line(self) -> None:
        self.b = auto_board(cap=1)
        self.state("ana@x.com")
        st = self.state("bo@x.com")
        self.assertEqual(st["me"]["role"], "member")
        self.assertEqual(st["my_hub"], HUB)
        self.assertEqual(st["my_hub_state"], {"state": "full", "hub": HUB, "place": 1, "waiting": 1, "cap": 1})
        self.assertIn("wait in line as number 1", self.b.s.pending(HUB)[-1]["text"])
        code, _, _ = self.b.req("POST", "/api/memo", {"to": "hub", "text": "bo's ask"}, {**POST, self.H: "bo@x.com"})
        self.assertEqual(code, 200)
        self.assertIn("bo's ask", [m["text"] for m in self.b.s.pending(HUB)])
        cfg = config_mod.load(self.b.cfg.home)
        self.assertEqual(cfg.hub_overflow, ["bo"])
        clock = Clock()
        s = Store(cfg.db, clock=clock)
        Engine(cfg, s, FakeRuntime(s), lambda _: None, clock=clock, log=lambda _: None).tick()
        items = [it for it in self.b.s.items(state="open", audience=HUMAN) if "hub of their own" in it["title"]]
        self.assertEqual(len(items), 1)
        self.b.s.remove_member("ana")
        self.assertEqual(config_mod.load(self.b.cfg.home).hub_of("bo"), "hub-bo")

    def test_other_domains_are_not_let_in(self) -> None:
        self.b = auto_board(domains=["team.example"])
        self.state("ana@team.example")
        code, _, _ = self.b.req("GET", "/api/state", headers={self.H: "eve@gmail.com"})
        self.assertEqual(code, 401)
        self.assertEqual([m["name"] for m in self.b.s.members()], ["ana"])

    def test_an_identity_that_is_no_email_is_not_let_in_on_its_own(self) -> None:
        self.b = auto_board()
        self.b.s.add_member("ana", "owner", "ana@x.com")
        code, _, _ = self.b.req("GET", "/api/state", headers={self.H: "svc"})
        self.assertEqual(code, 401)

    def test_off_unless_asked(self) -> None:
        self.b = Board(auth={"mode": "header", "header": self.H})
        st = self.state("ana@x.com")
        self.assertEqual((st["me"]["name"], st["my_hub_state"]["state"]), ("ana@x.com", "off"))
        self.assertEqual(self.b.s.members(), [])


class OpenedTest(unittest.TestCase):
    def test_a_hub_records_when_it_opened(self) -> None:
        cfg = with_members(make_home(), "ana")
        clock = Clock()
        s = Store(cfg.db, clock=clock)
        for h in cfg.hubs():
            s.set_lane(h, enabled=1)
        Engine(cfg, s, FakeRuntime(s), lambda _: None, clock=clock, log=lambda _: None).tick()
        self.assertEqual(s.lane("hub-ana")["opened"], clock())


if __name__ == "__main__":
    unittest.main()
