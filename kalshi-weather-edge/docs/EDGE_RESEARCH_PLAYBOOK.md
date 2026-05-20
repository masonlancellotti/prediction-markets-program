# Edge Research Playbook

## Daily Workflow

1. Keep the orderbook recorder running.
2. For broad market-making search, keep an all-market orderbook recorder running with controlled caps and monitor health.
3. Keep separate weather observation and forecast recorders running when testing forecast-aware weather strategies.
4. Run the scanner/live signal logger separately if needed.
5. Build exact settlements for newly resolved weather markets.
6. Build recorded replay for weather fair-value tests.
7. Run recorded sweeps.
8. Generate an edge report.
9. Check the dashboard recommendation.
10. Do not trade real money unless gates are passed.

## Commands

```powershell
python main.py record-orderbooks --weather-only --interval-seconds 30
python main.py record-orderbooks --all-markets --interval-seconds 30 --max-markets 1000 --max-market-pages 1 --max-trade-pages 1
python main.py rank-market-universe --max-pages 20 --probe-limit 2000
python main.py rank-market-universe --max-pages 50 --probe-limit 500 --skip-local-stats --no-persist
python main.py record-orderbooks --from-universe recordable --interval-seconds 30 --max-markets 1000
python main.py load-markets --all-markets --max-pages 1 --max-markets 1000
python main.py resolve-active-weather-stations
python main.py record-weather-observations --from-active-markets --interval-minutes 5
python main.py record-weather-forecasts --from-active-markets --interval-minutes 30
python main.py scan-live
python main.py audit-recorded-data
python main.py build-exact-settlements --start YYYY-MM-DD --end YYYY-MM-DD
python main.py build-recorded-replay --last-days 3 --recorded-weather-only --max-markets 100
python main.py mine-weather-edge --last-days 3
python main.py mine-weather-edge --last-days 7 --target range-bucket-buy-no
python main.py sweep-recorded --last-days 3
python main.py validate-signals --last-days 7
python main.py analyze-liquidity --last-days 7
python main.py analyze-market-making --last-days 7
python main.py backtest-market-making --last-days 1 --max-markets 50
python main.py backtest-market-making --last-days 1 --market-ticker TICKER --side BUY_NO
python main.py paper-market-making --market-ticker KXNBAGAME-26MAY25NYKCLE-NYK --side BUY_NO --dry-run --once
python main.py paper-market-making --market-ticker KXNBAGAME-26MAY25NYKCLE-NYK --side BUY_NO --interval-seconds 30 --duration-minutes 240 --quantity 1 --max-position 5 --max-open-quotes 1
python main.py trading-readiness --last-days 7
python main.py rank-opportunities --weather-only
python main.py daily-trading-research-update --last-days 7
python main.py edge-report --last-days 3
python main.py dashboard
```

Semantic cleanup and validation:

```powershell
python main.py project-status
python main.py reparse-contracts --weather-only --parser-version v2_range_bucket_semantics
python main.py rebuild-settlement-labels --weather-only --settlement-version v2_range_bucket_semantics
python main.py validate-settlement-labels --weather-only
python main.py validate-settlement-sources
python main.py mark-stale-runs --before-parser-version v2_range_bucket_semantics
python main.py rebuild-clean-edge-analysis --last-days 3 --dry-run
python main.py rebuild-clean-edge-analysis --last-days 3
```

Collector diagnostics:

```powershell
python main.py collector-health --last-hours 24
python main.py weather-recorder-health --last-hours 24
python main.py diagnose-settlement-skips --last-days 7
python main.py debug-city-settlements --city Austin --last-days 10
python main.py validate-orderbook-depths --last-days 3
```

## Research Questions

- Are markets stale after a threshold is already hit?
- Are late-day high-temp markets overpriced when the threshold is unlikely?
- Do ladder monotonicity violations exist and are they executable?
- Can wide spreads be passively quoted with enough edge?
- Which non-weather markets have persistent wide spreads, real trades, and low adverse selection?
- Do actual trade prints support passive fill assumptions, or are orderbook touches misleading?
- If the paper-maker loop had been running over the last day, which markets would have actually filled and beaten future mids after fees?
- Does any signal beat future or closing prices?
- Does any strategy survive fees and worse fills?
- Do passive fills beat future prices, or are they adverse-selection traps?
- Are current fair-value gaps large enough after uncertainty, fees, and risk buffers?
- Does direct replay mining surface executable weather dislocations before the fixed strategy sweep does?
- Does the isolated `range_bucket BUY_NO` weather-mining slice keep working on new dates, and does it beat 30/60-minute future mids instead of only final settlement?

## Strategy Gates

- Minimum 30 filled trades for preliminary confidence.
- Prefer 100+ trades for stronger confidence.
- Positive net P&L after fees.
- Survives 2x fees.
- Survives 1-cent and 3-cent worse fills.
- Not dependent on top 1 or top 3 trades.
- Primary settlement labels.
- No stale parser or settlement versions.
- Interpretable trade reasons.
- Signal must beat future/closing prices, not just settlement.
- A mined weather slice with positive settlement P&L but weak future-mid confirmation remains research-only.
- Passive fills must be traded-through or otherwise high-evidence, not touched-only.
- Market-making candidates should have actual trade-print fill evidence, positive future-mid edge after adverse-selection penalty, and enough fills across more than one market before paper quoting.
- `backtest-market-making` is the bridge between broad analyzer output and live paper runs. It can rank candidates faster than waiting an hour per market, but it is still queue-uncertain and must not skip forward paper testing.
- The current paper market-making path starts one market/side at a time from the analyzer's top `PAPER_WATCHLIST` row. Keep quantity at `1`, cap inventory, and treat the output as evidence collection, not real P&L.
- All-market market-making candidates should be reviewed by category before paper quoting; broad collection finds candidates, it does not explain event-specific risk.
- Weather-mining rows with `settled=False` are watchlist only. They need exact settlements before P&L claims.

## Paper/Live Gates

Paper testing may start only when a strategy has preliminary positive evidence.

For market making, preliminary paper testing means `analyze-market-making` shows `PAPER_WATCHLIST_CANDIDATES`, then `paper-market-making` is run against the named market/side. Graduation from paper requires repeated positive paper fills after fees, low 30-minute adverse-selection rate, and no dependence on a single event or stale market.

Use `backtest-market-making` before choosing the next paper target when the current paper target is idle because spreads tightened. Prefer candidates with repeated trade-print fills, positive average net 30-minute edge after fees, and low adverse rate.

As of 2026-05-18, `backtest-market-making` also checks the latest live orderbook for each replay candidate. For the fastest paper loop, run:

```powershell
python main.py backtest-market-making --last-days 1 --max-markets 100 --no-export
```

Then use `next_paper_command` only when `replay_supported_current_targets` is above zero. If `current_paper_targets` is positive but `replay_supported_current_targets` is zero, the market is wide right now but not yet backed by observed fill evidence; collect more data or widen the search before papering it.

Tiny live testing only comes after paper results agree with clean backtests. Live trading must remain disabled by default. The default recommendation should be `KEEP_COLLECTING_DATA` unless evidence is strong.

Run `python main.py trading-readiness --last-days 7` before recommending paper/live testing. If it says `NOT_READY_ANALYSIS_NOT_RUN`, run the requested analysis before making edge claims. If it says `NOT_READY_NO_EDGE`, do not soften that result.

As of 2026-05-19, refresh `python main.py analyze-market-making --last-days 7` before relying on `paper_watchlist_tickers`; the command is optimized to load trade prints only for analyzable two-sided book markets. If readiness returns a specific `backtest-market-making --market-ticker ...` command, run that before launching a new paper-only market-making session.

If one-market paper tracking opens quotes but gets no fills, switch to the basket runner:

```powershell
python main.py paper-market-making-basket --last-days 1 --search-max-markets 100 --max-targets 5 --duration-minutes 60 --quantity 1 --max-position 5 --max-open-quotes 1
```

Use basket output to collect fill evidence faster. Promote only targets with actual trade-print fills, positive future-mid markouts after fees, and low adverse-selection rates.
