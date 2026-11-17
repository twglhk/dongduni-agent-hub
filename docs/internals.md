# Internals

Design notes for code that carries no comments: what a reader cannot see from the names. Options that people set are in the README, the extension contract is in `agent_hub/guide/EXTEND.md`, the board routes are in [board-api.md](board-api.md) and the runtime adapters in [runtimes.md](runtimes.md).

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

## Safety gates and hooks (`gates.py`, `hooks.py`)

`hooks.py` is the hook every tower-opened Claude Code lane runs (`--settings`). Run as `python <path>/hooks.py` it needs only the standard library, no package install and no `PYTHONPATH`. `python hooks.py opencode-plugin` prints an OpenCode plugin (save it as `.opencode/plugin/dah-gates.ts`) that sends OpenCode's tool calls through the same gates.

- **PreToolUse** runs the gates. Deny is exit 2 with the reason on stderr (Claude Code hands stderr to the model and blocks the call); allow is exit 0. A gate that crashes never blocks work: the call goes through and the traceback goes to `.tower/state/hooks/gate-errors.log`. Every deny is appended to the lane's `events.jsonl` as `GateDeny`. The hook also hands the main agent any urgent tell for the lane (`dah tell --urgent`) as hook context, so a stop order lands at the next tool call, mid-turn; tells older than two hours are dropped.
- **Other events** (`StopFailure`) get one line each in `events.jsonl`. Nothing is printed, because hook stdout can steer the model.
- State is `<workspace>/.tower/state/hooks/<lane>/` (`events.jsonl`, `urgent/`). The lane comes from `TOWER_HOME` and `TOWER_LANE`, which the tower sets; a session started without the tower still gets the gates but no log and no urgent tells. `DAH_GATES=off` in a lane's environment turns the gates off for that lane.

`gates.py` is pure functions with no state. Commands are read as POSIX shell (Claude Code runs Bash, on Windows through Git Bash); quotes, separators, comments and heredocs are understood. A heredoc body is data (`cat > x.sh <<EOF ... pkill ... EOF` writes a file and kills nothing) unless it is fed to a shell (`bash <<EOF`), `sh -c "..."` or `eval "..."`, then it is checked as commands. `taskkill /IM name` is checked like `killall name`.

| gate | refuses |
| --- | --- |
| kill | a process kill whose pattern could hit the tower, other lanes or every process of the user: bare names (`pkill node`), `kill -1`, `kill 0`, and BSD `pkill -f "x" -n -u $(whoami)`, which reads `-n`, `-u` and the user name as extra patterns |
| git | sweeping the shared work tree: `git stash`, `reset --hard`, `clean`, `checkout -- .`, `restore .`, and a `git commit` without `-- <paths>` that would carry another lane's staged deletions. Off inside a linked worktree, where a lane may reset its own checkout |
| env | reading `.env` files; `.env.example` is fine |
| binary | OpenCode's `apply_patch` (a text diff) on an existing binary file; one such call once stored 70 MB in session history. Claude Code's Edit needs a Read first, so it is not guarded |

## Model API errors (`apierrors.py`)

Claude Code ends a turn on a model error with a `StopFailure` hook; `hooks.py` writes it to the lane's `events.jsonl` with `error` (`rate_limit`, `authentication_failed`, `billing_error`, `server_error`, ...) and `error_details`. Each tick the engine reads only the lines added since the last tick (files that existed when the tower started are read from their end) and pushes each kind once, then not again for 30 minutes. Errors that retry by themselves (token refresh throttled, overloaded, network drops) are not pushed. Sessions started without the tower have no hook, so their errors are not seen.

## Stage ratings (`rating.py`)

The first-pass rate of every stage of an engine's process, read from the task ledger. A rated task came with a `Line:` header naming a stage (or `prepare`, rated apart) and either closed or was cancelled after it started. A task is a first pass when it closed, the hub never sent it a fix (`dah tell` text containing `fix:`), no task for the same deliverable key, row and stage was cancelled or sent back by a later `fix` task, and, when the stage's card says `Passed by: human`, the human adopted the deliverable in the first round (the first recorded `dah deliverable KEY adopted|redirected|dropped` outcome is `adopted`, with no rejected board item before it). A task whose human result is not recorded yet is left out of `n` until it is. The score is first passes over `n`, rounded down.

The bars live in one block, `BARS`: new below 5 tasks; best from 85 %; good 65-84 %; needs fixing below 65 %; needs rework when `n >= 10`, below 50 %, and the last two fixes of the stage's card did not raise the score. A fix is a commit that raised the `— vN` in the card's title (read from git); "did not raise" means the attempts split by those two fix times into three non-empty parts with after <= between <= before. A fix of a stage is its pattern card; a fix of the process is the process card plus every stage card. A candidate for lighter review has 90 % or more with `n >= 10` and a high human adoption rate.

Tokens count a task's new reads plus writes (cache reads and unmeasured tasks left out); a task over a stage range splits them evenly. Per attempt is the median of the measured attempts; per adopted deliverable is the stage's token sum, failed attempts included, over the deliverables whose last outcome is adopted.

Blame: a failure counts against the stage that should have caught it. A lane writes `dah blame <stage>`, the hub confirms or changes it (`dah blame --confirm --task N [stage]`), and the confirmed stage wins. A task with a blame fails only that stage and passes the others it went through; a blame outside its stages adds one failed attempt to the blamed stage. A failed task without a blame is counted the plain way and marked `blame?`.

## Process cards and the `Line:` header (`engines.py`, `stagemodel.py`)

An engine's `library` is a full process of the process library (`processes/full/<id>.md`, read by `processes.py`; each sub-process is a row, `n` = `<full>/<sub>`), or an older pattern library that `dah update` has not converted: one markdown table with a row per kind of deliverable (`#`, `Type`, `Lane · skill`, `Process`, `Pattern`, `Model · effort`), columns found by header name.

A process card is `processes/sub/<id>/process.md` and its pattern cards are in `processes/sub/<id>/patterns/` (in an older, unconverted library the card is `<skills>/<skill>/references/process/<name>.md` and the patterns are `patterns/<name>.md` below the skill's `references/`). A card has a first line `# Process: <type> — v2`, a `Status: confirmed` line and a table of stages (`#`, `Stage`, `Input`, `Output`, `Gate`, `Passed by`, `Pattern card`). A pattern card is the file the `Pattern card` cell names: a title ending `— v2` and a `## Status` heading followed by the status line. Confirmed and tuned are green, collecting, draft and reviewed amber, anything else (or no file) grey.

A brief starts with a `Line:` header (`Line: <full> / <sub> · stage 2 · make`, or the old `Line: #3 Storyboard · stage 1-2 · process draft · pattern draft · prepare`, or `Line: none · chore: ...`). Modes: `prepare` (build or fix the method), `make` (run it), `fix` (send-back of earlier work). `make` is refused while the row's process is not confirmed or its pattern is neither confirmed nor tuned. `require_line` turns the gate on for `dah task`; without it the header is read when present and nothing is refused. An old `#<row>` header of a converted library maps through the full process's `Old #` column.

Model and effort per task (`stagemodel.py`): `model:` and `effort:` lines in the brief win; then the row the `Line:` header names, from the process library or from the `stage_table`. A cell is one `opus/high` for the whole row or per-stage entries (`1-2 sonnet/medium; 3 opus/high`); across a range of stages the strongest model and highest effort win. A name is `fable`, `opus`, `sonnet`, `haiku` or a full id the runtime accepts; a bare word that is none of the four aliases is flagged as a typo. No table, cell or line gives nothing, and the lane's own model applies.

## Stats and retros (`stats.py`)

`dah stats` and `dah retros` group the task ledger by lane, engine, model, effort or week; a task belongs to the day it was queued. Tokens come from the agent transcripts. A wrapped report may end with `one line || retro: cost=... | corrected=... | rule=...`; the line goes to the board, the three fields to the retro log.

## Machine card (`machine.py`)

Cut points come from the machine itself. Load is the one-minute average per core (green below 2, amber below 4). CPU is the share of all cores busy (60 / 85). Memory is the share of RAM free without swapping (green from 20 %, amber from 10 %); on macOS the kernel's memory pressure level wins when readable. Swap is the share of RAM swapped out (10 / 25). Reads use `sysctl`, `vm_stat` and `ps` on macOS, `/proc` on Linux and `GlobalMemoryStatusEx` on Windows. What cannot be read is unknown, never 0. The engine also writes why each queued task is not starting to `waits.json`.

## Memory guard (`memwatch.py`)

Every poll one `ps` snapshot is read; a lane whose session process is over `memory_limit_mb` is listed as heavy. The engine then asks that lane's task for a handoff and renews the session (an idle session with no task is closed). Nothing here kills a process, so work is handed over, not lost. A process belongs to a lane by markers its command line carries (the session id, or `--port N` for an OpenCode server). Where `ps` does not exist (Windows) the guard does nothing.

## Other modules

- `procs.py`: on POSIX children start in their own session so a stop reaches the whole group; on Windows they start in a new process group without a console window, a stop is `taskkill /T`, and liveness is asked of the OS because `os.kill(pid, 0)` terminates the process there. An npm-installed CLI is a `.cmd` shim that cmd.exe would cut at newlines, so a shim wrapping a Node script runs as `node <script>`.
- `discover.py` (`dah discover`): a read-only survey for the setup agent. It reports names, counts and booleans only, never a config value, token, env value or file content.
- `starter.py`: four neutral subagents (`explore`, `librarian`, `looker`, `general`) and a few skills from `templates/starter/`. Subagents go to `.claude/agents/` when a lane runs on Claude Code; skills go to `.claude/skills/` for Claude Code lanes (OpenCode reads it too) and to `.agents/skills/` for Codex, or OpenCode with no Claude lane. `refresh` swaps untouched copies through `templates/shipped.json`; an edited one gets the new text beside it as `<name>.new`. A subagent's `model:` and `effort:` lines are the workspace's own, so a file that only changed those still counts as untouched. Models are in `guide/MODELS.md`; a model the agent CLI refuses becomes `inherit`.
- `notify.py`: `notify` keys in `tower.json` are `ntfy_topic` (with `ntfy_server`), `slack_webhook` and `desktop`; values may be `env:NAME`.
- `donegate.py`: the config is in the README; a pending gate whose runner never wrote its pid is failed after two minutes, and a lock folder without a pid file older than a minute is taken over.
- `scratch.py`: a gate worktree inside the project would be seen by the project's own tools, so the root must be outside it.
- `install.py`: `_refresh_file` brings a template-based file to the newest template: untouched since some release, the old text is swapped; edited inside, it is left and the new template goes beside it as `<name>.new`. `dah update` also backs up package files changed in place to `.tower/patches/<version>/files/` with `changes.diff` and `index.json`.

## Skill lint (`tools/skill_lint.py`)

The idea comes from Claude Code Game Studios `skill-test static` (MIT, commit 05ad7c8); the checks are OpenCode's (https://opencode.ai/docs/skills/). Run `python3 tools/skill_lint.py [repo_root]` from a clone; it is not part of the installed package and needs only the standard library.

Skills are found by globbing `.opencode/skills/*/`, `.agents/skills/*/` and `.claude/skills/*/` (never a list, so a new skill cannot slip past). Per skill:

- FAIL: no `SKILL.md`; frontmatter missing or not YAML; `name` missing, not `^[a-z0-9]+(-[a-z0-9]+)*$`, over 64 characters, or not the folder name; `description` missing or not 1-1024 characters; a description cut short by an unquoted ` #` (YAML reads the rest as a comment); a relative markdown link in `SKILL.md` or `references/*.md` that points nowhere.
- WARN: frontmatter keys OpenCode ignores; a `SKILL.md` body over 150 lines or a reference over 300 lines (size budget).

Rules in `.claude/rules/*.md` (oh-my-opencode rules): FAIL without `paths`/`globs`, WARN when a pattern matches no file in the repository. A file that cannot be read is NOT ASSESSED, never OK. The exit code is 0 only when every item is OK or WARN and at least one skill was scanned.
