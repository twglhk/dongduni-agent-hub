from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .. import procs
from ..config import TOWER_DIR, Config, Lane
from ..store import Store
from .common import PACKAGE_PARENT, RuntimeFailure, lane_env
from .hosts import Host, host_class
from .worktree import lane_cwd


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


def _write_private(path: Path, text: str, mode: int) -> None:
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(text)


class Terminal:
    """Mixin for an adapter whose session runs in a window: the launcher, the handle, the signal file."""

    cfg: Config
    store: Store
    lane: Lane

    def _host(self) -> Host:
        return host_class(self.cfg, self.lane.terminal)(self.cfg)

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

    def _launch(self, argv: list[str], extra_env: dict[str, str] | None = None) -> None:
        """Write a launcher script (so the window gets the lane's environment whatever the terminal app's own
        is) and open a window running it. The lane's own `env` values go in a private file the launcher sources
        and deletes, never in the script."""
        cwd = lane_cwd(self.cfg, self.lane)
        env = {**lane_env(self.cfg, self.lane), **(extra_env or {})}
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(PACKAGE_PARENT), os.environ.get("PYTHONPATH", "")) if p)
        keys = ["TOWER_HOME", "TOWER_LANE", "PATH", *(extra_env or {}), "PYTHONPATH"]
        secrets = [k for k in self.lane.env if k not in keys]
        secret_file = self.term_dir / (f"{self.lane.id}.env.cmd" if procs.WINDOWS else f"{self.lane.id}.env")
        if procs.WINDOWS:
            path = self.term_dir / f"{self.lane.id}.cmd"
            lines = ["@echo off", "chcp 65001 >nul", f'cd /d "{cwd}"', *(f'set "{k}={env[k]}"' for k in keys)]
            if secrets:
                _write_private(secret_file, "\r\n".join(f'set "{k}={env[k]}"' for k in secrets) + "\r\n", 0o600)
                lines += [f'call "{secret_file}"', f'del "{secret_file}"']
            lines.append(subprocess.list2cmdline(argv))
            path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        else:
            path = self.term_dir / f"{self.lane.id}.sh"
            lines = ["#!/bin/sh", f"cd {shlex.quote(str(cwd))} || exit 1",
                     *(f"export {k}={shlex.quote(env[k])}" for k in keys)]
            if secrets:
                _write_private(secret_file, "\n".join(f"export {k}={shlex.quote(env[k])}" for k in secrets) + "\n", 0o600)
                lines += [f". {shlex.quote(str(secret_file))}", f"rm -f {shlex.quote(str(secret_file))}"]
            lines.append("exec " + shlex.join(argv))
            _write_private(path, "\n".join(lines) + "\n", 0o700)
        handle = self._host().open(self.lane.id, path, cwd)
        (self.term_dir / f"{self.lane.id}.handle").write_text(handle, encoding="utf-8")

    def _note(self, text: str) -> str:
        """Put a prompt in a file and return the one line the agent gets in its window."""
        path = self.term_dir / f"{self.lane.id}-{time.time_ns()}.md"
        path.write_text(text, encoding="utf-8")
        cwd = self.cfg.lane_dir(self.lane.id)
        where = path.relative_to(cwd).as_posix() if path.is_relative_to(cwd) else str(path)
        return f"Message from the tower in {where}: read that file and act on it."

    def _close_window(self) -> None:
        handle = self._handle()
        if handle:
            self._host().close(handle)
        lid = self.lane.id
        for f in self.term_dir.iterdir():
            note = f.suffix == ".md" and f.stem.startswith(f"{lid}-") and f.stem[len(lid) + 1:].isdigit()
            if note or f.name in (f"{lid}.handle", f"{lid}.json", f"{lid}.env", f"{lid}.env.cmd"):
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

    def send_queued(self, sid: str, text: str) -> None:
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
