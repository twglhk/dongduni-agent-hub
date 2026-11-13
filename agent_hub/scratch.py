"""Scratch the tower makes, and the sweep that removes it once nobody needs it.

Everything here lives under one dah-owned root outside the project: `scratch_root` in tower.json, or
`<system temp>/dah-<hash of the workspace path>`. It holds the done gate's one persistent worktree (donegate.py,
never swept) and the per-task scratch `dah scratch` hands out: a plain folder, or with `--worktree` a detached git
worktree of the lane's HEAD. Each one is written to `<root>/scratch.json` with its task id.

The sweep (every SWEEP_SECONDS, from the tower's tick) removes a manifest entry once its task is closed or cancelled
for `scratch_grace` seconds: a worktree through `git worktree remove --force` (then `prune`), a folder through rmtree.
It only ever removes paths listed in the manifest that resolve inside the root; anything else on disk, and anything a
person made, is never touched. Lane worktrees (`"worktree": true`, `.tower/state/worktrees/<lane>`) whose lane no
longer works in a worktree are removed too, but only when clean (`git worktree remove` without force; the branch
`dah/<lane>` keeps the commits). Every removal is logged with its size in `.tower/state/scratch.log`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .config import Config
    from .store import Store

DEFAULT_GRACE = 600
SWEEP_SECONDS = 120
MANIFEST = "scratch.json"
DONE_STATES = ("closed", "cancelled")


def dah_root(cfg: "Config") -> Path:
    """The folder the tower owns for gate worktrees and task scratch. Outside the project, one per workspace."""
    if cfg.scratch_root:
        p = Path(cfg.scratch_root).expanduser()
        return (p if p.is_absolute() else cfg.home / p).resolve()
    digest = hashlib.sha1(str(cfg.home.resolve()).encode("utf-8")).hexdigest()[:10]
    return Path(tempfile.gettempdir()).resolve() / f"dah-{digest}"


def check_root(root: Path, workdir: Path) -> None:
    """A gate worktree inside the project would be seen by the project's own tools (tests, search, git status)."""
    r, w = root.resolve(), workdir.resolve()
    if r == w or w in r.parents:
        raise ValueError(f"scratch_root {root} is inside the project {workdir}; pick a folder outside it")


def git(repo: Path, *args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(["git", *args], 1, "", str(e))


def dir_mb(path: Path) -> float:
    """Size on disk, walked in Python (no `du` on Windows). Links are not followed."""
    total = 0
    for base, dirs, files in os.walk(path, followlinks=False):
        for f in files:
            try:
                total += os.lstat(os.path.join(base, f)).st_size
            except OSError:
                pass
    return total / 1e6


def inside(path: Path, root: Path) -> bool:
    try:
        p, r = path.resolve(), root.resolve()
    except OSError:
        return False
    return r in p.parents


def read_manifest(root: Path) -> list[dict[str, Any]]:
    try:
        rows = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("path") and isinstance(r.get("task"), int)] \
        if isinstance(rows, list) else []


def write_manifest(root: Path, rows: list[dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    os.replace(tmp, root / MANIFEST)


def make(cfg: "Config", task_id: int, repo: Path, worktree: bool = False, clock: Callable[[], float] = time.time) -> Path:
    """A fresh scratch folder (or a detached worktree of repo's HEAD) for a task, recorded for the sweep."""
    root = dah_root(cfg)
    check_root(root, cfg.workdir)
    base = root / "scratch"
    base.mkdir(parents=True, exist_ok=True)
    n = 1
    while (base / f"t{task_id}-{n}").exists():
        n += 1
    path = base / f"t{task_id}-{n}"
    if worktree:
        p = git(repo, "worktree", "add", "--detach", str(path), "HEAD", timeout=600)
        if p.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {(p.stderr or p.stdout).strip()[:300]}")
    else:
        path.mkdir()
    rows = read_manifest(root)
    rows.append({"path": str(path), "task": int(task_id), "kind": "worktree" if worktree else "dir",
                 "repo": str(repo), "made": clock()})
    write_manifest(root, rows)
    return path


class ScratchSweep:
    def __init__(self, cfg: "Config", store: "Store", log: Callable[[str], None] = print,
                 clock: Callable[[], float] = time.time, size: Callable[[Path], float] = dir_mb) -> None:
        self.cfg, self.s, self.say, self.clock, self.size = cfg, store, log, clock, size
        self.next_at = 0.0
        self.kept: set[str] = set()  # dirty lane worktrees already reported

    def poll(self) -> list[tuple[str, float]]:
        now = self.clock()
        if now < self.next_at:
            return []
        self.next_at = now + SWEEP_SECONDS
        return self.sweep(now)

    def finished(self, now: float) -> set[int]:
        grace = self.cfg.scratch_grace
        return {t["id"] for t in self.s.tasks(DONE_STATES, limit=100000)
                if now - float(t.get("closed") or now) >= grace}

    def sweep(self, now: float) -> list[tuple[str, float]]:
        removed = self._task_scratch(now)
        removed += self._lane_leftovers()
        if removed:
            self.say(f"scratch: removed {len(removed)} leftover(s), {sum(mb for _, mb in removed) / 1000:.1f} GB")
        return removed

    def _task_scratch(self, now: float) -> list[tuple[str, float]]:
        root = dah_root(self.cfg)
        rows = read_manifest(root)
        if not rows:
            return []
        done = self.finished(now)
        keep: list[dict[str, Any]] = []
        removed: list[tuple[str, float]] = []
        repos: set[str] = set()
        for r in rows:
            path = Path(r["path"])
            if r["task"] not in done:
                keep.append(r)
                continue
            if not inside(path, root):  # never a path outside the dah root, whatever the manifest says
                self.say(f"scratch: {path} is outside {root}; dropped from the list, not removed")
                continue
            if not path.exists():
                continue
            mb = self.size(path)
            if r.get("kind") == "worktree" and r.get("repo"):
                git(Path(r["repo"]), "worktree", "remove", "--force", str(path), timeout=600)
                repos.add(r["repo"])
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
            if path.exists():
                self.say(f"scratch: could not remove {path}")
                keep.append(r)
                continue
            removed.append((str(path), mb))
            self.record(f"{r.get('kind', 'dir')} {path} task #{r['task']} {mb:.0f} MB")
        for repo in repos:
            git(Path(repo), "worktree", "prune")
        if len(keep) != len(rows):
            write_manifest(root, keep)
        return removed

    def _lane_leftovers(self) -> list[tuple[str, float]]:
        """`.tower/state/worktrees/<lane>` whose lane is gone from tower.json or no longer has "worktree": true."""
        base = self.cfg.state_dir / "worktrees"
        try:
            dirs = sorted(p for p in base.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            return []
        wanted = {lid for lid, ln in self.cfg.lanes.items() if ln.kind == "team" and ln.worktree}
        removed: list[tuple[str, float]] = []
        for p in dirs:
            if p.name in wanted:
                continue
            mb = self.size(p)
            r = git(self.cfg.workdir, "worktree", "remove", str(p), timeout=600)
            if r.returncode != 0 or p.exists():
                if str(p) not in self.kept:
                    self.kept.add(str(p))
                    self.say(f"scratch: kept lane worktree {p} (lane {p.name} no longer uses it, but it has "
                             f"uncommitted work: commit or discard it): {(r.stderr or r.stdout).strip()[:200]}")
                continue
            removed.append((str(p), mb))
            self.record(f"lane worktree {p} (branch dah/{p.name} kept) {mb:.0f} MB")
        return removed

    def record(self, line: str) -> None:
        self.say(f"scratch: removed {line}")
        try:
            self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
            with (self.cfg.state_dir / "scratch.log").open("a", encoding="utf-8") as f:
                f.write(f"{datetime.fromtimestamp(self.clock()):%Y-%m-%d %H:%M:%S} {line}\n")
        except OSError:
            pass
