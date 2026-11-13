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

import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Config

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


# ---------------------------------------------------------------- the engine map: skills, hand-written part, snapshots

SNAP_KEEP = 60           # weekly snapshots kept per engine
HASH_FILES = 200         # files read per skill when fingerprinting
HASH_BYTES = 1 << 18


def read_map(path: Path) -> dict[str, Any]:
    """The hand-written part of an engine map (`engine.json`): {notes, tools: [{name, note}], experiments: [{name, status, note}]}.
    Anything else in the file is ignored; a missing or broken file gives empty lists."""
    try:
        raw = json.loads(_read(path) or "{}")
    except ValueError:
        raw = {}
    raw = raw if isinstance(raw, dict) else {}

    def rows(key: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
        v = raw.get(key)
        return [{f: str(x.get(f) or "") for f in fields} for x in (v if isinstance(v, list) else []) if isinstance(x, dict) and x.get("name")]

    return {"notes": str(raw.get("notes") or ""), "tools": rows("tools", ("name", "note")),
            "experiments": rows("experiments", ("name", "status", "note"))}


def skill_files(skills: Path, name: str) -> list[Path]:
    root = skills / name
    return sorted(p for p in root.rglob("*") if p.is_file() and not any(x.startswith(".") for x in p.relative_to(root).parts))[:HASH_FILES]


def skill_list(skills: Path, lane_skills_by_lane: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Every folder of the skills directory: {name, files, hash, lanes: [lane ids whose role file or library rows name it]}."""
    try:
        names = sorted(p.name for p in skills.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        names = []
    out = []
    for n in names:
        files = skill_files(skills, n)
        h = hashlib.sha1()
        for f in files:
            h.update(str(f.relative_to(skills)).encode())
            try:
                h.update(f.read_bytes()[:HASH_BYTES])
            except OSError:
                pass
        out.append({"name": n, "files": len(files), "hash": h.hexdigest()[:12],
                    "lanes": [ln for ln, ss in lane_skills_by_lane.items() if any(s["name"] == n for s in ss)]})
    return out


def _card_text(c: dict[str, Any] | None) -> str:
    return "" if not c or not c["exists"] else " ".join(x for x in (f"v{c['version']}" if c["version"] else "", c["status"]) if x)


def snapshot(lanes: list[str], skills: list[dict[str, Any]], rows: list[dict[str, Any]], now: float) -> dict[str, Any]:
    """What a week-to-week comparison reads: lanes, skill fingerprints, and per library row the card versions and statuses."""
    return {"at": now, "lanes": sorted(lanes), "skills": {s["name"]: s["hash"] for s in skills},
            "rows": {r["n"]: {"type": r["type"], "process": " ".join(x for x in (f"v{r['process']['version']}" if r["process"] and r["process"]["version"] else "",
                                                                                r["process_status"]) if x),
                              "stages": {str(s["n"]): [s["name"], _card_text(s["card"])] for s in (r["process"]["stages"] if r["process"] else [])}}
                     for r in rows}}


def diff(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, str]]:
    """Changes from snapshot `old` to `new`, as {kind, name, from, to} (the board words them in the reader's language).
    Kinds: lane_added/removed, skill_added/changed/removed, row_added/removed, process (version or status), card (a stage's pattern card)."""
    out: list[dict[str, str]] = []

    def add(kind: str, name: str, a: str = "", b: str = "") -> None:
        out.append({"kind": kind, "name": name, "from": a, "to": b})

    for ln in sorted(set(new["lanes"]) - set(old["lanes"])):
        add("lane_added", ln)
    for ln in sorted(set(old["lanes"]) - set(new["lanes"])):
        add("lane_removed", ln)
    for k in sorted(set(new["skills"]) - set(old["skills"])):
        add("skill_added", k)
    for k in sorted(set(old["skills"]) - set(new["skills"])):
        add("skill_removed", k)
    for k in sorted(set(new["skills"]) & set(old["skills"])):
        if new["skills"][k] != old["skills"][k]:
            add("skill_changed", k)
    for n in sorted(set(new["rows"]) - set(old["rows"])):
        add("row_added", new["rows"][n]["type"], "", n)
    for n in sorted(set(old["rows"]) - set(new["rows"])):
        add("row_removed", old["rows"][n]["type"], n)
    for n in sorted(set(new["rows"]) & set(old["rows"])):
        a, b = old["rows"][n], new["rows"][n]
        if a["process"] != b["process"]:
            add("process", b["type"], a["process"], b["process"])
        for s in sorted(set(a["stages"]) & set(b["stages"])):
            if a["stages"][s][1] != b["stages"][s][1]:
                add("card", f"{b['type']} · {s} {b['stages'][s][0]}", a["stages"][s][1], b["stages"][s][1])
    return out


def week_key(now: float) -> str:
    y, w, _ = datetime.fromtimestamp(now).isocalendar()
    return f"{y}-W{w:02d}"


def snap_dir(cfg: Config, eid: str) -> Path:
    return cfg.state_dir / "snapshots" / eid


def snap_files(cfg: Config, eid: str) -> list[Path]:
    try:
        return sorted(snap_dir(cfg, eid).glob("*.json"))
    except OSError:
        return []


def load_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and {"at", "lanes", "skills", "rows"} <= set(d) else None


def baseline(cfg: Config, eid: str, now: float) -> dict[str, Any] | None:
    """The snapshot "what changed" compares with: the newest one from an earlier week, else the oldest there is."""
    files = [(p, load_snapshot(p)) for p in snap_files(cfg, eid)]
    files = [(p, d) for p, d in files if d]
    if not files:
        return None
    this = week_key(now)
    older = [d for p, d in files if p.stem < this]
    return older[-1] if older else files[0][1]


def live_snapshot(cfg: Config, eid: str, now: float) -> dict[str, Any]:
    e = cfg.engines[eid]
    skills_dir = cfg.home / (e.skills or SKILLS_DEFAULT)
    rows = build(cfg.home / e.library, skills_dir) if e.library else []
    return snapshot(list(e.lanes), skill_list(skills_dir, {}), rows, now)


def weekly(cfg: Config, now: float | None = None, log: Any = None) -> list[str]:
    """Write this ISO week's snapshot of every engine that has none yet (the tower calls this from its tick; cheap when done).
    Returns the files written. Old snapshots beyond SNAP_KEEP are removed."""
    now = time.time() if now is None else now
    wrote: list[str] = []
    for eid in cfg.engines:
        path = snap_dir(cfg, eid) / f"{week_key(now)}.json"
        if path.exists():
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(live_snapshot(cfg, eid, now), ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
            for old in snap_files(cfg, eid)[:-SNAP_KEEP]:
                old.unlink(missing_ok=True)
            wrote.append(str(path))
        except OSError as ex:
            if log:
                log(f"weekly snapshot of engine {eid} failed: {ex}")
    return wrote
