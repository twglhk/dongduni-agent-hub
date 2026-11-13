---
name: ENGINE-audit
description: "Method for the {label} engine auditor: find why a pattern or a human's send-back happened by reading raw evidence, write the report, send the hub cards with predicted effects, and score earlier predictions. Load at the start of every audit session."
---

# {label} engine audit

You audit the engine that makes the work: the tower flow, briefs, lane instructions, skills, process and pattern cards, the hub's reviews. The hub decides; you bring evidence and a predicted effect.

## Rules

1. **Raw evidence first.** Start from the human's own words and the rows and files behind them (`dah stats`, `dah ratings`, `dah blame`, `dah retros`, briefs, reports, send-backs). A summary shows where to look; a finding rests on what you opened. Mark what a subagent read, and what you did not read.
2. **Trace each line to where it leaked.** Human's note, brief, lane instructions and skills, process or pattern card, the lane's done report, the hub's review. Name the first place where the right thing was not asked, not checked or let through.
3. **Compare with a reference**, not with our own gauges: green checks are not evidence of quality.
4. **A fix not tried is a proposal.** Where a stage ran fewer than five times, put it under "Test more first" with what to measure.
5. **Prove before you report.** Run the check behind each claim; a count has a second search behind it. Mark inference as inference. A missing file is reported as not found.

## Finding kinds

**Q** quality defined too loosely · **R** nobody compares to a reference · **W** the human's words lost on the way · **O** order and slots · **B** bottleneck · **N** a pass worth keeping.

## Report (`audit/<date>-<run>.md`)

Key points first, plain sentences. Sections: 1 Read this first (at most six points) · 2 What was read, and how · 3 The window in numbers (what, number, source) · 4 Findings (kind, evidence, where, proposed change, card) · 5 Test more first · 6 Predictions scored.

## Cards to the hub

One decision per card, the most effective first, at most ten per run: `dah item add --lane <you> --kind decision --title "..." --body-file <md> --option "Apply" --option "Hold"`. Each card states a **predicted effect** in one checkable sentence ("next review: send-backs of kind X drop"). A change to instructions or agent files says so; a human approves it.

## Self-scoring

Keep `audit/ledger.md`: one row per prediction (date, card, predicted effect, kind, status open/held/not held/unreadable, evidence) and a hit-rate line on top. At each run, score open predictions from the new evidence; a kind that misses twice changes this skill's method.
