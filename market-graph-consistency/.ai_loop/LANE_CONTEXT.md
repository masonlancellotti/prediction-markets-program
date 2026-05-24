# Lane Context: market-graph-consistency

Diagnostic structural hints only.

Focus:

- Structural consistency checks.
- Hint schema.
- Hint diff outputs.
- Watchlist or manual-review diagnostics.

Rules:

- Outputs stay `WATCH` or `MANUAL_REVIEW`.
- No paper candidate generation.
- No evaluator integration.
- Future use is as information-only input unless strict review approves otherwise.

Do not add live trading, order submission, auth/account/private-key/signing/wallet logic, `.env` edits, or execution logic.

Codex summary discipline:

- Include task id.
- Include files changed.
- Include commands run.
- Include tests run and pass/fail.
- Include paper candidates count, which should remain zero for this diagnostic lane.
- Include risk flags.
- State whether Claude review is needed.
- Recommend exactly one next task or say blocked.
- State whether docs/state were updated.
