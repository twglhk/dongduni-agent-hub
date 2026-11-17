"""Cross-platform process handling: liveness, stopping a group, npm shims on Windows."""

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_hub import procs

NPM_SHIM = r"""@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0

IF EXIST "%dp0%\node.exe" (
  SET "_prog=%dp0%\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)

endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\node_modules\@openai\codex\bin\codex.js" %*
"""


class ProcsTest(unittest.TestCase):
    def test_alive_and_terminate_a_group(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **procs.detached())
        try:
            self.assertTrue(procs.alive(child.pid))
            procs.terminate(child.pid)
            child.wait(timeout=10)
            self.assertFalse(procs.alive(child.pid))
        finally:
            if child.poll() is None:
                child.kill()

    @unittest.skipIf(procs.WINDOWS, "POSIX signals")
    def test_stop_forces_a_process_that_ignores_the_polite_request(self) -> None:
        code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('up', flush=True); time.sleep(60)"
        child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, **procs.detached())
        try:
            self.assertEqual(child.stdout.readline().strip(), "up")
            self.assertTrue(procs.terminate_then_kill(child.pid, grace=0.5))
            self.assertFalse(procs.alive(child.pid))
        finally:
            if child.poll() is None:
                child.kill()

    def test_start_time_tells_a_reused_pid_from_the_original(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            stamp = procs.start_time(child.pid)
            self.assertTrue(stamp)
            self.assertTrue(procs.same_process(child.pid, stamp))
            self.assertFalse(procs.same_process(child.pid, "another start"))
            self.assertTrue(procs.same_process(child.pid, None))
        finally:
            child.kill()
            child.wait()

    def test_taskkill_has_a_timeout_and_a_hang_does_not_raise(self) -> None:
        with mock.patch.object(procs, "WINDOWS", True), mock.patch("agent_hub.procs.subprocess.run") as run:
            run.side_effect = subprocess.TimeoutExpired("taskkill", 1)
            procs.terminate(1234)
            procs.kill(1234)
            self.assertEqual(run.call_args.kwargs["timeout"], procs.TASKKILL_TIMEOUT)

    def test_alive_without_a_pid(self) -> None:
        self.assertFalse(procs.alive(None))
        self.assertFalse(procs.alive(0))

    def test_terminate_a_process_that_is_gone(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        time.sleep(0.1)
        procs.terminate(child.pid)  # no error
        procs.terminate(None)

    def test_npm_shim_runs_its_node_script(self) -> None:
        d = Path(tempfile.mkdtemp())
        script = d / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        script.parent.mkdir(parents=True)
        script.write_text("// codex")
        (d / "codex.cmd").write_text(NPM_SHIM)
        with mock.patch("shutil.which", side_effect=lambda b: str(d / "codex.cmd") if b == "codex" else "/x/node"):
            argv = procs.command("codex")
        self.assertEqual(argv[0], "/x/node")
        self.assertEqual(Path(argv[1]).resolve(), script.resolve())

    def test_plain_binaries_and_unknown_shims_run_as_they_are(self) -> None:
        d = Path(tempfile.mkdtemp())
        (d / "odd.cmd").write_text("@echo off\r\nsomething.exe %*\r\n")
        with mock.patch("shutil.which", side_effect=lambda b: {"claude": "/bin/claude", "odd": str(d / "odd.cmd")}.get(b)):
            self.assertEqual(procs.command("claude"), ["/bin/claude"])
            self.assertEqual(procs.command("odd"), [str(d / "odd.cmd")])
            self.assertEqual(procs.command("missing"), ["missing"])


if __name__ == "__main__":
    unittest.main()
