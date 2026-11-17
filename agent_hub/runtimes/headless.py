from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from .. import hooks as hooks_mod, procs
from .adapter import Adapter
from .common import RuntimeFailure


COMPACT_SENT, COMPACT_PENDING, COMPACT_TYPED = "sent", "pending", "typed"


class Headless(Adapter):
    """A session is an id; each prompt is one CLI run that resumes it. Busy while that run is alive."""

    def status(self) -> str:
        if not self.store.lane(self.lane.id).get("session"):
            return "closed"
        return "busy" if self._running() else "idle"

    def close(self) -> None:
        self._kill()
        self.store.set_lane(self.lane.id, session=None, pid=None)

    def _log_size(self) -> int:
        try:
            return self.log_path.stat().st_size
        except OSError:
            return 0

    def _events(self, since: int | None = None) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        if since is None:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
        else:
            with self.log_path.open("rb") as f:
                f.seek(since)
                lines = f.read().decode("utf-8", errors="replace").splitlines()
        out = []
        for line in lines:
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
        argv += [d for d in self._tower_outside() for d in ("--add-dir", d)]  # an option follows: --add-dir takes many
        argv += ["--agent", self.lane.agent] if self.lane.agent else []
        argv += ["--settings", str(hooks_mod.settings_file(self.cfg.home, self.lane.id))]  # gates, urgent tells, API errors
        effort = self.opts().get("effort")
        return argv + self.model_arg() + (["--effort", effort] if effort else [])

    def open(self, prompt: str) -> str:
        sid = str(uuid.uuid4())
        self.store.set_lane(self.lane.id, session=sid)
        self._spawn([*self._base(), "--session-id", sid, prompt])
        return sid

    def resume_argv(self, sid: str, text: str) -> list[str]:
        return [*self._base(), "--resume", sid, text]

    def compact(self, sid: str, focus: str) -> str | None:
        self.send(sid, f"/compact {focus}")  # print mode runs the slash command as the whole run
        return COMPACT_PENDING

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

    def ledger(self, sid: str | None) -> dict[str, int | None] | None:
        runs = [e for e in self._results() if sid and e.get("session_id") == sid]
        if not runs:
            return None
        new = cache = out = turns = 0
        for e in runs:  # one result event per run of the session (the first and each resume)
            u = e.get("usage") or {}
            new += int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
            cache += int(u.get("cache_read_input_tokens") or 0)
            out += int(u.get("output_tokens") or 0)
            turns += int(e.get("num_turns") or 0)
        return {"new": new, "cache": cache, "out": out, "turns": turns or None}

    def last_text(self, n: int = 1) -> list[str]:
        texts = [str(e.get("result") or "").strip() for e in self._results()]
        return [t for t in texts if t][-n:]


class Codex(Headless):
    OPEN_WAIT = 60

    def _base(self) -> list[str]:
        argv = [*procs.command(os.environ.get("TOWER_CODEX_BIN", "codex")), "exec", "--json", *self.lane.args]
        effort = self.opts().get("effort")
        return argv + self.model_arg() + (["-c", f"model_reasoning_effort={effort}"] if effort else [])

    @staticmethod
    def _thread_id(e: dict[str, Any]) -> str | None:
        for key in ("thread_id", "session_id", "conversation_id"):
            if e.get(key):
                return str(e[key])
        msg = e.get("msg") if isinstance(e.get("msg"), dict) else {}
        return str(msg["session_id"]) if msg.get("session_id") else None

    def open(self, prompt: str) -> str:
        start = self._log_size()
        self.store.set_lane(self.lane.id, session=None)
        self._spawn([*self._base(), prompt])
        deadline = time.time() + self.OPEN_WAIT
        while time.time() < deadline:
            for e in self._events(since=start):
                sid = self._thread_id(e)
                if sid:
                    self.store.set_lane(self.lane.id, session=sid)
                    return sid
            if not self._running():
                break
            time.sleep(0.5)
        self._kill()
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
