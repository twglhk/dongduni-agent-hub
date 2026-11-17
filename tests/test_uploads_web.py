import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from typing import Any

from agent_hub import uploads
from agent_hub.store import Store
from agent_hub.web import make_handler
from tests.helpers import Clock, make_home

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(200))
POST = {"x-tower": "1"}


class Board:
    def __init__(self, **extra: Any) -> None:
        self.cfg = make_home(**extra)
        self.clock = Clock(1_800_000_000.0)
        self.s = Store(self.cfg.db, clock=self.clock)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cfg, self.s, lambda _: None))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()

    def call(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None,
             raw: bytes | None = None) -> tuple[int, Any, http.client.HTTPMessage]:
        h = dict(headers or {})
        data = raw
        if raw is None and body is not None:
            data = json.dumps(body).encode()
            h["content-type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, data, h)
            r = conn.getresponse()
            payload = r.read()
            ctype = r.headers.get("content-type", "")
            return r.status, (json.loads(payload or b"{}") if "json" in ctype else payload), r.headers
        finally:
            conn.close()

    def upload(self, text: str, name: str, data: bytes, auth: dict[str, str] | None = None, ctype: str = "") -> dict[str, Any]:
        h = {**POST, **(auth or {})}
        code, plan, _ = self.call("POST", "/api/uploads/start", {"text": text, "files": [
            {"name": name, "type": ctype, "size": len(data)}]}, h)
        assert code == 200, plan
        rid = plan["id"]
        code, got, _ = self.call("POST", f"/api/uploads/chunk?id={rid}&n=1&offset=0", raw=data,
                                 headers={**h, "content-type": "application/octet-stream"})
        assert code == 200 and got["received"] == len(data), got
        code, out, _ = self.call("POST", "/api/uploads/finish", {"id": rid}, h)
        assert code == 200, out
        return {"id": rid, **out}


class LocalBoardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Board()

    def tearDown(self) -> None:
        self.b.close()
        uploads._providers.clear()

    def test_state_says_what_the_board_may_send(self) -> None:
        _, st, _ = self.b.call("GET", "/api/state")
        self.assertEqual((st["uploads"]["provider"], st["uploads"]["max_mb"], st["uploads"]["max_files"]),
                         ("local", 25, 10))
        self.assertIn("png", st["uploads"]["accept"])

    def test_upload_view_and_fetch_back(self) -> None:
        got = self.b.upload("look", "shot.png", PNG, ctype="image/png")
        self.assertEqual((got["sent"], got["failed"]), (1, 0))
        path = got["files"][0]["key"]
        [msg] = self.b.s.pending("hub")
        self.assertIn(path, msg["text"])
        code, data, headers = self.b.call("GET", "/f/" + path)
        self.assertEqual((code, data, headers["content-type"]), (200, PNG, "image/png"))
        code, view, _ = self.b.call("GET", "/api/uploads/view?key=" + path)
        self.assertEqual((code, view["url"], view["type"]), (200, "f/" + path, "image/png"))
        self.assertEqual(self.b.call("GET", "/api/uploads/view?key=uploads/2026-01-01/1/1-none.png")[0], 404)

    def test_a_text_file_is_served_as_text(self) -> None:
        got = self.b.upload("", "notes.txt", b"plain words", ctype="text/plain")
        code, data, headers = self.b.call("GET", "/f/" + got["files"][0]["key"])
        self.assertEqual((code, data), (200, b"plain words"))
        self.assertTrue(headers["content-type"].startswith("text/plain"))

    def test_post_rules_apply_to_every_upload_route(self) -> None:
        for path, kw in (("/api/uploads/start", {"body": {"files": []}}), ("/api/uploads/finish", {"body": {"id": 1}}),
                         ("/api/uploads/chunk?id=1&n=1&offset=0", {"raw": b"x"})):
            self.assertEqual(self.b.call("POST", path, **kw)[0], 403, path)
            self.assertEqual(self.b.call("POST", path, headers={**POST, "origin": "http://evil.example"}, **kw)[0], 403, path)
            self.assertEqual(self.b.call("POST", path, headers={**POST, "host": "evil.example"}, **kw)[0], 421, path)

    def test_chunk_size_order_and_owner(self) -> None:
        h = {**POST, "content-type": "application/octet-stream"}
        _, plan, _ = self.b.call("POST", "/api/uploads/start", {"files": [{"name": "a.png", "type": "image/png", "size": 24 * 1024 * 1024}]}, POST)
        rid = plan["id"]
        self.assertEqual(self.b.call("POST", f"/api/uploads/chunk?id={rid}&n=1&offset=0", raw=b"x" * (4 * 1024 * 1024 + 1), headers=h)[0], 413)
        self.assertEqual(self.b.call("POST", f"/api/uploads/chunk?id={rid}&n=1&offset=0", raw=b"abc", headers=h)[0], 200)
        code, body, _ = self.b.call("POST", f"/api/uploads/chunk?id={rid}&n=1&offset=0", raw=b"abc", headers=h)
        self.assertEqual((code, body["received"]), (409, 3))
        for q in ("id=x&n=1&offset=0", f"id={rid}&n=1&offset=-1", f"id={rid}&n=7&offset=3", "id=999&n=1&offset=0"):
            self.assertIn(self.b.call("POST", "/api/uploads/chunk?" + q, raw=b"abc", headers=h)[0], (400, 409), q)

    def test_oversize_and_type_are_refused_before_any_byte(self) -> None:
        for f in ({"name": "big.png", "type": "image/png", "size": 26 * 1024 * 1024},
                  {"name": "x.html", "type": "text/html", "size": 3}, {"name": "x.svg", "type": "image/svg+xml", "size": 3}):
            code, body, _ = self.b.call("POST", "/api/uploads/start", {"files": [f]}, POST)
            self.assertEqual(code, 409, f)
        self.assertEqual(self.b.s.pending("hold:hub"), [])

    def test_traversal_never_reaches_a_file(self) -> None:
        (self.b.cfg.workdir / "secret.txt").write_text("top")
        for path in ("/f/uploads/../secret.txt", "/f/uploads/2026-01-01/1/../../../secret.txt", "/f/%2e%2e/secret.txt"):
            self.assertEqual(self.b.call("GET", path)[0], 404, path)
        for key in ("uploads/../secret.txt", "../secret.txt", "/etc/passwd", "uploads/2026-01-01/1/..%2f..%2fsecret.txt"):
            self.assertEqual(self.b.call("GET", "/api/uploads/view?key=" + key)[0], 400, key)
            self.assertEqual(self.b.call("GET", "/api/uploads/file?key=" + key)[0], 400, key)

    def test_the_sweep_runs_with_the_board_state(self) -> None:
        _, plan, _ = self.b.call("POST", "/api/uploads/start", {"text": "words", "files": [
            {"name": "a.png", "type": "image/png", "size": 100}]}, POST)
        self.assertEqual(self.b.s.pending("hub"), [])
        self.b.clock.tick(uploads.HOLD_MAX + 1)
        self.b.call("GET", "/api/state")
        [msg] = self.b.s.pending("hub")
        self.assertEqual(msg["id"], plan["id"])
        self.assertTrue(msg["text"].startswith("words\n\n"))

    def test_off_means_no_routes_and_no_flag(self) -> None:
        b = Board(uploads={"provider": "off"})
        try:
            self.assertIsNone(b.call("GET", "/api/state")[1]["uploads"])
            self.assertEqual(b.call("POST", "/api/uploads/start", {"files": [{"name": "a.png", "type": "image/png", "size": 3}]}, POST)[0], 409)
        finally:
            b.close()


class MembersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Board(auth={"mode": "token"}, requester=True)
        self.tok = {n: self.b.s.add_member(n, "owner" if n == "root" else "member", with_token=True) for n in ("root", "ana", "bo")}
        self.auth = {n: {"authorization": f"Bearer {t}"} for n, t in self.tok.items()}

    def tearDown(self) -> None:
        self.b.close()
        uploads._providers.clear()

    def test_a_member_cannot_fetch_another_members_attachment(self) -> None:
        got = self.b.upload("mine", "shot.png", PNG, self.auth["ana"], "image/png")
        path = got["files"][0]["key"]
        for who, code in (("ana", 200), ("root", 200), ("bo", 404)):
            self.assertEqual(self.b.call("GET", "/f/" + path, headers=self.auth[who])[0], code, who)
            self.assertEqual(self.b.call("GET", "/api/uploads/view?key=" + path, headers=self.auth[who])[0], code, who)
        self.assertEqual(self.b.call("GET", "/f/" + path)[0], 401)

    def test_an_unreferenced_file_is_not_shown_to_members(self) -> None:
        got = self.b.upload("mine", "shot.png", PNG, self.auth["ana"], "image/png")
        path = got["files"][0]["key"]
        other = self.b.cfg.workdir / "uploads" / "2026-01-01" / "7" / "1-stray.png"
        other.parent.mkdir(parents=True)
        other.write_bytes(PNG)
        self.assertEqual(self.b.call("GET", "/f/uploads/2026-01-01/7/1-stray.png", headers=self.auth["ana"])[0], 404)
        self.assertEqual(self.b.call("GET", "/f/uploads/2026-01-01/7/1-stray.png", headers=self.auth["root"])[0], 200)
        self.assertEqual(self.b.call("GET", "/f/" + path, headers=self.auth["ana"])[0], 200)

    def test_a_held_request_shows_nothing_yet(self) -> None:
        _, plan, _ = self.b.call("POST", "/api/uploads/start", {"files": [{"name": "a.png", "type": "image/png", "size": 3}]}, {**POST, **self.auth["ana"]})
        _, st, _ = self.b.call("GET", "/api/state", headers=self.auth["root"])
        self.assertEqual([m["id"] for m in st["memos"]["hub"]], [])
        self.assertEqual(plan["id"], self.b.s.pending("hold:hub")[0]["id"])

    def test_another_member_cannot_continue_or_finish_it(self) -> None:
        _, plan, _ = self.b.call("POST", "/api/uploads/start", {"files": [{"name": "a.png", "type": "image/png", "size": 3}]}, {**POST, **self.auth["ana"]})
        rid = plan["id"]
        h = {**POST, **self.auth["bo"], "content-type": "application/octet-stream"}
        self.assertEqual(self.b.call("POST", f"/api/uploads/chunk?id={rid}&n=1&offset=0", raw=b"abc", headers=h)[0], 409)
        self.assertEqual(self.b.call("POST", "/api/uploads/finish", {"id": rid}, {**POST, **self.auth["bo"]})[0], 409)


if __name__ == "__main__":
    unittest.main()
