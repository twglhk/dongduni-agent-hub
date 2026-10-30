"""`dah discover`: a read-only survey of how this person works with AI agents, as JSON for the setup agent.

It reports names, counts and booleans only. It never prints a config value, a token, an env value or file
contents, so the output is safe to paste into a chat or a board item.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import procs

AGENT_CLIS = ("opencode", "claude", "codex", "gemini", "cursor-agent", "aider", "goose", "amp")
EXPOSE_CLIS = ("cloudflared", "tailscale", "ngrok", "aws", "gcloud", "az", "flyctl", "docker", "gh")
KNOWLEDGE = ("notion", "roam", "obsidian", "logseq", "linear", "jira", "atlassian", "confluence", "slack", "github",
             "gitlab", "google", "gdrive", "drive", "gmail", "calendar", "sheets", "figma", "sentry", "postgres",
             "supabase", "airtable", "asana", "clickup", "discord", "teams", "memory", "mem0", "zep", "wiki")


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments outside strings, then trailing commas."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(strip_jsonc(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def count(path: Path, pattern: str = "*") -> int:
    return sum(1 for _ in path.glob(pattern)) if path.is_dir() else 0


def skills(*dirs: Path) -> int:
    """Skills are folders with a SKILL.md, often nested (synced or grouped skills)."""
    return sum(sum(1 for _ in d.rglob("SKILL.md")) for d in dirs if d.is_dir())


def stems(*dirs: Path) -> list[str]:
    return sorted({p.stem for d in dirs if d.is_dir() for p in d.glob("*.md")})[:50]


def version(binary: str) -> str | None:
    path = shutil.which(binary)
    if not path:
        return None
    try:
        p = subprocess.run([*procs.command(path), "--version"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return "installed"
    line = (p.stdout or p.stderr).strip().splitlines()
    return line[0][:80] if line else "installed"


def keys(value: Any) -> list[str]:
    return sorted(str(k) for k in value) if isinstance(value, dict) else []


def claude_settings(d: Path) -> dict[str, Any]:
    """Hook event names and enabled plugin names from settings.json / settings.local.json (never commands or values)."""
    hooks: set[str] = set()
    plugins: set[str] = set()
    for name in ("settings.json", "settings.local.json"):
        s = read_json(d / name)
        hooks |= set(keys(s.get("hooks")))
        enabled = s.get("enabledPlugins")
        plugins |= {str(k) for k, v in enabled.items() if v} if isinstance(enabled, dict) else set()
    return {"hooks": sorted(hooks), "plugins": sorted(plugins)}


def claude(home: Path) -> dict[str, Any]:
    d = home / ".claude"
    cfg = read_json(home / ".claude.json")
    mcp = set(keys(cfg.get("mcpServers")))
    for proj in (cfg.get("projects") or {}).values():
        if isinstance(proj, dict):
            mcp |= set(keys(proj.get("mcpServers")))
    return {"present": d.is_dir(), "memory_file": (d / "CLAUDE.md").is_file(), "commands": count(d / "commands", "*.md"),
            "skills": skills(d / "skills"), "agents": stems(d / "agents"),
            "settings": (d / "settings.json").is_file(), **claude_settings(d), "mcp_servers": sorted(mcp)}


def opencode_agents(project_dir: Path) -> list[str]:
    """Agent names as OpenCode resolves them (config, plugins, project), from `opencode agent list` headers only."""
    binary = shutil.which(os.environ.get("TOWER_OPENCODE_BIN", "opencode"))
    if not binary:
        return []
    try:
        p = subprocess.run([*procs.command(binary), "agent", "list"], cwd=project_dir, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = re.findall(r"^(\S[^\n]*?) \((primary|subagent|all)\)\s*$", p.stdout, re.M)
    return list(dict.fromkeys(f"{name} ({mode})" for name, mode in found))[:60]


def opencode(home: Path) -> dict[str, Any]:
    d = home / ".config" / "opencode"
    cfg = read_json(d / "opencode.json") or read_json(d / "opencode.jsonc")
    plugins = [str(p).split("@")[0] for p in cfg.get("plugin")][:20] if isinstance(cfg.get("plugin"), list) else []
    short = {p.rsplit("/", 1)[-1] for p in plugins}
    stale = (".bak", "backup", ".legacy", ".tmp", ".old")
    plugin_configs = sorted(e.name for e in d.iterdir() if e.name.split(".")[0] in short
                            and not any(s in e.name for s in stale)) if d.is_dir() else []
    return {"present": d.is_dir(), "memory_file": (d / "AGENTS.md").is_file(),
            "commands": count(d / "command", "*.md") + count(d / "commands", "*.md"),
            "skills": skills(d / "skill", d / "skills"),
            "agents": sorted({*stems(d / "agent", d / "agents"), *keys(cfg.get("agent"))}),
            "default_agent": str(cfg.get("default_agent") or ""),
            "plugins": plugins, "plugin_configs": plugin_configs, "mcp_servers": keys(cfg.get("mcp")),
            "providers": keys(cfg.get("provider"))}


def codex_toml(path: Path) -> tuple[list[str], list[str]]:
    """MCP server and profile names from a Codex config.toml."""
    try:
        toml = path.read_text(encoding="utf-8")
    except OSError:
        return [], []

    def names(table: str) -> list[str]:
        return sorted({m.strip('"') for m in re.findall(rf"^\[{table}\.([A-Za-z0-9_\-\"]+)\]", toml, re.M)})

    return names("mcp_servers"), names("profiles")


def codex(home: Path) -> dict[str, Any]:
    d = home / ".codex"
    mcp, profiles = codex_toml(d / "config.toml")
    return {"present": d.is_dir(), "memory_file": (d / "AGENTS.md").is_file(), "prompts": count(d / "prompts", "*.md"),
            "skills": skills(d / "skills"), "profiles": profiles, "mcp_servers": mcp}


def claude_unapproved(path: Path, home: Path, servers: list[str]) -> list[str]:
    """`.mcp.json` servers Claude Code has not been allowed to start here. Headless lanes (`claude -p`) cannot
    approve them, so they silently run without them."""
    proj = (read_json(home / ".claude.json").get("projects") or {}).get(str(path)) or {}
    allowed, denied, allow_all = set(), set(), False
    for src in (proj, read_json(path / ".claude" / "settings.json"), read_json(path / ".claude" / "settings.local.json")):
        if not isinstance(src, dict):
            continue
        allowed |= {str(s) for s in src.get("enabledMcpjsonServers") or []}
        denied |= {str(s) for s in src.get("disabledMcpjsonServers") or []}
        allow_all = allow_all or bool(src.get("enableAllProjectMcpServers"))
    return [s for s in servers if s in denied or not (allow_all or s in allowed)]


def project_agents(path: Path, home: Path) -> dict[str, Any]:
    """What the workspace itself configures for each agent, next to the person's global setup."""
    claude_mcp = keys(read_json(path / ".mcp.json").get("mcpServers"))
    oc_cfg = read_json(path / "opencode.json") or read_json(path / "opencode.jsonc")
    oc = path / ".opencode"
    cx_mcp, cx_profiles = codex_toml(path / ".codex" / "config.toml")
    oc_plugins = oc_cfg.get("plugin") if isinstance(oc_cfg.get("plugin"), list) else []
    return {
        "claude": {"mcp_servers": claude_mcp, "mcp_not_approved": claude_unapproved(path, home, claude_mcp),
                   "agents": stems(path / ".claude" / "agents"), "commands": count(path / ".claude" / "commands", "*.md"),
                   "skills": skills(path / ".claude" / "skills"), **claude_settings(path / ".claude")},
        "opencode": {"config": bool(oc_cfg), "mcp_servers": keys(oc_cfg.get("mcp")),
                     "agents": sorted({*stems(oc / "agent", oc / "agents"), *keys(oc_cfg.get("agent"))}),
                     "commands": count(oc / "command", "*.md") + count(oc / "commands", "*.md"),
                     "skills": skills(oc / "skill", oc / "skills"),
                     "plugins": [str(p).split("@")[0] for p in oc_plugins][:20]},
        "codex": {"config": (path / ".codex" / "config.toml").is_file(), "mcp_servers": cx_mcp, "profiles": cx_profiles,
                  "skills": skills(path / ".codex" / "skills", path / ".agents" / "skills")},
    }


def project(path: Path, home: Path) -> dict[str, Any]:
    remote = None
    if shutil.which("git"):
        p = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"], capture_output=True, text=True)
        if p.returncode == 0:
            url = p.stdout.strip()
            m = re.search(r"(?:@|//)(?:[^@/]+@)?([^/:]+)[:/](.+?)(?:\.git)?$", url)
            remote = f"{m.group(1)}/{m.group(2)}" if m else "set"
    entries = sorted(e.name + ("/" if e.is_dir() else "") for e in path.iterdir()
                     if not e.name.startswith(".")) if path.is_dir() else []
    marks = {name: (path / name).exists() for name in
             ("AGENTS.md", "CLAUDE.md", ".opencode", ".claude", ".codex", ".cursor", ".github", "docs", ".tower")}
    return {"path": str(path), "git_remote": remote, "agent_files": {k: v for k, v in marks.items() if v},
            "agents": project_agents(path, home), "top_level": entries[:40], "top_level_total": len(entries)}


def exposure(home: Path) -> dict[str, Any]:
    out: dict[str, Any] = {name: bool(shutil.which(name)) for name in EXPOSE_CLIS}
    cf = home / ".cloudflared"
    out["cloudflared_login"] = (cf / "cert.pem").is_file()
    out["cloudflared_tunnels"] = count(cf, "*.json")
    aws = home / ".aws" / "config"
    try:
        text = aws.read_text(encoding="utf-8", errors="replace")
        out["aws_profiles"] = sorted(set(re.findall(r"^\[(?:profile )?([^\]]+)\]", text, re.M)))
    except OSError:
        out["aws_profiles"] = []
    if out["gh"]:
        out["gh_logged_in"] = subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0
    return out


def survey(project_dir: Path | None = None, home: Path | None = None) -> dict[str, Any]:
    home = home or Path.home()
    proj = (project_dir or Path.cwd()).resolve()
    agents = {name: version(name) for name in AGENT_CLIS}
    configs = {"claude": claude(home), "opencode": opencode(home), "codex": codex(home)}
    if agents.get("opencode"):
        configs["opencode"]["resolved_agents"] = opencode_agents(proj)
    proj_info = project(proj, home)
    mcp = sorted({m for c in [*configs.values(), *proj_info["agents"].values()] for m in c.get("mcp_servers", [])})
    hints = sorted({m for m in mcp if any(k in m.lower() for k in KNOWLEDGE)})
    return {
        "system": {"os": platform.system(), "release": platform.release(), "arch": platform.machine(),
                   "python": sys.version.split()[0]},
        "agent_clis": {k: v for k, v in agents.items() if v},
        "agent_configs": configs,
        "mcp_servers_all": mcp,
        "knowledge_hints": hints,
        "project": proj_info,
        "exposure": exposure(home),
        "terminals": {name: bool(shutil.which(name)) for name in ("tmux", "orca")},  # lanes can run in their windows
        "runtimes_supported": [r for r in ("opencode", "claude", "codex") if agents.get(r)],
    }
