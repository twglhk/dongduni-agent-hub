from __future__ import annotations

import math
import re
from dataclasses import replace
from typing import Any, Iterator
from urllib.parse import quote
from xml.sax.saxutils import escape

from .base import Opened, Slot, Storage, StorageError, View
from .s3sign import S3, S3Error

URL_SECONDS = 3600
MULTIPART_OVER = 100 * 1024 * 1024
PART_SIZE = 16 * 1024 * 1024
MAX_PARTS = 10000
PARTS_AT_A_TIME = 100
READ = 1 << 16


def part_size(size: int) -> int:
    return max(PART_SIZE, math.ceil(size / MAX_PARTS))


def part_length(slot: Slot, n: int) -> int:
    return min(slot.part_size, slot.size - (n - 1) * slot.part_size)


def _read(r: Any) -> Iterator[bytes]:
    while True:
        chunk = r.read(READ)
        if not chunk:
            return
        yield chunk


class S3Storage(Storage):
    name = "s3"
    direct = True

    def __init__(self, bucket: str, region: str, prefix: str = "", client: S3 | None = None):
        self.bucket, self.prefix = bucket, prefix
        self.s3 = client or S3(bucket, region)

    def object_key(self, key: str) -> str:
        return self.prefix + key

    def check(self) -> None:
        try:
            self.s3.creds()
        except S3Error as e:
            raise StorageError(str(e)) from None

    def prepare(self, slot: Slot) -> Slot:
        if slot.size <= MULTIPART_OVER:
            return slot
        code, _, body = self.s3.call("POST", self.object_key(slot.key), {"uploads": ""},
                                     headers={"content-type": slot.type})
        found = re.search(rb"<UploadId>([^<]+)</UploadId>", body)
        if code != 200 or not found:
            raise StorageError(f"S3 would not start a multipart upload ({code})")
        return replace(slot, upload_id=found.group(1).decode(), part_size=part_size(slot.size))

    def begin(self, slot: Slot) -> dict[str, Any]:
        if slot.upload_id:
            return {"mode": "multipart", "part_size": slot.part_size, "parts": math.ceil(slot.size / slot.part_size)}
        url = self.s3.presign("PUT", self.object_key(slot.key), URL_SECONDS,
                              headers={"content-type": slot.type, "content-length": str(slot.size)})
        return {"mode": "put", "url": url, "headers": {"content-type": slot.type}}

    def more(self, slot: Slot, numbers: list[int]) -> dict[str, str]:
        if not slot.upload_id:
            raise StorageError(f"file {slot.n} is not a multipart upload")
        total = math.ceil(slot.size / slot.part_size)
        if not 0 < len(numbers) <= PARTS_AT_A_TIME:
            raise StorageError(f"ask for 1 to {PARTS_AT_A_TIME} parts at a time")
        urls = {}
        for p in numbers:
            if isinstance(p, bool) or not isinstance(p, int) or not 1 <= p <= total:
                raise StorageError(f"no part {p}")
            urls[str(p)] = self.s3.presign("PUT", self.object_key(slot.key), URL_SECONDS,
                                           {"partNumber": str(p), "uploadId": slot.upload_id},
                                           headers={"content-length": str(part_length(slot, p))})
        return urls

    def _complete(self, slot: Slot, etags: Any) -> str:
        total = math.ceil(slot.size / slot.part_size)
        if not isinstance(etags, list) or len(etags) != total or not all(isinstance(e, str) and e for e in etags):
            return "cut"
        xml = "<CompleteMultipartUpload>" + "".join(
            f"<Part><PartNumber>{i}</PartNumber><ETag>{escape(e)}</ETag></Part>" for i, e in enumerate(etags, 1)
        ) + "</CompleteMultipartUpload>"
        code, _, body = self.s3.call("POST", self.object_key(slot.key), {"uploadId": slot.upload_id}, xml.encode(),
                                     {"content-type": "application/xml"})
        return "" if code == 200 and b"<Error>" not in body else "cut"

    def settle(self, slot: Slot, report: Any) -> str:
        if slot.upload_id:
            why = self._complete(slot, (report or {}).get("etags") if isinstance(report, dict) else None)
            if why:
                self.discard(slot)
                return why
        try:
            code, hs, _ = self.s3.call("HEAD", self.object_key(slot.key))
        except S3Error:
            return "cut"
        if code != 200:
            return "cut"
        return "" if hs.get("content-length") == str(slot.size) else "size"

    def discard(self, slot: Slot) -> None:
        if not slot.upload_id:
            return
        try:
            self.s3.call("DELETE", self.object_key(slot.key), {"uploadId": slot.upload_id})
        except S3Error:
            return

    def locator(self, key: str) -> str:
        return f"s3://{self.bucket}/{self.object_key(key)}"

    def view(self, key: str) -> View | None:
        try:
            code, hs, _ = self.s3.call("HEAD", self.object_key(key))
        except S3Error:
            return None
        if code != 200:
            return None
        ctype = hs.get("content-type", "application/octet-stream")
        url = "api/uploads/file?key=" + quote(key, safe="/")
        return View(url, ctype)

    def open(self, key: str, rng: str) -> Opened | None:
        try:
            r = self.s3.open(self.object_key(key), rng)
        except S3Error as e:
            raise StorageError(str(e)) from None
        length = r.headers.get("content-length")
        return Opened(r.status, r.headers.get("content-type", "application/octet-stream"),
                      int(length) if length else None, r.headers.get("content-range", ""), _read(r), r.close)

    def connect_host(self) -> str:
        return self.s3.host
