"""Opt-in (`branch_sync`, `branch_sync_seconds` in tower.json). The tower merges the workspace's branch (main) into
each worktree lane session's branch: before a task opens
(engine._sync_branch; a conflict keeps the task from opening), and every few minutes while the session is idle
(engine._catch_up_branches; the branch is pushed after only when `branch_sync_push` is on)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .config import Config, Lane
from .runtimes import RuntimeFailure, _base_branch, lane_cwd, worktree_branch


class WorktreeConflict(RuntimeFailure):
    """A lane session's branch cannot take the workspace's branch in cleanly; the task is not opened."""


def _git(path: Path, *a: str) -> subprocess.CompletedProcess[str]:
    """git in `path`, which is passed in POSIX form so it also works for git on Windows."""
    return subprocess.run(["git", "-C", path.as_posix(), *a], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)


def sync_worktree(cfg: Config, lane: Lane) -> str:
    """Before a task opens in a lane session's worktree, bring its branch up to date with the workspace's branch:
    a fast-forward when the branch has nothing of its own, else a merge commit. Returns what happened ('' when there
    is nothing to do). A merge that cannot finish (a conflict, uncommitted edits in the way) is aborted, the branch
    is left as it was, and WorktreeConflict says why."""
    if not lane.worktree:
        return ""
    path = lane_cwd(cfg, lane)
    base = _base_branch(cfg)
    if not base:
        return ""

    def g(*a: str) -> subprocess.CompletedProcess[str]:
        return _git(path, *a)
    if g("merge-base", "--is-ancestor", base, "HEAD").returncode == 0:
        return ""  # already has everything on the base branch
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
    """Between tasks bring an idle lane session's branch up to date with the workspace's branch and push
    it to origin when `cfg.branch_sync_push` is on. A worktree that does not exist yet, or holds uncommitted edits, is left alone. Returns what
    happened ('' when there is nothing to do); a conflict is aborted and raised as WorktreeConflict."""
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
