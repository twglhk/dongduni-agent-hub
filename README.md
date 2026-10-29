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

**Globally**: the agent installs the `dah` command for your user and runs `dah skill install`, which adds an `agent-hub-setup` skill (Claude Code) and command (OpenCode, Codex) to your agents after showing you the diff. From then on, "set up agent-hub here" works in any workspace.

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

## Extend it

agent-hub is a skeleton: build what your team needs on top of it in the workspace's `.tower/`, which lives in your git and survives updates. Ask infra on the board, or write it yourself (`dah guide extend`):

| | |
| --- | --- |
| `.tower/board/extra.js`, `extra.css` | sections, tabs and styles added to the board (`window.dah.addSection`, `addTab`, `get`, `post`, ...) |
| `.tower/board/index.html` | your own board instead of the built-in one (`?builtin` always opens the built-in board) |
| `.tower/ext/*.py` | API routes at `/api/x/<name>`, and tower hooks: `on_event` (reports, items, decisions, memos, goals) and `on_tick` |

Built-in routes and the tower's decisions stay the contract; a broken extension is logged and skipped. To change agent-hub itself, run it from a clone (see Development).

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

Each lane runs your agent as you configured it. When a role needs its own prompt, model or tool limits, point the lane at one of your existing agents (`dah lane add review --runtime claude --agent code-reviewer`, or `"agent"` in `tower.json`: an OpenCode agent name or a Claude `--agent`); `"model"` picks a model, `"args"` passes anything else (Codex: `["--profile", NAME]`). Lanes run unattended, so by default Claude runs with `bypassPermissions` and Codex without its sandbox; `dah init --safe` writes sandboxed arguments instead.

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
