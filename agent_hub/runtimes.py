"""Runtime adapters: how the tower starts, prompts, watches and stops an agent session for one lane.

  opencode  long-lived `opencode serve --port P` per lane; prompts over its HTTP API. Live `ask` questions
            are supported (they become board items). A human may run `opencode --port P` (TUI) instead;
            the tower then uses that server and never kills it.
  claude    headless Claude Code: `claude -p --session-id ID` to start, `claude -p --resume ID` per prompt.
  codex     headless Codex: `codex exec --json` to start, `codex exec resume ID` per prompt.

A lane with "terminal": "tmux" | "orca" runs the same agent interactively in a window the tower opens
(a tmux window in the `dah-<workspace>` session, or an Orca terminal tab) and closes when the lane is done.
People watch it and may type into it. Claude reports busy/idle through hooks the tower adds with
`--settings`, Codex through its `notify` program (both call `dah signal`); OpenCode runs its TUI, whose
HTTP API the tower drives as before. Prompts for Claude and Codex are written to a file and the window
gets one line asking the agent to read it, so long multi-line briefs never go through the keyboard.

Every adapter answers status(): closed (no session) | idle | busy | asking | unknown.
Binaries can be overridden with TOWER_OPENCODE_BIN / TOWER_CLAUDE_BIN / TOWER_CODEX_BIN /
TOWER_TMUX_BIN / TOWER_ORCA_BIN.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import procs
from .config import TOWER_DIR, Config, Lane
from .store import Store

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
PACKAGE_PARENT = Path(__file__).resolve().parents[1]  # where `python -m agent_hub` finds the package


class RuntimeFailure(Exception):
    pass


def http(port: int, method: str, path: str, body: Any = None, timeout: float = 3.0) -> tuple[int | None, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
        return None, None


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def pid_alive(pid: int | None) -> bool:
    return procs.alive(pid)


def lane_env(cfg: Config, lane: Lane) -> dict[str, str]:
    env = dict(os.environ)
    env.update(lane.env)
    env["TOWER_HOME"] = str(cfg.home)
    env["TOWER_LANE"] = lane.id
    for d in (BIN_DIR, Path(sys.executable).parent):  # a clone's bin/, or the venv a pip/pipx/uv install put dah in
        if (d / "dah").is_file() or (d / "dah.exe").is_file():
            env["PATH"] = f"{d}{os.pathsep}{env.get('PATH', '')}"
            break
    return env


class Adapter:
    """Shared plumbing: one lane, its child processes, its log file."""

    def __init__(self, cfg: Config, store: Store, lane: Lane):
        self.cfg, self.store, self.lane = cfg, store, lane
        self.procs: dict[int, subprocess.Popen[bytes]] = {}
        self.logs = cfg.state_dir / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)

    @property
    def log_path(self) -> Path:
        return self.logs / f"{self.lane.id}.jsonl"

    def _spawn(self, argv: list[str]) -> int:
        with self.log_path.open("ab") as out, (self.logs / f"{self.lane.id}.err").open("ab") as err:
            proc = subprocess.Popen(argv, cwd=self.cfg.workdir, env=lane_env(self.cfg, self.lane), stdout=out,
                                    stderr=err, stdin=subprocess.DEVNULL, **procs.detached())
        self.procs[proc.pid] = proc
        self.store.set_lane(self.lane.id, pid=proc.pid)
        return proc.pid

    def _running(self) -> bool:
        pid = self.store.lane(self.lane.id).get("pid")
        if not pid:
            return False
        proc = self.procs.get(pid)
        if proc is not None:  # our child: poll() also reaps it, so a finished run never looks alive
            return proc.poll() is None
        if procs.reap(pid):  # a child of this process we lost the handle for (config reload) that finished
            return False
        return pid_alive(pid)

    def _kill(self) -> None:
        pid = self.store.lane(self.lane.id).get("pid")
        if pid and self._running():
            procs.terminate(pid)
            deadline = time.time() + 10
            while self._running() and time.time() < deadline:
                time.sleep(0.2)
        self.procs.pop(pid or -1, None)

    def owned(self) -> bool:
        return True

    # optional capabilities
    def questions(self) -> list[dict[str, Any]]:
        return []

    def reply_question(self, qid: str, answers: list[list[str]]) -> None:
        raise RuntimeFailure(f"{self.lane.runtime} has no live questions")

    def context_k(self, sid: str | None) -> int:
        return 0

    def usage(self, sid: str | None) -> dict[str, Any] | None:
        """Totals for one session: tokens_in (cache included), tokens_out, cost (USD or None), ctx_k."""
        return None

    def last_text(self, n: int = 1) -> list[str]:
        return []


# ---------------------------------------------------------------- opencode

class OpenCode(Adapter):
    @property
    def port(self) -> int:
        if not self.lane.port:
            raise RuntimeFailure(f"lane {self.lane.id}: opencode needs a port")
        return self.lane.port

    def owned(self) -> bool:
        return bool(self.store.lane(self.lane.id).get("pid"))

    def status(self) -> str:
        if not listening(self.port):
            return "closed"
        if self.questions():
            return "asking"
        code, data = http(self.port, "GET", "/session/status", timeout=2)
        if code != 200 or not isinstance(data, dict):
            return "unknown"
        return "busy" if data else "idle"  # the server lists only sessions that are working

    def questions(self) -> list[dict[str, Any]]:
        code, data = http(self.port, "GET", "/question", timeout=2)
        return data if code == 200 and isinstance(data, list) else []

    def reply_question(self, qid: str, answers: list[list[str]]) -> None:
        code, body = http(self.port, "POST", f"/question/{qid}/reply", {"answers": answers}, timeout=10)
        if code != 200:
            raise RuntimeFailure(f"{self.lane.id}: question reply failed (http {code}): {body}")

    def _model(self) -> dict[str, str] | None:
        if "/" not in self.lane.model:
            return None
        provider, _, model = self.lane.model.partition("/")
        return {"providerID": provider, "modelID": model}

    def send(self, sid: str, text: str) -> None:
        body: dict[str, Any] = {"parts": [{"type": "text", "text": text}]}
        if self._model():
            body["model"] = self._model()
        if self.lane.agent:
            body["agent"] = self.lane.agent
        code, resp = http(self.port, "POST", f"/session/{sid}/prompt_async", body, timeout=10)
        if code not in (200, 204):
            raise RuntimeFailure(f"{self.lane.id}: prompt failed (http {code}): {resp}")

    def _start(self) -> None:
        binary = procs.command(os.environ.get("TOWER_OPENCODE_BIN", "opencode"))
        self._spawn([*binary, "serve", "--port", str(self.port), "--hostname", "127.0.0.1", *self.lane.args])

    def open(self, prompt: str) -> str:
        if not listening(self.port):
            self._start()
            deadline = time.time() + 90
            while http(self.port, "GET", "/session/status", timeout=2)[0] != 200:
                if time.time() > deadline or not self._running():
                    raise RuntimeFailure(f"{self.lane.id}: opencode did not come up on :{self.port} "
                                         f"(see {self.logs / (self.lane.id + '.err')})")
                time.sleep(1)
        code, sess = http(self.port, "POST", "/session", {"title": f"tower · {self.lane.id}"}, timeout=10)
        if code != 200 or not isinstance(sess, dict):
            raise RuntimeFailure(f"{self.lane.id}: new session failed (http {code}): {sess}")
        sid = str(sess["id"])
        http(self.port, "POST", "/tui/select-session", {"sessionID": sid})  # only matters when a human runs the TUI
        self.store.set_lane(self.lane.id, session=sid)
        self.send(sid, prompt)
        return sid

    def close(self) -> None:
        if self.owned():
            self._kill()
            deadline = time.time() + 20
            while listening(self.port) and time.time() < deadline:
                time.sleep(0.5)
        self.store.set_lane(self.lane.id, session=None, pid=None)

    def _messages(self, sid: str) -> list[dict[str, Any]]:
        code, data = http(self.port, "GET", f"/session/{sid}/message", timeout=5)
        return data if code == 200 and isinstance(data, list) else []

    def context_k(self, sid: str | None) -> int:
        if not sid:
            return 0
        for m in reversed(self._messages(sid)):
            info = m.get("info") or {}
            t = info.get("tokens") or {}
            c = t.get("cache") or {}
            total = (t.get("input") or 0) + (c.get("read") or 0) + (c.get("write") or 0)
            if info.get("role") == "assistant" and total:
                return round(total / 1000)
        return 0

    def usage(self, sid: str | None) -> dict[str, Any] | None:
        tin = tout = 0
        cost: float | None = None
        for m in self._messages(sid) if sid else []:
            info = m.get("info") or {}
            if info.get("role") != "assistant":
                continue
            t = info.get("tokens") or {}
            c = t.get("cache") or {}
            tin += int(t.get("input") or 0) + int(c.get("read") or 0) + int(c.get("write") or 0)
            tout += int(t.get("output") or 0) + int(t.get("reasoning") or 0)
            if isinstance(info.get("cost"), (int, float)):
                cost = (cost or 0.0) + float(info["cost"])
        if not tin and not tout:
            return None
        return {"tokens_in": tin, "tokens_out": tout, "cost": cost, "ctx_k": self.context_k(sid)}

    def last_text(self, n: int = 1) -> list[str]:
        sid = self.store.lane(self.lane.id).get("session")
        out: list[str] = []
        for m in reversed(self._messages(sid) if sid else []):
            if (m.get("info") or {}).get("role") != "assistant":
                continue
            text = "\n".join(p.get("text", "") for p in m.get("parts") or [] if p.get("type") == "text").strip()
            if text:
                out.append(text)
            if len(out) >= n:
                break
        return list(reversed(out))


# ---------------------------------------------------------------- headless CLIs

class Headless(Adapter):
    """A session is an id; each prompt is one CLI run that resumes it. Busy while that run is alive."""

    def status(self) -> str:
        if not self.store.lane(self.lane.id).get("session"):
            return "closed"
        return "busy" if self._running() else "idle"

    def close(self) -> None:
        self._kill()
        self.store.set_lane(self.lane.id, session=None, pid=None)

    def _events(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        out = []
        for line in self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]:
            line = line.strip()
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def send(self, sid: str, text: str) -> None:
        if self._running():
            raise RuntimeFailure(f"{self.lane.id}: still busy")
        self._spawn(self.resume_argv(sid, text))

    def resume_argv(self, sid: str, text: str) -> list[str]:
        raise NotImplementedError


class Claude(Headless):
    def _base(self) -> list[str]:
        argv = [*procs.command(os.environ.get("TOWER_CLAUDE_BIN", "claude")), "-p", "--output-format", "json", *self.lane.args]
        argv += ["--agent", self.lane.agent] if self.lane.agent else []
        return argv + (["--model", self.lane.model] if self.lane.model else [])

    def open(self, prompt: str) -> str:
        sid = str(uuid.uuid4())
        self.store.set_lane(self.lane.id, session=sid)
        self._spawn([*self._base(), "--session-id", sid, prompt])
        return sid

    def resume_argv(self, sid: str, text: str) -> list[str]:
        return [*self._base(), "--resume", sid, text]

    def _results(self) -> list[dict[str, Any]]:
        return [e for e in self._events() if e.get("type") == "result" or "result" in e]

    def context_k(self, sid: str | None) -> int:
        for e in reversed(self._results()):
            if sid and e.get("session_id") not in (None, sid):
                continue
            u = e.get("usage") or {}
            total = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                     "cache_creation_input_tokens"))
            return round(total / 1000)
        return 0

    def usage(self, sid: str | None) -> dict[str, Any] | None:
        runs = [e for e in self._results() if sid and e.get("session_id") == sid]
        if not runs:
            return None
        tin = tout = 0
        cost: float | None = None
        for e in runs:
            u = e.get("usage") or {}
            tin += sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                     "cache_creation_input_tokens"))
            tout += int(u.get("output_tokens") or 0)
            if isinstance(e.get("total_cost_usd"), (int, float)):
                cost = (cost or 0.0) + float(e["total_cost_usd"])
        return {"tokens_in": tin, "tokens_out": tout, "cost": cost, "ctx_k": self.context_k(sid)}

    def last_text(self, n: int = 1) -> list[str]:
        texts = [str(e.get("result") or "").strip() for e in self._results()]
        return [t for t in texts if t][-n:]


class Codex(Headless):
    OPEN_WAIT = 60

    def _base(self) -> list[str]:
        argv = [*procs.command(os.environ.get("TOWER_CODEX_BIN", "codex")), "exec", "--json", *self.lane.args]
        return argv + (["--model", self.lane.model] if self.lane.model else [])

    @staticmethod
    def _thread_id(e: dict[str, Any]) -> str | None:
        for key in ("thread_id", "session_id", "conversation_id"):
            if e.get(key):
                return str(e[key])
        msg = e.get("msg") if isinstance(e.get("msg"), dict) else {}
        return str(msg["session_id"]) if msg.get("session_id") else None

    def open(self, prompt: str) -> str:
        start = len(self._events())
        self.store.set_lane(self.lane.id, session=None)
        self._spawn([*self._base(), prompt])
        deadline = time.time() + self.OPEN_WAIT
        while time.time() < deadline:
            for e in self._events()[start:]:
                sid = self._thread_id(e)
                if sid:
                    self.store.set_lane(self.lane.id, session=sid)
                    return sid
            if not self._running():
                break
            time.sleep(0.5)
        raise RuntimeFailure(f"{self.lane.id}: codex printed no thread id (see {self.logs / (self.lane.id + '.err')})")

    def resume_argv(self, sid: str, text: str) -> list[str]:
        return [*self._base(), "resume", sid, text]

    def context_k(self, sid: str | None) -> int:
        for e in reversed(self._events()):
            u = e.get("usage") if isinstance(e.get("usage"), dict) else None
            if u and u.get("input_tokens"):
                return round(int(u["input_tokens"]) / 1000)
        return 0

    def usage(self, sid: str | None) -> dict[str, Any] | None:
        tin = tout = 0
        thread = None
        for e in self._events():
            thread = self._thread_id(e) or thread
            u = e.get("usage") if isinstance(e.get("usage"), dict) else None
            if sid and thread == sid and u and e.get("type") == "turn.completed":
                tin += int(u.get("input_tokens") or 0)
                tout += int(u.get("output_tokens") or 0)
        if not tin and not tout:
            return None
        return {"tokens_in": tin, "tokens_out": tout, "cost": None, "ctx_k": self.context_k(sid)}

    def last_text(self, n: int = 1) -> list[str]:
        out = []
        for e in self._events():
            item = e.get("item") if isinstance(e.get("item"), dict) else {}
            if item.get("type") in ("agent_message", "assistant_message") and item.get("text"):
                out.append(str(item["text"]).strip())
        return out[-n:]


# ---------------------------------------------------------------- terminals

def _run(argv: list[str], what: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except FileNotFoundError:
        raise RuntimeFailure(f"{what} not found ({argv[0]}); install it or set the lane's \"terminal\" to none")
    except subprocess.TimeoutExpired:
        raise RuntimeFailure(f"{what} did not answer: {' '.join(argv[:3])}")


def tmux_session(cfg: Config) -> str:
    return "dah-" + (re.sub(r"[^A-Za-z0-9_-]+", "-", cfg.home.name).strip("-") or "workspace")


class Host:
    """A terminal app the tower opens lane windows in. A handle names one window."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def open(self, title: str, launcher: Path) -> str:
        raise NotImplementedError

    def alive(self, handle: str) -> bool:
        raise NotImplementedError

    def send(self, handle: str, text: str) -> None:
        raise NotImplementedError

    def close(self, handle: str) -> None:
        raise NotImplementedError


class Tmux(Host):
    def _tmux(self, *args: str) -> subprocess.CompletedProcess[str]:
        return _run([*procs.command(os.environ.get("TOWER_TMUX_BIN", "tmux")), *args], "tmux")

    def open(self, title: str, launcher: Path) -> str:
        session, cmd = tmux_session(self.cfg), shlex.quote(str(launcher))
        where = ["-c", str(self.cfg.workdir), "-n", title, "-P", "-F", "#{window_id}"]
        if self._tmux("has-session", "-t", f"={session}").returncode == 0:
            p = self._tmux("new-window", "-d", "-t", f"={session}:", *where, cmd)
        else:
            p = self._tmux("new-session", "-d", "-s", session, *where, cmd)
        if p.returncode != 0 or not p.stdout.strip():
            raise RuntimeFailure(f"tmux could not open a window: {p.stderr.strip()}")
        return p.stdout.strip().splitlines()[-1]

    def alive(self, handle: str) -> bool:
        p = self._tmux("list-windows", "-a", "-F", "#{window_id} #{pane_dead}")
        if p.returncode != 0:
            if "no server running" in p.stderr or "error connecting" in p.stderr:
                return False
            raise RuntimeFailure(f"tmux did not answer: {p.stderr.strip()}")
        return any(line.split()[:2] == [handle, "0"] for line in p.stdout.splitlines())  # 1: remain-on-exit

    def send(self, handle: str, text: str) -> None:
        if self._tmux("send-keys", "-t", handle, "-l", "--", text).returncode != 0:
            raise RuntimeFailure(f"tmux could not type into {handle}")
        time.sleep(0.3)  # let the TUI take the text as typed, not as part of the Enter
        self._tmux("send-keys", "-t", handle, "Enter")

    def close(self, handle: str) -> None:
        self._tmux("kill-window", "-t", handle)


class Orca(Host):
    """Orca (an agent development environment) terminal tabs, through its `orca` CLI."""

    def _orca(self, *args: str) -> tuple[subprocess.CompletedProcess[str], Any]:
        p = _run([*procs.command(os.environ.get("TOWER_ORCA_BIN", "orca")), *args, "--json"], "orca")
        try:
            data = json.loads(p.stdout) if p.stdout.strip() else None
        except json.JSONDecodeError:
            data = None
        return p, data

    @staticmethod
    def _find(data: Any, key: str) -> Any:
        if isinstance(data, dict):
            if key in data and not isinstance(data[key], (dict, list)):
                return data[key]
            for v in data.values():
                found = Orca._find(v, key)
                if found is not None:
                    return found
        if isinstance(data, list):
            for v in data:
                found = Orca._find(v, key)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _find_obj(data: Any, key: str) -> Any:
        if isinstance(data, dict):
            if isinstance(data.get(key), dict):
                return data[key]
            for v in data.values():
                found = Orca._find_obj(v, key)
                if found is not None:
                    return found
        if isinstance(data, list):
            for v in data:
                found = Orca._find_obj(v, key)
                if found is not None:
                    return found
        return None

    def open(self, title: str, launcher: Path) -> str:
        cmd = f'"{launcher}"' if procs.WINDOWS else "exec " + shlex.quote(str(launcher))  # the tab ends with the agent
        p, data = self._orca("terminal", "create", "--worktree", f"path:{self.cfg.workdir}", "--title", title,
                             "--command", cmd)
        term = self._find_obj(data, "terminal")
        handle = self._find(term, "handle") if term is not None else None
        handle = handle or self._find(data, "handle")
        if p.returncode != 0 or not handle:
            raise RuntimeFailure(f"orca could not open a terminal: {(p.stderr or p.stdout).strip()[:300]}")
        return str(handle)

    def alive(self, handle: str) -> bool:
        p, data = self._orca("terminal", "show", "--terminal", handle)
        said = f"{p.stdout} {p.stderr}".lower()
        if p.returncode != 0:
            if "stale" in said or "not found" in said or "no terminal" in said or "unknown terminal" in said:
                return False
            raise RuntimeFailure(f"orca did not answer for {handle}: {said.strip()[:300]}")  # unknown, not closed
        return not (self._find(data, "exited") is True or self._find(data, "state") == "exited"
                    or self._find(data, "status") == "exited")

    def send(self, handle: str, text: str) -> None:
        p, _ = self._orca("terminal", "send", "--terminal", handle, "--text", text, "--enter")
        if p.returncode != 0:
            raise RuntimeFailure(f"orca could not type into {handle}: {(p.stderr or p.stdout).strip()[:300]}")

    def close(self, handle: str) -> None:
        self._orca("terminal", "close", "--terminal", handle)


HOSTS: dict[str, type[Host]] = {"tmux": Tmux, "orca": Orca}


def signal_path(home: Path, lane: str) -> Path:
    return home / TOWER_DIR / "state" / "term" / f"{lane}.json"


def write_signal(home: Path, lane: str, **fields: Any) -> None:
    path = signal_path(home, lane)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    data.update({k: v for k, v in fields.items() if v is not None}, at=time.time())
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


CLAUDE_STATES = {"UserPromptSubmit": "busy", "PostToolUse": "busy", "Stop": "idle"}


def record_signal(home: Path, lane: str, payload: dict[str, Any]) -> None:
    """What `dah signal` does with a hook (Claude: JSON on stdin) or a notify (Codex: JSON argument)."""
    event = str(payload.get("hook_event_name") or "")
    if event:
        state = CLAUDE_STATES.get(event)
        if event == "Notification":  # a permission menu waits for a person; the idle one also covers a turn
            kind = str(payload.get("notification_type") or "")  # a person interrupted (no Stop hook then)
            message = str(payload.get("message") or "").lower()
            if "permission" in kind or "permission" in message:
                state = "asking"
            elif kind == "idle_prompt" or "waiting for your input" in message:
                state = "idle"
        if state:
            write_signal(home, lane, state=state, transcript=payload.get("transcript_path"),
                         session=payload.get("session_id"))
        return
    if payload.get("type") == "agent-turn-complete":
        write_signal(home, lane, state="idle", last=payload.get("last-assistant-message"),
                     session=payload.get("thread-id") or payload.get("thread_id"))


class Terminal:
    """Mixin for an adapter whose session runs in a window: the launcher, the handle, the signal file."""

    cfg: Config
    store: Store
    lane: Lane

    def _host(self) -> Host:
        return HOSTS[self.lane.terminal](self.cfg)

    @property
    def term_dir(self) -> Path:
        d = self.cfg.state_dir / "term"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _handle(self) -> str:
        try:
            return (self.term_dir / f"{self.lane.id}.handle").read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _window_alive(self) -> bool:
        handle = self._handle()
        return bool(handle) and self._host().alive(handle)

    def _signal(self) -> dict[str, Any]:
        try:
            return json.loads(signal_path(self.cfg.home, self.lane.id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _launch(self, argv: list[str]) -> None:
        """Write a launcher script (so the window gets the lane's environment whatever the terminal app's own
        is) and open a window running it."""
        env = lane_env(self.cfg, self.lane)
        keys = ["TOWER_HOME", "TOWER_LANE", "PATH", *self.lane.env]
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(PACKAGE_PARENT), os.environ.get("PYTHONPATH", "")) if p)
        keys.append("PYTHONPATH")
        if procs.WINDOWS:
            path = self.term_dir / f"{self.lane.id}.cmd"
            lines = ["@echo off", "chcp 65001 >nul", f'cd /d "{self.cfg.workdir}"', *(f'set "{k}={env[k]}"' for k in keys),
                     subprocess.list2cmdline(argv)]
            path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        else:
            path = self.term_dir / f"{self.lane.id}.sh"
            lines = ["#!/bin/sh", f"cd {shlex.quote(str(self.cfg.workdir))} || exit 1",
                     *(f"export {k}={shlex.quote(env[k])}" for k in keys), "exec " + shlex.join(argv)]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            path.chmod(0o755)
        handle = self._host().open(self.lane.id, path)
        (self.term_dir / f"{self.lane.id}.handle").write_text(handle, encoding="utf-8")

    def _note(self, text: str) -> str:
        """Put a prompt in a file and return the one line the agent gets in its window."""
        path = self.term_dir / f"{self.lane.id}-{time.time_ns()}.md"
        path.write_text(text, encoding="utf-8")
        where = path.relative_to(self.cfg.workdir).as_posix() if path.is_relative_to(self.cfg.workdir) else str(path)
        return f"Message from the tower in {where}: read that file and act on it."

    def _close_window(self) -> None:
        handle = self._handle()
        if handle:
            self._host().close(handle)
        lid = self.lane.id
        for f in self.term_dir.iterdir():
            note = f.suffix == ".md" and f.stem.startswith(f"{lid}-") and f.stem[len(lid) + 1:].isdigit()
            if note or f.name in (f"{lid}.handle", f"{lid}.json"):
                try:
                    f.unlink(missing_ok=True)
                except OSError:  # Windows: still open by the window's process; the next open retries
                    pass

    def _type(self, text: str) -> None:
        handle = self._handle()
        if not handle:
            raise RuntimeFailure(f"{self.lane.id}: no window to type into")
        write_signal(self.cfg.home, self.lane.id, state="busy")
        self._host().send(handle, self._note(text))


class TerminalSession(Terminal):
    """Claude and Codex in a window: one interactive process per session, busy/idle from `dah signal`."""

    def status(self) -> str:
        if not self.store.lane(self.lane.id).get("session"):
            return "closed"
        if not self._window_alive():
            return "closed"
        state = self._signal().get("state")
        return state if state in ("busy", "asking") else "idle"

    def send(self, sid: str, text: str) -> None:
        if self.status() in ("busy", "asking"):
            raise RuntimeFailure(f"{self.lane.id}: still busy")
        self._type(text)

    def close(self) -> None:
        self._close_window()
        self.store.set_lane(self.lane.id, session=None, pid=None)

    def owned(self) -> bool:
        return True

    def _hook_command(self) -> str:
        if procs.WINDOWS:
            return f'"{sys.executable}" -m agent_hub signal'
        return shlex.join([sys.executable, "-m", "agent_hub", "signal"])


def _epoch(stamp: str) -> float | None:
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?", stamp or "")
    if not m:
        return None
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    return base + float(m.group(2) or 0)


def _tail(path: str | Path, size: int = 262144) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            start = max(0, f.tell() - size)
            f.seek(start)
            lines = f.read().decode("utf-8", errors="replace").splitlines()
            return lines[1:] if start else lines  # the first line of a cut is partial
    except OSError:
        return []


class ClaudeTerm(TerminalSession, Claude):
    def status(self) -> str:
        st = super().status()
        if st in ("busy", "asking") and self._turn_ended():
            return "idle"
        return st

    def _turn_ended(self) -> bool:
        """A turn a person interrupts (Esc, or No on a permission menu) ends without a Stop hook; Claude still
        closes it in the transcript with a turn_duration entry."""
        sig = self._signal()
        for line in reversed(_tail(self._transcript_path(), 65536)):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(e, dict) and e.get("type") == "system" and e.get("subtype") == "turn_duration":
                ended = _epoch(str(e.get("timestamp") or ""))
                return ended is not None and ended >= float(sig.get("at") or 0)
        return False

    def _argv(self, *extra: str) -> list[str]:
        argv = [*procs.command(os.environ.get("TOWER_CLAUDE_BIN", "claude")), *self.lane.args]
        argv += ["--agent", self.lane.agent] if self.lane.agent else []
        argv += ["--model", self.lane.model] if self.lane.model else []
        hook = [{"hooks": [{"type": "command", "command": self._hook_command()}]}]
        settings = self.term_dir / f"{self.lane.id}.settings.json"
        settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": hook, "PostToolUse": hook, "Stop": hook,
                                                  "Notification": hook}}), encoding="utf-8")
        return [*argv, "--settings", str(settings), *extra]

    def open(self, prompt: str) -> str:
        sid = str(uuid.uuid4())
        self._close_window()
        write_signal(self.cfg.home, self.lane.id, state="busy")
        self._launch(self._argv("--session-id", sid, self._note(prompt)))
        self.store.set_lane(self.lane.id, session=sid, pid=None)
        return sid

    def _transcript_path(self) -> str:
        """From the hooks, or where Claude Code keeps it: ~/.claude/projects/<cwd, non-alphanumerics as ->/<id>.jsonl"""
        path = self._signal().get("transcript")
        if path:
            return str(path)
        sid = self.store.lane(self.lane.id).get("session")
        if not sid:
            return ""
        base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        return str(base / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(self.cfg.workdir)) / f"{sid}.jsonl")

    def _transcript(self) -> list[dict[str, Any]]:
        path = self._transcript_path()
        if not path:
            return []
        out = []
        for line in _tail(path):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(e, dict) and e.get("type") == "assistant" and isinstance(e.get("message"), dict):
                out.append(e["message"])
        return out

    def _calls(self) -> list[dict[str, Any]]:
        """One entry per API call: the transcript's last line for each message id."""
        last: dict[str, dict[str, Any]] = {}
        for i, m in enumerate(self._transcript()):
            last[str(m.get("id") or i)] = m
        return list(last.values())

    def context_k(self, sid: str | None) -> int:
        for m in reversed(self._transcript()):
            u = m.get("usage") or {}
            total = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                     "cache_creation_input_tokens"))
            if total:
                return round(total / 1000)
        return 0

    def usage(self, sid: str | None) -> dict[str, Any] | None:
        msgs = self._calls()
        if not msgs:
            return None
        tin = tout = 0
        for m in msgs:
            u = m.get("usage") or {}
            tin += sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                     "cache_creation_input_tokens"))
            tout += int(u.get("output_tokens") or 0)
        return {"tokens_in": tin, "tokens_out": tout, "cost": None, "ctx_k": self.context_k(sid)}

    def last_text(self, n: int = 1) -> list[str]:
        texts = []
        for m in self._transcript():
            parts = m.get("content") if isinstance(m.get("content"), list) else []
            text = "\n".join(str(p.get("text") or "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
            if text.strip():
                texts.append(text.strip())
        return texts[-n:]


class CodexTerm(TerminalSession, Codex):
    EXEC_ONLY = ("--json", "--skip-git-repo-check")

    def open(self, prompt: str) -> str:
        sid = f"term-{uuid.uuid4()}"
        self._close_window()
        write_signal(self.cfg.home, self.lane.id, state="busy")
        argv = [*procs.command(os.environ.get("TOWER_CODEX_BIN", "codex")),
                *(a for a in self.lane.args if a not in self.EXEC_ONLY)]
        argv += ["--model", self.lane.model] if self.lane.model else []
        notify = [sys.executable, "-m", "agent_hub", "signal"]
        argv += ["-c", f"notify={json.dumps(notify)}", self._note(prompt)]
        self._launch(argv)
        self.store.set_lane(self.lane.id, session=sid, pid=None)
        return sid

    def context_k(self, sid: str | None) -> int:
        return 0

    def usage(self, sid: str | None) -> dict[str, Any] | None:
        return None

    def last_text(self, n: int = 1) -> list[str]:
        last = str(self._signal().get("last") or "").strip()
        return [last] if last else []


class OpenCodeTerm(Terminal, OpenCode):
    """The OpenCode TUI in a window; its own server answers the tower's HTTP calls as in background mode."""

    def owned(self) -> bool:
        return bool(self._handle())

    def _running(self) -> bool:
        return self._window_alive()

    def _start(self) -> None:
        self._close_window()
        binary = procs.command(os.environ.get("TOWER_OPENCODE_BIN", "opencode"))
        self._launch([*binary, "--port", str(self.port), "--hostname", "127.0.0.1", *self.lane.args])

    def close(self) -> None:
        if self.owned():
            self._close_window()
            deadline = time.time() + 20
            while listening(self.port) and time.time() < deadline:
                time.sleep(0.5)
        self.store.set_lane(self.lane.id, session=None, pid=None)


ADAPTERS = {"opencode": OpenCode, "claude": Claude, "codex": Codex}
TERMINAL_ADAPTERS = {"opencode": OpenCodeTerm, "claude": ClaudeTerm, "codex": CodexTerm}


class Runtimes:
    """What the engine talks to: one adapter per configured lane."""

    def __init__(self, cfg: Config, store: Store):
        self.adapters: dict[str, Adapter] = {
            lid: (ADAPTERS if ln.terminal == "none" else TERMINAL_ADAPTERS)[ln.runtime](cfg, store, ln)
            for lid, ln in cfg.lanes.items()}

    def _a(self, lane: str) -> Any:
        return self.adapters[lane]

    def status(self, lane: str) -> str:
        return self._a(lane).status()

    def owned(self, lane: str) -> bool:
        return self._a(lane).owned()

    def questions(self, lane: str) -> list[dict[str, Any]]:
        return self._a(lane).questions()

    def reply_question(self, lane: str, qid: str, answers: list[list[str]]) -> None:
        self._a(lane).reply_question(qid, answers)

    def open(self, lane: str, prompt: str) -> str:
        return self._a(lane).open(prompt)

    def send(self, lane: str, sid: str, text: str) -> None:
        self._a(lane).send(sid, text)

    def close(self, lane: str) -> None:
        self._a(lane).close()

    def context_k(self, lane: str, sid: str | None) -> int:
        return self._a(lane).context_k(sid)

    def usage(self, lane: str, sid: str | None) -> dict[str, Any] | None:
        return self._a(lane).usage(sid)

    def last_text(self, lane: str, n: int = 1) -> list[str]:
        return self._a(lane).last_text(n)
