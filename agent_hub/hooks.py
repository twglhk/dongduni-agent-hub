"""The hook every tower-opened Claude Code lane runs (`--settings`), and the state it keeps per lane.

  PreToolUse    runs the gates (`gates.py`). Deny = exit 2 with the reason on stderr (Claude Code hands stderr to
                the model and blocks the call). Allow = exit 0. A gate that crashes never blocks work: the call
                goes through and the traceback goes to `.tower/state/hooks/gate-errors.log`. Every deny is
                appended to the lane's `events.jsonl` as `GateDeny`, so an audit can count which rules fire.
                It also hands the main agent any urgent tell waiting for the lane (`dah tell --urgent`) as hook
                context, so a stop order lands at the lane's next tool call, mid-turn. Tells older than 2 h are
                dropped.
  other events  (StopFailure) one line each in the lane's `events.jsonl`; `apierrors.py` reads them and pushes
                "your login expired" and the like to people. Nothing is printed: hook stdout can steer the model.

State: `<workspace>/.tower/state/hooks/<lane>/` (events.jsonl, urgent/). The lane comes from TOWER_HOME and
TOWER_LANE, which the tower sets; a session started without the tower has neither, so it still gets the gates but
no log and no urgent tells. `DAH_GATES=off` in a lane's env turns the gates off for that lane.

Run as `python <path>/hooks.py`: no package install and no PYTHONPATH needed, only the standard library.
`python hooks.py opencode-plugin` prints an OpenCode plugin that sends OpenCode's tool calls through the same gates
(that is where `apply_patch`, and so the binary guard, exists); save it as `.opencode/plugin/dah-gates.ts`.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # run as a script by its path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_hub import gates  # noqa: E402

TOWER_DIR = ".tower"  # = config.TOWER_DIR; not imported so the hook loads nothing it does not need
URGENT_MAX_AGE_S = 7200  # a stop order that old is history: drop it instead of replaying it
MAX_LINES = 4000  # events.jsonl keeps its newest half past this
HOOK_TIMEOUT_S = 20
EVENTS = ("StopFailure",)  # recorded besides PreToolUse; add here to record more


def root(home: Path) -> Path:
    return Path(home) / TOWER_DIR / "state" / "hooks"


def lane_dir(home: Path, lane: str) -> Path:
    return root(home) / lane


# ---------------------------------------------------------------- urgent tells

def post_urgent(home: Path, lane: str, text: str, now: float | None = None) -> Path:
    d = lane_dir(home, lane) / "urgent"
    d.mkdir(parents=True, exist_ok=True)
    t = time.time() if now is None else now
    p = d / f"{int(t * 1000):015d}-{uuid.uuid4().hex[:6]}.txt"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)  # a hook reading at the same moment sees the whole message or none
    return p


def _stamp(f: Path) -> float | None:
    try:
        return int(f.stem.split("-")[0]) / 1000
    except ValueError:
        return None


def take_urgent(home: Path, lane: str, now: float | None = None) -> list[str]:
    """Oldest first. Each file is removed as it is read; one that cannot be removed (another hook took it) is
    skipped, so a message arrives once."""
    d = lane_dir(home, lane) / "urgent"
    t = time.time() if now is None else now
    out: list[str] = []
    try:
        files = sorted(d.glob("*.txt"))
    except OSError:
        return out
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
            f.unlink()
        except OSError:
            continue
        stamp = _stamp(f)
        if stamp is not None and t - stamp <= URGENT_MAX_AGE_S and text.strip():
            out.append(text.strip())
    return out


def urgent_tell(cfg: Any, lane: str, text: str) -> str:
    """`dah tell --urgent LANE TEXT`: drop the message for the lane and its parallel instances; returns the line to
    print. Raises ValueError for a lane that is not a team lane."""
    from .config import base_lane
    if base_lane(lane) not in cfg.team():
        raise ValueError(f"{lane} is not a team lane (team: {', '.join(cfg.team()) or 'none yet'})")
    targets = [lane] if lane != base_lane(lane) else [lane, *(i for i in cfg.instances if base_lane(i) == lane)]
    for t in targets:
        post_urgent(cfg.home, t, text)
    runtime = cfg.all_lanes[lane].runtime if lane in cfg.all_lanes else ""
    note = "" if runtime == "claude" else f" Note: {lane} runs {runtime or 'an unknown runtime'}; only Claude Code lanes read urgent tells."
    return f"→ {', '.join(targets)}: urgent, shown at the next tool call (dropped after 2 h).{note}"


def pending_urgent(home: Path, lane: str) -> int:
    try:
        return len(list((lane_dir(home, lane) / "urgent").glob("*.txt")))
    except OSError:
        return 0


# ---------------------------------------------------------------- event log

def _clip(v: Any, n: int) -> Any:
    return v[:n] if isinstance(v, str) else v


def record(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("hook_event_name") or "?")
    rec: dict[str, Any] = {"ts": time.time(), "event": name, "session_id": data.get("session_id"),
                           "agent_id": data.get("agent_id")}
    if name == "StopFailure":
        rec["error"] = _clip(data.get("error"), 300)
        rec["error_details"] = _clip(data.get("error_details"), 1000)
    return rec


def append_event(home: Path, lane: str, rec: dict[str, Any]) -> None:
    d = lane_dir(home, lane)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "events.jsonl"
    try:
        if path.stat().st_size > MAX_LINES * 400:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(lines) > MAX_LINES:
                path.write_text("\n".join(lines[-MAX_LINES // 2:]) + "\n", encoding="utf-8")
    except OSError:
        pass
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- the hook

def pretool(data: dict[str, Any], home: Path | None, lane: str | None, *, user: str | None = None,
            user_home: str | None = None) -> tuple[int, str, str]:
    """(exit code, stdout, stderr) for one PreToolUse payload."""
    tool = str(data.get("tool_name") or "")
    notice = ""
    if home and lane and not data.get("agent_id") and data.get("runtime") != "opencode":  # the main agent reads it
        try:
            msgs = take_urgent(home, lane)
        except Exception:
            msgs = []
        notice = "\n\n".join(f"URGENT from the hub (act on this now, before your next step): {m}" for m in msgs)
    args = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    verdict = None
    if os.environ.get("DAH_GATES", "").lower() not in ("off", "0", "false", "no"):
        try:
            verdict = gates.check(tool, args, str(data.get("cwd") or os.getcwd()), user, user_home)
        except Exception:
            if home:
                try:
                    root(home).mkdir(parents=True, exist_ok=True)
                    with (root(home) / "gate-errors.log").open("a", encoding="utf-8") as f:
                        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {lane} {tool}\n{traceback.format_exc()}\n")
                except OSError:
                    pass
            verdict = None
    if verdict is None:
        out = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": notice}},
                         ensure_ascii=False) if notice else ""
        return 0, out, ""
    gate, message = verdict
    if home and lane:
        try:
            append_event(home, lane, {"ts": time.time(), "event": "GateDeny", "gate": gate, "tool": tool,
                                      "session_id": data.get("session_id"), "agent_id": data.get("agent_id")})
        except OSError:
            pass
    return 2, "", (notice + "\n\n" if notice else "") + message


def handle(raw: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    env = os.environ if env is None else env
    home = Path(env["TOWER_HOME"]) if env.get("TOWER_HOME") else None
    lane = env.get("TOWER_LANE") or None
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0, "", ""
    if not isinstance(data, dict):
        return 0, "", ""
    event = str(data.get("hook_event_name") or "PreToolUse")
    if event == "PreToolUse":
        return pretool(data, home, lane)
    if home and lane:
        try:
            append_event(home, lane, record(data))
        except OSError:
            pass
    return 0, "", ""


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["opencode-plugin"]:
        sys.stdout.write(opencode_plugin())
        return 0
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        code, out, err = handle(raw)
    except Exception:  # the hook must never break a turn
        return 0
    if out:
        sys.stdout.buffer.write(out.encode("utf-8"))
        sys.stdout.flush()
    if err:
        sys.stderr.buffer.write(err.encode("utf-8"))
        sys.stderr.flush()
    return code


# ---------------------------------------------------------------- wiring

HOOK_FILE = Path(__file__).resolve()


def hook_command() -> str:
    """The shell command Claude Code runs for each hook: this interpreter on this file."""
    if os.name == "nt":
        return f'"{sys.executable}" "{HOOK_FILE}"'
    return shlex.join([sys.executable, str(HOOK_FILE)])


def claude_hooks() -> dict[str, Any]:
    """The `hooks` entries every tower-opened Claude Code lane gets (merged into its `--settings`)."""
    cmd = hook_command()
    out: dict[str, Any] = {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": cmd,
                                                                       "timeout": HOOK_TIMEOUT_S}]}]}
    for ev in EVENTS:
        out[ev] = [{"hooks": [{"type": "command", "command": cmd, "timeout": 10}]}]
    return out


def settings_file(home: Path, lane: str) -> Path:
    """A `--settings` file with only these hooks, for a headless lane."""
    d = lane_dir(home, lane)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "settings.json"
    path.write_text(json.dumps({"hooks": claude_hooks()}, indent=1), encoding="utf-8")
    return path


def opencode_plugin() -> str:
    return OPENCODE_PLUGIN.replace("__PYTHON__", json.dumps(sys.executable)).replace("__HOOK__", json.dumps(str(HOOK_FILE)))


OPENCODE_PLUGIN = """// agent-hub gates for OpenCode lanes: each tool call goes through the same PreToolUse gates as Claude Code lanes
// (kill, git sweep, .env read, binary apply_patch). Generated by `python hooks.py opencode-plugin`; save it as
// .opencode/plugin/dah-gates.ts. A gate that cannot run lets the call through.
import type { Plugin } from "@opencode-ai/plugin"
import { spawnSync } from "node:child_process"

const PYTHON: string = process.env.DAH_PYTHON || __PYTHON__
const HOOK: string = __HOOK__

export default (async ({ directory }: { directory: string }) => ({
  "tool.execute.before": async (input: { tool: string; sessionID: string }, output: { args: unknown }) => {
    const payload = { hook_event_name: "PreToolUse", runtime: "opencode", tool_name: input.tool,
                      tool_input: output.args, cwd: directory, session_id: input.sessionID }
    const r = spawnSync(PYTHON, [HOOK], { input: JSON.stringify(payload), encoding: "utf8", timeout: 20000 })
    if (r.status === 2) throw new Error(String(r.stderr || "refused by a gate").trim())
  },
})) satisfies Plugin
"""


if __name__ == "__main__":
    sys.exit(main())
