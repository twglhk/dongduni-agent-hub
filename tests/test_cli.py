"""The CLI end to end in a temp workspace, the way lanes call it."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ShippedTemplatesTest(unittest.TestCase):
    def test_the_newest_shipped_text_of_each_template_is_the_current_file(self) -> None:
        from agent_hub.install import TEMPLATES, shipped
        for rel, versions in shipped().items():
            self.assertEqual(versions[-1], (TEMPLATES / rel).read_text(encoding="utf-8"),
                             f"templates/{rel} changed: append its new text to templates/shipped.json so "
                             f"`dah update` can tell untouched workspace copies from edited ones")


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        out = self.tower("init", "--hub", "opencode", "--infra", "claude", "--lang", "ko")
        self.assertIn("PROTOCOL.md", out)
        self.tower("lane", "add", "docs", "--runtime", "codex")

    def tower(self, *args: str, lane: str | None = None, ok: bool = True, env: dict[str, str] | None = None) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(ROOT), **(env or {})}
        env.pop("TOWER_LANE", None)
        if lane:
            env["TOWER_LANE"] = lane
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        if ok and p.returncode:
            self.fail(f"tower {' '.join(args)} failed: {p.stderr}")
        return p.stdout + p.stderr

    def test_a_lane_with_an_invalid_id_leaves_tower_json_untouched(self) -> None:
        path = self.home / ".tower" / "tower.json"
        before = path.read_bytes()
        out = self.tower("lane", "add", "bad id!", "--runtime", "claude", ok=False)
        self.assertIn("lane id", out)
        self.assertEqual(path.read_bytes(), before)
        self.tower("engine", "add", "bad id!", ok=False)
        self.assertEqual(path.read_bytes(), before)

    def test_files_written(self) -> None:
        for rel in (".tower/tower.json", ".tower/PROTOCOL.md", ".tower/lanes/hub.md", ".tower/lanes/infra.md",
                    ".tower/lanes/docs.md", ".opencode/command/dah-hub.md", ".claude/commands/dah-infra.md"):
            self.assertTrue((self.home / rel).is_file(), rel)
        self.assertEqual((self.home / ".gitignore").read_text().split(), [".tower/state/", ".tower/venv/"])

    def test_update_brings_an_old_workspace_up_to_date(self) -> None:
        import json
        from agent_hub.install import shipped
        versions, tower = shipped(), self.home / ".tower"
        mine = "\n## This workspace\n\n- tests: `npm test`\n"
        (tower / "lanes/hub.md").write_text(versions["lanes/hub.md"][0] + mine, encoding="utf-8")
        (tower / "lanes/infra.md").write_text(versions["lanes/infra.md"][0].replace("#", "##", 1), encoding="utf-8")
        cfg_path = tower / "tower.json"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        data["lanes"]["infra"]["args"] = ["--permission-mode", "acceptEdits"]
        cfg_path.write_text(json.dumps(data), encoding="utf-8")
        (self.home / ".claude/commands/dah-docs.md").unlink()

        out = self.tower("update", "--no-upgrade")
        hub = (tower / "lanes/hub.md").read_text(encoding="utf-8")
        self.assertEqual(hub, versions["lanes/hub.md"][-1] + mine)  # template swapped, workspace part kept
        self.assertEqual((tower / "lanes/infra.md.new").read_text(encoding="utf-8"), versions["lanes/infra.md"][-1])
        self.assertIn("asked infra to merge", out)
        self.assertEqual(json.loads(cfg_path.read_text(encoding="utf-8"))["lanes"]["infra"]["args"],
                         ["--permission-mode", "acceptEdits", "--allowedTools", "Bash(dah:*)"])
        self.assertTrue((self.home / ".claude/commands/dah-docs.md").is_file())
        self.assertFalse((tower / "lanes/docs.md.new").exists())  # untouched files stay as they are
        from agent_hub import __version__
        self.assertEqual((tower / "state/version").read_text(encoding="utf-8"), __version__)

        again = self.tower("update", "--no-upgrade")  # nothing new: no second request to infra
        self.assertNotIn("asked infra", again)
        self.assertIn("still to merge: .tower/lanes/infra.md.new", again)
        self.assertEqual((tower / "lanes/hub.md").read_text(encoding="utf-8"), hub)

    def test_every_lane_gets_a_shortcut_in_every_runtime(self) -> None:
        # docs (codex) was added after init: every lane now has /dah-<lane> in OpenCode, Claude Code and Codex
        for lane in ("hub", "infra", "docs"):
            for rel in (f".opencode/command/dah-{lane}.md", f".claude/commands/dah-{lane}.md",
                        f".agents/skills/dah-{lane}/SKILL.md"):
                text = (self.home / rel).read_text()
                self.assertIn(f".tower/lanes/{lane}.md", text, rel)
        self.assertIn("name: dah-docs", (self.home / ".agents/skills/dah-docs/SKILL.md").read_text())
        mine = self.home / ".claude/commands/dah-review.md"
        self.assertIn("/dah-review", self.tower("lane", "add", "review", "--runtime", "claude", "--agent", "reviewer"))
        mine.write_text("my own edit")  # a shortcut someone changed is theirs: removing the lane keeps it
        self.tower("lane", "remove", "review")
        self.assertEqual(mine.read_text(), "my own edit")
        self.assertFalse((self.home / ".opencode/command/dah-review.md").exists())
        self.assertFalse((self.home / ".agents/skills/dah-review").exists())

    def test_task_item_report_flow(self) -> None:
        self.assertIn("only hub", self.tower("task", "docs", "--title", "T", "b", lane="docs", ok=False))
        self.assertIn("task #1", self.tower("task", "docs", "--title", "Write docs", "brief", lane="hub"))
        (self.home / "out.md").write_text("# hi")
        out = self.tower("item", "add", "--title", "Tone?", "--body", "## What to decide\nx\n## Options\ny",
                         "--option", "Formal=f", "--option", "Casual=c", "--attach", "out.md", lane="docs")
        self.assertIn("#1 posted", out)
        self.assertIn("for hub: 1", self.tower("status").replace("open for the hub: 1", "for hub: 1"))
        self.tower("decide", "1", "ok", "--choice", "Casual", "--basis", "style guide", lane="hub")
        self.assertIn("approved (Casual)", self.tower("show", "1"))
        self.tower("report", "done", "docs written", "--attach", "out.md", lane="docs")
        self.assertIn("files must be inside", self.tower("item", "add", "--title", "x", "--body", "y", "--attach",
                                                         "/etc/hosts", lane="docs", ok=False).replace(
            "attachments must be files inside", "files must be inside"))

    def test_memo_reply_and_forward(self) -> None:
        from agent_hub.store import Store
        s = Store(self.home / ".tower/state/tower.db")
        s.push("infra", "ana", "memo", "fix")
        s.push("infra", "ana", "memo", "feature")
        self.tower("memo", "reply", "1", "fixed", lane="infra")
        self.assertIn("→ hub", self.tower("memo", "forward", "2", lane="infra"))
        self.assertIn("only infra", self.tower("memo", "forward", "2", lane="hub", ok=False))
        self.assertIn("infra only uses", self.tower("report", "done", "x", lane="infra", ok=False))

    def test_hub_memo_reply_posts_a_review_card_for_a_human_request(self) -> None:
        from agent_hub.store import Store
        s = Store(self.home / ".tower/state/tower.db")
        mid = s.push("hub", "ana", "memo", "tidy the docs")
        out = self.tower("memo", "reply", str(mid), "Tidied.", lane="hub")
        self.assertIn("review card #1", out)
        it = s.item(1)
        self.assertEqual((it["audience"], it["memo_id"], it["title"]), ("human", mid, "tidy the docs"))
        self.assertIn("review card #1", self.tower("memo", "reply", str(mid), "More.", lane="hub"))
        self.assertEqual(len(s.items()), 1)
        self.assertNotIn("review card", self.tower("memo", "reply", str(s.push("infra", "ana", "memo", "x")), "ok",
                                                    lane="infra"))

    def test_infra_takes_requests_from_the_hub_only(self) -> None:
        from agent_hub.store import Store
        s = Store(self.home / ".tower/state/tower.db")
        self.assertIn("→ infra", self.tower("infra", "add a lane docs", "--approved", "request #7", lane="hub"))
        self.assertIn("→ infra", self.tower("memo", "send", "infra", "second", lane="hub"))
        first, second = s.pending("infra")
        self.assertIn("Approved by the human: request #7", first["text"])
        self.assertNotIn("Approved", second["text"])
        self.assertIn("only the hub", self.tower("infra", "x", lane="infra", ok=False))

    def test_members_and_discover(self) -> None:
        out = self.tower("member", "add", "ana", "--role", "owner", "--token")
        token = out.split("give it privately): ")[1].strip()
        self.assertNotIn(token, self.tower("member", "list"))
        self.assertIn('"agent_clis"', self.tower("discover"))

    def test_skill_install_previews_then_writes(self) -> None:
        user = Path(tempfile.mkdtemp())
        (user / ".claude").mkdir()
        (user / ".config/opencode").mkdir(parents=True)
        env = {"HOME": str(user), "USERPROFILE": str(user)}  # USERPROFILE: the home on Windows
        out = self.tower("skill", "install", "--runtime", "claude", "--runtime", "opencode", env=env)
        self.assertIn("Nothing written", out)
        self.assertIn("+name: agent-hub-setup", out)
        skill = user / ".claude/skills/agent-hub-setup/SKILL.md"
        command = user / ".config/opencode/command/agent-hub-setup.md"
        self.assertFalse(skill.exists())
        self.tower("skill", "install", "--runtime", "claude", "--runtime", "opencode", "--yes", env=env)
        self.assertIn("dah guide`", skill.read_text())
        self.assertIn("$ARGUMENTS", command.read_text())
        self.assertIn("up to date", self.tower("skill", "install", "--runtime", "claude", env=env))

        legacy = user / ".codex/prompts/agent-hub-setup.md"  # where 0.5 and older put it; Codex 0.117 dropped prompts
        legacy.parent.mkdir(parents=True)
        legacy.write_text("---\ndescription: old\n---\nold\n")
        self.assertIn("remove (the agent no longer reads it)", self.tower("skill", "install", "--runtime", "codex", env=env))
        self.tower("skill", "install", "--runtime", "codex", "--yes", env=env)
        codex = user / ".agents/skills/agent-hub-setup/SKILL.md"
        self.assertIn("name: agent-hub-setup", codex.read_text())
        self.assertFalse(legacy.exists())

        self.assertIn("Nothing removed", self.tower("skill", "uninstall", env=env))
        self.assertTrue(codex.exists())
        self.tower("skill", "uninstall", "--yes", env=env)
        for path in (skill, command, codex):
            self.assertFalse(path.exists(), path)
        self.assertFalse((user / ".agents/skills/agent-hub-setup").exists())
        self.assertIn("no agent-hub-setup", self.tower("skill", "uninstall", env=env))

    def test_uninstall_shows_the_plan_then_takes_agent_hub_out(self) -> None:
        (self.home / ".gitignore").write_text("node_modules/\n.tower/state/\n.tower/venv/\n")
        (self.home / "AGENTS.md").write_text("# Rules\nTower lanes: see .tower/PROTOCOL.md\n")
        mine = self.home / ".claude/commands/dah-docs.md"
        mine.write_text("my own edit")
        out = self.tower("uninstall")
        self.assertIn("Nothing changed", out)
        self.assertIn("keep (edited here)", out)
        self.assertTrue((self.home / ".opencode/command/dah-hub.md").exists())
        out = self.tower("uninstall", "--yes")
        self.assertFalse((self.home / ".opencode/command/dah-hub.md").exists())
        self.assertFalse((self.home / ".agents/skills/dah-docs").exists())
        self.assertEqual(mine.read_text(), "my own edit")  # edited by someone: theirs
        self.assertEqual((self.home / ".gitignore").read_text(), "node_modules/\n")
        self.assertIn("AGENTS.md:2", out)  # a line to remove by hand
        self.assertTrue((self.home / ".tower/tower.json").exists())  # kept without --purge
        self.assertNotIn(str(self.home), self.tower("workspaces"))
        out = self.tower("uninstall", "--yes", "--purge")
        self.assertFalse((self.home / ".tower").exists(), out)

    def test_goals_are_gone_but_task_goal_prints_a_notice(self) -> None:
        self.assertIn("invalid choice: 'goal'", self.tower("goal", "list", ok=False))
        out = self.tower("task", "docs", "--goal", "2", "--title", "T", "b", lane="hub")
        self.assertIn("goals were removed in 0.8.0", out)
        self.assertIn("task #1 → docs", out)
        self.assertNotIn("goal #", out)
        self.assertNotIn("goals", self.tower("status").replace("goals were", ""))
        self.assertNotIn("serves goal", self.tower("mine", lane="docs"))

    def test_guide_and_workspaces(self) -> None:
        self.assertIn("# SETUP.md", self.tower("guide"))
        self.assertIn("tailscale serve", self.tower("guide", "tailscale"))
        self.assertIn("guides: setup", self.tower("guide", "nope", ok=False))
        out = self.tower("workspaces")
        self.assertIn(str(self.home.resolve()), out)
        other = Path(tempfile.mkdtemp())
        self.tower("init", "--home", str(other), "--hub", "claude", "--lang", "ja")
        import json
        ports = [json.loads((h / ".tower/tower.json").read_text())["web"]["port"] for h in (self.home, other)]
        self.assertNotEqual(ports[0], ports[1])
        self.assertIn("--lang takes", self.tower("init", "--home", str(other), "--force", "--lang", "../x", ok=False))
        slug = next(ln.split()[0] for ln in self.tower("workspaces").splitlines() if ln.endswith(str(other.resolve())))
        self.assertIn("removed", self.tower("workspaces", "remove", slug))


if __name__ == "__main__":
    unittest.main()


class RestartTest(unittest.TestCase):

    def setUp(self) -> None:
        from unittest import mock
        from agent_hub import cli
        from tests.helpers import make_home
        self.cli, self.cfg = cli, make_home()
        self.calls: list[str] = []
        patches = [mock.patch.object(cli, "cfg", return_value=self.cfg),
                   mock.patch.object(cli, "start_tower", side_effect=lambda c: self.calls.append("start")),
                   mock.patch.object(cli.time, "sleep")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.mock = mock

    def run_restart(self, stopped: int | None, pids: list[int | None], managed: bool = False) -> str:
        import contextlib
        import io
        out = io.StringIO()
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        marker = self.cfg.state_dir / "tower.managed"
        marker.unlink(missing_ok=True)
        if managed:
            marker.write_text("1")
        with self.mock.patch.object(self.cli, "stop_loop", return_value=stopped), \
                self.mock.patch.object(self.cli, "tower_pid", side_effect=pids + [pids[-1]] * 400), \
                contextlib.redirect_stdout(out):
            self.cli.cmd_restart(None)
        return out.getvalue()

    def test_a_running_tower_is_stopped_and_started_here(self) -> None:
        out = self.run_restart(4242, [None])
        self.assertIn("tower stopped (pid 4242); lane sessions keep running", out)
        self.assertEqual(self.calls, ["start"])

    def test_a_service_that_brings_it_back_is_left_alone(self) -> None:
        out = self.run_restart(4242, [None, None, 5151], managed=True)
        self.assertIn("tower back (pid 5151), started by its service", out)
        self.assertEqual(self.calls, [])

    def test_without_a_service_manager_it_starts_at_once_and_does_not_wait(self) -> None:
        out = self.run_restart(4242, [None])
        self.assertIn("tower stopped (pid 4242)", out)
        self.assertEqual(self.calls, ["start"])
        self.assertEqual(self.cli.time.sleep.call_count, 0)

    def test_a_service_that_does_not_bring_it_back_is_waited_for_then_started_here(self) -> None:
        self.run_restart(4242, [None], managed=True)
        self.assertEqual(self.calls, ["start"])
        self.assertEqual(self.cli.time.sleep.call_count, self.cli.RESTART_WAIT_SECONDS * 4)

    def test_a_tower_that_was_not_running_is_started_without_waiting(self) -> None:
        out = self.run_restart(None, [None])
        self.assertIn("the tower was not running", out)
        self.assertEqual(self.calls, ["start"])
        self.assertEqual(self.cli.time.sleep.call_count, 0)


class EditConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        from tests.helpers import make_home
        self.home = make_home().home
        self.path = self.home / ".tower" / "tower.json"

    def test_an_edit_that_changes_nothing_does_not_rewrite_the_file(self) -> None:
        from agent_hub.install import edit_config
        self.path.write_text('{"name":"Test","lanes":{"hub":{"runtime":"claude"}}}', encoding="utf-8")
        self.assertFalse(edit_config(self.home, lambda d: d.__setitem__("name", "Test")))
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{"name":"Test","lanes":{"hub":{"runtime":"claude"}}}')
        self.assertTrue(edit_config(self.home, lambda d: d.__setitem__("name", "Other")))
        self.assertIn('"name": "Other"', self.path.read_text(encoding="utf-8"))

    def test_an_invalid_result_is_never_written_and_no_lock_or_tmp_is_left(self) -> None:
        from agent_hub.config import ConfigError
        from agent_hub.install import edit_config
        before = self.path.read_bytes()
        with self.assertRaises(ConfigError):
            edit_config(self.home, lambda d: d["lanes"].__setitem__("bad id", {"runtime": "claude"}))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir() if p.name.startswith("tower.json")), ["tower.json"])

    def test_a_held_lock_is_waited_for_and_a_stale_one_is_removed(self) -> None:
        import os
        import threading
        import time
        from agent_hub.install import edit_config
        lock = self.path.with_name("tower.json.lock")
        lock.write_text("")
        old = time.time() - 60
        os.utime(lock, (old, old))
        edit_config(self.home, lambda d: d.__setitem__("name", "A"))
        lock.write_text("")
        threading.Timer(0.3, lock.unlink).start()
        edit_config(self.home, lambda d: d.__setitem__("name", "B"))
        self.assertIn('"B"', self.path.read_text(encoding="utf-8"))


class ClaimPidTest(unittest.TestCase):
    def setUp(self) -> None:
        from tests.helpers import make_home
        self.cfg = make_home()
        self.pid_file = self.cfg.state_dir / "tower.pid"

    def test_a_second_tower_is_refused_and_a_stale_pid_is_replaced(self) -> None:
        from agent_hub import cli
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text("999999999")
        cli.claim_pid(self.cfg)
        self.assertEqual(self.pid_file.read_text(), str(os.getpid()))
        self.assertFalse(self.pid_file.with_name("tower.pid.lock").exists())
        with self.assertRaises(cli.TowerError):
            self.pid_file.write_text(str(os.getppid()))
            cli.claim_pid(self.cfg)
        self.assertEqual(self.pid_file.read_text(), str(os.getppid()))

    def test_the_service_marker_follows_the_environment(self) -> None:
        from unittest import mock
        from agent_hub import cli
        marker = self.cfg.state_dir / "tower.managed"
        with mock.patch.dict(os.environ, {"INVOCATION_ID": "abc"}):
            cli.claim_pid(self.cfg)
        self.assertTrue(marker.exists())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("INVOCATION_ID", None)
            os.environ.pop("DAH_SERVICE", None)
            cli.claim_pid(self.cfg)
        self.assertFalse(marker.exists())


class ForceBackupTest(unittest.TestCase):
    def test_force_saves_an_edited_file_as_bak_and_leaves_an_untouched_one_alone(self) -> None:
        from agent_hub import config as config_mod
        from agent_hub.install import install
        from tests.helpers import make_home
        cfg = make_home()
        install(cfg)
        proto = cfg.home / ".tower" / "PROTOCOL.md"
        shipped = proto.read_text(encoding="utf-8")
        proto.write_text(shipped + "\nmine\n", encoding="utf-8")
        wrote = install(cfg, force=True)
        self.assertEqual(proto.read_text(encoding="utf-8"), shipped)
        self.assertEqual(proto.with_name("PROTOCOL.md.bak").read_text(encoding="utf-8"), shipped + "\nmine\n")
        self.assertIn(".tower/PROTOCOL.md.bak (your previous copy)", wrote)
        self.assertEqual([p.name for p in (cfg.home / ".tower" / "lanes").glob("*.bak")], [])
        self.assertTrue(config_mod.load(cfg.home))

    def test_force_help_says_so(self) -> None:
        p = subprocess.run([sys.executable, "-m", "agent_hub", "install", "-h"], capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.assertIn(".bak", p.stdout)


class ModelCheckTest(unittest.TestCase):
    def probe(self, code: int, out: str) -> bool:
        from unittest import mock
        from agent_hub import install
        done = mock.Mock(returncode=code, stdout=out, stderr="")
        with mock.patch.object(install.shutil, "which", return_value="/bin/claude"), \
                mock.patch.object(install.subprocess, "run", return_value=done):
            return install.model_works("claude", "fable")

    def test_only_a_clear_model_refusal_replaces_the_model(self) -> None:
        self.assertTrue(self.probe(0, "ok"))
        self.assertFalse(self.probe(1, "There's an issue with the selected model (fable). It may not exist or you may not have access to it."))
        self.assertFalse(self.probe(1, "Error: model 'fable' not found"))
        for msg in ("Could not resolve host: api.anthropic.com", "Please run /login", "API Error: 429 rate limit exceeded", ""):
            self.assertTrue(self.probe(1, msg), msg)
