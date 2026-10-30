"""Write the tower's files into a workspace: config, protocol, lane instructions, and a `/dah-<lane>` shortcut per
lane so a human can also open a lane by hand in their own agent (a command in OpenCode and Claude Code, a skill
in Codex). A lane is a role put on top of the agent the person already uses, not a new agent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from .config import CONFIG_NAME, HUB, INFRA, TOWER_DIR, Config, free_ports, template

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


def lane_template(lane: str) -> str:
    name = {HUB: "hub.md", INFRA: "infra.md"}.get(lane)
    if name:
        return (TEMPLATES / "lanes" / name).read_text(encoding="utf-8")
    return (TEMPLATES / "lanes" / "team.md").read_text(encoding="utf-8").replace("{lane}", lane)


def install(cfg: Config, force: bool = False) -> list[str]:
    """Idempotent: existing files are kept unless force. Returns the paths written."""
    home, wrote = cfg.home, []
    _write(home / TOWER_DIR / "PROTOCOL.md", (TEMPLATES / "PROTOCOL.md").read_text(encoding="utf-8"), force, wrote, home)
    for lid in cfg.lanes:
        _write(cfg.instructions(lid), lane_template(lid), force, wrote, home)
    write_shims(cfg, list(cfg.lanes), force, wrote)
    ignore_state(home, wrote)
    if not (home / TOWER_DIR / "state" / "board.sha").is_file():
        board_changed(home)  # remember which built-in board this workspace started from
    return wrote


def ignore_state(home: Path, wrote: list[str]) -> None:
    """Runtime state and a project install's venv never belong in the workspace's git."""
    gi = home / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    missing = [ln for ln in (f"{TOWER_DIR}/state/", f"{TOWER_DIR}/venv/") if ln not in text.splitlines()]
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
    "codex": (Path(".codex/prompts") / f"{SKILL_NAME}.md",
              f"---\ndescription: {SKILL_DESC}\n---\n" + SKILL_BODY + "\n$ARGUMENTS\n"),
}
SKILL_HOMES = {"claude": Path(".claude"), "opencode": Path(".config/opencode"), "codex": Path(".codex")}


def skill_plan(user_home: Path, runtimes: list[str] | None = None) -> list[tuple[str, Path, str, str]]:
    """(runtime, path, current text, new text) for each agent this person has. Writes nothing."""
    chosen = runtimes or [r for r in SKILL_FILES if (user_home / SKILL_HOMES[r]).is_dir() or shutil.which(r)]
    plan = []
    for runtime in chosen:
        rel, text = SKILL_FILES[runtime]
        path = user_home / rel
        old = path.read_text(encoding="utf-8") if path.is_file() else ""
        plan.append((runtime, path, old, text.format(tower=tower_command(), hint=INSTALL_HINT)))
    return plan


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
        texts = [t.replace("{lane}", lid) for t in versions[_template_key(lid)]]
        _refresh_file(cfg.instructions(lid), texts, report, home)
    edit_config(home, lambda d: _safe_lane_fixes(d, report))
    write_shims(cfg, list(cfg.lanes), False, report["shortcuts"])
    ignore_state(home, report["shortcuts"])
    return report


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
