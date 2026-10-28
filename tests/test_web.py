import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from typing import Any

from agent_hub.store import HUMAN, INFRA, Store
from agent_hub.web import make_handler
from tests.helpers import make_home


class Board:
    def __init__(self, **cfg_extra: Any):
        self.cfg = make_home(**cfg_extra)
        self.s = Store(self.cfg.db)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cfg, self.s, lambda _: None))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def req(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, Any, Any]:
        h = {"content-type": "application/json", **(headers or {})}
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                raw = resp.read()
                ctype = resp.headers.get("content-type", "")
                return resp.status, (json.loads(raw) if "json" in ctype else raw), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), e.headers


POST = {"x-tower": "1"}


class LocalModeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Board()
        self.iid = self.b.s.add_item(INFRA, None, "decision", "Port?", "## a\nb",
                                     [{"label": "80", "desc": ""}, {"label": "8080", "desc": ""}], [], audience=HUMAN)

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def test_state_contract(self) -> None:
        code, st, _ = self.b.req("GET", "/api/state")
        self.assertEqual(code, 200)
        for key in ("title", "lang", "auth_mode", "me", "paused", "lane_cap", "lanes", "items", "tasks", "reports",
                    "memos", "counts"):
            self.assertIn(key, st)
        self.assertEqual([ln["lane"] for ln in st["lanes"]], ["hub", "infra", "docs"])
        self.assertEqual(st["items"][0]["options"][1]["label"], "8080")
        self.assertEqual(st["me"]["role"], "owner")

    def test_post_needs_header(self) -> None:
        code, _, _ = self.b.req("POST", f"/api/items/{self.iid}/decide", {"verdict": "ok", "choice": "80"})
        self.assertEqual(code, 403)

    def test_decide_and_conflict(self) -> None:
        code, body, _ = self.b.req("POST", f"/api/items/{self.iid}/decide", {"verdict": "ok", "choice": "8080"}, POST)
        self.assertEqual(code, 200)
        self.assertEqual(body["item"]["choice"], "8080")
        code, body, _ = self.b.req("POST", f"/api/items/{self.iid}/decide", {"verdict": "ok", "choice": "80"}, POST)
        self.assertEqual(code, 409)
        self.assertIn("already decided", body["error"])

    def test_memo_to_infra_and_hub_only(self) -> None:
        self.assertEqual(self.b.req("POST", "/api/memo", {"text": "hi", "to": "infra"}, POST)[0], 200)
        self.assertEqual(self.b.req("POST", "/api/memo", {"text": "hi", "to": "hub"}, POST)[0], 200)
        self.assertEqual(self.b.req("POST", "/api/memo", {"text": "hi", "to": "docs"}, POST)[0], 409)
        self.assertEqual(len(self.b.s.pending(INFRA)), 1)

    def test_files_refuse_secrets_and_dotpaths(self) -> None:
        home = self.b.cfg.home
        (home / "shot.png").write_bytes(b"\x89PNG....")
        (home / "secret.key").write_text("x")
        self.assertEqual(self.b.req("GET", "/f/shot.png")[0], 200)
        self.assertEqual(self.b.req("GET", "/f/secret.key")[0], 404)
        self.assertEqual(self.b.req("GET", "/f/.tower/tower.json")[0], 404)
        self.assertEqual(self.b.req("GET", "/f/..%2F..%2Fetc%2Fpasswd")[0], 404)
        code, raw, headers = self.b.req("GET", "/f/shot.png", headers={"range": "bytes=0-3"})
        self.assertEqual((code, raw, headers["content-range"]), (206, b"\x89PNG", "bytes 0-3/8"))

    def test_history_paging(self) -> None:
        code, body, _ = self.b.req("GET", "/api/history?kind=memos&offset=0")
        self.assertEqual((code, body["more"]), (200, False))
        self.assertEqual(self.b.req("GET", "/api/history?kind=nope")[0], 400)


class TokenModeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Board(auth={"mode": "token"})
        self.token = self.b.s.add_member("ana", "owner", with_token=True)

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def test_login_cookie_flow(self) -> None:
        self.assertEqual(self.b.req("GET", "/api/state")[0], 401)
        self.assertEqual(self.b.req("GET", "/")[0], 200)  # the page itself loads and shows sign-in
        self.assertEqual(self.b.req("POST", "/api/login", {"token": "wrong"}, POST)[0], 401)
        code, _, headers = self.b.req("POST", "/api/login", {"token": self.token}, POST)
        self.assertEqual(code, 200)
        cookie = headers["set-cookie"].split(";")[0]
        self.assertIn("HttpOnly", headers["set-cookie"])
        code, st, _ = self.b.req("GET", "/api/state", headers={"cookie": cookie})
        self.assertEqual((code, st["me"]["name"]), (200, "ana"))
        code, _, _ = self.b.req("GET", "/api/state", headers={"authorization": f"Bearer {self.token}"})
        self.assertEqual(code, 200)


class HeaderModeTest(unittest.TestCase):
    H = "Cf-Access-Authenticated-User-Email"

    def setUp(self) -> None:
        self.b = Board(auth={"mode": "header", "header": self.H})

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def test_identity_from_proxy_and_allow_list(self) -> None:
        self.assertEqual(self.b.req("GET", "/api/state")[0], 401)
        code, st, _ = self.b.req("GET", "/api/state", headers={self.H: "bo@x.com"})
        self.assertEqual((code, st["me"]["name"]), (200, "bo@x.com"))
        self.b.s.add_member("ana", "owner", "ana@x.com")
        self.assertEqual(self.b.req("GET", "/api/state", headers={self.H: "bo@x.com"})[0], 401)
        code, st, _ = self.b.req("GET", "/api/state", headers={self.H: "ana@x.com"})
        self.assertEqual(st["me"], {"name": "ana", "role": "owner"})


if __name__ == "__main__":
    unittest.main()
