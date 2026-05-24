# Codex Handoff

## Before Coding, Read These Files

1. `docs/PROJECT_PURPOSE.md`
2. `docs/DATA_REALITY.md`
3. `docs/DIRECTORY_MAP.md`
4. `docs/CURRENT_STATUS.md`
5. `docs/EDGE_RESEARCH_PLAYBOOK.md`

## Rules For Future Codex Agents

- Do not summarize the chat and guess architecture. Inspect repo files and these docs first.
- Do not build live trading unless explicitly asked and gates are passed.
- Do not claim profitability from stale runs.
- Do not use midpoint fills.
- Do not ignore fees.
- Do not use low-confidence settlement labels in primary results.
- Do not mix approximate passive P&L with conservative taker P&L.
- Do not modify parser or settlement semantics without invalidating old runs.
- Do not optimize strategies until parser and settlement labels are validated.
- Always check `parser_version`, `settlement_version`, and stale run flags before trusting P&L.
- Keep `record-orderbooks` isolated. Do not put weather, NWS, settlement, replay, scanner, or dashboard work inside the pure orderbook recorder.
- The project is no longer weather-only in data collection. Weather remains the first fair-value edge track, but all open Kalshi markets may be recorded for market-making/liquidity research.
- Weather observations and forecast snapshots are recorded by separate commands. They may fail without stopping orderbook collection.
- Run `python main.py trading-readiness --last-days 7` before recommending paper testing or any live-trading work.
- Do not build live trading before readiness gates pass and the user explicitly asks for it.
- Do not call passive liquidity profitable unless fill evidence survives adverse-selection checks.
- Remember the 2026-05-16 data-quality fixes: NWS precipitation unit conversion, NWS CLI issuance timestamp parsing, duplicate parsed-contract suppression, opt-in Kalshi cache writes, live market-context fields on orderbook snapshots, live trade capture, broader station resolution, and removal of the scanner's legacy orderbook-snapshot bloat write.
- Existing DB history still needs maintenance: dedupe old `parsed_contracts`, repair or exclude pre-fix precipitation rows, and collect fresh rows to populate new market-context/trade-ID fields.
- As of 2026-05-16, recorded replay includes richer weather columns for future modeling. Rebuild recorded replay before expecting these columns to be populated for old snapshots.
- As of 2026-05-16, `analyze-market-making` is the main liquidity/maker research command. It uses live orderbooks plus `historical_trades` to test passive bid-improvement candidates against actual trade-print fill evidence and future mids. It is research-only and does not justify live trading by itself.
- As of 2026-05-16, `record-orderbooks --all-markets` is supported for broad market-making collection. It uses raw all-market metadata, batch orderbook requests, and capped global trade polling. Do not treat non-weather markets as fair-value edge candidates until category-specific data, parsing, settlement, and replay exist.
- As of 2026-05-18, `paper-market-making` is the bridge from analyzer candidate to real-money readiness. It is paper-only, one market/side at a time, uses trade-print fills only, cancels quotes after TTL, and never sends Kalshi orders. Do not call a candidate live-ready from analyzer output alone.

## Current Mental Model

This is a data/research engine. The valuable asset is recorded orderbook data across Kalshi, recorded as-of weather/forecast data for weather fair-value work, exact settlement data where available, and actual trade prints for passive-fill evidence. The goal is finding whether edge exists, not making dashboard numbers green.

Every strategy result must be explainable trade by trade. If the data is incomplete, say what is missing. If no edge exists, say: "No reliable edge found yet."

## When Continuing Work

1. Run tests.
2. Run `python main.py audit-recorded-data`.
3. Run `python main.py project-status`.
4. Check parser version and stale runs.
5. Check settlement label quality.
6. Check `python main.py weather-recorder-health --last-hours 24` if forecast-aware strategies are involved.
7. Build replay only after data quality passes.
8. Run sweep.
9. Run `python main.py validate-signals --last-days 7`.
10. Run `python main.py analyze-liquidity --last-days 7`.
11. Run `python main.py trading-readiness --last-days 7`.
12. Generate edge report.
13. Update `docs/CURRENT_STATUS.md`.

## How To End Every Implementation Response

End with:

- What changed.
- What commands were run.
- Test results.
- Data counts if known.
- What is trustworthy.
- What is not trustworthy.
- Whether to keep collecting data.
- Whether real-money trading is justified.
- Next exact command.

## Project Status Command

Use:

```powershell
python main.py project-status
```

It prints docs path, key DB counts, latest audit verdict, latest edge report, latest recommendation, recorder freshness warning, stale run counts, and active parser/settlement versions.

## Latest Verification Note

On 2026-05-16, the laptop repo was inspected after Claude's weather-contract data-collection fixes. A migration-order bug was found and fixed in `data/storage.py`: new columns from `_MIGRATION_COLUMNS` must be added before creating the `historical_trades(market_ticker, trade_id)` unique index. One test hygiene bug was also fixed: `tests/test_backtest_no_lookahead.py` now uses a temp SQLite DB instead of writing a dummy no-data run into the real research DB. Verified commands:

```powershell
python -m pytest tests/test_weather_recorders.py tests/test_nws_climate_report_parser.py tests/test_live_orderbook_recorder.py -q
python -m pytest -q
```

Result: full test suite passed (`43 passed`). Live trading remains disabled; no real-money trading is justified.

## Latest Weather Replay Note

Later on 2026-05-16, replay weather features were expanded without changing raw collection. New replay columns include dewpoint, humidity, wind, pressure, visibility, precip 1h/3h, precip accumulated today, forecast dewpoint/humidity/wind, forecast precipitation probability, forecast QPF, sky cover, day-of-year, month, and season. `weather-recorder-health` now keeps `health` focused on live collection and reports low-confidence station mappings separately as `station_mapping_status`; mapping warnings are scoped to recent/latest resolver output so stale active-map rows do not create false alarms.

Also on 2026-05-16, `trading-readiness` was clarified: if replay rows exist but no clean recorded sweeps have been run, it reports `NOT_READY_ANALYSIS_NOT_RUN` instead of `NOT_READY_NO_EDGE`. This preserves the distinction between "analysis has not run" and "tested strategies found no edge."

## Latest Market-Making Note

On 2026-05-16, a trade-print-based market-making analyzer was added. Command:

```powershell
python main.py analyze-market-making --last-days 7
```

It exports `reports/market_making_candidates.csv`, `reports/market_making_quote_samples.csv`, and `reports/market_making_summary.json`. First laptop run found enough data for research review (`73,083` snapshots, `13,684` trades, `1,159` candidate quotes, `170` trade-evidence fills) but no robust paper-watchlist candidate yet. Keep collecting; do not live trade.

## Latest All-Market Collection Note

Later on 2026-05-16, all-market collection was added for the market-making track. Commands:

```powershell
python main.py load-markets --all-markets --max-pages 1 --max-markets 1000
python main.py record-orderbooks --all-markets --interval-seconds 30 --max-markets 1000 --max-market-pages 1 --max-trade-pages 1
```

Implementation details: `data.kalshi_market_loader.KalshiMarketLoader.load_active_markets` loads raw open Kalshi markets without weather parsing; `live.orderbook_recorder.LiveOrderbookRecorder` can batch `/markets/orderbooks` requests and uses one bounded global `/markets/trades` poll per all-market cycle. Smoke tests with five all-market tickers and a 100-market batch succeeded, and `python -m pytest -q` passed (`48 passed`). One attempted uncapped global trade poll was stopped because it paginated too long; keep `--max-trade-pages` bounded unless a dedicated bulk trade loader is built.

On 2026-05-17, broad collection showed huge raw coverage but still no paper-ready maker candidate. The key diagnostic is not raw markets; it is two-sided/candidate/fill evidence. Latest analyzed window showed `markets=183300`, `two_sided_markets=860`, `candidate_markets=332`, `filled_markets=73`, `trade_evidence_fills=209`, `paper_watchlist_candidates=0`. `research.market_making_analysis` now filters scoring to two-sided books and reports these counts explicitly. It also avoids `date(ts)` filters so SQLite can use timestamp indexes.

Also on 2026-05-17, `research.market_universe` was added to discover and rank useful recorder targets before spending collection budget. Command:

```powershell
python main.py rank-market-universe --max-pages 20 --probe-limit 2000
```

It discovers open markets, probes a metadata/recent-activity-ranked subset of orderbooks, scores current book quality plus recent local snapshot/trade stats, persists latest rows in `market_universe_rankings`, and exports `reports/market_universe_ranked.csv`, `reports/market_universe_summary.json`, `reports/market_universe_high_priority_tickers.txt`, and `reports/market_universe_recordable_tickers.txt`. `record-orderbooks` can consume it:

```powershell
python main.py record-orderbooks --from-universe recordable --interval-seconds 30 --max-markets 1000
```

Smoke result: scanning 1000 discovered markets while probing 200 found `1` medium-priority and `20` low-priority recordable markets; the recorder consumed `recordable` and wrote 21 snapshots. Full test suite passed (`50 passed`).

After a larger 20-page scan, the top discovered names were mostly `KXMVE...` multivariate/combinatoric markets with recent trades but empty/current one-sided orderbooks. Those are poor passive-maker recorder targets. `rank-market-universe` now excludes `KXMVE` from probe budget and recorder priority by default, while `--include-multivariate` allows explicit diagnostics. The persisted ranking includes `ticker_family` and `excluded_by_prefix`. For production-ish collection, prefer `record-orderbooks --from-universe medium` after high/medium rows exist; `recordable` includes low-priority rows and should be used only when intentionally monitoring recent-trade-only markets.

## Latest Replay Scope Note

After all-market collection, `build-recorded-replay --last-days 3 --allow-unsettled` attempted to iterate roughly 193k recorded tickers, most of which were non-weather and impossible for the weather replay builder. This has been fixed: `RecordedOrderbookReplayBuilder` now discovers replay candidates from `parsed_contracts` first, then checks orderbook rows only for those weather tickers. The CLI also has:

```powershell
python main.py build-recorded-replay --last-days 3 --allow-unsettled --recorded-weather-only --max-markets 5
```

Use `--recorded-weather-only` for fast/offline smoke tests; it marks missing live weather as low-quality instead of fetching historical NWS observations. Smoke result on 2026-05-17: 5 markets, 3,290 rows, no skips. Do not use all-market raw orderbook counts as the replay market count.

## Latest Weather Mining Note

`python main.py mine-weather-edge` now mines recorded weather replay rows directly instead of relying only on the older fixed strategy sweep. It scores every as-of row with the weather fair-value model, requires executable bid/ask entry prices, skips rows after close / next local-day artifacts, throttles repeated signals per market, and computes settled P&L only when settlement labels are attached. It also supports targeted filters such as `--target range-bucket-buy-no`, attaches 30/60/final future-mid validation to each signal, reports segment robustness, and writes a small discovery/validation rule grid. Exports:

```powershell
reports\weather_edge_mining_signals.csv
reports\weather_edge_mining_summary.json
reports\weather_edge_rule_search.csv
```

First run on the current smoke replay:

```powershell
python main.py mine-weather-edge --last-days 3
```

After exact settlements were built for 2026-05-15 through 2026-05-16, broad mining was rejected: `60` signals, `57` settled, net `-96c`, verdict `REJECTED_LOSES_AFTER_FEES`. Important debugging lesson: early fake "weather_locked" signals were caused by stale live weather rows; the miner now requires fresh observations (`--max-observation-age-minutes`, default `90`) and reasonably fresh forecasts (`--max-forecast-age-minutes`, default `360`).

The focused command:

```powershell
python main.py mine-weather-edge --start 2026-05-15 --end 2026-05-16 --target range-bucket-buy-no
```

found `47` signals, `45` settled, `+81c` net, `53.3%` win rate, and simple settlement-P&L stress passed. It is still not paper-ready: the verdict is `RESEARCH_ONLY_WEAK_FUTURE_PRICE_CONFIRMATION` because the signals beat the 30-minute future mid only `35.9%` of the time and final available mids only `43.5%` of the time. Treat `range_bucket BUY_NO` as the current best weather hypothesis to monitor on new dates, not as a trading system.

After this miner update, `python main.py trading-readiness --last-days 7` returned `NOT_READY_NO_EDGE`: stale runs exist and the best clean sweep is not positive. Do not recommend real-money trading.

## Latest Market-Making / Universe Ranker Note

On 2026-05-18, `python main.py analyze-market-making --last-days 1` returned `PAPER_WATCHLIST_CANDIDATES` for the first time. The top candidate was `KXNBAGAME-26MAY25NYKCLE-NYK BUY_NO`: `198` candidate quotes, `57` trade-evidence fills, `4.46c` average 30-minute future-mid edge, and `11.8%` adverse rate. This means review/paper-watchlist, not live trading.

The universe ranker originally appeared to stall under:

```powershell
python main.py rank-market-universe --max-pages 50 --probe-limit 5000 --recent-hours 12
```

Root cause: the orderbook recorder was holding SQLite locks, and the ranker was also doing expensive raw market persistence before probing. Fixes made: raw market persistence is now opt-in (`--persist-markets`), raw market saves are bulk-upserted when used, progress logs were added, and `--skip-local-stats` lets the ranker avoid local DB reads while recorders are running. Diagnostic command that completed quickly:

```powershell
python main.py rank-market-universe --max-pages 50 --probe-limit 500 --recent-hours 1 --skip-local-stats --no-persist --no-export
```

It found `17` high-priority and `237` medium-priority current-book markets. For DB-backed recorder rotation, pause recorders briefly and run the ranker without `--no-persist`; then resume `record-orderbooks --from-universe medium`.

## Latest Paper Market-Making Note

On 2026-05-18, a paper-only passive market-making tracker was added:

```powershell
python main.py paper-market-making --market-ticker KXNBAGAME-26MAY25NYKCLE-NYK --side BUY_NO --interval-seconds 30 --duration-minutes 240 --quantity 1 --max-position 5 --max-open-quotes 1
```

It logs simulated passive quotes to `paper_market_making_quotes`, logs paper events to `paper_orders`, and exports per-market CSV/JSON reports. It fills a quote only when an actual `historical_trades` print trades through the quote price before the quote TTL and before the current paper run time. It then tracks current mark, unrealized P&L after conservative fees, and 5/15/30/60-minute future-mid edge once those future books exist. This is the fastest current route toward possible tiny real-money testing, but the gate is paper evidence first: enough fills, positive after fees, low adverse-selection rate, and no one-market/outlier dependency.

Focused tests passed for opening/filling, not filling from future trade prints, and cancelling before late trade prints. Live trading remains disabled.

## Latest SQLite Locking Note

After starting `paper-market-making`, the separate weather observation and forecast recorders later crashed with `sqlite3.OperationalError: database is locked` while updating `collector_state`. The immediate issue was not bad weather data; it was SQLite writer contention amplified by `Storage.init_db()` being called by every storage method on every heartbeat/write. `Storage` now has per-process one-time initialization and configures SQLite connections with `busy_timeout=120000`, `journal_mode=WAL`, and `synchronous=NORMAL`. Verified with `python -m pytest -q` (`62 passed`).

Operational restart sequence after this patch:

```powershell
cd C:\Users\mason\Downloads\kalshi-weather-edge
python main.py init-db
python main.py record-orderbooks --from-universe medium --interval-seconds 30 --max-markets 1000 --max-trade-pages 2
python main.py record-weather-observations --from-active-markets --interval-minutes 5
python main.py record-weather-forecasts --from-active-markets --interval-minutes 30
python main.py paper-market-making --market-ticker KXNBAGAME-26MAY25NYKCLE-NYK --side BUY_NO --interval-seconds 30 --duration-minutes 60 --quantity 1 --max-position 5 --max-open-quotes 1
```

Run these in separate terminals after stopping old crashed/stale sessions. If SQLite still locks, reduce simultaneous writers by pausing weather forecast collection first; orderbooks and paper market-making are the current money-pipeline priority.

## Latest Paper Progress Note

The first full 60-minute `paper-market-making` run for `KXNBAGAME-26MAY25NYKCLE-NYK BUY_NO` produced zero quotes because the market spread was only 2c, below the default 8c minimum. That is a correct no-trade outcome, but the original command was too quiet. `paper-market-making` now prints `PAPER_MM HEARTBEAT` on each loop and returns `PAPER_WAITING_FOR_SETUP` when it is alive but not quoting due to filters such as tight spread, stale book, depth, max position, or closed market. Smoke output showed the reason clearly; full suite passed (`62 passed`).

## Latest Market-Making Backtest Note

`python main.py backtest-market-making` now replays the paper market-maker loop over recorded orderbooks and trades. It is conservative: max-open-quote and max-position constraints, quote TTL, quote spacing, fixed fees, and actual trade-print fills only. It does not assume orderbook touches fill. Exports:

```powershell
reports\market_making_replay_candidates.csv
reports\market_making_replay_fills.csv
reports\market_making_replay_summary.json
```

Good first commands:

```powershell
python main.py backtest-market-making --last-days 1 --max-markets 50
python main.py backtest-market-making --last-days 1 --market-ticker KXNBAGAME-26MAY25NYKCLE-NYK --side BUY_NO
```

Smoke result: `python main.py backtest-market-making --last-days 1 --max-markets 10 --no-export` completed in ~38 seconds with `COLLECT_MORE_TRADE_EVIDENCE`, `12,860` snapshots, `10` markets, `5` trades, `4,408` opened replay quotes, and `3` fills. This confirms the tool works but that small slice is fill-thin. Full tests passed (`64 passed`).

## Latest Current-Setup Paper Target Note

On 2026-05-18, the first heartbeat-heavy `paper-market-making` run on `KXNBAGAME-26MAY25NYKCLE-NYK BUY_NO` stayed alive but opened no quotes because the live spread compressed to 3-4c while the safety minimum was 8c. This is correct no-trade behavior, not a program failure.

`backtest-market-making` now attaches the latest live book to each replay candidate and reports `current_ok`, `current_spread`, `current_paper_targets`, and `replay_supported_current_targets`. It also accepts:

```powershell
python main.py backtest-market-making --last-days 1 --max-markets 100 --require-current-setup
```

Use this before launching the next one-market paper run. A candidate should ideally be both current-quoteable and replay-supported: current spread/depth passes filters, trade-print fills exist in replay, 30-minute net edge after fees is positive, and adverse rate is low. Latest live DB check found `51` current quoteable targets in the first 100 replay markets, but only `2` were replay-supported; the top suggested command was:

```powershell
python main.py paper-market-making --market-ticker KXMLBRFI-26MAY211310CLEDET --side BUY_YES --interval-seconds 30 --duration-minutes 60 --quantity 1 --max-position 5 --max-open-quotes 1
```

This is still paper-only. Live trading remains disabled.

## Latest Safety / Diagnostic Changes (2026-05-19 Overnight)

### Liquidity Verdict Rename: `PAPER_READY_SPECIFIC_STRATEGY` → `PAPER_CANDIDATE_APPROX_FILLS`

`analyze-liquidity` no longer returns `PAPER_READY_SPECIFIC_STRATEGY`. That verdict was renamed `PAPER_CANDIDATE_APPROX_FILLS` because it was based on touch/replay-model fills (not actual trade prints) and was **silently removing** the "not reliable fill" safety reason in `trading-readiness`. It now **adds** an explicit disclosure instead:

> "Passive liquidity fill candidates are approximate (touch/replay model, not trade prints). Verify with analyze-market-making."

The only correct route to `PAPER_READY_SPECIFIC_STRATEGY` trading status still goes through `trading-readiness` with all gates passing. Do not treat `analyze-liquidity` output alone as paper-ready evidence.

### Market-Making Readiness Labels Split

`NEED_MORE_FILLS` in `analyze-market-making` per-market output was split into:
- `ZERO_TRADE_PRINT_FILLS` — 0 actual trade-through fills; essentially no fill evidence
- `FEW_FILLS_NEED_MORE` — 1–9 fills; encouraging but not enough

If a top candidate has `ZERO_TRADE_PRINT_FILLS`, it is a spread/depth candidate only. Do not quote it for paper yet.

### Collector Health: `stale_heartbeat` Flag

`collector-health` now reports `stale_heartbeat: true/false` in `collector_state`. A stale heartbeat means the recorder process is listed as RECORDING but has not fired a heartbeat in over 10 minutes (the heartbeat should fire every ~2–5 minutes while running). If `stale_heartbeat` is true and `process_appears_stale` is true, the recorder is likely hung or dead. Restart sequence is in the SQLite Locking Note above.

### Stale Run Counts in Reasons

`trading-readiness` stale reason now includes explicit counts: e.g. `"Stale runs exist (43 stale, 25 clean current-version sweeps)"`. `project-status` now reports `clean_backtest_runs` and `clean_strategy_sweeps_current_version` alongside `stale_backtest_runs`. Check these before trusting any P&L summary.

### Market-Making `to_text()` Now Shows `edge_net` and Readiness Buckets

The per-market line in `analyze-market-making` output now shows:
- `edge_net=` — edge after adverse-selection penalty (more reliable than raw `edge30`)
- `score=` — composite ranking score
- `readiness_buckets:` summary line before the top-10 list

Before recommending a paper candidate, verify `edge_net > 0` and `readiness` is `PAPER_WATCHLIST`, not `ZERO_TRADE_PRINT_FILLS` or `FEW_FILLS_NEED_MORE`.

### 2026-05-19 Market-Making Refresh Note

`analyze-market-making --last-days 7` now scopes historical trade loading to markets that have analyzable two-sided orderbook rows. This keeps the command from scanning millions of irrelevant Kalshi trade prints and makes the reported `trades=` count the relevant trade-print universe for the analyzed books, not the full Kalshi tape. Latest refreshed summary populated `paper_watchlist_tickers` correctly with two non-expired BUY_NO candidates. Trading readiness still says `NOT_READY_NO_EDGE`; real-money trading remains unjustified.

`project-status` now reports both `stale_recorded_sweeps` / `clean_recorded_sweeps` and the older `stale_strategy_sweeps` / `clean_strategy_sweeps_current_version` aliases. Use these fields to separate stale runs from clean current-version sweeps before trusting any P&L.

### 2026-05-19 Basket Paper-Making Note

`paper-market-making-basket` is now the preferred next step when one-market paper tracking is too quiet. It runs multiple paper-only market-making trackers selected from replay/current setups. It still never calls Kalshi order endpoints and fills only from local trade-print evidence. Default readiness next command now points here:

```powershell
python main.py paper-market-making-basket --last-days 1 --search-max-markets 100 --max-targets 5 --duration-minutes 60 --quantity 1 --max-position 5 --max-open-quotes 1
```

Do not mistake exploratory basket targets for live-ready edge. Exploratory targets exist to gather paper fill evidence faster; real-money gates still require repeatable fills, positive markouts after fees, low adverse selection, and manual review.

Current paper-fill truth layer: query `paper_market_making_quotes WHERE status='FILLED'`. As of the latest user output, there are two paper fills: `KXNBATEAMTOTAL-26MAY19CLENYK-NYK100 BUY_NO` with `future_edge_30m_cents=14`, and `KXNBATEAMTOTAL-26MAY19CLENYK-CLE91 BUY_NO` with no 30-minute markout yet and negative current mark. Basket final summaries may undercount fills after candidate refresh; fix cumulative basket reporting before relying on final basket summaries.

Folder move request: user wants the repo under `C:\Users\mason\prediction-markets-program\kalshi-fair-value-and-liquidity-edge`. Only do this after all Python writers are stopped. Do not move while orderbook/weather recorders or paper loops are running.

## 2026-05-21 Weather Replay Coverage Fix

Recent `build-recorded-replay --last-days N --recorded-weather-only` smoke runs returned zero markets because recent `orderbook_snapshots_live` rows did not overlap parsed weather-contract tickers. The older weather overlap stopped at `2026-05-16 22:14:00`; recent orderbook recording had been focused on ranked/all-market universes. Weather-only orderbook recording now has an opt-in persistence flag so discovered active weather markets are also saved/parsed for replay eligibility:

```powershell
python main.py load-markets --max-pages 3 --max-series 25
python main.py record-orderbooks --weather-only --persist-weather-markets --interval-seconds 30 --max-markets 100 --duration-hours 6
python main.py weather-replay-coverage --last-days 7
python main.py build-recorded-replay --last-days 1 --recorded-weather-only --max-markets 25
```

`--persist-weather-markets` does not broaden weather filters and does not touch trading/readiness gates. It only keeps parsed weather contracts aligned with the weather orderbooks being recorded.

New read-only diagnostics:

```powershell
python main.py weather-replay-coverage --last-days 7
python main.py paper-market-making-drilldown --ticker KXPRIMARYTURNOUT-KY4R26-120000 --side BUY_YES
```

`weather-replay-coverage` explains parsed-weather/orderbook overlap by day and suggests the smallest replay command expected to produce >0 markets. `paper-market-making-drilldown` prints per-quote paper evidence for one ticker/side. Both are research-only and make no order/auth/account calls.
