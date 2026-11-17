from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import CHUNK, OutOfOrder, Opened, Receiver, Slot, Storage, StorageError, View
from .settings import PROVIDERS, parse

__all__ = ["CHUNK", "OutOfOrder", "Opened", "PROVIDERS", "Receiver", "Slot", "Storage", "StorageError", "View", "make",
           "parse"]


def make(settings: dict[str, Any], home: Path, workdir: Path) -> Storage:
    kind = settings.get("provider")
    if kind == "local":
        from .local import Local
        return Local(workdir)
    if kind == "drive":
        from .drive import Drive
        return Drive(settings["remote"], settings["root"], home / ".tower" / "state" / "uploads" / "staging",
                     os.environ.get("TOWER_RCLONE_BIN", "rclone"))
    if kind == "s3":
        from .s3 import S3Storage
        return S3Storage(settings["bucket"], settings["region"], settings["prefix"])
    raise StorageError("attachments are off")
