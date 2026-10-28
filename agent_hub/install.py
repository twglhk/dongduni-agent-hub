"""Write the tower's files into a workspace: config, protocol, lane instructions, and slash-command shims
so a human can also open a lane by hand in their agent (`/hub` in OpenCode or Claude Code).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import CONFIG_NAME, HUB, INFRA, TOWER_DIR, Config, template

TEMPLATES = Path(__file__).resolve().parent / "templates"
SHIM_DIRS = {"opencode": Path(".opencode/command"), "claude": Path(".claude/commands")}
SHIM = """---
description: Open the {lane} lane of the tower
---
Read `.tower/lanes/{lane}.md` and `.tower/PROTOCOL.md` and follow them. You are the {lane} lane (run `tower` with `--as {lane}` if TOWER_LANE is not set).

$ARGUMENTS
"""


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
    for runtime in sorted({ln.runtime for ln in cfg.lanes.values()}):
        shim_dir = SHIM_DIRS.get(runtime)
        if shim_dir:
            for lid in (HUB, INFRA):
                if lid in cfg.lanes:
                    _write(cfg.workdir / shim_dir / f"{lid}.md", SHIM.format(lane=lid), force, wrote, home)
    ignore_state(home, wrote)
    return wrote


def ignore_state(home: Path, wrote: list[str]) -> None:
    gi = home / ".gitignore"
    line = f"{TOWER_DIR}/state/"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if line not in text.splitlines():
        gi.write_text(text + ("" if text.endswith("\n") or not text else "\n") + line + "\n", encoding="utf-8")
        wrote.append(".gitignore")


def default_runtime() -> str | None:
    return next((r for r in ("opencode", "claude", "codex") if shutil.which(r)), None)


def init(home: Path, name: str, lang: str, hub: str, infra: str, project: str | None, safe: bool,
         force: bool) -> Path:
    path = home / TOWER_DIR / CONFIG_NAME
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists (use --force to overwrite)")
    data = template(name, lang, hub, infra, safe)
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
