from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import accounthold, config as config_mod, proccheck, procs, stats
from ..config import HUB, INFRA, Config, is_infra, tower_pid
from ..engine import block_text, clip, conflict_of, infra_waits, paths_label, team_turns
from ..fair import task_who, waiting_by_person
from ..store import HUMAN, Store, StoreError
from .common import REACH_HINT, TowerError, cfg, hhmm, need, need_team, store, verdict_text, who


def lane_markers(c: Config, s: Store) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for lid, ln in c.all_lanes.items():
        sid = s.lane(lid).get("session")
        if sid:
            out[lid] = [str(sid), f"--port {ln.port}" if ln.runtime == "opencode" and ln.port else ""]
    return out


def day_arg(day: str | None) -> float:
    if not day:
        return 0.0
    try:
        return stats.day_start(day)
    except ValueError:
        raise TowerError("--since takes a day as YYYY-MM-DD")


def cmd_stats(args: argparse.Namespace) -> None:
    s = store(cfg())
    tasks = stats.collect(s, args.lane, day_arg(args.since))
    print(stats.render(stats.summarize(tasks, args.by), args.lane, args.since))


def cmd_blame(args: argparse.Namespace) -> None:
    from .. import rating
    c = cfg()
    me = who(args, c)
    s = store(c)
    stage = rating.blame_stage(args.stage) if args.stage else None
    cause = clip(" ".join((args.cause or "").split()), 200)
    try:
        if args.confirm:
            need(args, c, HUB)
            if not args.task:
                raise TowerError("--confirm needs --task N")
            s.task(args.task)
            s.confirm_blame(args.task, me, stage, cause)
            cur = s.blames()[args.task]
            print(f"task #{args.task} blame confirmed: stage {cur['effective']}"
                  + (f" (the lane wrote {cur['stage']})" if cur["stage"] != cur["effective"] else ""))
            return
        if not stage:
            raise TowerError("name the stage, e.g. `dah blame 3 --cause 'one line'`")
        if c.is_hub(me):
            if not args.task:
                raise TowerError("the hub gives --task N (to confirm use --confirm)")
            tid = args.task
        else:
            need_team(c, me)
            live = s.live_task(me)
            tid = args.task or (live["id"] if live else None)
            if not tid:
                raise TowerError(f"{me} has no running task; give --task N")
            if s.task(tid)["lane"] != me:
                raise TowerError("that is not your task")
        s.task(tid)
        s.add_blame(tid, stage, cause, me)
    except StoreError as e:
        raise TowerError(str(e))
    print(f"task #{tid} blame: stage {stage}" + (f" · {cause}" if cause else "") + " (the hub confirms or changes it with `dah blame --confirm --task N`)")


def cmd_deliverable(args: argparse.Namespace) -> None:
    c = cfg()
    s = store(c)
    try:
        if args.outcome:
            me = need(args, c, HUB, *c.infra_sessions())
            if args.outcome == "redirected" and not (args.note or "").strip():
                raise TowerError("a redirected result needs --note with the human's own words")
            s.add_outcome(args.key, args.outcome, me, args.note or "")
            print(f"{args.key} -> {args.outcome}")
            return
        with s.conn() as con:
            tasks = [dict(r) for r in con.execute("SELECT * FROM tasks WHERE deliverable=? ORDER BY id", (args.key,))]
            items = [dict(r) for r in con.execute(
                "SELECT i.* FROM items i JOIN tasks t ON t.id=i.task_id WHERE t.deliverable=? ORDER BY i.id", (args.key,))]
        events = s.outcome_events(args.key)
    except StoreError as e:
        raise TowerError(str(e))
    if not (tasks or events):
        raise TowerError(f"no task carries the key {args.key}")
    last = events[-1]["outcome"] if events else "in progress"
    tok = lambda f: stats.mtok(sum(t[f] or 0 for t in tasks))  # noqa: E731
    print(f"{args.key} · {last} · {len(tasks)} task(s) · new {tok('tok_new')} · cache read {tok('tok_cache')} · out {tok('tok_out')} "
          f"· turns {sum(t['turns'] or 0 for t in tasks)}")
    for t in tasks:
        print(f"  task #{t['id']:<5} {t['state']:9} {clip(t['title'], 40)} {hhmm(t['created'])}"
              + (f" · row {t['line_row']} stage {t['line_stage']} {t['line_mode']}" if t["line_row"] else ""))
    for it in items:
        print(f"  item #{it['id']:<5} {it['state'] if it['state'] == 'open' else it['verdict']:9} {clip(it['title'], 40)} {hhmm(it['created'])}")
    for e in events:
        print(f"  result {hhmm(e['created'])} {e['outcome']} ({e['by']})" + (f" - {e['note']}" if e["note"] else ""))


def cmd_ratings(args: argparse.Namespace) -> None:
    from .. import engines as engines_mod
    from .. import rating
    c = cfg()
    rows: dict[str, list[dict[str, Any]]] = {}
    skills: dict[str, Path] = {}
    for eid, e in c.engines.items():
        if (args.engine and eid != args.engine) or not e.library:
            continue
        skills[eid] = engines_mod.skills_root(c.home, e.skills)
        rows[eid] = engines_mod.build(c.home / e.library, skills[eid])
    if args.engine and args.engine not in c.engines:
        raise TowerError(f"no engine {args.engine} (`dah engine list`)")
    rated = rating.attach(rows, store(c), c.home, skills)
    if args.lane:
        rated = [r for r in rated if r["lane"] == args.lane]
    print(rating.render(rated, lang=c.lang.split("-")[0], weak=args.weak))


def cmd_retros(args: argparse.Namespace) -> None:
    print(stats.render_retros(store(cfg()).retros(args.lane, day_arg(args.since))))


def cmd_status(_args: argparse.Namespace) -> None:
    c = cfg()
    s = store(c)
    pid = tower_pid(c)
    print(f"tower {'running (pid ' + str(pid) + ')' if pid else 'stopped'}"
          + (" · paused" if (c.state_dir / "paused").exists() else "") + f" · board http://{c.web_host}:{c.web_port}"
          + (f" · {c.public_url}" if c.public_url else ""))
    if c.local_only and not os.environ.get("TOWER_LANE"):
        print(REACH_HINT)
    all_queued = s.tasks(("queued",), 500)
    order = team_turns(s, {k for k, ln in c.all_lanes.items() if ln.kind == "team"}, c.fair_turns)
    for lid, ln in c.all_lanes.items():
        row = s.lane(lid)
        task = s.live_task(lid)
        queued = sorted((t for t in all_queued if t["lane"] == lid), key=lambda t: order.get(t["id"], (2, 0, t["id"])))
        if ln.base and not (task or queued or row.get("session") or row.get("active_message") is not None):
            continue
        runtime = ln.runtime + ("" if ln.terminal == "none" else f"·{ln.terminal}") + ("·wt" if ln.worktree else "")
        line = f"{lid:<10} {runtime:<13} {row.get('status') or 'closed':<7}"
        if task:
            line += f" · task #{task['id']} {task['state']} \"{clip(task['title'], 50)}\"" + (
                f" for {task['requester']}" if task.get("requester") else "")
            if c.branch_of(lid):
                line += f" · {c.branch_of(lid)}"
        if queued:
            line += f" · {len(queued)} queued"
        if is_infra(lid) and row.get("active_message") is not None:
            line += f" · message #{row['active_message']}"
        opened = sum(1 for it in s.items(state="open", lane=lid))
        if opened:
            line += f" · {opened} open items"
        held = accounthold.lane_line(s, lid, c.lang) if c.claude_accounts and not ln.base and not is_infra(lid) else None
        if held:
            line += f" · {held}"
        check = proccheck.lane_line(s, c.home, lid, c.lang) if c.process_verification and ln.kind == "team" and not ln.base else None
        if check:
            line += f" · {check}"
        print(line)
        for t in queued:
            why = conflict_of(s, t, home=c.home if c.process_verification else None, lang=c.lang) if not ln.base else None
            if why or t.get("requester"):
                print(f"  #{t['id']}" + (f" for {t['requester']}" if t.get("requester") else "")
                      + (f" waits: {why}" if why else ""))
    if c.fair_turns and all_queued:
        per = waiting_by_person(all_queued, lambda t: task_who(t) or "-")
        print("waiting tasks per person (the tower takes them in turns; `dah task bump ID` puts one first): "
              + ", ".join(f"{k} {n}" for k, n in per.items()))
    waiting = infra_waits(s, c.infra_sessions(), time.time(), c.fair_turns) if INFRA in c.lanes else []
    if waiting:
        n = len(c.infra_sessions())
        print("infra queue (`dah memo bump ID` puts one first"
              + (f"; {n} sessions at most, paths are a file lock: each session declares its own" if n > 1 else "") + "):")
        for m, b in waiting:
            why = block_text(b[0], b[1], n=n) if b else "the next free session takes it"
            print(f"  #{m['id']} {m['kind']} from {m['sender']}"
                  + (f" · paths: {paths_label(m)} · {why}" if n > 1 else ""))
    terms = {ln.terminal for ln in c.lanes.values()}
    if "tmux" in terms:
        from ..runtimes import tmux_session
        print(f"lane windows: tmux attach -t {tmux_session(c)}")
    if "orca" in terms:
        print("lane windows: Orca terminal tabs in this workspace")
    from ..hooks import gate_errors
    errors = gate_errors(c.home)
    if errors:
        print(f"gate errors: {errors} (a gate crashed and let the call through; see .tower/state/hooks/gate-errors.log)")
    hub_items = s.items(state="open", audience=HUB)
    print(f"\nopen for the hub: {len(hub_items)} · for humans: {len(s.items(state='open', audience=HUMAN))}")
    for it in hub_items[:20]:
        print(f"  #{it['id']} {it['lane']} \"{it['title']}\"")


def cmd_signal(args: argparse.Namespace) -> None:
    from ..runtimes import record_signal
    home, lane = os.environ.get("TOWER_HOME"), os.environ.get("TOWER_LANE")
    if not home or not lane:
        return
    try:
        raw = args.payload[-1] if args.payload else sys.stdin.buffer.read().decode("utf-8", errors="replace")
        payload = json.loads(raw) if raw.strip() else {}
        if isinstance(payload, dict):
            record_signal(Path(home), lane, payload)
        chained = os.environ.get("TOWER_CODEX_NOTIFY")
        if args.payload and chained:
            subprocess.Popen([*json.loads(chained), raw], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, **procs.detached())
    except (OSError, ValueError) as e:
        err = Path(home) / config_mod.TOWER_DIR / "state" / "term" / "signal.err"
        err.parent.mkdir(parents=True, exist_ok=True)
        with err.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {lane}: {e}\n")


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
        print(f"  - {o['label']}" + (" (recommended)" if o.get("recommended") else "")
              + (f": {o['desc']}" if o["desc"] else ""))
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
    from ..runtimes import Runtimes
    c = cfg()
    if args.lane not in c.all_lanes:
        raise TowerError(f"unknown lane {args.lane}")
    texts = Runtimes(c, store(c)).last_text(args.lane, args.n)
    print("\n\n---\n\n".join(texts) if texts else "(nothing yet)")
