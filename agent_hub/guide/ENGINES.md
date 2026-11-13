# Engines: scaffold, edit, confirm

An engine is a set of lanes that share one method: a pattern library (a table of deliverable types), the skills that hold each type's process card and pattern cards, and a review lane that checks the work against those cards. This guide covers starting one. The reasoning behind the method is in `docs/engine-playbook.md` (the process card, then the pattern card; revision as a process; rate the line).

## Start from a template

```
dah engine add NAME --from blank|animation|marketing [--label "Studio engine"] [--runtime claude] [--skills .agents/skills] [--cap tokens-day:2000000]
dah engine show NAME
```

`NAME` is lowercase letters, digits and single hyphens; it prefixes the skills and lanes the scaffold makes. Nothing is overwritten: an existing file or lane stops the command before it writes anything.

| template | what you get |
| --- | --- |
| `blank` | one deliverable row with three stages (Brief, Make, Review) and one maker lane |
| `animation` | Storyboard (Beats, Frames, Review) and Shot render (Setup, Render, Check) with a story lane and a render lane |
| `marketing` | Short post (Angle, Draft, Review) and Launch thread (Outline, Write) with one copy lane |

Every template also creates `NAME-review`, an engine review lane, and writes:

- `engines/NAME/pattern-library.md`: one row per deliverable type (lane · skill, process, pattern, model · effort)
- `engines/NAME/engine.json`: the hand-written part of the AI Engine tab (notes, tools, experiments)
- `<skills>/NAME-*/SKILL.md`, `references/process/<type>.md` and `references/patterns/<stage>.md`: the cards, all `Status: draft`
- `.tower/lanes/NAME-*.md`: the lane instructions

## Make it yours

1. Rename the rows and stages to what your team really ships. A process card is a table: `#`, `Stage`, `Input`, `Output`, `Gate`, `Passed by`, `Pattern card`.
2. Fill in each pattern card: sources (shipped work at a professional level), the recipe, the checks. The checks are the stage's gate.
3. Set a model and effort per row in the library's last column (see `stage_table` in tower.json).
4. Run a few tasks with `prepare` and `make` lines (`Line: #1 Deliverable · stage 2 · make`). `make` is refused until the process card says `Status: confirmed` and its pattern cards are confirmed or tuned; the review lane proposes the card changes that get them there.
5. Read `dah ratings --engine NAME` weekly and tune the lowest-rated stage first, one change at a time.

Check the scaffold at any time with `dah engine show NAME`, the AI Engine tab on the board, and `python3 tools/skill_lint.py` for the skills.

## A usage cap (optional)

`"cap": "tokens-day:2000000"` on an engine in tower.json holds that engine's queued tasks once its lanes have used that many tokens today. For another rule (a plan's remaining quota, a cost ceiling) register a function in `.tower/ext/*.py`: `caps = {"quota": fn}` and `"cap": "quota"`; see `dah guide extend`. Running sessions are never stopped by a cap.
