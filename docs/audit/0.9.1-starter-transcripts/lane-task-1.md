task #1 research closed "Category tagging in top budgeting CLIs: should Bean Counter build it?"
Request #1 (sinjongha): Find out how the best budgeting CLIs tag categories and whether Bean Counter should build it.

model: opus
effort: medium

Outcome: one referenced research note answering (a) how the leading budgeting / plain-text accounting CLIs model and assign categories, and (b) whether Bean Counter should build category tagging, and in what minimal shape.

Context: Bean Counter is a budgeting CLI (this repo; README.md is currently the only product file, so there is no existing category code or data to compare against: say so in the 'ours' column).

Scope of sources (tier-1, open each one you cite): ledger-cli, hledger, beancount (+ fava / smart_importer), plus at least two others among Actual Budget CLI/API, YNAB, Firefly III, GnuCash, or terminal tools like 'budget'/'moneymanagerex'. Cover: category model (hierarchical accounts vs flat tags vs both), how tags are assigned (manual, rules/regex, ML auto-categorization on import), splits, multiple tags per transaction, renaming/merging, and reporting by category.

Done criteria:
- Note at notes/research/category-tagging.md in the research skill's format: band table, draft gate lines, ranked gaps (owning lane each), verdict (build / don't / build minimal X).
- Every claim cites title + URL + date; unopened claims marked.
- Recommendation states a minimal first version and what to defer.

paths: notes/research/category-tagging.md (create the folder if missing). Do not touch product code, .tower/, or other lanes' files.
Report: dah report done with the note path. Building is a human decision; do not post a build item yourself, the hub will.

[11-13 16:54] done: Verdict: build a minimal version. All 5 tools studied (ledger, hledger, beancount+smart_importer, Actual, Firefly III) put one category on each posting, support splits, assign categories with regex rules on import and report by category. Minimal v1: colon-path category per posting, splits, an uncategorized default, a category report with depth roll-up, an ordered regex rules file on import, and rename/merge (draft gates G1-G6). Defer: free multi-tags and report-by-tag, rules learned from manual edits (Actual), ML auto-categorization (smart_importer). Limits: all sources are official docs (tier 2); ledger was read from its texinfo source because the site timed out; YNAB and GnuCash were not opened; Bean Counter has no code yet, so the gaps are present/absent only. · notes/research/category-tagging.md · open items: none
[11-13 16:55] wrapped: Category tagging note filed and committed (ffd2852); verdict: build a minimal version
