from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

EVERY = ("day", "week")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TITLE_CHARS = 80
AT_FORMAT = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")
ID_FORMAT = re.compile(r"[A-Za-z0-9_-]+")
KEYS = {"id", "lane", "every", "day", "at", "title", "text"}


@dataclass(frozen=True)
class Schedule:
    id: str
    lane: str
    every: str
    day: int
    hour: int
    minute: int
    text: str
    title: str


def _title(text: str) -> str:
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line if len(line) <= TITLE_CHARS else line[: TITLE_CHARS - 1] + "…"


def _one(i: int, raw: Any, kinds: dict[str, str]) -> Schedule:
    where = f"schedules[{i}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")
    if extra := sorted(set(raw) - KEYS):
        raise ValueError(f"{where}: unknown key {', '.join(extra)} (allowed: {', '.join(sorted(KEYS))})")
    lane = raw.get("lane")
    if not isinstance(lane, str) or lane not in kinds:
        raise ValueError(f"{where}: lane {lane!r} is not a lane in tower.json")
    if kinds[lane] not in ("team", "hub"):
        raise ValueError(f"{where}: lane {lane} cannot take a schedule; use a team lane (a task) or the hub (a request)")
    every = raw.get("every")
    if every not in EVERY:
        raise ValueError(f"{where}: every must be \"day\" or \"week\", got {every!r}")
    if every == "day" and "day" in raw:
        raise ValueError(f"{where}: \"day\" belongs to every \"week\"")
    name = str(raw.get("day", "mon")).strip().lower()
    if name not in DAYS:
        raise ValueError(f"{where}: day must be one of {', '.join(DAYS)}, got {raw.get('day')!r}")
    at = raw.get("at", "09:00")
    got = AT_FORMAT.fullmatch(at) if isinstance(at, str) else None
    if not got:
        raise ValueError(f"{where}: at must be a 24-hour time like \"09:00\", got {at!r}")
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{where}: text is required (the task brief, or the request to the hub)")
    title = raw.get("title", _title(text))
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"{where}: title must be a non-empty string")
    hour, minute = int(got.group(1)), int(got.group(2))
    sid = raw.get("id") or f"{lane}-{every}" + (f"-{name}" if every == "week" else "") + f"-{hour:02d}{minute:02d}"
    if not isinstance(sid, str) or not ID_FORMAT.fullmatch(sid):
        raise ValueError(f"{where}: id may use letters, digits, - and _")
    return Schedule(sid, lane, every, DAYS.index(name), hour, minute, text.strip(), title.strip())


def parse(raw: Any, kinds: dict[str, str]) -> list[Schedule]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise ValueError("schedules must be a list of objects: [{\"lane\": ..., \"every\": \"week\", ...}]")
    out = [_one(i, r, kinds) for i, r in enumerate(raw)]
    seen: set[str] = set()
    for s in out:
        if s.id in seen:
            raise ValueError(f"schedules: two entries share the id {s.id!r}; give one an \"id\"")
        seen.add(s.id)
    return out


def latest_period(s: Schedule, now: float) -> str:
    at = datetime.fromtimestamp(now)
    run = at.replace(hour=s.hour, minute=s.minute, second=0, microsecond=0)
    back = (at.weekday() - s.day) % 7 if s.every == "week" else 0
    run -= timedelta(days=back)
    if run > at:
        run -= timedelta(days=7 if s.every == "week" else 1)
    return run.strftime("%Y-%m-%d")

