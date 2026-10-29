"""dah: the CLI lanes, the hub, infra and humans use. Run `dah -h` or `dah COMMAND -h`.

Who is calling comes from TOWER_LANE (set in sessions the tower opens) or `--as LANE`.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as config_mod
from . import procs
from .config import HUB, INFRA, RUNTIMES, Config, ConfigError, tower_pid
from .engine import VERDICT_WORD, Engine, clip
from .install import SHIM_PREFIX, default_runtime, edit_config, init, install, lane_template, remove_shims, write_shims
from .notify import notifier
from .store import GOAL_RANKED, GOAL_STATES, HUMAN, Store, StoreError

TICK_SECONDS = 4
TUNNEL_RETRY = 30


class TowerError(Exception):
    pass


def cfg() -> Config:
    return config_mod.load()


def store(c: Config | None = None) -> Store:
    return Store((c or cfg()).db)


def who(args: argparse.Namespace, c: Config) -> str:
    me = getattr(args, "as_", None) or os.environ.get("TOWER_LANE")
    if not me:
        raise TowerError("Unknown caller. Sessions the tower opens have TOWER_LANE; by hand, add --as LANE.")
    if me not in c.lanes:
        raise TowerError(f"unknown lane {me} (lanes: {', '.join(c.lanes)})")
    return me


def need(args: argparse.Namespace, c: Config, *lanes: str) -> str:
    me = who(args, c)
    if me not in lanes:
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


# ---------------------------------------------------------------- lanes

def cmd_mine(args: argparse.Namespace) -> None:
    c = cfg()
    s, me = store(c), who(args, c)
    task = s.live_task(me)
    if not task:
        print("No task right now.")
        return
    print(f"task #{task['id']} \"{task['title']}\" ({task['state']})\n{task['brief']}\n")
    if task.get("goal_id"):
        print(f"serves goal: {goal_line(s.goal(task['goal_id']))}\n")
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
    options = parse_options(args.option)
    kind = args.kind or ("decision" if options else "review")
    task = s.live_task(me) if me in c.team() else None
    audience = HUB if me in c.team() else HUMAN  # hub and infra items go straight to humans
    try:
        iid = s.add_item(me, task["id"] if task else None, kind, args.title, body, options, atts, audience, args.multi,
                         assignee=args.to)
    except StoreError as e:
        raise TowerError(str(e))
    if audience == HUMAN:
        print(f"#{iid} posted for humans (push notification). Don't wait: finish your scope; the decision comes back "
              f"later (`dah show {iid}`).")
        return
    s.push(HUB, me, "item", f"Item #{iid} from {me}" + (f" task #{task['id']}" if task else "")
           + f" \"{args.title}\" → `dah show {iid}`", task["id"] if task else None, iid)
    s.signal(task["id"] if task else None)
    print(f"#{iid} posted; the hub sees it first. Don't wait: finish the unblocked scope and list #{iid} in "
          f"`dah report done`.")


def cmd_item_update(args: argparse.Namespace) -> None:
    c = cfg()
    s, me = store(c), who(args, c)
    it = s.item(args.id)
    if me not in (it["lane"], HUB):
        raise TowerError(f"#{args.id} belongs to {it['lane']}")
    if it.get("question"):
        raise TowerError(f"#{args.id} is a live question")
    body = body_of(args)
    atts = check_paths(c, args.attach) if args.attach else None
    if body is None and atts is None and not args.title and not args.option:
        raise TowerError("nothing to change (--title, --body/--body-file, --option, --attach)")
    try:
        s.revise_item(args.id, args.title, body, parse_options(args.option) if args.option else None, atts)
    except StoreError as e:
        raise TowerError(str(e))
    if me in c.team():
        task = s.live_task(me)
        s.push(HUB, me, "item", f"Item #{args.id} updated by {me} → `dah show {args.id}`",
               task["id"] if task else None, args.id)
    print(f"#{args.id} updated.")


def cmd_item_merge(args: argparse.Namespace) -> None:
    c = cfg()
    need(args, c, HUB)
    try:
        store(c).merge_item(args.id, args.into, HUB)
    except StoreError as e:
        raise TowerError(str(e))
    print(f"#{args.id} merged into #{args.into}.")


def cmd_report(args: argparse.Namespace) -> None:
    c = cfg()
    s, me = store(c), who(args, c)
    if me == HUB:
        raise TowerError("the hub does not report; it decides, tells and wraps")
    if me == INFRA and args.kind != "wrapped":
        raise TowerError("infra only uses `report wrapped`; answer memos with `dah memo reply`")
    task = s.live_task(me)
    tid = task["id"] if task else None
    atts = check_paths(c, args.attach)
    if args.kind == "handoff" and not task:
        raise TowerError("no task to hand off")
    s.add_report(tid, me, args.kind, args.text, atts)
    s.signal(tid)
    tag = me + (f" task #{tid} \"{task['title']}\"" if task else "")
    if args.kind == "handoff" and task:
        s.update_task(task["id"], handoff=args.text, rotate_ready=time.time())
        print("Handoff saved. End your turn; the tower continues in a fresh session.")
        return
    if args.kind == "wrapped":
        if task:
            s.update_task(task["id"], state="closed", closed=time.time())
        s.set_lane(me, closing=time.time())
        if me != INFRA:
            s.push(HUB, me, "wrapped", f"Wrapped: {tag}", tid)
        print("Wrapped. End your turn; the tower closes this session.")
        return
    att = f" (files: {', '.join(atts)})" if atts else ""
    s.push(HUB, me, args.kind, f"{args.kind.capitalize()}: {tag}: {clip(args.text, 700)}{att}", tid)
    print(f"Sent to the hub." + (" Review comes back to this session; you may end your turn." if args.kind == "done" else ""))


# ---------------------------------------------------------------- hub

def cmd_task(args: argparse.Namespace) -> None:
    c = cfg()
    need(args, c, HUB)
    need_team(c, args.lane)
    s = store(c)
    if args.goal is not None and s.goal(args.goal)["state"] != "active":
        raise TowerError(f"goal #{args.goal} is not active")
    tid = s.add_task(args.lane, args.title, args.brief, args.goal)
    live = s.live_task(args.lane)
    print(f"task #{tid} → {args.lane}" + (f" for goal #{args.goal}" if args.goal is not None else "")
          + (f" (after task #{live['id']} wraps)" if live else ""))


# ---------------------------------------------------------------- goals

def goal_line(g: dict[str, Any]) -> str:
    plan = f" · {g['progress']}% ({g['steps_done']}/{len(g['steps'])})" if g["steps"] else " · no plan yet"
    rank = f"{g['priority']}. " if g["state"] in GOAL_RANKED else ""
    return f"{rank}#{g['id']} [{g['state']}] {g['title']}{plan if g['state'] != 'done' else ' · done'}"


def goal_actor(args: argparse.Namespace) -> str:
    me = getattr(args, "as_", None) or os.environ.get("TOWER_LANE")
    if not me:
        return getpass.getuser()
    if me != HUB:
        raise TowerError("goals are set by humans and the hub; report to the hub instead")
    return me


def cmd_goal(args: argparse.Namespace) -> None:
    c = cfg()
    s = store(c)
    if args.sub == "list":
        goals = s.goals(GOAL_STATES if args.all else GOAL_RANKED)
        print("\n".join(goal_line(g) for g in goals) if goals else "No goals. `dah goal add \"title\"`")
        return
    if args.sub == "show":
        g = s.goal(args.id)
        print(goal_line(g) + f"\nby {g['created_by']} · {hhmm(g['created'])}")
        if g["detail"]:
            print(f"\n{g['detail']}")
        for i, st in enumerate(g["steps"], 1):
            print(f"  {i}. [{'x' if st.get('done') else ' '}] {st['text']}")
        if g.get("note"):
            print(f"\nnote: {g['note']}")
        for t in s.tasks(limit=500):
            if t["goal_id"] == g["id"]:
                print(f"  task #{t['id']} {t['lane']} {t['state']} \"{t['title']}\"")
        return
    me = goal_actor(args)
    if args.sub == "add":
        gid = s.add_goal(args.title, args.detail or "", me)
        if args.priority:
            s.move_goal(gid, args.priority)
        g = s.goal(gid)
        news = f"New goal #{gid} (priority {g['priority']}) from {me}: \"{g['title']}\"" + (
            f" — {g['detail']}" if g["detail"] else "") + ". Agree a plan with the humans, then `dah goal plan`."
        print(f"goal #{gid} added at priority {g['priority']}")
    elif args.sub == "priority":
        order = s.move_goal(args.id, args.position)
        news = f"{me} moved goal #{args.id} to priority {args.position}. Order now: " + ", ".join(f"#{i}" for i in order)
        print(f"order: {' '.join('#' + str(i) for i in order)}")
    elif args.sub == "plan":
        g = s.plan_goal(args.id, args.step)
        news = f"{me} set the plan of goal #{args.id}: " + "; ".join(st["text"] for st in g["steps"])
        print(goal_line(g))
    elif args.sub in ("check", "uncheck"):
        g = s.check_steps(args.id, args.steps, args.sub == "check")
        news = f"{me} {args.sub}ed step(s) {', '.join(map(str, args.steps))} of goal #{args.id}: now {g['progress']}%"
        print(goal_line(g))
    else:
        state = {"done": "done", "drop": "dropped", "pause": "paused", "resume": "active"}[args.sub]
        g = s.set_goal_state(args.id, state, args.note or "")
        news = f"{me} marked goal #{args.id} \"{g['title']}\" {state}" + (f": {args.note}" if args.note else "")
        print(goal_line(g))
    if me != HUB:
        s.push(HUB, me, "goal", news)


def cmd_doing(args: argparse.Namespace) -> None:
    c = cfg()
    need(args, c, HUB)
    store(c).set_lane(HUB, doing=clip(args.text, 80), doing_at=time.time())
    print("Shown on the board.")


def cmd_tell(args: argparse.Namespace) -> None:
    c = cfg()
    need(args, c, HUB)
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
            s.reply_memo(args.id, args.text, me)
            print(f"Answered #{args.id}; it shows on the board.")
        elif args.sub == "forward":
            if me != INFRA:
                raise TowerError("only infra forwards memos")
            memo = s.memo(args.id)
            s.push(HUB, memo["sender"], "memo", f"{memo['text']} (forwarded by infra from memo #{args.id})")
            s.reply_memo(args.id, "Passed to the hub.", INFRA)
            print(f"memo #{args.id} → hub")
        else:
            if me != HUB or args.target != INFRA or INFRA not in c.lanes:
                raise TowerError("only the hub sends memos, to infra")
            print(f"memo #{s.push(INFRA, HUB, 'memo', args.text)} → infra")
    except StoreError as e:
        raise TowerError(str(e))


# ---------------------------------------------------------------- reading

def cmd_status(_args: argparse.Namespace) -> None:
    c = cfg()
    s = store(c)
    pid = tower_pid(c)
    print(f"tower {'running (pid ' + str(pid) + ')' if pid else 'stopped'}"
          + (" · paused" if (c.state_dir / "paused").exists() else "") + f" · board http://{c.web_host}:{c.web_port}"
          + (f" · {c.public_url}" if c.public_url else ""))
    for lid, ln in c.lanes.items():
        row = s.lane(lid)
        task = s.live_task(lid)
        queued = sum(1 for t in s.tasks(("queued",), 500) if t["lane"] == lid)
        line = f"{lid:<10} {ln.runtime:<8} {row.get('status') or 'closed':<7}"
        if task:
            line += f" · task #{task['id']} {task['state']} \"{clip(task['title'], 50)}\""
        if queued:
            line += f" · {queued} queued"
        opened = sum(1 for it in s.items(state="open", lane=lid))
        if opened:
            line += f" · {opened} open items"
        print(line)
    goals = s.goals()
    print("\ngoals (first priority first):" if goals else "\ngoals: none yet")
    for g in goals:
        print(f"  {goal_line(g)}")
    hub_items = s.items(state="open", audience=HUB)
    print(f"\nopen for the hub: {len(hub_items)} · for humans: {len(s.items(state='open', audience=HUMAN))}")
    for it in hub_items[:20]:
        print(f"  #{it['id']} {it['lane']} \"{it['title']}\"")


def cmd_show(args: argparse.Namespace) -> None:
    s = store()
    try:
        it = s.item(args.id)
    except StoreError as e:
        raise TowerError(str(e))
    print(f"#{it['id']} [{it['kind']} · {it['lane']} · for {it['audience']}"
          + (f" → {it['assignee']}" if it.get("assignee") else "") + f"] {it['title']}")
    print(f"state: {verdict_text(it)}" + (f" · by {it['decided_by']}" if it.get("decided_by") else ""))
    for key in ("note", "basis", "escalation"):
        if it.get(key):
            print(f"{key}: {it[key]}")
    for o in it["options"]:
        print(f"  - {o['label']}" + (f": {o['desc']}" if o["desc"] else ""))
    for a in it["attachments"]:
        print(f"  file: {a}")
    print("\n" + it["body"])


def cmd_items(args: argparse.Namespace) -> None:
    s = store()
    for it in s.items(state="open" if args.open else None, lane=args.lane, limit=args.n):
        print(f"#{it['id']:<5} {it['lane']:<10} {it['audience']:<6} {verdict_text(it):<22} {it['title']}")


def cmd_tasks(args: argparse.Namespace) -> None:
    s = store()
    states = None if args.all else ("queued", "running", "wrapping")
    for t in s.tasks(states, 100):
        print(f"#{t['id']:<5} {t['lane']:<10} {t['state']:<9} {hhmm(t['created'])}  {t['title']}")


def cmd_log(args: argparse.Namespace) -> None:
    s = store()
    t = s.task(args.task)
    print(f"task #{t['id']} {t['lane']} {t['state']} \"{t['title']}\"\n{t['brief']}\n")
    for r in s.reports(t["id"]):
        print(f"[{hhmm(r['created'])}] {r['kind']}: {r['text']}")


def cmd_last(args: argparse.Namespace) -> None:
    from .runtimes import Runtimes
    c = cfg()
    if args.lane not in c.lanes:
        raise TowerError(f"unknown lane {args.lane}")
    texts = Runtimes(c, store(c)).last_text(args.lane, args.n)
    print("\n\n---\n\n".join(texts) if texts else "(nothing yet)")


# ---------------------------------------------------------------- running the tower

def say(text: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {text}", flush=True)


def cmd_run(args: argparse.Namespace) -> None:
    from .runtimes import Runtimes
    from .web import server

    c = cfg()
    if tower_pid(c) and tower_pid(c) != os.getpid():
        raise TowerError(f"the tower already runs (pid {tower_pid(c)})")
    s = store(c)
    (c.state_dir / "tower.pid").write_text(str(os.getpid()), encoding="utf-8")
    config_mod.register(c.home)
    if not args.no_hub:
        s.set_lane(HUB, enabled=1)
    httpd = server(c, s, say)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    say(f"board http://{c.web_host}:{c.web_port}" + (f" · {c.public_url}" if c.public_url else ""))
    cfg_path = c.home / config_mod.TOWER_DIR / config_mod.CONFIG_NAME
    mtime = cfg_path.stat().st_mtime
    engine = Engine(c, s, Runtimes(c, s), notifier(c, say), log=say)
    tunnel: subprocess.Popen[bytes] | None = None
    tunnel_at = 0.0

    def start_tunnel(cmd: str) -> subprocess.Popen[bytes]:
        with (c.state_dir / "tunnel.log").open("ab") as log:
            return subprocess.Popen(cmd, shell=True, stdout=log, stderr=subprocess.STDOUT, **procs.detached())

    try:
        while True:
            try:
                if cfg_path.stat().st_mtime != mtime:  # infra edited tower.json: pick it up without a restart
                    mtime = cfg_path.stat().st_mtime
                    c2 = config_mod.load(c.home)
                    if (c2.web_host, c2.web_port, c2.auth_mode) != (c.web_host, c.web_port, c.auth_mode):
                        say("web/auth changed in tower.json: restart the tower to apply it")
                    if c2.tunnel != c.tunnel and tunnel:
                        procs.terminate(tunnel.pid)
                        tunnel = None
                    c = c2
                    engine = Engine(c, s, Runtimes(c, s), notifier(c, say), log=say)
                    say("tower.json reloaded")
            except (ConfigError, OSError) as e:
                say(f"tower.json not reloaded: {e}")
            if c.tunnel and (tunnel is None or tunnel.poll() is not None) and time.time() - tunnel_at >= TUNNEL_RETRY:
                tunnel, tunnel_at = start_tunnel(c.tunnel), time.time()
                say(f"tunnel started: {c.tunnel}")
            try:
                if not (c.state_dir / "paused").exists():
                    engine.tick()
            except Exception as e:  # the loop never dies; the next tick looks again
                say(f"tick failed: {e!r}")
            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        if tunnel and tunnel.poll() is None:
            procs.terminate(tunnel.pid)
        (c.state_dir / "tower.pid").unlink(missing_ok=True)


def cmd_start(_args: argparse.Namespace) -> None:
    c = cfg()
    if tower_pid(c):
        print(f"already running (pid {tower_pid(c)})")
        return
    c.state_dir.mkdir(parents=True, exist_ok=True)
    log = (c.state_dir / "tower.log").open("ab")
    proc = subprocess.Popen([sys.executable, "-m", "agent_hub", "run"], cwd=c.home, stdout=log, stderr=log,
                            stdin=subprocess.DEVNULL, **procs.detached(),
                            env={**os.environ, "TOWER_HOME": str(c.home),
                                 "PYTHONPATH": str(Path(__file__).resolve().parents[1])
                                 + os.pathsep + os.environ.get("PYTHONPATH", "")})
    for _ in range(20):
        time.sleep(0.25)
        if tower_pid(c) or proc.poll() is not None:
            break
    if not tower_pid(c):
        raise TowerError(f"the tower did not start; see {c.state_dir / 'tower.log'}")
    print(f"tower started (pid {tower_pid(c)}) · board http://{c.web_host}:{c.web_port} · log {c.state_dir / 'tower.log'}")


def cmd_stop(args: argparse.Namespace) -> None:
    c = cfg()
    pid = tower_pid(c)
    if pid:  # stop the loop first, or it reopens the hub the moment its session closes
        procs.terminate(pid, group=False)  # the loop only: lane sessions are its children on Windows
        for _ in range(40):
            if not tower_pid(c):
                break
            time.sleep(0.25)
        print(f"tower stopped (pid {pid})." + ("" if args.close_lanes else
                                               " Lane sessions keep running unless you pass --close-lanes."))
    else:
        print("the tower is not running")
    if args.close_lanes:
        from .runtimes import Runtimes
        rt = Runtimes(c, store(c))
        for lid in c.lanes:
            if rt.owned(lid) and rt.status(lid) != "closed":
                rt.close(lid)
                print(f"closed {lid}")


def cmd_pause(args: argparse.Namespace) -> None:
    c = cfg()
    flag = c.state_dir / "paused"
    if args.cmd == "pause":
        c.state_dir.mkdir(parents=True, exist_ok=True)
        flag.touch()
        print("paused: the board still takes decisions; lanes are not driven")
    else:
        flag.unlink(missing_ok=True)
        print("resumed")


# ---------------------------------------------------------------- setup

def cmd_discover(args: argparse.Namespace) -> None:
    from .discover import survey
    print(json.dumps(survey(Path(args.project) if args.project else None), indent=2, ensure_ascii=False))


def cmd_init(args: argparse.Namespace) -> None:
    home = Path(args.home or os.getcwd()).resolve()
    if not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", args.lang):
        raise TowerError(f"--lang takes a language code such as en, ko, ja, zh, es, pt-BR (got {args.lang!r})")
    hub = args.hub or default_runtime()
    infra = args.infra or hub
    if not hub:
        raise TowerError("no agent CLI found (opencode, claude or codex); pass --hub/--infra")
    try:
        path = init(home, args.name, args.lang, hub, infra, args.project, args.safe, args.force)
    except FileExistsError as e:
        raise TowerError(str(e))
    c = config_mod.load(home)
    wrote = install(c, force=args.force)
    slug = config_mod.register(home)
    print(f"wrote {path}")
    for w in wrote:
        print(f"wrote {w}")
    print(f"board port {c.web_port} · workspace \"{slug}\" registered (`dah workspaces`)")
    print("next: `dah start`, then open the board")


def cmd_install(args: argparse.Namespace) -> None:
    for w in install(cfg(), force=args.force) or ["(nothing new)"]:
        print(w)


def cmd_skill(args: argparse.Namespace) -> None:
    """Global setup entry for each agent, so "set up agent-hub here" works from any workspace."""
    import difflib
    from .install import skill_plan
    plan = skill_plan(Path.home(), args.runtime)
    if not plan:
        raise TowerError("no agent config found (~/.claude, ~/.config/opencode, ~/.codex); pass --runtime")
    for runtime, path, old, new in plan:
        if old == new:
            print(f"{runtime}: {path} is up to date")
            continue
        if not args.yes:
            print(f"{runtime}: {'update' if old else 'create'} {path}")
            sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                       str(path) if old else "/dev/null", str(path)))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
        print(f"{runtime}: wrote {path}")
    if not args.yes and any(old != new for _, _, old, new in plan):
        print("\nNothing written. These are global agent files: run again with --yes to write them.")


def cmd_guide(args: argparse.Namespace) -> None:
    from .install import GUIDE
    name = args.name or "setup"
    path = GUIDE / ("SETUP.md" if name == "setup" else f"deploy/{name.removeprefix('deploy/')}.md")
    if not path.is_file():
        names = ["setup", *sorted(p.stem for p in (GUIDE / "deploy").glob("*.md"))]
        raise TowerError(f"no guide {name!r} (guides: {', '.join(names)})")
    print(f"<!-- {path} -->")
    print(path.read_text(encoding="utf-8"))


def cmd_workspaces(args: argparse.Namespace) -> None:
    if args.sub == "remove":
        config_mod.unregister(args.slug)
        print(f"{args.slug} removed from the list (its .tower/ is untouched)")
        return
    regs = config_mod.workspaces()
    if not regs:
        print("No workspaces yet. `dah init` in a workspace registers it.")
        return
    for slug, home in regs.items():
        try:
            c = config_mod.load(home)
        except ConfigError:
            print(f"{slug:<16} (missing: {home})")
            continue
        state = f"running · http://{c.web_host}:{c.web_port}" if tower_pid(c) else "stopped"
        print(f"{slug:<16} {c.name:<20} {state:<36} {home}")
    print(f"\nAny running board shows every workspace here under /w/<name>/ (switch from its title). List: "
          f"{config_mod.registry_path()}")


def cmd_lane(args: argparse.Namespace) -> None:
    c = cfg()
    if args.sub == "list":
        for lid, ln in c.lanes.items():
            print(f"{lid:<10} {ln.kind:<6} {ln.runtime:<8} {ln.label}" + (f" :{ln.port}" if ln.port else ""))
        return
    if args.id in (HUB, INFRA) and args.sub == "remove":
        raise TowerError("hub and infra are built in")
    if args.sub == "add":
        if args.id in c.lanes:
            raise TowerError(f"lane {args.id} exists")
        entry: dict[str, Any] = {"runtime": args.runtime, "label": args.label or args.id}
        if args.model:
            entry["model"] = args.model
        if args.agent:
            if args.runtime == "codex":
                raise TowerError("codex has no agents; set \"args\": [\"--profile\", NAME] in tower.json instead")
            entry["agent"] = args.agent
        edit_config(c.home, lambda d: d["lanes"].__setitem__(args.id, entry))
        c = config_mod.load(c.home)
        path = c.instructions(args.id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(lane_template(args.id), encoding="utf-8")
        wrote: list[str] = []
        write_shims(c, list(c.lanes), False, wrote)  # a new runtime gets every lane's shortcut, too
        print(f"lane {args.id} added ({args.runtime}" + (f", agent {args.agent}" if args.agent else "") + "). "
              f"Fill in {path.relative_to(c.home)}: what it owns and its done criteria. "
              f"Open it by hand with /{SHIM_PREFIX}{args.id}.")
        return
    if args.id not in c.lanes:
        raise TowerError(f"no lane {args.id}")
    if store(c).live_task(args.id):
        raise TowerError(f"{args.id} has a running task; wrap or cancel it first")
    edit_config(c.home, lambda d: d["lanes"].pop(args.id))
    remove_shims(c, args.id)
    print(f"lane {args.id} removed (its instruction file is kept)")


def cmd_member(args: argparse.Namespace) -> None:
    s = store()
    if args.sub == "list":
        for m in s.members():
            print(f"{m['name']:<16} {m['role']:<7} {m['ident'] or '-':<30} {'token' if m['has_token'] else ''}")
        return
    if args.sub == "remove":
        s.remove_member(args.name)
        print(f"removed {args.name}")
        return
    token = s.add_member(args.name, args.role, args.ident, args.token)
    print(f"member {args.name} ({args.role})" + (f" ident {args.ident}" if args.ident else ""))
    if token:
        print(f"token (shown once; give it privately): {token}")


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="dah", description="Control tower and review board for AI agent lanes.")
    ap.add_argument("--as", dest="as_", metavar="LANE", help="act as this lane (default: $TOWER_LANE)")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    sub.add_parser("mine", help="your task and its items")
    p = sub.add_parser("item", help="post or fix a board item")
    isub = p.add_subparsers(dest="sub", required=True)
    for name in ("add", "update"):
        q = isub.add_parser(name)
        if name == "update":
            q.add_argument("id", type=int)
        q.add_argument("--title", required=name == "add")
        q.add_argument("--body")
        q.add_argument("--body-file")
        q.add_argument("--option", action="append", default=[], metavar="LABEL=MEANING")
        q.add_argument("--attach", action="append", default=[], metavar="PATH")
        if name == "add":
            q.add_argument("--kind", choices=("decision", "review"))
            q.add_argument("--multi", action="store_true", help="several options may be picked")
            q.add_argument("--to", metavar="MEMBER", help="hub/infra: the person who should decide")
    q = isub.add_parser("merge")
    q.add_argument("id", type=int)
    q.add_argument("--into", type=int, required=True)

    p = sub.add_parser("report", help="team lanes: done | blocked | note | handoff; everyone: wrapped")
    p.add_argument("kind", choices=("done", "blocked", "note", "handoff", "wrapped"))
    p.add_argument("text")
    p.add_argument("--attach", action="append", default=[])

    p = sub.add_parser("task", help="hub: queue a task for a team lane")
    p.add_argument("lane")
    p.add_argument("--title", required=True)
    p.add_argument("--goal", type=int, metavar="GOAL_ID", help="the goal this task serves")
    p.add_argument("brief")
    p = sub.add_parser("doing", help="hub: one line shown on the board")
    p.add_argument("text")
    p = sub.add_parser("tell", help="hub: message a running task")
    p.add_argument("lane")
    p.add_argument("text")
    p = sub.add_parser("wrap", help="hub: ask a lane to wrap up")
    p.add_argument("lane")
    p.add_argument("--note")
    p = sub.add_parser("decide", help="hub: decide an item within approved scope")
    p.add_argument("id", type=int)
    p.add_argument("verdict", choices=("ok", "no", "hold"))
    p.add_argument("--choice", action="append")
    p.add_argument("--note")
    p.add_argument("--basis")
    p = sub.add_parser("escalate", help="hub: send an item to humans")
    p.add_argument("id", type=int)
    p.add_argument("--why", required=True)
    p.add_argument("--to", metavar="MEMBER")
    p = sub.add_parser("cancel", help="hub: cancel a task")
    p.add_argument("task", type=int)
    p = sub.add_parser("memo", help="hub/infra: reply, forward or send memos")
    msub = p.add_subparsers(dest="sub", required=True)
    q = msub.add_parser("reply")
    q.add_argument("id", type=int)
    q.add_argument("text")
    q = msub.add_parser("forward")
    q.add_argument("id", type=int)
    q = msub.add_parser("send")
    q.add_argument("target", choices=(INFRA,))
    q.add_argument("text")

    p = sub.add_parser("goal", help="goals the team works toward: add, rank, plan, tick steps, finish")
    gsub = p.add_subparsers(dest="sub", required=True)
    q = gsub.add_parser("add", help="a new goal (last priority unless --priority)")
    q.add_argument("title")
    q.add_argument("--detail", help="what done looks like, constraints")
    q.add_argument("--priority", type=int, metavar="N", help="1 = first")
    q = gsub.add_parser("list")
    q.add_argument("--all", action="store_true", help="also done and dropped goals")
    q = gsub.add_parser("show")
    q.add_argument("id", type=int)
    q = gsub.add_parser("priority", help="move a goal to position N (1 = first)")
    q.add_argument("id", type=int)
    q.add_argument("position", type=int)
    q = gsub.add_parser("plan", help="set the goal's steps (replaces the list; unchanged steps keep their tick)")
    q.add_argument("id", type=int)
    q.add_argument("--step", action="append", required=True, metavar="TEXT")
    for name in ("check", "uncheck"):
        q = gsub.add_parser(name, help=f"{name} steps by number")
        q.add_argument("id", type=int)
        q.add_argument("steps", type=int, nargs="+")
    for name in ("done", "pause", "resume", "drop"):
        q = gsub.add_parser(name)
        q.add_argument("id", type=int)
        q.add_argument("note", nargs="?", default="")

    sub.add_parser("status", help="goals, lanes, tasks and open items")
    p = sub.add_parser("show", help="one board item")
    p.add_argument("id", type=int)
    p = sub.add_parser("items", help="list board items")
    p.add_argument("--open", action="store_true")
    p.add_argument("--lane")
    p.add_argument("-n", type=int, default=50)
    p = sub.add_parser("tasks", help="list tasks")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("log", help="a task's brief and reports")
    p.add_argument("task", type=int)
    p = sub.add_parser("last", help="a lane's last words")
    p.add_argument("lane")
    p.add_argument("-n", type=int, default=1)

    p = sub.add_parser("run", help="run the tower in the foreground (loop + board)")
    p.add_argument("--no-hub", action="store_true", help="do not keep the hub running")
    sub.add_parser("start", help="run the tower in the background")
    p = sub.add_parser("stop", help="stop the background tower")
    p.add_argument("--close-lanes", action="store_true")
    sub.add_parser("pause", help="stop driving lanes (the board keeps working)")
    sub.add_parser("resume", help="drive lanes again")

    p = sub.add_parser("discover", help="survey agents, configs, MCP servers and hosting tools (JSON, no secrets)")
    p.add_argument("--project")
    p = sub.add_parser("init", help="create .tower/ in this workspace")
    p.add_argument("--home", help="workspace folder (default: here)")
    p.add_argument("--name", default="Agent Hub")
    p.add_argument("--lang", default="en", help="language people read the board in: en, ko, ja, zh, es, ... (default en)")
    p.add_argument("--hub", choices=RUNTIMES)
    p.add_argument("--infra", choices=RUNTIMES)
    p.add_argument("--project", help="folder agents work in, relative to the workspace (default: the workspace)")
    p.add_argument("--safe", action="store_true", help="sandboxed agent permissions instead of unattended ones")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("install", help="(re)write protocol, lane files and command shims")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("skill", help="install the global agent-hub-setup skill/command for your agents")
    ssub = p.add_subparsers(dest="sub", required=True)
    q = ssub.add_parser("install")
    q.add_argument("--runtime", action="append", choices=RUNTIMES, help="only this agent (repeatable)")
    q.add_argument("--yes", action="store_true", help="write the files (without it: show the diff only)")
    p = sub.add_parser("guide", help="print the setup playbook, or a guide for reaching the board (tailscale, ...)")
    p.add_argument("name", nargs="?", help="setup (default), lan, tailscale, cloudflare, aws")
    p = sub.add_parser("workspaces", help="workspaces with a tower on this machine (the board switches between them)")
    wsub = p.add_subparsers(dest="sub")
    wsub.add_parser("list")
    q = wsub.add_parser("remove")
    q.add_argument("slug")
    p = sub.add_parser("lane", help="list, add or remove lanes")
    lsub = p.add_subparsers(dest="sub", required=True)
    lsub.add_parser("list")
    q = lsub.add_parser("add")
    q.add_argument("id")
    q.add_argument("--runtime", choices=RUNTIMES, required=True)
    q.add_argument("--label")
    q.add_argument("--model")
    q.add_argument("--agent", help="run the role on one of your own agents (an OpenCode agent or a Claude --agent "
                                   "name); default: the agent you normally get")
    q = lsub.add_parser("remove")
    q.add_argument("id")
    p = sub.add_parser("member", help="people who use the board")
    esub = p.add_subparsers(dest="sub", required=True)
    esub.add_parser("list")
    q = esub.add_parser("add")
    q.add_argument("name")
    q.add_argument("--role", choices=("owner", "member"), default="member")
    q.add_argument("--ident", help="identity the proxy sends (auth.mode=header), e.g. an email")
    q.add_argument("--token", action="store_true", help="issue a sign-in token (auth.mode=token)")
    q = esub.add_parser("remove")
    q.add_argument("name")
    return ap


HANDLERS = {
    "mine": cmd_mine, "report": cmd_report, "task": cmd_task, "doing": cmd_doing, "tell": cmd_tell,
    "wrap": cmd_wrap, "decide": cmd_decide, "escalate": cmd_escalate, "cancel": cmd_cancel, "memo": cmd_memo,
    "status": cmd_status, "show": cmd_show, "items": cmd_items, "tasks": cmd_tasks, "log": cmd_log, "last": cmd_last,
    "run": cmd_run, "start": cmd_start, "stop": cmd_stop, "pause": cmd_pause, "resume": cmd_pause,
    "discover": cmd_discover, "init": cmd_init, "goal": cmd_goal, "guide": cmd_guide, "workspaces": cmd_workspaces, "install": cmd_install, "skill": cmd_skill, "lane": cmd_lane, "member": cmd_member,
    "item": lambda a: {"add": cmd_item_add, "update": cmd_item_update, "merge": cmd_item_merge}[a.sub](a),
}


def utf8_output() -> None:
    """Agents read dah's output through a pipe; on Windows that defaults to the ANSI code page, which cannot
    hold Korean or Japanese text. Board text is UTF-8 everywhere."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower().replace("-", "") != "utf8":
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> None:
    utf8_output()
    args = build_parser().parse_args(argv)
    try:
        HANDLERS[args.cmd](args)
    except (TowerError, ConfigError, StoreError) as e:
        print(f"dah: {e}", file=sys.stderr)
        sys.exit(1)
