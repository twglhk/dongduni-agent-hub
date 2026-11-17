from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import hooks as hooks_mod, procs
from .common import RuntimeFailure, claude_base
from .headless import COMPACT_TYPED, Claude
from .terminal import TerminalSession, write_signal


def _epoch(stamp: str) -> float | None:
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?", stamp or "")
    if not m:
        return None
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    return base + float(m.group(2) or 0)


WHOLE_BYTES = 256 * 1024 * 1024  # the ledger reads a transcript whole


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
        argv += self.model_arg()
        argv += ["--effort", self.opts()["effort"]] if self.opts().get("effort") else []
        argv += [d for d in self._tower_outside() for d in ("--add-dir", d)]
        hook = [{"hooks": [{"type": "command", "command": self._hook_command()}]}]
        settings = self.term_dir / f"{self.lane.id}.settings.json"
        hooks_mod.write_if_changed(settings, json.dumps({"hooks": {"UserPromptSubmit": hook, "PostToolUse": hook,
                                                                   "Stop": hook, "Notification": hook,
                                                                   **hooks_mod.claude_hooks()}}))
        return [*argv, "--settings", str(settings), *extra]

    def compact(self, sid: str, focus: str) -> str | None:
        if self.status() in ("busy", "asking"):
            raise RuntimeFailure(f"{self.lane.id}: still busy")
        handle = self._handle()
        if not handle:
            raise RuntimeFailure(f"{self.lane.id}: no window to type into")
        write_signal(self.cfg.home, self.lane.id, state="busy")
        self._host().send(handle, f"/compact {focus}")
        return COMPACT_TYPED

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
        return str(claude_base(self.account_dir()) / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(self.cwd.resolve())) / f"{sid}.jsonl")

    def _transcript(self, whole: bool = False) -> list[dict[str, Any]]:
        path = self._transcript_path()
        if not path:
            return []
        out = []
        for line in _tail(path, WHOLE_BYTES if whole else 262144):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(e, dict) and e.get("type") == "assistant" and isinstance(e.get("message"), dict):
                out.append(e["message"])
        return out

    def _calls(self, whole: bool = False) -> list[dict[str, Any]]:
        """One entry per API call: the transcript's last line for each message id."""
        last: dict[str, dict[str, Any]] = {}
        for i, m in enumerate(self._transcript(whole)):
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

    def ledger(self, sid: str | None) -> dict[str, int | None] | None:
        """The whole transcript (not just its tail): each API call once; a turn is one assistant message."""
        msgs = self._calls(whole=True)
        if not msgs:
            return None
        new = cache = out = 0
        for m in msgs:
            u = m.get("usage") or {}
            new += int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
            cache += int(u.get("cache_read_input_tokens") or 0)
            out += int(u.get("output_tokens") or 0)
        return {"new": new, "cache": cache, "out": out, "turns": len(msgs)}

    def last_text(self, n: int = 1) -> list[str]:
        texts = []
        for m in self._transcript():
            parts = m.get("content") if isinstance(m.get("content"), list) else []
            text = "\n".join(str(p.get("text") or "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
            if text.strip():
                texts.append(text.strip())
        return texts[-n:]
