# Lane Context: relative-value-scanner

Primary profit-speed lane.

Focus:

- Exact same-payoff pipeline.
- Conservative evaluator gates.
- Venue breadth and source inventory.
- Paper ledger only after strict review.

Known state:

- MLB semantic path worked, but conservative Polymarket fees killed a fake candidate. This is good fail-closed behavior.
- NBA/NHL currently fail closed around settlement timing and source drift.
- BTC/Fed need exact threshold and meeting/date-specific pipelines.
- No evaluator trust without review.

Do not add live trading, order submission, auth/account/private-key/signing/wallet logic, `.env` edits, or execution logic.

Codex summary discipline:

- Include task id.
- Include files changed.
- Include commands run.
- Include tests run and pass/fail.
- Include paper candidates count.
- Include risk flags.
- State whether Claude review is needed.
- Recommend exactly one next task or say blocked.
- State whether docs/state were updated.
