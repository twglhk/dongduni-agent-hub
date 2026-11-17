from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Config, base_lane
from .store import INFRA, Store, StoreError

CREDENTIALS = ".credentials.json"


def root_dir(root: Path | None = None) -> Path:
    return root or Path.home() / ".claude-accounts"


def person(c: Config, s: Store, who: Any) -> str | None:
    if not who:
        return None
    who = str(who)
    ln = c.person_hubs.get(who)
    if ln is not None:
        return ln.owner or None
    if "@" in who:
        m = s.member_by_ident(who)
        return str(m["name"]) if m else None
    return who if who in {m["name"] for m in s.members()} else None


def for_task(c: Config, s: Store, task: dict[str, Any]) -> str | None:
    return person(c, s, task.get("requester")) or person(c, s, task.get("hub"))


def for_message(c: Config, s: Store, msg: dict[str, Any]) -> str | None:
    if msg.get("kind") == "decision" and msg.get("item_id"):
        try:
            it = s.item(int(msg["item_id"]))
        except StoreError:
            return None
        return person(c, s, it.get("assignee")) or person(c, s, it.get("decided_by"))
    return person(c, s, msg.get("sender"))


def for_session(c: Config, s: Store, lane: str, task: dict[str, Any] | None = None,
                msg: dict[str, Any] | None = None) -> str | None:
    if c.is_hub(lane):
        return person(c, s, lane)
    if base_lane(lane) == INFRA:
        return for_message(c, s, msg) if msg else None
    return for_task(c, s, task) if task else None


def config_dir(member: str | None, root: Path | None = None) -> Path | None:
    if not member:
        return None
    d = root_dir(root) / member
    return d if (d / CREDENTIALS).is_file() else None
