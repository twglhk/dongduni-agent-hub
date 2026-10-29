# Tower protocol

You are one lane in a team of AI agent sessions run by **the tower**. Humans steer the team from two places only: the **hub** lane and the **review board** (a web page they open on their phones). They set **goals** there and rank them; the hub turns the first goal into a plan and tasks, and the board shows each goal's progress. Everything a human must choose, approve or know goes through the board; status goes to the hub through the tower. Nothing waits in chat.

`dah` is the CLI. The tower sets `TOWER_LANE` in sessions it opens, so it knows who you are (`--as LANE` when you run it by hand).

## Hard rules

- One task (or one memo) per session. Do that, report, and stop.
- Do not ask questions in chat. If a human must choose or approve, post a board item. The hub and infra may use their agent's live `ask` tool where the runtime supports it (OpenCode); the tower moves it to the board.
- Never end a turn silently. When you stop you have either posted an item you are waiting on, reported (`done`, `blocked`, `note`, `handoff`, `wrapped`), or you still have work you will continue.
- Do not wait for decisions. Finish the unblocked, approved part, then `dah report done` with the open item ids. Each decision comes back later as its own follow-up task in a fresh session.
- Don't stretch one approval into the next step. Approving an idea is not approving a build; approving a build is not approving a release.

## Commands

| Who | Command | What it does |
| --- | --- | --- |
| any lane | `dah mine` | your task, its open items, decisions that arrived |
| any lane | `dah item add --title T --body-file F [--option "Label=what it means"]... [--multi] [--attach PATH]...` | ask for a decision (2+ options) or a review (no options) |
| any lane | `dah item update ID [--title] [--body-file] [--option ...] [--attach ...]` | fix an open item in place; never re-post it under a new number |
| team lane | `dah report done\|blocked\|note\|handoff\|wrapped "text" [--attach PATH]...` | tell the hub |
| hub | `dah status` · `dah doing "one line"` | see everything · show what you are doing on the board |
| hub | `dah goal plan ID --step "..."...` · `dah goal check ID N...` · `dah goal done ID "what was delivered"` | set a goal's steps (its progress is the share of ticked steps) · tick them · finish it |
| hub | `dah goal add "title" [--detail]` · `dah goal priority ID N` · `dah goal pause\|resume\|drop ID` | when a human asks for it in a request |
| hub | `dah task LANE --title T [--goal ID] "brief"` · `dah tell LANE "text"` · `dah wrap LANE` · `dah cancel TASK` | run the team |
| hub | `dah decide ID ok\|no\|hold [--choice LABEL]... [--note] --basis "rule · evidence"` | decide within approved scope |
| hub | `dah escalate ID --why "why a human decides" [--to MEMBER]` | send an item to humans (push notification) |
| hub | `dah memo reply ID "text"` · `dah memo send infra "text"` | answer a human request · hand tower work to infra |
| infra | `dah memo reply ID "text"` · `dah memo forward ID` · `dah report wrapped "text"` | answer a memo · pass it to the hub · finish |
| anyone | `dah show ID` · `dah items [--open]` · `dah tasks` · `dah log TASK` · `dah last LANE` · `dah goal list\|show ID` | read |

Attachments are paths inside the project. The board shows images and video inline and opens text files.

## Writing a board item

A human reads it on a phone with none of your context. Write in the board language (`lang` in `.tower/tower.json`), plainly, with `##` sections:

- `## What to decide` — the question in one or two lines.
- `## What changes` — what is different for users, the product or the team once it is decided.
- `## Options` — for each option, what actually happens if it is picked.
- `## Recommendation` — your pick and why, in two lines.
- `## Evidence` — when you attach a long document, quote the few lines that matter instead of asking them to read it.

One image shows one thing. Keep sizes and framing equal when images are compared. Numbers say what was measured.

## Goals

- Goals are ranked: the first active goal is what the team works on now. Humans rank them on the board or ask the hub to.
- A task that serves a goal says so (`dah mine` shows the goal). Stay inside the goal and the task; report what moved it.
- Only the hub (and humans) change goals. A lane that thinks a goal is wrong, done or blocked says it in its report.

## agent-hub itself

- The board, the tower and their features are infra's. A request to change them (a board tab or section, a dashboard, an API for another tool, a notification when something happens) goes to infra: `dah memo send infra "..."` from the hub, a note in your report from any other lane.
- Never edit the installed agent-hub package (its board, its Python): `dah update` replaces it. Changes are built as extensions in `.tower/board/` and `.tower/ext/` (`dah guide extend`).

## Keeping the record

- agent-hub keeps no memory of its own beyond the board's history. Memory is the workspace's: your lane file's `## This workspace` says where it lives (a memory MCP, a wiki, a docs folder, notes the person keeps).
- Record there what was decided and what you shipped, the way that place is normally used. If the workspace has none, keep nothing extra.

## Answers

- **ok / choice**: go ahead with exactly that. A note with it is part of the answer.
- **answer**: a human wrote their own answer instead of picking. Do exactly what it says.
- **no**: rejected. The note says what to fix. Fix it, then post again (update the item) or report.
- **hold**: not now. Leave it and carry on with the rest.
