from __future__ import annotations

import subprocess
from pathlib import Path

from ..config import Config, Lane, lane_branch
from .common import RuntimeFailure


def _git(cfg: Config, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["git", "-C", str(cfg.workdir), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
    except FileNotFoundError:
        raise RuntimeFailure("git not found: a lane with \"worktree\": true needs git")
    except subprocess.TimeoutExpired:
        raise RuntimeFailure(f"git {args[0]} in {cfg.workdir} did not finish in 60 s")


def worktree_branch(lane: Lane) -> str:
    return lane_branch(lane.id)


def lane_cwd(cfg: Config, lane: Lane) -> Path:
    """Where the lane's agent runs, ready to use: the workspace, or the session's own git worktree (created on first
    use on branch dah/<lane> or dah/<lane>-N, from the workspace's current commit; an existing branch is checked out
    as it is)."""
    path = cfg.lane_dir(lane.id)
    if not lane.worktree or (path / ".git").exists():
        return path
    if _git(cfg, "rev-parse", "--is-inside-work-tree").returncode != 0:
        raise RuntimeFailure(f"{lane.id}: \"worktree\": true needs {cfg.workdir} to be a git repository")
    _git(cfg, "worktree", "prune")
    branch = worktree_branch(lane)
    have = _git(cfg, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    p = _git(cfg, "worktree", "add", str(path), branch) if have else _git(cfg, "worktree", "add", "-b", branch, str(path))
    if p.returncode != 0:
        raise RuntimeFailure(f"{lane.id}: git worktree add failed: {(p.stderr or p.stdout).strip()[:300]}")
    return path


def _base_branch(cfg: Config) -> str:
    p = _git(cfg, "rev-parse", "--abbrev-ref", "HEAD")
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() not in ("", "HEAD") else ""


def worktree_note(cfg: Config, lane: Lane) -> str:
    """What a lane in its own worktree is told before its first message."""
    base = _base_branch(cfg)
    if base and cfg.branch_sync:
        sync = f"The tower merged `{base}` into it before this task. Commit"
    else:
        sync = (f"Bring it up to date with `{base}` first (`git merge {base}`), then commit" if base else "Commit")
    return (f"(You work in a git worktree of your own, `{cfg.lane_dir(lane.id)}`, on branch `{worktree_branch(lane)}`. "
            f"{sync} your work there and name the branch and commits in your report. It reaches the main checkout "
            f"only when the hub or a person merges it.)\n\n")
