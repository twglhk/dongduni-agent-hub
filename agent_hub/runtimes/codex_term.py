from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from .. import procs
from .headless import Codex
from .terminal import TerminalSession, write_signal


def _toml_strings(value: str) -> list[str]:
    """The strings of a TOML array such as ["notify-send", 'Codex'] (basic and literal strings)."""
    out = []
    for basic, literal in re.findall(r'"((?:[^"\\]|\\.)*)"|\'([^\']*)\'', value):
        if literal or not basic:
            out.append(literal)
        else:
            try:
                out.append(json.loads(f'"{basic}"'))
            except json.JSONDecodeError:
                out.append(basic)
    return out


def _toml_notify(text: str, profile: str) -> list[str]:
    """`notify` from a Codex config.toml: the profile's when it sets one, else the top-level one."""
    try:
        import tomllib  # Python 3.11+
        data = tomllib.loads(text)
        prof = (data.get("profiles") or {}).get(profile) or {} if profile else {}
        value = prof.get("notify") or data.get("notify")
        return [str(v) for v in value] if isinstance(value, list) else []
    except ImportError:
        pass
    except ValueError:  # tomllib.TOMLDecodeError: Codex would not start either
        return []
    sections: dict[str, str] = {}
    name, start = "", 0
    for m in re.finditer(r"^\s*\[([^\]\n]+)\]\s*$", text, re.M):
        sections[name] = text[start:m.start()]
        name, start = m.group(1).strip().replace('"', ""), m.end()
    sections[name] = text[start:]
    for key in ([f"profiles.{profile}"] if profile else []) + [""]:
        m = re.search(r"^\s*notify\s*=\s*(\[[^\]]*\])", sections.get(key, ""), re.M)
        if m:
            return _toml_strings(m.group(1))
    return []


def codex_notify(args: list[str]) -> tuple[list[str], list[str]]:
    """The lane's args without a `-c notify=...`, and the notify program the person set (that one, their profile's,
    or their config.toml's), which `dah signal` passes each notification on to."""
    rest, own, profile = [], [], ""
    i = 0
    while i < len(args):
        a, nxt = args[i], args[i + 1] if i + 1 < len(args) else ""
        if a in ("-c", "--config") and nxt.replace(" ", "").startswith("notify="):
            own = _toml_strings(nxt.split("=", 1)[1])
            i += 2
            continue
        if a.startswith("--config=") and a[len("--config="):].replace(" ", "").startswith("notify="):
            own = _toml_strings(a.split("=", 2)[2])
            i += 1
            continue
        if a in ("--profile", "-p"):
            profile = nxt
        rest.append(a)
        i += 1
    if not own:
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        try:
            own = _toml_notify((home / "config.toml").read_text(encoding="utf-8"), profile)
        except OSError:
            own = []
    if any("agent_hub" in a for a in own) and "signal" in own:
        own = []  # already ours
    return rest, own


class CodexTerm(TerminalSession, Codex):
    EXEC_ONLY = ("--json", "--skip-git-repo-check")

    def open(self, prompt: str) -> str:
        sid = f"term-{uuid.uuid4()}"
        self._close_window()
        write_signal(self.cfg.home, self.lane.id, state="busy")
        args, chained = codex_notify([a for a in self.lane.args if a not in self.EXEC_ONLY])
        argv = [*procs.command(os.environ.get("TOWER_CODEX_BIN", "codex")), *args]
        argv += self.model_arg()
        argv += ["-c", f"model_reasoning_effort={self.opts()['effort']}"] if self.opts().get("effort") else []
        notify = [sys.executable, "-m", "agent_hub", "signal"]
        argv += ["-c", f"notify={json.dumps(notify)}", self._note(prompt)]
        self._launch(argv, {"TOWER_CODEX_NOTIFY": json.dumps(chained)} if chained else None)
        self.store.set_lane(self.lane.id, session=sid, pid=None)
        return sid

    def context_k(self, sid: str | None) -> int:
        return 0

    def usage(self, sid: str | None) -> dict[str, Any] | None:
        return None

    def last_text(self, n: int = 1) -> list[str]:
        last = str(self._signal().get("last") or "").strip()
        return [last] if last else []
