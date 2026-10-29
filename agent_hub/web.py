"""Review board web server (a thread inside `dah run`). Binds 127.0.0.1 by default; expose it through a proxy
that authenticates people (Cloudflare Access, Tailscale, an AWS ALB with OIDC, ...) or use token sign-in.

  GET  /                          board (board/index.html, or the workspace's .tower/board/index.html; /?builtin
                                  always opens the built-in one)
  GET  /api/state                 goals · lanes · open items · tasks · reports · memos · counts
  GET  /api/history?kind=K&offset=N   decided | reports | closed | goals | memos | requests, 20 per page
  GET  /api/items/<id>
  GET  /api/workspaces            every workspace registered on this machine, for the board's switcher
  GET  /f/<path>                  attachment under the project (dot paths and secrets are refused; Range supported)
  POST /api/items/<id>/decide     {verdict, choice | choices[], note}
  POST /api/memo                  {text, to: infra | hub}
  POST /api/goals                 {title, detail}
  POST /api/goals/<id>            {action: top | up | down | pause | resume | drop | done | reopen, note}
  POST /api/settings              {max_lanes: 1..10}   (written to tower.json; the tower picks it up on its next tick)
  POST /api/login {token} · POST /api/logout      (auth.mode = token)
  GET  /x/<path>                  the workspace's .tower/board/ files (extra.js, extra.css, images, ...)
  GET|POST /api/x/<name>          routes from the workspace's .tower/ext/*.py (see ext.py)

Other registered workspaces are served under /w/<slug>/ with the same routes, so one exposed board reaches every
project on the machine. Each workspace keeps its own auth; a `local` workspace is only served next to another
`local` one (local mode trusts whoever reaches it).

Every POST must carry `x-tower: 1` (a custom header forces a CORS preflight, so other sites cannot post forms).
"""

from __future__ import annotations

import getpass
import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote

from . import __version__
from . import config as config_mod
from .config import CONFIG_NAME, HUB, INFRA, RUNTIMES, TOWER_DIR, Config, ConfigError, tower_pid
from .engine import clip
from .ext import STATIC_SUFFIXES, Extensions, board_dir, board_extras, board_override
from .install import edit_config
from .store import GOAL_RANKED, HUMAN, Store, StoreError

BOARD = Path(__file__).resolve().parent / "board" / "index.html"
BLOCKED_NAMES = {".env", "opencode.json", "tower.json", "secrets.json", "credentials", "id_rsa", "id_ed25519"}
BLOCKED_SUFFIXES = {".db", ".sqlite", ".pem", ".key", ".p12"}
MAX_BODY = 64 * 1024
PREVIEW = 3
PAGE = 20
COOKIE = "tower_token"
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
MAX_LANES_LIMIT = 10
GOAL_ACTIONS = {"pause": "paused", "resume": "active", "drop": "dropped", "done": "done", "reopen": "active"}
mimetypes.add_type("text/plain; charset=utf-8", ".md")
for _type, _ext in (("text/javascript", ".js"), ("text/javascript", ".mjs"), ("text/css", ".css")):
    mimetypes.add_type(_type, _ext)  # the Windows registry can say text/plain


def safe_file(root: Path, rel: str) -> Path | None:
    parts = Path(unquote(rel)).parts
    if not parts or any(p.startswith(".") or p == ".." for p in parts):
        return None
    full = (root / Path(*parts)).resolve()
    if root.resolve() not in full.parents or not full.is_file():
        return None
    if full.name in BLOCKED_NAMES or full.suffix.lower() in BLOCKED_SUFFIXES:
        return None
    return full


def lanes_view(cfg: Config, s: Store, used: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for lid, ln in cfg.lanes.items():
        row = s.lane(lid)
        task = s.live_task(lid) if ln.kind == "team" else None
        doing = None
        live = row.get("status") not in (None, "closed")
        if lid == HUB and live and row.get("doing") and (row.get("doing_at") or 0) >= (row.get("opened") or 0):
            doing = clip(row["doing"], 80)
        elif lid == INFRA and live and row.get("active_message") is not None:
            try:
                m = s.message(int(row["active_message"]))
                doing = clip(f"#{m['id']} {m['text']}", 80)
            except StoreError:
                pass
        out.append({"lane": lid, "label": ln.label, "runtime": ln.runtime, "kind": ln.kind,
                    "status": row.get("status") or "closed", "doing": doing,
                    "task_id": task["id"] if task else None, "task_title": task["title"] if task else None,
                    "ctx_k": s.session_ctx(lid, row.get("session")) if live else None, "usage": used.get(lid)})
    return out


def usage_view(cfg: Config, day: str, used: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Today's totals per runtime in use, in lane order."""
    rows: dict[str, dict[str, Any]] = {}
    for lid, ln in cfg.lanes.items():
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


def state(cfg: Config, s: Store, me: dict[str, Any] | None) -> dict[str, Any]:
    tasks = s.tasks(("queued", "running", "wrapping"), limit=200)
    day = s.day()
    used = s.usage(day)
    return {
        "title": cfg.name,
        "lang": cfg.lang,
        "auth_mode": cfg.auth_mode,
        "me": me,
        "running": tower_pid(cfg) is not None,
        "version": __version__,
        "local_only": cfg.local_only,
        "paused": (cfg.state_dir / "paused").exists(),
        "extensions": board_extras(cfg.home),
        "lane_cap": {"used": len({t["lane"] for t in tasks if t["state"] in ("running", "wrapping")}),
                     "cap": cfg.max_lanes, "max": MAX_LANES_LIMIT},
        "runtimes": list(RUNTIMES),
        "default_runtime": cfg.lanes[HUB].runtime,
        "usage": usage_view(cfg, day, used),
        "goals": s.goals(GOAL_RANKED) + s.history("goals", 0, PREVIEW),
        "goal_tasks": {str(k): v for k, v in s.goal_tasks().items()},
        "lanes": lanes_view(cfg, s, used),
        "items": s.items(state="open", limit=500) + s.history("decided", 0, PREVIEW),
        "tasks": tasks + s.history("closed", 0, PREVIEW),
        "reports": s.history("reports", 0, PREVIEW),
        "memos": {INFRA: s.memos(INFRA, PREVIEW), HUB: s.memos(HUB, PREVIEW)},
        "counts": {k: s.history_count(k) for k in (*Store.HISTORY, *Store.MEMO_HISTORY)},
    }


def goal_action(s: Store, gid: int, action: str, note: str, by: str) -> str:
    """Apply one board action to a goal; returns the news line for the hub."""
    g = s.goal(gid)
    if action in ("top", "up", "down"):
        pos = {"top": 1, "up": g["priority"] - 1, "down": g["priority"] + 1}[action]
        order = s.move_goal(gid, max(1, pos))
        return f"{by} moved goal #{gid} \"{g['title']}\" to priority {order.index(gid) + 1}. Order now: " + \
            ", ".join(f"#{i}" for i in order)
    if action not in GOAL_ACTIONS:
        raise StoreError(f"action must be one of top, up, down, {', '.join(GOAL_ACTIONS)}")
    g = s.set_goal_state(gid, GOAL_ACTIONS[action], note)
    return f"{by} marked goal #{gid} \"{g['title']}\" {g['state']}" + (f": {note}" if note else "")


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

    def fresh_own() -> Site:
        """This workspace's config as tower.json has it now (infra adds lanes, the board sets max_lanes)."""
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
        return site is own or site.cfg.auth_mode != "local" or cfg.auth_mode == "local"

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
                top = site.s.goals(("active",))
                row["goal"] = top[0]["title"] if top else None
            out.append(row)
        return out

    class Handler(BaseHTTPRequestHandler):
        site: Site = own
        route = "/"

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _resolve(self) -> bool:
            """Pick the workspace from the path. False when a response (redirect, 404) was already sent."""
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
                self.end_headers()
                return False
            self.site, self.route, self.query = site, m.group(2), query
            return True

        @property
        def cookie(self) -> str:
            return COOKIE if self.site is own else f"{COOKIE}_{self.site.slug}"

        # ----- auth -----

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
                if any(x["ident"] for x in st.members()):
                    return None  # an allow-list exists and this person is not on it
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

        # ----- responses -----

        def _json(self, code: int, body: Any, headers: dict[str, str] | None = None) -> None:
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _static(self, path: Path, ctype: str) -> None:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("content-type", ctype)
            self.send_header("cache-control", "no-cache")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # ----- GET -----

        def do_GET(self) -> None:
            if not self._resolve():
                return
            c, st, path = self.site.cfg, self.site.s, self.route
            if path in ("/", "/index.html"):
                page = None if "builtin" in parse_qs(self.query, keep_blank_values=True) else board_override(c.home)
                return self._static(page or BOARD, "text/html; charset=utf-8")
            if path == "/manifest.webmanifest":
                return self._json(200, {"name": c.name, "short_name": c.name[:12], "start_url": "./",
                                        "display": "standalone", "background_color": "#17171c",
                                        "theme_color": "#17171c"})
            me = self._me()
            if me is None:
                return self._json(401, {"error": "sign in"})
            if path == "/api/state":
                return self._json(200, state(c, st, me))
            if path == "/api/workspaces":
                return self._json(200, {"workspaces": workspaces_view(self.site)})
            if path == "/api/history":
                q = parse_qs(self.query)
                try:
                    rows = st.history((q.get("kind") or [""])[0], max(0, int((q.get("offset") or ["0"])[0])), PAGE)
                except (StoreError, ValueError) as e:
                    return self._json(400, {"error": str(e)})
                return self._json(200, {"rows": rows, "more": len(rows) == PAGE})
            m = re.fullmatch(r"/api/items/(\d+)", path)
            if m:
                try:
                    return self._json(200, st.item(int(m.group(1))))
                except StoreError as e:
                    return self._json(404, {"error": str(e)})
            if path.startswith("/f/"):
                return self._file(path[3:])
            if path.startswith("/x/"):
                full = safe_file(board_dir(c.home), path[3:])
                if not full or full.suffix.lower() not in STATIC_SUFFIXES:
                    return self._json(404, {"error": "not found"})
                ctype = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
                if ctype.startswith(("text/", "application/json")) and "charset" not in ctype:
                    ctype += "; charset=utf-8"
                return self._static(full, ctype)
            if path.startswith("/api/x/"):
                return self._ext("GET", path[7:], me, {})
            self._json(404, {"error": "not found"})

        def _ext(self, method: str, name: str, me: dict[str, Any], body: dict[str, Any]) -> None:
            fn = self.site.ext.routes().get((method, name.strip("/")))
            if fn is None:
                return self._json(404, {"error": "not found"})
            ctx = {"cfg": self.site.cfg, "store": self.site.s, "me": me, "log": log,
                   "query": {k: v[-1] for k, v in parse_qs(self.query).items()}}
            try:
                out = fn(ctx, body)
                code, data = out if isinstance(out, tuple) and len(out) == 2 and isinstance(out[0], int) else (200, out)
                json.dumps(data)
            except Exception as e:  # an extension's bug is its own 500, never the board's
                log(f"extension route {method} /api/x/{name} failed: {type(e).__name__}: {e}")
                return self._json(500, {"error": f"extension failed: {type(e).__name__}: {e}"})
            return self._json(code, data)

        def _file(self, rel: str) -> None:
            full = safe_file(self.site.cfg.workdir, rel)
            if not full:
                return self._json(404, {"error": "not found"})
            ctype = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") and "charset" not in ctype:
                ctype += "; charset=utf-8"
            size = full.stat().st_size
            start, end = 0, size - 1
            rng = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("range", ""))
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
                    self.end_headers()
                    return
            self.send_response(206 if rng else 200)
            self.send_header("content-type", ctype)
            self.send_header("accept-ranges", "bytes")
            self.send_header("content-length", str(end - start + 1 if size else 0))
            if rng:
                self.send_header("content-range", f"bytes {start}-{end}/{size}")
            if ctype.startswith("text/html"):
                self.send_header("content-security-policy", "sandbox allow-scripts")
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

        # ----- POST -----

        def do_POST(self) -> None:
            if self.headers.get("x-tower") != "1":
                return self._json(403, {"error": "missing x-tower header"})
            if not self._resolve():
                return
            c, st, path = self.site.cfg, self.site.s, self.route
            n = int(self.headers.get("content-length") or 0)
            if n > MAX_BODY:
                return self._json(413, {"error": "too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
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
                    choices = body.get("choices")
                    it = st.decide(int(m.group(1)), str(body.get("verdict")), who,
                                   [str(x) for x in choices] if isinstance(choices, list) else body.get("choice") or None,
                                   str(body.get("note") or "")[:4000])
                    log(f"{who} decided #{it['id']}: {it['verdict']} {it['choice'] or ''}")
                    return self._json(200, {"ok": True, "item": it})
                if path == "/api/memo":
                    text = str(body.get("text") or "").strip()[:4000]
                    to = str(body.get("to") or INFRA)
                    if to not in c.lanes or to not in (HUB, INFRA):
                        raise StoreError("memos go to hub or infra")
                    if not text:
                        raise StoreError("empty memo")
                    mid = st.push(to, who, "memo", text)
                    log(f"{who} memo #{mid} → {to}")
                    return self._json(200, {"ok": True, "id": mid})
                if path == "/api/goals":
                    gid = st.add_goal(str(body.get("title") or "")[:200], str(body.get("detail") or "")[:4000], who)
                    g = st.goal(gid)
                    st.push(HUB, who, "goal", f"New goal #{gid} (priority {g['priority']}) from {who}: \"{g['title']}\""
                            + (f" — {g['detail']}" if g["detail"] else "")
                            + ". Agree a plan with them (a board item or a memo reply), then `dah goal plan`.")
                    log(f"{who} goal #{gid}")
                    return self._json(200, {"ok": True, "id": gid})
                if path == "/api/settings":
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
                m = re.fullmatch(r"/api/goals/(\d+)", path)
                if m:
                    news = goal_action(st, int(m.group(1)), str(body.get("action") or ""),
                                       str(body.get("note") or "")[:1000], who)
                    st.push(HUB, who, "goal", news)
                    log(news)
                    return self._json(200, {"ok": True, "goal": st.goal(int(m.group(1)))})
            except StoreError as e:
                return self._json(409, {"error": str(e)})
            if path.startswith("/api/x/"):
                return self._ext("POST", path[7:], me, body)
            self._json(404, {"error": "not found"})

    return Handler


def server(cfg: Config, s: Store, log: Callable[[str], None] = print,
           ext: Extensions | None = None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((cfg.web_host, cfg.web_port), make_handler(cfg, s, log, ext))
