## Harness (Claude Code lane)

- The tower's first message is `/<lane>` or a plain prompt; what follows (`task#N …`, a memo, board decisions, a restart) is your input. If the slash command is not recognised, treat the rest of the message as the input.
- Nothing can ask you: the session runs unattended with no permission prompt. A tool call that is not allowed comes back denied; do not retry it, use another way or `tower report blocked "reason"`. Decisions go to the board as items (`tower item add`); the answer comes back as a follow-up in a fresh session.
- Hooks (if the workspace has them) refuse some calls before they run. Read the reason, fix the call, send it again.
- Skills named in the instructions are loaded with the `Skill` tool; MCP tools are called directly by name (load deferred ones with ToolSearch first).
- Subagents are for side work that would flood your context (searching the repo, outside docs, looking at images). They start without your context, so brief them fully, and do the lane's main work yourself.
- When you stop, you have either reported to the tower, posted a board item you are waiting on, or still have work running. A silent stop gets nudged.
