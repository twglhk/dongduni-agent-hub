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
        fields = stat[stat.rfind(")") + 2:].split()
        if len(fields) < 20 or not cmd:
            continue
        out.append(LaneProc(int(d.name), int(fields[1]), lane, int(fields[19]), cmd))
    return out


def _trees(procs: list[LaneProc]) -> list[list[LaneProc]]:
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
