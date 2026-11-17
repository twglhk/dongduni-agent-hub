from __future__ import annotations

import json
import re
from typing import Any

from .store import Store

VERSION = 1

TABS = ("hub", "lanes", "engine", "history", "info")
FONTS = ("s", "m", "l")
ACCENTS = ("blue", "green", "orange", "purple", "pink")
SECTION_TABS = ("hub", "lanes")
SECTIONS = {"hub": ("asks", "yours"), "lanes": ("lanes", "tasks", "reports")}
TIPS = ("prefs",)
MAX_SECTIONS = 16

DEFAULTS: dict[str, Any] = {"first_tab": "hub", "font": "m", "accent": "blue", "seen": [],
                            "sections": {t: {"order": [], "collapsed": [], "hidden": []} for t in SECTION_TABS}}

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SECTION = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")


class PrefsError(Exception):
    pass


class NotMember(PrefsError):
    pass


def check_name(name: str) -> str:
    if not _NAME.fullmatch(name or "") or ".." in name:
        raise NotMember("only board members have their own settings")
    return name


def _ids(raw: Any, dropped: list[str], where: str) -> list[str]:
    if not isinstance(raw, list):
        dropped.append(where)
        return []
    out: list[str] = []
    for x in raw:
        if isinstance(x, str) and _SECTION.fullmatch(x) and x not in out and len(out) < MAX_SECTIONS:
            out.append(x)
        else:
            dropped.append(f"{where}[{x!r}]")
    return out


def clean(raw: Any) -> tuple[dict[str, Any], list[str]]:
    dropped: list[str] = []
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out, ["prefs"]
    for k, v in raw.items():
        if k == "v":
            continue
        if k == "first_tab" and v in TABS:
            out[k] = v
        elif k == "font" and v in FONTS:
            out[k] = v
        elif k == "accent" and v in ACCENTS:
            out[k] = v
        elif k == "seen" and isinstance(v, list):
            out[k] = [t for t in TIPS if t in v]
            dropped += [f"seen[{x!r}]" for x in v if x not in TIPS]
        elif k == "sections" and isinstance(v, dict):
            secs: dict[str, Any] = {}
            for tab, spec in v.items():
                if tab not in SECTION_TABS or not isinstance(spec, dict):
                    dropped.append(f"sections.{tab}")
                    continue
                one: dict[str, list[str]] = {}
                for part, ids in spec.items():
                    if part in ("order", "collapsed", "hidden"):
                        one[part] = _ids(ids, dropped, f"sections.{tab}.{part}")
                    else:
                        dropped.append(f"sections.{tab}.{part}")
                secs[tab] = one
            out[k] = secs
        else:
            dropped.append(str(k))
    return out, dropped


def effective(saved: dict[str, Any]) -> dict[str, Any]:
    full = json.loads(json.dumps(DEFAULTS))
    for k, v in saved.items():
        if k == "sections":
            for tab, spec in v.items():
                full["sections"][tab].update(spec)
        else:
            full[k] = v
    return full


def load(store: Store, name: str) -> dict[str, Any]:
    raw = store.member_prefs(check_name(name))
    if raw is None:
        return {}
    try:
        return clean(json.loads(raw))[0]
    except ValueError:
        return {}


def view(store: Store, name: str) -> dict[str, Any]:
    saved = load(store, name)
    return {"prefs": effective(saved), "saved": saved, "defaults": DEFAULTS,
            "choices": {"first_tab": list(TABS), "font": list(FONTS), "accent": list(ACCENTS),
                        "section_tabs": list(SECTION_TABS), "sections": {t: list(v) for t, v in SECTIONS.items()},
                        "seen": list(TIPS)}}


def save(store: Store, name: str, change: Any) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(change, dict):
        raise PrefsError("prefs must be an object")
    check_name(name)
    saved = load(store, name)
    for k in [k for k, v in change.items() if v is None]:
        saved.pop(k, None)
    got, dropped = clean({k: v for k, v in change.items() if v is not None})
    for k, v in got.items():
        if k == "sections":
            secs = saved.setdefault("sections", {})
            for tab, spec in v.items():
                secs.setdefault(tab, {}).update(spec)
        else:
            saved[k] = v
    store.put_member_prefs(name, json.dumps({"v": VERSION, **saved}, ensure_ascii=False))
    return saved, dropped


def parse_pairs(pairs: list[str]) -> dict[str, Any]:
    change: dict[str, Any] = {}
    for pair in pairs:
        key, eq, val = pair.partition("=")
        key, val = key.strip(), val.strip()
        if not eq:
            raise PrefsError(f"{pair!r}: write NAME=VALUE")
        tab, dot, part = key.partition(".")
        if dot:
            if tab not in SECTION_TABS or part not in ("order", "collapsed", "hidden"):
                raise PrefsError(f"{key}: use hub|lanes.order|collapsed|hidden")
            ids = [x.strip() for x in val.split(",") if x.strip()]
            bad = [x for x in ids if x not in SECTIONS[tab]]
            if bad:
                raise PrefsError(f"{key}: unknown section {', '.join(bad)} (sections: {', '.join(SECTIONS[tab])})")
            change.setdefault("sections", {}).setdefault(tab, {})[part] = ids
            continue
        allowed = {"first_tab": TABS, "font": FONTS, "accent": ACCENTS}.get(key)
        if allowed is None:
            raise PrefsError(f"{key}: names are first_tab, font, accent, hub.*, lanes.*")
        if val and val not in allowed:
            raise PrefsError(f"{key}={val}: choose {', '.join(allowed)}")
        change[key] = val or None
    return change


def reset(store: Store, name: str) -> None:
    seen = load(store, name).get("seen")
    store.drop_member_prefs(check_name(name))
    if seen:
        save(store, name, {"seen": seen})
