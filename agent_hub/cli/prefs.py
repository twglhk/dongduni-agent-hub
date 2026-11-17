from __future__ import annotations

import argparse
import getpass
import json

from .. import prefs as prefs_mod
from ..config import HUB, Config
from ..requester import member_or_error
from ..store import StoreError
from .common import TowerError, cfg, need, store


def _target(args: argparse.Namespace) -> tuple[Config, str]:
    c = cfg()
    me = need(args, c, HUB)
    try:
        name = args.name if c.auth_mode == "local" and args.name == getpass.getuser() else member_or_error(store(c), args.name)
    except StoreError as e:
        raise TowerError(str(e)) from e
    if c.hub_of(name) != me and name != getpass.getuser():
        raise TowerError(f"{name}'s requests go to {c.hub_of(name)}; only that hub changes {name}'s board settings")
    return c, name


def cmd_prefs(args: argparse.Namespace) -> None:
    c, name = _target(args)
    s = store(c)
    try:
        if args.sub == "reset":
            prefs_mod.reset(s, name)
            print(f"{name}: board settings back to the defaults")
        elif args.sub == "set":
            saved, dropped = prefs_mod.save(s, name, prefs_mod.parse_pairs(args.pairs))
            if dropped:
                print("dropped: " + ", ".join(dropped))
            print(f"{name} saved: {json.dumps(saved, ensure_ascii=False)}")
        else:
            v = prefs_mod.view(s, name)
            print(f"{name} saved: {json.dumps(v['saved'], ensure_ascii=False)}")
            print(f"in effect: {json.dumps(v['prefs'], ensure_ascii=False)}")
            ch = v["choices"]
            print(f"choices: first_tab {'|'.join(ch['first_tab'])} · font {'|'.join(ch['font'])} (small, normal, large)"
                  f" · accent {'|'.join(ch['accent'])} · hub sections {','.join(ch['sections']['hub'])}"
                  f" · lanes sections {','.join(ch['sections']['lanes'])} (order, collapsed, hidden)")
    except prefs_mod.PrefsError as e:
        raise TowerError(str(e)) from e
    if args.sub != "show":
        print("The board picks it up within 30 seconds, or at once when the page is opened again.")
