"""Stage ratings: the first-pass rate of every stage of an engine's process, read from the task ledger. No LLM, standard library only.

A rated task is one that came with a `Line: #<row> ... stage <n | n-m>` header (or `prepare`, rated apart as stage "prepare") and
either closed or was cancelled after it started. Cancelled before starting, and still-running tasks, are not counted (n).
A task is a *first pass* when all of these hold:
  - it closed (a task cancelled while running is not one);
  - the hub never sent it a fix: a `dah tell` whose text contains `fix:` (the word the hub writes to send work back);
  - no task for the same deliverable key, row and stage started and was cancelled (a redo is not a first pass), and no later
    `Line: ... fix` task for the same key, row and stage exists (that later task is the proof the first one was sent back);
  - when the stage's card says `Passed by: human`: the human adopted the deliverable in the first round, meaning the first recorded
    outcome (`dah deliverable KEY adopted|redirected|dropped`) is `adopted` and no board item for that key was rejected before it.
    A task whose human result has not been recorded yet is left out of n (pending) until it is.
The score is first passes / n as a whole percent (rounded down); a stage's state comes from BARS, one place:
new n<5 · best >=85 % · good 65-84 % · needs fixing <65 % · needs rework: n>=10, <50 % and the last two fixes of the stage's card
(a commit that raised the `— vN` in its title, read from git) did not raise the score. "Did not raise" = the attempts split by those two
fix times into three parts (before, between, after), all non-empty, with after <= between <= before. A fix of a stage = that stage's
pattern card; a fix of the process = the process card plus every stage card. A process score is the sum of its numeric stages.

Tokens: one task's cost = the ledger's new reads (tok_new) + writes (tok_out); cache reads are left out, tasks not yet measured too.
A task over a stage range splits its tokens evenly across the stages. Per attempt = median of the measured attempts; per adopted
deliverable = the stage's attempts' token sum (failed ones included) / the number of distinct deliverables whose last outcome is
adopted (None when there is none).

Blame: a failure counts against the stage that should have caught it. A lane writes `dah blame <stage>`; the hub confirms or changes it
(`dah blame --confirm --task N [stage]`), and the confirmed stage wins. A task with a blame fails only that stage and passes the others it
went through (a blame outside its stages adds one failed attempt to the blamed stage). A failed task without a blame is counted the
plain way and marked `blame?`.
"""

from __future__ import annotations

import re
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from .store import HUB, INFRA, Store

# The bars. One block; tuning changes only this.
BARS: dict[str, Any] = {
    "new_below": 5,        # n below this is new (grey)
    "best_at": 85,         # % at or above is best (green)
    "fix_below": 65,       # % below needs fixing (amber), at or above is good (blue)
    "rework_below": 50,    # % below this, with n >= rework_min_n and rework_fixes fixes that did not help, needs rework (red)
    "rework_min_n": 10,
    "rework_fixes": 2,
    "candidate_at": 90,    # % at or above, n >= candidate_min_n and a human adoption rate >= human_at % over human_min_n results: lighter review
    "candidate_min_n": 10,
    "human_at": 90,
    "human_min_n": 10,
}

LANGS = ("en", "ko", "ja", "zh", "es")
STATES: dict[str, tuple[dict[str, str], str]] = {
    "new": ({"en": "new", "ko": "신규", "ja": "新規", "zh": "新建", "es": "nuevo"}, "none"),
    "best": ({"en": "best", "ko": "최적", "ja": "最適", "zh": "最佳", "es": "óptimo"}, "green"),
    "good": ({"en": "good", "ko": "양호", "ja": "良好", "zh": "良好", "es": "bueno"}, "blue"),
    "fix": ({"en": "needs fixing", "ko": "보완 필요", "ja": "要改善", "zh": "需改进", "es": "a corregir"}, "amber"),
    "rework": ({"en": "needs rework", "ko": "리팩토링 필요", "ja": "再設計が必要", "zh": "需要重构", "es": "a rehacer"}, "red"),
}
WORDS: dict[str, dict[str, str]] = {
    "head": {"en": "Ratings (first-pass rate)", "ko": "레이팅(첫 통과율)", "ja": "レーティング(初回合格率)", "zh": "评级(一次通过率)", "es": "Valoraciones (tasa de primer paso)"},
    "stage": {"en": "stage", "ko": "단계", "ja": "ステージ", "zh": "阶段", "es": "etapa"},
    "attempt": {"en": "attempt", "ko": "시도", "ja": "試行", "zh": "尝试", "es": "intento"},
    "adopted": {"en": "adopted", "ko": "채택", "ja": "採用", "zh": "采纳", "es": "adoptado"},
    "median": {"en": "median tokens", "ko": "토큰 중앙값", "ja": "トークン中央値", "zh": "令牌中位数", "es": "mediana de tokens"},
    "measured": {"en": "measured", "ko": "잰", "ja": "計測", "zh": "已测", "es": "medidos"},
    "per_adopted": {"en": "per adopted deliverable", "ko": "채택 산출물당", "ja": "採用成果物あたり", "zh": "每个被采纳的成果", "es": "por entregable adoptado"},
    "pending": {"en": "waiting for the human's result", "ko": "사람 결과 기다림", "ja": "人の結果待ち", "zh": "等待人的结果", "es": "esperando el resultado humano"},
    "fixes": {"en": "card fixes", "ko": "고침", "ja": "改訂", "zh": "修订", "es": "revisiones"},
    "candidate": {"en": "lighter review candidate", "ko": "검수 줄임 후보", "ja": "レビュー軽減候補", "zh": "可减少评审", "es": "candidato a menos revisión"},
    "next": {"en": "fix this stage next", "ko": "다음에 고칠 단계", "ja": "次に直すステージ", "zh": "下一个要修的阶段", "es": "la próxima etapa a corregir"},
    "prepare": {"en": "prepare", "ko": "준비", "ja": "準備", "zh": "准备", "es": "preparación"},
    "first_pass": {"en": "first pass", "ko": "첫 통과", "ja": "初回合格", "zh": "一次通过", "es": "primer paso"},
    "none": {"en": "No rated tasks yet: a task whose brief has a `Line:` header with a stage counts once it closes.",
             "ko": "아직 센 작업이 없습니다. 브리프에 단계가 적힌 `Line:` 줄이 있는 작업이 끝나면 쌓입니다.",
             "ja": "まだ数えた作業がありません。ステージ付きの `Line:` 行を持つ作業が終わると集計されます。",
             "zh": "还没有计入的任务:带阶段的 `Line:` 行的任务结束后会累积在这里。",
             "es": "Aún no hay tareas valoradas: cuenta una tarea con cabecera `Line:` y etapa cuando se cierra."},
    "weak_none": {"en": "No process needs fixing or rework.", "ko": "보완 필요·리팩토링 필요 공정이 없습니다.", "ja": "改善・再設計が必要なプロセスはありません。",
                  "zh": "没有需要改进或重构的流程。", "es": "Ningún proceso necesita corrección ni rehacerse."},
}
HUMAN_BY = "human"
SENDBACK = "fix:"  # a hub tell containing this sends the task back


def word(key: str, lang: str = "en") -> str:
    return WORDS[key].get(lang) or WORDS[key]["en"]


def label(state: str, lang: str = "en") -> str:
    return STATES[state][0].get(lang) or STATES[state][0]["en"]


def pct_of(n: int, passed: int) -> int | None:
    return passed * 100 // n if n else None


def tier(n: int, passed: int, human: dict[str, Any] | None = None, stuck: bool = False, bars: dict[str, Any] = BARS) -> tuple[str, bool]:
    """(state key, lighter-review candidate?). `stuck` = fixes happened and the score did not rise (`stuck_after_fixes`)."""
    pct = pct_of(n, passed)
    if n < bars["new_below"] or pct is None:
        return "new", False
    if pct >= bars["best_at"]:
        state = "best"
    elif pct >= bars["fix_below"]:
        state = "good"
    elif stuck and n >= bars["rework_min_n"] and pct < bars["rework_below"]:
        state = "rework"
    else:
        state = "fix"
    h = human or {}
    cand = pct >= bars["candidate_at"] and n >= bars["candidate_min_n"] and h.get("resolved", 0) >= bars["human_min_n"] \
        and h.get("adopted", 0) * 100 >= bars["human_at"] * max(1, h.get("resolved", 1))
    return state, bool(cand)


def stuck_after_fixes(attempts: list[tuple[float, bool]], fixes: list[float], bars: dict[str, Any] = BARS) -> bool:
    """Split the attempts (time, passed) at the last two fix times; True when the three parts all have attempts and the rate never rose."""
    k = int(bars["rework_fixes"])
    if k != 2 or len(fixes) < k:
        return False
    f1, f2 = sorted(fixes)[-2:]
    segs = ([a for a in attempts if a[0] < f1], [a for a in attempts if f1 <= a[0] < f2], [a for a in attempts if a[0] >= f2])
    if not all(segs):
        return False
    r = [sum(1 for _, ok in sg if ok) / len(sg) for sg in segs]
    return r[1] <= r[0] and r[2] <= r[1]


def _version(title: str) -> int | None:
    m = re.search(r"[—–-]\s*v(\d+)\s*$", title.strip())
    return int(m.group(1)) if m else None


def version_bumps(repo: Path, rel: str) -> list[float]:
    """Commit times (ascending) at which the card's title `— vN` went up. The commit that created the file and commits that did
    not change the version do not count. No git or no history gives []."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "log", "--follow", "-p", "-U0", "--format=@%ct", "--", rel],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    bumps: list[float] = []
    ts, old, new = 0.0, None, None

    def flush() -> None:
        if ts and old is not None and new is not None and new > old:
            bumps.append(ts)

    for ln in out.splitlines():
        if ln.startswith("@") and ln[1:].isdigit():
            flush()
            ts, old, new = float(ln[1:]), None, None
        elif ln.startswith(("-#", "+#")):
            v = _version(ln[1:])
            if v is not None:
                if ln[0] == "-":
                    old = v
                else:
                    new = v
    flush()
    return sorted(bumps)


def short_model(model: str | None, effort: str | None) -> str:
    m = (model or "").replace("claude-", "") or "?"
    return f"{m}/{effort}" if effort else m


def stage_numbers(stage: str | None) -> list[str]:
    """"2-4" -> ["2","3","4"]; "3" -> ["3"]; "prepare" -> ["prepare"]; None -> []."""
    if not stage:
        return []
    m = re.fullmatch(r"(\d+)-(\d+)", stage)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return [str(i) for i in range(a, b + 1)] if 0 < a <= b <= 50 else [stage]
    return [stage]


def blame_stage(text: str | None) -> str | None:
    """`stage 3` · `3` · `prepare` -> the stage name (digits only when there are digits)."""
    t = " ".join(str(text or "").split())
    if not t:
        return None
    m = re.search(r"\d+(?:-\d+)?", t)
    return m.group(0) if m else re.sub(r"^stage\s+", "", t, flags=re.I).lower()


def collect(store: Store, default_engine: str = "default") -> dict[str, Any]:
    """The raw ledger for rating: rated tasks, fix counts, deliverable events, human rejections, blames."""
    with store.conn() as con:
        tasks = []
        for r in con.execute("SELECT * FROM tasks WHERE line_row IS NOT NULL AND started IS NOT NULL "
                             "AND state IN ('closed','cancelled') ORDER BY id"):
            t = dict(r)
            stage = t["line_stage"] or ("prepare" if t["line_mode"] == "prepare" else None)
            if not stage:
                continue
            tasks.append({**t, "line_stage": stage, "engine": t["engine"] or default_engine})
        fixes = {tid: n for tid, n in con.execute(
            "SELECT task_id, count(*) FROM messages WHERE kind='tell' AND sender=? AND lower(text) LIKE ? "
            "AND task_id IS NOT NULL GROUP BY task_id", (HUB, f"%{SENDBACK}%"))}
        task_key = {r[0]: r[1] for r in con.execute("SELECT id, deliverable FROM tasks WHERE deliverable IS NOT NULL")}
        events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in con.execute("SELECT * FROM deliverable_events ORDER BY id"):
            events[r["key"]].append(dict(r))
        rejects: dict[str, list[float]] = defaultdict(list)
        lanes = {r[0] for r in con.execute("SELECT lane FROM lanes")} | {HUB, INFRA}
        for r in con.execute("SELECT * FROM items WHERE state='decided' AND verdict='no' AND question IS NULL"):
            if r["decided_by"] in lanes:  # only a human's no is a rejection
                continue
            key = task_key.get(r["task_id"])
            if key:
                rejects[key].append(float(r["decided"] or 0))
    return {"tasks": tasks, "fixes": fixes, "events": events, "rejects": rejects, "blames": store.blames()}


def first_round_adopted(key: str | None, events: dict[str, list[dict[str, Any]]], rejects: dict[str, list[float]]) -> bool | None:
    """Did the human adopt the deliverable in the first round? None while no outcome is recorded."""
    ev = events.get(key or "")
    if not key or not ev:
        return None
    first = ev[0]
    if first["outcome"] != "adopted":
        return False
    return not any(t < float(first["created"]) for t in rejects.get(key, []))


def compute(raw: dict[str, Any], gated: set[tuple[str, str, str]], bars: dict[str, Any] = BARS,
            fixes_at: dict[tuple[str, str, str], list[float]] | None = None) -> dict[tuple[str, str, str], dict[str, Any]]:
    """(engine, row, stage) -> {n, pass, rate, pct, state, color, candidate, models, human, pending, attempts, tokens...}.
    `gated` = the (engine, row, stage) whose card says `Passed by: human`; `fixes_at` = that stage's card fix times."""
    tasks, fixes, events, rejects, blames = raw["tasks"], raw["fixes"], raw["events"], raw["rejects"], raw.get("blames", {})
    redo: set[tuple[Any, str, str, str]] = set()
    for t in tasks:
        if t["state"] == "cancelled" and t.get("deliverable"):
            redo.add((t["deliverable"], t["engine"], str(t["line_row"]), t["line_stage"]))
    sent_back: dict[tuple[Any, str, str, str], int] = {}  # a later fix task for the same key, row and stage sends the earlier ones back
    for t in tasks:
        if t["line_mode"] == "fix" and t.get("deliverable"):
            for st in stage_numbers(t["line_stage"]):
                k = (t["deliverable"], t["engine"], str(t["line_row"]), st)
                sent_back[k] = max(sent_back.get(k, 0), t["id"])
    out: dict[tuple[str, str, str], dict[str, Any]] = {}

    def slot_of(eng: str, row: str, st: str) -> dict[str, Any]:
        return out.setdefault((eng, row, st), {"n": 0, "pass": 0, "models": {}, "human": {"resolved": 0, "adopted": 0}, "pending": 0, "ids": [],
                                               "attempts": [], "toks": [], "adopted_keys": set(), "blame_missing": 0, "blamed": 0})

    for t in tasks:
        eng, row = t["engine"], str(t["line_row"])
        base_ok = t["state"] == "closed" and not fixes.get(t["id"], 0) \
            and not (t.get("deliverable") and (t["deliverable"], eng, row, t["line_stage"]) in redo)
        adopted = first_round_adopted(t.get("deliverable"), events, rejects)
        for st in stage_numbers(t["line_stage"]):
            sb = bool(t.get("deliverable")) and sent_back.get((t["deliverable"], eng, row, st), 0) > t["id"]
            slot = slot_of(eng, row, st)
            is_gated = (eng, row, st) in gated and st != "prepare"
            if is_gated and adopted is None and base_ok and not sb:
                slot["pending"] += 1
                continue
            ok = base_ok and not sb and (adopted is True if is_gated else True)
            bl = blames.get(t["id"])
            if bl:  # a failure counts only against the stage that should have caught it
                ok = blame_stage(bl["effective"]) != st
                slot["blamed"] += int(not ok)
            elif not ok:
                slot["blame_missing"] += 1
            slot["n"] += 1
            slot["pass"] += int(ok)
            slot["ids"].append(t["id"])
            slot["attempts"].append((float(t.get("started") or 0), ok))
            if t.get("tok_at") is not None:
                slot["toks"].append(((t.get("tok_new") or 0) + (t.get("tok_out") or 0)) / len(stage_numbers(t["line_stage"])))
            m = slot["models"].setdefault(short_model(t.get("model"), t.get("effort")), {"n": 0, "pass": 0})
            m["n"] += 1
            m["pass"] += int(ok)
            if t.get("deliverable") and events.get(t["deliverable"]):
                last = events[t["deliverable"]][-1]["outcome"]
                slot["human"]["resolved"] += 1
                slot["human"]["adopted"] += int(last == "adopted")
                if last == "adopted":
                    slot["adopted_keys"].add(t["deliverable"])
    for t in tasks:  # a blame outside the task's own stages adds one failed attempt to the blamed stage
        bl = blames.get(t["id"])
        bs = blame_stage(bl["effective"]) if bl else None
        if bs and bs not in stage_numbers(t["line_stage"]):
            slot = slot_of(t["engine"], str(t["line_row"]), bs)
            slot["n"] += 1
            slot["blamed"] += 1
            slot["ids"].append(t["id"])
            slot["attempts"].append((float(t.get("started") or 0), False))
    for key, slot in out.items():
        slot["rate"] = slot["pass"] / slot["n"] if slot["n"] else None
        toks = slot.pop("toks")
        slot["tok_n"] = len(toks)
        slot["tok_median"] = int(statistics.median(toks)) if toks else None
        slot["tok_per_adopted"] = int(sum(toks) / len(slot["adopted_keys"])) if toks and slot["adopted_keys"] else None
        slot["adopted_n"] = len(slot.pop("adopted_keys"))
        slot["pct"] = pct_of(slot["n"], slot["pass"])
        fx = (fixes_at or {}).get(key, [])
        slot["fixes"] = len(fx)
        slot["state"], slot["candidate"] = tier(slot["n"], slot["pass"], slot["human"], stuck_after_fixes(slot["attempts"], fx, bars), bars)
        slot["color"] = STATES[slot["state"]][1]
    return out


def attach(engines_rows: dict[str, list[dict[str, Any]]], store: Store, repo: Path | None = None, skills: dict[str, Path] | None = None,
           bars: dict[str, Any] = BARS) -> list[dict[str, Any]]:
    """Rate every library row. `engines_rows` = engine id -> `engines.build` rows; `skills` = engine id -> its skills directory (to
    find the cards in git; without `repo` no fixes are counted). Returns one dict per row with `process` (the row's score, summed
    over the numeric stages), `stages` (each with `rating`), `prepare` and `weakest`."""
    raw = collect(store)
    gated = {(eng, r["n"], str(s["n"])) for eng, rows in engines_rows.items() for r in rows if r["process"]
             for s in r["process"]["stages"] if HUMAN_BY in s["by"].lower()}
    fixes_at: dict[tuple[str, str, str], list[float]] = {}
    row_fixes: dict[tuple[str, str], list[float]] = {}
    if repo is not None:
        for eng, rows in engines_rows.items():
            base = (skills or {}).get(eng)
            if base is None:
                continue
            for r in rows:
                p = r["process"]
                if not p:
                    continue

                def bumps(rel: str, base: Path = base) -> list[float]:
                    try:
                        return version_bumps(repo, str((base / rel).resolve().relative_to(repo.resolve())))
                    except ValueError:
                        return []

                allf = list(bumps(p["path"]))
                for s in p["stages"]:
                    c = s.get("card")
                    f = bumps(c["path"]) if c and c["exists"] else []
                    fixes_at[(eng, r["n"], str(s["n"]))] = f
                    allf += f
                row_fixes[(eng, r["n"])] = sorted(allf)
    rated = compute(raw, gated, bars, fixes_at)
    out: list[dict[str, Any]] = []
    for eng, rows in engines_rows.items():
        for r in rows:
            p = r["process"]
            stages = []
            tot = {"n": 0, "pass": 0, "pending": 0}
            attempts: list[tuple[float, bool]] = []
            human = {"resolved": 0, "adopted": 0}
            weakest: tuple[float, int, str] | None = None
            for s in (p["stages"] if p else []):
                g = rated.get((eng, r["n"], str(s["n"])))
                stages.append({"n": str(s["n"]), "name": s["name"], "rating": g})
                if not g:
                    continue
                tot["n"] += g["n"]
                tot["pass"] += g["pass"]
                tot["pending"] += g["pending"]
                attempts += g["attempts"]
                for k in human:
                    human[k] += g["human"][k]
                if g["state"] != "new" and g["rate"] is not None and g["rate"] < 1.0:
                    key = (g["rate"], -g["n"], str(s["n"]))
                    if weakest is None or key < weakest:
                        weakest = key
            for s in stages:
                s["weakest"] = bool(weakest and s["n"] == weakest[2])
            state, cand = tier(tot["n"], tot["pass"], human, stuck_after_fixes(attempts, row_fixes.get((eng, r["n"]), []), bars), bars)
            prep = rated.get((eng, r["n"], "prepare"))
            out.append({"engine": eng, "row": r["n"], "type": r["type"], "lane": r["lane"], "stages": stages,
                        "process": {**tot, "pct": pct_of(tot["n"], tot["pass"]), "state": state, "color": STATES[state][1], "candidate": cand,
                                    "human": human, "fixes": len(row_fixes.get((eng, r["n"]), []))},
                        "prepare": {"n": prep["n"], "pass": prep["pass"]} if prep else None})
    return out


def k_text(v: int) -> str:
    return f"{v / 1000:.1f}K" if v < 1_000_000 else f"{v / 1e6:.2f}M"


def tok_text(g: dict[str, Any], lang: str = "en") -> str:
    """`median tokens 12.3K/attempt (measured 4) · per adopted deliverable 45K (2)`; empty without a measured attempt."""
    if not g.get("tok_n"):
        return ""
    out = f"{word('median', lang)} {k_text(g['tok_median'])}/{word('attempt', lang)} ({word('measured', lang)} {g['tok_n']})"
    per = f"{k_text(g['tok_per_adopted'])} ({g['adopted_n']})" if g.get("tok_per_adopted") is not None else "-"
    return f"{out} · {word('per_adopted', lang)} {per}"


def score_text(g: dict[str, Any]) -> str:
    """`pass/n · pct%`; `0/0` with no attempt."""
    return f"{g['pass']}/{g['n']}" + (f" · {g['pct']}%" if g["n"] else "")


def bars_text(bars: dict[str, Any] = BARS, lang: str = "en") -> str:
    lab = lambda s: label(s, lang)  # noqa: E731
    return (f"{lab('new')} n<{bars['new_below']} · {lab('best')} >={bars['best_at']}% · {lab('good')} {bars['fix_below']}-{bars['best_at'] - 1}% · "
            f"{lab('fix')} <{bars['fix_below']}% · {lab('rework')} n>={bars['rework_min_n']}, <{bars['rework_below']}%, {bars['rework_fixes']} {word('fixes', lang)}")


def weak_processes(rated: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows that need fixing or rework: rework first, then by score."""
    weak = [r for r in rated if r["process"]["state"] in ("fix", "rework")]
    weak.sort(key=lambda r: (r["process"]["state"] != "rework", r["process"]["pct"] if r["process"]["pct"] is not None else 0))
    return weak


def render(rated: list[dict[str, Any]], bars: dict[str, Any] = BARS, lang: str = "en", weak: bool = False) -> str:
    """What `dah ratings` prints: per row its score and state, under it each stage's score, state, models, tokens."""
    rows = weak_processes(rated) if weak else rated
    out = [f"{word('head', lang)} · {bars_text(bars, lang)}"]
    shown = False
    for r in rows:
        g = r["process"]
        lines: list[str] = []
        for s in r["stages"]:
            sg = s["rating"]
            if not sg or (not sg["n"] and not sg["pending"]):
                continue
            by = ", ".join(f"{k} {v['pass']}/{v['n']}" for k, v in sorted(sg["models"].items()))
            extra = (f" · {word('candidate', lang)}" if sg["candidate"] else "") + (f" · <- {word('next', lang)}" if s["weakest"] else "") \
                + (f" · {word('pending', lang)} {sg['pending']}" if sg["pending"] else "") \
                + (f" · blame {sg['blamed']}" if sg["blamed"] else "") + (f" · blame? {sg['blame_missing']}" if sg["blame_missing"] else "") \
                + (f" · {word('fixes', lang)} {sg['fixes']}" if sg["fixes"] else "")
            lines.append(f"    {word('stage', lang)} {s['n']} {s['name']}: {score_text(sg)} · {label(sg['state'], lang)}{extra}" + (f"  [{by}]" if by else ""))
            if tok_text(sg, lang):
                lines.append(f"        {tok_text(sg, lang)}")
        if r["prepare"]:
            lines.append(f"    {word('prepare', lang)}: {r['prepare']['pass']}/{r['prepare']['n']} {word('first_pass', lang)}")
        if lines or (weak and g["state"] in ("fix", "rework")):
            shown = True
            out.append(f"  [{r['engine']}] #{r['row']} {r['type']} — {score_text(g)} · {label(g['state'], lang)}")
            out += lines
    if not shown:
        out.append("  " + word("weak_none" if weak else "none", lang))
    return "\n".join(out)
