from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import claudelogin
from .config import Config
from .store import Store

ACTIONS = ("status", "start", "code", "cancel", "disconnect")


def answer(cfg: Config, s: Store, me: dict[str, Any], body: dict[str, Any], log: Callable[[str], None],
           root: Path | None = None) -> tuple[int, dict[str, Any]]:
    if not cfg.claude_accounts:
        return 404, {"error": "not_enabled", "why": "not_enabled"}
    who = str(me["name"])
    members = {m["name"]: m for m in s.members()}
    if who not in members:
        return 403, {"error": "not_member", "why": "not_member"}
    act = str(body.get("action") or "")
    if act not in ACTIONS:
        return 400, {"error": "bad_action", "why": "bad_action"}
    try:
        if act == "status":
            out = claudelogin.status(who, root)
            if out.get("state") == "connected":
                s.mark_account_seen(who)
            elif out.get("state") == "none" and s.account_seen(who):
                out["ended"] = True
        elif act == "start":
            out = claudelogin.start(who, members[who].get("ident"), root)
            log(f"{who} started connecting their Claude account")
        elif act == "code":
            out = claudelogin.submit(who, str(body.get("code") or ""), root)
            s.mark_account_seen(who)
            log(f"{who} connected their Claude account")
        elif act == "cancel":
            out = claudelogin.cancel(who, root)
        else:
            s.mark_account_seen(who)
            out = {**claudelogin.disconnect(who, root), "ended": True}
            log(f"{who} disconnected their Claude account")
    except claudelogin.LoginError as e:
        if act == "code":
            log(f"{who} Claude account code refused: {e.why}")
        return e.code, {"error": e.why, "why": e.why}
    return 200, out
