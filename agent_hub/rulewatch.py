from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Any, Callable

from . import procs
from .store import HUMAN, TOWER, Store, StoreError

DEFAULT_DELAY_MINUTES = 10
DEFAULT_CARDS_PER_DAY = 3
CHECK_SECONDS = 60
GIT_SECONDS = 30
KEEP_POSTED = 50
SHOW_LINES = 6
LINE_CHARS = 160
KEYS = {"files", "delay_minutes", "cards_per_day"}
REF = re.compile(r"\b(memo|request|card|decision|task)\s*#(\d+)", re.I)
GROUP_ORDER = ("memo", "request", "card", "decision", "task")
WORDS = {
    "en": {"memo": "memo", "request": "request", "card": "card", "decision": "decision", "task": "task",
           "commit": "commit"},
    "ko": {"memo": "메모", "request": "요청", "card": "카드", "decision": "결정", "task": "작업", "commit": "커밋"},
    "ja": {"memo": "メモ", "request": "リクエスト", "card": "カード", "decision": "決定", "task": "タスク", "commit": "コミット"},
    "zh": {"memo": "备忘", "request": "请求", "card": "卡片", "decision": "决定", "task": "任务", "commit": "提交"},
    "es": {"memo": "memo", "request": "solicitud", "card": "tarjeta", "decision": "decisión", "task": "tarea",
           "commit": "commit"},
}
TEXT = {
    "en": {"title": "Shared rules changed · {files}{ref}", "files": "## What changed", "who": "## Whose work",
           "lines": "## Changed lines", "more": "… {n} more line(s)",
           "why": ("## Why everyone gets this\nThis is a notice, not a result card: a rule every lane and person "
                   "follows has changed, so it goes to everyone, whoever asked for the work."),
           "todo": "## What to do\nMark it seen once read. Anything odd: say so in the hub chat.",
           "nobody": "The commit messages name no memo, request or card.", "and": " and {n} more",
           "sent": "sent by {hub}", "add": "added", "del": "removed"},
    "ko": {"title": "공용 지침이 바뀌었어요 · {files}{ref}", "files": "## 무엇이 바뀌었나요",
           "who": "## 누구 일인가요", "lines": "## 바뀐 문장", "more": "… 그 밖에 {n}줄",
           "why": ("## 왜 모두에게 오나요\n결과 카드가 아니라, 모든 레인과 사람이 따르는 공용 지침이 바뀌었다는 "
                   "알림이에요. 누가 요청한 일이든 모두에게 가요."),
           "todo": "## 할 일\n읽었으면 '확인했어요'를 눌러 주세요. 이상한 점은 허브 채팅에 남겨 주세요.",
           "nobody": "커밋 메시지에 메모·요청·카드 번호가 없어요.", "and": " 외 {n}개", "sent": "보낸 허브 {hub}",
           "add": "추가", "del": "뺌"},
    "ja": {"title": "共有ルールが変わりました · {files}{ref}", "files": "## 変わったもの",
           "who": "## だれの作業か", "lines": "## 変わった行", "more": "… ほか {n} 行",
           "why": ("## なぜ全員に届くか\n結果のカードではなく、すべてのレーンと人が従う共有ルールが変わったという"
                   "お知らせです。だれが依頼した作業でも全員に届きます。"),
           "todo": "## すること\n読んだら「確認しました」を押してください。おかしな点はハブのチャットに書いてください。",
           "nobody": "コミットメッセージにメモ・リクエスト・カードの番号がありません。", "and": " ほか {n} 件",
           "sent": "送信元のハブ {hub}", "add": "追加", "del": "削除"},
    "zh": {"title": "共享规则已更改 · {files}{ref}", "files": "## 改了什么",
           "who": "## 是谁的工作", "lines": "## 改动的行", "more": "… 另有 {n} 行",
           "why": ("## 为什么所有人都会收到\n这不是结果卡片，而是通知：每个 lane 和每个人都要遵守的共享规则变了，"
                   "所以无论是谁提出的工作，都会发给所有人。"),
           "todo": "## 要做什么\n读完后标记为已看。有异常请在 hub 聊天里说明。",
           "nobody": "提交信息里没有写明备忘、请求或卡片编号。", "and": " 等 {n} 项",
           "sent": "由 {hub} 发送", "add": "新增", "del": "删除"},
    "es": {"title": "Reglas compartidas modificadas · {files}{ref}", "files": "## Qué cambió",
           "who": "## De quién es el trabajo", "lines": "## Líneas modificadas", "more": "… {n} línea(s) más",
           "why": ("## Por qué lo recibe todo el mundo\nEs un aviso, no una tarjeta de resultado: cambió una regla que "
                   "siguen todos los lanes y todas las personas, así que llega a todos, sea quien sea quien pidió el trabajo."),
           "todo": "## Qué hacer\nMárcala como vista cuando la leas. Si algo no cuadra, dilo en el chat del hub.",
           "nobody": "Los mensajes del commit no nombran ningún memo, solicitud ni tarjeta.", "and": " y {n} más",
           "sent": "enviado por {hub}", "add": "añadido", "del": "eliminado"},
}


def parse(raw: Any) -> dict[str, Any]:
    if raw is None or raw is False:
        return {}
    if not isinstance(raw, dict):
        raise ValueError('rule_watch must be an object: {"files": ["AGENTS.md"], "delay_minutes": 10}')
    unknown = set(raw) - KEYS
    if unknown:
        raise ValueError(f"rule_watch: unknown key {', '.join(sorted(unknown))} ({', '.join(sorted(KEYS))})")
    files = raw.get("files")
    if isinstance(files, str):
        files = [files]
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        raise ValueError("rule_watch.files must be a list of paths or patterns")
    files = [normal(f) for f in files if normal(f)]
    if not files:
        raise ValueError("rule_watch.files must name at least one file (`rule_watch` absent = off)")
    return {"files": files, "delay_minutes": whole(raw, "delay_minutes", DEFAULT_DELAY_MINUTES),
            "cards_per_day": whole(raw, "cards_per_day", DEFAULT_CARDS_PER_DAY, 1)}


def whole(raw: dict[str, Any], key: str, default: int, minimum: int = 0) -> int:
    v = raw.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        raise ValueError(f"rule_watch.{key} must be a whole number of at least {minimum}, got {v!r}")
    return v


def normal(path: str) -> str:
    return path.strip().strip("`").removeprefix("./").strip("/")


def matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    return fnmatch.fnmatchcase(path, pattern) and path.count("/") == pattern.count("/")


def is_rule(path: str, files: list[str]) -> bool:
    p = normal(path)
    return any(matches(p, pat) for pat in files)


def rule_rank(path: str, files: list[str]) -> tuple[int, str]:
    return next((i for i, pat in enumerate(files) if matches(path, pat)), len(files)), path


def touches_rules(path: str, files: list[str]) -> bool:
    p = normal(path)
    if not p:
        return False
    if is_rule(p, files):
        return True
    return any(pat.split("*", 1)[0].rstrip("/").startswith(p + "/") for pat in files)


def refs(text: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for word, n in REF.findall(text or ""):
        ref = (word.lower(), int(n))
        if ref not in out:
            out.append(ref)
    return out


def group_key(subject: str, body: str, sha: str) -> str:
    found = refs(f"{subject}\n{body}")
    for kind in GROUP_ORDER:
        for k, n in found:
            if k == kind:
                return f"{k}:{n}"
    return f"commit:{sha[:12]}"


def ref_label(r: str, lang: str = "en") -> str:
    kind, n = r.split(":")
    if kind == "commit":
        return n[:7]
    return f"{WORDS.get(lang, WORDS['en'])[kind]} #{n}"


class RuleWatch:
    def __init__(self, home: Path, store: Store, state_dir: Path, settings: dict[str, Any], lang: str = "en",
                 clock: Callable[[], float] | None = None, log: Callable[[str], None] = print,
                 person: Callable[[str], str | None] = lambda s: s):
        self.home, self.store, self.lang, self.log, self.person = home, store, lang, log, person
        self.files: list[str] = settings["files"]
        self.quiet = int(settings["delay_minutes"]) * 60
        self.per_day = int(settings["cards_per_day"])
        self.clock = clock or store.clock
        self.file = state_dir / "rulewatch.json"
        self.checked: float | None = None

    def git(self, *a: str) -> str | None:
        r = procs.capture(["git", "-C", str(self.home), "-c", "core.quotepath=off", *a], GIT_SECONDS)
        return r.stdout if r is not None and r.returncode == 0 else None

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, st: dict[str, Any]) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.file)

    def tick(self) -> None:
        now = self.clock()
        if self.checked is not None and now - self.checked < CHECK_SECONDS:
            return
        self.checked = now
        head = (self.git("rev-parse", "HEAD") or "").strip()
        if not head:
            return
        st = self.load()
        before = json.loads(json.dumps(st))
        mark = st.get("head")
        if not mark or self.git("merge-base", "--is-ancestor", mark, head) is None:
            st["head"] = head
        elif mark != head:
            self.collect(st, mark, head, now)
            st["head"] = head
        self.post_quiet(st, now)
        if st != before:
            self.save(st)

    def collect(self, st: dict[str, Any], mark: str, head: str, now: float) -> None:
        out = self.git("log", "--no-merges", "--reverse", "--format=%x1e%H%x1f%s%x1f%b%x1f", "--name-only",
                       f"{mark}..{head}")
        pending = st.setdefault("pending", {})
        for rec in (out or "").split("\x1e"):
            parts = rec.split("\x1f")
            if len(parts) < 4:
                continue
            sha, subject, body, names = parts[0].strip(), parts[1].strip(), parts[2], parts[3]
            files = [f.strip() for f in names.splitlines() if f.strip() and is_rule(f.strip(), self.files)]
            if not files:
                continue
            g = pending.setdefault(group_key(subject, body, sha), {"commits": []})
            g["commits"].append({"sha": sha, "subject": subject,
                                 "refs": [f"{k}:{n}" for k, n in refs(f"{subject}\n{body}")], "files": files})
            g["seen"] = now

    def post_quiet(self, st: dict[str, Any], now: float) -> None:
        pending, posted = st.setdefault("pending", {}), st.setdefault("posted", {})
        day = self.store.day(now)
        if st.get("day") != day:
            st["day"], st["count"] = day, 0
        for key in sorted(pending, key=lambda k: pending[k].get("seen", 0)):
            g = pending[key]
            if now - float(g.get("seen", 0)) < self.quiet:
                continue
            prev = posted.get(key)
            commits = [*prev.get("commits", []), *g["commits"]] if prev else g["commits"]
            if prev and self.revise(int(prev["item"]), key, commits):
                posted[key] = {"item": prev["item"], "commits": commits, "at": now}
                del pending[key]
                continue
            if int(st.get("count", 0)) >= self.per_day:
                continue
            iid = self.post(key, g["commits"])
            posted[key] = {"item": iid, "commits": g["commits"], "at": now}
            st["count"] = int(st.get("count", 0)) + 1
            del pending[key]
        if len(posted) > KEEP_POSTED:
            for k in sorted(posted, key=lambda k: posted[k].get("at", 0))[:len(posted) - KEEP_POSTED]:
                del posted[k]

    def revise(self, item_id: int, key: str, commits: list[dict[str, Any]]) -> bool:
        try:
            it = self.store.item(item_id)
        except StoreError:
            return False
        if it["state"] != "open":
            return False
        title, body = self.card(key, commits)
        self.store.revise_item(item_id, title=title, body=body)
        self.log(f"rules: card #{item_id} revised ({key})")
        return True

    def post(self, key: str, commits: list[dict[str, Any]]) -> int:
        title, body = self.card(key, commits)
        iid = self.store.add_item(TOWER, None, "review", title, body, [], [], audience=HUMAN)
        self.log(f"rules: card #{iid} for {key}")
        return iid

    def card(self, key: str, commits: list[dict[str, Any]]) -> tuple[str, str]:
        t = TEXT.get(self.lang, TEXT["en"])
        files: list[str] = []
        for c in commits:
            files += [f for f in c["files"] if f not in files]
        files.sort(key=lambda f: rule_rank(f, self.files))
        found: list[str] = []
        for c in commits:
            found += [r for r in c.get("refs", []) if r not in found]
        stats, shown = self.diff([c["sha"] for c in commits], files)
        head = files[0].rsplit("/", 1)[-1] + (t["and"].format(n=len(files) - 1) if len(files) > 1 else "")
        ref = "" if key.startswith("commit:") else f" ({ref_label(key, self.lang)})"
        title = t["title"].format(files=head, ref=ref)[:120]
        lines = [t["files"], *[f"- `{f}` (+{stats.get(f, (0, 0))[0]} −{stats.get(f, (0, 0))[1]})" for f in files], "",
                 t["who"]]
        lines.append("- " + " · ".join(self.ref_text(r) for r in found) if found else "- " + t["nobody"])
        lines += [f"- `{c['sha'][:7]}` {c['subject']}" for c in commits]
        lines += ["", t["lines"]]
        for f in files:
            lines += [f"### `{f}`", *(f"- {x}" for x in shown.get(f) or ["…"]), ""]
        lines += ["", t["why"], "", t["todo"]]
        return title, "\n".join(lines)

    def ref_text(self, r: str) -> str:
        kind, n = r.split(":")
        label = ref_label(r, self.lang)
        if kind != "memo":
            return label
        try:
            sender = str(self.store.message(int(n))["sender"])
        except StoreError:
            return label
        who = self.person(sender)
        hub = sender + (f" → {who}" if who and who != sender else "")
        return f"{label} ({TEXT.get(self.lang, TEXT['en'])['sent'].format(hub=hub)})"

    def diff(self, shas: list[str], files: list[str]) -> tuple[dict[str, tuple[int, int]], dict[str, list[str]]]:
        stats: dict[str, list[int]] = {}
        shown: dict[str, list[str]] = {}
        more: dict[str, int] = {}
        t = TEXT.get(self.lang, TEXT["en"])
        for sha in shas:
            out = self.git("show", "--format=", "--unified=0", "--no-color", sha, "--", *files) or ""
            cur = None
            for ln in out.splitlines():
                if ln.startswith("+++ "):
                    cur = ln[6:] if ln.startswith("+++ b/") else None
                    continue
                if cur is None or ln.startswith(("--- ", "@@", "diff ", "index ")) or ln[:1] not in ("+", "-"):
                    continue
                st = stats.setdefault(cur, [0, 0])
                st[0 if ln[0] == "+" else 1] += 1
                if not ln[1:].strip():
                    continue
                keep = shown.setdefault(cur, [])
                if len(keep) < SHOW_LINES:
                    body = ln[1:].strip()
                    keep.append(f"{t['add'] if ln[0] == '+' else t['del']} · "
                                f"{body[:LINE_CHARS]}{'…' if len(body) > LINE_CHARS else ''}")
                else:
                    more[cur] = more.get(cur, 0) + 1
        for f, n in more.items():
            shown[f].append(t["more"].format(n=n))
        return {f: (a, b) for f, (a, b) in stats.items()}, shown
