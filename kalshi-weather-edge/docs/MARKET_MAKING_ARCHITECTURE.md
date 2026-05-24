# Market-Making Microstructure Architecture

This project has two separate research tracks that should stay separate in code, reports, and operator language.

1. Weather fair-value edge is weather-only. It depends on forecasts, observations, station mapping, exact settlement labels, recorded replay, and weather-specific validation.
2. Market-making microstructure research is venue/orderbook driven. It depends on bid/ask, depth, quote freshness, trade prints, fees, adverse selection, queue assumptions, and stale-event filters.

Market-making evidence can span non-weather markets because it is not trying to forecast the true probability of a weather event. It is trying to measure whether passive quoting behavior appears to get filled at favorable prices after conservative costs and adverse-selection penalties. That makes the evidence track broader, but not safer by default. It remains research-only until paper evidence is strong and trading-readiness gates are changed deliberately in a separate task.

## Boundary With Weather Fair Value

Weather fair-value research answers: "Does our weather model know something about settlement value?"

Market-making microstructure research answers: "Do recorded books and prints suggest passive quotes might collect spread without being selected against?"

Those are different claims:

- Weather replay can use only parsed weather contracts with settlement labels.
- All-market market-making can inspect sports, politics, crypto, and other Kalshi categories, but must not borrow weather readiness language.
- Weather-only market-making is just a filtered microstructure view over weather tickers. It still does not prove fair-value edge.
- `trading-readiness` remains the authority for readiness. Market-making reports are paper/research inputs, not readiness promotion.

## Venue Eligibility Taxonomy

Use these labels for future venue adapters and reports.

| Venue or Source | Market-Making Source Class | Current Status | Notes |
| --- | --- | --- | --- |
| Kalshi | `MARKET_MAKING_EXECUTABLE_VENUE` | Implemented read-only venue | Current live orderbook snapshots, trade prints, and paper market-making evidence are Kalshi-only. No order endpoints are used by research commands. |
| Polymarket | `MARKET_MAKING_EXECUTABLE_VENUE` | Planned/read-only venue | Needs normalized orderbook depth, trade prints or equivalent fills, fee model, token/unit handling, and legal/API review before paper research. |
| SX Bet | `MARKET_MAKING_EXECUTABLE_VENUE` | Planned/read-only venue | Research-only feasibility exists elsewhere; no wallet, signing, or order logic should be added in this project without a separate review. |
| ProphetX | `MARKET_MAKING_EXECUTABLE_VENUE` | Planned, access review needed | Potentially useful if API terms permit read-only market data and exchange-style books. |
| IBKR / ForecastEx | `MARKET_MAKING_EXECUTABLE_VENUE` | Planned, high-friction | Valuable if accessible, but account/API constraints and data shape need a separate boundary review. |
| Crypto.com prediction markets | `DO_NOT_USE_FOR_MARKET_MAKING_YET` | Unclear | Needs confirmation of orderbook/API availability, fees, permissions, and market structure. |
| The Odds API | `MARKET_MAKING_REFERENCE_ONLY` | Reference-only | Sportsbook odds are context only. They are not exchange orderbooks and cannot create a market-making candidate. |
| Traditional sportsbooks | `MARKET_MAKING_REFERENCE_ONLY` | Reference-only by default | Do not add sportsbook betting automation. Only a permitted exchange/orderbook API could change classification. |
| Manifold | `MARKET_MAKING_SIGNAL_ONLY` | Signal/reference only | Useful for sentiment/reference context, not executable microstructure evidence here. |
| Metaculus | `MARKET_MAKING_SIGNAL_ONLY` | Signal/reference only | Forecast signal only, not an orderbook venue. |

Reference-only and signal-only sources can help explain context, but they cannot be market-making legs, cannot create paper market-making candidates, and cannot support executable-liquidity claims.

## Normalized Market-Making Snapshot Interface

A venue-agnostic market-making layer should normalize venue snapshots into a small common interface before analysis:

- `venue_id`
- `market_id` and/or `ticker`
- `title` / `question`
- `category`
- `event_time`, `close_time`, and `settlement_time` where available
- `status`: open, closed, settled, suspended, halted, unknown
- `bid` / `ask` by side
- `depth` by side and level
- `quote_timestamp`
- `trade_prints` with side, price, size, and timestamp when available
- `fee_model`
- `min_tick`
- `contract_unit`
- `source_provenance`
- `restrictions`
- `unresolved_risks`

The current Kalshi tables already cover much of this for one venue through `orderbook_snapshots_live`, `historical_trades`, `market_status`, `market_close_time`, recorded depths, and paper quote ledgers. A future venue-agnostic layer should adapt Kalshi into this interface first, then add other venues only after read-only data access and source boundaries are reviewed.

## Paper Candidate Gates

Paper market-making candidates must pass conservative gates before being treated as worth forward paper collection:

- Real bid/ask, not midpoint-only prices.
- Displayed top-of-book depth or explicit size support.
- Fresh quote timestamp or a clear stale-data warning.
- Trade-print evidence, or an explicit reason why trade prints are unavailable.
- Fee, slippage, and adverse-selection penalty.
- Stale, closed, post-event, suspended, or likely-expired market filter.
- No midpoint-fill assumption.
- Queue-position caveat.
- Event-drift risk flag for sports, elections, crypto catalysts, and similar time-sensitive markets.
- Research-only output with no live readiness promotion.

The current Kalshi analyzer uses recorded orderbooks and observed trade prints. Those trade-print fills are inferred marketable prints crossing the simulated passive quote level, not our actual orders. Paper market-making fills are stronger than offline analyzer rows, but still do not prove queue priority or live executability.

## Evidence Levels

Use this ladder when describing output:

1. `OBSERVED_BOOK`: saved bid/ask/depth exists.
2. `TRADE_PRINT_EVIDENCE`: observed prints suggest a passive quote may have filled.
3. `PAPER_QUOTE_EVIDENCE`: forward paper quote ledger has simulated fills from local trade prints.
4. `PAPER_RESEARCH_CANDIDATE`: enough paper evidence to keep testing at tiny simulated size.
5. No live readiness level exists in this architecture plan.

Reports should decompose gross markout, fees, adverse-selection/slippage penalty, and net markout. They should also show missing 30-minute markouts, stale open quotes, too few fills, high adverse selection, and current unrealized negatives.

## Staged Implementation Plan

Stage 0: Current Kalshi all-market research.

- Keep `analyze-market-making` as the current Kalshi all-market microstructure analyzer.
- Keep `analyze-market-making --weather-only` as a weather ticker filter, not a fair-value gate.
- Keep paper market-making basket/drilldown reports research-only.

Stage 1: Venue-agnostic normalized snapshot schema.

- Define an inert schema or dataclass around the normalized interface above.
- Include source class, venue class, provenance, restrictions, and unresolved risks.
- Add tests proving reference-only sources cannot become market-making venues.
- First implementation command: `python main.py build-market-making-snapshot --venue kalshi --last-days 7`. It reads local Kalshi orderbook/trade-print tables only, writes `reports/market_making_snapshot_kalshi.json` and `.md`, and keeps detailed rows bounded while summary counts cover the full requested window.

Stage 2: Kalshi adapter into venue-agnostic schema.

- Map `orderbook_snapshots_live` and `historical_trades` into the normalized interface.
- Preserve existing Kalshi reports during migration.
- Keep behavior unchanged until parity tests pass.

Stage 3: All-market category diagnostics across Kalshi.

- Break down watchlist and rejected candidates by market family/category.
- Make post-event and multivariate/combinatoric risk easier to see.
- Keep weather fair-value readiness separate.

Stage 4: Add another read-only exchange-style venue.

- Prefer the venue with the cleanest legal/API/read-only fit, likely ProphetX or SX Bet depending access and terms.
- Do not add wallet, signing, order placement, account state, or execution logic.
- Require orderbook depth, quote timestamps, fee model, and trade prints or a fail-closed equivalent.

Stage 5: Cross-venue/reference context.

- Add reference context only after venue snapshots are normalized.
- The Odds API and sportsbooks remain reference-only and cannot create market-making candidates.
- Reference context may explain why a market is risky or stale, not why it is executable.

Stage 6: Conservative paper market-making simulation.

- Simulate paper quotes using normalized venue snapshots.
- Fill only on conservative print-through rules or a venue-specific tested equivalent.
- Track queue caveats, fees, slippage, stale quotes, markouts, and adverse selection.

There is no live execution stage in this plan.

## Current Research-Only Commands

- `python main.py analyze-market-making --last-days 7`
- `python main.py analyze-market-making --last-days 7 --weather-only`
- For quicker diagnostics on large tables, `analyze-market-making` also accepts read-only caps:
  `--max-markets`, `--max-snapshots`, and `--profile-runtime`. These caps are debugging controls only; capped reports mark `caps_truncated_analysis=true` and must not be treated as full-corpus evidence.
- `python main.py backtest-market-making --last-days 1 --max-markets 100`
- `python main.py paper-market-making-basket --last-days 1 --search-max-markets 100 --max-targets 5 --duration-minutes 60 --quantity 1 --max-position 5 --max-open-quotes 1`
- `python main.py paper-market-making-evidence --last-days 7`
- `python main.py paper-market-making-target-review --last-days 7`
- `python main.py paper-market-making-drilldown --ticker TICKER --side BUY_YES`

All of these are research or paper-only evidence commands. They must not call order endpoints, account endpoints, private-key signing, wallet logic, balance/position logic, or live execution logic.

## What Must Not Change Accidentally

- Do not make sportsbooks executable.
- Do not treat reference odds as a market-making leg.
- Do not use midpoint fills.
- Do not claim profit from stale quotes or incomplete markouts.
- Do not hide missing fees, missing depth, missing trade prints, quote age, queue priority, or event drift.
- Do not let all-market microstructure evidence promote weather fair-value readiness.
- Do not lower `trading-readiness` gates.
- Do not add live order, cancel, balance, position, wallet, signing, or execution paths.
