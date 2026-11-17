from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable

from . import procverify
from .config import HUB, base_lane
from .procverify import VERIFIED_FILE
from .store import HUMAN, Store, StoreError

NEXT, REDO = "next", "redo"
PENDING, HOLD = "pending", "hold"
LIB_DIR = "processes"

LABELS = {
    "en": {NEXT: ("Next", "The next sub-process starts from this output."),
           REDO: ("Redo with fixes", "This sub-process runs again with your note; the new output comes back for a check.")},
    "ko": {NEXT: ("다음으로", "이 결과로 다음 세부 공정을 시작해요."),
           REDO: ("고쳐서 다시", "적어 주신 내용대로 이 세부 공정을 다시 해요. 고친 결과를 다시 확인받아요.")},
    "ja": {NEXT: ("次へ", "この結果で次のサブ工程を始めます。"),
           REDO: ("直してやり直す", "書いていただいた内容でこのサブ工程をやり直します。直した結果をもう一度確認します。")},
    "zh": {NEXT: ("下一步", "用这个结果开始下一个子流程。"),
           REDO: ("修改后重做", "按您写的内容重做这个子流程，新的结果会再次请您确认。")},
    "es": {NEXT: ("Siguiente", "El siguiente subproceso empieza con este resultado."),
           REDO: ("Rehacer con cambios", "Este subproceso se repite con tu nota; el nuevo resultado vuelve para otra revisión.")},
}
VERIFY = {
    "en": ("Mark this process as verified?", "Verified", "Each sub-process runs through from now on; only the final result is checked.",
           "Not yet", "The next run is checked after every sub-process again."),
    "ko": ("이 공정을 검증됨으로 할까요", "검증됨으로", "이제 세부 공정은 이어서 돌고 마지막 결과만 확인해요.",
           "아직", "다음 회차도 세부 공정마다 확인받아요."),
    "ja": ("この工程を検証済みにしますか", "検証済みにする", "これからサブ工程は続けて動き、最後の結果だけ確認します。",
           "まだ", "次回もサブ工程ごとに確認します。"),
    "zh": ("要把这个流程标为已验证吗", "标为已验证", "之后子流程会连续运行，只确认最后的结果。",
           "暂不", "下一轮仍然每个子流程都确认一次。"),
    "es": ("¿Marcar este proceso como verificado?", "Verificado", "Desde ahora los subprocesos corren seguidos; solo se revisa el resultado final.",
           "Aún no", "La próxima ejecución se revisa otra vez tras cada subproceso."),
}
TEXT = {
    "en": {"pending": "waiting for {m}'s check · card #{n}", "missing": "waiting for the check card of {sub}",
           "redo": "{sub} is being redone · card #{n}", "hold": "card #{n} is on hold · the hub decides",
           "someone": "the requester", "more": " and {n} more"},
    "ko": {"pending": "{m} 님의 확인을 기다려요 · 카드 #{n}", "missing": "{sub} 확인 카드를 기다려요",
           "redo": "{sub}를 고쳐서 다시 하는 중이에요 · 카드 #{n}", "hold": "카드 #{n}이 보류됐어요 · 허브가 정해요",
           "someone": "요청한 분", "more": " 외 {n}개"},
    "ja": {"pending": "{m} さんの確認を待っています · カード #{n}", "missing": "{sub} の確認カードを待っています",
           "redo": "{sub} をやり直しています · カード #{n}", "hold": "カード #{n} は保留中 · ハブが決めます",
           "someone": "依頼した人", "more": " ほか {n} 件"},
    "zh": {"pending": "等待 {m} 的确认 · 卡片 #{n}", "missing": "等待 {sub} 的确认卡片",
           "redo": "{sub} 正在重做 · 卡片 #{n}", "hold": "卡片 #{n} 已搁置 · 由 hub 决定",
           "someone": "提出请求的人", "more": " 等 {n} 项"},
    "es": {"pending": "esperando la revisión de {m} · tarjeta #{n}", "missing": "esperando la tarjeta de revisión de {sub}",
           "redo": "{sub} se está rehaciendo · tarjeta #{n}", "hold": "la tarjeta #{n} está en pausa · decide el hub",
           "someone": "quien lo pidió", "more": " y {n} más"},
}


class CheckError(Exception):
    pass


def _lang(lang: str) -> str:
    code = lang.split("-")[0].lower()
    return code if code in TEXT else "en"


def labels(lang: str) -> dict[str, tuple[str, str]]:
    return LABELS[_lang(lang)]


def options(lang: str) -> list[dict[str, Any]]:
    return [{"label": a, "desc": b} for a, b in labels(lang).values()]


def verify_ask(lang: str) -> dict[str, Any]:
    title, yes, yes_desc, no, no_desc = VERIFY[_lang(lang)]
    return {"title": title, "options": [{"label": yes, "desc": yes_desc}, {"label": no, "desc": no_desc}]}


def pick(it: dict[str, Any]) -> str | None:
    v = it.get("verdict")
    if v in ("no", "answer"):
        return REDO
    if v != "ok":
        return None
    picked = str(it.get("choice") or "")
    for by_lang in LABELS.values():
        for k, (label, _) in by_lang.items():
            if picked == label:
                return k
    return None


def run_of(task: dict[str, Any]) -> tuple[str, str, str] | None:
    row = str(task.get("line_row") or "")
    if "/" not in row or task.get("line_mode") not in ("make", "fix") or not task.get("deliverable"):
        return None
    full, _, sub = row.partition("/")
    return full, sub, str(task["deliverable"])


def _stages(stage: str | None) -> set[int]:
    if not stage:
        return set()
    a, _, b = stage.partition("-")
    try:
        return set(range(int(a), int(b or a) + 1))
    except ValueError:
        return set()


def finishes_sub(task: dict[str, Any], sub: dict[str, Any]) -> bool:
    nums = [int(st["n"]) for st in sub["stages"] if str(st["n"]).isdigit()]
    mine = _stages(task.get("line_stage"))
    return not nums or not mine or max(nums) in mine


def _library(home: Path) -> dict[str, Any] | None:
    if not (home / LIB_DIR).is_dir():
        return None
    from . import processes
    return processes.load(home)


def run_required(lib: dict[str, Any], parsed: dict[str, Any], deliverable: str | None) -> str | None:
    f = lib["full"].get(parsed["full"]) if parsed["full"] and not parsed["full_new"] else None
    if not f or parsed["mode"] not in ("make", "fix") or f["status"] == "verified" or deliverable:
        return None
    return (f"`{parsed['full']}` is not verified yet, so each of its sub-processes is checked before the next starts: "
            f"name the run with `--deliverable KEY`, the same key on every task of this run "
            f"(e.g. --deliverable {parsed['full'].split('-')[0]}:<idea>)")


def needs_check(lib: dict[str, Any], task: dict[str, Any]) -> bool:
    r = run_of(task)
    if not r:
        return False
    f, s = lib["full"].get(r[0]), lib["sub"].get(r[1])
    if not f or not s or not finishes_sub(task, s):
        return False
    steps = [st["sub"] for st in f["steps"]]
    return f["status"] != "verified" or (bool(steps) and steps[-1] == r[1])


def latest(s: Store, full: str, run: str, sub: str) -> dict[str, Any] | None:
    got = s.process_checks(full=full, run=run, sub=sub)
    return got[-1] if got else None


def hold_of(s: Store, home: Path | None, task: dict[str, Any], lib: dict[str, Any] | None = None) -> dict[str, Any] | None:
    r = run_of(task)
    if not r or home is None:
        return None
    lib = lib if lib is not None else _library(home)
    f = lib["full"].get(r[0]) if lib else None
    if not f or f["status"] == "verified":
        return None
    steps = [st["sub"] for st in f["steps"]]
    if r[1] not in steps or steps.index(r[1]) == 0:
        return None
    prev = steps[steps.index(r[1]) - 1]
    last = latest(s, r[0], r[2], prev)
    who = task.get("requester")
    if last is None:
        return {"why": "missing", "sub": prev, "item_id": None, "member": who}
    if last["state"] == NEXT:
        return None
    return {"why": last["state"], "sub": prev, "item_id": last["item_id"], "member": last["member"] or who}


def held_text(h: dict[str, Any], lang: str = "en") -> str:
    t = TEXT[_lang(lang)]
    return t[h["why"]].format(m=h.get("member") or t["someone"], n=h.get("item_id") or "", sub=h.get("sub") or "")


def lane_line(s: Store, home: Path, lane: str, lang: str = "en",
              sees: Callable[[str | None], bool] | None = None) -> str | None:
    queued = [t for t in s.tasks(("queued",), limit=500) if base_lane(str(t["lane"])) == base_lane(lane) and run_of(t)]
    lib = _library(home) if queued else None
    if not lib:
        return None
    held = [h for t in queued if (h := hold_of(s, home, t, lib)) and (sees is None or sees(h["member"]))]
    if not held:
        return None
    more = TEXT[_lang(lang)]["more"].format(n=len(held) - 1) if len(held) > 1 else ""
    return held_text(held[0], lang) + more


def missing_check(s: Store, home: Path, task: dict[str, Any]) -> str | None:
    lib = _library(home)
    if not lib or not needs_check(lib, task) or s.process_checks(task_id=task["id"]):
        return None
    r = run_of(task) or ("", "", "")
    return (f"this task finishes `{r[1]}` of `{r[0]}`, which is not verified yet: post the check card first, with the "
            f"output attached: `dah check --title T --body-file F --attach PATH` (the requester answers Next or Redo "
            f"with fixes; the tower holds the next sub-process until then)")


def post(s: Store, home: Path, lang: str, lane: str, task: dict[str, Any], title: str, body: str,
         attachments: list[str]) -> tuple[int, bool]:
    r = run_of(task)
    if not r:
        raise CheckError("a check card belongs to a make or fix task of a library sub-process with a deliverable key "
                         "(the brief's `Line:` header and `dah task --deliverable`); this task has none")
    lib = _library(home)
    if not lib or not needs_check(lib, task):
        raise CheckError(f"`{r[1]}` of `{r[0]}` needs no check card here (the full process is verified and this is "
                         f"not its last sub-process, or this task does not end the sub-process)")
    for old in s.process_checks(task_id=task["id"]):
        try:
            if s.item(int(old["item_id"]))["state"] == "open":
                s.revise_item(int(old["item_id"]), title, body, None, attachments or None)
                return int(old["item_id"]), True
        except StoreError:
            continue
    member = s.requester_of_task(task["id"])
    iid = s.add_item(lane, task["id"], "decision", title, body, options(lang), attachments, audience=HUMAN,
                     assignee=member)
    s.add_process_check(iid, task["id"], lane, r[0], r[1], r[2], member)
    return iid, False


AFTER_HEADER = re.compile(r"^(?:model|effort|paths)\s*:", re.I)


def fix_brief(src: dict[str, Any], row: dict[str, Any], it: dict[str, Any], stage: str, lang: str) -> str:
    keep = [ln for ln in str(src.get("brief") or "").splitlines() if AFTER_HEADER.match(ln.strip())]
    note = str(it.get("note") or "").strip() or f"(no note: read the card with `dah show {it['id']}`)"
    lb = labels(lang)
    return "\n".join([
        f"Line: {row['full']} / {row['sub']} · stage {stage} · fix", *keep, "",
        f"Check card #{it['id']} came back \"{lb[REDO][0]}\" from {it.get('decided_by')}: {note}", "",
        f"Re-run `{row['sub']}` of run `{row['run']}` with this note, on the outputs already there. Then post a new check "
        f"card with the new output attached (`dah check --title ... --body-file ... --attach ...`) and `dah report done`. "
        f"The tower holds the next sub-process until that card says \"{lb[NEXT][0]}\".", "",
        f"SOURCE TASK #{src['id']} BRIEF (context, do not replay):", str(src.get("brief") or "")])


class Checks:
    def __init__(self, cfg: Any, store: Store, log: Callable[[str], None] = print):
        self.cfg, self.store, self.log = cfg, store, log

    def route(self, it: dict[str, Any]) -> dict[str, Any] | None:
        found = self.store.process_checks(item_id=int(it["id"]))
        if not found:
            return None
        plan, _ = self.plan(found[0], it, pick(it))
        self.log(f"check card #{it['id']}: {plan['check'][1]}")
        return plan

    def plan(self, row: dict[str, Any], it: dict[str, Any], what: str | None) -> tuple[dict[str, Any], str]:
        try:
            src = self.store.task(row["task_id"])
        except StoreError:
            src = {"id": row["task_id"], "brief": "", "hub": None, "lane": row["lane"]}
        hub = src.get("hub") or HUB
        head = (f"Check card #{it['id']} ({row['full']} / {row['sub']}, run {row['run']}): "
                f"{it.get('choice') or it['verdict']} by {it.get('decided_by')}"
                + (f", note: {it['note']}" if it.get("note") else ""))
        if what == REDO:
            lane = src.get("lane") or row["lane"]
            tail = (f"fix task #{{tid}} for {lane} (queued with the note; the next sub-process waits for its new "
                    f"check card)")
            return ({"follow": self._fix(src, row, it, lambda tid: f"{head} → {tail.format(tid=tid)}"),
                     "check": (row["id"], REDO)}, tail)
        if what == NEXT:
            tail = self._after_next(row)
            state = NEXT
        else:
            state = HOLD
            tail = (f"no option picked: the next sub-process of this run keeps waiting. Judge the note (ask the person "
                    f"if it is unclear), then `dah process check {it['id']} next` or `dah process check {it['id']} redo`")
        return {"push": (hub, f"{head} → {tail}"), "check": (row["id"], state)}, tail

    def settle(self, item_id: int, what: str, by: str) -> str:
        found = self.store.process_checks(item_id=item_id)
        if not found:
            raise CheckError(f"#{item_id} is not a check card")
        if found[0]["state"] != HOLD:
            raise CheckError(f"check card #{item_id} is {found[0]['state']}, not waiting for the hub")
        it = {**self.store.item(item_id), "decided_by": by}
        plan, tail = self.plan(found[0], it, what)
        if not self.store.settle_process_check(found[0]["id"], plan["check"][1], plan.get("push"), plan.get("follow"), item_id):
            raise CheckError(f"check card #{item_id} was settled meanwhile")
        fix = self.store.process_checks(item_id=item_id)[0]["fix_task"]
        return tail.format(tid=fix) if fix else tail

    def _after_next(self, row: dict[str, Any]) -> str:
        lib = _library(self.cfg.home) or {"full": {}}
        f = lib["full"].get(row["full"])
        steps = [st["sub"] for st in f["steps"]] if f else []
        if steps and steps[-1] == row["sub"]:
            if f and f["status"] == "verified":
                return "the final result is approved."
            ask = verify_ask(self.cfg.lang)
            yes, no = (o["label"] for o in ask["options"])
            return (f"every sub-process of run {row['run']} has passed. Post the verify card to "
                    f"{row['member'] or 'the requester'} now: \"{ask['title']}\" (options {yes} / {no}), "
                    f"and on \"{yes}\" run `dah process verify {row['full']} --card <that card>` and commit "
                    f"{VERIFIED_FILE}.")
        nxt = steps[steps.index(row["sub"]) + 1] if row["sub"] in steps else "?"
        return (f"the next sub-process `{nxt}` of run {row['run']} may start: a queued task for it starts on the next "
                f"tick; queue it now if there is none (same --deliverable {row['run']}).")

    def _fix(self, src: dict[str, Any], row: dict[str, Any], it: dict[str, Any],
             text: Callable[[int], str]) -> dict[str, Any]:
        from .engine import clip
        lib = _library(self.cfg.home) or {"sub": {}}
        sub = lib["sub"].get(row["sub"])
        nums = sorted(int(st["n"]) for st in (sub or {}).get("stages", []) if str(st["n"]).isdigit())
        stage = src.get("line_stage") or (f"{nums[0]}-{nums[-1]}" if len(nums) > 1 else str(nums[0]) if nums else "1")
        return {"lane": base_lane(str(src.get("lane") or row["lane"])), "title": clip(f"fix #{it['id']} {row['sub']}", 80),
                "brief": fix_brief(src, row, it, stage, self.cfg.lang), "engine": src.get("engine"),
                "task_id": row["task_id"], "requester": src.get("requester") or row["member"], "hub": src.get("hub"),
                "fields": {"deliverable": row["run"], "line_row": f"{row['full']}/{row['sub']}", "line_stage": stage,
                           "line_mode": "fix"},
                "text": text}


def verify(s: Store, home: Path, full: str, card: int, by: str | None = None) -> dict[str, Any]:
    lib = _library(home)
    f = lib["full"].get(full) if lib else None
    if not lib or not f:
        raise CheckError(f"full process `{full}` is not in the library ({', '.join(lib['full']) if lib else 'none'})")
    if f["status"] not in ("confirmed", "verified"):
        raise CheckError(f"`{full}` is {f['status']}: every sub-process card and pattern card must be confirmed first")
    try:
        it = s.item(card)
    except StoreError:
        raise CheckError(f"card #{card} does not exist")
    if it["state"] == "open" or it.get("verdict") != "ok":
        raise CheckError(f"card #{card} is not approved (state {it['state']}, verdict {it.get('verdict') or '-'})")
    steps = [st["sub"] for st in f["steps"]]
    rows = s.process_checks(full=full)
    passed = None
    for run in dict.fromkeys(r["run"] for r in reversed(rows)):
        if all((x := latest(s, full, run, sub)) and x["state"] == NEXT for sub in steps):
            passed = run
            break
    if passed is None:
        raise CheckError(f"no run of `{full}` passed the check of every sub-process ({', '.join(steps)}) yet "
                         f"(it needs `process_verification` on in tower.json)")
    rec = {"card": card, "by": by or it.get("decided_by"), "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(s.clock())),
           "run": passed, "commit": procverify.git_head(home), **procverify.fingerprint(home, f, lib["sub"])}
    procverify.write(home, full, rec)
    return rec


def run_view(s: Store, full: str, steps: list[str]) -> dict[str, Any] | None:
    every = s.run_tasks(full)
    if not every:
        return None
    key = every[-1]["deliverable"]
    tasks = [t for t in every if t["deliverable"] == key]
    checks = s.process_checks(full=full, run=key)
    live = [t for t in tasks if t["state"] in ("running", "wrapping")] or [t for t in tasks if t["state"] == "queued"]
    cur = live[0] if live else tasks[-1]
    sub = str(cur["line_row"]).partition("/")[2]
    last = checks[-1] if checks else None
    return {"key": key, "sub": sub, "n": steps.index(sub) + 1 if sub in steps else None,
            "who": tasks[-1].get("requester"),
            "check": ({k: last[k] for k in ("item_id", "state", "member", "sub")} if last else None),
            "held": bool(live) and cur["state"] == "queued"}
