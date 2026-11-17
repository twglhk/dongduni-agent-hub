import http.client
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from agent_hub import config as config_mod
from agent_hub.web import BOARD, BOARD_NEXT
from tests.helpers import make_home
from tests.test_web import Board

APP = Path(__file__).resolve().parent.parent / "board-app"
LANGS = ("en", "ko", "ja", "zh", "es")


def flat(d: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for k, v in d.items():
        out |= flat(v, prefix + k + ".") if isinstance(v, dict) else {prefix + k}
    return out


class BoardNextTest(unittest.TestCase):

    def setUp(self) -> None:
        self.b = Board()
        self.assets = re.findall(r'(?:src|href)="\./(assets/[^"]+)"', (BOARD_NEXT / "index.html").read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def _location(self, path: str) -> tuple[int, str | None]:
        c = http.client.HTTPConnection("127.0.0.1", self.b.srv.server_address[1], timeout=5)
        c.request("GET", path)
        r = c.getresponse()
        r.read()
        c.close()
        return r.status, r.getheader("location")

    def test_build_is_committed_and_whole(self) -> None:
        self.assertTrue(any(a.endswith(".js") for a in self.assets))
        self.assertTrue(any(a.endswith(".css") for a in self.assets))
        for a in self.assets:
            self.assertTrue((BOARD_NEXT / a).is_file(), a)
        on_disk = {p.name for p in (BOARD_NEXT / "assets").iterdir()}
        self.assertEqual(on_disk, {a.split("/")[-1] for a in self.assets}, "a stale file from an older build is still in board_next/")

    def test_build_makes_no_external_request(self) -> None:
        text = "".join((BOARD_NEXT / a).read_text(encoding="utf-8") for a in self.assets)
        page = (BOARD_NEXT / "index.html").read_text(encoding="utf-8")
        self.assertNotRegex(page, r"""(?:src|href)=["']?https?://""")
        self.assertNotRegex(text, r"""@import\s+(?:url\()?["']?https?://""")
        self.assertNotRegex(text, r"""fetch\(["']https?://""")
        self.assertNotIn("fonts.googleapis", text)

    def test_page_and_assets_at_root(self) -> None:
        for page in ("/", "/index.html"):
            code, body, h = self.b.req("GET", page)
            self.assertEqual(code, 200)
            self.assertIn(b'<div id="root">', body)
            self.assertEqual(h["cache-control"], "no-cache")
        for a in self.assets:
            code, _, h = self.b.req("GET", "/" + a)
            self.assertEqual(code, 200, a)
            self.assertIn("immutable", h["cache-control"])
            self.assertTrue(h["content-type"].startswith("text/javascript" if a.endswith(".js") else "text/css"))

    def test_refuses_what_is_not_the_build(self) -> None:
        for bad in ("/assets/../web.py", "/assets/%2e%2e/web.py", "/assets/.hidden.js", "/assets/missing.js", "/assets/",
                    "/old/assets/x.js", "/old/missing"):
            self.assertEqual(self.b.req("GET", bad)[0], 404, bad)

    def test_old_board_stays_at_old(self) -> None:
        code, body, _ = self.b.req("GET", "/old/")
        self.assertEqual(code, 200)
        self.assertEqual(body, BOARD.read_bytes())
        self.assertIn(rb"location.pathname.replace(/\/old\/[^/]*$/, '/')", body)
        self.assertIn(b'href="../manifest.webmanifest"', body)
        self.assertEqual(self.b.req("GET", "/old/index.html")[1], body)
        self.assertEqual(self._location("/old"), (302, "/old/"))
        self.assertNotEqual(self.b.req("GET", "/")[1], body)

    def test_the_api_the_app_reads(self) -> None:
        code, st, _ = self.b.req("GET", "/api/state")
        self.assertEqual(code, 200)
        for k in ("lane_cap", "runtimes", "default_runtime", "usage", "lanes", "items", "tasks", "reports", "memos", "counts",
                  "extensions", "local_only", "paused"):
            self.assertIn(k, st, k)
        self.assertTrue({"used", "cap", "max"} <= set(st["lane_cap"]))
        for ln in st["lanes"]:
            self.assertTrue({"lane", "label", "kind", "status", "runtime", "doing", "task_id", "ctx_k", "usage"} <= set(ln), ln)
        for kind in ("decided", "reports", "closed", "memos", "requests"):
            code, h, _ = self.b.req("GET", "/api/history?kind=" + kind)
            self.assertEqual(code, 200, kind)
            self.assertTrue({"rows", "more"} <= set(h), kind)
        code, ld, _ = self.b.req("GET", "/api/lanes/" + st["lanes"][0]["lane"])
        self.assertEqual(code, 200)
        self.assertTrue({"runtime", "status", "skills", "task", "process", "fixing", "days", "recent"} <= set(ld))
        self.assertEqual(self.b.req("GET", "/api/lanes/nope")[0], 404)
        code, m, _ = self.b.req("GET", "/api/machine")
        self.assertEqual(code, 200)
        self.assertIn("machine", m)
        self.assertEqual(self.b.req("GET", "/api/engines")[0], 200)
        code, pv, _ = self.b.req("GET", "/api/processes")
        self.assertEqual(code, 200)
        self.assertTrue({"full", "sub", "lanes", "problems"} <= set(pv))
        self.assertEqual(self.b.req("GET", "/api/workspaces")[0], 200)

    def test_the_build_calls_the_routes_it_needs(self) -> None:
        js = "".join((BOARD_NEXT / a).read_text(encoding="utf-8") for a in self.assets if a.endswith(".js"))
        for route in ("api/state", "api/machine", "api/history?kind=", "api/lanes/", "api/processes", "api/processes/doc?path=",
                      "api/items/", "/decide", "api/memo", "api/settings", "api/login", "api/logout", "api/workspaces", "api/me/prefs"):
            self.assertIn(route, js, route)
        for gone in ("api/uploads", "api/x/lane-rows"):
            self.assertNotIn(gone, js, gone)

    def test_extension_contract_is_in_the_build(self) -> None:
        js = "".join((BOARD_NEXT / a).read_text(encoding="utf-8") for a in self.assets if a.endswith(".js"))
        for name in ("addSection", "addTab", "onState", "extra.js", "extra.css", "window.dah", "dah"):
            self.assertIn(name.replace("window.", ""), js, name)

    def test_one_dictionary_per_language_with_the_same_keys(self) -> None:
        en = json.loads((APP / "src/i18n/en.json").read_text(encoding="utf-8"))
        for code in LANGS:
            d = json.loads((APP / f"src/i18n/{code}.json").read_text(encoding="utf-8"))
            self.assertEqual(flat(en) ^ flat(d), set(), code)

    def test_every_dictionary_string_the_old_board_has_is_in_the_app(self) -> None:
        old = BOARD.read_text(encoding="utf-8")
        ui = old[old.index("const UI = {"):old.index("const LANGS =")]
        en = json.loads((APP / "src/i18n/en.json").read_text(encoding="utf-8"))
        for key in ("sec.yours", "sec.handling", "btn.answer", "btn.done", "btn.hold", "label.hubSide", "lv.week", "eg.usage",
                    "mc.title", "use.today", "x.err", "msg.held", "chat.title"):
            self.assertIn(key, flat(en), key)
        self.assertIn("tabs: { hub:", ui)
        self.assertNotIn("chat.open", flat(en))

    def test_recommended_option_reaches_the_card_sheet(self) -> None:
        from agent_hub.config import INFRA
        from agent_hub.store import HUMAN
        iid = self.b.s.add_item(INFRA, None, "decision", "Pick", "## a\nb", [{"label": "A", "desc": "a"},
                                {"label": "B", "desc": "b", "recommended": True}], [], audience=HUMAN)
        _, st, _ = self.b.req("GET", "/api/state")
        it = next(i for i in st["items"] if i["id"] == iid)
        self.assertEqual([o.get("recommended") for o in it["options"]], [None, True])
        js = "".join((BOARD_NEXT / a).read_text(encoding="utf-8") for a in self.assets if a.endswith(".js"))
        self.assertIn(".recommended", js)


class UrlBuilderTest(unittest.TestCase):
    def run_node(self, script: str) -> str:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        path = APP / "src" / "lib" / "path.ts"
        code = f"import {{ joinBase, relPath, encodePath }} from {json.dumps(path.as_uri())};\n{script}"
        p = subprocess.run([node, "--experimental-strip-types", "--input-type=module", "-e", code], capture_output=True,
                           text=True, encoding="utf-8")
        if p.returncode:
            self.skipTest("this node cannot run TypeScript: " + p.stderr[-200:])
        return p.stdout.strip()

    def test_extension_calls_are_not_prefixed_twice(self) -> None:
        out = json.loads(self.run_node(
            "console.log(JSON.stringify([joinBase('/', 'api/x/stats'), joinBase('/w/s/', relPath('/api/x/stats')), "
            "joinBase('/w/s/', 'api/x/stats'), joinBase('/', encodePath('a b/c#1/d?.png'))]));"))
        self.assertEqual(out, ["/api/x/stats", "/w/s/api/x/stats", "/w/s/api/x/stats", "/a%20b/c%231/d%3F.png"])

    def test_registry_hands_the_api_a_path_without_the_prefix(self) -> None:
        reg = (APP / "src" / "ext" / "registry.ts").read_text(encoding="utf-8")
        self.assertIn("get: (p: string) => get(relPath(p))", reg)
        self.assertIn("post: (p: string, body?: unknown) => post(relPath(p), body)", reg)
        self.assertNotIn("BASE + ", reg.split("const API")[1])
        self.assertIn("joinBase(BASE, path)", (APP / "src" / "lib" / "api.ts").read_text(encoding="utf-8"))

    def test_no_empty_catch_blocks_in_the_app(self) -> None:
        for f in (APP / "src").rglob("*.ts*"):
            text = f.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"catch\s*(\([^)]*\))?\s*\{\s*(/\*.*?\*/)?\s*\}", text), f.name)

    def test_polling_sends_if_none_match_and_takes_a_304(self) -> None:
        api = (APP / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
        self.assertIn("'if-none-match'", api)
        self.assertIn("r.status === 304", api)
        js = "".join((BOARD_NEXT / a).read_text(encoding="utf-8") for a in re.findall(
            r'(?:src|href)="\./(assets/[^"]+\.js)"', (BOARD_NEXT / "index.html").read_text(encoding="utf-8")))
        self.assertIn("if-none-match", js)


class WorkspacePrefixTest(unittest.TestCase):

    def setUp(self) -> None:
        self.front = Board(name="Front")
        self.other = make_home(name="Other")
        self.slug = config_mod.register(self.other.home)

    def tearDown(self) -> None:
        self.front.srv.shutdown()
        config_mod.unregister(self.slug)

    def test_app_and_old_board_under_the_prefix(self) -> None:
        code, page, _ = self.front.req("GET", f"/w/{self.slug}/")
        self.assertEqual(code, 200)
        self.assertIn(b'<div id="root">', page)
        asset = re.findall(r'src="\./(assets/[^"]+)"', page.decode())[0]
        self.assertEqual(self.front.req("GET", f"/w/{self.slug}/{asset}")[0], 200)
        code, old, _ = self.front.req("GET", f"/w/{self.slug}/old/")
        self.assertEqual((code, old), (200, BOARD.read_bytes()))


if __name__ == "__main__":
    unittest.main()
