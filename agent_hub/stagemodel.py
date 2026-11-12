"""Model and effort per task.

The hub names them in the brief, one line each:

    model: opus
    effort: high

A brief line always wins. Optional second source: a markdown table named in `tower.json` as `stage_table`
(a row per kind of work, one column whose header contains "model"). The brief points at a row and stages with a
line like `Line: row 4 stage 2-3` (or `Line: #4 stage 2`). A cell is one `opus/high` for the whole row or
per-stage entries `1-2 sonnet/medium; 3 opus/high`. Across a range of stages the strongest model and the highest
effort win. Names are not checked here: whatever the runtime accepts passes through (`opus`, `gpt-5`, ...);
the rank tables only order the ones they know.

No table, no cell, or no line gives (None, None) and the lane's own configured model applies.
"""

from __future__ import annotations

import re
from pathlib import Path

MODEL_RANK = {"haiku": 0, "sonnet": 1, "opus": 2}
EFFORT_RANK = {"low": 0, "medium": 1, "high": 2, "xhigh": 3, "max": 4}
_EFFORTS = "low|medium|high|xhigh|max"
_ENTRY = re.compile(rf"^(?:(\d+)(?:\s*-\s*(\d+))?\s*[:=]?\s+)?([\w.\-]+)\s*/\s*({_EFFORTS})$", re.I)
_LINE = re.compile(r"^line:\s*(?:row\s*|#)?(\d+)\b[^\n]*?\bstage\s*(\d+)(?:\s*-\s*(\d+))?", re.M | re.I)
_MODEL = re.compile(r"^\s*model:\s*([\w.\-/]+)", re.M | re.I)
_EFFORT = re.compile(rf"^\s*effort:\s*({_EFFORTS})\b", re.M | re.I)


def missing_lines(brief: str) -> list[str]:
    """Which of the brief's `model:` / `effort:` lines are absent."""
    return [n for n, rx in (("model: <name>", _MODEL), ("effort: low|medium|high|xhigh|max", _EFFORT)) if not rx.search(brief)]


def _entries(cell: str) -> list[tuple[int, int, str, str]]:
    out = []
    for part in re.split(r"[;,]", cell):
        m = _ENTRY.match(part.strip().strip("`"))
        if m:
            a = int(m[1]) if m[1] else 0
            b = int(m[2]) if m[2] else (a if m[1] else 99)
            out.append((a, b, m[3].lower(), m[4].lower()))
    return out


def _table(path: Path) -> dict[int, list[tuple[int, int, str, str]]]:
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
        if col < 0 or col >= len(cells) or not cells[0].isdigit():
            continue
        if ents := _entries(cells[col]):
            out[int(cells[0])] = ents
    return out


def from_table(path: Path, row: int, first: int | None, last: int | None) -> tuple[str | None, str | None]:
    ents = _table(path).get(row)
    if not ents:
        return None, None
    lo, hi = (first, last) if first is not None and last is not None else (0, 99)
    hit = [e for e in ents if e[0] <= hi and e[1] >= lo]
    if not hit:
        return None, None
    return (max((e[2] for e in hit), key=lambda m: MODEL_RANK.get(m, -1)),
            max((e[3] for e in hit), key=EFFORT_RANK.__getitem__))


def resolve(table: Path | None, brief: str) -> tuple[str | None, str | None]:
    """(model, effort) for a brief: explicit lines first, then the table row the `Line:` line names."""
    em, ee = _MODEL.search(brief), _EFFORT.search(brief)
    model, effort = (em[1] if em else None), (ee[1].lower() if ee else None)
    if table and (model is None or effort is None):
        m = _LINE.search(brief)
        if m:
            first = int(m[2])
            tm, te = from_table(table, int(m[1]), first, int(m[3]) if m[3] else first)
            model, effort = model or tm, effort or te
    return model, effort
