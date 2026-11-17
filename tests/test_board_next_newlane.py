import json
import unittest
from pathlib import Path

from agent_hub.web import BOARD_NEXT

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "board-app" / "src"
LANGS = ("en", "ko", "ja", "zh", "es")
MARK = "[New lane request]"


class NewLaneRequestTest(unittest.TestCase):
    def test_the_form_is_gone(self) -> None:
        self.assertFalse((APP / "lib" / "laneMemo.ts").exists())
        row = (APP / "components" / "lanes" / "LaneAdd.tsx").read_text(encoding="utf-8")
        self.assertNotIn("<input", row)
        self.assertNotIn("<textarea", row)
        for lang in LANGS:
            self.assertNotIn("lane", json.loads((APP / "i18n" / f"{lang}.json").read_text(encoding="utf-8")))

    def test_the_request_goes_through_the_chat_route_with_a_marker_line(self) -> None:
        store = (APP / "state" / "newLane.ts").read_text(encoding="utf-8")
        self.assertIn("post('api/memo', { to: 'hub', text:", store)
        self.assertIn(f"NEW_LANE_MARK = '{MARK}'", store)
        self.assertIn("`${NEW_LANE_MARK}\\n${body}` : NEW_LANE_MARK", store, "an empty text box still sends the marker")

    def test_the_strings_are_in_every_language_and_the_build(self) -> None:
        for lang in LANGS:
            nl = json.loads((APP / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))["nl"]
            self.assertEqual(sorted(nl), ["add", "hint", "label", "ph", "send", "sent", "title"], lang)
        js = "".join(p.read_text(encoding="utf-8") for p in (BOARD_NEXT / "assets").glob("*.js"))
        for s in (MARK, "Ask the hub", "허브에 요청", "ハブに依頼", "请求中枢", "Pedir al hub"):
            self.assertIn(s, js)

    def test_the_hub_template_knows_the_marker(self) -> None:
        hub = (ROOT / "agent_hub" / "templates" / "lanes" / "hub.md").read_text(encoding="utf-8")
        self.assertIn(MARK, hub)

    def test_the_request_reaches_the_only_hub_of_a_single_user_install(self) -> None:
        from tests.test_web import POST, Board
        b = Board()
        try:
            code, _, _ = b.req("POST", "/api/memo", {"to": "hub", "text": f"{MARK}\nrelease notes"}, POST)
            self.assertEqual(code, 200)
            self.assertEqual([m["text"].splitlines()[0] for m in b.s.pending("hub")], [MARK])
        finally:
            b.srv.shutdown()


if __name__ == "__main__":
    unittest.main()
