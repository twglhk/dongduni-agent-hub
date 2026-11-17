from __future__ import annotations

import argparse
import os
from typing import Any

from .. import config as config_mod
from ..config import HUB, INFRA, RUNTIMES
from ..install import SHIM_PREFIX, lane_template, remove_shims, write_shims
from .common import TowerError, cfg, edit_tower_json, store


def cmd_lane(args: argparse.Namespace) -> None:
    c = cfg()
    if args.sub == "list":
        for lid, ln in c.lanes.items():
            print(f"{lid:<10} {ln.kind:<6} {ln.runtime:<8} {ln.label}" + (f" :{ln.port}" if ln.port else "")
                  + (f" [{ln.engine}]" if ln.engine else ""))
        return
    if args.id in (HUB, INFRA) and args.sub == "remove":
        raise TowerError("hub and infra are built in")
    if args.sub == "add":
        if args.id in c.lanes:
            raise TowerError(f"lane {args.id} exists")
        if args.runtime not in RUNTIMES and args.runtime not in config_mod.ext_names(c.home, "runtimes"):
            raise TowerError(f"--runtime: one of {', '.join(RUNTIMES)}, or one an extension in .tower/ext/ registers")
        entry: dict[str, Any] = {"runtime": args.runtime, "label": args.label or args.id}
        if args.worktree:
            entry["worktree"] = True
        if args.model:
            entry["model"] = args.model
        if args.agent:
            if args.runtime == "codex":
                raise TowerError("codex has no agents; set \"args\": [\"--profile\", NAME] in tower.json instead")
            entry["agent"] = args.agent
        if args.engine:
            if args.engine not in c.engines or c.engines[args.engine].implicit:
                raise TowerError(f"--engine: no engine {args.engine} (`dah engine list`; `dah engine add NAME`)")
            entry["engine"] = args.engine
        edit_tower_json(c.home, lambda d: d["lanes"].__setitem__(args.id, entry))
        c = config_mod.load(c.home)
        path = c.instructions(args.id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(lane_template(args.id), encoding="utf-8")
        wrote: list[str] = []
        write_shims(c, list(c.lanes), False, wrote)
        print(f"lane {args.id} added ({args.runtime}" + (f", agent {args.agent}" if args.agent else "")
              + (f", engine {args.engine}" if args.engine else "")
              + (", in its own git worktree" if args.worktree else "") + "). "
              f"Fill in {path.relative_to(c.home)}: what it owns and its done criteria. "
              f"Open it by hand with /{SHIM_PREFIX}{args.id}.")
        return
    if args.id not in c.lanes:
        raise TowerError(f"no lane {args.id}")
    if store(c).live_task(args.id):
        raise TowerError(f"{args.id} has a running task; wrap or cancel it first")
    edit_tower_json(c.home, lambda d: d["lanes"].pop(args.id))
    remove_shims(c, args.id)
    print(f"lane {args.id} removed (its instruction file is kept)")


def cmd_engine(args: argparse.Namespace) -> None:
    c = cfg()
    if args.sub == "list":
        for eid, e in c.engines.items():
            print(f"{eid:<12} lanes={len(e.lanes)}  {e.label}" + ("  (implicit)" if e.implicit else "")
                  + (f"  {', '.join(e.lanes)}" if e.lanes else ""))
        return
    if args.sub == "show":
        if args.id not in c.engines:
            raise TowerError(f"no engine {args.id} (`dah engine list`)")
        e = c.engines[args.id]
        print(f"{e.id}: {e.label}" + (" (implicit: every team lane without an engine)" if e.implicit else ""))
        for key in ("dir", "library", "skills", "map"):
            print(f"  {key + ':':<9} {getattr(e, key) or '-'}")
        print(f"  lanes:    {', '.join(e.lanes) or '-'}")
        if e.library:
            from .. import engines as engines_mod
            print("pipelines:")
            print(engines_mod.pipelines(engines_mod.build(c.home / e.library, engines_mod.skills_root(c.home, e.skills))))
        return
    if not args.id.replace("-", "").replace("_", "").isalnum():
        raise TowerError("engine id: use letters, digits, - and _")
    if args.id in c.engines and not c.engines[args.id].implicit:
        raise TowerError(f"engine {args.id} exists")
    if args.template:
        return scaffold_engine_cmd(c, args)
    entry = {k: v for k, v in (("label", args.label), ("dir", args.dir), ("library", args.library),
                               ("skills", args.skills), ("map", args.map), ("cap", args.cap)) if v}
    edit_tower_json(c.home, lambda d: d.setdefault("engines", {}).__setitem__(args.id, entry))
    print(f"engine {args.id} added. Put lanes in it with `dah lane add ID --runtime R --engine {args.id}`.")


def scaffold_engine_cmd(c: Any, args: argparse.Namespace) -> None:
    from ..install import scaffold_engine
    if args.runtime not in RUNTIMES and args.runtime not in config_mod.ext_names(c.home, "runtimes"):
        raise TowerError(f"--runtime: one of {', '.join(RUNTIMES)}, or one an extension in .tower/ext/ registers")
    try:
        made = scaffold_engine(c, args.id, args.template, args.label or args.id.replace("-", " ").title(), args.runtime,
                               args.skills or "", args.cap or "")
    except ValueError as e:
        raise TowerError(str(e))

    def add(d: dict[str, Any]) -> None:
        d.setdefault("engines", {})[args.id] = made["entry"]
        d["lanes"].update(made["lanes"])

    edit_tower_json(c.home, add)
    c2 = config_mod.load(c.home)
    for f, text in made["files"].items():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    write_shims(c2, list(c2.lanes), False, [])
    print(f"engine {args.id} added from {args.template}: {len(made['files'])} files, lanes {', '.join(made['lanes'])}. "
          f"Everything starts as draft: `dah engine show {args.id}`, then edit the cards in {made['entry']['skills']} "
          f"(`dah guide engines`).")


def cmd_member(args: argparse.Namespace) -> None:
    c = cfg()
    s = store(c)
    if args.sub == "list":
        for m in s.members():
            hub = c.hub_of(m["name"]) if c.hub_cap else ""
            print(f"{m['name']:<16} {m['role']:<7} {m['ident'] or '-':<30} {'token' if m['has_token'] else '':<6} {hub}".rstrip())
        return
    if args.sub == "remove":
        s.remove_member(args.name)
        print(f"removed {args.name}")
        if c.claude_accounts:
            from .. import claudelogin
            try:
                claudelogin.disconnect(args.name)
            except claudelogin.LoginError as e:
                print(f"{args.name}'s Claude sign-in folder was not removed ({e.why}): delete it from ~/.claude-accounts yourself")
    else:
        token = s.add_member(args.name, args.role, args.ident, args.token)
        print(f"member {args.name} ({args.role})" + (f" ident {args.ident}" if args.ident else ""))
        if token:
            print(f"token (shown once; give it privately): {token}")
    if c.hub_cap:
        os.utime(c.home / config_mod.TOWER_DIR / config_mod.CONFIG_NAME)
        c2 = config_mod.load(c.home)
        print("person hubs: " + (", ".join(c2.person_hubs) or "none")
              + (f" · in line for a hub of their own (cap {c2.hub_cap}): {', '.join(c2.hub_overflow)}"
                 if c2.hub_overflow else ""))
