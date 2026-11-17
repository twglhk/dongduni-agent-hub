from __future__ import annotations

import os
import time
from typing import Any

from .. import procs
from .adapter import Adapter
from .common import RuntimeFailure, http, listening
from .terminal import Terminal


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

    def questions(self) -> list[dict[str, Any]] | None:
        code, data = http(self.port, "GET", "/question", timeout=2)
        return data if code == 200 and isinstance(data, list) else None

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
