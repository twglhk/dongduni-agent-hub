# tower-opened research session log (tool calls only)

FINAL: The research note is written and reported to the hub as done. My recommendation is that Bean Counter build a small first version of category tagging. Building it is still the humans' call, and the hub will raise that item.

**How the leading tools handle categories:**
- **Plain-text tools (ledger, hledger, beancount):** the category is a hierarchical account like `Expenses:Food:Groceries`. On top of that, each transaction can carry several free tags.
- **Budget apps:** Actual uses two levels (category groups and categories). Firefly III gives each transaction one category, a budget and several tags.
- **Splits:** all five tools studied let one transaction be divided into parts, each with its own category.
- **Assigning categories:** you can always set them by hand, and every tool also has 

FINAL: Task #1 is wrapped and reported to the hub.

- **Commit:** the note `notes/research/category-tagging.md` is committed as `ffd2852`. Nothing else was added.
- **Temp files:** I had already deleted my downloaded pages. The other files left in `/tmp` were not made by this session, so I left them.
- **Memory:** the research lane file has no `## This workspace` section naming a place for memory, so I saved nothing beyond the note and the board report.
- **Retro:** most of the cost was fetching sources (the ledger site timed out and the Firefly docs refused the fetch tool). I proposed a rule for the research skill: when a docs site fails, fetch the manual's source from the project's repository before marking the source as not opened.

FINAL: I wrote the research note at `notes/research/csv-import.md` and reported it to the hub as done.

**Tools covered (six):** ledger `convert`, hledger, beancount's beangulp importers, Actual Budget, the Firefly III Data Importer and GnuCash. All of them import bank CSV files, and they agree on four things:
- **Dates:** the user always states the date format. None of them guesses between dd/mm and mm/dd.
- **Amounts:** all six accept either one signed amount column or separate debit/credit columns. Only three let you declare the decimal mark (comma or point).
- **Duplicates:** all six detect re-imports, in three ways:
  - **Exact match:** ledger stores a checksum of each CSV line; Firefly uses a content hash or a bank ID column; Actual uses the bank's ID when there is one.
  - **Last-date cuto

FINAL: I've wrapped up task #2. The research note is committed as `c0c8e6d` (`notes/research/csv-import.md`), and the hub has it.

- **Handoff:** I sent the handoff report. The note meets every done criterion, so a fresh session has nothing left to continue.
- **Cleanup:** I deleted the two temp folders I used for downloaded sources (`/tmp/csvres`, `/tmp/csvscripts`). The commit contains only the note.
- **Wrap report:** sent, with a retro line. The thing that took most turns was reaching the docs: the Firefly III site blocked automated fetches, and the GnuCash manual page came back empty, so I read both from their GitHub doc sources. The rule I proposed for the research skill: when a docs site blocks fetching, read the project's docs repo on GitHub and list both URLs in the sources table.
- **Me

FINAL: I couldn't write the Q3 summary because the file it's based on, `data/q3-export.csv`, isn't in the workspace. There's no `data/` folder at all. I also checked for any `.csv` file, anything with "q3" in the name, and the git history, and found nothing.

The task says not to invent data, so I didn't create `notes/research/q3.md`. I've reported the task to the hub as blocked on a missing input.

To unblock it, put your Q3 bank export at `data/q3-export.csv` and have task #3 run again. No board items are open.

FINAL: `data/q3-export.csv` still isn't in the workspace. I already reported task #3 to the hub as blocked on that missing file, so I'm not sending a second report. I didn't create a note or use any substitute data. No board items are open, and I'm stopping here.
