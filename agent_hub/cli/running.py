from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import config as config_mod, procs
from ..config import Config, ConfigError, tower_pid
from ..engine import Engine
from ..install import lock_file
from ..memwatch import MemWatch
from ..notify import notifier
from .common import REACH_HINT, TowerError, cfg, store
from .reading import lane_markers


TICK_SECONDS = 4
TUNNEL_RETRY = 30


def say(text: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {text}", flush=True)


def close_changed(rt: Any, old: Config, new: Config, say: Any) -> None:
    """A live session belongs to the adapter that started it: when tower.json changes a lane's runtime or terminal,
    close it the old way, or the new adapter would start a second agent beside it."""
    for lid, ln in old.all_lanes.items():
        nl = new.all_lanes.get(lid)
        if nl is None and ln.owner and rt.status(lid) != "closed":
            rt.close(lid)
            say(f"{lid}: its member is gone, closed its hub session")
        if nl and (nl.runtime, nl.terminal, nl.worktree) != (ln.runtime, ln.terminal, ln.worktree) \
                and rt.status(lid) != "closed":
            rt.close(lid)
            say(f"{lid}: runtime, terminal or worktree changed, closed its session; it reopens the new way")


def claim_pid(c: Config) -> None:
    c.state_dir.mkdir(parents=True, exist_ok=True)
    pid_file = c.state_dir / "tower.pid"
    lock = lock_file(pid_file)
    try:
        running = tower_pid(c)
        if running and running != os.getpid():
            raise TowerError(f"the tower already runs (pid {running})")
        pid_file.unlink(missing_ok=True)
        fd = os.open(pid_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
    finally:
        lock.unlink(missing_ok=True)
    managed = c.state_dir / "tower.managed"
    if os.environ.get("INVOCATION_ID") or os.environ.get("DAH_SERVICE"):
        managed.write_text("1", encoding="utf-8")
    else:
        managed.unlink(missing_ok=True)


def cmd_run(args: argparse.Namespace) -> None:
    from ..ext import Extensions
    from ..runtimes import Runtimes
    from ..web import server

    os.environ.pop("TOWER_LANE", None)
    c = cfg()
    claim_pid(c)
    s = store(c)
    config_mod.register(c.home)
    if not args.no_hub:
        for hub in c.hubs():
            s.set_lane(hub, enabled=1)
    ext = Extensions(c.home, say)  # one load of .tower/ext for the board and the engine
    httpd = server(c, s, say, ext)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    say(f"board http://{c.web_host}:{c.web_port}" + (f" · {c.public_url}" if c.public_url else ""))
    cfg_path = c.home / config_mod.TOWER_DIR / config_mod.CONFIG_NAME
    mtime = cfg_path.stat().st_mtime
    engine = Engine(c, s, Runtimes(c, s, ext), notifier(c, say), log=say, ext=ext)
    memwatch = MemWatch(c.memory_limit_mb, lambda: lane_markers(c, s), say)
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
                    close_changed(engine.rt, c, c2, say)
                    if not args.no_hub:
                        for hub in c2.hubs():
                            s.set_lane(hub, enabled=1)
                    c = c2
                    rebuilt = Engine(c, s, Runtimes(c, s, ext), notifier(c, say), log=say, ext=ext)
                    rebuilt.inherit(engine)
                    engine = rebuilt
                    memwatch = MemWatch(c.memory_limit_mb, lambda: lane_markers(c, s), say)
                    say("tower.json reloaded")
            except (ConfigError, OSError, ValueError, TypeError) as e:
                say(f"tower.json not reloaded: {e}")
            if c.tunnel and (tunnel is None or tunnel.poll() is not None) and time.time() - tunnel_at >= TUNNEL_RETRY:
                tunnel, tunnel_at = start_tunnel(c.tunnel), time.time()
                say(f"tunnel started: {c.tunnel}")
            try:
                if not (c.state_dir / "paused").exists():
                    memwatch.poll()
                    engine.heavy = memwatch.heavy
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
    start_tower(cfg())


def start_tower(c: Config) -> None:
    if tower_pid(c):
        print(f"already running (pid {tower_pid(c)})")
        return
    c.state_dir.mkdir(parents=True, exist_ok=True)
    log = (c.state_dir / "tower.log").open("ab")
    proc = subprocess.Popen([sys.executable, "-m", "agent_hub", "run"], cwd=c.home, stdout=log, stderr=log,
                            stdin=subprocess.DEVNULL, **procs.detached(),
                            env={**{k: v for k, v in os.environ.items() if k != "TOWER_LANE"},
                                 "TOWER_HOME": str(c.home),
                                 "PYTHONPATH": str(Path(__file__).resolve().parents[2])
                                 + os.pathsep + os.environ.get("PYTHONPATH", "")})
    for _ in range(20):
        time.sleep(0.25)
        if tower_pid(c) or proc.poll() is not None:
            break
    if not tower_pid(c):
        raise TowerError(f"the tower did not start; see {c.state_dir / 'tower.log'}")
    print(f"tower started (pid {tower_pid(c)}) · board http://{c.web_host}:{c.web_port} · log {c.state_dir / 'tower.log'}")
    if c.local_only:
        print(REACH_HINT)


def stop_loop(c: Config) -> int | None:
    pid = tower_pid(c)
    if pid:  # stop the loop first, or it reopens the hub the moment its session closes
        procs.terminate(pid, group=False)  # the loop only: lane sessions are its children on Windows
        for _ in range(40):
            if not tower_pid(c):
                break
            time.sleep(0.25)
    return pid


RESTART_WAIT_SECONDS = 15


def cmd_restart(_args: argparse.Namespace) -> None:
    c = cfg()
    managed = (c.state_dir / "tower.managed").exists()
    pid = stop_loop(c)
    print(f"tower stopped (pid {pid}); lane sessions keep running" if pid else "the tower was not running")
    for _ in range(RESTART_WAIT_SECONDS * 4 if pid and managed else 0):
        if tower_pid(c):
            print(f"tower back (pid {tower_pid(c)}), started by its service")
            return
        time.sleep(0.25)
    start_tower(c)


def cmd_stop(args: argparse.Namespace) -> None:
    c = cfg()
    pid = stop_loop(c)
    if pid:
        print(f"tower stopped (pid {pid})." + ("" if args.close_lanes else
                                               " Lane sessions keep running unless you pass --close-lanes."))
    else:
        print("the tower is not running")
    if args.close_lanes:
        from ..runtimes import Runtimes
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
