# AGENTS.md

Instructions for coding agents working in this repository.

- **Asked to set agent-hub up for someone (in this project or globally)?** Follow [agent_hub/guide/SETUP.md](agent_hub/guide/SETUP.md) (`dah guide` prints it). Do not improvise the order.
- **Changing agent-hub itself?** Read on.

## Layout

| path | what |
| --- | --- |
| `agent_hub/engine.py` | the tick: hub, infra inbox, team lanes, decision routing, live asks, scheduling (paths, slots, linked tasks, usage caps). No LLM. |
| `agent_hub/runtimes/` | one adapter per agent runtime (opencode / claude / codex), in the background or in a terminal window (tmux, Orca); modules in [docs/runtimes.md](docs/runtimes.md) |
| `agent_hub/store.py` | SQLite schema and all state changes (tasks, items, messages, ledger, deliverables, …) |
| `agent_hub/config.py` | `tower.json`, the workspace registry (`~/.config/agent-hub/workspaces.json`, `%APPDATA%\agent-hub` on Windows), port picking |
| `agent_hub/procs.py` | starting, checking and stopping processes on Linux, macOS and Windows; npm shims on Windows |
| `agent_hub/ext.py` | workspace extensions: `.tower/board/` files and override, `.tower/ext/*.py` routes and engine hooks |
| `agent_hub/web.py` | board API (also serves other registered workspaces under `/w/<slug>/`), the board pages and the files below them |
| `board-app/` | source of the board UI: React 19, TypeScript, Vite and Tailwind. UI strings are in `src/i18n/<lang>.json` (en, ko, ja, zh, es). `src/ext/` is `window.dah` for workspace extensions |
| `agent_hub/board_next/` | the committed build of `board-app/`: served at `/` (page) and `/assets/` (hashed files), shipped as package data. Never edit it by hand |
| `agent_hub/board/index.html` | the single-file board from before the React app, kept as it was and served at `/old/`; it has its own `UI` dict and is no longer where new board features go |
| `agent_hub/cli/` | the `dah` command lanes and people use: `common`, `lanes`, `hub`, `reading`, `running`, `setup`, `manage`, `parser`, `main` |
| `agent_hub/discover.py` · `install.py` | setup survey and workspace installer |
| `agent_hub/templates/` | protocol and lane instruction templates copied into workspaces |
| `agent_hub/guide/` | the setup playbook, the extension guide and the guides for reaching the board (`dah guide`); shipped in the package |
| `agent_hub/stagemodel.py` | per-task model and effort from brief lines, a library's `model · effort` column or an optional table |
| `agent_hub/engines.py` · `rating.py` · `stats.py` | engines: process and pattern cards, an engine's rows (a full process, or an old pattern library), skills list, weekly snapshots; ratings and blame; the token ledger |
| `agent_hub/processes.py` · `line.py` · `libmigrate.py` | the process library (`processes/full/`, `processes/sub/`, which lanes use what, `view()` for the board); the `Line: <full> / <sub> · stage <n> · mode` header, its gate and the old `#<row>` form; `dah update`'s conversion of pattern libraries |
| `agent_hub/branchsync.py` | opt-in: a worktree lane's branch takes the workspace's branch before each task, and while idle (`branch_sync`, `branch_sync_seconds`, `branch_sync_push`) |
| `agent_hub/requester.py` | opt-in (`requester`): who asked for a task, who a card is for, infra news to the hub, the card visibility rule |
| `agent_hub/accounts.py` · `claudelogin.py` · `accountroutes.py` | opt-in (`claude_accounts`, needs header or token auth): whose Claude account a session belongs to (`accounts.py`), each member's own `~/.claude-accounts/<member>` signed in from the board with `claude auth login` and a pasted code that only goes to its stdin (`claudelogin.py`, never stored or logged), `/api/me/claude` for the viewer only (`accountroutes.py`) |
| `agent_hub/fair.py` | opt-in (`fair_turns`): the order waiting tasks start in, by turns per requester |
| `agent_hub/config.py` person hubs | opt-in (`person_hubs`): `hub-<member>` lanes are built in `config.load` from the member list (`Config.person_hubs`, `hub_of`, `is_hub`, `hubs()`); tasks record the hub that queued them (`tasks.hub`) and news goes there (`Store.hub_for_task`) |
| `agent_hub/prefs.py` | each member's own board settings, kept in the `member_prefs` table through `Store` and served at `/api/me/prefs`; `dah prefs` (`cli/prefs.py`) is the hub's way in; the board side is `board-app/src/lib/prefs.ts`, `state/prefs.tsx`, `components/prefs/` |
| `agent_hub/uploads.py` · `uploadroutes.py` · `storage/` | chat attachments (`uploads` in tower.json): the held request, plans in `.tower/state/uploads/`, the sweep, the request text; the board routes; the `Storage` interface with `local`, `drive` (rclone) and `s3` (`s3sign.py`, stdlib SigV4) providers; design in [docs/attachments.md](docs/attachments.md) |
| `agent_hub/queues.py` · `agent_hub/session_view.py` | the Lanes tab's queue strip (`state.queues`: per lane what its sessions hold now and what waits, with why and per-person counts) and the session sheet (`GET /api/sessions/<key>`); both are filtered through `visibility.Gate` (`sees_scoped`) |
| `agent_hub/leftovers.py` | opt-in (`leftover_sweep`, Linux): stops lane processes left by closed sessions |
| `agent_hub/sessinfo.py` | the model and effort a Claude Code lane session really runs with, read from its transcript (`run` in the board state) |
| `agent_hub/memwatch.py` | renews idle sessions over a memory line |
| `docs/` | README illustrations, the engine playbook, a Claude lane harness section, runtime adapters and the cli/runtimes module layout, releasing |
| `tools/skill_lint.py` | lint for OpenCode skills |

## Parallel sessions

- A team lane may run `lane#2` ... up to `max_instances` (top level, or on the lane); with `"worktree": true` every session has its own worktree and branch (`config.lane_dir`, `branch_of`, `lane_branch`: `dah/<lane>`, `dah/<lane>-N`), and branch sync runs per session. Infra takes `max_instances` on its own lane (`Config.infra_sessions()`, default 1); `max_instances` on the hub is refused.
- Paths are a file lock (`engine.task_block`, `message_block`): a session declares its own with `dah paths` (tasks, `tasks.paths`) or `dah memo paths` (infra, `messages.paths`, `ALONE` = alone, `NOTHING` = touches no files); declared overlap gives the work back (`Store.release_task`, `release_inbox`) and closes the session; work with no paths starts and is waited for `DECLARE_GRACE` seconds. Decisions on an infra card inherit the memo's paths through `items.message_id`. A single-session lane gets none of this text or behaviour.
- `Store.claim_inbox(lane, target, allow, fair)` and `inbox_queue(target, fair)` take infra messages in `dah memo bump` order, by turns per sending hub when `fair_turns` is on. `Store.cancel_message` backs `dah memo cancel` and `dah memo reply --covers`.

## Rules

- Standard library only, Python 3.10+. No new dependency without a strong reason.
- No comments and no docstrings by default, in code and tests alike. Names, small functions and tests carry the meaning; usage and design go into `agent_hub/guide/` or `docs/`. Write a comment only where the code cannot say it (a workaround for an outside tool's bug, a constraint nobody would guess), one line, the why and never the what.
- `cli` and `runtimes` are packages: a test that patches a name patches the module that looks it up (`agent_hub.cli.running`, not `agent_hub.cli`). A `common` module imports no sibling of its package.
- Every state change goes through `Store`. The engine must stay deterministic given a clock and a runtime; tests inject both (`tests/helpers.py`).
- A new runtime = one `Adapter` subclass with `status/open/send/close` (+ optional `questions`, `context_k`, `last_text`) and a stand-in CLI test in `tests/test_runtimes.py` (a workspace can also register one from `.tower/ext/`, without changing the package) (terminal windows: `tests/test_terminal.py`, against a stand-in terminal app). Never call a real model in tests.
- The board is the React app in `board-app/`. Its build is committed in `agent_hub/board_next/` and shipped in the package, so users need no Node; contributors run `cd board-app && npm ci && npm run build` (it typechecks first) and commit source and build together. CI rebuilds and fails when they differ, so keep the build reproducible (pinned versions, `npm ci`, no timestamps). Python code never imports or runs Node.
- The board makes no external request at runtime: no CDN fonts, scripts or styles, no analytics. Everything it loads comes from the board's own server (`/assets/`, its API, the workspace's `/x/` files).
- All UI strings live in `board-app/src/i18n/<lang>.json` for en, ko, ja, zh and es; a new key goes into every language (`tests/test_board_next.py` checks that the five files have the same keys). No UI text in components.
- `/old/` stays as a way back and for workspaces that depend on it. Fix it when it breaks; do not grow it. Web routes that both boards read (`web.py`) stay compatible with `/old/`.
- Changing a template in `agent_hub/templates/` (PROTOCOL.md, lanes/*.md): append its new text to `templates/shipped.json` (oldest first, newest last). `dah update` uses those texts to tell a workspace's untouched copy from an edited one; a test fails until you do.
- Extension points (`ext.py`: `window.dah`, `/x/`, `/api/x/`, `routes`, `on_event` kinds, `on_notify`, `on_tick`, `runtimes` and `terminals` with the `Adapter` / `Host` methods EXTEND.md lists) are a contract workspaces build on: extend them, do not rename or drop them without a major version. Keep `guide/EXTEND.md` in step.
- `discover` reports names, counts and booleans only. A test plants secrets and fails if any leaks; keep it that way.
- It runs on Linux, macOS and Windows. Start, check and stop processes only through `procs.py`; never `os.kill(pid, 0)` (it kills on Windows), `os.killpg` or `start_new_session` directly. Read and write text files with `encoding="utf-8"`.
- Releases: follow [docs/RELEASING.md](docs/RELEASING.md) (bump `__version__`, push tag `vX.Y.Z`; CI tests on all three systems and publishes `dongduni-agent-hub` to PyPI).
- Run `python3 -m unittest discover -s tests -t .` before you finish; it must print `OK`.
