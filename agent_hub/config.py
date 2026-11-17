"""Workspace config: `<home>/.tower/tower.json`.

The home is the folder whose `.tower/` holds the config. Agents run in `project` (default: the home itself),
or a team lane with "worktree": true in its own git worktree under `.tower/state/worktrees/<lane>`.
State (SQLite DB, logs, members) lives in `<home>/.tower/state/`, which must stay out of git.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import procs

HUB = "hub"
INFRA = "infra"
RUNTIMES = ("opencode", "claude", "codex")
TERMINALS = ("none", "tmux", "orca")  # where a lane's session runs: in the background, or in a window people watch
TOWER_DIR = ".tower"
CONFIG_NAME = "tower.json"
DEFAULT_CONTEXT_LIMIT_K = 0
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


def is_infra(lane: str) -> bool:
    return base_lane(lane) == INFRA


def lane_slug(lane: str) -> str:
    return lane.replace(INSTANCE_SEP, "-")


def lane_branch(lane: str) -> str:
    return f"dah/{lane_slug(lane)}"


PERSON_HUB_PREFIX = HUB + "-"
DEFAULT_HUB_CAP = 3
HUB_SLUG_MAX = 32


def _slug(member: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", member.lower()).strip("-_")[:HUB_SLUG_MAX].strip("-_")


def person_hub_id(member: str) -> str:
    slug = _slug(member)
    if not slug and member.strip():
        slug = "u" + hashlib.sha1(member.encode("utf-8")).hexdigest()[:8]
    return PERSON_HUB_PREFIX + slug if slug else ""


def auto_member_name(ident: str, taken: Iterable[str]) -> str:
    base = re.sub(r"[^a-z0-9._-]+", "-", ident.split("@", 1)[0].strip().lower()).strip("-_.") or "member"
    names = {t.lower() for t in taken}
    hubs = {person_hub_id(t) for t in taken}
    name, n = base, 1
    while name in names or person_hub_id(name) in hubs or person_hub_id(name) == PERSON_HUB_PREFIX + HUB:
        n += 1
        name = f"{base}-{n}"
    return name


def member_names(db: Path) -> list[str]:
    import sqlite3
    if not db.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            return [str(r[0]) for r in con.execute("SELECT name FROM members ORDER BY created, name")]
        finally:
            con.close()
    except sqlite3.Error:
        return []


def _person_hub_options(raw: Any) -> tuple[int, bool, list[str]]:
    if raw is True:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("person_hubs must be false, true or an object: {\"max\": 3, \"auto\": false, \"domains\": []}")
    unknown = set(raw) - {"max", "auto", "domains"}
    if unknown:
        raise ConfigError(f"person_hubs: unknown key {', '.join(sorted(unknown))} (max, auto, domains)")
    cap = _int(raw, "max", DEFAULT_HUB_CAP, 1, "person_hubs")
    auto = _bool(raw, "auto", False, "person_hubs")
    domains = raw.get("domains") or []
    if isinstance(domains, str):
        domains = [domains]
    if not isinstance(domains, list) or not all(isinstance(d, str) for d in domains):
        raise ConfigError("person_hubs.domains must be a list of email domains")
    return cap, auto, [d.strip().lower().lstrip("@") for d in domains if d.strip()]


def _person_hubs(home: Path, lanes: dict[str, Lane], cap: int, next_port: int) -> tuple[dict[str, Lane], list[str]]:
    hub, out, overflow = lanes[HUB], {}, []
    for name in member_names(home / TOWER_DIR / "state" / "tower.db"):
        lid = person_hub_id(name)
        if not lid:
            continue
        if lid in lanes or lid in out:
            lid = f"{lid}-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:4]}"
        if lid in lanes or lid in out:
            continue
        if len(out) >= cap:
            overflow.append(name)
            continue
        out[lid] = dataclasses.replace(hub, id=lid, owner=name, label=f"{hub.label} · {name}",
                                       port=next_port if hub.port else None, args=list(hub.args), env=dict(hub.env))
        next_port += 1 if hub.port else 0
    return out, overflow


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
    worktree: bool = False       # team lanes: work in a git worktree of their own on branch dah/<lane> (dah/<lane>-N for instance #N)
    max_instances: int = 0       # team lanes: sessions this lane may run at once (0 = the top-level max_instances); infra: its own (0 = 1)
    engine: str = ""             # team lanes: the engine it belongs to (resolved in load); hub and infra have none
    base: str = ""               # instance lanes (`docs#2`): the team lane they are parallel sessions of
    context_limit_k: int | None = None
    owner: str = ""


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
    context_limit_k: int = DEFAULT_CONTEXT_LIMIT_K
    model_limits_k: dict[str, int] = field(default_factory=dict)
    instances: dict[str, Lane] = field(default_factory=dict)  # `lane#N` copies of each team lane, up to max_instances
    done_gate: dict[str, Any] = field(default_factory=dict)  # donegate.parse: paths, command, setup, verify, timeout, memory_mb
    scratch_root: str = ""       # dah-owned folder outside the project for the gate worktree and task scratch (scratch.py)
    scratch_grace: int = 600     # seconds after a task closes before its scratch is swept
    branch_sync: bool = False
    branch_sync_seconds: int = 0
    branch_sync_push: bool = False
    leftover_sweep: bool = False
    schedules: list[Any] = field(default_factory=list)  # schedules.Schedule: a task or request queued on a clock (opt-in)
    person_hubs: dict[str, Lane] = field(default_factory=dict)
    hub_cap: int = 0
    hub_overflow: list[str] = field(default_factory=list)
    hub_auto: bool = False
    hub_domains: list[str] = field(default_factory=list)
    fair_turns: bool = False
    requester: bool = False
    usage_percent: str = ""      # "claude" = show the Claude subscription percent on the board (claudeusage.py); "" = off
    uploads: dict[str, Any] = field(default_factory=dict)  # chat attachments: storage.parse; {} = off (a loaded config has provider local by default)

    @property
    def all_lanes(self) -> dict[str, Lane]:
        """The configured lanes plus their parallel instances."""
        return {**self.lanes, **self.instances, **self.person_hubs}

    def hubs(self) -> list[str]:
        return [HUB, *self.person_hubs]

    def is_hub(self, lane: str) -> bool:
        return lane == HUB or lane in self.person_hubs

    def hub_of(self, member: str) -> str:
        return next((lid for lid, ln in self.person_hubs.items() if ln.owner == member), HUB)

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
        """Where a lane's agent works: the workspace, or this session's own git worktree. Without "worktree": true a
        lane and its instances share the workspace, which is why their `paths:` may not overlap."""
        ln = self.lanes.get(base_lane(lane))
        return self.state_dir / "worktrees" / lane_slug(lane) if ln and ln.worktree else self.workdir

    def branch_of(self, lane: str) -> str:
        ln = self.lanes.get(base_lane(lane))
        return lane_branch(lane) if ln and ln.kind == "team" and ln.worktree else ""

    def instance_cap(self, lane: str) -> int:
        ln = self.lanes.get(base_lane(lane))
        if ln and ln.kind == INFRA:
            return ln.max_instances or 1
        return (ln.max_instances or self.max_instances) if ln and ln.kind == "team" else 1

    def infra_sessions(self) -> list[str]:
        if INFRA not in self.lanes:
            return []
        return [INFRA, *(instance_lane(INFRA, n) for n in range(2, self.instance_cap(INFRA) + 1))]

    def context_limit(self, lane: str, model: str = "") -> int:
        ln = self.lanes.get(base_lane(lane))
        if not ln or ln.kind != "team":
            return 0
        if ln.context_limit_k is not None:
            return ln.context_limit_k
        name = (model or ln.model).lower()
        for key in sorted(self.model_limits_k, key=len, reverse=True):
            if key in name:
                return self.model_limits_k[key]
        return self.context_limit_k

    def hub_context_limit(self, lane: str) -> int:
        ln = self.all_lanes.get(lane)
        return (ln.context_limit_k or 0) if ln and ln.kind == HUB else 0

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


def _int(raw: dict[str, Any], key: str, default: int, minimum: int | None = None, where: str = "tower.json") -> int:
    v = raw.get(key)
    if v is None or v == "":
        return default
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        raise ConfigError(f"{where}: \"{key}\" must be a whole number, got {v!r}")
    try:
        n = int(v)
    except ValueError:
        raise ConfigError(f"{where}: \"{key}\" must be a whole number, got {v!r}")
    if minimum is not None and n < minimum:
        raise ConfigError(f"{where}: \"{key}\" must be at least {minimum}, got {n}")
    return n


def _bool(raw: dict[str, Any], key: str, default: bool = False, where: str = "tower.json") -> bool:
    v = raw.get(key, default)
    if v is None:
        return default
    if not isinstance(v, bool):
        raise ConfigError(f"{where}: \"{key}\" must be true or false (a JSON boolean), got {v!r}")
    return v


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
    if _bool(raw, "worktree", False, f"lane {lid}") and kind != "team":
        raise ConfigError(f"lane {lid}: \"worktree\" is for team lanes; {lid} works in the workspace itself")
    if raw.get("max_instances") is not None and kind == HUB:
        raise ConfigError(f"lane {lid}: \"max_instances\" is for team lanes and infra; the hub runs one session")
    cap = _int(raw, "max_instances", 0, 1, f"lane {lid}")
    limit = raw.get("context_limit_k")
    if limit is not None and kind == INFRA:
        raise ConfigError(f"lane {lid}: \"context_limit_k\" is for team lanes and the hub (infra runs one memo per session)")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 0):
        raise ConfigError(f"lane {lid}: \"context_limit_k\" must be a whole number of k tokens (0 = no limit)")
    engine = str(raw.get("engine") or "")
    if engine and kind != "team":
        raise ConfigError(f"lane {lid}: \"engine\" is for team lanes; {lid} runs the whole install")
    return Lane(
        id=lid, kind=kind, engine=engine, runtime=runtime, label=str(raw.get("label") or lid),
        port=_int(raw, "port", 0, 1, f"lane {lid}") or (port if runtime == "opencode" else None),
        model=str(raw.get("model") or ""),
        agent=agent,
        args=[str(a) for a in raw["args"]] if isinstance(raw.get("args"), list) else list(DEFAULT_ARGS.get(runtime, [])),
        instructions=str(raw.get("instructions") or f"{TOWER_DIR}/lanes/{lid}.md"),
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
        terminal=_terminal(f"lane {lid}", raw["terminal"], home) if raw.get("terminal") else terminal,
        worktree=_bool(raw, "worktree", False, f"lane {lid}") if "worktree" in raw else (worktree and kind == "team"),
        context_limit_k=limit,
        max_instances=cap,
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
            if e.get(key) and (Path(str(e[key])).is_absolute() or str(e[key]).startswith(("/", "\\")) or ".." in Path(str(e[key])).parts):
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
    return from_raw(home, raw)


def from_raw(home: Path, raw: Any) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError(f"{home / TOWER_DIR / CONFIG_NAME} must hold a JSON object")
    lanes_raw = raw.get("lanes") or {}
    if HUB not in lanes_raw:
        raise ConfigError("tower.json needs a `hub` lane.")
    lanes: dict[str, Lane] = {}
    terminal = _terminal("tower.json", raw.get("terminal"), home)
    worktree = _bool(raw, "worktree")
    first_port = _int(raw, "opencode_port", FIRST_OPENCODE_PORT, 1)
    next_port = first_port
    for lid, lraw in lanes_raw.items():
        if not lid.replace("-", "").replace("_", "").isalnum():
            raise ConfigError(f"lane id {lid!r}: use letters, digits, - and _")
        lanes[lid] = _lane(lid, lraw or {}, next_port, terminal, home, worktree)
        next_port += 1
    max_instances = _int(raw, "max_instances", 1, 1)
    instances: dict[str, Lane] = {}
    for lid, ln in list(lanes.items()):
        for n in range(2, (ln.max_instances or (max_instances if ln.kind == "team" else 1)) + 1) \
                if ln.kind in ("team", INFRA) else ():
            key = instance_lane(lid, n)
            if ln.worktree and lane_slug(key) in lanes:
                raise ConfigError(f"lane {lane_slug(key)}: its worktree and branch name are the ones {lid} #{n} uses "
                                  f"({lane_branch(key)}); rename one of them")
            instances[key] = dataclasses.replace(ln, id=key, base=lid, label=f"{ln.label} #{n}",
                                                 port=next_port if ln.port else None, args=list(ln.args), env=dict(ln.env))
            next_port += 1 if ln.port else 0
    hub_cap, person_hubs, hub_overflow, hub_auto, hub_domains = 0, {}, [], False, []
    if raw.get("person_hubs"):
        hub_cap, hub_auto, hub_domains = _person_hub_options(raw["person_hubs"])
        if reserved := [lid for lid in lanes if lid.startswith(PERSON_HUB_PREFIX)]:
            raise ConfigError(f"lane id {reserved[0]!r}: ids starting with {PERSON_HUB_PREFIX!r} belong to person hubs")
        person_hubs, hub_overflow = _person_hubs(home, lanes, hub_cap, next_port)
    ports = [ln.port for ln in [*lanes.values(), *instances.values(), *person_hubs.values()] if ln.port]
    if len(ports) != len(set(ports)):
        raise ConfigError("two lanes share one port")
    web = raw.get("web") or {}
    auth = raw.get("auth") or {}
    mode = str(auth.get("mode") or "local")
    if mode not in ("local", "header", "token"):
        raise ConfigError("auth.mode must be local, header or token")
    if mode == "header" and not auth.get("header"):
        raise ConfigError("auth.mode header needs auth.header (e.g. Cf-Access-Authenticated-User-Email)")
    exposed = str(web.get("host") or "127.0.0.1") not in ("127.0.0.1", "localhost", "::1") or web.get("public_url") or raw.get("tunnel")
    if mode == "local" and exposed and not auth.get("allow_exposed"):
        raise ConfigError("auth.mode local trusts everyone who reaches the board, but web.host, web.public_url or tunnel "
                          "expose it: use auth.mode header or token (or set auth.allow_exposed true to accept that)")
    project = raw.get("project")
    engines = _engines(raw.get("engines"), lanes)
    return Config(
        home=home, name=str(raw.get("name") or "Agent Hub"), lang=str(raw.get("lang") or "en"),
        project=(home / project).resolve() if project else None,
        web_host=str(web.get("host") or "127.0.0.1"), web_port=_int(web, "port", 8787, 1, "web"),
        public_url=str(web.get("public_url") or ""),
        auth_mode=mode, auth_header=str(auth.get("header") or ""),
        max_lanes=_int(raw, "max_lanes", 2, 1),
        opencode_port=first_port,
        notify={str(k): str(v) for k, v in (raw.get("notify") or {}).items()},
        tunnel=str(raw.get("tunnel") or ""),
        lanes=lanes,
        terminal=terminal,
        worktree=worktree,
        stage_table=str(raw.get("stage_table") or ""),
        require_model_lines=_bool(raw, "require_model_lines"),
        require_line=_bool(raw, "require_line"),
        engines=engines,
        light_lanes=_int(raw, "light_lanes", 0, 0) if raw.get("light_lanes") is not None else None,
        max_instances=max_instances,
        heavy_paths=[str(p).strip().strip("/") for p in raw.get("heavy_paths") or [] if str(p).strip().strip("/")],
        memory_limit_mb=_int(raw, "memory_limit_mb", 0, 0),
        context_limit_k=_limit_k(raw),
        model_limits_k=_model_limits_k(raw),
        instances=instances,
        person_hubs=person_hubs,
        hub_cap=hub_cap,
        hub_overflow=hub_overflow,
        hub_auto=hub_auto,
        hub_domains=hub_domains,
        **_extras(raw, home, project, {lid: ln.kind for lid, ln in lanes.items()}),
    )


LIMIT_K_ERROR = ("context_limit_k must be a whole number of k tokens (0 = no limit), or a table of them by model: "
                 "{\"default\": 400, \"sonnet\": 300, \"opus\": 400}")


def _whole_k(v: Any) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise ConfigError(LIMIT_K_ERROR)
    return v


def _limit_k(raw: dict[str, Any]) -> int:
    v = raw.get("context_limit_k", DEFAULT_CONTEXT_LIMIT_K)
    if isinstance(v, dict):
        return _whole_k(v.get("default", DEFAULT_CONTEXT_LIMIT_K))
    return _whole_k(v)


def _model_limits_k(raw: dict[str, Any]) -> dict[str, int]:
    v = raw.get("context_limit_k")
    if not isinstance(v, dict):
        return {}
    out = {str(k).strip().lower(): _whole_k(n) for k, n in v.items() if str(k).strip().lower() != "default"}
    if "" in out:
        raise ConfigError(LIMIT_K_ERROR)
    return out


def _extras(raw: dict[str, Any], home: Path, project: Any, kinds: dict[str, str]) -> dict[str, Any]:
    from .claudeusage import PROVIDERS
    from .donegate import parse
    from .scratch import check_root
    try:
        gate = parse(raw.get("done_gate"))
    except ValueError as e:
        raise ConfigError(str(e))
    root = str(raw.get("scratch_root") or "")
    if root:
        p = Path(root).expanduser()
        try:
            check_root(p if p.is_absolute() else home / p, (home / project).resolve() if project else home)
        except ValueError as e:
            raise ConfigError(str(e))
    from .schedules import parse as parse_schedules
    try:
        schedules = parse_schedules(raw.get("schedules"), kinds)
    except ValueError as e:
        raise ConfigError(str(e))
    usage = str(raw.get("usage_percent") or "")
    if usage and usage not in PROVIDERS:
        raise ConfigError(f"usage_percent must be one of {', '.join(PROVIDERS)}, or empty (off)")
    from .storage import parse as parse_uploads
    try:
        uploads = parse_uploads(raw.get("uploads"))
    except ValueError as e:
        raise ConfigError(str(e))
    return {"done_gate": gate, "scratch_root": root, "usage_percent": usage, "schedules": schedules, "uploads": uploads,
            "scratch_grace": _int(raw, "scratch_grace", 600, 0),
            "leftover_sweep": _bool(raw, "leftover_sweep"),
            "requester": _bool(raw, "requester"),
            "fair_turns": _bool(raw, "fair_turns"),
            "branch_sync": _bool(raw, "branch_sync"),
            "branch_sync_seconds": _int(raw, "branch_sync_seconds", 0, 0),
            "branch_sync_push": _bool(raw, "branch_sync_push")}


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
            "research": {"runtime": hub, "label": "Research", "args": args[hub], **({"model": "opus"} if hub == "claude" else {})},
            "audit": {"runtime": hub, "label": "Engine audit", "args": args[hub], **({"model": "fable"} if hub == "claude" else {})},
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


def _registry_data(strict: bool) -> Any:
    path = registry_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        if not strict:
            return {}
        backup = path.with_name(path.name + ".bak")
        try:
            shutil.copyfile(path, backup)
        except OSError:
            raise ConfigError(f"{path} cannot be read ({e}); fix or remove it first")
        raise ConfigError(f"{path} cannot be read ({e}); a copy is at {backup}. Fix or remove it, then try again")


def workspaces(strict: bool = False) -> dict[str, Path]:
    data = _registry_data(strict)
    raw = data.get("workspaces") if isinstance(data, dict) else None
    return {str(k): Path(v["home"]) for k, v in (raw or {}).items() if isinstance(v, dict) and v.get("home")}


def _save_workspaces(regs: dict[str, Path]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"workspaces": {k: {"home": str(v)} for k, v in regs.items()}}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def register(home: Path) -> str:
    home = home.resolve()
    regs = workspaces(strict=True)
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
    regs = workspaces(strict=True)
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
