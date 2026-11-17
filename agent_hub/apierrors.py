from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

COOLDOWN_S = 1800
MAX_READ = 4 << 20

EXPIRED = ("Claude login expired: run `claude` in a terminal and log in again. "
           "Claude lanes stay stopped until you do.")

# first match wins
TRANSIENT = ("token refresh is rate-limited", "overloaded", "service unavailable", "econnreset", "econnrefused", "enotfound",
             "etimedout", "eai_again", "enetunreach", "ehostunreach", "cannot connect to api", "fetch failed",
             "socket connection was closed", "unable to connect", "timed out")

# first match wins
KINDS = (
    ("credentials are unavailable", "login", EXPIRED),
    ("authentication token has expired", "login", EXPIRED),
    ("token expired and refresh failed", "login", EXPIRED),
    ("authentication_failed", "login", EXPIRED),
    ("billing_error", "billing", "Claude billing problem: check the subscription or credits. "
                                 "Claude lanes stay stopped until it is fixed."),
    ("usage limit has been reached", "usage", "Claude usage limit reached: Claude lanes wait until it resets."),
    ("rate_limit", "rate", "Claude rate limit hit: Claude lanes are slowed down until it clears."),
    ("rate limit", "rate", "Claude rate limit hit: Claude lanes are slowed down until it clears."),
    ("server_error", "server", "Claude API server error ended a lane's turn; it is nudged again on its next round."),
)


def classify(text: str) -> tuple[str, str] | None:
    low = text.lower()
    if any(t in low for t in TRANSIENT):
        return None
    for needle, kind, message in KINDS:
        if needle in low:
            return kind, message
    return None


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


class EventWatch:
    def __init__(self, root: Path, clock: Callable[[], float]):
        self.root, self.clock = Path(root), clock
        self.offsets: dict[Path, int] = {p: _size(p) for p in self._files()}
        self.rest: dict[Path, bytes] = {}
        self.last: dict[str, float] = {}

    def _files(self) -> list[Path]:
        try:
            return sorted(self.root.glob("*/events.jsonl"))
        except OSError:
            return []

    def poll(self) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        for path in self._files():
            size, off = _size(path), self.offsets.get(path, 0)
            if size < off:
                off, self.rest[path] = 0, b""
            if size == off:
                self.offsets[path] = off
                continue
            try:
                with path.open("rb") as f:
                    f.seek(off)
                    chunk = f.read(MAX_READ)
            except OSError:
                continue
            self.offsets[path] = off + len(chunk)
            *lines, self.rest[path] = (self.rest.get(path, b"") + chunk).split(b"\n")
            for raw in lines:
                hit = self._line(raw)
                if hit:
                    out.append((hit[0], path.parent.name, hit[1]))
        return out

    def _line(self, raw: bytes) -> tuple[str, str] | None:
        if b"StopFailure" not in raw:
            return None
        try:
            ev = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(ev, dict) or ev.get("event") != "StopFailure":
            return None
        hit = classify(" ".join(str(ev[k]) for k in ("error", "error_details") if ev.get(k)))
        if hit is None:
            return None
        now = self.clock()
        if now - self.last.get(hit[0], float("-inf")) < COOLDOWN_S:
            return None
        self.last[hit[0]] = now
        return hit
