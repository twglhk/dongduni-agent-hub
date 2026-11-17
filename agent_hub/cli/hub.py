from __future__ import annotations

import argparse
import sys
import time

from .. import stagemodel
from ..config import HUB, INFRA, Config, base_lane, is_infra
from ..engine import clip
from ..requester import (cancelled_text, covered_text, escalation_audience, infra_reply_text, member_name,
                         requester_for)
from ..store import Store, StoreError
from .common import TowerError, cfg, need, need_team, store, verdict_text, who


def cmd_task(args: argparse.Namespace) -> None:
    c = cfg()
    me = need(args, c, HUB)
    need_team(c, args.lane)
    from ..stagemodel import library_for, missing_lines, problems, resolve, table_for
    if gap := missing_lines(args.brief):
        if c.require_model_lines:
            raise TowerError("the brief needs these lines (one per line): " + " · ".join(gap))
        if resolve(table_for(c, args.lane), args.brief, library_for(c)) != (None, None) and len(gap) == 2:
            gap = []  # a `Line:` header that names a library row sets both
        if gap:
            print("warning: the brief has no " + " and no ".join(g.split(":")[0] for g in gap) + " line; the lane's own default runs. "
                  "Pick one on purpose (`dah guide models`).", file=sys.stderr)
    m, e = resolve(table_for(c, args.lane), args.brief, library_for(c))
    for bad in problems(m, e):
        print(f"warning: {bad}", file=sys.stderr)
    from .. import engines as engines_mod, processes
    if c.require_line:
        _gate_line(c, args.lane, args.brief)
    s = store(c)
    if args.goal is not None:
        print("notice: goals were removed in 0.8.0; --goal is ignored (send the work as requests and tasks)")
    try:
        asker = requester_for(c, s, me, args.request, args.for_)
        hub = c.hub_of(asker) if asker and me == HUB else me
        tid = s.add_task(args.lane, args.title, args.brief, c.lanes[args.lane].engine, args.deliverable,
                         requester=asker, hub=hub)
    except StoreError as e:
        raise TowerError(str(e))
    s.update_task(tid, **engines_mod.line_fields(args.brief, processes.lane_full(c, args.lane)))
    live = s.live_task(args.lane)
    print(f"task #{tid} → {args.lane}"
          + (f" (after task #{live['id']} wraps)" if live else "")
          + (f" · for {asker}" + (f", news to {hub}" if hub != me else "") if asker else ""))


def cmd_task_bump(args: argparse.Namespace) -> None:
    c = cfg()
    need(args, c, HUB)
    try:
        task = store(c).bump_task(args.id)
    except StoreError as e:
        raise TowerError(str(e))
    print(f"task #{args.id} is first in the queue: the next free session of {base_lane(task['lane'])} takes it")


def _gate_line(c: Config, lane: str, brief: str) -> None:
    from .. import engines as engines_mod, line, processes
    legacy = processes.lane_full(c, lane)
    try:
        role = c.instructions(lane).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        role = ""
    uses = processes.lane_uses(role)
    if (c.home / processes.DIR).is_dir() and (uses is not None or legacy is not None):
        try:
            p = line.gate(line.translate_legacy(brief, legacy), processes.load(c.home))
        except ValueError as e:
            raise TowerError(str(e))
        if line.old_row(brief):
            print(f"notice: `#{line.old_row(brief)}` is an old pattern-library row; it was read as `{p['full']} / {p['sub']}`. "
                  f"Write the process library form from now on.", file=sys.stderr)
        if uses is not None and p["full"] and not p["full_new"] and p["full"] not in uses:
            print(f"warning: {lane}'s lane file does not list `{p['full']}` under `## Processes used` "
                  f"(it lists: {', '.join(uses) or 'none'}); add it there if this lane now runs it", file=sys.stderr)
        return
    eng = c.engines.get(c.lanes[lane].engine)
    if eng and eng.library:
        try:
            engines_mod.gate(brief, engines_mod.build(c.home / eng.library, engines_mod.skills_root(c.home, eng.skills)))
        except ValueError as e:
            raise TowerError(str(e))


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
    me = need(args, c, HUB)
    store(c).set_lane(me, doing=clip(args.text, 80), doing_at=time.time())
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
    me = need(args, c, HUB)
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
    s.push(args.lane, me, "tell", f"From the hub: {args.text}", task["id"])
    print(f"→ {args.lane} task #{task['id']} (delivered when idle)")


def cmd_wrap(args: argparse.Namespace) -> None:
    c = cfg()
    me = need(args, c, HUB)
    need_team(c, args.lane)
    s = store(c)
    task = s.live_task(args.lane)
    if not task:
        raise TowerError(f"{args.lane} has no running task")
    s.update_task(task["id"], state="wrapping")
    s.push(args.lane, me, "wrap", f"Wrap up task #{task['id']}: remove temp files, save what the next session needs, "
           f"then `dah report wrapped \"one line\"`. {args.note or ''}".strip(), task["id"])
    print(f"→ {args.lane} wrap requested")


def cmd_decide(args: argparse.Namespace) -> None:
    c = cfg()
    me = need(args, c, HUB)
    try:
        it = store(c).decide(args.id, args.verdict, me, args.choice, args.note or "", args.basis or "")
    except StoreError as e:
        raise TowerError(str(e))
    print(f"#{it['id']} {verdict_text(it)}; the tower routes it.")


def cmd_escalate(args: argparse.Namespace) -> None:
    c = cfg()
    need(args, c, HUB)
    s = store(c)
    try:
        to = escalation_audience(s, s.item(args.id), args.to, c.requester)
        s.escalate(args.id, args.why, to)
    except StoreError as e:
        raise TowerError(str(e))
    print(f"#{args.id} → humans" + (f" ({to} only)" if to else " (everyone)") + ". Carry on with other work.")


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
    me = need(args, c, HUB, *c.infra_sessions())
    s = store(c)
    try:
        if args.sub == "paths":
            memo_paths(c, s, me, args.id, args.paths, args.alone)
            return
        if args.sub == "reply":
            if args.covers and not is_infra(me):
                raise TowerError("only infra marks other memos as covered by its answer")
            memo = s.memo(args.id)
            asker = member_name(s, str(memo["sender"])) if c.requester else None
            card = s.reply_memo(args.id, args.text, INFRA if is_infra(me) else me, set(c.all_lanes),
                                asker if asker in {m["name"] for m in s.members()} else None)
            print(f"Answered #{args.id}; posted as review card #{card} for humans to confirm." if card
                  else f"Answered #{args.id}; it shows on the board.")
            if is_infra(me) and c.requester and c.is_hub(str(memo["sender"])):
                s.push(str(memo["sender"]), INFRA, "infra_reply", infra_reply_text(args.id, args.text),
                       item_id=args.id)
            for n in args.covers or []:
                try:
                    cancel_waiting(c, s, me, n, covered_text(c.lang, args.id))
                except StoreError as e:
                    print(f"#{n} not taken out: {e}")
        elif args.sub == "cancel":
            if not c.is_hub(me):
                raise TowerError("only a hub cancels a waiting infra memo or decision")
            cancel_waiting(c, s, me, args.id, args.why)
        elif args.sub == "bump":
            if not c.is_hub(me):
                raise TowerError("only the hub reorders the infra queue")
            s.bump_message(args.id, INFRA)
            print(f"#{args.id} is first in the infra queue: the next free infra session takes it")
        elif args.sub == "forward":
            if not is_infra(me):
                raise TowerError("only infra forwards memos")
            memo = s.memo(args.id)
            back = memo["sender"] if c.is_hub(memo["sender"]) else HUB
            s.push(back, memo["sender"], "memo", f"{memo['text']} (forwarded by infra from memo #{args.id})")
            s.reply_memo(args.id, "Passed to the hub.", INFRA)
            print(f"memo #{args.id} → hub")
        else:
            send_infra(c, s, me, args.text, args.approved, args.paths, args.alone)
    except StoreError as e:
        raise TowerError(str(e))


def cancel_waiting(c: Config, s: Store, me: str, mid: int, why: str) -> None:
    why = why.strip()
    if not why:
        raise TowerError("say why with --why: it stays on the board")
    msg = s.cancel_message(mid, INFRA, cancelled_text(c.lang, me, why), me)
    sender = str(msg["sender"])
    told = c.is_hub(sender) and sender != me
    if told:
        s.push(sender, me, "memo_cancelled", f"Infra {msg['kind']} #{mid} left the queue ({me}): {clip(why, 700)}")
    print(f"#{mid} is out of the infra queue; the reason shows on the board" + (f" and went to {sender}." if told else "."))


def memo_paths(c: Config, s: Store, me: str, mid: int, given: list[str], alone: bool = False) -> None:
    from ..engine import ALONE, block_text, message_block, paths_label
    paths = ALONE if alone else (", ".join(p.strip() for p in given if p.strip()) or None)
    msg = s.message(mid)
    if is_infra(me):
        if s.lane(me).get("active_message") != mid:
            raise TowerError(f"#{mid} is not the message this session ({me}) holds")
        if paths is None:
            raise TowerError("name the files and folders you will change, or --alone")
        s.set_message_paths(mid, paths)
        live = [{**m, "_lane": k} for k, m in s.held_messages([k for k in c.infra_sessions() if k != me]).items()]
        b = message_block({**msg, "paths": paths}, live, time.time())
        if b and b[0] != "declaring":
            s.release_inbox(me)
            print(f"#{mid} paths: {paths_label({'paths': paths})}. {block_text(b[0], b[1])}: #{mid} went back to the "
                  f"infra queue and this session closes. Change nothing, do not report; end your turn now.")
            return
        print(f"#{mid} paths: {paths_label({'paths': paths})} (declared by {me}). Stay inside them.")
        return
    if not c.is_hub(me):
        raise TowerError("only the hub or the infra session that holds it sets a message's paths")
    if msg["target"] != INFRA or msg["delivered"] is not None:
        raise TowerError(f"#{mid} is not a waiting infra message")
    s.set_message_paths(mid, paths)
    print(f"#{mid} paths: {paths_label({'paths': paths})}"
          + ("" if paths else " · the session that takes it declares them"))


def send_infra(c: Config, s: Store, me: str, text: str, approved: str | None, paths: str | None = None,
               alone: bool = False, model: str | None = None, effort: str | None = None) -> None:
    from ..engine import ALONE
    if not c.is_hub(me) or INFRA not in c.lanes:
        raise TowerError("only the hub sends requests to infra; humans ask the hub")
    if bad := stagemodel.problems(model, effort):
        raise TowerError("; ".join(bad))
    head = [f"{k}: {v.strip()}" for k, v in (("model", model), ("effort", effort and effort.lower())) if v and v.strip()]
    if head:
        text = "\n".join(head) + "\n\n" + text
    if approved and approved.strip():
        text = f"{text}\n\nApproved by the human: {approved.strip()}"
    paths = ALONE if alone else (", ".join(p.strip() for p in (paths or "").split(",") if p.strip()) or None)
    parallel = len(c.infra_sessions()) > 1
    print(f"memo #{s.push(INFRA, me, 'memo', text, paths=paths)} → infra"
          + (" (alone: no other infra session runs beside it)" if alone and parallel else "" if paths or not parallel else
             " (info: no --paths; the infra session declares its own when it starts. Add --paths as a hint, or "
             "--alone only when it must run with no other infra session, e.g. a reinstall)"))


def cmd_infra(args: argparse.Namespace) -> None:
    c = cfg()
    try:
        send_infra(c, store(c), need(args, c, HUB, *c.infra_sessions()), args.text, args.approved, args.paths, args.alone,
                   args.model, args.effort)
    except StoreError as e:
        raise TowerError(str(e))
