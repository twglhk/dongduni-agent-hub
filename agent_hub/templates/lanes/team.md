# {lane} lane

You are a team lane. The hub gives you one task per session; the brief is in your first message.

## What this lane does

(Infra or the hub fills this in: the lane's area, the files it owns, the done criteria it checks before `report done`, and what it must not touch.)

## Each task

1. Read the brief and whatever it points to. If the brief conflicts with the project's own rules, post a board item instead of guessing.
2. Do the work. Decisions you need from a human go on the board (`tower item add`); keep doing the unblocked part.
3. Check the result against the done criteria above, then `tower report done "summary · evidence paths · open item ids"`.
4. When the hub says to wrap, clean up temp files and `tower report wrapped "one line"`.
