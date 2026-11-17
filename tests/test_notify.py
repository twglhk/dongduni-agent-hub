import json
import re
import ssl
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from unittest import mock

from agent_hub import notify
from tests.helpers import make_home

ITEM = [{"id": 3, "title": "Port?"}]


def hits(status: int) -> tuple[HTTPServer, list[bytes]]:
    got: list[bytes] = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            got.append(self.rfile.read(int(self.headers["content-length"])))
            self.send_response(status)
            self.send_header("content-length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, got


class PostTest(unittest.TestCase):
    def test_an_http_error_is_logged_and_not_resent_through_curl(self) -> None:
        srv, got = hits(500)
        logs: list[str] = []
        try:
            with mock.patch("agent_hub.notify.subprocess.run") as run, mock.patch("agent_hub.notify.shutil.which", return_value="/usr/bin/curl"):
                notify._post(f"http://127.0.0.1:{srv.server_address[1]}/", {"text": "hi"}, logs.append)
            run.assert_not_called()
        finally:
            srv.shutdown()
        self.assertEqual(len(got), 1)
        self.assertIn("notify failed", logs[0])

    def test_a_certificate_error_falls_back_to_curl_with_the_url_and_body_on_stdin(self) -> None:
        err = urllib.error.URLError(ssl.SSLCertVerificationError("no CA"))
        done = mock.Mock(returncode=0, stderr="")
        with mock.patch("agent_hub.notify.urllib.request.urlopen", side_effect=err), \
                mock.patch("agent_hub.notify.shutil.which", return_value="/usr/bin/curl"), \
                mock.patch("agent_hub.notify.subprocess.run", return_value=done) as run:
            notify._post("https://hooks.example.com/SECRET", {"text": 'say "hi"\nthere é'}, print)
        argv = run.call_args.args[0]
        self.assertEqual(argv, ["curl", "--config", "-"])
        self.assertNotIn("SECRET", " ".join(argv))
        cfg = run.call_args.kwargs["input"]
        self.assertIn('url = "https://hooks.example.com/SECRET"', cfg)
        data = next(line for line in cfg.splitlines() if line.startswith("data = "))
        quoted = data[len("data = "):][1:-1]
        raw = json.loads(re.sub(r"\\(.)", lambda m: {"n": "\n", "r": "\r"}.get(m.group(1), m.group(1)), quoted))
        self.assertEqual(raw["text"], 'say "hi"\nthere é')

    def test_other_network_errors_are_not_retried_with_curl(self) -> None:
        with mock.patch("agent_hub.notify.urllib.request.urlopen", side_effect=TimeoutError("slow")), \
                mock.patch("agent_hub.notify.shutil.which", return_value="/usr/bin/curl"), \
                mock.patch("agent_hub.notify.subprocess.run") as run:
            notify._post("https://x.example/", {"text": "hi"}, lambda _: None)
        run.assert_not_called()


class PushTest(unittest.TestCase):
    def test_sending_happens_off_the_calling_thread(self) -> None:
        cfg = make_home(notify={"slack_webhook": "https://hooks.example.com/x"})
        started, release, caller = threading.Event(), threading.Event(), threading.get_ident()
        seen: list[int] = []

        def slow(url: str, body: dict[str, Any], log: Any) -> None:
            seen.append(threading.get_ident())
            started.set()
            release.wait(5)

        before = set(threading.enumerate())
        with mock.patch("agent_hub.notify._post", slow):
            notify.notifier(cfg)(ITEM)
            self.assertTrue(started.wait(5))
            self.assertNotEqual(seen[0], caller)
            release.set()
            for sender in set(threading.enumerate()) - before:
                sender.join(5)

    def test_the_macos_text_keeps_non_ascii_characters(self) -> None:
        with mock.patch("agent_hub.notify.sys.platform", "darwin"), mock.patch("agent_hub.notify.subprocess.run") as run:
            notify._desktop("제목", "한글 \"인용\"")
        script = run.call_args.args[0][2]
        self.assertIn("제목", script)
        self.assertNotIn("\\u", script)
