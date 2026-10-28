"""`tower discover`: a read-only survey of how this person works with AI agents, as JSON for the setup agent.

It reports names, counts and booleans only. It never prints a config value, a token, an env value or file
contents, so the output is safe to paste into a chat or a board item.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

AGENT_CLIS = ("opencode", "claude", "codex", "gemini", "cursor-agent", "aider", "goose", "amp")
EXPOSE_CLIS = ("cloudflared", "tailscale", "ngrok", "aws", "gcloud", "az", "flyctl", "docker", "gh")
KNOWLEDGE = ("notion", "roam", "obsidian", "logseq", "linear", "jira", "atlassian", "confluence", "slack", "github",
             "gitlab", "google", "gdrive", "drive", "gmail", "calendar", "sheets", "figma", "sentry", "postgres",
             "supabase", "airtable", "asana", "clickup", "discord", "teams")


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


def version(binary: str) -> str | None:
    path = shutil.which(binary)
    if not path:
        return None
    try:
        p = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return "installed"
    line = (p.stdout or p.stderr).strip().splitlines()
    return line[0][:80] if line else "installed"


def keys(value: Any) -> list[str]:
    return sorted(str(k) for k in value) if isinstance(value, dict) else []


def claude(home: Path) -> dict[str, Any]:
    d = home / ".claude"
    cfg = read_json(home / ".claude.json")
    mcp = set(keys(cfg.get("mcpServers")))
    for proj in (cfg.get("projects") or {}).values():
        if isinstance(proj, dict):
            mcp |= set(keys(proj.get("mcpServers")))
    return {"present": d.is_dir(), "memory_file": (d / "CLAUDE.md").is_file(), "commands": count(d / "commands", "*.md"),
            "skills": count(d / "skills"), "agents": count(d / "agents", "*.md"),
            "settings": (d / "settings.json").is_file(), "mcp_servers": sorted(mcp)}


def opencode(home: Path) -> dict[str, Any]:
    d = home / ".config" / "opencode"
    cfg = read_json(d / "opencode.json") or read_json(d / "opencode.jsonc")
    plugins = cfg.get("plugin") if isinstance(cfg.get("plugin"), list) else []
    return {"present": d.is_dir(), "memory_file": (d / "AGENTS.md").is_file(), "commands": count(d / "command", "*.md"),
            "skills": count(d / "skills"), "agents": count(d / "agent", "*.md") + len(keys(cfg.get("agent"))),
            "plugins": [str(p).split("@")[0] for p in plugins][:20], "mcp_servers": keys(cfg.get("mcp")),
            "providers": keys(cfg.get("provider"))}


def codex(home: Path) -> dict[str, Any]:
    d = home / ".codex"
    toml = ""
    try:
        toml = (d / "config.toml").read_text(encoding="utf-8")
    except OSError:
        pass
    mcp = sorted(set(re.findall(r"^\[mcp_servers\.([A-Za-z0-9_\-\"]+)\]", toml, re.M)))
    return {"present": d.is_dir(), "memory_file": (d / "AGENTS.md").is_file(), "prompts": count(d / "prompts", "*.md"),
            "mcp_servers": [m.strip('"') for m in mcp]}


def project(path: Path) -> dict[str, Any]:
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
            "top_level": entries[:40], "top_level_total": len(entries)}


def exposure(home: Path) -> dict[str, Any]:
    out: dict[str, Any] = {name: bool(shutil.which(name)) for name in EXPOSE_CLIS}
    cf = home / ".cloudflared"
    out["cloudflared_login"] = (cf / "cert.pem").is_file()
    out["cloudflared_tunnels"] = count(cf, "*.json")
    aws = home / ".aws" / "config"
    try:
        out["aws_profiles"] = sorted(set(re.findall(r"^\[(?:profile )?([^\]]+)\]", aws.read_text(), re.M)))
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
    mcp = sorted({m for c in configs.values() for m in c.get("mcp_servers", [])})
    hints = sorted({m for m in mcp if any(k in m.lower() for k in KNOWLEDGE)})
    return {
        "system": {"os": platform.system(), "release": platform.release(), "arch": platform.machine(),
                   "python": sys.version.split()[0]},
        "agent_clis": {k: v for k, v in agents.items() if v},
        "agent_configs": configs,
        "mcp_servers_all": mcp,
        "knowledge_hints": hints,
        "project": project(proj),
        "exposure": exposure(home),
        "runtimes_supported": [r for r in ("opencode", "claude", "codex") if agents.get(r)],
    }
