# Markout Replay

## Purpose

`replay-paper-candidate-markouts` fills paper candidate ledger markout windows from later saved enriched snapshots. It is a saved-file-only research pass for checking how quoted bid/ask gaps changed after detection.

It is not trading, not P&L, not a fill simulator, not settlement-rule proof, and not an executable-liquidity claim. A spread closing is not guaranteed profit.

## Command

```powershell
python scan.py replay-paper-candidate-markouts --ledger reports\paper_candidates_ledger.json --polymarket-enriched-later reports\polymarket_orderbook_enriched_snapshot_later.json --kalshi-enriched-later reports\kalshi_orderbook_enriched_snapshot_later.json --output reports\paper_candidates_ledger_marked.json
```

For ad hoc replay against the latest saved enriched files:

```powershell
python scan.py replay-paper-candidate-markouts --ledger reports\paper_candidates_ledger.json --polymarket-enriched-later reports\polymarket_orderbook_enriched_snapshot.json --kalshi-enriched-later reports\kalshi_orderbook_enriched_snapshot.json --output reports\paper_candidates_ledger_marked.json
```

## Matching

Rows are joined by the existing ledger identifiers:

- Polymarket: `polymarket.market_id`
- Kalshi: `kalshi.ticker`

The replay does not infer missing IDs from titles, timestamps, or fuzzy matching. Title and time similarity do not prove settlement equivalence.

## Window Rules

The supported windows are:

- `t_plus_30s`
- `t_plus_5m`
- `t_plus_30m`
- `t_plus_2h`

A window is filled only when both later saved orderbook captures are within tolerance of the target time. The default tolerance is 60 seconds and can be changed with `--window-tolerance-seconds`.

`markout_status` values:

- `filled`: both later enriched orderbooks joined and timestamps are within tolerance
- `no_data`: the later snapshot is too early for that window or the original row lacks enough timing/direction data
- `stale`: at least one later quote is too late or otherwise outside the window tolerance
- `missing_market`: the later saved snapshots do not contain the joined market
- `missing_orderbook`: the later market exists but lacks an enriched orderbook or required bid/ask/time fields

## Price Logic

Replay uses the original ledger direction:

- original `BUY_YES` uses the later best ask
- original `SELL_YES` uses the later best bid

It never uses midpoint prices, never assumes a fill, and never walks the book. Later gross gap and estimated net gap reuse the evaluator's fee defaults: Polymarket no-fee placeholder and Kalshi conservative tiered fee estimate.

## Output Fields

Each markout window records:

- `markout_status`
- later quote capture timestamps
- later best bid and ask for both venues
- later bid/ask gross gap
- later per-leg fee estimates
- later estimated net gap
- change versus the original estimated net gap when available
- `spread_closed_boolean` when determinable

Null markouts are intentional. Missing, stale, too-early, or too-late evidence should remain null rather than being guessed.
