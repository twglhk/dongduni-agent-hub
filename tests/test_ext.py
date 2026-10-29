"""Workspace extensions (.tower/board, .tower/ext) and keeping package edits across `dah update`."""

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from agent_hub import config as config_mod
from agent_hub.engine import Engine
from agent_hub.install import (backup_patches, fetch_originals, file_hash, modified_files, unrequested_patches)
from agent_hub.store import HUB, HUMAN, INFRA, Store
from agent_hub.web import BOARD
from tests.helpers import Clock, FakeRuntime, make_home
from tests.test_web import POST, Board

ROOT = Path(__file__).resolve().parents[1]
ROUTES = '''
calls = []
def stats(ctx, body):
    return {"lanes": sorted(ctx["cfg"].lanes), "who": ctx["me"]["name"], "q": ctx["query"].get("x")}
def echo(ctx, body):
    return 201, {"got": body}
def bad(ctx, body):
    raise RuntimeError("boom")
routes = {("GET", "stats"): stats, ("POST", "echo"): echo, ("GET", "bad"): bad}
'''


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class BoardExtensionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.logs: list[str] = []
        self.b = Board(log=self.logs.append)
        self.tower = self.b.cfg.home / ".tower"

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def test_board_files_and_override(self) -> None:
        _, st, _ = self.b.req("GET", "/api/state")
        self.assertEqual(st["extensions"], {"css": False, "js": False})
        write(self.tower / "board/extra.js", "dah.addSection('hub', 'x', () => 'hi')")
        write(self.tower / "board/hook.py", "print('no')")
        _, st, _ = self.b.req("GET", "/api/state")
        self.assertEqual(st["extensions"], {"css": False, "js": True})
        code, body, headers = self.b.req("GET", "/x/extra.js")
        self.assertEqual((code, body), (200, b"dah.addSection('hub', 'x', () => 'hi')"))
        self.assertTrue(headers["content-type"].startswith("text/javascript"))
        for path in ("/x/hook.py", "/x/../tower.json", "/x/%2e%2e/tower.json", "/x/.secret", "/x/missing.js"):
            self.assertEqual(self.b.req("GET", path)[0], 404, path)

        builtin = BOARD.read_bytes()
        self.assertEqual(self.b.req("GET", "/")[1], builtin)
        write(self.tower / "board/index.html", "<!doctype html><p>ours</p>")
        self.assertEqual(self.b.req("GET", "/")[1], b"<!doctype html><p>ours</p>")
        self.assertEqual(self.b.req("GET", "/?builtin")[1], builtin)  # the way back when an override breaks

    def test_api_routes(self) -> None:
        write(self.tower / "ext/stats.py", ROUTES)
        write(self.tower / "ext/broken.py", "raise ImportError('nope')")
        code, body, _ = self.b.req("GET", "/api/x/stats?x=1")
        self.assertEqual((code, body["lanes"], body["q"]), (200, ["docs", "hub", "infra"], "1"))
        self.assertEqual(self.b.req("POST", "/api/x/echo", {"a": 1})[0], 403)  # needs x-tower like every POST
        self.assertEqual(self.b.req("POST", "/api/x/echo", {"a": 1}, POST)[:2], (201, {"got": {"a": 1}}))
        code, body, _ = self.b.req("GET", "/api/x/bad")
        self.assertEqual(code, 500)
        self.assertIn("boom", body["error"])
        self.assertEqual(self.b.req("GET", "/api/x/stats")[0], 200)
        self.assertEqual(self.b.req("GET", "/api/x/nope")[0], 404)
        self.assertTrue(any("broken.py" in line and "not loaded" in line for line in self.logs))

        time.sleep(0.01)
        write(self.tower / "ext/stats.py", ROUTES.replace('"lanes": sorted', '"n": len'))  # edited: reloaded
        self.assertEqual(self.b.req("GET", "/api/x/stats")[1]["n"], 3)

    def test_signed_in_only(self) -> None:
        b = Board(auth={"mode": "token"})
        try:
            write(b.cfg.home / ".tower/board/extra.css", "body{}")
            write(b.cfg.home / ".tower/ext/stats.py", ROUTES)
            self.assertEqual(b.req("GET", "/x/extra.css")[0], 401)
            self.assertEqual(b.req("GET", "/api/x/stats")[0], 401)
            auth = {"authorization": f"Bearer {b.s.add_member('ana', 'owner', with_token=True)}"}
            self.assertEqual(b.req("GET", "/x/extra.css", headers=auth)[0], 200)
            self.assertEqual(b.req("GET", "/api/x/stats", headers=auth)[1]["who"], "ana")
        finally:
            b.srv.shutdown()

    def test_sibling_workspace_extensions(self) -> None:
        other = make_home(name="Other", lanes={"hub": {"runtime": "claude"}})
        write(other.home / ".tower/ext/stats.py", ROUTES)
        write(other.home / ".tower/board/extra.css", "p{}")
        slugs = [config_mod.register(self.b.cfg.home), config_mod.register(other.home)]
        try:
            self.assertEqual(self.b.req("GET", f"/w/{slugs[1]}/api/x/stats")[1]["lanes"], ["hub"])
            self.assertEqual(self.b.req("GET", f"/w/{slugs[1]}/x/extra.css")[:2], (200, b"p{}"))
            self.assertEqual(self.b.req("GET", f"/w/{slugs[1]}/api/state")[1]["extensions"]["css"], True)
            self.assertEqual(self.b.req("GET", "/api/x/stats")[0], 404)  # this workspace has none
        finally:
            for slug in slugs:
                config_mod.unregister(slug)


class EngineHooksTest(unittest.TestCase):
    def test_events_then_tick_and_errors_are_contained(self) -> None:
        cfg = make_home()
        write(cfg.home / ".tower/ext/watch.py", '''
seen, ticks = [], []
def on_event(ctx, kind, data):
    if kind == "memo" and "explode" in data["text"]:
        raise ValueError("hook bug")
    seen.append((kind, data.get("id")))
def on_tick(ctx):
    ticks.append(ctx["now"])
''')
        clock, logs = Clock(), []
        s = Store(cfg.db, clock=clock)
        s.push(INFRA, "ana", "memo", "before the tower started")  # not news to extensions
        e = Engine(cfg, s, FakeRuntime(s), lambda _: None, clock=clock, log=logs.append)
        e.tick()
        mod = e.ext.modules()[0]
        self.assertEqual((mod.seen, len(mod.ticks)), ([], 1))

        clock.tick(5)
        gid = s.add_goal("Ship", "", "ana")
        iid = s.add_item(HUB, None, "decision", "Port?", "## a\nb", [{"label": "80", "desc": ""}, {"label": "8080", "desc": ""}], [], audience=HUMAN)
        mid = s.push(INFRA, "ana", "memo", "explode please")
        rid = s.add_report(None, "docs", "done", "Done", [])
        e.tick()
        clock.tick(5)
        s.decide(iid, "ok", "ana", "80")
        e.tick()
        self.assertEqual(mod.seen, [("report", rid), ("item", iid), ("goal", gid), ("decided", iid)])
        self.assertEqual(len(mod.ticks), 3)
        self.assertTrue(any("hook bug" in line for line in logs))  # logged, and the tick went on
        self.assertNotIn(("memo", mid), mod.seen)


class PackageEditsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.site = Path(tempfile.mkdtemp())
        write(self.site / "agent_hub/web.py", "print('released')\n")
        write(self.site / "agent_hub/board/index.html", "<p>released</p>\n")
        self.entries = [(rel, file_hash(self.site / rel)) for rel in ("agent_hub/web.py", "agent_hub/board/index.html")]
        self.entries += [("agent_hub/__pycache__/web.cpython-312.pyc", None), ("../../bin/dah", "sha256=x")]

    def test_detect_back_up_and_diff(self) -> None:
        self.assertEqual(modified_files(self.entries, self.site), [])
        write(self.site / "agent_hub/board/index.html", "<p>ours</p>\n")
        changed = modified_files(self.entries, self.site)
        self.assertEqual(changed, ["agent_hub/board/index.html"])

        home = make_home().home
        root = backup_patches(home, "0.3.1", self.site, changed, {"agent_hub/board/index.html": "<p>released</p>\n"})
        self.assertEqual(root, home / ".tower/patches/0.3.1")
        self.assertEqual((root / "files/agent_hub/board/index.html").read_text(), "<p>ours</p>\n")
        diff = (root / "changes.diff").read_text()
        self.assertIn("-<p>released</p>", diff)
        self.assertIn("+<p>ours</p>", diff)
        self.assertEqual(unrequested_patches(home, "0.3.1"), [])  # still that version: nothing replaced yet
        self.assertEqual([p["files"] for p in unrequested_patches(home, "0.4.0")], [changed])

        backup_patches(home, "0.3.2", self.site, changed, None)  # originals unknown: files only
        self.assertFalse((home / ".tower/patches/0.3.2/changes.diff").exists())

    def test_originals_from_the_release_wheel(self) -> None:
        wheel = self.site / "pkg.whl"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("agent_hub/web.py", "print('released')\n")
        wheel.write_bytes(buf.getvalue())
        meta = self.site / "meta.json"
        meta.write_text(json.dumps({"urls": [{"packagetype": "sdist", "url": "x"},
                                             {"packagetype": "bdist_wheel", "url": wheel.as_uri()}]}))
        got = fetch_originals("0.3.1", ["agent_hub/web.py", "agent_hub/gone.py"], meta.as_uri())
        self.assertEqual(got, {"agent_hub/web.py": "print('released')\n"})
        self.assertIsNone(fetch_originals("0.3.1", ["agent_hub/web.py"], (self.site / "none.json").as_uri()))


class UpdateRequestsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.tower("init", "--hub", "claude", "--infra", "claude")

    def tower(self, *args: str) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(ROOT)}
        env.pop("TOWER_LANE", None)
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout + p.stderr

    def test_package_edits_and_a_replaced_board_reach_infra_once(self) -> None:
        site = Path(tempfile.mkdtemp())
        write(site / "agent_hub/web.py", "patched\n")
        backup_patches(self.home, "0.0.1", site, ["agent_hub/web.py"], {"agent_hub/web.py": "released\n"})
        write(self.home / ".tower/board/index.html", "<p>ours</p>")
        write(self.home / ".tower/state/board.sha", "sha256=older-board")

        out = self.tower("update", "--no-upgrade", "--no-restart")
        self.assertIn("asked infra to re-apply the package changes", out)
        self.assertIn("asked infra to carry over the board changes", out)
        memos = Store(config_mod.load(self.home).db).memos(INFRA)
        texts = " ".join(m["text"] for m in memos)
        self.assertIn(".tower/patches/0.0.1/changes.diff", texts)
        self.assertIn("?builtin", texts)
        index = json.loads((self.home / ".tower/patches/0.0.1/index.json").read_text())
        self.assertTrue(index["requested"])

        again = self.tower("update", "--no-upgrade", "--no-restart")
        self.assertNotIn("asked infra", again)
        self.assertEqual(len(Store(config_mod.load(self.home).db).memos(INFRA)), len(memos))

    def test_dry_run_changes_nothing(self) -> None:
        out = self.tower("update", "--dry-run")
        self.assertIn("would upgrade: nothing (dah runs from a clone", out)
        self.assertIn("package files changed in place: none", out)
        self.assertFalse((self.home / ".tower/state/version").exists())

    def test_extend_guide(self) -> None:
        self.assertIn(".tower/ext", self.tower("guide", "extend"))


if __name__ == "__main__":
    unittest.main()
