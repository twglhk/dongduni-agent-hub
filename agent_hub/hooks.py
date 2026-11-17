from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_hub import gates  # noqa: E402

TOWER_DIR = ".tower"  # = config.TOWER_DIR; not imported so the hook loads nothing it does not need
URGENT_MAX_AGE_S = 7200
MAX_LINES = 4000
HOOK_TIMEOUT_S = 20
EVENTS = ("StopFailure",)
GATE_ERRORS = "gate-errors.log"
GATE_ERRORS_MAX_BYTES = 512 * 1024
_ERROR_HEADER = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d ", re.M)


def root(home: Path) -> Path:
    return Path(home) / TOWER_DIR / "state" / "hooks"


def lane_dir(home: Path, lane: str) -> Path:
    return root(home) / lane


def write_if_changed(path: Path, text: str) -> None:
    try:
        current: str | None = path.read_text(encoding="utf-8")
    except OSError:
        current = None
    if current == text:
        return
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def log_error(home: Path | None, lane: str | None, what: str) -> None:
    if not home:
        return
    path = root(home) / GATE_ERRORS
    entry = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {lane or '-'} {what}\n{traceback.format_exc()}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > GATE_ERRORS_MAX_BYTES:
            tail = path.read_bytes()[-GATE_ERRORS_MAX_BYTES // 4:].decode("utf-8", errors="replace")
            path.write_text(tail[tail.find("\n") + 1:], encoding="utf-8")
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)
    except OSError as e:
        sys.stderr.write(f"dah hook: cannot write {GATE_ERRORS}: {e}\n")


def gate_errors(home: Path) -> int:
    try:
        return len(_ERROR_HEADER.findall((root(home) / GATE_ERRORS).read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return 0


def post_urgent(home: Path, lane: str, text: str, now: float | None = None, source: str = "") -> Path:
    d = lane_dir(home, lane) / "urgent"
    d.mkdir(parents=True, exist_ok=True)
    t = time.time() if now is None else now
    p = d / f"{int(t * 1000):015d}-{uuid.uuid4().hex[:6]}{'.' + source if source else ''}.txt"
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


def drop_urgent(home: Path, lane: str, source: str) -> int:
    d = lane_dir(home, lane) / "urgent"
    dropped = 0
    try:
        files = sorted(d.glob(f"*.{source}.txt"))
    except OSError:
        return dropped
    for f in files:
        try:
            f.unlink()
        except OSError:
            continue
        dropped += 1
    return dropped


def urgent_tell(cfg: Any, lane: str, text: str) -> str:
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


def pretool(data: dict[str, Any], home: Path | None, lane: str | None, *, user: str | None = None,
            user_home: str | None = None) -> tuple[int, str, str]:
    tool = str(data.get("tool_name") or "")
    notice = ""
    if home and lane and not data.get("agent_id") and data.get("runtime") != "opencode":
        try:
            msgs = take_urgent(home, lane)
        except Exception:
            log_error(home, lane, f"{tool} urgent tells")
            msgs = []
        notice = "\n\n".join(f"URGENT from the hub (act on this now, before your next step): {m}" for m in msgs)
    args = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    verdict = None
    if os.environ.get("DAH_GATES", "").lower() not in ("off", "0", "false", "no"):
        try:
            verdict = gates.check(tool, args, str(data.get("cwd") or os.getcwd()), user, user_home)
        except Exception:
            log_error(home, lane, tool)
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
    except Exception:
        log_error(Path(os.environ["TOWER_HOME"]) if os.environ.get("TOWER_HOME") else None,
                  os.environ.get("TOWER_LANE"), "hook")
        return 0
    if out:
        sys.stdout.buffer.write(out.encode("utf-8"))
        sys.stdout.flush()
    if err:
        sys.stderr.buffer.write(err.encode("utf-8"))
        sys.stderr.flush()
    return code


HOOK_FILE = Path(__file__).resolve()
_WINDOWS = os.name == "nt"


def hook_args() -> list[str]:
    # a missing hook script exits 2, which Claude Code reads as "block the tool call"; a missing module exits 1
    installed = HOOK_FILE.is_relative_to(Path(sys.prefix).resolve())
    return ["-m", "agent_hub.hooks"] if installed else [str(HOOK_FILE)]


def hook_command() -> str:
    args = hook_args()
    if _WINDOWS:
        return " ".join([f'"{sys.executable}"', *(args if args[0] == "-m" else [f'"{args[0]}"'])])
    return shlex.join([sys.executable, *args])


def claude_hooks() -> dict[str, Any]:
    cmd = hook_command()
    out: dict[str, Any] = {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": cmd,
                                                                       "timeout": HOOK_TIMEOUT_S}]}]}
    for ev in EVENTS:
        out[ev] = [{"hooks": [{"type": "command", "command": cmd, "timeout": 10}]}]
    return out


def settings_file(home: Path, lane: str) -> Path:
    d = lane_dir(home, lane)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "settings.json"
    write_if_changed(path, json.dumps({"hooks": claude_hooks()}, indent=1))
    return path


def opencode_plugin() -> str:
    return OPENCODE_PLUGIN.replace("__PYTHON__", json.dumps(sys.executable)).replace("__ARGS__", json.dumps(hook_args()))


OPENCODE_PLUGIN = """// agent-hub gates for OpenCode lanes: each tool call goes through the same PreToolUse gates as Claude Code lanes
// (kill, git sweep, .env read, binary apply_patch). Generated by `python hooks.py opencode-plugin`; save it as
// .opencode/plugin/dah-gates.ts. A gate that cannot run lets the call through.
import type { Plugin } from "@opencode-ai/plugin"
import { spawnSync } from "node:child_process"

const PYTHON: string = process.env.DAH_PYTHON || __PYTHON__
const HOOK_ARGS: string[] = __ARGS__

export default (async ({ directory }: { directory: string }) => ({
  "tool.execute.before": async (input: { tool: string; sessionID: string }, output: { args: unknown }) => {
    const payload = { hook_event_name: "PreToolUse", runtime: "opencode", tool_name: input.tool,
                      tool_input: output.args, cwd: directory, session_id: input.sessionID }
    const r = spawnSync(PYTHON, HOOK_ARGS, { input: JSON.stringify(payload), encoding: "utf8", timeout: 20000 })
    if (r.status === 2) throw new Error(String(r.stderr || "refused by a gate").trim())
  },
})) satisfies Plugin
"""


if __name__ == "__main__":
    sys.exit(main())
