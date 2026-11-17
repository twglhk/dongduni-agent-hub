from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Iterable

Row = dict[str, Any]
Key = tuple[int, int, float]


def task_who(t: Row) -> str:
    return str(t.get("requester") or t.get("hub") or "")


def message_who(m: Row) -> str:
    return str(m.get("sender") or "")


def turns(rows: Iterable[Row], who: Callable[[Row], str], running: Iterable[Row] = (),
          first: Callable[[Row], bool] = lambda _: False) -> dict[int, Key]:
    seen = Counter(who(r) for r in running)
    out: dict[int, Key] = {}
    for i, r in enumerate(rows):
        if first(r):
            out[r["id"]] = (0, 0, i)
            continue
        k = who(r)
        out[r["id"]] = (1, seen[k], i)
        seen[k] += 1
    return out


def in_turns(rows: list[Row], who: Callable[[Row], str], running: Iterable[Row] = (),
             first: Callable[[Row], bool] = lambda _: False) -> list[Row]:
    key = turns(rows, who, running, first)
    return sorted(rows, key=lambda r: key[r["id"]])


def waiting_by_person(rows: Iterable[Row], who: Callable[[Row], str]) -> dict[str, int]:
    c = Counter(who(r) for r in rows)
    return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))
