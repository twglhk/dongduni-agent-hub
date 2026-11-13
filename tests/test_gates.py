"""The safety gates (agent_hub/gates.py): kill, git sweep, .env read, binary apply_patch. The kill and binary cases
are the source tower's own test cases, with its desk's names swapped for agent-hub's."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_hub import gates

USER, HOME = "alice", "/home/alice"


def verdict(tool: str, args: dict, cwd: str = "/tmp") -> str | None:
    r = gates.check(tool, args, cwd, USER, HOME)
    return r[0] if r else None


def kill(cmd: str) -> bool:
    return verdict("Bash", {"command": cmd}) == "kill"


class KillGate(unittest.TestCase):
    REJECT = [
        # the command that once took down the desk, the tower and every lane (BSD pkill: late options are patterns)
        'pkill -f "run_tests.py" -n -u $(whoami) 2>/dev/null; wc -l /tmp/t.log',
        'pkill -f "run_tests.py" -n',
        "pkill -f alice",
        'pkill -f "/home/alice/projects"',
        "pkill -f opencode",
        "pkill -f claude",
        "pkill -f codex",
        'pkill -f "python -m agent_hub"',
        "pkill -f dah",
        "pkill -f tmux",
        "pkill node",
        'pkill -f "."',
        "pkill -u alice",
        'killall -u "$USER"',
        "killall opencode",
        "kill -9 -1",
        "kill -- -1",
        "kill 0",
        'sleep 1 && sudo pkill -f "foo.py" -9',
        "echo ok; (cd app && pkill -f x.py -n)",
        "taskkill /IM python.exe /F",
        "taskkill /F /IM node.exe",
        "C:/Windows/System32/taskkill.exe /im claude.exe",
    ]
    PASS = [
        'pkill -n -u alice -f "run_tests.py"',
        'pkill -f "vite --port 5177"',
        "pkill -f 'ping -i 1 -c 600'",
        'pkill -f "python3 -m http.server 8000"',
        'pkill -TERM -f "webpack serve"',
        'killall "Simulator" 2>/dev/null',
        'killall -u "$USER" cfprefsd 2>/dev/null',
        "kill $PID",
        "kill -9 12345",
        "kill %1",
        'pgrep -f "run_tests.py" -n -u $(whoami)',
        'echo "pkill -f x -n" > notes.txt',
        'grep -n "kill -1" file.sh',
        "taskkill /PID 4242 /F",
        "ls  # pkill -f opencode",
    ]

    def test_rejects(self) -> None:
        for cmd in self.REJECT:
            with self.subTest(cmd):
                self.assertTrue(kill(cmd), cmd)

    def test_lets_through(self) -> None:
        for cmd in self.PASS:
            with self.subTest(cmd):
                self.assertFalse(kill(cmd), cmd)

    def test_message_says_nothing_ran_and_why(self) -> None:
        r = gates.check("Bash", {"command": "pkill -f opencode"}, "/tmp", USER, HOME)
        assert r
        self.assertIn("Kill gate refused", r[1])
        self.assertIn("Nothing ran", r[1])
        self.assertIn('contains "opencode"', r[1])

    def test_real_user_and_home_by_default(self) -> None:
        user = gates._user()
        if len(user) >= 3:
            self.assertTrue(gates.kill_gate(f"pkill -f {user}"))
        self.assertTrue(gates.kill_gate(f'pkill -f "{Path.home()}/x"'))

    def test_other_tools_and_bad_args_are_ignored(self) -> None:
        self.assertIsNone(verdict("Write", {"file_path": "/tmp/x", "command": "pkill -f opencode"}))
        self.assertIsNone(verdict("Bash", {"command": 42}))
        self.assertIsNone(gates.check("Bash", "not a dict", "/tmp"))  # type: ignore[arg-type]


class Heredocs(unittest.TestCase):
    """A heredoc body is data unless a shell reads it (audit risk: the source refused a file that only mentioned kill)."""

    def test_a_heredoc_that_writes_a_file_is_data(self) -> None:
        for cmd in ["cat > stop.sh <<'EOF'\npkill -f opencode\nkill -9 -1\ngit stash\nEOF\nchmod +x stop.sh",
                    'cat <<EOF > notes.md\nNever run `pkill -f claude`.\nEOF',
                    "python3 - <<'PY'\nimport os\nos.system('pkill -f opencode')\nPY",
                    "tee a.txt <<A >/dev/null; tee b.txt <<B\npkill node\nA\nkillall codex\nB\necho done",
                    "cat <<\"END\"\npkill -f tmux",  # never closed: still data
                    "cat <<-EOF\n\tpkill -f opencode\n\tEOF\n"]:
            with self.subTest(cmd):
                self.assertIsNone(verdict("Bash", {"command": cmd}), cmd)

    def test_commands_after_the_heredoc_are_still_checked(self) -> None:
        self.assertTrue(kill("cat > a.txt <<EOF\nhello\nEOF\npkill -f opencode"))
        self.assertTrue(kill("cat <<-EOF\n\thello\n\tEOF\nkill -9 -1"))

    def test_a_heredoc_fed_to_a_shell_is_checked(self) -> None:
        self.assertTrue(kill("bash <<'EOF'\necho hi\npkill -f opencode\nEOF"))
        self.assertTrue(kill("sudo sh -s <<EOF\nkill -9 -1\nEOF"))
        self.assertEqual(verdict("Bash", {"command": "zsh <<EOF\ngit stash\nEOF"}), "git")

    def test_shell_dash_c_and_eval_are_checked(self) -> None:
        self.assertTrue(kill('sh -c "pkill -f opencode"'))
        self.assertTrue(kill("bash -lc 'sleep 1; killall claude'"))
        self.assertTrue(kill('eval "kill -9 -1"'))
        self.assertEqual(verdict("Bash", {"command": 'bash -c "git reset --hard"'}), "git")
        self.assertFalse(kill('bash -c "echo pkill -f opencode"'))

    def test_here_strings_are_data(self) -> None:
        self.assertFalse(kill('grep -c kill <<< "pkill -f opencode"'))

    def test_parse_attaches_bodies_to_their_command(self) -> None:
        cmds = gates.parse("cat <<A; wc -l <<B\none\nA\ntwo\nB\nls")
        self.assertEqual([(c.words, c.heredocs) for c in cmds],
                         [(["cat"], ["one"]), (["wc", "-l"], ["two"]), (["ls"], [])])


class GitSweepGate(unittest.TestCase):
    def test_denied(self) -> None:
        for cmd in ["git stash", "git stash pop", "git reset --hard HEAD~1", "git checkout -- .", "git checkout .",
                    "git restore .", "git clean -fd", "cd x && git stash", "git -C /tmp/y clean -n",
                    "sudo git reset --hard", "git --no-pager stash list"]:
            with self.subTest(cmd):
                self.assertEqual(verdict("Bash", {"command": cmd}), "git", cmd)

    def test_allowed(self) -> None:
        for cmd in ["git status", "git reset HEAD file", "git checkout main", "git checkout -b topic",
                    "git restore src/app.py", "git worktree add /tmp/x HEAD", "git log --oneline", 'echo "git stash"',
                    "git diff", "git commit -m 'msg' -- a.txt"]:
            with self.subTest(cmd):
                self.assertNotEqual(verdict("Bash", {"command": cmd}), "git", cmd)

    def test_message_points_at_a_worktree(self) -> None:
        r = gates.check("Bash", {"command": "git stash"}, "/tmp", USER, HOME)
        assert r
        self.assertIn("git worktree add", r[1])


@unittest.skipUnless(shutil.which("git"), "git not installed")
class GitRepoCases(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        for name in ("a.txt", "b.txt"):
            (self.repo / name).write_text(name, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "init")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str, cwd: Path | None = None) -> None:
        subprocess.run(["git", *args], cwd=cwd or self.repo, check=True, capture_output=True)

    def test_commit_with_another_lanes_staged_deletion_is_refused(self) -> None:
        self.git("rm", "-q", "b.txt")
        r = gates.check("Bash", {"command": "git commit -m mine"}, str(self.repo), USER, HOME)
        self.assertTrue(r and r[0] == "git" and "b.txt" in r[1])
        self.assertIsNone(gates.check("Bash", {"command": "git commit -m mine -- a.txt"}, str(self.repo), USER, HOME))

    def test_commit_all_counts_unstaged_deletions(self) -> None:
        (self.repo / "b.txt").unlink()
        self.assertIsNone(gates.check("Bash", {"command": "git commit -m x"}, str(self.repo), USER, HOME))
        r = gates.check("Bash", {"command": "git commit -am x"}, str(self.repo), USER, HOME)
        self.assertTrue(r and r[0] == "git")

    def test_a_linked_worktree_belongs_to_one_lane(self) -> None:
        wt = self.tmp / "wt"
        self.git("worktree", "add", "-q", str(wt), "HEAD")
        self.assertTrue(gates.linked_worktree(str(wt / "sub")))
        self.assertFalse(gates.linked_worktree(str(self.repo)))
        self.assertIsNone(gates.check("Bash", {"command": "git stash && git reset --hard"}, str(wt), USER, HOME))
        self.assertEqual(verdict("Bash", {"command": "git stash"}, str(self.repo)), "git")
        self.assertEqual(verdict("Bash", {"command": f'git -C "{self.repo}" stash'}, str(wt)), "git")


class EnvGate(unittest.TestCase):
    def test_env_files(self) -> None:
        for p in ["/a/b/.env", ".env", "/a/.env.local", "C:\\proj\\.env.production"]:
            self.assertEqual(verdict("Read", {"file_path": p}), "env", p)
        self.assertEqual(verdict("read", {"filePath": "/a/.env"}), "env")  # OpenCode's tool
        for p in ["/a/.env.example", "/a/env.txt", "/a/.envrc.md", "/a/dotenv"]:
            self.assertIsNone(verdict("Read", {"file_path": p}), p)


CHUNK = gates.CHUNK_BYTES


class BinaryPatchGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def fixture(self, name: str, data: bytes | str) -> str:
        p = os.path.join(self.dir, name)
        Path(p).write_bytes(data if isinstance(data, bytes) else data.encode())
        return p

    @staticmethod
    def update(path: str) -> str:
        return f"*** Begin Patch\n*** Update File: {path}\n@@\n-a\n+b\n*** End Patch"

    def refused(self, patch: str) -> str | None:
        r = gates.check("apply_patch", {"patchText": patch}, self.dir)
        return r[1] if r and r[0] == "binary" else None

    def test_binary_files_are_refused(self) -> None:
        nul = self.fixture("a.bin", bytes([0x61, 0, 0x62]))
        self.assertTrue(self.refused(self.update(nul)))
        self.assertTrue(self.refused(f"*** Begin Patch\n*** Delete File: {nul}\n*** End Patch"))
        self.fixture("notes.md", bytes([0x89, 0x50, 0x4E, 0x47, 0]))
        self.assertTrue(self.refused(self.update("notes.md")))  # relative path, text extension
        self.assertTrue(self.refused(self.update(self.fixture("bad.txt", bytes([0x61, 0xC3, 0x28])))))
        self.assertTrue(self.refused(self.update(self.fixture("cut.txt", bytes([0x61, 0xED, 0x95])))))
        late = bytearray(b"a" * (CHUNK * 2 + 10))
        late[CHUNK + 5] = 0
        self.assertTrue(self.refused(self.update(self.fixture("late.dat", bytes(late)))))
        src = self.fixture("src.txt", "a\n")
        dest = self.fixture("dest.bin", bytes([0, 1]))
        self.assertTrue(self.refused(f"*** Begin Patch\n*** Update File: {src}\n*** Move to: {dest}\n@@\n-a\n+b\n*** End Patch"))

    def test_text_empty_missing_and_split_characters_pass(self) -> None:
        wide = self.fixture("wide.md", b"a" * (CHUNK - 1) + "\u00e9\u4e2d\u6587\n".encode())
        t = self.fixture("plain.txt", "hello\n")
        e = self.fixture("empty.txt", "")
        self.assertIsNone(self.refused(self.update(wide)))
        self.assertIsNone(self.refused(f"*** Begin Patch\n*** Update File: {t}\n@@\n-a\n+b\n*** Update File: {e}\n"
                                       f"*** Update File: {self.dir}/missing.txt\n*** Add File: {self.dir}/new.txt\n+x\n*** End Patch"))
        os.makedirs(os.path.join(self.dir, "adir"))
        self.assertIsNone(self.refused(self.update("adir")))

    def test_message_names_the_file_not_its_content(self) -> None:
        p = self.fixture("leak.bin", "TOPSECRET-\0-CONTENT")
        msg = self.refused(self.update(p)) or ""
        self.assertIn(p, msg)
        self.assertNotIn("TOPSECRET", msg)

    def test_only_apply_patch_is_guarded(self) -> None:
        p = self.fixture("x.bin", bytes([0, 1]))
        self.assertIsNone(gates.check("Edit", {"file_path": p, "old_string": "a", "new_string": "b"}, self.dir))
        self.assertIsNone(gates.check("apply_patch", {"patchText": 42}, self.dir))


if __name__ == "__main__":
    unittest.main()
