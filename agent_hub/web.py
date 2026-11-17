from __future__ import annotations

import getpass
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from . import accountroutes
from . import accounthold
from . import accounts as accounts_mod
from . import config as config_mod
from . import engines as engines_mod
from . import processes as processes_mod
from . import prefs as prefs_mod
from . import rating, sessinfo, uploadroutes
from . import visibility as vis
from .machine import read_waits
from .queues import queues_view
from .session_view import session_view
from .config import CONFIG_NAME, HUB, INFRA, RUNTIMES, TOWER_DIR, Config, ConfigError, tower_pid
from .engine import clip
from .ext import STATIC_SUFFIXES, Extensions, board_dir, board_extras, board_override
from .install import edit_config
from .store import HUMAN, TOWER, Store, StoreError, is_hub_name

BOARD = Path(__file__).resolve().parent / "board" / "index.html"
BOARD_NEXT = Path(__file__).resolve().parent / "board_next"
BLOCKED_NAMES = {".env", "opencode.json", "tower.json", "secrets.json", "secrets.yaml", "secrets.yml", "secret.json",
                 "credentials", "credentials.json", "token.json", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "kubeconfig",
                 "known_hosts", "terraform.tfvars", "rclone.conf", "prod.env", "authorized_keys", "htpasswd"}
BLOCKED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".pem", ".key", ".p12", ".pfx", ".pkcs12", ".ppk",
                    ".jks", ".keystore", ".gpg", ".kdbx", ".tfstate", ".tfvars", ".env", ".p8", ".secret", ".kdb"}
BLOCKED_PREFIXES = ("service-account", "serviceaccount", "secrets.", "credentials", "client_secret", "client-secret",
                    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "rclone.conf")
MAX_BODY = 1024 * 1024
MAX_ID = 10 ** 15
PREVIEW = 3
PAGE = 20
COOKIE = "tower_token"
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
MAX_LANES_LIMIT = 10
MAX_SESSIONS_LIMIT = 5
XML_TYPES = ("image/svg+xml", "application/xhtml+xml", "application/xml", "text/xml")
PAGE_CSP_TEMPLATE = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "media-src 'self' blob:; font-src 'self' data:; connect-src 'self'{connect}; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'")
mimetypes.add_type("text/plain; charset=utf-8", ".md")
for _type, _ext in (("text/javascript", ".js"), ("text/javascript", ".mjs"), ("text/css", ".css")):
    mimetypes.add_type(_type, _ext)  # the Windows registry can say text/plain


def page_csp(cfg: Config) -> str:
    host = uploadroutes.connect_host(cfg)
    return PAGE_CSP_TEMPLATE.format(connect=f" https://{host}" if host else "")


def blocked_name(name: str) -> bool:
    low = name.lower().rstrip(". ")
    return (low.startswith(".") or (os.name == "nt" and ":" in low) or low in BLOCKED_NAMES or low.endswith(tuple(BLOCKED_SUFFIXES))
            or low.startswith(BLOCKED_PREFIXES) or ".tfstate" in low)


def safe_file(root: Path, rel: str) -> Path | None:
    parts = Path(unquote(rel)).parts
    if not parts or any(blocked_name(p) or p == ".." for p in parts):
        return None
    base = root.resolve()
    full = (base / Path(*parts)).resolve()
    try:
        shown = full.relative_to(base).parts
    except ValueError:
        return None
    if not shown or any(blocked_name(p) for p in shown) or not full.is_file():
        return None
    return full


def mime(name: str) -> str:
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if ctype.startswith(("text/", "application/json")) and "charset" not in ctype:
        ctype += "; charset=utf-8"
    return ctype


def host_name(value: str) -> tuple[str, int | None]:
    try:
        u = urlsplit("//" + value.strip())
        return (u.hostname or "").lower(), u.port
    except ValueError:
        return "", None


def is_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def engine_rows(cfg: Config, lane: str) -> tuple[list[dict[str, Any]], Path]:
    ln = cfg.all_lanes.get(lane)
    eng = cfg.engines.get(ln.engine) if ln and ln.engine else None
    skills = engines_mod.skills_root(cfg.home, eng.skills if eng else "")
    return (engines_mod.build(cfg.home / eng.library, skills) if eng and eng.library else []), skills


def tok(t: dict[str, Any]) -> dict[str, Any] | None:
    return ({"new": t["tok_new"], "cache": t["tok_cache"], "out": t["tok_out"], "turns": t["turns"]} if t.get("tok_at") else None)


def fixing_of(t: dict[str, Any] | None, blame: dict[str, Any] | None, view: dict[str, Any] | None) -> dict[str, Any] | None:
    if not t:
        return None
    if t.get("line_mode") == "fix":
        return {"by": "send-back", "task_id": t["id"], "row": t.get("line_row"), "type": view["type"] if view else None,
                "stage": t.get("stage_now") or t.get("line_stage")}
    if blame:
        return {"by": "blame", "task_id": t["id"], "row": t.get("line_row"), "type": view["type"] if view else None,
                "stage": blame["effective"], "confirmed": blame["is_confirmed"]}
    return None


def lane_view(cfg: Config, s: Store, lid: str, g: vis.Gate | None = None) -> dict[str, Any]:
    ln = cfg.all_lanes[lid]
    row = s.lane(lid)
    live = row.get("status") not in (None, "closed")
    rows, skills_dir = engine_rows(cfg, lid)
    try:
        role = cfg.instructions(lid).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        role = ""
    recent = s.lane_tasks(lid, 6) if ln.kind == "team" else []
    cur = next((t for t in recent if t["state"] in ("running", "wrapping")), None)
    shown = cur or (recent[0] if recent else None)
    shown_hidden = bool(g and shown and not vis.task_visible(g, shown))
    cur_hidden = bool(g and cur and not vis.task_visible(g, cur))
    view = engines_mod.row_view(rows, shown.get("line_row") if shown and not shown_hidden else None,
                                (shown.get("stage_now") or shown.get("line_stage")) if shown and not shown_hidden else None)
    blame = s.blames().get(cur["id"]) if cur and not cur_hidden else None
    return {"lane": lid, "label": ln.label or lid, "kind": ln.kind, "runtime": ln.runtime, "engine": ln.engine or None,
            "model": ln.model or None, "status": row.get("status") or "closed", "live": live,
            "skills": engines_mod.lane_skills(role, skills_dir, rows, lid) if ln.kind == "team" else [],
            "task": ({"id": shown["id"], "hidden": True} if shown_hidden else
                     {"id": shown["id"], "title": shown["title"], "state": shown["state"], "model": shown["model"],
                      "effort": shown["effort"], "line_row": shown["line_row"], "line_stage": shown["line_stage"],
                      "line_mode": shown["line_mode"], "stage_now": shown["stage_now"], "tokens": tok(shown)}
                     if shown else None),
            "process": view, "fixing": None if cur_hidden else fixing_of(cur, blame, view),
            "days": s.usage_days(lid, 7),
            "recent": [{"id": t["id"], "hidden": True, "state": t["state"]} if g and not vis.task_visible(g, t) else
                       {"id": t["id"], "title": t["title"], "state": t["state"], "model": t["model"], "effort": t["effort"],
                        "line_row": t["line_row"], "tokens": tok(t)} for t in recent]}


def engines_view(cfg: Config) -> list[dict[str, Any]]:
    out = []
    for eid, e in cfg.engines.items():
        rows = engines_mod.library(cfg.home / e.library) if e.library else []
        out.append({"id": eid, "label": e.label or eid, "lanes": list(e.lanes), "rows": len(rows), "implicit": e.implicit})
    return out


def _home_rel(cfg: Config, base: Path, rel: str | None) -> str | None:
    if not rel:
        return None
    try:
        return Path(os.path.normpath(base / rel)).relative_to(os.path.normpath(cfg.home)).as_posix()
    except ValueError:
        return None


def _skill_doc(cfg: Config, skills_dir: Path, name: str) -> str | None:
    f = skills_dir / name / "SKILL.md"
    return _home_rel(cfg, skills_dir, f"{name}/SKILL.md") if f.is_file() else None


def engine_map(cfg: Config, s: Store, eid: str, now: float) -> dict[str, Any]:
    e = cfg.engines[eid]
    skills_dir = engines_mod.skills_root(cfg.home, e.skills)
    rows = engines_mod.build(cfg.home / e.library, skills_dir) if e.library else []
    rated = {r["row"]: r for r in rating.attach({eid: rows}, s, cfg.home, {eid: skills_dir})} if rows else {}
    by_lane: dict[str, list[dict[str, Any]]] = {}
    lanes = []
    for lid in e.lanes:
        ln = cfg.lanes[lid]
        try:
            role = cfg.instructions(lid).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            role = ""
        sk = engines_mod.lane_skills(role, skills_dir, rows, lid)
        by_lane[lid] = sk
        st = s.lane(lid)
        task = s.live_task(lid)
        lanes.append({"lane": lid, "label": ln.label or lid, "status": st.get("status") or "closed", "skills": sk,
                      "rows": [r["n"] for r in rows if lid in (r.get("lanes") or [r["lane"]])], "task_id": task["id"] if task else None,
                      "process": next((r["type"] for r in rows if task and r["n"] == task["line_row"]), None)})
    pipes = []
    for r in rows:
        p, g = r["process"], rated.get(r["n"])
        stages = []
        for i, st in enumerate(p["stages"] if p else []):
            gs = g["stages"][i]["rating"] if g and i < len(g["stages"]) else None
            c = st["card"]
            stages.append({"n": st["n"], "name": st["name"], "by": st["by"],
                           "card": {"status": c["status"], "version": c["version"], "color": c["color"],
                                    "path": _home_rel(cfg, skills_dir, c["path"])} if c and c["exists"] else None,
                           "rating": ({k: gs[k] for k in ("n", "pct", "state", "color", "pending")} if gs else None),
                           "weakest": bool(g and g["stages"][i].get("weakest"))})
        pr = g["process"] if g else None
        pipes.append({"row": r["n"], "type": r["type"], "lane": r["lane"], "skill": r["skill"], "version": p["version"] if p else None,
                      "process_status": r["process_status"], "pattern_status": r["pattern_status"], "model": r["model_text"],
                      "card": _home_rel(cfg, skills_dir, p["path"]) if p else None,
                      "rating": {k: pr[k] for k in ("n", "pct", "state", "color", "fixes")} if pr else None, "stages": stages})
    skills = engines_mod.engine_skills(cfg, eid, {**by_lane, "": [{"name": n, "found": True} for n in e.skill_names]})
    days: dict[str, dict[str, Any]] = {}
    for lid in e.lanes:
        for d in s.usage_days(lid, 7):
            slot = days.setdefault(d["day"], {"day": d["day"], "tokens_in": 0, "tokens_out": 0})
            slot["tokens_in"] += d["tokens_in"] or 0
            slot["tokens_out"] += d["tokens_out"] or 0
    base = engines_mod.baseline(cfg, eid, now)
    live = engines_mod.snapshot(list(e.lanes), skills, rows, now)
    hand = engines_mod.read_map(cfg.home / e.map) if e.map else engines_mod.read_map(cfg.home / e.dir / "engine.json") if e.dir else \
        engines_mod.read_map(cfg.home / "engine.json")
    return {"id": eid, "label": e.label or eid, "implicit": e.implicit, "lanes": lanes, "pipelines": pipes,
            "skills": [{**{k: x[k] for k in ("name", "files", "lanes", "shared")}, "doc": _skill_doc(cfg, skills_dir, x["name"])}
                       for x in skills],
            "tools": hand["tools"], "experiments": hand["experiments"], "notes": hand["notes"],
            "usage": sorted(days.values(), key=lambda x: x["day"]),
            "changes": {"since": base["at"] if base else None, "items": engines_mod.diff(base, live) if base else []}}


def engine_doc(cfg: Config, s: Store, eid: str, path: str) -> dict[str, Any] | None:
    m = engine_map(cfg, s, eid, time.time())
    named = {p["card"] for p in m["pipelines"]} | {st["card"]["path"] for p in m["pipelines"] for st in p["stages"] if st["card"]}
    named |= {x["doc"] for x in m["skills"]}
    if not path or path not in named:
        return None
    try:
        return {"path": path, "text": (cfg.home / path).read_text(encoding="utf-8")}
    except (OSError, UnicodeDecodeError):
        return None


def _rating(g: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any] | None:
    return {k: g[k] for k in keys} if g and g.get("n") else None


def process_view(cfg: Config, s: Store) -> dict[str, Any]:
    v = processes_mod.view(cfg.home)
    for f in v["full"]:
        rated: dict[str, Any] = {}
        for eid, e in cfg.engines.items():
            if e.library and Path(e.library).as_posix() == f["file"]:
                skills_dir = engines_mod.skills_root(cfg.home, e.skills)
                rows = engines_mod.build(cfg.home / e.library, skills_dir)
                rated = {r["row"]: r for r in rating.attach({eid: rows}, s, cfg.home, {eid: skills_dir})}
                break
        for st in f["steps"]:
            g = rated.get(f"{f['id']}/{st['sub']}")
            st["rating"] = _rating(g["process"], ("n", "pct", "state", "color", "fixes")) if g else None
            st["stages"] = {x["n"]: {**(_rating(x["rating"], ("n", "pct", "state", "color")) or {}), "weakest": x["weakest"]}
                            for x in g["stages"] if x["rating"] and x["rating"].get("n")} if g else {}
    return v


def process_doc(cfg: Config, path: str) -> dict[str, Any] | None:
    if not path or path not in processes_mod.docs(processes_mod.view(cfg.home)):
        return None
    try:
        return {"path": path, "text": (cfg.home / path).read_text(encoding="utf-8")}
    except (OSError, UnicodeDecodeError):
        return None


def memo_title(text: str) -> str:
    lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
    return clip(lines[0] if lines else "", 80)


def shown_lanes(cfg: Config, s: Store) -> list[str]:
    out: list[str] = []
    for lid in [*cfg.lanes, *cfg.person_hubs]:
        out.append(lid)
        for key, ln in cfg.instances.items():
            if ln.base != lid:
                continue
            row = s.lane(key)
            if row.get("status") not in (None, "closed") or row.get("active_message") is not None or s.live_task(key):
                out.append(key)
    return out


WAIT_HEAD = {"en": "{n} waiting · #{id}: {why}", "ko": "대기 {n}개 · #{id}: {why}", "ja": "待機 {n} 件 · #{id}: {why}",
             "zh": "等待 {n} 条 · #{id}：{why}", "es": "{n} en espera · #{id}: {why}"}


def infra_wait_line(cfg: Config, s: Store) -> str | None:
    from .engine import block_text, infra_waits
    sessions = cfg.infra_sessions()
    if len(sessions) < 2:
        return None
    waits = infra_waits(s, sessions, time.time(), cfg.fair_turns)
    stuck = next(((m, b) for m, b in waits if b), None)
    if not stuck:
        return None
    m, b = stuck
    lang = cfg.lang if cfg.lang in WAIT_HEAD else "en"
    return WAIT_HEAD[lang].format(n=len(waits), id=m["id"], why=block_text(b[0], b[1], lang, len(sessions)))


def account_fields(cfg: Config, s: Store, g: vis.Gate | None, lane: str, ln: Any, session: str | None) -> dict[str, Any]:
    if not cfg.claude_accounts:
        return {}
    only = None if g is None or g.owner else g.name
    owner = s.session_account(lane, session)
    held = accounthold.lane_line(s, lane, cfg.lang, only) if not ln.base and ln.kind != INFRA else None
    return {"account": accounthold.shown_owner(owner, only), "held": held}


def lanes_view(cfg: Config, s: Store, used: dict[str, dict[str, Any]], g: vis.Gate | None = None) -> list[dict[str, Any]]:
    out = []
    blamed = set(s.blames())
    libs: dict[str, list[dict[str, Any]]] = {}
    for lid in shown_lanes(cfg, s):
        ln = cfg.all_lanes[lid]
        row = s.lane(lid)
        task = s.live_task(lid) if ln.kind == "team" else None
        doing = None
        memo = None
        hidden = False
        status = row.get("status") or "closed"
        live = status != "closed"
        if ln.kind == HUB and live and row.get("doing") and (row.get("doing_at") or 0) >= (row.get("opened") or 0):
            if g is None or not ln.owner or vis.sees_scoped(g, ln.owner):
                doing = clip(row["doing"], 80)
            else:
                hidden = True
        elif ln.kind == INFRA and live and row.get("active_message") is not None:
            try:
                m = s.message(int(row["active_message"]))
                if g is None or vis.memo_visible(g, m):
                    doing = clip(f"#{m['id']} {m['text']}", 80)
                    memo = {"id": m["id"], "kind": m["kind"], "title": memo_title(m["text"])}
                else:
                    hidden = True
                if status == "idle":
                    status = "busy"
            except StoreError:
                pass
        if task and g is not None and not vis.task_visible(g, task):
            hidden = True
            task = {**task, "title": None, "requester": None, "line_row": None, "stage_now": None, "line_stage": None,
                    "line_mode": None, "id_only": True}
        proc = None
        if task and task["line_row"]:
            rows = libs.get(ln.engine) if ln.engine in libs else libs.setdefault(ln.engine, engine_rows(cfg, lid)[0])
            hit = next((r for r in rows if r["n"] == task["line_row"]), None)
            proc = hit["type"] if hit else None
        out.append({"lane": lid, "label": ln.label, "runtime": ln.runtime, "kind": ln.kind,
                    "owner": ln.owner or None, "status": status, "doing": doing, "memo": memo,
                    "wait": infra_wait_line(cfg, s) if lid == INFRA and ln.kind == INFRA else None,
                    "base": ln.base or None, "branch": cfg.branch_of(lid) or None,
                    "sessions": None if ln.base or ln.kind == HUB else cfg.instance_cap(lid),
                    "engine": ln.engine or None,
                    "hidden": hidden,
                    "task_id": task["id"] if task else None, "task_title": task["title"] if task else None,
                    "requester": task["requester"] if task else None,
                    "model": task["model"] if task else None, "effort": task["effort"] if task else None,
                    "line_row": task["line_row"] if task else None, "process": proc,
                    "stage": (task["stage_now"] or task["line_stage"]) if task else None,
                    "fixing": bool(task and not task.get("id_only") and (task["line_mode"] == "fix" or task["id"] in blamed)),
                    "task_tokens": ({"new": task["tok_new"], "cache": task["tok_cache"], "out": task["tok_out"],
                                     "turns": task["turns"]} if task and task["tok_at"] else None),
                    "ctx_k": s.session_ctx(lid, row.get("session")) if live else None, "usage": used.get(lid),
                    "run": sessinfo.for_lane(cfg, s, lid, ln.runtime, ln.model, row.get("session") if live else None),
                    **account_fields(cfg, s, g, lid, ln, row.get("session") if live else None)})
    return out


def usage_view(cfg: Config, day: str, used: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    for lid, ln in {**cfg.lanes, **cfg.person_hubs}.items():
        r = rows.setdefault(ln.runtime, {"runtime": ln.runtime, "lanes": [], "tokens_in": 0, "tokens_out": 0,
                                         "cost": None, "sessions": 0})
        r["lanes"].append(lid)
        u = used.get(lid)
        if u:
            r["tokens_in"] += u["tokens_in"]
            r["tokens_out"] += u["tokens_out"]
            r["sessions"] += u["sessions"]
            if u["cost"] is not None:
                r["cost"] = (r["cost"] or 0.0) + u["cost"]
    return {"day": day, "runtimes": list(rows.values())}


def counts_view(g: vis.Gate) -> dict[str, int]:
    out = {}
    for k in (*Store.HISTORY, *Store.MEMO_HISTORY, Store.WORK):
        if k == "decided":
            out[k] = vis.decided_count(g)
        elif k == Store.WORK:
            out[k] = vis.work_count(g)
        elif k in Store.MEMO_HISTORY:
            out[k] = vis.memo_count(g, Store.MEMO_HISTORY[k])
        else:
            out[k] = g.s.history_count(k)
    return out


def history_rows(g: vis.Gate, kind: str, offset: int) -> list[dict[str, Any]]:
    if kind == "decided":
        return vis.decided(g, offset, PAGE)
    if kind in Store.MEMO_HISTORY:
        return vis.memos(g, Store.MEMO_HISTORY[kind], PAGE, offset)
    if kind == Store.WORK:
        return vis.work(g, offset, PAGE)
    rows = g.s.history(kind, offset, PAGE)
    if kind == "closed":
        return [vis.task_row(g, t) for t in rows]
    if kind == "reports":
        return [vis.report_row(g, r) for r in rows]
    return rows


def with_hubs(s: Store, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    owners = s.task_hubs()
    for it in items:
        lane = str(it.get("lane") or "")
        it["hub"] = lane if is_hub_name(lane) else owners.get(it.get("task_id"), HUB)
    return items


def my_hub_view(cfg: Config, s: Store, me: dict[str, Any] | None) -> dict[str, Any]:
    if not me or not cfg.hub_cap:
        return {"state": "off", "hub": HUB}
    name = str(me["name"])
    if name in cfg.hub_overflow:
        return {"state": "full", "hub": HUB, "place": cfg.hub_overflow.index(name) + 1,
                "waiting": len(cfg.hub_overflow), "cap": cfg.hub_cap}
    lid = cfg.hub_of(name)
    if lid == HUB:
        return {"state": "off", "hub": HUB}
    row = s.lane(lid)
    st = row.get("status")
    if st == "idle" or (st in ("busy", "asking") and s.delivered_since(lid, row.get("opened") or 0)):
        return {"state": "ready", "hub": lid}
    return {"state": "starting", "hub": lid, "step": "reading" if st in ("busy", "asking") else "opening",
            "since": row.get("opened")}


def state(cfg: Config, s: Store, me: dict[str, Any] | None) -> dict[str, Any]:
    tasks = s.tasks(("queued", "running", "wrapping"), limit=200)
    day = s.day()
    used = s.usage(day)
    my_hub = cfg.hub_of(str(me["name"])) if me else HUB
    g = vis.Gate.of(cfg, s, me)
    return {
        "title": cfg.name,
        "lang": cfg.lang,
        "uploads": uploadroutes.info(cfg),
        "auth_mode": cfg.auth_mode,
        "me": me,
        "my_hub": my_hub,
        "my_hub_state": my_hub_view(cfg, s, me),
        "requester": cfg.requester,
        "claude_accounts": cfg.claude_accounts,
        "accounts": accounts_mod.overview(s) if cfg.claude_accounts and vis.sees_all(me) else None,
        "running": tower_pid(cfg) is not None,
        "version": __version__,
        "local_only": cfg.local_only,
        "paused": (cfg.state_dir / "paused").exists(),
        "extensions": board_extras(cfg.home),
        "lane_cap": {"used": len({t["lane"] for t in tasks if t["state"] in ("running", "wrapping")}),
                     "cap": cfg.max_lanes, "max": MAX_LANES_LIMIT, "sessions_max": MAX_SESSIONS_LIMIT},
        "runtimes": list(RUNTIMES),
        "default_runtime": cfg.lanes[HUB].runtime,
        "usage": usage_view(cfg, day, used),
        "lanes": lanes_view(cfg, s, used, g),
        "queues": queues_view(cfg, s, tasks, g, read_waits(cfg.state_dir)),
        "items": with_hubs(s, vis.mine(g, s.items(state="open", limit=500)) + vis.decided(g, 0, PREVIEW)),
        "tasks": [vis.task_row(g, t) for t in tasks + s.history("closed", 0, PREVIEW)],
        "reports": [vis.report_row(g, r) for r in s.history("reports", 0, PREVIEW)],
        "memos": {INFRA: vis.memos(g, INFRA, PREVIEW), HUB: vis.memos(g, my_hub, PREVIEW)},
        "counts": counts_view(g),
    }


JOIN_LOCK = threading.Lock()


def joinable(cfg: Config, ident: str) -> bool:
    user, at, domain = ident.strip().lower().rpartition("@")
    if not at or not user or not domain:
        return False
    return not cfg.hub_domains or domain in cfg.hub_domains


class Site:
    def __init__(self, slug: str, cfg: Config, store: Store, prefix: str, ext: Extensions, mtime: float = 0.0):
        self.slug, self.cfg, self.s, self.prefix, self.ext, self.mtime = slug, cfg, store, prefix, ext, mtime


def make_handler(cfg: Config, s: Store, log: Callable[[str], None], ext: Extensions | None = None):
    secure = cfg.public_url.startswith("https://")
    own_home = cfg.home.resolve()
    own_cfg_path = own_home / TOWER_DIR / CONFIG_NAME
    own = Site("", cfg, s, "", ext or Extensions(cfg.home, log),
               own_cfg_path.stat().st_mtime if own_cfg_path.exists() else 0.0)
    siblings: dict[str, Site] = {}
    from .claudeusage import ClaudeUsage
    from .machine import Machine
    mach, cusage = Machine(), ClaudeUsage()
    sweepers: dict[str, uploadroutes.Sweeper] = {}

    def fresh_own() -> Site:
        try:
            mtime = own_cfg_path.stat().st_mtime
        except OSError:
            return own
        if mtime != own.mtime:
            own.mtime = mtime
            try:
                own.cfg = config_mod.load(own.cfg.home)
            except ConfigError as e:
                log(f"board keeps the last good tower.json: {e}")
        return own

    def sibling(slug: str) -> Site | None:
        home = config_mod.workspaces().get(slug)
        if home is None:
            return None
        if home.resolve() == own_home:
            return fresh_own()
        try:
            mtime = (home / TOWER_DIR / CONFIG_NAME).stat().st_mtime
            hit = siblings.get(slug)
            if hit and hit.mtime == mtime and hit.cfg.home == home:
                return hit
            c2 = config_mod.load(home)
        except (OSError, ConfigError):
            return None
        ext2 = hit.ext if hit and hit.cfg.home == home else Extensions(home, log)
        site = siblings[slug] = Site(slug, c2, Store(c2.db), f"/w/{slug}", ext2, mtime)
        return site

    def reachable(site: Site) -> bool:
        mode = site.cfg.auth_mode
        if site is own or mode == "token":
            return True
        if mode == "local":
            return cfg.auth_mode == "local"
        return cfg.auth_mode == "header" and cfg.auth_header.lower() == site.cfg.auth_header.lower()

    public_host = host_name(cfg.public_url.split("://", 1)[-1])[0] if cfg.public_url else ""
    public_origin = "{0.scheme}://{0.netloc}".format(urlsplit(cfg.public_url)).lower() if cfg.public_url else ""
    bind_host = cfg.web_host.strip("[]").lower()
    exposed = bind_host not in ("127.0.0.1", "localhost", "::1")

    def host_ok(value: str) -> bool:
        name, _ = host_name(value)
        if not name:
            return False
        if name == "localhost" or is_ip(name) or name == public_host:
            return True
        return name in own.cfg.allowed_hosts or (exposed and name == bind_host)

    def origin_ok(origin: str, host: str) -> bool:
        parts = urlsplit(origin)
        if parts.netloc.lower() == host.lower():
            return True
        if cfg.public_url and origin.lower().rstrip("/") == public_origin:
            return True
        return host_name(parts.netloc)[0] in own.cfg.allowed_hosts

    def same_origin(headers: Any) -> bool:
        site = (headers.get("sec-fetch-site") or "").lower()
        if site and site not in ("same-origin", "none"):
            return False
        origin = headers.get("origin")
        if origin and origin != "null":
            return origin_ok(origin, headers.get("host") or "")
        return origin is None

    def workspaces_view(current: Site) -> list[dict[str, Any]]:
        regs = config_mod.workspaces()
        own_slug = next((k for k, h in regs.items() if h.resolve() == own_home), "")
        out = []
        for slug in [own_slug if own_slug else "", *[k for k in regs if k != own_slug]]:
            site = own if slug == own_slug else sibling(slug)
            if site is None:
                continue
            ok = reachable(site)
            row: dict[str, Any] = {"slug": slug, "name": site.cfg.name, "current": site is current,
                                   "href": ("/" if site is own else f"/w/{slug}/") if ok else None,
                                   "running": tower_pid(site.cfg) is not None}
            if ok:
                row["open"] = len(site.s.items(state="open", audience=HUMAN))
            out.append(row)
        return out

    class Handler(BaseHTTPRequestHandler):
        site: Site = own
        route = "/"
        timeout = 30

        def version_string(self) -> str:
            return "dah"

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _guard(self) -> bool:
            if not host_ok(self.headers.get("host") or ""):
                self._json(421, {"error": "unknown Host: open the board by its own address (public_url in tower.json)"})
                return False
            return True

        def _hardening(self, frame_only: bool = False, csp: str | None = None) -> None:
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("x-frame-options", "DENY")
            self.send_header("referrer-policy", "no-referrer")
            self.send_header("content-security-policy", csp or ("frame-ancestors 'none'" if frame_only else page_csp(self.site.cfg)))

        def _resolve(self) -> bool:
            path, _, query = self.path.partition("?")
            m = re.fullmatch(r"/w/([a-z0-9-]+)(/.*)?", path)
            if not m:
                self.site, self.route, self.query = fresh_own(), path, query
                return True
            site = sibling(m.group(1))
            if site is None:
                self._json(404, {"error": "no such workspace"})
                return False
            if not reachable(site):
                self._json(403, {"error": "this workspace only accepts local visitors (auth.mode local)"})
                return False
            if m.group(2) is None:
                self.send_response(308)
                self.send_header("location", f"/w/{m.group(1)}/" + (f"?{query}" if query else ""))
                self.send_header("content-length", "0")
                self._hardening(frame_only=True)
                self.end_headers()
                return False
            self.site, self.route, self.query = site, m.group(2), query
            return True

        @property
        def cookie(self) -> str:
            return COOKIE if self.site is own else f"{COOKIE}_{self.site.slug}"


        def _me(self) -> dict[str, Any] | None:
            c, st = self.site.cfg, self.site.s
            if c.auth_mode == "local":
                return {"name": getpass.getuser(), "role": "owner"}
            if c.auth_mode == "header":
                if self.client_address[0] not in LOOPBACK:
                    return None  # identity headers are only trusted from the local proxy
                ident = (self.headers.get(c.auth_header) or "").strip()
                if not ident:
                    return None
                m = st.member_by_ident(ident)
                if m:
                    return m
                if c.hub_auto and joinable(c, ident):
                    return self._join(ident)
                if any(x["ident"] for x in st.members()):
                    return None
                return {"name": ident, "role": "member"}
            token = ""
            auth = self.headers.get("authorization") or ""
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
            for part in (self.headers.get("cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == self.cookie:
                    token = token or v
            return st.member_by_token(token) if token else None

        def _join(self, ident: str) -> dict[str, Any] | None:
            st = self.site.s
            with JOIN_LOCK:
                m = st.member_by_ident(ident)
                if m:
                    return m
                ms = st.members()
                name = config_mod.auto_member_name(ident, [x["name"] for x in ms])
                st.add_member(name, "owner" if not ms else "member", ident)
                path = self.site.cfg.home / TOWER_DIR / CONFIG_NAME
                os.utime(path)
                try:
                    self.site.cfg, self.site.mtime = config_mod.load(self.site.cfg.home), path.stat().st_mtime
                except ConfigError as e:
                    log(f"board keeps the last good tower.json: {e}")
                c = self.site.cfg
                hub = c.hub_of(name)
                where = (f"their hub {hub} is opening" if hub != HUB else
                         f"person hubs are full ({c.hub_cap}): they wait in line as number "
                         f"{c.hub_overflow.index(name) + 1 if name in c.hub_overflow else '?'} and you take their "
                         f"requests meanwhile")
                st.push(HUB, TOWER, "member", f"New board member {name} ({ident}) signed in for the first time and was "
                        f"added on their own: {where}.")
                log(f"{ident} joined as member {name} · {hub}")
                return st.member_by_ident(ident)


        def _json(self, code: int, body: Any, headers: dict[str, str] | None = None) -> None:
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(data)))
            self._hardening(frame_only=True)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _json_tagged(self, body: Any) -> None:
            data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
            tag = '"' + hashlib.sha1(data).hexdigest()[:16] + '"'
            self.send_response(304 if self.headers.get("if-none-match") == tag else 200)
            self.send_header("etag", tag)
            self.send_header("cache-control", "no-cache")
            self._hardening(frame_only=True)
            if self.headers.get("if-none-match") == tag:
                self.send_header("content-length", "0")
                self.end_headers()
                return
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _contained(self, name: str, ctype: str) -> None:
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("referrer-policy", "no-referrer")
            self.send_header("content-security-policy", "sandbox allow-scripts" if ctype.startswith("text/html") else "sandbox")
            xml = ctype.startswith(XML_TYPES) or name.lower().endswith((".svg", ".xml", ".xhtml", ".xht"))
            if xml and (self.headers.get("sec-fetch-dest") or "").lower() != "image":
                self.send_header("content-disposition", "attachment")

        def _static(self, path: Path, ctype: str, cache: str = "no-cache", page: str = "app") -> None:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("content-type", ctype)
            self.send_header("cache-control", cache)
            self.send_header("content-length", str(len(data)))
            if page == "file":
                self._contained(path.name, ctype)
            else:
                self._hardening(frame_only=page != "app")
            self.end_headers()
            self.wfile.write(data)


        def do_GET(self) -> None:
            if not self._guard() or not self._resolve():
                return
            c, st, path = self.site.cfg, self.site.s, self.route
            if path == "/api/goals" or path.startswith("/api/goals/"):
                return self._json(410, {"error": "goals were removed in 0.8.0; send requests to the hub instead"})
            if path in ("/", "/index.html"):
                page = None if "builtin" in parse_qs(self.query, keep_blank_values=True) else board_override(c.home)
                return self._static(page, "text/html; charset=utf-8", page="frame") if page else self._next("")
            if path.startswith("/assets/"):
                return self._next(path[1:])
            if path == "/old":
                return self._redirect("/old/")
            if path in ("/old/", "/old/index.html"):
                return self._static(BOARD, "text/html; charset=utf-8", page="frame")
            if path == "/manifest.webmanifest":
                return self._json(200, {"name": c.name, "short_name": c.name[:12], "start_url": "./",
                                        "display": "standalone", "background_color": "#17171c",
                                        "theme_color": "#17171c"})
            me = self._me()
            if me is None:
                return self._json(401, {"error": "sign in"})
            if path == "/api/state":
                sweepers.setdefault(self.site.slug, uploadroutes.Sweeper()).run(c, st, log)
                return self._json(200, state(self.site.cfg, st, me))
            if path == "/api/machine":
                return self._json(200, {"machine": mach.view(), "waits": read_waits(c.state_dir),
                                        "usage": cusage.view() if c.usage_percent == "claude" else None})
            if path == "/api/workspaces":
                return self._json(200, {"workspaces": workspaces_view(self.site)})
            if path == "/api/me/prefs":
                try:
                    return self._json(200, prefs_mod.view(st, str(me["name"])))
                except prefs_mod.PrefsError as e:
                    return self._json(403, {"error": str(e)})
            if path == "/api/me/claude":
                code, out = accountroutes.answer(c, st, me, {"action": "status"}, log)
                return self._json(code, out)
            if path == "/api/history":
                q = parse_qs(self.query)
                try:
                    offset = max(0, int((q.get("offset") or ["0"])[0]))
                    if offset > MAX_ID:
                        raise ValueError("offset is too large")
                    rows = history_rows(vis.Gate.of(c, st, me), (q.get("kind") or [""])[0], offset)
                except (StoreError, ValueError) as e:
                    return self._json(410 if q.get("kind") == ["goals"] else 400, {"error": str(e)})
                return self._json(200, {"rows": rows, "more": len(rows) == PAGE})
            if path == "/api/uploads/view":
                return uploadroutes.view(self, c, st, me, self.query)
            if path == "/api/uploads/file":
                return uploadroutes.stream(self, c, st, me, self.query)
            if path == "/api/engines":
                return self._json(200, {"engines": engines_view(c)})
            m = re.fullmatch(r"/api/engines/([^/]+)/map", path)
            if m:
                eid = unquote(m.group(1))
                if eid not in c.engines:
                    return self._json(404, {"error": f"no engine {eid}"})
                return self._json_tagged(engine_map(c, st, eid, time.time()))
            m = re.fullmatch(r"/api/engines/([^/]+)/doc", path)
            if m:
                eid = unquote(m.group(1))
                got = engine_doc(c, st, eid, (parse_qs(self.query).get("path") or [""])[0]) if eid in c.engines else None
                return self._json(200, got) if got else self._json(404, {"error": "no such document"})
            if path == "/api/processes":
                return self._json_tagged(process_view(c, st))
            if path == "/api/processes/doc":
                got = process_doc(c, (parse_qs(self.query).get("path") or [""])[0])
                return self._json(200, got) if got else self._json(404, {"error": "no such document"})
            m = re.fullmatch(r"/api/lanes/([^/]+)", path)
            if m:
                lid = unquote(m.group(1))
                if lid not in c.lanes and lid not in c.person_hubs:
                    return self._json(404, {"error": f"no lane {lid}"})
                gate = vis.Gate.of(c, st, me)
                owner = c.all_lanes[lid].owner
                if owner and not vis.sees_scoped(gate, owner):
                    return self._json(404, {"error": f"no lane {lid}"})
                return self._json(200, lane_view(c, st, lid, gate))
            m = re.fullmatch(r"/api/sessions/([^/]+)", path)
            if m:
                got = session_view(c, st, unquote(m.group(1)), vis.Gate.of(c, st, me))
                return self._json(200, got) if got else self._json(404, {"error": "no such session"})
            m = re.fullmatch(r"/api/items/(\d+)", path)
            if m:
                if len(m.group(1)) > 15:
                    return self._json(400, {"error": "item number out of range"})
                try:
                    it = st.item(int(m.group(1)))
                except StoreError as e:
                    return self._json(404, {"error": str(e)})
                if not vis.can_see(vis.Gate.of(c, st, me), it):
                    return self._json(404, {"error": f"item #{it['id']} not found"})
                return self._json(200, it)
            if path.startswith("/f/"):
                full = safe_file(c.workdir, path[3:])
                if not full or not vis.file_allowed(vis.Gate.of(c, st, me), c.workdir, full):
                    return self._json(404, {"error": "not found"})
                return self._file(full)
            if path.startswith("/x/"):
                full = safe_file(board_dir(c.home), path[3:])
                if not full or full.suffix.lower() not in STATIC_SUFFIXES:
                    return self._json(404, {"error": "not found"})
                return self._static(full, mime(full.name), page="file")
            if path.startswith("/api/x/"):
                return self._ext("GET", path[7:], me, {})
            self._json(404, {"error": "not found"})

        def _redirect(self, to: str) -> None:
            self.send_response(302)
            self.send_header("location", self.site.prefix + to + (f"?{self.query}" if self.query else ""))
            self.send_header("content-length", "0")
            self._hardening(frame_only=True)
            self.end_headers()

        def _next(self, rel: str) -> None:
            full = BOARD_NEXT / "index.html" if rel in ("", "index.html") else safe_file(BOARD_NEXT, rel)
            if not full or not full.is_file() or (full.suffix.lower() not in STATIC_SUFFIXES and full.suffix != ".html"):
                return self._json(404, {"error": "not found"})
            self._static(full, mime(full.name), "public, max-age=31536000, immutable" if rel.startswith("assets/") else "no-cache")

        def _ext(self, method: str, name: str, me: dict[str, Any], body: dict[str, Any]) -> None:
            fn = self.site.ext.routes().get((method, name.strip("/")))
            if fn is None:
                return self._json(404, {"error": "not found"})
            ctx = {"cfg": self.site.cfg, "store": self.site.s, "me": me, "gate": vis.Gate.of(self.site.cfg, self.site.s, me), "log": log,
                   "query": {k: v[-1] for k, v in parse_qs(self.query).items()}}
            try:
                out = fn(ctx, body)
                code, data = out if isinstance(out, tuple) and len(out) == 2 and isinstance(out[0], int) else (200, out)
                json.dumps(data)
            except Exception as e:
                log(f"extension route {method} /api/x/{name} failed: {type(e).__name__}: {e}")
                return self._json(500, {"error": f"extension failed: {type(e).__name__}: {e}"})
            return self._json(code, data)

        def _prefs(self, who: str, body: dict[str, Any]) -> None:
            st = self.site.s
            try:
                if body.get("reset") is True:
                    prefs_mod.reset(st, who)
                    log(f"{who} reset their board settings")
                    return self._json(200, {"ok": True, **prefs_mod.view(st, who), "dropped": []})
                _, dropped = prefs_mod.save(st, who, body.get("prefs"))
            except prefs_mod.NotMember as e:
                return self._json(403, {"error": str(e)})
            except prefs_mod.PrefsError as e:
                return self._json(400, {"error": str(e)})
            log(f"{who} changed their board settings" + (f" (dropped {', '.join(dropped)})" if dropped else ""))
            return self._json(200, {"ok": True, **prefs_mod.view(st, who), "dropped": dropped})

        def _file(self, full: Path) -> None:
            ctype = mime(full.name)
            size = full.stat().st_size
            start, end = 0, size - 1
            rng = re.match(r"bytes=(\d{0,15})-(\d{0,15})$", self.headers.get("range", ""))
            partial = bool(rng and size)
            if rng and size:
                if rng.group(1):
                    start = int(rng.group(1))
                    end = int(rng.group(2)) if rng.group(2) else size - 1
                else:
                    start = max(0, size - int(rng.group(2) or 0))
                end = min(end, size - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header("content-range", f"bytes */{size}")
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
            self.send_response(206 if partial else 200)
            self.send_header("content-type", ctype)
            self.send_header("accept-ranges", "bytes")
            self.send_header("content-length", str(end - start + 1 if size else 0))
            if partial:
                self.send_header("content-range", f"bytes {start}-{end}/{size}")
            self._contained(full.name, ctype)
            self.end_headers()
            with full.open("rb") as f:
                f.seek(start)
                left = end - start + 1 if size else 0
                while left > 0:
                    chunk = f.read(min(1 << 16, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)


        def do_POST(self) -> None:
            if not self._guard():
                return
            if self.headers.get("x-tower") != "1":
                return self._json(403, {"error": "missing x-tower header"})
            if not same_origin(self.headers):
                return self._json(403, {"error": "cross-site request"})
            if not self._resolve():
                return
            c, st, path = self.site.cfg, self.site.s, self.route
            if path == "/api/goals" or path.startswith("/api/goals/"):
                return self._json(410, {"error": "goals were removed in 0.8.0; send requests to the hub instead"})
            if path == "/api/uploads/chunk":
                me = self._me()
                if me is None:
                    return self._json(401, {"error": "sign in"})
                return uploadroutes.chunk(self, c, st, str(me["name"]))
            try:
                n = int(self.headers.get("content-length") or 0)
            except ValueError:
                return self._json(400, {"error": "invalid content-length"})
            if not 0 <= n <= MAX_BODY:
                return self._json(413, {"error": "too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, RecursionError):
                return self._json(400, {"error": "invalid JSON"})
            if not isinstance(body, dict):
                return self._json(400, {"error": "invalid JSON"})
            if path == "/api/login":
                m = st.member_by_token(str(body.get("token") or "")) if c.auth_mode == "token" else None
                if not m:
                    return self._json(401, {"error": "unknown token"})
                cookie = (f"{self.cookie}={body['token']}; Path={self.site.prefix}/; HttpOnly; SameSite=Strict; "
                          f"Max-Age=2592000")
                return self._json(200, {"ok": True, "me": m}, {"set-cookie": cookie + ("; Secure" if secure else "")})
            if path == "/api/logout":
                return self._json(200, {"ok": True}, {"set-cookie": f"{self.cookie}=; Path={self.site.prefix}/; Max-Age=0"})
            me = self._me()
            if me is None:
                return self._json(401, {"error": "sign in"})
            who = str(me["name"])
            try:
                m = re.fullmatch(r"/api/items/(\d+)/decide", path)
                if m:
                    if len(m.group(1)) > 15:
                        return self._json(400, {"error": "item number out of range"})
                    if not vis.can_see(vis.Gate.of(c, st, me), st.item(int(m.group(1)))):
                        return self._json(404, {"error": f"item #{m.group(1)} not found"})
                    choices = body.get("choices")
                    it = st.decide(int(m.group(1)), str(body.get("verdict")), who,
                                   [str(x) for x in choices] if isinstance(choices, list) else body.get("choice") or None,
                                   str(body.get("note") or "")[:4000])
                    log(f"{who} decided #{it['id']}: {it['verdict']} {it['choice'] or ''}")
                    return self._json(200, {"ok": True, "item": it})
                if path == "/api/me/prefs":
                    return self._prefs(who, body)
                if path == "/api/me/claude":
                    code, out = accountroutes.answer(c, st, me, body, log)
                    return self._json(code, out)
                if path == "/api/memo":
                    text = str(body.get("text") or "").strip()
                    to = str(body.get("to") or HUB)
                    if to == INFRA:
                        raise StoreError("infra only takes requests from the hub: ask the hub")
                    if to != HUB or to not in c.lanes:
                        raise StoreError("requests from the board go to the hub")
                    to = c.hub_of(who)
                    if not text:
                        raise StoreError("empty memo")
                    mid = st.push(to, who, "memo", text)
                    log(f"{who} memo #{mid} → {to}")
                    return self._json(200, {"ok": True, "id": mid})
                if path.startswith("/api/uploads/"):
                    return uploadroutes.steps(self, c, st, who, path[13:], body, log)
                if path == "/api/settings" and "lane" in body:
                    if me.get("role") != "owner":
                        return self._json(403, {"error": "only an owner changes settings"})
                    lid, raw = str(body.get("lane") or ""), body.get("sessions")
                    ln = c.lanes.get(lid)
                    if ln is None or ln.kind == HUB:
                        raise StoreError("sessions are set for a team lane or infra; each hub runs one session per person")
                    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= MAX_SESSIONS_LIMIT:
                        raise StoreError(f"sessions must be a whole number from 1 to {MAX_SESSIONS_LIMIT}")
                    old = c.instance_cap(lid)
                    held = [k for k, x in c.instances.items() if x.base == lid and int(k.rpartition("#")[2]) > raw
                            and (st.lane(k).get("active_message") is not None or st.live_task(k))]
                    if held and ln.kind != "team":
                        raise StoreError(f"{', '.join(held)} is still working: lower it once that session wraps")
                    edit_config(c.home, lambda d: d["lanes"][lid].__setitem__("max_instances", raw))
                    self.site.mtime = -1.0
                    if raw != old:
                        st.push(HUB, who, "settings", f"{who} set {lid} sessions {old} → {raw}: {lid} may run up to "
                                f"{raw} sessions at once.")
                        log(f"{who} {lid} sessions {old} → {raw}")
                    return self._json(200, {"ok": True, "lane": lid, "sessions": raw})
                if path == "/api/settings":
                    if me.get("role") != "owner":
                        return self._json(403, {"error": "only an owner changes settings"})
                    raw = body.get("max_lanes")
                    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= MAX_LANES_LIMIT:
                        raise StoreError(f"max_lanes must be a whole number from 1 to {MAX_LANES_LIMIT}")
                    old = c.max_lanes
                    edit_config(c.home, lambda d: d.__setitem__("max_lanes", raw))
                    self.site.mtime = -1.0  # reload on the next request even where mtime is coarse
                    if raw != old:
                        st.push(HUB, who, "settings", f"{who} set max_lanes {old} → {raw}: up to {raw} team lanes "
                                f"may run tasks at once.")
                        log(f"{who} max_lanes {old} → {raw}")
                    return self._json(200, {"ok": True, "max_lanes": raw})
            except (StoreError, ConfigError, OSError) as e:
                return self._json(409, {"error": str(e)})
            if path.startswith("/api/x/"):
                return self._ext("POST", path[7:], me, body)
            self._json(404, {"error": "not found"})

    return Handler


def server(cfg: Config, s: Store, log: Callable[[str], None] = print,
           ext: Extensions | None = None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((cfg.web_host, cfg.web_port), make_handler(cfg, s, log, ext))
