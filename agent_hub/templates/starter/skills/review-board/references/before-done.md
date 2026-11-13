# Before you report done: what could not be checked

Optional reference of the `review-board` skill. A check that can't say it did not run is not a check: a step that couldn't run and a step that ran and found nothing produce the same clean output, and the clean reading is the one that gets believed.

- **Unchecked is its own verdict.** Untested or without evidence means "unchecked", never "passed". One unchecked required criterion keeps the whole result off "passed", and it never hides a defect you did find.
- **Name the reason.** Each unchecked item says why: no build, no capture, the tool failed, an input is missing, it is out of this session's reach. "I looked and found nothing" and "I could not look" need different fixes.
- **No permissive default.** A missing value, an empty search, zero files scanned or an exit code alone is never a pass. Say which happened: "0 hits in 214 files" or "not scanned". Some test runners exit 0 even when they crash; read the last line of their output.
- **A skipped step shows in the output.** If a pass, a check or a subagent didn't run, the report names it. A silent skip reads like a success.
- **Don't fill a gap from memory.** An API or fact you could not find in the docs or the repo is written as "not sourced: <what>", and that item stays unchecked.
- **Watch a new check fail once.** Break what it guards, see it fail, restore it. A test or gate only ever seen green is not known to test anything.
- **Compare with a reference, not with your own gauges.** For anything people see, put the result beside the approved reference at the same size and conditions and list the first differences a user would notice; each is fixed or carried as an open line.

Before reporting, ask: if this step had silently not run, would the report look any different? If not, it isn't finished.
