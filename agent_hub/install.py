"""Write the tower's files into a workspace: config, protocol, lane instructions, and a `/dah-<lane>` shortcut per
lane so a human can also open a lane by hand in their own agent (a command in OpenCode and Claude Code, a skill
in Codex). A lane is a role put on top of the agent the person already uses, not a new agent.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from .config import CONFIG_NAME, HUB, INFRA, TOWER_DIR, Config, free_ports, template

TEMPLATES = Path(__file__).resolve().parent / "templates"
SHIM_PREFIX = "dah-"
SHIM_BODY = ("Read `.tower/lanes/{lane}.md` and `.tower/PROTOCOL.md` and follow them. You are the {lane} lane "
             "(run `dah` with `--as {lane}` if TOWER_LANE is not set).\n")
SHIM_DESC = "Work as the {lane} lane of this workspace's agent-hub"


def shim_files(lane: str, runtime: str) -> tuple[Path, str] | None:
    """Where the `/dah-<lane>` shortcut lives for one runtime, and its text."""
    name, desc, body = SHIM_PREFIX + lane, SHIM_DESC.format(lane=lane), SHIM_BODY.format(lane=lane)
    if runtime == "opencode":
        return Path(".opencode/command") / f"{name}.md", f"---\ndescription: {desc}\n---\n{body}\n$ARGUMENTS\n"
    if runtime == "claude":
        return Path(".claude/commands") / f"{name}.md", f"---\ndescription: {desc}\n---\n{body}\n$ARGUMENTS\n"
    if runtime == "codex":  # Codex has no project commands; a project skill is invoked the same way
        return Path(".agents/skills") / name / "SKILL.md", f"---\nname: {name}\ndescription: {desc}\n---\n{body}"
    return None


def write_shims(cfg: Config, lanes: list[str], force: bool, wrote: list[str]) -> None:
    for runtime in sorted({ln.runtime for ln in cfg.lanes.values()}):
        for lid in lanes:
            f = shim_files(lid, runtime)
            if f:
                _write(cfg.workdir / f[0], f[1], force, wrote, cfg.home)


def remove_shims(cfg: Config, lane: str) -> list[str]:
    """Delete a lane's shortcuts, but only files still exactly as we wrote them."""
    gone = []
    for runtime in ("opencode", "claude", "codex"):
        f = shim_files(lane, runtime)
        path = cfg.workdir / f[0] if f else None
        if f and path and path.is_file() and path.read_text(encoding="utf-8") == f[1]:
            path.unlink()
            if path.name == "SKILL.md" and not any(path.parent.iterdir()):
                path.parent.rmdir()
            gone.append(str(f[0]))
    return gone


def _write(path: Path, text: str, force: bool, wrote: list[str], home: Path) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    wrote.append(str(path.relative_to(home)) if home in path.parents else str(path))


def lane_template(lane: str) -> str:
    name = {HUB: "hub.md", INFRA: "infra.md"}.get(lane)
    if name:
        return (TEMPLATES / "lanes" / name).read_text(encoding="utf-8")
    return (TEMPLATES / "lanes" / "team.md").read_text(encoding="utf-8").replace("{lane}", lane)


def install(cfg: Config, force: bool = False) -> list[str]:
    """Idempotent: existing files are kept unless force. Returns the paths written."""
    home, wrote = cfg.home, []
    _write(home / TOWER_DIR / "PROTOCOL.md", (TEMPLATES / "PROTOCOL.md").read_text(encoding="utf-8"), force, wrote, home)
    for lid in cfg.lanes:
        _write(cfg.instructions(lid), lane_template(lid), force, wrote, home)
    write_shims(cfg, list(cfg.lanes), force, wrote)
    ignore_state(home, wrote)
    return wrote


def ignore_state(home: Path, wrote: list[str]) -> None:
    gi = home / ".gitignore"
    line = f"{TOWER_DIR}/state/"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if line not in text.splitlines():
        gi.write_text(text + ("" if text.endswith("\n") or not text else "\n") + line + "\n", encoding="utf-8")
        wrote.append(".gitignore")


GUIDE = Path(__file__).resolve().parent / "guide"
PACKAGE = "dongduni-agent-hub"
REPO = "https://github.com/twglhk/dongduni-agent-hub"
CLONE_BIN = Path(__file__).resolve().parents[1] / "bin" / "dah"
INSTALL_HINT = f"`uv tool install {PACKAGE}` (or `pipx install {PACKAGE}`)"
SKILL_NAME = "agent-hub-setup"
SKILL_DESC = ("Set up agent-hub (the tower, its hub and lanes, and the review board) in this workspace. Use when asked to "
              "install, set up or transplant agent-hub / the tower, for this project or globally.")
SKILL_BODY = """Set up agent-hub for the workspace you are in (or the one the person names).

- `dah` must run: `{tower} -h`. If it does not, it installs with {hint}. Installing software is the person's call: ask first.
- Run `{tower} guide` and work through that playbook's phases in order. Do not improvise the order or skip the survey.
- The person's own agents, instruction files, MCP servers, skills and subagents stay as they are; the tower drives them.
"""


def tower_command() -> str:
    """How the person's agents should call dah: `dah` when it is on PATH, else the full path of this install's
    command (a project venv), else a clone's script."""
    if shutil.which("dah"):
        return "dah"
    for exe in (Path(sys.executable).parent / "dah", Path(sys.executable).parent / "dah.exe"):
        if exe.is_file():
            return str(exe)
    return str(CLONE_BIN) if CLONE_BIN.is_file() else "dah"

SKILL_FILES = {
    "claude": (Path(".claude/skills") / SKILL_NAME / "SKILL.md",
               f"---\nname: {SKILL_NAME}\ndescription: {SKILL_DESC}\n---\n" + SKILL_BODY),
    "opencode": (Path(".config/opencode/command") / f"{SKILL_NAME}.md",
                 f"---\ndescription: {SKILL_DESC}\n---\n" + SKILL_BODY + "\n$ARGUMENTS\n"),
    "codex": (Path(".codex/prompts") / f"{SKILL_NAME}.md",
              f"---\ndescription: {SKILL_DESC}\n---\n" + SKILL_BODY + "\n$ARGUMENTS\n"),
}
SKILL_HOMES = {"claude": Path(".claude"), "opencode": Path(".config/opencode"), "codex": Path(".codex")}


def skill_plan(user_home: Path, runtimes: list[str] | None = None) -> list[tuple[str, Path, str, str]]:
    """(runtime, path, current text, new text) for each agent this person has. Writes nothing."""
    chosen = runtimes or [r for r in SKILL_FILES if (user_home / SKILL_HOMES[r]).is_dir() or shutil.which(r)]
    plan = []
    for runtime in chosen:
        rel, text = SKILL_FILES[runtime]
        path = user_home / rel
        old = path.read_text(encoding="utf-8") if path.is_file() else ""
        plan.append((runtime, path, old, text.format(tower=tower_command(), hint=INSTALL_HINT)))
    return plan


def default_runtime() -> str | None:
    return next((r for r in ("opencode", "claude", "codex") if shutil.which(r)), None)


def init(home: Path, name: str, lang: str, hub: str, infra: str, project: str | None, safe: bool,
         force: bool) -> Path:
    path = home / TOWER_DIR / CONFIG_NAME
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists (use --force to overwrite)")
    web_port, opencode_port = free_ports(home)
    data = template(name, lang, hub, infra, safe, web_port, opencode_port)
    if project:
        data["project"] = project
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def edit_config(home: Path, fn) -> None:
    path = home / TOWER_DIR / CONFIG_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
