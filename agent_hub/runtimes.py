"""Runtime adapters: how the tower starts, prompts, watches and stops an agent session for one lane.

  opencode  long-lived `opencode serve --port P` per lane; prompts over its HTTP API. Live `ask` questions
            are supported (they become board items). A human may run `opencode --port P` (TUI) instead;
            the tower then uses that server and never kills it.
  claude    headless Claude Code: `claude -p --session-id ID` to start, `claude -p --resume ID` per prompt.
  codex     headless Codex: `codex exec --json` to start, `codex exec resume ID` per prompt.

Every adapter answers status(): closed (no session) | idle | busy | asking | unknown.
Binaries can be overridden with TOWER_OPENCODE_BIN / TOWER_CLAUDE_BIN / TOWER_CODEX_BIN.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from . import procs
from .config import Config, Lane
from .store import Store

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"


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

    def open(self, prompt: str) -> str:
        if not listening(self.port):
            binary = procs.command(os.environ.get("TOWER_OPENCODE_BIN", "opencode"))
            self._spawn([*binary, "serve", "--port", str(self.port), "--hostname", "127.0.0.1", *self.lane.args])
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


ADAPTERS = {"opencode": OpenCode, "claude": Claude, "codex": Codex}


class Runtimes:
    """What the engine talks to: one adapter per configured lane."""

    def __init__(self, cfg: Config, store: Store):
        self.adapters: dict[str, Adapter] = {lid: ADAPTERS[ln.runtime](cfg, store, ln) for lid, ln in cfg.lanes.items()}

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
