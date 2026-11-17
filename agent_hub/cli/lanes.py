from __future__ import annotations

import argparse
import time

from .. import stats
from ..config import HUB, INFRA, Config
from ..engine import clip
from ..requester import card_audience, infra_requester, infra_wrapped_text
from ..store import HUMAN, Store, StoreError
from .common import TowerError, body_of, cfg, check_paths, need, parse_options, recommend, store, verdict_text, who


def cmd_mine(args: argparse.Namespace) -> None:
    c = cfg()
    s, me = store(c), who(args, c)
    task = s.live_task(me)
    if not task:
        print("No task right now.")
        return
    print(f"task #{task['id']} \"{task['title']}\" ({task['state']})\n{task['brief']}\n")
    for it in s.items(lane=me):
        if it["task_id"] == task["id"]:
            print(f"#{it['id']} [{verdict_text(it)}] {it['title']}" + (f" — {it['note']}" if it.get("note") else ""))


def cmd_item_add(args: argparse.Namespace) -> None:
    c = cfg()
    s, me = store(c), who(args, c)
    body = body_of(args) or ""
    if not body.strip():
        raise TowerError("--body or --body-file is required (markdown with ## sections, see PROTOCOL.md)")
    atts = check_paths(c, args.attach)
    options = recommend(parse_options(args.option), args.recommend)
    kind = args.kind or ("decision" if options else "review")
    task = s.live_task(me) if me in c.team() else None
    audience = HUB if me in c.team() else HUMAN  # hub and infra items go straight to humans
    try:
        to = card_audience(c, s, args.to, task, me, args.request)
        iid = s.add_item(me, task["id"] if task else None, kind, args.title, body, options, atts, audience, args.multi,
                         assignee=to)
    except StoreError as e:
        raise TowerError(str(e))
    if audience == HUMAN:
        print(f"#{iid} posted for humans (push notification)" + (f", shown to {to} only" if to else "")
              + f". Don't wait: finish your scope; the decision comes back later (`dah show {iid}`).")
        return
    s.push(s.hub_for_task(task["id"] if task else None), me, "item", f"Item #{iid} from {me}" + (f" task #{task['id']}" if task else "")
           + f" \"{args.title}\" → `dah show {iid}`", task["id"] if task else None, iid)
    s.signal(task["id"] if task else None)
    print(f"#{iid} posted; the hub sees it first. Don't wait: finish the unblocked scope and list #{iid} in "
          f"`dah report done`.")


def cmd_item_update(args: argparse.Namespace) -> None:
    c = cfg()
    s, me = store(c), who(args, c)
    it = s.item(args.id)
    if me != it["lane"] and not c.is_hub(me):
        raise TowerError(f"#{args.id} belongs to {it['lane']}")
    if it.get("question"):
        raise TowerError(f"#{args.id} is a live question")
    body = body_of(args)
    atts = check_paths(c, args.attach) if args.attach else None
    if body is None and atts is None and not args.title and not args.option and args.recommend is None:
        raise TowerError("nothing to change (--title, --body/--body-file, --option, --recommend, --attach)")
    options = parse_options(args.option) if args.option else None
    if args.recommend is not None:
        options = recommend(options if options is not None else it["options"], args.recommend)
    try:
        s.revise_item(args.id, args.title, body, options, atts)
    except StoreError as e:
        raise TowerError(str(e))
    if me in c.team():
        task = s.live_task(me)
        s.push(s.hub_for_task(task["id"] if task else None), me, "item", f"Item #{args.id} updated by {me} → `dah show {args.id}`",
               task["id"] if task else None, args.id)
    print(f"#{args.id} updated.")


def cmd_item_merge(args: argparse.Namespace) -> None:
    c = cfg()
    me = need(args, c, HUB)
    try:
        store(c).merge_item(args.id, args.into, me)
    except StoreError as e:
        raise TowerError(str(e))
    print(f"#{args.id} merged into #{args.into}.")


def cmd_report(args: argparse.Namespace) -> None:
    c = cfg()
    s, me = store(c), who(args, c)
    if c.is_hub(me):
        if args.kind != "handoff":
            raise TowerError("the hub does not report; it decides, tells and wraps (only `report handoff` when the "
                             "tower asks it to hand over to a fresh hub session)")
        s.set_lane(me, handoff=args.text, handoff_session=s.lane(me).get("session") or "")
        print("Handoff saved. End your turn; the tower opens a fresh hub session with this handoff.")
        return
    if me == INFRA and args.kind != "wrapped":
        raise TowerError("infra only uses `report wrapped`; answer memos with `dah memo reply`")
    task = s.live_task(me)
    tid = task["id"] if task else None
    atts = check_paths(c, args.attach)
    if args.kind == "handoff" and not task:
        raise TowerError("no task to hand off")
    if args.kind == "done" and task and c.done_gate:
        from .. import donegate
        if donegate.applies(c, task, me):  # held until the gate passes; the tower delivers the verdict
            try:
                print(donegate.start(c, task, me, args.text, atts))
            except (RuntimeError, ValueError) as e:
                raise TowerError(f"done not sent: {e}")
            s.signal(tid)
            return
    text, retro = stats.split_retro(args.text) if args.kind == "wrapped" else (args.text, None)
    s.add_report(tid, me, args.kind, text, atts)
    if retro:
        s.add_retro(tid, me, text, retro["cost"], retro["corrected"], retro["rule"], retro["raw"])
    s.signal(tid)
    tag = me + (f" task #{tid} \"{task['title']}\"" if task else "")
    if task and c.branch_of(me) and args.kind in ("done", "wrapped"):
        tag += f" (branch {c.branch_of(me)})"
    if args.kind == "handoff" and task:
        s.update_task(task["id"], handoff=args.text, rotate_ready=time.time())
        print("Handoff saved. End your turn; the tower continues in a fresh session.")
        return
    if args.kind == "wrapped":
        if task:
            s.update_task(task["id"], state="closed", closed=time.time())
        if me == INFRA:
            infra_wrapped(c, s, text)
        else:
            s.push(s.hub_for_task(tid), me, "wrapped", f"Wrapped: {tag}", tid)
        s.set_lane(me, closing=time.time())
        print("Wrapped. End your turn; the tower closes this session.")
        return
    att = f" (files: {', '.join(atts)})" if atts else ""
    s.push(s.hub_for_task(tid), me, args.kind, f"{args.kind.capitalize()}: {tag}: {clip(args.text, 700)}{att}", tid)
    print(f"Sent to the hub." + (" Review comes back to this session; you may end your turn." if args.kind == "done" else ""))


def infra_wrapped(c: Config, s: Store, text: str) -> None:
    if not c.requester:
        return
    mid = s.lane(INFRA).get("active_message")
    if mid is None:
        return
    try:
        msg = s.message(int(mid))
    except StoreError:
        return
    sender = str(msg["sender"])
    if msg["kind"] == "memo" and c.is_hub(sender) and s.replied(int(mid), INFRA):
        return
    asker = infra_requester(c, s)
    hub = sender if c.is_hub(sender) else c.hub_of(asker) if asker else HUB
    s.push(hub, INFRA, "wrapped", infra_wrapped_text(s, msg, text), None, msg.get("item_id"))
