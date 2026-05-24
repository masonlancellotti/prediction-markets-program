# Command Policy

Agents must route command requests through lane `.ai_loop` files. They should not ask Mason to paste command outputs manually.

## Safe Short Auto

- Classification: `SAFE_SHORT_AUTO`
- Destination: `.ai_loop/COMMANDS_SHORT_PENDING.jsonl`
- Runner may execute only if the command is explicitly allowlisted, bounded, and scoped inside the lane folder.

Required JSONL schema:

```json
{"id":"unique-stable-id","classification":"SAFE_SHORT_AUTO","cwd":"C:\\absolute\\lane\\path","command":"git status --short","reason":"why needed","expected_output":"what success looks like","timeout_seconds":120}
```

Every field is required. `cwd` must be an absolute path inside the lane folder. `timeout_seconds` must be between 1 and 180.

## Long Or Risky Manual

- Classification: `LONG_OR_RISKY_MANUAL`
- Destination: `.ai_loop/COMMANDS_LONG_REVIEW.md`
- Mason reviews and runs manually, preferably through `scripts/run-manual-command-and-log.ps1`.
- Long/risky commands are never auto-run.

Manual requests must use this structured markdown section:

```text
## command-id-here
id: command-id-here
lane: relative_value|graph|weather
cwd: C:\absolute\lane\path
command: exact command to run
why_needed: why the command is needed
blocking_task_id: task id this blocks, or none
expected_output: what output matters
risk_reason: why this is manual instead of SAFE_SHORT_AUTO
timeout_suggestion: seconds
status: OPEN | RUNNING | DONE | SKIPPED
```

Lifecycle:

- `OPEN`: Mason has not run or skipped it yet.
- `RUNNING`: Mason started it, typically with `run-manual-command-and-log.ps1 -Background`.
- `DONE`: Output has been written to `.ai_loop/COMMAND_RESULTS.md`.
- `SKIPPED`: Mason decided not to run it.

If a manual command blocks the current task, GPT should mark that task `WAITING_USER_COMMAND`. If independent ready tasks exist in the same lane, GPT may select one of them next. If no independent task exists, GPT should mark the lane `BLOCKED` and write `BLOCKED_REASON`.

If a task needs credentials, account setup, API connection, venue eligibility approval, or risky settlement approval, GPT must write `USER_ACTION_REQUIRED.md` and mark the task `BLOCKED_USER` instead of guessing.

Manual command output must return through `.ai_loop/COMMAND_RESULTS.md`. Mason should run approved commands with:

```powershell
powershell -ExecutionPolicy Bypass -File .\ai-orchestrator\scripts\run-manual-command-and-log.ps1 -LaneName relative_value -CommandId command-id-here -Command "exact command"
```

## Never Auto-Run

- Live trading, order submission, cancel, or modify.
- Auth, account, balance, position, private-key, signing, wallet, or secrets commands.
- `.env` reads or edits.
- Destructive DB operations.
- `git reset`, `git clean`, broad deletes, or destructive filesystem operations.
- Commits or pushes unless Mason explicitly asks.
- Installs, deploys, proxy/VPN/Tor, scraping infrastructure, or browser automation.

## Allowlist Summary

The short-command runner keeps a hard allowlist. It currently allows only narrow diagnostics such as:

- `git status --short`
- `git diff --stat`
- `git diff --name-only`
- `git branch --show-current`
- `rg ...`
- `python -m compileall ...`
- targeted `pytest` with `-q`
- explicitly allowlisted narrow help commands

Anything outside the allowlist is rejected and logged to `COMMAND_RESULTS.md`.
