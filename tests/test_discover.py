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

    def test_project_and_nested_assets_without_values(self) -> None:
        home, proj = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        (home / ".claude/skills/synced/u1/docx").mkdir(parents=True)
        (home / ".claude/skills/synced/u1/docx/SKILL.md").write_text("x")
        (home / ".claude/skills/pdf").mkdir()
        (home / ".claude/skills/pdf/SKILL.md").write_text("x")
        (home / ".claude/agents").mkdir()
        (home / ".claude/agents/oracle.md").write_text(SECRET)
        (home / ".claude/settings.json").write_text(json.dumps({
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": f"curl -H {SECRET}"}]}]},
            "enabledPlugins": {"memory@market": True, "off@market": False}}))
        (home / ".config/opencode").mkdir(parents=True)
        (home / ".config/opencode/opencode.json").write_text(json.dumps(
            {"plugin": ["oh-my-openagent@latest"], "default_agent": "Sisyphus", "agent": {"build": {"prompt": SECRET}}}))
        (home / ".config/opencode/oh-my-openagent.json").write_text(json.dumps({"k": SECRET}))
        (home / ".codex").mkdir()
        (home / ".codex/config.toml").write_text('[profiles.fast]\nmodel = "%s"\n' % SECRET)
        (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"notion": {"env": {"T": SECRET}}, "mem0": {}}}))
        (home / ".claude.json").write_text(json.dumps({"projects": {str(proj.resolve()): {"enabledMcpjsonServers": ["notion"]}}}))
        (proj / "opencode.json").write_text(json.dumps({"mcp": {"memory": {"env": {"T": SECRET}}}, "plugin": ["p@1"]}))
        (proj / ".opencode/agent").mkdir(parents=True)
        (proj / ".opencode/agent/reviewer.md").write_text(SECRET)
        (proj / ".claude/settings.local.json").parent.mkdir()
        (proj / ".claude/settings.local.json").write_text(json.dumps({"hooks": {"PostToolUse": [{"command": SECRET}]}}))
        (proj / ".codex").mkdir()
        (proj / ".codex/config.toml").write_text('[mcp_servers.linear]\ntoken = "%s"\n' % SECRET)
        out = survey(proj, home=home)
        self.assertNotIn(SECRET, json.dumps(out))
        g = out["agent_configs"]
        self.assertEqual(g["claude"]["skills"], 2)
        self.assertEqual(g["claude"]["agents"], ["oracle"])
        self.assertEqual(g["claude"]["hooks"], ["Stop"])
        self.assertEqual(g["claude"]["plugins"], ["memory@market"])
        self.assertEqual(g["opencode"]["default_agent"], "Sisyphus")
        self.assertEqual(g["opencode"]["agents"], ["build"])
        self.assertEqual(g["opencode"]["plugin_configs"], ["oh-my-openagent.json"])
        self.assertEqual(g["codex"]["profiles"], ["fast"])
        p = out["project"]["agents"]
        self.assertEqual(p["claude"]["mcp_servers"], ["mem0", "notion"])
        self.assertEqual(p["claude"]["mcp_not_approved"], ["mem0"])  # headless Claude lanes would run without it
        self.assertEqual(p["claude"]["hooks"], ["PostToolUse"])
        self.assertEqual(p["opencode"]["mcp_servers"], ["memory"])
        self.assertEqual(p["opencode"]["agents"], ["reviewer"])
        self.assertEqual(p["codex"]["mcp_servers"], ["linear"])
        self.assertTrue({"notion", "memory", "linear"} <= set(out["mcp_servers_all"]))
        self.assertIn("notion", out["knowledge_hints"])

    def test_jsonc_keeps_urls_in_strings(self) -> None:
        self.assertEqual(json.loads(strip_jsonc('{"u": "https://x.y/z", /* c */ "a": [1,],}')),
                         {"u": "https://x.y/z", "a": [1]})


if __name__ == "__main__":
    unittest.main()
