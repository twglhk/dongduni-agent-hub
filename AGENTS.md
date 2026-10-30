# AGENTS.md

Instructions for coding agents working in this repository.

- **Asked to set agent-hub up for someone (in this project or globally)?** Follow [agent_hub/guide/SETUP.md](agent_hub/guide/SETUP.md) (`dah guide` prints it). Do not improvise the order.
- **Changing agent-hub itself?** Read on.

## Layout

| path | what |
| --- | --- |
| `agent_hub/engine.py` | the tick: hub, infra inbox, team lanes, decision routing, live asks, goal prompts. No LLM. |
| `agent_hub/runtimes.py` | one adapter per agent runtime (opencode / claude / codex), in the background or in a terminal window (tmux, Orca) |
| `agent_hub/store.py` | SQLite schema and all state changes (goals, tasks, items, messages, …) |
| `agent_hub/config.py` | `tower.json`, the workspace registry (`~/.config/agent-hub/workspaces.json`, `%APPDATA%\agent-hub` on Windows), port picking |
| `agent_hub/procs.py` | starting, checking and stopping processes on Linux, macOS and Windows; npm shims on Windows |
| `agent_hub/ext.py` | workspace extensions: `.tower/board/` files and override, `.tower/ext/*.py` routes and engine hooks |
| `agent_hub/web.py` · `board/index.html` | board API (also serves other registered workspaces under `/w/<slug>/`) and the single-file board UI |
| `agent_hub/cli.py` | the `dah` command lanes and people use |
| `agent_hub/discover.py` · `install.py` | setup survey and workspace installer |
| `agent_hub/templates/` | protocol and lane instruction templates copied into workspaces |
| `agent_hub/guide/` | the setup playbook, the extension guide and the guides for reaching the board (`dah guide`); shipped in the package |
| `docs/images/` | README illustrations |

## Rules

- Standard library only, Python 3.10+. No new dependency without a strong reason.
- Every state change goes through `Store`. The engine must stay deterministic given a clock and a runtime; tests inject both (`tests/helpers.py`).
- A new runtime = one `Adapter` subclass with `status/open/send/close` (+ optional `questions`, `context_k`, `last_text`) and a stand-in CLI test in `tests/test_runtimes.py` (a workspace can also register one from `.tower/ext/`, without changing the package) (terminal windows: `tests/test_terminal.py`, against a stand-in terminal app). Never call a real model in tests.
- The board is one HTML file with no build step and no external requests. All UI strings live in its `UI` dict (en, ko, ja, zh, es; a new key goes into every language).
- Changing a template in `agent_hub/templates/` (PROTOCOL.md, lanes/*.md): append its new text to `templates/shipped.json` (oldest first, newest last). `dah update` uses those texts to tell a workspace's untouched copy from an edited one; a test fails until you do.
- Extension points (`ext.py`: `window.dah`, `/x/`, `/api/x/`, `routes`, `on_event` kinds, `on_notify`, `on_tick`, `runtimes` and `terminals` with the `Adapter` / `Host` methods EXTEND.md lists) are a contract workspaces build on: extend them, do not rename or drop them without a major version. Keep `guide/EXTEND.md` in step.
- `discover` reports names, counts and booleans only. A test plants secrets and fails if any leaks; keep it that way.
- It runs on Linux, macOS and Windows. Start, check and stop processes only through `procs.py`; never `os.kill(pid, 0)` (it kills on Windows), `os.killpg` or `start_new_session` directly. Read and write text files with `encoding="utf-8"`.
- Releases: follow [docs/RELEASING.md](docs/RELEASING.md) (bump `__version__`, push tag `vX.Y.Z`; CI tests on all three systems and publishes `dongduni-agent-hub` to PyPI).
- Run `python3 -m unittest discover -s tests -t .` before you finish; it must print `OK`.
