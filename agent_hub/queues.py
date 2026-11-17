from __future__ import annotations

import time
from typing import Any

from .config import INFRA, Config
from .engine import block_text, infra_waits, team_turns
from .fair import message_who, task_who, waiting_by_person
from .store import Store
from .visibility import Gate, memo_visible, sees_scoped

SHOWN = 30


def _title(text: str) -> str:
    lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
    first = lines[0] if lines else ""
    return first[:79] + "…" if len(first) > 80 else first


def _session(key: str) -> int | None:
    head, sep, n = key.rpartition("#")
    return int(n) if sep and n.isdigit() else None


def person(cfg: Config, key: str) -> str | None:
    if not key:
        return None
    ln = cfg.all_lanes.get(key)
    if ln is not None:
        return ln.owner or None
    return key


def _entry(g: Gate | None, who: str | None, shown: bool | None = None, **fields: Any) -> dict[str, Any]:
    if shown if shown is not None else g is None or sees_scoped(g, who):
        return {**fields, "who": who}
    return {"id": fields["id"], "kind": fields["kind"], "session": fields.get("session"), "title": "", "who": None,
            "hidden": True}


def _named(g: Gate | None, counts: dict[str, int]) -> list[dict[str, Any]]:
    return [{"who": k or None, "n": n} for k, n in counts.items() if g is None or sees_scoped(g, k or None)]


def team_queue(cfg: Config, lid: str, live: list[dict[str, Any]], order: dict[int, Any] | None = None,
               g: Gate | None = None, waits: dict[str, str] | None = None) -> dict[str, Any]:
    keys = {lid, *(k for k, ln in cfg.instances.items() if ln.base == lid)}
    order = order or {}
    waits = waits or {}
    now = sorted((t for t in live if t["lane"] in keys and t["state"] in ("running", "wrapping")), key=lambda t: t["id"])
    wait = sorted((t for t in live if t["lane"] in keys and t["state"] == "queued"),
                  key=lambda t: order.get(t["id"], (2, 0, t["id"])))

    def item(t: dict[str, Any]) -> dict[str, Any]:
        return _entry(g, person(cfg, task_who(t)), id=t["id"], title=t["title"], kind="task", session=_session(t["lane"]),
                      why=waits.get(str(t["id"])))
    return {"now": [item(t) for t in now], "wait": [item(t) for t in wait[:SHOWN]], "waiting": len(wait),
            "by_person": _named(g, waiting_by_person(wait, lambda t: person(cfg, task_who(t)) or ""))}


def infra_queue(cfg: Config, s: Store, g: Gate | None = None) -> dict[str, Any]:
    sessions = cfg.infra_sessions()
    held = s.held_messages(sessions)

    def who(m: dict[str, Any]) -> str | None:
        return person(cfg, message_who(m))

    def shown(m: dict[str, Any]) -> bool:
        return g is None or memo_visible(g, m)
    now = [_entry(g, who(m), shown(m), id=m["id"], title=_title(m["text"]), kind=m["kind"], session=_session(k) or 1)
           for k, m in sorted(held.items(), key=lambda kv: _session(kv[0]) or 1)]
    lang = cfg.lang if cfg.lang in ("en", "ko", "ja", "zh", "es") else "en"
    queue = s.inbox_queue(INFRA, cfg.fair_turns)
    parallel = len(sessions) > 1
    wait = [_entry(g, who(m), shown(m), id=m["id"], title=_title(m["text"]), kind=m["kind"],
                   why=block_text(b[0], b[1], lang, len(sessions)) if b and parallel else None)
            for m, b in infra_waits(s, sessions, time.time(), cfg.fair_turns, limit=SHOWN)]
    return {"now": now, "wait": wait, "waiting": len(queue),
            "by_person": _named(g, waiting_by_person(queue, lambda m: who(m) or ""))}


def queues_view(cfg: Config, s: Store, live: list[dict[str, Any]], g: Gate | None = None,
                waits: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    team = {k for k, ln in {**cfg.lanes, **cfg.instances}.items() if ln.kind == "team"}
    order = team_turns(s, team, cfg.fair_turns)
    for lid, ln in cfg.lanes.items():
        if ln.kind == "team":
            out[lid] = team_queue(cfg, lid, live, order, g, waits)
        elif ln.kind == INFRA:
            out[lid] = infra_queue(cfg, s, g)
    return out
