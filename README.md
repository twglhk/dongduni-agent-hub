# agent-hub

A control tower and a phone-friendly review board for a team of AI coding agents.

One **hub** agent stays on, turns your requests into tasks, hands them to other agent sessions (**lanes**), reviews what comes back, and sends you only the calls that need a human. An **infra** agent looks after the system itself. You steer it all from a web board on your phone: pick an option, approve a result, write a memo. The tower in the middle is a small Python loop with no LLM in it; it opens, prompts, watches and closes the agent sessions.

It works with the agents you already use: **OpenCode**, **Claude Code** and **Codex**, mixed freely per lane.

```
 phone / laptop                         your machine or a server
┌──────────────┐   HTTPS (Cloudflare,  ┌─────────────────────────────────────────────┐
│ review board │ ◀─ Tailscale, AWS …) ─▶│ tower run                                   │
│  Hub · Infra │                        │  ├─ board (web, :8787)                      │
│  Lanes · Log │                        │  ├─ loop: every 4 s                         │
└──────────────┘                        │  │   hub ◀─ news, decisions, requests       │
                                        │  │   infra ◀─ one memo per fresh session    │
                                        │  │   team lanes ◀─ one task per session     │
                                        │  └─ SQLite: tasks · items · messages · …    │
                                        │                                             │
                                        │  opencode serve · claude -p · codex exec    │
                                        └─────────────────────────────────────────────┘
```

## Set it up with your agent

The intended setup is agent-driven, because every team keeps its agents, docs and servers somewhere different. Clone this repo, open your coding agent in it, and say:

> Set up agent-hub for my workspace. Follow SETUP.md.

The agent follows [SETUP.md](SETUP.md): it surveys how you work (`tower discover`: agent CLIs, their configs and MCP servers, where your knowledge lives, which hosting tools you have), asks you a handful of questions, installs the tower into your workspace with lane instructions tailored to it, starts it, puts the board where your phone can reach it, and checks the whole loop end to end.

## Or by hand

```sh
git clone https://github.com/twglhk/agent-hub && export PATH="$PWD/agent-hub/bin:$PATH"
cd ~/my-workspace
tower init --name "My Team" --hub opencode --infra claude   # writes .tower/
tower start                                                   # tower + board on http://127.0.0.1:8787
tower status
```

Python 3.10+ and nothing else (standard library only). Add `.tower/state/` to git ignore (`init` does it).

## Concepts

| | |
| --- | --- |
| **lane** | one agent role with its own session: `hub`, `infra`, and any team lanes you add (`tower lane add docs --runtime codex`). Instructions live in `.tower/lanes/<lane>.md`. |
| **task** | a brief the hub gives a team lane. Each task runs in a fresh session; the tower closes it when the lane wraps. |
| **item** | something a human or the hub must decide: a `decision` with options or a `review` of an artifact. Lanes post items; the hub decides what is in scope and escalates the rest to people. |
| **memo** | what people write on the board: to `infra` (the system) or to `hub` (the work). Each gets a reply on the board. |
| **follow-up** | a decided item on a lane's task becomes one new task in a fresh session, so decisions never pile into an old context. |

The full working rules every lane follows are in [`templates/PROTOCOL.md`](agent_hub/templates/PROTOCOL.md).

## Runtimes

| runtime | how the tower drives it | live `ask` to the board |
| --- | --- | --- |
| `opencode` | `opencode serve --port P` per lane, HTTP API. You can also run `opencode --port P` yourself and watch the TUI; the tower then uses it and never kills it. | yes |
| `claude` | `claude -p --session-id` to start, `claude -p --resume` for each prompt | no (lanes post items instead) |
| `codex` | `codex exec --json` to start, `codex exec resume` for each prompt | no |

Lanes run unattended, so by default Claude runs with `bypassPermissions` and Codex without its sandbox. `tower init --safe` writes sandboxed arguments instead; per-lane `args` in `tower.json` override either.

## Teams

`auth.mode` in `.tower/tower.json`:

- `local` (default): the board listens on 127.0.0.1 only; whoever can reach it is the owner.
- `header`: a proxy authenticates people and passes an identity header (Cloudflare Access, Tailscale Serve, an AWS ALB with OIDC). Only trusted from the local proxy. `tower member add ana --ident ana@company.com` turns it into an allow-list.
- `token`: `tower member add ana --token` prints a sign-in token for the board.

Every decision records who made it, and the hub can route an item to one person (`tower escalate ID --to ana`).

## Hosting

The tower runs wherever your agents run: your laptop, a dev box, an EC2 instance. To reach the board from a phone, see [deploy/](deploy): Cloudflare Tunnel + Access, Tailscale, or AWS.

## Development

```sh
python3 -m unittest discover -s tests -t .
```

Tests drive the adapters against stand-in CLIs and a stand-in OpenCode server; no model is called.

## License

MIT
