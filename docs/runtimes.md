# Runtime adapters

How the tower starts, prompts, watches and stops an agent session for one lane. Code: `agent_hub/runtimes/`.

| runtime | how |
| --- | --- |
| `opencode` | long-lived `opencode serve --port P` per lane; prompts over its HTTP API. Live `ask` questions are supported (they become board items). A human may run `opencode --port P` (TUI) instead; the tower then uses that server and never kills it. |
| `claude` | headless Claude Code: `claude -p --session-id ID` to start, `claude -p --resume ID` per prompt. |
| `codex` | headless Codex: `codex exec --json` to start, `codex exec resume ID` per prompt. |

A lane with `"terminal": "tmux"` or `"orca"` runs the same agent interactively in a window the tower opens (a tmux window in the `dah-<workspace>` session, or an Orca terminal tab) and closes it when the lane is done. People watch it and may type into it. Claude reports busy/idle through hooks the tower adds with `--settings`, Codex through its `notify` program (both call `dah signal`); OpenCode runs its TUI, whose HTTP API the tower drives as before. Prompts for Claude and Codex are written to a file and the window gets one line asking the agent to read it, so long multi-line briefs never go through the keyboard.

A team lane with `"worktree": true` works in a git worktree of its own (`.tower/state/worktrees/<lane>`, branch `dah/<lane>`), created the first time the lane opens; its parallel instance #N gets one of its own too (`worktrees/<lane>-N`, branch `dah/<lane>-N`). The hub or a person merges the branch.

A workspace adds runtimes and terminal apps from `.tower/ext/*.py` (see `agent_hub/guide/EXTEND.md`).

Every adapter answers `status()`: `closed` (no session) | `idle` | `busy` | `asking` | `unknown`.

Binaries can be overridden with `TOWER_OPENCODE_BIN`, `TOWER_CLAUDE_BIN`, `TOWER_CODEX_BIN`, `TOWER_TMUX_BIN` and `TOWER_ORCA_BIN`.

## Modules

`agent_hub/runtimes/__init__.py` re-exports every name, so `agent_hub.runtimes.X` works for all of them.

| module | what |
| --- | --- |
| `common` | `RuntimeFailure`, HTTP and port helpers, `lane_env`, `claude_base`, package paths |
| `worktree` | lane working folder and git worktree helpers |
| `adapter` | the `Adapter` base class (`set_opts` keeps a session's model, effort and account folder; `env()` is the process environment, with the member's `CLAUDE_CONFIG_DIR` when `claude_accounts` picked one) |
| `opencode` | `OpenCode` and `OpenCodeTerm` |
| `headless` | `Headless`, `Claude`, `Codex` (print mode) |
| `hosts` | `Host`, `Tmux`, `Orca` |
| `terminal` | window mixins `Terminal` / `TerminalSession` and the signal file |
| `claude_term` | `ClaudeTerm` |
| `codex_term` | `CodexTerm` and its `notify` parsing |
| `dispatch` | adapter choice, `Unavailable`, `Runtimes` |

The `dah` CLI is split the same way in `agent_hub/cli/`: `common`, `lanes` (items, reports), `hub` (tasks, memos, decisions), `reading` (status, logs, stats), `running` (the tower loop), `setup` (init, update, install, guide), `manage` (lane, engine, member), `parser`, `main`. `agent_hub/cli/__init__.py` re-exports the names; tests patch the module where a name is looked up (for example `agent_hub.cli.running`).

## Adapter notes

- `Headless`: a session is an id; each prompt is one CLI run that resumes it, and the lane is busy while that run is alive. `compact` sends `/compact` as a whole print-mode run.
- Worktrees (`lane_cwd`): created on first use on branch `dah/<lane>` or `dah/<lane>-N`, from the workspace's current commit; an existing branch is checked out as it is. A lane in a worktree is told so before its first message, and gets read access to `.tower/` (lane files, protocol and briefs live there).
- `set_opts` keeps a task's own model and effort so every resume of the session runs with them.
- `usage(sid)` returns tokens in (cache included), tokens out, cost (USD or `None`) and `ctx_k`. `ledger(sid)` splits the same totals into new (input + cache writes), cache (cache reads), out and turns (`None` when the runtime does not count them); the default reads `usage()` and counts everything as new. `ClaudeTerm` reads the whole transcript for it (each API call once; a turn is one assistant message).
- `Terminal` / `TerminalSession`: the launcher script gives the window the lane's environment whatever the terminal app's own is. The lane's own `env` values go in a private file the launcher sources and deletes, never in the script. `dah signal` records a hook or notify in the signal file; busy/idle comes from it.
- `Claude` transcripts are found from the hooks, or at `~/.claude/projects/<cwd, non-alphanumerics as ->/<id>.jsonl`.
- `CodexTerm` uses the person's own `notify` program (from the lane args, their profile or `config.toml`) and passes each notification on through `dah signal`.
- `Host` subclasses: `open()` runs the launcher script in a new window in `cwd` and returns a handle naming one window. `Orca` drives Orca terminal tabs through its `orca` CLI.
- `Unavailable` stands in for a lane whose runtime an extension registered but none registers now (file removed or failing to load): reported as `unknown`, never opened. `Runtimes` holds one adapter per configured lane.
