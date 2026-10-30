# dongduni-agent-hub

**Stop typing to every agent. Set the goal; the hub runs the lanes.**

How long will you keep five terminals open, re-explaining context to each agent session, spawning subagents by hand, and digging through scrollback to find out who finished what? agent-hub turns that around. You write down what you want done and in which order. One always-on **hub** agent plans it with you, hands the work to agent sessions it controls (**lanes**: OpenCode, Claude Code or Codex, mixed freely), checks what comes back, and reports to a review board on your phone. You only step in for the calls that need a human.

![Without agent-hub: you type to every terminal and keep the state in your head. With agent-hub: you set and rank goals on a board; the hub assigns work to lanes and reports back.](https://raw.githubusercontent.com/twglhk/dongduni-agent-hub/main/docs/images/why.svg)

## What you get

- **Goals, not prompts.** Add as many goals as you like, rank them, pause or drop them. The board shows which goal the team is on now and how far each one is (its achievement rate is the share of its agreed steps that are done).
- **A hub that runs the team.** It agrees a plan with you, splits it into tasks, starts a fresh agent session per task (up to `max_lanes` at once), nudges stalled lanes, reviews results against the real output, and decides what is inside the approved scope.
- **Lanes that work on their own.** Each lane is your agent as you configured it, with your instruction files, skills, MCP servers, hooks and subagents. It reports `done`, `blocked` or posts a decision; it never waits in chat.
- **A board instead of chat.** Decisions with options, reviews of artifacts (images, video, text inline), memos to the hub or to infra, history. Built for a phone; translated into English, 한국어, 日本語, 中文 and Español.
- **Your setup, your rules.** Lane instructions, runtimes, models, agents, permissions, notifications, auth and hosting are plain files you (or the infra lane) edit. The tower in the middle is a small Python loop with no LLM in it.

![The loop: you set goals, the hub plans them with you, tasks go to lanes, lanes work and report, the hub reviews and asks you only what needs a human.](https://raw.githubusercontent.com/twglhk/dongduni-agent-hub/main/docs/images/how.svg)

<p align="center">
  <img src="https://raw.githubusercontent.com/twglhk/dongduni-agent-hub/main/docs/images/board-goals.png" width="300" alt="The board's Hub tab: ranked goals with progress, the goal in progress marked Now">
  &nbsp;&nbsp;
  <img src="https://raw.githubusercontent.com/twglhk/dongduni-agent-hub/main/docs/images/board-decide.png" width="300" alt="A decision on the board: options, recommendation, Decide button">
</p>

> **Agents:** asked to install or set up agent-hub / dongduni-agent-hub? Install the package (`uv tool install dongduni-agent-hub`, or `pipx install dongduni-agent-hub`; for one project only, see [Install](#install)), then run `dah guide` and follow that playbook phase by phase. It is also [online](https://github.com/twglhk/dongduni-agent-hub/blob/main/agent_hub/guide/SETUP.md).

## Install

One package for Windows, macOS and Linux: [`dongduni-agent-hub`](https://pypi.org/project/dongduni-agent-hub/) on PyPI. It needs Python 3.10+ and nothing else, and installs one command, **`dah`**.

```sh
uv tool install dongduni-agent-hub     # recommended: uv brings its own Python if you have none
pipx install dongduni-agent-hub        # or, if you use pipx
```

No uv yet? It is one line: `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS, Linux) or `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` (Windows). Check with `dah -h`; upgrade with `uv tool upgrade dongduni-agent-hub`.

Only for one project, without touching your user: `uv venv .tower/venv && uv pip install --python .tower/venv dongduni-agent-hub`, then run `.tower/venv/bin/dah` (`.tower\venv\Scripts\dah` on Windows).

## Set it up: ask your agent

Setup is agent-driven, because every team keeps its agents, docs and servers somewhere different. Open your coding agent in your workspace and say one of:

> Install dongduni-agent-hub in this project.

> Install dongduni-agent-hub globally.

**In this project**: the agent follows the setup playbook (`dah guide`): it surveys how you work (`dah discover`: agent CLIs, their configs, MCP servers, skills, where your knowledge lives, hosting tools), asks you a handful of questions (which agents run the hub and infra, board language, where your team's memory lives, how your phone should reach the board), writes `.tower/` with lane instructions tailored to your workspace, starts the tower, adds your first goals, and checks the whole loop end to end.

**Globally**: the agent installs the `dah` command for your user and runs `dah skill install`, which adds an `agent-hub-setup` skill (Claude Code, Codex) and command (OpenCode) to your agents after showing you the diff. From then on, "set up agent-hub here" works in any workspace.

If your agent cannot find the playbook, point it at [agent_hub/guide/SETUP.md](https://github.com/twglhk/dongduni-agent-hub/blob/main/agent_hub/guide/SETUP.md).

### By hand

```sh
cd ~/my-workspace
dah init --name "My Team" --lang en --hub claude --infra claude   # writes .tower/, picks free ports
dah start                                                          # tower + board, URL printed
dah goal add "Launch the public beta" --detail "sign-up open, onboarding under 2 minutes"
dah status
```

## Update

```sh
dah update            # in the workspace: upgrade dah, bring .tower/ up to date, restart the tower
dah update --all      # every workspace on this machine
```

It upgrades the package the way you installed it (uv tool, pipx, a project venv), then brings each workspace to the new version without losing your setup: protocol and lane files whose template you only added to get the new template with your sections kept, files you edited get the new template beside them as `.new` and a request to infra to merge them, and known `tower.json` fixes are applied. The tower loop restarts on the new code; lane sessions keep running. You can also ask infra on the board to do it. Nothing updates by itself.

Package files someone changed in place (the installed board, its Python) are kept before the upgrade replaces them: copies and a diff against the release go to `.tower/patches/<version>/`, and infra gets a request to rebuild them as extensions. `dah update --dry-run` lists them.

To go back to a release: `uv tool install --force dongduni-agent-hub==X.Y.Z` (or `pipx install --force ...`), then `dah update --no-upgrade`.

## Uninstall

```sh
dah uninstall                 # shows what it would remove; changes nothing
dah uninstall --yes           # stop the tower, close lane sessions, take agent-hub out of the workspace
dah uninstall --yes --purge   # ... and delete .tower/ too
dah skill uninstall --yes     # the global agent-hub-setup skill/command, if you installed it
uv tool uninstall dongduni-agent-hub   # or: pipx uninstall dongduni-agent-hub
```

`dah uninstall` removes only what agent-hub wrote: the `/dah-<lane>` shortcuts still as written (edited ones stay), its `.gitignore` lines, lane worktrees without uncommitted work (their `dah/<lane>` branches stay), and the workspace's entry in this machine's list. `.tower/` (config, lane files, extensions, board history) stays unless you pass `--purge`. It lists lines in your `AGENTS.md` / `CLAUDE.md` that still point at `.tower/` for you to remove.

## Extend it

agent-hub is a skeleton: build what your team needs on top of it in the workspace's `.tower/`, which lives in your git and survives updates. Ask infra on the board, or write it yourself (`dah guide extend`):

| | |
| --- | --- |
| `.tower/board/extra.js`, `extra.css` | sections, tabs and styles added to the board (`window.dah.addSection`, `addTab`, `get`, `post`, ...) |
| `.tower/board/index.html` | your own board instead of the built-in one (`?builtin` always opens the built-in board) |
| `.tower/ext/*.py` | API routes at `/api/x/<name>`; tower hooks: `on_event` (reports, items, decisions, memos, goals, tasks, lane status), `on_notify` (every push to people, e.g. to another chat) and `on_tick`; new agent runtimes and terminal apps (`runtimes`, `terminals`) |

Built-in routes and the tower's decisions stay the contract; a broken extension is logged and skipped. Updates never touch `.tower/`; when the built-in board changes, `dah update` asks infra to check the workspace's board additions (the guide lists which parts stay stable). To change agent-hub itself, run it from a clone (see Development).

## One board, every project

Each workspace has its own tower, state and board port. `dah init` registers it on this machine (`dah workspaces` lists them), and every board can switch to the others from its title: other workspaces are served under `/w/<name>/`. Expose one board and you reach all your projects from the phone; a workspace that only trusts local visitors stays hidden behind a board that signs people in.

## Reach the board from your phone

The board listens on `127.0.0.1` by default. `dah guide <name>` prints each path:

| situation | path |
| --- | --- |
| phone on the same Wi-Fi, trying it out | `dah guide lan`: listen on the network, token sign-in |
| Tailscale on the machine and the phone | `dah guide tailscale`: `tailscale serve`, identity from Tailscale |
| a domain on Cloudflare | `dah guide cloudflare`: Tunnel + Access (email code, Google, GitHub, Okta …) |
| agents on a server | `dah guide aws`: EC2 with one of the above, or an ALB with OIDC |

Push notifications (ntfy, Slack, desktop) tell you when something needs you.

## Concepts

| | |
| --- | --- |
| **goal** | an outcome you want, ranked against the others. The first active goal is what the team works on now. The hub agrees its steps with you; progress is the share of steps done. Add, rank, pause, finish and drop goals on the board or with `dah goal`. |
| **hub** | the always-on lane. Turns goals and requests into tasks, reviews results, decides inside approved scope, escalates the rest to people. |
| **lane** | a role with its own session: `hub`, `infra`, and any team lanes you add (`dah lane add docs --runtime codex`). A lane is not a new agent: it is a role file (`.tower/lanes/<lane>.md`, edit freely) put on the agent you already use, with your hooks, skills and plugins. Every lane also gets a `/dah-<lane>` shortcut so you can open the role by hand in Claude Code, OpenCode or Codex. |
| **task** | a brief the hub gives a team lane, usually for one step of a goal. Each task runs in a fresh session; the tower closes it when the lane wraps. |
| **item** | something a human or the hub must decide: a `decision` with options or a `review` of an artifact. |
| **memo** | what people write on the board: to the `hub` (the work, priorities) or to `infra` (the system: lanes, models, access). Each gets a reply on the board. |
| **infra** | the lane that maintains the tower itself: adds lanes, changes models, sets up access, one memo per fresh session. |

**Memory is yours.** agent-hub keeps the board's history (goals, decisions, reports, memos) and nothing else. Where your team keeps decisions and notes (a memory MCP, a wiki, a docs folder) is your choice: setup writes it into the lane instructions, and lanes record there the way you normally do.

The working rules every lane follows are in [`templates/PROTOCOL.md`](https://github.com/twglhk/dongduni-agent-hub/blob/main/agent_hub/templates/PROTOCOL.md).

## Runtimes

| runtime | how the tower drives it | live `ask` to the board |
| --- | --- | --- |
| `opencode` | `opencode serve --port P` per lane, HTTP API. You can also run `opencode --port P` yourself and watch the TUI; the tower then uses it and never kills it. | yes |
| `claude` | `claude -p --session-id` to start, `claude -p --resume` for each prompt | no (lanes post items instead) |
| `codex` | `codex exec --json` to start, `codex exec resume` for each prompt | no |

Another agent CLI can be added as an extension: a `runtimes` entry in `.tower/ext/*.py` (`dah guide extend`), then `"runtime": "<its name>"`.

Each lane runs your agent as you configured it. When a role needs its own prompt, model or tool limits, point the lane at one of your existing agents (`dah lane add review --runtime claude --agent code-reviewer`, or `"agent"` in `tower.json`: an OpenCode agent name or a Claude `--agent`); `"model"` picks a model, `"args"` passes anything else (Codex: `["--profile", NAME]`). Lanes run unattended, so by default Claude runs with `bypassPermissions` and Codex without its sandbox; `dah init --safe` writes sandboxed arguments instead.

### Lanes in terminal windows

By default lane sessions run in the background and you follow them on the board. To watch them work, set `"terminal"` in `tower.json` (for every lane, or per lane) or pass `dah init --terminal`:

| `terminal` | where a lane's session runs |
| --- | --- |
| `none` (default) | in the background, as in the table above |
| `tmux` | a window per lane in the tmux session `dah-<workspace>` (`tmux attach -t dah-<workspace>`; `dah status` prints it). Linux, macOS, WSL. |
| `orca` | a terminal tab per lane in [Orca](https://github.com/stablyai/orca), opened with its `orca` CLI; Orca shows each lane's state in its sidebar |
| any other name | a terminal app an extension registers (`terminals` in `.tower/ext/*.py`) |

The tower opens the window when a lane starts, gives it its briefs (written to a file, the window gets one line pointing at it), and closes it when the lane is done; `dah stop --close-lanes` closes them all. The agent runs interactively with the same configuration and arguments, so you can scroll, read along and type to it. The tower knows when it works or waits from Claude Code hooks and Codex `notify`, both added for that session only (your own Claude hooks still run, and your own Codex `notify` program still gets every notification); OpenCode runs its TUI, which the tower drives over HTTP as before. With `--safe` arguments an agent in a window stops at its own permission prompts until someone answers there; the lane shows as `asking` and the tower sends one alert.

Good to know: open each agent once by hand in the workspace first, so first-run screens (folder trust, accepting `bypassPermissions`) are out of the way; a lane cannot answer them. Changing a lane's `terminal` or `runtime` closes its live session, which then reopens the new way. Codex reports only finished turns, so the tower does not see a turn a person starts or interrupts in a Codex window; Claude Code reports both.

### Lanes in their own git worktree

Team lanes share one checkout with each other and with you by default. Set `"worktree": true` on a team lane (or at the top level for every team lane; `dah lane add ID --worktree`) and its sessions run in a git worktree of their own, `.tower/state/worktrees/<lane>` on branch `dah/<lane>`, created from your current commit the first time the lane opens. The lane commits there and reports the branch; the hub reviews it (`git diff HEAD...dah/<lane>`) and merges it, or asks you on the board. Nothing lands in your checkout by itself. With `terminal: orca` the lane's tab opens in that worktree. The hub and infra always work in the workspace itself.

## Requirements and limits

- Python 3.10+ for `dah`, and at least one agent CLI (OpenCode, Claude Code or Codex) installed and signed in. Lanes use your agent accounts: every lane session counts against your subscription or API usage.
- By default lanes run unattended with your user's rights: Claude with `bypassPermissions`, Codex without its sandbox. `dah init --safe` writes sandboxed arguments; then a lane stops at anything not allowed (`--allowedTools` in its `args`), and a lane in a window waits at the prompt until someone answers.
- Lanes without `"worktree"` edit the same checkout as each other and as you.
- Claude lane sessions are ordinary Claude Code sessions: they show up in `claude --resume` for the workspace. The `/dah-<lane>` shortcuts live in the workspace (`.claude/commands/`, `.opencode/command/`, `.agents/skills/`), so they go into its git unless you ignore them.
- Live `ask` to the board works with OpenCode only; Claude and Codex lanes post board items instead.
- Lane windows: tmux on Linux, macOS and WSL; Orca anywhere it runs, and the only choice on Windows. Orca support is tested against a stand-in of its CLI, not a real Orca yet. Codex in a window reports finished turns only.
- The board reaches your phone only through a path you set up (`dah guide`); by default it listens on this machine.

## Language

`--lang` takes any language code: agents write everything people read on the board in that language. The board's buttons and labels come in English, Korean, Japanese, Chinese and Spanish; each viewer can switch them from the board's header.

## Teams

`auth.mode` in `.tower/tower.json`:

- `local` (default): the board listens on 127.0.0.1 only; whoever can reach it is the owner.
- `header`: a proxy authenticates people and passes an identity header (Cloudflare Access, Tailscale Serve, an AWS ALB with OIDC). Only trusted from the local proxy. `dah member add ana --ident ana@company.com` turns it into an allow-list.
- `token`: `dah member add ana --token` prints a sign-in token for the board.

Every decision records who made it, and the hub can route an item to one person (`dah escalate ID --to ana`).

## Development

```sh
git clone https://github.com/twglhk/dongduni-agent-hub && cd dongduni-agent-hub
python3 -m unittest discover -s tests -t .
bin/dah -h          # runs from the clone without installing
```

Releasing (from any machine with push access): [docs/RELEASING.md](https://github.com/twglhk/dongduni-agent-hub/blob/main/docs/RELEASING.md). Tests drive the adapters against stand-in CLIs and a stand-in OpenCode server; no model is called. See [AGENTS.md](https://github.com/twglhk/dongduni-agent-hub/blob/main/AGENTS.md) for the layout and rules.

## License

MIT
