import calendar
import json
import math
import unittest
from typing import Any
from urllib.parse import parse_qs, urlsplit

from agent_hub import uploads
from agent_hub.storage.s3 import MULTIPART_OVER, PART_SIZE, S3Storage
from agent_hub.storage.s3sign import S3, Creds
from agent_hub.store import Store
from tests.helpers import Clock, make_home

MB = 1024 * 1024


class FakeS3(S3):
    def __init__(self) -> None:
        super().__init__("example-bucket", "eu-west-1", creds=lambda: Creds("K", "SECRETVALUE"))
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.objects: dict[str, int] = {}

    def call(self, method: str, key: str, query: dict[str, str] | None = None, body: bytes = b"",
             headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        q = query or {}
        self.calls.append((method, key, q))
        if method == "POST" and "uploads" in q:
            return 200, {}, b"<InitiateMultipartUploadResult><UploadId>UP1</UploadId></InitiateMultipartUploadResult>"
        if method == "POST" and "uploadId" in q:
            return 200, {}, b"<CompleteMultipartUploadResult/>"
        if method == "HEAD":
            n = self.objects.get(key)
            return (200, {"content-length": str(n), "content-type": "image/png"}, b"") if n is not None else (404, {}, b"")
        return 204, {}, b""


class S3FlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home(uploads={"provider": "s3", "bucket": "example-bucket", "region": "eu-west-1", "prefix": "hub/", "max_mb": 1000})
        self.clock = Clock(calendar.timegm((2026, 11, 16, 3, 0, 0)))
        self.s = Store(self.cfg.db, clock=self.clock)
        self.fake = FakeS3()
        k = (json.dumps(self.cfg.uploads, sort_keys=True), str(self.cfg.home))
        uploads._providers[k] = S3Storage("example-bucket", "eu-west-1", "hub/", self.fake)

    def tearDown(self) -> None:
        uploads._providers.clear()

    def files(self, big: int = 0) -> list[dict[str, Any]]:
        return [{"name": "a.png", "type": "image/png", "size": 3 * MB}] + (
            [{"name": "clip.mov", "type": "video/quicktime", "size": big}] if big else [])

    def test_small_file_gets_a_presigned_put_with_type_and_size_signed(self) -> None:
        out = uploads.start(self.cfg, self.s, "ana", "hub", "see", self.files())
        [one] = out["files"]
        key = f"hub/uploads/2026-11-16/{out['id']}/1-a.png"
        self.assertEqual((one["mode"], one["headers"]), ("put", {"content-type": "image/png"}))
        url = urlsplit(one["url"])
        self.assertEqual((url.netloc, url.path), ("example-bucket.s3.eu-west-1.amazonaws.com", "/" + key))
        q = parse_qs(url.query)
        self.assertEqual(q["X-Amz-SignedHeaders"], ["content-length;content-type;host"])
        self.assertEqual(q["X-Amz-Expires"], ["3600"])
        self.assertNotIn("SECRETVALUE", one["url"])
        self.assertEqual(self.s.pending("hub"), [])

    def test_finish_checks_the_size_and_names_the_s3_path(self) -> None:
        out = uploads.start(self.cfg, self.s, "ana", "hub", "see", self.files())
        key = f"hub/uploads/2026-11-16/{out['id']}/1-a.png"
        self.fake.objects[key] = 3 * MB
        r = uploads.finish(self.cfg, self.s, out["id"], "ana")
        self.assertEqual((r["sent"], r["failed"]), (1, 0))
        text = self.s.pending("hub")[0]["text"]
        self.assertIn(f"- s3://example-bucket/{key} · image/png · 3.0 MB (original name: a.png)", text)
        self.assertIn("stored in S3", text)

    def test_a_wrong_size_or_a_missing_object_fails(self) -> None:
        out = uploads.start(self.cfg, self.s, "ana", "hub", "", self.files())
        self.fake.objects[f"hub/uploads/2026-11-16/{out['id']}/1-a.png"] = 1
        uploads.finish(self.cfg, self.s, out["id"], "ana")
        self.assertIn("a.png · image/png · 3.0 MB: size differs", self.s.pending("hub")[0]["text"])
        out = uploads.start(self.cfg, self.s, "ana", "hub", "", self.files())
        uploads.finish(self.cfg, self.s, out["id"], "ana")
        self.assertIn("upload did not finish", self.s.pending("hub")[1]["text"])

    def test_a_big_file_is_multipart_with_part_urls_and_etags(self) -> None:
        big = MULTIPART_OVER + 40 * MB
        out = uploads.start(self.cfg, self.s, "ana", "hub", "see", self.files(big))
        two = out["files"][1]
        parts = math.ceil(big / PART_SIZE)
        self.assertEqual((two["mode"], two["part_size"], two["parts"]), ("multipart", PART_SIZE, parts))
        self.assertIn(("POST", f"hub/uploads/2026-11-16/{out['id']}/2-clip.mov", {"uploads": ""}), self.fake.calls)
        urls = uploads.part_urls(self.cfg, self.s, out["id"], "ana", 2, [1, parts])
        q = parse_qs(urlsplit(urls[str(parts)]).query)
        self.assertEqual((q["partNumber"], q["uploadId"]), ([str(parts)], ["UP1"]))
        self.assertIn("content-length", q["X-Amz-SignedHeaders"][0])
        for bad in ([0], [parts + 1], [True], [], list(range(1, 102))):
            with self.assertRaises(uploads.UploadError):
                uploads.part_urls(self.cfg, self.s, out["id"], "ana", 2, bad)
        with self.assertRaises(uploads.UploadError):
            uploads.part_urls(self.cfg, self.s, out["id"], "ana", 1, [1])
        with self.assertRaises(uploads.UploadError):
            uploads.part_urls(self.cfg, self.s, out["id"], "bo", 2, [1])
        self.fake.objects[f"hub/uploads/2026-11-16/{out['id']}/1-a.png"] = 3 * MB
        self.fake.objects[f"hub/uploads/2026-11-16/{out['id']}/2-clip.mov"] = big
        r = uploads.finish(self.cfg, self.s, out["id"], "ana", [{"n": 2, "etags": [f'"e{i}"' for i in range(parts)]}])
        self.assertEqual((r["sent"], r["failed"]), (2, 0))
        done = next(c for c in self.fake.calls if c[0] == "POST" and "uploadId" in c[2])
        self.assertEqual(done[2], {"uploadId": "UP1"})

    def test_missing_etags_abort_the_multipart_upload(self) -> None:
        out = uploads.start(self.cfg, self.s, "ana", "hub", "", self.files(MULTIPART_OVER + MB)[1:])
        r = uploads.finish(self.cfg, self.s, out["id"], "ana", [{"n": 1}])
        self.assertEqual((r["sent"], r["failed"]), (0, 1))
        self.assertIn("DELETE", [c[0] for c in self.fake.calls])

    def test_view_goes_through_the_board_not_a_signed_address(self) -> None:
        out = uploads.start(self.cfg, self.s, "ana", "hub", "", self.files())
        key = f"uploads/2026-11-16/{out['id']}/1-a.png"
        self.fake.objects["hub/" + key] = 3 * MB
        v = uploads.provider(self.cfg).view(key)
        self.assertEqual((v.url, v.type), ("api/uploads/file?key=" + key, "image/png"))
        self.assertIsNone(uploads.provider(self.cfg).view("uploads/2026-11-16/99/1-none.png"))

    def test_the_page_may_connect_only_to_the_bucket_host(self) -> None:
        from agent_hub.web import page_csp
        self.assertIn("connect-src 'self' https://example-bucket.s3.eu-west-1.amazonaws.com;", page_csp(self.cfg))
        self.assertIn("connect-src 'self';", page_csp(make_home()))

    def test_missing_credentials_are_reported_before_anything_is_held(self) -> None:
        def none() -> Creds:
            from agent_hub.storage.s3sign import S3Error
            raise S3Error("no AWS credentials: set AWS_ACCESS_KEY_ID")
        self.fake._fetch = none
        with self.assertRaises(uploads.UploadError) as got:
            uploads.start(self.cfg, self.s, "ana", "hub", "x", self.files())
        self.assertIn("no AWS credentials", str(got.exception))
        self.assertEqual(self.s.pending("hold:hub"), [])


if __name__ == "__main__":
    unittest.main()
