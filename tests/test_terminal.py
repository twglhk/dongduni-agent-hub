"""Lanes in terminal windows (tmux, Orca) against stand-in terminal apps and stand-in interactive agents.

The stand-in terminal app really starts each window's command, so the launcher script, the lane's environment,
Claude's hooks and Codex's notify all run for real; no model is called. POSIX only: on Windows the launcher is a
.cmd file and tmux does not exist."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_hub import config as config_mod
from agent_hub.config import ConfigError
from agent_hub.cli import close_changed
from agent_hub.runtimes import (Orca, RuntimeFailure, Runtimes, codex_notify, record_signal, signal_path,
                                tmux_session)
from agent_hub.store import Store
from tests.helpers import make_home
from tests.test_runtimes import script, wait_for

# One stand-in for both tmux and orca (picked by its file name). Windows are processes it starts in their own
# process group; typed text goes to a file the process reads.
FAKE_TERM = r'''#!PY
import json, os, signal, subprocess, sys
state_dir = os.environ["FAKE_TERM_DIR"]
db = os.path.join(state_dir, "windows.json")
wins = json.load(open(db)) if os.path.exists(db) else {}
def save():
    json.dump(wins, open(db, "w"))
def alive(w):
    try:
        os.kill(w["pid"], 0)
    except OSError:
        return False
    return True
def spawn(cmd, cwd, title, argv):
    wid = ("@" if tmux else "term-") + str(len(wins) + 1)
    inp = os.path.join(state_dir, wid.strip("@") + ".in")
    open(inp, "w").close()
    p = subprocess.Popen(["sh", "-c", cmd], cwd=cwd, env={**os.environ, "FAKE_WINDOW_IN": inp},
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    wins[wid] = {"pid": p.pid, "title": title, "in": inp, "argv": argv}
    save()
    return wid
def kill(wid):
    w = wins.get(wid)
    if w and alive(w):
        os.killpg(w["pid"], signal.SIGTERM)
def opt(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default
args = sys.argv[1:]
tmux = os.path.basename(sys.argv[0]).startswith("tmux")
if tmux:
    cmd = args[0]
    if cmd == "has-session":
        sys.exit(0 if any(alive(w) for w in wins.values()) else 1)
    if cmd in ("new-session", "new-window"):
        print(spawn(args[-1], opt(args, "-c"), opt(args, "-n"), args))
    elif cmd == "list-windows":
        if not any(alive(w) for w in wins.values()):
            print("no server running on /tmp/tmux-fake", file=sys.stderr)
            sys.exit(1)
        print("\n".join(f"{k} 0" for k, w in wins.items() if alive(w)))
    elif cmd == "send-keys":
        w = wins[opt(args, "-t")]
        with open(w["in"], "a") as f:
            f.write(args[-1] if "-l" in args else "\n")
    elif cmd == "kill-window":
        kill(opt(args, "-t"))
    sys.exit(0)
sub = args[1]
if sub == "create":
    wid = spawn(opt(args, "--command"), opt(args, "--worktree").split(":", 1)[1], opt(args, "--title"), args)
    print(json.dumps({"result": {"terminal": {"handle": wid}}}))
elif sub == "show":
    w = wins.get(opt(args, "--terminal"))
    if not w or not alive(w):
        print(json.dumps({"error": "terminal_handle_stale"}))
        sys.exit(1)
    print(json.dumps({"result": {"terminal": {"exited": False}}}))
elif sub == "send":
    with open(wins[opt(args, "--terminal")]["in"], "a") as f:
        f.write(opt(args, "--text") + ("\n" if "--enter" in args else ""))
elif sub == "close":
    kill(opt(args, "--terminal"))
'''

# Interactive Claude Code: the first prompt is the last argument, later ones are typed lines. Each turn runs the
# hooks from --settings with what Claude would pass on stdin, and writes the reply to a transcript.
FAKE_TUI_CLAUDE = r'''#!PY
import json, os, re, subprocess, sys, time
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": args, "lane": os.environ.get("TOWER_LANE"), "home": os.environ.get("TOWER_HOME"),
                        "cwd": os.getcwd()}) + "\n")
hooks = json.load(open(args[args.index("--settings") + 1]))["hooks"]
sid = args[args.index("--session-id") + 1]
transcript = os.path.join(os.environ["FAKE_TERM_DIR"], sid + ".jsonl")
def hook(event, **extra):
    for group in hooks.get(event, []):
        for h in group["hooks"]:
            payload = {"hook_event_name": event, "session_id": sid, "transcript_path": transcript, **extra}
            subprocess.run(h["command"], shell=True, input=json.dumps(payload), text=True)
def ended():
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".%03dZ" % (time.time() % 1 * 1000)
    with open(transcript, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "system", "subtype": "turn_duration", "timestamp": stamp}) + "\n")
def turn(line):
    hook("UserPromptSubmit", prompt=line)
    if line == "interrupt me":  # a person pressed Esc: the turn ends without a Stop hook
        time.sleep(0.2)
        ended()
        return
    m = re.search(r"in (\S+): read that file", line)
    text = open(m.group(1), encoding="utf-8").read() if m else line
    time.sleep(0.2)
    hook("PostToolUse", tool_name="Read")
    with open(transcript, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "did: " + text}],
                 "usage": {"input_tokens": 1000, "cache_read_input_tokens": 9000, "output_tokens": 50}}}) + "\n")
    ended()
    hook("Stop")
turn(args[-1])
seen = 0
while True:
    data = open(os.environ["FAKE_WINDOW_IN"], encoding="utf-8").read()
    while "\n" in data[seen:]:
        end = data.index("\n", seen)
        turn(data[seen:end])
        seen = end + 1
    time.sleep(0.05)
'''

# Interactive Codex: reports each finished turn to its notify program, with the JSON as the last argument.
FAKE_TUI_CODEX = r'''#!PY
import json, os, re, subprocess, sys, time
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": args, "lane": os.environ.get("TOWER_LANE")}) + "\n")
notify = json.loads(next(a for a in args if a.startswith("notify="))[len("notify="):])
def turn(line):
    m = re.search(r"in (\S+): read that file", line)
    text = open(m.group(1), encoding="utf-8").read() if m else line
    time.sleep(0.2)
    subprocess.run([*notify, json.dumps({"type": "agent-turn-complete", "thread-id": "th-1",
                                         "last-assistant-message": "codex did: " + text})])
turn(args[-1])
seen = 0
while True:
    data = open(os.environ["FAKE_WINDOW_IN"], encoding="utf-8").read()
    while "\n" in data[seen:]:
        end = data.index("\n", seen)
        turn(data[seen:end])
        seen = end + 1
    time.sleep(0.05)
'''

# The OpenCode TUI: its own server answers the tower's HTTP calls.
FAKE_TUI_OPENCODE = r'''#!PY
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def reply(self, code, body=None):
        raw = b"" if body is None else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        self.reply(200, {} if self.path == "/session/status" else [])
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"null")
        with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
            f.write(json.dumps({"path": self.path, "body": body, "lane": os.environ.get("TOWER_LANE")}) + "\n")
        if self.path == "/session":
            self.reply(200, {"id": "ses-1"})
        elif self.path.endswith("/prompt_async"):
            self.reply(204)
        else:
            self.reply(200, True)
HTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipIf(os.name == "nt", "terminal lanes are tested on POSIX (no tmux on Windows)")
class TerminalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.log = self.tmp / "calls.jsonl"
        self.env = {"FAKE_LOG": str(self.log), "FAKE_TERM_DIR": str(self.tmp),
                    "TOWER_TMUX_BIN": str(script(self.tmp, "tmux", FAKE_TERM)),
                    "TOWER_ORCA_BIN": str(script(self.tmp, "orca", FAKE_TERM)),
                    "TOWER_CLAUDE_BIN": str(script(self.tmp, "claude", FAKE_TUI_CLAUDE)),
                    "TOWER_CODEX_BIN": str(script(self.tmp, "codex", FAKE_TUI_CODEX)),
                    "TOWER_OPENCODE_BIN": str(script(self.tmp, "opencode", FAKE_TUI_OPENCODE))}
        os.environ.update(self.env)
        self.port = free_port()
        self.cfg = make_home({"hub": {"runtime": "claude", "model": "opus"},
                              "docs": {"runtime": "codex", "terminal": "orca"},
                              "web": {"runtime": "opencode", "port": self.port},
                              "quiet": {"runtime": "claude", "terminal": "none"}}, terminal="tmux")
        self.s = Store(self.cfg.db)
        self.rt = Runtimes(self.cfg, self.s)

    def tearDown(self) -> None:
        for lane in ("hub", "docs", "web"):
            self.rt.close(lane)
        for k in self.env:
            os.environ.pop(k, None)

    def settle(self, lane: str = "hub") -> None:
        """Idle, and the turn's last hook has run (the transcript can show the end a moment before Stop)."""
        wait_for(lambda: self.rt.status(lane) == "idle"
                 and json.loads(signal_path(self.cfg.home, lane).read_text()).get("state") == "idle")

    def calls(self) -> list[dict]:
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def windows(self) -> dict:
        return json.loads((self.tmp / "windows.json").read_text())

    def test_settings_pick_a_terminal_per_lane(self) -> None:
        lanes = self.cfg.lanes
        self.assertEqual((lanes["hub"].terminal, lanes["docs"].terminal, lanes["quiet"].terminal), ("tmux", "orca", "none"))
        self.assertEqual(type(self.rt.adapters["quiet"]).__name__, "Claude")
        with self.assertRaises(ConfigError):
            make_home({"hub": {"runtime": "claude"}}, terminal="screen")
        with self.assertRaises(ConfigError):
            make_home({"hub": {"runtime": "claude", "terminal": "wt"}})
        data = config_mod.template("T", "en", "claude", "claude", terminal="tmux")
        self.assertEqual(data["terminal"], "tmux")
        self.assertNotIn("terminal", config_mod.template("T", "en", "claude", "claude"))
        self.assertEqual(tmux_session(self.cfg), "dah-" + self.cfg.home.name)

    def test_claude_in_a_tmux_window(self) -> None:
        self.assertEqual(self.rt.status("hub"), "closed")
        sid = self.rt.open("hub", "first brief\nwith two lines")
        self.assertEqual(self.rt.status("hub"), "busy")
        wait_for(lambda: self.rt.last_text("hub") == ["did: first brief\nwith two lines"])
        self.settle()
        first = self.calls()[0]
        self.assertEqual(first["argv"][first["argv"].index("--session-id") + 1], sid)
        self.assertNotIn("-p", first["argv"])  # interactive, not headless
        self.assertEqual(first["argv"][first["argv"].index("--model") + 1], "opus")
        self.assertIn("bypassPermissions", first["argv"])
        self.assertEqual((first["lane"], first["home"]), ("hub", str(self.cfg.home)))
        self.assertEqual(Path(first["cwd"]).resolve(), self.cfg.workdir.resolve())  # macOS: /var is /private/var
        win = next(iter(self.windows().values()))
        self.assertEqual((win["title"], win["argv"][0]), ("hub", "new-session"))
        self.assertEqual(self.rt.usage("hub", sid), {"tokens_in": 10000, "tokens_out": 50, "cost": None, "ctx_k": 10})

        self.rt.send("hub", sid, "second")
        self.assertEqual(self.rt.status("hub"), "busy")
        wait_for(lambda: self.rt.last_text("hub") == ["did: second"])
        self.settle()
        record_signal(self.cfg.home, "hub", {"hook_event_name": "UserPromptSubmit"})  # a person typed in the window
        self.assertEqual(self.rt.status("hub"), "busy")
        with self.assertRaises(RuntimeFailure):  # the tower waits for the turn to end
            self.rt.send("hub", sid, "third")
        record_signal(self.cfg.home, "hub", {"hook_event_name": "Stop"})

        self.rt.close("hub")
        self.assertEqual(self.rt.status("hub"), "closed")
        self.assertEqual([k for k in self.windows() if self._alive(k)], [])
        self.assertEqual(sorted(p.name for p in (self.cfg.state_dir / "term").glob("hub*")),
                         ["hub.settings.json", "hub.sh"])  # notes, handle and signal are gone

    def _alive(self, wid: str) -> bool:
        out = subprocess.run([self.env["TOWER_TMUX_BIN"], "list-windows", "-a", "-F", "#{window_id} #{pane_dead}"],
                             capture_output=True, text=True).stdout.split()
        return wid in out

    def test_a_turn_a_person_interrupts_ends_without_a_stop_hook(self) -> None:
        self.rt.open("hub", "hi")
        self.settle()
        stop_hooks = json.loads(signal_path(self.cfg.home, "hub").read_text())["at"]
        subprocess.run([self.env["TOWER_TMUX_BIN"], "send-keys", "-t", next(iter(self.windows())), "-l", "interrupt me"])
        subprocess.run([self.env["TOWER_TMUX_BIN"], "send-keys", "-t", next(iter(self.windows())), "Enter"])
        wait_for(lambda: json.loads(signal_path(self.cfg.home, "hub").read_text())["at"] > stop_hooks)  # busy
        wait_for(lambda: self.rt.status("hub") == "idle")
        self.assertEqual(json.loads(signal_path(self.cfg.home, "hub").read_text())["state"], "busy")  # no Stop came

    def test_a_person_closing_the_window_closes_the_lane(self) -> None:
        self.rt.open("hub", "hi")
        self.settle()
        subprocess.run([self.env["TOWER_TMUX_BIN"], "kill-window", "-t", next(iter(self.windows()))])
        wait_for(lambda: self.rt.status("hub") == "closed")

    def test_a_permission_prompt_shows_as_asking(self) -> None:
        self.rt.open("hub", "hi")
        self.settle()
        record_signal(self.cfg.home, "hub", {"hook_event_name": "Notification", "notification_type": "permission_prompt"})
        self.assertEqual(self.rt.status("hub"), "asking")
        with self.assertRaises(RuntimeFailure):
            self.rt.send("hub", "x", "not now")
        record_signal(self.cfg.home, "hub", {"hook_event_name": "PostToolUse"})
        self.assertEqual(self.rt.status("hub"), "busy")
        record_signal(self.cfg.home, "hub", {"hook_event_name": "Notification", "notification_type": "auth_success"})
        self.assertEqual(self.rt.status("hub"), "busy")  # other notifications change nothing
        record_signal(self.cfg.home, "hub", {"hook_event_name": "Notification", "notification_type": "idle_prompt"})
        self.assertEqual(self.rt.status("hub"), "idle")  # a turn a person interrupted ends without a Stop hook

    def test_the_persons_own_codex_notify_still_runs(self) -> None:
        mine = self.tmp / "mine.jsonl"
        hook = script(self.tmp, "my-notify", "#!PY\nimport sys\nopen(%r, 'a').write(sys.argv[-1] + '\\n')\n" % str(mine))
        codex_home = self.tmp / "codex-home"
        codex_home.mkdir()
        (codex_home / "config.toml").write_text(f'model = "gpt"\nnotify = ["{hook}"]\n\n[profiles.fast]\nmodel = "mini"\n')
        os.environ["CODEX_HOME"] = str(codex_home)
        try:
            self.rt.open("docs", "write the docs")
            wait_for(lambda: self.rt.status("docs") == "idle")
            wait_for(lambda: mine.exists())
        finally:
            os.environ.pop("CODEX_HOME")
        self.assertEqual(json.loads(mine.read_text().splitlines()[0])["type"], "agent-turn-complete")
        argv = self.calls()[0]["argv"]
        self.assertEqual(len([a for a in argv if a.startswith("notify=")]), 1)  # the tower's, which passes it on

    def test_reading_codex_notify(self) -> None:
        cfg = self.tmp / "ch"
        cfg.mkdir()
        (cfg / "config.toml").write_text('notify = ["top", \'x y\']\n[profiles.p]\nnotify = ["prof"]\n[mcp_servers.m]\n'
                                         'command = "z"\n')
        os.environ["CODEX_HOME"] = str(cfg)
        try:
            self.assertEqual(codex_notify(["--sandbox", "workspace-write"]), (["--sandbox", "workspace-write"], ["top", "x y"]))
            self.assertEqual(codex_notify(["--profile", "p"])[1], ["prof"])
            self.assertEqual(codex_notify(["-c", 'notify=["own"]', "--x"]), (["--x"], ["own"]))
            self.assertEqual(codex_notify(["-c", 'notify=["python3","-m","agent_hub","signal"]'])[1], [])
        finally:
            os.environ.pop("CODEX_HOME")

    def test_changing_a_lanes_terminal_closes_its_live_session_first(self) -> None:
        self.rt.open("hub", "hi")
        self.settle()
        data = json.loads((self.cfg.home / ".tower/tower.json").read_text())
        data["lanes"]["hub"]["terminal"] = "none"
        (self.cfg.home / ".tower/tower.json").write_text(json.dumps(data))
        said: list[str] = []
        close_changed(self.rt, self.cfg, config_mod.load(self.cfg.home), said.append)
        self.assertEqual(self.rt.status("hub"), "closed")
        self.assertEqual([k for k in self.windows() if self._alive(k)], [])
        self.assertIn("hub: runtime, terminal or worktree changed", said[0])
        close_changed(self.rt, self.cfg, config_mod.load(self.cfg.home), said.append)
        self.assertEqual(len(said), 1)  # closed lanes are left alone

    def test_usage_counts_each_api_call_once(self) -> None:
        sid = self.rt.open("hub", "hi")
        self.settle()
        transcript = json.loads(signal_path(self.cfg.home, "hub").read_text())["transcript"]
        call = {"id": "msg_1", "content": [{"type": "text", "text": "x"}],
                "usage": {"input_tokens": 100, "cache_read_input_tokens": 0, "output_tokens": 7}}
        with open(transcript, "a", encoding="utf-8") as f:  # one call: a thinking line and a text line
            for _ in range(2):
                f.write(json.dumps({"type": "assistant", "message": call}) + "\n")
        self.assertEqual(self.rt.usage("hub", sid)["tokens_in"], 10000 + 100)

    def test_orca_that_does_not_answer_is_unknown_not_closed(self) -> None:
        self.rt.open("docs", "x")
        wait_for(lambda: self.rt.status("docs") == "idle")
        broken = script(self.tmp, "orca-broken", "#!PY\nimport sys\nprint('runtime socket unavailable', file=sys.stderr)\n"
                        "sys.exit(1)\n")
        os.environ["TOWER_ORCA_BIN"] = str(broken)
        try:
            with self.assertRaises(RuntimeFailure):  # the engine records "unknown" and opens nothing new
                self.rt.status("docs")
        finally:
            os.environ["TOWER_ORCA_BIN"] = self.env["TOWER_ORCA_BIN"]
        self.assertEqual(self.rt.status("docs"), "idle")
        nested = {"result": {"worktree": {"handle": "wt-9"}, "terminal": {"handle": "term-7"}}}
        self.assertEqual(Orca._find(Orca._find_obj(nested, "terminal"), "handle"), "term-7")

    def test_codex_in_an_orca_tab(self) -> None:
        sid = self.rt.open("docs", "write the docs")
        self.assertTrue(sid.startswith("term-"))
        wait_for(lambda: self.rt.status("docs") == "idle")
        self.assertEqual(self.rt.last_text("docs"), ["codex did: write the docs"])
        argv = self.calls()[0]["argv"]
        self.assertNotIn("--skip-git-repo-check", argv)  # exec-only flag
        self.assertNotIn("exec", argv)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", argv)
        tab = next(iter(self.windows().values()))
        self.assertEqual(tab["argv"][tab["argv"].index("--worktree") + 1], f"path:{self.cfg.workdir}")
        self.assertTrue(tab["argv"][tab["argv"].index("--command") + 1].startswith("exec "))
        self.rt.send("docs", sid, "and the changelog")
        wait_for(lambda: self.rt.last_text("docs") == ["codex did: and the changelog"])
        self.rt.close("docs")
        self.assertEqual(self.rt.status("docs"), "closed")

    def test_opencode_tui_in_a_window(self) -> None:
        sid = self.rt.open("web", "build the page")
        self.assertEqual(sid, "ses-1")
        self.assertTrue(self.rt.owned("web"))
        self.assertEqual(self.rt.status("web"), "idle")
        prompt = [c for c in self.calls() if c.get("path", "").endswith("/prompt_async")][0]
        self.assertEqual(prompt["body"]["parts"][0]["text"], "build the page")  # over HTTP, not typed
        self.assertEqual(prompt["lane"], "web")
        self.rt.close("web")
        self.assertEqual(self.rt.status("web"), "closed")


class SignalTest(unittest.TestCase):
    def test_hooks_and_notify_set_the_state(self) -> None:
        home = Path(tempfile.mkdtemp())
        record_signal(home, "hub", {"hook_event_name": "UserPromptSubmit", "transcript_path": "/t.jsonl", "session_id": "s"})
        self.assertEqual(json.loads(signal_path(home, "hub").read_text())["state"], "busy")
        record_signal(home, "hub", {"hook_event_name": "Stop"})
        data = json.loads(signal_path(home, "hub").read_text())
        self.assertEqual((data["state"], data["transcript"]), ("idle", "/t.jsonl"))  # earlier fields stay
        record_signal(home, "docs", {"type": "agent-turn-complete", "thread-id": "th", "last-assistant-message": "ok"})
        data = json.loads(signal_path(home, "docs").read_text())
        self.assertEqual((data["state"], data["last"], data["session"]), ("idle", "ok", "th"))

    def test_dah_version(self) -> None:
        from agent_hub import __version__
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        out = subprocess.run([sys.executable, "-m", "agent_hub", "--version"], env=env, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), f"dah {__version__}")

    def test_dah_signal_never_fails_the_agent(self) -> None:
        home = Path(tempfile.mkdtemp())
        env = {**os.environ, "TOWER_HOME": str(home), "TOWER_LANE": "hub",
               "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        ok = subprocess.run([sys.executable, "-m", "agent_hub", "signal"], input='{"hook_event_name": "Stop"}',
                            env=env, capture_output=True, text=True)
        self.assertEqual((ok.returncode, ok.stdout), (0, ""))
        self.assertEqual(json.loads(signal_path(home, "hub").read_text())["state"], "idle")
        bad = subprocess.run([sys.executable, "-m", "agent_hub", "signal", "not json"], env=env,
                             capture_output=True, text=True)
        self.assertEqual(bad.returncode, 0)
        self.assertIn("hub:", (home / ".tower/state/term/signal.err").read_text())


if __name__ == "__main__":
    unittest.main()
