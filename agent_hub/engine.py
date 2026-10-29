"""One tower tick. No LLM here: it reads the store and each lane's status and moves work along.

  hub              always on. News for it (reports, items to review, decisions, human requests) is batched and
                   sent while it is idle. When its context grows large it is restarted quietly (state lives here).
  infra            one human memo (or one infra decision) per fresh session; the next waits until it wraps.
  team lanes       the hub queues tasks; the tower opens a fresh session per task (up to max_lanes at once),
                   delivers `tell`/`wrap` while idle, nudges silent lanes, revives closed ones, closes wrapped ones.
  decided items    routed once: a team item with a task becomes ONE follow-up task in a fresh session;
                   everything else goes to the hub (or infra) as news.
  live asks        hub and infra may ask (opencode only): each question becomes a board item for humans and the
                   answer goes back to the ask. Team lanes' asks are bounced to the board.
  human items      push notification once.
  stuck hub        news waits while the hub is busy. If it waits HUB_STUCK_SECONDS (a hook or plugin that keeps the
                   session working, a turn that never ends), humans get one board item about it.
  goals            when the hub is idle, nothing is queued or running and the goals changed since it was last told,
                   the hub gets the ranked goals and moves the first one forward. Unchanged goals never re-prompt it,
                   so a hub that is waiting on humans is left alone.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from .config import HUB, INFRA, Config
from .store import HUMAN, MULTI_SEP, TOWER, Store

BATCH_SECONDS = 8
SETTLE_SECONDS = 3
NUDGE_AFTER = 120
NUDGE_LIMIT = 2
RESPAWN_LIMIT = 3
HUB_ROTATE_K = 300
HUB_STUCK_SECONDS = 900
GOAL_IDLE_SECONDS = 60
STUCK_TITLE = {"en": "The hub does not go idle", "ko": "허브가 idle로 돌아오지 않음",
               "ja": "ハブがアイドルに戻らない", "zh": "中枢一直没有回到空闲", "es": "El hub no vuelve a quedar libre"}
STUCK_BODY = {
    "en": ("## What happened\n{n} piece(s) of news for the hub have waited {m} min: the tower only writes into the hub "
           "while it is idle, and it has stayed busy.\n\n## Likely causes\n- A hook or plugin in the hub's agent keeps "
           "the session working (auto-continue, todo loops).\n- One very long turn.\n\n## What to do\n- `tower last hub` "
           "and `.tower/state/logs/hub.err` show what it is doing.\n- Give the hub lane a lighter agent (`\"agent\"` in "
           "`.tower/tower.json`) or another runtime: a memo to infra can do it.\n- Approve this item once you have seen it."),
    "ko": ("## 무슨 일\n허브에 보낼 소식 {n}건이 {m}분째 대기 중입니다. tower는 허브가 idle일 때만 소식을 넣는데, 허브가 계속 "
           "busy 상태입니다.\n\n## 흔한 원인\n- 허브 에이전트의 hook이나 플러그인이 세션을 계속 돌림(자동 계속, todo 루프).\n"
           "- 아주 긴 한 턴.\n\n## 할 일\n- `tower last hub`와 `.tower/state/logs/hub.err`로 무엇을 하는지 확인.\n"
           "- 허브 레인에 더 가벼운 에이전트(`.tower/tower.json`의 `\"agent\"`)나 다른 런타임 지정: infra에 메모하면 됩니다.\n"
           "- 확인했으면 이 항목을 승인하세요."),
    "ja": ("## 何が起きたか\nハブ宛ての知らせ {n} 件が {m} 分待っています。tower はハブがアイドルのときだけ知らせを書き込みますが、"
           "ハブがずっと作業中のままです。\n\n## よくある原因\n- ハブのエージェントの hook やプラグインがセッションを動かし続けている"
           "(自動継続、todo ループ)。\n- とても長い 1 ターン。\n\n## すること\n- `tower last hub` と `.tower/state/logs/hub.err` "
           "で何をしているか確認。\n- ハブのレーンに軽いエージェント(`.tower/tower.json` の `\"agent\"`)か別のランタイムを指定: "
           "infra へのメモで頼めます。\n- 確認したらこの項目を承認してください。"),
    "zh": ("## 发生了什么\n发给中枢的 {n} 条消息已等待 {m} 分钟。tower 只在中枢空闲时写入消息，而中枢一直在忙。\n\n"
           "## 常见原因\n- 中枢代理的 hook 或插件让会话一直运行(自动继续、todo 循环)。\n- 一个非常长的回合。\n\n## 怎么办\n"
           "- 用 `tower last hub` 和 `.tower/state/logs/hub.err` 查看它在做什么。\n- 给中枢通道换一个更轻的代理"
           "(`.tower/tower.json` 里的 `\"agent\"`)或别的运行时：给 infra 写备忘即可。\n- 看过后请批准此项。"),
    "es": ("## Qué pasó\n{n} aviso(s) para el hub llevan {m} min esperando: la torre solo escribe al hub cuando está libre, "
           "y sigue ocupado.\n\n## Causas probables\n- Un hook o plugin del agente del hub mantiene la sesión trabajando "
           "(auto-continuar, bucles de tareas).\n- Un turno muy largo.\n\n## Qué hacer\n- `tower last hub` y "
           "`.tower/state/logs/hub.err` muestran qué hace.\n- Asigna al hub un agente más ligero (`\"agent\"` en "
           "`.tower/tower.json`) u otro runtime: basta una nota a infra.\n- Aprueba este elemento cuando lo hayas visto."),
}
FOLLOW_VERDICTS = ("ok", "no", "answer")
VERDICT_WORD = {"ok": "approved", "no": "rejected", "hold": "on hold", "answer": "answered in own words",
                "merged": "merged"}

RULES = ("(Tower task. Need a decision or an approval? Post it with `tower item add` and do not wait for it: finish "
         "the unblocked, approved scope and `tower report done \"summary · evidence paths · open item ids\"`. "
         "Decisions arrive later as separate follow-up tasks in fresh sessions. Never ask questions in chat.)")
ASK_BOUNCE = ("Do not ask in chat. Post a board item with `tower item add` if a human must decide, then carry on "
              "with other work or end your turn.")
ASK_GONE = "the question disappeared (answered in the terminal, or the session ended)"
ASK_HOW = "## How to answer\n- Your pick and note go straight back to the session.\n- No option fits? Write your own answer."
NUDGE = ("You stopped without a report. Finished: `tower report done \"summary · evidence\"`. Need a decision: "
         "`tower item add`. Stuck: `tower report blocked \"why\"`. Work left: keep going.")
WRAP_RULE = ("(This session carries exactly one item. Handle it, answer it (`tower memo reply ID \"...\"` for a memo), "
             "then `tower report wrapped \"one line\"`. The tower closes the session; anything else arrives later "
             "in a fresh one.)")


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


class Engine:
    def __init__(self, cfg: Config, store: Store, rt: Any, notify: Callable[[list[dict[str, Any]]], None],
                 clock: Callable[[], float] = time.time, log: Callable[[str], None] = print):
        self.cfg, self.store, self.rt, self.notify, self.clock, self.log = cfg, store, rt, notify, clock, log
        self.team = cfg.team()
        self.has_infra = INFRA in cfg.lanes
        self.max_lanes = cfg.max_lanes
        self.idle_since: dict[str, float] = {}
        self.stuck_for: int | None = None  # oldest waiting hub message we already raised a stuck alert for
        self.goals_told = ""  # the goals as the hub last heard them; unchanged goals never re-prompt it

    # ---------- prompts ----------

    def _read_first(self, lane: str) -> str:
        protocol = self.cfg.home / ".tower" / "PROTOCOL.md"
        return (f"First read `{self.cfg.instructions(lane)}` and `{protocol}` and follow them. "
                f"Humans read the board in `{self.cfg.lang}`; write everything they read in that language.")

    def start_prompt(self, task: dict[str, Any]) -> str:
        return (f"[tower] {task['lane']} lane · task #{task['id']} \"{task['title']}\"\n\n{self._read_first(task['lane'])}"
                f"\n\n{task['brief']}\n\n{RULES}")

    def resume_prompt(self, task: dict[str, Any], why: str) -> str:
        done = "\n".join(f"- {r['kind']}: {clip(r['text'], 300)}" for r in self.store.reports(task["id"])[-6:]) or "- none"
        hand = f"\n\nHandoff:\n{task['handoff']}" if task.get("handoff") else ""
        return (f"[tower] {task['lane']} lane · task #{task['id']} \"{task['title']}\" continued: {why}\n\n"
                f"{self._read_first(task['lane'])}\n\nOriginal brief:\n{task['brief']}\n\nReports so far:\n{done}{hand}"
                f"\n\nRun `tower mine` for this task's open board items, then continue. {RULES}")

    def hub_prompt(self) -> str:
        return f"[tower] You are the hub lane.\n\n{self._read_first(HUB)}\n\nThen run `tower status` and handle what is open."

    def inbox_prompt(self, msg: dict[str, Any]) -> str:
        body = (f"Memo #{msg['id']} from {msg['sender']}:\n{msg['text']}" if msg["kind"] == "memo" else msg["text"])
        return f"[tower] You are the infra lane.\n\n{self._read_first(INFRA)}\n\n{body}\n\n{WRAP_RULE}"

    # ---------- helpers ----------

    def _idle_for(self, lane: str, st: str) -> float:
        now = self.clock()
        if st != "idle":
            self.idle_since.pop(lane, None)
            return 0.0
        return now - self.idle_since.setdefault(lane, now)

    def _send(self, lane: str, sid: str, text: str) -> None:
        self.rt.send(lane, sid, text)
        self.idle_since[lane] = self.clock()

    def summary(self) -> str:
        h = len(self.store.items(state="open", audience=HUB))
        m = len(self.store.items(state="open", audience=HUMAN))
        run = len(self.store.tasks(("running", "wrapping")))
        q = len(self.store.tasks(("queued",)))
        g = len(self.store.goals(("active",)))
        return f"(now: {g} active goals · {h} items for the hub · {m} for humans · {run} tasks running · {q} queued)"

    def goals_prompt(self, goals: list[dict[str, Any]]) -> str:
        lines = []
        for g in goals:
            plan = f"{g['steps_done']}/{len(g['steps'])} steps" if g["steps"] else "no plan yet"
            nxt = next((f" · next: {s['text']}" for s in g["steps"] if not s.get("done")), "")
            lines.append(f"{g['priority']}. goal #{g['id']} \"{g['title']}\" ({plan}{nxt})")
        return ("[tower] Nothing is queued or running. Active goals, first priority first:\n" + "\n".join(lines)
                + "\n\nMove the first goal you can forward. No plan yet: propose one to the humans (a board item, or "
                  "`tower goal plan ID --step ...` when the request already says what to do). Otherwise queue the next "
                  "step as a task (`tower task LANE --goal ID ...`) or do it yourself, and tick finished steps "
                  "(`tower goal check ID N`). A goal is met: `tower goal done ID \"what was delivered\"`. If every goal "
                  "waits on humans, end your turn.")

    # ---------- tick ----------

    def lanes(self) -> list[str]:
        return [HUB, *([INFRA] if self.has_infra else []), *self.team]

    def tick(self) -> None:
        statuses: dict[str, str] = {}
        for lane in self.lanes():
            try:
                statuses[lane] = self.rt.status(lane)
            except Exception as e:  # one broken lane must not stop the others
                statuses[lane] = "unknown"
                self.log(f"{lane}: status failed: {e}")
            self.store.set_lane(lane, status=statuses[lane], seen=self.clock())
        self._questions(statuses)
        self.route_decisions()
        self._notify_humans()
        for lane in self.team:
            try:
                self._lane(lane, statuses[lane])
            except Exception as e:
                self.log(f"{lane}: {e}")
        self._start_queued(statuses)
        for name, fn in ((HUB, self._hub), (INFRA, self._infra)):
            if name in statuses:
                try:
                    fn(statuses[name])
                except Exception as e:
                    self.log(f"{name}: {e}")

    # ---------- live asks ----------

    def _questions(self, statuses: dict[str, str]) -> None:
        for lane, st in statuses.items():
            if lane in (HUB, INFRA):
                self._ask_board(lane, st)
            elif st == "asking":
                for q in self.rt.questions(lane):
                    self.rt.reply_question(lane, q["id"], [[ASK_BOUNCE]] * max(1, len(q.get("questions", []))))
                    self.log(f"{lane}: bounced an ask to the board")

    def _ask_board(self, lane: str, st: str) -> None:
        if st == "unknown":
            return
        asks = self.rt.questions(lane) if st != "closed" else []
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
        return self.store.add_item(lane, task["id"] if task else None, "decision", title, body, options, [],
                                   audience=HUMAN, multi=bool(sub.get("multiple")), question=key)

    # ---------- decisions ----------

    def route_decisions(self) -> None:
        """Route each decided item exactly once, in one transaction per item."""
        for it in self.store.undelivered_decisions():
            con = self.store.conn()
            try:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT delivered FROM items WHERE id=?", (it["id"],)).fetchone()
                if row is None or row[0] is not None:
                    con.execute("ROLLBACK")
                    continue
                self._route(con, it)
                con.execute("UPDATE items SET delivered=? WHERE id=?", (self.clock(), it["id"]))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            finally:
                con.close()

    def _route(self, con: Any, it: dict[str, Any]) -> None:
        line = decision_line(it)
        lane = it["lane"]
        if lane == INFRA and self.has_infra:
            self.store.push(INFRA, TOWER, "decision", line + f"\n(`tower show {it['id']}` for the full item.)",
                            it["task_id"], it["id"], con=con)
            return
        src = con.execute("SELECT * FROM tasks WHERE id=?", (it["task_id"],)).fetchone() if it["task_id"] else None
        follow = (lane in self.team and src is not None and it["verdict"] in FOLLOW_VERDICTS
                  and src["state"] in ("running", "wrapping", "closed"))
        if follow:
            brief = (f"Follow-up to board item #{it['id']} only (fresh session). `tower show {it['id']}` has the item, "
                     f"`tower log {src['id']}` the source task.\n\nDECISION: {line}\n\nDo exactly what this decision "
                     f"allows and nothing beyond it. If it is unclear, `tower item add` or `tower report blocked`."
                     f"\n\nSOURCE TASK #{src['id']} BRIEF (context, do not replay):\n{src['brief']}")
            cur = con.execute("INSERT INTO tasks(lane, title, brief, state, created, goal_id) VALUES(?,?,?,?,?,?)",
                              (lane, clip(f"#{it['id']} {it['title']}", 80), brief, "queued", self.clock(),
                               src["goal_id"]))
            self.store.push(HUB, TOWER, "decision", f"{line} → follow-up task #{cur.lastrowid} for {lane}",
                            it["task_id"], it["id"], con=con)
            return
        if it["decided_by"] != HUB or lane != HUB:
            self.store.push(HUB, TOWER, "decision", line, it["task_id"], it["id"], con=con)

    def _notify_humans(self) -> None:
        new = self.store.unnotified()
        if new:
            self.notify(new)
            for it in new:
                self.store.update_item(it["id"], notified=self.clock())

    # ---------- team lanes ----------

    def _lane(self, lane: str, st: str) -> None:
        task = self.store.live_task(lane)
        info = self.store.lane(lane)
        if task is None:
            if info.get("closing") and st in ("idle", "closed"):
                if st == "idle" and self.rt.owned(lane):
                    self.rt.close(lane)
                    self.log(f"{lane}: wrapped, closed")
                self.store.set_lane(lane, closing=None)
            return
        if st == "closed":
            self._revive(lane, task)
            return
        if task.get("rotate_ready"):
            if st == "idle" and self.rt.owned(lane):
                self.rt.close(lane)
                self._open_resume(lane, task, "handed off to a fresh session")
                self.store.update_task(task["id"], rotate_ready=None)
            return
        idle_for = self._idle_for(lane, st)
        if st != "idle":
            return
        msgs = [m for m in self.store.pending(lane) if m["task_id"] == task["id"]]
        if msgs and idle_for >= SETTLE_SECONDS:
            self._send(lane, task["session"], "[tower]\n" + "\n\n".join(m["text"] for m in msgs))
            self.store.mark_delivered(m["id"] for m in msgs)
            self.store.update_task(task["id"], last_prompt=self.clock())
            return
        waiting = self.store.open_items_for_task(task["id"]) > 0
        silent = (task.get("last_signal") or 0) < (task.get("last_prompt") or 0)
        if idle_for >= NUDGE_AFTER and silent and not waiting:
            n = task["nudges"]
            if n < NUDGE_LIMIT:
                self.store.push(lane, TOWER, "nudge", NUDGE, task["id"])
                self.store.update_task(task["id"], nudges=n + 1)
            elif n == NUDGE_LIMIT:
                self.store.push(HUB, TOWER, "stalled", f"Stalled: {lane} task #{task['id']} \"{task['title']}\" went "
                                f"quiet after {NUDGE_LIMIT} nudges. Its last words: `tower last {lane}`.", task["id"])
                self.store.update_task(task["id"], nudges=n + 1)

    def _start_queued(self, statuses: dict[str, str]) -> None:
        busy = {t["lane"] for t in self.store.tasks(("running", "wrapping"), limit=500)}
        free = self.max_lanes - len(busy)
        waiting = []
        for lane in self.team:
            nxt = self.store.next_queued(lane)
            if nxt and lane not in busy and not self.store.lane(lane).get("closing"):
                st = self.rt.status(lane)
                if st == "closed" or (st == "idle" and self.rt.owned(lane)):
                    statuses[lane] = st
                    waiting.append(nxt)
        for nxt in sorted(waiting, key=lambda t: t["id"])[:max(0, free)]:
            lane = nxt["lane"]
            try:
                if statuses[lane] == "idle":
                    self.rt.close(lane)  # a fresh session per task
                task = self.store.claim_task(nxt["id"])
                if task is None:
                    continue
                try:
                    sid = self.rt.open(lane, self.start_prompt(task))
                except Exception:
                    self.store.update_task(nxt["id"], state="queued", started=None, last_prompt=None)
                    raise
            except Exception as e:
                self.log(f"{lane}: task #{nxt['id']} failed to start: {e}")
                continue
            self.store.update_task(nxt["id"], session=sid)
            self.store.drop_pending(lane, ("tell", "nudge", "wrap"), keep_task=nxt["id"])
            self.idle_since.pop(lane, None)
            self.log(f"{lane}: task #{nxt['id']} started")

    def _revive(self, lane: str, task: dict[str, Any]) -> None:
        if task["respawns"] >= RESPAWN_LIMIT:
            if task["respawns"] == RESPAWN_LIMIT:
                self.store.push(HUB, TOWER, "stalled", f"Closed: {lane} task #{task['id']} closed {RESPAWN_LIMIT} times "
                                f"after reopening. `tower cancel {task['id']}` or reassign it.", task["id"])
                self.store.update_task(task["id"], respawns=RESPAWN_LIMIT + 1)
            return
        handed = bool(task.get("rotate_ready"))
        self._open_resume(lane, task, "handed off to a fresh session" if handed else "the session had closed")
        self.store.update_task(task["id"], respawns=task["respawns"] + (0 if handed else 1), rotate_ready=None)

    def _open_resume(self, lane: str, task: dict[str, Any], why: str) -> None:
        sid = self.rt.open(lane, self.resume_prompt(task, why))
        self.store.update_task(task["id"], session=sid, last_prompt=self.clock(), nudges=0)
        self.idle_since.pop(lane, None)
        self.log(f"{lane}: task #{task['id']} new session ({why})")

    # ---------- hub ----------

    def _hub(self, st: str) -> None:
        info = self.store.lane(HUB)
        if st == "closed":
            if info.get("enabled"):
                self.rt.open(HUB, self.hub_prompt())
                self.idle_since.pop(HUB, None)
                self.log("hub: opened")
            return
        idle_for = self._idle_for(HUB, st)
        if st == "busy":
            self._watch_stuck()
        if st != "idle" or not info.get("enabled"):
            return
        sid = info.get("session")
        msgs = self.store.pending(HUB)
        if msgs and sid and idle_for >= SETTLE_SECONDS and self.clock() - msgs[0]["created"] >= BATCH_SECONDS:
            lines = []
            for m in msgs:
                if m["kind"] == "memo":
                    lines.append(f"• Request #{m['id']} from {m['sender']}: {m['text']} "
                                 f"(answer with `tower memo reply {m['id']} \"...\"`)")
                else:
                    lines.append(f"• {m['text']}")
            self._send(HUB, sid, f"[tower] {len(msgs)} new\n" + "\n".join(lines) + f"\n{self.summary()}")
            self.store.mark_delivered(m["id"] for m in msgs)
            return
        if not msgs and sid and idle_for >= GOAL_IDLE_SECONDS and self._drive_goals(sid):
            return
        if (not msgs and sid and idle_for >= 30 and self.rt.owned(HUB)
                and self.rt.context_k(HUB, sid) >= HUB_ROTATE_K):
            self.rt.close(HUB)
            self.rt.open(HUB, self.hub_prompt())
            self.idle_since.pop(HUB, None)
            self.log("hub: context grew large, fresh session")

    def _drive_goals(self, sid: str) -> bool:
        goals = self.store.goals(("active",))
        if not goals or self.store.tasks(("queued", "running", "wrapping"), limit=1):
            return False
        closed = self.store.goal_tasks()
        mark = json.dumps([(g["id"], g["priority"], g["steps"], g["updated"], closed.get(g["id"], {}).get("closed", 0))
                           for g in goals])
        if mark == self.goals_told:
            return False
        self.goals_told = mark
        self._send(HUB, sid, self.goals_prompt(goals))
        self.log(f"hub: told about {len(goals)} active goal(s)")
        return True

    def _watch_stuck(self) -> None:
        msgs = self.store.pending(HUB)
        if not msgs:
            return
        oldest = msgs[0]
        waited = self.clock() - oldest["created"]
        if waited < HUB_STUCK_SECONDS or self.stuck_for == oldest["id"]:
            return
        self.stuck_for = oldest["id"]
        lang = self.cfg.lang.split("-")[0].lower()
        lang = lang if lang in STUCK_TITLE else "en"
        title = STUCK_TITLE[lang]
        if any(it["title"] == title for it in self.store.items(state="open", lane=HUB)):
            return  # already on the board (e.g. raised before a config reload)
        body = STUCK_BODY[lang].format(n=len(msgs), m=int(waited // 60))
        self.store.add_item(HUB, None, "review", title, body, [], [], audience=HUMAN)
        self.log(f"hub: busy with news waiting {int(waited // 60)} min, told humans")

    # ---------- infra ----------

    def _infra(self, st: str) -> None:
        self._idle_for(INFRA, st)
        info = self.store.lane(INFRA)
        if info.get("closing"):
            if st not in ("idle", "closed"):
                return
            if st == "idle" and self.rt.owned(INFRA):
                self.rt.close(INFRA)
                self.log("infra: wrapped, closed")
            self.store.finish_inbox(INFRA)
            if st == "idle":
                return
        if st != "closed":  # never inject a second item into a live session
            return
        msg = self.store.claim_inbox(INFRA)
        if msg is None:
            return
        if msg["delivered"] is not None:  # its session closed without wrapping
            n = self.store.retry_inbox(INFRA)
            if n > RESPAWN_LIMIT:
                if n == RESPAWN_LIMIT + 1:
                    self.notify([{"id": msg["id"], "lane": INFRA, "title": f"infra closed {RESPAWN_LIMIT} times on "
                                  f"message #{msg['id']}; skipped", "alert": True}])
                    self.store.push(HUB, TOWER, "stalled", f"infra gave up on message #{msg['id']} after "
                                    f"{RESPAWN_LIMIT} closed sessions.")
                self.store.finish_inbox(INFRA)
                return
        try:
            self.rt.open(INFRA, self.inbox_prompt(msg))
        except Exception as e:
            self.log(f"infra: message #{msg['id']} failed to open: {e}")
            self.store.mark_delivered([msg["id"]])
            return
        self.store.mark_delivered([msg["id"]])
        self.idle_since.pop(INFRA, None)
        self.log(f"infra: opened for message #{msg['id']}")
