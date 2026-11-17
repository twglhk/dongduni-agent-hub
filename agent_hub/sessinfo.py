from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
UNKNOWN = "unknown"
_TAIL = 262144
_MAX_SCAN = 8 * _TAIL
_ID = re.compile(r"^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$")
_paths: dict[str, Path] = {}
_read: dict[str, tuple[int, int, dict[str, Any] | None]] = {}


def label(model: str | None) -> str | None:
    if not model:
        return None
    m = _ID.match(model.strip().lower())
    if m:
        return m[1].capitalize() + " " + m[2] + (f".{m[3]}" if m[3] else "")
    return model.capitalize() if model.isalpha() else model


def transcript(sid: str | None, projects: Path = CLAUDE_PROJECTS) -> Path | None:
    if not sid:
        return None
    p = _paths.get(sid)
    if p is not None and p.exists():
        return p
    p = next(iter(projects.glob(f"*/{sid}.jsonl")), None) if projects.is_dir() else None
    if p is not None:
        _paths[sid] = p
    return p


def from_transcript(sid: str | None, projects: Path = CLAUDE_PROJECTS) -> dict[str, Any] | None:
    p = transcript(sid, projects)
    if p is None:
        return None
    try:
        st = p.stat()
    except OSError:
        return None
    key = f"{sid}@{projects}"
    seen = _read.get(key)
    if seen and seen[:2] == (st.st_mtime_ns, st.st_size):
        return seen[2]
    got = _tail_read(p)
    _read[key] = (st.st_mtime_ns, st.st_size, got)
    return got


def _tail_read(p: Path) -> dict[str, Any] | None:
    try:
        with p.open("rb") as f:
            end = f.seek(0, 2)
            pos, carry = end, b""
            model = effort = None
            while pos > 0 and end - pos < _MAX_SCAN:
                step = min(_TAIL, pos)
                pos -= step
                f.seek(pos)
                chunk = f.read(step) + carry
                lines = chunk.split(b"\n")
                carry = lines.pop(0) if pos > 0 else b""
                for raw in reversed(lines):
                    if b'"assistant"' not in raw:
                        continue
                    try:
                        e = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(e, dict) or e.get("type") != "assistant":
                        continue
                    msg = e.get("message") if isinstance(e.get("message"), dict) else {}
                    m = msg.get("model") if str(msg.get("model") or "").startswith("claude") else None
                    model = model or m
                    effort = effort or e.get("effort") or e.get("perTurnEffort")
                    if model and effort:
                        return {"model": model, "effort": effort}
            return {"model": model, "effort": effort} if model or effort else None
    except OSError:
        return None


def describe(runtime: str, sid: str | None = None, last_sid: str | None = None, opts: dict[str, Any] | None = None,
             configured: str | None = None, projects: Path = CLAUDE_PROJECTS) -> dict[str, Any]:
    opts = opts or {}
    if runtime != "claude":
        model, effort = opts.get("model") or configured, opts.get("effort")
        why = f"{runtime} session transcripts are not read"
        return _out(model, effort, "live" if sid else "next", why)
    if sid:
        t = from_transcript(sid, projects) or {}
        model, effort = t.get("model") or opts.get("model") or configured, t.get("effort") or opts.get("effort")
        why = None if t else "the session has not written its first answer yet"
        return _out(model, effort, "live", why or ("the transcript has no effort value" if not effort else None))
    t = from_transcript(last_sid, projects)
    if t:
        return _out(t.get("model"), t.get("effort"), "last", None if t.get("effort") else "the transcript has no effort value")
    why = "no session opened yet; the effort is set when a session opens" if configured else "no session opened yet and tower.json names no model"
    return _out(configured, None, "next", why)


def for_lane(cfg: Any, store: Any, lane: str, runtime: str, model: str | None, session: str | None,
             projects: Path = CLAUDE_PROJECTS) -> dict[str, Any]:
    try:
        opts = json.loads((cfg.state_dir / "logs" / f"{lane}.opts.json").read_text(encoding="utf-8")) if session else {}
    except (OSError, ValueError):
        opts = {}
    try:
        found = next((x for x in store.recent_sessions(lane) if transcript(x, projects)), None) if runtime == "claude" else None
        return describe(runtime, session, found, opts, model or None, projects)
    except (OSError, ValueError, KeyError) as e:
        return _out(None, None, "next", f"could not read it: {e}")


def _out(model: str | None, effort: str | None, state: str, why: str | None) -> dict[str, Any]:
    name = label(model)
    return {"model": name, "effort": effort, "text": f"{name or UNKNOWN} · {effort or UNKNOWN}",
            "state": state, "why": why if (not name or not effort) else None}
