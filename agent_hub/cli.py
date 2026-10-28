"""tower: the CLI lanes, the hub, infra and humans use. Run `tower -h` or `tower COMMAND -h`.

Who is calling comes from TOWER_LANE (set in sessions the tower opens) or `--as LANE`.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as config_mod
from .config import HUB, INFRA, RUNTIMES, Config, ConfigError
from .engine import VERDICT_WORD, Engine, clip
from .install import default_runtime, edit_config, init, install, lane_template
from .notify import notifier
from .store import HUMAN, Store, StoreError

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
              f"later (`tower show {iid}`).")
        return
    s.push(HUB, me, "item", f"Item #{iid} from {me}" + (f" task #{task['id']}" if task else "")
           + f" \"{args.title}\" → `tower show {iid}`", task["id"] if task else None, iid)
    s.signal(task["id"] if task else None)
    print(f"#{iid} posted; the hub sees it first. Don't wait: finish the unblocked scope and list #{iid} in "
          f"`tower report done`.")


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
        s.push(HUB, me, "item", f"Item #{args.id} updated by {me} → `tower show {args.id}`",
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
        raise TowerError("infra only uses `report wrapped`; answer memos with `tower memo reply`")
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
    tid = s.add_task(args.lane, args.title, args.brief)
    live = s.live_task(args.lane)
    print(f"task #{tid} → {args.lane}" + (f" (after task #{live['id']} wraps)" if live else ""))


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
        raise TowerError(f"{args.lane} has no running task; use `tower task`")
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
           f"then `tower report wrapped \"one line\"`. {args.note or ''}".strip(), task["id"])
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
        raise TowerError(f"no member {args.to} (`tower member list`)")
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


def tower_pid(c: Config) -> int | None:
    pidfile = c.state_dir / "tower.pid"
    try:
        pid = int(pidfile.read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def cmd_run(args: argparse.Namespace) -> None:
    from .runtimes import Runtimes
    from .web import server

    c = cfg()
    if tower_pid(c) and tower_pid(c) != os.getpid():
        raise TowerError(f"the tower already runs (pid {tower_pid(c)})")
    s = store(c)
    (c.state_dir / "tower.pid").write_text(str(os.getpid()))
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
            return subprocess.Popen(cmd, shell=True, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)

    try:
        while True:
            try:
                if cfg_path.stat().st_mtime != mtime:  # infra edited tower.json: pick it up without a restart
                    mtime = cfg_path.stat().st_mtime
                    c2 = config_mod.load(c.home)
                    if (c2.web_host, c2.web_port, c2.auth_mode) != (c.web_host, c.web_port, c.auth_mode):
                        say("web/auth changed in tower.json: restart the tower to apply it")
                    if c2.tunnel != c.tunnel and tunnel:
                        os.killpg(tunnel.pid, signal.SIGTERM)
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
            os.killpg(tunnel.pid, signal.SIGTERM)
        (c.state_dir / "tower.pid").unlink(missing_ok=True)


def cmd_start(_args: argparse.Namespace) -> None:
    c = cfg()
    if tower_pid(c):
        print(f"already running (pid {tower_pid(c)})")
        return
    c.state_dir.mkdir(parents=True, exist_ok=True)
    log = (c.state_dir / "tower.log").open("ab")
    proc = subprocess.Popen([sys.executable, "-m", "agent_hub", "run"], cwd=c.home, stdout=log, stderr=log,
                            stdin=subprocess.DEVNULL, start_new_session=True,
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
    if args.close_lanes:
        from .runtimes import Runtimes
        rt = Runtimes(c, store(c))
        for lid in c.lanes:
            if rt.owned(lid) and rt.status(lid) != "closed":
                rt.close(lid)
                print(f"closed {lid}")
    if not pid:
        print("the tower is not running")
        return
    os.kill(pid, signal.SIGTERM)
    print(f"tower stopped (pid {pid}). Lane sessions keep running unless you pass --close-lanes.")


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
    hub = args.hub or default_runtime()
    infra = args.infra or hub
    if not hub:
        raise TowerError("no agent CLI found (opencode, claude or codex); pass --hub/--infra")
    try:
        path = init(home, args.name, args.lang, hub, infra, args.project, args.safe, args.force)
    except FileExistsError as e:
        raise TowerError(str(e))
    wrote = install(config_mod.load(home), force=args.force)
    print(f"wrote {path}")
    for w in wrote:
        print(f"wrote {w}")
    print("next: `tower start`, then open the board")


def cmd_install(args: argparse.Namespace) -> None:
    for w in install(cfg(), force=args.force) or ["(nothing new)"]:
        print(w)


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
        edit_config(c.home, lambda d: d["lanes"].__setitem__(args.id, entry))
        c = config_mod.load(c.home)
        path = c.instructions(args.id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(lane_template(args.id), encoding="utf-8")
        print(f"lane {args.id} added ({args.runtime}). Fill in {path.relative_to(c.home)}: what it owns and its done "
              f"criteria.")
        return
    if args.id not in c.lanes:
        raise TowerError(f"no lane {args.id}")
    if store(c).live_task(args.id):
        raise TowerError(f"{args.id} has a running task; wrap or cancel it first")
    edit_config(c.home, lambda d: d["lanes"].pop(args.id))
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
    ap = argparse.ArgumentParser(prog="tower", description="Control tower and review board for AI agent lanes.")
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

    sub.add_parser("status", help="lanes, tasks and open items")
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
    p.add_argument("--lang", default="en", choices=("en", "ko"))
    p.add_argument("--hub", choices=RUNTIMES)
    p.add_argument("--infra", choices=RUNTIMES)
    p.add_argument("--project", help="folder agents work in, relative to the workspace (default: the workspace)")
    p.add_argument("--safe", action="store_true", help="sandboxed agent permissions instead of unattended ones")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("install", help="(re)write protocol, lane files and command shims")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("lane", help="list, add or remove lanes")
    lsub = p.add_subparsers(dest="sub", required=True)
    lsub.add_parser("list")
    q = lsub.add_parser("add")
    q.add_argument("id")
    q.add_argument("--runtime", choices=RUNTIMES, required=True)
    q.add_argument("--label")
    q.add_argument("--model")
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
    "discover": cmd_discover, "init": cmd_init, "install": cmd_install, "lane": cmd_lane, "member": cmd_member,
    "item": lambda a: {"add": cmd_item_add, "update": cmd_item_update, "merge": cmd_item_merge}[a.sub](a),
}


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        HANDLERS[args.cmd](args)
    except (TowerError, ConfigError, StoreError) as e:
        print(f"tower: {e}", file=sys.stderr)
        sys.exit(1)
