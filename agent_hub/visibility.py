from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from . import worklog
from .config import INFRA, Config
from .fair import task_who
from .requester import visible_to_requester
from .store import HOLD, HUMAN, Store, StoreError, item_view
from .uploads import TAIL

Viewer = dict[str, Any] | None
ItemRule = Callable[["Gate", dict[str, Any]], bool]
MemoRule = Callable[["Gate", str, dict[str, Any]], bool]

BOARD_DIR = "docs/board/"
UPLOADS = "uploads/"
TAIL_ANY = re.compile(TAIL.pattern, re.I)
MEMO_SCAN = 1000


def sees_all(me: Viewer) -> bool:
    return bool(me) and me.get("role") == "owner"


class Gate:
    def __init__(self, s: Store, me: Viewer, requester: bool = False, person_hubs: Mapping[str, str] | None = None,
                 lane_owners: Mapping[str, str] | None = None):
        self.s, self.me, self.requester = s, me, requester
        self.person_hubs = dict(person_hubs or {})
        self.lane_owners = dict(lane_owners or {})

    @classmethod
    def of(cls, cfg: Config, s: Store, me: Viewer) -> Gate:
        return cls(s, me, cfg.requester, {lid: ln.owner for lid, ln in cfg.person_hubs.items() if ln.owner},
                   {lid: ln.owner or "" for lid, ln in cfg.all_lanes.items()})

    def who_of(self, key: str) -> str | None:
        if not key:
            return None
        if key in self.lane_owners:
            return self.lane_owners[key] or None
        return self.person_hubs.get(key, key)

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


def sees_scoped(g: Gate, who: str | None) -> bool:
    if g.owner:
        return True
    if not g.me:
        return False
    if not (g.person_hubs or g.requester) or not who:
        return True
    return who == g.name


def task_visible(g: Gate, t: dict[str, Any]) -> bool:
    return sees_scoped(g, g.who_of(task_who(t)))


def _masked(row: dict[str, Any], *keep: str) -> dict[str, Any]:
    return {"id": row["id"], "hidden": True, **{k: row.get(k) for k in keep}}


def task_row(g: Gate, t: dict[str, Any]) -> dict[str, Any]:
    if task_visible(g, t):
        return t
    return _masked(t, "lane", "state", "created", "started", "closed")


def memo_visible(g: Gate, memo: dict[str, Any]) -> bool:
    if g.owner:
        return True
    if not g.me:
        return False
    if not (g.person_hubs or g.requester):
        return True
    return can_see_memo(g, INFRA, memo)


def report_visible(g: Gate, r: dict[str, Any]) -> bool:
    if g.owner:
        return True
    if not g.me:
        return False
    if not (g.person_hubs or g.requester):
        return True
    if r.get("task_id") is not None:
        try:
            return task_visible(g, g.s.task(int(r["task_id"])))
        except StoreError:
            return False
    if r.get("message_id") is not None:
        try:
            m = g.s.message(int(r["message_id"]))
        except StoreError:
            return False
        return can_see_memo(g, str(m.get("target") or INFRA), m)
    owner = g.person_hubs.get(str(r.get("lane") or ""))
    return bool(owner) and owner == g.name


def report_row(g: Gate, r: dict[str, Any]) -> dict[str, Any]:
    if report_visible(g, r):
        return r
    return _masked(r, "lane", "kind", "created")


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


def _work_allow(g: Gate) -> worklog.Allow | None:
    if g.owner:
        return None
    if not g.me:
        return lambda _: False
    if not (g.person_hubs or g.requester):
        return None

    def allow(key: dict[str, Any]) -> bool:
        who = str(key["from"] or "")
        return key["src"] != "report" and bool(who) and (g.person_hubs.get(who) or who) == g.name
    return allow


def work(g: Gate, offset: int, limit: int) -> list[dict[str, Any]]:
    card = None if g.owner else (lambda it: can_see(g, it))
    with g.s.conn() as con:
        return worklog.page(con, offset, limit, _work_allow(g), card)


def work_count(g: Gate) -> int:
    with g.s.conn() as con:
        return worklog.count(con, _work_allow(g))


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _request_visible(g: Gate, rid: int) -> bool:
    try:
        m = g.s.message(rid)
    except StoreError:
        return False
    target = str(m["target"])
    return m["kind"] == "memo" and not target.startswith(HOLD) and can_see_memo(g, target, m)


def _upload_rid(rel: str) -> int | None:
    parts = rel.split("/")
    return int(parts[2]) if len(parts) > 3 and parts[2].isdigit() else None


def _same_file(root: Path, full: Path, rel: str) -> bool:
    try:
        return os.path.samefile(root / rel, full)
    except (OSError, ValueError):
        return False


def _same_folder(root: Path, full: Path, rel: str) -> bool:
    try:
        return os.path.samefile((root / rel).parent, full.parent)
    except (OSError, ValueError):
        return False


def _attached(g: Gate, rel: str, root: Path | None = None, full: Path | None = None) -> bool:
    folded = rel.casefold()
    folder = rel.rpartition("/")[0] if root and folded.startswith(BOARD_DIR) else ""

    def hit(paths: set[str]) -> bool:
        if root is None or full is None:
            return rel in paths
        for p in paths:
            if p.casefold() == folded and _same_file(root, full, p):
                return True
            if folder and p.casefold().startswith(BOARD_DIR) and p.rpartition("/")[0].casefold() == folder.casefold() \
                    and _same_folder(root, full, p):
                return True
        return False
    pat = _like((folder + "/") if folder else rel)
    with g.s.conn() as con:
        cards = [dict(r) for r in con.execute("SELECT * FROM items WHERE attachments LIKE ? ESCAPE '\\'", (pat,))]
        reps = [dict(r) for r in con.execute("SELECT * FROM reports WHERE attachments LIKE ? ESCAPE '\\'", (pat,))]
    return (any(hit(_paths(it["attachments"])) and can_see(g, it) for it in cards)
            or any(hit(_paths(r["attachments"])) and report_visible(g, r) for r in reps))


def upload_allowed(g: Gate, rel: str, root: Path | None = None, full: Path | None = None) -> bool:
    if not g.me:
        return False
    if g.owner:
        return True
    rid = _upload_rid(rel)
    return (rid is not None and _request_visible(g, rid)) or _attached(g, rel, root, full)


def file_allowed(g: Gate, root: Path, full: Path) -> bool:
    if not g.me:
        return False
    if g.owner or not g.s.members():
        return True
    try:
        rel = "/".join(full.relative_to(root.resolve()).parts)
    except ValueError:
        return False
    if rel.casefold().startswith(UPLOADS):
        return bool(TAIL_ANY.fullmatch(rel)) and upload_allowed(g, rel, root.resolve(), full)
    return _attached(g, rel, root.resolve(), full)


def _paths(raw: str | None) -> set[str]:
    try:
        got = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return set()
    return {"/".join(Path(str(p).lstrip("/")).parts) for p in got if isinstance(p, str)}
