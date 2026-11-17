from __future__ import annotations

import re
import sqlite3
from typing import Any

from .config import HUB, INFRA, Config
from .fair import message_who, task_who
from .queues import person
from .store import Store
from .visibility import Gate, sees_scoped

REQUEST = re.compile(r"(?:request|요청)\s*#(\d+)", re.I)
REPORT_CHARS = 280


def _title(text: str) -> str:
    first = next((x.strip() for x in (text or "").splitlines() if x.strip()), "")
    m = re.match(r"^\[([^\]]+)\]", first) or re.match(r'^Decision #\d+ "(.+?)"', first)
    return (m.group(1) if m else first)[:140]


def _request(text: str) -> int | None:
    m = REQUEST.search(text or "")
    return int(m.group(1)) if m else None


def _memo(cfg: Config, con: sqlite3.Connection, key: str, mid: int, live: bool) -> dict[str, Any] | None:
    m = con.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    if not m:
        return None
    m = dict(m)
    who = message_who(m)
    ended = None
    if not live:
        r = con.execute("SELECT MAX(created) FROM reports WHERE lane=? AND kind='wrapped'", (key,)).fetchone()
        ended = r[0] if r else None
    return {"kind": "decision" if m["kind"] == "decision" else "memo", "id": m["id"], "title": _title(m["text"]),
            "body": m["text"], "from": person(cfg, who), "hub": who if cfg.is_hub(who) else None,
            "request": _request(m["text"]), "item": m["item_id"], "started": m["delivered"], "ended": ended, "live": live}


def _task(cfg: Config, t: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "task", "id": t["id"], "title": t["title"], "body": t["brief"], "from": person(cfg, task_who(t)),
            "hub": t.get("hub") or HUB, "request": _request(t["brief"]), "item": None,
            "started": t["started"], "ended": t["closed"], "live": t["state"] != "closed"}


def _work(cfg: Config, s: Store, key: str, kind: str, row: dict[str, Any], live: bool) -> dict[str, Any] | None:
    with s.conn() as con:
        if kind == INFRA:
            mid = row.get("active_message") if live else row.get("last_message")
            return _memo(cfg, con, key, int(mid), live) if mid is not None else None
        t = con.execute("SELECT * FROM tasks WHERE lane=? AND state IN ('running','wrapping') ORDER BY id DESC LIMIT 1",
                        (key,)).fetchone()
        t = t or con.execute("SELECT * FROM tasks WHERE lane=? AND state='closed' ORDER BY COALESCE(closed, id) DESC "
                             "LIMIT 1", (key,)).fetchone()
        return _task(cfg, dict(t)) if t else None


def _report(s: Store, key: str) -> dict[str, Any] | None:
    with s.conn() as con:
        rep = con.execute("SELECT kind, text, created FROM reports WHERE lane=? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
    if not rep:
        return None
    text = " ".join(str(rep["text"] or "").split("||")[0].split())
    return {"kind": rep["kind"], "text": text[:REPORT_CHARS - 1] + "…" if len(text) > REPORT_CHARS else text,
            "created": rep["created"]}


def session_view(cfg: Config, s: Store, key: str, g: Gate | None = None) -> dict[str, Any] | None:
    ln = cfg.all_lanes.get(key)
    if ln is None:
        return None
    if g is not None and ln.owner and not sees_scoped(g, ln.owner):
        return None
    row = s.lane(key)
    live = (row.get("status") or "closed") != "closed"
    work = _work(cfg, s, key, ln.kind, row, live) if ln.kind in ("team", INFRA) else None
    hidden = bool(g is not None and work and not sees_scoped(g, work["from"]))
    if hidden and live:
        return None
    if hidden:
        work = None
    return {"key": key, "live": live, "work": work, "report": None if hidden else _report(s, key), "hidden": hidden}
