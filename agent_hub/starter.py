from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable

from .config import Config

STARTER = Path(__file__).resolve().parent / "templates" / "starter"

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
    return sorted("starter/" + p.relative_to(STARTER).as_posix() for p in STARTER.rglob("*") if p.is_file())


def fill(text: str, model: str, effort: str) -> str:
    return text.replace("{model}", model).replace("{effort}", effort)


def _current(path: Path, name: str) -> tuple[str, str]:
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


def backup(path: Path, new_text: str) -> Path | None:
    if not path.is_file():
        return None
    try:
        if path.read_text(encoding="utf-8") == new_text:
            return None
    except (OSError, UnicodeDecodeError):
        pass
    bak = path.with_name(path.name + ".bak")
    shutil.copyfile(path, bak)
    return bak


def install(cfg: Config, force: bool, wrote: list[str]) -> None:
    for key, dest in targets(cfg):
        if dest.exists() and not force:
            continue
        raw = (STARTER / key.removeprefix("starter/")).read_text(encoding="utf-8")
        text = fill(raw, *SUBAGENTS[Path(key).stem]) if key.startswith("starter/agents/") else raw
        dest.parent.mkdir(parents=True, exist_ok=True)
        if bak := backup(dest, text):
            wrote.append(_rel(bak, cfg.home) + " (your previous copy)")
        dest.write_text(text, encoding="utf-8")
        wrote.append(_rel(dest, cfg.home))


def refresh(cfg: Config, versions: dict[str, list[str]], report: dict[str, list[str]], refresh_file: Callable) -> None:
    for key, dest in targets(cfg):
        texts = versions.get(key)
        if not texts:
            continue
        base = cfg.home if cfg.home in dest.parents else cfg.workdir
        refresh_file(dest, [_text(key, t, dest) for t in texts], report, base)


def fix_models(cfg: Config, probe: Callable[[str, str], bool]) -> list[str]:
    notes: list[str] = []
    seen: dict[str, bool] = {}
    for name, (model, _effort) in SUBAGENTS.items():
        path = cfg.workdir / ".claude" / "agents" / f"{name}.md"
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        m = _LINE["model"].search(text)
        if not m or m.group(1) != model:
            continue
        if model not in seen:
            seen[model] = probe("claude", model)
        if not seen[model]:
            path.write_text(text[:m.start()] + f"model: {FALLBACK_MODEL}" + text[m.end():], encoding="utf-8")
            notes.append(f"subagent {name}: model {model} is not available to this agent, using {FALLBACK_MODEL}")
    return notes


def _rel(path: Path, home: Path) -> str:
    return path.relative_to(home).as_posix() if home in path.parents else str(path)
