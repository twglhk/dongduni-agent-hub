from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from .base import Receiver, Slot, StorageError, View

PART = ".part"


class Local(Receiver):
    name = "local"

    def __init__(self, root: Path):
        super().__init__()
        self.root = root

    def check(self) -> None:
        if not self.root.is_dir():
            raise StorageError("the workspace folder is not there")

    def path(self, key: str) -> Path:
        parts = key.split("/")
        base = self.root.resolve()
        cur = base
        for part in parts:
            if part in ("", ".", "..") or "\\" in part:
                raise StorageError("not a stored attachment path")
            cur = cur / part
            if cur.is_symlink():
                raise StorageError("attachments are never kept behind a link")
        if not cur.resolve().is_relative_to(base):
            raise StorageError("not inside the workspace")
        return cur

    def prepare(self, slot: Slot) -> Slot:
        folder = self.path(slot.key).parent
        folder.mkdir(parents=True, exist_ok=True)
        marker = folder.parent / ".gitignore"
        if not marker.exists():
            marker.write_text("*\n", encoding="utf-8")
        return slot

    def staging(self, slot: Slot) -> Path:
        full = self.path(slot.key)
        return full.with_name(full.name + PART)

    def settle(self, slot: Slot, report: object) -> str:
        have = self.have(slot)
        if have != slot.size:
            self.discard(slot)
            return "cut" if have < slot.size else "size"
        try:
            os.replace(self.staging(slot), self.path(slot.key))
        except OSError:
            return "store"
        return ""

    def locator(self, key: str) -> str:
        return key

    def view(self, key: str) -> View | None:
        try:
            full = self.path(key)
        except StorageError:
            return None
        if not full.is_file():
            return None
        return View("f/" + key, mimetypes.guess_type(full.name)[0] or "application/octet-stream")

    def used_bytes(self) -> int:
        top = self.root / "uploads"
        total = 0
        for folder, _, names in os.walk(top):
            for name in names:
                try:
                    total += (Path(folder) / name).stat().st_size
                except OSError:
                    continue
        return total
