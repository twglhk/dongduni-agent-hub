# AGENTS.md

Instructions for coding agents working in this repository.

- **Asked to set agent-hub up for someone (in this project or globally)?** Follow [agent_hub/guide/SETUP.md](agent_hub/guide/SETUP.md) (`tower guide` prints it). Do not improvise the order.
- **Changing agent-hub itself?** Read on.

## Layout

| path | what |
| --- | --- |
| `agent_hub/engine.py` | the tick: hub, infra inbox, team lanes, decision routing, live asks, goal prompts. No LLM. |
| `agent_hub/runtimes.py` | one adapter per agent runtime (opencode / claude / codex) |
| `agent_hub/store.py` | SQLite schema and all state changes (goals, tasks, items, messages, …) |
| `agent_hub/config.py` | `tower.json`, the workspace registry (`~/.config/agent-hub/workspaces.json`), port picking |
| `agent_hub/web.py` · `board/index.html` | board API (also serves other registered workspaces under `/w/<slug>/`) and the single-file board UI |
| `agent_hub/cli.py` | the `tower` command lanes and people use |
| `agent_hub/discover.py` · `install.py` | setup survey and workspace installer |
| `agent_hub/templates/` | protocol and lane instruction templates copied into workspaces |
| `agent_hub/guide/` | the setup playbook and the guides for reaching the board (`tower guide`); shipped in the package |
| `docs/images/` | README illustrations |

## Rules

- Standard library only, Python 3.10+. No new dependency without a strong reason.
- Every state change goes through `Store`. The engine must stay deterministic given a clock and a runtime; tests inject both (`tests/helpers.py`).
- A new runtime = one `Adapter` subclass with `status/open/send/close` (+ optional `questions`, `context_k`, `last_text`) and a stand-in CLI test in `tests/test_runtimes.py`. Never call a real model in tests.
- The board is one HTML file with no build step and no external requests. All UI strings live in its `UI` dict (en, ko, ja, zh, es; a new key goes into every language).
- `discover` reports names, counts and booleans only. A test plants secrets and fails if any leaks; keep it that way.
- Run `python3 -m unittest discover -s tests -t .` before you finish; it must print `OK`.
