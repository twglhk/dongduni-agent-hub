"""Adapters against stand-in CLIs and a stand-in OpenCode server (no real agent runs, no tokens spent)."""

import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent_hub.config import ConfigError
from agent_hub.runtimes import Runtimes
from agent_hub.store import Store
from tests.helpers import make_home

FAKE_CLAUDE = r'''#!PY
import json, os, sys, time
args = sys.argv[1:]
sid = args[args.index("--session-id") + 1] if "--session-id" in args else args[args.index("--resume") + 1]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"argv": args, "lane": os.environ.get("TOWER_LANE"), "home": os.environ.get("TOWER_HOME")}) + "\n")
time.sleep(float(os.environ.get("FAKE_SLEEP", "0.3")))
print(json.dumps({"type": "result", "session_id": sid, "result": "said: " + args[-1][:20],
                  "usage": {"input_tokens": 1000, "cache_read_input_tokens": 41000, "cache_creation_input_tokens": 0}}))
'''

FAKE_CODEX = r'''#!PY
import json, os, sys, time
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"argv": args}) + "\n")
tid = args[args.index("resume") + 1] if "resume" in args else "thread-123"
print(json.dumps({"type": "thread.started", "thread_id": tid}), flush=True)
time.sleep(0.3)
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "codex says hi"}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12000, "output_tokens": 50}}))
'''


def script(dirpath: Path, name: str, body: str) -> Path:
    p = dirpath / name
    p.write_text(body.replace("#!PY", "#!" + sys.executable))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def wait_for(fn, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while not fn():
        if time.time() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.05)


class HeadlessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.log = self.tmp / "calls.jsonl"
        os.environ["FAKE_LOG"] = str(self.log)
        os.environ["TOWER_CLAUDE_BIN"] = str(script(self.tmp, "claude", FAKE_CLAUDE))
        os.environ["TOWER_CODEX_BIN"] = str(script(self.tmp, "codex", FAKE_CODEX))
        self.cfg = make_home({"hub": {"runtime": "claude", "model": "opus"}, "docs": {"runtime": "codex"},
                              "review": {"runtime": "claude", "agent": "code-reviewer"}})
        self.s = Store(self.cfg.db)
        self.rt = Runtimes(self.cfg, self.s)

    def tearDown(self) -> None:
        for k in ("FAKE_LOG", "TOWER_CLAUDE_BIN", "TOWER_CODEX_BIN"):
            os.environ.pop(k, None)

    def calls(self) -> list[dict]:
        return [json.loads(x) for x in self.log.read_text().splitlines()]

    def test_claude_session_lifecycle(self) -> None:
        self.assertEqual(self.rt.status("hub"), "closed")
        sid = self.rt.open("hub", "hello there")
        self.assertEqual(self.rt.status("hub"), "busy")
        wait_for(lambda: self.rt.status("hub") == "idle")
        first = self.calls()[0]
        self.assertIn("--session-id", first["argv"])
        self.assertIn("bypassPermissions", first["argv"])
        self.assertEqual(first["argv"][first["argv"].index("--model") + 1], "opus")
        self.assertNotIn("--agent", first["argv"])  # no agent set: the runtime's default
        self.assertEqual(first["lane"], "hub")
        self.assertEqual(first["home"], str(self.cfg.home))
        self.assertEqual(self.rt.last_text("hub"), ["said: hello there"])
        self.assertEqual(self.rt.context_k("hub", sid), 42)
        self.rt.send("hub", sid, "second")
        wait_for(lambda: self.rt.status("hub") == "idle")
        second = self.calls()[1]["argv"]
        self.assertEqual(second[second.index("--resume") + 1], sid)
        self.rt.close("hub")
        self.assertEqual(self.rt.status("hub"), "closed")

    def test_close_kills_a_running_turn(self) -> None:
        os.environ["FAKE_SLEEP"] = "30"
        try:
            self.rt.open("hub", "long")
            self.assertEqual(self.rt.status("hub"), "busy")
            self.rt.close("hub")
            self.assertEqual(self.rt.status("hub"), "closed")
        finally:
            os.environ.pop("FAKE_SLEEP")

    def test_claude_lane_runs_its_agent_on_every_prompt(self) -> None:
        sid = self.rt.open("review", "look")
        wait_for(lambda: self.rt.status("review") == "idle")
        self.rt.send("review", sid, "again")
        wait_for(lambda: self.rt.status("review") == "idle")
        for call in self.calls():
            self.assertEqual(call["argv"][call["argv"].index("--agent") + 1], "code-reviewer")

    def test_codex_rejects_an_agent(self) -> None:
        with self.assertRaises(ConfigError):
            make_home({"hub": {"runtime": "codex", "agent": "x"}})

    def test_codex_thread_id_and_resume(self) -> None:
        sid = self.rt.open("docs", "do docs")
        self.assertEqual(sid, "thread-123")
        wait_for(lambda: self.rt.status("docs") == "idle")
        self.assertEqual(self.rt.last_text("docs"), ["codex says hi"])
        self.assertEqual(self.rt.context_k("docs", sid), 12)
        self.rt.send("docs", sid, "more")
        wait_for(lambda: self.rt.status("docs") == "idle")
        argv = self.calls()[1]["argv"]
        self.assertEqual(argv[argv.index("resume") + 1], "thread-123")
        self.assertIn("--json", argv)


class FakeOpenCode(BaseHTTPRequestHandler):
    busy: dict = {}
    questions: list = []
    prompts: list = []

    def log_message(self, *a) -> None:
        pass

    def _send(self, code: int, body) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/session/status":
            return self._send(200, FakeOpenCode.busy)
        if self.path == "/question":
            return self._send(200, FakeOpenCode.questions)
        if self.path.endswith("/message"):
            return self._send(200, [{"info": {"role": "assistant", "tokens": {"input": 5000, "cache": {"read": 95000}}},
                                     "parts": [{"type": "text", "text": "done here"}]}])
        self._send(404, {})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        if self.path == "/session":
            return self._send(200, {"id": "ses_1"})
        if self.path.endswith("/prompt_async"):
            FakeOpenCode.prompts.append(body)
            return self._send(200, {})
        if self.path.startswith("/question/"):
            FakeOpenCode.questions = []
            return self._send(200, True)
        self._send(200, {})


class OpenCodeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenCode)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        port = self.srv.server_address[1]
        self.cfg = make_home({"hub": {"runtime": "opencode", "port": port, "model": "anthropic/claude-x"}})
        self.s = Store(self.cfg.db)
        self.rt = Runtimes(self.cfg, self.s)
        FakeOpenCode.busy, FakeOpenCode.questions, FakeOpenCode.prompts = {}, [], []

    def tearDown(self) -> None:
        self.srv.shutdown()

    def test_uses_a_server_a_human_started_and_never_owns_it(self) -> None:
        sid = self.rt.open("hub", "hi")
        self.assertEqual(sid, "ses_1")
        self.assertEqual(FakeOpenCode.prompts[0]["model"], {"providerID": "anthropic", "modelID": "claude-x"})
        self.assertNotIn("agent", FakeOpenCode.prompts[0])  # no agent set: the server's default agent
        self.assertFalse(self.rt.owned("hub"))
        self.assertEqual(self.rt.status("hub"), "idle")
        FakeOpenCode.busy = {"ses_1": {"type": "busy"}}
        self.assertEqual(self.rt.status("hub"), "busy")
        FakeOpenCode.questions = [{"id": "q1", "questions": []}]
        self.assertEqual(self.rt.status("hub"), "asking")
        self.rt.reply_question("hub", "q1", [["x"]])
        self.assertEqual(self.rt.questions("hub"), [])
        self.assertEqual(self.rt.context_k("hub", sid), 100)
        self.assertEqual(self.rt.last_text("hub"), ["done here"])
        FakeOpenCode.busy = {}
        self.rt.close("hub")  # not owned: clears the session, leaves the server up
        self.assertEqual(self.rt.status("hub"), "idle")

    def test_lane_agent_goes_with_every_prompt(self) -> None:
        cfg = make_home({"hub": {"runtime": "opencode", "port": self.srv.server_address[1], "agent": "writer"}})
        rt = Runtimes(cfg, Store(cfg.db))
        sid = rt.open("hub", "hi")
        rt.send("hub", sid, "more")
        self.assertEqual([p["agent"] for p in FakeOpenCode.prompts], ["writer", "writer"])


if __name__ == "__main__":
    unittest.main()
