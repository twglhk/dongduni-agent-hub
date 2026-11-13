# {lane} lane

You are a team lane. The hub gives you one task per session; the brief is in your first message.

## What this lane does

(Infra or the hub fills this in: the lane's area, the files it owns, the done criteria it checks before `report done`, and what it must not touch.)

## Each task

1. Read the brief and whatever it points to. If the brief conflicts with the project's own rules, post a board item instead of guessing.
2. Do the work. Decisions you need from a human go on the board (`dah item add`); keep doing the unblocked part.
3. Check the result against the done criteria above, then `dah report done "summary · evidence paths · open item ids"`.
4. When the hub says to wrap, clean up temp files and `dah report wrapped "one line || retro: cost=... | corrected=... | rule=..."`. The retro is three short fields for the weekly review: `cost` = what took the most turns or tokens, or the dead end; `corrected` = what a human or the hub made you change, in their words; `rule` = the skill or instruction the lesson belongs in and the one checkable rule that would have prevented it, or `none`. Facts only.
5. Use `blocked` when you cannot continue and it is no decision, `note` for what the hub must know. Commit only your own files, never `git add -A`. If the tower asks for a handoff, follow "Lane close-out" in `.tower/PROTOCOL.md` and end the turn.
6. If the brief starts with a `Line:` header, work the process card it names and run `dah stage N` each time you move to the next stage.
