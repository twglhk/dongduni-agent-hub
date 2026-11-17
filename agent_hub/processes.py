from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from . import engines

DIR = "processes"
LANES_DIR = ".tower/lanes"
ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRAME = ("planning", "production", "qa")
COLUMNS = ("frame", "sub", "what", "model", "old")
_TICK = re.compile(r"`([^`]+)`")
_SKILL_DIR = re.compile(r"(?:^|/)skills/([A-Za-z0-9_-]+)(?:/|$)")
_USED = re.compile(r"^##\s+Processes used\s*$", re.I)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def is_full(text: str) -> bool:
    return any(ln.lower().startswith("# full process:") for ln in text.splitlines()[:3])


def _line_value(text: str, key: str) -> str:
    return next((ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.lower().startswith(key.lower() + ":")), "")


def parse_full(text: str, fid: str) -> dict[str, Any]:
    title = next((ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.lower().startswith("# full process:")), fid)
    cols: dict[str, int] = {}
    steps = []
    for ln in text.splitlines():
        if not ln.lstrip().startswith("|"):
            continue
        cs = engines._cells(ln)
        if cs and cs[0] == "#":
            for i, c in enumerate(cs):
                for k in COLUMNS:
                    if c.lower().startswith(k) and k not in cols:
                        cols[k] = i
            continue
        if not cols or not cs or not cs[0].isdigit():
            continue

        def cell(k: str) -> str:
            return cs[cols[k]] if k in cols and cols[k] < len(cs) else ""

        steps.append({"n": int(cs[0]), "frame": cell("frame").lower(), "sub": cell("sub").strip("`* ").strip(),
                      "what": cell("what"), "model": cell("model"), "old": cell("old").strip("`#* ").upper()})
    return {"id": fid, "title": title, "deliverable": _line_value(text, "Deliverable"),
            "frame_exception": _line_value(text, "Frame"), "steps": steps}


def card_skills(text: str) -> list[str]:
    out: list[str] = []
    for ln in text.splitlines():
        s = ln.strip().lstrip("-* ").strip()
        if re.match(r"^skills?\s*:", s, re.I):
            out += _skill_names(s.split(":", 1)[1])
    return list(dict.fromkeys(out))


def _skill_names(cell: str) -> list[str]:
    toks = _TICK.findall(cell) or [t.strip() for t in re.split(r"[,;]", cell) if t.strip()]
    out = []
    for t in toks:
        m = _SKILL_DIR.search(t)
        if m:
            out.append(m.group(1))
        elif t.endswith(".md") or re.fullmatch(r"[A-Za-z0-9_-]+", t):
            out.append(t)
    return out


def lane_uses(text: str) -> list[str] | None:
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if _USED.match(ln.strip())), None)
    if start is None:
        return None
    out = []
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        if ln.lstrip().startswith(("- ", "* ")):
            m = _TICK.search(ln)
            if m and ID.match(m.group(1)):
                out.append(m.group(1))
    return out


def lane_texts(home: Path) -> dict[str, str]:
    try:
        files = sorted((home / LANES_DIR).glob("*.md"))
    except OSError:
        files = []
    return {f.stem: _read(f) for f in files}


def _sub(home: Path, sid: str) -> dict[str, Any] | None:
    rel = f"{DIR}/sub/{sid}/process.md"
    text = _read(home / rel)
    if not text:
        return None
    card = engines.parse_process(home, "", rel)
    skills = card_skills(text)
    cells = _stage_cells(text)
    stages = []
    for s in card["stages"]:
        row = cells.get(s["n"], {})
        c = s["card"]
        if c is None and (m := engines._PATTERN.search(row.get("pattern", ""))):
            c = engines.pattern_card(home, f"{DIR}/sub/{sid}/{m.group(1)}")
        stages.append({"n": s["n"], "name": s["name"], "input": s["input"], "output": s["output"], "gate": s["gate"],
                       "by": s["by"], "pattern": ({k: c[k] for k in ("path", "exists", "version", "status", "color")} if c else None),
                       "skills": _skill_names(row.get("skill", "")) or skills})
    pattern_status = engines._pattern_status({"pattern_text": ""}, card)
    return {"id": sid, "title": card["type"], "version": card["version"], "status": card["status"], "file": rel,
            "pattern_status": pattern_status, "confirmed": card["status"] == "confirmed" and pattern_status in engines.GREEN,
            "skills": skills, "stages": stages}


def _stage_cells(text: str) -> dict[str, dict[str, str]]:
    head: list[str] = []
    out: dict[str, dict[str, str]] = {}
    for ln in text.splitlines():
        if not ln.strip().startswith("|"):
            if out:
                break
            continue
        cs = engines._cells(ln)
        if not head:
            if any(c.lower().startswith("stage") for c in cs):
                head = [c.lower() for c in cs]
            continue
        if cs and cs[0] and not re.fullmatch(r":?-{2,}:?", cs[0]):
            out[cs[0]] = {h.split()[0] if h else h: cs[i] for i, h in enumerate(head) if i < len(cs)}
    return out


def load(home: Path, lanes: dict[str, str] | None = None) -> dict[str, Any]:
    root = home / DIR
    lanes = lane_texts(home) if lanes is None else lanes
    subs: dict[str, dict[str, Any]] = {}
    for d in sorted(p for p in (root / "sub").glob("*") if p.is_dir()) if (root / "sub").is_dir() else []:
        s = _sub(home, d.name)
        if s:
            subs[d.name] = {**s, "used_by_full": [], "used_by_lanes": []}
    fulls: dict[str, dict[str, Any]] = {}
    for f in sorted((root / "full").glob("*.md")) if (root / "full").is_dir() else []:
        fulls[f.stem] = {**parse_full(_read(f), f.stem), "file": f"{DIR}/full/{f.name}", "lanes": []}
    problems: list[str] = []
    uses: dict[str, list[str]] = {}
    for lid, text in lanes.items():
        u = lane_uses(text)
        if u is None:
            continue
        uses[lid] = u
        for fid in u:
            if fid in fulls:
                fulls[fid]["lanes"].append(lid)
            else:
                problems.append(f"lane {lid} uses full process `{fid}`, which is not in {DIR}/full/")
    for fid, f in fulls.items():
        if not ID.match(fid):
            problems.append(f"full process file `{fid}.md`: the id must be lowercase words joined by hyphens")
        for st in f["steps"]:
            st["known"] = st["sub"] in subs
            if st["known"]:
                s = subs[st["sub"]]
                if fid not in s["used_by_full"]:
                    s["used_by_full"].append(fid)
                for lid in f["lanes"]:
                    if lid not in s["used_by_lanes"]:
                        s["used_by_lanes"].append(lid)
            else:
                problems.append(f"full process `{fid}` step {st['n']} names `{st['sub']}`, which is not in {DIR}/sub/")
        frames = [st["frame"] for st in f["steps"]]
        first = [frames.index(x) if x in frames else -1 for x in FRAME]
        if not f["frame_exception"] and (min(first) < 0 or first != sorted(first)):
            problems.append(f"full process `{fid}` does not hold planning, production and QA in that order "
                            f"(frames: {', '.join(frames) or 'none'}); add them or write `Frame: exception — <why>`")
        known = [subs[st["sub"]] for st in f["steps"] if st["known"]]
        f["status"] = "confirmed" if known and len(known) == len(f["steps"]) and all(s["confirmed"] for s in known) else \
            "draft" if f["steps"] else "empty"
    for sid, s in subs.items():
        if not ID.match(sid):
            problems.append(f"sub-process folder `{sid}`: the id must be lowercase words joined by hyphens")
        if not s["used_by_full"]:
            problems.append(f"sub-process `{sid}` is in no full process")
        for st in s["stages"]:
            if st["pattern"] and not st["pattern"]["exists"]:
                problems.append(f"sub-process `{sid}` stage {st['n']}: pattern card {st['pattern']['path']} is missing")
    readme = _read(root / "README.md")
    for kind, ids in (("full process", fulls), ("sub-process", subs)):
        for i in ids:
            if readme and f"`{i}`" not in readme:
                problems.append(f"{DIR}/README.md does not list the {kind} `{i}`")
    return {"full": fulls, "sub": subs, "lanes": uses, "problems": problems}


def view(home: Path, lanes: dict[str, str] | None = None, skills_dir: Path | None = None) -> dict[str, Any]:
    lib = load(home, lanes)
    skills_dir = skills_dir or engines.skills_root(home)

    def skill(n: str) -> dict[str, Any]:
        f = home / n if n.endswith(".md") else skills_dir / n / "SKILL.md"
        found = f.is_file() if n.endswith(".md") else (skills_dir / n).is_dir()
        return {"name": n, "found": found, "doc": _home_path(home, f) if f.is_file() else None}

    subs = [{**{k: s[k] for k in ("id", "title", "version", "status", "pattern_status", "confirmed", "file",
                                  "used_by_full", "used_by_lanes")},
             "skills": [skill(n) for n in s["skills"]],
             "stages": [{**{k: st[k] for k in ("n", "name", "input", "output", "gate", "by", "pattern")},
                         "skills": [skill(n) for n in st["skills"]]} for st in s["stages"]]}
            for s in lib["sub"].values()]
    fulls = [{**{k: f[k] for k in ("id", "title", "deliverable", "frame_exception", "file", "lanes", "status")},
              "steps": [{**st, "title": lib["sub"][st["sub"]]["title"] if st["known"] else None,
                         "status": lib["sub"][st["sub"]]["status"] if st["known"] else None} for st in f["steps"]]}
             for f in lib["full"].values()]
    return {"full": fulls, "sub": subs, "lanes": lib["lanes"], "problems": lib["problems"]}


def _home_path(home: Path, f: Path) -> str | None:
    try:
        return Path(os.path.normpath(f)).relative_to(os.path.normpath(home)).as_posix()
    except ValueError:
        return None


def docs(v: dict[str, Any]) -> set[str]:
    out = {f["file"] for f in v["full"]} | {s["file"] for s in v["sub"]}
    for s in v["sub"]:
        out |= {k["doc"] for k in s["skills"] if k["doc"]}
        for st in s["stages"]:
            out |= {k["doc"] for k in st["skills"] if k["doc"]}
            if st["pattern"] and st["pattern"]["exists"]:
                out.add(st["pattern"]["path"])
    return out


def engine_rows(home: Path, fid: str, skills: Path) -> list[dict[str, Any]]:
    lib = load(home)
    f = lib["full"].get(fid)
    if not f:
        return []
    out = []
    for st in f["steps"]:
        s = lib["sub"].get(st["sub"])
        rel = engines._rel(skills, home / s["file"]) if s else ""
        skill = s["skills"][0] if s and s["skills"] else ""
        proc = engines.parse_process(skills, skill, rel) if s else None
        out.append({"n": f"{fid}/{st['sub']}", "old": st["old"], "type": (s["title"] if s else st["sub"]),
                    "lane": (f["lanes"] or [""])[0], "lanes": list(f["lanes"]), "skill": skill, "process_text": rel,
                    "pattern_text": "", "model_text": st["model"], "process": proc,
                    "process_status": (s["status"] if s else ""), "pattern_status": (s["pattern_status"] if s else "")})
    return out


def step_model(home: Path, fid: str, sid: str) -> str:
    f = parse_full(_read(home / DIR / "full" / f"{fid}.md"), fid)
    return next((st["model"] for st in f["steps"] if st["sub"] == sid), "")


def full_file(home: Path, rel: str) -> tuple[str, list[dict[str, Any]]] | None:
    path = home / rel
    if not rel or path.parent.name != "full" or path.parent.parent.name != DIR:
        return None
    text = _read(path)
    return (path.stem, parse_full(text, path.stem)["steps"]) if is_full(text) else None


def lane_full(cfg: Any, lane: str) -> tuple[str, list[dict[str, Any]]] | None:
    ln = cfg.all_lanes.get(lane)
    eng = cfg.engines.get(ln.engine) if ln and ln.engine else None
    return full_file(cfg.home, eng.library) if eng and eng.library else None


def main(argv: list[str]) -> None:
    home = Path(argv[0]) if argv else Path.cwd()
    print(json.dumps(view(home), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
