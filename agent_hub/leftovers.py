"""Lane processes left behind by closed sessions (opt-in: `leftover_sweep` in tower.json).

A lane session is an agent CLI the tower starts with TOWER_HOME and TOWER_LANE in its environment; everything it
starts (shells, tools, servers run in the background) inherits them. Closing a session closes its window or stops
its process, but a process can outlive that: a CLI that ignored the close, or a background job whose parent died.
`scan` reads the process table (Linux /proc; elsewhere it finds nothing and the check does nothing) and `stale`
picks the process trees that belong to no live session:

- the lane is not in tower.json any more, or has no session now (closed);
- the tree's top process names a session id (`--session-id X` / `--resume X`) other than the lane's current one;
- it names none (a job whose session process died) and started before the lane's current session process.

The engine stops a tree only when it is found on two checks in a row, so a session that is opening or closing at
that moment is left alone. Linux only: it needs /proc; on macOS and Windows `scan` finds nothing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

SID = re.compile(r"--(?:session-id|resume)[ =]([0-9A-Za-z_-]{8,})")


@dataclass(frozen=True)
class LaneProc:
    pid: int
    ppid: int
    lane: str
    start: int  # clock ticks after boot (/proc/<pid>/stat field 22): only compared with each other
    cmd: str

    @property
    def sid(self) -> str | None:
        m = SID.search(self.cmd)
        return m.group(1) if m else None


def _env(pid: str) -> dict[str, str]:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {}
    out = {}
    for part in raw.split(b"\0"):
        k, _, v = part.partition(b"=")
        if k in (b"TOWER_HOME", b"TOWER_LANE"):
            out[k.decode()] = v.decode("utf-8", errors="replace")
    return out


def scan(home: Path) -> list[LaneProc]:
    """Every process of this user whose environment says it belongs to a lane of the tower at `home`."""
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    home_s, me, out = str(Path(home).resolve()), os.getpid(), []
    for d in proc.iterdir():
        if not d.name.isdigit() or int(d.name) == me:
            continue
        env = _env(d.name)
        lane = env.get("TOWER_LANE")
        if not lane or not env.get("TOWER_HOME") or str(Path(env["TOWER_HOME"]).resolve()) != home_s:
            continue
        try:
            stat = (d / "stat").read_text()
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
        except OSError:
            continue
        fields = stat[stat.rfind(")") + 2:].split()  # after "pid (comm) ": state ppid pgrp ... starttime is the 20th
        if len(fields) < 20 or not cmd:
            continue  # a zombie has no command line: nothing to stop
        out.append(LaneProc(int(d.name), int(fields[1]), lane, int(fields[19]), cmd))
    return out


def _trees(procs: list[LaneProc]) -> list[list[LaneProc]]:
    """Each top process (its parent is not a process of the same lane) with everything under it, top first."""
    by_pid = {p.pid: p for p in procs}
    kids: dict[int, list[LaneProc]] = {}
    for p in procs:
        kids.setdefault(p.ppid, []).append(p)
    out = []
    for p in procs:
        parent = by_pid.get(p.ppid)
        if parent is not None and parent.lane == p.lane:
            continue
        tree, todo = [], [p]
        while todo:
            q = todo.pop(0)
            tree.append(q)
            todo += [k for k in kids.get(q.pid, []) if k.lane == p.lane]
        out.append(tree)
    return out


def stale(procs: list[LaneProc], current: dict[str, str | None]) -> list[tuple[list[LaneProc], str]]:
    """(tree, why) for each tree that belongs to no live session. `current`: every lane the tower runs -> its session
    id (None = closed)."""
    trees = _trees(procs)
    live_start: dict[str, int] = {}
    for t in trees:
        root = t[0]
        if root.sid and current.get(root.lane) == root.sid:
            live_start[root.lane] = min(live_start.get(root.lane, root.start), root.start)
    out = []
    for t in trees:
        reason = why(t[0], current, live_start)
        if reason:
            out.append((t, reason))
    return out


def why(root: LaneProc, current: dict[str, str | None], live_start: dict[str, int] | None = None) -> str:
    """Why this top process is left over ('' = it is not)."""
    if root.lane not in current:
        return "lane no longer in tower.json"
    cur = current[root.lane]
    if not cur:
        return "lane session closed"
    if root.sid:
        return f"old session {root.sid[:8]} (now {cur[:8]})" if root.sid != cur else ""
    if live_start is not None and root.lane in live_start and root.start < live_start[root.lane]:
        return "started by an earlier session"
    return ""
