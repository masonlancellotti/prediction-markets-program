# Project Charter

Main goal: build a conservative prediction-market relative-value and structural-consistency system.

The system should prioritize exact, reviewable relationships over broad predictive claims. The best near-term profit path is:

1. Exact same-payoff arbitrage.
2. Structural consistency diagnostics.
3. Venue breadth and source inventory.
4. Conservative paper ledger promotion after strict review.

Lanes:

- `relative-value-scanner` is the primary lane and the main profit-speed path.
- `market-graph-consistency` is diagnostic-only structural hinting.
- `kalshi-weather-edge` is lower priority unless strict paper evidence appears.

Operating belief: killing fake candidates is progress. A candidate rejected for fees, stale quotes, settlement mismatch, source drift, missing depth, or non-executable legs improves the system.

Success means fewer false positives, cleaner exact universes, better conservative ranking, and paper candidates that survive strict fee/depth/freshness/settlement review.
