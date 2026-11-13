"""Optional: Claude subscription usage percent (5-hour and weekly windows) for the board's footer strip.

Off unless tower.json says `"usage_percent": "claude"`. It reads the OAuth access token Claude Code already holds
(macOS keychain item "Claude Code-credentials", else `~/.claude/.credentials.json`), read-only: it never refreshes
or writes the token, so Claude Code's own login is not disturbed. It then asks
`https://api.anthropic.com/api/oauth/usage`, an UNDOCUMENTED endpoint Claude Code itself uses; it may change or
vanish without notice. Any failure (no token, expired token, network, a new response shape) shows as "unknown",
never as 0. The token is never logged or sent anywhere else. Results are cached for TTL seconds.
"""

from __future__ import annotations

import json
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
    """Claude Code's OAuth access token, or '' when there is none to read."""
    home = home or Path.home() / ".claude"
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


def fetch(tok: str, timeout: float = 10) -> dict[str, Any]:
    """GET the usage JSON. urllib first; some Python builds ship without CA certificates, so curl is the fallback."""
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
        args = [curl, "-fsS", "-m", str(int(timeout))]
        for k, v in headers.items():
            args += ["-H", f"{k}: {v}"]
        p = subprocess.run(args + [URL], capture_output=True, text=True, timeout=timeout + 5)
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
    """Response -> [{"name": "5h" | "week", "percent": float | None, "resets": epoch | None}]."""
    out: list[dict[str, Any]] = []
    for lim in d.get("limits") or []:
        kind = lim.get("kind") if isinstance(lim, dict) else None
        name = {"session": "5h", "weekly_all": "week"}.get(str(kind))
        if name:
            out.append({"name": name, "percent": _pct(lim.get("percent")), "resets": _epoch(lim.get("resets_at"))})
    if not out:  # the older shape
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

    def view(self) -> dict[str, Any]:
        """{"provider": "claude", "windows": [...]} or {"provider": "claude", "unknown": why}."""
        with self.lock:
            if self.cached is not None and self.clock() < self.until:
                return self.cached
            try:
                tok = self.get_token()
                if not tok:
                    raise RuntimeError("no Claude Code login found")
                wins = parse(self.get(tok))
                if not wins or all(w["percent"] is None for w in wins):
                    raise RuntimeError("the response has no usage windows")
                self.cached, ttl = {"provider": "claude", "windows": wins}, TTL
            except Exception as e:  # never 0: say it is unknown and why (no token text in it)
                why = f"HTTP {e.code}" if isinstance(e, urllib.error.HTTPError) else type(e).__name__ \
                    if not isinstance(e, RuntimeError) else str(e)[:120]
                self.cached, ttl = {"provider": "claude", "unknown": why}, FAIL_TTL
            self.until = self.clock() + ttl
            return self.cached
