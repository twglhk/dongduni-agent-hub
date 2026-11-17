from __future__ import annotations

import contextlib
import datetime
import importlib.util
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "agent_hub" / "guide" / "deploy" / "tools" / "tower_db_backup.py"
_spec = importlib.util.spec_from_file_location("tower_db_backup", TOOL)
bk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bk)

NOW = datetime.datetime(2030, 3, 15, 4, 7)


class FakeUploader:
    def __init__(self, days=(), lie_size=None, drop=False, fail_put=False):
        self.files: dict[str, bytes] = {}
        self.folders = list(days)
        self.lie_size, self.drop, self.fail_put = lie_size, drop, fail_put
        self.deleted: list[str] = []

    def put(self, local, day, name):
        if self.fail_put:
            raise bk.BackupError("network down")
        if not self.drop:
            self.files[f"{day}/{name}"] = Path(local).read_bytes()
        return f"fake://{day}/{name}"

    def size(self, day, name):
        data = self.files.get(f"{day}/{name}")
        if data is None:
            return None
        return self.lie_size if self.lie_size is not None else len(data)

    def days(self):
        return self.folders

    def delete_day(self, day):
        self.deleted.append(day)


def make_db(path: Path, rows: int = 3, wal: bool = True) -> None:
    c = sqlite3.connect(str(path))
    if wal:
        c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT)")
    c.executemany("INSERT INTO messages (body) VALUES (?)", [(f"m{i}",) for i in range(rows)])
    c.commit()
    c.close()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "tower.db"
        self.status = self.tmp / "status.json"

    def run_it(self, up, keep=30):
        out, err = io.StringIO(), io.StringIO()
        code = bk.run_backup(self.db, up, self.status, keep, "daily", NOW, out, err)
        return code, out.getvalue(), err.getvalue()

    def read_status(self) -> dict:
        return json.loads(self.status.read_text(encoding="utf-8"))


class Snapshot(Base):
    def test_copy_has_the_rows_and_a_single_file_journal(self) -> None:
        make_db(self.db, rows=5)
        live = sqlite3.connect(str(self.db))
        live.execute("INSERT INTO messages (body) VALUES ('late')")
        live.commit()
        copy = self.tmp / "copy.db"
        tables = bk.snapshot(self.db, copy)
        live.close()
        self.assertEqual(tables, 1)
        c = sqlite3.connect(str(copy))
        self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0], 6)
        self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        c.close()

    def test_a_missing_database_is_an_error_not_an_empty_file(self) -> None:
        with self.assertRaises(bk.BackupError):
            bk.snapshot(self.tmp / "nope.db", self.tmp / "copy.db")
        self.assertFalse((self.tmp / "nope.db").exists())

    def test_a_file_that_is_not_a_database_fails_the_run(self) -> None:
        self.db.write_bytes(b"this is not sqlite" * 100)
        up = FakeUploader()
        code, _, err = self.run_it(up)
        self.assertEqual(code, 1)
        self.assertEqual(up.files, {})
        self.assertIn("FAILED", err)
        self.assertGreater(self.read_status()["last_fail"], 0)

    def test_an_integrity_failure_stops_before_the_upload(self) -> None:
        make_db(self.db)
        real = sqlite3.connect

        class Bad(sqlite3.Connection):
            def execute(self, sql, *a):
                if sql.startswith("PRAGMA integrity_check"):
                    return real(":memory:").execute("SELECT 'row 1 missing from index x'")
                return super().execute(sql, *a)

        bk.sqlite3 = type("S", (), {"connect": staticmethod(lambda p, **k: real(p, factory=Bad, **k)),
                                    "Connection": sqlite3.Connection})
        self.addCleanup(setattr, bk, "sqlite3", sqlite3)
        up = FakeUploader()
        code, _, err = self.run_it(up)
        self.assertEqual(code, 1)
        self.assertIn("integrity_check", err)
        self.assertEqual(up.files, {})


class Run(Base):
    def test_success_uploads_verifies_and_writes_the_status(self) -> None:
        make_db(self.db)
        up = FakeUploader()
        code, out, _ = self.run_it(up)
        self.assertEqual(code, 0)
        self.assertIn("2030-03-15/tower-0407.db", up.files)
        st = self.read_status()
        self.assertEqual(st["ok_key"], "fake://2030-03-15/tower-0407.db")
        self.assertEqual(st["ok_why"], "daily")
        self.assertEqual(st["ok_size"], len(up.files["2030-03-15/tower-0407.db"]))
        self.assertGreater(st["last_ok"], 0)
        self.assertNotIn("last_fail", st)
        self.assertIn("daily", out)

    def test_the_uploaded_copy_is_a_working_database(self) -> None:
        make_db(self.db, rows=4)
        up = FakeUploader()
        self.run_it(up)
        restored = self.tmp / "restored.db"
        restored.write_bytes(up.files["2030-03-15/tower-0407.db"])
        c = sqlite3.connect(str(restored))
        self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0], 4)
        c.close()

    def test_a_size_mismatch_fails(self) -> None:
        make_db(self.db)
        code, _, err = self.run_it(FakeUploader(lie_size=1))
        self.assertEqual(code, 1)
        self.assertIn("size check failed", err)

    def test_a_file_that_is_not_there_after_the_upload_fails(self) -> None:
        make_db(self.db)
        code, _, err = self.run_it(FakeUploader(drop=True))
        self.assertEqual(code, 1)
        self.assertIn("missing", err)

    def test_an_upload_error_fails_and_keeps_the_last_success(self) -> None:
        make_db(self.db)
        self.assertEqual(self.run_it(FakeUploader())[0], 0)
        first_ok = self.read_status()["last_ok"]
        code, _, _ = self.run_it(FakeUploader(fail_put=True))
        st = self.read_status()
        self.assertEqual(code, 1)
        self.assertEqual(st["last_ok"], first_ok)
        self.assertIn("network down", st["error"])
        self.assertEqual(st["fail_why"], "daily")

    def test_a_pruning_error_keeps_the_backup_a_success(self) -> None:
        make_db(self.db)
        up = FakeUploader(days=["2020-01-01"])

        def broken(day):
            raise bk.BackupError("permission denied")

        up.delete_day = broken
        code, _, err = self.run_it(up)
        st = self.read_status()
        self.assertEqual(code, 0)
        self.assertGreater(st["last_ok"], 0)
        self.assertNotIn("last_fail", st)
        self.assertIn("permission denied", st["prune_error"])
        self.assertIn("pruning failed", err)

    def test_nothing_is_pruned_when_the_upload_failed(self) -> None:
        make_db(self.db)
        up = FakeUploader(days=["2020-01-01"], lie_size=1)
        self.run_it(up)
        self.assertEqual(up.deleted, [])


class Prune(Base):
    def test_old_days_are_chosen_by_date_and_other_names_are_left_alone(self) -> None:
        today = datetime.date(2030, 3, 15)
        days = ["2030-03-15", "2030-03-01", "2030-02-14", "2030-02-12", "2029-12-31", "notes", "2030-13-45", "x2030-01-01"]
        self.assertEqual(bk.old_days(days, today, 30), ["2029-12-31", "2030-02-12"])
        self.assertEqual(bk.old_days(days, today, 1), ["2029-12-31", "2030-02-12", "2030-02-14", "2030-03-01"])

    def test_a_run_deletes_only_the_old_date_folders(self) -> None:
        make_db(self.db)
        up = FakeUploader(days=["2030-03-14", "2030-01-01", "2029-06-01", "keep-me"])
        code, out, _ = self.run_it(up, keep=30)
        self.assertEqual(code, 0)
        self.assertEqual(up.deleted, ["2029-06-01", "2030-01-01"])
        self.assertEqual(self.read_status()["pruned"], ["2029-06-01", "2030-01-01"])
        self.assertIn("removed", out)


class DirTarget(Base):
    def test_a_folder_destination_end_to_end(self) -> None:
        make_db(self.db)
        dest = self.tmp / "backups"
        (dest / "2020-01-01").mkdir(parents=True)
        (dest / "2020-01-01" / "tower-0000.db").write_bytes(b"old")
        (dest / "other").mkdir()
        code, _, _ = self.run_it(bk.DirUploader(str(dest)))
        self.assertEqual(code, 0)
        self.assertTrue((dest / "2030-03-15" / "tower-0407.db").is_file())
        self.assertFalse((dest / "2020-01-01").exists())
        self.assertTrue((dest / "other").is_dir())


class Commands(unittest.TestCase):
    def fake(self, answers):
        calls = []

        def runner(argv):
            calls.append(list(argv))
            for prefix, reply in answers:
                if list(argv[:len(prefix)]) == prefix:
                    return reply
            return 0, "", ""

        return runner, calls

    def test_rclone(self) -> None:
        listing = json.dumps([{"Name": "tower-0407.db", "Size": 42}])
        runner, calls = self.fake([(["rclone", "lsjson", "remote:b/tower/2030-03-15"], (0, listing, "")),
                                   (["rclone", "lsjson", "remote:b/tower"], (0, json.dumps([{"Name": "2030-03-14"}, {"Name": "x"}]), ""))])
        up = bk.RcloneUploader("remote:b/tower/", "rclone", ["--drive-use-trash=true"], runner)
        up.put(Path("/tmp/t.db"), "2030-03-15", "tower-0407.db")
        self.assertEqual(calls[0], ["rclone", "copyto", "/tmp/t.db", "remote:b/tower/2030-03-15/tower-0407.db", "--drive-use-trash=true"])
        self.assertEqual(up.size("2030-03-15", "tower-0407.db"), 42)
        self.assertIsNone(up.size("2030-03-15", "other.db"))
        self.assertEqual(up.days(), ["2030-03-14", "x"])
        up.delete_day("2030-03-14")
        self.assertEqual(calls[-1][:3], ["rclone", "purge", "remote:b/tower/2030-03-14"])

    def test_a_failing_command_is_a_backup_error_with_its_message(self) -> None:
        runner, _ = self.fake([(["rclone"], (3, "", "directory not found"))])
        with self.assertRaises(bk.BackupError) as cm:
            bk.RcloneUploader("r:x", "rclone", (), runner).days()
        self.assertIn("directory not found", str(cm.exception))
        self.assertIn("exit 3", str(cm.exception))

    def test_aws(self) -> None:
        ls = "                           PRE 2030-03-14/\n                           PRE notes/\n2030-01-01 00:00:00  5 file.txt\n"
        runner, calls = self.fake([(["aws", "s3api", "head-object"], (0, json.dumps({"ContentLength": 42}), "")),
                                   (["aws", "s3", "ls"], (0, ls, ""))])
        up = bk.S3Uploader("s3://bucket/some/prefix/", "aws", ["--endpoint-url", "https://r2.example.com"], runner)
        up.put(Path("/tmp/t.db"), "2030-03-15", "tower-0407.db")
        self.assertEqual(calls[0][:5], ["aws", "s3", "cp", "/tmp/t.db", "s3://bucket/some/prefix/2030-03-15/tower-0407.db"])
        self.assertEqual(calls[0][-2:], ["--endpoint-url", "https://r2.example.com"])
        self.assertEqual(up.size("2030-03-15", "tower-0407.db"), 42)
        self.assertIn("some/prefix/2030-03-15/tower-0407.db", calls[1])
        self.assertEqual(up.days(), ["2030-03-14", "notes"])
        self.assertEqual(calls[2][3], "s3://bucket/some/prefix/")
        up.delete_day("2030-03-14")
        self.assertEqual(calls[-1][:4], ["aws", "s3", "rm", "s3://bucket/some/prefix/2030-03-14/"])

    def test_aws_object_not_found_is_none(self) -> None:
        runner, _ = self.fake([(["aws"], (254, "", "An error occurred (404) when calling the HeadObject operation: Not Found"))])
        self.assertIsNone(bk.S3Uploader("s3://b/p", "aws", (), runner).size("d", "n"))

    def test_the_method_is_picked_from_the_destination(self) -> None:
        self.assertIsInstance(bk.make_uploader("s3://b/p"), bk.S3Uploader)
        self.assertIsInstance(bk.make_uploader("myremote:backups/tower"), bk.RcloneUploader)
        self.assertIsInstance(bk.make_uploader(os.path.abspath("x")), bk.DirUploader)
        self.assertIsInstance(bk.make_uploader("~/backups"), bk.DirUploader)
        self.assertIsInstance(bk.make_uploader("s3://b/p", "rclone"), bk.RcloneUploader)
        with self.assertRaises(bk.BackupError):
            bk.make_uploader("x:y", "ftp")


class Main(Base):
    def setUp(self) -> None:
        super().setUp()
        quiet = contextlib.ExitStack()
        quiet.enter_context(contextlib.redirect_stdout(io.StringIO()))
        quiet.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.addCleanup(quiet.close)

    def test_no_destination_is_a_usage_error_with_no_default(self) -> None:
        old = os.environ.pop("TOWER_BACKUP_DEST", None)
        self.addCleanup(lambda: old is None or os.environ.__setitem__("TOWER_BACKUP_DEST", old))
        self.assertEqual(bk.main(["--db", str(self.db)]), 2)

    def test_the_destination_can_come_from_the_environment(self) -> None:
        make_db(self.db)
        dest = self.tmp / "out"
        os.environ["TOWER_BACKUP_DEST"] = str(dest)
        self.addCleanup(os.environ.pop, "TOWER_BACKUP_DEST", None)
        self.assertEqual(bk.main(["--db", str(self.db), "--why", "manual"]), 0)
        self.assertEqual(len(list(dest.glob("*/tower-*.db"))), 1)
        self.assertTrue((self.tmp / "tower-db-backup.json").is_file())

    def test_a_bad_keep_days_is_refused(self) -> None:
        self.assertEqual(bk.main(["--db", str(self.db), "--dest", str(self.tmp), "--keep-days", "0"]), 2)


NOTIFY = TOOL.with_name("tower_db_backup_notify.py")
_nspec = importlib.util.spec_from_file_location("tower_db_backup_notify", NOTIFY)
nt = importlib.util.module_from_spec(_nspec)
_nspec.loader.exec_module(nt)


class Notify(unittest.TestCase):
    def test_a_failure_is_told_once_and_a_later_success_needs_no_message(self) -> None:
        status = {"last_ok": 1000.0, "last_fail": 2000.0, "fail_why": "daily", "error": "disk full"}
        texts, state = nt.notices(status, {}, 2100.0)
        self.assertEqual(len(texts), 1)
        self.assertIn("disk full", texts[0])
        self.assertEqual(nt.notices(status, state, 2200.0)[0], [])
        recovered = dict(status, last_ok=3000.0)
        self.assertEqual(nt.notices(recovered, state, 3100.0)[0], [])

    def test_a_stale_backup_is_told_once_per_last_success(self) -> None:
        status = {"last_ok": 1000.0}
        late = 1000.0 + (nt.STALE_HOURS + 1) * 3600
        texts, state = nt.notices(status, {}, late)
        self.assertEqual(len(texts), 1)
        self.assertEqual(nt.notices(status, state, late + 3600)[0], [])
        self.assertEqual(nt.notices(status, {}, 1000.0 + 3600)[0], [])

    def test_on_tick_pushes_to_the_hub_and_remembers(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d)
            (sd / "tower-db-backup.json").write_text(json.dumps({"last_fail": 50.0, "fail_why": "daily", "error": "x"}))
            pushed, logged = [], []

            class Store:
                def push(self, *a):
                    pushed.append(a)

            class Cfg:
                state_dir = sd

            ctx = {"cfg": Cfg, "store": Store(), "log": logged.append, "now": 100.0}
            nt.on_tick(ctx)
            nt.on_tick(ctx)
            self.assertEqual(len(pushed), 1)
            self.assertEqual(pushed[0][:3], ("hub", "infra", "backup_fail"))
            self.assertTrue((sd / "tower_db_backup_notify.json").is_file())

    def test_no_status_file_does_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            class Cfg:
                state_dir = Path(d)
            nt.on_tick({"cfg": Cfg, "store": None, "log": print, "now": 1.0})


if __name__ == "__main__":
    unittest.main()
