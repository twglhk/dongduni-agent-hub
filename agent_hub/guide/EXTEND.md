# Extending agent-hub in a workspace

agent-hub is a skeleton. What a team wants beyond it (a board section, a dashboard tab, a Slack ping when a lane is blocked, an API for another tool) is built in the workspace's own `.tower/`, not in the installed package. `.tower/` is in the workspace's git; the package is replaced by every `dah update`.

| folder | what goes there | who runs it |
| --- | --- | --- |
| `.tower/board/extra.js`, `extra.css` | additions to the built-in board | each person's browser |
| `.tower/board/index.html` | a board of your own instead of the built-in one | each person's browser |
| `.tower/board/<anything>` | files those use (scripts, styles, images, fonts, JSON) | served at `x/<path>` |
| `.tower/ext/*.py` | API routes, tower hooks, and runtimes and terminal apps for lanes | the tower, with the tower's rights |

Built-in routes and the engine's decisions are the contract extensions build on; they cannot be replaced. A broken extension is logged to `.tower/state/tower.log` and skipped; the board and the tower keep working. Files are picked up when they change: no restart. Ask infra on the board to build or change any of this.

## Board

Everything under `.tower/board/` is served to signed-in people at `x/<path>` (relative to the board, so it also works under `/w/<workspace>/`). Python files, dot files and secrets are never served.

If `extra.css` exists the board links it; if `extra.js` exists the board loads it once, after the first state arrives, with `window.dah` ready:

| | |
| --- | --- |
| `dah.addSection(tab, id, render)` | a section after the built-in ones on `tab` (`hub`, `infra`, `lanes`, `history`). `render(state)` returns a Node, a string (shown as text), an array of them, or null. The same `id` again replaces it. |
| `dah.addTab(id, label, icon, render)` | a tab after the built-in ones in the bottom bar, opened at `#x-<id>` (`id`: letters, digits, `-`, `_`). `label` is a string or `{en, ko, ...}`; `icon` an SVG path string (optional). |
| `dah.onState(fn)` | `fn(state)` after every state load (about every 5 s while the page is open), and once when registered |
| `dah.get(path)`, `dah.post(path, body)` | JSON calls relative to the board, e.g. `dah.get('api/x/stats')`; they throw with the server's error |
| `dah.refresh()` | fetch the state now |
| `dah.h`, `dah.svg`, `dah.sec`, `dah.empty`, `dah.toast` | the board's own builders, for a matching look |
| `dah.S`, `dah.L`, `dah.lang`, `dah.BASE`, `dah.version` | the board's state (`dah.S.data` is the server state), current UI strings, board language, path prefix (`""` or `/w/<workspace>`), agent-hub version |

`render` runs on every refresh of the visible tab, but the section is repainted only when its HTML changes, so scroll position, focus and typed text survive. A click handler should therefore read fresh data when it runs (`dah.S.data`), not rely on values captured at render time. Returning the same Node each time keeps it mounted for you to update. An extension that throws shows a small error box in its own section; the rest of the board is unaffected. Do not call `dah.refresh()` from `onState`: it would load the state in a loop.

A whole board of your own: put it at `.tower/board/index.html`. It can use every route in the board API (`GET api/state`, `POST api/memo`, ...; see the top of `agent_hub/web.py`) and your own `api/x/...` routes. `?builtin` on the board URL always opens the built-in board, so a broken page never locks people out. When an update changes the built-in board, `dah update` asks infra to carry the change over.

## API routes

```python
# .tower/ext/stats.py
def stats(ctx, body):
    s = ctx["store"]
    return {"open": len(s.items(state="open")), "asked_by": ctx["me"]["name"]}

def sync(ctx, body):
    if not body.get("ticket"):
        return 400, {"error": "ticket missing"}
    ctx["log"](f"sync {body['ticket']}")
    return {"ok": True}

routes = {("GET", "stats"): stats, ("POST", "jira/sync"): sync}
```

- `("GET", "stats")` answers `GET api/x/stats`; `("POST", "jira/sync")` answers `POST api/x/jira/sync`.
- `fn(ctx, body)` returns a JSON value (status 200) or `(status, value)`. An exception is a 500 with its message.
- `ctx`: `cfg` (the workspace config), `store` (the board's state: goals, items, tasks, reports, memos), `me` (who is signed in), `query` (the URL query, one value per key), `log`.
- Only signed-in people reach them, with the workspace's own auth. POSTs need the `x-tower: 1` header, as every board POST does (`dah.post` sends it).
- Another workspace shown on the same board (`/w/<workspace>/`) serves its own routes.

## Tower hooks

```python
# .tower/ext/notify_blocked.py
import json, urllib.request

def on_event(ctx, kind, data):
    if kind == "report" and data["kind"] == "blocked":
        req = urllib.request.Request("https://hooks.slack.com/...", json.dumps({"text": f"{data['lane']} blocked: {data['text']}"}).encode(),
                                     {"content-type": "application/json"})
        urllib.request.urlopen(req, timeout=10)

def on_tick(ctx):
    pass  # every tick (a few seconds), after the tower's own work
```

- `on_event(ctx, kind, data)` for each change since the last tick, after the tower's own work:
  - `report`: a lane reported (`data["kind"]`: done, blocked, note, handoff, wrapped)
  - `item`: a decision or review was posted
  - `decided`: one was decided (`verdict`, `choice`, `note`, `decided_by`)
  - `memo`: someone wrote to the hub or infra on the board
  - `goal`: a goal was added, ranked, planned, ticked, paused, finished or dropped
  - `task`: a task was queued, started, closed or cancelled (`lane`, `state`, `title`, `goal_id`, ...)
  - `lane`: a lane's status changed (`lane`, `status`, `was`, `runtime`, `terminal`; status: closed, idle, busy, asking, unknown)
- `on_notify(ctx, notes)` for every push the tower sends people (ntfy, Slack, desktop), with the same notes: each has `title` and `lane`, `alert` is true for the tower's own alerts (a lane waiting in its window, a hub that keeps closing), and board items carry the item's fields. Use it for a channel agent-hub does not ship (Discord, Telegram, a pager). Notes are copies: changing them changes nothing.
- `on_tick(ctx)` once per tick. `ctx`: `cfg`, `store`, `log`, `now`.
- Hooks run in the tower loop: keep them quick, and hand slow work to a thread. Changes made while the tower was stopped are not replayed.
- Hooks run only in the workspace's own tower.

## Runtimes and terminal apps

A lane can run an agent CLI or open windows in a terminal app agent-hub does not ship. Register them in a `.tower/ext/*.py` file; `tower.json` then names them like the built-in ones.

```python
# .tower/ext/aider.py
import os
from agent_hub import procs
from agent_hub.runtimes import Headless

class Aider(Headless):  # one CLI run per prompt; busy while it runs
    def open(self, prompt):
        sid = "aider"
        self.store.set_lane(self.lane.id, session=sid)
        self._spawn([*procs.command(os.environ.get("AIDER_BIN", "aider")), "--yes-always", *self.lane.args,
                     "--message", prompt])
        return sid

    def resume_argv(self, sid, text):
        return [*procs.command(os.environ.get("AIDER_BIN", "aider")), "--yes-always", *self.lane.args, "--message", text]

runtimes = {"aider": Aider}
```

```json
"lanes": {"fix": {"runtime": "aider", "args": ["--model", "sonnet"]}}
```

- `runtimes = {"name": cls}`: `cls` subclasses `agent_hub.runtimes.Adapter` (or `Headless`, for CLIs that run once per prompt). It is built as `cls(cfg, store, lane)` and answers `status()` (`closed`, `idle`, `busy`, `asking` or `unknown`), `open(prompt)` (starts a session, returns its id), `send(sid, text)` and `close()`; optionally `owned()`, `questions()`, `reply_question(qid, answers)`, `context_k(sid)`, `usage(sid)`, `last_text(n)`. `self._spawn(argv)` starts a process in the lane's folder (its worktree if it has one) with the lane's environment and log files. Raise `RuntimeFailure` for a failure the tower should log.
- `terminals = {"name": cls}`: `cls` subclasses `agent_hub.runtimes.Host`, built as `cls(cfg)`: `open(title, launcher, cwd)` runs the launcher script in a new window in `cwd` and returns a handle (a string), `alive(handle)`, `send(handle, text)` (type one line and Enter), `close(handle)`. `alive` raises when the app cannot tell, so the lane shows `unknown` rather than closed. The built-in Claude, Codex and OpenCode window adapters use it as they use tmux.
- A name in `tower.json` that no extension registers is a config error; if the extension goes away later, its lanes show `unknown` and are never opened.
- These modules are also loaded by `dah` commands that read `tower.json` when it names an extension's runtime or terminal: keep module-level code free of side effects.

## What stays stable

An update replaces the package, never `.tower/`; what can change is what an extension leans on.

- Stable (changed only in a major version): the `.tower/` layout; `dah.addSection`, `addTab`, `onState`, `get`, `post`, `refresh`, `L`, `lang`, `BASE`, `version`; the top-level fields of the server state (`dah.S.data`, `GET api/state`) and the board API routes; `/api/x/<name>` with `fn(ctx, body)`; `on_event` / `on_notify` / `on_tick` with their kinds and the `ctx` keys listed above; `runtimes` and `terminals`, with the `Adapter` and `Host` methods listed above, `Headless.resume_argv`, `_spawn`, `RuntimeFailure`, `lane_env(cfg, lane)` and `lane_cwd(cfg, lane)`. New fields and kinds may be added.
- Internal (may change in any release): the board's DOM, CSS classes and variables, the builders (`dah.h`, `svg`, `sec`, `empty`, `toast`) and the rest of `dah.S`; `Store` methods beyond reading through `ctx["store"]`; anything in the package not listed above.

Prefer the stable part. When an update changes the built-in board, `dah update` asks infra to check a workspace's `extra.js` / `extra.css` (or to carry the change over to its own `index.html`).

## Changes made inside the package

Editing the installed package still works until the next update. `dah update` checks the installed files against the release before upgrading, keeps changed ones in `.tower/patches/<version>/` (with `changes.diff` against the release when it can be fetched), upgrades anyway, and asks infra to rebuild them as extensions. `dah update --dry-run` lists them without changing anything. To change agent-hub itself, run it from a clone (`git clone`, then `bin/dah`) and send the change upstream.
