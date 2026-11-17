import py_compile
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "agent_hub" / "guide" / "deploy" / "tools" / "shot.py"
WITHOUT_PLAYWRIGHT = ("import runpy, sys; sys.modules['playwright'] = None; sys.modules['playwright.sync_api'] = None; "
                      "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')")


def run(*args: str, without_playwright: bool = False) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-c", WITHOUT_PLAYWRIGHT, str(TOOL), *args] if without_playwright else [sys.executable, str(TOOL), *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


class ShotToolTest(unittest.TestCase):
    def test_it_compiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            py_compile.compile(str(TOOL), cfile=str(Path(tmp) / "shot.pyc"), doraise=True)

    def test_help_works_without_playwright(self) -> None:
        got = run("--help", without_playwright=True)
        self.assertEqual(got.returncode, 0)
        self.assertIn("--phone", got.stdout)

    def test_missing_playwright_exits_with_the_install_hint(self) -> None:
        got = run("http://127.0.0.1:1/", "out.png", without_playwright=True)
        self.assertEqual(got.returncode, 2)
        self.assertIn("pip install playwright", got.stderr)
        self.assertIn("playwright install chromium", got.stderr)

    def test_the_package_does_not_import_it(self) -> None:
        pkg = TOOL.parents[3]
        for py in pkg.glob("*.py"):
            self.assertNotIn("playwright", py.read_text(encoding="utf-8"), py.name)

    def test_a_malformed_header_is_refused_before_a_browser_starts(self) -> None:
        got = run("http://127.0.0.1:1/", "out.png", "--header", "nope", without_playwright=True)
        self.assertNotEqual(got.returncode, 0)
        self.assertIn("NAME=VALUE", got.stderr)


if __name__ == "__main__":
    unittest.main()
