from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from .. import procs
from ..config import Config
from ..ext import registry
from .common import RuntimeFailure


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
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def open(self, title: str, launcher: Path, cwd: Path) -> str:
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

    def open(self, title: str, launcher: Path, cwd: Path) -> str:
        session, cmd = tmux_session(self.cfg), shlex.quote(str(launcher))
        where = ["-c", str(cwd), "-n", title, "-P", "-F", "#{window_id}"]
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
        return any(line.split()[:2] == [handle, "0"] for line in p.stdout.splitlines())  # tmux keeps a finished pane (remain-on-exit) with pane_dead 1

    def send(self, handle: str, text: str) -> None:
        if self._tmux("send-keys", "-t", handle, "-l", "--", text).returncode != 0:
            raise RuntimeFailure(f"tmux could not type into {handle}")
        time.sleep(0.3)  # let the TUI take the text as typed, not as part of the Enter
        self._tmux("send-keys", "-t", handle, "Enter")

    def close(self, handle: str) -> None:
        self._tmux("kill-window", "-t", handle)


class Orca(Host):
    GONE = ("stale", "not found", "no terminal", "unknown terminal")

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

    def open(self, title: str, launcher: Path, cwd: Path) -> str:
        cmd = f'"{launcher}"' if procs.WINDOWS else "exec " + shlex.quote(str(launcher))
        p, data = self._orca("terminal", "create", "--worktree", f"path:{cwd}", "--title", title,
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
            if any(g in said for g in self.GONE):
                return False
            raise RuntimeFailure(f"orca did not answer for {handle}: {said.strip()[:300]}")
        # Orca 1.4.257 keeps answering ok for an ended tab: `exitCause` set, `connected: false`, not writable.
        term = self._find_obj(data, "terminal") or {}
        if term.get("exitCause") or self._find(data, "exitCause") or (
                term.get("connected") is False and term.get("writable") is False):
            return False
        return not (self._find(data, "exited") is True or self._find(data, "state") == "exited"
                    or self._find(data, "status") == "exited")

    def send(self, handle: str, text: str) -> None:
        p, _ = self._orca("terminal", "send", "--terminal", handle, "--text", text, "--enter")
        if p.returncode != 0:
            raise RuntimeFailure(f"orca could not type into {handle}: {(p.stderr or p.stdout).strip()[:300]}")

    def close(self, handle: str) -> None:
        self._orca("terminal", "close", "--terminal", handle)


HOSTS: dict[str, type[Host]] = {"tmux": Tmux, "orca": Orca}


def host_class(cfg: Config, name: str) -> type[Host]:
    cls = HOSTS.get(name) or registry(cfg.home).table("terminals").get(name)
    if not isinstance(cls, type):
        raise RuntimeFailure(f"no terminal {name!r}: neither built in nor registered in .tower/ext/")
    return cls
