from __future__ import annotations

from typing import Any

from .config import HUB, INFRA, Config
from .engine import clip
from .store import HUMAN, TOWER, Store, StoreError

EVERYONE = ("all", "-")


def member_name(s: Store, who: str) -> str:
    if "@" in who:
        m = s.member_by_ident(who)
        if m:
            return str(m["name"])
    return who


def member_or_error(s: Store, name: str) -> str:
    name = member_name(s, name)
    if name not in {m["name"] for m in s.members()}:
        raise StoreError(f"no member {name} (`dah member list`; `--to all` shows it to everyone)")
    return name


def person_of_memo(c: Config, s: Store, memo_id: int) -> str:
    sender = str(s.memo(memo_id)["sender"])
    if sender in c.all_lanes or sender in (TOWER, HUMAN):
        raise StoreError(f"memo #{memo_id} came from {sender}, not a person")
    return member_name(s, sender)


def requester_for(c: Config, s: Store, me: str, request: int | None = None, person: str | None = None) -> str | None:
    if person:
        return None if person in EVERYONE else member_or_error(s, person)
    if request is not None:
        return person_of_memo(c, s, request)
    ln = c.person_hubs.get(me)
    return ln.owner if ln and ln.owner else None


def card_audience(c: Config, s: Store, to: str | None, task: dict[str, Any] | None, me: str,
                  request: int | None) -> str | None:
    if to:
        return None if to in EVERYONE else member_or_error(s, to)
    if not c.requester:
        return None
    if task:
        return s.requester_of_task(task["id"])
    if c.is_hub(me):
        return requester_for(c, s, me, request)
    if me == INFRA:
        return infra_requester(c, s)
    return None


def infra_requester(c: Config, s: Store) -> str | None:
    mid = s.lane(INFRA).get("active_message")
    if mid is None:
        return None
    try:
        msg = s.message(int(mid))
    except StoreError:
        return None
    if msg["kind"] == "decision" and msg.get("item_id"):
        try:
            return s.item(int(msg["item_id"])).get("assignee")
        except StoreError:
            return None
    ln = c.person_hubs.get(str(msg["sender"]))
    return ln.owner if ln and ln.owner else None


def escalation_audience(s: Store, item: dict[str, Any], to: str | None, requester_on: bool) -> str | None:
    if to:
        return None if to in EVERYONE else member_or_error(s, to)
    if not requester_on:
        return None
    return item.get("assignee") or s.requester_of_task(item.get("task_id"))


def visible_to_requester(item: dict[str, Any], viewer: str | None, local: bool) -> bool:
    assignee = item.get("assignee")
    if not assignee or local or not viewer:
        return True
    return assignee == viewer


def infra_reply_text(memo_id: int, text: str) -> str:
    return f"Infra answered memo #{memo_id}: {clip(text, 1500)}"


def infra_wrapped_text(s: Store, msg: dict[str, Any], text: str) -> str:
    what = f"memo #{msg['id']}" if msg["kind"] == "memo" else f"{msg['kind']} message #{msg['id']}" + (
        f" (item #{msg['item_id']})" if msg.get("item_id") else "")
    return f"Wrapped: infra {what}: {clip(text, 700)}"

