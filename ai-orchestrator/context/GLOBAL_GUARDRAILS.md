# Global Guardrails

Hard rules:

- No live trading.
- No order submission, cancellation, or modification.
- No account, balance, or position reads unless explicitly approved later.
- No private-key, signing, wallet, or credential-handling logic.
- No `.env` edits.
- No title similarity as settlement equivalence.
- No midpoint-fill assumptions.
- No stale quote profit claims.
- No reference-only executable legs.
- No sportsbook/reference odds as executable.
- No ignoring fees, slippage, gas, spread, depth, liquidity, quote age, settlement wording, deadlines, or timezones.
- Graph hints cannot become paper candidates.
- Uncertain outputs stay `WATCH` or `MANUAL_REVIEW`.
- `PAPER_CANDIDATE` requires strict review.

Any task that weakens evaluator gates, trusts new settlement normalization, changes fee/slippage/gas assumptions, promotes graph hints, or designs live execution must be reviewed before it can affect candidate promotion.
