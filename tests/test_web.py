import http.client
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from typing import Any

from agent_hub import config as config_mod
from agent_hub.store import HUB, HUMAN, INFRA, Store
from agent_hub.web import make_handler
from tests.helpers import make_home


class Board:
    def __init__(self, log: Any = None, **cfg_extra: Any):
        self.cfg = make_home(**cfg_extra)
        self.s = Store(self.cfg.db)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.cfg, self.s, log or (lambda _: None)))
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
        for ln in st["lanes"]:
            self.assertEqual(set(ln["run"]), {"model", "effort", "text", "state", "why"}, ln["lane"])
        self.assertEqual(st["me"]["role"], "owner")
        self.assertTrue(st["local_only"])

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

    def test_board_memos_go_to_the_hub_only(self) -> None:
        self.assertEqual(self.b.req("POST", "/api/memo", {"text": "hi", "to": "hub"}, POST)[0], 200)
        self.assertEqual(self.b.req("POST", "/api/memo", {"text": "no to"}, POST)[0], 200)
        code, body, _ = self.b.req("POST", "/api/memo", {"text": "hi", "to": "infra"}, POST)
        self.assertEqual(code, 409)
        self.assertIn("ask the hub", body["error"])
        self.assertEqual(self.b.req("POST", "/api/memo", {"text": "hi", "to": "docs"}, POST)[0], 409)
        self.assertEqual(self.b.s.pending(INFRA), [])
        self.assertEqual(len(self.b.s.pending(HUB)), 2)

    def test_board_has_no_infra_tab_and_every_language_has_the_same_keys(self) -> None:
        import re
        from pathlib import Path
        html = (Path(__file__).resolve().parent.parent / "agent_hub" / "board" / "index.html").read_text(encoding="utf-8")
        self.assertIn("const TABS = ['hub', 'engine', 'lanes', 'history'];", html)
        self.assertNotIn('id="v-infra"', html)
        self.assertIn('id="chat-ta"', html)
        ui = html[html.index("const UI = {"):html.index("const LANGS =")]
        blocks = re.split(r"\n  (?=[a-z]{2}: \{\n)", ui)[1:]
        self.assertEqual(len(blocks), 5)

        def keys(block: str) -> list[str]:
            return sorted(re.findall(r"(\w+):", re.sub(r"'(?:[^'\\]|\\.)*'", "''", block.split("\n", 1)[1])))
        base = keys(blocks[0])
        for b in blocks[1:]:
            self.assertEqual(keys(b), base, b[:12])

    def test_max_lanes_from_the_board(self) -> None:
        self.assertEqual(self.b.req("GET", "/api/state")[1]["lane_cap"], {"used": 0, "cap": 2, "max": 10, "sessions_max": 5})
        code, body, _ = self.b.req("POST", "/api/settings", {"max_lanes": 4}, POST)
        self.assertEqual((code, body["max_lanes"]), (200, 4))
        self.assertEqual(config_mod.load(self.b.cfg.home).max_lanes, 4)
        self.assertEqual(self.b.req("GET", "/api/state")[1]["lane_cap"]["cap"], 4)
        self.assertIn("max_lanes 2 → 4", self.b.s.pending(HUB)[-1]["text"])
        for bad in (0, 11, "3", True, None):
            self.assertEqual(self.b.req("POST", "/api/settings", {"max_lanes": bad}, POST)[0], 409)

    def test_sessions_per_lane_from_the_board(self) -> None:
        code, body, _ = self.b.req("POST", "/api/settings", {"lane": "docs", "sessions": 3}, POST)
        self.assertEqual((code, body["sessions"]), (200, 3))
        cfg = config_mod.load(self.b.cfg.home)
        self.assertEqual((cfg.lanes["docs"].max_instances, cfg.instance_cap("docs"), cfg.instance_cap("infra")), (3, 3, 1))
        self.assertIn("docs sessions 1 → 3", self.b.s.pending(HUB)[-1]["text"])
        self.assertEqual(self.b.req("POST", "/api/settings", {"lane": "infra", "sessions": 2}, POST)[0], 200)
        self.assertEqual(config_mod.load(self.b.cfg.home).infra_sessions(), ["infra", "infra#2"])
        for bad in ({"lane": "hub", "sessions": 2}, {"lane": "ghost", "sessions": 2}, {"lane": "docs", "sessions": 0},
                    {"lane": "docs", "sessions": 6}, {"lane": "docs", "sessions": True}):
            self.assertEqual(self.b.req("POST", "/api/settings", bad, POST)[0], 409)

    def test_usage_per_lane_and_runtime(self) -> None:
        self.b.s.set_lane("docs", session="t1", status="busy")
        self.b.s.record_usage("docs", "t1", "codex", 12000, 300, None, 12)
        self.b.s.record_usage("hub", "h1", "claude", 50000, 900, 0.4, 50)
        self.b.s.record_usage("infra", "i1", "claude", 10000, 100, 0.1, 10)
        st = self.b.req("GET", "/api/state")[1]
        self.assertEqual(st["runtimes"], ["opencode", "claude", "codex"])
        self.assertEqual(st["default_runtime"], "claude")
        by = {r["runtime"]: r for r in st["usage"]["runtimes"]}
        self.assertEqual(list(by), ["claude", "codex"])
        self.assertEqual((by["claude"]["lanes"], by["claude"]["tokens_in"], by["claude"]["sessions"]),
                         (["hub", "infra"], 60000, 2))
        self.assertAlmostEqual(by["claude"]["cost"], 0.5)
        self.assertIsNone(by["codex"]["cost"])
        docs = next(ln for ln in st["lanes"] if ln["lane"] == "docs")
        self.assertEqual((docs["ctx_k"], docs["usage"]["tokens_out"]), (12, 300))

    def test_lanes_added_to_tower_json_show_up_without_a_restart(self) -> None:
        path = self.b.cfg.home / ".tower" / "tower.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["lanes"]["qa"] = {"runtime": "claude", "label": "QA"}
        path.write_text(json.dumps(data), encoding="utf-8")
        os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
        lanes = [ln["lane"] for ln in self.b.req("GET", "/api/state")[1]["lanes"]]
        self.assertEqual(lanes, ["hub", "infra", "docs", "qa"])

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
        self.assertEqual(self.b.req("GET", "/")[0], 200)
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


class GoalsGoneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Board()

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def test_goal_routes_answer_410_and_state_has_no_goals(self) -> None:
        for method, path in (("GET", "/api/goals"), ("POST", "/api/goals"), ("POST", "/api/goals/2"),
                             ("GET", "/api/goals/2")):
            self.assertEqual(self.b.req(method, path, {"title": "x"} if method == "POST" else None,
                                        POST if method == "POST" else None)[0], 410, path)
        self.assertEqual(self.b.req("GET", "/api/history?kind=goals")[0], 410)
        code, st, _ = self.b.req("GET", "/api/state")
        self.assertEqual(code, 200)
        self.assertNotIn("goals", st)
        self.assertNotIn("goals", st["counts"])
        self.assertEqual(self.b.s.pending(HUB), [])


class WorkspacesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.front = Board(name="Front")
        self.other = make_home(name="Other")
        Store(self.other.db).add_item(HUB, None, "review", "Look", "## a\nb", [], [], audience=HUMAN)
        self.slugs = [config_mod.register(self.front.cfg.home), config_mod.register(self.other.home)]

    def tearDown(self) -> None:
        self.front.srv.shutdown()
        for slug in self.slugs:
            config_mod.unregister(slug)

    def test_one_board_switches_between_workspaces(self) -> None:
        _, ws, _ = self.front.req("GET", "/api/workspaces")
        rows = {w["slug"]: w for w in ws["workspaces"]}
        me, other = rows[self.slugs[0]], rows[self.slugs[1]]
        self.assertEqual((me["href"], me["current"]), ("/", True))
        self.assertEqual((other["href"], other["open"], other["name"]), (f"/w/{self.slugs[1]}/", 1, "Other"))
        code, _, headers = self.front.req("GET", f"/w/{self.slugs[1]}")  # urllib follows 308 only from Python 3.11
        if code == 308:
            self.assertEqual(headers["location"], f"/w/{self.slugs[1]}/")
        code, page, _ = self.front.req("GET", f"/w/{self.slugs[1]}/")
        self.assertEqual(code, 200)
        self.assertIn(b"<!doctype html>", page)
        _, st, _ = self.front.req("GET", f"/w/{self.slugs[1]}/api/state")
        self.assertEqual(st["title"], "Other")
        iid = st["items"][0]["id"]
        code, _, _ = self.front.req("POST", f"/w/{self.slugs[1]}/api/items/{iid}/decide", {"verdict": "ok"}, POST)
        self.assertEqual(code, 200)
        self.assertEqual(Store(self.other.db).item(iid)["state"], "decided")
        _, ws, _ = self.front.req("GET", f"/w/{self.slugs[1]}/api/workspaces")
        self.assertTrue({w["slug"]: w for w in ws["workspaces"]}[self.slugs[1]]["current"])
        self.assertEqual(self.front.req("GET", "/w/nope/api/state")[0], 404)

    def test_local_workspace_is_not_served_behind_a_signed_in_board(self) -> None:
        front = Board(auth={"mode": "token"})
        slug = config_mod.register(front.cfg.home)
        try:
            token = front.s.add_member("ana", "owner", with_token=True)
            auth = {"authorization": f"Bearer {token}"}
            self.assertEqual(front.req("GET", f"/w/{self.slugs[1]}/api/state", headers=auth)[0], 403)
            _, ws, _ = front.req("GET", "/api/workspaces", headers=auth)
            locked = {w["slug"]: w for w in ws["workspaces"]}[self.slugs[1]]
            self.assertIsNone(locked["href"])
            self.assertNotIn("open", locked)
        finally:
            front.srv.shutdown()
            config_mod.unregister(slug)


def raw(b: Board, method: str, path: str, headers: dict[str, str], body: bytes | None = None) -> tuple[int, Any, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", b.srv.server_address[1], timeout=5)
    try:
        conn.putrequest(method, path, skip_host="host" in {k.lower() for k in headers})
        for k, v in headers.items():
            conn.putheader(k, v)
        conn.endheaders(body)
        r = conn.getresponse()
        return r.status, r, r.read()
    finally:
        conn.close()


class HostAndOriginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Board()
        self.port = self.b.srv.server_address[1]

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def test_unknown_host_is_refused(self) -> None:
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1:1", "localhost.evil.example"):
            self.assertEqual(self.b.req("GET", "/api/state", headers={"Host": host})[0], 421, host)
            self.assertEqual(self.b.req("POST", "/api/memo", {"text": "x"}, {**POST, "Host": host})[0], 421, host)
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"):
            self.assertEqual(self.b.req("GET", "/api/state", headers={"Host": host})[0], 200, host)

    def test_public_url_host_and_exposed_bind_are_allowed(self) -> None:
        b = Board(web={"host": "0.0.0.0", "public_url": "https://board.example.com"}, auth={"mode": "token", "allow_exposed": True})
        try:
            for host in ("board.example.com", f"192.168.1.5:{b.srv.server_address[1]}"):
                self.assertEqual(b.req("GET", "/", headers={"Host": host})[0], 200, host)
            self.assertEqual(b.req("GET", "/", headers={"Host": "evil.example"})[0], 421)
        finally:
            b.srv.shutdown()

    def test_post_from_another_origin_is_refused(self) -> None:
        host = f"127.0.0.1:{self.port}"
        for h in ({"Origin": "http://evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"},
                  {"Sec-Fetch-Site": "same-site"}):
            self.assertEqual(self.b.req("POST", "/api/memo", {"text": "x"}, {**POST, **h})[0], 403, h)
        for h in ({"Origin": f"http://{host}"}, {"Sec-Fetch-Site": "same-origin"}, {}):
            self.assertEqual(self.b.req("POST", "/api/memo", {"text": "x"}, {**POST, **h})[0], 200, h)

    def test_content_length_is_checked_before_reading(self) -> None:
        host = {"host": f"127.0.0.1:{self.port}", "x-tower": "1"}
        self.assertEqual(raw(self.b, "POST", "/api/memo", {**host, "content-length": "-1"})[0], 413)
        self.assertEqual(raw(self.b, "POST", "/api/memo", {**host, "content-length": "99999999"})[0], 413)
        self.assertEqual(raw(self.b, "POST", "/api/memo", {**host, "content-length": "abc"})[0], 400)

    def test_handler_has_a_socket_timeout_and_no_python_banner(self) -> None:
        _, resp, _ = raw(self.b, "GET", "/api/state", {"host": f"127.0.0.1:{self.port}"})
        self.assertNotIn("Python", resp.getheader("server"))
        self.assertNotIn("BaseHTTP", resp.getheader("server"))
        self.assertEqual(self.b.srv.RequestHandlerClass.timeout, 30)


class SiblingAuthTest(unittest.TestCase):
    H = "Tailscale-User-Login"

    def other_home(self, **extra: Any) -> str:
        cfg = make_home(**extra)
        slug = config_mod.register(cfg.home)
        self.slugs.append(slug)
        return slug

    def setUp(self) -> None:
        self.slugs: list[str] = []

    def tearDown(self) -> None:
        for slug in self.slugs:
            config_mod.unregister(slug)

    def test_header_sibling_needs_a_header_front_with_the_same_header_name(self) -> None:
        sib = self.other_home(auth={"mode": "header", "header": self.H})
        for front_auth, ok in (({"mode": "token"}, False), ({"mode": "header", "header": "X-Other"}, False),
                               ({"mode": "header", "header": self.H.lower()}, True)):
            front = Board(auth=front_auth)
            try:
                if front_auth["mode"] == "token":
                    tok = front.s.add_member("ana", "owner", with_token=True)
                    head = {"authorization": f"Bearer {tok}", self.H: "x@y.z"}
                else:
                    head = {front_auth["header"]: "x@y.z", self.H: "x@y.z"}
                self.assertEqual(front.req("GET", f"/w/{sib}/api/state", headers=head)[0], 200 if ok else 403, front_auth)
            finally:
                front.srv.shutdown()


class ServedFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Board()
        self.home = self.b.cfg.home

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def test_every_part_of_the_resolved_path_obeys_the_name_rules(self) -> None:
        (self.home / ".env.local").write_text("TOKEN=1")
        (self.home / "innocent.txt").symlink_to(self.home / ".env.local")
        (self.home / "state.TFSTATE").write_text("{}")
        (self.home / "Secrets.YAML").write_text("a: 1")
        (self.home / "service-account-prod.json").write_text("{}")
        (self.home / "cert.pfx").write_bytes(b"x")
        (self.home / "ok.txt").write_text("fine")
        self.assertEqual(self.b.req("GET", "/f/ok.txt")[0], 200)
        for name in ("innocent.txt", "state.TFSTATE", "Secrets.YAML", "service-account-prod.json", "cert.pfx"):
            self.assertEqual(self.b.req("GET", "/f/" + name)[0], 404, name)

    def test_files_are_sandboxed_and_xml_types_download(self) -> None:
        (self.home / "pic.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>")
        (self.home / "page.html").write_text("<p>x</p>")
        (self.home / "a.png").write_bytes(b"\x89PNG")
        code, _, h = self.b.req("GET", "/f/pic.svg")
        self.assertEqual(code, 200)
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertTrue(h["content-security-policy"].startswith("sandbox"))
        self.assertEqual(h["content-disposition"], "attachment")
        _, _, h = self.b.req("GET", "/f/pic.svg", headers={"Sec-Fetch-Dest": "image"})
        self.assertIsNone(h["content-disposition"])
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertIsNone(self.b.req("GET", "/f/a.png")[2]["content-disposition"])
        self.assertIn("sandbox", self.b.req("GET", "/f/page.html")[2]["content-security-policy"])
        board = self.home / ".tower" / "board"
        board.mkdir()
        (board / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
        h = self.b.req("GET", "/x/logo.svg")[2]
        self.assertEqual((h["x-content-type-options"], h["content-disposition"]), ("nosniff", "attachment"))
        self.assertIn("sandbox", h["content-security-policy"])

    def test_empty_file_with_a_range_is_a_plain_200(self) -> None:
        (self.home / "empty.txt").write_text("")
        code, body, h = self.b.req("GET", "/f/empty.txt", headers={"range": "bytes=0-"})
        self.assertEqual((code, body, h["content-range"], h["content-length"]), (200, b"", None, "0"))


class HardeningHeadersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Board()

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def test_page_and_json_cannot_be_framed(self) -> None:
        for path in ("/", "/old/", "/api/state"):
            h = self.b.req("GET", path)[2]
            self.assertEqual(h["x-frame-options"], "DENY", path)
            self.assertEqual(h["x-content-type-options"], "nosniff", path)
            self.assertEqual(h["referrer-policy"], "no-referrer", path)
            self.assertIn("frame-ancestors 'none'", h["content-security-policy"], path)

    def test_react_page_csp_allows_only_its_own_origin(self) -> None:
        csp = self.b.req("GET", "/")[2]["content-security-policy"]
        self.assertIn("default-src 'self'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertNotIn("http", csp)


class SettingsRoleTest(unittest.TestCase):
    def test_only_an_owner_changes_settings(self) -> None:
        b = Board(auth={"mode": "token"})
        try:
            member = b.s.add_member("bo", "member", with_token=True)
            owner = b.s.add_member("ana", "owner", with_token=True)
            self.assertEqual(b.req("POST", "/api/settings", {"max_lanes": 3}, {**POST, "authorization": f"Bearer {member}"})[0], 403)
            self.assertEqual(b.req("POST", "/api/settings", {"max_lanes": 3}, {**POST, "authorization": f"Bearer {owner}"})[0], 200)
        finally:
            b.srv.shutdown()


class ExposedLocalModeTest(unittest.TestCase):
    def test_local_mode_refuses_to_be_exposed_without_opt_in(self) -> None:
        for extra in ({"web": {"host": "0.0.0.0"}}, {"web": {"public_url": "https://b.example.com"}}, {"tunnel": "cloudflared run x"}):
            with self.assertRaises(config_mod.ConfigError, msg=extra):
                make_home(**extra)
            make_home(**extra, auth={"mode": "local", "allow_exposed": True})
            make_home(**extra, auth={"mode": "token"})
        make_home(web={"host": "127.0.0.1", "port": 9000})


if __name__ == "__main__":
    unittest.main()
