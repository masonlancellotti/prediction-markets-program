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

Manual requests must include:

- command
- cwd
- reason
- expected runtime
- risk
- whether it writes files or a database
- what output matters

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
