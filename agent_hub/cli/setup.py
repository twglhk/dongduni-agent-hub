from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .. import config as config_mod
from ..config import INFRA, Config, ConfigError, tower_pid
from ..install import default_runtime, init, install
from .common import TowerError, cfg, store
from .running import start_tower, stop_loop


def cmd_discover(args: argparse.Namespace) -> None:
    from ..discover import survey
    print(json.dumps(survey(Path(args.project) if args.project else None), indent=2, ensure_ascii=False))


def cmd_init(args: argparse.Namespace) -> None:
    home = Path(args.home or os.getcwd()).resolve()
    if not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", args.lang):
        raise TowerError(f"--lang takes a language code such as en, ko, ja, zh, es, pt-BR (got {args.lang!r})")
    hub = args.hub or default_runtime()
    infra = args.infra or hub
    if not hub:
        raise TowerError("no agent CLI found (opencode, claude or codex); pass --hub/--infra")
    try:
        path = init(home, args.name, args.lang, hub, infra, args.project, args.safe, args.force, args.terminal)
    except FileExistsError as e:
        raise TowerError(str(e))
    from ..install import fix_models
    notes = fix_models(home) if not os.environ.get("DAH_SKIP_MODEL_CHECK") else []
    c = config_mod.load(home)
    wrote = install(c, force=args.force)
    if not os.environ.get("DAH_SKIP_MODEL_CHECK"):
        from ..install import model_works
        from ..starter import fix_models as fix_subagent_models
        notes += fix_subagent_models(c, model_works)
    slug = config_mod.register(home)
    print(f"wrote {path}")
    for n in notes:
        print(f"note: {n}")
    for w in wrote:
        print(f"wrote {w}")
    print(f"board port {c.web_port} · workspace \"{slug}\" registered (`dah workspaces`)")
    if args.terminal != "none" and args.safe:
        print("note: with --safe, a lane in a window stops at its agent's permission prompts until someone answers "
              "there (the tower sends an alert); without --safe it never asks")
    print("next: `dah start`, then open the board")


def cmd_install(args: argparse.Namespace) -> None:
    for w in install(cfg(), force=args.force) or ["(nothing new)"]:
        print(w)


def cmd_update(args: argparse.Namespace) -> None:
    from .. import __version__
    from ..install import backup_patches, fetch_originals, installed_package, modified_files, upgrade_command
    homes = list(config_mod.workspaces().values()) if args.all else [cfg().home]
    if not args.no_upgrade:
        try:
            how = upgrade_command()
        except FileNotFoundError as e:
            raise TowerError(f"{e}: upgrade it the way you installed it, then run `dah update --no-upgrade`")
        pkg = installed_package() if how is not None else None
        changed = modified_files(pkg[2], pkg[1]) if pkg else []
        if args.dry_run:
            print("would upgrade: " + (" ".join(how) if how else "nothing (dah runs from a clone: `git pull` it)"))
            print("package files changed in place: " + (", ".join(changed) if changed else "none"))
            return
        if how is None:
            print("dah runs from a clone: `git pull` it yourself. Updating the workspace with this code.")
        else:
            if pkg and changed:
                originals = fetch_originals(pkg[0], changed)
                for home in homes:
                    if (home / config_mod.TOWER_DIR / config_mod.CONFIG_NAME).is_file():
                        where = backup_patches(home, pkg[0], pkg[1], changed, originals)
                        print(f"{home}: kept {len(changed)} changed package file(s) in {where}"
                              + ("" if originals else " (no diff: the released files could not be fetched)"))
            print("upgrading: " + " ".join(how), flush=True)
            if subprocess.run(how).returncode != 0:
                hint = ""
                if os.name == "nt":
                    hint = (" On Windows the running dah.exe cannot be replaced: close this terminal, then in a new one run "
                            f"`{subprocess.list2cmdline(how)}` and `dah update --no-upgrade" + (" --all" if args.all else "") + "`.")
                raise TowerError("the upgrade failed; nothing in the workspace changed." + hint)
            rest = ["--no-upgrade"] + (["--all"] if args.all else []) + (["--no-restart"] if args.no_restart else [])
            sys.exit(subprocess.run([sys.executable, "-m", "agent_hub", "update", *rest]).returncode)  # the new code
    elif args.dry_run:
        raise TowerError("--dry-run checks an upgrade; drop --no-upgrade")
    for home in homes:
        try:
            c = config_mod.load(home)
        except ConfigError as e:
            print(f"{home}: skipped ({e})")
            continue
        update_workspace(c, __version__, args.no_restart)


def update_workspace(c: Config, version: str, no_restart: bool) -> None:
    from ..install import BUILTIN_BOARD, board_changed, mark_requested, refresh, unrequested_patches
    mark = c.state_dir / "version"
    before = mark.read_text(encoding="utf-8").strip() if mark.is_file() else ""
    report = refresh(c)
    c.state_dir.mkdir(parents=True, exist_ok=True)
    mark.write_text(version, encoding="utf-8")
    print(f"{c.name} ({c.home}): agent-hub " + (f"{before} → {version}" if before and before != version else version))
    for key, what in (("created", "created"), ("updated", "updated to the new template, workspace parts kept"),
                      ("config", "tower.json"), ("shortcuts", "added")):
        for line in report[key]:
            print(f"  {what}: {line}")
    if before and before != version or not before and report["updated"]:
        from ..install import migration_notes
        for line in migration_notes(c, report):
            print(f"  note: {line}")
    requests: list[tuple[str, str, Path | None]] = []  # (what infra should do, request text, patch backup)
    if report["merge"]:
        files = ", ".join(f"`{f}`" for f in report["merge"])
        print(f"  edited here, new template beside it as .new: {', '.join(report['merge'])}")
        requests.append(("merge them", f"agent-hub was updated to {version}. These files were edited in this workspace, so "
                         f"the new template was put beside each as `<file>.new`: {files}. For each, bring what changed "
                         f"in the new template into the file, keep this workspace's own parts, delete the .new file, "
                         f"and reply with what changed.", None))
    for line in report["pending"]:
        print(f"  still to merge: {line}.new")
    patches = unrequested_patches(c.home, version)
    for p in patches:
        rel = p["dir"].relative_to(c.home).as_posix()
        print(f"  package files changed under {p['version']}, kept in {rel}: {', '.join(p['files'])}")
        requests.append(("re-apply the package changes", f"agent-hub was updated from {p['version']} to {version}, which "
                         f"replaced package files someone had changed in place: {', '.join(p['files'])}. They are kept "
                         f"in `{rel}/files/`" + (f", with `{rel}/changes.diff` against the release" if p["diff"] else "")
                         + ". Bring that behaviour back without editing the installed package again: board changes in "
                         "`.tower/board/` (extra.js / extra.css, or index.html to replace the board), API routes and "
                         "engine hooks in `.tower/ext/*.py` (see `dah guide extend`). Reply with what you rebuilt and "
                         "anything that could not be carried over.", p["dir"]))
    board = board_changed(c.home)
    if board == "extended":
        print("  the built-in board changed; this workspace adds to it with .tower/board/extra.js / extra.css")
        requests.append(("check the board extensions", f"agent-hub was updated to {version} and its built-in board "
                         f"changed. This workspace adds to it with `.tower/board/extra.js` / `extra.css`. Open the "
                         f"board and check every tab and section they add: a section that broke shows an error box, a "
                         f"style may no longer match. Fix what uses anything outside the stable part of `window.dah` "
                         f"(see \"What stays stable\" in `dah guide extend`) and reply with what you checked and fixed.",
                         None))
    if board == "replaced":
        print("  the built-in board changed; this workspace replaces it with .tower/board/index.html")
        requests.append(("carry over the board changes", f"agent-hub was updated to {version} and its built-in board changed. This "
                         f"workspace replaces the board with `.tower/board/index.html`, so the change does not show. "
                         f"Compare with the new built-in board (open the board with `?builtin`; its built files are in `{BUILTIN_BOARD.parent}`, and `/old/` is the single-file board before the React app), "
                         f"carry over what this workspace wants, and reply with what changed.", None))
    for what, text, patch_dir in requests:
        if INFRA not in c.lanes:
            print(f"  no infra lane: {what} yourself")
            continue
        mid = store(c).push(INFRA, "dah update", "memo", text)
        print(f"  asked infra to {what} (request #{mid} on the board)")
        if patch_dir:
            mark_requested(patch_dir, mid)
    if not no_restart and stop_loop(c):
        start_tower(c)


def cmd_skill(args: argparse.Namespace) -> None:
    """Global setup entry for each agent, so "set up agent-hub here" works from any workspace."""
    import difflib
    from ..install import remove_file, skill_files, skill_plan
    if args.sub == "uninstall":
        found = skill_files(Path.home(), args.runtime)
        if not found:
            print("no agent-hub-setup skill or command found")
        for runtime, path in found:
            if args.yes:
                remove_file(path)
            print(f"{runtime}: {'removed' if args.yes else 'would remove'} {path}")
        if found and not args.yes:
            print("\nNothing removed. These are global agent files: run again with --yes to remove them.")
        return
    plan = skill_plan(Path.home(), args.runtime)
    if not plan:
        raise TowerError("no agent config found (~/.claude, ~/.config/opencode, ~/.codex); pass --runtime")
    for runtime, path, old, new in plan:
        if old == new:
            print(f"{runtime}: {path} is up to date")
            continue
        if not args.yes:
            print(f"{runtime}: {'remove (the agent no longer reads it)' if not new else 'update' if old else 'create'} {path}")
            sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                       str(path) if old else "/dev/null", str(path) if new else "/dev/null"))
            continue
        if not new:
            remove_file(path)
            print(f"{runtime}: removed {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
        print(f"{runtime}: wrote {path}")
    if not args.yes and any(old != new for _, _, old, new in plan):
        print("\nNothing written. These are global agent files: run again with --yes to write them.")


def cmd_uninstall(args: argparse.Namespace) -> None:
    """Take agent-hub out of this workspace. Shows the plan first; --yes does it."""
    from ..install import detach, detach_plan
    c = cfg()
    plan = detach_plan(c)
    print(f"{c.name} ({c.home})")
    print(f"  stop the tower{' and close its lane sessions' if not args.keep_lanes else ''}")
    for key, what in (("worktrees", "remove the lane worktree (if clean; its branch stays)"),
                      ("shortcuts", "remove"), ("gitignore", "remove from .gitignore")):
        for line in plan[key]:
            print(f"  {what}: {line}")
    print("  remove it from this machine's workspace list")
    print(f"  {'delete' if args.purge else 'keep'} {c.home / config_mod.TOWER_DIR}"
          + ("" if args.purge else " (config, lane files, extensions, board history; --purge deletes it)"))
    for line in plan["edited_shortcuts"]:
        print(f"  keep (edited here): {line}")
    if not args.yes:
        print("\nNothing changed. Run again with --yes to do it.")
        return
    stop_loop(c)
    if not args.keep_lanes:
        from ..runtimes import Runtimes
        rt = Runtimes(c, store(c))
        for lid in c.lanes:
            try:
                if rt.owned(lid) and rt.status(lid) != "closed":
                    rt.close(lid)
                    print(f"closed {lid}")
            except Exception as e:  # a lane that cannot be checked is left running; say so
                print(f"{lid}: not closed ({e})")
    for line in detach(c, args.purge):
        print(line)
    if plan["mentions"]:
        print("\nThese lines still point at .tower/; remove them by hand if you added them for agent-hub:")
        for line in plan["mentions"]:
            print(f"  {line}")
    print("\nGlobal parts, if you installed them: `dah skill uninstall --yes` (the setup skill/command), then the "
          "command itself: `uv tool uninstall dongduni-agent-hub` or `pipx uninstall dongduni-agent-hub`.")


GUIDES_TOP = {"setup": "SETUP.md", "extend": "EXTEND.md", "engines": "ENGINES.md", "models": "MODELS.md"}


def guide_names() -> list[str]:
    from ..install import GUIDE
    return [*GUIDES_TOP, *sorted(p.stem for p in (GUIDE / "deploy").glob("*.md"))]


def cmd_guide(args: argparse.Namespace) -> None:
    from ..install import GUIDE
    name = args.name or "setup"
    top = GUIDES_TOP
    path = GUIDE / (top[name] if name in top else f"deploy/{name.removeprefix('deploy/')}.md")
    if not path.is_file():
        raise TowerError(f"no guide {name!r} (guides: {', '.join(guide_names())})")
    print(f"<!-- {path} -->")
    print(path.read_text(encoding="utf-8"))


def cmd_workspaces(args: argparse.Namespace) -> None:
    if args.sub == "remove":
        config_mod.unregister(args.slug)
        print(f"{args.slug} removed from the list (its .tower/ is untouched)")
        return
    regs = config_mod.workspaces()
    if not regs:
        print("No workspaces yet. `dah init` in a workspace registers it.")
        return
    for slug, home in regs.items():
        try:
            c = config_mod.load(home)
        except ConfigError:
            print(f"{slug:<16} (missing: {home})")
            continue
        state = f"running · http://{c.web_host}:{c.web_port}" if tower_pid(c) else "stopped"
        print(f"{slug:<16} {c.name:<20} {state:<36} {home}")
    print(f"\nAny running board shows every workspace here under /w/<name>/ (switch from its title). List: "
          f"{config_mod.registry_path()}")
