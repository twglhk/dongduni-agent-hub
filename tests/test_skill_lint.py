import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("skill_lint", ROOT / "tools" / "skill_lint.py")
assert spec and spec.loader
lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint)


def skill(root: Path, name: str, front: str, body: str = "Body.\n", folder: str | None = None) -> Path:
    d = root / ".claude" / "skills" / (folder or name)
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\n{front}\n---\n{body}", encoding="utf-8")
    return d


class FrontmatterTest(unittest.TestCase):
    def test_scalars_quotes_blocks_lists_and_nesting(self) -> None:
        meta = lint.parse_frontmatter(
            "name: my-skill\n"
            "description: 'It''s a skill'  # a comment\n"
            'alt: "line\\nbreak \\u00e9"\n'
            "folded: >\n  one\n  two\n\n  three\n"
            "literal: |\n  a\n  b\n"
            "count: 3\nflag: true\nnothing:\nlist:\n  - a\n  - b\ninline: [x, 'y z']\n"
            "metadata:\n  owner: me\n  tags:\n    - t\n")
        self.assertEqual(meta["name"], "my-skill")
        self.assertEqual(meta["description"], "It's a skill")
        self.assertEqual(meta["alt"], "line\nbreak é")
        self.assertEqual(meta["folded"], "one two\nthree")
        self.assertEqual(meta["literal"], "a\nb")
        self.assertEqual((meta["count"], meta["flag"], meta["nothing"]), (3, True, None))
        self.assertEqual((meta["list"], meta["inline"]), (["a", "b"], ["x", "y z"]))
        self.assertEqual(meta["metadata"], {"owner": "me", "tags": ["t"]})

    def test_what_yaml_rejects_is_rejected(self) -> None:
        for bad in ("description: Use when: the user asks", "description: @mention", "not a mapping line", "a: 'open",
                    "  indented: 1"):
            with self.assertRaises(lint.FrontmatterError, msg=bad):
                lint.parse_frontmatter(bad)

    def test_plain_scalar_is_cut_at_a_space_hash(self) -> None:
        self.assertEqual(lint.parse_frontmatter("description: before # after")["description"], "before")


class LintTest(unittest.TestCase):
    def verdict(self, front: str, body: str = "Body.\n", folder: str = "my-skill") -> tuple[str, list[str]]:
        root = Path(tempfile.mkdtemp())
        d = skill(root, folder, front, body)
        return lint.lint_skill(d)

    def test_a_good_skill_passes(self) -> None:
        self.assertEqual(self.verdict("name: my-skill\ndescription: Does a thing."), ("OK", []))

    def test_name_must_match_the_folder_and_the_pattern(self) -> None:
        self.assertEqual(self.verdict("name: other\ndescription: x")[0], "FAIL")
        self.assertEqual(self.verdict("name: My_Skill\ndescription: x", folder="My_Skill")[0], "FAIL")
        self.assertEqual(self.verdict("name: 123\ndescription: x", folder="123")[0], "FAIL")

    def test_description_rules(self) -> None:
        self.assertEqual(self.verdict("name: my-skill")[0], "FAIL")
        self.assertEqual(self.verdict("name: my-skill\ndescription: " + "x" * 1025)[0], "FAIL")
        verdict, notes = self.verdict("name: my-skill\ndescription: cut here # not a comment for opencode")
        self.assertEqual(verdict, "FAIL")
        self.assertTrue(any("quote it" in n for n in notes))
        verdict, notes = self.verdict("name: my-skill\ndescription: Use when: the user asks")
        self.assertEqual(verdict, "FAIL")
        self.assertTrue(any("not YAML" in n for n in notes))

    def test_unknown_keys_and_long_bodies_warn_and_dead_links_fail(self) -> None:
        self.assertEqual(self.verdict("name: my-skill\ndescription: x\ntools: a")[0], "WARN")
        self.assertEqual(self.verdict("name: my-skill\ndescription: x", "line\n" * 200)[0], "WARN")
        self.assertEqual(self.verdict("name: my-skill\ndescription: x", "see [it](references/none.md)\n")[0], "FAIL")
        self.assertEqual(self.verdict("name: my-skill\ndescription: x", "see `[it](references/none.md)`\n")[0], "OK")

    def test_no_frontmatter_fails(self) -> None:
        d = Path(tempfile.mkdtemp()) / "x"
        d.mkdir()
        (d / "SKILL.md").write_text("just text", encoding="utf-8")
        self.assertEqual(lint.lint_skill(d)[0], "FAIL")

    def test_the_skills_the_package_ships_pass(self) -> None:
        for f in sorted((ROOT / "agent_hub" / "templates").rglob("SKILL.md")):
            meta = lint.parse_frontmatter(lint.split_frontmatter(f.read_text(encoding="utf-8"))[0])
            self.assertIsInstance(meta.get("name"), str, f)
            self.assertIsInstance(meta.get("description"), str, f)
