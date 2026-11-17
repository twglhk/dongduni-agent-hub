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
| `adapter` | the `Adapter` base class |
| `opencode` | `OpenCode` and `OpenCodeTerm` |
| `headless` | `Headless`, `Claude`, `Codex` (print mode) |
| `hosts` | `Host`, `Tmux`, `Orca` |
| `terminal` | window mixins `Terminal` / `TerminalSession` and the signal file |
| `claude_term` | `ClaudeTerm` |
| `codex_term` | `CodexTerm` and its `notify` parsing |
| `dispatch` | adapter choice, `Unavailable`, `Runtimes` |

The `dah` CLI is split the same way in `agent_hub/cli/`: `common`, `lanes` (items, reports), `hub` (tasks, memos, decisions), `reading` (status, logs, stats), `running` (the tower loop), `setup` (init, update, install, guide), `manage` (lane, engine, member), `parser`, `main`. `agent_hub/cli/__init__.py` re-exports the names; tests patch the module where a name is looked up (for example `agent_hub.cli.running`).
