"""Tower state in one SQLite file: tasks, board items, message queues, reports, lanes, members.

An item row is the single source of truth for a decision. Messages only say whom to wake and what to tell:
`target` is a lane id (hub, infra, a team lane) or `human` (replies shown on the board).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Iterable

HUB = "hub"
INFRA = "infra"
HUMAN = "human"
TOWER = "tower"

TASK_LIVE = ("running", "wrapping")
GOAL_STATES = ("active", "paused", "done", "dropped")
GOAL_RANKED = ("active", "paused")
VERDICTS = ("ok", "no", "hold", "answer")  # answer = a human wrote their own answer; the note is the decision
MULTI_SEP = " + "

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY,
  lane TEXT NOT NULL,
  title TEXT NOT NULL,
  brief TEXT NOT NULL,
  state TEXT NOT NULL,              -- queued | running | wrapping | closed | cancelled
  session TEXT,
  created REAL NOT NULL,
  started REAL,
  closed REAL,
  last_prompt REAL,                 -- when the tower last wrote into this task's session
  last_signal REAL,                 -- when the lane last reported or posted an item
  nudges INTEGER NOT NULL DEFAULT 0,
  respawns INTEGER NOT NULL DEFAULT 0,
  handoff TEXT,
  rotate_ready REAL,
  goal_id INTEGER                   -- the goal this task serves, if any
);
CREATE TABLE IF NOT EXISTS goals(
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  priority INTEGER NOT NULL,        -- 1 = first; only active and paused goals are ranked
  state TEXT NOT NULL,              -- active | paused | done | dropped
  steps TEXT NOT NULL DEFAULT '[]', -- the plan: [{"text", "done"}]; progress = done / all
  note TEXT,
  created_by TEXT NOT NULL,
  created REAL NOT NULL,
  updated REAL NOT NULL,
  closed REAL
);
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY,
  task_id INTEGER,
  lane TEXT NOT NULL,
  kind TEXT NOT NULL,               -- decision | review
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  options TEXT NOT NULL DEFAULT '[]',
  multi INTEGER NOT NULL DEFAULT 0,
  attachments TEXT NOT NULL DEFAULT '[]',
  audience TEXT NOT NULL,           -- hub | human
  assignee TEXT,                    -- member name, when the hub routes it to one person
  escalation TEXT,
  state TEXT NOT NULL,              -- open | decided | merged
  verdict TEXT,
  choice TEXT,
  note TEXT,
  basis TEXT,
  decided_by TEXT,
  decided REAL,
  delivered REAL,
  notified REAL,
  created REAL NOT NULL,
  question TEXT                     -- live ask moved to the board: "<question id>#<index>"
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY,
  target TEXT NOT NULL,
  sender TEXT NOT NULL,
  kind TEXT NOT NULL,
  task_id INTEGER,
  item_id INTEGER,
  text TEXT NOT NULL,
  created REAL NOT NULL,
  delivered REAL
);
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY,
  task_id INTEGER,
  lane TEXT NOT NULL,
  kind TEXT NOT NULL,               -- done | blocked | note | handoff | wrapped
  text TEXT NOT NULL,
  attachments TEXT NOT NULL DEFAULT '[]',
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lanes(
  lane TEXT PRIMARY KEY,
  session TEXT,
  pid INTEGER,                      -- process the tower started for this lane (opencode server or headless run)
  opened REAL,
  status TEXT,
  seen REAL,
  closing REAL,                     -- the lane wrapped; close it once idle
  enabled INTEGER,                  -- hub: keep it running
  active_message INTEGER,           -- infra: the one message this session owns
  active_retries INTEGER DEFAULT 0,
  doing TEXT,
  doing_at REAL
);
CREATE TABLE IF NOT EXISTS members(
  name TEXT PRIMARY KEY,
  role TEXT NOT NULL DEFAULT 'member',  -- owner | member
  ident TEXT,                           -- identity from the proxy header (auth.mode=header), e.g. an email
  token_hash TEXT,                      -- sha256 of the login token (auth.mode=token)
  created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_pending ON messages(target, delivered);
CREATE INDEX IF NOT EXISTS items_state ON items(state, audience);
CREATE INDEX IF NOT EXISTS tasks_lane ON tasks(lane, state);
"""


class StoreError(Exception):
    pass


def _id(cur: sqlite3.Cursor) -> int:
    if cur.lastrowid is None:
        raise StoreError("insert failed")
    return cur.lastrowid


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


def _one(cur: sqlite3.Cursor) -> dict[str, Any] | None:
    r = cur.fetchone()
    return dict(r) if r else None


def item_view(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["options"] = json.loads(out["options"] or "[]")
    out["attachments"] = json.loads(out["attachments"] or "[]")
    out["multi"] = bool(out.get("multi"))
    return out


def goal_view(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["steps"] = json.loads(out["steps"] or "[]")
    done = sum(1 for st in out["steps"] if st.get("done"))
    out["steps_done"] = done
    out["progress"] = 100 if out["state"] == "done" else round(100 * done / len(out["steps"])) if out["steps"] else 0
    return out


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as con:
            con.executescript(SCHEMA)
            cols = {r[1] for r in con.execute("PRAGMA table_info(tasks)")}
            if "goal_id" not in cols:  # databases made before goals existed
                con.execute("ALTER TABLE tasks ADD COLUMN goal_id INTEGER")

    def conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=15000")
        return con

    # ---------- messages ----------

    def push(self, target: str, sender: str, kind: str, text: str, task_id: int | None = None,
             item_id: int | None = None, con: sqlite3.Connection | None = None) -> int:
        sql = "INSERT INTO messages(target, sender, kind, task_id, item_id, text, created) VALUES(?,?,?,?,?,?,?)"
        args = (target, sender, kind, task_id, item_id, text, self.clock())
        if con is not None:
            return _id(con.execute(sql, args))
        with self.conn() as c:
            return _id(c.execute(sql, args))

    def pending(self, target: str) -> list[dict[str, Any]]:
        with self.conn() as con:
            return _rows(con.execute("SELECT * FROM messages WHERE target=? AND delivered IS NULL ORDER BY id", (target,)))

    def mark_delivered(self, ids: Iterable[int]) -> None:
        ids = list(ids)
        if ids:
            with self.conn() as con:
                con.execute(f"UPDATE messages SET delivered=? WHERE id IN ({','.join('?' * len(ids))})",
                            (self.clock(), *ids))

    def drop_pending(self, target: str, kinds: Iterable[str], keep_task: int | None = None) -> None:
        kinds = list(kinds)
        with self.conn() as con:
            con.execute(f"UPDATE messages SET delivered=? WHERE target=? AND delivered IS NULL "
                        f"AND kind IN ({','.join('?' * len(kinds))}) AND COALESCE(task_id, -1) != ?",
                        (self.clock(), target, *kinds, -2 if keep_task is None else keep_task))

    def message(self, message_id: int) -> dict[str, Any]:
        with self.conn() as con:
            row = _one(con.execute("SELECT * FROM messages WHERE id=?", (message_id,)))
        if not row:
            raise StoreError(f"message #{message_id} not found")
        return row

    # ---------- infra inbox: one message per session ----------

    def claim_inbox(self, lane: str) -> dict[str, Any] | None:
        with self.conn() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                con.execute("INSERT OR IGNORE INTO lanes(lane) VALUES(?)", (lane,))
                active = con.execute("SELECT active_message FROM lanes WHERE lane=?", (lane,)).fetchone()[0]
                if active is None:
                    row = _one(con.execute("SELECT id FROM messages WHERE target=? AND delivered IS NULL "
                                           "ORDER BY id LIMIT 1", (lane,)))
                    if row:
                        active = row["id"]
                        con.execute("UPDATE lanes SET active_message=?, active_retries=0 WHERE lane=?", (active, lane))
                msg = _one(con.execute("SELECT * FROM messages WHERE id=?", (active,))) if active is not None else None
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return msg

    def retry_inbox(self, lane: str) -> int:
        with self.conn() as con:
            con.execute("UPDATE lanes SET active_retries=COALESCE(active_retries, 0)+1 WHERE lane=?", (lane,))
            return int(con.execute("SELECT active_retries FROM lanes WHERE lane=?", (lane,)).fetchone()[0])

    def finish_inbox(self, lane: str) -> None:
        with self.conn() as con:
            con.execute("UPDATE messages SET delivered=? WHERE delivered IS NULL "
                        "AND id=(SELECT active_message FROM lanes WHERE lane=?)", (self.clock(), lane))
            con.execute("UPDATE lanes SET active_message=NULL, active_retries=0, closing=NULL WHERE lane=?", (lane,))

    # ---------- memos: humans -> infra / hub; replies go to target=human with item_id = memo id ----------

    def memos(self, target: str, limit: int = 30, offset: int = 0) -> list[dict[str, Any]]:
        with self.conn() as con:
            rows = _rows(con.execute("SELECT id, text, sender, created, delivered FROM messages "
                                     "WHERE target=? AND kind='memo' ORDER BY id DESC LIMIT ? OFFSET ?",
                                     (target, limit, offset)))
            if not rows:
                return rows
            ids = [r["id"] for r in rows]
            replies = _rows(con.execute(
                f"SELECT item_id, text, created FROM messages WHERE target=? AND kind='memo_reply' "
                f"AND item_id IN ({','.join('?' * len(ids))}) ORDER BY id", (HUMAN, *ids)))
        for r in rows:
            r["replies"] = [{"text": x["text"], "created": x["created"]} for x in replies if x["item_id"] == r["id"]]
        return rows

    def memo(self, memo_id: int) -> dict[str, Any]:
        with self.conn() as con:
            row = _one(con.execute("SELECT * FROM messages WHERE id=? AND kind='memo'", (memo_id,)))
        if not row:
            raise StoreError(f"memo #{memo_id} not found")
        return row

    def reply_memo(self, memo_id: int, text: str, by: str) -> None:
        memo = self.memo(memo_id)
        if memo["target"] != by:
            raise StoreError(f"memo #{memo_id} was sent to {memo['target']}, not {by}")
        now = self.clock()
        with self.conn() as con:
            con.execute("INSERT INTO messages(target, sender, kind, item_id, text, created, delivered) "
                        "VALUES(?,?,?,?,?,?,?)", (HUMAN, by, "memo_reply", memo_id, text, now, now))

    # ---------- tasks ----------

    def add_task(self, lane: str, title: str, brief: str, goal_id: int | None = None) -> int:
        with self.conn() as con:
            return _id(con.execute("INSERT INTO tasks(lane, title, brief, state, created, goal_id) VALUES(?,?,?,?,?,?)",
                                   (lane, title, brief, "queued", self.clock(), goal_id)))

    def task(self, task_id: int) -> dict[str, Any]:
        with self.conn() as con:
            row = _one(con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)))
        if not row:
            raise StoreError(f"task #{task_id} not found")
        return row

    def live_task(self, lane: str) -> dict[str, Any] | None:
        with self.conn() as con:
            return _one(con.execute("SELECT * FROM tasks WHERE lane=? AND state IN ('running','wrapping') "
                                    "ORDER BY id DESC LIMIT 1", (lane,)))

    def next_queued(self, lane: str) -> dict[str, Any] | None:
        with self.conn() as con:
            return _one(con.execute("SELECT * FROM tasks WHERE lane=? AND state='queued' ORDER BY id LIMIT 1", (lane,)))

    def tasks(self, states: Iterable[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.conn() as con:
            if states:
                states = list(states)
                return _rows(con.execute(f"SELECT * FROM tasks WHERE state IN ({','.join('?' * len(states))}) "
                                         f"ORDER BY id DESC LIMIT ?", (*states, limit)))
            return _rows(con.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)))

    def claim_task(self, task_id: int) -> dict[str, Any] | None:
        now = self.clock()
        with self.conn() as con:
            cur = con.execute("UPDATE tasks SET state='running', started=?, last_prompt=? WHERE id=? AND state='queued'",
                              (now, now, task_id))
            if cur.rowcount != 1:
                return None
            return _one(con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)))

    def update_task(self, task_id: int, **fields: Any) -> None:
        if fields:
            with self.conn() as con:
                con.execute(f"UPDATE tasks SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                            (*fields.values(), task_id))

    def signal(self, task_id: int | None) -> None:
        if task_id is not None:
            self.update_task(task_id, last_signal=self.clock(), nudges=0)

    # ---------- goals: what the team works toward, ranked by humans or the hub ----------

    def add_goal(self, title: str, detail: str, by: str) -> int:
        if not title.strip():
            raise StoreError("a goal needs a title")
        now = self.clock()
        with self.conn() as con:
            last = con.execute(f"SELECT COALESCE(MAX(priority), 0) FROM goals WHERE state IN {GOAL_RANKED}").fetchone()[0]
            return _id(con.execute("INSERT INTO goals(title, detail, priority, state, created_by, created, updated) "
                                   "VALUES(?,?,?,?,?,?,?)", (title.strip(), detail.strip(), last + 1, "active", by,
                                                             now, now)))

    def goal(self, goal_id: int) -> dict[str, Any]:
        with self.conn() as con:
            row = _one(con.execute("SELECT * FROM goals WHERE id=?", (goal_id,)))
        if not row:
            raise StoreError(f"goal #{goal_id} not found")
        return goal_view(row)

    def goals(self, states: Iterable[str] = GOAL_RANKED) -> list[dict[str, Any]]:
        """Ranked goals first (by priority), then the rest newest first."""
        states = list(states)
        with self.conn() as con:
            rows = _rows(con.execute(f"SELECT * FROM goals WHERE state IN ({','.join('?' * len(states))}) "
                                     f"ORDER BY state NOT IN {GOAL_RANKED}, "
                                     f"CASE WHEN state IN {GOAL_RANKED} THEN priority END, closed DESC, id", states))
        return [goal_view(r) for r in rows]

    def _rerank(self, con: sqlite3.Connection, order: list[int]) -> None:
        for i, gid in enumerate(order, 1):
            con.execute("UPDATE goals SET priority=? WHERE id=?", (i, gid))

    def move_goal(self, goal_id: int, position: int) -> list[int]:
        """Put a ranked goal at `position` (1 = first). Returns the new order of goal ids."""
        g = self.goal(goal_id)
        if g["state"] not in GOAL_RANKED:
            raise StoreError(f"goal #{goal_id} is {g['state']}; only active or paused goals are ranked")
        with self.conn() as con:
            order = [r[0] for r in con.execute(f"SELECT id FROM goals WHERE state IN {GOAL_RANKED} "
                                               f"ORDER BY priority, id")]
            order.remove(goal_id)
            order.insert(max(0, min(position - 1, len(order))), goal_id)
            self._rerank(con, order)
            con.execute("UPDATE goals SET updated=? WHERE id=?", (self.clock(), goal_id))
        return order

    def set_goal_state(self, goal_id: int, state: str, note: str = "") -> dict[str, Any]:
        if state not in GOAL_STATES:
            raise StoreError(f"goal state must be one of {', '.join(GOAL_STATES)}")
        g = self.goal(goal_id)
        now = self.clock()
        with self.conn() as con:
            if state in GOAL_RANKED and g["state"] not in GOAL_RANKED:  # reopened: rank it last
                last = con.execute(f"SELECT COALESCE(MAX(priority), 0) FROM goals WHERE state IN {GOAL_RANKED}"
                                   ).fetchone()[0]
                con.execute("UPDATE goals SET priority=? WHERE id=?", (last + 1, goal_id))
            con.execute("UPDATE goals SET state=?, note=COALESCE(NULLIF(?, ''), note), updated=?, closed=? WHERE id=?",
                        (state, note, now, None if state in GOAL_RANKED else now, goal_id))
            order = [r[0] for r in con.execute(f"SELECT id FROM goals WHERE state IN {GOAL_RANKED} ORDER BY priority, id")]
            self._rerank(con, order)
        return self.goal(goal_id)

    def plan_goal(self, goal_id: int, steps: list[str]) -> dict[str, Any]:
        """Replace the plan. A step whose text is unchanged keeps its done mark."""
        g = self.goal(goal_id)
        was = {st["text"]: bool(st.get("done")) for st in g["steps"]}
        new = [{"text": t.strip(), "done": was.get(t.strip(), False)} for t in steps if t.strip()]
        with self.conn() as con:
            con.execute("UPDATE goals SET steps=?, updated=? WHERE id=?",
                        (json.dumps(new, ensure_ascii=False), self.clock(), goal_id))
        return self.goal(goal_id)

    def check_steps(self, goal_id: int, numbers: Iterable[int], done: bool = True) -> dict[str, Any]:
        g = self.goal(goal_id)
        steps = g["steps"]
        for n in numbers:
            if not 1 <= n <= len(steps):
                raise StoreError(f"goal #{goal_id} has steps 1..{len(steps)}" if steps else
                                 f"goal #{goal_id} has no plan yet (`tower goal plan`)")
            steps[n - 1]["done"] = done
        with self.conn() as con:
            con.execute("UPDATE goals SET steps=?, updated=? WHERE id=?",
                        (json.dumps(steps, ensure_ascii=False), self.clock(), goal_id))
        return self.goal(goal_id)

    def goal_tasks(self) -> dict[int, dict[str, int]]:
        """Per goal: tasks queued or running (`live`) and finished (`closed`)."""
        out: dict[int, dict[str, int]] = {}
        with self.conn() as con:
            for gid, state, n in con.execute("SELECT goal_id, state, count(*) FROM tasks WHERE goal_id IS NOT NULL "
                                             "GROUP BY goal_id, state"):
                d = out.setdefault(gid, {"live": 0, "closed": 0})
                if state in ("queued", *TASK_LIVE):
                    d["live"] += n
                elif state == "closed":
                    d["closed"] += n
        return out

    # ---------- items ----------

    def add_item(self, lane: str, task_id: int | None, kind: str, title: str, body: str,
                 options: list[dict[str, Any]], attachments: list[str], audience: str = HUB,
                 multi: bool = False, question: str | None = None, assignee: str | None = None) -> int:
        if kind not in ("decision", "review"):
            raise StoreError("kind must be decision or review")
        if kind == "decision" and len(options) < 2:
            raise StoreError("a decision needs two or more options (--option). Use --kind review to approve an artifact.")
        if kind == "review" and options:
            raise StoreError("a review takes no options")
        if multi and kind != "decision":
            raise StoreError("--multi only works on decisions")
        with self.conn() as con:
            return _id(con.execute(
                "INSERT INTO items(task_id, lane, kind, title, body, options, multi, attachments, audience, assignee, "
                "state, created, question) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, lane, kind, title, body, json.dumps(options, ensure_ascii=False), int(multi),
                 json.dumps(attachments, ensure_ascii=False), audience, assignee, "open", self.clock(), question)))

    def item(self, item_id: int) -> dict[str, Any]:
        with self.conn() as con:
            row = _one(con.execute("SELECT * FROM items WHERE id=?", (item_id,)))
        if not row:
            raise StoreError(f"item #{item_id} not found")
        return item_view(row)

    def items(self, state: str | None = None, audience: str | None = None, lane: str | None = None,
              limit: int = 200) -> list[dict[str, Any]]:
        where, args = [], []
        for col, val in (("state", state), ("audience", audience), ("lane", lane)):
            if val:
                where.append(f"{col}=?")
                args.append(val)
        sql = "SELECT * FROM items" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
        with self.conn() as con:
            return [item_view(r) for r in _rows(con.execute(sql, (*args, limit)))]

    def escalate(self, item_id: int, why: str, assignee: str | None = None) -> None:
        it = self.item(item_id)
        if it["state"] != "open":
            raise StoreError(f"#{item_id} is already decided")
        with self.conn() as con:
            con.execute("UPDATE items SET audience=?, escalation=?, assignee=?, notified=NULL WHERE id=?",
                        (HUMAN, why, assignee, item_id))

    @staticmethod
    def check_decision(it: dict[str, Any], verdict: str, choice: str | list[str] | None, note: str) -> str | None:
        if verdict not in VERDICTS:
            raise StoreError(f"verdict must be one of {', '.join(VERDICTS)}")
        labels = [o["label"] for o in it["options"]]
        picked = [choice] if isinstance(choice, str) else list(choice or [])
        if any(c not in labels for c in picked):
            raise StoreError(f"#{it['id']} options are {labels}")
        if len(set(picked)) > 1 and not it["multi"]:
            raise StoreError(f"#{it['id']} takes one choice")
        out = MULTI_SEP.join(lb for lb in labels if lb in picked) or None
        if it["kind"] == "decision" and verdict == "ok" and out is None:
            raise StoreError(f"#{it['id']} is a decision: pick an option, or answer in your own words")
        if verdict == "answer" and (out is not None or not note.strip()):
            raise StoreError("an own answer needs text and no option")
        return out

    def decide(self, item_id: int, verdict: str, by: str, choice: str | list[str] | None = None, note: str = "",
               basis: str = "", hub_may_decide: bool = False) -> dict[str, Any]:
        it = self.item(item_id)
        if it["state"] != "open":
            raise StoreError(f"#{item_id} is already decided ({it['decided_by']})")
        out = self.check_decision(it, verdict, choice, note)
        if by == HUB:
            if it["audience"] == HUMAN and not hub_may_decide:
                raise StoreError(f"#{item_id} was escalated to humans; the hub does not decide it")
            if not basis.strip():
                raise StoreError("the hub must give --basis (which rule or evidence)")
        with self.conn() as con:
            cur = con.execute("UPDATE items SET state='decided', verdict=?, choice=?, note=?, basis=?, decided_by=?, "
                              "decided=? WHERE id=? AND state='open'",
                              (verdict, out, note, basis, by, self.clock(), item_id))
            if cur.rowcount != 1:
                raise StoreError(f"#{item_id} was decided meanwhile")
        return self.item(item_id)

    def revise_item(self, item_id: int, title: str | None = None, body: str | None = None,
                    options: list[dict[str, Any]] | None = None, attachments: list[str] | None = None) -> dict[str, Any]:
        it = self.item(item_id)
        if it["state"] != "open":
            raise StoreError(f"#{item_id} is closed")
        opts = it["options"] if options is None else options
        if it["kind"] == "decision" and len(opts) < 2:
            raise StoreError("a decision needs two or more options")
        with self.conn() as con:
            con.execute("UPDATE items SET title=?, body=?, options=?, attachments=?, notified=NULL "
                        "WHERE id=? AND state='open'",
                        (title or it["title"], it["body"] if body is None else body,
                         json.dumps(opts, ensure_ascii=False),
                         json.dumps(it["attachments"] if attachments is None else attachments, ensure_ascii=False),
                         item_id))
        return self.item(item_id)

    def merge_item(self, old_id: int, into_id: int, by: str) -> dict[str, Any]:
        if old_id == into_id:
            raise StoreError("cannot merge an item into itself")
        for it in (self.item(old_id), self.item(into_id)):
            if it["state"] != "open":
                raise StoreError(f"#{it['id']} is closed")
        now = self.clock()
        with self.conn() as con:
            con.execute("UPDATE items SET state='merged', verdict='merged', note=?, decided_by=?, decided=?, delivered=? "
                        "WHERE id=? AND state='open'", (f"merged into #{into_id}", by, now, now, old_id))
        return self.item(old_id)

    def undelivered_decisions(self) -> list[dict[str, Any]]:
        with self.conn() as con:
            return [item_view(r) for r in _rows(con.execute(
                "SELECT * FROM items WHERE state='decided' AND delivered IS NULL AND question IS NULL ORDER BY decided"))]

    def question_items(self, lane: str) -> list[dict[str, Any]]:
        with self.conn() as con:
            return [item_view(r) for r in _rows(con.execute(
                "SELECT * FROM items WHERE lane=? AND question IS NOT NULL AND delivered IS NULL ORDER BY id", (lane,)))]

    def unnotified(self) -> list[dict[str, Any]]:
        with self.conn() as con:
            return [item_view(r) for r in _rows(con.execute(
                "SELECT * FROM items WHERE state='open' AND audience=? AND notified IS NULL ORDER BY id", (HUMAN,)))]

    def update_item(self, item_id: int, **fields: Any) -> None:
        with self.conn() as con:
            con.execute(f"UPDATE items SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), item_id))

    def open_items_for_task(self, task_id: int) -> int:
        with self.conn() as con:
            return int(con.execute("SELECT count(*) FROM items WHERE task_id=? AND state='open'", (task_id,)).fetchone()[0])

    # ---------- reports ----------

    def add_report(self, task_id: int | None, lane: str, kind: str, text: str, attachments: list[str]) -> int:
        with self.conn() as con:
            return _id(con.execute("INSERT INTO reports(task_id, lane, kind, text, attachments, created) "
                                   "VALUES(?,?,?,?,?,?)",
                                   (task_id, lane, kind, text, json.dumps(attachments, ensure_ascii=False),
                                    self.clock())))

    def reports(self, task_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.conn() as con:
            if task_id is not None:
                rows = _rows(con.execute("SELECT * FROM reports WHERE task_id=? ORDER BY id", (task_id,)))
            else:
                rows = _rows(con.execute("SELECT * FROM reports ORDER BY id DESC LIMIT ?", (limit,)))
        for r in rows:
            r["attachments"] = json.loads(r["attachments"] or "[]")
        return rows

    # ---------- history (board "more") ----------

    HISTORY = {
        "decided": ("items WHERE state='decided'", "decided DESC, id DESC"),
        "reports": ("reports", "id DESC"),
        "closed": ("tasks WHERE state IN ('closed','cancelled')", "closed DESC, id DESC"),
        "goals": ("goals WHERE state IN ('done','dropped')", "closed DESC, id DESC"),
    }
    MEMO_HISTORY = {"memos": INFRA, "requests": HUB}

    def history_count(self, kind: str) -> int:
        with self.conn() as con:
            if kind in self.MEMO_HISTORY:
                return int(con.execute("SELECT count(*) FROM messages WHERE target=? AND kind='memo'",
                                       (self.MEMO_HISTORY[kind],)).fetchone()[0])
            return int(con.execute(f"SELECT count(*) FROM {self.HISTORY[kind][0]}").fetchone()[0])

    def history(self, kind: str, offset: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        if kind in self.MEMO_HISTORY:
            return self.memos(self.MEMO_HISTORY[kind], limit, offset)
        if kind not in self.HISTORY:
            raise StoreError(f"history kind must be one of {', '.join([*self.HISTORY, *self.MEMO_HISTORY])}")
        src, order = self.HISTORY[kind]
        with self.conn() as con:
            rows = _rows(con.execute(f"SELECT * FROM {src} ORDER BY {order} LIMIT ? OFFSET ?", (limit, offset)))
        if kind == "decided":
            return [item_view(r) for r in rows]
        if kind == "goals":
            return [goal_view(r) for r in rows]
        if kind == "reports":
            for r in rows:
                r["attachments"] = json.loads(r["attachments"] or "[]")
        return rows

    # ---------- lanes ----------

    def lane(self, lane: str) -> dict[str, Any]:
        with self.conn() as con:
            return _one(con.execute("SELECT * FROM lanes WHERE lane=?", (lane,))) or {"lane": lane}

    def set_lane(self, lane: str, **fields: Any) -> None:
        with self.conn() as con:
            con.execute("INSERT OR IGNORE INTO lanes(lane) VALUES(?)", (lane,))
            if fields:
                con.execute(f"UPDATE lanes SET {', '.join(f'{k}=?' for k in fields)} WHERE lane=?",
                            (*fields.values(), lane))

    # ---------- members ----------

    def add_member(self, name: str, role: str = "member", ident: str | None = None,
                   with_token: bool = False) -> str | None:
        if role not in ("owner", "member"):
            raise StoreError("role must be owner or member")
        token = secrets.token_urlsafe(24) if with_token else None
        with self.conn() as con:
            con.execute("INSERT INTO members(name, role, ident, token_hash, created) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(name) DO UPDATE SET role=excluded.role, ident=COALESCE(excluded.ident, ident), "
                        "token_hash=COALESCE(excluded.token_hash, token_hash)",
                        (name, role, ident, token_hash(token) if token else None, self.clock()))
        return token

    def remove_member(self, name: str) -> None:
        with self.conn() as con:
            if con.execute("DELETE FROM members WHERE name=?", (name,)).rowcount != 1:
                raise StoreError(f"no member {name}")

    def members(self) -> list[dict[str, Any]]:
        with self.conn() as con:
            return _rows(con.execute("SELECT name, role, ident, token_hash IS NOT NULL AS has_token, created "
                                     "FROM members ORDER BY name"))

    def member_by_token(self, token: str) -> dict[str, Any] | None:
        with self.conn() as con:
            return _one(con.execute("SELECT name, role FROM members WHERE token_hash=?", (token_hash(token),)))

    def member_by_ident(self, ident: str) -> dict[str, Any] | None:
        with self.conn() as con:
            return _one(con.execute("SELECT name, role FROM members WHERE lower(ident)=lower(?)", (ident,)))
