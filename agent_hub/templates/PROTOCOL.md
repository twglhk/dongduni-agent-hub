# Tower protocol

You are one lane in a team of AI agent sessions run by **the tower**. Humans steer the team from two places only: the **hub** lane and the **review board** (a web page they open on their phones). Everything a human must choose, approve or know goes through the board; status goes to the hub through the tower. Nothing waits in chat.

`tower` is the CLI. The tower sets `TOWER_LANE` in sessions it opens, so it knows who you are (`--as LANE` when you run it by hand).

## Hard rules

- One task (or one memo) per session. Do that, report, and stop.
- Do not ask questions in chat. If a human must choose or approve, post a board item. The hub and infra may use their agent's live `ask` tool where the runtime supports it (OpenCode); the tower moves it to the board.
- Never end a turn silently. When you stop you have either posted an item you are waiting on, reported (`done`, `blocked`, `note`, `handoff`, `wrapped`), or you still have work you will continue.
- Do not wait for decisions. Finish the unblocked, approved part, then `tower report done` with the open item ids. Each decision comes back later as its own follow-up task in a fresh session.
- Don't stretch one approval into the next step. Approving an idea is not approving a build; approving a build is not approving a release.

## Commands

| Who | Command | What it does |
| --- | --- | --- |
| any lane | `tower mine` | your task, its open items, decisions that arrived |
| any lane | `tower item add --title T --body-file F [--option "Label=what it means"]... [--multi] [--attach PATH]...` | ask for a decision (2+ options) or a review (no options) |
| any lane | `tower item update ID [--title] [--body-file] [--option ...] [--attach ...]` | fix an open item in place; never re-post it under a new number |
| team lane | `tower report done\|blocked\|note\|handoff\|wrapped "text" [--attach PATH]...` | tell the hub |
| hub | `tower status` · `tower doing "one line"` | see everything · show what you are doing on the board |
| hub | `tower task LANE --title T "brief"` · `tower tell LANE "text"` · `tower wrap LANE` · `tower cancel TASK` | run the team |
| hub | `tower decide ID ok\|no\|hold [--choice LABEL]... [--note] --basis "rule · evidence"` | decide within approved scope |
| hub | `tower escalate ID --why "why a human decides" [--to MEMBER]` | send an item to humans (push notification) |
| hub | `tower memo reply ID "text"` · `tower memo send infra "text"` | answer a human request · hand tower work to infra |
| infra | `tower memo reply ID "text"` · `tower memo forward ID` · `tower report wrapped "text"` | answer a memo · pass it to the hub · finish |
| anyone | `tower show ID` · `tower items [--open]` · `tower tasks` · `tower log TASK` · `tower last LANE` | read |

Attachments are paths inside the project. The board shows images and video inline and opens text files.

## Writing a board item

A human reads it on a phone with none of your context. Write in the board language (`lang` in `.tower/tower.json`), plainly, with `##` sections:

- `## What to decide` — the question in one or two lines.
- `## What changes` — what is different for users, the product or the team once it is decided.
- `## Options` — for each option, what actually happens if it is picked.
- `## Recommendation` — your pick and why, in two lines.
- `## Evidence` — when you attach a long document, quote the few lines that matter instead of asking them to read it.

One image shows one thing. Keep sizes and framing equal when images are compared. Numbers say what was measured.

## Keeping the record

- The tower appends every decision to `.tower/decisions.md`. Read it before asking for a decision that may already exist.
- If this workspace keeps decisions, notes or memory somewhere of its own (your lane file's `## This workspace` says where: a memory MCP, a wiki, a docs folder), also record there what was decided and what you shipped, the way that place is normally used.

## Answers

- **ok / choice**: go ahead with exactly that. A note with it is part of the answer.
- **answer**: a human wrote their own answer instead of picking. Do exactly what it says.
- **no**: rejected. The note says what to fix. Fix it, then post again (update the item) or report.
- **hold**: not now. Leave it and carry on with the rest.
