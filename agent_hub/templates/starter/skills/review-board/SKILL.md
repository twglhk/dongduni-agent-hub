---
name: review-board
description: "How a lane uses the review board through dah: when an item is due, writing it for a phone, the evidence it carries, revising instead of reposting, and what each answer (ok, choice, answer, no, hold) means. Load before posting or revising an item and when a task applies a decision."
---

# Review board

Humans steer the team from the hub and the review board, a web page they open on their phones. Every choice, approval or review result a lane needs goes on the board as an item; status goes to the hub as reports (`dah report`). Nothing waits in chat. `.tower/PROTOCOL.md` has the short form of these rules; this skill is the full one.

## When to post an item

Post an item when someone else must choose or approve before part of your work can go on:

- Choosing between alternatives (designs, wording, approaches, names): two or more `--option "Label=what it means"`. Add `--multi` when picking several is a real answer (which of these to keep); the answer then names every pick.
- Approving an artifact (a render, a screen, a document, a build) before it moves to the next stage: no options, `--kind review`. The human presses one button: blank means approved, text is their own answer.
- Anything the Permissions rule in `.tower/PROTOCOL.md` or the project instructions say to confirm first: stack, new dependencies, paid calls, keys, deploys and publishes.

Don't post for a choice inside your already-approved scope, a progress update (`dah report note`), something the repo or the workspace's memory already answers, or something already asked. Check first with `dah items` and `dah show ID`.

## Approvals are separate

- Keeping an idea, allowing a trial, and final adoption (locked numbers, paid generation, shipping it) are three approvals. Don't stretch one into the next, and don't fill in values nobody decided.
- Keep existing material apart from new proposals, and what is built apart from what is not. Keep the human's choices and extra ideas word for word and read them literally: when they name a product, a count or a method, restate it from the source's own facts at the top of your item, and never widen one choice into a general taste.
- Observations by an agent are labelled as such; they never stand in for a test with real people.

## Evidence on the item

- Anything a human judges by eye goes on the item itself, viewable on a phone: images or a short video attached, never a path or a measurement alone.
- One image shows one thing (one candidate, one state, one frame). Attach each as its own file, in viewing order, at the same size and framing, so each can be opened and marked on its own. Never merge several into a grid or a strip.
- An item about a change to the process itself (the tower setup, lane instructions, skills, process or pattern cards) carries one picture of before and after side by side: a small diagram image, or an `.html` page when the human should play with numbers. This is the one place where two states share a picture.
- Numbers say what was measured, how, and on how many cases. A long document is attached as background; the body quotes the few lines the decision rests on under `## Evidence`, each tagged with where it came from.
- The board is the only review surface. Don't send a human to a local server, a separate review page, or raw data (`.json`, `.csv`, code); turn it into a short report first.

## Writing it

The reader has a phone and none of your context. Write in the board language (`lang` in `.tower/tower.json`), plainly, with the `##` sections `.tower/PROTOCOL.md` lists (What to decide · What changes · Options · Recommendation · Evidence). One idea per line, a blank line between parts, no wall of bold. The title says what is decided in a few words. For English, run the `humanizer` skill over the body before posting.

## After posting

- Keep doing the parts that are not blocked. When the unblocked, approved work is done, `dah report done` with the open item ids. Never claim blocked or unapproved scope as done. If the deliverable is the items themselves ("make options and ask"), report done right after posting.
- Each answer comes back later as its own follow-up task in a fresh session, with the source brief and the item id. Apply only that decision: read it with `dah show ID`, don't replay the original task or widen its scope.
- What each answer means:
  - **ok / choice**: go ahead with exactly that. A note with it is part of the answer.
  - **answer**: the human wrote their own answer instead of picking. Do exactly what it says: carry on, change only what it names, or stop if it says to wait.
  - **no**: don't. The note says what to fix. Fix it, then update the item or report. On a decision, "no" means none of the options works: build new options from the note.
  - **hold**: not now. Leave it and carry on with the rest.
  - Marks on single images and the note are part of the answer.

## Revise, don't repost

- Run `dah show ID` before promising an update. An open item is revised in place: `dah item update ID --body-file … --attach …` (new options or attachments replace the old list). Never post a second copy with "(again)" in the title.
- A closed item gets a new item whose title names the old number.
- Two open items that ask the same thing: the hub folds one into the other (`dah item merge ID --into OTHER`); a lane tells the hub with `dah report note`.
- Don't post the same question again without a new reason.

Before you report done on a reviewed artifact, read [before-done](references/before-done.md): it is how to say what you could not check.
