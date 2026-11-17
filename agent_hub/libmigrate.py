from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from . import engines, processes
from .config import Config

POINTER = """# Pattern library: moved

This engine's processes are in the process library now: the full process `{full}` and its sub-processes in
`processes/sub/`. `dah update` converted this table; the original is kept as `{bak}`.
Edit the process library, not this file.
"""
STUB = "Moved to `{dest}` (the process library) by `dah update`. Edit it there.\n"
FULL = """# Full process: {label}

Deliverable: the deliverables of the {label}, converted from the pattern library `{library}`.

Frame: exception — converted from a pattern library, which has no frames; give each sub-process its frame (planning, production, QA), then delete this line.

| # | Old # | Frame | Sub-process | What it does | Model · effort |
|---|---|---|---|---|---|
{rows}
"""
SKELETON = """# Process: {title} — v1

Status: draft

- Skill: `{skill}`

Converted from row {n} of the pattern library `{library}` (process: {process}; pattern: {pattern}). That row had no
process card: add the stages table (`#`, `Stage`, `Input`, `Output`, `Gate`, `Passed by`, `Pattern card`).
"""
_NON_ID = re.compile(r"[^a-z0-9]+")
_RULE = re.compile(r":?-{2,}:?")


class Skipped(Exception):
    pass


def slug(text: str) -> str:
    return _NON_ID.sub("-", text.lower()).strip("-")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _pattern_source(card: Path, cell: str, skill: str, skills: Path) -> tuple[str, Path] | None:
    if found := engines.repo_file(card.parent, cell):
        return next(t for t in engines._MD_PATH.findall(cell) if found.as_posix().endswith(t)), found
    m = engines._PATTERN.search(cell)
    if m and skill and (f := skills / skill / "references" / m.group(1)).is_file():
        return m.group(1), f
    return None


def _convert_card(text: str, card: Path, row: dict[str, Any], skills: Path) -> tuple[str, list[tuple[Path, str]]]:
    head: list[str] | None = None
    out: list[str] = []
    moved: dict[Path, str] = {}
    for ln in text.splitlines():
        if not ln.strip().startswith("|"):
            head = None
        else:
            cs = engines._cells(ln)
            if head is None:
                if any(c.lower().startswith("stage") for c in cs):
                    head = [c.lower() for c in cs]
            elif not all(_RULE.fullmatch(c) for c in cs if c):
                idx = next((i for i, h in enumerate(head) if h.startswith("pattern")), None)
                hit = _pattern_source(card, cs[idx], row["skill"], skills) if idx is not None and idx < len(cs) else None
                if hit:
                    tok, src = hit
                    if src not in moved:
                        name, n = src.name, 2
                        while name in moved.values():
                            name, n = f"{src.stem}-{n}{src.suffix}", n + 1
                        moved[src] = name
                    ln = ln.replace(tok, f"patterns/{moved[src]}", 1)
        out.append(ln)
    lines = out
    at = next((i for i, ln in enumerate(lines) if ln.lower().startswith("status:")), None)
    if at is None:
        lines[1:1] = ["", f"Status: {row['process_status'] or 'draft'}"]
        at = 2
    if not processes.card_skills(text) and row["skill"]:
        lines[at + 1:at + 1] = ["", f"- Skill: `{row['skill']}`"]
    return "\n".join(lines) + "\n", list(moved.items())


def _plan(cfg: Config, eid: str, library_rel: str, rows: list[dict[str, Any]], skills: Path) -> dict[str, Any]:
    home = cfg.home
    fid = slug(eid) or "engine"
    full_rel = f"{processes.DIR}/full/{fid}.md"
    if (home / full_rel).exists():
        raise Skipped(f"{full_rel} already exists")
    taken = {p.name for p in (home / processes.DIR / "sub").glob("*")} if (home / processes.DIR / "sub").is_dir() else set()
    files: dict[Path, str] = {}
    stubs: dict[Path, str] = {}
    ids: dict[str, str] = {}
    table = []
    label = cfg.engines[eid].label or eid
    for r in rows:
        base = f"{fid}-{slug(r['type']) or 'row-' + slug(r['n'])}"
        sid, n = base, 2
        while sid in taken:
            sid, n = f"{base}-{n}", n + 1
        taken.add(sid)
        ids[r["n"]] = sid
        dest = home / processes.DIR / "sub" / sid
        proc = r["process"]
        card = Path(os.path.normpath(skills / proc["path"])) if proc else None
        text = _read(card) if card else ""
        if text:
            new, moved = _convert_card(text, card, r, skills)
            files[dest / "process.md"] = new
            stubs[card] = STUB.format(dest=f"{processes.DIR}/sub/{sid}/process.md")
            for src, name in moved:
                files[dest / "patterns" / name] = _read(src)
                stubs.setdefault(src, STUB.format(dest=f"{processes.DIR}/sub/{sid}/patterns/{name}"))
        else:
            files[dest / "process.md"] = SKELETON.format(
                title=r["type"], skill=r["skill"] or "none", n=r["n"], library=library_rel, process=r["process_text"] or "-", pattern=r["pattern_text"] or "-")
        table.append(f"| {len(table) + 1} | {r['n']} |  | `{sid}` | {r['type'].replace('|', '/')} | {r['model_text'].replace('|', '/')} |")
    files[home / full_rel] = FULL.format(label=label, library=library_rel, rows="\n".join(table))
    for path, text in files.items():
        if path.exists() and _read(path) != text:
            raise Skipped(f"{path.relative_to(home).as_posix()} already exists")
    return {"fid": fid, "full": full_rel, "files": files, "stubs": stubs, "ids": ids}


def _add_lane_sections(cfg: Config, eid: str, rows: list[dict[str, Any]], fid: str, full_rel: str) -> list[str]:
    owners = [r["lane"] for r in rows if r["lane"] in cfg.lanes]
    lanes = list(dict.fromkeys(owners)) or list(cfg.engines[eid].lanes)
    done = []
    for lid in lanes:
        path = cfg.instructions(lid)
        text = _read(path)
        if text and processes.lane_uses(text) is None:
            path.write_text(text.rstrip("\n") + f"\n\n## Processes used\n\n- `{fid}` (`{full_rel}`)\n", encoding="utf-8")
            done.append(path.relative_to(cfg.home).as_posix())
    return done


def _convert(cfg: Config, eid: str, rows: list[dict[str, Any]], library_rel: str, skills: Path) -> list[str]:
    from .install import edit_config
    from .store import Store
    plan = _plan(cfg, eid, library_rel, rows, skills)
    home, fid, full_rel = cfg.home, plan["fid"], plan["full"]
    full_path = home / full_rel
    for path, text in plan["files"].items():
        if path != full_path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(plan["files"][full_path], encoding="utf-8")
    lanes = _add_lane_sections(cfg, eid, rows, fid, full_rel)
    lib = home / library_rel
    bak = lib.with_name(lib.name + ".bak")
    if not bak.exists():
        bak.write_text(_read(lib), encoding="utf-8")
    lib.write_text(POINTER.format(full=full_rel, bak=bak.relative_to(home).as_posix()), encoding="utf-8")

    def point(data: dict) -> None:
        for e in (data.get("engines") or {}).values():
            if isinstance(e, dict) and e.get("library") == library_rel:
                e["library"] = full_rel

    edit_config(home, point)
    if cfg.db.is_file():
        Store(cfg.db).remap_line_rows(eid, {r["n"]: f"{fid}/{plan['ids'][r['n']]}" for r in rows})
    for src, text in plan["stubs"].items():
        src.write_text(text, encoding="utf-8")
    out = [f"{library_rel}: converted into the process library: {full_rel} and {len(rows)} sub-process(es) in "
           f"{processes.DIR}/sub/ ({library_rel}.bak keeps the original table; the old card files now point to their new place)"]
    out += [f"{p}: lists `{fid}` under `## Processes used`" for p in lanes]
    return out


def migrate(cfg: Config) -> list[str]:
    report: list[str] = []
    done: set[str] = set()
    for eid, e in cfg.engines.items():
        if e.implicit or not e.library or e.library in done:
            continue
        path = cfg.home / e.library
        text = _read(path)
        if not text or processes.is_full(text):
            continue
        skills = engines.skills_root(cfg.home, e.skills)
        rows = engines.build(path, skills)
        if not rows:
            continue
        done.add(e.library)
        try:
            report += _convert(cfg, eid, rows, e.library, skills)
        except Skipped as why:
            report.append(f"{e.library}: not converted ({why}); it keeps working as an old pattern library")
    return report
