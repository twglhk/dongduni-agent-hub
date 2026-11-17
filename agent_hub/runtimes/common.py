from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .. import procs
from ..config import Config, Lane


BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
PACKAGE_PARENT = Path(__file__).resolve().parents[2]  # where `python -m agent_hub` finds the package


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


def claude_base() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


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
