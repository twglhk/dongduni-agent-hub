from __future__ import annotations

import json
import mimetypes
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterator

from .. import procs
from .base import Opened, Receiver, Slot, StorageError, View

COPY_SECONDS = 3600
QUICK_SECONDS = 60
READ = 1 << 16
NOT_FOUND = 3
MISSING = "rclone is not installed: install it from https://rclone.org/install/, run `rclone config` to add the remote, and restart the board"


class Drive(Receiver):
    name = "drive"

    def __init__(self, remote: str, root: str, staging_dir: Path, binary: str = "rclone"):
        super().__init__()
        self.remote, self.root, self.staging_dir, self.binary = remote, root, staging_dir, binary

    def argv(self, *args: str) -> list[str]:
        return [*procs.command(self.binary), *args]

    def run(self, *args: str, timeout: float = QUICK_SECONDS) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(self.argv(*args), capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise StorageError(MISSING) from None
        except subprocess.TimeoutExpired:
            raise StorageError("rclone did not answer in time") from None

    def target(self, key: str) -> str:
        base = f"{self.root}/{key}" if self.root else key
        return f"{self.remote}:{base}"

    def check(self) -> None:
        if not shutil.which(self.binary):
            raise StorageError(MISSING)
        got = self.run("listremotes")
        if got.returncode != 0:
            raise StorageError("rclone could not list its remotes: " + got.stderr.decode(errors="replace").strip()[:200])
        if f"{self.remote}:" not in got.stdout.decode(errors="replace").split():
            raise StorageError(f"rclone has no remote named {self.remote!r}: run `rclone config` to add it")

    def staging(self, slot: Slot) -> Path:
        return self.staging_dir / str(slot.rid) / f"{slot.n}.part"

    def stat(self, key: str) -> dict[str, Any] | None:
        got = self.run("lsjson", "--no-modtime", "--no-mimetype", self.target(key))
        if got.returncode != 0:
            return None
        try:
            rows = json.loads(got.stdout or b"[]")
        except ValueError:
            return None
        return rows[0] if rows and not rows[0].get("IsDir") else None

    def used_bytes(self) -> int:
        got = self.run("size", "--json", self.target("uploads"))
        if got.returncode == NOT_FOUND:
            return 0
        try:
            return int(json.loads(got.stdout or b"{}")["bytes"]) if got.returncode == 0 else self._size_failed(got)
        except (ValueError, KeyError, TypeError):
            raise StorageError("rclone size gave an answer the board cannot read") from None

    def _size_failed(self, got: subprocess.CompletedProcess[bytes]) -> int:
        raise StorageError("rclone could not measure the attachments folder: " + got.stderr.decode(errors="replace").strip()[:200])

    def settle(self, slot: Slot, report: Any) -> str:
        have = self.have(slot)
        if have != slot.size:
            self.discard(slot)
            return "cut" if have < slot.size else "size"
        try:
            copied = self.run("copyto", str(self.staging(slot)), self.target(slot.key), timeout=COPY_SECONDS)
        except StorageError:
            return "store"
        if copied.returncode != 0:
            return "store"
        self.discard(slot)
        found = self.stat(slot.key)
        if not found:
            return "store"
        return "" if found.get("Size") == slot.size else "size"

    def locator(self, key: str) -> str:
        return f"drive://{self.target(key).replace(':', '/', 1)}"

    def view(self, key: str) -> View | None:
        found = self.stat(key)
        if not found:
            return None
        return View("api/uploads/file?key=" + key, mimetypes.guess_type(key)[0] or "application/octet-stream")

    def open(self, key: str, rng: str) -> Opened | None:
        found = self.stat(key)
        if not found:
            return None
        size = int(found.get("Size", 0))
        start, end = 0, size - 1
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", rng)
        if match and size:
            if match.group(1):
                start = int(match.group(1))
                end = min(int(match.group(2)) if match.group(2) else end, size - 1)
            else:
                start = max(0, size - int(match.group(2) or 0))
            if start > end:
                raise StorageError("range not satisfiable")
        partial = bool(match and size)
        count = end - start + 1 if size else 0
        ctype = mimetypes.guess_type(key)[0] or "application/octet-stream"
        proc = subprocess.Popen(self.argv("cat", "--offset", str(start), "--count", str(count), self.target(key)),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                **procs.detached())

        out = proc.stdout

        def chunks() -> Iterator[bytes]:
            while out is not None:
                data = out.read(READ)
                if not data:
                    return
                yield data

        def close() -> None:
            if out is not None:
                out.close()
            if proc.poll() is None:
                procs.terminate(proc.pid)
            proc.wait()

        return Opened(206 if partial else 200, ctype, count, f"bytes {start}-{end}/{size}" if partial else "",
                      chunks(), close)
