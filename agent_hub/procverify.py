from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import procs

VERIFIED_FILE = "processes/verified.json"


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def fingerprint(home: Path, full: dict[str, Any], subs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    files: dict[str, str] = {}
    for st in full["steps"]:
        s = subs.get(st["sub"])
        if not s:
            continue
        files[s["file"]] = _sha(home / s["file"])
        for stage in s["stages"]:
            p = stage.get("pattern")
            if p and p.get("path"):
                files[p["path"]] = _sha(home / p["path"])
    return {"steps": [st["sub"] for st in full["steps"]], "files": dict(sorted(files.items()))}


def records(home: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads((home / VERIFIED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def changed(rec: dict[str, Any], now: dict[str, Any]) -> list[str]:
    out = []
    if rec.get("steps") != now["steps"]:
        out.append("the order of sub-processes changed")
    old = rec.get("files") or {}
    for p in sorted(set(old) | set(now["files"])):
        if old.get(p) != now["files"].get(p):
            out.append(f"{p} " + ("is new" if p not in old else "is gone" if p not in now["files"] else "changed"))
    return out


def mark(home: Path, fulls: dict[str, dict[str, Any]], subs: dict[str, dict[str, Any]]) -> None:
    recs = records(home)
    for fid, f in fulls.items():
        rec = recs.get(fid)
        f["verified"] = rec if isinstance(rec, dict) else None
        f["reverify"], f["changes"] = False, []
        if not f["verified"]:
            continue
        diff = changed(f["verified"], fingerprint(home, f, subs))
        if diff:
            f["reverify"], f["changes"] = True, diff
        elif f["status"] == "confirmed":
            f["status"] = "verified"


def git_head(home: Path) -> str:
    r = procs.capture(["git", "-C", str(home), "rev-parse", "--short", "HEAD"], 10)
    return r.stdout.strip() if r is not None and r.returncode == 0 else ""


def write(home: Path, full: str, rec: dict[str, Any]) -> None:
    recs = records(home)
    recs[full] = rec
    path = home / VERIFIED_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(recs, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
