import unittest
from pathlib import Path

from agent_hub.store import HUB
from agent_hub.web import BOARD_NEXT, MAX_BODY
from tests.test_web import POST, Board, raw

APP = Path(__file__).resolve().parent.parent / "board-app" / "src"


class ChatFullTextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Board()

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def test_long_request_and_reply_round_trip_whole(self) -> None:
        ask = "가" * 9000 + "\n둘째 줄 https://example.com/" + "a/" * 300
        code, body, _ = self.b.req("POST", "/api/memo", {"to": "hub", "text": ask}, POST)
        self.assertEqual(code, 200)
        answer = "답 " * 6000 + "\n```\n" + "x" * 500 + "\n```"
        self.b.s.reply_memo(body["id"], answer, HUB)
        _, st, _ = self.b.req("GET", "/api/state")
        m = next(x for x in st["memos"][HUB] if x["id"] == body["id"])
        self.assertEqual(m["text"], ask)
        self.assertEqual(m["replies"][-1]["text"], answer)
        _, page, _ = self.b.req("GET", "/api/history?kind=requests")
        row = next(r for r in page["rows"] if r["id"] == body["id"])
        self.assertEqual((row["text"], row["replies"][-1]["text"]), (ask, answer))

    def test_a_request_past_the_body_cap_is_refused(self) -> None:
        host = {"host": f"127.0.0.1:{self.b.srv.server_address[1]}", "x-tower": "1", "content-length": str(MAX_BODY + 1)}
        self.assertEqual(raw(self.b, "POST", "/api/memo", host)[0], 413)

    def test_history_rows_are_never_folded(self) -> None:
        memo = (APP / "components" / "history" / "MemoRow.tsx").read_text(encoding="utf-8")
        self.assertNotIn("Clamp", memo)
        self.assertIn('className="chat mt-1"', memo)
        css = (APP / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".prose.chat pre code { white-space: pre-wrap; }", css)

    def test_build_has_the_chat_wrap(self) -> None:
        css = "".join(p.read_text(encoding="utf-8") for p in (BOARD_NEXT / "assets").glob("*.css"))
        self.assertRegex(css, r"\.prose\.chat pre code\{white-space:pre-wrap\}")


if __name__ == "__main__":
    unittest.main()
