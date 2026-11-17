from __future__ import annotations

import re
from pathlib import Path

from .config import Config

MODEL_RANK = {"haiku": 0, "sonnet": 1, "opus": 2, "fable": 3}
MODEL_ALIASES = tuple(MODEL_RANK)
EFFORT_RANK = {"low": 0, "medium": 1, "high": 2, "xhigh": 3, "max": 4}
_EFFORTS = "low|medium|high|xhigh|max"
_ENTRY = re.compile(rf"^(?:(\d+)(?:\s*-\s*(\d+))?\s*[:=]?\s+)?([\w.\-]+)\s*/\s*({_EFFORTS})$", re.I)
_LINE = re.compile(r"^line:\s*(?:row\s*|#)?([A-Za-z]?\d+)\b[^\n]*?\bstage\s*(\d+)(?:\s*-\s*(\d+))?", re.M | re.I)
_MODEL = re.compile(r"^\s*model:\s*([\w.\-/]+)", re.M | re.I)
_EFFORT = re.compile(rf"^\s*effort:\s*({_EFFORTS})\b", re.M | re.I)


def missing_lines(brief: str) -> list[str]:
    return [n for n, rx in (("model: <name>", _MODEL), ("effort: low|medium|high|xhigh|max", _EFFORT)) if not rx.search(brief)]


def problems(model: str | None, effort: str | None) -> list[str]:
    out = []
    if model and model.lower() not in MODEL_ALIASES and not re.search(r"[-./\d]", model):
        out.append(f"model {model!r}: use fable, opus, sonnet, haiku or a full model id")
    if effort and effort.lower() not in EFFORT_RANK:
        out.append(f"effort {effort!r}: use low, medium, high, xhigh or max")
    return out


def _entries(cell: str) -> list[tuple[int, int, str, str]]:
    out = []
    for part in re.split(r"[;,]", cell):
        m = _ENTRY.match(part.strip().strip("`"))
        if m:
            a = int(m[1]) if m[1] else 0
            b = int(m[2]) if m[2] else (a if m[1] else 99)
            out.append((a, b, m[3].lower(), m[4].lower()))
    return out


def _table(path: Path) -> dict[str, list[tuple[int, int, str, str]]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    col, out = -1, {}
    for ln in text.splitlines():
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if col < 0 and cells and cells[0] == "#":
            col = next((i for i, c in enumerate(cells) if "model" in c.lower()), -1)
            continue
        if col < 0 or col >= len(cells) or not re.fullmatch(r"[A-Za-z]?\d+", cells[0]):
            continue
        if ents := _entries(cells[col]):
            out[cells[0].upper()] = ents
    return out


def from_table(path: Path, row: int | str, first: int | None, last: int | None) -> tuple[str | None, str | None]:
    return _pick(_table(path).get(str(row).upper()) or [], first, last)


def _pick(ents: list[tuple[int, int, str, str]], first: int | None, last: int | None) -> tuple[str | None, str | None]:
    if not ents:
        return None, None
    lo, hi = (first, last) if first is not None and last is not None else (0, 99)
    hit = [e for e in ents if e[0] <= hi and e[1] >= lo]
    if not hit:
        return None, None
    return (max((e[2] for e in hit), key=lambda m: MODEL_RANK.get(m, -1)),
            max((e[3] for e in hit), key=EFFORT_RANK.__getitem__))


def table_for(cfg: Config, lane: str) -> Path | None:
    ln = cfg.all_lanes.get(lane)
    engine = cfg.engines.get(ln.engine) if ln and ln.engine else None
    rel = (engine.library if engine else "") or cfg.stage_table
    return cfg.home / rel if rel else None


def _is_old_table(table: Path) -> bool:
    from . import processes
    try:
        return table.is_file() and not processes.is_full(table.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return False


def library_for(cfg: Config) -> Path | None:
    from . import processes
    return cfg.home / processes.DIR if (cfg.home / processes.DIR).is_dir() else None


def _from_library(library: Path, brief: str, table: Path | None) -> tuple[str | None, str | None]:
    from . import line, processes
    home = library.parent
    head = next((ln for ln in brief.splitlines() if ln.strip().lower().startswith("line:")), "")
    legacy = processes.full_file(home, table.relative_to(home).as_posix()) if table and table.is_relative_to(home) else None
    try:
        p = line.parse(line.translate_legacy(head, legacy))
    except ValueError:
        return None, None
    if not p["sub"] or p["full_new"] or p["sub_new"]:
        return None, None
    a, _, b = (p["stage"] or "").partition("-")
    first = int(a) if a else None
    old = line.old_row(head)
    step = next((s for s in legacy[1] if s["old"] == old), None) if legacy and old else None
    cell = step["model"] if step else processes.step_model(home, p["full"], p["sub"])
    return _pick(_entries(cell), first, int(b) if b else first)


def resolve(table: Path | None, brief: str, library: Path | None = None) -> tuple[str | None, str | None]:
    em, ee = _MODEL.search(brief), _EFFORT.search(brief)
    model, effort = (em[1] if em else None), (ee[1].lower() if ee else None)
    if model is not None and effort is not None:
        return model, effort
    if library is not None:
        tm, te = _from_library(library, brief, table)
        model, effort = model or tm, effort or te
    if table and _is_old_table(table) and (model is None or effort is None):
        m = _LINE.search(brief)
        if m:
            first = int(m[2])
            tm, te = from_table(table, m[1], first, int(m[3]) if m[3] else first)
            model, effort = model or tm, effort or te
    return model, effort
