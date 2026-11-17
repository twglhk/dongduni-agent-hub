#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Sequence

DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATUS_NAME = "tower-db-backup.json"
TIMEOUT = 600

Runner = Callable[[Sequence[str]], "tuple[int, str, str]"]


class BackupError(RuntimeError):
    pass


def _private(path: Path, mode: int) -> None:
    if os.name != "nt":
        os.chmod(path, mode)


def snapshot(src: Path, dest: Path) -> int:
    if not src.is_file():
        raise BackupError(f"database not found: {src}")
    if dest.exists():
        dest.unlink()
    conn = sqlite3.connect(str(src), timeout=30)
    try:
        out = sqlite3.connect(str(dest))
        try:
            conn.backup(out)
            _private(dest, 0o600)
            out.execute("PRAGMA journal_mode=DELETE")
            verdict = out.execute("PRAGMA integrity_check").fetchone()[0]
            if verdict != "ok":
                raise BackupError(f"integrity_check: {verdict}")
            return out.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        finally:
            out.close()
    finally:
        conn.close()


def run_command(argv: Sequence[str]) -> "tuple[int, str, str]":
    try:
        r = subprocess.run(list(argv), capture_output=True, text=True, timeout=TIMEOUT, encoding="utf-8")
    except FileNotFoundError:
        raise BackupError(f"command not found: {argv[0]} (is it on this service's PATH?)") from None
    except subprocess.TimeoutExpired:
        raise BackupError(f"{argv[0]} timed out after {TIMEOUT}s") from None
    return r.returncode, r.stdout, r.stderr


def _checked(runner: Runner, argv: Sequence[str]) -> str:
    code, out, err = runner(argv)
    if code:
        raise BackupError(f"{Path(argv[0]).name} {argv[1]} exit {code}: {(err or out).strip()[:300]}")
    return out


class RcloneUploader:
    def __init__(self, dest: str, binary: str = "rclone", extra: Sequence[str] = (), runner: Runner = run_command):
        self.dest, self.binary, self.extra, self.run = dest.rstrip("/"), binary, list(extra), runner

    def _call(self, *args: str) -> str:
        return _checked(self.run, [self.binary, *args, *self.extra])

    def put(self, local: Path, day: str, name: str) -> str:
        self._call("copyto", str(local), f"{self.dest}/{day}/{name}")
        return f"{self.dest}/{day}/{name}"

    def size(self, day: str, name: str) -> "int | None":
        for f in json.loads(self._call("lsjson", f"{self.dest}/{day}", "--files-only") or "[]"):
            if f.get("Name") == name:
                return int(f.get("Size", -1))
        return None

    def days(self) -> "list[str]":
        return [d.get("Name", "") for d in json.loads(self._call("lsjson", self.dest, "--dirs-only") or "[]")]

    def delete_day(self, day: str) -> None:
        self._call("purge", f"{self.dest}/{day}")


class S3Uploader:
    def __init__(self, dest: str, binary: str = "aws", extra: Sequence[str] = (), runner: Runner = run_command):
        m = re.match(r"^s3://([^/]+)/?(.*)$", dest)
        if not m:
            raise BackupError(f"not an s3:// destination: {dest}")
        self.bucket, self.prefix = m.group(1), m.group(2).strip("/")
        self.binary, self.extra, self.run = binary, list(extra), runner

    def _key(self, *parts: str) -> str:
        return "/".join(p for p in (self.prefix, *parts) if p)

    def _url(self, *parts: str) -> str:
        return f"s3://{self.bucket}/{self._key(*parts)}"

    def _call(self, *args: str) -> str:
        return _checked(self.run, [self.binary, *args, *self.extra])

    def put(self, local: Path, day: str, name: str) -> str:
        self._call("s3", "cp", str(local), self._url(day, name), "--only-show-errors")
        return self._url(day, name)

    def size(self, day: str, name: str) -> "int | None":
        code, out, err = self.run([self.binary, "s3api", "head-object", "--bucket", self.bucket, "--key",
                                   self._key(day, name), "--output", "json", *self.extra])
        if code:
            if "404" in err or "Not Found" in err:
                return None
            raise BackupError(f"aws s3api exit {code}: {(err or out).strip()[:300]}")
        return int(json.loads(out)["ContentLength"])

    def days(self) -> "list[str]":
        out = self._call("s3", "ls", self._url() + "/")
        return [m.group(1) for m in re.finditer(r"^\s*PRE\s+(\S+?)/\s*$", out, re.M)]

    def delete_day(self, day: str) -> None:
        self._call("s3", "rm", self._url(day) + "/", "--recursive", "--only-show-errors")


class DirUploader:
    def __init__(self, dest: str):
        self.root = Path(dest).expanduser()

    def put(self, local: Path, day: str, name: str) -> str:
        target = self.root / day / name
        target.parent.mkdir(parents=True, exist_ok=True)
        _private(target.parent, 0o700)
        shutil.copyfile(local, target)
        _private(target, 0o600)
        return str(target)

    def size(self, day: str, name: str) -> "int | None":
        p = self.root / day / name
        return p.stat().st_size if p.is_file() else None

    def days(self) -> "list[str]":
        return [p.name for p in self.root.iterdir() if p.is_dir()] if self.root.is_dir() else []

    def delete_day(self, day: str) -> None:
        shutil.rmtree(self.root / day)


def make_uploader(dest: str, method: str = "auto", rclone_args: Sequence[str] = (), aws_args: Sequence[str] = (),
                  rclone_bin: str = "rclone", aws_bin: str = "aws"):
    if method == "auto":
        if dest.startswith("s3://"):
            method = "aws"
        elif os.path.isabs(dest) or dest.startswith((".", "~")):
            method = "dir"
        else:
            method = "rclone"
    if method == "aws":
        return S3Uploader(dest, aws_bin, aws_args)
    if method == "rclone":
        return RcloneUploader(dest, rclone_bin, rclone_args)
    if method == "dir":
        return DirUploader(dest)
    raise BackupError(f"unknown method {method!r} (rclone, aws, dir)")


def old_days(days: Sequence[str], today: datetime.date, keep_days: int) -> "list[str]":
    cutoff = today - datetime.timedelta(days=keep_days)
    out = []
    for d in sorted(days):
        if not DATE_DIR.match(d):
            continue
        try:
            when = datetime.date.fromisoformat(d)
        except ValueError:
            continue
        if when < cutoff:
            out.append(d)
    return out


def write_status(path: Path, update: dict) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.update(update)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def run_backup(db: Path, uploader, status: Path, keep_days: int = 30, why: str = "manual",
               now: "datetime.datetime | None" = None, out=None, err=None) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    now = now or datetime.datetime.now()
    day, name = f"{now:%Y-%m-%d}", f"tower-{now:%H%M}.db"
    started = time.time()
    where = f"{day}/{name}"
    try:
        with tempfile.TemporaryDirectory(prefix="tower-db-backup-") as tmp:
            copy = Path(tmp) / "tower.db"
            tables = snapshot(db, copy)
            size = copy.stat().st_size
            where = uploader.put(copy, day, name)
            remote = uploader.size(day, name)
            if remote is None:
                raise BackupError(f"uploaded file missing: {day}/{name}")
            if remote != size:
                raise BackupError(f"uploaded size check failed: local {size}, remote {remote}")
    except Exception as e:
        write_status(status, {"last_fail": started, "fail_key": where, "fail_why": why, "error": str(e)[:400]})
        print(f"tower-db-backup: FAILED ({why}): {e}", file=err)
        return 1
    pruned, prune_error = [], ""
    try:
        pruned = old_days(uploader.days(), now.date(), keep_days)
        for d in pruned:
            uploader.delete_day(d)
    except Exception as e:
        prune_error = f"{type(e).__name__}: {e}"[:400]
        print(f"tower-db-backup: backup ok, pruning failed: {e}", file=err)
    write_status(status, {"last_ok": started, "ok_key": where, "ok_why": why, "ok_size": size,
                          "ok_tables": tables, "pruned": pruned, "prune_error": prune_error})
    print(f"tower-db-backup: {why} -> {where} ({size} bytes, {tables} tables)"
          + (f", removed {', '.join(pruned)}" if pruned else ""), file=out)
    return 0


def main(argv: "Sequence[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="back up the tower's tower.db to rclone, S3 or a folder")
    ap.add_argument("--workspace", default=".", help="the workspace folder (default: the current folder)")
    ap.add_argument("--db", help="database file (default: <workspace>/.tower/state/tower.db)")
    ap.add_argument("--dest", default=os.environ.get("TOWER_BACKUP_DEST"),
                    help="remote:path | s3://bucket/prefix | folder (or env TOWER_BACKUP_DEST)")
    ap.add_argument("--method", default=os.environ.get("TOWER_BACKUP_METHOD", "auto"),
                    choices=["auto", "rclone", "aws", "dir"])
    ap.add_argument("--keep-days", type=int, default=int(os.environ.get("TOWER_BACKUP_KEEP_DAYS", "30")),
                    help="delete date folders older than this many days (default 30)")
    ap.add_argument("--rclone-arg", action="append", default=[], help="extra rclone argument, repeatable")
    ap.add_argument("--aws-arg", action="append", default=[], help="extra aws argument, repeatable")
    ap.add_argument("--rclone-bin", default="rclone")
    ap.add_argument("--aws-bin", default="aws")
    ap.add_argument("--status", help="status file (default: next to the database)")
    ap.add_argument("--why", default="manual", help="daily | restart | manual (only recorded)")
    a = ap.parse_args(argv)
    if not a.dest:
        print("tower-db-backup: no destination: pass --dest or set TOWER_BACKUP_DEST", file=sys.stderr)
        return 2
    if a.keep_days < 1:
        print("tower-db-backup: --keep-days must be at least 1", file=sys.stderr)
        return 2
    db = Path(a.db) if a.db else Path(a.workspace) / ".tower" / "state" / "tower.db"
    status = Path(a.status) if a.status else db.parent / STATUS_NAME
    try:
        uploader = make_uploader(a.dest, a.method, a.rclone_arg, a.aws_arg, a.rclone_bin, a.aws_bin)
    except BackupError as e:
        print(f"tower-db-backup: {e}", file=sys.stderr)
        return 2
    return run_backup(db, uploader, status, a.keep_days, a.why)


if __name__ == "__main__":
    sys.exit(main())
