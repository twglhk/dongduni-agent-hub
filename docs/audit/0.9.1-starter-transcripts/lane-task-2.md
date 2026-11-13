task #2 research closed "Bank CSV import in top budgeting CLIs: should Bean Counter support it?"
Request #7 (sinjongha): Research how the top budgeting CLIs import bank CSV files (column mapping, dedupe, date formats) and whether Bean Counter should support it. Cover at least five tools and open every source.

model: opus
effort: medium

Outcome: one referenced research note answering (a) how at least five leading budgeting / plain-text accounting tools import bank CSV files, and (b) whether Bean Counter should support CSV import, and in what minimal shape.

Read first: notes/research/category-tagging.md (task #1). It already covers category assignment rules on import (hledger if-blocks, ledger convert/payee rules, Actual and Firefly rules, smart_importer). Don't repeat that; cite it, and only note how CSV import connects to those rules (where rules run, the unknown/uncategorized default).

Context: Bean Counter is a budgeting CLI with no product code yet (README.md only), so 'ours' is none.

Tools: at least five, e.g. ledger (convert), hledger (CSV rules), beancount (beangulp/importers, + fava), Actual Budget, Firefly III (Data Importer), GnuCash, or others you can document. Cover:
- column mapping: header vs position, config format, per-bank profiles, reusable mappings
- amounts: one signed column vs debit/credit columns, decimal/thousands separators, currency
- date formats: how they're specified, locale, ambiguous dd/mm vs mm/dd
- dedupe: how re-imports and overlapping files are detected (ids, hashes, fuzzy match on date+amount+payee), and what happens to duplicates
- encoding, header/footer rows to skip, multiple accounts per file
- preview / dry-run before writing

Hard rule from the human: open every source you cite. Nothing unopened may appear as evidence; if a source can't be opened, list it under Limits only.

Done criteria:
- Note at notes/research/csv-import.md in the research skill's format: band table, draft gate lines, ranked gaps (owning lane each), verdict (build / don't / build minimal X), Limits.
- At least five tools covered; every claim cites title + URL + version/date.
- Recommendation states a minimal first version and what to defer.

paths: notes/research/csv-import.md only. Do not touch product code, .tower/, or other lanes' files.
Report: dah report done with the note path. Building is a human decision; do not post a build item, the hub will.

[11-13 17:06] done: CSV import research: 6 tools (ledger convert, hledger, beangulp, Actual, Firefly Data Importer, GnuCash) all import CSV, all require an explicit date format, all dedupe (hash/id, date watermark or fuzzy) and all preview. Verdict: build minimal: one account per file, plain-text per-bank profile (position map, signed or debit/credit amount, date-format, decimal-mark, encoding, skip lines), exact id/hash dedupe with an occurrence counter, duplicates reported not written, dry-run default, then task #1 rules + uncategorized. Defer fuzzy matching vs manual entries, multi-account files, header-name mapping, OFX/CAMT, ML. Draft gates G1-G8. Every cited source opened; Firefly read from its GitHub docs repo (site blocked by Cloudflare), GnuCash from manual DocBook source; Fava and others unopened, listed under Limits. · notes/research/csv-import.md · open items: none
[11-13 17:06] handoff: [now] task #2 finished and reported done; wrapping (commit note, delete temp dirs). [plan] note written: pass; >=5 tools (6): pass; every claim cited title+URL+version/date: pass; minimal v1 + defer list: pass; all sources opened, unopened only in Limits: pass. Nothing left but wrap. [files] notes/research/csv-import.md [items] none
[11-13 17:06] wrapped: CSV import note committed (c0c8e6d, notes/research/csv-import.md), temp dirs removed
