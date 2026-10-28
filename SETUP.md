# SETUP.md — agent playbook

You are a coding agent (OpenCode, Claude Code, Codex or similar) and a person asked you to set up agent-hub for them. Work through the phases in order. The goal: by the end, the tower runs in their workspace with a **hub** and an **infra** lane that fit how they already work, the board opens on their phone, and you have watched one memo go round trip.

## Ground rules

- Read before you write. Survey first; change nothing in phase 1.
- Ask only what the survey cannot answer, in one batch of at most six short questions, each with your recommended answer first. Use your ask/question tool if you have one.
- Never print, copy or commit a secret (tokens, keys, webhooks, passwords). In `tower.json` refer to them as `env:NAME`. Never put one on the board.
- Installing software, opening the board to the internet, creating DNS records or cloud resources, and editing the person's global agent config are their call: show the exact command or diff and get a yes first.
- Prefer what they already have over something new: their agent, their tunnel, their chat, their docs.

## Phase 1 — Survey how they work

1. Get the code on PATH: `export PATH="<this repo>/bin:$PATH"` (or `pipx install .`). Check `tower -h` runs.
2. Run `tower discover --project <their workspace>` and read the JSON. It lists agent CLIs and versions, each agent's config (instruction files, commands, skills, agents, plugins, MCP server names), knowledge hints from MCP names (Notion, Linear, Slack, Roam, Google, GitHub …), the workspace's git remote and top-level layout, and hosting tools (cloudflared, tailscale, aws profiles, gh, docker). It never prints values.
3. Look deeper where it matters, read-only:
   - Their global and project instruction files (`~/.config/opencode/AGENTS.md`, `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, the workspace's `AGENTS.md` / `CLAUDE.md`): language they work in, conventions, rules every agent must follow, where decisions and docs are kept.
   - Commands, skills and subagents they already built: the hub should reuse them instead of reinventing them.
   - Where the work's source of truth lives: the repo, a wiki or notes MCP, an issue tracker, a design doc folder.
4. Write a short, secret-free summary to `<workspace>/.tower/environment.md` (create `.tower/` if needed): agents and which one they use most, instruction files that bind every agent, knowledge sources and how agents reach them, hosting tools available, team size. Infra sessions read it later.

## Phase 2 — Ask

Only what is still open, for example:

1. Which agent runs the hub and which runs infra? (Recommend the one they use most; OpenCode if they want live `ask` questions on the board.) Which model?
2. Board language: `en` or `ko`?
3. Just them, or a team? (Solo → `local` or `header`; team → `header` behind their SSO proxy, or `token`.)
4. How should their phone reach the board? (Recommend what they already have: Tailscale if installed, else Cloudflare Tunnel + Access if they have a domain on Cloudflare, else AWS if the agents should run on a server. See `deploy/`.)
5. Push notifications: ntfy, Slack webhook, desktop only, or none?
6. Unattended permissions (default) or sandboxed (`--safe`)? Explain in one line each.

## Phase 3 — Install

1. `cd <workspace> && tower init --name "<team>" --lang <en|ko> --hub <runtime> --infra <runtime> [--safe]`. It writes `.tower/tower.json`, `.tower/PROTOCOL.md`, `.tower/lanes/hub.md`, `.tower/lanes/infra.md`, slash-command shims (`.opencode/command/hub.md`, `.claude/commands/hub.md` …) and ignores `.tower/state/`.
2. Edit `.tower/tower.json`: models in each runtime's own format (OpenCode `provider/model`, Claude an alias or full name, Codex a model id; use what they already use), `notify` (`"ntfy_topic": "env:TOWER_NTFY_TOPIC"`, `"slack_webhook": "env:TOWER_SLACK_WEBHOOK"`), `auth`, `web.public_url`.
3. Tailor the lane files. Append a `## This workspace` section to `.tower/lanes/hub.md` and `.tower/lanes/infra.md` with what phase 1 found: where knowledge and decisions live and how to reach them (which MCP server, which folder), the instruction files every lane must also obey, existing commands/skills/subagents to use, the test and build commands, what must never be touched. Keep it short and concrete; no generic advice.
4. If their agent has a place for project rules, add one line there pointing at `.tower/PROTOCOL.md` so hand-opened sessions know the tower exists. Show the diff first.
5. Team lanes are optional at the start. Add them only if the person names them: `tower lane add <id> --runtime <r>` then fill in `.tower/lanes/<id>.md` (area, files it owns, done criteria, what not to touch).

## Phase 4 — Launch

1. `tower start` (background; log at `.tower/state/tower.log`) or `tower run` in a terminal tab they keep open.
2. `tower status` shows the hub opening within a few seconds. `curl -s localhost:8787/api/state | head -c 300` answers.
3. Open `http://127.0.0.1:8787` in a browser and check the four tabs render.
4. To keep it running across reboots, offer a launchd (macOS) or systemd (Linux) unit that runs `tower run` in the workspace; show it before installing.

## Phase 5 — Reach it from the phone

Follow the matching guide in `deploy/` (Cloudflare, Tailscale or AWS). Then set `web.public_url` and the auth mode in `tower.json`, add members (`tower member add NAME [--ident EMAIL] [--token]`; tokens are printed once, hand them over privately), and restart the tower if `web` or `auth` changed.

## Phase 6 — Prove the loop

1. From the board's Infra tab, send the memo: "Reply with the tower version and the lanes you see." Watch `tower status`: infra opens, replies (`tower memo reply`), wraps, closes. The reply shows under the memo.
2. From the Hub tab, send a request: "Post a review item asking me to approve the setup." The hub posts it; approve it on the phone; the hub gets the decision.
3. Fix anything that did not work, then tell the person in their language: the board URL, how to sign in, how to add a lane (a memo to infra), how to stop (`tower stop`), and where the logs are.

## Troubleshooting

- A lane stays `closed`: `.tower/state/logs/<lane>.err` has the CLI's error (not logged in, unknown flag, missing model).
- OpenCode lane never comes up: is the port free (`lsof -i :4301`)? Does `opencode serve --port 4301` work by hand?
- Claude/Codex lane runs once and stops: check its permissions in `args`; a denied tool call ends the turn.
- The board says sign in on every load behind a proxy: the identity header name in `auth.header` does not match what the proxy sends.
