import calendar
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_hub import uploads
from agent_hub.storage import StorageError
from agent_hub.storage.drive import Drive
from agent_hub.store import Store
from tests.helpers import Clock, make_home

FAKE_RCLONE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

store = Path(os.environ["FAKE_RCLONE_STORE"])
log = Path(os.environ["FAKE_RCLONE_STORE"] + ".log")
args = sys.argv[1:]
log.open("a").write(json.dumps(args) + "\n")

def local(spec):
    remote, _, rest = spec.partition(":")
    return store / remote / rest

if os.environ.get("FAKE_RCLONE_FAIL") == args[0]:
    sys.stderr.write("boom")
    sys.exit(1)
if args[0] == "listremotes":
    print("mydrive:")
elif args[0] == "copyto":
    dst = local(args[2]); dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(Path(args[1]).read_bytes())
elif args[0] == "lsjson":
    p = local(args[-1])
    print(json.dumps([{"Name": p.name, "Size": p.stat().st_size, "IsDir": False}] if p.is_file() else []))
elif args[0] == "size":
    root = local(args[-1])
    files = [f for f in root.rglob("*") if f.is_file()] if root.is_dir() else []
    print(json.dumps({"count": len(files), "bytes": sum(f.stat().st_size for f in files)}))
elif args[0] == "cat":
    a = dict(zip(args[1:-1:2], args[2:-1:2]))
    data = local(args[-1]).read_bytes()[int(a["--offset"]):][:int(a["--count"])]
    sys.stdout.buffer.write(data)
'''


@unittest.skipIf(os.name == "nt", "the stand-in rclone is a POSIX script")
class DriveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="rclone-test-"))
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        script = bin_dir / "rclone"
        script.write_text(FAKE_RCLONE.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        self.env = mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                                                "FAKE_RCLONE_STORE": str(self.dir / "remote")})
        self.env.start()
        self.cfg = make_home(uploads={"provider": "drive", "remote": "mydrive", "root": "hub/files"})
        self.clock = Clock(calendar.timegm((2026, 11, 16, 3, 0, 0)))
        self.s = Store(self.cfg.db, clock=self.clock)

    def tearDown(self) -> None:
        self.env.stop()
        uploads._providers.clear()

    def calls(self) -> list[list[str]]:
        return [json.loads(x) for x in Path(str(self.dir / "remote") + ".log").read_text().splitlines()]

    def put(self, data: bytes = b"0123456789") -> tuple[int, str]:
        out = uploads.start(self.cfg, self.s, "ana", "hub", "see", [{"name": "clip.mp3", "type": "audio/mpeg", "size": len(data)}])
        self.assertEqual(out["files"][0]["mode"], "chunks")
        uploads.chunk(self.cfg, self.s, out["id"], "ana", 1, 0, data)
        return out["id"], f"uploads/2026-11-16/{out['id']}/1-clip.mp3"

    def test_the_file_goes_to_the_remote_and_the_request_names_it(self) -> None:
        rid, key = self.put()
        self.assertEqual(self.s.pending("hub"), [])
        r = uploads.finish(self.cfg, self.s, rid, "ana")
        self.assertEqual((r["sent"], r["failed"]), (1, 0))
        self.assertEqual((self.dir / "remote" / "mydrive" / "hub" / "files" / key).read_bytes(), b"0123456789")
        text = self.s.pending("hub")[0]["text"]
        self.assertIn(f"- drive://mydrive/hub/files/{key} · audio/mpeg · 1 KB (original name: clip.mp3)", text)
        self.assertIn("stored on an rclone remote", text)
        self.assertEqual(list((self.cfg.home / ".tower" / "state" / "uploads" / "staging").rglob("*.part")), [])
        self.assertTrue(all(c[0] in ("listremotes", "copyto", "lsjson", "size") for c in self.calls()))

    def test_the_folder_total_is_capped_for_the_drive_too(self) -> None:
        cfg = make_home(uploads={"provider": "drive", "remote": "mydrive", "max_total_mb": 1})
        s = Store(cfg.db, clock=self.clock)
        big = [{"name": "a.mp3", "type": "audio/mpeg", "size": 700 * 1024}]
        uploads.start(cfg, s, "ana", "hub", "x", big)
        with self.assertRaises(uploads.UploadError) as got:
            uploads.start(cfg, s, "ana", "hub", "x", big)
        self.assertIn("full", str(got.exception))

    def test_rclone_gets_a_plain_argument_list(self) -> None:
        rid, key = self.put()
        uploads.finish(self.cfg, self.s, rid, "ana")
        copy = next(c for c in self.calls() if c[0] == "copyto")
        self.assertEqual(copy[2], f"mydrive:hub/files/{key}")
        self.assertIn("staging", copy[1])

    def test_a_failed_copy_is_reported_and_nothing_is_left(self) -> None:
        rid, _ = self.put()
        with mock.patch.dict(os.environ, {"FAKE_RCLONE_FAIL": "copyto"}):
            r = uploads.finish(self.cfg, self.s, rid, "ana")
        self.assertEqual((r["sent"], r["failed"]), (0, 1))
        self.assertIn("could not be stored", self.s.pending("hub")[0]["text"])

    def test_view_and_ranged_read_go_through_the_board(self) -> None:
        rid, key = self.put()
        uploads.finish(self.cfg, self.s, rid, "ana")
        p = uploads.provider(self.cfg)
        v = p.view(key)
        self.assertEqual((v.url, v.type), ("api/uploads/file?key=" + key, "audio/mpeg"))
        whole = p.open(key, "")
        self.assertEqual((whole.status, b"".join(whole.chunks)), (200, b"0123456789"))
        whole.close()
        part = p.open(key, "bytes=2-4")
        self.assertEqual((part.status, part.content_range, b"".join(part.chunks)), (206, "bytes 2-4/10", b"234"))
        part.close()
        tail = p.open(key, "bytes=-3")
        self.assertEqual(b"".join(tail.chunks), b"789")
        tail.close()
        self.assertIsNone(p.view("uploads/2026-11-16/99/1-none.mp3"))
        with self.assertRaises(StorageError):
            p.open(key, "bytes=50-60")

    def test_a_missing_rclone_says_so_and_nothing_is_held(self) -> None:
        with mock.patch.dict(os.environ, {"PATH": str(self.dir)}):
            uploads._providers.clear()
            with self.assertRaises(uploads.UploadError) as got:
                uploads.start(self.cfg, self.s, "ana", "hub", "x", [{"name": "a.png", "type": "image/png", "size": 3}])
        self.assertIn("rclone is not installed", str(got.exception))
        self.assertEqual(self.s.pending("hold:hub"), [])

    def test_an_unknown_remote_says_so(self) -> None:
        cfg = make_home(uploads={"provider": "drive", "remote": "other"})
        with self.assertRaises(uploads.UploadError) as got:
            uploads.start(cfg, Store(cfg.db), "ana", "hub", "x", [{"name": "a.png", "type": "image/png", "size": 3}])
        self.assertIn("no remote named 'other'", str(got.exception))

    def test_the_sweep_sends_what_reached_the_remote(self) -> None:
        rid, key = self.put()
        self.clock.tick(uploads.HOLD_MAX + 1)
        self.assertEqual(uploads.sweep(self.cfg, self.s), [rid])
        self.assertTrue((self.dir / "remote" / "mydrive" / "hub" / "files" / key).is_file())

    def test_the_locator_names_remote_and_path(self) -> None:
        d = Drive("mydrive", "", self.dir)
        self.assertEqual(d.locator("uploads/2026-11-16/1/1-a.png"), "drive://mydrive/uploads/2026-11-16/1/1-a.png")


if __name__ == "__main__":
    unittest.main()
