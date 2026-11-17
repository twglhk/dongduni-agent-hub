from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
TTL = 300
FAIL_TTL = 60
PROVIDERS = ("claude",)


def token(home: Path | None = None, platform: str = sys.platform,
          keychain: Callable[[], str] | None = None) -> str:
    home = home or Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    raw = ""
    if platform == "darwin":
        raw = (keychain or _keychain)()
    if not raw.strip():
        try:
            raw = (home / ".credentials.json").read_text(encoding="utf-8")
        except OSError:
            return ""
    try:
        d = json.loads(raw)
        return str((d.get("claudeAiOauth") or d).get("accessToken") or "")
    except (ValueError, AttributeError):
        return ""


def _keychain() -> str:
    try:
        p = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout if p.returncode == 0 else ""


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def fetch(tok: str, timeout: float = 10) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {tok}", "anthropic-beta": "oauth-2025-04-20"}
    try:
        with urllib.request.urlopen(urllib.request.Request(URL, headers=headers), timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, OSError) as e:
        curl = shutil.which("curl")
        if not curl:
            raise RuntimeError(str(e))
        config = "".join(f'header = "{_quote(f"{k}: {v}")}"\n' for k, v in headers.items()) + f'url = "{URL}"\n'
        p = subprocess.run([curl, "-fsS", "-m", str(int(timeout)), "--config", "-"], input=config,
                           capture_output=True, text=True, timeout=timeout + 5)
        if p.returncode:
            raise RuntimeError(f"curl exit {p.returncode}")
        return json.loads(p.stdout)


def _epoch(s: Any) -> int | None:
    try:
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()) if s else None
    except ValueError:
        return None


def _pct(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse(d: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for lim in d.get("limits") or []:
        kind = lim.get("kind") if isinstance(lim, dict) else None
        name = {"session": "5h", "weekly_all": "week"}.get(str(kind))
        if name:
            out.append({"name": name, "percent": _pct(lim.get("percent")), "resets": _epoch(lim.get("resets_at"))})
    if not out:
        for key, name in (("five_hour", "5h"), ("seven_day", "week")):
            w = d.get(key)
            if isinstance(w, dict):
                out.append({"name": name, "percent": _pct(w.get("utilization")), "resets": _epoch(w.get("resets_at"))})
    return out


class ClaudeUsage:
    def __init__(self, get_token: Callable[[], str] = token, get: Callable[[str], dict[str, Any]] = fetch,
                 clock: Callable[[], float] = time.time):
        self.get_token, self.get, self.clock = get_token, get, clock
        self.lock = threading.Lock()
        self.cached: dict[str, Any] | None = None
        self.until = 0.0
        self.loading = False

    def view(self) -> dict[str, Any]:
        with self.lock:
            if self.cached is not None and self.clock() < self.until:
                return self.cached
            if self.loading:
                return self.cached or {"provider": "claude", "unknown": "loading"}
            self.loading = True
        try:
            result, ttl = self.load()
        finally:
            with self.lock:
                self.loading = False
        with self.lock:
            self.cached, self.until = result, self.clock() + ttl
        return result

    def load(self) -> tuple[dict[str, Any], int]:
        try:
            tok = self.get_token()
            if not tok:
                raise RuntimeError("no Claude Code login found")
            wins = parse(self.get(tok))
            if not wins or all(w["percent"] is None for w in wins):
                raise RuntimeError("the response has no usage windows")
            return {"provider": "claude", "windows": wins}, TTL
        except Exception as e:
            why = f"HTTP {e.code}" if isinstance(e, urllib.error.HTTPError) else type(e).__name__ \
                if not isinstance(e, RuntimeError) else str(e)[:120]
            return {"provider": "claude", "unknown": why}, FAIL_TTL
