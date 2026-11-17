from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import storage
from .config import Config
from .storage import OutOfOrder, Slot, Storage, StorageError
from .store import HOLD, Store, StoreError

MAX_NAME = 200
HOLD_MAX = 2 * 3600
EXT = {
    "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp", "image/heic": "heic",
    "image/heif": "heif", "image/avif": "avif",
    "video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm", "video/x-m4v": "m4v", "video/3gpp": "3gp",
    "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac", "audio/wav": "wav",
    "audio/x-wav": "wav", "audio/wave": "wav", "audio/ogg": "ogg", "audio/flac": "flac", "audio/webm": "weba",
    "text/plain": "txt", "text/markdown": "md", "text/csv": "csv", "application/json": "json",
    "application/pdf": "pdf",
}
BY_EXT = {"jpeg": "image/jpeg", **{v: k for k, v in reversed(EXT.items())}}
EXTENSIONS = tuple(sorted(BY_EXT))
TAIL = re.compile(r"uploads/\d{4}-\d{2}-\d{2}/\d+/\d+-[A-Za-z0-9_-]+\.(?:" + "|".join(EXTENSIONS) + ")")
KEY_AT_END = re.compile(r"(?:^|/)(" + TAIL.pattern + ")$")
TEXT = {
    "ko": {"head": "첨부 {n}개 ({where}, 보드에서 바로 보기):", "fail": "첨부 실패 {f}개 / {n}개:",
           "orig": "원래 이름", "only": "(첨부만 보냄)", "cut": "올리기가 끝나지 않음", "size": "크기가 다름",
           "store": "저장하지 못함",
           "local": "작업 폴더 {root}에 저장됨", "drive": "rclone 원격에 저장됨", "s3": "S3에 저장됨"},
    "en": {"head": "{n} attachment(s) ({where}, viewable on the board):", "fail": "{f} of {n} attachment(s) failed:",
           "orig": "original name", "only": "(attachments only)", "cut": "upload did not finish",
           "size": "size differs", "store": "could not be stored",
           "local": "saved in the workspace under {root}", "drive": "stored on an rclone remote", "s3": "stored in S3"},
}
_lock = threading.Lock()
_rid_locks: dict[int, threading.Lock] = {}
_providers: dict[tuple[str, str], Storage] = {}


class UploadError(Exception):
    pass


class Conflict(UploadError):
    def __init__(self, received: int):
        super().__init__(f"the server has {received} bytes of this file: continue from there")
        self.received = received


def enabled(cfg: Config) -> bool:
    return cfg.uploads.get("provider", "off") != "off"


def info(cfg: Config) -> dict[str, Any] | None:
    if not enabled(cfg):
        return None
    return {"provider": cfg.uploads["provider"], "max_mb": cfg.uploads["max_mb"], "max_files": cfg.uploads["max_files"],
            "chunk": storage.CHUNK, "accept": list(EXTENSIONS)}


def provider(cfg: Config) -> Storage:
    if not enabled(cfg):
        raise UploadError("attachments are off: tower.json has uploads.provider off")
    k = (json.dumps(cfg.uploads, sort_keys=True), str(cfg.home))
    with _lock:
        if k not in _providers:
            _providers[k] = storage.make(cfg.uploads, cfg.home, cfg.workdir)
        return _providers[k]


def _guard(rid: int) -> threading.Lock:
    with _lock:
        return _rid_locks.setdefault(int(rid), threading.Lock())


def _dir(cfg: Config) -> Path:
    return cfg.state_dir / "uploads"


def _plan_path(cfg: Config, rid: int) -> Path:
    return _dir(cfg) / f"{int(rid)}.json"


def _slot(f: dict[str, Any]) -> Slot:
    return Slot(f["rid"], f["n"], f["key"], f["type"], f["size"], f.get("upload_id", ""), f.get("part_size", 0))


def _load(cfg: Config, rid: int, who: str | None) -> dict[str, Any]:
    try:
        plan = json.loads(_plan_path(cfg, rid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise UploadError(f"no upload in progress for request #{rid}") from None
    if who is not None and plan["sender"] != who:
        raise UploadError("this upload belongs to someone else")
    return plan


def _save(cfg: Config, plan: dict[str, Any]) -> None:
    path = _plan_path(cfg, plan["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def safe_stem(name: str) -> str:
    base = re.split(r"[\\/]", name)[-1]
    stem = base.rpartition(".")[0] if "." in base else base
    ascii_stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9_]+", "-", ascii_stem).strip("-_")[:48] or "file"


def kind_of(ctype: str, name: str) -> tuple[str, str]:
    ctype = ctype.split(";")[0].strip().lower()
    if ctype not in EXT:
        ctype = BY_EXT.get(name.rpartition(".")[2].lower() if "." in name else "", "")
    if not ctype:
        raise UploadError(f"{name}: this kind of file cannot be attached (images, video, audio, pdf, text, csv, json)")
    return ctype, EXT[ctype]


def check(cfg: Config, files: Any) -> list[dict[str, Any]]:
    cap = cfg.uploads["max_mb"] * 1024 * 1024
    if not isinstance(files, list) or not files:
        raise UploadError("no files")
    if len(files) > cfg.uploads["max_files"]:
        raise UploadError(f"at most {cfg.uploads['max_files']} files per request")
    out = []
    for f in files:
        f = f if isinstance(f, dict) else {}
        name = str(f.get("name") or "file")[:MAX_NAME]
        size = f.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise UploadError(f"{name}: empty or unknown size")
        ctype, ext = kind_of(str(f.get("type") or ""), name)
        if size > cap:
            raise UploadError(f"{name}: over {cfg.uploads['max_mb']} MB")
        out.append({"name": name, "type": ctype, "ext": ext, "size": size})
    return out


def _texts(cfg: Config) -> dict[str, str]:
    return TEXT.get(cfg.lang.split("-")[0].lower(), TEXT["en"])


def _room(cfg: Config, p: Storage, incoming: int) -> None:
    total_mb = cfg.uploads.get("max_total_mb", 0)
    if total_mb and p.used_bytes() + incoming > total_mb * 1024 * 1024:
        raise UploadError(f"the attachments folder is full (uploads.max_total_mb {total_mb})")


def start(cfg: Config, s: Store, who: str, hub: str, text: str, files: Any) -> dict[str, Any]:
    p = provider(cfg)
    checked = check(cfg, files)
    try:
        p.check()
    except StorageError as e:
        raise UploadError(str(e)) from None
    _room(cfg, p, sum(f["size"] for f in checked))
    text = text or _texts(cfg)["only"]
    rid = s.push(HOLD + hub, who, "memo", text)
    now = s.clock()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    plan: dict[str, Any] = {"id": rid, "sender": who, "hub": hub, "text": text, "created": now, "touched": now,
                            "files": []}
    out = []
    try:
        for i, f in enumerate(checked, 1):
            slot = Slot(rid, i, f"uploads/{day}/{rid}/{i}-{safe_stem(f['name'])}.{f['ext']}", f["type"], f["size"])
            slot = p.prepare(slot)
            plan["files"].append({**asdict(slot), "name": f["name"]})
            out.append({"n": i, **p.begin(slot)})
    except StorageError as e:
        for f in plan["files"]:
            p.discard(_slot(f))
        s.release_held(rid, hub, text)
        raise UploadError(str(e)) from None
    with _guard(rid):
        _save(cfg, plan)
    return {"id": rid, "files": out}


def _file(plan: dict[str, Any], n: Any) -> dict[str, Any]:
    f = next((x for x in plan["files"] if x["n"] == n), None)
    if f is None:
        raise UploadError(f"no file {n} in this upload")
    return f


def _touch(cfg: Config, plan: dict[str, Any], now: float) -> None:
    plan["touched"] = now
    _save(cfg, plan)


def chunk(cfg: Config, s: Store, rid: int, who: str, n: Any, offset: Any, data: bytes) -> dict[str, int]:
    p = provider(cfg)
    with _guard(rid):
        plan = _load(cfg, rid, who)
        f = _file(plan, n)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise UploadError("offset must be a whole number")
        try:
            received = p.receive(_slot(f), offset, data)
        except OutOfOrder as e:
            raise Conflict(e.received) from None
        except StorageError as e:
            raise UploadError(str(e)) from None
        _touch(cfg, plan, s.clock())
    return {"received": received}


def part_urls(cfg: Config, s: Store, rid: int, who: str, n: Any, numbers: Any) -> dict[str, str]:
    p = provider(cfg)
    with _guard(rid):
        plan = _load(cfg, rid, who)
        f = _file(plan, n)
        _touch(cfg, plan, s.clock())
    if not isinstance(numbers, list):
        raise UploadError("ask for 1 to 100 parts at a time")
    try:
        return p.more(_slot(f), numbers)
    except StorageError as e:
        raise UploadError(str(e)) from None


def _settle(cfg: Config, s: Store, plan: dict[str, Any], results: dict[int, Any]) -> dict[str, Any]:
    p = provider(cfg)
    rows = [(f, p.settle(_slot(f), results.get(f["n"]))) for f in plan["files"]]
    t = _texts(cfg)
    ok = [f for f, why in rows if not why]
    bad = [(f, why) for f, why in rows if why]
    lines = [plan["text"], ""]
    if ok:
        where = t[p.name].format(root=cfg.workdir)
        lines.append(t["head"].format(n=len(ok), where=where))
        lines += [f"- {p.locator(f['key'])} · {f['type']} · {mb(f['size'])} ({t['orig']}: {f['name']})" for f in ok]
    if bad:
        lines.append(t["fail"].format(f=len(bad), n=len(rows)))
        lines += [f"- {f['name']} · {f['type']} · {mb(f['size'])}: {t[why]}" for f, why in bad]
    s.release_held(plan["id"], plan["hub"], "\n".join(lines))
    _plan_path(cfg, plan["id"]).unlink(missing_ok=True)
    _rid_locks.pop(int(plan["id"]), None)
    return {"id": plan["id"], "sent": len(ok), "failed": len(bad), "total": len(rows),
            "files": [{"n": f["n"], "ok": not why, "key": f["key"]} for f, why in rows]}


def finish(cfg: Config, s: Store, rid: int, who: str, results: Any = None) -> dict[str, Any]:
    by_n = {r.get("n"): r for r in results if isinstance(r, dict)} if isinstance(results, list) else {}
    with _guard(rid):
        return _settle(cfg, s, _load(cfg, rid, who), by_n)


def sweep(cfg: Config, s: Store) -> list[int]:
    if not _dir(cfg).is_dir():
        return []
    done = []
    for path in sorted(_dir(cfg).glob("*.json")):
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        with _guard(plan["id"]):
            try:
                plan = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if s.clock() - max(plan["created"], plan.get("touched", 0)) < HOLD_MAX:
                continue
            try:
                if enabled(cfg):
                    _settle(cfg, s, plan, {})
                else:
                    s.release_held(plan["id"], plan["hub"], plan["text"])
                    path.unlink(missing_ok=True)
            except StoreError:
                path.unlink(missing_ok=True)
                continue
            done.append(plan["id"])
    return done


def resolve(key: str) -> str:
    m = KEY_AT_END.search(key)
    if not m or ".." in key.split("/"):
        raise UploadError("not an attachment path")
    return m.group(1)


def mb(n: int) -> str:
    return f"{n / 1024 ** 2:.1f} MB" if n >= 1024 * 1024 else f"{max(1, round(n / 1024))} KB"
