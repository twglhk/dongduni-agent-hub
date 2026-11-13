"""Scratch sweep: only what the tower made (manifest entries under its root, leftover lane worktrees), after a grace."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_hub import config as config_mod
from agent_hub import scratch
from agent_hub.scratch import ScratchSweep, dah_root
from agent_hub.store import Store
from tests.helpers import Clock


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout


def workspace(lanes: dict | None = None, **extra) -> config_mod.Config:
    home = Path(tempfile.mkdtemp(prefix="scratch-ws-"))
    git(home, "init", "-q")
    (home / ".gitignore").write_text(".tower/\n", encoding="utf-8")
    (home / "a.txt").write_text("a\n", encoding="utf-8")
    git(home, "add", ".")
    git(home, "commit", "-qm", "base")
    (home / ".tower").mkdir()
    data = {"name": "T", "lanes": lanes or {"hub": {"runtime": "claude"}, "docs": {"runtime": "claude"}},
            "scratch_root": tempfile.mkdtemp(prefix="scratch-root-"), **extra}
    (home / ".tower" / "tower.json").write_text(json.dumps(data), encoding="utf-8")
    return config_mod.load(home)


class ScratchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = workspace()
        self.clock = Clock(2_000_000.0)
        self.s = Store(self.cfg.db, clock=self.clock)
        self.log: list[str] = []
        self.sweep = ScratchSweep(self.cfg, self.s, self.log.append, self.clock, size=lambda p: 12.0)

    def task(self) -> int:
        tid = self.s.add_task("docs", "t", "b")
        self.s.claim_task(tid)
        return tid

    def close(self, tid: int) -> None:
        self.s.update_task(tid, state="closed", closed=self.clock())

    def test_default_root_is_outside_the_project_and_per_workspace(self) -> None:
        cfg = workspace()
        cfg.scratch_root = ""
        root = dah_root(cfg)
        self.assertNotIn(cfg.home.resolve(), root.parents)
        self.assertTrue(root.name.startswith("dah-"))
        other = workspace()
        other.scratch_root = ""
        self.assertNotEqual(dah_root(other), root)

    def test_dir_and_worktree_are_removed_after_the_task_closes_and_the_grace(self) -> None:
        tid = self.task()
        d = scratch.make(self.cfg, tid, self.cfg.home, clock=self.clock)
        wt = scratch.make(self.cfg, tid, self.cfg.home, worktree=True, clock=self.clock)
        (d / "big.bin").write_bytes(b"x" * 10)
        self.assertTrue((wt / "a.txt").exists())
        self.assertEqual(self.sweep.sweep(self.clock()), [])  # task still running
        self.close(tid)
        self.clock.tick(scratch.DEFAULT_GRACE - 1)
        self.assertEqual(self.sweep.sweep(self.clock()), [])  # inside the grace
        self.clock.tick(2)
        removed = self.sweep.sweep(self.clock())
        self.assertEqual(sorted(p for p, _ in removed), sorted([str(d), str(wt)]))
        self.assertFalse(d.exists() or wt.exists())
        self.assertNotIn(str(wt), git(self.cfg.home, "worktree", "list"))
        self.assertEqual(scratch.read_manifest(dah_root(self.cfg)), [])
        log = (self.cfg.state_dir / "scratch.log").read_text(encoding="utf-8")
        self.assertIn(f"task #{tid}", log)
        self.assertIn("12 MB", log)  # sizes are logged

    def test_a_cancelled_task_counts_and_other_tasks_stay(self) -> None:
        a, b = self.task(), self.task()
        da = scratch.make(self.cfg, a, self.cfg.home, clock=self.clock)
        db = scratch.make(self.cfg, b, self.cfg.home, clock=self.clock)
        self.s.update_task(a, state="cancelled", closed=self.clock())
        self.clock.tick(scratch.DEFAULT_GRACE + 1)
        self.sweep.sweep(self.clock())
        self.assertFalse(da.exists())
        self.assertTrue(db.exists())
        self.assertEqual([r["task"] for r in scratch.read_manifest(dah_root(self.cfg))], [b])

    def test_never_touches_paths_it_did_not_make(self) -> None:
        tid = self.task()
        root = dah_root(self.cfg)
        root.mkdir(parents=True, exist_ok=True)
        mine = root / "scratch" / f"t{tid}-by-hand"  # named like task scratch, but not in the manifest
        mine.mkdir(parents=True)
        outside = Path(tempfile.mkdtemp(prefix="user-"))
        scratch.write_manifest(root, [{"path": str(outside), "task": tid, "kind": "dir", "made": 0}])  # a bad entry
        gate = root / "done-gate"
        gate.mkdir()
        self.close(tid)
        self.clock.tick(scratch.DEFAULT_GRACE + 1)
        self.sweep.sweep(self.clock())
        self.assertTrue(mine.exists() and outside.exists() and gate.exists())
        self.assertTrue(any("outside" in ln for ln in self.log))

    def test_poll_runs_every_interval(self) -> None:
        calls: list[float] = []
        self.sweep.sweep = lambda now: calls.append(now) or []  # type: ignore[method-assign]
        self.sweep.poll()
        self.sweep.poll()
        self.clock.tick(scratch.SWEEP_SECONDS)
        self.sweep.poll()
        self.assertEqual(len(calls), 2)


class LaneWorktreeTest(unittest.TestCase):
    def test_a_removed_lanes_clean_worktree_goes_and_a_dirty_one_stays(self) -> None:
        cfg = workspace({"hub": {"runtime": "claude"}, "docs": {"runtime": "claude", "worktree": True},
                         "web": {"runtime": "claude", "worktree": True}, "api": {"runtime": "claude", "worktree": True}})
        base = cfg.state_dir / "worktrees"
        for lid in ("docs", "web", "api"):
            git(cfg.home, "worktree", "add", "-q", "-b", f"dah/{lid}", str(base / lid))
        (base / "api" / "a.txt").write_text("uncommitted\n", encoding="utf-8")
        raw = json.loads((cfg.home / ".tower/tower.json").read_text(encoding="utf-8"))
        del raw["lanes"]["web"], raw["lanes"]["api"]  # lanes removed; their worktrees are leftovers
        (cfg.home / ".tower/tower.json").write_text(json.dumps(raw), encoding="utf-8")
        cfg = config_mod.load(cfg.home)
        log: list[str] = []
        sweep = ScratchSweep(cfg, Store(cfg.db), log.append, size=lambda p: 1.0)
        removed = sweep.sweep(0)
        self.assertEqual([Path(p).name for p, _ in removed], ["web"])
        self.assertFalse((base / "web").exists())
        self.assertTrue((base / "docs").exists())  # its lane still works there
        self.assertTrue((base / "api").exists())  # uncommitted work is never removed
        self.assertIn("dah/web", git(cfg.home, "branch"))  # the branch keeps the commits
        self.assertTrue(any("kept lane worktree" in ln for ln in log))
        sweep.sweep(0)
        self.assertEqual(len([ln for ln in log if "kept lane worktree" in ln]), 1)  # said once


class CliScratchTest(unittest.TestCase):
    def test_dah_scratch_needs_a_live_task(self) -> None:
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from unittest import mock
        from agent_hub import cli
        cfg = workspace()
        s = Store(cfg.db)
        env = {"TOWER_HOME": str(cfg.home), "TOWER_LANE": "docs"}
        with mock.patch.dict(os.environ, env), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["scratch"])
        tid = s.add_task("docs", "t", "b")
        s.claim_task(tid)
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), redirect_stdout(out):
            cli.main(["scratch"])
        p = Path(out.getvalue().strip())
        self.assertTrue(p.is_dir())
        self.assertIn(dah_root(cfg), p.parents)
        self.assertEqual(scratch.read_manifest(dah_root(cfg))[0]["task"], tid)


if __name__ == "__main__":
    unittest.main()
