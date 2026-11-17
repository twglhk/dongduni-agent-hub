import calendar
import json
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_hub import config as config_mod
from agent_hub import uploads
from agent_hub.storage import parse
from agent_hub.store import Store
from tests.helpers import Clock, make_home

MB = 1024 * 1024
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100


class SettingsTest(unittest.TestCase):
    def test_default_is_local_with_caps(self) -> None:
        self.assertEqual(parse(None), {"provider": "local", "max_mb": 25, "max_files": 10, "max_total_mb": 2048})
        self.assertEqual(make_home().uploads["provider"], "local")

    def test_off_and_the_other_providers(self) -> None:
        self.assertEqual(parse({"provider": "off"}), {"provider": "off"})
        self.assertEqual(parse({"provider": "drive", "remote": "mydrive", "root": "hub/files"})["root"], "hub/files")
        s3 = parse({"provider": "s3", "bucket": "example-bucket", "region": "eu-west-1", "prefix": "hub/"})
        self.assertEqual((s3["bucket"], s3["prefix"], s3["max_mb"]), ("example-bucket", "hub/", 25))

    def test_bad_values_are_refused(self) -> None:
        for bad in ("local", [], {"provider": "ftp"}, {"max_mb": 0}, {"max_mb": True}, {"max_files": 51}, {"max_mb": "9"},
                    {"remote": "x"}, {"provider": "off", "max_mb": 5},
                    {"provider": "drive"}, {"provider": "drive", "remote": "a:b"}, {"provider": "drive", "remote": "d", "root": "../x"},
                    {"provider": "drive", "remote": "d", "root": "/abs"}, {"provider": "drive", "remote": "d", "root": "a/./b"},
                    {"provider": "s3", "bucket": "b", "region": "eu-west-1"},
                    {"provider": "s3", "bucket": "Bad_Bucket", "region": "eu-west-1"},
                    {"provider": "s3", "bucket": "okay-bucket", "region": "nowhere"},
                    {"provider": "s3", "bucket": "okay-bucket", "region": "eu-west-1", "prefix": "../x/"},
                    {"provider": "s3", "bucket": "okay-bucket", "region": "eu-west-1", "access_key": "AKIA"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                parse(bad)

    def test_config_load_reports_a_bad_value(self) -> None:
        with self.assertRaises(config_mod.ConfigError):
            make_home(uploads={"provider": "drive"})


class LocalFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home(lang="ko")
        self.clock = Clock(calendar.timegm((2026, 11, 16, 3, 0, 0)))
        self.s = Store(self.cfg.db, clock=self.clock)

    def tearDown(self) -> None:
        uploads._providers.clear()

    def files(self) -> list[dict[str, Any]]:
        return [{"name": "화면 IMG_1.PNG", "type": "image/png", "size": len(PNG)},
                {"name": "notes.md", "type": "", "size": 5}]

    def send(self, rid: int, n: int, data: bytes, who: str = "ana") -> None:
        uploads.chunk(self.cfg, self.s, rid, who, n, 0, data)

    def test_request_is_held_until_finish(self) -> None:
        out = uploads.start(self.cfg, self.s, "ana", "hub", "사진 봐 주세요", self.files())
        rid = out["id"]
        self.assertEqual(self.s.pending("hub"), [])
        self.assertEqual([(f["mode"], f["chunk"]) for f in out["files"]], [("chunks", 4 * MB)] * 2)
        self.send(rid, 1, PNG)
        self.send(rid, 2, b"hello")
        with self.assertRaises(uploads.UploadError):
            uploads.finish(self.cfg, self.s, rid, "bo")
        r = uploads.finish(self.cfg, self.s, rid, "ana")
        self.assertEqual((r["sent"], r["failed"]), (2, 0))
        [msg] = self.s.pending("hub")
        p1, p2 = f"uploads/2026-11-16/{rid}/1-IMG_1.png", f"uploads/2026-11-16/{rid}/2-notes.md"
        self.assertTrue(msg["text"].startswith("사진 봐 주세요\n\n첨부 2개 (작업 폴더"), msg["text"])
        self.assertIn(f"- {p1} · image/png · 1 KB (원래 이름: 화면 IMG_1.PNG)", msg["text"])
        self.assertIn(f"- {p2} · text/markdown · 1 KB (원래 이름: notes.md)", msg["text"])
        self.assertEqual((self.cfg.workdir / p1).read_bytes(), PNG)
        self.assertEqual(list((self.cfg.workdir / "uploads").rglob("*.part")), [])
        with self.assertRaises(uploads.UploadError):
            uploads.finish(self.cfg, self.s, rid, "ana")

    def test_the_day_folder_keeps_itself_out_of_git(self) -> None:
        uploads.start(self.cfg, self.s, "ana", "hub", "x", self.files()[1:])
        self.assertEqual((self.cfg.workdir / "uploads" / "2026-11-16" / ".gitignore").read_text(), "*\n")
        self.assertFalse((self.cfg.workdir / "uploads" / ".gitignore").exists())

    def test_english_text_and_attachments_only(self) -> None:
        cfg = make_home()
        s = Store(cfg.db, clock=self.clock)
        out = uploads.start(cfg, s, "ana", "hub", "", self.files()[1:])
        uploads.chunk(cfg, s, out["id"], "ana", 1, 0, b"hello")
        uploads.finish(cfg, s, out["id"], "ana")
        text = s.pending("hub")[0]["text"]
        self.assertTrue(text.startswith("(attachments only)\n\n1 attachment(s) (saved in the workspace under "), text)
        self.assertIn("· text/markdown · 1 KB (original name: notes.md)", text)

    def test_chunks_must_arrive_in_order_and_not_overflow(self) -> None:
        rid = uploads.start(self.cfg, self.s, "ana", "hub", "x", self.files()[:1])["id"]
        uploads.chunk(self.cfg, self.s, rid, "ana", 1, 0, PNG[:40])
        with self.assertRaises(uploads.Conflict) as got:
            uploads.chunk(self.cfg, self.s, rid, "ana", 1, 0, PNG[:40])
        self.assertEqual(got.exception.received, 40)
        self.assertEqual(uploads.chunk(self.cfg, self.s, rid, "ana", 1, 0, b""), {"received": 40})
        with self.assertRaises(uploads.UploadError):
            uploads.chunk(self.cfg, self.s, rid, "ana", 1, 40, PNG)
        uploads.chunk(self.cfg, self.s, rid, "ana", 1, 40, PNG[40:])
        with self.assertRaises(uploads.UploadError):
            uploads.chunk(self.cfg, self.s, rid, "bo", 1, 0, b"x")
        with self.assertRaises(uploads.UploadError):
            uploads.chunk(self.cfg, self.s, rid, "ana", 9, 0, b"x")
        with self.assertRaises(uploads.UploadError):
            uploads.chunk(self.cfg, self.s, rid, "ana", 1, -1, b"x")

    def test_a_short_file_is_a_failure_in_the_request(self) -> None:
        rid = uploads.start(self.cfg, self.s, "ana", "hub", "", self.files())["id"]
        self.send(rid, 1, PNG[:10])
        self.send(rid, 2, b"hello")
        r = uploads.finish(self.cfg, self.s, rid, "ana")
        self.assertEqual((r["sent"], r["failed"], r["total"]), (1, 1, 2))
        text = self.s.pending("hub")[0]["text"]
        self.assertIn("첨부 실패 1개 / 2개:", text)
        self.assertIn("화면 IMG_1.PNG · image/png · 1 KB: 올리기가 끝나지 않음", text)
        self.assertEqual(list((self.cfg.workdir / "uploads").rglob("1-*")), [])

    def test_sweep_sends_an_abandoned_request_with_what_arrived(self) -> None:
        rid = uploads.start(self.cfg, self.s, "ana", "hub-ana", "x", self.files())["id"]
        self.send(rid, 2, b"hello")
        self.assertEqual(uploads.sweep(self.cfg, self.s), [])
        self.clock.tick(uploads.HOLD_MAX - 10)
        self.send(rid, 1, PNG[:10])
        self.clock.tick(uploads.HOLD_MAX - 10)
        self.assertEqual(uploads.sweep(self.cfg, self.s), [], "a chunk that just came keeps the request waiting")
        self.clock.tick(20)
        self.assertEqual(uploads.sweep(self.cfg, self.s), [rid])
        text = self.s.pending("hub-ana")[0]["text"]
        self.assertIn("notes.md", text)
        self.assertIn("올리기가 끝나지 않음", text)
        self.assertEqual(uploads.sweep(self.cfg, self.s), [])

    def test_sweep_sends_the_words_when_attachments_were_turned_off(self) -> None:
        rid = uploads.start(self.cfg, self.s, "ana", "hub", "글", self.files()[:1])["id"]
        self.clock.tick(uploads.HOLD_MAX + 1)
        later = replace(self.cfg, uploads={"provider": "off"})
        self.assertEqual(uploads.sweep(later, self.s), [rid])
        self.assertEqual(self.s.pending("hub")[0]["text"], "글")

    def test_limits_and_types(self) -> None:
        cfg = make_home(uploads={"max_mb": 1, "max_files": 2})
        s = Store(cfg.db, clock=self.clock)
        too_many = [{"name": "a.png", "type": "image/png", "size": 1}] * 3
        for bad in ([], "x", [{"name": "a.exe", "type": "application/x-msdownload", "size": 1}],
                    [{"name": "a.html", "type": "text/html", "size": 1}], [{"name": "a.svg", "type": "image/svg+xml", "size": 1}],
                    [{"name": "a", "type": "", "size": 1}],
                    [{"name": "a.png", "type": "image/png", "size": 1 * MB + 1}],
                    [{"name": "a.png", "type": "image/png", "size": 0}], [{"name": "a.png", "type": "image/png", "size": True}],
                    [{"name": "a.png", "type": "image/png"}], [None], too_many):
            with self.assertRaises(uploads.UploadError, msg=str(bad)):
                uploads.start(cfg, s, "ana", "hub", "x", bad)
        self.assertEqual(s.pending("hub"), [])
        self.assertEqual(s.pending("hold:hub"), [])
        uploads.start(cfg, s, "ana", "hub", "x", [{"name": "a.png", "type": "image/png", "size": 1 * MB}])

    def test_the_folder_total_is_capped(self) -> None:
        cfg = make_home(uploads={"max_total_mb": 1})
        s = Store(cfg.db, clock=self.clock)
        rid = uploads.start(cfg, s, "ana", "hub", "x", [{"name": "a.png", "type": "image/png", "size": 900 * 1024}])["id"]
        uploads.chunk(cfg, s, rid, "ana", 1, 0, b"x" * 900 * 1024)
        with self.assertRaises(uploads.UploadError) as got:
            uploads.start(cfg, s, "ana", "hub", "x", [{"name": "b.png", "type": "image/png", "size": 200 * 1024}])
        self.assertIn("full", str(got.exception))

    def test_the_server_picks_name_and_extension(self) -> None:
        names = [("../../etc/passwd.png", "image/png", "passwd", "png"), ("C:\\x\\evil.PNG", "image/png", "evil", "png"),
                 ("shell.html", "image/png", "shell", "png"), (".hidden", "text/plain", "file", "txt"),
                 ("a b;c$d.txt", "", "a-b-c-d", "txt"), ("日本語.pdf", "application/pdf", "file", "pdf"),
                 ("x" * 150 + ".jpeg", "", "x" * 48, "jpg")]
        for name, ctype, stem, ext in names:
            out = uploads.start(self.cfg, self.s, "ana", "hub", "x", [{"name": name, "type": ctype, "size": 1}])
            plan = json.loads(uploads._plan_path(self.cfg, out["id"]).read_text(encoding="utf-8"))
            self.assertEqual(plan["files"][0]["key"], f"uploads/2026-11-16/{out['id']}/1-{stem}.{ext}", name)

    def test_resolve_takes_only_the_canonical_tail(self) -> None:
        ok = "uploads/2026-11-16/5/1-a.png"
        for key in (ok, "s3://bk/pre/" + ok, "drive://d/root/" + ok):
            self.assertEqual(uploads.resolve(key), ok)
        for bad in ("uploads/../backups/x.png", "x/../" + ok, "uploads/2026-11-16/5/1-a.png.part", "uploads/2026-11-16/5/1-a.html",
                    "xuploads/2026-11-16/5/1-a.png", "/etc/passwd", "uploads/2026-11-16/5/../1-a.png", ""):
            with self.assertRaises(uploads.UploadError, msg=bad):
                uploads.resolve(bad)

    def test_off(self) -> None:
        cfg = make_home(uploads={"provider": "off"})
        self.assertIsNone(uploads.info(cfg))
        with self.assertRaises(uploads.UploadError):
            uploads.start(cfg, Store(cfg.db), "ana", "hub", "x", self.files())


class LinkTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home()
        self.s = Store(self.cfg.db, clock=Clock(calendar.timegm((2026, 11, 16, 3, 0, 0))))

    def tearDown(self) -> None:
        uploads._providers.clear()

    def test_a_link_in_the_path_is_refused(self) -> None:
        elsewhere = Path(self.cfg.home) / "elsewhere"
        elsewhere.mkdir()
        try:
            (self.cfg.workdir / "uploads").symlink_to(elsewhere, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks here")
        with self.assertRaises(uploads.UploadError):
            rid = uploads.start(self.cfg, self.s, "ana", "hub", "x", [{"name": "a.png", "type": "image/png", "size": 3}])["id"]
            uploads.chunk(self.cfg, self.s, rid, "ana", 1, 0, b"abc")
        self.assertEqual(list(elsewhere.rglob("*")), [])


if __name__ == "__main__":
    unittest.main()
