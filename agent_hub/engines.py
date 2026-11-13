"""Process cards, pattern cards and the pattern library of an engine; the `Line:` header of a brief. No LLM, standard library only.

An engine's library is one markdown table (`engine.library`), a row per kind of deliverable:

    | # | Type | Lane · skill | Process | Pattern | Model · effort |
    | 1 | Storyboard | story · layout | confirmed v2 (process/storyboard.md) | confirmed | 1 sonnet/medium; 2-3 opus/high |

The columns are found by header name. A process card is `<skills>/<skill>/references/process/<name>.md`: a first line
`# Process: <type> — v2`, a `Status: confirmed` line and a table of stages (columns `#`, `Stage`, `Input`, `Output`,
`Gate`, `Passed by`, `Pattern card`). A pattern card is the file the `Pattern card` cell names (`patterns/<name>.md`,
below the skill's `references/`): a title ending `— v2` and a `## Status` heading followed by the status line.
Statuses: confirmed and tuned are green, collecting, draft and reviewed amber, anything else (or no file) grey.

A brief starts with a `Line:` header that says which row and stage the task works on:

    Line: #3 Storyboard · stage 2 · process confirmed · pattern confirmed · make
    Line: #3 Storyboard · stage 1-2 · process draft · pattern draft · prepare
    Line: none · chore: move the exports folder

Modes: `prepare` (build or fix the method itself), `make` (run it), `fix` (send-back of earlier work). `make` is refused
while the row's process is not confirmed or its pattern is neither confirmed nor tuned. `config.require_line` turns the
gate on for `dah task`; without it the header is read when it is there and nothing is refused.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MODES = ("prepare", "make", "fix")
GREEN = ("confirmed", "tuned")
AMBER = ("collecting", "draft", "reviewed")
FORMAT = ("Line: #<row> <type> · stage <n|n-m> · process <status> · pattern <status> · prepare|make|fix\n"
          "   or, for chores only: Line: none · chore: <reason>")

_ID = r"[A-Za-z]?\d+"
_HEAD = re.compile(rf"^line:\s*#?({_ID})\b", re.I)
_NONE = re.compile(r"^line:\s*none\s*[·,:-]\s*chore:\s*\S", re.I)
_STAGE = re.compile(r"(?:^|[·:])\s*stage\s+(\d+(?:\s*[-–]\s*\d+)?)(?![\d-])(?=\s|·|$)", re.I)
_VERSION = re.compile(r"[—–-]\s*v(\d+)\s*$")
_CARD = re.compile(r"process/([A-Za-z0-9_-]+)\.md")
_PATTERN = re.compile(r"(patterns/[A-Za-z0-9_.-]+\.md)")
_ROW_ID = re.compile(rf"^{_ID}$")
SKILLS_DEFAULT = ".agents/skills"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _first_word(text: str) -> str:
    m = re.match(r"[A-Za-z]+", text.strip().strip("*`").lower())
    return m.group(0) if m else ""


def _version(title: str) -> int | None:
    m = _VERSION.search(title.strip())
    return int(m.group(1)) if m else None


def library(path: Path) -> list[dict[str, str]]:
    """Rows of the library table: {n, type, lane, skill, process_text, pattern_text, model_text}. Missing file -> []."""
    rows: list[dict[str, str]] = []
    cols: dict[str, int] = {}
    names = {"type": "type", "lane": "lane", "process": "process", "pattern": "pattern", "model": "model"}
    for ln in _read(path).splitlines():
        if not ln.lstrip().startswith("|"):
            continue
        cs = _cells(ln)
        if cs and cs[0] == "#":
            cols = {v: i for i, c in enumerate(cs) for k, v in names.items() if c.lower().startswith(k) and v not in cols}
            continue
        if not cols or not cs or not _ROW_ID.match(cs[0]):
            continue
        g = lambda k: cs[cols[k]] if k in cols and cols[k] < len(cs) else ""  # noqa: E731
        lane, _, skill = g("lane").partition("·")
        rows.append({"n": cs[0].upper(), "type": g("type"), "lane": lane.strip(), "skill": skill.strip(),
                     "process_text": g("process"), "pattern_text": g("pattern"), "model_text": g("model")})
    return rows


def pattern_card(root: Path, rel: str) -> dict[str, Any]:
    """{path, exists, version, status, color}. A missing file is `exists: False`."""
    text = _read(root / rel)
    if not text:
        return {"path": rel, "exists": False, "version": None, "status": "", "color": "grey"}
    lines = text.splitlines()
    title = next((ln[1:].strip() for ln in lines if ln.startswith("# ")), "")
    status = ""
    for i, ln in enumerate(lines):
        if re.match(r"^##\s+Status\b", ln):
            status = next((x.strip() for x in lines[i + 1:] if x.strip() and not x.startswith("#")), "")
            break
    word = _first_word(status)
    return {"path": rel, "exists": True, "version": _version(title), "status": word,
            "color": "green" if word in GREEN else "amber" if word in AMBER else "grey"}


def parse_process(root: Path, skill: str, rel: str) -> dict[str, Any] | None:
    """A process card: {path, type, version, status, stages: [{n, name, input, output, gate, by, card}]}; None if no file.
    `root` is the skills directory; pattern card paths are resolved below `<root>/<skill>/references/`."""
    text = _read(root / rel)
    if not text:
        return None
    lines = text.splitlines()
    title = next((ln[1:].strip() for ln in lines if ln.startswith("# ")), "")
    status_line = next((ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("status:")), "")
    stages: list[dict[str, Any]] = []
    head: list[str] = []
    for ln in lines:
        if not ln.strip().startswith("|"):
            if stages:
                break
            continue
        cs = _cells(ln)
        if not head:
            if any(c.lower().startswith("stage") for c in cs):
                head = [c.lower() for c in cs]
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cs if c):
            continue
        col = lambda *keys: next((cs[i] for i, h in enumerate(head) if i < len(cs) and h.startswith(keys)), "")  # noqa: E731
        m = _PATTERN.search(col("pattern"))
        card = pattern_card(root, f"{skill}/references/{m.group(1)}") if m and skill else None
        stages.append({"n": col("#"), "name": col("stage").replace("`", ""), "input": col("input").replace("`", ""),
                       "output": col("output").replace("`", ""), "gate": col("gate"), "by": col("passed"), "card": card})
    return {"path": rel, "type": re.sub(r"^Process:\s*", "", _VERSION.sub("", title)).strip(), "version": _version(title),
            "status": _first_word(status_line), "stages": stages}


def build(library_path: Path, skills: Path) -> list[dict[str, Any]]:
    """Every library row with its process card read in. `status` fields are the card's word when a card is found, else the
    table cell's first word; `process` is the parsed card or None."""
    out = []
    for r in library(library_path):
        m = _CARD.search(r["process_text"])
        proc = parse_process(skills, r["skill"], f"{r['skill']}/references/process/{m.group(1)}.md") if m and r["skill"] else None
        out.append({**r, "process": proc,
                    "process_status": (proc["status"] if proc else "") or _first_word(r["process_text"]),
                    "pattern_status": _pattern_status(r, proc)})
    return out


def _pattern_status(row: dict[str, str], proc: dict[str, Any] | None) -> str:
    cards = [s["card"] for s in (proc or {}).get("stages", []) if s.get("card")]
    if cards and all(c["exists"] for c in cards):
        words = {c["status"] for c in cards}
        if words <= set(GREEN):
            return "confirmed"
        return next((w for w in ("collecting", "draft", "reviewed") if w in words), "draft")
    return _first_word(row["pattern_text"])


def confirmed(row: dict[str, Any]) -> bool:
    """Process confirmed, and pattern confirmed or tuned."""
    return row["process_status"] == "confirmed" and row["pattern_status"] in GREEN


# ---------------------------------------------------------------- what a lane runs

_SKILL_CALL = re.compile(r"""skill\(\s*["']([A-Za-z0-9_-]+)["']\s*\)""")


def lane_skills(role_text: str, skills: Path, rows: list[dict[str, Any]] | None = None, lane: str = "") -> list[dict[str, Any]]:
    """Skills a lane runs: `skill("name")` mentions in its role file, plus the skill column of the library rows it owns.
    `found` says whether a folder of that name exists in the skills directory."""
    names = list(dict.fromkeys(_SKILL_CALL.findall(role_text)))
    for r in rows or []:
        if r["lane"] == lane and r["skill"] and r["skill"] not in names:
            names.append(r["skill"])
    return [{"name": n, "found": (skills / n).is_dir()} for n in names]


def row_view(rows: list[dict[str, Any]], row: str | None, stage: str | None) -> dict[str, Any] | None:
    """The process a task runs: {row, type, version, status, stages: [{n, name}], now} for library row `row`; None when unknown.
    `now` is the stage the lane named with `dah stage`, else the one the Line header names."""
    hit = next((r for r in rows if row and r["n"] == str(row).upper()), None)
    if hit is None:
        return None
    p = hit["process"]
    return {"row": hit["n"], "type": hit["type"], "version": p["version"] if p else None, "status": hit["process_status"],
            "stages": [{"n": s["n"], "name": s["name"]} for s in p["stages"]] if p else [], "now": stage or None}


# ---------------------------------------------------------------- the Line: header

def first_line(brief: str) -> str:
    return next((ln.strip() for ln in brief.splitlines() if ln.strip()), "")


def stage_of(brief: str) -> str | None:
    """`stage 3` or `stage 2-4` on the header -> "3" / "2-4"; None when it names none."""
    m = _STAGE.search(first_line(brief))
    return re.sub(r"\s+", "", m.group(1)).replace("–", "-") if m else None


def parse_line(brief: str) -> tuple[str | None, str | None, str | None]:
    """(row, mode, error). A chore header gives (None, None, None); an error is a sentence for the human."""
    head = first_line(brief)
    if _NONE.match(head):
        return None, None, None
    m = _HEAD.match(head)
    if not m:
        return None, None, "the first line of the brief does not start with `Line:`"
    words = re.findall("|".join(MODES), head.lower())
    if not words:
        return None, None, "the Line header ends without prepare, make or fix"
    return m.group(1).upper(), words[-1], None


def line_fields(brief: str) -> dict[str, str | None]:
    """What the task row keeps of the header: line_row, line_stage, line_mode. All None when the brief has no valid header."""
    row, mode, err = parse_line(brief)
    if err or row is None:
        return {"line_row": None, "line_stage": None, "line_mode": None}
    return {"line_row": row, "line_stage": stage_of(brief), "line_mode": mode}


def gate(brief: str, rows: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Pass -> (row, mode); a chore -> (None, None). Refuse -> ValueError with the reason and the expected shape."""
    row, mode, err = parse_line(brief)
    if err:
        raise ValueError(f"{err}\nStart the brief with:\n{FORMAT}")
    if row is None:
        return None, None
    hit = next((r for r in rows if r["n"] == row), None)
    if hit is None:
        raise ValueError(f"row #{row} is not in the library (rows: {', '.join(r['n'] for r in rows) or 'none'})")
    if mode != "prepare" and stage_of(brief) is None:
        raise ValueError("the Line header has no stage: write `stage <n>` or `stage <n-m>` (numbers from the process card)")
    if mode == "make" and not confirmed(hit):
        raise ValueError(f"row #{row} ({hit['type']}) is not confirmed: process {hit['process_status'] or 'missing'}, "
                         f"pattern {hit['pattern_status'] or 'missing'}. Prepare the method first (`prepare`), or ask the hub.")
    return row, mode


# ---------------------------------------------------------------- text view

def pipelines(rows: list[dict[str, Any]]) -> str:
    """`dah engine show` body: each row as a pipeline of stages with the status of its cards."""
    if not rows:
        return "  (no library rows)"
    out = []
    for r in rows:
        p = r["process"]
        out.append(f"  #{r['n']} {r['type']}  [{r['lane'] or '-'}"
                   + (f" · {r['skill']}" if r["skill"] else "") + f"]  process {r['process_status'] or 'missing'}"
                   + (f" v{p['version']}" if p and p["version"] else "") + f" · pattern {r['pattern_status'] or 'missing'}"
                   + (f" · model {r['model_text']}" if r["model_text"] else ""))
        if not p:
            out.append("      (no process card found)")
            continue
        for s in p["stages"]:
            c = s["card"]
            out.append(f"      {s['n'] or '-'}. {s['name']}  →  {s['output'] or '-'}"
                       + (f"  [{c['status'] or 'no status'}{' v' + str(c['version']) if c['version'] else ''}]" if c and c["exists"]
                          else "  [no pattern card]" if c else ""))
    return "\n".join(out)
