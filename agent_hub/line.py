from __future__ import annotations

import re
from typing import Any

from . import engines

MODES = ("prepare", "make", "fix")
FORMAT = ("Line: <full process> / <sub-process> · stage <n|n-m> · prepare|make|fix\n"
          "   or, for a process the library does not have yet: Line: <full process> / new <name> · prepare,"
          " Line: new <name> [/ <sub-process>] · prepare\n"
          "   or, for chores only: Line: none · chore: <reason>")
ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_LINE = re.compile(r"^line:\s*(.*)$", re.I)
_NONE = re.compile(r"^none\s*[·,:-]\s*chore:\s*\S", re.I)
_STAGE = re.compile(r"^stage\s+(\d+(?:\s*[-–]\s*\d+)?)$", re.I)
_OLD_ROW = re.compile(r"^#?([A-Za-z]?\d+)(?=\s|$)")


def first_line(brief: str) -> str:
    return next((ln.strip() for ln in brief.splitlines() if ln.strip()), "")


def is_library_line(brief: str) -> bool:
    m = _LINE.match(first_line(brief))
    if not m:
        return False
    head = m.group(1).split("·")[0].strip()
    return not _OLD_ROW.match(head) and ("/" in head or head.lower().startswith("new ") or bool(ID.match(head)))


def _name(part: str) -> tuple[str, bool]:
    part = part.strip()
    if part.lower().startswith("new ") or part.lower() == "new":
        return part[3:].strip(), True
    return part.strip("`"), False


def parse(brief: str) -> dict[str, Any]:
    m = _LINE.match(first_line(brief))
    if not m:
        raise ValueError("the first line of the brief does not start with `Line:`")
    rest = m.group(1).strip()
    if _NONE.match(rest):
        return {"chore": True, "full": None, "full_new": False, "sub": None, "sub_new": False, "stage": None, "mode": None}
    segs = [s.strip() for s in rest.split("·")]
    head = segs[0]
    if _OLD_ROW.match(head):
        raise ValueError(f"`{head}` is a row of an old pattern library, which this workspace's process library does not have "
                         f"(`dah update` converts pattern libraries): name a full process and a sub-process")
    full_part, slash, sub_part = head.partition("/")
    full, full_new = _name(full_part)
    sub, sub_new = _name(sub_part) if slash else (None, False)
    if not full or (slash and not sub):
        raise ValueError("the Line header names no full process, or a `/` with nothing after it"
                         + (": write `new <name>`" if full_new or sub_new else ""))
    for name, new in ((full, full_new), (sub, sub_new)):
        if name and not new and not ID.match(name):
            raise ValueError(f"`{name}` is not a library id (lowercase words joined by hyphens); for a process the library "
                             f"does not have yet write `new {name}`")
    stage = None
    for s in segs[1:]:
        if sm := _STAGE.match(s):
            stage = re.sub(r"\s+", "", sm.group(1)).replace("–", "-")
    modes = [s.split()[0].lower() for s in segs[1:] if s and s.split()[0].lower() in MODES]
    if not modes:
        raise ValueError("the Line header ends without prepare, make or fix")
    return {"chore": False, "full": full, "full_new": full_new, "sub": sub, "sub_new": sub_new, "stage": stage, "mode": modes[-1]}


def old_row(brief: str) -> str | None:
    m = _LINE.match(first_line(brief))
    r = _OLD_ROW.match(m.group(1).split("·")[0].strip()) if m else None
    return r.group(1).upper() if r else None


def translate_legacy(brief: str, full: tuple[str, list[dict[str, Any]]] | None) -> str:
    row = old_row(brief)
    if row is None or full is None:
        return brief
    fid, steps = full
    step = next((s for s in steps if s["old"] == row), None)
    if step is None:
        mapped = ", ".join(f"#{s['old']} = {s['sub']}" for s in steps if s["old"]) or "none"
        raise ValueError(f"`#{row}` is a row of the old pattern library, which the process library `{fid}` replaced "
                         f"(old rows: {mapped}); write `Line: {fid} / <sub-process> · stage <n> · prepare|make|fix`")
    _, mode, err = engines.parse_line(brief)
    if err or mode is None:
        raise ValueError(err or "the Line header ends without prepare, make or fix")
    stage = engines.stage_of(brief)
    head = f"Line: {fid} / {step['sub']}" + (f" · stage {stage}" if stage else "") + f" · {mode}"
    lines = brief.splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.strip())
    return "\n".join([*lines[:at], head, *lines[at + 1:]])


def fields(brief: str) -> dict[str, str | None]:
    none = {"line_row": None, "line_stage": None, "line_mode": None}
    try:
        p = parse(brief)
    except ValueError:
        return none
    if p["chore"] or p["full_new"] or p["sub_new"]:
        return none
    return {"line_row": p["full"] + (f"/{p['sub']}" if p["sub"] else ""), "line_stage": p["stage"], "line_mode": p["mode"]}


def _stage_numbers(stage: str) -> list[int]:
    a, _, b = stage.partition("-")
    return list(range(int(a), int(b or a) + 1))


def _match(name: str, ids: dict[str, Any]) -> str | None:
    key = re.sub(r"[\s_-]+", "", name.lower())
    return next((i for i, v in ids.items() if key in (re.sub(r"[\s_-]+", "", i), re.sub(r"[\s_-]+", "", str(v.get("title", "")).lower()))), None)


def gate(brief: str, lib: dict[str, Any]) -> dict[str, Any]:
    try:
        p = parse(brief)
    except ValueError as e:
        raise ValueError(f"{e}\nStart the brief with:\n{FORMAT}")
    if p["chore"]:
        return p
    fulls, subs = lib["full"], lib["sub"]
    if p["full_new"] and (same := _match(p["full"], fulls)):
        raise ValueError(f"the library already has the full process `{same}`: write `Line: {same} / ... · prepare`")
    if p["sub_new"] and (same := _match(p["sub"], subs)):
        raise ValueError(f"the library already has the sub-process `{same}`: name it without `new`")
    if not p["full_new"] and p["full"] not in fulls:
        raise ValueError(f"full process `{p['full']}` is not in the library (full processes: {', '.join(fulls) or 'none'}); "
                         f"to build it write `Line: new <name> · prepare`")
    if p["sub"] and not p["sub_new"] and p["sub"] not in subs:
        raise ValueError(f"sub-process `{p['sub']}` is not in the library (sub-processes: {', '.join(subs) or 'none'}); "
                         f"to build it write `Line: {p['full'] if not p['full_new'] else 'new ' + p['full']} / new <name> · prepare`")
    new = p["full_new"] or p["sub_new"]
    listed = not new and p["sub"] and p["sub"] in [s["sub"] for s in fulls[p["full"]]["steps"]]
    if p["mode"] != "prepare":
        if new:
            raise ValueError("a process that is not in the library yet has no cards, so it can only be prepared: end the header with `prepare`")
        if not p["sub"]:
            raise ValueError(f"`{p['mode']}` runs one sub-process: write `Line: {p['full']} / <sub-process> · stage <n> · {p['mode']}` "
                             f"(sub-processes of {p['full']}: {', '.join(s['sub'] for s in fulls[p['full']]['steps']) or 'none'})")
        if not listed:
            raise ValueError(f"`{p['sub']}` is not a step of `{p['full']}` (steps: {', '.join(s['sub'] for s in fulls[p['full']]['steps']) or 'none'}); "
                             f"add it to the full process first (`prepare`)")
        if p["stage"] is None:
            raise ValueError("the Line header has no stage: write `stage <n>` or `stage <n-m>` (numbers from the process card)")
        s = subs[p["sub"]]
        have = {str(st["n"]) for st in s["stages"]}
        if have and (miss := [n for n in _stage_numbers(p["stage"]) if str(n) not in have]):
            raise ValueError(f"`{p['sub']}` has no stage {', '.join(map(str, miss))} (stages: {', '.join(sorted(have, key=lambda x: (len(x), x)))})")
        if p["mode"] == "make" and not s["confirmed"]:
            raise ValueError(f"`{p['sub']}` is not confirmed: process {s['status'] or 'missing'}, pattern {s['pattern_status'] or 'missing'}. "
                             f"Prepare the method first (`prepare`), or ask the hub.")
    return p
