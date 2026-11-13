---
name: project-audit
description: "Recipe for a whole-project clean-up audit, run once when a stronger model ships: read everything, find what is wrong, inconsistent, wasteful or no longer needed, fix only what is certain and safe, list the rest. The hub sends it to infra; load it in the standalone audit session."
---

# Project audit (run when a stronger model comes out)

One standalone session reads the whole project and answers one request: find everything that is wrong, inconsistent, wasteful or no longer needed; fix what is certain; list the rest. It is not a team lane and takes no task slot. Run it again for each new top model.

## Launch (the hub)

The hub sends it to infra once a human has asked for it:

```
dah infra "open one standalone session on <model>, effort high, in its own window or tab, with the same permissions and hooks as a lane; first message: load skill project-audit, run date <YYYY-MM-DD>; send the result to the hub as a memo; close the session after" --approved "request #N"
```

- Model: the strongest the workspace has for whole-codebase review (`dah guide models`: Fable `high`, Opus `high` where Fable is not available). Effort high, not max: the job is breadth plus judgment.
- Check the subscription's weekly usage first; when it is high, start right after the reset.
- Put the brief in a file and pass its path, not a long command line. Don't give it a token budget.

## Read

The repository is the scope; outside notes and trackers are read only to check a contradiction. Read the project instructions, agent and lane files (`.tower/lanes/*`, `.claude/agents/*`, commands), every skill and its references, tools and scripts with their tests, the docs that are read as current, and the product code, tests and data layout. Other folders only to check a reference.

## Find, four kinds

1. **Wrong:** broken paths or links, references to missing files, code bugs, tests that can't fail, instructions that contradict a recorded human decision (cite the item or request number).
2. **Inconsistent:** one rule stated two ways, numbers that differ between docs and data, names that drifted.
3. **Inefficient:** text every lane loads that only one lane needs, duplicated instructions, slow or redundant scripts, steps that waste tokens.
4. **No longer needed:** files nothing references (prove it with a repo-wide search), dead code, archived docs still read as current, unused skills or references.

## Fix only what is certain and safe

- Allowed: broken paths and links, dead files with the search proof in the commit message, stale lines contradicting a cited decision, duplicates where one copy is the source of truth, obvious code bugs with a test.
- Never: product rules, tuned numbers, content, names, design, anything the Permissions rule lists, and anything outside the repository (list those instead).
- Files with uncommitted changes in `git status` belong to running lanes: list them, don't touch them.
- Small commits, one topic each, message `audit: …`, staged by path. The project's tests must pass after each.

## Output

A report at `audit/<date>-<model>.md` (or where the workspace keeps reports), with two tables: **Fixed** (file · what · why · commit) and **List** (kind · severity · file:line · finding · proposed fix · owner lane), high severity first. Then a ten-line summary for the humans in the board language. The hub reviews it, turns the list into lane tasks, and records method changes where the workspace keeps them.

## Runs

Log each run here in one line: date · model · effort · where the brief and the result are · fixes and listed counts · what went wrong in the run itself.
