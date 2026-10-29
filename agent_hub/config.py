"""Workspace config: `<home>/.tower/tower.json`.

The home is the folder whose `.tower/` holds the config. Agents run in `project` (default: the home itself).
State (SQLite DB, logs, members) lives in `<home>/.tower/state/`, which must stay out of git.
"""

from __future__ import annotations

import json
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HUB = "hub"
INFRA = "infra"
RUNTIMES = ("opencode", "claude", "codex")
TOWER_DIR = ".tower"
CONFIG_NAME = "tower.json"
FIRST_OPENCODE_PORT = 4301

DEFAULT_ARGS = {
    # Lanes run unattended, so they need to act without a human approving each tool call.
    # `tower init --safe` writes the stricter variants instead; see SETUP.md "Permissions".
    "claude": ["--permission-mode", "bypassPermissions"],
    "codex": ["--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check"],
    "opencode": [],
}
SAFE_ARGS = {
    "claude": ["--permission-mode", "acceptEdits"],
    "codex": ["--sandbox", "workspace-write", "--skip-git-repo-check"],
    "opencode": [],
}


class ConfigError(Exception):
    pass


@dataclass
class Lane:
    id: str
    kind: str                    # hub | infra | team
    runtime: str                 # opencode | claude | codex
    label: str = ""
    port: int | None = None      # opencode only
    model: str = ""
    agent: str = ""              # the runtime's own agent (OpenCode agent, Claude --agent); empty = its default
    args: list[str] = field(default_factory=list)
    instructions: str = ""       # path relative to home
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    home: Path
    name: str = "Agent Hub"
    lang: str = "en"
    project: Path | None = None
    web_host: str = "127.0.0.1"
    web_port: int = 8787
    public_url: str = ""
    auth_mode: str = "local"     # local | header | token
    auth_header: str = ""
    max_lanes: int = 2
    opencode_port: int = FIRST_OPENCODE_PORT
    notify: dict[str, str] = field(default_factory=dict)
    tunnel: str = ""             # a command the tower keeps alive, e.g. `cloudflared tunnel run board`
    lanes: dict[str, Lane] = field(default_factory=dict)

    @property
    def state_dir(self) -> Path:
        return self.home / TOWER_DIR / "state"

    @property
    def db(self) -> Path:
        return self.state_dir / "tower.db"

    @property
    def workdir(self) -> Path:
        return self.project or self.home

    def team(self) -> list[str]:
        return [lid for lid, ln in self.lanes.items() if ln.kind == "team"]

    def instructions(self, lane: str) -> Path:
        ln = self.lanes[lane]
        return self.home / (ln.instructions or f"{TOWER_DIR}/lanes/{lane}.md")


def find_home(start: Path | None = None) -> Path:
    env = os.environ.get("TOWER_HOME")
    if env:
        return Path(env).expanduser().resolve()
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / TOWER_DIR / CONFIG_NAME).is_file():
            return d
    raise ConfigError("No .tower/tower.json found here or above. Run `tower init` in your workspace, "
                      "or set TOWER_HOME.")


def _lane(lid: str, raw: dict[str, Any], port: int | None) -> Lane:
    runtime = str(raw.get("runtime") or "opencode")
    if runtime not in RUNTIMES:
        raise ConfigError(f"lane {lid}: runtime must be one of {', '.join(RUNTIMES)}")
    agent = str(raw.get("agent") or "")
    if agent and runtime == "codex":
        raise ConfigError(f"lane {lid}: codex has no agents; pick a profile with \"args\": [\"--profile\", NAME]")
    kind = HUB if lid == HUB else INFRA if lid == INFRA else "team"
    return Lane(
        id=lid, kind=kind, runtime=runtime, label=str(raw.get("label") or lid),
        port=int(raw["port"]) if raw.get("port") else (port if runtime == "opencode" else None),
        model=str(raw.get("model") or ""),
        agent=agent,
        args=[str(a) for a in raw["args"]] if isinstance(raw.get("args"), list) else list(DEFAULT_ARGS[runtime]),
        instructions=str(raw.get("instructions") or f"{TOWER_DIR}/lanes/{lid}.md"),
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
    )


def load(home: Path | None = None) -> Config:
    home = home or find_home()
    path = home / TOWER_DIR / CONFIG_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path} does not exist. Run `tower init`.")
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}")
    lanes_raw = raw.get("lanes") or {}
    if HUB not in lanes_raw:
        raise ConfigError("tower.json needs a `hub` lane.")
    lanes: dict[str, Lane] = {}
    first_port = int(raw.get("opencode_port") or FIRST_OPENCODE_PORT)
    next_port = first_port
    for lid, lraw in lanes_raw.items():
        if not lid.replace("-", "").replace("_", "").isalnum():
            raise ConfigError(f"lane id {lid!r}: use letters, digits, - and _")
        lanes[lid] = _lane(lid, lraw or {}, next_port)
        next_port += 1
    ports = [ln.port for ln in lanes.values() if ln.port]
    if len(ports) != len(set(ports)):
        raise ConfigError("two lanes share one port")
    web = raw.get("web") or {}
    auth = raw.get("auth") or {}
    mode = str(auth.get("mode") or "local")
    if mode not in ("local", "header", "token"):
        raise ConfigError("auth.mode must be local, header or token")
    if mode == "header" and not auth.get("header"):
        raise ConfigError("auth.mode header needs auth.header (e.g. Cf-Access-Authenticated-User-Email)")
    project = raw.get("project")
    return Config(
        home=home, name=str(raw.get("name") or "Agent Hub"), lang=str(raw.get("lang") or "en"),
        project=(home / project).resolve() if project else None,
        web_host=str(web.get("host") or "127.0.0.1"), web_port=int(web.get("port") or 8787),
        public_url=str(web.get("public_url") or ""),
        auth_mode=mode, auth_header=str(auth.get("header") or ""),
        max_lanes=int(raw.get("max_lanes") or 2),
        opencode_port=first_port,
        notify={str(k): str(v) for k, v in (raw.get("notify") or {}).items()},
        tunnel=str(raw.get("tunnel") or ""),
        lanes=lanes,
    )


def template(name: str, lang: str, hub: str, infra: str, safe: bool = False, web_port: int = 8787,
             opencode_port: int = FIRST_OPENCODE_PORT) -> dict[str, Any]:
    args = SAFE_ARGS if safe else DEFAULT_ARGS
    return {
        "name": name,
        "lang": lang,
        "web": {"host": "127.0.0.1", "port": web_port, "public_url": ""},
        "auth": {"mode": "local"},
        "max_lanes": 2,
        "opencode_port": opencode_port,
        "notify": {},
        "tunnel": "",
        "lanes": {
            HUB: {"runtime": hub, "label": "Hub", "args": args[hub]},
            INFRA: {"runtime": infra, "label": "Infra", "args": args[infra]},
        },
    }


# ---------- the workspaces this person runs a tower in (one board can switch between them) ----------

def registry_path() -> Path:
    base = os.environ.get("AGENT_HUB_HOME")
    if not base:
        base = str(Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "agent-hub")
    return Path(base).expanduser() / "workspaces.json"


def workspaces() -> dict[str, Path]:
    """slug -> workspace home, in the order they were registered."""
    try:
        data = json.loads(registry_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get("workspaces") if isinstance(data, dict) else None
    return {str(k): Path(v["home"]) for k, v in (raw or {}).items() if isinstance(v, dict) and v.get("home")}


def _save_workspaces(regs: dict[str, Path]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"workspaces": {k: {"home": str(v)} for k, v in regs.items()}}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def register(home: Path) -> str:
    home = home.resolve()
    regs = workspaces()
    for slug, h in regs.items():
        if h == home:
            return slug
    base = re.sub(r"[^a-z0-9]+", "-", home.name.lower()).strip("-") or "workspace"
    slug, n = base, 2
    while slug in regs:
        slug, n = f"{base}-{n}", n + 1
    regs[slug] = home
    _save_workspaces(regs)
    return slug


def unregister(slug: str) -> None:
    regs = workspaces()
    if slug not in regs:
        raise ConfigError(f"no workspace {slug} (`tower workspaces`)")
    del regs[slug]
    _save_workspaces(regs)


def free_ports(home: Path) -> tuple[int, int]:
    """A board port and an OpenCode base port no other registered workspace uses and nothing listens on."""
    used: set[int] = set()
    for h in workspaces().values():
        if h.resolve() == home.resolve():
            continue
        try:
            c = load(h)
        except ConfigError:
            continue
        used.add(c.web_port)
        used.update(ln.port for ln in c.lanes.values() if ln.port)

    def free(port: int) -> bool:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                return False
            return True

    web = next(p for p in range(8787, 8887) if p not in used and free(p))
    base = next(b for b in range(FIRST_OPENCODE_PORT, 5301, 10)
                if not used & set(range(b, b + 10)) and all(free(p) for p in range(b, b + 3)))
    return web, base


def tower_pid(c: Config) -> int | None:
    try:
        pid = int((c.state_dir / "tower.pid").read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def secret(value: str) -> str:
    """Config values may point at the environment (`env:NAME`) so secrets stay out of tower.json."""
    if value.startswith("env:"):
        return os.environ.get(value[4:], "")
    return value
