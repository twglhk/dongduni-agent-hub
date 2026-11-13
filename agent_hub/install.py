"""Write the tower's files into a workspace: config, protocol, lane instructions, and a `/dah-<lane>` shortcut per
lane so a human can also open a lane by hand in their own agent (a command in OpenCode and Claude Code, a skill
in Codex). A lane is a role put on top of the agent the person already uses, not a new agent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .config import CONFIG_NAME, DEFAULT_ENGINE, HUB, INFRA, TOWER_DIR, Config, free_ports, template

TEMPLATES = Path(__file__).resolve().parent / "templates"
SHIM_PREFIX = "dah-"
SHIM_BODY = ("Read `.tower/lanes/{lane}.md` and `.tower/PROTOCOL.md` and follow them. You are the {lane} lane "
             "(run `dah` with `--as {lane}` if TOWER_LANE is not set).\n")
SHIM_DESC = "Work as the {lane} lane of this workspace's agent-hub"


def shim_files(lane: str, runtime: str) -> tuple[Path, str] | None:
    """Where the `/dah-<lane>` shortcut lives for one runtime, and its text."""
    name, desc, body = SHIM_PREFIX + lane, SHIM_DESC.format(lane=lane), SHIM_BODY.format(lane=lane)
    if runtime == "opencode":
        return Path(".opencode/command") / f"{name}.md", f"---\ndescription: {desc}\n---\n{body}\n$ARGUMENTS\n"
    if runtime == "claude":
        return Path(".claude/commands") / f"{name}.md", f"---\ndescription: {desc}\n---\n{body}\n$ARGUMENTS\n"
    if runtime == "codex":  # Codex has no project commands; a project skill is invoked the same way
        return Path(".agents/skills") / name / "SKILL.md", f"---\nname: {name}\ndescription: {desc}\n---\n{body}"
    return None


def write_shims(cfg: Config, lanes: list[str], force: bool, wrote: list[str]) -> None:
    for runtime in sorted({ln.runtime for ln in cfg.lanes.values()}):
        for lid in lanes:
            f = shim_files(lid, runtime)
            if f:
                _write(cfg.workdir / f[0], f[1], force, wrote, cfg.home)


def remove_shims(cfg: Config, lane: str) -> list[str]:
    """Delete a lane's shortcuts, but only files still exactly as we wrote them."""
    gone = []
    for runtime in ("opencode", "claude", "codex"):
        f = shim_files(lane, runtime)
        path = cfg.workdir / f[0] if f else None
        if f and path and path.is_file() and path.read_text(encoding="utf-8") == f[1]:
            path.unlink()
            if path.name == "SKILL.md" and not any(path.parent.iterdir()):
                path.parent.rmdir()
            gone.append(str(f[0]))
    return gone


def _write(path: Path, text: str, force: bool, wrote: list[str], home: Path) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    wrote.append(str(path.relative_to(home)) if home in path.parents else str(path))


REQUIRED_LANES = {"research": ("research.md", "opus"), "audit": ("audit.md", "fable")}  # role -> (template, claude model alias)


def required_lane_text(role: str, lane: str, skill: str) -> str:
    return (TEMPLATES / "lanes" / REQUIRED_LANES[role][0]).read_text(encoding="utf-8").replace("{lane}", lane).replace("{skill}", skill)


def lane_template(lane: str) -> str:
    name = {HUB: "hub.md", INFRA: "infra.md"}.get(lane)
    if name:
        return (TEMPLATES / "lanes" / name).read_text(encoding="utf-8")
    if lane in REQUIRED_LANES:
        return required_lane_text(lane, lane, lane)
    return (TEMPLATES / "lanes" / "team.md").read_text(encoding="utf-8").replace("{lane}", lane)


BLOCK_START, BLOCK_END = "<!-- dah:start -->", "<!-- dah:end -->"


def write_agents_block(home: Path, wrote: list[str]) -> None:
    """Put the short agent-hub pointer into CLAUDE.md and AGENTS.md so a plain session finds the tower. An existing
    file is only appended to (our own marked block is refreshed in place); a CLAUDE.md that imports AGENTS.md is left."""
    block = (TEMPLATES / "AGENTS-block.md").read_text(encoding="utf-8")
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = home / name
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if name == "CLAUDE.md" and "@AGENTS.md" in text.splitlines() and BLOCK_START in (home / "AGENTS.md").read_text(encoding="utf-8"):
            continue
        a, b = text.find(BLOCK_START), text.find(BLOCK_END)
        if a >= 0 and b > a:
            new = text[:a] + block.rstrip("\n") + text[b + len(BLOCK_END):]
        else:
            new = text + ("" if not text or text.endswith("\n\n") else "\n" if text.endswith("\n") else "\n\n") + block
        if new != text:
            path.write_text(new, encoding="utf-8")
            wrote.append(name)


def remove_agents_block(home: Path) -> list[str]:
    gone = []
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = home / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        a, b = text.find(BLOCK_START), text.find(BLOCK_END)
        if a >= 0 and b > a:
            rest = (text[:a].rstrip("\n") + "\n" + text[b + len(BLOCK_END):].lstrip("\n")).strip("\n")
            if rest:
                path.write_text(rest + "\n", encoding="utf-8")
            else:
                path.unlink()
            gone.append(f"removed the agent-hub block from {name}")
    return gone


def required_skills_dir(cfg: Config, role: str) -> str:
    """Claude Code reads project skills from .claude/skills; the others from .agents/skills."""
    return ".claude/skills" if cfg.lanes[role].runtime == "claude" else ""


def install(cfg: Config, force: bool = False) -> list[str]:
    """Idempotent: existing files are kept unless force. Returns the paths written."""
    home, wrote = cfg.home, []
    _write(home / TOWER_DIR / "PROTOCOL.md", (TEMPLATES / "PROTOCOL.md").read_text(encoding="utf-8"), force, wrote, home)
    for lid in cfg.lanes:
        _write(cfg.instructions(lid), lane_template(lid), force, wrote, home)
    for role in REQUIRED_LANES:
        if role in cfg.lanes and cfg.lanes[role].engine in ("", DEFAULT_ENGINE):
            for path, text in required_skill_files(home, role, role, "this project", required_skills_dir(cfg, role)).items():
                _write(path, text, force, wrote, home)
    write_shims(cfg, list(cfg.lanes), force, wrote)
    write_agents_block(home, wrote)
    ignore_state(home, wrote)
    if not (home / TOWER_DIR / "state" / "board.sha").is_file():
        board_changed(home)  # remember which built-in board this workspace started from
    return wrote


IGNORED = (f"{TOWER_DIR}/state/", f"{TOWER_DIR}/venv/")


def ignore_state(home: Path, wrote: list[str]) -> None:
    """Runtime state and a project install's venv never belong in the workspace's git."""
    gi = home / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    missing = [ln for ln in IGNORED if ln not in text.splitlines()]
    if missing:
        gi.write_text(text + ("" if text.endswith("\n") or not text else "\n") + "\n".join(missing) + "\n",
                      encoding="utf-8")
        wrote.append(".gitignore")


GUIDE = Path(__file__).resolve().parent / "guide"
PACKAGE = "dongduni-agent-hub"
REPO = "https://github.com/twglhk/dongduni-agent-hub"
CLONE_BIN = Path(__file__).resolve().parents[1] / "bin" / "dah"
INSTALL_HINT = f"`uv tool install {PACKAGE}` (or `pipx install {PACKAGE}`)"
SKILL_NAME = "agent-hub-setup"
SKILL_DESC = ("Set up agent-hub (the tower, its hub and lanes, and the review board) in this workspace. Use when asked to "
              "install, set up or transplant agent-hub / the tower, for this project or globally.")
SKILL_BODY = """Set up agent-hub for the workspace you are in (or the one the person names).

- `dah` must run: `{tower} -h`. If it does not, it installs with {hint}. Installing software is the person's call: ask first.
- Run `{tower} guide` and work through that playbook's phases in order. Do not improvise the order or skip the survey.
- The person's own agents, instruction files, MCP servers, skills and subagents stay as they are; the tower drives them.
"""


def tower_command() -> str:
    """How the person's agents should call dah: `dah` when it is on PATH, else the full path of this install's
    command (a project venv), else a clone's script."""
    if shutil.which("dah"):
        return "dah"
    for exe in (Path(sys.executable).parent / "dah", Path(sys.executable).parent / "dah.exe"):
        if exe.is_file():
            return str(exe)
    return str(CLONE_BIN) if CLONE_BIN.is_file() else "dah"

SKILL_FILES = {
    "claude": (Path(".claude/skills") / SKILL_NAME / "SKILL.md",
               f"---\nname: {SKILL_NAME}\ndescription: {SKILL_DESC}\n---\n" + SKILL_BODY),
    "opencode": (Path(".config/opencode/command") / f"{SKILL_NAME}.md",
                 f"---\ndescription: {SKILL_DESC}\n---\n" + SKILL_BODY + "\n$ARGUMENTS\n"),
    "codex": (Path(".agents/skills") / SKILL_NAME / "SKILL.md",  # Codex reads user skills from ~/.agents/skills
              f"---\nname: {SKILL_NAME}\ndescription: {SKILL_DESC}\n---\n" + SKILL_BODY),
}
LEGACY_SKILL_FILES = {"codex": Path(".codex/prompts") / f"{SKILL_NAME}.md"}  # custom prompts, gone since Codex 0.117
SKILL_HOMES = {"claude": Path(".claude"), "opencode": Path(".config/opencode"), "codex": Path(".codex")}


def skill_plan(user_home: Path, runtimes: list[str] | None = None) -> list[tuple[str, Path, str, str]]:
    """(runtime, path, current text, new text) for each agent this person has; new text "" removes a file an older
    version wrote where the agent no longer reads it. Writes nothing."""
    chosen = runtimes or [r for r in SKILL_FILES if (user_home / SKILL_HOMES[r]).is_dir() or shutil.which(r)]
    plan = []
    for runtime in chosen:
        rel, text = SKILL_FILES[runtime]
        path = user_home / rel
        old = path.read_text(encoding="utf-8") if path.is_file() else ""
        plan.append((runtime, path, old, text.format(tower=tower_command(), hint=INSTALL_HINT)))
        legacy = user_home / LEGACY_SKILL_FILES[runtime] if runtime in LEGACY_SKILL_FILES else None
        if legacy and legacy.is_file():
            plan.append((runtime, legacy, legacy.read_text(encoding="utf-8"), ""))
    return plan


def skill_files(user_home: Path, runtimes: list[str] | None = None) -> list[tuple[str, Path]]:
    """The global agent-hub-setup files that exist, current and older locations, for `dah skill uninstall`."""
    out = []
    for runtime in runtimes or list(SKILL_FILES):
        for rel in (SKILL_FILES[runtime][0], LEGACY_SKILL_FILES.get(runtime)):
            if rel and (user_home / rel).is_file():
                out.append((runtime, user_home / rel))
    return out


def remove_file(path: Path) -> None:
    """Delete a file this package wrote, and its folder when that was the file's own (a skill folder)."""
    path.unlink()
    if path.name == "SKILL.md" and not any(path.parent.iterdir()):
        path.parent.rmdir()


def upgrade_command() -> list[str] | None:
    """How to upgrade this install the way it was made (uv tool, pipx, a venv); None for a clone."""
    root = Path(__file__).resolve().parents[1]
    if (root / "pyproject.toml").is_file():
        return None
    prefix = Path(sys.prefix).resolve()
    uv, pipx = shutil.which("uv"), shutil.which("pipx")
    if uv:
        r = subprocess.run([uv, "tool", "dir"], capture_output=True, text=True, encoding="utf-8")
        if r.returncode == 0 and r.stdout.strip() and Path(r.stdout.strip()).resolve() in prefix.parents:
            return [uv, "tool", "upgrade", PACKAGE]
    if pipx and "pipx" in {p.lower() for p in prefix.parts}:
        return [pipx, "upgrade", PACKAGE]
    if subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode == 0:
        return [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE]
    if uv:  # a venv made by `uv venv` has no pip
        return [uv, "pip", "install", "--python", sys.executable, "--upgrade", PACKAGE]
    raise FileNotFoundError("cannot tell how dah was installed")


def model_works(runtime: str, model: str, timeout: int = 90) -> bool:
    """One tiny call to see whether the agent CLI accepts a model alias (a plan may lack `fable`)."""
    if runtime != "claude" or not shutil.which("claude"):
        return True
    try:
        p = subprocess.run(["claude", "--model", model, "-p", "Reply with ok."], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return True  # cannot tell: keep the default
    return p.returncode == 0


def fix_models(home: Path, probe=model_works) -> list[str]:
    """Lanes that name a model the runtime refuses get `opus` instead; says what it changed."""
    notes: list[str] = []

    def fn(data: dict) -> None:
        for lid, ln in (data.get("lanes") or {}).items():
            if (ln or {}).get("model") == "fable" and not probe(ln.get("runtime", ""), "fable"):
                ln["model"] = "opus"
                notes.append(f"lane {lid}: model fable is not available to this agent, using opus")

    edit_config(home, fn)
    return notes


def default_runtime() -> str | None:
    return next((r for r in ("opencode", "claude", "codex") if shutil.which(r)), None)


def init(home: Path, name: str, lang: str, hub: str, infra: str, project: str | None, safe: bool,
         force: bool, terminal: str = "none") -> Path:
    path = home / TOWER_DIR / CONFIG_NAME
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists (use --force to overwrite)")
    web_port, opencode_port = free_ports(home)
    data = template(name, lang, hub, infra, safe, web_port, opencode_port, terminal)
    if project:
        data["project"] = project
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def edit_config(home: Path, fn) -> None:
    path = home / TOWER_DIR / CONFIG_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


SHIPPED = TEMPLATES / "shipped.json"  # every released text of each template, oldest first, the current one last


def shipped() -> dict[str, list[str]]:
    return json.loads(SHIPPED.read_text(encoding="utf-8"))


def _template_key(lane: str) -> str:
    if lane in REQUIRED_LANES:
        return f"lanes/{REQUIRED_LANES[lane][0]}"
    return {HUB: "lanes/hub.md", INFRA: "lanes/infra.md"}.get(lane, "lanes/team.md")


def _refresh_file(path: Path, versions: list[str], report: dict[str, list[str]], home: Path) -> None:
    """Bring one template-based file to the newest template without losing what the workspace added.

    Untouched since some release (the workspace only appended below it): swap that release's text for the new one.
    Edited inside: leave it, and put the new template beside it as `<name>.new` when it changed since the release
    the file is closest to."""
    new, rel = versions[-1], path.relative_to(home).as_posix()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
        report["created"].append(rel)
        return
    text = path.read_text(encoding="utf-8")
    side = path.with_name(path.name + ".new")
    if text.startswith(new):
        side.unlink(missing_ok=True)
        return
    for old in sorted(versions[:-1], key=len, reverse=True):
        if text.startswith(old):
            path.write_text(new + text[len(old):], encoding="utf-8")
            side.unlink(missing_ok=True)
            report["updated"].append(rel)
            return
    import difflib
    closest = max(range(len(versions)), key=lambda i: difflib.SequenceMatcher(None, versions[i], text).ratio())
    if closest == len(versions) - 1:
        return
    if side.is_file() and side.read_text(encoding="utf-8") == new:
        report["pending"].append(rel)  # asked for already; infra has not merged it yet
        return
    side.write_text(new, encoding="utf-8")
    report["merge"].append(rel)


def _safe_lane_fixes(data: dict, report: dict[str, list[str]]) -> None:
    for lid, ln in (data.get("lanes") or {}).items():
        args = (ln or {}).get("args")
        if (ln or {}).get("runtime") != "claude" or not isinstance(args, list) or "acceptEdits" not in args:
            continue
        if not any(str(a).startswith(("--allowedTools", "--allowed-tools")) for a in args):
            args += ["--allowedTools", "Bash(dah:*)"]
            report["config"].append(f"{lid}: --allowedTools \"Bash(dah:*)\" so the lane can run dah in --safe mode")


def refresh(cfg: Config) -> dict[str, list[str]]:
    """Make an existing workspace match the installed version: templates, tower.json fixes, missing shortcuts."""
    report: dict[str, list[str]] = {"created": [], "updated": [], "merge": [], "pending": [], "config": [], "shortcuts": []}
    home, versions = cfg.home, shipped()
    _refresh_file(home / TOWER_DIR / "PROTOCOL.md", versions["PROTOCOL.md"], report, home)
    for lid in cfg.lanes:
        texts = [t.replace("{lane}", lid).replace("{skill}", lid) for t in versions[_template_key(lid)]]
        _refresh_file(cfg.instructions(lid), texts, report, home)
    edit_config(home, lambda d: _safe_lane_fixes(d, report))
    write_shims(cfg, list(cfg.lanes), False, report["shortcuts"])
    for role in REQUIRED_LANES:
        if role in cfg.lanes and cfg.lanes[role].engine in ("", DEFAULT_ENGINE) and cfg.lanes[role].runtime == "claude":
            for path, text in required_skill_files(home, role, role, "this project", ".claude/skills").items():
                _write(path, text, False, report["shortcuts"], home)
    write_agents_block(home, report["shortcuts"])
    ignore_state(home, report["shortcuts"])
    return report


def migration_notes(cfg: Config, report: dict[str, list[str]]) -> list[str]:
    """What a workspace coming from before 0.8.0 should know, judged from the workspace itself (nothing is changed here)."""
    import sqlite3
    notes = []
    try:
        with sqlite3.connect(cfg.db) as con:
            n = con.execute("SELECT count(*) FROM goals").fetchone()[0]
    except (sqlite3.Error, OSError):
        n = 0
    if n:
        notes.append(f"goals are gone: the {n} goal row(s) stay in the database, unread. Send that work as requests; "
                     f"/api/goals answers 410 and `goal` events no longer fire.")
    hub = cfg.instructions(HUB).relative_to(cfg.home).as_posix()
    if any(f == hub for f in report["merge"] + report["pending"]):
        notes.append(f"the board has no Infra tab now and the hub is the only door to infra: until `{hub}.new` is merged "
                     f"into `{hub}`, the hub does not forward infra work.")
    if not any(e.implicit is False for e in cfg.engines.values()):
        notes.append("engines: tower.json declares none, so the team lanes run as one engine `default`; nothing to change. "
                     "`dah engine add NAME --from blank|animation|marketing` starts a method with cards (`dah guide engines`).")
    return notes


# ---------- package files changed in place (kept across upgrades) ----------

PATCHES_DIR = "patches"
PYPI_JSON = "https://pypi.org/pypi/{package}/{version}/json"
BUILTIN_BOARD = Path(__file__).resolve().parent / "board" / "index.html"


def file_hash(path: Path) -> str:
    """The form wheel RECORD files use: sha256=<urlsafe base64, no padding>."""
    import base64
    import hashlib
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest()).rstrip(b"=").decode()


def modified_files(entries: list[tuple[str, str | None]], base: Path) -> list[str]:
    """The package's own files (RECORD path, hash) that no longer match what was installed."""
    out = []
    for rel, digest in entries:
        if not digest or not rel.startswith("agent_hub/") or "__pycache__" in rel or rel.endswith(".pyc"):
            continue
        path = base / rel
        if path.is_file() and file_hash(path) != digest:
            out.append(rel)
    return sorted(out)


def installed_package() -> tuple[str, Path, list[tuple[str, str | None]]] | None:
    """(version, site dir, RECORD entries) of the installed package this code runs from; None for a clone."""
    import importlib.metadata
    try:
        dist = importlib.metadata.distribution(PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return None
    base = Path(str(dist.locate_file(""))).resolve()
    if (base / "agent_hub").resolve() != Path(__file__).resolve().parent:
        return None  # running from a clone while another copy is installed
    entries = [(f.as_posix(), f"{f.hash.mode}={f.hash.value}" if f.hash else None) for f in dist.files or []]
    return dist.version, base, entries


def fetch_originals(version: str, files: list[str], index: str = PYPI_JSON) -> dict[str, str] | None:
    """The released text of each file, from that version's wheel on PyPI. None when it cannot be fetched."""
    import io
    import urllib.request
    import zipfile
    try:
        with urllib.request.urlopen(index.format(package=PACKAGE, version=version), timeout=20) as r:
            meta = json.loads(r.read().decode("utf-8"))
        url = next(u["url"] for u in meta["urls"] if u.get("packagetype") == "bdist_wheel")
        with urllib.request.urlopen(url, timeout=60) as r:
            wheel = zipfile.ZipFile(io.BytesIO(r.read()))
        names = set(wheel.namelist())
        return {rel: wheel.read(rel).decode("utf-8", errors="replace") for rel in files if rel in names}
    except Exception:  # offline, a private index, a yanked release: keep the files without a diff
        return None


def backup_patches(home: Path, version: str, base: Path, files: list[str],
                   originals: dict[str, str] | None) -> Path:
    """Copy the changed package files to .tower/patches/<version>/files/, with changes.diff against the release
    when the originals are known, and index.json (what was saved, whether infra was asked to re-apply it)."""
    import difflib
    root = home / TOWER_DIR / PATCHES_DIR / version
    old = json.loads((root / "index.json").read_text(encoding="utf-8")) if (root / "index.json").is_file() else {}
    diff = []
    for rel in files:
        dest = root / "files" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(base / rel, dest)
        if originals and rel in originals:
            now = (base / rel).read_text(encoding="utf-8", errors="replace")
            diff += difflib.unified_diff(originals[rel].splitlines(keepends=True), now.splitlines(keepends=True),
                                         f"a/{rel}", f"b/{rel}")
    if diff:
        (root / "changes.diff").write_text("".join(d if d.endswith("\n") else d + "\n" for d in diff),
                                           encoding="utf-8")
    index = {"version": version, "files": files, "diff": bool(diff),
             "hashes": {rel: file_hash(base / rel) for rel in files}, "requested": None}
    if old.get("files") == files and old.get("requested"):
        index["requested"] = old["requested"]
    (root / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return root


def unrequested_patches(home: Path, current: str) -> list[dict]:
    """Backups of an older version whose re-apply request has not gone to infra yet."""
    out = []
    for idx in sorted((home / TOWER_DIR / PATCHES_DIR).glob("*/index.json")):
        data = json.loads(idx.read_text(encoding="utf-8"))
        if data.get("version") != current and not data.get("requested"):
            out.append({**data, "dir": idx.parent})
    return out


def mark_requested(patch_dir: Path, memo_id: int) -> None:
    idx = patch_dir / "index.json"
    data = json.loads(idx.read_text(encoding="utf-8"))
    data["requested"] = memo_id
    idx.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def board_changed(home: Path) -> str:
    """Once per change of the built-in board: "replaced" when this workspace replaces it (.tower/board/index.html),
    "extended" when it adds to it (extra.js / extra.css), else ""."""
    mark = home / TOWER_DIR / "state" / "board.sha"
    now = file_hash(BUILTIN_BOARD)
    before = mark.read_text(encoding="utf-8").strip() if mark.is_file() else ""
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text(now, encoding="utf-8")
    if not before or before == now:
        return ""
    board = home / TOWER_DIR / "board"
    if (board / "index.html").is_file():
        return "replaced"
    return "extended" if (board / "extra.js").is_file() or (board / "extra.css").is_file() else ""


# ---------- taking agent-hub out of a workspace (`dah uninstall`) ----------

def shim_state(cfg: Config) -> tuple[list[Path], list[Path]]:
    """Every lane's `/dah-<lane>` shortcuts that exist: (still as written, edited since)."""
    ours, edited = [], []
    for lane in cfg.lanes:
        for runtime in ("opencode", "claude", "codex"):
            f = shim_files(lane, runtime)
            path = cfg.workdir / f[0] if f else None
            if f and path and path.is_file():
                (ours if path.read_text(encoding="utf-8") == f[1] else edited).append(path)
    return ours, edited


def mentions(cfg: Config) -> list[str]:
    """Lines in the workspace's own agent instruction files that point at .tower/ (setup may have added one)."""
    out = []
    for d in dict.fromkeys((cfg.workdir, cfg.home)):
        for name in ("AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md"):
            path = d / name
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            out += [f"{path}:{i}: {ln.strip()[:120]}" for i, ln in enumerate(lines, 1) if f"{TOWER_DIR}/" in ln]
    return out


def detach_plan(cfg: Config) -> dict[str, list[str]]:
    """What `dah uninstall` removes and what it leaves. Changes nothing."""
    ours, edited = shim_state(cfg)
    gi = cfg.home / ".gitignore"
    text = gi.read_text(encoding="utf-8").splitlines() if gi.is_file() else []
    trees = [str(cfg.lane_dir(lid)) for lid, ln in cfg.lanes.items() if ln.worktree and cfg.lane_dir(lid).exists()]
    return {"shortcuts": [str(p) for p in ours], "edited_shortcuts": [str(p) for p in edited],
            "gitignore": [ln for ln in IGNORED if ln in text], "worktrees": trees, "mentions": mentions(cfg)}


def detach(cfg: Config, purge: bool) -> list[str]:
    """Take agent-hub out of the workspace: worktrees the lanes worked in (only clean ones; their branches stay),
    the shortcuts as written, the .gitignore lines, the registry entry, and with purge the whole .tower/."""
    done, dirty = [], False
    for lid, ln in cfg.lanes.items():
        path = cfg.lane_dir(lid)
        if ln.worktree and path.exists():
            p = subprocess.run(["git", "-C", str(cfg.workdir), "worktree", "remove", str(path)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            dirty = dirty or p.returncode != 0
            done.append(f"removed worktree {path} (branch dah/{lid} kept)" if p.returncode == 0 else
                        f"kept worktree {path}: {(p.stderr or p.stdout).strip()[:200]}")
    for lid in cfg.lanes:
        done += [f"removed {f}" for f in remove_shims(cfg, lid)]
    done += remove_agents_block(cfg.home)
    gi = cfg.home / ".gitignore"
    if gi.is_file():
        lines = gi.read_text(encoding="utf-8").splitlines()
        keep = [ln for ln in lines if ln not in IGNORED]
        if keep != lines:
            if any(ln.strip() for ln in keep):
                gi.write_text("\n".join(keep).rstrip("\n") + "\n", encoding="utf-8")
            else:
                gi.unlink()
            done.append(f"removed {', '.join(ln for ln in IGNORED if ln in lines)} from .gitignore")
    from .config import unregister, workspaces
    for slug, home in workspaces().items():
        if home.resolve() == cfg.home.resolve():
            unregister(slug)
            done.append(f"removed workspace \"{slug}\" from this machine's list")
    if purge and dirty:  # never delete work nobody committed
        done.append(f"kept {cfg.home / TOWER_DIR}: a lane worktree inside it has uncommitted work; commit or discard "
                    f"it, then run `dah uninstall --yes --purge` again")
    elif purge:
        try:
            remove_tree(cfg.home / TOWER_DIR)
            done.append(f"deleted {cfg.home / TOWER_DIR}")
        except OSError as e:  # Windows: dah.exe of a project install is still running
            done.append(f"could not delete all of {cfg.home / TOWER_DIR}: {e}")
    return done


def remove_tree(path: Path, tries: int = 10) -> None:
    """shutil.rmtree that also takes read-only files (git objects on Windows) and retries a file that a virus
    scanner or a closing process holds for a moment (Windows)."""
    import gc
    import stat
    import time

    def writable(fn, p, _exc):  # read-only on Windows: make it writable and try that one again
        os.chmod(p, stat.S_IWRITE)
        fn(p)

    for i in range(tries):
        gc.collect()  # SQLite connections nobody references any more
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=writable)
            else:
                shutil.rmtree(path, onerror=writable)
            return
        except FileNotFoundError:
            return
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(0.5)


# ---------------------------------------------------------------- engine scaffolds (`dah engine add NAME --from KIND`)

ENGINE_TEMPLATES = TEMPLATES / "engines"
ENGINE_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REVIEW_ROLE = "review"
REQUIRED_SKILLS = {"research": "ENGINE-research", "audit": "ENGINE-audit"}
RESEARCH_ROW = "| {n} | Research note | {id}-research · {id}-research | draft v1 (process/research-note.md) | draft | opus/medium |"


def required_skill_files(home: Path, role: str, prefix: str, label: str, skills_dir: str) -> dict[Path, str]:
    """The skill folder of a required lane: `{prefix}-{role}`, or plain `{role}` when prefix == role (no engine)."""
    src = ENGINE_TEMPLATES / "required" / "skills" / REQUIRED_SKILLS[role]
    name = role if prefix == role else f"{prefix}-{role}"
    out: dict[Path, str] = {}
    for f in sorted(src.rglob("*.md")):
        text = f.read_text(encoding="utf-8").replace("{label}", label).replace("ENGINE-" + role, name)
        out[home / (skills_dir or ".agents/skills") / name / f.relative_to(src).as_posix()] = text
    return out


def engine_kinds() -> list[str]:
    return sorted(p.name for p in ENGINE_TEMPLATES.iterdir() if (p / "kind.json").is_file()) if ENGINE_TEMPLATES.is_dir() else []


def scaffold_engine(cfg: Config, eid: str, kind: str, label: str, runtime: str, skills: str = "", cap: str = "") -> dict:
    """Create engine `eid` from template `kind`: a library, an `engine.json`, skills with process and pattern cards (all
    `draft`), a lane per role and an engine review lane, with their instruction files. Nothing is overwritten: an existing
    file or engine is an error before anything is written. Returns {"entry", "lanes", "files"}; the caller writes tower.json."""
    from . import engines as engines_mod
    if not ENGINE_ID.match(eid):
        raise ValueError("engine id for --from: lowercase letters, digits and single hyphens (it prefixes the skill and lane names)")
    if kind not in engine_kinds():
        raise ValueError(f"--from: one of {', '.join(engine_kinds())}")
    if any(c in label for c in '"\\\n:#'):
        raise ValueError('--label: no quotes, backslashes, colons, # or line breaks (it goes into skill descriptions)')
    src = ENGINE_TEMPLATES / kind
    meta = json.loads((src / "kind.json").read_text(encoding="utf-8"))
    skills_dir = skills or (".claude/skills" if runtime == "claude" else engines_mod.SKILLS_DEFAULT)
    edir = f"engines/{eid}"
    library = f"{edir}/pattern-library.md"
    lib_path = cfg.home / library

    def fill(text: str, **extra: str) -> str:
        for k, v in {"id": eid, "label": label, **extra}.items():
            text = text.replace("{" + k + "}", v)
        return text.replace("ENGINE-", eid + "-")

    files: dict[Path, str] = {cfg.home / edir / "pattern-library.md": fill((src / "pattern-library.md").read_text(encoding="utf-8")),
                              cfg.home / edir / "engine.json": (src / "engine.json").read_text(encoding="utf-8")}
    for f in sorted((src / "skills").rglob("*.md")):
        rel = fill(f.relative_to(src / "skills").as_posix())
        files[cfg.home / skills_dir / rel] = fill(f.read_text(encoding="utf-8"))
    lib = files[lib_path].rstrip("\n")
    n = max((int(r["n"]) for r in engines_mod.library_rows(lib) if r["n"].isdigit()), default=0) + 1
    files[lib_path] = lib + "\n" + RESEARCH_ROW.format(n=n, id=eid) + "\n"
    rows = engines_mod.library_rows(files[lib_path])
    roles = {f"{eid}-{r}": t for r, t in meta["lanes"].items()}
    lanes: dict[str, str] = {}
    for lid, what in roles.items():
        use = [r["skill"] for r in rows if r["lane"] == lid and r["skill"]]
        text = lane_template(lid).replace(
            next(ln for ln in lane_template(lid).splitlines() if ln.startswith("(Infra or the hub")),
            f"{what} It works the {label} pattern library at `{library}`." + "".join(f' Run `skill("{s}")` before each task.' for s in dict.fromkeys(use)))
        lanes[lid] = text
    for role in REQUIRED_LANES:  # research and the engine auditor come with every engine
        lid = f"{eid}-{role}"
        lanes[lid] = required_lane_text(role, lid, lid)
        files.update(required_skill_files(cfg.home, role, eid, label, skills_dir))
    rid = f"{eid}-{REVIEW_ROLE}"
    lanes[rid] = fill((ENGINE_TEMPLATES / "review-lane.md").read_text(encoding="utf-8"), lane=rid, library=library)
    for lid, text in lanes.items():
        files[cfg.home / TOWER_DIR / "lanes" / f"{lid}.md"] = text
    clash = [p for p in files if p.exists()]
    if clash:
        raise ValueError("would overwrite: " + ", ".join(p.relative_to(cfg.home).as_posix() for p in clash))
    dup = [lid for lid in lanes if lid in cfg.lanes]
    if dup:
        raise ValueError(f"lane exists: {', '.join(dup)}")
    entry = {"label": label, "dir": edir, "library": library, "skills": skills_dir, "map": f"{edir}/engine.json"}
    if cap:
        entry["cap"] = cap
    lane_entries = {lid: {"runtime": runtime, "label": lid, "engine": eid} for lid in lanes}
    if runtime == "claude":
        for role, (_, model) in REQUIRED_LANES.items():
            lane_entries[f"{eid}-{role}"]["model"] = model
    return {"entry": entry, "lanes": lane_entries, "files": files}
