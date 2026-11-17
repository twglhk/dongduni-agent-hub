from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Config, Lane
from ..ext import Extensions, registry
from ..store import Store
from .adapter import Adapter
from .claude_term import ClaudeTerm
from .codex_term import CodexTerm
from .common import RuntimeFailure
from .headless import Claude, Codex
from .opencode import OpenCode, OpenCodeTerm
from .worktree import lane_cwd, worktree_note


ADAPTERS = {"opencode": OpenCode, "claude": Claude, "codex": Codex}
TERMINAL_ADAPTERS = {"opencode": OpenCodeTerm, "claude": ClaudeTerm, "codex": CodexTerm}


class Unavailable(Adapter):
    """A lane whose runtime an extension registered, but no extension registers now (the file was removed or
    fails to load): reported as unknown, never opened."""

    def _why(self) -> RuntimeFailure:
        return RuntimeFailure(f"{self.lane.id}: no runtime {self.lane.runtime!r} (an extension in .tower/ext/ must "
                              f"register it; see .tower/state/tower.log)")

    def status(self) -> str:
        raise self._why()

    def open(self, prompt: str) -> str:
        raise self._why()

    def send(self, sid: str, text: str) -> None:
        raise self._why()

    def close(self) -> None:
        self.store.set_lane(self.lane.id, session=None, pid=None)


def adapter_class(lane: Lane, ext: Extensions) -> type[Adapter]:
    if lane.runtime in ADAPTERS:
        return (ADAPTERS if lane.terminal == "none" else TERMINAL_ADAPTERS)[lane.runtime]
    cls = ext.table("runtimes").get(lane.runtime)
    return cls if isinstance(cls, type) and issubclass(cls, Adapter) else Unavailable


class Runtimes:
    """What the engine talks to: one adapter per configured lane."""

    def __init__(self, cfg: Config, store: Store, ext: Extensions | None = None):
        self.cfg = cfg
        ext = ext or registry(cfg.home)
        self.adapters: dict[str, Adapter] = {lid: adapter_class(ln, ext)(cfg, store, ln) for lid, ln in cfg.all_lanes.items()}

    def _a(self, lane: str) -> Any:
        return self.adapters[lane]

    def status(self, lane: str) -> str:
        return self._a(lane).status()

    def owned(self, lane: str) -> bool:
        return self._a(lane).owned()

    def questions(self, lane: str) -> list[dict[str, Any]] | None:
        return self._a(lane).questions()

    def reply_question(self, lane: str, qid: str, answers: list[list[str]]) -> None:
        self._a(lane).reply_question(qid, answers)

    def open(self, lane: str, prompt: str, model: str | None = None, effort: str | None = None,
             account: Path | None = None) -> str:
        ln = self.cfg.all_lanes[lane]
        self._a(lane).set_opts(model, effort, account)  # OpenCode lanes keep their configured model; claude and codex honour these
        if ln.worktree:
            lane_cwd(self.cfg, ln)
            prompt = worktree_note(self.cfg, ln) + prompt
        return self._a(lane).open(prompt)

    def sync(self, lane: str) -> str:
        from ..branchsync import sync_worktree
        return sync_worktree(self.cfg, self.cfg.all_lanes[lane])

    def send(self, lane: str, sid: str, text: str) -> None:
        self._a(lane).send(sid, text)

    def send_queued(self, lane: str, sid: str, text: str) -> None:
        self._a(lane).send_queued(sid, text)

    def close(self, lane: str) -> None:
        self._a(lane).close()

    def context_k(self, lane: str, sid: str | None) -> int:
        return self._a(lane).context_k(sid)

    def compact(self, lane: str, sid: str, focus: str) -> str | None:
        return self._a(lane).compact(sid, focus)

    def usage(self, lane: str, sid: str | None) -> dict[str, Any] | None:
        return self._a(lane).usage(sid)

    def ledger(self, lane: str, sid: str | None) -> dict[str, int | None] | None:
        return self._a(lane).ledger(sid)

    def last_text(self, lane: str, n: int = 1) -> list[str]:
        return self._a(lane).last_text(n)
