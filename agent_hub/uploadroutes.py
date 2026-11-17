from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs

from . import uploads
from . import visibility as vis
from .config import Config
from .storage import CHUNK, StorageError
from .store import Store

SWEEP_EVERY = 60
CACHE = "private, max-age=3600"
READ = 1 << 16
VIEW_SECONDS = 86400


def info(cfg: Config) -> dict[str, Any] | None:
    return uploads.info(cfg)


def connect_host(cfg: Config) -> str:
    if cfg.uploads.get("provider") != "s3":
        return ""
    try:
        return uploads.provider(cfg).connect_host()
    except uploads.UploadError:
        return ""


def _key(query: str) -> str:
    return uploads.resolve((parse_qs(query).get("key") or [""])[0])


def view(h: Any, cfg: Config, st: Store, me: dict[str, Any], query: str) -> None:
    try:
        key = _key(query)
        if not vis.upload_allowed(vis.Gate.of(cfg, st, me), key):
            return h._json(404, {"error": "not found"})
        got = uploads.provider(cfg).view(key)
    except uploads.UploadError as e:
        return h._json(400, {"error": str(e)})
    except StorageError as e:
        return h._json(502, {"error": str(e)})
    if got is None:
        return h._json(404, {"error": "no such attachment"})
    out = {"url": got.url, "type": got.type, "expires": int(time.time()) + VIEW_SECONDS}
    if got.type.startswith("image/"):
        out["thumb"] = got.thumb or got.url
    return h._json(200, out)


def stream(h: Any, cfg: Config, st: Store, me: dict[str, Any], query: str) -> None:
    try:
        key = _key(query)
        if not vis.upload_allowed(vis.Gate.of(cfg, st, me), key):
            return h._json(404, {"error": "not found"})
        got = uploads.provider(cfg).open(key, h.headers.get("range", ""))
    except uploads.UploadError as e:
        return h._json(400, {"error": str(e)})
    except StorageError as e:
        return h._json(416 if "range" in str(e).lower() else 502, {"error": str(e)})
    if got is None:
        return h._json(404, {"error": "no such attachment"})
    try:
        h.send_response(got.status)
        h.send_header("content-type", got.type)
        h.send_header("accept-ranges", "bytes")
        if got.length is not None:
            h.send_header("content-length", str(got.length))
        if got.content_range:
            h.send_header("content-range", got.content_range)
        h.send_header("cache-control", CACHE)
        h.send_header("x-content-type-options", "nosniff")
        h.send_header("content-security-policy", "sandbox")
        h.send_header("referrer-policy", "no-referrer")
        if not got.type.startswith(("image/", "video/", "audio/")):
            h.send_header("content-disposition", "attachment")
        h.end_headers()
        for part in got.chunks:
            h.wfile.write(part)
    except (BrokenPipeError, ConnectionResetError):
        return
    finally:
        if got.close:
            got.close()


def steps(h: Any, cfg: Config, st: Store, who: str, step: str, body: dict[str, Any], log: Any) -> None:
    try:
        if step == "start":
            text = str(body.get("text") or "").strip()[:4000]
            out = uploads.start(cfg, st, who, cfg.hub_of(who), text, body.get("files"))
            log(f"{who} request #{out['id']} with {len(out['files'])} attachment(s), held until uploaded")
            return h._json(200, out)
        if step == "parts":
            urls = uploads.part_urls(cfg, st, _whole(body.get("id")), who, body.get("n"), body.get("parts"))
            return h._json(200, {"urls": urls})
        if step == "finish":
            out = uploads.finish(cfg, st, _whole(body.get("id")), who, body.get("results"))
            log(f"{who} request #{out['id']} → {cfg.hub_of(who)}: {out['sent']}/{out['total']} attachment(s)")
            return h._json(200, out)
    except uploads.UploadError as e:
        return h._json(409, {"error": str(e)})
    h._json(404, {"error": "not found"})


def _whole(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def chunk(h: Any, cfg: Config, st: Store, who: str) -> None:
    q = {k: v[-1] for k, v in parse_qs(h.query).items()}
    try:
        size = int(h.headers.get("content-length") or 0)
        rid, n, offset = int(q.get("id", "")), int(q.get("n", "")), int(q.get("offset", ""))
    except ValueError:
        return h._json(400, {"error": "id, n and offset must be whole numbers"})
    if not 0 <= size <= CHUNK:
        if 0 < size <= 2 * CHUNK:
            h.rfile.read(size)
        return h._json(413, {"error": f"send at most {CHUNK} bytes at a time"})
    data = h.rfile.read(size)
    if len(data) != size:
        return h._json(400, {"error": "the chunk ended early"})
    try:
        return h._json(200, uploads.chunk(cfg, st, rid, who, n, offset, data))
    except uploads.Conflict as e:
        return h._json(409, {"error": str(e), "received": e.received})
    except uploads.UploadError as e:
        return h._json(409, {"error": str(e)})


class Sweeper:
    def __init__(self) -> None:
        self.at = 0.0

    def run(self, cfg: Config, st: Store, log: Any) -> None:
        if time.time() - self.at < SWEEP_EVERY:
            return
        self.at = time.time()
        for rid in uploads.sweep(cfg, st):
            log(f"request #{rid}: its upload never finished, sent on with what arrived")
