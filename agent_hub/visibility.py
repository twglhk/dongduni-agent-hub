from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .config import INFRA
from .store import HUMAN, Store, item_view

Viewer = dict[str, Any] | None
ItemRule = Callable[[dict[str, Any], dict[str, Any]], bool]
MemoRule = Callable[[dict[str, Any], str, dict[str, Any]], bool]

BOARD_DIR = "docs/board/"
MEMO_SCAN = 1000


def sees_all(me: Viewer) -> bool:
    return bool(me) and me.get("role") == "owner"


def _on_the_humans_side(me: dict[str, Any], it: dict[str, Any]) -> bool:
    return it.get("audience") == HUMAN


def _addressed_to_me_or_nobody(me: dict[str, Any], it: dict[str, Any]) -> bool:
    return not it.get("assignee") or it["assignee"] == me.get("name")


ITEM_RULES: tuple[ItemRule, ...] = (_on_the_humans_side, _addressed_to_me_or_nobody)


MEMO_RULES: tuple[MemoRule, ...] = ()


def can_see(me: Viewer, it: dict[str, Any]) -> bool:
    if not me:
        return False
    return sees_all(me) or all(rule(me, it) for rule in ITEM_RULES)


def mine(me: Viewer, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [it for it in items if can_see(me, it)]


def can_see_memo(me: Viewer, target: str, memo: dict[str, Any]) -> bool:
    if not me:
        return False
    if sees_all(me):
        return True
    return target != INFRA and all(rule(me, target, memo) for rule in MEMO_RULES)


def _item_where(me: Viewer) -> tuple[str, tuple[Any, ...]]:
    if sees_all(me):
        return "", ()
    if not me:
        return " AND 0", ()
    return (" AND audience=? AND (assignee IS NULL OR assignee='' OR assignee=?)",
            (HUMAN, str(me.get("name") or "")))


def decided(s: Store, me: Viewer, offset: int, limit: int) -> list[dict[str, Any]]:
    where, vals = _item_where(me)
    with s.conn() as con:
        rows = con.execute(f"SELECT * FROM items WHERE state='decided'{where} ORDER BY decided DESC, id DESC "
                           f"LIMIT ? OFFSET ?", (*vals, limit, offset)).fetchall()
    return [item_view(dict(r)) for r in rows]


def decided_count(s: Store, me: Viewer) -> int:
    where, vals = _item_where(me)
    with s.conn() as con:
        return int(con.execute(f"SELECT count(*) FROM items WHERE state='decided'{where}", vals).fetchone()[0])


def memos(s: Store, me: Viewer, target: str, limit: int, offset: int = 0) -> list[dict[str, Any]]:
    if sees_all(me) or (me and target != INFRA and not MEMO_RULES):
        return s.memos(target, limit, offset)
    if not me or target == INFRA:
        return []
    seen = [m for m in s.memos(target, MEMO_SCAN) if can_see_memo(me, target, m)]
    return seen[offset:offset + limit]


def memo_count(s: Store, me: Viewer, target: str) -> int:
    if sees_all(me) or (me and target != INFRA and not MEMO_RULES):
        return s.history_count("memos" if target == INFRA else "requests")
    if not me or target == INFRA:
        return 0
    return len([m for m in s.memos(target, MEMO_SCAN) if can_see_memo(me, target, m)])


def file_allowed(s: Store, me: Viewer, rel: str) -> bool:
    if sees_all(me):
        return True
    rel = "/".join(Path(rel.lstrip("/")).parts)
    folder = rel.rpartition("/")[0] if rel.startswith(BOARD_DIR) else ""
    key = folder + "/" if folder else rel

    def hit(paths: set[str]) -> bool:
        return rel in paths or bool(folder) and any(p.rpartition("/")[0] == folder for p in paths)

    pat = "%" + key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with s.conn() as con:
        cards = con.execute("SELECT * FROM items WHERE attachments LIKE ? ESCAPE '\\'", (pat,)).fetchall()
        holders = [dict(r) for r in cards if hit(_paths(r["attachments"]))]
        if not holders or any(can_see(me, h) for h in holders):
            return True
        reps = con.execute("SELECT attachments FROM reports WHERE attachments LIKE ? ESCAPE '\\'", (pat,)).fetchall()
    return any(hit(_paths(r["attachments"])) for r in reps)


def _paths(raw: str | None) -> set[str]:
    try:
        got = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return set()
    return {"/".join(Path(str(p).lstrip("/")).parts) for p in got if isinstance(p, str)}
