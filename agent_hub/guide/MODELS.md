# Choosing a model and effort

Every task runs on one model at one effort. The hub picks both on purpose, in the brief or from the stage table, and judges the choice by **cost per completed task**, not per request: a stronger model that finishes in one pass is often cheaper than a weaker one that is sent back twice.

## The lineup (Anthropic docs, cached 2026-11-03; check the docs before you rely on a price)

| model | alias | price per MTok (in/out) | what it is for | effort notes |
|---|---|---|---|---|
| Claude Fable 5.2 | `fable` | $8 / $40 | the most capable: long-horizon engineering, whole-codebase review, pieces Opus failed twice | thinking is always on. Start at `high`; `xhigh` only where a gain was measured; `max` only to probe the ceiling |
| Claude Opus 5.5 | `opus` | $4 / $20 | new design and taste calls, modeling, rigging, animation, rules that cross systems, hard debugging | the API default is `medium`: set it explicitly |
| Claude Sonnet 5.5 | `sonnet` | $2 / $10 | the everyday worker | default `high`; levels were recalibrated, so `medium` is the step down for agentic coding and multistep tool use |
| Claude Haiku 5.5 | `haiku` | $0.10 / $0.50 | bulk reading or extraction by subagents only | default `medium` |

Effort levels: `low`, `medium`, `high`, `xhigh`, `max`. A model name can also be a full id (`claude-opus-5-5`) or another vendor's (`gpt-5`); `dah task` warns on a bare word that is none of the four aliases and on an effort outside the five levels.

## The rule

1. **Start from the model's own default**: Sonnet at `high`, Opus at `medium`.
2. **Raise to Opus `high`** for a new design or taste call, a rule that crosses systems, modeling, rigging or animation, a piece Sonnet failed once, and hard debugging or a refactor with hidden coupling.
3. **Opus `medium`** for long reading with a plain output: audits, surveys, research notes.
4. **Sonnet `medium`** only for plainly mechanical work: captures to a list, data entry, scripted moves, test runs.
5. **Fable `high`** for long-horizon engineering, whole-codebase reviews, and a piece Opus failed twice. Run it as a trial first and keep it if the first-pass rate (`dah ratings`) beats Opus on the same kind of work.
6. **Make the task small before making the model strong.** Split the hard piece into its own task that runs on the stronger model; leave the rest on the default.

## Where it is set

- **The brief**: `model: opus` and `effort: high`, one per line. A brief line always wins. `dah task` warns when either is missing; `"require_model_lines": true` in `tower.json` makes it refuse.
- **The stage table**: a pattern library's `model · effort` column (`stage_table`, or an engine's `library`). A brief that says `Line: #3 … stage 2` takes the cell's value for that stage. A cell is `opus/high` for the whole row or per stage: `1-2 sonnet/high; 3 opus/high`. Across several stages the strongest model and the highest effort win, so split a brief at the boundary if you do not want that.
- **The lane**: `"model"` in `tower.json` is the fallback when neither the brief nor the table names one. Effort has no lane setting; it comes only from the brief or the table.

The starter subagents in `.claude/agents/` follow the same table: `explore` and `librarian` on Haiku `medium` (bulk reading), `looker` and `general` on Sonnet `high`. `dah init` asks the claude CLI once per model; a model it refuses becomes `model: inherit` (the calling session's model). Edit the `model:` and `effort:` lines freely; `dah update` keeps them.

The engine templates ship their libraries with these defaults (`sonnet/high`, `opus/high` where a stage is a design call). The `research` lane defaults to Opus `medium`; the engine auditor to Fable `high` (Opus `high` where Fable is not configured).

Only the Claude and Codex runtimes honour a per-task model and effort; an OpenCode lane keeps its configured model.
