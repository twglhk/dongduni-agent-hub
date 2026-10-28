"""Review board web server (a thread inside `tower run`). Binds 127.0.0.1 by default; expose it through a proxy
that authenticates people (Cloudflare Access, Tailscale, an AWS ALB with OIDC, ...) or use token sign-in.

  GET  /                          board (board/index.html)
  GET  /api/state                 lanes · open items · tasks · reports · memos · counts
  GET  /api/history?kind=K&offset=N   decided | reports | closed | memos | requests, 20 per page
  GET  /api/items/<id>
  GET  /f/<path>                  attachment under the project (dot paths and secrets are refused; Range supported)
  POST /api/items/<id>/decide     {verdict, choice | choices[], note}
  POST /api/memo                  {text, to: infra | hub}
  POST /api/login {token} · POST /api/logout      (auth.mode = token)

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

from .config import HUB, INFRA, Config
from .engine import clip
from .store import Store, StoreError

BOARD = Path(__file__).resolve().parent / "board" / "index.html"
BLOCKED_NAMES = {".env", "opencode.json", "tower.json", "secrets.json", "credentials", "id_rsa", "id_ed25519"}
BLOCKED_SUFFIXES = {".db", ".sqlite", ".pem", ".key", ".p12"}
MAX_BODY = 64 * 1024
PREVIEW = 3
PAGE = 20
COOKIE = "tower_token"
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
mimetypes.add_type("text/plain; charset=utf-8", ".md")


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


def lanes_view(cfg: Config, s: Store) -> list[dict[str, Any]]:
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
                    "ctx_k": None})
    return out


def state(cfg: Config, s: Store, me: dict[str, Any] | None) -> dict[str, Any]:
    tasks = s.tasks(("queued", "running", "wrapping"), limit=200)
    return {
        "title": cfg.name,
        "lang": cfg.lang,
        "auth_mode": cfg.auth_mode,
        "me": me,
        "paused": (cfg.state_dir / "paused").exists(),
        "lane_cap": {"used": len({t["lane"] for t in tasks if t["state"] in ("running", "wrapping")}),
                     "cap": cfg.max_lanes},
        "lanes": lanes_view(cfg, s),
        "items": s.items(state="open", limit=500) + s.history("decided", 0, PREVIEW),
        "tasks": tasks + s.history("closed", 0, PREVIEW),
        "reports": s.history("reports", 0, PREVIEW),
        "memos": {INFRA: s.memos(INFRA, PREVIEW), HUB: s.memos(HUB, PREVIEW)},
        "counts": {k: s.history_count(k) for k in (*Store.HISTORY, *Store.MEMO_HISTORY)},
    }


def make_handler(cfg: Config, s: Store, log: Callable[[str], None]):
    secure = cfg.public_url.startswith("https://")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        # ----- auth -----

        def _me(self) -> dict[str, Any] | None:
            if cfg.auth_mode == "local":
                return {"name": getpass.getuser(), "role": "owner"}
            if cfg.auth_mode == "header":
                if self.client_address[0] not in LOOPBACK:
                    return None  # identity headers are only trusted from the local proxy
                ident = (self.headers.get(cfg.auth_header) or "").strip()
                if not ident:
                    return None
                m = s.member_by_ident(ident)
                if m:
                    return m
                if any(x["ident"] for x in s.members()):
                    return None  # an allow-list exists and this person is not on it
                return {"name": ident, "role": "member"}
            token = ""
            auth = self.headers.get("authorization") or ""
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
            for part in (self.headers.get("cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE:
                    token = token or v
            return s.member_by_token(token) if token else None

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
            path, _, query = self.path.partition("?")
            if path in ("/", "/index.html"):
                return self._static(BOARD, "text/html; charset=utf-8")
            if path == "/manifest.webmanifest":
                return self._json(200, {"name": cfg.name, "short_name": cfg.name[:12], "start_url": "/",
                                        "display": "standalone", "background_color": "#17171c",
                                        "theme_color": "#17171c"})
            me = self._me()
            if me is None:
                return self._json(401, {"error": "sign in"})
            if path == "/api/state":
                return self._json(200, state(cfg, s, me))
            if path == "/api/history":
                q = parse_qs(query)
                try:
                    rows = s.history((q.get("kind") or [""])[0], max(0, int((q.get("offset") or ["0"])[0])), PAGE)
                except (StoreError, ValueError) as e:
                    return self._json(400, {"error": str(e)})
                return self._json(200, {"rows": rows, "more": len(rows) == PAGE})
            m = re.fullmatch(r"/api/items/(\d+)", path)
            if m:
                try:
                    return self._json(200, s.item(int(m.group(1))))
                except StoreError as e:
                    return self._json(404, {"error": str(e)})
            if path.startswith("/f/"):
                return self._file(path[3:])
            self._json(404, {"error": "not found"})

        def _file(self, rel: str) -> None:
            full = safe_file(cfg.workdir, rel)
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
            n = int(self.headers.get("content-length") or 0)
            if n > MAX_BODY:
                return self._json(413, {"error": "too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "invalid JSON"})
            if not isinstance(body, dict):
                return self._json(400, {"error": "invalid JSON"})
            if self.path == "/api/login":
                m = s.member_by_token(str(body.get("token") or "")) if cfg.auth_mode == "token" else None
                if not m:
                    return self._json(401, {"error": "unknown token"})
                cookie = f"{COOKIE}={body['token']}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000"
                return self._json(200, {"ok": True, "me": m}, {"set-cookie": cookie + ("; Secure" if secure else "")})
            if self.path == "/api/logout":
                return self._json(200, {"ok": True}, {"set-cookie": f"{COOKIE}=; Path=/; Max-Age=0"})
            me = self._me()
            if me is None:
                return self._json(401, {"error": "sign in"})
            try:
                m = re.fullmatch(r"/api/items/(\d+)/decide", self.path)
                if m:
                    choices = body.get("choices")
                    it = s.decide(int(m.group(1)), str(body.get("verdict")), str(me["name"]),
                                  [str(c) for c in choices] if isinstance(choices, list) else body.get("choice") or None,
                                  str(body.get("note") or "")[:4000])
                    log(f"{me['name']} decided #{it['id']}: {it['verdict']} {it['choice'] or ''}")
                    return self._json(200, {"ok": True, "item": it})
                if self.path == "/api/memo":
                    text = str(body.get("text") or "").strip()[:4000]
                    to = str(body.get("to") or INFRA)
                    if to not in cfg.lanes or to not in (HUB, INFRA):
                        raise StoreError("memos go to hub or infra")
                    if not text:
                        raise StoreError("empty memo")
                    mid = s.push(to, str(me["name"]), "memo", text)
                    log(f"{me['name']} memo #{mid} → {to}")
                    return self._json(200, {"ok": True, "id": mid})
            except StoreError as e:
                return self._json(409, {"error": str(e)})
            self._json(404, {"error": "not found"})

    return Handler


def server(cfg: Config, s: Store, log: Callable[[str], None] = print) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((cfg.web_host, cfg.web_port), make_handler(cfg, s, log))
