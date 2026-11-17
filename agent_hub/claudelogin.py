from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import accounts, procs
from .runtimes.common import claude_base

CLAUDE = "claude"
PRIVATE = {accounts.CREDENTIALS, ".claude.json", "backups", "cache", "downloads", ".last-cleanup",
           ".last-update-result.json"}
URL_WAIT = 20.0
CODE_WAIT = 45.0
LOGIN_TTL = 600.0
_URL = re.compile(r"https://[^\s\x07\x1b]*oauth[^\s\x07\x1b]*")
_CODE = re.compile(r"[A-Za-z0-9_\-.~=+/]+#[A-Za-z0-9_\-.~=+/]+")
_REPROMPT = ("doesn't look like", "Paste code here")


class LoginError(Exception):
    def __init__(self, why: str, code: int = 409):
        super().__init__(why)
        self.why, self.code = why, code


@dataclass
class _Login:
    proc: subprocess.Popen[bytes]
    started: float
    url: str | None = None
    out: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)
    url_seen: threading.Event = field(default_factory=threading.Event)
    timer: threading.Timer | None = None


_PENDING: dict[str, _Login] = {}
_LOCK = threading.Lock()


def default_dir() -> Path:
    return claude_base()


def default_json() -> Path:
    return default_dir() / ".claude.json" if os.environ.get("CLAUDE_CONFIG_DIR") else Path.home() / ".claude.json"


def account_dir(member: str, root: Path | None = None) -> Path:
    if not member or not member.strip():
        raise LoginError("not_member", 403)
    return accounts.root_dir(root) / accounts.folder_name(member)


def _env(d: Path, root: Path | None = None) -> dict[str, str]:
    base = accounts.root_dir(root).resolve()
    real = d.resolve()
    if real == default_dir().resolve() or real.parent != base:
        raise LoginError("bad_folder", 500)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_CODE_"))}
    env.update(CLAUDE_CONFIG_DIR=str(real), BROWSER="true", NO_COLOR="1")
    return env


def _private_file(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    if not procs.WINDOWS:
        os.chmod(path, 0o600)


def prepare(d: Path, source_dir: Path | None = None, source_json: Path | None = None) -> None:
    source_dir, source_json = source_dir or default_dir(), source_json or default_json()
    d.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    d.mkdir(mode=0o700, exist_ok=True)
    if not procs.WINDOWS:
        os.chmod(d.parent, 0o700)
        os.chmod(d, 0o700)
        if source_dir.is_dir():
            for src in source_dir.iterdir():
                dst = d / src.name
                if src.name in PRIVATE or dst.exists() or dst.is_symlink():
                    continue
                dst.symlink_to(src)
    try:
        base = json.loads(source_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        base = {}
    cj = d / ".claude.json"
    try:
        mine = json.loads(cj.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        mine = {}
    if isinstance(base, dict):
        mine["mcpServers"] = base.get("mcpServers") or {}
        mine.setdefault("hasCompletedOnboarding", True)
        trusted = {p: {"hasTrustDialogAccepted": True} for p, v in (base.get("projects") or {}).items()
                   if isinstance(v, dict) and v.get("hasTrustDialogAccepted")}
        projects = mine.setdefault("projects", {})
        for p, v in trusted.items():
            projects.setdefault(p, {}).update(v)
    _private_file(cj, json.dumps(mine, indent=2))


def _tighten(d: Path) -> None:
    if procs.WINDOWS:
        return
    creds = d / accounts.CREDENTIALS
    if creds.is_file() and not creds.is_symlink():
        os.chmod(creds, 0o600)


def _read(lg: _Login) -> None:
    assert lg.proc.stdout is not None
    while True:
        chunk = lg.proc.stdout.read1(4096) if hasattr(lg.proc.stdout, "read1") else lg.proc.stdout.read(4096)
        if not chunk:
            break
        with lg.lock:
            lg.out = (lg.out + chunk.decode("utf-8", "replace"))[-65536:]
            if lg.url is None:
                m = _URL.search(lg.out)
                if m:
                    lg.url = m.group(0)
                    lg.url_seen.set()
    lg.url_seen.set()


def _drop(member: str, lg: _Login | None = None) -> None:
    with _LOCK:
        cur = _PENDING.get(member)
        if cur is None or (lg is not None and cur is not lg):
            return
        del _PENDING[member]
    if cur.timer:
        cur.timer.cancel()
    if cur.proc.poll() is None:
        procs.terminate(cur.proc.pid)
        try:
            cur.proc.wait(5)
        except subprocess.TimeoutExpired:
            procs.kill(cur.proc.pid)
    for pipe in (cur.proc.stdin, cur.proc.stdout):
        if pipe is not None:
            with contextlib.suppress(OSError):
                pipe.close()


def status_json(d: Path, root: Path | None) -> dict[str, Any]:
    try:
        r = subprocess.run(procs.command(CLAUDE) + ["auth", "status", "--json"], env=_env(d, root), capture_output=True,
                           timeout=15, cwd=str(d))
        j = json.loads(r.stdout or b"{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    return j if isinstance(j, dict) else {}


def status(member: str, root: Path | None = None) -> dict[str, Any]:
    d = account_dir(member, root)
    with _LOCK:
        lg = _PENDING.get(member)
    if lg is not None and lg.proc.poll() is None:
        return {"state": "pending", "url": lg.url, "since": lg.started}
    if lg is not None:
        _drop(member, lg)
    if not (d / accounts.CREDENTIALS).is_file():
        return {"state": "none"}
    j = status_json(d, root)
    keep = {"email": j.get("email"), "org": j.get("orgName"), "plan": j.get("subscriptionType"),
            "expires": j.get("loginExpiresAt")}
    return {"state": "connected" if j.get("loggedIn") else "expired", **{k: v for k, v in keep.items() if v}}


def start(member: str, email: str | None, root: Path | None = None) -> dict[str, Any]:
    d = account_dir(member, root)
    with _LOCK:
        lg = _PENDING.get(member)
    if lg is not None and lg.proc.poll() is None:
        return status(member, root)
    if lg is not None:
        _drop(member, lg)
    if status(member, root)["state"] == "connected":
        raise LoginError("already_connected")
    try:
        prepare(d)
    except OSError:
        raise LoginError("failed", 500) from None
    cmd = procs.command(CLAUDE) + ["auth", "login", "--claudeai"] + (["--email", email] if email and "@" in email else [])
    try:
        proc = subprocess.Popen(cmd, env=_env(d, root), cwd=str(d), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, **procs.detached())
    except OSError:
        raise LoginError("no_cli", 500) from None
    lg = _Login(proc, time.time())
    with _LOCK:
        _PENDING[member] = lg
    threading.Thread(target=_read, args=(lg,), daemon=True).start()
    lg.timer = threading.Timer(LOGIN_TTL, _drop, (member, lg))
    lg.timer.daemon = True
    lg.timer.start()
    lg.url_seen.wait(URL_WAIT)
    if not lg.url:
        _drop(member, lg)
        raise LoginError("no_url", 502)
    return {"state": "pending", "url": lg.url, "since": lg.started}


def submit(member: str, code: str, root: Path | None = None) -> dict[str, Any]:
    code = (code or "").strip()
    with _LOCK:
        lg = _PENDING.get(member)
    if lg is None or lg.proc.poll() is not None:
        if lg is not None:
            _drop(member, lg)
        raise LoginError("no_login")
    if len(code) > 2048 or not _CODE.fullmatch(code):
        raise LoginError("bad_code", 400)
    with lg.lock:
        mark = len(lg.out)
    assert lg.proc.stdin is not None
    try:
        lg.proc.stdin.write(code.encode() + b"\n")
        lg.proc.stdin.flush()
    except OSError:
        _drop(member, lg)
        raise LoginError("failed", 502) from None
    del code
    end = time.time() + CODE_WAIT
    while time.time() < end and lg.proc.poll() is None:
        with lg.lock:
            fresh = lg.out[mark:]
        if any(x in fresh for x in _REPROMPT):
            raise LoginError("bad_code", 400)
        time.sleep(0.1)
    ok = lg.proc.poll() == 0
    _drop(member, lg)
    if not ok:
        raise LoginError("failed", 502)
    _tighten(account_dir(member, root))
    out = status(member, root)
    if out["state"] != "connected":
        raise LoginError("failed", 502)
    return out


def cancel(member: str, root: Path | None = None) -> dict[str, Any]:
    account_dir(member, root)
    _drop(member)
    return status(member, root)


def _logout(d: Path, root: Path | None) -> bool:
    try:
        subprocess.run(procs.command(CLAUDE) + ["auth", "logout"], env=_env(d, root), capture_output=True, timeout=30, cwd=str(d))
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True


def disconnect(member: str, root: Path | None = None) -> dict[str, Any]:
    d = account_dir(member, root)
    _drop(member)
    if d.is_symlink():
        raise LoginError("bad_folder", 500)
    if d.is_dir():
        _logout(d, root)
        shutil.rmtree(d)
    return {"state": "none"}
