from __future__ import annotations

import re
import time
import types
from pathlib import Path
from typing import Any, Callable

from . import accounthold, engines as engines_mod, leftovers, line as line_mod, overlap, procs, processes, rulewatch, schedules, stagemodel
from .config import HUB, INFRA, Config, base_lane, instance_lane, is_infra
from .ext import Extensions
from .runtimes import COMPACT_PENDING, COMPACT_TYPED
from .store import HUMAN, MULTI_SEP, TOWER, Store, StoreError, is_hub_name

BATCH_SECONDS = 8
SETTLE_SECONDS = 3
NUDGE_AFTER = 120
HUB_RETRY_WINDOW = 600
NUDGE_LIMIT = 2
RESPAWN_LIMIT = 3
HUB_ROTATE_K = 300
HUB_HANDOFF_WAIT = 900
HUB_STUCK_SECONDS = 900
LEFTOVER_SECONDS = 300
USAGE_SECONDS = 60
CHAIN_CONTEXT_K = 250
STUCK_TITLE = {"en": "The hub does not go idle", "ko": "허브가 idle로 돌아오지 않음",
               "ja": "ハブがアイドルに戻らない", "zh": "中枢一直没有回到空闲", "es": "El hub no vuelve a quedar libre"}
STUCK_BODY = {
    "en": ("## What happened\n{n} piece(s) of news for the hub have waited {m} min: the tower only writes into the hub "
           "while it is idle, and it has stayed busy.\n\n## Likely causes\n- A hook or plugin in the hub's agent keeps "
           "the session working (auto-continue, todo loops).\n- One very long turn.\n\n## What to do\n- `dah last hub` "
           "and `.tower/state/logs/hub.err` show what it is doing.\n- Give the hub lane a lighter agent (`\"agent\"` in "
           "`.tower/tower.json`) or another runtime: a request to infra can do it.\n- Approve this item once you have seen it."),
    "ko": ("## 무슨 일\n허브에 보낼 소식 {n}건이 {m}분째 대기 중입니다. tower는 허브가 idle일 때만 소식을 넣는데, 허브가 계속 "
           "busy 상태입니다.\n\n## 흔한 원인\n- 허브 에이전트의 hook이나 플러그인이 세션을 계속 돌림(자동 계속, todo 루프).\n"
           "- 아주 긴 한 턴.\n\n## 할 일\n- `dah last hub`와 `.tower/state/logs/hub.err`로 무엇을 하는지 확인.\n"
           "- 허브 레인에 더 가벼운 에이전트(`.tower/tower.json`의 `\"agent\"`)나 다른 런타임 지정: 인프라에 요청하면 됩니다.\n"
           "- 확인했으면 이 항목을 승인하세요."),
    "ja": ("## 何が起きたか\nハブ宛ての知らせ {n} 件が {m} 分待っています。tower はハブがアイドルのときだけ知らせを書き込みますが、"
           "ハブがずっと作業中のままです。\n\n## よくある原因\n- ハブのエージェントの hook やプラグインがセッションを動かし続けている"
           "(自動継続、todo ループ)。\n- とても長い 1 ターン。\n\n## すること\n- `dah last hub` と `.tower/state/logs/hub.err` "
           "で何をしているか確認。\n- ハブのレーンに軽いエージェント(`.tower/tower.json` の `\"agent\"`)か別のランタイムを指定: "
           "infra への依頼で頼めます。\n- 確認したらこの項目を承認してください。"),
    "zh": ("## 发生了什么\n发给中枢的 {n} 条消息已等待 {m} 分钟。tower 只在中枢空闲时写入消息，而中枢一直在忙。\n\n"
           "## 常见原因\n- 中枢代理的 hook 或插件让会话一直运行(自动继续、todo 循环)。\n- 一个非常长的回合。\n\n## 怎么办\n"
           "- 用 `dah last hub` 和 `.tower/state/logs/hub.err` 查看它在做什么。\n- 给中枢通道换一个更轻的代理"
           "(`.tower/tower.json` 里的 `\"agent\"`)或别的运行时：向 infra 提请求即可。\n- 看过后请批准此项。"),
    "es": ("## Qué pasó\n{n} aviso(s) para el hub llevan {m} min esperando: la torre solo escribe al hub cuando está libre, "
           "y sigue ocupado.\n\n## Causas probables\n- Un hook o plugin del agente del hub mantiene la sesión trabajando "
           "(auto-continuar, bucles de tareas).\n- Un turno muy largo.\n\n## Qué hacer\n- `dah last hub` y "
           "`.tower/state/logs/hub.err` muestran qué hace.\n- Asigna al hub un agente más ligero (`\"agent\"` en "
           "`.tower/tower.json`) u otro runtime: basta una petición a infra.\n- Aprueba este elemento cuando lo hayas visto."),
}
OVERFLOW_TITLE = {"en": "People waiting for a hub of their own", "ko": "전용 허브를 기다리는 사람",
                  "ja": "専用ハブを待っている人", "zh": "正在等待专属中枢的人", "es": "Personas esperando un hub propio"}
OVERFLOW_BODY = {
    "en": ("## What happened\n{n} person(s) wait in line for a hub of their own: {names}. `person_hubs.max` in tower.json "
           "is {cap} and every place is taken. Their board shows \"all hubs are taken\" with their place in line; the "
           "default hub takes their requests meanwhile.\n\n## When a place opens\nWhen a member is removed "
           "(`dah member remove NAME`) the first one in line gets a hub of their own.\n\n## Why there is a cap\nEach hub "
           "is an always-on agent session that holds memory on this machine.\n\n## What to do\n- Fine as it is: approve "
           "this item.\n- Raise the cap or free a place: change `person_hubs.max` (check the machine's memory first)."),
    "ko": ("## 무슨 일\n전용 허브를 기다리는 사람이 {n}명 있습니다: {names}. tower.json의 `person_hubs.max`가 {cap}이고 "
           "자리가 다 찼습니다. 이분들 보드에는 '허브 자리가 가득 찼어요'와 대기 순서가 보이고, 그동안 요청은 기본 "
           "허브(hub)가 받습니다.\n\n## 자리가 나면\n멤버 한 명을 빼면(`dah member remove 이름`) 대기 1번 사람에게 "
           "허브가 열립니다.\n\n## 상한이 있는 이유\n허브 하나가 항상 켜진 에이전트 세션이라 이 컴퓨터의 메모리를 "
           "씁니다.\n\n## 할 일\n- 지금대로 괜찮으면 이 항목을 승인하세요.\n- 상한을 올리거나 자리를 비우려면 "
           "`person_hubs.max`를 바꾸세요(컴퓨터 메모리를 먼저 확인)."),
    "ja": ("## 何が起きたか\n専用ハブを待っている人が {n} 人います: {names}。tower.json の `person_hubs.max` は {cap} で、"
           "枠がすべて埋まっています。その人たちのボードには「ハブの枠がいっぱい」と順番が表示され、その間のリクエストは"
           "既定のハブが受けます。\n\n## 枠が空いたら\nメンバーを 1 人外すと(`dah member remove NAME`)、順番の先頭の人に"
           "専用ハブが開きます。\n\n## 上限がある理由\nハブは常時動くエージェントのセッションで、このマシンのメモリを"
           "使います。\n\n## すること\n- このままで良ければこの項目を承認。\n- 上限を上げる、または枠を空けるには "
           "`person_hubs.max` を変更(先にマシンのメモリを確認)。"),
    "zh": ("## 发生了什么\n有 {n} 个人在排队等待专属中枢：{names}。tower.json 中的 `person_hubs.max` 为 {cap}，名额已满。"
           "他们的看板会显示「中枢名额已满」和排队位置，期间他们的请求由默认中枢接收。\n\n## 名额空出时\n移除一名成员"
           "(`dah member remove NAME`)后，排在最前面的人会得到专属中枢。\n\n## 为什么有上限\n每个中枢都是常驻的代理会话，"
           "会占用这台机器的内存。\n\n## 怎么办\n- 保持现状：批准此项。\n- 提高上限或腾出名额：修改 "
           "`person_hubs.max`(先确认机器内存)。"),
    "es": ("## Qué pasó\n{n} persona(s) esperan un hub propio: {names}. `person_hubs.max` en tower.json es {cap} y todos los "
           "puestos están ocupados. Su tablero muestra \"todos los hubs están ocupados\" con su lugar en la fila; mientras "
           "tanto el hub por defecto recibe sus peticiones.\n\n## Cuando se libera un puesto\nAl quitar a un miembro "
           "(`dah member remove NOMBRE`) la primera persona de la fila recibe un hub propio.\n\n## Por qué hay un límite\n"
           "Cada hub es una sesión de agente siempre activa que ocupa memoria de esta máquina.\n\n## Qué hacer\n- Si está "
           "bien así: aprueba este elemento.\n- Para subir el límite o liberar un puesto: cambia `person_hubs.max` "
           "(revisa antes la memoria de la máquina)."),
}
FOLLOW_VERDICTS = ("ok", "no", "answer")
VERDICT_WORD = {"ok": "approved", "no": "rejected", "hold": "on hold", "answer": "answered in own words",
                "merged": "merged"}

RULES = ("(Tower task. Need a decision or an approval? Post it with `dah item add` and do not wait for it: finish "
         "the unblocked, approved scope and `dah report done \"summary · evidence paths · open item ids\"`. "
         "Decisions arrive later as separate follow-up tasks in fresh sessions. Never ask questions in chat.)")
ASK_BOUNCE = ("Do not ask in chat. Post a board item with `dah item add` if a human must decide, then carry on "
              "with other work or end your turn.")
ASK_GONE = "the question disappeared (answered in the terminal, or the session ended)"
ASK_HOW = "## How to answer\n- Your pick and note go straight back to the session.\n- No option fits? Write your own answer."
NUDGE = ("You stopped without a report. Finished: `dah report done \"summary · evidence\"`. Need a decision: "
         "`dah item add`. Stuck: `dah report blocked \"why\"`. Work left: keep going.")
WRAP_RULE = ("(This session carries exactly one item. Handle it, answer it (`dah memo reply ID \"...\"` for a memo), "
             "then `dah report wrapped \"one line\"`. The tower closes the session; anything else arrives later "
             "in a fresh one.)")


MODE_LINE = re.compile(r"\[mode\]\s*(compact|fresh)", re.I)
CONTEXT_ROTATE = ("Your session's context is {ctx}k tokens, over the limit for this lane ({limit}k). Close out now "
                  "(\"Lane close-out\" in the protocol), then hand off: `dah report handoff \"[mode] fresh "
                  "[now] ... [plan] ... [files] ... [items] ...\"` and end your turn. The tower continues the task in a "
                  "fresh session. Write `[mode] compact` instead of `[mode] fresh` only when the next step continues this "
                  "exact work closely (same files, mid-edit): the session is then compacted and keeps going.")
HUB_ROTATE_ASK = ("[tower] Your hub session is {why}, over the hub limit, and nothing new is waiting: hand over to a "
                  "fresh hub session now. `dah report handoff \"[now] ... [open] requests #.. · items #.. · tasks #..\"` "
                  "with what is still open: request numbers waiting for an answer, board items waiting for a decision, "
                  "tasks you follow, and what you were about to do. Then end your turn. The tower opens a new hub "
                  "session with your handoff and `dah status`.")
COMPACT_TYPED_GAP_SECONDS = 2.0
COMPACT_GIVE_UP_SECONDS = 600
SYNC_RETRY_SECONDS = 60
COMPACT_FOCUS = ("Keep the task brief, the latest handoff, the files changed, the open board items and the next step; "
                 "drop tool output already acted on.")
PATHS_LINE = re.compile(r"^\s*paths\s*:\s*(.+)$", re.I | re.M)
AFTER_LINE = re.compile(r"^\s*after\s*:\s*#?(\d+)", re.I | re.M)


def handoff_mode(text: str | None) -> str:
    m = MODE_LINE.search(text or "")
    return m.group(1).lower() if m else "fresh"


def task_paths(task: dict[str, Any]) -> list[str] | None:
    m = PATHS_LINE.search(f"paths: {task['paths']}" if task.get("paths") else str(task.get("brief") or ""))
    if not m:
        return None
    out = [p.strip().strip("`").strip().strip("/") for p in re.split(r"[,\n]", m.group(1))]
    return [p for p in out if p] or None


def paths_overlap(a: list[str], b: list[str]) -> bool:
    return any(x == y or x.startswith(y + "/") or y.startswith(x + "/") for x in a for y in b)


def after_of(task: dict[str, Any]) -> int | None:
    m = AFTER_LINE.search(str(task.get("brief") or ""))
    return int(m.group(1)) if m else None


ALONE = "*"
NOTHING = "-"
DECLARE_GRACE = 180
MEMO_REF = re.compile(r"memo\s*#(\d+)", re.I)


def conflict_of(store: Store, task: dict[str, Any], also: list[dict[str, Any]] = []) -> str | None:
    dep_id = after_of(task)
    if dep_id is not None:
        try:
            dep = store.task(dep_id)
        except StoreError:
            dep = None
        if dep and dep["state"] in ("queued", "running", "wrapping"):
            return f"waiting for #{dep['id']}: after"
    hold = overlap.hold_of(store, f"task:{task['id']}")
    if hold:
        return overlap.held_text(hold)
    signin = accounthold.hold_of(store, f"task:{task['id']}")
    if signin:
        return accounthold.held_text(signin["member"])
    b = task_block(store, task, also)
    if b is None:
        return None
    return (f"blocked beside #{b[1]['id']}: overlap" if b[0] == "overlap"
            else f"waiting for #{b[1]['id']} to declare its paths")


def task_block(store: Store, task: dict[str, Any], also: list[dict[str, Any]] = []) -> tuple[str, dict[str, Any]] | None:
    base = base_lane(str(task["lane"]))
    others = [t for t in [*store.tasks(("running", "wrapping"), limit=500), *also]
              if t["id"] != task["id"] and base_lane(str(t["lane"])) == base]
    mine, now = task_paths(task), store.clock()
    for o in others:
        theirs = task_paths(o)
        if theirs is None:
            if o.get("started") is None or now - float(o["started"]) < DECLARE_GRACE:
                return "declaring", o
            continue
        if mine and paths_overlap(mine, theirs):
            return "overlap", o
    return None


def team_turns(store: Store, team: set[str], fair: bool = True) -> dict[int, tuple[int, int, float]]:
    from .fair import task_who, turns
    queued = [t for t in sorted(store.tasks(("queued",), limit=500),
                                key=lambda t: (t["rank"] if t.get("rank") is not None else t["id"], t["id"]))
              if t["lane"] in team]
    if not fair:
        return {t["id"]: (0, 0, i) if t.get("rank") is not None else (1, 0, i) for i, t in enumerate(queued)}
    running = [t for t in store.tasks(("running", "wrapping"), limit=500) if t["lane"] in team]

    def first(t: dict[str, Any]) -> bool:
        if t.get("rank") is not None:
            return True
        dep = after_of(t)
        if dep is None:
            return False
        try:
            return store.task(dep)["state"] == "closed"
        except StoreError:
            return False
    return turns(queued, task_who, running, first)


def message_paths(msg: dict[str, Any]) -> list[str] | None:
    col = str(msg.get("paths") or "").strip()
    if col == NOTHING:
        return []
    if col == ALONE:
        return [ALONE]
    if col:
        return task_paths({"brief": f"paths: {col}"})
    return task_paths({"brief": msg.get("text")})


def paths_label(msg: dict[str, Any]) -> str:
    ps = message_paths(msg)
    if ps is None:
        return "not declared (the session declares them)"
    if ps == [ALONE]:
        return "alone"
    return ", ".join(ps) if ps else "nothing (check only)"


def message_block(msg: dict[str, Any], live: list[dict[str, Any]], now: float | None = None) -> tuple[str, dict[str, Any]] | None:
    mine = message_paths(msg)
    for o in live:
        if mine == [ALONE]:
            return "busy", o
        theirs = message_paths(o)
        if theirs is None:
            opened = o.get("delivered")
            if now is None or opened is None or now - float(opened) < DECLARE_GRACE:
                return "declaring", o
            continue
        if theirs == [ALONE]:
            return "alone", o
        if mine and theirs and paths_overlap(mine, theirs):
            return "overlap", o
    return None


BLOCK_TEXT = {
    "en": {"declaring": "waits for #{o} ({lane}) to declare its paths", "alone": "#{o} ({lane}) runs alone",
           "busy": "runs alone: waits until #{o} ({lane}) and every other infra session finish",
           "overlap": "paths overlap #{o} ({lane}): waits until it finishes", "full": "all {n} infra sessions are busy",
           "behind": "waits behind #{o}, which runs alone",
           "overlap_hold": "overlaps another person's work: waits for the answer on card #{o}",
           "signin_hold": "waiting for {o} to sign in to Claude again"},
    "ko": {"declaring": "#{o}({lane})가 고칠 경로를 선언하는 중이라 대기", "alone": "#{o}({lane})가 혼자 실행 중이라 대기",
           "busy": "혼자 실행할 메모라 #{o}({lane}) 등 다른 인프라 세션이 끝날 때까지 대기",
           "overlap": "#{o}({lane})와 고칠 경로가 겹쳐 그것이 끝날 때까지 대기", "full": "인프라 세션 {n}개가 모두 사용 중",
           "behind": "혼자 실행할 #{o} 차례를 기다리는 중",
           "overlap_hold": "다른 사람 일과 겹쳐 카드 #{o} 답을 기다리는 중",
           "signin_hold": "{o} 님이 Claude에 다시 로그인하기를 기다리는 중"},
    "ja": {"declaring": "#{o}（{lane}）が変更するパスを宣言するのを待っています", "alone": "#{o}（{lane}）が単独で実行中のため待機",
           "busy": "単独実行のメモなので、#{o}（{lane}）ほかの infra セッションが終わるまで待機",
           "overlap": "#{o}（{lane}）と変更するパスが重なるため、それが終わるまで待機", "full": "infra セッション {n} 件がすべて使用中",
           "behind": "単独実行の #{o} の順番を待っています",
           "overlap_hold": "他の人の作業と重なるため、カード #{o} の返答を待機",
           "signin_hold": "{o} さんが Claude に再ログインするのを待っています"},
    "zh": {"declaring": "等待 #{o}（{lane}）声明它要修改的路径", "alone": "#{o}（{lane}）正在独占运行，等待",
           "busy": "此备忘需独占运行：等到 #{o}（{lane}）和其他 infra 会话都结束", "overlap": "与 #{o}（{lane}）要修改的路径重叠，等它结束",
           "full": "{n} 个 infra 会话全部在用", "behind": "排在需独占运行的 #{o} 之后",
           "overlap_hold": "与他人的工作重叠，等待卡片 #{o} 的答复",
           "signin_hold": "等待 {o} 重新登录 Claude"},
    "es": {"declaring": "espera a que #{o} ({lane}) declare sus rutas", "alone": "#{o} ({lane}) se ejecuta a solas",
           "busy": "se ejecuta a solas: espera a que #{o} ({lane}) y las demás sesiones de infra terminen",
           "overlap": "las rutas se solapan con #{o} ({lane}): espera a que termine", "full": "las {n} sesiones de infra están ocupadas",
           "behind": "espera detrás de #{o}, que se ejecuta a solas",
           "overlap_hold": "se solapa con el trabajo de otra persona: espera la respuesta en la tarjeta #{o}",
           "signin_hold": "esperando a que {o} inicie sesión de nuevo en Claude"},
}


def block_text(why: str, other: dict[str, Any] | None, lang: str = "en", n: int = 0) -> str:
    t = BLOCK_TEXT.get(lang, BLOCK_TEXT["en"])[why]
    return t.format(o=other["id"] if other else "", lane=(other or {}).get("_lane") or "infra", n=n)


def message_conflict(msg: dict[str, Any], live: list[dict[str, Any]], now: float | None = None) -> str | None:
    b = message_block(msg, live, now)
    return f"beside #{b[1]['id']}: {block_text(b[0], b[1])}" if b else None


def infra_waits(store: Store, sessions: list[str], now: float, fair: bool = False,
                limit: int = 20) -> list[tuple[dict[str, Any], tuple[str, dict[str, Any] | None] | None]]:
    held = store.held_messages(sessions)
    live = [{**m, "_lane": k} for k, m in held.items()]
    free = len(sessions) - len(held)
    out: list[tuple[dict[str, Any], tuple[str, dict[str, Any] | None] | None]] = []
    barrier = None
    for m in store.inbox_queue(INFRA, fair)[:limit]:
        hold = overlap.hold_of(store, f"memo:{m['id']}")
        if hold:
            out.append((m, ("overlap_hold", {"id": hold["items"][0], "_lane": "card"})))
            continue
        signin = accounthold.hold_of(store, f"memo:{m['id']}")
        if signin:
            out.append((m, ("signin_hold", {"id": signin["member"], "_lane": "account"})))
            continue
        b = message_block(m, live, now)
        if b is None and barrier is not None:
            b = ("behind", barrier)
        elif b is None and free <= 0:
            b = ("full", None)
        if b is None:
            free -= 1
            live.append({**m, "_lane": "next", "delivered": None})
        elif message_paths(m) == [ALONE] and barrier is None:
            barrier = m
        out.append((m, b))
    return out


def linked(prev: dict[str, Any], nxt: dict[str, Any]) -> bool:
    return after_of(nxt) == prev["id"]


def clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def decision_line(it: dict[str, Any]) -> str:
    parts = [f"Decision #{it['id']} \"{it['title']}\": {VERDICT_WORD.get(str(it['verdict']), it['verdict'])}"]
    if it.get("choice"):
        parts.append(f"choice: {it['choice']}")
    if it.get("note"):
        parts.append(f"{'answer' if it['verdict'] == 'answer' else 'note'}: {it['note']}")
    parts.append(f"by {it['decided_by']}" + (f", basis: {it['basis']}" if it.get("basis") else ""))
    return " / ".join(parts)


def ask_answer(it: dict[str, Any]) -> list[str]:
    note = str(it.get("note") or "").strip()
    memo = [f"note: {note}"] if note else []
    if it["verdict"] == "ok" and it.get("choice"):
        return str(it["choice"]).split(MULTI_SEP) + memo
    if it["verdict"] in ("no", "answer"):
        return [f"Own answer instead of an option: {note}"] if note else ["None of the options works. Ask again differently."]
    return [f"On hold: {note}" if note else "On hold: no answer for now, move on."]


SNAPSHOT_EVERY = 3600


class Engine:
    def __init__(self, cfg: Config, store: Store, rt: Any, notify: Callable[[list[dict[str, Any]]], None],
                 clock: Callable[[], float] = time.time, log: Callable[[str], None] = print,
                 ext: Extensions | None = None):
        self.cfg, self.store, self.rt, self.push, self.clock, self.log = cfg, store, rt, notify, clock, log
        self.ext = ext or Extensions(cfg.home, log)
        self.marks: dict[str, float] | None = None
        self.ext_lanes: dict[str, str] | None = None
        self._team = cfg.team()
        self.heavy: dict[str, int] = {}
        self.rotate_asked: set[tuple[int, str]] = set()
        self.ctx_asked: set[tuple[int, str]] = set()
        self.rotate_why: dict[int, str] = {}
        self.compacting: dict[int, tuple[str, str, float]] = {}
        self.hub_asked: dict[str, tuple[str, float]] = {}
        store.sync_lane_engines({lid: cfg.all_lanes[lid].engine for lid in self.team})
        self.has_infra = INFRA in cfg.lanes
        self.infra_keys = cfg.infra_sessions()
        self.max_lanes = cfg.max_lanes
        self.idle_since: dict[str, float] = {}
        self.hub_opens: dict[str, list[float]] = {}
        self.hub_backoff: set[str] = set()
        self.overflow_seen: tuple[str, ...] = ()
        self.waiting: set[str] = set()
        self.model_told: set[int] = set()
        self.stuck_for: dict[str, int] = {}
        self.last_status: dict[str, str] = {}
        self.usage_at: dict[str, float] = {}
        self.snap_at: float = -SNAPSHOT_EVERY
        self.api_watch: Any = None
        self.sweep: Any = None
        self.waits: dict[int, str] | None = None
        self.proc_scan: Callable[[Path], list[Any]] = leftovers.scan
        self.proc_stop: Callable[[int], None] = lambda pid: procs.terminate(pid, group=False)
        self.leftover_at: float | None = None
        self.leftover_seen: set[int] = set()
        self.sync_told: dict[int, str] = {}
        self.catch_up_at: float | None = None
        self.catch_up_told: dict[str, str] = {}
        self.sync_retry: dict[str, float] = {}
        self.overlaps = overlap.Overlaps(cfg, store, log) if cfg.overlap_check else None
        self.rules = rulewatch.RuleWatch(cfg.home, store, cfg.state_dir, cfg.rule_watch, cfg.lang, clock, log,
                                         lambda k: self.person_of(k)) if cfg.rule_watch else None
        self.accounts = accounthold.Gate(cfg, store, log)

    def inherit(self, old: Engine) -> None:
        self.compacting, self.hub_asked = old.compacting, old.hub_asked
        self.hub_opens, self.hub_backoff, self.stuck_for = old.hub_opens, old.hub_backoff, old.stuck_for
        self.sync_told, self.catch_up_told = old.sync_told, old.catch_up_told
        self.rotate_asked, self.ctx_asked, self.rotate_why = old.rotate_asked, old.ctx_asked, old.rotate_why
        self.marks, self.ext_lanes, self.waiting, self.model_told = old.marks, old.ext_lanes, old.waiting, old.model_told


    def _read_first(self, lane: str) -> str:
        protocol = self.cfg.home / ".tower" / "PROTOCOL.md"
        return (f"First read `{self.cfg.instructions(lane)}` and `{protocol}` and follow them. "
                f"Humans read the board in `{self.cfg.lang}`; write everything they read in that language.")

    def _opts(self, task: dict[str, Any]) -> dict[str, str]:
        model, effort = stagemodel.resolve(stagemodel.table_for(self.cfg, task["lane"]), task["brief"], stagemodel.library_for(self.cfg))
        if (model, effort) != (task.get("model"), task.get("effort")):
            self.store.update_task(task["id"], model=model, effort=effort, **engines_mod.line_fields(task["brief"], processes.lane_full(self.cfg, task["lane"])))
        if (model, effort) == (None, None) and task["id"] not in self.model_told and self._names_a_step(task["brief"]):
            self._model_unresolved(task)
        return {k: v for k, v in (("model", model), ("effort", effort)) if v}

    @staticmethod
    def _names_a_step(brief: str) -> bool:
        if not line_mod.is_library_line(brief):
            return False
        try:
            p = line_mod.parse(brief)
        except ValueError:
            return False
        return bool(p["sub"]) and not (p["chore"] or p["full_new"] or p["sub_new"])

    def _model_unresolved(self, task: dict[str, Any]) -> None:
        self.model_told.add(task["id"])
        text = (f"Task #{task['id']} ({task['lane']}): its Line header names a step the process library no longer has "
                f"(renamed or removed), so no model or effort could be read from it and the lane's own default runs. "
                f"Fix the header or add `model:` and `effort:` lines.")
        self.log(f"task #{task['id']}: {text}")
        self.store.push(self.store.hub_for_task(task["id"]), TOWER, "stalled", text)

    def _lock_line(self, task: dict[str, Any]) -> str:
        if self.cfg.instance_cap(base_lane(str(task["lane"]))) < 2:
            return ""
        hint = task_paths(task)
        return (f"\n\nPaths (a file lock): other sessions of this lane may work beside you. This task's paths now: "
                f"{', '.join(hint) if hint else 'none yet'}. Before you change any file, declare the files and folders "
                f"you will change: `dah paths A B ...` (the brief's `paths:` is the hub's hint). If they overlap "
                f"another session's, the tower puts the task back in the queue and closes this session: change nothing "
                f"and end your turn; it restarts once the other one finishes.")

    def start_prompt(self, task: dict[str, Any]) -> str:
        return (f"[tower] {task['lane']} lane · task #{task['id']} \"{task['title']}\"\n\n{self._read_first(task['lane'])}"
                f"\n\n{task['brief']}{self._lock_line(task)}\n\n{RULES}")

    def resume_prompt(self, task: dict[str, Any], why: str) -> str:
        done = "\n".join(f"- {r['kind']}: {clip(r['text'], 300)}" for r in self.store.reports(task["id"])[-6:]) or "- none"
        hand = f"\n\nHandoff:\n{task['handoff']}" if task.get("handoff") else ""
        return (f"[tower] {task['lane']} lane · task #{task['id']} \"{task['title']}\" continued: {why}\n\n"
                f"{self._read_first(task['lane'])}\n\nOriginal brief:\n{task['brief']}\n\nReports so far:\n{done}{hand}"
                f"\n\nRun `dah mine` for this task's open board items, then continue. {RULES}")

    def compact_prompt(self, task: dict[str, Any]) -> str:
        return (f"[tower] {task['lane']} lane · task #{task['id']} \"{task['title']}\": your context was compacted; this "
                f"session goes on with the task. Your handoff:\n{task.get('handoff') or '(none)'}\n\nRun `dah mine` "
                f"for this task's open board items, then continue. {RULES}")

    def hub_prompt(self, handoff: str = "", lane: str = HUB) -> str:
        hand = (f"\n\nThe last hub session handed over to you (its context grew too large). Its handoff:\n{handoff}"
                if handoff else "")
        owner = self.cfg.all_lanes[lane].owner if lane in self.cfg.all_lanes else ""
        if owner:
            who = (f"You are the hub lane `{lane}`, the hub of {owner}: you get {owner}'s board requests and the news "
                   f"about tasks you queue. Other people have hubs of their own; the team lanes and the task queue are "
                   f"shared, so check `dah status` before you queue work that may overlap.")
        elif self.cfg.person_hubs:
            who = ("You are the hub lane (the default hub: requests from people without a hub of their own, and news "
                   "about tasks no person hub queued).")
        else:
            who = "You are the hub lane."
        return f"[tower] {who}\n\n{self._read_first(lane)}{hand}\n\nThen run `dah status` and handle what is open."

    @staticmethod
    def _memo_opts(msg: dict[str, Any]) -> dict[str, str]:
        if msg.get("kind") != "memo":
            return {}
        model, effort = stagemodel.resolve(None, str(msg.get("text") or ""))
        return {k: v for k, v in (("model", model), ("effort", effort)) if v}

    def inbox_prompt(self, msg: dict[str, Any], lane: str = INFRA) -> str:
        body = (f"Memo #{msg['id']} from {msg['sender']}:\n{msg['text']}" if msg["kind"] == "memo" else msg["text"])
        who = "You are the infra lane." if lane == INFRA else (
            f"You are the infra lane, parallel session {lane}: other infra sessions work in the same checkout at the "
            f"same time, so stay inside this memo's paths and stage only your own files.")
        lock = self._memo_lock_line(msg) if len(self.infra_keys) > 1 else ""
        return f"[tower] {who}\n\n{self._read_first(INFRA)}\n\n{body}{lock}\n\n{WRAP_RULE}"

    @staticmethod
    def _memo_lock_line(msg: dict[str, Any]) -> str:
        hint = message_paths(msg)
        has = ("none yet" if hint is None else "alone" if hint == [ALONE] else
               ", ".join(hint) if hint else "nothing (check only)")
        return (f"\n\nPaths (a file lock): this message's paths now: {has}. Before you change any file, declare the "
                f"files and folders you will change: `dah memo paths {msg['id']} A B ...` (what the hub wrote is a "
                f"hint), or `dah memo paths {msg['id']} --alone` for work that must run with no other infra session "
                f"(a reinstall, a restart). If they overlap another session's, the tower gives the message back to the "
                f"queue and closes this session: end your turn then; it restarts once the other one finishes.")


    def notify(self, notes: list[dict[str, Any]]) -> None:
        self.push(notes)
        hooks = self.ext.hooks("on_notify")
        if hooks and notes:
            ctx = {"cfg": self.cfg, "store": self.store, "log": self.log, "now": self.clock()}
            for fn in hooks:
                self.ext.call(fn, ctx, [dict(n) for n in notes])

    def _idle_for(self, lane: str, st: str) -> float:
        now = self.clock()
        if st != "idle":
            self.idle_since.pop(lane, None)
            return 0.0
        return now - self.idle_since.setdefault(lane, now)

    @staticmethod
    def _account_kw(la: Any) -> dict[str, Any]:
        return {"account": la.config_dir} if la is not None and la.config_dir else {}

    def _send(self, lane: str, sid: str, text: str, queued: bool = False) -> None:
        (self.rt.send_queued if queued else self.rt.send)(lane, sid, text)
        self.idle_since[lane] = self.clock()

    def summary(self) -> str:
        h = len(self.store.items(state="open", audience=HUB))
        m = len(self.store.items(state="open", audience=HUMAN))
        run = len(self.store.tasks(("running", "wrapping")))
        q = len(self.store.tasks(("queued",)))
        return f"(now: {h} items for the hub · {m} for humans · {run} tasks running · {q} queued)"


    @property
    def team(self) -> list[str]:
        return [*self._team, *(k for k in self.cfg.instances if base_lane(k) in self._team)]

    def person_of(self, lane: str) -> str | None:
        ln = self.cfg.person_hubs.get(lane)
        return ln.owner if ln and ln.owner else None

    def conflict(self, task: dict[str, Any], also: list[dict[str, Any]] = []) -> str | None:
        return conflict_of(self.store, task, also)

    def cap_reason(self, task: dict[str, Any]) -> str | None:
        lane = self.cfg.all_lanes.get(base_lane(str(task["lane"])))
        eng = self.cfg.engines.get(lane.engine) if lane else None
        if eng is None or not eng.cap:
            return None
        kind, _, arg = eng.cap.partition(":")
        if kind == "tokens-day":
            if not arg.strip().isdigit():
                return None
            used = sum(u["tokens_in"] + u["tokens_out"] for ln, u in self.store.usage(self.store.day()).items()
                       if ln in eng.lanes)
            return f"engine {eng.id} used {used} of {arg.strip()} tokens today (cap)" if used >= int(arg) else None
        fn = self.ext.table("caps").get(eng.cap)
        if not callable(fn):
            return None
        ctx = types.SimpleNamespace(cfg=self.cfg, store=self.store, log=self.log, now=self.clock(), engine=eng.id)
        got = self.ext.call(fn, ctx)
        return f"engine {eng.id} held by cap {eng.cap}: {got}" if got else None

    def weight(self, task: dict[str, Any]) -> str:
        paths = task_paths(task) or []
        roots = self.cfg.heavy_paths
        return "heavy" if any(p == r or p.startswith(r + "/") for p in paths for r in roots) else "light"

    def lanes(self) -> list[str]:
        return [*self.cfg.hubs(), *self.infra_keys, *self.team]

    def tick(self) -> None:
        statuses: dict[str, str] = {}
        for lane in self.lanes():
            try:
                statuses[lane] = self.rt.status(lane)
            except Exception as e:
                statuses[lane] = "unknown"
                self.log(f"{lane}: status failed: {e}")
            self.store.set_lane(lane, status=statuses[lane], seen=self.clock())
        for name, fn in (("usage", lambda: self._usage(statuses)), ("questions", lambda: self._questions(statuses)),
                         ("decisions", self.route_decisions), ("notify", self._notify_humans),
                         ("api errors", self._api_errors), ("done gate", self._done_gates),
                         ("scratch", self._scratch), ("schedules", self._schedules)):
            try:
                fn()
            except Exception as e:
                self.log(f"{name}: {e}")
        for lane in self.team:
            try:
                self._lane(lane, statuses[lane])
            except Exception as e:
                self.log(f"{lane}: {e}")
        try:
            if self.overlaps:
                self.overlaps.scan()
        except Exception as e:
            self.log(f"overlap: {e}")
        try:
            self.accounts.poll()
        except Exception as e:
            self.log(f"accounts: {e}")
        self._start_queued(statuses)
        try:
            self._person_hubs()
        except Exception as e:
            self.log(f"person hubs: {e}")
        for name in self.cfg.hubs():
            try:
                self._hub(statuses[name], name)
            except Exception as e:
                self.log(f"{name}: {e}")
        if self.infra_keys:
            self._infra_all(statuses)
        try:
            self._leftovers()
        except Exception as e:
            self.log(f"leftover: {e}")
        try:
            self._catch_up_branches(statuses)
        except Exception as e:
            self.log(f"branch sync: {e}")
        try:
            if self.rules:
                self.rules.tick()
        except Exception as e:
            self.log(f"rules: {e}")
        self._extensions(statuses)


    def _weekly(self) -> None:
        now = self.clock()
        if now - self.snap_at < SNAPSHOT_EVERY:
            return
        self.snap_at = now
        try:
            engines_mod.weekly(self.cfg, now, self.log)
        except Exception as e:
            self.log(f"weekly snapshot: {e}")

    def _extensions(self, statuses: dict[str, str]) -> None:
        self._weekly()
        on_event, on_tick = self.ext.hooks("on_event"), self.ext.hooks("on_tick")
        if not on_event:
            self.marks = self.ext_lanes = None
        if not (on_event or on_tick):
            return
        ctx = {"cfg": self.cfg, "store": self.store, "log": self.log, "now": self.clock()}
        if on_event:
            events, self.marks = self.store.changes(self.marks)
            seen, self.ext_lanes = self.ext_lanes, dict(statuses)
            for lane, st in statuses.items():
                if seen is not None and seen.get(lane) != st:
                    ln = self.cfg.all_lanes[lane]
                    events.append(("lane", {"lane": lane, "status": st, "was": seen.get(lane),
                                            "runtime": ln.runtime, "terminal": ln.terminal}))
            for kind, data in events:
                for fn in on_event:
                    self.ext.call(fn, ctx, kind, data)
        for fn in on_tick:
            self.ext.call(fn, ctx)


    def _usage(self, statuses: dict[str, str]) -> None:
        now = self.clock()
        for lane, st in statuses.items():
            prev, self.last_status[lane] = self.last_status.get(lane), st
            sid = self.store.lane(lane).get("session")
            if not sid or st not in ("idle", "busy", "asking"):
                continue
            if not (st == "idle" and prev != "idle") and now - self.usage_at.get(lane, 0.0) < USAGE_SECONDS:
                continue
            self.usage_at[lane] = now
            try:
                u = self.rt.usage(lane, sid)
            except Exception as e:
                self.log(f"{lane}: usage failed: {e}")
                continue
            if u:
                self.store.record_usage(lane, sid, self.cfg.all_lanes[lane].runtime, int(u.get("tokens_in") or 0),
                                        int(u.get("tokens_out") or 0), u.get("cost"), u.get("ctx_k"))
            self._ledger(lane, sid)

    def _ledger(self, lane: str, sid: str) -> None:
        task = self.store.task_for_session(lane, sid)
        if task is None:
            return
        try:
            got = self.rt.ledger(lane, sid)
        except Exception as e:
            self.log(f"{lane}: ledger failed: {e}")
            return
        if got:
            self.store.record_task_usage(task["id"], sid, int(got.get("new") or 0), int(got.get("cache") or 0),
                                         int(got.get("out") or 0), got.get("turns"))


    def _questions(self, statuses: dict[str, str]) -> None:
        for lane, st in statuses.items():
            if self.cfg.all_lanes[lane].terminal == "none" or st != "asking":
                self.waiting.discard(lane)
            elif lane not in self.waiting:
                self.waiting.add(lane)
                self.notify([{"id": lane, "lane": lane, "alert": True,
                              "title": f"{lane} waits for an answer in its {self.cfg.all_lanes[lane].terminal} window"}])
                self.log(f"{lane}: waiting for an answer in its window")
            if is_infra(lane) or self.cfg.is_hub(lane):
                self._ask_board(lane, st)
            elif st == "asking":
                for q in self.rt.questions(lane) or []:
                    self.rt.reply_question(lane, q["id"], [[ASK_BOUNCE]] * max(1, len(q.get("questions", []))))
                    self.log(f"{lane}: bounced an ask to the board")

    def _ask_board(self, lane: str, st: str) -> None:
        if st == "unknown":
            return
        asks = self.rt.questions(lane) if st != "closed" else []
        if asks is None:
            return
        live = {str(q["id"]) for q in asks}
        items = self.store.question_items(lane)
        for it in items:
            if str(it["question"]).rpartition("#")[0] in live:
                continue
            if it["state"] == "open":
                self.store.decide(it["id"], "hold", TOWER, note=ASK_GONE)
            self.store.update_item(it["id"], delivered=self.clock())
        have = {str(it["question"]): it for it in items}
        for q in asks:
            subs: list[dict[str, Any]] = q.get("questions") or []
            keys = [f"{q['id']}#{i}" for i in range(len(subs))]
            for i, (key, sub) in enumerate(zip(keys, subs)):
                if key not in have:
                    have[key] = self.store.item(self._ask_item(lane, key, sub, i, len(subs)))
                    self.log(f"{lane}: ask → board #{have[key]['id']}")
            if not keys or any(have[k]["state"] != "decided" for k in keys):
                continue
            self.rt.reply_question(lane, q["id"], [ask_answer(have[k]) for k in keys])
            for k in keys:
                self.store.update_item(have[k]["id"], delivered=self.clock())
            self.log(f"{lane}: board answer → ask")

    def _ask_item(self, lane: str, key: str, sub: dict[str, Any], i: int, n: int) -> int:
        options = [{"label": str(o.get("label", "")), "desc": str(o.get("description", ""))}
                   for o in sub.get("options") or []]
        while len(options) < 2:
            options.append({"label": ("Write my own", "Skip")[len(options) % 2], "desc": ""})
        title = clip(str(sub.get("header") or sub.get("question") or "Question"), 40) + (f" {i + 1}/{n}" if n > 1 else "")
        body = f"## Question\n{sub.get('question') or title}\n\n{ASK_HOW}"
        task = self.store.live_task(lane)
        owner = self.cfg.all_lanes[lane].owner or None if lane in self.cfg.all_lanes else None
        return self.store.add_item(lane, task["id"] if task else None, "decision", title, body, options, [],
                                   audience=HUMAN, multi=bool(sub.get("multiple")), question=key, assignee=owner)


    def route_decisions(self) -> None:
        for it in self.store.undelivered_decisions():
            told = self.overlaps.route(it) if self.overlaps else None
            if told:
                self.store.route_decision(it["id"], told[0], also=told[1:])
                continue
            push, follow = self._route(it)
            self.store.route_decision(it["id"], push, follow, self._decision_paths(it) if push and push[0] == INFRA else None)

    def _decision_paths(self, it: dict[str, Any]) -> str | None:
        mids = [it.get("message_id")] + [int(x) for x in MEMO_REF.findall(f"{it.get('title') or ''}\n{it.get('body') or ''}")]
        with self.store.conn() as con:
            for mid in mids:
                if not mid:
                    continue
                row = con.execute("SELECT * FROM messages WHERE id=? AND target=?", (int(mid), INFRA)).fetchone()
                if row is not None:
                    ps = message_paths(dict(row))
                    if ps is not None and ps != [ALONE]:
                        return ", ".join(ps) if ps else NOTHING
                    break
        if it.get("kind") == "review" and not str(it.get("note") or "").strip():
            return NOTHING
        return None

    def _route(self, it: dict[str, Any]) -> tuple[tuple[str, str] | None, dict[str, Any] | None]:
        line = decision_line(it)
        lane = it["lane"]
        if it.get("memo_id") and it["verdict"] == "ok" and not (it.get("note") or "").strip():
            return None, None
        if is_infra(lane) and self.has_infra:
            return (INFRA, line + f"\n(`dah show {it['id']}` for the full item.)"), None
        try:
            src = self.store.task(it["task_id"]) if it["task_id"] else None
        except StoreError:
            src = None
        follow = (lane in self.team and src is not None and it["verdict"] in FOLLOW_VERDICTS
                  and src["state"] in ("running", "wrapping", "closed"))
        if follow and src is not None:
            brief = (f"Follow-up to board item #{it['id']} only (fresh session). `dah show {it['id']}` has the item, "
                     f"`dah log {src['id']}` the source task.\n\nDECISION: {line}\n\nDo exactly what this decision "
                     f"allows and nothing beyond it. If it is unclear, `dah item add` or `dah report blocked`."
                     f"\n\nSOURCE TASK #{src['id']} BRIEF (context, do not replay):\n{src['brief']}")
            ln = self.cfg.all_lanes.get(lane)
            return None, {"lane": base_lane(lane), "title": clip(f"#{it['id']} {it['title']}", 80), "brief": brief,
                          "engine": ln.engine if ln else None, "task_id": it["task_id"],
                          "requester": src.get("requester"), "hub": src.get("hub"),
                          "text": lambda tid: f"{line} → follow-up task #{tid} for {lane}"}
        if is_hub_name(lane):
            return (None, None) if it["decided_by"] == lane else ((lane, line), None)
        hub = (src.get("hub") if src is not None else None) or HUB
        return (hub, line), None

    def _notify_humans(self) -> None:
        new = self.store.unnotified()
        if new:
            self.notify(new)
            for it in new:
                self.store.update_item(it["id"], notified=self.clock())

    def _api_errors(self) -> None:
        from . import apierrors, hooks
        if self.api_watch is None:
            self.api_watch = apierrors.EventWatch(hooks.root(self.cfg.home), self.clock)
        for kind, lane, message in self.api_watch.poll():
            self.log(f"{lane}: api {kind}: {message}")
            self.notify([{"id": lane, "lane": lane, "alert": True, "title": f"{lane}: {message}"}])

    def _done_gates(self) -> None:
        from . import donegate
        for info, r in donegate.poll(self.cfg, now=self.clock):
            lane, tid = info["lane"], info["task"]
            try:
                task = self.store.task(tid)
            except StoreError:
                donegate.settle(self.cfg, info)
                continue
            if task["state"] not in ("running", "wrapping"):
                self.log(f"{lane}: done gate {r.get('state')} for task #{tid}, which is no longer live")
                donegate.settle(self.cfg, info)
                continue
            if r.get("state") == "pass":
                atts = info.get("atts") or []
                self.store.add_report(tid, lane, "done", info["text"], atts)
                self.store.signal(tid)
                att = f" (files: {', '.join(atts)})" if atts else ""
                br = f" on branch {self.cfg.branch_of(lane)}" if self.cfg.branch_of(lane) else ""
                self.store.push(self.store.hub_for_task(tid), lane, "done", f"Done: {lane} task #{tid} \"{task['title']}\" (done gate passed "
                                f"at {str(info.get('sha'))[:8]}{br}): {clip(info['text'], 700)}{att}", tid)
                self.store.push(lane, TOWER, "tell", "The done gate passed; your done went to the hub. Review comes "
                                "back to this session.", tid)
            else:
                self.store.push(lane, TOWER, "tell", donegate.refusal(info, r), tid)
            donegate.settle(self.cfg, info)
            self.log(f"{lane}: done gate {r.get('state')} (task #{tid})")

    def _scratch(self) -> None:
        from .scratch import ScratchSweep
        if self.sweep is None:
            self.sweep = ScratchSweep(self.cfg, self.store, self.log, self.clock)
        self.sweep.poll()

    def _schedules(self) -> None:
        now = self.clock()
        for sched in self.cfg.schedules:
            period = schedules.latest_period(sched, now)
            last = self.store.schedule_period(sched.id)
            if last == period:
                continue
            try:
                if last is not None:
                    self._fire_schedule(sched, period)
                self.store.mark_schedule(sched.id, period)
            except Exception as e:
                self.log(f"schedule {sched.id}: {e}")

    def _fire_schedule(self, sched: schedules.Schedule, period: str) -> None:
        if sched.lane == HUB:
            self.store.push(HUB, "schedule", "memo", sched.text)
            self.log(f"schedule {sched.id}: request to the hub for {period}")
            return
        tid = self.store.add_task(sched.lane, sched.title, sched.text, self.cfg.lanes[sched.lane].engine)
        self.store.update_task(tid, **engines_mod.line_fields(sched.text))
        self.log(f"schedule {sched.id}: task #{tid} for {sched.lane} ({period})")


    def _lane(self, lane: str, st: str) -> None:
        task = self.store.live_task(lane)
        info = self.store.lane(lane)
        if task is None:
            if info.get("closing") and st in ("idle", "closed", "asking"):
                if st == "idle" and self.rt.owned(lane) and self._chain(lane, info):
                    return
                if st != "closed" and self.rt.owned(lane):
                    self.rt.close(lane)
                    self.log(f"{lane}: wrapped, closed")
                self.store.set_lane(lane, closing=None)
            elif lane in self.heavy and st == "idle" and self.rt.owned(lane) and self._idle_for(lane, st) >= SETTLE_SECONDS:
                mb = self.heavy.pop(lane)
                self.rt.close(lane)
                self.log(f"{lane}: {mb} MB, idle session closed")
            return
        if st == "closed":
            self._revive(lane, task)
            return
        if task.get("rotate_ready"):
            if st == "idle" and self.rt.owned(lane):
                self._rotate(lane, task)
            return
        idle_for = self._idle_for(lane, st)
        self._context_guard(lane, st, task)
        if self._continue_after_compact(lane, st, task, idle_for):
            return
        if st != "idle":
            return
        msgs = [m for m in self.store.pending(lane) if m["task_id"] == task["id"]]
        if msgs and idle_for >= SETTLE_SECONDS:
            self._send(lane, task["session"], "[tower]\n" + "\n\n".join(m["text"] for m in msgs))
            self.store.mark_delivered(m["id"] for m in msgs)
            self.store.update_task(task["id"], last_prompt=self.clock())
            return
        if lane in self.heavy and (task["id"], task.get("session") or "") not in self.rotate_asked:
            self.rotate_asked.add((task["id"], task.get("session") or ""))
            self.store.push(lane, TOWER, "rotate", f"Your session process is using {self.heavy[lane]} MB, over the limit "
                            f"({self.cfg.memory_limit_mb} MB). Write down where you are and hand off: "
                            f"`dah report handoff \"[now] ... [plan] ... [files] ...\"`, then end your turn. A fresh "
                            f"session continues the task.", task["id"])
            self.store.update_task(task["id"], last_prompt=self.clock())
            return
        if self.cfg.done_gate:
            from . import donegate
            if donegate.running(self.cfg, task["id"]):
                self.idle_since[lane] = self.clock()
                return
        waiting = self.store.open_items_for_task(task["id"]) > 0
        silent = (task.get("last_signal") or 0) < (task.get("last_prompt") or 0)
        if idle_for >= NUDGE_AFTER and silent and not waiting:
            n = task["nudges"]
            if n < NUDGE_LIMIT:
                self.store.push(lane, TOWER, "nudge", NUDGE, task["id"])
                self.store.update_task(task["id"], nudges=n + 1)
            elif n == NUDGE_LIMIT:
                self.store.push(self.store.hub_for_task(task["id"]), TOWER, "stalled", f"Stalled: {lane} task #{task['id']} \"{task['title']}\" went "
                                f"quiet after {NUDGE_LIMIT} nudges. Its last words: `dah last {lane}`.", task["id"])
                self.store.update_task(task["id"], nudges=n + 1)

    def _continue_after_compact(self, lane: str, st: str, task: dict[str, Any], idle_for: float) -> bool:
        waiting = self.compacting.get(task["id"])
        if waiting is None:
            return False
        sid, how, since = waiting
        if how == COMPACT_TYPED:
            ready = self.clock() - since >= COMPACT_TYPED_GAP_SECONDS
        else:
            ready = st == "idle" and idle_for >= SETTLE_SECONDS
        if not ready:
            return False
        try:
            self._send(lane, sid, self.compact_prompt(task), queued=how == COMPACT_TYPED)
        except Exception as e:
            if self.clock() - since < COMPACT_GIVE_UP_SECONDS:
                self.log(f"{lane}: task #{task['id']} continue message not sent yet: {e}")
                return True
            self.log(f"{lane}: task #{task['id']} continue message not sent for {COMPACT_GIVE_UP_SECONDS} s: {e}")
            del self.compacting[task["id"]]
            return False
        del self.compacting[task["id"]]
        self.store.update_task(task["id"], last_prompt=self.clock())
        return True

    def _context_guard(self, lane: str, st: str, task: dict[str, Any]) -> None:
        limit, sid = self.cfg.context_limit(lane, task.get("model") or ""), task.get("session")
        if not limit or not sid or st not in ("idle", "busy") or (task["id"], sid) in self.ctx_asked:
            return
        ctx = self.store.session_ctx(lane, sid) or 0
        if ctx < limit:
            return
        self.ctx_asked.add((task["id"], sid))
        self.rotate_why[task["id"]] = f"context {ctx}k over the {limit}k limit"
        text = CONTEXT_ROTATE.format(ctx=ctx, limit=limit)
        if st == "busy" and self.cfg.all_lanes[lane].runtime == "claude":
            from .hooks import post_urgent
            try:
                post_urgent(self.cfg.home, lane, f"[tower] {text}", source=TOWER)
            except OSError as e:
                self.log(f"{lane}: urgent handoff ask not written: {e}")
        self.store.push(lane, TOWER, "rotate", text, task["id"])
        self.log(f"{lane}: task #{task['id']} context {ctx}k over {limit}k, handoff asked")

    def _rotate(self, lane: str, task: dict[str, Any]) -> None:
        tid, sid = task["id"], task.get("session") or ""
        why = self.rotate_why.pop(tid, None) or (f"process at {self.heavy[lane]} MB" if lane in self.heavy else "")
        why = f" ({why})" if why else ""
        self.store.drop_pending(lane, ("rotate",))
        from .hooks import drop_urgent
        drop_urgent(self.cfg.home, lane, TOWER)
        how = None
        if handoff_mode(task.get("handoff")) == "compact" and sid:
            try:
                how = self.rt.compact(lane, sid, COMPACT_FOCUS)
            except Exception as e:
                self.log(f"{lane}: compact failed: {e}")
            if how:
                self.store.update_task(tid, rotate_ready=None, last_prompt=self.clock(), nudges=0)
                self.store.set_session_ctx(lane, sid, 0)
                self.ctx_asked.discard((tid, sid))
                self.idle_since[lane] = self.clock()
                if how in (COMPACT_PENDING, COMPACT_TYPED):
                    self.compacting[tid] = (sid, how, self.clock())
                self._hub_note(task, f"Handoff: {lane} task #{tid} \"{task['title']}\" compacted, same session{why}.")
                self.log(f"{lane}: task #{tid} compacted ({how})")
                return
        if self.accounts.check(lane, task=task) is None:
            return
        self.rt.close(lane)
        self.heavy.pop(lane, None)
        self._open_resume(lane, task, "handed off to a fresh session")
        self.store.update_task(tid, rotate_ready=None)
        wanted = " (compact asked; this runtime cannot, so a fresh one)" if handoff_mode(task.get("handoff")) == "compact" else ""
        self._hub_note(task, f"Handoff: {lane} task #{tid} \"{task['title']}\" continues in a new session{why}{wanted}.")

    def _hub_note(self, task: dict[str, Any], text: str) -> None:
        self.store.push(self.store.hub_for_task(task["id"]), TOWER, "handoff", text, task["id"])

    def _chain(self, lane: str, info: dict[str, Any]) -> bool:
        prev = self.store.last_closed_task(lane)
        sid = info.get("session")
        if not prev or not sid or lane in self.heavy:
            return False
        nxt = next((t for t in self.store.queued_for(lane) if linked(prev, t) and not self.conflict(t)), None)
        limit = min(CHAIN_CONTEXT_K, self.cfg.context_limit(lane) or CHAIN_CONTEXT_K)
        if nxt is None or self.rt.context_k(lane, sid) >= limit:
            return False
        la = self.accounts.check(lane, task=nxt)
        if la is None or la.owner != self.store.session_account(lane, sid):
            return False
        task = self.store.claim_task(nxt["id"])
        if task is None:
            return False
        text = (f"[tower] Task #{prev['id']} is wrapped and this one continues it, so you keep this session. Use what "
                f"you know, but report on this task fresh with `dah report`.\n\n{self.start_prompt(task)}")
        try:
            self._send(lane, sid, text)
        except Exception as e:
            self.store.update_task(nxt["id"], state="queued", started=None, last_prompt=None)
            self.log(f"{lane}: task #{nxt['id']} could not be sent to the same session: {e}")
            return False
        self.store.update_task(task["id"], session=sid)
        self.store.set_lane(lane, closing=None)
        self.store.drop_pending(lane, ("tell", "nudge", "wrap", "rotate"), keep_task=task["id"])
        self.log(f"{lane}: task #{task['id']} continues #{prev['id']} in the same session")
        return True

    def task_turns(self) -> dict[int, tuple[int, int, float]]:
        return team_turns(self.store, set(self.team), self.cfg.fair_turns)

    def _slots(self) -> tuple[Callable[[dict[str, Any]], str], int, int]:
        split = self.cfg.light_lanes is not None and bool(self.cfg.heavy_paths)
        wt = self.weight if split else (lambda t: "heavy")
        busy = self.store.busy_lanes() & set(self.team)
        live = {t["lane"]: wt(t) for t in self.store.tasks(("running", "wrapping"), limit=500)}
        heavy_busy = len([b for b in busy if live.get(b, "heavy") == "heavy"])
        light_busy = len(busy) - heavy_busy
        return wt, self.max_lanes - heavy_busy, (self.cfg.light_lanes or 0) - light_busy if split else 0

    def _start_queued(self, statuses: dict[str, str]) -> None:
        wt, free_h, free_l = self._slots()
        busy = self.store.busy_lanes()
        waiting: list[dict[str, Any]] = []
        for lane in self.team:
            if lane in busy or not self.store.queued_for(lane):
                continue
            st = self.rt.status(lane)
            if st == "closed" or (st == "idle" and self.rt.owned(lane)):
                statuses[lane] = st
                waiting += self.store.queued_for(lane)
        waiting += self._spill(busy, free_h + free_l, statuses)
        started: list[dict[str, Any]] = []
        waits: dict[int, str] = {}
        for lane in busy & set(self.team):
            for t in self.store.queued_for(lane):
                waits[t["id"]] = f"lane {lane} is busy with another task"
        order = self.task_turns()
        for nxt in sorted(waiting, key=lambda t: order.get(t["id"], (2, 0, t["id"]))):
            lane = nxt["lane"]
            w = wt(nxt)
            if any(t["lane"] == lane for t in started):
                waits[nxt["id"]] = f"lane {lane} started another task this tick"
                continue
            if len([t for t in started if wt(t) == w]) >= max(0, free_h if w == "heavy" else free_l):
                waits[nxt["id"]] = f"every {w} slot is busy" if self.cfg.light_lanes is not None else "every slot is busy"
                continue
            signin = accounthold.hold_of(self.store, f"task:{nxt['id']}")
            if signin:
                waits[nxt["id"]] = accounthold.held_text(signin["member"], self.cfg.lang)
                continue
            why = self.conflict(nxt, started) or self.cap_reason(nxt)
            if why:
                waits[nxt["id"]] = why
                self.log(f"{lane}: task #{nxt['id']} waits: {why}")
                continue
            if not self._sync_branch(lane, nxt, waits, busy | {t["lane"] for t in started}, statuses):
                continue
            try:
                la = self.accounts.check(lane, task=nxt)
                if la is None:
                    waits[nxt["id"]] = accounthold.held_text(str(self.accounts.launch(lane, task=nxt).member), self.cfg.lang)
                    continue
                if statuses[lane] == "idle":
                    self.rt.close(lane)
                task = self.store.claim_task(nxt["id"])
                if task is None:
                    continue
                try:
                    sid = self.rt.open(lane, self.start_prompt(task), **self._opts(task), **self._account_kw(la))
                except Exception:
                    self.store.update_task(nxt["id"], state="queued", started=None, last_prompt=None)
                    raise
            except Exception as e:
                self.log(f"{lane}: task #{nxt['id']} failed to start: {e}")
                continue
            self.accounts.opened(lane, sid, la)
            started.append(nxt)
            self.heavy.pop(lane, None)
            self.store.update_task(nxt["id"], session=sid)
            self.store.drop_pending(lane, ("tell", "nudge", "wrap", "rotate"), keep_task=nxt["id"])
            self.idle_since.pop(lane, None)
            self.log(f"{lane}: task #{nxt['id']} started")
        if waits != self.waits:
            from .machine import write_waits
            self.waits = waits
            write_waits(self.cfg.state_dir, waits)

    def _leftovers(self) -> None:
        if not self.cfg.leftover_sweep:
            return
        now = self.clock()
        if self.leftover_at is not None and now - self.leftover_at < LEFTOVER_SECONDS:
            return
        self.leftover_at = now
        found = self.proc_scan(self.cfg.home)
        if not found:
            self.leftover_seen = set()
            return
        current = {lane: self.store.lane(lane).get("session") for lane in self.lanes()}
        trees = leftovers.stale(found, current)
        roots = {t[0].pid for t, _ in trees}
        lines = []
        for tree, reason in trees:
            root = tree[0]
            if root.pid not in self.leftover_seen:
                continue
            for p in reversed(tree):
                try:
                    self.proc_stop(p.pid)
                except Exception as e:
                    self.log(f"leftover: pid {p.pid} not stopped: {e}")
            lines.append(f"- {root.lane}: pid {root.pid}" + (f" and {len(tree) - 1} child process(es)" if len(tree) > 1
                         else "") + f": {reason} · `{clip(root.cmd, 120)}`")
            roots.discard(root.pid)
        self.leftover_seen = roots
        if lines:
            self.store.push(HUB, TOWER, "leftover", "Leftover lane processes stopped (their session had closed):\n"
                            + "\n".join(lines))
            self.log(f"leftover: stopped {len(lines)} process tree(s)")

    def _tree_in_use(self, lane: str, busy: set[str], statuses: dict[str, str]) -> bool:
        return lane in busy or statuses.get(lane, "closed") not in ("closed", "idle")

    def _sync_branch(self, lane: str, task: dict[str, Any], waits: dict[int, str], busy: set[str],
                     statuses: dict[str, str]) -> bool:
        from .branchsync import WorktreeConflict, WorktreeDirty
        ln = self.cfg.all_lanes.get(lane)
        if not (self.cfg.branch_sync and ln and ln.worktree) or not hasattr(self.rt, "sync"):
            return True
        if self.clock() < self.sync_retry.get(lane, 0.0) or self._tree_in_use(lane, busy, statuses):
            return True
        try:
            what = self.rt.sync(lane)
        except WorktreeConflict as e:
            why = str(e)
            dirty = isinstance(e, WorktreeDirty)
            waits[task["id"]] = f"its worktree has {why}" if dirty else f"its branch does not take main cleanly: {why}"
            if self.sync_told.get(task["id"]) != why:
                self.sync_told[task["id"]] = why
                fix = ("Commit or discard them; the task opens once the tree is clean." if dirty else
                       f"Resolve it in {self.cfg.lane_dir(lane)} (merge main, commit) or merge the branch yourself; "
                       "the task opens once main merges in.")
                self.store.push(self.store.hub_for_task(task["id"]), TOWER, "stalled",
                                f"Not started: {lane} task #{task['id']} \"{task['title']}\": {why}. {fix}", task["id"])
            self.log(f"{lane}: task #{task['id']} waits: {why}")
            return False
        except Exception as e:
            self.sync_retry[lane] = self.clock() + SYNC_RETRY_SECONDS
            self.log(f"{lane}: branch sync before task #{task['id']} failed: {e}")
            return True
        self.sync_told.pop(task["id"], None)
        if what:
            self.log(f"{lane}: {what} (task #{task['id']})")
        return True

    def _catch_up_branches(self, statuses: dict[str, str]) -> None:
        from .branchsync import WorktreeConflict, catch_up
        every = self.cfg.branch_sync_seconds
        now = self.clock()
        if not every or (self.catch_up_at is not None and now - self.catch_up_at < every):
            return
        self.catch_up_at = now
        busy = self.store.busy_lanes()
        for lane in self.team:
            ln = self.cfg.all_lanes.get(lane)
            if not (ln and ln.worktree):
                continue
            if now < self.sync_retry.get(lane, 0.0) or self._tree_in_use(lane, busy, statuses):
                continue
            try:
                what = catch_up(self.cfg, ln)
            except WorktreeConflict as e:
                why = str(e)
                if self.catch_up_told.get(lane) != why:
                    self.catch_up_told[lane] = why
                    self.store.push(HUB, TOWER, "stalled", f"{lane}: the tower could not merge main into its idle "
                                    f"branch and undid the merge: {why}. Resolve it in {self.cfg.lane_dir(lane)} "
                                    "(merge main, commit) or merge the branch yourself.")
                self.log(f"{lane}: branch sync: {why}")
                continue
            except Exception as e:
                self.sync_retry[lane] = now + SYNC_RETRY_SECONDS
                self.log(f"{lane}: branch sync failed: {e}")
                continue
            self.catch_up_told.pop(lane, None)
            if what:
                self.log(f"{lane}: {what}")

    def _spill(self, busy: set[str], room: int, statuses: dict[str, str]) -> list[dict[str, Any]]:
        for task in self.store.tasks(("queued",), limit=500):
            n = task["lane"].partition("#")[2]
            if n and (not n.isdigit() or int(n) > self.cfg.instance_cap(task["lane"])):
                self.store.update_task(task["id"], lane=base_lane(task["lane"]))
        out: list[dict[str, Any]] = []
        if room <= 0:
            return out
        moved: list[dict[str, Any]] = []
        order = self.task_turns()
        for base in self._team:
            if base not in busy or self.cfg.instance_cap(base) < 2:
                continue
            keys = [instance_lane(base, n) for n in range(2, self.cfg.instance_cap(base) + 1)]
            key = next((k for k in keys if k not in busy and not self.store.lane(k).get("closing")
                        and not self.store.queued_for(k) and self.rt.status(k) == "closed"), None)
            if key is None:
                continue
            for task in sorted(self.store.queued_for(base), key=lambda t: order.get(t["id"], (2, 0, t["id"]))):
                if not self.conflict(task, moved):
                    moved.append({**task, "lane": key})
                    break
        for task in sorted(moved, key=lambda t: order.get(t["id"], (2, 0, t["id"])))[:room]:
            self.store.update_task(task["id"], lane=task["lane"])
            statuses[task["lane"]] = "closed"
            self.log(f"{task['lane']}: task #{task['id']} moved to a parallel session")
            out.append(self.store.task(task["id"]))
        return out

    def _revive(self, lane: str, task: dict[str, Any]) -> None:
        if task["respawns"] >= RESPAWN_LIMIT:
            if task["respawns"] == RESPAWN_LIMIT:
                self.store.push(self.store.hub_for_task(task["id"]), TOWER, "stalled", f"Closed: {lane} task #{task['id']} closed {RESPAWN_LIMIT} times "
                                f"after reopening. `dah cancel {task['id']}` or reassign it.", task["id"])
                self.store.update_task(task["id"], respawns=RESPAWN_LIMIT + 1)
            return
        handed = bool(task.get("rotate_ready"))
        if not self._open_resume(lane, task, "handed off to a fresh session" if handed else "the session had closed"):
            return
        self.store.update_task(task["id"], respawns=task["respawns"] + (0 if handed else 1), rotate_ready=None)
        self.compacting.pop(task["id"], None)
        if handed:
            self.store.drop_pending(lane, ("rotate",))
            self._hub_note(task, f"Handoff: {lane} task #{task['id']} \"{task['title']}\" continues in a new session.")

    def _open_resume(self, lane: str, task: dict[str, Any], why: str) -> bool:
        la = self.accounts.check(lane, task=task)
        if la is None:
            return False
        sid = self.rt.open(lane, self.resume_prompt(task, why), **self._opts(task), **self._account_kw(la))
        self.accounts.opened(lane, sid, la)
        self.store.update_task(task["id"], session=sid, last_prompt=self.clock(), nudges=0)
        self.idle_since.pop(lane, None)
        self.log(f"{lane}: task #{task['id']} new session ({why})")
        return True


    def _person_hubs(self) -> None:
        for target in self.store.pending_targets():
            if is_hub_name(target) and not self.cfg.is_hub(target):
                n = self.store.retarget_pending(target, HUB)
                self.log(f"{target}: gone, {n} message(s) moved to {HUB}")
        over = tuple(self.cfg.hub_overflow)
        if over == self.overflow_seen:
            return
        self.overflow_seen = over
        lang = self.cfg.lang.split("-")[0].lower()
        lang = lang if lang in OVERFLOW_TITLE else "en"
        title = OVERFLOW_TITLE[lang]
        old = next((it for it in self.store.items(state="open", lane=HUB) if it["title"] == title), None)
        if not over:
            if old:
                self.store.decide(old["id"], "ok", TOWER, note="The line is empty.")
                self.store.update_item(old["id"], delivered=self.clock())
            return
        body = OVERFLOW_BODY[lang].format(n=len(over), cap=self.cfg.hub_cap, names=", ".join(over))
        if old:
            self.store.revise_item(old["id"], None, body, None, None)
        else:
            self.store.add_item(HUB, None, "review", title, body, [], [], audience=HUMAN)
        self.log(f"person hubs: over the cap of {self.cfg.hub_cap}: {', '.join(over)}")

    def _hub(self, st: str, lane: str = HUB) -> None:
        info = self.store.lane(lane)
        if st == "closed":
            if info.get("enabled"):
                now = self.clock()
                opens = self.hub_opens[lane] = [t for t in self.hub_opens.get(lane, []) if now - t < HUB_RETRY_WINDOW]
                if len(opens) < RESPAWN_LIMIT:
                    self.hub_backoff.discard(lane)
                else:
                    if lane not in self.hub_backoff:
                        self.hub_backoff.add(lane)
                        self.notify([{"id": lane, "lane": lane, "alert": True, "title": f"{lane} closed {RESPAWN_LIMIT} "
                                      f"times in {HUB_RETRY_WINDOW // 60} min; see .tower/state/logs/{lane}.err or its "
                                      f"window"}])
                        self.log(f"{lane}: closed {RESPAWN_LIMIT} times in a row, retrying slowly")
                    if now - opens[-1] < HUB_RETRY_WINDOW / 2:
                        return
                    opens.pop(0)
                la = self.accounts.check(lane)
                if la is None:
                    return
                opens.append(now)
                handoff = str(info.get("handoff") or "")
                self.accounts.opened(lane, self.rt.open(lane, self.hub_prompt(handoff, lane), **self._account_kw(la)), la)
                self.store.set_lane(lane, opened=now)
                if handoff:
                    self.store.set_lane(lane, handoff=None, handoff_session=None)
                self.idle_since.pop(lane, None)
                self.log(f"{lane}: opened")
            return
        idle_for = self._idle_for(lane, st)
        if st == "busy":
            self._watch_stuck(lane)
        if st != "idle" or not info.get("enabled"):
            return
        sid = info.get("session")
        msgs = self.store.pending(lane)
        if msgs and sid and idle_for >= SETTLE_SECONDS and self.clock() - msgs[0]["created"] >= BATCH_SECONDS:
            lines = []
            for m in msgs:
                if m["kind"] == "memo":
                    lines.append(f"• Request #{m['id']} from {m['sender']}: {m['text']} "
                                 f"(answer with `dah memo reply {m['id']} \"...\"`)")
                else:
                    lines.append(f"• {m['text']}")
            self._send(lane, sid, f"[tower] {len(msgs)} new\n" + "\n".join(lines) + f"\n{self.summary()}")
            self.store.mark_delivered(m["id"] for m in msgs)
            return
        if not msgs and sid and idle_for >= 30 and self.rt.owned(lane):
            self._hub_handoff(sid, info, lane)

    def _hub_handoff(self, sid: str, info: dict[str, Any], lane: str = HUB) -> None:
        handoff = str(info.get("handoff") or "") if info.get("handoff_session") == sid else ""
        asked = self.hub_asked.get(lane)
        asked = asked if asked and asked[0] == sid else None
        if handoff or (asked and self.clock() - asked[1] >= HUB_HANDOFF_WAIT):
            why = "handed off" if handoff else f"asked to hand off {int(HUB_HANDOFF_WAIT // 60)} min ago, no handoff"
            la = self.accounts.check(lane)
            if la is None:
                return
            self.heavy.pop(lane, None)
            self.rt.close(lane)
            self.accounts.opened(lane, self.rt.open(lane, self.hub_prompt(handoff, lane), **self._account_kw(la)), la)
            self.store.set_lane(lane, opened=self.clock(), handoff=None, handoff_session=None)
            self.hub_asked.pop(lane, None)
            self.idle_since.pop(lane, None)
            self.log(f"{lane}: {why}, fresh session")
            return
        if asked:
            return
        limit = self.cfg.hub_context_limit(lane) or HUB_ROTATE_K
        ctx = self.rt.context_k(lane, sid)
        if lane in self.heavy:
            why = f"at {self.heavy[lane]} MB of memory"
        elif ctx >= limit:
            why = f"at {ctx}k tokens of context (limit {limit}k)"
        else:
            return
        self._send(lane, sid, HUB_ROTATE_ASK.format(why=why))
        self.hub_asked[lane] = (sid, self.clock())
        self.log(f"{lane}: {why}, handoff asked")

    def _watch_stuck(self, lane: str = HUB) -> None:
        msgs = self.store.pending(lane)
        if not msgs:
            return
        oldest = msgs[0]
        waited = self.clock() - oldest["created"]
        if waited < HUB_STUCK_SECONDS or self.stuck_for.get(lane) == oldest["id"]:
            return
        self.stuck_for[lane] = oldest["id"]
        lang = self.cfg.lang.split("-")[0].lower()
        lang = lang if lang in STUCK_TITLE else "en"
        title = STUCK_TITLE[lang] + ("" if lane == HUB else f" ({lane})")
        if any(it["title"] == title for it in self.store.items(state="open", lane=lane)):
            return
        body = STUCK_BODY[lang].format(n=len(msgs), m=int(waited // 60))
        if lane != HUB:
            body = body.replace("dah last hub", f"dah last {lane}").replace("logs/hub.err", f"logs/{lane}.err")
        self.store.add_item(lane, None, "review", title, body, [], [], audience=HUMAN)
        self.log(f"{lane}: busy with news waiting {int(waited // 60)} min, told humans")


    def _infra_all(self, statuses: dict[str, str]) -> None:
        for key in self.infra_keys:
            try:
                self._infra(statuses.get(key, "unknown"), key)
            except Exception as e:
                self.log(f"{key}: {e}")

    def _infra(self, st: str, lane: str = INFRA) -> None:
        self._idle_for(lane, st)
        info = self.store.lane(lane)
        if info.get("closing"):
            if st not in ("idle", "closed"):
                return
            if st == "idle" and self.rt.owned(lane):
                self.rt.close(lane)
                self.log(f"{lane}: wrapped, closed")
            self.store.finish_inbox(lane)
            if st == "idle":
                return
        if st != "closed":
            return
        live = [{**m, "_lane": k} for k, m in self.store.held_messages([k for k in self.infra_keys if k != lane]).items()]
        now, barrier = self.clock(), []
        held = {int(r["work"].split(":")[1]) for r in [*self.store.overlaps(overlap.HOLDS), *self.store.account_holds()]
                if r["work"].startswith("memo:")}

        def allow(m: dict[str, Any]) -> bool:
            if barrier or m["id"] in held:
                return False
            if message_block(m, live, now) is None:
                return True
            if message_paths(m) == [ALONE]:
                barrier.append(m)
            return False
        msg = self.store.claim_inbox(lane, INFRA, allow, self.cfg.fair_turns)
        if msg is None:
            return
        if msg["delivered"] is not None:
            n = self.store.retry_inbox(lane)
            if n > RESPAWN_LIMIT:
                if n == RESPAWN_LIMIT + 1:
                    self.notify([{"id": msg["id"], "lane": INFRA, "title": f"infra closed {RESPAWN_LIMIT} times on "
                                  f"message #{msg['id']}; skipped", "alert": True}])
                    self.store.push(msg["sender"] if self.cfg.is_hub(str(msg["sender"])) else HUB, TOWER, "stalled",
                                    f"infra gave up on message #{msg['id']} after "
                                    f"{RESPAWN_LIMIT} closed sessions.")
                self.store.finish_inbox(lane)
                return
        la = self.accounts.check(lane, msg=msg)
        if la is None:
            self.store.release_inbox(lane)
            self.store.set_lane(lane, closing=None)
            return
        try:
            self.accounts.opened(lane, self.rt.open(lane, self.inbox_prompt(msg, lane), **self._memo_opts(msg),
                                                    **self._account_kw(la)), la)
        except Exception as e:
            self.log(f"{lane}: message #{msg['id']} failed to open: {e}")
            self.store.mark_delivered([msg["id"]])
            return
        self.store.mark_delivered([msg["id"]])
        self.idle_since.pop(lane, None)
        self.log(f"{lane}: opened for message #{msg['id']}")
