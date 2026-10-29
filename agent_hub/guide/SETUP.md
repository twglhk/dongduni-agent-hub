# SETUP.md — agent playbook

You are a coding agent (OpenCode, Claude Code, Codex or similar) and a person asked you to set up agent-hub for them. Work through the phases in order. The goal: by the end, the tower runs in their workspace with a **hub** and an **infra** lane that fit how they already work, the board opens on their phone, their first goal is on it, and you have watched one memo go round trip.

`dah guide` prints this playbook; `dah guide lan|tailscale|cloudflare|aws` prints the guides for reaching the board.

## Ground rules

- Read before you write. Survey first; change nothing in phase 1.
- Ask only what the survey cannot answer, in one batch of at most seven short questions, each with your recommended answer first. Use your ask/question tool if you have one. If nobody can answer (a headless or scripted run), take the recommended answer for each and list the answers you took in your final message.
- Never print, copy or commit a secret (tokens, keys, webhooks, passwords). In `tower.json` refer to them as `env:NAME`. Never put one on the board.
- Installing software, opening the board to the internet, creating DNS records or cloud resources, and editing the person's global agent config are their call: show the exact command or diff and get a yes first.
- Prefer what they already have over something new: their agent, their tunnel, their chat, their docs.

## Phase 0 — Get the tower

agent-hub is the Python package `dongduni-agent-hub` (Python 3.10+, no dependencies, same package on Windows, macOS and Linux); it installs one command, `dah`. If `dah -h` already works, skip to the part below that matches their request. Two ways people ask:

- **"Install it in this project"**: nothing global. Install the package into a virtual environment inside the workspace (`dah init` adds `.tower/venv/` to the workspace's `.gitignore`):
  - with uv: `uv venv .tower/venv && uv pip install --python .tower/venv dongduni-agent-hub`
  - without uv: `python3 -m venv .tower/venv` (Windows: `py -m venv .tower\venv`), then `.tower/venv/bin/pip install dongduni-agent-hub` (Windows: `.tower\venv\Scripts\pip install dongduni-agent-hub`)

  Then call `.tower/venv/bin/dah` (Windows: `.tower\venv\Scripts\dah`) wherever this playbook says `dah`. Lanes the tower starts get it on their PATH automatically.
- **"Install it globally"**: the `dah` command for this user, plus a setup entry in each of their agents so "set up agent-hub here" works in any workspace later.
  1. Install the command with the tool they already have: `uv tool install dongduni-agent-hub` (uv fetches a Python if the machine has none; recommended on Windows and macOS) or `pipx install dongduni-agent-hub`. No uv or pipx: offer uv's one-line installer from https://docs.astral.sh/uv/. Check `dah -h` in a new terminal; if it is not found, `uv tool update-shell` (or `pipx ensurepath`) adds the tool folder to PATH.
  2. `dah skill install` shows the global `agent-hub-setup` skill (Claude Code) and command (OpenCode, Codex) it would write; with their yes, `dah skill install --yes`.
  3. If they named a workspace, or you are in one, continue with phase 1 for it. Every workspace set up this way is registered (`dah workspaces`), gets its own board port, and any of their boards switches to the others from its title.

Installing software and writing global agent files are their call: show the command, get a yes. Upgrading later: `uv tool upgrade dongduni-agent-hub`, `pipx upgrade dongduni-agent-hub`, or `pip install -U` in the project venv; then `dah stop && dah start`.

## Phase 1 — Survey how they work

1. `dah -h` runs (phase 0). On Windows, run the commands in PowerShell or Windows Terminal; paths in this playbook use `/`, which PowerShell accepts too.
2. Run `dah discover --project <their workspace>` and read the JSON. It lists agent CLIs and versions, each agent's global config (instruction files, commands, skills, agent names, plugins and their config files, Claude hook events, Codex profiles, MCP server names), the agents OpenCode actually resolves including plugin-provided ones (`resolved_agents`), what the workspace itself configures per agent (`project.agents`: `.mcp.json`, `opencode.json`, `.claude/`, `.opencode/`, `.codex/`), knowledge hints from MCP names (Notion, Linear, Slack, Roam, Google, GitHub …), the git remote and top-level layout, and hosting tools (cloudflared, tailscale, aws profiles, gh, docker). It never prints values.
3. Look deeper where it matters, read-only:
   - Their global and project instruction files (`~/.config/opencode/AGENTS.md`, `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, the workspace's `AGENTS.md` / `CLAUDE.md`): language they work in, conventions, rules every agent must follow, where decisions and docs are kept.
   - Commands, skills and subagents they already built: the hub should reuse them instead of reinventing them.
   - Where the work's source of truth lives: the repo, a wiki or notes MCP, an issue tracker, a design doc folder.
4. Write a short, secret-free summary to `<workspace>/.tower/environment.md` (create `.tower/` if needed): agents and which one they use most, instruction files that bind every agent, knowledge sources and how agents reach them, hosting tools available, team size. Infra sessions read it later.

## Phase 2 — Ask

Only what is still open, for example:

1. Which agent runs the hub and which runs infra? (Recommend the one they use most; OpenCode if they want live `ask` questions on the board.) Which model, and which of their own agents inside it (default: the runtime's default agent)? Agents with auto-continue hooks or loops make a poor hub: it must go idle between turns.
2. Board language? Any language code works: agents write everything people read in it. The board's own buttons are translated for `en`, `ko`, `ja`, `zh`, `es` (other codes show English buttons), and each viewer can switch them on the board.
3. Where does their team's memory live: decisions, notes, what shipped? (A memory MCP, a wiki, a docs folder, nothing.) agent-hub keeps no memory of its own beyond the board's history; lanes record in the place they name, and nowhere if they name none.
4. Just them, or a team? (Solo → `local` or `header`; team → `header` behind their SSO proxy, or `token`.)
5. How should their phone reach the board? (Recommend what they already have: Tailscale if installed, else Cloudflare Tunnel + Access if they have a domain on Cloudflare, else AWS if the agents should run on a server; the same Wi-Fi with token sign-in to try it quickly. See phase 5.)
6. Push notifications: ntfy, Slack webhook, desktop only, or none?
7. Unattended permissions (default) or sandboxed (`--safe`)? Explain in one line each.

## Phase 3 — Install

1. `cd <workspace> && dah init --name "<team>" --lang <code> --hub <runtime> --infra <runtime> [--safe]`. It picks a board port and OpenCode ports no other workspace of theirs uses, registers the workspace (`dah workspaces`), and writes `.tower/tower.json`, `.tower/PROTOCOL.md`, `.tower/lanes/hub.md`, `.tower/lanes/infra.md`, a `/dah-<lane>` shortcut per lane for each runtime in use (`.opencode/command/dah-hub.md`, `.claude/commands/dah-hub.md`, `.agents/skills/dah-hub/SKILL.md` for Codex) and ignores `.tower/state/`.
2. Edit `.tower/tower.json`: models in each runtime's own format (OpenCode `provider/model`, Claude an alias or full name, Codex a model id; use what they already use), optionally `"agent"` per lane to run one of their own agents (an OpenCode agent name or a Claude `--agent` name; leave it out for the default; Codex has none, use `"args": [..., "--profile", NAME]`), `notify` (`"ntfy_topic": "env:TOWER_NTFY_TOPIC"`, `"slack_webhook": "env:TOWER_SLACK_WEBHOOK"`), `auth`, `web.public_url`.
3. Tailor the lane files. Append a `## This workspace` section to `.tower/lanes/hub.md` and `.tower/lanes/infra.md` with what phase 1 found: where knowledge lives and how to reach it (which MCP server, which folder), **where decisions and results are recorded** (their answer to question 3: the memory MCP, wiki or docs folder and how it is normally used; if they have none, write "none: keep nothing beyond the board"), the instruction files every lane must also obey, existing commands/skills/subagents to use, the test and build commands, what must never be touched. Keep it short and concrete; no generic advice.
4. Make sure lanes get the same tools the person gets. Headless sessions cannot approve anything, so a workspace MCP server Claude Code has not approved yet (`project.agents.claude.mcp_not_approved` in the survey) never loads in a Claude lane. With their yes, add its name to `enabledMcpjsonServers` in the workspace's `.claude/settings.local.json` (or have them open `claude` there once and approve it), then check with `claude mcp list` in the workspace.
5. If their agent has a place for project rules, add one line there pointing at `.tower/PROTOCOL.md` so hand-opened sessions know the tower exists. Show the diff first.
6. Team lanes are optional at the start. Add them only if the person names them. A lane is a role on top of the agent they already use, not a new agent: `dah lane add <id> --runtime <r>` writes `.tower/lanes/<id>.md` (fill in: area, files it owns, done criteria, what not to touch) and a `/dah-<id>` shortcut so they can also open the role by hand. Only when a role needs its own model, prompt or tool limits, point it at one of *their* existing agents with `--agent NAME` (an OpenCode agent or a Claude Code `--agent`; Codex: `"args": ["--profile", NAME]`). Do not create new agent definitions for lanes.

## Phase 4 — Launch

1. `dah start` (background; log at `.tower/state/tower.log`) or `dah run` in a terminal tab they keep open.
2. `dah status` shows the hub opening within a few seconds, and the board URL (the port `init` picked). `curl -s localhost:<port>/api/state | head -c 300` answers.
3. Open `http://127.0.0.1:<port>` in a browser and check the four tabs render.
4. To keep it running across reboots, offer a launchd agent (macOS), a systemd user unit (Linux) or a Task Scheduler task at log-on (Windows: `schtasks /Create /SC ONLOGON /TN dah-<workspace> /TR "<full path to dah> run"` with the workspace as its start folder, or a shortcut in `shell:startup`) that runs `dah run` in the workspace; show it before installing.

## Phase 5 — Reach it from the phone

Pick with them, from what they already have:

| situation | path | guide |
| --- | --- | --- |
| phone on the same Wi-Fi, trying it out | the board on the network, token sign-in | `dah guide lan` |
| Tailscale installed (phone on the tailnet) | `tailscale serve`, identity header | `dah guide tailscale` |
| a domain on Cloudflare | Cloudflare Tunnel + Access | `dah guide cloudflare` |
| agents should run on a server | EC2 + one of the above, or an ALB with OIDC | `dah guide aws` |

1. Follow the guide. Opening the board beyond this machine is their call: show each command first.
2. Set `web.public_url` and `auth` in `tower.json`; add members (`dah member add NAME [--ident EMAIL] [--token]`; tokens are printed once, hand them over privately). Restart the tower (`dah stop && dah start`) when `web` or `auth` changed.
3. Check from outside the machine: `curl -s -o /dev/null -w "%{http_code}\n" <public_url>/api/state` answers (`401` without sign-in is right for token mode; through Access or Tailscale it needs their sign-in). Then have them open it on the phone and add it to the home screen.
4. More than one workspace: expose one board only. The others are reachable through it (`/w/<name>/`, switched from the board's title); a workspace in `local` auth mode is hidden behind a board that signs people in.

## Phase 6 — Prove the loop

0. Ask for their first goal or goals (first priority first) and add them in the board's **Goals** section (or `dah goal add "..." --detail "..."`). The hub proposes a plan as a board item; once they approve it the goal shows its steps and progress, and the first steps go to lanes.
1. From the board's Infra tab, send the memo: "Reply with the tower version and the lanes you see." Watch `dah status`: infra opens, replies (`dah memo reply`), wraps, closes. The reply shows under the memo.
2. From the Hub tab, send a request: "Post a review item asking me to approve the setup." The hub posts it; approve it on the phone; the hub gets the decision.
3. Check the hub goes idle again after each of these (`dah status` shows `idle` within a few minutes). If it stays `busy`, a hook or plugin keeps its session working (see `hooks`/`plugins` in the survey): give the hub lane another `"agent"` or runtime. The tower also posts a board item when news waits on a busy hub for 15 minutes.
4. Fix anything that did not work, then tell the person in their language: the board URL, how to sign in, how to set and rank goals, how to add a lane (a memo to infra), how to stop (`dah stop`), and where the logs are.

## Troubleshooting

- A lane stays `closed`: `.tower/state/logs/<lane>.err` has the CLI's error (not logged in, unknown flag, missing model).
- OpenCode lane never comes up: is the port free (`lsof -i :<port>`, ports start at `opencode_port` in `tower.json`)? Does `opencode serve --port <port>` work by hand?
- Two workspaces fight over a port: `dah workspaces` lists each board port; change `web.port` or `opencode_port` in one `tower.json`.
- A Claude lane does not see a workspace MCP server the person uses: `claude mcp list` in the workspace says "Pending approval". See Phase 3 step 4.
- Claude/Codex lane runs once and stops: check its permissions in `args`; a denied tool call ends the turn.
- The hub stays `busy` and news piles up: its agent keeps the session working (auto-continue hooks, loops). Set a lighter `"agent"` on the hub lane, or another runtime.
- The board says sign in on every load behind a proxy: the identity header name in `auth.header` does not match what the proxy sends.
