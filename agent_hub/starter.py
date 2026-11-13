"""Starter content every workspace gets: four neutral Claude Code subagents (explore, librarian, looker, general) and a
few skills (humanizer, media-reading, review-board, project-audit), copied from `templates/starter/`.

Where they go (the folder the lanes run in, like the `/dah-<lane>` shortcuts):
- subagents: `.claude/agents/<name>.md`, only when a lane runs on Claude Code;
- skills: `.claude/skills/<name>/` when a lane runs on Claude Code (OpenCode reads that folder too), and
  `.agents/skills/<name>/` when a lane runs on Codex, or on OpenCode with no Claude lane.

`install` writes missing files and keeps existing ones. `refresh` (`dah update`) brings each file to the newest
template through `templates/shipped.json`, like PROTOCOL.md: an untouched copy is swapped, an edited one gets the new
text beside it as `<name>.new`. A subagent's `model:` and `effort:` lines are the workspace's own: a file that only
changed those is still untouched.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .config import Config

STARTER = Path(__file__).resolve().parent / "templates" / "starter"

# Model and effort per subagent, from guide/MODELS.md: Haiku is for bulk reading by subagents (default effort medium);
# Sonnet is the everyday worker (default effort high). A model the agent CLI refuses becomes `inherit` (the session's
# own model), the same probe `dah init` runs for the lanes' `fable`.
SUBAGENTS: dict[str, tuple[str, str]] = {
    "explore": ("haiku", "medium"),
    "librarian": ("haiku", "medium"),
    "looker": ("sonnet", "high"),
    "general": ("sonnet", "high"),
}
FALLBACK_MODEL = "inherit"
SKILLS = ("humanizer", "media-reading", "review-board", "project-audit")
_LINE = {k: re.compile(rf"^{k}: *(\S+) *$", re.M) for k in ("model", "effort")}


def template_keys() -> list[str]:
    """Every starter file as its key in shipped.json (`starter/...`, posix)."""
    return sorted("starter/" + p.relative_to(STARTER).as_posix() for p in STARTER.rglob("*") if p.is_file())


def fill(text: str, model: str, effort: str) -> str:
    return text.replace("{model}", model).replace("{effort}", effort)


def _current(path: Path, name: str) -> tuple[str, str]:
    """The model and effort a subagent file already names, else the defaults."""
    model, effort = SUBAGENTS[name]
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        m, e = _LINE["model"].search(text), _LINE["effort"].search(text)
        model, effort = (m.group(1) if m else model), (e.group(1) if e else effort)
    return model, effort


def skill_dirs(cfg: Config) -> list[str]:
    runtimes = {ln.runtime for ln in cfg.lanes.values()}
    dirs = [".claude/skills"] if "claude" in runtimes else []
    if "codex" in runtimes or ("opencode" in runtimes and "claude" not in runtimes):
        dirs.append(".agents/skills")
    return dirs


def targets(cfg: Config) -> list[tuple[str, Path]]:
    """(shipped.json key, destination) for every starter file this workspace should have."""
    out: list[tuple[str, Path]] = []
    base = cfg.workdir
    if any(ln.runtime == "claude" for ln in cfg.lanes.values()):
        out += [(f"starter/agents/{n}.md", base / ".claude" / "agents" / f"{n}.md") for n in SUBAGENTS]
    for d in skill_dirs(cfg):
        for key in template_keys():
            if key.startswith("starter/skills/"):
                out.append((key, base / d / key.removeprefix("starter/skills/")))
    return out


def _text(key: str, raw: str, dest: Path) -> str:
    if key.startswith("starter/agents/"):
        return fill(raw, *_current(dest, Path(key).stem))
    return raw


def install(cfg: Config, force: bool, wrote: list[str]) -> None:
    """Write the starter files that are missing (all of them with force)."""
    for key, dest in targets(cfg):
        if dest.exists() and not force:
            continue
        raw = (STARTER / key.removeprefix("starter/")).read_text(encoding="utf-8")
        text = fill(raw, *SUBAGENTS[Path(key).stem]) if key.startswith("starter/agents/") else raw
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        wrote.append(_rel(dest, cfg.home))


def refresh(cfg: Config, versions: dict[str, list[str]], report: dict[str, list[str]], refresh_file: Callable) -> None:
    """`dah update`: each starter file through `refresh_file` (install._refresh_file) with its shipped texts."""
    for key, dest in targets(cfg):
        texts = versions.get(key)
        if not texts:
            continue
        base = cfg.home if cfg.home in dest.parents else cfg.workdir
        refresh_file(dest, [_text(key, t, dest) for t in texts], report, base)


def fix_models(cfg: Config, probe: Callable[[str, str], bool]) -> list[str]:
    """Subagent files still on a default alias the claude CLI refuses get `model: inherit`. Says what it changed."""
    notes: list[str] = []
    seen: dict[str, bool] = {}
    for name, (model, _effort) in SUBAGENTS.items():
        path = cfg.workdir / ".claude" / "agents" / f"{name}.md"
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        m = _LINE["model"].search(text)
        if not m or m.group(1) != model:
            continue  # the workspace chose its own model
        if model not in seen:
            seen[model] = probe("claude", model)
        if not seen[model]:
            path.write_text(text[:m.start()] + f"model: {FALLBACK_MODEL}" + text[m.end():], encoding="utf-8")
            notes.append(f"subagent {name}: model {model} is not available to this agent, using {FALLBACK_MODEL}")
    return notes


def _rel(path: Path, home: Path) -> str:
    return path.relative_to(home).as_posix() if home in path.parents else str(path)
