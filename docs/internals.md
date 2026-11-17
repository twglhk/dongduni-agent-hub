# Module notes

Design notes for modules whose code carries no comments. Options that people set are in the README; the extension contract is in `agent_hub/guide/EXTEND.md`.

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

A process card is `<skills>/<skill>/references/process/<name>.md`: a first line `# Process: <type> — v2`, a `Status: confirmed` line and a table of stages (`#`, `Stage`, `Input`, `Output`, `Gate`, `Passed by`, `Pattern card`). A pattern card is the file the `Pattern card` cell names (`patterns/<name>.md` below the skill's `references/`): a title ending `— v2` and a `## Status` heading followed by the status line. Confirmed and tuned are green, collecting, draft and reviewed amber, anything else (or no file) grey.

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
