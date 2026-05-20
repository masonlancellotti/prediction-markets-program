# Current Status

## Current Date/Time

Update manually after each major run. Current repo documentation update: 2026-05-18.

## Implemented Components

- Live Kalshi orderbook recorder.
- Separate live weather observation recorder.
- Separate live forecast snapshot recorder.
- Active Kalshi weather market to station resolver.
- NWS Daily Climate Report client/parser.
- Settlement label builder with exact-source preference.
- Recorded full-orderbook replay builder.
- Recorded strategy sweeps.
- Trading readiness scorecard.
- Liquidity/adverse-selection analysis command.
- Trade-print-based passive market-making research command.
- Paper-only passive market-making tracker for one market/side; it never sends real orders.
- Conservative market-making replay/backtest command that simulates the paper-maker loop over recorded books/trades.
- Broad all-open-market orderbook collection path for market-making research.
- Fair-value opportunity ranker.
- Paper-only order logger/simulator scaffold.
- Risk engine for paper/research candidates.
- Signal future-price validation.
- Edge report generator.
- Streamlit dashboard.
- Project status and diagnostic commands.
- Parser/settlement semantics version: `v2_range_bucket_semantics`.
- Unit-aware live weather observation recorder for NWS length fields (`wmoUnit:mm`, `m`, `km`, `cm`, `in`).
- Live orderbook snapshots can now include market-state context from Kalshi `/markets` payloads: last price, previous bid/ask, volume, open interest, liquidity, status, and close time.
- Live orderbook recorder now polls recent trades per ticker and dedupes by `(market_ticker, trade_id)` when Kalshi supplies trade IDs.
- `parsed_contracts` writes now skip byte-identical latest payloads to stop new duplicate bloat.
- Kalshi response cache writes now require explicit `use_cache=True`.
- Recorded replay now promotes richer no-lookahead weather fields from live observation/forecast snapshots: month, day-of-year, season, dewpoint, humidity, wind, pressure, visibility, current/accumulated precip, forecast dewpoint/humidity/wind, forecast precip probability, forecast QPF, and sky cover.
- Weather recorder health now separates live collection health from low-confidence station mappings and scopes mapping warnings to recent/latest active resolver output instead of stale historical map rows.
- Trading readiness now distinguishes `NOT_READY_ANALYSIS_NOT_RUN` from `NOT_READY_NO_EDGE`; replay data without clean sweeps means analysis has not run, not that edge has been disproven.
- Added `python main.py analyze-market-making --last-days N`, which studies passive bid improvements using actual `historical_trades` fill evidence plus future orderbook mids. It exports `reports/market_making_candidates.csv`, `reports/market_making_quote_samples.csv`, and `reports/market_making_summary.json`.
- Added `python main.py record-orderbooks --all-markets`, which records raw orderbooks across open Kalshi markets for liquidity research without weather parsing or live trading. The broad path uses the multi-orderbook endpoint in batches and a bounded global trade poll (`--max-trade-pages`, default `1`) so trade collection cannot stall the recorder.
- Added `python main.py load-markets --all-markets` to persist raw metadata for open non-weather markets. These markets are collected for market-making first; future fair-value edge detection still requires category-specific data and settlement logic.
- Added `python main.py mine-weather-edge`, a proof-of-concept weather replay miner that scans recorded weather replay rows for executable fair-value dislocations, weather-locked/near-certain states, and forecast/as-of weather gaps. It supports targeted filters such as `--target range-bucket-buy-no`, attaches future-mid validation, runs a small discovery/validation rule grid, and exports `reports/weather_edge_mining_signals.csv`, `reports/weather_edge_mining_summary.json`, and `reports/weather_edge_rule_search.csv`. It is research-only and requires settlement labels plus future-price confirmation before any P&L interpretation.

## Current Known DB State

Do not trust stale counts in docs. Refresh with:

```powershell
python main.py audit-recorded-data
python main.py project-status
```

Laptop verification on 2026-05-16:

- Real SQLite DB present at `kalshi_weather_edge.db`, about 5.3 GB.
- `project-status` before new collection showed 927,501 live orderbook snapshots, 7,397 weather observations, 171,336 forecast rows, 664,457 recorded replay rows, 58 backtest runs, and 952 recorded strategy sweeps.
- Existing `parsed_contracts` table still has historical duplicates: 58,299 rows for 1,146 markets. New identical writes are skipped, but a one-shot cleanup has not yet been run.
- Existing historical precipitation rows recorded before the unit fix may be 1000x too large where NWS supplied millimeters. A one-shot repair has not yet been implemented or run.
- Existing `historical_trades` rows predate live trade ID capture; after migration they have `trade_id = NULL`. New recorder runs should populate `trade_id` when Kalshi supplies one.
- Existing `orderbook_snapshots_live` rows predate market-context enrichment; new enrichment columns are present but old rows are null.
- Existing `recorded_orderbook_replay_snapshots` rows predate richer weather replay fields; rebuild replay to populate the new columns.

## Current Blockers

- Need more resolved markets and sample size.
- Need exact NWS settlement labels for primary backtests.
- Need parser validation for range/bucket markets.
- Need stale run cleanup after semantic changes.
- Need robust passive fill assumptions.
- Need manual verification of low-confidence station mappings for cities where Kalshi may use a non-obvious official station.
- Need paper trading only after clean evidence.
- Need live paper results before any tiny live readiness claim.
- Need one-shot maintenance for historical `parsed_contracts` duplicates and previously mis-scaled live precipitation rows.
- Need new recorder data after market-context and trade-capture schema changes before using those fields in research.
- Need more trade-print evidence before paper market-making. First run on 2026-05-16 found enough data for research review, but no robust paper-watchlist candidate yet.
- Need sustained all-market orderbook/trade collection before broad market-making rankings are trustworthy.
- Need category-specific parsers and external data sources before non-weather fair-value edge detection is trustworthy.

## Current Recommendation

- Keep the recorder running.
- For broad liquidity search, run an all-market orderbook recorder separately or instead of the weather-only recorder after smoke testing rate limits.
- If possible, also run weather observation and forecast recorders in separate terminals.
- Do not trade real money yet.
- Fix data correctness before strategy expansion.
- Generate a new edge report after semantic cleanup and replay rebuild.
- Use `python main.py trading-readiness --last-days 7` as the gatekeeper. Current laptop result was `NOT_READY_DATA_INCOMPLETE`.

## Last Known Concern

Prior `already_hit` P&L may have been distorted by range/bucket parsing errors. Old backtest runs should be marked stale if parser or settlement logic changed.

On 2026-05-16, code verification found and fixed one migration-order issue introduced by the trade capture changes: `historical_trades.trade_id` must be added before creating the unique `(market_ticker, trade_id)` index. `Storage().init_db()` now succeeds on the laptop DB. A test hygiene bug was also fixed so `tests/test_backtest_no_lookahead.py` uses a temp DB instead of polluting the real DB. The full test suite passes (`43 passed`).

Later on 2026-05-16, richer replay weather features were added and the misleading `MISSING_STATION_MAPPING` health label was corrected. After live checks showed healthy collection and replay rows but no fresh sweeps, readiness wording was refined so missing sweeps report `NOT_READY_ANALYSIS_NOT_RUN`. Verified with `Storage().init_db()`, `python main.py weather-recorder-health --last-hours 2`, and the full test suite (`43 passed`).

Later on 2026-05-16, the project scope expanded to all Kalshi markets for market-making data collection only. `record-orderbooks --all-markets` now has explicit CLI support, broad market metadata loading, batched multi-orderbook collection, and bounded global trade polling. Smoke tests with five all-market tickers, one capped global trade page, and a 100-market batch succeeded. Full test suite passed (`48 passed`). Live trading remains disabled.

Post-smoke DB check on 2026-05-16 showed `markets_total=2288`, `recent_orderbook_markets_1h=220`, `recent_orderbook_rows_1h=1920`, `trades_total=32504`, and `recent_trades_1h=15059`. These are moving counts because the live weather recorders/orderbook recorder were still running.

On 2026-05-17, all-market collection had grown substantially, but market-making still was not paper-ready. `analyze-market-making --last-days 1 --no-export` showed `snapshots=909041`, `markets=183300`, `two_sided_markets=860`, `candidate_markets=332`, `filled_markets=73`, `trade_evidence_fills=209`, and `paper_watchlist_candidates=0`. The analyzer now reports two-sided and candidate market counts separately so raw all-market coverage is not confused with tradable maker setups.

Also on 2026-05-17, a market-universe ranking helper was added. `python main.py rank-market-universe` discovers open Kalshi markets, optionally probes orderbooks in 100-ticker batches, combines current book quality with recent local snapshot/trade stats, writes latest priorities to `market_universe_rankings`, and exports `reports/market_universe_ranked.csv`, `reports/market_universe_summary.json`, `reports/market_universe_high_priority_tickers.txt`, and `reports/market_universe_recordable_tickers.txt`. The recorder can now use `python main.py record-orderbooks --from-universe recordable` to focus on ranked useful markets instead of the first raw `/markets` page. Smoke result: `rank-market-universe --max-pages 5 --max-markets 1000 --probe-limit 200 --no-export` found `1` medium-priority and `20` low-priority recordable markets; `record-orderbooks --from-universe recordable --max-markets 25 --once --no-trades` recorded `21` snapshots. Full test suite passed (`50 passed`).

After a broader universe run on 2026-05-17, the first 20 `/markets` pages were dominated by `KXMVE...` multivariate/combinatoric markets with empty batch orderbooks. The ranker now excludes `KXMVE` from probe budget and recorder priority by default, records `ticker_family` and `excluded_by_prefix`, and has `--include-multivariate` for explicit diagnostics. This is a market-selection protection, not a live-trading feature.

Also on 2026-05-17, `build-recorded-replay` was hardened after all-market collection made raw orderbook ticker counts enormous. The weather replay builder now discovers tickers from parsed weather contracts first instead of scanning every recorded all-market ticker, supports `--max-markets` for smoke tests, and supports `--recorded-weather-only` to avoid slow external NWS historical fetches. Smoke result: `python main.py build-recorded-replay --last-days 3 --allow-unsettled --recorded-weather-only --max-markets 5` wrote `3,290` replay rows for `5` markets in about 25 seconds.

Later on 2026-05-17, `mine-weather-edge` was run after exact settlements were built for 2026-05-15 through 2026-05-16. Broad result rejected: `60` signals, `57` settled, net `-96c`, verdict `REJECTED_LOSES_AFTER_FEES`. Diagnosis: stale weather observations initially created fake locked signals, so the miner now gates max observation age (`90` minutes default) and max forecast age (`360` minutes default). Segment result worth further study: `range_bucket BUY_NO` had `45` settled signals, `+81c` net, `53.3%` win rate. Threshold-style mined signals were strongly negative.

After adding target filters, future-mid validation, richer segment summaries, and rule-search export, the focused command `python main.py mine-weather-edge --start 2026-05-15 --end 2026-05-16 --target range-bucket-buy-no` produced `47` signals, `45` settled, `+81c` net, and passed simple settlement-P&L stress. However it beat the 30-minute future mid only `35.9%` of the time and final available mids only `43.5%` of the time, so the verdict is now `RESEARCH_ONLY_WEAK_FUTURE_PRICE_CONFIRMATION`. This is a real hypothesis to monitor, not a paper-ready strategy.

Latest readiness check after this work: `python main.py trading-readiness --last-days 7` returned `NOT_READY_NO_EDGE`. Reasons: stale runs still exist and the best clean sweep is not positive. The command can take around a minute on the current all-market DB.

On 2026-05-18, market-making analysis finally produced one paper watchlist candidate: `KXNBAGAME-26MAY25NYKCLE-NYK BUY_NO` with `198` candidate quotes, `57` trade-evidence fills, `4.46c` average 30-minute future-mid edge, and `11.8%` adverse rate. Overall analyzer verdict: `PAPER_WATCHLIST_CANDIDATES`. This is not live-ready; review CSVs and run paper-only tracking before any real orders.

Also on 2026-05-18, `rank-market-universe --max-pages 50 --probe-limit 5000` stalled while recorders were running because SQLite was locked during schema/local-stat reads and raw market persistence. The ranker now bulk-upserts raw markets when requested, no longer persists raw market metadata by default, logs progress, and supports `--skip-local-stats` for running while recorders are active. Smoke command `python main.py rank-market-universe --max-pages 50 --probe-limit 500 --recent-hours 1 --skip-local-stats --no-persist --no-export` completed in ~19 seconds and found `17` high-priority and `237` medium-priority markets from current books.

Later on 2026-05-18, a paper-only market-making tracker was added to bridge from research candidates to real-money readiness. Command:

```powershell
python main.py paper-market-making --market-ticker KXNBAGAME-26MAY25NYKCLE-NYK --side BUY_NO --interval-seconds 30 --duration-minutes 240 --quantity 1 --max-position 5 --max-open-quotes 1
```

It reads locally recorded orderbooks and `historical_trades`, opens simulated passive quotes, marks fills only when actual trade prints pass through the limit price before the quote TTL, exports `reports/paper_market_making_quotes_<ticker>_<side>.csv` plus a JSON summary, and logs paper events into `paper_orders` / `paper_market_making_quotes`. It never calls Kalshi order endpoints. Focused tests passed for opening/filling, no future-print lookahead, and cancelling before late trade prints.

After paper tracking started, the weather observation and forecast recorders crashed with `sqlite3.OperationalError: database is locked` during heartbeat state updates. Root cause: every storage method was calling `Storage.init_db()`, so long-running processes repeatedly ran schema/table checks and migrations while other writers were active. `Storage` now initializes only once per process and SQLite connections use a longer busy timeout plus WAL/synchronous-normal pragmas. Full tests passed (`62 passed`). After this patch, stop long-running DB writers, run `python main.py init-db` once, then restart recorders.

The first 60-minute `paper-market-making` run on `KXNBAGAME-26MAY25NYKCLE-NYK BUY_NO` returned with zero paper quotes because the live spread compressed below the default 8c minimum (`NO_QUOTE Spread 2.0 below minimum 8.00c`). The command now prints `PAPER_MM HEARTBEAT` lines during long runs and uses `PAPER_WAITING_FOR_SETUP` when it is alive but filters prevent quoting. Smoke run confirmed progress output; full tests passed (`62 passed`).

Also on 2026-05-18, `python main.py backtest-market-making` was added. It replays the paper market-maker loop over recorded orderbooks/trades with quote TTL, quote spacing, max open quotes, max inventory, conservative fixed fees, and trade-print-only fills. It exports `reports/market_making_replay_candidates.csv`, `reports/market_making_replay_fills.csv`, and `reports/market_making_replay_summary.json`. Smoke result on the live DB: `python main.py backtest-market-making --last-days 1 --max-markets 10 --no-export` completed in 38 seconds and returned `COLLECT_MORE_TRADE_EVIDENCE` with `12,860` snapshots, `10` markets, `5` trades, `4,408` opened replay quotes, and `3` fills. Full tests passed after the change (`64 passed`).

Later on 2026-05-18, the paper target loop was tightened after `KXNBAGAME-26MAY25NYKCLE-NYK BUY_NO` stayed in `PAPER_WAITING_FOR_SETUP`: the live spread was only 3-4c, below the 8c safety minimum, so zero quotes was correct. `backtest-market-making` now annotates replay candidates with the latest current book and reports `current_ok`, `current_spread`, `current_paper_targets`, `replay_supported_current_targets`, and a `next_paper_command` only when a candidate is both currently quoteable and has positive replay fill evidence. Live DB check: `python main.py backtest-market-making --last-days 1 --max-markets 100 --no-export` found `72` replay fills, `51` current quoteable targets, and `2` replay-supported current targets; suggested paper command was `python main.py paper-market-making --market-ticker KXMLBRFI-26MAY211310CLEDET --side BUY_YES --interval-seconds 30 --duration-minutes 60 --quantity 1 --max-position 5 --max-open-quotes 1`. Full tests passed (`65 passed`).

## Next Best Actions

1. Restart pure orderbook collection.
2. For broad market-making research, start `record-orderbooks --all-markets` with a controlled cap and monitor health before scaling up.
3. If recorders are running and you only need a diagnostic universe probe, use `python main.py rank-market-universe --max-pages 50 --probe-limit 500 --skip-local-stats --no-persist`. This avoids local SQLite reads/writes.
4. To update the DB-backed universe used by `record-orderbooks --from-universe`, pause orderbook recorders briefly, then run `python main.py rank-market-universe --max-pages 50 --probe-limit 5000 --recent-hours 12`. Default behavior excludes `KXMVE` combinatoric markets; use `--include-multivariate` only for diagnostics.
5. Prefer `python main.py record-orderbooks --from-universe medium --interval-seconds 30 --max-markets 1000` when the ranker finds high/medium rows. Use `recordable` only if you intentionally want low-priority recent-trade-only markets too.
5. Restart separate weather observation and forecast recorders when weather fair-value research matters.
6. After enough fresh weather data and exact settlements exist, rebuild weather replay. Use `--recorded-weather-only --max-markets N` for smoke tests; do not run unbounded replay while expecting it to process all-market non-weather tickers.
7. Rerun weather sweeps.
8. Generate edge report.
9. Run `python main.py weather-recorder-health --last-hours 24` when forecast-aware analysis matters.
10. Run `python main.py analyze-market-making --last-days 7`.
11. If `analyze-market-making` still shows `PAPER_WATCHLIST_CANDIDATES`, run `python main.py backtest-market-making --last-days 1 --max-markets 100 --no-export` first and use its `next_paper_command` only if `replay_supported_current_targets > 0`.
12. Run `python main.py trading-readiness --last-days 7`.
13. For broad weather mining, run `python main.py mine-weather-edge --last-days 3` after replay is built. If signals appear with `settled_signals=0`, build exact settlements for that date window before trusting P&L.
14. For the current best weather hypothesis, run `python main.py mine-weather-edge --last-days 7 --target range-bucket-buy-no` and inspect both settlement P&L and `future_mid_*_beat_rate`. Do not paper trade it unless future-mid confirmation improves on new dates.

## 2026-05-19 Codex Cycle

Baseline checks: `python -m pytest -q` passed (`94 passed, 2 warnings`) before changes. `project-status` showed live orderbooks/trades fresh, `market_making_summary_warning=null`, `stale_strategy_sweeps=833`, and `clean_strategy_sweeps_current_version=357`. `trading-readiness --last-days 7` remained `NOT_READY_NO_EDGE` and requested `python main.py analyze-market-making --last-days 7`.

`analyze-market-making --last-days 7` initially timed out after about 5 minutes because it loaded all trade prints in the seven-day window. The analyzer now loads historical trades only for markets with analyzable two-sided books. Refreshed result completed and produced `PAPER_WATCHLIST_CANDIDATES`: `1,435,644` snapshots, `1,153` two-sided markets, `49,188` relevant trade prints, `1,064` trade-evidence fills, `2` paper watchlist candidates. `paper_watchlist_tickers` populated with `KXNBATEAMTOTAL-26MAY19CLENYK-CLE91 BUY_NO` and `KXNBAGAME-26MAY25NYKCLE-NYK BUY_NO`, both not expired.

`project-status` now exposes both legacy and handoff-friendly stale sweep names: `stale_recorded_sweeps=833`, `clean_recorded_sweeps=357`, `stale_strategy_sweeps=833`, `clean_strategy_sweeps_current_version=357`. Post-change checks: `python -m pytest -q` passed (`95 passed, 2 warnings`); `trading-readiness --last-days 7` remained `NOT_READY_NO_EDGE`. Next exact command: `python main.py backtest-market-making --last-days 1 --market-ticker KXNBATEAMTOTAL-26MAY19CLENYK-CLE91 --no-export`.

User then ran that exact backtest. Result: `COLLECT_MORE_TRADE_EVIDENCE`, one market, `2,973` snapshots, `8` trades, `562` replay quotes, `5` trade-print fills, `0.9%` fill rate, `12.80c` average net 30-minute edge, `0.0%` adverse rate. The current book qualified (`current_ok=True`, `current_spread=22.00`) and the replay produced `replay_supported_current_targets=1`, so the next paper-only command is valid for evidence collection: `python main.py paper-market-making --market-ticker KXNBATEAMTOTAL-26MAY19CLENYK-CLE91 --side BUY_NO --interval-seconds 30 --duration-minutes 60 --quantity 1 --max-position 5 --max-open-quotes 1`. This is not real-money-ready; fills are still too thin.

The one-hour paper run on `KXNBATEAMTOTAL-26MAY19CLENYK-CLE91 BUY_NO` opened/cancelled/reopened quotes correctly but got zero trade-print fills. Final status: `PAPER_ACTIVE_NO_FILLS_YET`, `13` quotes opened, `12` cancelled, `1` open at finish, `0` fills, current mark about `20c`, quote prices mostly `8c-10c`. This proves the paper loop is operational and conservative, but adds no fill-quality evidence. Next action is to keep collecting and either rerun paper longer on this target or use `backtest-market-making` to find a currently quoteable target with more recent fill activity.

Later on 2026-05-19, `paper-market-making-basket` was added to avoid over-pigeonholing paper evidence collection into one quiet market. It runs several paper-only market-making trackers at once, still using local orderbooks/trade prints only and never sending Kalshi orders. Selection starts from `backtest-market-making` current setups, prioritizes replay-supported targets, and can include exploratory current-quoteable targets when strict targets are scarce. Smoke command `python main.py paper-market-making-basket --once --dry-run --no-export --last-days 1 --search-max-markets 50 --max-targets 3` found `3` replay-supported current targets: `KXNBATEAMTOTAL-26MAY19CLENYK-CLE91 BUY_NO`, `KXNBAOVERTIME-26MAY23NYKCLE-OT BUY_NO`, and `KXNBATEAMTOTAL-26MAY19CLENYK-NYK124 BUY_NO`. It also cancelled the stale open quote from the prior paper run after TTL; no real orders were sent.

Post-basket checks: `python -m pytest -q` passed (`96 passed, 2 warnings`). `python main.py project-status` showed fresh orderbooks/trades, no recorder/trade warnings, and `market_making_summary_warning=null`. `python main.py trading-readiness --last-days 7` remains `NOT_READY_NO_EDGE`, but now recommends the faster paper evidence command: `python main.py paper-market-making-basket --last-days 1 --search-max-markets 100 --max-targets 5 --duration-minutes 60 --quantity 1 --max-position 5 --max-open-quotes 1`. Fresh weather edge check `python main.py mine-weather-edge --last-days 7 --target range-bucket-buy-no --no-export` still returned `RESEARCH_ONLY_WEAK_FUTURE_PRICE_CONFIRMATION`: `+81c` settled net but only `35.9%` 30-minute future-mid beat rate.

User then ran `backtest-market-making --last-days 1 --max-markets 100 --require-current-setup --no-export`, which found `35` current paper targets and `8` replay-supported current targets. Top target was `KXNBATEAMTOTAL-26MAY19CLENYK-NYK100 BUY_NO`: `699` replay quotes, `6` trade-print fills, `16.60c` net 30-minute replay edge, `0.0%` adverse, current spread `32c`. A one-hour `paper-market-making` run on that target produced the first useful forward paper fill in this cycle: `1` fill, `8` cancelled quotes, `11.1%` paper fill rate, fill at `8c`, current mark about `21c`, unrealized paper P&L `+12c` after fee, `avg_edge_30m=14.00c`, `future30_n=1`, `adverse30=0.000`. This is promising paper evidence but only one fill; it is not real-money-ready.

The DB paper-fill query now shows two filled paper quotes: `KXNBATEAMTOTAL-26MAY19CLENYK-NYK100 BUY_NO` filled at `8c`, later had `future_edge_30m=14c`, current mark `11c`, and `+2c` current unrealized paper P&L in the query; `KXNBATEAMTOTAL-26MAY19CLENYK-CLE91 BUY_NO` filled at `10c`, current mark `9c`, current unrealized paper P&L `-2c`, and no 30-minute markout yet. These are useful forward paper data points but still a tiny sample. Basket final summaries can understate earlier fills after candidate refresh, so use DB queries against `paper_market_making_quotes WHERE status='FILLED'` as the truth layer until cumulative basket reporting is fixed.

The user wants to move/rename the project to a larger parent folder after active writers are stopped. Proposed destination: `C:\Users\mason\prediction-markets-program\kalshi-fair-value-and-liquidity-edge`. Do not move the repo while `record-orderbooks`, `record-weather-observations`, `record-weather-forecasts`, or any paper loop is running against the SQLite DB.
