import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_hub import sessinfo
from agent_hub.cli import TowerError, send_infra
from agent_hub.engine import Engine
from agent_hub.store import HUB, INFRA, Store
from tests.helpers import Clock, FakeRuntime, make_home


def turn(model: str, effort: str | None) -> str:
    e = {"type": "assistant", "message": {"model": model, "content": [{"type": "text", "text": "x" * 50}]}}
    if effort:
        e["effort"] = effort
    return json.dumps(e)


class TranscriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sessinfo-"))
        (self.root / "-proj").mkdir()

    def write(self, sid: str, lines: list[str]) -> None:
        (self.root / "-proj" / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_label(self) -> None:
        self.assertEqual(sessinfo.label("claude-opus-5-5"), "Opus 5.5")
        self.assertEqual(sessinfo.label("claude-haiku-4-5-20251001"), "Haiku 4.5")
        self.assertEqual(sessinfo.label("claude-sonnet-4-20250514"), "Sonnet 4")
        self.assertEqual(sessinfo.label("opus"), "Opus")
        self.assertEqual(sessinfo.label("gpt-5"), "gpt-5")
        self.assertIsNone(sessinfo.label(None))

    def test_newest_turn_wins_and_synthetic_turns_are_skipped(self) -> None:
        self.write("a", [turn("claude-sonnet-5-5", "low"), json.dumps({"type": "user", "message": {}}),
                         turn("claude-opus-5-5", "high"), turn("<synthetic>", None)])
        self.assertEqual(sessinfo.from_transcript("a", self.root), {"model": "claude-opus-5-5", "effort": "high"})

    def test_reads_across_chunks(self) -> None:
        self.write("b", [turn("claude-opus-5-5", "xhigh")] + [json.dumps({"type": "user", "pad": "y" * 1000})] * 600)
        self.assertEqual(sessinfo.from_transcript("b", self.root)["effort"], "xhigh")

    def test_the_claude_config_dir_decides_where_transcripts_are(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="claude-base-"))
        (base / "projects" / "-p").mkdir(parents=True)
        (base / "projects" / "-p" / "cfgdir.jsonl").write_text(turn("claude-opus-5-5", "high") + "\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(base)}):
            self.assertEqual(sessinfo.claude_projects(), base / "projects")
            self.assertEqual(sessinfo.from_transcript("cfgdir"), {"model": "claude-opus-5-5", "effort": "high"})

    def test_a_missing_transcript_is_not_searched_again_at_once(self) -> None:
        with mock.patch.object(Path, "glob", return_value=iter(())) as glob:
            self.assertIsNone(sessinfo.transcript("nope-1", self.root))
            self.assertIsNone(sessinfo.transcript("nope-1", self.root))
        self.assertEqual(glob.call_count, 1)
        with mock.patch.object(sessinfo.time, "monotonic", return_value=time.monotonic() + 60):
            self.assertIsNone(sessinfo.transcript("nope-1", self.root))

    def test_live_last_next(self) -> None:
        self.write("live", [turn("claude-opus-5-5", "medium")])
        live = sessinfo.describe("claude", "live", None, {"model": "sonnet"}, "sonnet", self.root)
        self.assertEqual((live["text"], live["state"], live["why"]), ("Opus 5.5 · medium", "live", None))
        last = sessinfo.describe("claude", None, "live", {}, "sonnet", self.root)
        self.assertEqual((last["text"], last["state"]), ("Opus 5.5 · medium", "last"))
        new = sessinfo.describe("claude", "fresh", None, {"model": "opus", "effort": "high"}, None, self.root)
        self.assertEqual((new["text"], new["state"]), ("Opus · high", "live"))
        nxt = sessinfo.describe("claude", None, None, {}, "sonnet", self.root)
        self.assertEqual((nxt["text"], nxt["state"]), ("Sonnet · unknown", "next"))
        self.assertTrue(nxt["why"])
        none = sessinfo.describe("claude", None, None, {}, None, self.root)
        self.assertEqual(none["text"], "unknown · unknown")
        self.assertNotIn("default", none["text"].lower())
        other = sessinfo.describe("codex", "x", None, {"effort": "high"}, "gpt-5", self.root)
        self.assertEqual(other["text"], "gpt-5 · high")

    def test_cached_until_the_transcript_changes(self) -> None:
        self.write("c", [turn("claude-opus-5-5", "low")])
        self.assertEqual(sessinfo.from_transcript("c", self.root)["effort"], "low")
        with (self.root / "-proj" / "c.jsonl").open("a", encoding="utf-8") as f:
            f.write(turn("claude-opus-5-5", "max") + "\n")
        self.assertEqual(sessinfo.from_transcript("c", self.root)["effort"], "max")

    def test_for_lane_reads_the_last_session_of_a_closed_lane(self) -> None:
        cfg = make_home({"hub": {"runtime": "claude"}, "docs": {"runtime": "claude"}, "web": {"runtime": "codex"}})
        s = Store(cfg.db)
        s.record_usage("docs", "gone", "claude", 1, 1, None, 1)
        self.assertEqual(s.recent_sessions("docs"), ["gone"])
        self.write("gone", [turn("claude-opus-5-5", "high")])
        run = sessinfo.for_lane(cfg, s, "docs", "claude", "sonnet", None, self.root)
        self.assertEqual((run["text"], run["state"]), ("Opus 5.5 · high", "last"))
        s.record_usage("web", "w1", "codex", 1, 1, None, 1)
        run = sessinfo.for_lane(cfg, s, "web", "codex", "gpt-5", None, self.root)
        self.assertEqual((run["text"], run["state"]), ("gpt-5 · unknown", "next"))


class InfraModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home()
        self.s = Store(self.cfg.db)
        self.rt = FakeRuntime(self.s)
        self.e = Engine(self.cfg, self.s, self.rt, lambda n: None, clock=Clock(), log=lambda _: None)

    def test_flags_become_lines_and_open_the_session(self) -> None:
        send_infra(self.cfg, self.s, HUB, "small fix", None, model="sonnet", effort="LOW")
        self.e.tick()
        self.assertEqual(self.rt.opts[-1], ("sonnet", "low"))
        self.assertIn("model: sonnet\neffort: low\n\nsmall fix", self.rt.opened[-1][1])

    def test_body_lines_work_and_none_keeps_the_default(self) -> None:
        self.s.push(INFRA, HUB, "memo", "effort: high\ndo it")
        self.e.tick()
        self.assertEqual(self.rt.opts[-1], (None, "high"))

    def test_plain_memo_and_decisions_open_with_lane_default(self) -> None:
        self.s.push(INFRA, HUB, "decision", "model: opus\nperson wrote this")
        self.e.tick()
        self.assertEqual(self.rt.opts[-1], (None, None))

    def test_bad_values_are_refused(self) -> None:
        with self.assertRaises(TowerError):
            send_infra(self.cfg, self.s, HUB, "x", None, model="opsu")
        with self.assertRaises(TowerError):
            send_infra(self.cfg, self.s, HUB, "x", None, effort="huge")


if __name__ == "__main__":
    unittest.main()
