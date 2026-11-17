from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .. import hooks as hooks_mod, procs
from ..config import TOWER_DIR, Config, Lane
from ..store import Store
from .common import RuntimeFailure, lane_env, pid_alive
from .worktree import lane_cwd


class Adapter:
    """Shared plumbing: one lane, its child processes, its log file. Extensions subclass it (or Headless) to add a
    runtime: status() -> closed|idle|busy|asking|unknown, open(prompt) -> session id, send(sid, text), close()."""

    def __init__(self, cfg: Config, store: Store, lane: Lane):
        self.cfg, self.store, self.lane = cfg, store, lane
        self.procs: dict[int, subprocess.Popen[bytes]] = {}
        self.logs = cfg.state_dir / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)

    @property
    def log_path(self) -> Path:
        return self.logs / f"{self.lane.id}.jsonl"

    def set_opts(self, model: str | None, effort: str | None) -> None:
        """A task's own model and effort (stagemodel.py), kept so every resume of the session runs with them."""
        hooks_mod.write_if_changed(self.logs / f"{self.lane.id}.opts.json", json.dumps({"model": model, "effort": effort}))

    def opts(self) -> dict[str, str | None]:
        try:
            return json.loads((self.logs / f"{self.lane.id}.opts.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def model_arg(self) -> list[str]:
        model = self.opts().get("model") or self.lane.model
        return ["--model", model] if model else []

    @property
    def cwd(self) -> Path:
        """Where the agent runs (its worktree when the lane has one); lane_cwd() creates the worktree."""
        return self.cfg.lane_dir(self.lane.id)

    def _tower_outside(self) -> list[str]:
        """`.tower/` when the agent works outside it (a worktree, a `project` subfolder): lane files, the protocol
        and briefs live there, and an agent that guards reads outside its folder must be allowed to read them."""
        tower = self.cfg.home / TOWER_DIR
        return [] if tower.resolve().is_relative_to(self.cwd.resolve()) else [str(tower)]

    def _spawn(self, argv: list[str]) -> int:
        cwd = lane_cwd(self.cfg, self.lane)
        with self.log_path.open("ab") as out, (self.logs / f"{self.lane.id}.err").open("ab") as err:
            proc = subprocess.Popen(argv, cwd=cwd, env=lane_env(self.cfg, self.lane), stdout=out,
                                    stderr=err, stdin=subprocess.DEVNULL, **procs.detached())
        self.procs[proc.pid] = proc
        self.store.set_lane(self.lane.id, pid=proc.pid, pid_start=procs.start_time(proc.pid))
        return proc.pid

    def _running(self) -> bool:
        pid = self.store.lane(self.lane.id).get("pid")
        if not pid:
            return False
        proc = self.procs.get(pid)
        if proc is not None:  # our child: poll() also reaps it, so a finished run never looks alive
            return proc.poll() is None
        if not self._ours(pid):
            return False
        if procs.reap(pid):  # a child of this process we lost the handle for (config reload) that finished
            return False
        return pid_alive(pid)

    def _ours(self, pid: int) -> bool:
        return procs.same_process(pid, self.store.lane(self.lane.id).get("pid_start"))

    def _kill(self) -> None:
        pid = self.store.lane(self.lane.id).get("pid")
        if pid and self._running():
            procs.terminate_then_kill(pid)
            proc = self.procs.get(pid)
            if proc is not None:
                proc.poll()
        self.procs.pop(pid or -1, None)

    def owned(self) -> bool:
        return True

    # optional capabilities
    def questions(self) -> list[dict[str, Any]] | None:
        return []

    def reply_question(self, qid: str, answers: list[list[str]]) -> None:
        raise RuntimeFailure(f"{self.lane.runtime} has no live questions")

    def send_queued(self, sid: str, text: str) -> None:
        self.send(sid, text)

    def context_k(self, sid: str | None) -> int:
        return 0

    def compact(self, sid: str, focus: str) -> str | None:
        return None

    def usage(self, sid: str | None) -> dict[str, Any] | None:
        """Totals for one session: tokens_in (cache included), tokens_out, cost (USD or None), ctx_k."""
        return None

    def ledger(self, sid: str | None) -> dict[str, int | None] | None:
        """One session's totals split for the task ledger: new (input + cache writes), cache (cache reads), out, turns
        (None when the runtime does not count them). The default reads usage(): everything counts as new."""
        u = self.usage(sid)
        if not u:
            return None
        return {"new": int(u.get("tokens_in") or 0), "cache": 0, "out": int(u.get("tokens_out") or 0), "turns": None}

    def last_text(self, n: int = 1) -> list[str]:
        return []
