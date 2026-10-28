from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from agent_hub import config as config_mod
from agent_hub.store import Store


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, s: float) -> None:
        self.t += s


def make_home(lanes: dict[str, Any] | None = None, **extra: Any) -> config_mod.Config:
    home = Path(tempfile.mkdtemp(prefix="tower-test-"))
    data = {"name": "Test", "lang": "en", "lanes": lanes or {"hub": {"runtime": "claude"}, "infra": {"runtime": "claude"},
                                                             "docs": {"runtime": "codex"}}, **extra}
    (home / ".tower").mkdir()
    (home / ".tower" / "tower.json").write_text(json.dumps(data))
    return config_mod.load(home)


class FakeRuntime:
    """Same surface as runtimes.Runtimes; status is whatever the test sets."""

    def __init__(self, store: Store):
        self.store = store
        self.st: dict[str, str] = {}
        self.opened: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str, str]] = []
        self.closed: list[str] = []
        self.qs: dict[str, list[dict[str, Any]]] = {}
        self.replies: list[tuple[str, str, list[list[str]]]] = []
        self.ctx: dict[str, int] = {}
        self.not_owned: set[str] = set()

    def status(self, lane: str) -> str:
        if self.qs.get(lane):
            return "asking"
        return self.st.get(lane, "closed")

    def owned(self, lane: str) -> bool:
        return lane not in self.not_owned

    def questions(self, lane: str) -> list[dict[str, Any]]:
        return self.qs.get(lane, [])

    def reply_question(self, lane: str, qid: str, answers: list[list[str]]) -> None:
        self.replies.append((lane, qid, answers))
        self.qs[lane] = [q for q in self.qs.get(lane, []) if q["id"] != qid]

    def open(self, lane: str, prompt: str) -> str:
        self.opened.append((lane, prompt))
        self.st[lane] = "busy"
        sid = f"s-{lane}-{len(self.opened)}"
        self.store.set_lane(lane, session=sid)
        return sid

    def send(self, lane: str, sid: str, text: str) -> None:
        self.sent.append((lane, sid, text))
        self.st[lane] = "busy"

    def close(self, lane: str) -> None:
        self.closed.append(lane)
        self.st[lane] = "closed"
        self.store.set_lane(lane, session=None)

    def context_k(self, lane: str, sid: str | None) -> int:
        return self.ctx.get(lane, 0)

    def last_text(self, lane: str, n: int = 1) -> list[str]:
        return []
