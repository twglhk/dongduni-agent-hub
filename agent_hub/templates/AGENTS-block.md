<!-- dah:start -->
## agent-hub

This workspace runs agent-hub. The tower opens, resumes, feeds and closes lane sessions; never do that by hand. Humans use the hub chat and the review board.

- You are a plain session unless `TOWER_LANE` is set. If it is set, read `.tower/lanes/$TOWER_LANE.md` and `.tower/PROTOCOL.md` first and follow them.
- A request for work, research or a review goes through `dah` (`dah status`, `dah task`, `dah item add`); `.tower/PROTOCOL.md` is the single source for the rules.
<!-- dah:end -->
