from __future__ import annotations

import argparse

from .. import proccheck, processes
from ..config import HUB, base_lane
from ..store import StoreError
from .common import TowerError, body_of, cfg, check_paths, need, store, who


def cmd_check(args: argparse.Namespace) -> None:
    c = cfg()
    if not c.process_verification:
        raise TowerError("process_verification is off in tower.json: there are no check cards")
    s, me = store(c), who(args, c)
    if base_lane(me) not in c.team():
        raise TowerError("only a team lane posts a check card, for the task it is running")
    task = s.live_task(me)
    if not task:
        raise TowerError(f"{me} has no running task")
    body = body_of(args) or ""
    if not body.strip():
        raise TowerError("--body or --body-file is required: what this sub-process made, how it was checked, what "
                         "the person should look at")
    atts = check_paths(c, args.attach)
    if not atts:
        raise TowerError("attach the sub-process output (--attach PATH, a preview for a large file) so the person can "
                         "judge it on the card")
    try:
        iid, revised = proccheck.post(s, c.home, c.lang, me, task, args.title, body, atts)
    except (proccheck.CheckError, StoreError) as e:
        raise TowerError(str(e))
    s.signal(task["id"])
    s.push(s.hub_for_task(task["id"]), me, "item", f"Check card #{iid} from {me} task #{task['id']} \"{args.title}\""
           + (" revised" if revised else "") + f" → `dah show {iid}`", task["id"], iid)
    asker = s.requester_of_task(task["id"])
    print(f"check card #{iid} " + ("revised" if revised else "posted") + f" for {asker or 'everyone'}. Now `dah report "
          f"done`; the next sub-process of this run starts only after the person picks "
          f"\"{proccheck.labels(c.lang)[proccheck.NEXT][0]}\".")


def cmd_process(args: argparse.Namespace) -> None:
    c = cfg()
    if args.sub == "verify":
        need(args, c, HUB)
        try:
            rec = proccheck.verify(store(c), c.home, args.full, args.card)
        except (proccheck.CheckError, StoreError) as e:
            raise TowerError(str(e))
        print(f"{args.full} is verified (card #{rec['card']} by {rec['by']}, run {rec['run']}, commit "
              f"{rec['commit'] or '-'}, {len(rec['files'])} cards fingerprinted). Commit {proccheck.VERIFIED_FILE}; "
              f"from now on its sub-processes run through and only the last one posts a check card.")
        return
    if args.sub == "check":
        me = need(args, c, HUB)
        try:
            print(proccheck.Checks(c, store(c), lambda _: None).settle(args.card, args.how, me))
        except (proccheck.CheckError, StoreError) as e:
            raise TowerError(str(e))
        return
    lib = processes.load(c.home)
    s = store(c)
    ids = [args.full] if args.full else list(lib["full"])
    for fid in ids:
        f = lib["full"].get(fid)
        if not f:
            raise TowerError(f"full process `{fid}` is not in the library")
        line = f"{fid}: {f['status']}"
        if f.get("verified"):
            v = f["verified"]
            line += f" (verified by card #{v.get('card')} on {v.get('at')}, run {v.get('run')})"
        if f.get("reverify"):
            line += " · needs a new verification run: " + "; ".join(f["changes"][:5])
        run = proccheck.run_view(s, fid, [st["sub"] for st in f["steps"]])
        if run:
            ck = run["check"]
            line += (f"\n  latest run {run['key']}: on {run['sub']}"
                     + (f" · check card #{ck['item_id']} ({ck['sub']}) {ck['state']}" if ck else ""))
        print(line)
