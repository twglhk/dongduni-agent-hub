from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from .config import INFRA, Config
from .requester import visible_to_requester
from .store import HUMAN, Store, item_view

Viewer = dict[str, Any] | None
ItemRule = Callable[["Gate", dict[str, Any]], bool]
MemoRule = Callable[["Gate", str, dict[str, Any]], bool]

BOARD_DIR = "docs/board/"
MEMO_SCAN = 1000


def sees_all(me: Viewer) -> bool:
    return bool(me) and me.get("role") == "owner"


class Gate:
    def __init__(self, s: Store, me: Viewer, requester: bool = False, person_hubs: Mapping[str, str] | None = None):
        self.s, self.me, self.requester = s, me, requester
        self.person_hubs = dict(person_hubs or {})

    @classmethod
    def of(cls, cfg: Config, s: Store, me: Viewer) -> Gate:
        return cls(s, me, cfg.requester, {lid: ln.owner for lid, ln in cfg.person_hubs.items() if ln.owner})

    @property
    def name(self) -> str:
        return str(self.me.get("name") or "") if self.me else ""

    @property
    def owner(self) -> bool:
        return sees_all(self.me)

    @property
    def own_hubs(self) -> list[str]:
        return [lid for lid, who in self.person_hubs.items() if who == self.name]

    def in_my_hub(self, it: dict[str, Any]) -> bool:
        if not (self.person_hubs and self.name):
            return False
        if it.get("lane") in self.own_hubs:
            return True
        tid = it.get("task_id")
        if tid is None:
            return False
        return self.s.hub_for_task(tid) in self.own_hubs or (self.requester and self.s.requester_of_task(tid) == self.name)


def _on_the_humans_side_or_in_my_hub(g: Gate, it: dict[str, Any]) -> bool:
    return it.get("audience") == HUMAN or g.in_my_hub(it)


def _addressed_to_me_or_nobody(g: Gate, it: dict[str, Any]) -> bool:
    return visible_to_requester(it, g.name, False)


ITEM_RULES: tuple[ItemRule, ...] = (_on_the_humans_side_or_in_my_hub, _addressed_to_me_or_nobody)


MEMO_RULES: tuple[MemoRule, ...] = ()


def _not_another_persons_hub(g: Gate, target: str, memo: dict[str, Any]) -> bool:
    where = str(memo.get("target") or target)
    return where not in g.person_hubs or where in g.own_hubs


def _my_requests_or_my_hub_chat(g: Gate, target: str, memo: dict[str, Any]) -> bool:
    return memo.get("sender") == g.name or str(memo.get("target") or target) in g.own_hubs


def can_see(g: Gate, it: dict[str, Any]) -> bool:
    if not g.me:
        return False
    return g.owner or all(rule(g, it) for rule in ITEM_RULES)


def mine(g: Gate, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [it for it in items if can_see(g, it)]


def _caused_by_me(g: Gate, memo: dict[str, Any]) -> bool:
    return str(memo.get("sender") or "") in g.own_hubs


def can_see_memo(g: Gate, target: str, memo: dict[str, Any]) -> bool:
    if not g.me:
        return False
    if g.owner:
        return True
    if target == INFRA:
        return _caused_by_me(g, memo)
    if not _not_another_persons_hub(g, target, memo):
        return False
    if g.requester and not _my_requests_or_my_hub_chat(g, target, memo):
        return False
    return all(rule(g, target, memo) for rule in MEMO_RULES)


def _item_where(g: Gate) -> tuple[str, tuple[Any, ...]]:
    if g.owner:
        return "", ()
    if not g.me:
        return " AND 0", ()
    side, side_vals = " AND audience=?", [HUMAN]
    if g.person_hubs:
        hubs = g.own_hubs
        marks = ",".join("?" * len(hubs))
        by_task = [f"hub IN ({marks})"] if hubs else []
        task_vals = list(hubs)
        if g.requester:
            by_task.append("requester=?")
            task_vals.append(g.name)
        clauses = ["audience=?"] + ([f"lane IN ({marks})"] if hubs else [])
        side_vals = [HUMAN, *hubs]
        if by_task:
            clauses.append(f"task_id IN (SELECT id FROM tasks WHERE {' OR '.join(by_task)})")
            side_vals += task_vals
        side = f" AND ({' OR '.join(clauses)})"
    return (side + " AND (assignee IS NULL OR assignee='' OR assignee=?)", (*side_vals, g.name))


def decided(g: Gate, offset: int, limit: int) -> list[dict[str, Any]]:
    where, vals = _item_where(g)
    with g.s.conn() as con:
        rows = con.execute(f"SELECT * FROM items WHERE state='decided'{where} ORDER BY decided DESC, id DESC "
                           f"LIMIT ? OFFSET ?", (*vals, limit, offset)).fetchall()
    return [item_view(dict(r)) for r in rows]


def decided_count(g: Gate) -> int:
    where, vals = _item_where(g)
    with g.s.conn() as con:
        return int(con.execute(f"SELECT count(*) FROM items WHERE state='decided'{where}", vals).fetchone()[0])


def _everything_shown(g: Gate, target: str) -> bool:
    return g.owner or bool(g.me and target != INFRA and not g.requester and not g.person_hubs and not MEMO_RULES)


def memos(g: Gate, target: str, limit: int, offset: int = 0) -> list[dict[str, Any]]:
    if _everything_shown(g, target):
        return g.s.memos(target, limit, offset)
    if not g.me:
        return []
    seen = [m for m in g.s.memos(target, MEMO_SCAN) if can_see_memo(g, target, m)]
    return seen[offset:offset + limit]


def memo_count(g: Gate, target: str) -> int:
    if _everything_shown(g, target):
        return g.s.history_count("memos" if target == INFRA else "requests")
    if not g.me:
        return 0
    return len([m for m in g.s.memos(target, MEMO_SCAN) if can_see_memo(g, target, m)])


def file_allowed(g: Gate, rel: str) -> bool:
    s = g.s
    if g.owner:
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
        if not holders or any(can_see(g, h) for h in holders):
            return True
        reps = con.execute("SELECT attachments FROM reports WHERE attachments LIKE ? ESCAPE '\\'", (pat,)).fetchall()
    return any(hit(_paths(r["attachments"])) for r in reps)


def _paths(raw: str | None) -> set[str]:
    try:
        got = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return set()
    return {"/".join(Path(str(p).lstrip("/")).parts) for p in got if isinstance(p, str)}
