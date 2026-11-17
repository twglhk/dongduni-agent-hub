import json
import os
import re
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

from agent_hub import config as config_mod, engines, line, proccheck, processes, procverify, visibility as vis
from agent_hub.web import lanes_view, process_view
from agent_hub.cli import hub as cli_hub
from agent_hub.cli.common import TowerError
from agent_hub.engine import Engine, conflict_of
from agent_hub.store import HUB, HUMAN, SCHEMA, Store
from tests.helpers import Clock, FakeRuntime, make_home
from tests.test_library_migration import OLD_LINE, OldWorkspace
from tests.test_process_library import write_full, write_sub

LANES = {"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"}, "demo": {"runtime": "claude"}}
STEPS = [("planning", "book-plan", "sonnet/high"), ("production", "book-make", "opus/high"), ("QA", "book-qa", "sonnet/high")]
NEXT, REDO = (proccheck.LABELS["en"][k][0] for k in (proccheck.NEXT, proccheck.REDO))


def brief(sub: str, mode: str = "make", stage: str = "1-2") -> str:
    return f"Line: book / {sub} · stage {stage} · {mode}\nmodel: opus\neffort: high\n\nDo it."


class Workspace(unittest.TestCase):
    CONFIG: dict = {}

    def setUp(self) -> None:
        self.cfg = make_home(LANES, require_line=True, process_verification=True, **self.CONFIG)
        self.home = self.cfg.home
        for _, sid, _ in STEPS:
            write_sub(self.home, sid, sid, "confirmed", "confirmed")
        write_full(self.home, "book", "Book", STEPS)
        (self.home / "processes" / "README.md").write_text("`book` `book-plan` `book-make` `book-qa`", encoding="utf-8")
        (self.home / ".tower" / "lanes").mkdir(parents=True, exist_ok=True)
        (self.home / ".tower" / "lanes" / "demo.md").write_text("# Demo\n\n## Processes used\n\n- `book`\n", encoding="utf-8")
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.s.add_member("alice")
        self.rt = FakeRuntime(self.s)
        self.logs: list[str] = []
        self.e = Engine(self.cfg, self.s, self.rt, lambda n: None, clock=self.clock, log=self.logs.append)

    def task(self, sub: str, mode: str = "make", run: str = "book:one", stage: str = "1-2") -> dict:
        b = brief(sub, mode, stage)
        tid = self.s.add_task("demo", f"{mode} {sub}", b, None, run, requester="alice")
        self.s.update_task(tid, **line.fields(b))
        self.clock.tick(1)
        return self.s.task(tid)

    def run_it(self, t: dict) -> dict:
        self.s.update_task(t["id"], state="running", started=self.clock())
        return self.s.task(t["id"])

    def close(self, t: dict) -> None:
        self.s.update_task(t["id"], state="closed", closed=self.clock())

    def check(self, t: dict) -> int:
        iid, revised = proccheck.post(self.s, self.home, "en", "demo", self.s.task(t["id"]), "check", "body", ["out.md"])
        self.assertFalse(revised)
        return iid

    def why(self, t: dict) -> str | None:
        return conflict_of(self.s, self.s.task(t["id"]), home=self.home)

    def decide(self, iid: int, choice: str | None, verdict: str = "ok", note: str | None = None) -> None:
        self.s.decide(iid, verdict, "alice", choice=choice, note=note)
        self.e.route_decisions()

    def news(self) -> str:
        return "\n".join(m["text"] for m in self.s.pending(HUB))

    def pass_run(self, run: str = "book:one") -> int:
        last = 0
        for _, sid, _ in STEPS:
            t = self.run_it(self.task(sid, run=run))
            last = self.check(t)
            self.close(t)
            self.decide(last, NEXT)
        return last


class GateTest(Workspace):
    def test_next_sub_process_waits_for_the_check_and_starts_on_next(self) -> None:
        a = self.run_it(self.task("book-plan"))
        b = self.task("book-make")
        self.assertIn("check card of book-plan", self.why(b))
        self.assertIn("book-plan", proccheck.missing_check(self.s, self.home, a))
        iid = self.check(a)
        self.assertIsNone(proccheck.missing_check(self.s, self.home, a))
        card = self.s.item(iid)
        self.assertEqual((card["kind"], card["audience"], card["assignee"]), ("decision", HUMAN, "alice"))
        self.assertEqual([o["label"] for o in card["options"]], [NEXT, REDO])
        self.assertEqual(self.why(b), f"waiting for alice's check · card #{iid}")
        self.assertIn("alice", proccheck.lane_line(self.s, self.home, "demo"))
        self.assertEqual(proccheck.held_text(proccheck.hold_of(self.s, self.home, self.s.task(b["id"])), "ko"),
                         f"alice 님의 확인을 기다려요 · 카드 #{iid}")
        self.close(a)
        self.decide(iid, NEXT)
        self.assertIsNone(self.why(b))
        self.assertIn("`book-make` of run book:one may start", self.news())
        self.assertIsNone(proccheck.lane_line(self.s, self.home, "demo"))
        run = proccheck.run_view(self.s, "book", [x[1] for x in STEPS])
        self.assertEqual((run["key"], run["sub"], run["check"]["state"]), ("book:one", "book-make", proccheck.NEXT))

    def test_first_sub_process_and_another_run_are_not_held(self) -> None:
        self.assertIsNone(self.why(self.task("book-plan")))
        a = self.run_it(self.task("book-plan"))
        self.decide(self.check(a), NEXT)
        self.assertIsNotNone(self.why(self.task("book-make", run="book:two")))

    def test_part_of_a_sub_process_needs_no_check(self) -> None:
        a = self.run_it(self.task("book-plan", stage="1"))
        self.assertIsNone(proccheck.missing_check(self.s, self.home, a))
        with self.assertRaises(proccheck.CheckError):
            self.check(a)

    def test_redo_queues_a_fix_task_with_the_note(self) -> None:
        a = self.run_it(self.task("book-plan"))
        b = self.task("book-make")
        iid = self.check(a)
        self.close(a)
        self.decide(iid, REDO, note="the ending is too sad")
        fix = [t for t in self.s.tasks(("queued",)) if t["line_mode"] == "fix"]
        self.assertEqual(len(fix), 1)
        f = fix[0]
        self.assertEqual((f["lane"], f["deliverable"], f["requester"], f["line_row"], f["line_stage"]),
                         ("demo", "book:one", "alice", "book/book-plan", "1-2"))
        self.assertTrue(f["brief"].startswith("Line: book / book-plan · stage 1-2 · fix\nmodel: opus\neffort: high"))
        self.assertIn("the ending is too sad", f["brief"])
        line.gate(f["brief"], processes.load(self.home))
        self.assertIn(f"fix task #{f['id']}", self.news())
        self.assertIn("being redone", self.why(b))
        self.assertIsNone(self.why(f))  # the fix itself re-runs the first sub-process
        f = self.run_it(f)
        self.assertIsNotNone(proccheck.missing_check(self.s, self.home, f))
        again = self.check(f)
        self.close(f)
        self.assertIn(f"card #{again}", self.why(b))
        self.decide(again, NEXT)
        self.assertIsNone(self.why(b))

    def test_rejection_and_own_answer_redo_hold_waits(self) -> None:
        a = self.run_it(self.task("book-plan"))
        b = self.task("book-make")
        self.decide(self.check(a), None, verdict="answer", note="use the other title")
        self.assertEqual(len([t for t in self.s.tasks(("queued",)) if t["line_mode"] == "fix"]), 1)
        c = self.run_it(self.task("book-plan", mode="fix"))
        iid = self.check(c)
        self.decide(iid, None, verdict="hold")
        self.assertIn("on hold", self.why(b))
        self.assertIn(f"dah process check {iid} next", self.news())

    def test_hold_waits_for_the_hub_then_its_answer_applies(self) -> None:
        a = self.run_it(self.task("book-plan"))
        b = self.task("book-make")
        iid = self.check(a)
        self.close(a)
        self.decide(iid, None, verdict="hold", note="looks fine but shorter title?")
        self.assertIn("on hold", self.why(b))
        checks = proccheck.Checks(self.cfg, self.s, lambda _: None)
        with self.assertRaises(proccheck.CheckError):
            checks.settle(9999, proccheck.NEXT, HUB)
        self.assertIn("may start", checks.settle(iid, proccheck.NEXT, HUB))
        self.assertIsNone(self.why(b))
        with self.assertRaises(proccheck.CheckError):
            checks.settle(iid, proccheck.REDO, HUB)  # already settled

    def test_hub_redo_after_no_option(self) -> None:
        a = self.run_it(self.task("book-plan"))
        iid = self.check(a)
        self.close(a)
        self.decide(iid, None, verdict="hold", note="make the bear older")
        out = proccheck.Checks(self.cfg, self.s, lambda _: None).settle(iid, proccheck.REDO, HUB)
        fix = [t for t in self.s.tasks(("queued",)) if t["line_mode"] == "fix"]
        self.assertEqual(len(fix), 1)
        self.assertIn(f"fix task #{fix[0]['id']}", out)
        self.assertIn("make the bear older", fix[0]["brief"])

    def test_tick_starts_the_released_task(self) -> None:
        a = self.run_it(self.task("book-plan"))
        b = self.task("book-make")
        for t in (a, b):  # nobody's work: it runs on the shared account, so no sign-in hold gets in the way
            self.s.update_task(t["id"], requester=None)
        iid = self.check(a)
        self.close(a)
        self.e.tick()
        self.assertEqual(self.s.task(b["id"])["state"], "queued")
        self.s.decide(iid, "ok", "alice", choice=NEXT)
        self.e.tick()
        self.assertEqual(self.s.task(b["id"])["state"], "running", self.logs)

    def test_last_check_asks_the_hub_for_the_verify_card_then_verify(self) -> None:
        last = self.pass_run()
        self.assertIn("Post the verify card", self.news())
        self.assertIn("dah process verify book", self.news())
        ask = self.s.add_item(HUB, None, "decision", "이 공정을 검증됨으로 할까요", "b",
                              [{"label": "검증됨으로", "desc": ""}, {"label": "아직", "desc": ""}], [], audience=HUMAN,
                              assignee="alice")
        with self.assertRaises(proccheck.CheckError):
            proccheck.verify(self.s, self.home, "book", ask)  # still open
        self.s.decide(ask, "ok", "alice", choice="검증됨으로")
        rec = proccheck.verify(self.s, self.home, "book", ask)
        self.assertEqual((rec["card"], rec["by"], rec["run"], rec["steps"]), (ask, "alice", "book:one", [x[1] for x in STEPS]))
        self.assertIn("processes/sub/book-make/patterns/one.md", rec["files"])
        saved = json.loads((self.home / procverify.VERIFIED_FILE).read_text(encoding="utf-8"))
        self.assertEqual(saved["book"]["card"], ask)
        lib = processes.load(self.home)
        self.assertEqual((lib["full"]["book"]["status"], lib["full"]["book"]["reverify"]), ("verified", False))
        self.assertEqual(processes.view(self.home)["full"][0]["status"], "verified")
        self.assertIsNone(self.why(self.task("book-make", run="book:two")))  # verified: runs through
        mid = self.run_it(self.task("book-make", run="book:two"))
        self.assertIsNone(proccheck.missing_check(self.s, self.home, mid))
        end = self.run_it(self.task("book-qa", run="book:two"))
        self.assertIsNotNone(proccheck.missing_check(self.s, self.home, end))  # only the final result is checked
        self.decide(self.check(end), NEXT)
        self.assertIn("final result is approved", self.news())
        self.assertTrue(last)

    def test_verify_refused_without_a_passed_run(self) -> None:
        a = self.run_it(self.task("book-plan"))
        self.decide(self.check(a), NEXT)
        ok = self.s.add_item(HUB, None, "decision", "verify?", "b", [{"label": "yes", "desc": ""}, {"label": "no", "desc": ""}],
                             [], audience=HUMAN)
        self.s.decide(ok, "ok", "alice", choice="yes")
        with self.assertRaises(proccheck.CheckError) as e:
            proccheck.verify(self.s, self.home, "book", ok)
        self.assertIn("no run", str(e.exception))
        write_sub(self.home, "book-qa", "book-qa", "draft", "confirmed")
        with self.assertRaises(proccheck.CheckError) as e:
            proccheck.verify(self.s, self.home, "book", ok)
        self.assertIn("draft", str(e.exception))

    def test_a_changed_card_drops_back_and_the_gate_holds_again(self) -> None:
        self.pass_run()
        ask = self.s.add_item(HUB, None, "decision", "verify?", "b", [{"label": "yes", "desc": ""}, {"label": "no", "desc": ""}],
                              [], audience=HUMAN)
        self.s.decide(ask, "ok", "alice", choice="yes")
        proccheck.verify(self.s, self.home, "book", ask)
        pat = self.home / "processes" / "sub" / "book-make" / "patterns" / "two.md"
        pat.write_text(pat.read_text(encoding="utf-8") + "\nA new rule.\n", encoding="utf-8")
        f = processes.load(self.home)["full"]["book"]
        self.assertEqual((f["status"], f["reverify"]), ("confirmed", True))
        self.assertEqual(f["changes"], ["processes/sub/book-make/patterns/two.md changed"])
        v = processes.view(self.home)["full"][0]
        self.assertTrue(v["reverify"])
        self.assertIsNotNone(self.why(self.task("book-make", run="book:three")))
        write_full(self.home, "book", "Book", STEPS[:1] + STEPS[2:])
        self.assertIn("the order of sub-processes changed", processes.load(self.home)["full"]["book"]["changes"])


class TaskGateTest(unittest.TestCase):
    def test_make_of_an_unverified_full_process_needs_a_deliverable(self) -> None:
        cfg = make_home(LANES, require_line=True, process_verification=True)
        for _, sid, _ in STEPS:
            write_sub(cfg.home, sid, sid, "confirmed", "confirmed")
        write_full(cfg.home, "book", "Book", STEPS)
        (cfg.home / ".tower" / "lanes").mkdir(parents=True, exist_ok=True)
        (cfg.home / ".tower" / "lanes" / "demo.md").write_text("## Processes used\n\n- `book`\n", encoding="utf-8")
        with self.assertRaises(TowerError) as e:
            cli_hub._gate_line(cfg, "demo", brief("book-plan"))
        self.assertIn("--deliverable", str(e.exception))
        cli_hub._gate_line(cfg, "demo", brief("book-plan"), "book:one")
        cli_hub._gate_line(cfg, "demo", "Line: book / book-plan · prepare\n\nx")


class NoLibraryTest(unittest.TestCase):
    def test_tasks_without_a_run_are_never_held(self) -> None:
        cfg = make_home(LANES, require_line=True, process_verification=True)
        s = Store(cfg.db, clock=Clock())
        tid = s.add_task("demo", "t", "Line: none · chore: x\n", None, None)
        self.assertIsNone(proccheck.hold_of(s, cfg.home, s.task(tid)))
        self.assertIsNone(proccheck.missing_check(s, cfg.home, s.task(tid)))
        self.assertEqual(s.process_checks(), [])
        self.assertFalse(Path(cfg.home / procverify.VERIFIED_FILE).exists())


class RequesterViewTest(Workspace):
    CONFIG = {"requester": True}

    def setUp(self) -> None:
        super().setUp()
        self.s.add_member("bob")

    def viewer(self, name: str, role: str = "member") -> vis.Gate:
        return vis.Gate.of(self.cfg, self.s, {"name": name, "role": role})

    def test_only_the_requester_and_the_owner_see_the_run_and_the_held_line(self) -> None:
        a = self.run_it(self.task("book-plan"))
        self.task("book-make")
        iid = self.check(a)
        self.close(a)
        for name, role, sees in (("alice", "member", True), ("bob", "member", False), ("root", "owner", True)):
            g = self.viewer(name, role)
            full = process_view(self.cfg, self.s, g)["full"][0]
            self.assertEqual(full["run"] is not None, sees, name)
            lane = next(x for x in lanes_view(self.cfg, self.s, {}, g) if x["lane"] == "demo")
            self.assertEqual(bool(lane.get("held")), sees, name)
        self.assertEqual(process_view(self.cfg, self.s, self.viewer("alice"))["full"][0]["run"]["check"]["item_id"], iid)
        self.assertNotIn("who", process_view(self.cfg, self.s, self.viewer("alice"))["full"][0]["run"])
        self.assertFalse(vis.can_see(self.viewer("bob"), self.s.item(iid)))


class OffByDefaultTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home(LANES, require_line=True)
        self.home = self.cfg.home
        for _, sid, _ in STEPS:
            write_sub(self.home, sid, sid, "confirmed", "confirmed")
        write_full(self.home, "book", "Book", STEPS)
        (self.home / ".tower" / "lanes").mkdir(parents=True, exist_ok=True)
        (self.home / ".tower" / "lanes" / "demo.md").write_text("## Processes used\n\n- `book`\n", encoding="utf-8")
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.e = Engine(self.cfg, self.s, FakeRuntime(self.s), lambda n: None, clock=self.clock, log=lambda _: None)

    def test_nothing_is_held_or_required(self) -> None:
        b = brief("book-make")
        tid = self.s.add_task("demo", "t", b, None, "book:one")
        self.s.update_task(tid, **line.fields(b))
        self.assertFalse(self.cfg.process_verification)
        self.assertIsNone(self.e.conflict(self.s.task(tid)))
        cli_hub._gate_line(self.cfg, "demo", b)
        self.assertFalse(process_view(self.cfg, self.s)["verification"])
        self.assertIsNone(process_view(self.cfg, self.s)["full"][0]["run"])
        self.assertEqual(processes.load(self.home)["full"]["book"]["status"], "confirmed")

    def test_the_key_needs_require_line(self) -> None:
        with self.assertRaises(config_mod.ConfigError):
            make_home(LANES, process_verification=True)


class CommandsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home(LANES, require_line=True, process_verification=True)
        self.home = self.cfg.home
        for _, sid, _ in STEPS:
            write_sub(self.home, sid, sid, "confirmed", "confirmed")
        write_full(self.home, "book", "Book", STEPS)
        (self.home / ".tower" / "lanes").mkdir(parents=True, exist_ok=True)
        (self.home / ".tower" / "lanes" / "demo.md").write_text("## Processes used\n\n- `book`\n", encoding="utf-8")

    def dah(self, lane: str, *args: str, ok: bool = True) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
               "TOWER_LANE": lane}
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        self.assertEqual(p.returncode == 0, ok, f"dah {' '.join(args)}: {p.stdout}{p.stderr}")
        return p.stdout + p.stderr

    def test_the_flow_through_the_commands(self) -> None:
        task = ("task", "demo", "--title", "plan", "--deliverable", "book:one")
        self.assertIn("--deliverable", self.dah("hub", "task", "demo", "--title", "plan", brief("book-plan"), ok=False))
        self.dah("hub", *task, brief("book-plan"))
        self.dah("hub", *task[:3], "make", "--deliverable", "book:one", brief("book-make"))
        s = Store(self.cfg.db)
        s.update_task(1, state="running", started=1.0)
        self.assertIn("post the check card first", self.dah("demo", "report", "done", "finished", ok=False))
        (self.home / "out.md").write_text("output", encoding="utf-8")
        (self.home / "why.md").write_text("what it made", encoding="utf-8")
        self.assertIn("check card #1 posted", self.dah("demo", "check", "--title", "plan check", "--body-file", "why.md", "--attach", "out.md"))
        self.assertIn("Sent to the hub", self.dah("demo", "report", "done", "finished"))
        self.assertIn("waiting for the requester's check · card #1", self.dah("hub", "status"))
        self.assertIn("book: confirmed\n  latest run book:one: on book-plan · check card #1 (book-plan) pending",
                      self.dah("hub", "process", "status", "book"))
        self.assertIn("only a team lane", self.dah("hub", "check", "--title", "x", "--body", "b", "--attach", "out.md", ok=False))
        self.assertIn("not waiting for the hub", self.dah("hub", "process", "check", "1", "next", ok=False))
        self.assertIn("card #1 is not approved", self.dah("hub", "process", "verify", "book", "--card", "1", ok=False))

    def test_check_is_refused_when_the_key_is_off(self) -> None:
        data = json.loads((self.home / ".tower" / "tower.json").read_text(encoding="utf-8"))
        data["process_verification"] = False
        (self.home / ".tower" / "tower.json").write_text(json.dumps(data), encoding="utf-8")
        self.assertIn("process_verification is off", self.dah("demo", "check", "--title", "x", "--body", "b", "--attach", "x", ok=False))


class ConvertedLibraryTest(OldWorkspace):
    def test_a_converted_pattern_library_is_gated_like_a_new_one(self) -> None:
        path = self.home / ".tower" / "tower.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["process_verification"] = True
        path.write_text(json.dumps(data), encoding="utf-8")
        self.update()
        refused = self.dah("task", "story", "--title", "t", OLD_LINE, ok=False, lane="hub")
        self.assertIn("--deliverable", refused)
        self.dah("task", "story", "--title", "t", "--deliverable", "anim:one", OLD_LINE, lane="hub")
        task = Store(self.cfg.db).task(1)
        self.assertEqual((task["line_row"], task["deliverable"]), ("anim/anim-storyboard", "anim:one"))
        self.assertEqual(proccheck.run_of(task), ("anim", "anim-storyboard", "anim:one"))
        self.assertIsNone(proccheck.hold_of(Store(self.cfg.db), self.home, task))


class OldDatabaseTest(unittest.TestCase):
    def test_a_database_from_before_the_check_table_opens_and_keeps_its_rows(self) -> None:
        path = make_home(LANES).db
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.executescript(re.sub(r"CREATE TABLE IF NOT EXISTS process_checks\(.*?\n\);\n", "", SCHEMA, flags=re.S))
        con.execute("INSERT INTO tasks(lane, title, brief, state, created) VALUES('demo', 'old', 'b', 'queued', 1)")
        con.commit()
        self.assertNotIn("process_checks", {r[0] for r in con.execute("SELECT name FROM sqlite_master")})
        con.close()
        s = Store(path)
        self.assertEqual(s.task(1)["title"], "old")
        self.assertEqual(s.process_checks(), [])
        s.add_process_check(5, 1, "demo", "book", "book-plan", "book:one", "alice")
        self.assertEqual(s.process_checks(full="book")[0]["state"], "pending")
        Store(path)
        self.assertEqual(len(s.process_checks()), 1)
        with self.assertRaises(Exception):
            s.process_checks(nope=1)


if __name__ == "__main__":
    unittest.main()
