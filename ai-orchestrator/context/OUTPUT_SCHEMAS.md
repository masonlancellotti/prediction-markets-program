# Output Schemas

The GPT prompter must return every marker section below on every successful response. The PowerShell prompter loop validates this contract before it updates `NEXT_CODEX_PROMPT.md`.

Use `UNCHANGED` when a section should not modify a file. Empty sections are only allowed for command request sections.

## Required GPT Markers

```text
UPDATED_PROGRAM_STATUS_START
UNCHANGED or full replacement PROGRAM_STATUS.md text
UPDATED_PROGRAM_STATUS_END

UPDATED_ACTIVE_GOALS_START
UNCHANGED or full replacement ACTIVE_GOALS.md text
UPDATED_ACTIVE_GOALS_END

UPDATED_NEXT_STEPS_START
UNCHANGED or full replacement NEXT_STEPS.md text
UPDATED_NEXT_STEPS_END

UPDATED_LANE_STATUS_START
UNCHANGED or full replacement LANE_STATUS.md text
UPDATED_LANE_STATUS_END

NEXT_CODEX_PROMPT_START
one concrete bounded Codex task with a task id, or UNCHANGED
NEXT_CODEX_PROMPT_END

SHORT_COMMANDS_JSONL_START
zero or more JSONL safe short command requests
SHORT_COMMANDS_JSONL_END

LONG_COMMANDS_MD_START
zero or more markdown long/manual command requests
LONG_COMMANDS_MD_END

NEXT_ACTION_PACKET_START
short lane packet including selected task id and next owner
NEXT_ACTION_PACKET_END

CLAUDE_REVIEW_NEEDED_START
YES or NO, with one short reason
CLAUDE_REVIEW_NEEDED_END

BLOCKED_REASON_START
UNCHANGED or a concise blocker that should pause the lane
BLOCKED_REASON_END

ROADMAP_BACKLOG_UPDATES_START
UNCHANGED or full replacement ROADMAP_BACKLOG.json
ROADMAP_BACKLOG_UPDATES_END

USER_ACTION_REQUIRED_START
UNCHANGED or markdown entries to append to USER_ACTION_REQUIRED.md
USER_ACTION_REQUIRED_END

TASK_QUEUE_UPDATES_START
UNCHANGED or full replacement TASK_QUEUE.json
TASK_QUEUE_UPDATES_END

MODEL_USED_START
model name
MODEL_USED_END

REASONING_SUMMARY_START
UNCHANGED by default. If `GPT_PROMPTER_REASONING_SUMMARY=1`, a brief non-sensitive planning/routing summary of 1-2 lines.
REASONING_SUMMARY_END
```

`REASONING_SUMMARY` remains required for parser compatibility. Hidden model reasoning is never visible. Visible summaries are generated output text and cost output tokens, so routine GPT prompter calls should use `UNCHANGED`.

## Handled Sections

The PowerShell prompter loop handles every marker above:

- Full replacement sections: `UPDATED_PROGRAM_STATUS`, `UPDATED_ACTIVE_GOALS`, `UPDATED_NEXT_STEPS`, `UPDATED_LANE_STATUS`, `ROADMAP_BACKLOG_UPDATES`, `TASK_QUEUE_UPDATES`.
- Direct lane handoff sections: `NEXT_CODEX_PROMPT`, `NEXT_ACTION_PACKET`, `SHORT_COMMANDS_JSONL`, `LONG_COMMANDS_MD`, `CLAUDE_REVIEW_NEEDED`, `BLOCKED_REASON`.
- Audit sections: `MODEL_USED`, `REASONING_SUMMARY`.
- `USER_ACTION_REQUIRED` appends new markdown entries instead of replacing the whole file.

If required markers are missing, the loop writes the failure to `FAILURE_LOG.md`, sets `GPT_REVIEW_NEEDED.txt`, and does not overwrite `NEXT_CODEX_PROMPT.md`.
