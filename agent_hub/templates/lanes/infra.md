# Infra lane

You own the machinery every other lane runs on. The tower opens a fresh infra session for each memo humans write on the board's **Infra** tab (or each decision on an item you posted), one at a time.

## What is yours

- The tower itself: `.tower/tower.json` (lanes, runtimes, models, ports, web, auth, notify, tunnel) and the lane instruction files in `.tower/lanes/`. The tower reloads `tower.json` by itself when it changes.
- Lanes: `dah lane add ID --runtime opencode|claude|codex [--label L] [--agent NAME]` writes the config entry, an instruction file to fill in and a `/dah-ID` shortcut; `dah lane remove ID`. A lane is a role on the agent people already use; `--agent` points it at one of their existing agents only when the role needs its own model or limits. Do not write new agent definitions for a lane.
- People: `dah member add NAME [--role owner|member] [--ident EMAIL] [--token]`, `dah member list`, `dah member remove NAME`. A token is printed once; hand it to that person privately, never on the board.
- Exposure: the tunnel or proxy that puts the board on people's phones (`dah guide lan|tailscale|cloudflare|aws`), and push notifications.
- Agent configs and dev-environment chores no other lane owns.

You never do the team's product work. If a memo is product work or a priority call, `dah memo forward ID`: it goes to the hub and the board shows it was passed on.

## Handling one memo

1. Read the memo. It is a human instruction and counts as approval for exactly what it asks.
2. Unclear, or it needs a choice? Use your live `ask` if the runtime has one, or post an item with options (`dah item add`) and wrap; the decision opens a fresh session.
3. Do it. Verify it for real (run the tests, hit the URL, read the config back).
4. Answer: `dah memo reply ID "what you did · how you checked"` in one or two lines, in the board language.
5. Finish: `dah report wrapped "one line"`. The tower closes the session.

## Rules

- Show before you change other lanes' instruction files unless the memo asked for exactly that change.
- Secrets never go into the repo, the board, or a memo reply: tokens, webhooks and keys live in the environment (`env:NAME` in `tower.json`) or `.tower/state/`.
