"""The lane hook (agent_hub/hooks.py): the PreToolUse runner, urgent tells, the event log, and how tower-opened
Claude Code lanes get it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_hub import gates, hooks
from agent_hub.runtimes import Claude, ClaudeTerm
from agent_hub.store import Store
from tests.helpers import make_home

ROOT = Path(__file__).resolve().parents[1]


def events(home: Path, lane: str) -> list[dict]:
    p = hooks.lane_dir(home, lane) / "events.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


class HookProcess(unittest.TestCase):
    """The hook as Claude Code runs it: the file by path, JSON on stdin, no PYTHONPATH."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())

    def run_hook(self, payload: object, lane: str = "docs", raw: str | None = None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "DAH_GATES")}
        env.update(TOWER_HOME=str(self.home), TOWER_LANE=lane)
        cmd = hooks.hook_command()
        return subprocess.run(cmd, shell=True, input=raw if raw is not None else json.dumps(payload),
                              capture_output=True, text=True, encoding="utf-8", env=env, cwd=tempfile.gettempdir())

    def test_deny_is_exit_2_with_the_reason_on_stderr_and_a_gate_deny_event(self) -> None:
        p = self.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "s1",
                           "tool_input": {"command": "pkill -f opencode"}, "cwd": "/tmp"})
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("Kill gate refused", p.stderr)
        self.assertEqual(p.stdout, "")
        self.assertEqual([(e["event"], e["gate"], e["tool"], e["session_id"]) for e in events(self.home, "docs")],
                         [("GateDeny", "kill", "Bash", "s1")])

    def test_allow_is_silent_exit_0(self) -> None:
        p = self.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls -la"}})
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))
        self.assertEqual(events(self.home, "docs"), [])

    def test_bad_input_never_blocks(self) -> None:
        for raw in ("not json", "", "[1, 2]", '{"tool_name": 5, "tool_input": "x"}'):
            with self.subTest(raw):
                self.assertEqual(self.run_hook(None, raw=raw).returncode, 0)

    def test_stop_failure_is_recorded_and_prints_nothing(self) -> None:
        p = self.run_hook({"hook_event_name": "StopFailure", "session_id": "s", "error": "authentication_failed",
                           "error_details": "x" * 5000})
        self.assertEqual((p.returncode, p.stdout), (0, ""))
        [ev] = events(self.home, "docs")
        self.assertEqual((ev["event"], ev["error"]), ("StopFailure", "authentication_failed"))
        self.assertEqual(len(ev["error_details"]), 1000)

    def test_urgent_tell_reaches_the_next_tool_call_once(self) -> None:
        hooks.post_urgent(self.home, "docs", "stop now and report blocked")
        call = {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}}
        p = self.run_hook(call)
        self.assertEqual(p.returncode, 0)
        ctx = json.loads(p.stdout)["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "PreToolUse")
        self.assertIn("URGENT", ctx["additionalContext"])
        self.assertIn("stop now and report blocked", ctx["additionalContext"])
        self.assertEqual(self.run_hook(call).stdout, "")  # delivered once


class Pretool(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())

    def call(self, tool: str = "Bash", **inp: object) -> dict:
        return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inp or {"command": "ls"}}

    def test_a_crashing_gate_fails_open_and_is_logged(self) -> None:
        with mock.patch.object(gates, "check", side_effect=RuntimeError("boom")):
            code, out, err = hooks.pretool(self.call(command="pkill -f opencode"), self.home, "docs")
        self.assertEqual((code, out, err), (0, "", ""))
        log = (hooks.root(self.home) / "gate-errors.log").read_text(encoding="utf-8")
        self.assertIn("RuntimeError: boom", log)
        self.assertIn("docs Bash", log)

    def test_a_deny_carries_a_waiting_urgent_message_too(self) -> None:
        hooks.post_urgent(self.home, "docs", "stop")
        code, _, err = hooks.pretool(self.call(command="git stash"), self.home, "docs")
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("URGENT from the hub"))
        self.assertIn("Git sweep gate refused", err)

    def test_subagents_and_opencode_do_not_consume_urgent_tells(self) -> None:
        hooks.post_urgent(self.home, "docs", "stop")
        self.assertEqual(hooks.pretool({**self.call(), "agent_id": "a1"}, self.home, "docs")[1], "")
        self.assertEqual(hooks.pretool({**self.call(), "runtime": "opencode"}, self.home, "docs")[1], "")
        self.assertIn("stop", hooks.pretool(self.call(), self.home, "docs")[1])

    def test_without_the_tower_the_gates_still_run(self) -> None:
        code, _, err = hooks.pretool(self.call(command="kill -9 -1"), None, None)
        self.assertEqual(code, 2)
        self.assertIn("Kill gate", err)

    def test_gates_can_be_turned_off_per_lane(self) -> None:
        with mock.patch.dict(os.environ, {"DAH_GATES": "off"}):
            self.assertEqual(hooks.pretool(self.call(command="git stash"), self.home, "docs")[0], 0)

    def test_event_log_is_trimmed(self) -> None:
        d = hooks.lane_dir(self.home, "docs")
        d.mkdir(parents=True)
        line = json.dumps({"event": "X", "pad": "p" * 500})
        (d / "events.jsonl").write_text((line + "\n") * (hooks.MAX_LINES + 10), encoding="utf-8")
        hooks.append_event(self.home, "docs", {"event": "GateDeny"})
        lines = (d / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), hooks.MAX_LINES // 2 + 1)
        self.assertEqual(json.loads(lines[-1])["event"], "GateDeny")


class Urgent(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())

    def test_oldest_first_and_messages_over_two_hours_dropped(self) -> None:
        now = time.time()
        hooks.post_urgent(self.home, "docs", "old", now=now - hooks.URGENT_MAX_AGE_S - 60)
        hooks.post_urgent(self.home, "docs", "first", now=now - 10)
        hooks.post_urgent(self.home, "docs", "second", now=now - 5)
        hooks.post_urgent(self.home, "docs", "   ", now=now)
        self.assertEqual(hooks.take_urgent(self.home, "docs", now=now), ["first", "second"])
        self.assertEqual(hooks.pending_urgent(self.home, "docs"), 0)  # the stale and the blank are gone too
        self.assertEqual(hooks.take_urgent(self.home, "other", now=now), [])

    def test_two_posts_in_one_millisecond_both_arrive(self) -> None:
        hooks.post_urgent(self.home, "docs", "a", now=100.0)
        hooks.post_urgent(self.home, "docs", "b", now=100.0)
        self.assertEqual(sorted(hooks.take_urgent(self.home, "docs", now=100.0)), ["a", "b"])

    def test_a_file_another_hook_already_took_is_skipped(self) -> None:
        hooks.post_urgent(self.home, "docs", "once")
        with mock.patch.object(Path, "unlink", side_effect=FileNotFoundError):
            self.assertEqual(hooks.take_urgent(self.home, "docs"), [])


class TellUrgentCli(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home({"hub": {"runtime": "claude"}, "docs": {"runtime": "claude"}, "web": {"runtime": "codex"}})

    def tower(self, *args: str, lane: str = "hub") -> subprocess.CompletedProcess:
        env = {**os.environ, "TOWER_HOME": str(self.cfg.home), "PYTHONPATH": str(ROOT), "TOWER_LANE": lane}
        return subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.cfg.home, env=env,
                              capture_output=True, text=True, encoding="utf-8")

    def test_drops_a_file_for_the_lane_without_a_running_task(self) -> None:
        p = self.tower("tell", "--urgent", "docs", "stop")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("next tool call", p.stdout)
        self.assertEqual(hooks.take_urgent(self.cfg.home, "docs"), ["stop"])

    def test_warns_for_a_lane_that_cannot_read_it_and_refuses_unknown_lanes(self) -> None:
        self.assertIn("only Claude Code lanes", self.tower("tell", "--urgent", "web", "stop").stdout)
        self.assertNotEqual(self.tower("tell", "--urgent", "nope", "stop").returncode, 0)
        self.assertNotEqual(self.tower("tell", "--urgent", "docs", "stop", lane="docs").returncode, 0)  # hub only


class Wiring(unittest.TestCase):
    """Lanes the tower opens get the hook in their --settings."""

    def setUp(self) -> None:
        self.cfg = make_home({"hub": {"runtime": "claude"}, "docs": {"runtime": "claude", "terminal": "tmux"}})
        self.s = Store(self.cfg.db)

    @staticmethod
    def settings(argv: list[str]) -> dict:
        return json.loads(Path(argv[argv.index("--settings") + 1]).read_text(encoding="utf-8"))

    def test_headless_lane(self) -> None:
        argv = Claude(self.cfg, self.s, self.cfg.lanes["hub"])._base()
        h = self.settings(argv)["hooks"]
        self.assertEqual(h["PreToolUse"][0]["matcher"], "*")
        self.assertEqual(h["PreToolUse"][0]["hooks"][0]["command"], hooks.hook_command())
        self.assertIn("StopFailure", h)

    def test_terminal_lane_keeps_its_signal_hooks(self) -> None:
        argv = ClaudeTerm(self.cfg, self.s, self.cfg.lanes["docs"])._argv("--session-id", "x")
        h = self.settings(argv)["hooks"]
        self.assertTrue({"UserPromptSubmit", "PostToolUse", "Stop", "Notification", "PreToolUse", "StopFailure"} <= set(h))

    def test_opencode_plugin_calls_this_hook(self) -> None:
        text = hooks.opencode_plugin()
        self.assertIn(json.dumps(str(hooks.HOOK_FILE)), text)
        self.assertIn('"tool.execute.before"', text)
        self.assertIn("r.status === 2", text)
        self.assertNotIn("__HOOK__", text)


if __name__ == "__main__":
    unittest.main()
