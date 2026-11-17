from __future__ import annotations

import subprocess
from pathlib import Path

from .config import Config, Lane
from .runtimes import RuntimeFailure, _base_branch, lane_cwd, worktree_branch


class WorktreeConflict(RuntimeFailure):
    pass


def _git(path: Path, *a: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", path.as_posix(), *a], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)


def sync_worktree(cfg: Config, lane: Lane) -> str:
    if not lane.worktree:
        return ""
    path = lane_cwd(cfg, lane)
    base = _base_branch(cfg)
    if not base:
        return ""

    def g(*a: str) -> subprocess.CompletedProcess[str]:
        return _git(path, *a)
    if g("merge-base", "--is-ancestor", base, "HEAD").returncode == 0:
        return ""
    branch = worktree_branch(lane)
    if g("merge", "--ff-only", "--quiet", base).returncode == 0:
        return f"{branch} fast-forwarded to {base}"
    p = g("merge", "--no-edit", "--quiet", "-m", f"Merge {base} into {branch} (tower, before a task)", base)
    if p.returncode == 0:
        return f"{base} merged into {branch}"
    if g("rev-parse", "--verify", "--quiet", "MERGE_HEAD").returncode == 0:
        g("merge", "--abort")
    raise WorktreeConflict(f"{base} does not merge cleanly into {branch} ({path}): "
                           f"{(p.stderr or p.stdout).strip()[:300]}")


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
