from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .. import accounts, hooks as hooks_mod, procs
from ..config import TOWER_DIR, Config, Lane
from ..store import Store
from .common import RuntimeFailure, lane_env, pid_alive
from .worktree import lane_cwd


class Adapter:
    def __init__(self, cfg: Config, store: Store, lane: Lane):
        self.cfg, self.store, self.lane = cfg, store, lane
        self.procs: dict[int, subprocess.Popen[bytes]] = {}
        self.logs = cfg.state_dir / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)

    @property
    def log_path(self) -> Path:
        return self.logs / f"{self.lane.id}.jsonl"

    def set_opts(self, model: str | None, effort: str | None, account: Path | str | None = None) -> None:
        data = {"model": model, "effort": effort, **({"account": str(account)} if account else {})}
        hooks_mod.write_if_changed(self.logs / f"{self.lane.id}.opts.json", json.dumps(data))

    def opts(self) -> dict[str, str | None]:
        try:
            return json.loads((self.logs / f"{self.lane.id}.opts.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def account_dir(self) -> str | None:
        return self.opts().get("account") or None

    def env(self) -> dict[str, str]:
        return accounts.session_env(self.account_dir(), lane_env(self.cfg, self.lane))

    def model_arg(self) -> list[str]:
        model = self.opts().get("model") or self.lane.model
        return ["--model", model] if model else []

    @property
    def cwd(self) -> Path:
        return self.cfg.lane_dir(self.lane.id)

    def _tower_outside(self) -> list[str]:
        tower = self.cfg.home / TOWER_DIR
        return [] if tower.resolve().is_relative_to(self.cwd.resolve()) else [str(tower)]

    def _spawn(self, argv: list[str]) -> int:
        cwd = lane_cwd(self.cfg, self.lane)
        with self.log_path.open("ab") as out, (self.logs / f"{self.lane.id}.err").open("ab") as err:
            proc = subprocess.Popen(argv, cwd=cwd, env=self.env(), stdout=out,
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
        if procs.reap(pid):
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
        return None

    def ledger(self, sid: str | None) -> dict[str, int | None] | None:
        u = self.usage(sid)
        if not u:
            return None
        return {"new": int(u.get("tokens_in") or 0), "cache": 0, "out": int(u.get("tokens_out") or 0), "turns": None}

    def last_text(self, n: int = 1) -> list[str]:
        return []
