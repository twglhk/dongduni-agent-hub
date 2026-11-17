from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Callable

POLL_SECONDS = 60


@dataclass(frozen=True)
class Proc:
    pid: int
    rss_mb: int
    cmd: str


def parse_ps(text: str) -> list[Proc]:
    out: list[Proc] = []
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            out.append(Proc(int(parts[0]), int(parts[1]) // 1024, parts[2]))
    return out


def read_ps() -> list[Proc]:
    try:
        r = subprocess.run(["ps", "-axo", "pid=,rss=,command="], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_ps(r.stdout)


class MemWatch:
    def __init__(self, limit_mb: int, markers: Callable[[], dict[str, list[str]]], log: Callable[[str], None] = print,
                 ps: Callable[[], list[Proc]] = read_ps, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit_mb, self.markers, self.log, self.ps, self.clock = limit_mb, markers, log, ps, clock
        self.heavy: dict[str, int] = {}
        self.last: float | None = None

    def poll(self, force: bool = False) -> None:
        if self.limit_mb <= 0:
            self.heavy = {}
            return
        now = self.clock()
        if not force and self.last is not None and now - self.last < POLL_SECONDS:
            return
        self.last = now
        procs = self.ps()
        heavy: dict[str, int] = {}
        for lane, marks in self.markers().items():
            marks = [m for m in marks if m]
            mb = max((p.rss_mb for p in procs if any(m in p.cmd for m in marks)), default=0)
            if mb >= self.limit_mb:
                heavy[lane] = mb
        for lane, mb in heavy.items():
            if lane not in self.heavy:
                self.log(f"memwatch: {lane} session at {mb} MB, over {self.limit_mb} MB: renewed when idle")
        self.heavy = heavy
