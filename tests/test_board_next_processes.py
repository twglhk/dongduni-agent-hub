import unittest

from tests.test_process_library import make_library
from tests.test_web import Board


class ProcessRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b = Board(engines={"writer": {"library": "processes/full/article-publishing.md", "skills": ".claude/skills"}})
        self.home = self.b.cfg.home
        make_library(self.home)
        (self.home / ".claude/skills/article-craft").mkdir(parents=True)
        (self.home / ".claude/skills/article-craft/SKILL.md").write_text("# writer craft\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.b.srv.shutdown()

    def test_library_with_cross_references(self) -> None:
        code, v, _ = self.b.req("GET", "/api/processes")
        self.assertEqual(code, 200)
        full = {f["id"]: f for f in v["full"]}
        self.assertEqual(full["article-publishing"]["lanes"], ["covers", "writer"])
        self.assertEqual([st["sub"] for st in full["demo-app"]["steps"]], ["app-build", "cover-making"])
        step = full["article-publishing"]["steps"][0]
        self.assertIn("rating", step)
        self.assertEqual(step["stages"], {})
        sub = {s["id"]: s for s in v["sub"]}
        self.assertEqual(sub["cover-making"]["used_by_full"], ["article-publishing", "demo-app"])
        self.assertEqual(sub["cover-making"]["used_by_lanes"], ["covers", "writer"])
        self.assertEqual(sub["article-planning"]["skills"][0]["doc"], ".claude/skills/article-craft/SKILL.md")
        self.assertEqual(v["lanes"]["writer"], ["article-publishing"])
        self.assertNotIn("research", v["lanes"])

    def test_doc_serves_only_what_the_library_names(self) -> None:
        for path in ("processes/full/demo-app.md", "processes/sub/article-qa/process.md",
                     "processes/sub/article-qa/patterns/one.md", ".claude/skills/article-craft/SKILL.md"):
            code, d, _ = self.b.req("GET", "/api/processes/doc?path=" + path)
            self.assertEqual(code, 200, path)
            self.assertEqual(d["path"], path)
            self.assertTrue(d["text"])
        for bad in ("", ".tower/tower.json", ".tower/lanes/writer.md", "processes/../.tower/tower.json",
                    "processes/sub/article-qa/patterns/missing.md"):
            self.assertEqual(self.b.req("GET", "/api/processes/doc?path=" + bad)[0], 404, bad)

    def test_engine_routes_stay_for_lane_cards(self) -> None:
        code, e, _ = self.b.req("GET", "/api/engines")
        self.assertEqual(code, 200)
        self.assertEqual([x["id"] for x in e["engines"] if not x["implicit"]], ["writer"])
        code, m, _ = self.b.req("GET", "/api/engines/writer/map")
        self.assertEqual(code, 200)
        self.assertEqual(len(m["pipelines"]), 4)


if __name__ == "__main__":
    unittest.main()
