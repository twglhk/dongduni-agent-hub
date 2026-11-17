from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import Config, base_lane
from .store import INFRA, Store, StoreError

CREDENTIALS = ".credentials.json"
_PLAIN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_SLUG = re.compile(r"[^a-z0-9]+")
CONNECTED, ENDED, NONE = "connected", "ended", "none"
PROBE_TTL = 300.0
SIGN_IN_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


def root_dir(root: Path | None = None) -> Path:
    return root or Path.home() / ".claude-accounts"


def folder_name(member: str) -> str:
    if _PLAIN.fullmatch(member) and ".." not in member:
        return member
    slug = _SLUG.sub("-", member.lower()).strip("-")[:32] or "u"
    return f"{slug}-{hashlib.sha1(member.encode('utf-8')).hexdigest()[:8]}"


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
    d = root_dir(root) / folder_name(member)
    return d if (d / CREDENTIALS).is_file() else None


Probe = Callable[[Path], "bool | None"]
_probed: dict[str, tuple[int, float, bool | None]] = {}


def cli_probe(d: Path) -> bool | None:
    from . import claudelogin
    try:
        j = claudelogin.status_json(d, d.parent)
    except claudelogin.LoginError:
        return None
    return bool(j["loggedIn"]) if "loggedIn" in j else None


def _signed_in(d: Path, probe: Probe, now: float) -> bool | None:
    try:
        mtime = (d / CREDENTIALS).stat().st_mtime_ns
    except OSError:
        return False
    hit = _probed.get(str(d))
    if hit and hit[0] == mtime and now - hit[1] < PROBE_TTL:
        return hit[2]
    got = probe(d)
    _probed[str(d)] = (mtime, now, got)
    return got


def state(member: str | None, s: Store, root: Path | None = None, probe: Probe | None = None,
          now: float | None = None) -> str:
    if not member:
        return NONE
    d = root_dir(root) / folder_name(member)
    if (d / CREDENTIALS).is_file() and _signed_in(d, probe or cli_probe, time.time() if now is None else now) is not False:
        s.mark_account_seen(member)
        return CONNECTED
    return ENDED if s.account_seen(member) else NONE


@dataclass(frozen=True)
class Launch:
    member: str | None = None
    config_dir: Path | None = None
    held: bool = False

    @property
    def owner(self) -> str | None:
        return self.member if self.config_dir else None


def launch_for(c: Config, s: Store, lane: str, task: dict[str, Any] | None = None, msg: dict[str, Any] | None = None,
               root: Path | None = None, probe: Probe | None = None) -> Launch:
    if not c.claude_accounts:
        return Launch()
    ln = c.all_lanes.get(lane) or c.all_lanes.get(base_lane(lane))
    if ln is None or ln.runtime != "claude":
        return Launch()
    member = for_session(c, s, lane, task, msg)
    st = state(member, s, root, probe)
    if st == CONNECTED:
        return Launch(member, root_dir(root) / folder_name(str(member)))
    if st == ENDED:
        return Launch(member, None, True)
    return Launch()


def session_env(config_dir: Path | str | None, base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    if not config_dir:
        return env
    for k in SIGN_IN_VARS:
        env.pop(k, None)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return env


def overview(s: Store, root: Path | None = None) -> list[dict[str, str]]:
    out = []
    for m in s.members():
        name = str(m["name"])
        if config_dir(name, root):
            state = "connected"
        else:
            state = "ended" if s.account_seen(name) else "none"
        out.append({"member": name, "state": state})
    return out
