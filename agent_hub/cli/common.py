from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .. import config as config_mod
from ..config import HUB, Config, base_lane
from ..engine import VERDICT_WORD
from ..install import edit_config
from ..store import Store


class TowerError(Exception):
    pass


def cfg() -> Config:
    return config_mod.load()


def edit_tower_json(home: Path, fn: Callable[[dict[str, Any]], None]) -> bool:
    try:
        return edit_config(home, fn)
    except config_mod.ConfigError as e:
        raise TowerError(str(e))


def store(c: Config | None = None) -> Store:
    return Store((c or cfg()).db)


def who(args: argparse.Namespace, c: Config) -> str:
    asked, env = getattr(args, "as_", None), os.environ.get("TOWER_LANE")
    me = asked or env
    if asked and env and asked != env:
        if asked != base_lane(env):
            raise TowerError(f"this session is {env}; --as {asked} would write as another lane. Run dah without --as.")
        me = env
        print(f"(this session is {env}: --as {asked} ignored, writing as {env})", file=sys.stderr)
    if not me:
        raise TowerError("Unknown caller. Sessions the tower opens have TOWER_LANE; by hand, add --as LANE.")
    if me not in c.all_lanes:
        raise TowerError(f"unknown lane {me} (lanes: {', '.join(c.lanes)})")
    return me


def need(args: argparse.Namespace, c: Config, *lanes: str) -> str:
    me = who(args, c)
    if me not in lanes and not (HUB in lanes and c.is_hub(me)):
        raise TowerError(f"only {' / '.join(lanes)} use this command")
    return me


def need_team(c: Config, lane: str) -> None:
    if lane not in c.team():
        raise TowerError(f"{lane} is not a team lane (team: {', '.join(c.team()) or 'none yet; infra adds lanes'})")


def check_paths(c: Config, paths: list[str]) -> list[str]:
    root = c.workdir.resolve()
    out = []
    for p in paths:
        full = Path(p).resolve() if Path(p).is_absolute() else (root / p).resolve()
        if root not in full.parents or not full.is_file():
            raise TowerError(f"attachments must be files inside the project ({root}): {p}")
        out.append(str(full.relative_to(root)))
    return out


def body_of(args: argparse.Namespace) -> str | None:
    if getattr(args, "body_file", None):
        return Path(args.body_file).read_text(encoding="utf-8")
    return getattr(args, "body", None)


def parse_options(raw: list[str]) -> list[dict[str, Any]]:
    out = []
    for o in raw:
        label, _, desc = o.partition("=")
        if not label.strip():
            raise TowerError(f"empty option label: {o}")
        out.append({"label": label.strip(), "desc": desc.strip()})
    return out


def hhmm(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else "-"


def verdict_text(it: dict[str, Any]) -> str:
    if it["state"] == "open":
        return "open"
    return VERDICT_WORD.get(it["verdict"], it["verdict"]) + (f" ({it['choice']})" if it.get("choice") else "")


REACH_HINT = ("The board only opens on this machine. To open it from a phone: `dah guide lan` (same Wi-Fi), "
              "`dah guide tailscale`, `dah guide cloudflare` (your domain) or `dah guide aws`; or ask infra on the "
              "board to set one up.")
