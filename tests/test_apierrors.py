"""API-error push (agent_hub/apierrors.py) from the StopFailure lines the lane hook writes, and its engine tick."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_hub import apierrors as A, hooks
from agent_hub.engine import Engine
from agent_hub.store import Store
from tests.helpers import Clock, FakeRuntime, make_home


def fail(error: str, details: str = "", event: str = "StopFailure") -> str:
    return json.dumps({"ts": 1.0, "event": event, "session_id": "s", "error": error, "error_details": details}) + "\n"


EXPIRED = fail("authentication_failed", "Claude Code credentials are unavailable or expired. Run `claude` to refresh them.")
REFRESH = fail("authentication_failed", "Claude token refresh is rate-limited; retry shortly.")
RATE = fail("rate_limit", "This request would exceed your account's rate limit. Please try again later.")
USAGE = fail("rate_limit", "Claude usage limit has been reached")
BILLING = fail("billing_error", "Your credit balance is too low")
SERVER = fail("server_error", "Internal server error")
OVERLOADED = fail("server_error", "API Error: 529 Overloaded")
NETWORK = fail("unknown", "TypeError: getaddrinfo ENOTFOUND api.anthropic.com")
STOP = fail("", "", event="Stop")


class Classify(unittest.TestCase):
    def test_kinds(self) -> None:
        def kind(line: str) -> str | None:
            ev = json.loads(line)
            hit = A.classify(f"{ev['error']} {ev['error_details']}")
            return hit[0] if hit else None
        self.assertEqual([kind(x) for x in (EXPIRED, RATE, USAGE, BILLING, SERVER)],
                         ["login", "rate", "usage", "billing", "server"])

    def test_errors_that_retry_by_themselves_are_ignored(self) -> None:
        for line in (REFRESH, OVERLOADED, NETWORK):
            ev = json.loads(line)
            self.assertIsNone(A.classify(f"{ev['error']} {ev['error_details']}"), line)
        self.assertIsNone(A.classify("prompt is too long"))

    def test_login_expired_says_what_to_do(self) -> None:
        msg = A.classify("authentication_failed")[1]  # type: ignore[index]
        self.assertIn("login expired", msg)
        self.assertIn("run `claude`", msg)
        self.assertIn("log in again", msg)


class Watch(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.now = 1000.0

    def append(self, lane: str, text: str | bytes) -> None:
        p = self.root / lane / "events.jsonl"
        p.parent.mkdir(exist_ok=True)
        with p.open("ab") as f:
            f.write(text.encode() if isinstance(text, str) else text)

    def watch(self) -> A.EventWatch:
        return A.EventWatch(self.root, lambda: self.now)

    def test_starts_at_the_end_then_pushes_each_kind_once_until_the_cool_down(self) -> None:
        self.append("docs", EXPIRED)  # before the tower started: old news
        w = self.watch()
        self.assertEqual(w.poll(), [])
        self.append("docs", EXPIRED + EXPIRED + REFRESH + OVERLOADED + STOP + "not json StopFailure\n")
        self.append("web", EXPIRED + RATE)
        got = w.poll()
        self.assertEqual([(k, lane) for k, lane, _ in got], [("login", "docs"), ("rate", "web")])
        self.now += A.COOLDOWN_S - 1
        self.append("docs", EXPIRED)
        self.assertEqual(w.poll(), [])
        self.now += 2
        self.append("docs", EXPIRED)
        self.assertEqual([k for k, _, _ in w.poll()], ["login"])

    def test_a_lane_file_created_later_is_read_from_the_top(self) -> None:
        w = self.watch()
        self.append("new", BILLING)
        self.assertEqual([(k, lane) for k, lane, _ in w.poll()], [("billing", "new")])

    def test_a_line_split_across_ticks_and_a_trimmed_file(self) -> None:
        w = self.watch()
        raw = BILLING.encode()
        self.append("docs", raw[:20])
        self.assertEqual(w.poll(), [])
        self.append("docs", raw[20:])
        self.assertEqual(len(w.poll()), 1)
        short = fail("rate_limit")
        self.assertLess(len(short), len(BILLING))
        (self.root / "docs" / "events.jsonl").write_text(short, encoding="utf-8")  # shorter than before: re-read
        self.assertEqual([k for k, _, _ in w.poll()], ["rate"])


class EngineTick(unittest.TestCase):
    def test_the_tick_pushes_an_alert_for_a_lane_that_lost_its_login(self) -> None:
        cfg = make_home({"hub": {"runtime": "claude"}, "docs": {"runtime": "claude"}})
        clock = Clock()
        s = Store(cfg.db, clock=clock)
        notes: list[list[dict]] = []
        e = Engine(cfg, s, FakeRuntime(s), notes.append, clock=clock, log=lambda _: None)
        e.tick()
        hooks.append_event(cfg.home, "docs", hooks.record({"hook_event_name": "StopFailure",
                                                            "error": "authentication_failed"}))
        e.tick()
        alerts = [n for batch in notes for n in batch if n.get("alert")]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["lane"], "docs")
        self.assertIn("login expired", alerts[0]["title"])
        e.tick()
        self.assertEqual(len([n for batch in notes for n in batch if n.get("alert")]), 1)


if __name__ == "__main__":
    unittest.main()
