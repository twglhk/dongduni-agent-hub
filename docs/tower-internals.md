# Tower internals

Design notes for `agent_hub/engine.py`, `store.py`, `config.py` and `cli/`. The code carries no comments; what a reader cannot see from the names is here.

## One tick (`engine.py`)

The tick calls no LLM. It reads the store and each lane's status and moves work along.

- **hub**: always on. News for it (reports, items to review, decisions, human requests) is batched and sent while it is idle. When its context grows large it is restarted quietly; its state lives in the store. If it closes `RESPAWN_LIMIT` times within `HUB_RETRY_WINDOW` seconds the tower says so once and retries slowly.
- **infra**: one human memo (or one infra decision) per fresh session; the next waits until it wraps.
- **team lanes**: the hub queues tasks; the tower opens a fresh session per task (up to `max_lanes` at once), delivers `tell` / `wrap` while idle, nudges silent lanes, revives closed ones and closes wrapped ones. A lane whose session is idle with no task is closed to free memory.
- **decided items** are routed once: a team item with a task becomes ONE follow-up task in a fresh session; everything else goes to the hub (or infra) as news.
- **live asks** (OpenCode only): a hub or infra question becomes a board item for humans and the answer goes back to the ask. A team lane's asks are bounced to the board.
- **human items** send one push notification.
- **usage**: each open session's token and cost totals, read when a turn ends and every minute. The task ledger splits the same reading into new / cache / out with turns.
- **stuck hub**: news waits while the hub is busy. If it waits `HUB_STUCK_SECONDS` (a hook or plugin that keeps the session working, a turn that never ends), humans get one board item about it.
- **schedules**: `tower.json` `schedules` queue a task for a team lane or a request to the hub once per day or week (`schedules.py`). A period that passed while the tower was down fires once, at the next tick.
- **extensions**: `.tower/ext/*.py` get `on_event(ctx, kind, data)` for what changed since the last tick (lane statuses included), then `on_tick(ctx)`, after the built-in work; errors are logged (see `agent_hub/guide/EXTEND.md`). Every push to people also goes to `on_notify(ctx, notes)`. A broken lane or a bad skills folder never stops the rest of the tick.

### Which queued task starts

- `after: T` waits for T to finish; an unknown T does not block.
- Beside other live tasks of the same lane, paths are a file lock: declared paths that overlap wait; a task with no paths may start (its session declares them with `dah paths`); a live task with no paths is waited for only `DECLARE_GRACE` seconds after it started. A task's paths are the ones its session declared, else the brief's `paths:` line, normalised.
- A task whose `paths:` fall under `heavy_paths` is heavy and takes a `max_lanes` slot; any other is light and uses the `light_lanes` slots. A closing session counts as heavy. Without `light_lanes` and `heavy_paths` there is one pool.
- A task skipped for a conflict takes no slot, so a later task can start (head-of-line skip). The reason shows on the board's machine card and in `dah status`.
- Parallel sessions (`lane#2` ...): a busy lane with tasks queued behind it, while a slot and an instance key are free, moves its first queued task that may run beside the live ones onto the key. Tasks that conflict stay queued and the next one is tried. When `max_instances` is lowered, instance work goes back to the base lane.
- Linked tasks: when the next queued task says `after: <the task just finished>`, it goes into the finished task's session instead of a fresh one, unless the session is fat (memwatch) or its context is over `CHAIN_CONTEXT_K`.
- Usage cap: an engine's `cap` is `tokens-day:N` (tokens in + out today over its lanes) or a function an extension registers in `caps`; it holds new tasks of that engine, never running ones.

A model and effort from the brief (or the stage table) go to the runtime only when set; OpenCode lanes keep their configured model.

## State (`store.py`)

Tower state is one SQLite file: tasks, board items, message queues, reports, lanes, members. An item row is the single source of truth for a decision. Messages only say whom to wake and what to tell: `target` is a lane id (hub, infra, a team lane) or `human` (replies shown on the board). A target starting with `hold:` is a board request whose attachments are still arriving; no lane takes it.

- Verdicts on an item: `ok`, `no`, `hold`, `answer` (a human wrote their own answer; the note is the decision).
- A hub reply to a person's request also becomes one review card for humans; a second reply revises it.
- `changes(marks)` returns what happened since the marks as `(kind, row)`, grouped as report, item (created), decided, memo, task (queued, started, closed or cancelled), oldest first within each. Pass the returned marks next time; `None` only takes the marks.
- Deliverable keys are one short word without spaces (`ui:shop`); `T12` and `#12` shapes are refused. A human's outcome (adopted, redirected, dropped) counts last-write-wins. A lane's blame names the stage that should have caught a failure; the hub's confirmed stage is the effective one.
- Task usage is per session, cumulative. Tasks that followed each other in one session (`after:`) split it: a task owns what the earlier ones did not. A total below what is already written is a transcript that lost its head and never replaces the larger value; the task row carries the sum.
- Schema changes are additive only: `ALTER TABLE ... ADD COLUMN` in `Store` for databases made before the column existed.

## Config (`config.py`)

`<home>/.tower/tower.json`. The home is the folder whose `.tower/` holds the config. Agents run in `project` (default: the home itself); a team lane with `"worktree": true` works in its own git worktree under `.tower/state/worktrees/<lane>`. State (SQLite DB, logs, members) lives in `<home>/.tower/state/` and must stay out of git. Without `worktree` a lane and its instances share the workspace, which is why their `paths:` may not overlap.

- A lane's `kind` is `hub`, `infra` or `team`; `terminal` is `none` (the tower runs the session in the background), `tmux` or `orca` (a window). `max_instances` 0 means the top-level value (infra: 1).
- `docs#2` is a parallel instance of `docs`; the base lane is the part before `#`.
- An install with no `engines` gets an implicit `default` engine that every team lane without one belongs to, so `dah engine list` is never empty. A lane names its engine with `engine` or an engine lists it in `lanes`; two different engines is an error.
- `"env:NAME"` as a value reads the environment so secrets stay out of `tower.json`.
- Default `args` run lanes unattended (`bypassPermissions` for Claude, no sandbox for Codex); `dah init --safe` writes the stricter ones (`SETUP.md`, "Permissions").
- `require_model_lines`, `require_line`, `done_gate`, `rule_watch`, `overlap_check`, `usage_percent`, `uploads`, `scratch_root` and the rest are described in the README sections for their features. `scratch_grace` is the seconds after a task closes before its scratch is swept; `memory_limit_mb` renews an idle session whose process passes it (0 = off).
- Extension runtimes and terminals are loaded only when `tower.json` names one that is not built in.

## CLI (`cli/`)

- `dah scratch` gives a scratch folder (or a detached worktree of your HEAD) outside the project, removed after the task closes.
- `dah blame` (a lane) names the stage that should have caught the failure; the hub confirms or changes it with `--confirm --task N [STAGE]`.
- `dah deliverable` (hub or infra) records the human's result; with no result it prints the lineage of tasks, items and results.
- `dah stats` shows the task ledger (tokens, turns, rework) per lane, engine, model, effort or week; `dah ratings` the first-pass rate per stage and process.
- `dah signal` is called by a lane's own hooks in a terminal window (Claude hook JSON on stdin, Codex notify JSON as the last argument). It never fails the agent's turn: problems go to `.tower/state/term/signal.err`. The person's own Codex `notify` program still gets every notification.
- `dah skill` writes the global setup entry for each agent so "set up agent-hub here" works from any workspace; `dah uninstall` shows its plan first and `--yes` does it.
