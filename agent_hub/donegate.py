"""Done gate: a task whose commits touch a configured path must pass the project's command before its `done` reaches the hub.

tower.json:
  "done_gate": {"paths": ["src", "tests"],          # commits since the task started under these -> gate (empty = any commit)
                "command": "python -m pytest -q",   # string (shell) or argv list; exit 0 = pass
                "setup": "...", "verify": "...",    # optional: prepare the tree, then check it is ready (up to 3 tries)
                "timeout": 1800, "memory_mb": 8192, # seconds per step; ceiling on the command's process tree (0 = off)
                "sparse": ["src", "tests"]}         # optional sparse checkout of the gate worktree

`dah report done` with such commits does not report: it records the report as pending, starts the gate in the
background and returns at once. The tower's tick delivers the verdict: pass -> the done goes to the hub as usual; fail ->
a tell to the lane with the last lines and the log path, and the lane fixes, commits and sends `report done` again.
While a gate is pending the nudger treats the lane as busy.

How the gate runs (it never makes a fresh full checkout per report):
- ONE persistent worktree, `<dah root>/done-gate` (scratch.dah_root: outside the project), reused for every gate and
  never swept. Each gate does `checkout --detach -f <sha>` and `clean -ffd` (ignored build caches stay). Objects are the
  repository's own (a git worktree shares the object store; Git LFS objects come from the shared `.git/lfs`).
- ONE gate at a time: a lock folder holding the runner's pid; a dead holder's lock is taken over.
- `setup` then `verify` before the command; the command does not run until verify passes (setup is retried up to
  SETUP_TRIES times). Without `verify`, setup's exit code is the check.
- Every step has the timeout; the command's process tree is stopped when its memory passes `memory_mb` (POSIX `ps`; on
  Windows the ceiling is not enforced and the log says so).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from . import procs
from .scratch import check_root, dah_root, git

if TYPE_CHECKING:
    from .config import Config

DEFAULT_TIMEOUT = 1800
DEFAULT_MEMORY_MB = 8192
SETUP_TRIES = 3
TAIL = 6
POLL = 1.0
LOCK_STALE = 60          # a lock folder with no pid file this old is taken over
NO_PID_GRACE = 120       # a pending gate whose runner pid was never written is failed after this


def parse(raw: Any) -> dict[str, Any]:
    """tower.json `done_gate` -> normalised dict ({} = no gate). Raises ValueError on a bad shape."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("done_gate must be an object with at least `command`")

    def cmd(key: str) -> str | list[str] | None:
        v = raw.get(key)
        if v in (None, "", []):
            return None
        if isinstance(v, str):
            return v
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            return list(v)
        raise ValueError(f"done_gate.{key} must be a string or a list of strings")

    command = cmd("command")
    if command is None:
        raise ValueError("done_gate needs `command`")
    paths = raw.get("paths") or []
    sparse = raw.get("sparse") or []
    if not isinstance(paths, list) or not isinstance(sparse, list):
        raise ValueError("done_gate.paths and done_gate.sparse are lists of folders")
    clean = lambda xs: [str(p).strip().replace("\\", "/").strip("/") for p in xs if str(p).strip().strip("/\\")]
    try:
        timeout = int(raw["timeout"]) if raw.get("timeout") is not None else DEFAULT_TIMEOUT
        memory = int(raw["memory_mb"]) if raw.get("memory_mb") is not None else DEFAULT_MEMORY_MB
    except (TypeError, ValueError):
        raise ValueError("done_gate.timeout and done_gate.memory_mb are whole numbers")
    if timeout <= 0 or memory < 0:
        raise ValueError("done_gate.timeout must be over 0 and memory_mb 0 (off) or more")
    return {"paths": clean(paths), "command": command, "setup": cmd("setup"), "verify": cmd("verify"),
            "timeout": timeout, "memory_mb": memory, "sparse": clean(sparse)}


# ---------------------------------------------------------------- report side (`dah report done`)

def gate_dir(cfg: "Config") -> Path:
    return cfg.state_dir / "donegate"


def head(repo: Path) -> str:
    p = git(repo, "rev-parse", "HEAD")
    return p.stdout.strip() if p.returncode == 0 else ""


def changed(repo: Path, started: float | None, paths: list[str]) -> list[str]:
    """Files under `paths` that commits since `started` touched ([] when none, no start time or no git)."""
    if not started:
        return []
    p = git(repo, "log", f"--since=@{int(started)}", "--name-only", "--format=", "--", *(paths or ["."]))
    return sorted({ln.strip() for ln in p.stdout.splitlines() if ln.strip()}) if p.returncode == 0 else []


def applies(cfg: "Config", task: dict[str, Any] | None, lane: str) -> bool:
    if not cfg.done_gate or not task:
        return False
    return bool(changed(cfg.lane_dir(lane), task.get("started"), cfg.done_gate["paths"]))


def pending(cfg: "Config", task_id: int) -> list[Path]:
    d = gate_dir(cfg)
    return sorted(d.glob(f"{task_id}-*.pending")) if d.exists() else []


def running(cfg: "Config", task_id: int) -> bool:
    return bool(pending(cfg, task_id))


def start(cfg: "Config", task: dict[str, Any], lane: str, text: str, atts: list[str],
          spawn: Callable[[list[str], Path], int] | None = None) -> str:
    """Remember the report, start the gate in the background, and return what to tell the lane."""
    repo = cfg.lane_dir(lane)
    sha = head(repo)
    if not sha:
        raise RuntimeError(f"cannot read HEAD in {repo}")
    check_root(dah_root(cfg), cfg.workdir)
    old = pending(cfg, task["id"])
    if old:
        return (f"A done gate for this task is already running ({old[0].stem.split('-', 1)[1][:8]}). Its verdict "
                "comes to this session; send `dah report done` again after it if you have changed more. You may end your turn.")
    d = gate_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{task['id']}-{sha[:10]}"
    pend, res, log = d / f"{stem}.pending", d / f"{stem}.json", d / f"{stem}.log"
    res.unlink(missing_ok=True)
    info = {"task": task["id"], "lane": lane, "text": text, "atts": atts, "sha": sha, "at": time.time(), "log": str(log)}
    pend.write_text(json.dumps(info), encoding="utf-8")
    argv = [sys.executable, "-m", "agent_hub.donegate", str(cfg.home), sha, str(res), str(log)]
    try:
        info["pid"] = (spawn or _spawn)(argv, cfg.home)
    except OSError as e:
        pend.unlink(missing_ok=True)
        raise RuntimeError(f"could not start the done gate: {e}")
    pend.write_text(json.dumps(info), encoding="utf-8")
    return (f"Report received. The done gate runs `{_show(cfg.done_gate['command'])}` on a clean worktree of "
            f"{sha[:8]} (one gate at a time). Pass: the done goes to the hub. Fail: the last lines and the log path come "
            "to this session. Only committed work is tested. You may end your turn.")


def _show(cmd: str | list[str]) -> str:
    return cmd if isinstance(cmd, str) else " ".join(cmd)


def _spawn(argv: list[str], cwd: Path) -> int:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(Path(__file__).resolve().parent.parent),
                                                                    os.environ.get("PYTHONPATH", "")]))}
    p = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env=env, **procs.detached())
    return p.pid


# ---------------------------------------------------------------- tower side (tick)

def tail(log: Path, n: int = TAIL) -> list[str]:
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln for ln in lines if ln.strip()][-n:]


def poll(cfg: "Config", alive: Callable[[int | None], bool] | None = None,
         now: Callable[[], float] = time.time) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Finished gates as (pending info, result); both files are consumed. A runner that died without a result is a fail."""
    alive = alive or procs.alive
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    d = gate_dir(cfg)
    for pend in (sorted(d.glob("*.pending")) if d.exists() else []):
        res = pend.with_suffix(".json")
        try:
            info = json.loads(pend.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pend.unlink(missing_ok=True)
            continue
        if res.exists():
            try:
                r = json.loads(res.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # still being written
            res.unlink(missing_ok=True)
        else:
            pid = info.get("pid")
            gone = (not alive(pid)) if pid else now() - float(info.get("at") or 0) > NO_PID_GRACE
            if not gone:
                continue
            log = Path(info.get("log") or "")
            r = {"state": "fail", "sha": info.get("sha"), "log": str(log),
                 "why": "the gate runner stopped without a verdict", "tail": tail(log)}
        pend.unlink(missing_ok=True)
        out.append((info, r))
    return out


def refusal(info: dict[str, Any], r: dict[str, Any]) -> str:
    why = f" ({r['why']})" if r.get("why") else ""
    lines = "\n".join(r.get("tail") or []) or "(no output)"
    return (f"Done not accepted: the done gate failed on a clean worktree of {str(info.get('sha', ''))[:8]}{why}. "
            f"Last lines:\n{lines}\nLog: {r.get('log')}\nFix it, commit, and send `dah report done` again.")


# ---------------------------------------------------------------- runner (a background process)

def tree_mb(pid: int) -> int | None:
    """RSS of pid and its descendants in MB; None where `ps` cannot tell (Windows)."""
    if procs.WINDOWS:
        return None
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    kids: dict[int, list[int]] = {}
    rss: dict[int, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and all(x.isdigit() for x in parts):
            p, pp, kb = map(int, parts)
            kids.setdefault(pp, []).append(p)
            rss[p] = kb
    if pid not in rss:
        return None
    total, todo, seen = 0, [pid], set()
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        total += rss.get(p, 0)
        todo += kids.get(p, [])
    return total // 1024


def run_step(cmd: str | list[str], cwd: Path, log: Any, timeout: float, memory_mb: int = 0,
             measure: Callable[[int], int | None] = tree_mb, env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run one step with its output in log. (exit code, why it was stopped or '')."""
    log.flush()
    try:
        p = subprocess.Popen(cmd, shell=isinstance(cmd, str), cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=log,
                             stderr=subprocess.STDOUT, env=env, **procs.detached())
    except OSError as e:
        return 127, f"could not start: {e}"
    deadline = time.monotonic() + timeout
    why = ""
    warned = False
    while p.poll() is None:
        if time.monotonic() > deadline:
            why = f"stopped after the {int(timeout)} s timeout"
        elif memory_mb:
            mb = measure(p.pid)
            if mb is None and not warned:
                warned = True
                log.write("done gate: memory ceiling not enforced on this system\n")
                log.flush()
            elif mb is not None and mb > memory_mb:
                why = f"stopped at {mb} MB, over the {memory_mb} MB memory ceiling"
        if why:
            procs.terminate(p.pid)
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
            log.write(f"done gate: {why}\n")
            log.flush()
            return p.returncode if p.returncode is not None else -1, why
        try:
            p.wait(POLL)
        except subprocess.TimeoutExpired:
            pass
    return p.returncode, ""


class Lock:
    """One gate at a time: a folder holding the runner's pid. A dead holder's lock is taken over."""

    def __init__(self, path: Path, alive: Callable[[int | None], bool] = procs.alive, sleep: Callable[[float], None] = time.sleep):
        self.path, self.alive, self.sleep = path, alive, sleep

    def acquire(self, say: Callable[[str], None] = lambda _: None) -> None:
        told = False
        while True:
            try:
                self.path.mkdir(parents=True)
                (self.path / "pid").write_text(str(os.getpid()), encoding="utf-8")
                return
            except FileExistsError:
                pass
            try:
                pid = int((self.path / "pid").read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                pid = 0
            try:
                stale = not pid and time.time() - self.path.stat().st_mtime > LOCK_STALE
            except OSError:
                continue
            if (pid and not self.alive(pid)) or stale:
                shutil.rmtree(self.path, ignore_errors=True)
                continue
            if not told:
                told = True
                say(f"waiting for the gate lock {self.path} (held by pid {pid or '?'})")
            self.sleep(3)

    def release(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def prepare(repo: Path, wt: Path, sha: str, sparse: list[str], log: Any) -> str:
    """Bring the persistent gate worktree to sha. '' = ready, else what went wrong."""
    def g(*args: str, at: Path = wt) -> bool:
        p = git(at, *args, timeout=1800)
        log.write((p.stdout or "") + (p.stderr or ""))
        log.flush()
        return p.returncode == 0

    if not (wt / ".git").exists():
        g("worktree", "prune", at=repo)
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)  # a half-made gate tree under the dah root
        wt.parent.mkdir(parents=True, exist_ok=True)
        if not g("worktree", "add", "--detach", "--no-checkout", str(wt), sha, at=repo):
            return "git worktree add failed"
    if sparse:
        if not g("sparse-checkout", "init", "--no-cone"):
            return "git sparse-checkout init failed"
        p = subprocess.run(["git", "-C", str(wt), "sparse-checkout", "set", "--no-cone", "--stdin"], capture_output=True,
                           text=True, input="".join(f"/{s}/\n" for s in sparse), timeout=600)
        if p.returncode != 0:
            log.write(p.stderr)
            return "git sparse-checkout set failed"
    if not g("checkout", "--detach", "-f", sha):
        return f"git checkout {sha[:8]} failed"
    g("clean", "-ffdq")  # leftovers of the last gate; ignored caches stay
    return ""


def run_gate(cfg: "Config", sha: str, result: Path, log_path: Path, lock: Lock | None = None,
             measure: Callable[[int], int | None] = tree_mb) -> dict[str, Any]:
    gate = cfg.done_gate
    root = dah_root(cfg)
    wt = root / "done-gate"
    lock = lock or Lock(root / "done-gate.lock")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    state, why = "fail", ""
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        def say(line: str) -> None:
            log.write(line + "\n")
            log.flush()
        try:
            check_root(root, cfg.workdir)
            lock.acquire(say)
        except (ValueError, OSError) as e:
            why = str(e)
        else:
            try:
                say(f"done gate: lock held, preparing {wt} at {sha[:10]}")
                why = prepare(cfg.workdir, wt, sha, gate["sparse"], log)
                env = {**os.environ, "DAH_GATE": "1", "DAH_GATE_SHA": sha}
                if not why and (gate["setup"] or gate["verify"]):
                    ready = False
                    for i in range(1, SETUP_TRIES + 1):
                        rc = run_step(gate["setup"], wt, log, gate["timeout"], env=env)[0] if gate["setup"] else 0
                        if gate["verify"]:
                            rc = run_step(gate["verify"], wt, log, gate["timeout"], env=env)[0]
                        say(f"done gate: setup pass {i}: {'ready' if rc == 0 else 'not ready'}")
                        if rc == 0:
                            ready = True
                            break
                    if not ready:
                        why = f"setup did not leave the tree ready after {SETUP_TRIES} tries; command not run"
                if not why:
                    say(f"done gate: running {_show(gate['command'])}")
                    rc, why = run_step(gate["command"], wt, log, gate["timeout"], gate["memory_mb"], measure, env)
                    if rc == 0 and not why:
                        state = "pass"
                    elif not why:
                        why = f"exit code {rc}"
            except Exception as e:  # the verdict is always written
                why = f"{type(e).__name__}: {e}"
            finally:
                lock.release()
        if why:
            say(f"done gate: {why}")
    r = {"state": state, "sha": sha, "log": str(log_path), "why": why, "tail": tail(log_path)}
    tmp = result.with_suffix(".tmp")
    tmp.write_text(json.dumps(r), encoding="utf-8")
    os.replace(tmp, result)
    return r


def main(argv: list[str]) -> int:
    from . import config as config_mod
    home, sha, result, log = argv
    cfg = config_mod.load(Path(home))
    if not cfg.done_gate:
        Path(result).write_text(json.dumps({"state": "fail", "sha": sha, "log": log, "why": "done_gate was removed",
                                            "tail": []}), encoding="utf-8")
        return 1
    return 0 if run_gate(cfg, sha, Path(result), Path(log))["state"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
