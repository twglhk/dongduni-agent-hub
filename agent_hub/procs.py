from __future__ import annotations

import ctypes
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from ctypes import wintypes
from typing import Any, Callable

WINDOWS = os.name == "nt"
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
TASKKILL_TIMEOUT = 15


def detached() -> dict[str, Any]:
    if WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def capture(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str] | None:
    extra: dict[str, Any] = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if WINDOWS else {}
    try:
        return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, **extra)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _kernel() -> Any:
    kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    return kernel


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    if WINDOWS:
        kernel = _kernel()
        handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _taskkill(pid: int, group: bool) -> None:
    argv = ["taskkill", "/PID", str(pid), "/F"] + (["/T"] if group else [])
    try:
        subprocess.run(argv, capture_output=True, timeout=TASKKILL_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        return


def _signal(pid: int, sig: int, group: bool) -> None:
    try:
        if group:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        if group:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                return


def terminate(pid: int | None, group: bool = True) -> None:
    if not pid:
        return
    if WINDOWS:
        _taskkill(pid, group)
        return
    _signal(pid, signal.SIGTERM, group)


def kill(pid: int | None, group: bool = True) -> None:
    if not pid:
        return
    if WINDOWS:
        _taskkill(pid, group)
        return
    _signal(pid, getattr(signal, "SIGKILL"), group)


def terminate_then_kill(pid: int | None, group: bool = True, grace: float = 10.0, now: Callable[[], float] = time.time,
         sleep: Callable[[float], None] = time.sleep) -> bool:
    if not pid:
        return True
    terminate(pid, group)
    deadline = now() + grace
    while now() < deadline:
        if reap(pid) or not alive(pid):
            return True
        sleep(0.2)
    kill(pid, group)
    sleep(0.2)
    return reap(pid) or not alive(pid)


def start_time(pid: int | None) -> str | None:
    if not pid:
        return None
    if WINDOWS:
        return _windows_start(pid)
    return _proc_stat_start(pid) or _ps_start(pid)


def _proc_stat_start(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        return stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _ps_start(pid: int) -> str | None:
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return " ".join(out.split()) or None


def _windows_start(pid: int) -> str | None:
    kernel = _kernel()
    handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return None
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def same_process(pid: int | None, started: str | None) -> bool:
    if not started:
        return True
    return start_time(pid) == started


def reap(pid: int) -> bool:
    if WINDOWS:
        return False
    try:
        return os.waitpid(pid, os.WNOHANG)[0] == pid
    except ChildProcessError:
        return False


NPM_SHIM_JS = re.compile(r'"%(?:~?dp0%?)\\?([^"%]+?\.(?:c|m)?js)"', re.I)


def command(binary: str) -> list[str]:
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
