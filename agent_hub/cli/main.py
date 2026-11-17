from __future__ import annotations

import sys

from ..config import ConfigError
from ..store import StoreError
from .common import TowerError
from .hub import (cmd_cancel, cmd_decide, cmd_doing, cmd_escalate, cmd_infra, cmd_memo, cmd_scratch, cmd_stage,
                  cmd_task, cmd_task_bump, cmd_tell, cmd_wrap)
from .lanes import cmd_item_add, cmd_item_merge, cmd_item_update, cmd_mine, cmd_report
from .manage import cmd_engine, cmd_lane, cmd_member
from .parser import build_parser
from .prefs import cmd_prefs
from .reading import (cmd_blame, cmd_deliverable, cmd_items, cmd_last, cmd_log, cmd_ratings, cmd_retros, cmd_show,
                      cmd_signal, cmd_stats, cmd_status, cmd_tasks)
from .running import cmd_pause, cmd_restart, cmd_run, cmd_start, cmd_stop
from .setup import cmd_discover, cmd_guide, cmd_init, cmd_install, cmd_skill, cmd_uninstall, cmd_update, cmd_workspaces


HANDLERS = {
    "mine": cmd_mine, "report": cmd_report, "task": cmd_task, "task-bump": cmd_task_bump, "doing": cmd_doing, "scratch": cmd_scratch, "stage": cmd_stage, "blame": cmd_blame, "deliverable": cmd_deliverable, "ratings": cmd_ratings, "tell": cmd_tell,
    "wrap": cmd_wrap, "decide": cmd_decide, "escalate": cmd_escalate, "cancel": cmd_cancel, "memo": cmd_memo, "infra": cmd_infra,
    "status": cmd_status, "stats": cmd_stats, "retros": cmd_retros, "signal": cmd_signal, "show": cmd_show, "items": cmd_items, "tasks": cmd_tasks, "log": cmd_log, "last": cmd_last,
    "run": cmd_run, "start": cmd_start, "restart": cmd_restart, "stop": cmd_stop, "pause": cmd_pause, "resume": cmd_pause,
    "discover": cmd_discover, "init": cmd_init, "guide": cmd_guide, "workspaces": cmd_workspaces, "install": cmd_install, "update": cmd_update, "skill": cmd_skill, "uninstall": cmd_uninstall, "lane": cmd_lane, "engine": cmd_engine, "member": cmd_member, "prefs": cmd_prefs,
    "item": lambda a: {"add": cmd_item_add, "update": cmd_item_update, "merge": cmd_item_merge}[a.sub](a),
}


def utf8_output() -> None:
    """Agents read dah's output through a pipe; on Windows that defaults to the ANSI code page, which cannot
    hold Korean or Japanese text. Board text is UTF-8 everywhere."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower().replace("-", "") != "utf8":
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> None:
    utf8_output()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:2] == ["task", "bump"]:  # `dah task LANE` takes a lane first, so `bump` is routed before argparse sees it
        argv = ["task-bump", *argv[2:]]
    args = build_parser().parse_args(argv)
    try:
        HANDLERS[args.cmd](args)
    except (TowerError, ConfigError, StoreError) as e:
        print(f"dah: {e}", file=sys.stderr)
        sys.exit(1)
