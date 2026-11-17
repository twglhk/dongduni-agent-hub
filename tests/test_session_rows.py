from __future__ import annotations

import unittest

from agent_hub.store import Store
from agent_hub.web import lanes_view
from tests.helpers import make_home


class SessionRowsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = make_home({"hub": {"runtime": "claude"}, "docs": {"runtime": "claude", "worktree": True}},
                             max_instances=2)
        self.s = Store(self.cfg.db)

    def rows(self) -> dict[str, dict]:
        return {r["lane"]: r for r in lanes_view(self.cfg, self.s, {})}

    def test_a_parallel_session_shows_only_while_it_is_in_use(self) -> None:
        self.assertEqual(sorted(self.rows()), ["docs", "hub"])
        tid = self.s.add_task("docs#2", "Beside", "paths: a")
        self.s.claim_task(tid)
        rows = self.rows()
        self.assertEqual(sorted(rows), ["docs", "docs#2", "hub"])
        self.assertEqual((rows["docs#2"]["base"], rows["docs"]["base"]), ("docs", None))

    def test_every_session_names_its_own_branch(self) -> None:
        tid = self.s.add_task("docs#2", "Beside", "paths: a")
        self.s.claim_task(tid)
        rows = self.rows()
        self.assertEqual((rows["docs"]["branch"], rows["docs#2"]["branch"], rows["hub"]["branch"]),
                         ("dah/docs", "dah/docs-2", None))


if __name__ == "__main__":
    unittest.main()
