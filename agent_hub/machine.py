"""This machine's load, CPU, memory and swap for the board, each graded green / amber / red against its own spec.

Cut points come from the machine itself (its cores and RAM), not fixed numbers:
- load   = 1-minute load average per core: green < 2.0, amber < 4.0, red from 4.0.
- cpu    = share of all cores busy (100 = every core): green < 60, amber < 85, red from 85.
- memory = share of RAM free to hand out without swapping: green >= 20 %, amber >= 10 %, red below. On macOS the
           kernel's own memory pressure level (the signal behind Activity Monitor's graph) wins when it is readable.
- swap   = swap in use as a share of RAM: green < 10 %, amber < 25 %, red from 25 %.
Reads: macOS `sysctl`, `vm_stat`, `ps`; Linux `/proc`; Windows memory through GlobalMemoryStatusEx and nothing else.
Whatever cannot be read is None and shows as "unknown" on the board, never as 0.

The tower also writes why each queued task is not starting (`waits.json`, from engine.py) so the board can say so.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

LOAD_CUTS = (2.0, 4.0)     # per core
CPU_CUTS = (60.0, 85.0)    # percent of all cores
FREE_CUTS = (20.0, 10.0)   # percent of RAM free (low is bad)
SWAP_CUTS = (10.0, 25.0)   # percent of RAM swapped out
PRESSURE = {"1": "g", "2": "a", "4": "r"}
TTL = 5.0
WAITS = "waits.json"


def level(value: float | None, amber: float, red: float, high_bad: bool = True) -> str | None:
    """'g' | 'a' | 'r' for a value against two cut points; None when unknown."""
    if value is None:
        return None
    if not high_bad:
        return "g" if value >= amber else "a" if value >= red else "r"
    return "g" if value < amber else "a" if value < red else "r"


def worse(*lv: str | None) -> str | None:
    got = [x for x in lv if x]
    return max(got, key="gar".index) if got else None


def _run(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------- parsers (pure, tested with captured text)

def parse_vm_stat(text: str) -> float | None:
    """Free + inactive + speculative pages of `vm_stat` in GB."""
    page = re.search(r"page size of (\d+) bytes", text)
    if not page:
        return None
    n, found = 0, False
    for key in ("Pages free", "Pages inactive", "Pages speculative"):
        m = re.search(rf"^{key}:\s+(\d+)", text, re.M)
        if m:
            n += int(m.group(1))
            found = True
    return n * int(page.group(1)) / 1024 ** 3 if found else None


def parse_swapusage(text: str) -> float | None:
    """`sysctl -n vm.swapusage` -> GB used."""
    m = re.search(r"used = ([\d.]+)M", text)
    return float(m.group(1)) / 1024 if m else None


def parse_meminfo(text: str) -> dict[str, float]:
    """/proc/meminfo -> GB for MemTotal, MemAvailable, SwapTotal, SwapFree (those present)."""
    out: dict[str, float] = {}
    for key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
        m = re.search(rf"^{key}:\s+(\d+)\s*kB", text, re.M)
        if m:
            out[key] = int(m.group(1)) / 1024 ** 2
    return out


def parse_proc_stat(text: str) -> tuple[int, int] | None:
    """/proc/stat first line -> (busy jiffies, total jiffies)."""
    m = re.match(r"cpu\s+([\d\s]+)", text)
    if not m:
        return None
    v = [int(x) for x in m.group(1).split()]
    if len(v) < 4:
        return None
    idle = v[3] + (v[4] if len(v) > 4 else 0)  # idle + iowait
    return sum(v) - idle, sum(v)


def cpu_from_ps(text: str, cores: int) -> float | None:
    try:
        vals = [float(x) for x in text.split()]
    except ValueError:
        return None
    return sum(vals) / max(1, cores) if vals else None


# ---------------------------------------------------------------- readers per system

def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def read_darwin(cores: int) -> dict[str, Any]:
    sysctl = lambda name: _run(["/usr/sbin/sysctl", "-n", name]).strip()
    try:
        ram = int(sysctl("hw.memsize")) / 1024 ** 3
    except ValueError:
        ram = None
    return {"ram_gb": ram, "free_gb": parse_vm_stat(_run(["/usr/bin/vm_stat"])),
            "swap_gb": parse_swapusage(sysctl("vm.swapusage")),
            "cpu": cpu_from_ps(_run(["/bin/ps", "-A", "-o", "%cpu="]), cores),
            "pressure": PRESSURE.get(sysctl("kern.memorystatus_vm_pressure_level"))}


def read_linux(cores: int, sample: float = 0.2) -> dict[str, Any]:
    mi = parse_meminfo(_read("/proc/meminfo"))
    a = parse_proc_stat(_read("/proc/stat"))
    cpu = None
    if a:
        time.sleep(sample)
        b = parse_proc_stat(_read("/proc/stat"))
        if b and b[1] > a[1]:
            cpu = 100.0 * (b[0] - a[0]) / (b[1] - a[1])
    swap = mi["SwapTotal"] - mi["SwapFree"] if "SwapTotal" in mi and "SwapFree" in mi else None
    return {"ram_gb": mi.get("MemTotal"), "free_gb": mi.get("MemAvailable"), "swap_gb": swap, "cpu": cpu, "pressure": None}


def read_windows() -> dict[str, Any]:
    out: dict[str, Any] = {"ram_gb": None, "free_gb": None, "swap_gb": None, "cpu": None, "pressure": None}
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):  # type: ignore[attr-defined]
            out["ram_gb"] = st.ullTotalPhys / 1024 ** 3
            out["free_gb"] = st.ullAvailPhys / 1024 ** 3
    except Exception:
        pass
    return out


def read_raw(platform: str = sys.platform) -> dict[str, Any]:
    cores = os.cpu_count() or 1
    try:
        load: float | None = os.getloadavg()[0]
    except (OSError, AttributeError):
        load = None  # Windows has no load average
    if platform == "darwin":
        got = read_darwin(cores)
    elif platform.startswith("linux"):
        got = read_linux(cores)
    elif platform.startswith("win"):
        got = read_windows()
    else:
        got = {"ram_gb": None, "free_gb": None, "swap_gb": None, "cpu": None, "pressure": None}
    return {"cores": cores, "load": load, **got}


# ---------------------------------------------------------------- grading

def _r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(x, nd)


def grade(raw: dict[str, Any]) -> dict[str, Any]:
    """Raw readings -> the board's view: numbers, a level per row ('g'|'a'|'r'|None) and the worst of them."""
    cores = max(1, int(raw.get("cores") or 1))
    load, ram, free, swap, cpu = (raw.get(k) for k in ("load", "ram_gb", "free_gb", "swap_gb", "cpu"))
    per_core = load / cores if load is not None else None
    free_pct = 100.0 * free / ram if free is not None and ram else None
    swap_pct = 100.0 * swap / ram if swap is not None and ram else None
    if cpu is not None:
        cpu = max(0.0, min(100.0, cpu))
    mem_lv = raw.get("pressure") or level(free_pct, *FREE_CUTS, high_bad=False)
    out = {"cores": cores, "ram_gb": _r(ram, 0), "load": _r(load), "load_per_core": _r(per_core, 2),
           "cpu": _r(cpu, 0), "free_gb": _r(free), "free_pct": _r(free_pct, 0), "swap_gb": _r(swap),
           "swap_pct": _r(swap_pct, 0),
           "load_lv": level(per_core, *LOAD_CUTS), "cpu_lv": level(cpu, *CPU_CUTS), "mem_lv": mem_lv,
           "swap_lv": level(swap_pct, *SWAP_CUTS)}
    out["lv"] = worse(out["load_lv"], out["cpu_lv"], out["mem_lv"], out["swap_lv"])
    return out


class Machine:
    """A few seconds' cache so every board poll does not run `ps` and friends."""

    def __init__(self, read: Callable[[], dict[str, Any]] = read_raw, ttl: float = TTL,
                 clock: Callable[[], float] = time.monotonic):
        self.read, self.ttl, self.clock = read, ttl, clock
        self.lock = threading.Lock()
        self.cached: dict[str, Any] | None = None
        self.at = 0.0

    def view(self) -> dict[str, Any]:
        with self.lock:
            if self.cached is None or self.clock() - self.at > self.ttl:
                try:
                    self.cached = grade(self.read())
                except Exception:  # never a fake number: everything unknown
                    self.cached = grade({"cores": os.cpu_count() or 1})
                self.at = self.clock()
            return self.cached


# ---------------------------------------------------------------- why queued tasks wait (engine -> board)

def write_waits(state_dir: Path, waits: dict[int, str]) -> None:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        tmp = state_dir / (WAITS + ".tmp")
        tmp.write_text(json.dumps({str(k): v for k, v in waits.items()}), encoding="utf-8")
        os.replace(tmp, state_dir / WAITS)
    except OSError:
        pass


def read_waits(state_dir: Path) -> dict[str, str]:
    try:
        d = json.loads((state_dir / WAITS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
