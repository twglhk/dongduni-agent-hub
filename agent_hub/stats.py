"""`dah stats` and `dah retros`: the task ledger grouped by lane, engine, model, effort or week. Computes and renders text only.

One row per group: tasks, closed, cancelled before they ran, tokens (new / cache read / out), turns, tells (messages the hub
sent into the task), nudges, rotations (extra sessions a task went through). A task belongs to the group of the day it was
queued. Tokens come from the agent transcripts (`runtimes.ledger`), measured while a session works and when its turn ends.

A wrapped report may end with a retro: `one line || retro: cost=... | corrected=... | rule=...`. `split_retro` cuts it
off; the line goes to the board, the three fields to the retro log.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .config import base_lane
from .store import Store

BY = ("lane", "engine", "model", "effort", "week")
NONE_KEY = "-"
RETRO_FIELDS = ("cost", "corrected", "rule")
_FIELD_SPLIT = re.compile(r"\s*\|\s*(?=(?:%s)\s*=)" % "|".join(RETRO_FIELDS), re.I)


def split_retro(text: str) -> tuple[str, dict[str, str] | None]:
    """`line || retro: cost=... | corrected=... | rule=...` -> (line, {cost, corrected, rule, raw}); no retro -> (text, None)."""
    head, sep, tail = text.partition("||")
    if not sep:
        return text, None
    raw = re.sub(r"^\s*retro\s*:\s*", "", tail.strip(), flags=re.I)
    fields = {k: "" for k in RETRO_FIELDS}
    for part in _FIELD_SPLIT.split(raw):
        k, eq, v = part.partition("=")
        if eq and k.strip().lower() in fields:
            fields[k.strip().lower()] = v.strip()
    return head.strip(), {**fields, "raw": raw}


def week_of(ts: float) -> str:
    y, w, _ = datetime.fromtimestamp(ts).isocalendar()
    return f"{y}-W{w:02d}"


def day_start(day: str) -> float:
    return datetime.strptime(day, "%Y-%m-%d").timestamp()


def mtok(n: float) -> str:
    return f"{n / 1e6:.2f}M" if n >= 1e5 else f"{n / 1e3:.0f}k" if n >= 1e3 else f"{n:.0f}"


def collect(store: Store, lane: str | None = None, since: float = 0.0) -> list[dict[str, Any]]:
    """Tasks queued since `since` (all of a lane's parallel sessions count as the lane), with their message counts and rotations."""
    with store.conn() as con:
        tasks = [dict(r) for r in con.execute("SELECT * FROM tasks WHERE created>=? ORDER BY id", (since,))]
        tells = dict(con.execute("SELECT task_id, count(*) FROM messages WHERE kind='tell' AND task_id IS NOT NULL "
                                 "GROUP BY task_id").fetchall())
        nudges = dict(con.execute("SELECT task_id, count(*) FROM messages WHERE kind='nudge' AND task_id IS NOT NULL "
                                  "GROUP BY task_id").fetchall())
    rot = store.task_rotations()
    for t in tasks:
        t["lane"] = base_lane(t["lane"])
        t["tells"], t["nudges"], t["rotations"] = tells.get(t["id"], 0), nudges.get(t["id"], 0), max(0, rot.get(t["id"], 0))
    return [t for t in tasks if not lane or t["lane"] == base_lane(lane)]


def group_of(by: str, t: dict[str, Any]) -> str:
    if by == "week":
        return week_of(float(t["created"]))
    return str(t.get(by) or NONE_KEY)


def summarize(tasks: list[dict[str, Any]], by: str) -> dict[str, Any]:
    if by not in BY:
        raise ValueError(f"group by one of: {', '.join(BY)}")
    groups: dict[str, dict[str, Any]] = {}
    for t in tasks:
        g = groups.setdefault(group_of(by, t), {"group": group_of(by, t), "tasks": 0, "closed": 0, "cancelled_early": 0,
                                                "new": 0, "cache": 0, "out": 0, "turns": 0, "tells": 0, "nudges": 0,
                                                "rotations": 0})
        g["tasks"] += 1
        g["closed"] += t["state"] == "closed"
        g["cancelled_early"] += t["state"] == "cancelled" and not t["started"]
        for k, f in (("new", "tok_new"), ("cache", "tok_cache"), ("out", "tok_out"), ("turns", "turns")):
            g[k] += t[f] or 0
        for k in ("tells", "nudges", "rotations"):
            g[k] += t[k]
    rows = sorted(groups.values(), key=lambda g: (g["group"] == NONE_KEY, g["group"]))
    return {"by": by, "rows": rows, "unmeasured": sum(1 for t in tasks if t["started"] and t["tok_at"] is None)}


def render(summary: dict[str, Any], lane: str | None = None, since: str | None = None) -> str:
    head = f"ledger · by {summary['by']}" + (f" · lane {lane}" if lane else "") + (f" · since {since}" if since else "")
    cols = ("group", "tasks", "closed", "cancelled", "new", "cache", "out", "turns", "tells", "nudges", "rotations")
    keys = ("tasks", "closed", "cancelled_early", "new", "cache", "out", "turns", "tells", "nudges", "rotations")
    tok = {"new", "cache", "out"}

    def cell(g: dict[str, Any], k: str) -> Any:
        return mtok(g[k]) if k in tok else g[k]

    table: list[tuple[Any, ...]] = [cols] + [(g["group"], *[cell(g, k) for k in keys]) for g in summary["rows"]]
    total = {k: sum(g[k] for g in summary["rows"]) for k in keys}
    table.append(("total", *[cell(total, k) for k in keys]))
    widths = [max(len(str(r[i])) for r in table) for i in range(len(cols))]
    lines = [head]
    for i, r in enumerate(table):
        lines.append("  ".join(str(c).ljust(w) if j == 0 else str(c).rjust(w) for j, (c, w) in enumerate(zip(r, widths))))
        if i == 0:
            lines.append("  ".join("-" * w for w in widths))
    if summary["unmeasured"]:
        lines.append(f"\n{summary['unmeasured']} started task(s) have no token reading yet (no readable session).")
    return "\n".join(lines)


def render_retros(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No retros yet."
    out = []
    for r in reversed(rows):  # oldest first
        stamp = datetime.fromtimestamp(r["created"]).strftime("%m-%d %H:%M")
        out.append(f"[{stamp}] {r['lane']}" + (f" task #{r['task_id']}" if r["task_id"] else "") + f" - {r['summary']}")
        for k in RETRO_FIELDS:
            if r[k]:
                out.append(f"    {k}: {r[k]}")
        if not any(r[k] for k in RETRO_FIELDS) and r["raw"]:
            out.append(f"    raw: {r['raw']}")
    return "\n".join(out)
