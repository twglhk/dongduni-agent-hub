"""Workspace extensions. agent-hub is a skeleton; a workspace extends it from its own `.tower/`, which survives
`dah update` and lives in the workspace's git:

  .tower/board/index.html     replaces the built-in board (`/?builtin` still opens the built-in one)
  .tower/board/extra.css|js   loaded by the built-in board; extra.js gets `window.dah` (sections, tabs, API calls)
  .tower/board/<file>         served at /x/<file> to signed-in people (scripts, styles, images, fonts, data)
  .tower/ext/*.py             Python run by the tower:
      routes = {("GET", "stats"): fn, ("POST", "jira/sync"): fn}   → /api/x/stats, /api/x/jira/sync
          fn(ctx, body) -> dict | (status, dict); ctx: cfg, store, me, query, log. Signed-in people only;
          POST needs the board's `x-tower: 1` header like every built-in POST.
      on_tick(ctx)                    after every tower tick; ctx: cfg, store, log, now
      on_event(ctx, kind, data)       kind: report | item | decided | memo | goal | task (data: that row)
                                      | lane (data: lane, status, was, runtime, terminal)
      on_notify(ctx, notes)           each push the tower sends people (title, lane, alert, the item's fields)
      runtimes = {"name": cls}        an agent runtime: a subclass of runtimes.Adapter, used as "runtime": "name"
      terminals = {"name": cls}       a terminal app for lane windows: a subclass of runtimes.Host

Built-in routes and engine decisions are the contract and cannot be overridden. A failing extension is logged and
skipped; it never stops the board or the tower. Files are reloaded when they change.
"""

from __future__ import annotations

import importlib.util
import threading
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from .config import TOWER_DIR

BOARD_DIR = "board"
EXT_DIR = "ext"
STATIC_SUFFIXES = {".js", ".mjs", ".css", ".json", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
                   ".woff", ".woff2", ".txt", ".md"}
EVENT_KINDS = ("report", "item", "decided", "memo", "goal", "task", "lane")


def board_dir(home: Path) -> Path:
    return home / TOWER_DIR / BOARD_DIR


def board_override(home: Path) -> Path | None:
    page = board_dir(home) / "index.html"
    return page if page.is_file() else None


def board_extras(home: Path) -> dict[str, bool]:
    d = board_dir(home)
    return {"css": (d / "extra.css").is_file(), "js": (d / "extra.js").is_file()}


class Extensions:
    """The `.tower/ext/*.py` modules of one workspace, reloaded when any of them changes."""

    def __init__(self, home: Path, log: Callable[[str], None] = print):
        self.dir = home / TOWER_DIR / EXT_DIR
        self.log = log
        self._lock = threading.Lock()
        self._sig: tuple[tuple[str, int, int], ...] | None = None
        self._mods: list[ModuleType] = []

    def _signature(self) -> tuple[tuple[str, int, int], ...]:
        try:
            files = sorted(p for p in self.dir.glob("*.py") if not p.name.startswith(("_", ".")))
            return tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)
        except OSError:
            return ()

    def modules(self) -> list[ModuleType]:
        with self._lock:
            sig = self._signature()
            if sig != self._sig:
                self._sig, self._mods = sig, []
                loaded: list[str] = []
                for name, *_ in sig:
                    path = self.dir / name
                    try:
                        spec = importlib.util.spec_from_file_location(f"tower_ext_{abs(hash(str(path)))}_{path.stem}",
                                                                      path)
                        if spec is None or spec.loader is None:
                            raise ImportError("cannot load")
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        self._mods.append(mod)
                        loaded.append(path.name)
                    except Exception as e:  # a broken extension is reported, the rest still load
                        self.log(f"extension {path}: not loaded: {type(e).__name__}: {e}")
                if sig:
                    self.log(f"extensions in {self.dir}: {', '.join(loaded) or 'none loaded'}")
            return list(self._mods)

    def routes(self) -> dict[tuple[str, str], Callable[..., Any]]:
        out: dict[tuple[str, str], Callable[..., Any]] = {}
        for mod in self.modules():
            table = getattr(mod, "routes", None)
            if not isinstance(table, dict):
                continue
            for key, fn in table.items():
                if (isinstance(key, tuple) and len(key) == 2 and str(key[0]).upper() in ("GET", "POST")
                        and callable(fn)):
                    out[(str(key[0]).upper(), str(key[1]).strip("/"))] = fn
        return out

    def table(self, name: str) -> dict[str, Any]:
        """A name -> value table the modules declare (`runtimes`, `terminals`), merged in file order."""
        out: dict[str, Any] = {}
        for mod in self.modules():
            value = getattr(mod, name, None)
            if isinstance(value, dict):
                out.update({str(k): v for k, v in value.items()})
        return out

    def hooks(self, name: str) -> list[Callable[..., Any]]:
        return [fn for fn in (getattr(m, name, None) for m in self.modules()) if callable(fn)]

    def call(self, fn: Callable[..., Any], *args: Any) -> Any:
        try:
            return fn(*args)
        except Exception as e:
            where = traceback.extract_tb(e.__traceback__)[-1]
            self.log(f"extension {getattr(fn, '__module__', '?')}.{getattr(fn, '__name__', '?')} failed: "
                     f"{type(e).__name__}: {e} ({Path(where.filename).name}:{where.lineno})")
            return None


_REGISTRY: dict[Path, Extensions] = {}


def registry(home: Path, log: Callable[[str], None] | None = None) -> Extensions:
    """One Extensions per workspace in this process, for code that needs what extensions register (runtimes,
    terminals) without the tower's own instance. Load errors are logged by the tower; here they stay quiet."""
    key = Path(home).resolve()
    if key not in _REGISTRY:
        _REGISTRY[key] = Extensions(key, log or (lambda _msg: None))
    return _REGISTRY[key]
