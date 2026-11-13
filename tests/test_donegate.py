"""Done gate: report done is held until the project's command passes on one persistent, serial, clean gate worktree."""

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from agent_hub import config as config_mod
from agent_hub import donegate
from agent_hub.config import ConfigError
from agent_hub.engine import NUDGE_AFTER, Engine
from agent_hub.scratch import dah_root
from agent_hub.store import HUB, Store
from tests.helpers import Clock, FakeRuntime

PY = sys.executable


def git(repo: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env={**os.environ, **(env or {})}).stdout


OLD = {"GIT_AUTHOR_DATE": "2020-01-01T00:00:00", "GIT_COMMITTER_DATE": "2020-01-01T00:00:00"}


def workspace(gate: dict | None = None, **extra) -> config_mod.Config:
    """A git repo that is also the tower home; the gate root lives in its own temp dir, outside the repo."""
    home = Path(tempfile.mkdtemp(prefix="gate-ws-"))
    git(home, "init", "-q")
    (home / ".gitignore").write_text(".tower/\ncache/\n", encoding="utf-8")
    (home / "src").mkdir()
    (home / "src" / "a.txt").write_text("one\n", encoding="utf-8")
    (home / "docs").mkdir()
    (home / "docs" / "d.md").write_text("d\n", encoding="utf-8")
    git(home, "add", ".")
    git(home, "commit", "-qm", "base", env=OLD)  # before any task started
    (home / ".tower").mkdir()
    data = {"name": "T", "lanes": {"hub": {"runtime": "claude"}, "docs": {"runtime": "claude"}},
            "scratch_root": tempfile.mkdtemp(prefix="gate-root-"), **extra}
    if gate is not None:
        data["done_gate"] = gate
    (home / ".tower" / "tower.json").write_text(json.dumps(data), encoding="utf-8")
    return config_mod.load(home)


def commit(cfg: config_mod.Config, rel: str, text: str) -> None:
    p = cfg.home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    git(cfg.home, "add", rel)
    git(cfg.home, "commit", "-qm", f"edit {rel}")


def check(code: str) -> list[str]:
    return [PY, "-c", code]


class ParseTest(unittest.TestCase):
    def test_defaults_and_normalised_paths(self) -> None:
        g = donegate.parse({"paths": ["src/", "\\lib\\", " "], "command": "make test"})
        self.assertEqual(g["paths"], ["src", "lib"])
        self.assertEqual((g["timeout"], g["memory_mb"]), (donegate.DEFAULT_TIMEOUT, donegate.DEFAULT_MEMORY_MB))
        self.assertIsNone(g["setup"])
        self.assertEqual(donegate.parse(None), {})

    def test_bad_shapes_are_refused(self) -> None:
        for raw in ({"paths": ["src"]}, {"command": 3}, {"command": "x", "timeout": 0}, {"command": "x", "memory_mb": -1},
                    {"command": "x", "paths": "src"}, "make test"):
            with self.assertRaises(ValueError, msg=raw):
                donegate.parse(raw)

    def test_config_errors(self) -> None:
        with self.assertRaises(ConfigError):
            workspace({"paths": ["src"]})
        cfg = workspace({"command": "x"})
        raw = json.loads((cfg.home / ".tower/tower.json").read_text(encoding="utf-8"))
        raw["scratch_root"] = str(cfg.home / "tmp")  # the gate worktree must live outside the project
        (cfg.home / ".tower/tower.json").write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(ConfigError):
            config_mod.load(cfg.home)


class AppliesTest(unittest.TestCase):
    def test_only_commits_under_the_paths_since_the_task_started(self) -> None:
        cfg = workspace({"paths": ["src"], "command": check("")})
        task = {"id": 1, "started": time.time() - 30}
        self.assertFalse(donegate.applies(cfg, task, "docs"))
        commit(cfg, "docs/d.md", "changed\n")
        self.assertFalse(donegate.applies(cfg, task, "docs"))  # docs/ is not gated
        commit(cfg, "src/a.txt", "two\n")
        self.assertTrue(donegate.applies(cfg, task, "docs"))
        self.assertFalse(donegate.applies(cfg, {"id": 1, "started": time.time() + 3600}, "docs"))
        self.assertFalse(donegate.applies(cfg, {"id": 1, "started": None}, "docs"))
        self.assertFalse(donegate.applies(workspace(), task, "docs"))  # no done_gate configured


class RunGateTest(unittest.TestCase):
    def run_gate(self, cfg: config_mod.Config, **kw) -> dict:
        sha = donegate.head(cfg.home)
        d = Path(tempfile.mkdtemp())
        return donegate.run_gate(cfg, sha, d / "r.json", d / "r.log", **kw)

    def test_pass_on_a_clean_checkout_of_head_in_one_persistent_worktree(self) -> None:
        cfg = workspace({"command": check("import pathlib,sys; sys.exit(0 if pathlib.Path('src/a.txt').read_text()=='one\\n' else 5)")})
        (cfg.home / "src" / "a.txt").write_text("uncommitted\n", encoding="utf-8")  # not what the gate sees
        r = self.run_gate(cfg)
        self.assertEqual(r["state"], "pass", r)
        wt = dah_root(cfg) / "done-gate"
        self.assertTrue((wt / ".git").exists())
        self.assertNotIn(cfg.home.resolve(), wt.resolve().parents)  # outside the project
        (wt / "stray.txt").write_text("left by the last gate", encoding="utf-8")
        (wt / "cache").mkdir()
        (wt / "cache" / "kept").write_text("ignored build cache", encoding="utf-8")
        git(cfg.home, "checkout", "-q", "--", "src/a.txt")
        commit(cfg, "src/b.txt", "new\n")
        r = self.run_gate(cfg)
        self.assertEqual(r["state"], "pass", r)
        self.assertTrue((wt / "src" / "b.txt").exists())  # moved to the new HEAD, same tree
        self.assertFalse((wt / "stray.txt").exists())  # untracked leftovers cleaned
        self.assertTrue((wt / "cache" / "kept").exists())  # ignored caches stay (no full re-setup per gate)
        gates = [ln for ln in git(cfg.home, "worktree", "list", "--porcelain").splitlines()
                 if ln.startswith("worktree ") and "done-gate" in ln]
        self.assertEqual(len(gates), 1)
        self.assertFalse((dah_root(cfg) / "done-gate.lock").exists())  # released

    def test_fail_reports_last_lines_and_exit_code(self) -> None:
        cfg = workspace({"command": check("print('assertion failed in test_x'); raise SystemExit(3)")})
        r = self.run_gate(cfg)
        self.assertEqual(r["state"], "fail")
        self.assertIn("exit code 3", r["why"])
        self.assertTrue(any("assertion failed in test_x" in ln for ln in r["tail"]))
        text = donegate.refusal({"sha": r["sha"]}, r)
        self.assertIn(r["log"], text)
        self.assertIn("assertion failed", text)

    def test_setup_is_retried_and_verified_before_the_command(self) -> None:
        bump = check("import pathlib; p=pathlib.Path('cache/n'); p.parent.mkdir(exist_ok=True); "
                     "p.write_text(str(int(p.read_text() if p.exists() else 0)+1))")
        cfg = workspace({"command": check("open('cache/ran','w').write('1')"), "setup": bump,
                         "verify": check("import pathlib,sys; sys.exit(0 if int(pathlib.Path('cache/n').read_text())>=2 else 1)")})
        r = self.run_gate(cfg)
        self.assertEqual(r["state"], "pass", r)
        log = Path(r["log"]).read_text(encoding="utf-8")
        self.assertIn("setup pass 1: not ready", log)
        self.assertIn("setup pass 2: ready", log)

    def test_command_does_not_run_when_setup_never_gets_ready(self) -> None:
        cfg = workspace({"command": check("open('cache/ran','w').write('1')"), "setup": check(""),
                         "verify": check("raise SystemExit(1)")})
        r = self.run_gate(cfg)
        self.assertEqual(r["state"], "fail")
        self.assertIn("command not run", r["why"])
        self.assertFalse((dah_root(cfg) / "done-gate" / "cache" / "ran").exists())

    def test_timeout_stops_the_command(self) -> None:
        cfg = workspace({"command": check("import time; time.sleep(60)"), "timeout": 1})
        t0 = time.monotonic()
        r = self.run_gate(cfg)
        self.assertEqual(r["state"], "fail")
        self.assertIn("timeout", r["why"])
        self.assertLess(time.monotonic() - t0, 30)

    def test_memory_ceiling_stops_the_command(self) -> None:
        cfg = workspace({"command": check("import time; time.sleep(60)"), "memory_mb": 100})
        r = self.run_gate(cfg, measure=lambda pid: 5000)
        self.assertEqual(r["state"], "fail")
        self.assertIn("memory ceiling", r["why"])

    def test_one_gate_at_a_time(self) -> None:
        out = Path(tempfile.mkdtemp()) / "order.txt"
        code = (f"import time; f=open({str(out)!r},'a'); f.write('start\\n'); f.flush(); time.sleep(1); "
                f"f.write('end\\n'); f.close()")
        cfg = workspace({"command": check(code)})
        results: list[dict] = []
        ts = [threading.Thread(target=lambda: results.append(self.run_gate(cfg))) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        self.assertEqual([r["state"] for r in results], ["pass", "pass"])
        self.assertEqual(out.read_text(encoding="utf-8").split(), ["start", "end", "start", "end"])


class LockTest(unittest.TestCase):
    def test_waits_for_a_live_holder_and_takes_over_a_dead_one(self) -> None:
        path = Path(tempfile.mkdtemp()) / "gate.lock"
        path.mkdir()
        (path / "pid").write_text("4242", encoding="utf-8")
        alive = {"v": True}
        waited: list[float] = []

        def sleep(s: float) -> None:
            waited.append(s)
            alive["v"] = False  # the holder dies while we wait

        said: list[str] = []
        lock = donegate.Lock(path, alive=lambda pid: alive["v"], sleep=sleep)
        lock.acquire(said.append)
        self.assertEqual(len(waited), 1)
        self.assertIn("4242", said[0])
        self.assertEqual((path / "pid").read_text(encoding="utf-8"), str(os.getpid()))
        lock.release()
        self.assertFalse(path.exists())


class PendingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = workspace({"paths": ["src"], "command": check("")})
        commit(self.cfg, "src/a.txt", "two\n")
        self.task = {"id": 7, "started": time.time() - 60, "title": "t"}

    def test_start_poll_once_and_already_running(self) -> None:
        msg = donegate.start(self.cfg, self.task, "docs", "did it", ["src/a.txt"], spawn=lambda argv, cwd: 4242)
        self.assertIn("done gate", msg)
        self.assertTrue(donegate.running(self.cfg, 7))
        again = donegate.start(self.cfg, self.task, "docs", "did it", [], spawn=lambda argv, cwd: self.fail("spawned twice"))
        self.assertIn("already running", again)
        self.assertEqual(donegate.poll(self.cfg, alive=lambda pid: True), [])  # still running
        pend = donegate.pending(self.cfg, 7)[0]
        pend.with_suffix(".json").write_text(json.dumps({"state": "pass", "log": "/l", "tail": []}), encoding="utf-8")
        got = donegate.poll(self.cfg, alive=lambda pid: True)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0]["text"], "did it")
        self.assertEqual(got[0][1]["state"], "pass")
        self.assertFalse(donegate.running(self.cfg, 7))
        self.assertEqual(donegate.poll(self.cfg), [])

    def test_a_runner_that_died_is_a_fail(self) -> None:
        donegate.start(self.cfg, self.task, "docs", "x", [], spawn=lambda argv, cwd: 4242)
        got = donegate.poll(self.cfg, alive=lambda pid: False)
        self.assertEqual(got[0][1]["state"], "fail")
        self.assertIn("without a verdict", donegate.refusal(*got[0]))

    def test_the_real_runner_process_delivers_a_verdict(self) -> None:
        donegate.start(self.cfg, self.task, "docs", "real", [])
        deadline = time.time() + 120
        got: list = []
        while not got and time.time() < deadline:
            got = donegate.poll(self.cfg)
            time.sleep(0.5)
        self.assertTrue(got, "the gate runner never answered")
        self.assertEqual(got[0][1]["state"], "pass", got[0][1])


class ReportAndEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = workspace({"paths": ["src"], "command": check("")})
        self.clock = Clock(time.time())
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda n: None, clock=self.clock, log=lambda _: None)
        tid = self.s.add_task("docs", "Fix a", "brief")
        self.s.claim_task(tid)
        self.s.update_task(tid, started=time.time() - 60, session="s1")
        self.tid = tid
        commit(self.cfg, "src/a.txt", "two\n")

    def report(self, text: str) -> str:
        from agent_hub import cli
        buf = io.StringIO()
        env = {"TOWER_HOME": str(self.cfg.home), "TOWER_LANE": "docs"}
        with mock.patch.dict(os.environ, env), mock.patch("agent_hub.donegate._spawn", return_value=4242), redirect_stdout(buf):
            cli.main(["report", "done", text])
        return buf.getvalue()

    def test_report_done_is_held_then_delivered_on_pass(self) -> None:
        out = self.report("fixed a")
        self.assertIn("done gate", out)
        self.assertEqual(self.s.reports(self.tid), [])  # not reported yet
        self.assertEqual([m for m in self.s.pending(HUB) if m["kind"] == "done"], [])
        # the lane goes idle while the gate runs: no nudge
        self.rt.st["docs"] = "idle"
        with mock.patch("agent_hub.procs.alive", return_value=True):
            for _ in range(5):
                self.e.tick()
                self.clock.tick(NUDGE_AFTER)
        self.assertEqual([m for m in self.s.pending("docs") if m["kind"] == "nudge"], [])
        pend = donegate.pending(self.cfg, self.tid)[0]
        pend.with_suffix(".json").write_text(json.dumps({"state": "pass", "log": "/l", "tail": []}), encoding="utf-8")
        self.e._done_gates()
        self.assertEqual([r["kind"] for r in self.s.reports(self.tid)], ["done"])
        done = [m for m in self.s.pending(HUB) if m["kind"] == "done"]
        self.assertEqual(len(done), 1)
        self.assertIn("fixed a", done[0]["text"])
        self.assertTrue(any("passed" in m["text"] for m in self.s.pending("docs")))

    def test_fail_tells_the_lane_and_it_can_resend(self) -> None:
        self.report("fixed a")
        pend = donegate.pending(self.cfg, self.tid)[0]
        pend.with_suffix(".json").write_text(json.dumps({"state": "fail", "log": "/tmp/x.log", "tail": ["E boom"],
                                                         "why": "exit code 1"}), encoding="utf-8")
        self.e._done_gates()
        tell = [m for m in self.s.pending("docs") if m["kind"] == "tell"]
        self.assertTrue(tell and "E boom" in tell[0]["text"] and "/tmp/x.log" in tell[0]["text"])
        self.assertEqual(self.s.reports(self.tid), [])
        self.assertEqual([m for m in self.s.pending(HUB) if m["kind"] == "done"], [])
        self.assertIn("done gate", self.report("fixed for real"))  # resend starts a new gate
        self.assertTrue(donegate.running(self.cfg, self.tid))

    def test_no_gated_change_reports_at_once(self) -> None:
        cfg = workspace({"paths": ["lib"], "command": check("")})
        s = Store(cfg.db)
        tid = s.add_task("docs", "t", "b")
        s.claim_task(tid)
        from agent_hub import cli
        with mock.patch.dict(os.environ, {"TOWER_HOME": str(cfg.home), "TOWER_LANE": "docs"}), redirect_stdout(io.StringIO()):
            cli.main(["report", "done", "ok"])
        self.assertEqual([r["kind"] for r in s.reports(tid)], ["done"])


if __name__ == "__main__":
    unittest.main()
