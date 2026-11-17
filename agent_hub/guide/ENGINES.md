# Engines: scaffold, edit, confirm

Every process lives in one process library, `processes/`. A **sub-process** (`processes/sub/<id>/`) is a process card (its stages) with each stage's pattern card in `patterns/`; the card names the skills it uses on a `- Skill:` line (a stage may name its own in a `Skill` column). A **full process** (`processes/full/<id>.md`) makes one deliverable by running sub-processes in order, and several full processes may use the same sub-process. A **lane** owns no process: its lane file lists the full processes it runs under `## Processes used`, and which lanes use a process is computed from the lane files, never written twice. An **engine** in tower.json is a set of lanes around one full process (its `library`), with the skills they run and a review lane that checks the work against the cards. This guide covers starting one. The reasoning behind the method is in `docs/engine-playbook.md` (the process card, then the pattern card; revision as a process; rate the line).

## Start from a template

```
dah engine add NAME --from blank|animation|marketing [--label "Studio engine"] [--runtime claude] [--skills .agents/skills] [--cap tokens-day:2000000]
dah engine show NAME
```

`NAME` is lowercase letters, digits and single hyphens; it prefixes the skills and lanes the scaffold makes. Nothing is overwritten: an existing file or lane stops the command before it writes anything.

| template | what you get |
| --- | --- |
| `blank` | one sub-process with three stages (Brief, Make, Review) and one maker lane |
| `animation` | Storyboard (Beats, Frames, Review) and Shot render (Setup, Render, Check) with a story lane and a render lane |
| `marketing` | Short post (Angle, Draft, Review) and Launch thread (Outline, Write) with one copy lane |

Every template also creates `NAME-review`, an engine review lane, and writes:

- `processes/full/NAME.md`: the full process, its sub-processes in order (`#`, `Frame`, `Sub-process`, `What it does`, `Model · effort`)
- `processes/sub/NAME-<type>/process.md` and `patterns/<stage>.md`: the cards, all `Status: draft`
- `engines/NAME/engine.json`: the hand-written part of the AI Engine tab (notes, tools, experiments)
- `<skills>/NAME-*/SKILL.md`: the skills the cards name
- `.tower/lanes/NAME-*.md`: the lane instructions, each with `## Processes used`

## Make it yours

1. Rename the sub-processes and stages to what your team really ships, and give the full process a planning, a production and a QA sub-process (the `Frame` column; the template's `Frame: exception — …` line goes once it has them). A process card is a table: `#`, `Stage`, `Input`, `Output`, `Gate`, `Passed by`, `Pattern card`.
2. Fill in each pattern card: sources (shipped work at a professional level), the recipe, the checks. The checks are the stage's gate.
3. Set a model and effort per sub-process in the full process's last column. How to choose: [MODELS.md](MODELS.md) (`dah guide models`).

Every engine also gets two lanes: `NAME-research` (a referenced note against tier-1 sources, on the full process `NAME-research` and its sub-process `NAME-research-note`; Opus medium) and `NAME-audit` (reads raw evidence for why a pattern happened and sends the hub cards with predicted effects; Fable high). Their skills are `NAME-research` and `NAME-audit`; edit them like any card. `dah init` creates plain `research` and `audit` lanes the same way.
4. Run a few tasks with `prepare` and `make` lines (`Line: NAME / NAME-deliverable · stage 2 · make`). `make` is refused until the process card says `Status: confirmed` and its pattern cards are confirmed or tuned; the review lane proposes the card changes that get them there. A process the library does not have yet is built with `Line: NAME / new <name> · prepare` or `Line: new <name> · prepare` (only `prepare` is allowed; it is refused when the library already has that name). When an engine sets no `skills` folder, `.claude/skills` is used if it exists, else `.agents/skills`; a `- Skill:` line may also name a repo path (`docs/demo/skill.md`) instead of a skill folder.
5. Read `dah ratings --engine NAME` weekly and tune the lowest-rated stage first, one change at a time.

Check the scaffold at any time with `dah engine show NAME`, the AI Engine tab on the board, and, from a clone of the agent-hub repository (the script is not in the installed package), `python3 tools/skill_lint.py` for the skills. It needs no extra package.

## The `Line:` header

```
Line: <full> / <sub> · stage <n|n-m> · prepare|make|fix
Line: <full> · prepare                          the full process itself: its order of sub-processes
Line: <full> / new <name> · prepare             a sub-process the library does not have yet
Line: new <name> [/ <sub>] · prepare            a new full process
Line: none · chore: <reason>
```

Names are library ids (lowercase words joined by hyphens), except after `new`. `prepare` builds or fixes the method, `make` runs it, `fix` is a send-back of earlier work. `make` and `fix` name a stage the sub-process card has, and `make` needs the card confirmed and its pattern cards confirmed or tuned. Text after the mode word is ignored. The sub-process's cell in the full process's `Model · effort` column sets model and effort (`dah guide models`); a `model:` or `effort:` line in the brief wins. The task keeps `<full>/<sub>`, the stage and the mode.

`dah task` checks the header against the library when `require_line` is on and the lane's file lists `## Processes used` or its engine's `library` is a full process. A lane that runs a full process its file does not list gets a warning. `processes/README.md` is optional; when it exists, every process id has to appear in it.

Read the whole library with its reverse references (which full processes and lanes use each sub-process): `python3 -m agent_hub.processes .` from the workspace, the Engine tab, or `GET /api/processes`.

## Process verification

A full process has a status: **draft** (a card is not confirmed), **confirmed** (every sub-process card and pattern card approved as text) and **verified** (one whole run was checked step by step and a person said it is good). `"process_verification": true` in tower.json (default off; it needs `require_line`) turns on the checks that lead there. With it off nothing is held or required, and a `processes/verified.json` you wrote still shows the status.

1. Every task of one run carries the same `--deliverable KEY`; `dah task` refuses a make or fix of a full process that is not verified without it.
2. A make or fix task that runs the last stage of its sub-process posts a check card with `dah check --title T --body-file F --attach OUTPUT`. It goes to the task's requester (visible only to them with `requester` or `person_hubs`), options *Next* and *Redo with fixes* in the board language. `dah report done` is refused until it exists.
3. The tower holds the next sub-process task of the run while the card before it is open, on hold or being redone (`dah status`, the Lanes tab and the full process sheet say who it waits for).
4. *Next* releases it. *Redo with fixes*, a rejection, or an answer in the person's own words queues a fix task of the same sub-process (`Line: <full> / <sub> · stage <n> · fix`) with the note; it posts a new check card. A card put on hold keeps the run waiting for the hub: `dah process check CARD next|redo`.
5. When the last sub-process passes, the tower tells the hub to post the verify card to the requester. On its yes the hub runs `dah process verify <full> --card N`; it is refused unless the cards are confirmed, the card is approved and one run has every check on *Next*. The record (`processes/verified.json`: card, person, run, commit, the sub-process order and a sha256 of every sub-process card and pattern card) is library content: commit it.
6. A verified full process runs through; only its last sub-process posts a check card. Changing any of its cards, or their order, drops it back to confirmed (the Engine tab says *Needs a new check* and which file changed) until a new run is verified.

`dah process status [FULL]` prints the status and the latest run. The check cards live in the tower's database (table `process_checks`); the library files stay the only thing the workspace's git keeps. Converted pattern libraries work the same, and an engine with no process library is not affected.

## Workspaces from before the process library

`dah update` converts an engine whose `library` is a pattern-library table (one row per deliverable type, no reuse):

1. One full process `processes/full/<engine id>.md` gets a step per row, in order. Its `Old #` column keeps the row number (`3`, `D5`) and `Model · effort` keeps the row's cell. It carries `Frame: exception — …` because a pattern library has no frames; give each sub-process its frame, then delete that line.
2. Each process card becomes a sub-process `processes/sub/<engine id>-<type>/` (rows that name the same card share one sub-process, each row keeping its own step, `Old #` and model; a pattern card used by several sub-processes is copied into each, its old file lists every copy, and `dah update` reports it): the process card moves to `process.md` (a `- Skill:` line is added from the row's skill) and its pattern cards to `patterns/`, with the card's `Pattern card` cells pointing at the new place. A row with no process card file becomes a `Status: draft` card with no stages that says what the row had; write its stages and confirm it before `make`.
3. The old card files stay where they were, each replaced by a one-line pointer to the new file, so skills that link to them still lead somewhere.
4. `pattern-library.md` becomes a pointer and the original table is kept as `pattern-library.md.bak`.
5. The engine's `library` in tower.json now names the full process, and the lanes that owned rows (all lanes of the engine when none did) get a `## Processes used` section appended. Tasks already in the database keep their rating row: `line_row` `3` becomes `<full>/<sub>`.

It runs once per engine (an engine whose `library` is already a full process is skipped) and changes nothing on a second run. If `processes/full/<id>.md` or a target file already exists with other content, that engine is left as it was and `dah update` says so.

Briefs written before the conversion (`Line: #3 Storyboard · stage 2 · process confirmed · pattern confirmed · make`) still work: the row number is looked up in the `Old #` column of the lane's engine's full process and read as `Line: <full> / <sub> · stage 2 · make` (a notice says so). A row number the column does not have is refused with the list of old rows and the new form to write. In a workspace that has not been converted, the old form and the old gate work as before.

## A usage cap (optional)

`"cap": "tokens-day:2000000"` on an engine in tower.json holds that engine's queued tasks once its lanes have used that many tokens today. For another rule (a plan's remaining quota, a cost ceiling) register a function in `.tower/ext/*.py`: `caps = {"quota": fn}` and `"cap": "quota"`; see `dah guide extend`. Running sessions are never stopped by a cap.
