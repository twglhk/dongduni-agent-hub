from __future__ import annotations

import bisect
import json
import re
import sqlite3
from typing import Any, Callable

INFRA_SQL = "(target='infra' OR target LIKE 'infra#%')"
NOTICE_RE = re.compile(r"^Wrapped: infra (?:memo|\w+ message) #(\d+)")
MEMO_RE = re.compile(r"(?:memo|message)\s*#(\d+)", re.I)
ITEM_RE = re.compile(r"(?:decision|card|item)\s*#(\d+)", re.I)
COMMIT_RE = re.compile(r"(?<![0-9A-Za-z#/])(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}(?![0-9A-Za-z])")
BRIEF_HEADER = re.compile(r"^(?:Line|model|effort|after|paths|engine|deliverable|request|requester):", re.I)
EMPTY = {"none", "-", "n/a"}
SAME_MOMENT = 2.0
SCAN = 2000

Allow = Callable[[dict[str, Any]], bool]


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _brief(text: str) -> tuple[str, list[str]]:
    body, paths = [], []
    for line in (text or "").splitlines():
        if BRIEF_HEADER.match(line.strip()):
            if line.lower().startswith("paths:"):
                paths += [p.strip() for p in line.split(":", 1)[1].split(",") if p.strip()]
            continue
        body.append(line)
    return "\n".join(body).strip(), paths


def _title(text: str) -> str:
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    m = re.match(r"^\[([^\]]{2,80})\]", first)
    if m:
        return m.group(1)
    return first if len(first) <= 70 else first[:69] + "…"


def _commits(*texts: str) -> list[str]:
    seen: list[str] = []
    for t in texts:
        for h in COMMIT_RE.findall(t or ""):
            if h not in seen:
                seen.append(h)
    return seen


def line_of(t: dict[str, Any]) -> dict[str, Any] | None:
    row = t.get("line_row")
    if not row:
        return None
    row = str(row)
    full, slash, sub = row.partition("/")
    base = {"row": row, "stage": t.get("line_stage"), "mode": t.get("line_mode")}
    if slash:
        return {**base, "full": full, "sub": sub}
    return {**base, "full": None, "sub": None}


def _retro_for(retros: list[dict[str, Any]], rep: dict[str, Any]) -> dict[str, Any] | None:
    for r in retros:
        if r["lane"] == rep["lane"] and abs(r["created"] - rep["created"]) < SAME_MOMENT:
            return {k: r[k] for k in ("cost", "corrected", "rule") if r[k] and r[k].strip().lower() not in EMPTY} or None
    return None


def _infra_links(con: sqlite3.Connection, reports: list[dict[str, Any]],
                 msgs: dict[int, dict[str, Any]]) -> dict[int, int]:
    notices = _rows(con.execute("SELECT text, created FROM messages WHERE kind='wrapped' AND sender='infra' ORDER BY created"))
    times = [n["created"] for n in notices]
    by_item: dict[int, list[int]] = {}
    for mid, m in msgs.items():
        if m.get("item_id"):
            by_item.setdefault(int(m["item_id"]), []).append(mid)
    out: dict[int, int] = {}
    for r in reports:
        mid = r.get("message_id") if r.get("message_id") in msgs else None
        for n in ([] if mid is not None else notices[bisect.bisect_left(times, r["created"] - SAME_MOMENT):]):
            if n["created"] > r["created"] + SAME_MOMENT:
                break
            hit = NOTICE_RE.match(n["text"])
            if hit and int(hit.group(1)) in msgs:
                mid = int(hit.group(1))
                break
        if mid is None:
            hit = MEMO_RE.search(r["text"])
            if hit and int(hit.group(1)) in msgs:
                mid = int(hit.group(1))
        if mid is None:
            hit = ITEM_RE.search(r["text"])
            cands = [x for x in by_item.get(int(hit.group(1)), []) if msgs[x]["created"] <= r["created"]] if hit else []
            mid = max(cands) if cands else None
        if mid is not None:
            out[r["id"]] = mid
    return out


def _report_view(r: dict[str, Any], retros: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": r["id"], "lane": r["lane"], "kind": r["kind"], "text": r["text"], "created": r["created"],
            "attachments": json.loads(r["attachments"] or "[]"), "retro": _retro_for(retros, r) if r["kind"] == "wrapped" else None}


def _finish(row: dict[str, Any]) -> dict[str, Any]:
    texts = [x["text"] for x in row["reports"]] + [x["text"] for x in row.get("replies", [])]
    row["commits"] = _commits(*texts)
    files = list(row.get("paths") or [])
    for x in row["reports"]:
        files += [p for p in x["attachments"] if p not in files]
    row["files"] = files
    ends = [x["created"] for x in row["reports"] if x["kind"] in ("done", "wrapped", "note")] + \
           [x["created"] for x in row.get("replies", [])]
    if row.get("ended") is None and ends and row["state"] in ("done", "cancelled"):
        row["ended"] = max(ends)
    return row


def _infra(con: sqlite3.Connection) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]], dict[int, int]]:
    msgs = {r["id"]: r for r in _rows(con.execute(
        f"SELECT id, item_id, sender, created FROM messages WHERE {INFRA_SQL} AND kind IN ('memo','decision')"))}
    reps = _rows(con.execute("SELECT * FROM reports WHERE task_id IS NULL ORDER BY id"))
    return msgs, reps, _infra_links(con, reps, msgs)


def _assignees(con: sqlite3.Connection, msgs: dict[int, dict[str, Any]]) -> dict[int, str | None]:
    ids = sorted({int(m["item_id"]) for m in msgs.values() if m.get("item_id")})
    out: dict[int, str | None] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in con.execute(f"SELECT id, assignee FROM items WHERE id IN ({','.join('?' * len(chunk))})", chunk):
            out[r[0]] = r[1]
    return out


def feed(con: sqlite3.Connection, infra: tuple | None = None) -> list[dict[str, Any]]:
    keys = [{"src": "task", "id": r[0], "at": r[1], "from": r[2] or r[3] or "", "lane": r[4]} for r in con.execute(
        "SELECT id, max(created, coalesce(started, 0), coalesce(closed, 0)), requester, hub, lane FROM tasks")]
    msgs, infra_reps, links = infra or _infra(con)
    assignee = _assignees(con, msgs)
    last: dict[int, float] = {}
    for r in infra_reps:
        if r["id"] in links:
            last[links[r["id"]]] = max(last.get(links[r["id"]], 0), r["created"])
        else:
            keys.append({"src": "report", "id": r["id"], "at": r["created"], "from": None, "lane": r["lane"]})
    for mid, m in msgs.items():
        who = assignee.get(int(m["item_id"])) if m.get("item_id") else None
        keys.append({"src": "infra", "id": mid, "at": last.get(mid, m["created"]), "from": who or m["sender"] or "",
                     "lane": "infra"})
    keys.sort(key=lambda k: (k["at"], k["id"]), reverse=True)
    return keys



def visible_keys(con: sqlite3.Connection, allow: Allow | None = None) -> list[dict[str, Any]]:
    keys = feed(con)
    if allow is None:
        return keys
    return [k for k in keys[:SCAN] if allow(k)]


def count(con: sqlite3.Connection, allow: Allow | None = None) -> int:
    return len(visible_keys(con, allow))


def _items_of_task(con: sqlite3.Connection, task_id: int) -> list[dict[str, Any]]:
    return _rows(con.execute("SELECT * FROM items WHERE task_id=? ORDER BY id", (task_id,)))


def _card(it: dict[str, Any]) -> dict[str, Any]:
    return {"id": it["id"], "title": it["title"], "kind": it["kind"], "state": it["state"]}


def page(con: sqlite3.Connection, offset: int = 0, limit: int = 20, allow: Allow | None = None,
         card_allow: Allow | None = None) -> list[dict[str, Any]]:
    infra = _infra(con)
    keys = (feed(con, infra) if allow is None else [k for k in feed(con, infra)[:SCAN] if allow(k)])[offset:offset + limit]
    if not keys:
        return []
    _, infra_reps, links = infra
    retros = _rows(con.execute("SELECT * FROM retros"))
    holder = {r[0]: r[1] for r in con.execute("SELECT active_message, lane FROM lanes WHERE active_message IS NOT NULL")}
    shown = card_allow or (lambda _: True)
    out: list[dict[str, Any]] = []
    for k in keys:
        if k["src"] == "task":
            t = _rows(con.execute("SELECT * FROM tasks WHERE id=?", (k["id"],)))[0]
            brief, paths = _brief(t["brief"])
            reps = _rows(con.execute("SELECT * FROM reports WHERE task_id=? ORDER BY id", (t["id"],)))
            out.append(_finish({
                "src": "task", "id": t["id"], "at": k["at"], "lane": t["lane"], "title": t["title"], "brief": brief,
                "state": t["state"], "requester": t["requester"], "by": t["hub"] or "hub", "paths": paths,
                "line": line_of(t), "started": t["started"],
                "ended": t["closed"] if t["state"] in ("closed", "cancelled") else None,
                "model": t["model"], "effort": t["effort"],
                "cards": [_card(i) for i in _items_of_task(con, t["id"]) if shown(i)],
                "reports": [_report_view(r, retros) for r in reps]}))
        elif k["src"] == "infra":
            m = _rows(con.execute("SELECT * FROM messages WHERE id=?", (k["id"],)))[0]
            item = _rows(con.execute("SELECT * FROM items WHERE id=?", (m["item_id"],))) if m["item_id"] else []
            card = item[0] if item else None
            reps = [r for r in infra_reps if links.get(r["id"]) == m["id"]]
            replies = _rows(con.execute("SELECT text, created, sender FROM messages WHERE kind='memo_reply' "
                                        "AND item_id=? AND sender LIKE 'infra%' ORDER BY id", (m["id"],))) \
                if m["kind"] == "memo" else []
            cancelled = any(r["kind"] == "note" for r in reps)
            done = bool(reps) or bool(replies)
            out.append(_finish({
                "src": "infra", "id": m["id"], "kind": m["kind"], "item_id": m["item_id"], "at": k["at"],
                "lane": next((r["lane"] for r in reps), holder.get(m["id"], "infra")),
                "title": card["title"] if card else _title(m["text"]), "brief": m["text"],
                "state": "cancelled" if cancelled else "done" if done else ("running" if m["delivered"] else "queued"),
                "by": m["sender"], "requester": k["from"] or None,
                "paths": [p.strip() for p in (m["paths"] or "").split(",") if p.strip()],
                "started": m["delivered"], "ended": None,
                "cards": [_card(card)] if card and shown(card) else [],
                "reports": [_report_view(r, retros) for r in reps], "replies": replies}))
        else:
            r = _rows(con.execute("SELECT * FROM reports WHERE id=?", (k["id"],)))[0]
            out.append(_finish({
                "src": "report", "id": r["id"], "at": k["at"], "lane": r["lane"], "title": _title(r["text"]),
                "brief": "", "state": "done", "by": None, "requester": None, "paths": [], "cards": [], "started": None,
                "ended": r["created"], "reports": [_report_view(r, retros)]}))
    return out
