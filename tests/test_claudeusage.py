import json
import tempfile
import os
import threading
import unittest
from unittest import mock
import urllib.error
from email.message import Message
from pathlib import Path

from agent_hub import claudeusage
from agent_hub.claudeusage import ClaudeUsage
from agent_hub.config import ConfigError
from tests.helpers import Clock, make_home


class TokenTest(unittest.TestCase):
    def test_keychain_first_on_macos_then_the_credentials_file(self) -> None:
        home = Path(tempfile.mkdtemp())
        blob = json.dumps({"claudeAiOauth": {"accessToken": "from-file"}})
        (home / ".credentials.json").write_text(blob, encoding="utf-8")
        kc = json.dumps({"claudeAiOauth": {"accessToken": "from-keychain"}})
        self.assertEqual(claudeusage.token(home, "darwin", lambda: kc), "from-keychain")
        self.assertEqual(claudeusage.token(home, "darwin", lambda: ""), "from-file")
        self.assertEqual(claudeusage.token(home, "linux", lambda: kc), "from-file")
        self.assertEqual(claudeusage.token(Path(tempfile.mkdtemp()), "linux"), "")
        (home / ".credentials.json").write_text("not json", encoding="utf-8")
        self.assertEqual(claudeusage.token(home, "linux"), "")

    def test_the_credentials_file_is_only_read(self) -> None:
        home = Path(tempfile.mkdtemp())
        p = home / ".credentials.json"
        p.write_text(json.dumps({"accessToken": "t"}), encoding="utf-8")
        before = (p.read_bytes(), p.stat().st_mtime_ns)
        claudeusage.token(home, "linux")
        self.assertEqual((p.read_bytes(), p.stat().st_mtime_ns), before)


class ParseTest(unittest.TestCase):
    def test_limits_shape_and_the_older_shape(self) -> None:
        new = {"limits": [{"kind": "session", "percent": 42, "resets_at": "2026-11-13T10:00:00+00:00"},
                          {"kind": "weekly_all", "percent": 61.5}, {"kind": "weekly_scoped", "percent": 9}]}
        self.assertEqual([(w["name"], w["percent"]) for w in claudeusage.parse(new)], [("5h", 42.0), ("week", 61.5)])
        self.assertIsNotNone(claudeusage.parse(new)[0]["resets"])
        old = {"five_hour": {"utilization": 10}, "seven_day": {"utilization": 20, "resets_at": "2026-11-13T10:00:00Z"}}
        self.assertEqual([w["percent"] for w in claudeusage.parse(old)], [10.0, 20.0])
        self.assertEqual(claudeusage.parse({"limits": [{"kind": "session", "percent": "x"}]})[0]["percent"], None)


class ViewTest(unittest.TestCase):
    def test_success_is_cached(self) -> None:
        calls: list[str] = []
        clock = Clock(0.0)

        def get(tok: str) -> dict:
            calls.append(tok)
            return {"limits": [{"kind": "session", "percent": 5}]}
        u = ClaudeUsage(lambda: "tok", get, clock)
        self.assertEqual(u.view()["windows"][0]["percent"], 5.0)
        u.view()
        clock.tick(claudeusage.TTL + 1)
        u.view()
        self.assertEqual(len(calls), 2)

    def test_every_failure_is_unknown_never_zero_and_never_shows_the_token(self) -> None:
        def http(tok: str) -> dict:
            raise urllib.error.HTTPError(claudeusage.URL, 401, "no", Message(), None)

        def net(tok: str) -> dict:
            raise RuntimeError("curl exit 6")

        cases = [(lambda: "", lambda t: {}), (lambda: "secret-tok", http), (lambda: "secret-tok", net),
                 (lambda: "secret-tok", lambda t: {"something": "else"}), (lambda: "secret-tok", lambda t: [])]
        for tok, get in cases:
            v = ClaudeUsage(tok, get, Clock(0.0)).view()
            self.assertIn("unknown", v)
            self.assertNotIn("windows", v)
            self.assertNotIn("secret-tok", json.dumps(v))

    def test_off_by_default_and_only_known_providers(self) -> None:
        self.assertEqual(make_home().usage_percent, "")
        self.assertEqual(make_home(usage_percent="claude").usage_percent, "claude")
        with self.assertRaises(ConfigError):
            make_home(usage_percent="other")


class FetchTest(unittest.TestCase):
    def test_curl_gets_the_token_on_stdin_never_in_argv(self) -> None:
        seen: dict = {}

        def run(argv, **kw):
            seen["argv"], seen["input"] = argv, kw.get("input")
            return mock.Mock(returncode=0, stdout='{"limits": []}')
        with mock.patch("agent_hub.claudeusage.urllib.request.urlopen", side_effect=urllib.error.URLError("no CA")), \
                mock.patch("agent_hub.claudeusage.shutil.which", return_value="/usr/bin/curl"), \
                mock.patch("agent_hub.claudeusage.subprocess.run", run):
            self.assertEqual(claudeusage.fetch("secret-tok"), {"limits": []})
        self.assertFalse(any("secret-tok" in a for a in seen["argv"]))
        self.assertIn("--config", seen["argv"])
        self.assertIn("Bearer secret-tok", seen["input"])
        self.assertIn(claudeusage.URL, seen["input"])

    def test_a_quote_in_a_header_cannot_break_out_of_the_config_line(self) -> None:
        self.assertEqual(claudeusage._quote('a"b\\c\nd'), 'a\\"b\\\\c d')


class ConfigDirTest(unittest.TestCase):
    def test_claude_config_dir_is_honoured(self) -> None:
        home = Path(tempfile.mkdtemp())
        (home / ".credentials.json").write_text(json.dumps({"accessToken": "from-env-dir"}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(home)}):
            self.assertEqual(claudeusage.token(platform="linux"), "from-env-dir")


class LockTest(unittest.TestCase):
    def test_the_network_call_does_not_hold_the_lock(self) -> None:
        u = ClaudeUsage()
        held: list[bool] = []

        def get(tok: str) -> dict:
            held.append(u.lock.locked())
            return {"limits": [{"kind": "session", "percent": 5}]}
        u.get_token, u.get = (lambda: "tok"), get
        u.view()
        self.assertEqual(held, [False])

    def test_a_second_caller_during_a_fetch_does_not_wait_for_it(self) -> None:
        release, started = threading.Event(), threading.Event()

        def get(tok: str) -> dict:
            started.set()
            release.wait(5)
            return {"limits": [{"kind": "session", "percent": 5}]}
        u = ClaudeUsage(lambda: "tok", get, Clock(0.0))
        t = threading.Thread(target=u.view)
        t.start()
        started.wait(5)
        self.assertIn("unknown", u.view())
        release.set()
        t.join()
        self.assertIn("windows", u.view())


if __name__ == "__main__":
    unittest.main()
