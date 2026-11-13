from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_hub import stagemodel
from agent_hub.engine import Engine
from agent_hub.runtimes import Runtimes
from agent_hub.store import Store
from tests.helpers import Clock, FakeRuntime, make_home
from tests.test_runtimes import FAKE_CLAUDE, script, wait_for

TABLE = """| # | Kind | Model · effort |
|---|---|---|
| 1 | Draft | 1 sonnet/medium; 2-3 opus/high |
| 2 | Plain | sonnet/low |
"""


class StageModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.table = Path(tempfile.mkdtemp()) / "lib.md"
        self.table.write_text(TABLE, encoding="utf-8")

    def test_explicit_lines_win(self) -> None:
        self.assertEqual(stagemodel.resolve(self.table, "x\nmodel: opus\neffort: max\nLine: row 2 stage 1"), ("opus", "max"))

    def test_table_row_and_stage_range(self) -> None:
        self.assertEqual(stagemodel.resolve(self.table, "Line: row 1 stage 1"), ("sonnet", "medium"))
        self.assertEqual(stagemodel.resolve(self.table, "Line: #1 stage 1-3"), ("opus", "high"))
        self.assertEqual(stagemodel.resolve(self.table, "Line: row 2 stage 5"), ("sonnet", "low"))

    def test_explicit_model_keeps_table_effort(self) -> None:
        self.assertEqual(stagemodel.resolve(self.table, "model: opus\nLine: row 2 stage 1"), ("opus", "low"))

    def test_nothing_named(self) -> None:
        self.assertEqual(stagemodel.resolve(None, "just do it"), (None, None))
        self.assertEqual(stagemodel.resolve(self.table, "Line: row 9 stage 1"), (None, None))

    def test_missing_lines(self) -> None:
        self.assertEqual(len(stagemodel.missing_lines("nothing")), 2)
        self.assertEqual(stagemodel.missing_lines("model: opus\neffort: high"), [])


class ValidationTest(unittest.TestCase):
    def test_four_aliases_and_full_ids_pass(self) -> None:
        for m in ("fable", "opus", "sonnet", "haiku", "Opus", "claude-opus-5-5", "gpt-5", "openai/gpt-5"):
            self.assertEqual(stagemodel.problems(m, "high"), [], m)

    def test_typo_and_bad_effort_are_flagged(self) -> None:
        self.assertEqual(len(stagemodel.problems("opuss", None)), 1)
        self.assertEqual(len(stagemodel.problems("opus", "ultra")), 1)
        self.assertEqual(len(stagemodel.problems("sonet", "huge")), 2)

    def test_all_five_effort_levels_and_fable_in_a_table(self) -> None:
        for lvl in ("low", "medium", "high", "xhigh", "max"):
            self.assertEqual(stagemodel.problems("fable", lvl), [])
        t = Path(tempfile.mkdtemp()) / "lib.md"
        t.write_text("| # | Kind | Model · effort |\n|---|---|---|\n| 1 | A | 1 haiku/low; 2 sonnet/high; 3 opus/xhigh; 4 fable/max |\n", encoding="utf-8")
        self.assertEqual(stagemodel.resolve(t, "Line: #1 stage 1-4"), ("fable", "max"))
        self.assertEqual(stagemodel.resolve(t, "Line: #1 stage 3"), ("opus", "xhigh"))
        self.assertEqual(stagemodel.resolve(t, "Line: #1 stage 1"), ("haiku", "low"))


class EngineOptsTest(unittest.TestCase):
    def test_task_start_passes_model_and_effort(self) -> None:
        cfg = make_home({"hub": {"runtime": "claude"}, "docs": {"runtime": "claude"}})
        s = Store(cfg.db)
        rt = FakeRuntime(s)
        eng = Engine(cfg, s, rt, lambda _: None, clock=Clock(), log=lambda _: None)
        s.add_task("docs", "t", "do it\nmodel: opus\neffort: high")
        rt.st["docs"] = "closed"
        eng.tick()
        self.assertEqual(rt.opts[-1], ("opus", "high"))


class ClaudeArgvTest(unittest.TestCase):
    def test_model_and_effort_survive_resume(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        log = tmp / "calls.jsonl"
        os.environ["FAKE_LOG"] = str(log)
        os.environ["TOWER_CLAUDE_BIN"] = str(script(tmp, "claude", FAKE_CLAUDE))
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("FAKE_LOG", "TOWER_CLAUDE_BIN")])
        cfg = make_home({"hub": {"runtime": "claude", "model": "sonnet"}})
        rt = Runtimes(cfg, Store(cfg.db))
        sid = rt.open("hub", "go", model="opus", effort="high")
        wait_for(lambda: rt.status("hub") == "idle")
        rt.send("hub", sid, "again")
        wait_for(lambda: rt.status("hub") == "idle")
        calls = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(calls), 2)
        for call in calls:
            a = call["argv"]
            self.assertEqual(a[a.index("--model") + 1], "opus")
            self.assertEqual(a[a.index("--effort") + 1], "high")
        rt.open("hub", "plain")  # a task without the lines goes back to the lane's own model
        wait_for(lambda: rt.status("hub") == "idle")
        a = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])["argv"]
        self.assertEqual(a[a.index("--model") + 1], "sonnet")
        self.assertNotIn("--effort", a)


if __name__ == "__main__":
    unittest.main()
