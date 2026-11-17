import json
import os
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

from agent_hub import prefs
from agent_hub.store import SCHEMA, Store
from tests.helpers import make_home
from tests.test_person_hubs import with_members
from tests.test_web import POST, Board

ROOT = Path(__file__).resolve().parents[1]


class CleanTest(unittest.TestCase):
    def test_keeps_known_values_and_drops_the_rest(self) -> None:
        got, dropped = prefs.clean({"v": 1, "first_tab": "history", "font": "xl", "accent": "pink", "css": "x",
                                    "sections": {"hub": {"order": ["yours", "Bad Id", "yours"], "x": []},
                                                 "info": {"order": []}}})
        self.assertEqual(got, {"first_tab": "history", "accent": "pink", "sections": {"hub": {"order": ["yours"]}}})
        self.assertEqual(sorted(dropped), sorted(["font", "css", "sections.hub.order['Bad Id']",
                                                  "sections.hub.order['yours']", "sections.hub.x", "sections.info"]))

    def test_not_an_object(self) -> None:
        self.assertEqual(prefs.clean([1]), ({}, ["prefs"]))

    def test_names_that_are_not_members_get_no_settings(self) -> None:
        for bad in ("bo@x.com", "../ana", "", "a/b", ".hidden"):
            with self.assertRaises(prefs.NotMember):
                prefs.check_name(bad)

    def test_seen_keeps_known_tips_only(self) -> None:
        self.assertEqual(prefs.clean({"seen": ["prefs", "x"]}), ({"seen": ["prefs"]}, ["seen['x']"]))

    def test_the_sections_match_the_board(self) -> None:
        text = (ROOT / "board-app" / "src" / "lib" / "prefs.ts").read_text(encoding="utf-8")
        for tab, ids in prefs.SECTIONS.items():
            self.assertIn(f"{tab}: [{', '.join(repr(i) for i in ids)}]", text)
        for font in prefs.FONTS:
            self.assertIn(repr(font), text)
        for accent in prefs.ACCENTS:
            self.assertIn(repr(accent), text)


class PairsTest(unittest.TestCase):
    def test_words_become_a_change(self) -> None:
        self.assertEqual(prefs.parse_pairs(["font=l", "accent=", "hub.order=yours,asks", "lanes.hidden="]),
                         {"font": "l", "accent": None, "sections": {"hub": {"order": ["yours", "asks"]},
                                                                    "lanes": {"hidden": []}}})

    def test_typos_are_refused(self) -> None:
        for bad in (["font"], ["font=xl"], ["colour=red"], ["hub.order=nope"], ["info.order=x"], ["hub.wide=asks"]):
            with self.assertRaises(prefs.PrefsError, msg=bad):
                prefs.parse_pairs(bad)


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.s = Store(make_home().db)

    def test_save_merges_and_null_goes_back_to_the_default(self) -> None:
        prefs.save(self.s, "ana", {"first_tab": "lanes", "font": "l"})
        saved, dropped = prefs.save(self.s, "ana", {"font": None, "accent": "neon",
                                                    "sections": {"hub": {"collapsed": ["yours"]}}})
        self.assertEqual(saved, {"first_tab": "lanes", "sections": {"hub": {"collapsed": ["yours"]}}})
        self.assertEqual(dropped, ["accent"])
        self.assertEqual(json.loads(self.s.member_prefs("ana"))["v"], prefs.VERSION)

    def test_reset_keeps_the_tips_read(self) -> None:
        prefs.save(self.s, "ana", {"seen": ["prefs"], "font": "l"})
        prefs.reset(self.s, "ana")
        self.assertEqual(prefs.load(self.s, "ana"), {"seen": ["prefs"]})
        self.s.drop_member_prefs("ana")
        self.assertEqual(prefs.load(self.s, "ana"), {})

    def test_a_broken_row_reads_as_the_defaults(self) -> None:
        self.s.put_member_prefs("ana", "{not json")
        self.assertEqual(prefs.view(self.s, "ana")["prefs"], prefs.DEFAULTS)

    def test_a_removed_member_takes_their_settings_along(self) -> None:
        self.s.add_member("ana")
        prefs.save(self.s, "ana", {"font": "l"})
        self.s.remove_member("ana")
        self.assertIsNone(self.s.member_prefs("ana"))

    def test_an_old_database_gets_the_table(self) -> None:
        path = make_home().db
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.executescript(SCHEMA.replace("CREATE TABLE IF NOT EXISTS member_prefs(", "CREATE TABLE IF NOT EXISTS unused("))
        con.commit()
        con.close()
        s = Store(path)
        prefs.save(s, "ana", {"font": "s"})
        self.assertEqual(prefs.load(s, "ana"), {"font": "s"})


class ApiTest(unittest.TestCase):
    H = "X-Forwarded-Email"

    def setUp(self) -> None:
        self.b = Board(auth={"mode": "header", "header": self.H})
        self.b.s.add_member("ana", "owner", "ana@x.com")
        self.b.s.add_member("bo", "member", "bo@x.com")
        self.ana = {self.H: "ana@x.com"}
        self.bo = {self.H: "bo@x.com"}

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def test_defaults_before_anything_is_saved(self) -> None:
        code, out, _ = self.b.req("GET", "/api/me/prefs", headers=self.ana)
        self.assertEqual(code, 200)
        self.assertEqual(out["prefs"], prefs.DEFAULTS)
        self.assertEqual(out["saved"], {})
        self.assertIn("engine", out["choices"]["first_tab"])
        self.assertIsNone(self.b.s.member_prefs("ana"))

    def test_save_merge_and_null_back_to_default(self) -> None:
        code, out, _ = self.b.req("POST", "/api/me/prefs", {"prefs": {"first_tab": "lanes", "font": "l"}},
                                  {**POST, **self.ana})
        self.assertEqual((code, out["prefs"]["first_tab"], out["prefs"]["font"], out["dropped"]), (200, "lanes", "l", []))
        _, out, _ = self.b.req("POST", "/api/me/prefs",
                               {"prefs": {"font": None, "accent": "neon", "sections": {"hub": {"collapsed": ["yours"]}}}},
                               {**POST, **self.ana})
        self.assertEqual(out["saved"], {"first_tab": "lanes", "sections": {"hub": {"collapsed": ["yours"]}}})
        self.assertEqual(out["prefs"]["font"], "m")
        self.assertEqual(out["dropped"], ["accent"])

    def test_each_person_sees_and_writes_only_their_own(self) -> None:
        self.b.req("POST", "/api/me/prefs", {"prefs": {"accent": "green"}}, {**POST, **self.ana})
        _, out, _ = self.b.req("GET", "/api/me/prefs", headers=self.bo)
        self.assertEqual(out["saved"], {})
        self.assertEqual(self.b.req("GET", "/api/me/prefs/ana", headers=self.bo)[0], 404)
        self.b.req("POST", "/api/me/prefs", {"prefs": {"font": "s"}, "member": "ana"}, {**POST, **self.bo})
        self.assertEqual(prefs.load(self.b.s, "ana"), {"accent": "green"})
        self.assertEqual(prefs.load(self.b.s, "bo"), {"font": "s"})

    def test_reset(self) -> None:
        self.b.req("POST", "/api/me/prefs", {"prefs": {"accent": "green"}}, {**POST, **self.ana})
        code, out, _ = self.b.req("POST", "/api/me/prefs", {"reset": True}, {**POST, **self.ana})
        self.assertEqual((code, out["saved"]), (200, {}))
        self.assertIsNone(self.b.s.member_prefs("ana"))

    def test_refusals(self) -> None:
        self.assertEqual(self.b.req("GET", "/api/me/prefs")[0], 401)
        self.assertEqual(self.b.req("POST", "/api/me/prefs", {"prefs": []}, {**POST, **self.ana})[0], 400)
        self.assertEqual(self.b.req("POST", "/api/me/prefs", {"prefs": {}}, self.ana)[0], 403)

    def test_a_visitor_named_by_email_is_refused(self) -> None:
        code, _, _ = self.b.req("GET", "/api/me/prefs", headers={self.H: "zed@x.com"})
        self.assertEqual(code, 401)


class NonMemberTest(unittest.TestCase):
    def test_a_visitor_without_a_member_row_is_refused(self) -> None:
        b = Board(auth={"mode": "header", "header": "X-Forwarded-Email"})
        try:
            code, _, _ = b.req("GET", "/api/me/prefs", headers={"X-Forwarded-Email": "bo@x.com"})
            self.assertEqual(code, 403)
        finally:
            b.srv.shutdown()


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = with_members(make_home(), "ana", "bo")
        self.home = self.cfg.home
        self.s = Store(self.cfg.db)

    def dah(self, *args: str, lane: str, ok: bool = True) -> str:
        env = {**os.environ, "TOWER_HOME": str(self.home), "PYTHONPATH": str(ROOT), "TOWER_LANE": lane}
        p = subprocess.run([sys.executable, "-m", "agent_hub", *args], cwd=self.home, env=env, capture_output=True,
                           text=True, encoding="utf-8")
        self.assertEqual(p.returncode == 0, ok, p.stdout + p.stderr)
        return p.stdout + p.stderr

    def test_a_person_hub_sets_shows_and_resets_its_own_person(self) -> None:
        out = self.dah("prefs", "set", "ana", "first_tab=history", "font=l", "hub.collapsed=yours", lane="hub-ana")
        self.assertIn("30 seconds", out)
        self.assertEqual(prefs.load(self.s, "ana"), {"first_tab": "history", "font": "l",
                                                     "sections": {"hub": {"collapsed": ["yours"]}}})
        self.dah("prefs", "set", "ana", "font=", lane="hub-ana")
        self.assertNotIn("font", prefs.load(self.s, "ana"))
        self.assertIn('"first_tab": "history"', self.dah("prefs", "show", "ana", lane="hub-ana"))
        self.dah("prefs", "reset", "ana", lane="hub-ana")
        self.assertEqual(prefs.load(self.s, "ana"), {})

    def test_only_that_persons_hub(self) -> None:
        self.assertIn("only that hub", self.dah("prefs", "set", "bo", "font=l", lane="hub-ana", ok=False))
        self.assertIn("only that hub", self.dah("prefs", "set", "ana", "font=l", lane="hub", ok=False))
        self.assertIn("only hub", self.dah("prefs", "set", "ana", "font=l", lane="infra", ok=False))
        self.assertIn("no member", self.dah("prefs", "show", "zed", lane="hub", ok=False))
        self.assertIn("choose", self.dah("prefs", "set", "ana", "font=xl", lane="hub-ana", ok=False))
        self.assertIsNone(self.s.member_prefs("bo"))

    def test_without_person_hubs_the_hub_serves_every_member(self) -> None:
        cfg = with_members(make_home(), "ana", cap=None)
        self.home = cfg.home
        self.dah("prefs", "set", "ana", "accent=green", lane="hub")
        self.assertEqual(prefs.load(Store(cfg.db), "ana"), {"accent": "green"})


if __name__ == "__main__":
    unittest.main()
