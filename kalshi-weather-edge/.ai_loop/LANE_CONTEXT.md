# Lane Context: kalshi-weather-edge

Weather evidence, replay, and market-making maintenance lane.

Focus:

- Weather evidence hygiene.
- Replay and settlement-label validation.
- Market-making research maintenance.

Priority:

- Lower priority than exact arbitrage.
- No real money until evidence-backed paper history exists.
- Weather edge requires external observations, forecasts, settlement labels, and conservative validation.

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
