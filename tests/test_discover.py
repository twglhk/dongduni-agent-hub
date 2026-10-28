import json
import tempfile
import unittest
from pathlib import Path

from agent_hub.discover import strip_jsonc, survey

SECRET = "sk-live-DO-NOT-LEAK-123"


class DiscoverTest(unittest.TestCase):
    def test_names_only_never_values(self) -> None:
        home = Path(tempfile.mkdtemp())
        (home / ".config/opencode").mkdir(parents=True)
        (home / ".config/opencode/opencode.jsonc").write_text(
            '{\n // comment\n "mcp": {"notion": {"env": {"KEY": "%s"}}, "roam-research": {}},\n'
            ' "provider": {"anthropic": {"apiKey": "%s"}},\n "plugin": ["oh-my-opencode@1.2"],\n}' % (SECRET, SECRET))
        (home / ".claude").mkdir()
        (home / ".claude/CLAUDE.md").write_text("x")
        (home / ".claude.json").write_text(json.dumps({"mcpServers": {"linear": {"token": SECRET}},
                                                       "projects": {"/p": {"mcpServers": {"slack": {}}}}}))
        (home / ".codex").mkdir()
        (home / ".codex/config.toml").write_text('[mcp_servers.github]\ncommand = "x"\nenv = { T = "%s" }\n' % SECRET)
        (home / ".aws").mkdir()
        (home / ".aws/config").write_text("[default]\nregion=x\n[profile work]\naws_secret_access_key=%s\n" % SECRET)
        out = survey(home, home=home)
        text = json.dumps(out)
        self.assertNotIn(SECRET, text)
        self.assertEqual(out["agent_configs"]["opencode"]["mcp_servers"], ["notion", "roam-research"])
        self.assertEqual(out["agent_configs"]["opencode"]["plugins"], ["oh-my-opencode"])
        self.assertEqual(out["agent_configs"]["claude"]["mcp_servers"], ["linear", "slack"])
        self.assertEqual(out["agent_configs"]["codex"]["mcp_servers"], ["github"])
        self.assertEqual(out["exposure"]["aws_profiles"], ["default", "work"])
        self.assertIn("notion", out["knowledge_hints"])

    def test_jsonc_keeps_urls_in_strings(self) -> None:
        self.assertEqual(json.loads(strip_jsonc('{"u": "https://x.y/z", /* c */ "a": [1,],}')),
                         {"u": "https://x.y/z", "a": [1]})


if __name__ == "__main__":
    unittest.main()
