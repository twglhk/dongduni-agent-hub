# Port plan: the production loop from the source tower into agent-hub

Status: plan only (step 1 of the chain). No agent-hub code changes are made by this document.
Source: the founder's own production tower (`tools/tower/*` in the source repo, ~19K lines: tower, board, ledgers). Target: this repo, package `dongduni-agent-hub` 0.7.0.
Goal: agent-hub becomes a general **process-driven production loop**: several **engines** (for example an animation engine, a marketing engine) run in one install; each engine is lanes + skills + process cards + a pattern library; the board shows each engine, each lane, which process it runs and fixes, with which model, effort and tokens. A teammate can build a new engine from it without game words anywhere.

## Decisions taken from the founder's request (verbatim intent)

1. Remove the **goals** feature. Keep only one always-pinned bottom chat, **"Request to the hub"**.
2. Remove the **Infra tab**. Infra runs only on a hub request.
3. Add an **AI Engine** tab and support **several engines** in one install.
4. A **per-lane view**: skills and process it runs, which process and stage it is fixing, model and effort, tokens.
5. Lift everything generic in the source UI/UX and engine into agent-hub.
6. No release: no PyPI publish and no `v*` tag without the founder's own OK.

## 1. Feature map

Verdict: **port** = generic, goes in; **leave** = specific to the source product; **there** = agent-hub already has it (maybe needs a small change).

| # | Source feature (where) | agent-hub today | Verdict |
| --- | --- | --- | --- |
| 1 | Tower loop: tick, lane sessions, nudges, respawn, rotate with hand-over (`tower.py`, `engine.py`, `runtime*.py`) | `engine.py`, `runtimes.py` (opencode, claude, codex; terminal windows) | there |
| 2 | Store: tasks, items, messages, reports, lanes (`store.py`) | `store.py` with the same tables plus goals | there |
| 3 | Review board: decisions, reviews, history, phone layout, five languages (`board.html`, `web.py`) | `board/index.html`, `web.py` | there |
| 4 | Tabs of the source board: Make (lanes, asks, decisions, pinned request chat), Marketing, Posts, Story, Wiki, AI Engine, Schedule, All | tabs: Hub, Infra, Lanes, History | port the chat and AI Engine; leave Story, Wiki, Posts, Schedule, build cards (extension material, see `dah guide extend`) |
| 5 | Pinned bottom chat "request to direction" (Make tab, last block, always visible) | a memo sheet on the Hub and Infra tabs, not pinned | port (change a) |
| 6 | Direction-to-infra requests (`tower infra "…"`, `tower memo reply`; infra opens one fresh session per request, oldest first, never injected into a busy session) | humans write to infra on the Infra tab; hub can `dah memo send infra` | port the rule "infra only via hub"; the memo plumbing is there (change b) |
| 7 | Goals | goals table, `dah goal`, Hub goals tab, hub prompt, README pitch | removed (change a). The source has no goals feature |
| 8 | Lanes = a role + skills + process cards (agent files, `.opencode/skills/*`, `references/process/*.md`) | lane = role file `.tower/lanes/<lane>.md`; no skills/process concept in the tower | port as a convention + reader (items 9, 10) |
| 9 | Process card: first line `# Process: <type> — vN`, `Status:` line, stage table; pattern card per stage (`process_view.py`) | none (the playbook describes it as prose) | port |
| 10 | Pattern library table: one row per deliverable type: lane, skill, process status, pattern status, per-stage `model · effort` | `stage_table` (stagemodel.py) reads only the model column | port: extend the same file |
| 11 | Line header on a task brief (`line.py`): type, row, stage, process and pattern status; refuses a task whose process or pattern is unconfirmed | none | port, optional switch (`require_line`) |
| 12 | Per-task model and effort from the row's stage, brief lines win (`stagemodel.py`) | same, v0.7.0 | there |
| 13 | `tower stage N`: the lane names its current stage; the board shows it (`taskproc.py`) | none | port (change d) |
| 14 | Stage rating: first-pass rate per stage, bars (new / optimal / good / needs work / needs refactor), per model and effort, token cost per attempt and per adopted deliverable (`rating.py`) | none | port |
| 15 | Blame: the lane names the stage that should have caught a failure; the hub confirms (`tower blame`) | none | port |
| 16 | Escape ledger: failures a human found that review had passed | game-feedback files | port the idea as a generic `dah deliverable` verdict log; leave the file format |
| 17 | Deliverables: key per artifact, verdict history, lineage (`tower tag`, `tower deliverable`) | items carry no artifact key | port |
| 18 | Task ledger: tokens (new / cache / out) and turns per task, read from the agent transcripts (`ledger.py`, `tokenlog.py`) | `usage` table by lane, session, day (`record_usage`) | port: add per-task totals |
| 19 | Stats: per lane / deliverable / week, tokens per adopted deliverable, rounds per adopted deliverable (`stats.py`, `tower stats`) | none | port |
| 20 | Retros: `wrapped "one line \|\| retro: cost=… \| corrected=… \| rule=…"` (`ledger.py`, `tower retros`) | `report wrapped "one line"` | port |
| 21 | AI Engine tab: one map of lanes, skills, tools, experiments, metrics, what changed (`studio.py`, `studio*.js`) | none | port the map reader; its hand-written part (`engine-architecture.json`) becomes per-engine `engine.json` |
| 22 | Weekly snapshots: "what changed since last week" for the map and for process cards (`studio` snapshot, `process_changes.py`, `weekly.py`) | none | port |
| 23 | Process-per-lane view (`process_view.py`: each lane as a pipeline of stages, status chips) | Lanes tab lists lanes with status and tasks | port (change d) |
| 24 | Multi-session lanes: one part lane runs several sessions, `lane#2` instances, `max_instances` | one session per lane | port |
| 25 | Declared `paths:` per task; two tasks of one lane run together only if paths do not overlap (`engine.conflict_of`) | `worktree: true` per lane (isolated checkout) | port; complements worktrees |
| 26 | Head-of-line skip: a blocked first task (path conflict, cap, no memory) does not stop later tasks (`engine._start_queued`) | one `next_queued` per lane, FIFO | port |
| 27 | Light / heavy slots: heavy tasks (build, render) count against `max_lanes`, light ones (research, writing) against `light_lanes` | one `max_lanes` | port; "heavy" is declared in config (path roots or a brief tag), not hard-coded |
| 28 | Usage-based cap: the weekly subscription percentage lowers or raises the lane cap (`tower.read_usage`, `usage.py`) | usage records only | port as an optional provider (Claude subscription API); the cap function is generic |
| 29 | Memory guard: lanes over a memory limit are renewed when idle; admission by memory pressure (`memwatch.py`, `pressure.py`) | none | port (generic, small) |
| 30 | Linked tasks share a session (`linked`, `after: T`) | none | port |
| 31 | Decision to follow-up task: every decided item becomes one fresh queued task (`decision_dispatch.py`) | decision routing in `engine.py` | there (compare in request 4) |
| 32 | Usage bar on the board (5h and weekly %, tokens today) (`usage.py`) | `usage_view`, per lane tokens | port the bar; subscription % optional |
| 33 | Audit lane: wakes on feedback, weekly review, monthly full review (`audit_wake.py`) | none | port as a generic "engine review" lane template, schedule by config |
| 34 | Weekly distill, daily build, night check, wishlist, YouTube/X/Reddit bridges, wiki, story map, builds, studio of heroes, Godot slots, Blender detect | none | leave (game or channel specific); extension points stay |
| 35 | Extensions (`window.dah`, `/api/x/`, `on_event`) | `ext.py` | there |
| 36 | Engine playbook (methods) | `docs/engine-playbook.md` (done in c13e28e) | there |

## 2. The four explicit changes

### (a) Remove goals; keep only the pinned bottom chat "Request to the hub"

Delete or change:

| File | Change |
| --- | --- |
| `agent_hub/store.py` | stop creating/using the `goals` table API (`add_goal`, `goals`, `rank`, `plan`, `tick`); keep the table in old DBs untouched (no destructive migration); `tasks.goal_id` stays as an ignored nullable column |
| `agent_hub/cli.py` | remove `dah goal …`; `dah task --goal` prints a one-line notice and is ignored (one release), `dah status` drops the goal block |
| `agent_hub/engine.py` | remove the goal prompts ("the tower sends you the goal list when nothing is queued"), the goal news kind, goal-based pickup |
| `agent_hub/web.py` | remove `/api/goals*`, goals from `/api/state` and `/api/history`; answer 410 for one release |
| `agent_hub/board/index.html` | remove the Hub tab goals section, the history Goals list and the goals strings in all five languages; add the pinned chat (below) |
| `agent_hub/ext.py`, `guide/EXTEND.md` | `on_event` no longer fires `goal`; document it |
| `agent_hub/templates/lanes/hub.md`, `templates/PROTOCOL.md`, `templates/shipped.json` | rewrite the hub's "Goals" section as "Requests"; append the new text to `shipped.json` (a test enforces it) |
| `agent_hub/guide/SETUP.md` | the "add your first goals" step becomes "send your first request" |
| `README.md`, `docs/images/why.svg`, `how.svg`, `board-goals.png` | the pitch is built on goals ("Set the goal; the hub runs the lanes"); replace with the engine pitch; new board images; `docs/RELEASING.md` and `AGENTS.md` mentions |
| tests | remove goal tests (`test_cli`, `test_store`, `test_engine`, `test_web`, `test_ext`); add tests for the 410s and for a DB that still has goals |

Pinned chat: one input fixed at the bottom of every tab (above the nav), sends to the hub (`POST /api/memo` with `to: hub`), shows the last reply inline, opens the thread in a sheet. The Hub tab keeps decisions and reviews waiting for a human. No goal ranking anywhere.

### (b) Remove the Infra tab; infra runs only on a hub request

| File | Change |
| --- | --- |
| `board/index.html` | remove the `infra` tab, `v-infra`, its items and memo box; the `TABS` list becomes Hub, Engine, Lanes, History; infra items and replies show in History as "Infra requests" |
| `web.py` | `POST /api/memo` accepts `to: hub` only from the board; `to: infra` is refused (409 with "ask the hub") |
| `cli.py` | `dah memo send infra …` stays for the hub (and `dah infra "…"` as the short form, named like the source) with the rule: only the hub lane may send; a request carries the human's approval citation field `--approved "request #N"` for instruction or role changes |
| `engine.py` | infra inbox unchanged (one fresh session per request, oldest first); infra replies reach the hub as news, and the hub tells the human in its reply to the chat |
| `templates/lanes/infra.md`, `hub.md`, `PROTOCOL.md`, `shipped.json` | infra "wakes only on a hub request"; the hub instruction gains "send board, lane or model changes to infra, quote the human's words" |
| board strings | remove `infra*`, the "Ask infra" tips and the lane-add hint wording ("replies on the Infra tab"); "Add lane" sends a hub request instead |
| tests | `test_web` memo routing, `test_engine` infra inbox |

Open question for the founder: a person who wants something changed in the system has only the hub chat. That is what he asked for; the hub must forward cleanly.

### (c) An "AI Engine" tab and several engines in one install

Data model (new, in `tower.json` and the workspace):

```
engines: {
  "animation": { "label": "Animation engine", "dir": "engines/animation",
                 "lanes": ["story", "layout", "render"], "library": "engines/animation/pattern-library.md",
                 "skills": ".agents/skills", "map": "engines/animation/engine.json" },
  "marketing": { … }
}
lanes: { "render": { "engine": "animation", … } }
```

- A lane belongs to one engine (`lane.engine`); the hub and infra belong to none (they run the whole install). A task carries `engine` (from its lane).
- Each engine owns a pattern library table, its process cards and pattern cards (directories next to the skills), and an optional hand-written `engine.json` (the map's human part: loops, experiments, notes).
- One install with zero `engines` keeps working as today: a single implicit engine "default" containing all team lanes.
- Files touched: `config.py` (schema, validation, migration), `store.py` (`tasks.engine`, `lanes.engine` columns, additive), `cli.py` (`dah engine list|add|show`, `dah lane add --engine`), `engine.py` (per-engine caps optional later), `web.py` (`/api/engines`, `/api/engines/<id>/map`), new `agent_hub/engines.py` (reader of process cards, pattern library, skills, tools, snapshots: generalised `process_view.py` + `studio.py`), `board/index.html` (the AI Engine tab with an engine switcher in its header, deep-linked `#engine/<id>`), `templates/engines/` (a blank engine skeleton, process card and pattern card templates), `guide/` (a new `ENGINES.md`), `install.py`/`discover.py` (offer to create the first engine).
- The tab shows, per engine: the lane list with its skills; process cards as pipelines with status chips; the rating of each stage (request 7); the weekly "what changed" list; tools and experiments tables if the engine's map declares them.

### (d) Per-lane view

In the Lanes tab (and reachable from the engine tab), one card per lane, opened as a sheet:

| Field | Source |
| --- | --- |
| skills it runs | skill names found in the lane file / agent file (`skill("name")` mentions) matched to the skills directory; same logic the source map uses |
| process it runs | process card chosen by the live task's `Line:` header |
| stage it is on, and the process or stage it is fixing | `dah stage N` (stage now) and the task's line; "fixing" = a task marked as a send-back or one whose blame points at that stage |
| model and effort | the task's resolved model and effort (`stagemodel.resolve`), stored on the task |
| tokens | per task and per day: new, cache, out, turns; the lane's last 7 days (from the ledger) |

Files: `store.py` (`tasks.line_row`, `line_stage`, `stage_now`, `model`, `effort`, `tok_new`, `tok_cache`, `tok_out`, `turns`), `engine.py` (store the resolved model and effort at start, settle tokens at close), `runtimes.py` (a `usage(lane, sid)` already exists for each adapter; add a per-task transcript reader only for claude if needed), `web.py` (`/api/lanes/<id>`), `board/index.html`.

## 3. The chain of requests

Rules for every request: one branch `port/engines` in the agent-hub repo (created; local only), one commit per request on that branch, tests green (`python3 -m unittest discover -s tests -t .` prints `OK`), board before/after screenshots through the repo's existing stand-in tower and a headless browser capture (stored in `docs/images/port/` and named `NN-before.png`, `NN-after.png`), the five board languages updated for every new string. Each request fits one fresh session (~150K). No PyPI publish, no tag.

| Req | Deliverable | Files | Tests | Proof |
| --- | --- | --- | --- | --- |
| P1 | Engines in config and store: `engines` schema, `lane.engine`, `tasks.engine`, implicit "default" engine, additive DB columns, `dah engine list/add/show`, `dah lane add --engine` | `config.py`, `store.py`, `cli.py`, `install.py` | new `test_engines.py`: config round trip, old config loads as one engine, DB upgrade keeps rows | `dah engine list` output pasted in the commit message; no board change (before/after identical capture) |
| P2 | Remove goals (change a, back end and templates) | `store.py`, `cli.py`, `engine.py`, `web.py`, `ext.py`, `templates/*`, `guide/*`, tests | goal tests replaced: 410s, old DB with goals still opens, `dah task --goal` notice | Hub tab capture before (goals) and after (no goals section) |
| P3 | Pinned chat, Infra tab removed, infra only via hub (changes a UI part and b) | `board/index.html`, `web.py`, `cli.py`, `templates/lanes/*.md`, `shipped.json` | memo routing, refused `to: infra` from the board, five-language key check | captures of every tab before/after; the chat visible on all tabs on a 390 px phone width |
| P4 | Scheduler upgrades: declared `paths:` conflicts, head-of-line skip, light/heavy slots, linked tasks, memory guard | `engine.py`, `config.py` (`light_lanes`, `max_instances`, `heavy_paths`), new `memwatch.py` | `test_engine.py` additions with the injected clock and runtime | `dah status` output with two same-lane tasks, one blocked by overlap and a later one starting |
| P5 | Task ledger: per-task model, effort, tokens, turns; retro line on `wrapped`; `dah stats` | `store.py`, `engine.py`, `runtimes.py`, `cli.py`, new `stats.py` | store/ledger tests with stand-in transcripts; `stats` golden output | `dah stats` table; Lanes tab shows tokens (capture) |
| P6 | Process cards and pattern library reader; `Line:` header parsing; `dah stage`; per-stage `model · effort` from the library (extend `stagemodel`); optional `require_line` | new `engines.py` (reader), `stagemodel.py`, `cli.py`, `store.py` | fixtures of a tiny library, one process card and one pattern card; gate accepts/refuses | `dah engine show animation` prints the pipelines with statuses |
| P7 | Deliverables, rating and blame: `dah deliverable`, `dah blame`, first-pass rate, bars, token cost per attempt and per adopted deliverable | new `rating.py`, `store.py`, `cli.py` | rating math fixtures (n<5 new, ≥85 % optimal, refactor rule) | `dah ratings` output for a fixture engine |
| P8 | Per-lane view (change d) and the Lanes tab rebuild: skills, process, stage now, fixing, model, effort, tokens | `web.py` (`/api/lanes/<id>`), `board/index.html`, `engines.py` | API shape test, five-language keys | Lanes tab capture before/after, one sheet capture |
| P9 | AI Engine tab with the engine switcher, pipelines with ratings, skills, weekly snapshot and "what changed", usage bar | `web.py` (`/api/engines*`), `engines.py` (snapshots), `board/index.html`, `weekly` hook via `on_tick` | snapshot diff tests, 304 on unchanged map | tab capture for two engines, switching between them |
| P10 | Engine scaffolding and templates: `dah engine add NAME --from blank|animation|marketing`, process/pattern card templates, an engine review lane template, `guide/ENGINES.md`, `docs/engine-playbook.md` links, optional usage-based cap provider | `templates/engines/*`, `cli.py`, `install.py`, `guide/*`, `engine.py` (cap hook) | scaffold creates a valid engine that passes `engine show` and `tools/skill_lint.py` | two scaffolded engines side by side in the AI Engine tab |
| P11 | Docs and release prep, no release: README rewrite (engine pitch, no goals), SETUP guide, `dah update` migration for `tower.json` and the shipped template texts, version set to 0.8.0 in `__init__.py` and `pyproject.toml`, CHANGELOG-style notes in `docs/RELEASING.md` | `README.md`, `SETUP.md`, `AGENTS.md`, `install.py`, `docs/*`, images | full suite on the three systems' CI config unchanged; an update test from a 0.7.0 workspace fixture | before/after README render, update dry-run output; branch left unmerged for the founder |

Order notes: P1 first (everything keys on `engine`); P2 and P3 are independent of P4 to P7; P6 before P7 and P8; P9 needs P5 to P8; P10 needs P1, P6, P9. If a session runs long, P3 and P9 split first (UI), then P4.

## 4. Risks

- **Breaking for current users.**
  - Goals disappear: CLI, API (410), board, hub prompt, README pitch. Existing goal rows stay in the DB, unread. A user who lives on goals needs a note in the release text: use requests, or keep the old version.
  - The Infra tab disappears: humans can no longer type to infra. The hub becomes the only door. Workspaces whose hub instruction predates the change must pick up the new hub template: `dah update` writes `.new` beside an edited file (the existing mechanism); until merged, the hub does not forward infra work. The migration must say so in its output.
  - `on_event` kind `goal` stops firing; routes `/api/goals*` go away. `AGENTS.md` says contract points are not dropped without a major version. Decision needed: bump to **0.8.0** (the project is 0.x) and document, or keep dead endpoints answering for one more minor. This plan assumes 0.8.0 with 410s for one release.
  - `tower.json`: new optional keys (`engines`, `light_lanes`, `max_instances`, `heavy_paths`, `require_line`); an old file loads unchanged (one implicit engine). No required key is added.
  - DB: additive columns only; no column or table is dropped, so a downgrade to 0.7.x still opens the DB.
  - Templates change (hub, infra, PROTOCOL): every one needs a new entry in `templates/shipped.json` or the test fails; users with edited copies get `.new` files.
- **Version.** 0.8.0 (minor, pre-1.0, with breaking notes). Set in P11 on the branch; not tagged, not published.
- **Release gate.** No `git tag`, no push of a tag, no `uv publish` or CI release trigger from any request in this chain. The branch stays local until the founder OKs a merge; merging to `main` locally and pushing `main` is also left for him (CI publishes on tags only, but a push is still outward). Commits for the chain go on the local branch `port/engines` (created by this step; this document is its first commit).
- **Size.** The source board is 3K lines and its UI strings cover tabs this port leaves out; the agent-hub board is one file with no build step and every string in five languages, so P3, P8 and P9 are the heavy ones.
- **Neutrality.** Source code carries game words (rows, stages, Godot, Blender, heroes). Each port copies the idea and rewrites names; a grep for those words in `agent_hub/` is part of P11's check.
- **No LLM in the tower.** Everything above (ratings, blame, snapshots, reader) stays plain Python; the extensions contract and standard-library-only rule stay.
- **Subscription usage cap** reads a vendor's private OAuth usage endpoint. Port it only as an optional provider, off by default, never refreshing tokens.
