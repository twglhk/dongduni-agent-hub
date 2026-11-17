import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        self.assertEqual(self.sweep.sweep(self.clock()), [])
        self.close(tid)
        self.clock.tick(scratch.DEFAULT_GRACE - 1)
        self.assertEqual(self.sweep.sweep(self.clock()), [])
        self.clock.tick(2)
        removed = self.sweep.sweep(self.clock())
        self.assertEqual(sorted(p for p, _ in removed), sorted([str(d), str(wt)]))
        self.assertFalse(d.exists() or wt.exists())
        self.assertNotIn(str(wt), git(self.cfg.home, "worktree", "list"))
        self.assertEqual(scratch.read_manifest(dah_root(self.cfg)), [])
        log = (self.cfg.state_dir / "scratch.log").read_text(encoding="utf-8")
        self.assertIn(f"task #{tid}", log)
        self.assertIn("12 MB", log)

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
        mine = root / "scratch" / f"t{tid}-by-hand"
        mine.mkdir(parents=True)
        outside = Path(tempfile.mkdtemp(prefix="user-"))
        scratch.write_manifest(root, [{"path": str(outside), "task": tid, "kind": "dir", "made": 0}])
        gate = root / "done-gate"
        gate.mkdir()
        self.close(tid)
        self.clock.tick(scratch.DEFAULT_GRACE + 1)
        self.sweep.sweep(self.clock())
        self.assertTrue(mine.exists() and outside.exists() and gate.exists())
        self.assertTrue(any("outside" in ln for ln in self.log))

    def test_poll_runs_every_interval(self) -> None:
        calls: list[float] = []
        with mock.patch.object(self.sweep, "sweep", lambda now: calls.append(now) or []):
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
        del raw["lanes"]["web"], raw["lanes"]["api"]
        (cfg.home / ".tower/tower.json").write_text(json.dumps(raw), encoding="utf-8")
        cfg = config_mod.load(cfg.home)
        log: list[str] = []
        sweep = ScratchSweep(cfg, Store(cfg.db), log.append, size=lambda p: 1.0)
        removed = sweep.sweep(0)
        self.assertEqual([Path(p).name for p, _ in removed], ["web"])
        self.assertFalse((base / "web").exists())
        self.assertTrue((base / "docs").exists())
        self.assertTrue((base / "api").exists())
        self.assertIn("dah/web", git(cfg.home, "branch"))
        self.assertTrue(any("kept lane worktree" in ln for ln in log))
        sweep.sweep(0)
        self.assertEqual(len([ln for ln in log if "kept lane worktree" in ln]), 1)

    def test_instance_worktrees_stay_while_the_lane_may_run_them(self) -> None:
        cfg = workspace({"hub": {"runtime": "claude"}, "docs": {"runtime": "claude", "worktree": True,
                                                                "max_instances": 3}})
        base = cfg.state_dir / "worktrees"
        for d in ("docs", "docs-2", "docs-3"):
            git(cfg.home, "worktree", "add", "-q", "-b", f"dah/{d}", str(base / d))
        raw = json.loads((cfg.home / ".tower/tower.json").read_text(encoding="utf-8"))
        raw["lanes"]["docs"]["max_instances"] = 2
        (cfg.home / ".tower/tower.json").write_text(json.dumps(raw), encoding="utf-8")
        cfg = config_mod.load(cfg.home)
        removed = ScratchSweep(cfg, Store(cfg.db), lambda _: None, size=lambda p: 1.0).sweep(0)
        self.assertEqual([Path(p).name for p, _ in removed], ["docs-3"])
        self.assertTrue((base / "docs").exists() and (base / "docs-2").exists())
        self.assertIn("dah/docs-3", git(cfg.home, "branch"))


class ScratchRootTest(unittest.TestCase):
    def test_default_root_is_under_the_user_cache_dir(self) -> None:
        cache = Path(tempfile.mkdtemp(prefix="cache-"))
        cfg = workspace()
        cfg.scratch_root = ""
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(cache), "LOCALAPPDATA": str(cache)}), \
                mock.patch("agent_hub.scratch.sys.platform", "linux"):
            root = dah_root(cfg)
        self.assertIn(cache.resolve(), root.parents)

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_the_root_is_made_private(self) -> None:
        cfg = workspace()
        root = scratch.secure_root(cfg)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_a_symlinked_root_is_refused(self) -> None:
        cfg = workspace()
        target = Path(tempfile.mkdtemp())
        link = Path(tempfile.mkdtemp()) / "link"
        link.symlink_to(target)
        cfg.scratch_root = str(link)
        with self.assertRaises(ValueError):
            scratch.secure_root(cfg)
        with self.assertRaises(ValueError):
            scratch.make(cfg, 1, cfg.home)
        self.assertEqual(list(target.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "POSIX owners")
    def test_a_root_that_belongs_to_someone_else_is_refused(self) -> None:
        cfg = workspace()
        with mock.patch("agent_hub.scratch.os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(ValueError):
                scratch.secure_root(cfg)

    def test_the_sweep_keeps_an_entry_made_while_it_was_removing_others(self) -> None:
        cfg = workspace()
        clock = Clock(2_000_000.0)
        s = Store(cfg.db, clock=clock)
        a = s.add_task("docs", "t", "b")
        s.claim_task(a)
        da = scratch.make(cfg, a, cfg.home, clock=clock)
        s.update_task(a, state="closed", closed=clock())
        clock.tick(scratch.DEFAULT_GRACE + 1)
        newer: list[Path] = []
        real = shutil.rmtree

        def rmtree_and_add(path, *args, **kw):
            real(path, *args, **kw)
            if not newer:
                newer.append(scratch.make(cfg, 99, cfg.home, clock=clock))
        sweep = ScratchSweep(cfg, s, lambda _: None, clock, size=lambda p: 1.0)
        with mock.patch("agent_hub.scratch.shutil.rmtree", rmtree_and_add):
            sweep.sweep(clock())
        self.assertFalse(da.exists())
        self.assertEqual([r["task"] for r in scratch.read_manifest(dah_root(cfg))], [99])


class CliScratchTest(unittest.TestCase):
    def test_dah_scratch_needs_a_live_task(self) -> None:
        import io
        from contextlib import redirect_stderr, redirect_stdout
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
