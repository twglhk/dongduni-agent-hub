from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

CHUNK = 4 * 1024 * 1024


class StorageError(Exception):
    pass


class OutOfOrder(StorageError):
    def __init__(self, received: int):
        super().__init__(f"the server has {received} bytes of this file: continue from there")
        self.received = received


@dataclass(frozen=True)
class Slot:
    rid: int
    n: int
    key: str
    type: str
    size: int
    upload_id: str = ""
    part_size: int = 0


@dataclass(frozen=True)
class View:
    url: str
    type: str
    thumb: str = ""


@dataclass
class Opened:
    status: int
    type: str
    length: int | None
    content_range: str
    chunks: Iterator[bytes]
    close: Any = None


class Storage(ABC):
    name = ""
    direct = False

    @abstractmethod
    def check(self) -> None:
        ...

    @abstractmethod
    def begin(self, slot: Slot) -> dict[str, Any]:
        ...

    def prepare(self, slot: Slot) -> Slot:
        return slot

    def more(self, slot: Slot, numbers: list[int]) -> dict[str, str]:
        raise StorageError("this provider takes no part uploads")

    def receive(self, slot: Slot, offset: int, data: bytes) -> int:
        raise StorageError("this provider does not take bytes through the board")

    @abstractmethod
    def settle(self, slot: Slot, report: Any) -> str:
        ...

    @abstractmethod
    def discard(self, slot: Slot) -> None:
        ...

    @abstractmethod
    def locator(self, key: str) -> str:
        ...

    @abstractmethod
    def view(self, key: str) -> View | None:
        ...

    def open(self, key: str, rng: str) -> Opened | None:
        raise StorageError("this provider is shown by its own address")

    live_usage = False

    def have(self, slot: Slot) -> int:
        return 0

    def used_bytes(self) -> int:
        return 0

    def connect_host(self) -> str:
        return ""


class Receiver(Storage):
    def __init__(self) -> None:
        self._lock = threading.Lock()

    @abstractmethod
    def staging(self, slot: Slot) -> Path:
        ...

    def begin(self, slot: Slot) -> dict[str, Any]:
        return {"mode": "chunks", "chunk": CHUNK}

    def have(self, slot: Slot) -> int:
        try:
            return self.staging(slot).stat().st_size
        except OSError:
            return 0

    def receive(self, slot: Slot, offset: int, data: bytes) -> int:
        with self._lock:
            have = self.have(slot)
            if not data:
                return have
            if offset != have:
                raise OutOfOrder(have)
            if have + len(data) > slot.size:
                raise StorageError("more bytes than the file was announced with")
            path = self.staging(slot)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as f:
                f.write(data)
            return have + len(data)

    def discard(self, slot: Slot) -> None:
        self.staging(slot).unlink(missing_ok=True)
