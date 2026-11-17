from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_hub import accounts, claudelogin, procs
from agent_hub.store import Store
from tests.helpers import make_home
from tests.test_person_hubs import ROOT
from tests.test_web import POST, Board

URL = "https://claude.com/cai/oauth/authorize?code=true&state=s1"
GOOD, HALF, BAD, TOKEN = "good#state", "half#cod", "bad#state", "tok-secret"
FAKE = f'''#!/usr/bin/env python3
import json, os, sys
d = os.environ["CLAUDE_CONFIG_DIR"]
a = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({{"argv": a, "dir": d, "env": dict(os.environ)}}) + "\\n")
creds = os.path.join(d, ".credentials.json")
if a[:2] == ["auth", "login"]:
    print("Opening browser to sign in...")
    print("If the browser didn't open, visit: \\x1b]8;;{URL}\\x07{URL}\\x1b]8;;\\x07", flush=True)
    while True:
        print("Paste code here if prompted > ", end="", flush=True)
        line = sys.stdin.readline()
        if not line:
            sys.exit(1)
        code = line.strip()
        if code == "{GOOD}":
            open(creds, "w").write('{{"accessToken": "{TOKEN}"}}')
            print("Login successful.")
            sys.exit(0)
        if code == "{BAD}":
            print("OAuth error: invalid code " + code)
            sys.exit(1)
        print("That doesn't look like the full code. " + code)
elif a[:2] == ["auth", "status"]:
    on = os.path.exists(creds)
    print(json.dumps({{"loggedIn": on, "email": "ana@x.com" if on else None, "subscriptionType": "pro" if on else None,
                      "accessToken": "{TOKEN}", "configDirectory": d}}))
    sys.exit(0 if on else 1)
elif a[:2] == ["auth", "logout"]:
    if os.path.exists(creds):
        os.remove(creds)
'''


class Fixture:
    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.root, self.default, self.log = t / "accounts", t / "dot-claude", t / "fake.log"
        self.default.mkdir()
        (self.default / ".credentials.json").write_text('{"default": "tok-default"}')
        (self.default / "settings.json").write_text("{}")
        (self.default / "skills").mkdir()
        (self.default / "projects").mkdir()
        (self.default / "backups").mkdir()
        self.default_json = t / "dot-claude.json"
        self.default_json.write_text(json.dumps({
            "mcpServers": {"docs-memory": {"type": "http", "url": "http://127.0.0.1:8796/mcp"}},
            "oauthAccount": {"emailAddress": "default@x.com"}, "userID": "u1",
            "projects": {"/w": {"hasTrustDialogAccepted": True, "history": ["x"]}, "/v": {}}}))
        fake = t / "claude"
        fake.write_text(FAKE)
        fake.chmod(0o755)
        self.patches = [mock.patch.object(accounts, "root_dir", lambda root=None: root or self.root),
                        mock.patch.object(claudelogin, "default_dir", lambda: self.default),
                        mock.patch.object(claudelogin, "default_json", lambda: self.default_json),
                        mock.patch.object(claudelogin, "CLAUDE", str(fake)),
                        mock.patch.dict(os.environ, {"FAKE_LOG": str(self.log), "ANTHROPIC_API_KEY": "sk-shared",
                                                     "CLAUDE_CODE_OAUTH_TOKEN": "oauth-shared"})]
        for p in self.patches:
            p.start()

    def calls(self) -> list[dict]:
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def close(self) -> None:
        for m in list(claudelogin._PENDING):
            claudelogin._drop(m)
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()


@unittest.skipIf(procs.WINDOWS, "the stand-in claude is a script with a shebang: it cannot run on Windows")
class ApiTest(unittest.TestCase):
    H = "X-Forwarded-Email"

    def setUp(self) -> None:
        self.f = Fixture()
        self.lines: list[str] = []
        self.b = Board(log=self.lines.append, auth={"mode": "header", "header": self.H}, claude_accounts=True)
        self.b.s.add_member("ana", "owner", "ana@x.com")
        self.b.s.add_member("bo", "member", "bo@x.com")
        self.ana = {**POST, self.H: "ana@x.com"}
        self.bo = {**POST, self.H: "bo@x.com"}

    def tearDown(self) -> None:
        self.b.srv.shutdown()
        self.f.close()

    def act(self, who: dict, action: str, **kw):
        return self.b.req("POST", "/api/me/claude", {"action": action, **kw}, who)

    def test_connect_with_a_pasted_code_then_disconnect(self) -> None:
        code, out, _ = self.b.req("GET", "/api/me/claude", headers=self.ana)
        self.assertEqual((code, out), (200, {"state": "none"}))
        code, out, _ = self.act(self.ana, "start")
        self.assertEqual((code, out["state"], out["url"]), (200, "pending", URL))
        self.assertEqual(self.b.req("GET", "/api/me/claude", headers=self.ana)[1]["url"], URL)
        self.assertEqual(self.act(self.ana, "code", code="no hash")[1]["why"], "bad_code")
        code, out, _ = self.act(self.ana, "code", code=HALF)
        self.assertEqual((code, out["why"]), (400, "bad_code"))
        self.assertEqual(self.b.req("GET", "/api/me/claude", headers=self.ana)[1]["state"], "pending")
        code, out, _ = self.act(self.ana, "code", code=GOOD)
        self.assertEqual((code, out), (200, {"state": "connected", "email": "ana@x.com", "plan": "pro"}))

        d = self.f.root / "ana"
        self.assertEqual(accounts.config_dir("ana"), d)
        self.assertTrue((d / "skills").is_symlink() and (d / "settings.json").is_symlink())
        self.assertFalse((d / ".credentials.json").is_symlink())
        self.assertFalse((d / "backups").exists())
        cj = json.loads((d / ".claude.json").read_text())
        self.assertEqual(list(cj["mcpServers"]), ["docs-memory"])
        self.assertEqual(cj["projects"], {"/w": {"hasTrustDialogAccepted": True}})
        self.assertNotIn("oauthAccount", cj)

        self.assertEqual(self.act(self.ana, "disconnect")[1], {"state": "none", "ended": True})
        self.assertFalse(d.exists())
        self.assertIsNone(accounts.config_dir("ana"))
        self.assertEqual(self.act(self.ana, "status")[1], {"state": "none", "ended": True})
        self.assertTrue((self.f.default / "skills").is_dir())
        self.assertEqual((self.f.default / ".credentials.json").read_text(), '{"default": "tok-default"}')

        calls = self.f.calls()
        self.assertTrue(calls)
        self.assertTrue(all(Path(c["dir"]).parent == self.f.root for c in calls))
        login = next(c for c in calls if c["argv"][:2] == ["auth", "login"])
        self.assertEqual(login["argv"][-2:], ["--email", "ana@x.com"])

    def test_the_account_folder_and_its_files_are_private(self) -> None:
        self.act(self.ana, "start")
        self.act(self.ana, "code", code=GOOD)
        d = self.f.root / "ana"
        self.assertEqual(stat.S_IMODE(self.f.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((d / ".claude.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((d / ".credentials.json").stat().st_mode), 0o600)

    def test_the_code_and_tokens_never_reach_argv_env_store_log_or_answers(self) -> None:
        self.act(self.ana, "start")
        bodies = [self.act(self.ana, "code", code=HALF)[1], self.act(self.ana, "code", code=GOOD)[1],
                  self.b.req("GET", "/api/me/claude", headers=self.ana)[1], self.b.req("GET", "/api/state", headers=self.ana)[1]]
        calls = json.dumps(self.f.calls())
        text = json.dumps(bodies) + "\n".join(self.lines) + calls
        for secret in (GOOD, HALF, TOKEN):
            self.assertNotIn(secret, text)
        for secret in (GOOD.encode(), HALF.encode(), TOKEN.encode()):
            self.assertNotIn(secret, self.b.cfg.db.read_bytes())
        for call in self.f.calls():
            self.assertNotIn(GOOD, " ".join(call["argv"]))
            self.assertEqual(call["env"]["CLAUDE_CONFIG_DIR"], call["dir"])

    def test_a_refused_code_is_not_echoed_in_the_answer_or_the_log(self) -> None:
        self.act(self.ana, "start")
        code, out, _ = self.act(self.ana, "code", code=BAD)
        self.assertEqual((code, out["why"]), (502, "failed"))
        self.assertNotIn(BAD, json.dumps(out) + "\n".join(self.lines))
        with self.assertRaises(claudelogin.LoginError) as raised:
            claudelogin.submit("ana", BAD)
        self.assertNotIn(BAD, str(raised.exception) + raised.exception.why)

    def test_the_login_process_gets_no_other_sign_in_variable(self) -> None:
        self.act(self.ana, "start")
        env = self.f.calls()[-1]["env"]
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(self.f.root / "ana"))
        self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "sk-shared")
        self.assertNotEqual(os.environ.get("CLAUDE_CONFIG_DIR"), str(self.f.root / "ana"))

    def test_a_refused_code_ends_the_sign_in(self) -> None:
        self.act(self.ana, "start")
        code, out, _ = self.act(self.ana, "code", code=BAD)
        self.assertEqual((code, out["why"]), (502, "failed"))
        self.assertEqual(self.b.req("GET", "/api/me/claude", headers=self.ana)[1], {"state": "none"})
        self.assertIsNone(accounts.config_dir("ana"))

    def test_each_person_sees_only_their_own(self) -> None:
        self.act(self.ana, "start")
        self.assertEqual(self.b.req("GET", "/api/me/claude", headers=self.bo)[1], {"state": "none"})
        self.assertEqual(self.act(self.bo, "code", code=GOOD)[1]["why"], "no_login")
        self.assertEqual(self.act(self.ana, "code", code=GOOD, member="bo")[1]["state"], "connected")
        self.assertFalse((self.f.root / "bo").exists())
        self.assertEqual(self.act(self.bo, "disconnect", member="ana")[1], {"state": "none", "ended": True})
        self.assertTrue((self.f.root / "ana" / ".credentials.json").is_file())
        self.assertEqual(self.b.req("GET", "/api/me/claude?member=ana", headers=self.bo)[1], {"state": "none", "ended": True})

    def test_the_owner_sees_who_is_connected_and_a_member_does_not(self) -> None:
        self.act(self.ana, "start")
        self.act(self.ana, "code", code=GOOD)
        owner = self.b.req("GET", "/api/state", headers=self.ana)[1]
        member = self.b.req("GET", "/api/state", headers=self.bo)[1]
        self.assertEqual(owner["accounts"], [{"member": "ana", "state": "connected"}, {"member": "bo", "state": "none"}])
        self.assertIsNone(member["accounts"])
        self.assertNotIn(TOKEN, json.dumps(owner))

    def test_connected_refuses_a_second_start_and_cancel_stops_it(self) -> None:
        self.act(self.ana, "start")
        self.act(self.ana, "code", code=GOOD)
        self.assertEqual(self.act(self.ana, "start")[1]["why"], "already_connected")
        self.act(self.bo, "start")
        proc = claudelogin._PENDING["bo"].proc
        self.assertEqual(self.act(self.bo, "cancel")[1], {"state": "none"})
        self.assertNotIn("bo", claudelogin._PENDING)
        self.assertIsNotNone(proc.poll())

    def test_a_forgotten_sign_in_stops_by_itself(self) -> None:
        with mock.patch.object(claudelogin, "LOGIN_TTL", 0.3):
            self.act(self.ana, "start")
            time.sleep(0.8)
        self.assertEqual(self.b.req("GET", "/api/me/claude", headers=self.ana)[1], {"state": "none"})

    def test_a_cli_that_gives_no_address_is_a_reason_word(self) -> None:
        with mock.patch.object(claudelogin, "CLAUDE", "no-such-claude-binary"):
            code, out, _ = self.act(self.ana, "start")
        self.assertEqual((code, out["why"]), (500, "no_cli"))

    def test_unknown_action(self) -> None:
        self.assertEqual(self.act(self.ana, "steal")[0], 400)


class OffAndUnknownTest(unittest.TestCase):
    def test_off_is_a_404_for_every_verb_and_state_says_so(self) -> None:
        b = Board(auth={"mode": "header", "header": "X-U"})
        try:
            b.s.add_member("ana", "owner", "ana@x.com")
            h = {"X-U": "ana@x.com", **POST}
            self.assertEqual(b.req("GET", "/api/me/claude", headers=h)[0], 404)
            self.assertEqual(b.req("POST", "/api/me/claude", {"action": "start"}, h)[0], 404)
            st = b.req("GET", "/api/state", headers=h)[1]
            self.assertFalse(st["claude_accounts"])
            self.assertIsNone(st["accounts"])
        finally:
            b.srv.shutdown()

    def test_a_signed_in_person_who_is_not_a_member_is_refused(self) -> None:
        f = Fixture()
        b = Board(auth={"mode": "header", "header": "X-U"}, claude_accounts=True)
        try:
            code, out, _ = b.req("GET", "/api/me/claude", headers={"X-U": "stranger@x.com"})
            self.assertEqual((code, out["why"]), (403, "not_member"))
            self.assertEqual(f.calls(), [])
        finally:
            b.srv.shutdown()
            f.close()


@unittest.skipIf(procs.WINDOWS, "the stand-in claude is a script with a shebang: it cannot run on Windows")
class RemoveMemberTest(unittest.TestCase):
    def test_removing_a_member_signs_out_and_deletes_their_folder(self) -> None:
        cfg = make_home(claude_accounts=True, auth={"mode": "token"})
        s = Store(cfg.db)
        s.add_member("ana", "member", None, True)
        s.add_member("bo", "member", None, True)
        with tempfile.TemporaryDirectory() as tmp:
            home, bin_dir = Path(tmp) / "home", Path(tmp) / "bin"
            bin_dir.mkdir()
            home.mkdir()
            fake = bin_dir / "claude"
            fake.write_text(FAKE)
            fake.chmod(0o755)
            for who in ("ana", "bo"):
                d = home / ".claude-accounts" / who
                d.mkdir(parents=True)
                (d / ".credentials.json").write_text(json.dumps({"accessToken": TOKEN}))
            env = {**os.environ, "HOME": str(home), "TOWER_HOME": str(cfg.home), "PYTHONPATH": str(ROOT),
                   "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": str(Path(tmp) / "fake.log")}
            p = subprocess.run([sys.executable, "-m", "agent_hub", "member", "remove", "ana"], cwd=cfg.home, env=env,
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertFalse((home / ".claude-accounts" / "ana").exists())
            self.assertTrue((home / ".claude-accounts" / "bo" / ".credentials.json").is_file())
            self.assertNotIn(TOKEN, p.stdout + p.stderr)
            logout = [json.loads(x)["argv"] for x in (Path(tmp) / "fake.log").read_text().splitlines()]
            self.assertEqual(logout, [["auth", "logout"]])


class FolderTest(unittest.TestCase):
    def test_never_the_default_folder(self) -> None:
        f = Fixture()
        try:
            with mock.patch.object(claudelogin, "default_dir", lambda: f.root / "ana"):
                (f.root / "ana").mkdir(parents=True)
                with self.assertRaises(claudelogin.LoginError):
                    claudelogin._env(f.root / "ana")
            with self.assertRaises(claudelogin.LoginError):
                claudelogin._env(f.default)
            with self.assertRaises(claudelogin.LoginError):
                claudelogin.account_dir("")
        finally:
            f.close()

    def test_any_member_name_maps_to_one_folder_inside_the_accounts_root(self) -> None:
        f = Fixture()
        try:
            seen = {}
            for name in ("ana", "Ana", "../x", "a/b", ".hidden", "홍길동", "bo@x.com", "김철수"):
                d = claudelogin.account_dir(name)
                self.assertEqual(d.parent, f.root, name)
                self.assertNotIn(d.name, seen, name)
                seen[d.name] = name
            self.assertEqual(claudelogin.account_dir("ana").name, "ana")
        finally:
            f.close()

    def test_the_default_account_follows_claude_config_dir(self) -> None:
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/srv/claude"}):
            self.assertEqual(claudelogin.default_dir(), Path("/srv/claude"))
            self.assertEqual(claudelogin.default_json(), Path("/srv/claude/.claude.json"))


if __name__ == "__main__":
    unittest.main()
