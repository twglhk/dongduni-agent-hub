from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from .config import HUB, INFRA, Config
from .rulewatch import is_rule, ref_label, touches_rules
from .store import HUMAN, TOWER, Store, StoreError, is_hub_name

CLEAR, WAIT, AFTER, GO, MERGE = "clear", "wait", "after", "go", "merge"
HOLDS = (WAIT, AFTER, MERGE)
MIN_WORDS = 2
LIMIT = 500
STOP = {"the", "and", "for", "with", "from", "into", "this", "that", "are", "was", "add", "fix", "new", "update",
        "memo", "task", "card", "request", "decision", "board", "hub", "infra", "lane", "rule", "rules", "file",
        "files", "change", "changes"}
WORD = re.compile(r"\w+")
FILE_WORD = re.compile(r"[\w.*\-]+(?:/[\w.*\-]+)+|[\w\-]+\.\w{1,5}")

LABELS = {
    "en": {GO: ("Go ahead", "It starts now despite the overlap; both may change the same file separately."),
           AFTER: ("After the earlier one", "It starts once the earlier work is finished, on top of its result."),
           MERGE: ("Merge", "It does not start; the hubs fold it into the earlier work and cancel it.")},
    "ko": {GO: ("그대로 진행", "겹쳐도 지금 시작해요. 두 일이 같은 파일을 따로 고칠 수 있어요."),
           AFTER: ("먼저 것 뒤에", "먼저 들어온 일이 끝난 뒤에 시작해요. 그 결과를 보고 고쳐요."),
           MERGE: ("합치기", "이 일은 시작하지 않아요. 허브가 먼저 일에 합치고 이 일을 취소해요.")},
    "ja": {GO: ("そのまま進める", "重なっていても今始めます。二つの作業が同じファイルを別々に変えることがあります。"),
           AFTER: ("先の作業のあとで", "先に入った作業が終わってから、その結果の上で始めます。"),
           MERGE: ("まとめる", "この作業は始めません。ハブが先の作業にまとめ、この作業を取り消します。")},
    "zh": {GO: ("照常进行", "尽管有重叠也现在开始；两项工作可能分别修改同一个文件。"),
           AFTER: ("排在先前那项之后", "等先前的工作完成后，在其结果之上开始。"),
           MERGE: ("合并", "这项工作不会开始；hub 会把它并入先前的工作并取消它。")},
    "es": {GO: ("Seguir adelante", "Empieza ahora pese al solapamiento; ambos pueden cambiar el mismo archivo por separado."),
           AFTER: ("Después de la anterior", "Empieza cuando termine el trabajo anterior, sobre su resultado."),
           MERGE: ("Fusionar", "No empieza; los hubs lo funden con el trabajo anterior y lo cancelan.")},
}
TEXT = {
    "en": {"title": "These two overlap · {new} ↔ {old}", "what": "## What to decide",
           "ask": "{who}'s {new} changes shared rules and overlaps open work of {others}. What should happen?",
           "new": "## The new work (waiting)", "old": "## The earlier work", "why": "Overlap: {why}",
           "files": "same file {files}", "words": "same topic words {words}",
           "change": ("## What changes\nThe new work does not start until this is answered. Work of one person only "
                      "meets the path lock, as before."),
           "opts": "## Options",
           "rec": "## Recommendation\nAfter the earlier one: changing on top of its result avoids two different edits "
                  "of the same lines.",
           "for": "A card for both {a} and {b}: the first answer decides.", "unknown": "someone"},
    "ko": {"title": "이 둘이 겹쳐요 · {new} ↔ {old}", "what": "## 무엇을 정하나요",
           "ask": "{who} 님의 새 일({new})이 공용 지침을 고쳐요. {others} 님의 아직 열린 일과 겹쳐요. 어떻게 할까요?",
           "new": "## 새로 들어온 일 (대기 중)", "old": "## 먼저 있던 일", "why": "겹치는 점: {why}",
           "files": "같은 파일 {files}", "words": "같은 주제 낱말 {words}",
           "change": ("## 무엇이 바뀌나요\n답이 올 때까지 새 일은 시작하지 않아요. 같은 사람의 일끼리는 지금처럼 "
                      "경로 잠금만 해요."),
           "opts": "## 선택지",
           "rec": "## 추천\n먼저 것 뒤에: 먼저 일의 결과 위에서 고치면 같은 문장을 두 번 다르게 바꾸는 일이 없어요.",
           "for": "{a} 님과 {b} 님 두 분께 보내는 카드예요. 먼저 오는 답이 정해요.", "unknown": "누군가"},
    "ja": {"title": "この二つが重なっています · {new} ↔ {old}", "what": "## 決めること",
           "ask": "{who} さんの {new} は共有ルールを変えます。{others} さんのまだ開いている作業と重なっています。どうしますか？",
           "new": "## 新しい作業 (待機中)", "old": "## 先にあった作業", "why": "重なっている点: {why}",
           "files": "同じファイル {files}", "words": "同じ話題の語 {words}",
           "change": ("## 何が変わるか\nこの質問に答えが来るまで、新しい作業は始まりません。同じ人の作業どうしは、"
                      "これまでどおりパスのロックだけです。"),
           "opts": "## 選択肢",
           "rec": "## おすすめ\n先の作業のあとで: 先の結果の上で直せば、同じ行を二通りに変えてしまうことがありません。",
           "for": "{a} さんと {b} さんの両方へのカードです。先に来た答えで決まります。", "unknown": "だれか"},
    "zh": {"title": "这两项工作重叠了 · {new} ↔ {old}", "what": "## 需要决定什么",
           "ask": "{who} 的 {new} 会修改共享规则，并与 {others} 尚未结束的工作重叠。该怎么办？",
           "new": "## 新的工作（等待中）", "old": "## 先前的工作", "why": "重叠之处：{why}",
           "files": "同一个文件 {files}", "words": "相同的主题词 {words}",
           "change": ("## 会发生什么\n在得到答复之前，新的工作不会开始。同一个人的工作之间，仍然只受路径锁限制。"),
           "opts": "## 选项",
           "rec": "## 建议\n排在先前那项之后：在先前的结果之上修改，就不会对同样的行做两种不同的改动。",
           "for": "这是发给 {a} 和 {b} 两位的卡片：先到的答复为准。", "unknown": "某人"},
    "es": {"title": "Estos dos se solapan · {new} ↔ {old}", "what": "## Qué decidir",
           "ask": "{new} de {who} cambia reglas compartidas y se solapa con trabajo aún abierto de {others}. ¿Qué hacemos?",
           "new": "## El trabajo nuevo (en espera)", "old": "## El trabajo anterior", "why": "Solapamiento: {why}",
           "files": "el mismo archivo {files}", "words": "las mismas palabras de tema {words}",
           "change": ("## Qué cambia\nEl trabajo nuevo no empieza hasta que se responda. El trabajo de una sola persona "
                      "sigue sujeto solo al bloqueo de rutas, como antes."),
           "opts": "## Opciones",
           "rec": "## Recomendación\nDespués de la anterior: cambiar sobre su resultado evita dos ediciones distintas "
                  "de las mismas líneas.",
           "for": "Una tarjeta para {a} y {b}: decide la primera respuesta.", "unknown": "alguien"},
}


def title_of(text: str) -> str:
    t = str(text or "").strip()
    m = re.match(r"\[([^\]]+)\]", t)
    if m:
        return m.group(1)
    first = next((ln.strip() for ln in t.splitlines() if ln.strip()), "")
    return first[:80]


def words(title: str) -> set[str]:
    return {w for w in WORD.findall(title.lower()) if len(w) >= 2 and not w.isdigit() and w not in STOP}


def named_files(text: str, files: list[str]) -> set[str]:
    literal = [f for f in files if "*" not in f]
    out = set()
    for tok in FILE_WORD.findall(str(text or "")):
        p = tok.strip("`").rstrip(".,:;()").removeprefix("./")
        if is_rule(p, files):
            out.add(p)
        elif "/" not in p:
            out.update(f for f in literal if f.rsplit("/", 1)[-1] == p)
    return out


def held_text(r: dict[str, Any]) -> str:
    if r["state"] == AFTER:
        return "overlap: waits for " + ", ".join(r["others"] or ["the earlier work"]) + " to finish"
    if r["state"] == MERGE:
        return f"overlap: card #{r['items'][0]} said merge; the hub cancels it"
    return f"overlap: waits for the answer on card #{r['items'][0]}"


def hold_of(s: Store, work: str) -> dict[str, Any] | None:
    r = s.overlap(work)
    return r if r and r["state"] in HOLDS else None


def choice_key(it: dict[str, Any]) -> str | None:
    if it.get("verdict") != "ok":
        return None
    picked = str(it.get("choice") or "")
    if not picked:
        return AFTER
    for lb in LABELS.values():
        for k, (label, _) in lb.items():
            if picked == label:
                return k
    return None


class Overlaps:
    def __init__(self, cfg: Config, store: Store, log: Callable[[str], None] = print):
        self.cfg, self.store, self.log = cfg, store, log
        self.files: list[str] = list(cfg.rule_watch.get("files", []))
        self.lang = cfg.lang if cfg.lang in TEXT else "en"

    def rule_paths(self, paths: list[str] | None) -> list[str]:
        return [p for p in (paths or []) if touches_rules(p, self.files)]

    def person_of_sender(self, sender: str, members: set[str]) -> str | None:
        ln = self.cfg.person_hubs.get(sender)
        if ln:
            return ln.owner or None
        return sender if sender in members else None

    def owner_of_hub(self, hub: str | None) -> str | None:
        ln = self.cfg.person_hubs.get(hub or "")
        return ln.owner if ln and ln.owner else None

    def works(self) -> list[dict[str, Any]]:
        from .engine import message_paths, task_paths
        s, out = self.store, []
        members = {str(m["name"]) for m in s.members()}
        sessions = self.cfg.infra_sessions() if INFRA in self.cfg.lanes else []
        held = s.held_messages(sessions)
        queued = s.inbox_queue(INFRA) if sessions else []
        for m in [*held.values(), *queued]:
            if m.get("kind") != "memo":
                continue
            ps = message_paths(m)
            sender = str(m.get("sender") or "")
            out.append({"key": f"memo:{m['id']}", "person": self.person_of_sender(sender, members), "title": title_of(m["text"]),
                        "paths": None if ps is None or ps == ["*"] else ps, "created": float(m["created"]),
                        "live": any(m["id"] == h["id"] for h in held.values()), "label": ref_label(f"memo:{m['id']}", self.lang)})
        for t in s.tasks(("queued", "running", "wrapping"), limit=LIMIT):
            who = t.get("requester") or self.owner_of_hub(t.get("hub"))
            out.append({"key": f"task:{t['id']}", "person": who, "title": t["title"], "paths": task_paths(t),
                        "created": float(t["created"]), "live": t["state"] != "queued",
                        "label": ref_label(f"task:{t['id']}", self.lang)})
        for it in s.items(state="open", limit=LIMIT):
            if it["kind"] != "decision" or it["lane"] == TOWER:
                continue
            who = it.get("assignee") or s.requester_of_task(it.get("task_id")) or self.owner_of_hub(it["lane"])
            out.append({"key": f"item:{it['id']}", "person": who, "title": it["title"],
                        "paths": sorted(named_files(f"{it['title']}\n{it['body']}", self.files)),
                        "created": float(it["created"]), "live": False,
                        "label": ref_label(f"card:{it['id']}", self.lang)})
        return out

    def scan(self) -> None:
        from .engine import paths_overlap
        works = self.works()
        open_keys = {w["key"] for w in works}
        for r in self.store.overlaps((AFTER,)):
            if not any(o in open_keys for o in r["others"]):
                self.store.put_overlap(r["work"], GO)
                self.log(f"overlap: {r['work']} goes, what it waited for has finished")
        for r in self.store.overlaps(HOLDS):
            if r["work"] not in open_keys:
                self.retire(r)
        for w in sorted(works, key=lambda w: w["created"]):
            if w["key"].startswith("item:") or w["live"] or self.store.overlap(w["key"]):
                continue
            mine = self.rule_paths(w["paths"])
            if not mine:
                continue
            hits = []
            for o in works:
                if o["key"] == w["key"] or o["created"] >= w["created"] or not w["person"] or not o["person"] \
                        or o["person"] == w["person"]:
                    continue
                same = sorted({p for p in mine for q in self.rule_paths(o["paths"]) if paths_overlap([p], [q])})
                common = sorted(words(w["title"]) & words(o["title"]))
                shared = common if len(common) >= MIN_WORDS else []
                if same or shared:
                    hits.append((o, same, shared))
            if not hits:
                self.store.put_overlap(w["key"], CLEAR)
                continue
            ids = self.post(w, hits)
            self.store.put_overlap(w["key"], WAIT, ids, [o["key"] for o, _, _ in hits])
            self.log(f"overlap: {w['key']} waits on card #{ids[0]}")

    def retire(self, r: dict[str, Any]) -> None:
        self.store.put_overlap(r["work"], CLEAR)
        self.close_cards(r["items"], f"{r['work']} is no longer open")

    def close_cards(self, ids: Iterable[int], why: str) -> None:
        for iid in ids:
            try:
                it = self.store.item(iid)
            except StoreError:
                continue
            if it["state"] == "open":
                now = self.store.clock()
                self.store.update_item(iid, state="merged", verdict="merged", note=why, decided_by=TOWER, decided=now,
                                       delivered=now)

    def post(self, w: dict[str, Any], hits: list[tuple[dict[str, Any], list[str], list[str]]]) -> list[int]:
        t, lb = TEXT[self.lang], LABELS[self.lang]
        olds = [o for o, _, _ in hits]
        people = list(dict.fromkeys(o["person"] for o in olds))
        title = t["title"].format(new=w["label"], old=", ".join(o["label"] for o in olds))[:120]
        lines = [t["what"], t["ask"].format(who=w["person"] or t["unknown"], new=w["label"], others=", ".join(people)),
                 t["for"].format(a=w["person"], b=", ".join(people)), "", t["new"],
                 f"- {w['label']} · {w['person']} · {w['title']}", f"  - paths: {', '.join(w['paths'] or [])}", "",
                 t["old"]]
        for o, same, common in hits:
            why = [t["files"].format(files=", ".join(f"`{f}`" for f in same))] if same else []
            why += [t["words"].format(words=", ".join(common))] if common else []
            lines += [f"- {o['label']} · {o['person']} · {o['title']}", "  - " + t["why"].format(why=" · ".join(why))]
        lines += ["", t["change"], "", t["opts"], *[f"- **{lb[k][0]}**: {lb[k][1]}" for k in (GO, AFTER, MERGE)], "",
                  t["rec"]]
        options = [{"label": lb[k][0], "desc": lb[k][1], **({"recommended": True} if k == AFTER else {})}
                   for k in (GO, AFTER, MERGE)]
        addressed = [w["person"], *people] if self.cfg.requester or self.cfg.person_hubs else [None]
        return [self.store.add_item(TOWER, None, "decision", title, "\n".join(lines), options, [], audience=HUMAN,
                                    assignee=who) for who in dict.fromkeys(addressed)]

    def route(self, it: dict[str, Any]) -> list[tuple[str, str]] | None:
        if it.get("lane") != TOWER or it.get("kind") != "decision":
            return None
        r = self.store.overlap_of_item(int(it["id"]))
        if r is None:
            return None
        pick = choice_key(it)
        note = str(it.get("note") or "").strip()
        if pick:
            self.store.put_overlap(r["work"], pick)
        self.close_cards([i for i in r["items"] if i != it["id"]], f"answered on #{it['id']}")
        did = {GO: "it starts now", AFTER: "it starts once the earlier work finishes",
               MERGE: "it does not start: fold it into the earlier work, then cancel it "
                      "(`dah memo cancel ID --why ...` for a memo, `dah cancel TASK` for a task)"}
        if pick:
            what = did[pick]
        elif it["verdict"] == "hold":
            what = "on hold: it keeps waiting"
        else:
            what = ("no option picked; it keeps waiting. Do what the person wrote, then `dah overlap "
                    f"{it['id']} go|after|merge`")
        line = (f"Overlap card #{it['id']} on {r['work']}: {it.get('choice') or it['verdict']} by {it['decided_by']}"
                + (f", note: {note}" if note else "") + f" → {what}.")
        return [(hub, line) for hub in self.hubs_of([r["work"], *r["others"]])]

    def hubs_of(self, keys: list[str]) -> list[str]:
        out: list[str] = []
        for k in keys:
            kind, _, n = k.partition(":")
            hub = None
            if kind == "memo":
                try:
                    hub = str(self.store.message(int(n))["sender"])
                except StoreError:
                    hub = None
            elif kind == "task":
                hub = self.store.hub_for_task(int(n))
            elif kind == "item":
                try:
                    lane = str(self.store.item(int(n))["lane"])
                except StoreError:
                    lane = ""
                hub = lane if is_hub_name(lane) else None
            hub = hub if hub and self.cfg.is_hub(hub) else HUB
            if hub not in out:
                out.append(hub)
        return out


def decide_by_hub(cfg: Config, s: Store, item_id: int, how: str, me: str) -> str:
    if how not in (GO, AFTER, MERGE):
        raise ValueError("go, after or merge")
    r = s.overlap_of_item(item_id)
    if r is None:
        raise ValueError(f"#{item_id} is not an overlap card")
    if not any(s.item(i)["state"] != "open" for i in r["items"]):
        raise ValueError(f"card #{item_id} is still open: the person answers it first")
    if cfg.person_hubs and me not in Overlaps(cfg, s).hubs_of([r["work"], *r["others"]]):
        raise ValueError(f"{r['work']} and the work it waits on came through other hubs")
    s.put_overlap(r["work"], how)
    return str(r["work"])
