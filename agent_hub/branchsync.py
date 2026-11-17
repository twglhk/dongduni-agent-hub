from __future__ import annotations

import subprocess
from pathlib import Path

from .config import Config, Lane
from .runtimes import RuntimeFailure, _base_branch, lane_cwd, worktree_branch


GIT_TIMEOUT_SECONDS = 20


class WorktreeConflict(RuntimeFailure):
    pass


class WorktreeDirty(WorktreeConflict):
    pass


_conflicts: dict[str, tuple[str, str, str]] = {}


def _git(path: Path, *a: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["git", "-C", path.as_posix(), *a], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise RuntimeFailure(f"git {a[0]} in {path} did not finish in {GIT_TIMEOUT_SECONDS} s")


def sync_worktree(cfg: Config, lane: Lane) -> str:
    if not lane.worktree:
        return ""
    path = lane_cwd(cfg, lane)
    base = _base_branch(cfg)
    if not base:
        return ""

    def g(*a: str) -> subprocess.CompletedProcess[str]:
        return _git(path, *a)
    key = path.as_posix()
    if g("merge-base", "--is-ancestor", base, "HEAD").returncode == 0:
        _conflicts.pop(key, None)
        return ""
    if g("status", "--porcelain").stdout.strip():
        raise WorktreeDirty(f"uncommitted edits in {path}")
    branch = worktree_branch(lane)
    marks = (g("rev-parse", base).stdout.strip(), g("rev-parse", "HEAD").stdout.strip())
    known = _conflicts.get(key)
    if known and known[:2] == marks:
        raise WorktreeConflict(known[2])
    if g("merge", "--ff-only", "--quiet", base).returncode == 0:
        return f"{branch} fast-forwarded to {base}"
    p = g("merge", "--no-edit", "--quiet", "-m", f"Merge {base} into {branch} (tower, before a task)", base)
    if p.returncode == 0:
        return f"{base} merged into {branch}"
    if g("rev-parse", "--verify", "--quiet", "MERGE_HEAD").returncode == 0:
        g("merge", "--abort")
    why = f"{base} does not merge cleanly into {branch} ({path}): {(p.stderr or p.stdout).strip()[:300]}"
    _conflicts[key] = (*marks, why)
    raise WorktreeConflict(why)


def catch_up(cfg: Config, lane: Lane) -> str:
    path = cfg.lane_dir(lane.id)
    if not lane.worktree or not (path / ".git").exists():
        return ""
    branch = worktree_branch(lane)

    def g(*a: str) -> subprocess.CompletedProcess[str]:
        return _git(path, *a)
    if g("status", "--porcelain").stdout.strip():
        return f"{branch} skipped: uncommitted edits in {path}"
    what = sync_worktree(cfg, lane)
    if not what or not cfg.branch_sync_push or g("remote", "get-url", "origin").returncode != 0:
        return what
    p = g("push", "--quiet", "origin", branch)
    return f"{what}, pushed" if p.returncode == 0 else f"{what}, push failed: {(p.stderr or p.stdout).strip()[:200]}"
