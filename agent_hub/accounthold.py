from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import accounts
from .config import Config, base_lane
from .store import HUMAN, TOWER, Store, StoreError

TEXT = {
    "en": {"title": "Your Claude login ended · please reconnect",
           "body": ("## What to do\n{m}'s Claude login has ended. Tap your name in the top bar → **My Claude account** "
                    "and reconnect.\n\n## What changes\n{m}'s work never runs on the shared account. "
                    "Once reconnected, the work below starts by itself.\n\n## Held work\n{works}"),
           "done": "\n\n## Reconnected\nThe held work has started.",
           "wait": "waiting for {m} to sign in again", "n": " · {n} held",
           "task": "task #{id} {t}", "memo": "infra memo #{id} {t}", "hub": "{m}'s hub"},
    "ko": {"title": "Claude 로그인이 끝났어요 · 다시 연결해 주세요",
           "body": ("## 무엇을 해야 하나요\n{m} 님의 Claude 로그인이 끝났어요. 위 줄의 이름 → **내 Claude 계정**에서 "
                    "다시 연결해 주세요.\n\n## 무엇이 바뀌나요\n{m} 님의 일은 공용 계정으로 돌리지 않아요. "
                    "다시 연결하면 아래 일이 저절로 시작돼요.\n\n## 멈춘 일\n{works}"),
           "done": "\n\n## 다시 연결됐어요\n멈췄던 일이 시작됐어요.",
           "wait": "{m} 님이 Claude에 다시 로그인하기를 기다려요", "n": " · 일 {n}개",
           "task": "작업 #{id} {t}", "memo": "인프라 메모 #{id} {t}", "hub": "{m} 님의 허브"},
    "ja": {"title": "Claude のログインが切れました · 再接続してください",
           "body": ("## すること\n{m} さんの Claude のログインが切れました。上の行の名前 → **自分の Claude アカウント**から"
                    "再接続してください。\n\n## 何が変わるか\n{m} さんの作業は共用アカウントでは動かしません。"
                    "再接続すると、下の作業が自動的に始まります。\n\n## 止まっている作業\n{works}"),
           "done": "\n\n## 再接続されました\n止まっていた作業が始まりました。",
           "wait": "{m} さんが Claude に再ログインするのを待っています", "n": " · {n} 件",
           "task": "タスク #{id} {t}", "memo": "infra メモ #{id} {t}", "hub": "{m} さんのハブ"},
    "zh": {"title": "Claude 登录已失效 · 请重新连接",
           "body": ("## 需要做什么\n{m} 的 Claude 登录已失效。点击顶部栏的名字 → **我的 Claude 账号**，重新连接。"
                    "\n\n## 会发生什么\n{m} 的工作不会改用共享账号运行。重新连接后，下面的工作会自动开始。"
                    "\n\n## 暂停的工作\n{works}"),
           "done": "\n\n## 已重新连接\n暂停的工作已开始。",
           "wait": "等待 {m} 重新登录 Claude", "n": " · {n} 项",
           "task": "任务 #{id} {t}", "memo": "infra 备忘 #{id} {t}", "hub": "{m} 的 hub"},
    "es": {"title": "Tu sesión de Claude terminó · vuelve a conectar",
           "body": ("## Qué hacer\nLa sesión de Claude de {m} terminó. Toca tu nombre en la barra superior → **Mi cuenta de "
                    "Claude** y vuelve a conectar.\n\n## Qué cambia\nEl trabajo de {m} nunca se ejecuta con la cuenta "
                    "compartida. Al reconectar, el trabajo de abajo empieza solo.\n\n## Trabajo detenido\n{works}"),
           "done": "\n\n## Reconectado\nEl trabajo detenido ha empezado.",
           "wait": "esperando a que {m} inicie sesión de nuevo en Claude", "n": " · {n} detenidos",
           "task": "tarea #{id} {t}", "memo": "memo de infra #{id} {t}", "hub": "hub de {m}"},
}

SEEN_EVERY = 60.0


def _lang(lang: str) -> dict[str, str]:
    return TEXT.get(lang.split("-")[0].lower(), TEXT["en"])


def hold_of(s: Store, work: str) -> dict[str, Any] | None:
    return s.account_hold(work)


def held_text(member: str, lang: str = "en") -> str:
    return _lang(lang)["wait"].format(m=member)


def shown_owner(owner: str | None, only: str | None) -> str | None:
    if owner is None or only is None or owner == only:
        return owner
    return ""


def lane_line(s: Store, lane: str, lang: str = "en", only: str | None = None) -> str | None:
    mine = [r for r in s.account_holds() if base_lane(r["lane"]) == base_lane(lane) and (only is None or r["member"] == only)]
    if not mine:
        return None
    t = _lang(lang)
    who = list(dict.fromkeys(r["member"] for r in mine))
    return ", ".join(t["wait"].format(m=m) for m in who) + (t["n"].format(n=len(mine)) if len(mine) > 1 else "")


def work_key(lane: str, task: dict[str, Any] | None = None, msg: dict[str, Any] | None = None) -> str:
    if task is not None:
        return f"task:{task['id']}"
    if msg is not None:
        return f"memo:{msg['id']}"
    return f"hub:{lane}"


def _title(lane: str, task: dict[str, Any] | None, msg: dict[str, Any] | None) -> str:
    if task is not None:
        return str(task.get("title") or "")
    if msg is not None:
        first = next((x.strip() for x in str(msg.get("text") or "").splitlines() if x.strip()), "")
        return first[:80]
    return lane


class Gate:
    def __init__(self, cfg: Config, store: Store, log: Callable[[str], None] = print, root: Path | None = None,
                 probe: accounts.Probe | None = None):
        self.cfg, self.store, self.log, self.root, self.probe = cfg, store, log, root, probe
        self.seen_at = float("-inf")

    def launch(self, lane: str, task: dict[str, Any] | None = None, msg: dict[str, Any] | None = None) -> accounts.Launch:
        return accounts.launch_for(self.cfg, self.store, lane, task, msg, self.root, self.probe)

    def check(self, lane: str, task: dict[str, Any] | None = None,
              msg: dict[str, Any] | None = None) -> accounts.Launch | None:
        la = self.launch(lane, task, msg)
        if not la.held:
            return la
        self._hold(work_key(lane, task, msg), str(la.member), lane, _title(lane, task, msg))
        return None

    def opened(self, lane: str, session: str | None, la: accounts.Launch | None) -> None:
        if self.cfg.claude_accounts:
            self.store.set_session_account(lane, session, la.owner if la else None)

    def _hold(self, work: str, member: str, lane: str, title: str) -> None:
        if self.store.hold_account_work(work, member, lane, title):
            self.log(f"{lane}: {work} held: {member}'s Claude login has ended")
            self._ask(member)

    def _works(self, member: str) -> str:
        t = _lang(self.cfg.lang)
        out = []
        for r in self.store.account_holds():
            if r["member"] != member:
                continue
            kind, _, wid = r["work"].partition(":")
            line = t["hub"].format(m=member) if kind == "hub" else t[kind].format(id=wid, t=f"\"{r['title']}\"")
            out.append(f"- {line}")
        return "\n".join(out) or "-"

    def _ask(self, member: str) -> None:
        t = _lang(self.cfg.lang)
        body = t["body"].format(m=member, works=self._works(member))
        known = self.store.account_ask(member)
        if known is not None:
            try:
                if self.store.item(known)["state"] == "open":
                    self.store.revise_item(known, None, body, None, None)
                    return
            except StoreError:
                self.log(f"{member}: account card #{known} is gone, posting a new one")
        iid = self.store.add_item(self.cfg.hub_of(member), None, "review", t["title"], body, [], [], audience=HUMAN,
                                  assignee=member)
        self.store.set_account_ask(member, iid)
        self.log(f"{member}: Claude login ended, card #{iid}")

    def poll(self) -> None:
        if not self.cfg.claude_accounts:
            return
        now = self.store.clock()
        if now - self.seen_at >= SEEN_EVERY:
            self.seen_at = now
            for m in self.store.members():
                if accounts.config_dir(m["name"], self.root):
                    self.store.mark_account_seen(m["name"])
        held = self.store.account_holds()
        if not held:
            return
        gone: list[str] = []
        released: set[str] = set()
        for r in held:
            if not self._waiting(r["work"]):
                gone.append(r["work"])
                continue
            if r["member"] in released:
                gone.append(r["work"])
            elif accounts.state(accounts.person(self.cfg, self.store, r["member"]), self.store, self.root, self.probe) != accounts.ENDED:
                released.add(r["member"])
                gone.append(r["work"])
        if not gone:
            return
        self.store.drop_account_holds(gone)
        for m in released:
            self.log(f"{m}: Claude account usable again, held work released")
            self._close_ask(m)
        left = {r["member"] for r in self.store.account_holds()}
        for m in {r["member"] for r in held} - left - released:
            self._close_ask(m)

    def _waiting(self, work: str) -> bool:
        kind, _, wid = work.partition(":")
        try:
            if kind == "task":
                return self.store.task(int(wid))["state"] in ("queued", "running", "wrapping")
            if kind == "memo":
                return self.store.message(int(wid))["delivered"] is None
        except (StoreError, ValueError):
            return False
        return self.cfg.is_hub(wid) and bool(self.store.lane(wid).get("enabled"))

    def _close_ask(self, member: str) -> None:
        known = self.store.account_ask(member)
        self.store.drop_account_ask(member)
        if known is None:
            return
        try:
            it = self.store.item(known)
        except StoreError:
            return
        if it["state"] != "open":
            return
        now = self.store.clock()
        self.store.update_item(it["id"], body=it["body"] + _lang(self.cfg.lang)["done"], state="decided",
                               verdict="ok", decided_by=TOWER, decided=now, delivered=now, note="reconnected")
