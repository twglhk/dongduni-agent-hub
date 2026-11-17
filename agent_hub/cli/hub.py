from __future__ import annotations

import argparse
import sys
import time

from .. import stagemodel
from ..config import HUB, INFRA, Config
from ..engine import clip
from ..store import Store, StoreError
from .common import TowerError, cfg, need, need_team, store, verdict_text, who


def cmd_task(args: argparse.Namespace) -> None:
    c = cfg()
    need(args, c, HUB)
    need_team(c, args.lane)
    from ..stagemodel import missing_lines, problems, resolve, table_for
    if gap := missing_lines(args.brief):
        if c.require_model_lines:
            raise TowerError("the brief needs these lines (one per line): " + " · ".join(gap))
        if resolve(table_for(c, args.lane), args.brief) != (None, None) and len(gap) == 2:
            gap = []  # a `Line:` header that names a library row sets both
        if gap:
            print("warning: the brief has no " + " and no ".join(g.split(":")[0] for g in gap) + " line; the lane's own default runs. "
                  "Pick one on purpose (`dah guide models`).", file=sys.stderr)
    m, e = resolve(table_for(c, args.lane), args.brief)
    for bad in problems(m, e):
        print(f"warning: {bad}", file=sys.stderr)
    from .. import engines as engines_mod
    if c.require_line:
        eng = c.engines.get(c.lanes[args.lane].engine)
        if eng and eng.library:
            try:
                engines_mod.gate(args.brief, engines_mod.build(c.home / eng.library, engines_mod.skills_root(c.home, eng.skills)))
            except ValueError as e:
                raise TowerError(str(e))
    s = store(c)
    if args.goal is not None:
        print("notice: goals were removed in 0.8.0; --goal is ignored (send the work as requests and tasks)")
    try:
        tid = s.add_task(args.lane, args.title, args.brief, c.lanes[args.lane].engine, args.deliverable)
    except StoreError as e:
        raise TowerError(str(e))
    s.update_task(tid, **engines_mod.line_fields(args.brief))
    live = s.live_task(args.lane)
    print(f"task #{tid} → {args.lane}"
          + (f" (after task #{live['id']} wraps)" if live else ""))


def cmd_stage(args: argparse.Namespace) -> None:
    c = cfg()
    me = who(args, c)
    task = store(c).live_task(me)
    if not task:
        raise TowerError(f"{me} has no running task")
    store(c).update_task(task["id"], stage_now=clip(args.stage, 40))
    print(f"task #{task['id']} is on stage {args.stage}")


def cmd_doing(args: argparse.Namespace) -> None:
    c = cfg()
    need(args, c, HUB)
    store(c).set_lane(HUB, doing=clip(args.text, 80), doing_at=time.time())
    print("Shown on the board.")


def cmd_scratch(args: argparse.Namespace) -> None:
    """A scratch folder (or a detached worktree of your HEAD) outside the project, removed after your task closes."""
    from .. import scratch
    c = cfg()
    me = who(args, c)
    task = store(c).live_task(me)
    if not task:
        raise TowerError("no live task: scratch belongs to a task and is swept after it closes")
    try:
        print(scratch.make(c, task["id"], c.lane_dir(me), worktree=args.worktree))
    except (RuntimeError, ValueError, OSError) as e:
        raise TowerError(str(e))


def cmd_tell(args: argparse.Namespace) -> None:
    c = cfg()
    need(args, c, HUB)
    if getattr(args, "urgent", False):
        from ..hooks import urgent_tell
        try:
            print(urgent_tell(c, args.lane, args.text))
        except ValueError as e:
            raise TowerError(str(e))
        return
    need_team(c, args.lane)
    s = store(c)
    task = s.live_task(args.lane)
    if not task:
        raise TowerError(f"{args.lane} has no running task; use `dah task`")
    s.push(args.lane, HUB, "tell", f"From the hub: {args.text}", task["id"])
    print(f"→ {args.lane} task #{task['id']} (delivered when idle)")


def cmd_wrap(args: argparse.Namespace) -> None:
    c = cfg()
    need(args, c, HUB)
    need_team(c, args.lane)
    s = store(c)
    task = s.live_task(args.lane)
    if not task:
        raise TowerError(f"{args.lane} has no running task")
    s.update_task(task["id"], state="wrapping")
    s.push(args.lane, HUB, "wrap", f"Wrap up task #{task['id']}: remove temp files, save what the next session needs, "
           f"then `dah report wrapped \"one line\"`. {args.note or ''}".strip(), task["id"])
    print(f"→ {args.lane} wrap requested")


def cmd_decide(args: argparse.Namespace) -> None:
    c = cfg()
    need(args, c, HUB)
    try:
        it = store(c).decide(args.id, args.verdict, HUB, args.choice, args.note or "", args.basis or "")
    except StoreError as e:
        raise TowerError(str(e))
    print(f"#{it['id']} {verdict_text(it)}; the tower routes it.")


def cmd_escalate(args: argparse.Namespace) -> None:
    c = cfg()
    need(args, c, HUB)
    s = store(c)
    if args.to and args.to not in {m["name"] for m in s.members()}:
        raise TowerError(f"no member {args.to} (`dah member list`)")
    try:
        s.escalate(args.id, args.why, args.to)
    except StoreError as e:
        raise TowerError(str(e))
    print(f"#{args.id} → humans" + (f" ({args.to})" if args.to else "") + ". Carry on with other work.")


def cmd_cancel(args: argparse.Namespace) -> None:
    c = cfg()
    need(args, c, HUB)
    s = store(c)
    task = s.task(args.task)
    if task["state"] in ("closed", "cancelled"):
        raise TowerError(f"task #{args.task} is already {task['state']}")
    s.update_task(args.task, state="cancelled", closed=time.time())
    if task["state"] in ("running", "wrapping"):
        s.set_lane(task["lane"], closing=time.time())
    print(f"task #{args.task} cancelled")


def cmd_memo(args: argparse.Namespace) -> None:
    c = cfg()
    me = need(args, c, HUB, INFRA)
    s = store(c)
    try:
        if args.sub == "reply":
            card = s.reply_memo(args.id, args.text, me, set(c.all_lanes))
            print(f"Answered #{args.id}; posted as review card #{card} for humans to confirm." if card
                  else f"Answered #{args.id}; it shows on the board.")
        elif args.sub == "forward":
            if me != INFRA:
                raise TowerError("only infra forwards memos")
            memo = s.memo(args.id)
            s.push(HUB, memo["sender"], "memo", f"{memo['text']} (forwarded by infra from memo #{args.id})")
            s.reply_memo(args.id, "Passed to the hub.", INFRA)
            print(f"memo #{args.id} → hub")
        else:
            send_infra(c, s, me, args.text, args.approved)
    except StoreError as e:
        raise TowerError(str(e))


def send_infra(c: Config, s: Store, me: str, text: str, approved: str | None, model: str | None = None,
               effort: str | None = None) -> None:
    if me != HUB or INFRA not in c.lanes:
        raise TowerError("only the hub sends requests to infra; humans ask the hub")
    if bad := stagemodel.problems(model, effort):
        raise TowerError("; ".join(bad))
    head = [f"{k}: {v.strip()}" for k, v in (("model", model), ("effort", effort and effort.lower())) if v and v.strip()]
    if head:
        text = "\n".join(head) + "\n\n" + text
    if approved and approved.strip():
        text = f"{text}\n\nApproved by the human: {approved.strip()}"
    print(f"memo #{s.push(INFRA, HUB, 'memo', text)} → infra")


def cmd_infra(args: argparse.Namespace) -> None:
    c = cfg()
    try:
        send_infra(c, store(c), need(args, c, HUB, INFRA), args.text, args.approved, args.model, args.effort)
    except StoreError as e:
        raise TowerError(str(e))
