"""Workspace config: `<home>/.tower/tower.json`.

The home is the folder whose `.tower/` holds the config. Agents run in `project` (default: the home itself),
or a team lane with "worktree": true in its own git worktree under `.tower/state/worktrees/<lane>`.
State (SQLite DB, logs, members) lives in `<home>/.tower/state/`, which must stay out of git.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import procs

HUB = "hub"
INFRA = "infra"
RUNTIMES = ("opencode", "claude", "codex")
TERMINALS = ("none", "tmux", "orca")  # where a lane's session runs: in the background, or in a window people watch
TOWER_DIR = ".tower"
CONFIG_NAME = "tower.json"
INSTANCE_SEP = "#"          # a parallel session of a team lane: `docs#2`
DEFAULT_ENGINE = "default"  # the implicit engine of an install that declares none: every team lane without one
FIRST_OPENCODE_PORT = 4301

DEFAULT_ARGS = {
    # Lanes run unattended, so they need to act without a human approving each tool call.
    # `dah init --safe` writes the stricter variants instead; see SETUP.md "Permissions".
    "claude": ["--permission-mode", "bypassPermissions"],
    "codex": ["--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check"],
    "opencode": [],
}
SAFE_ARGS = {
    # acceptEdits denies every Bash call not on an allow list, and a lane that cannot run `dah` cannot report.
    "claude": ["--permission-mode", "acceptEdits", "--allowedTools", "Bash(dah:*)"],
    "codex": ["--sandbox", "workspace-write", "--skip-git-repo-check"],
    "opencode": [],
}


class ConfigError(Exception):
    pass


def base_lane(lane: str) -> str:
    """`docs#2` -> `docs`: the lane an instance belongs to."""
    return lane.partition(INSTANCE_SEP)[0]


def instance_lane(base: str, n: int) -> str:
    return f"{base}{INSTANCE_SEP}{n}"


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
    terminal: str = "none"       # none: the tower runs the session in the background; tmux | orca: in a window
    worktree: bool = False       # team lanes: work in a git worktree of their own on branch dah/<lane>
    engine: str = ""             # team lanes: the engine it belongs to (resolved in load); hub and infra have none
    base: str = ""               # instance lanes (`docs#2`): the team lane they are parallel sessions of


@dataclass
class Engine:
    id: str
    label: str = ""
    dir: str = ""                # engine folder, relative to home (process cards, pattern cards)
    lanes: list[str] = field(default_factory=list)  # team lanes in this engine (filled from lane.engine as well)
    library: str = ""            # pattern library markdown, relative to home
    skills: str = ""             # skills folder, relative to home
    map: str = ""                # optional hand-written engine.json, relative to home
    skill_names: list[str] = field(default_factory=list)  # skill folders this engine owns besides the ones its lanes name
    cap: str = ""                # optional usage cap: "tokens-day:N" (built in) or a name an extension registers in `caps`
    implicit: bool = False       # made up because tower.json declares no `engines`


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
    terminal: str = "none"       # default for lanes that do not set their own
    worktree: bool = False       # default for team lanes that do not set their own
    stage_table: str = ""        # markdown table with a `model · effort` column (stagemodel.py), path from home
    require_model_lines: bool = False  # `dah task` refuses a brief without `model:` and `effort:` lines
    require_line: bool = False   # `dah task` refuses a brief whose `Line:` header is missing or names an unconfirmed method (engines.py)
    engines: dict[str, Engine] = field(default_factory=dict)  # always has at least "default" when a team lane has no engine
    light_lanes: int | None = None  # extra slots (beyond max_lanes) for tasks that touch none of heavy_paths; None = one pool
    max_instances: int = 1       # sessions one team lane may run at once: the lane itself plus `lane#2` ... (1 = no parallel sessions)
    heavy_paths: list[str] = field(default_factory=list)  # a task whose `paths:` fall under one of these takes a max_lanes slot
    memory_limit_mb: int = 0     # a lane session whose process passes this is renewed when idle (memwatch.py); 0 = off
    instances: dict[str, Lane] = field(default_factory=dict)  # `lane#N` copies of each team lane, up to max_instances

    @property
    def all_lanes(self) -> dict[str, Lane]:
        """The configured lanes plus their parallel instances."""
        return {**self.lanes, **self.instances}

    @property
    def state_dir(self) -> Path:
        return self.home / TOWER_DIR / "state"

    @property
    def db(self) -> Path:
        return self.state_dir / "tower.db"

    @property
    def workdir(self) -> Path:
        return self.project or self.home

    @property
    def local_only(self) -> bool:
        """Nothing set up to reach the board from another device (no public URL, tunnel, proxy or network bind)."""
        return (self.web_host in ("127.0.0.1", "localhost", "::1") and not self.public_url and not self.tunnel
                and self.auth_mode == "local")

    def lane_dir(self, lane: str) -> Path:
        """Where a lane's agent works: the workspace, or the lane's own git worktree."""
        base = base_lane(lane)  # instances share their lane's working tree: that is why their `paths:` may not overlap
        return self.state_dir / "worktrees" / base if self.lanes[base].worktree else self.workdir

    def team(self) -> list[str]:
        return [lid for lid, ln in self.lanes.items() if ln.kind == "team"]

    def instructions(self, lane: str) -> Path:
        ln = self.all_lanes[lane]
        return self.home / (ln.instructions or f"{TOWER_DIR}/lanes/{lane}.md")


def find_home(start: Path | None = None) -> Path:
    env = os.environ.get("TOWER_HOME")
    if env:
        return Path(env).expanduser().resolve()
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / TOWER_DIR / CONFIG_NAME).is_file():
            return d
    raise ConfigError("No .tower/tower.json found here or above. Run `dah init` in your workspace, "
                      "or set TOWER_HOME.")


def ext_names(home: Path, table: str) -> list[str]:
    """Runtimes or terminals the workspace's `.tower/ext/*.py` register. Loaded only when tower.json names one
    that is not built in."""
    from .ext import registry
    return sorted(registry(home).table(table))


def _terminal(where: str, value: Any, home: Path) -> str:
    term = str(value or "none")
    if term not in TERMINALS and term not in ext_names(home, "terminals"):
        raise ConfigError(f"{where}: terminal must be one of {', '.join(TERMINALS)}, or one an extension in "
                          f".tower/ext/ registers")
    return term


def _lane(lid: str, raw: dict[str, Any], port: int | None, terminal: str = "none", home: Path = Path("."),
          worktree: bool = False) -> Lane:
    runtime = str(raw.get("runtime") or "opencode")
    if runtime not in RUNTIMES and runtime not in ext_names(home, "runtimes"):
        raise ConfigError(f"lane {lid}: runtime must be one of {', '.join(RUNTIMES)}, or one an extension in "
                          f".tower/ext/ registers")
    agent = str(raw.get("agent") or "")
    if agent and runtime == "codex":
        raise ConfigError(f"lane {lid}: codex has no agents; pick a profile with \"args\": [\"--profile\", NAME]")
    kind = HUB if lid == HUB else INFRA if lid == INFRA else "team"
    if raw.get("worktree") and kind != "team":
        raise ConfigError(f"lane {lid}: \"worktree\" is for team lanes; {lid} works in the workspace itself")
    engine = str(raw.get("engine") or "")
    if engine and kind != "team":
        raise ConfigError(f"lane {lid}: \"engine\" is for team lanes; {lid} runs the whole install")
    return Lane(
        id=lid, kind=kind, engine=engine, runtime=runtime, label=str(raw.get("label") or lid),
        port=int(raw["port"]) if raw.get("port") else (port if runtime == "opencode" else None),
        model=str(raw.get("model") or ""),
        agent=agent,
        args=[str(a) for a in raw["args"]] if isinstance(raw.get("args"), list) else list(DEFAULT_ARGS.get(runtime, [])),
        instructions=str(raw.get("instructions") or f"{TOWER_DIR}/lanes/{lid}.md"),
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
        terminal=_terminal(f"lane {lid}", raw["terminal"], home) if raw.get("terminal") else terminal,
        worktree=bool(raw["worktree"]) if "worktree" in raw else (worktree and kind == "team"),
    )


_ENGINE_PATHS = ("dir", "library", "skills", "map")


def _engines(raw: Any, lanes: dict[str, Lane]) -> dict[str, Engine]:
    """Declared engines plus the implicit "default" one for team lanes that name none. A lane's engine comes from its
    own `engine` key or from an engine's `lanes` list; naming two different engines is an error."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("engines must be an object: {\"name\": {...}}")
    out: dict[str, Engine] = {}
    for eid, e in raw.items():
        if not str(eid).replace("-", "").replace("_", "").isalnum():
            raise ConfigError(f"engine id {eid!r}: use letters, digits, - and _")
        if not isinstance(e, dict):
            raise ConfigError(f"engine {eid}: must be an object")
        for key in _ENGINE_PATHS:
            if e.get(key) and (Path(str(e[key])).is_absolute() or ".." in Path(str(e[key])).parts):
                raise ConfigError(f"engine {eid}: {key} is a path inside the workspace, got {e[key]!r}")
        if not isinstance(e.get("lanes", []), list):
            raise ConfigError(f"engine {eid}: lanes must be a list of lane ids")
        out[eid] = Engine(id=eid, label=str(e.get("label") or eid), lanes=[], cap=str(e.get("cap") or "").strip(),
                          skill_names=[str(x) for x in e.get("skill_names") or [] if isinstance(x, str)],
                          **{k: str(e.get(k) or "") for k in _ENGINE_PATHS})
        for lid in e.get("lanes") or []:
            ln = lanes.get(str(lid))
            if ln is None or ln.kind != "team":
                raise ConfigError(f"engine {eid}: {lid!r} is not a team lane")
            if ln.engine and ln.engine != eid:
                raise ConfigError(f"lane {lid} is in engine {ln.engine} (its own key) and in {eid} (engine list)")
            ln.engine = eid
    for ln in lanes.values():
        if ln.kind != "team":
            continue
        if not ln.engine:
            ln.engine = DEFAULT_ENGINE
            if DEFAULT_ENGINE not in out:
                out[DEFAULT_ENGINE] = Engine(id=DEFAULT_ENGINE, label="Default", implicit=True)
        elif ln.engine not in out:
            raise ConfigError(f"lane {ln.id}: engine {ln.engine!r} is not in `engines`")
        out[ln.engine].lanes.append(ln.id)
    if not out:  # no team lane yet: the implicit engine still exists, so `dah engine list` is never empty
        out[DEFAULT_ENGINE] = Engine(id=DEFAULT_ENGINE, label="Default", implicit=True)
    return out


def load(home: Path | None = None) -> Config:
    home = home or find_home()
    path = home / TOWER_DIR / CONFIG_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path} does not exist. Run `dah init`.")
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}")
    lanes_raw = raw.get("lanes") or {}
    if HUB not in lanes_raw:
        raise ConfigError("tower.json needs a `hub` lane.")
    lanes: dict[str, Lane] = {}
    terminal = _terminal("tower.json", raw.get("terminal"), home)
    worktree = bool(raw.get("worktree"))
    first_port = int(raw.get("opencode_port") or FIRST_OPENCODE_PORT)
    next_port = first_port
    for lid, lraw in lanes_raw.items():
        if not lid.replace("-", "").replace("_", "").isalnum():
            raise ConfigError(f"lane id {lid!r}: use letters, digits, - and _")
        lanes[lid] = _lane(lid, lraw or {}, next_port, terminal, home, worktree)
        next_port += 1
    max_instances = max(1, int(raw.get("max_instances") or 1))
    instances: dict[str, Lane] = {}
    for lid, ln in list(lanes.items()):
        for n in range(2, max_instances + 1) if ln.kind == "team" else ():
            key = instance_lane(lid, n)
            instances[key] = dataclasses.replace(ln, id=key, base=lid, label=f"{ln.label} #{n}",
                                                 port=next_port if ln.port else None, args=list(ln.args), env=dict(ln.env))
            next_port += 1 if ln.port else 0
    ports = [ln.port for ln in [*lanes.values(), *instances.values()] if ln.port]
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
    engines = _engines(raw.get("engines"), lanes)
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
        terminal=terminal,
        worktree=worktree,
        stage_table=str(raw.get("stage_table") or ""),
        require_model_lines=bool(raw.get("require_model_lines")),
        require_line=bool(raw.get("require_line")),
        engines=engines,
        light_lanes=int(raw["light_lanes"]) if raw.get("light_lanes") is not None else None,
        max_instances=max_instances,
        heavy_paths=[str(p).strip().strip("/") for p in raw.get("heavy_paths") or [] if str(p).strip().strip("/")],
        memory_limit_mb=max(0, int(raw.get("memory_limit_mb") or 0)),
        instances=instances,
    )


def template(name: str, lang: str, hub: str, infra: str, safe: bool = False, web_port: int = 8787,
             opencode_port: int = FIRST_OPENCODE_PORT, terminal: str = "none") -> dict[str, Any]:
    args = SAFE_ARGS if safe else DEFAULT_ARGS
    data = {
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
    if terminal != "none":
        data["terminal"] = terminal
    return data


# ---------- the workspaces this person runs a tower in (one board can switch between them) ----------

def registry_path() -> Path:
    base = os.environ.get("AGENT_HUB_HOME")
    if not base and os.name == "nt" and os.environ.get("APPDATA"):
        base = str(Path(os.environ["APPDATA"]) / "agent-hub")
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
        raise ConfigError(f"no workspace {slug} (`dah workspaces`)")
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
        pid = int((c.state_dir / "tower.pid").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return pid if procs.alive(pid) else None


def secret(value: str) -> str:
    """Config values may point at the environment (`env:NAME`) so secrets stay out of tower.json."""
    if value.startswith("env:"):
        return os.environ.get(value[4:], "")
    return value
