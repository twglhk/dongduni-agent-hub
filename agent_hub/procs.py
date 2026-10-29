"""Process handling that works on Linux, macOS and Windows.

POSIX: children start in their own session so a stop reaches the whole group (an agent CLI and the tools it
spawned). Windows: children start in a new process group without a console window, a stop is `taskkill /T`
(the tree), and liveness is asked of the OS instead of `os.kill(pid, 0)`, which on Windows terminates the process.
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any

WINDOWS = os.name == "nt"


def detached() -> dict[str, Any]:
    """Popen keyword arguments for a child that outlives a console and can be stopped as a group."""
    if WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    if WINDOWS:
        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate(pid: int | None, group: bool = True) -> None:
    """Ask a process (and, with group, everything it started) to stop. Missing processes are fine."""
    if not pid:
        return
    if WINDOWS:
        argv = ["taskkill", "/PID", str(pid), "/F"] + (["/T"] if group else [])
        subprocess.run(argv, capture_output=True)
        return
    try:
        if group:
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        if group:  # not a group leader (started by hand): stop the process itself
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass


def reap(pid: int) -> bool:
    """POSIX: True if pid was our finished child (now reaped). Windows has no zombies to reap."""
    if WINDOWS:
        return False
    try:
        return os.waitpid(pid, os.WNOHANG)[0] == pid
    except ChildProcessError:
        return False


NPM_SHIM_JS = re.compile(r'"%(?:~?dp0%?)\\?([^"%]+?\.(?:c|m)?js)"', re.I)


def command(binary: str) -> list[str]:
    """argv prefix that runs an agent CLI. On Windows an npm-installed CLI is a `.cmd` shim; running it goes
    through cmd.exe, which cuts arguments at newlines and mangles quotes, and prompts are multi-line. So a
    shim that wraps a Node script runs as `node <script>` instead."""
    path = shutil.which(binary) or binary
    if not path.lower().endswith((".cmd", ".bat")):
        return [path]
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [path]
    m = NPM_SHIM_JS.search(text)
    script = Path(path).parent / m.group(1).replace("\\", "/") if m else None
    node = Path(path).parent / "node.exe"
    if script and script.is_file():
        return [str(node) if node.is_file() else (shutil.which("node") or "node"), str(script)]
    return [path]
