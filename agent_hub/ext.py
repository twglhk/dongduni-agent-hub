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
EVENT_KINDS = ("report", "item", "decided", "memo", "task", "lane")


def board_dir(home: Path) -> Path:
    return home / TOWER_DIR / BOARD_DIR


def board_override(home: Path) -> Path | None:
    page = board_dir(home) / "index.html"
    return page if page.is_file() else None


def board_extras(home: Path) -> dict[str, bool]:
    d = board_dir(home)
    return {"css": (d / "extra.css").is_file(), "js": (d / "extra.js").is_file()}


class Extensions:
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
                    except Exception as e:
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
    key = Path(home).resolve()
    if key not in _REGISTRY:
        _REGISTRY[key] = Extensions(key, log or (lambda _msg: None))
    return _REGISTRY[key]
