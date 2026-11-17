import subprocess
import unittest
from pathlib import Path

from agent_hub.branchsync import WorktreeConflict, catch_up, sync_worktree
from agent_hub.runtimes import lane_cwd
from agent_hub.engine import Engine
from agent_hub.store import Store
from tests.helpers import Clock, FakeRuntime, make_home


def git(at: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(at), *a], check=True, capture_output=True, text=True).stdout


class BranchSyncTest(unittest.TestCase):

    extra: dict = {}

    def setUp(self) -> None:
        self.cfg = make_home({"hub": {"runtime": "claude"}, "wt": {"runtime": "claude", "worktree": True}}, **self.extra)
        self.home = self.cfg.home
        git(self.home, "init", "-q", "-b", "main")
        git(self.home, "config", "user.email", "t@t")
        git(self.home, "config", "user.name", "t")
        (self.home / ".gitignore").write_text(".tower/\n")
        (self.home / "app.txt").write_text("v1\n")
        git(self.home, "add", ".gitignore", "app.txt")
        git(self.home, "commit", "-qm", "init")
        self.lane = self.cfg.lanes["wt"]
        self.tree = self.cfg.lane_dir("wt")

    def main_commit(self, name: str, text: str) -> None:
        (self.home / name).write_text(text)
        git(self.home, "add", name)
        git(self.home, "commit", "-qm", f"main {name}")

    def test_a_branch_without_its_own_commits_is_fast_forwarded(self) -> None:
        self.assertEqual(sync_worktree(self.cfg, self.lane), "")
        self.main_commit("app.txt", "v2\n")
        self.assertEqual(sync_worktree(self.cfg, self.lane), "dah/wt fast-forwarded to main")
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), git(self.home, "rev-parse", "main"))

    def test_a_branch_with_its_own_commits_gets_a_merge_commit(self) -> None:
        sync_worktree(self.cfg, self.lane)
        (self.tree / "lane.txt").write_text("lane work\n")
        git(self.tree, "add", "lane.txt")
        git(self.tree, "commit", "-qm", "lane")
        self.main_commit("other.txt", "main work\n")
        self.assertEqual(sync_worktree(self.cfg, self.lane), "main merged into dah/wt")
        self.assertEqual((self.tree / "other.txt").read_text(), "main work\n")
        self.assertEqual(len(git(self.tree, "rev-list", "--parents", "-1", "HEAD").split()), 3)

    def test_a_conflict_is_aborted_and_leaves_the_branch_as_it_was(self) -> None:
        sync_worktree(self.cfg, self.lane)
        (self.tree / "app.txt").write_text("lane v2\n")
        git(self.tree, "commit", "-qam", "lane edit")
        before = git(self.tree, "rev-parse", "HEAD")
        self.main_commit("app.txt", "main v2\n")
        with self.assertRaises(WorktreeConflict) as cm:
            sync_worktree(self.cfg, self.lane)
        self.assertIn("main does not merge cleanly into dah/wt", str(cm.exception))
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), before)
        self.assertEqual(git(self.tree, "status", "--porcelain"), "")
        self.assertEqual((self.tree / "app.txt").read_text(), "lane v2\n")


class EngineBranchSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home({"hub": {"runtime": "claude"}, "wt": {"runtime": "claude", "worktree": True},
                              "docs": {"runtime": "claude"}}, branch_sync=True)
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda _: None, clock=self.clock, log=lambda _: None)

    def stalled(self) -> list[str]:
        return [m["text"] for m in self.s.pending("hub") if m["kind"] == "stalled"]

    def test_a_worktree_task_opens_only_after_its_branch_takes_main(self) -> None:
        self.rt.sync_conflict["wt"] = "main does not merge cleanly into dah/wt (x): CONFLICT app.txt"
        tid = self.s.add_task("wt", "Fix it", "brief")
        for _ in range(3):
            self.e.tick()
            self.clock.tick(5)
        self.assertEqual([o for o in self.rt.opened if o[0] == "wt"], [])
        self.assertEqual(self.s.task(tid)["state"], "queued")
        told = self.stalled()
        self.assertEqual(len(told), 1)
        self.assertIn(f"wt task #{tid}", told[0])
        self.assertIn("CONFLICT app.txt", told[0])
        self.rt.sync_conflict.pop("wt")
        self.e.tick()
        self.assertEqual([o[0] for o in self.rt.opened if o[0] == "wt"], ["wt"])
        self.assertEqual(self.s.task(tid)["state"], "running")

    def test_off_by_default_the_task_opens_without_a_sync(self) -> None:
        cfg = make_home({"hub": {"runtime": "claude"}, "wt": {"runtime": "claude", "worktree": True}})
        s = Store(cfg.db, clock=self.clock)
        rt = FakeRuntime(s)
        rt.sync_conflict["wt"] = "would conflict"
        s.add_task("wt", "Fix it", "brief")
        Engine(cfg, s, rt, lambda _: None, clock=self.clock, log=lambda _: None).tick()
        self.assertEqual(rt.synced, [])
        self.assertEqual([o[0] for o in rt.opened if o[0] == "wt"], ["wt"])

    def test_a_lane_in_the_workspace_is_not_synced(self) -> None:
        self.s.add_task("docs", "Write", "brief")
        self.e.tick()
        self.assertEqual(self.rt.synced, [])
        self.assertEqual([o[0] for o in self.rt.opened], ["docs"])


class CatchUpTest(unittest.TestCase):

    extra = {"branch_sync_seconds": 300, "branch_sync_push": True}

    def setUp(self) -> None:
        self.cfg = make_home({"hub": {"runtime": "claude"}, "wt": {"runtime": "claude", "worktree": True}}, **self.extra)
        self.home = self.cfg.home
        self.origin = self.home.parent / f"{self.home.name}-origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)
        git(self.home, "init", "-q", "-b", "main")
        git(self.home, "config", "user.email", "t@t")
        git(self.home, "config", "user.name", "t")
        git(self.home, "remote", "add", "origin", str(self.origin))
        (self.home / ".gitignore").write_text(".tower/\n")
        (self.home / "app.txt").write_text("v1\n")
        git(self.home, "add", ".gitignore", "app.txt")
        git(self.home, "commit", "-qm", "init")
        self.lane = self.cfg.lanes["wt"]
        self.tree = self.cfg.lane_dir("wt")
        self.clock = Clock()
        self.s = Store(self.cfg.db, clock=self.clock)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda _: None, clock=self.clock, log=lambda _: None)

    def main_commit(self, name: str, text: str) -> None:
        (self.home / name).write_text(text)
        git(self.home, "add", name)
        git(self.home, "commit", "-qm", f"main {name}")

    def head(self) -> str:
        return git(self.tree, "rev-parse", "HEAD")

    def stalled(self) -> list[str]:
        return [m["text"] for m in self.s.pending("hub") if m["kind"] == "stalled"]

    def test_a_worktree_not_made_yet_is_left_alone(self) -> None:
        self.assertEqual(catch_up(self.cfg, self.lane), "")
        self.assertFalse(self.tree.exists())

    def test_an_idle_branch_takes_main_and_is_pushed(self) -> None:
        lane_cwd(self.cfg, self.lane)
        self.main_commit("app.txt", "v2\n")
        self.assertEqual(catch_up(self.cfg, self.lane), "dah/wt fast-forwarded to main, pushed")
        self.assertEqual(git(self.origin, "rev-parse", "dah/wt"), git(self.home, "rev-parse", "main"))
        self.assertEqual(catch_up(self.cfg, self.lane), "")

    def test_uncommitted_edits_skip_the_branch(self) -> None:
        lane_cwd(self.cfg, self.lane)
        before = self.head()
        (self.tree / "draft.txt").write_text("half done\n")
        self.main_commit("other.txt", "x\n")
        self.assertIn("skipped: uncommitted edits", catch_up(self.cfg, self.lane))
        self.assertEqual(self.head(), before)

    def test_the_engine_checks_on_its_interval_and_skips_busy_sessions(self) -> None:
        lane_cwd(self.cfg, self.lane)
        before = self.head()
        self.e.tick()
        self.main_commit("app.txt", "v2\n")
        tid = self.s.add_task("wt", "Work", "brief")
        self.s.claim_task(tid)
        self.rt.st["wt"] = "busy"
        self.clock.tick(301)
        self.e.tick()
        self.assertEqual(self.head(), before)
        self.s.update_task(tid, state="closed")
        self.rt.st["wt"] = "closed"
        self.clock.tick(5)
        self.e.tick()
        self.assertEqual(self.head(), before)
        self.clock.tick(300)
        self.e.tick()
        self.assertEqual(self.head(), git(self.home, "rev-parse", "main"))
        self.assertEqual(git(self.origin, "rev-parse", "dah/wt"), self.head())

    def test_a_conflict_is_undone_and_the_hub_told_once(self) -> None:
        lane_cwd(self.cfg, self.lane)
        (self.tree / "app.txt").write_text("lane v2\n")
        git(self.tree, "commit", "-qam", "lane edit")
        before = self.head()
        self.main_commit("app.txt", "main v2\n")
        for _ in range(3):
            self.e.tick()
            self.clock.tick(301)
        self.assertEqual(self.head(), before)
        self.assertEqual(git(self.tree, "status", "--porcelain"), "")
        told = self.stalled()
        self.assertEqual(len(told), 1)
        self.assertIn("wt: the tower could not merge main", told[0])

    def test_everything_is_off_unless_tower_json_asks(self) -> None:
        self.assertEqual((self.cfg.branch_sync_seconds, self.cfg.branch_sync_push), (300, True))
        off = make_home({"hub": {"runtime": "claude"}})
        self.assertEqual((off.branch_sync, off.branch_sync_seconds, off.branch_sync_push), (False, 0, False))

    def test_without_branch_sync_push_the_branch_stays_local(self) -> None:
        lane_cwd(self.cfg, self.lane)
        self.main_commit("app.txt", "v2\n")
        self.cfg.branch_sync_push = False
        self.assertEqual(catch_up(self.cfg, self.lane), "dah/wt fast-forwarded to main")
        self.assertNotIn("dah/wt", git(self.origin, "branch", "--list"))

    def test_the_engine_does_nothing_when_the_interval_is_zero(self) -> None:
        lane_cwd(self.cfg, self.lane)
        before = self.head()
        self.main_commit("app.txt", "v2\n")
        self.cfg.branch_sync_seconds = 0
        for _ in range(2):
            self.e.tick()
            self.clock.tick(1000)
        self.assertEqual(self.head(), before)


if __name__ == "__main__":
    unittest.main()
