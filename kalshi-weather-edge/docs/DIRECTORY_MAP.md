# Directory Map

## Root Files

- `main.py`: CLI entry point. Important commands include `record-orderbooks`, `record-orderbooks --all-markets`, `record-orderbooks --from-universe recordable`, `rank-market-universe`, `load-markets --all-markets`, `record-weather-observations`, `record-weather-forecasts`, `resolve-active-weather-stations`, `audit-recorded-data`, `build-recorded-replay`, `mine-weather-edge`, `sweep-recorded`, `analyze-market-making`, `backtest-market-making`, `paper-market-making`, `paper-market-making-basket`, `edge-report`, `project-status`, and semantic cleanup commands.
- `config.py`: Environment-backed settings. Do not casually loosen live-trading or risk defaults.
- `README.md`: User-facing setup and workflow.
- `kalshi_weather_edge.db`: Local SQLite database. Large and valuable. Do not delete unless intentionally resetting research history.

## data/

- `kalshi_client.py`: Kalshi REST client with retry/cache support. Used by market loading, history loading, scanner, and orderbook recorder.
- `kalshi_market_loader.py`: Discovers likely weather markets and persists raw markets plus parsed contracts. Also has a generic `load_active_markets` path for raw all-category metadata without weather parsing.
- `active_weather_station_resolver.py`: Maps active Kalshi weather markets to likely station codes for separate weather/forecast recorders.
- `kalshi_historical_loader.py`: Historical candlestick/trade loading where available.
- `weather_client.py`: NWS observations, IEM ASOS fallback, Open-Meteo forecast fallback. Do not treat Open-Meteo as settlement truth.
- `nws_climate_report_client.py` and `nws_climate_report_parser.py`: Exact NWS Daily Climate Report ingestion/parsing.
- `weather_settlement_loader.py`: Builds settlement labels. Current semantic version: `v2_range_bucket_semantics`.
- `weather_station_mapper.py`: City to station mapping and overrides. Fragile for cities where Kalshi rules use a specific station.
- `storage.py`: SQLAlchemy table definitions, migrations, upserts. Be careful with schema changes because old runs can become stale.

## parsing/

- `market_parser.py`: Converts Kalshi market/rule text into a `WeatherContract`.
- `rule_parser.py`: Extracts city, station, variable, threshold/range semantics, and settlement source.
- `weather_contract.py`: Pydantic contract model. Current parser version: `v2_range_bucket_semantics`.

Known fragile part: range/bucket markets such as `66-67 degrees` must be `contract_type=range_bucket`, not threshold-above/below.

## features/

- `weather_features.py`, `market_features.py`, `feature_builder.py`: Feature-generation helpers for scanner/backtest paths.
- Replay features must obey no-lookahead rules.

## models/

- `baseline_model.py`: Heuristic weather model.
- `prob_model.py`, `calibration.py`, `model_registry.py`: ML/calibration scaffolding. ML is not trusted without enough historical labels.

## strategies/

- `already_hit_threshold.py`: Threshold crossed or bucket made impossible.
- `late_day_high_fade.py`: Late-day high-temperature NO logic.
- `ladder_consistency.py`: Bid/ask monotonicity checks across threshold ladders.
- `passive_market_maker.py`: Passive quote scaffolding.
- `base.py`: Strategy interface.

## backtest/

- `recorded_audit.py`: Audits recorded full orderbook coverage.
- `recorded_replay.py`: Builds no-lookahead replay rows from recorded orderbooks, preferring recorded live weather/forecast snapshots when available.
- `recorded_backtester.py`: Runs recorded taker/signal/passive-approx tests and sweeps.
- `execution.py`: Kalshi binary orderbook math.
- `fees.py`: Conservative fee models.
- `metrics.py`, `simulator.py`, `runner.py`: Historical/candlestick replay path.
- `edge_report.py`: Markdown report and manual review exports.

Do not mix stale runs, approximate passive P&L, and conservative taker P&L in one edge decision.

## research/

- `market_making_analysis.py`: Research-only passive market-making analysis using live orderbooks plus actual trade prints across any recorded market. It exports market-making candidates and quote samples, but does not place orders.
- `market_making_replay.py`: Conservative replay/backtest of the paper market-maker loop over recorded books/trades. Uses TTL, quote spacing, max inventory, fees, and trade-print-only fills; exports replay candidates/fills/summary.
- `market_universe.py`: Discovers open Kalshi markets, probes selected orderbooks, ranks which markets deserve recorder budget, excludes `KXMVE` multivariate/combinatoric tickers by default, persists `market_universe_rankings`, and exports universe CSV/JSON/ticker files.
- `weather_edge_miner.py`: Mines recorded weather replay snapshots for executable fair-value/weather-locked dislocations, supports targeted filters such as `--target range-bucket-buy-no`, attaches future-mid validation, exports signal/rule-search files, and reports settled P&L only when labels exist.
- `liquidity_analysis.py`: Spread persistence and approximate passive adverse-selection diagnostics.
- `signal_validation.py`: Checks whether signals beat future prices.
- `trading_readiness.py`: Gatekeeper for paper/live readiness. It is not an edge discovery engine.

## live/

- `orderbook_recorder.py`: Records current Kalshi orderbooks. Default path remains weather-only, `--all-markets` records broad open Kalshi markets using batched orderbook requests and bounded global trade polling, and `--from-universe` records the latest ranked useful universe. Places no orders.
- `weather_recorder.py`: Records live station observations and forecast snapshots in separate processes. Must not block `orderbook_recorder.py`.
- `collector.py`: Long-running collection loop.
- `scanner.py`: Live/paper signal logger.
- `paper_market_maker.py`: Paper-only passive market-making tracker for one market/side. Opens simulated quotes, fills only from actual trade prints before TTL, exports paper quote evidence, and never sends orders.
- `paper_trader.py`: Paper trading scaffold.
- `risk_manager.py`, `order_manager.py`: Safety scaffolds. Live trading remains disabled.

## dashboard/

- `app.py`: Streamlit dashboard.
- `dataframe_utils.py`: Safe JSON flattening and duplicate-column protection.

## tests/

Critical tests protect against fake edge:

- `test_orderbook_math.py`: YES/NO bid/ask math.
- `test_market_parser.py`: Weather parser, including range buckets.
- `test_settlement_labels.py`: Comparator and range/bucket settlement logic.
- `test_replay_builder_no_lookahead.py`: Weather-as-of feature discipline.
- `test_dashboard_dataframe_utils.py`: DataFrame flattening does not create duplicate columns.

## Key Database Tables

- `markets`: Raw Kalshi market payloads across weather and non-weather categories when all-market loading/recording is used.
- `parsed_contracts`: Parsed `WeatherContract` payloads.
- `settlement_labels`: Settlement value and YES/NO label.
- `nws_daily_climate_reports`: Raw and parsed NWS climate reports.
- `orderbook_snapshots_live`: Recorded full current Kalshi orderbooks.
- `historical_trades`: Historical and live-polled trade prints. New rows should include `trade_id` when Kalshi supplies it; this is the strongest passive-fill evidence available short of true queue position.
- `market_universe_rankings`: Latest usefulness rank for open Kalshi markets. Priorities are `RECORD_HIGH_PRIORITY`, `RECORD_MEDIUM_PRIORITY`, `RECORD_LOW_PRIORITY`, `METADATA_ONLY`, and `IGNORE_EMPTY_OR_DEAD`; includes `ticker_family` and `excluded_by_prefix` for filtering noisy market families.
- `active_weather_station_map`: Active market to station mapping with confidence and warnings.
- `weather_observation_snapshots_live`: Append-only live station observations.
- `weather_forecast_snapshots_live`: Append-only as-seen forecast snapshots.
- `recorded_orderbook_replay_snapshots`: No-lookahead replay rows from recorded orderbooks.
- `signals`: Scanner decisions.
- `backtest_runs`: Backtest metadata; stale runs must be labeled.
- `backtest_trades`: Filled replay trades.
- `recorded_data_audits`: Recorded-data audit snapshots.
- `paper_orders`, `paper_positions`: Paper trading state.
- `paper_market_making_quotes`: Paper-only passive quote ledger with quote/fill/cancel times, simulated limit price, trade-print fill evidence, fees, current mark, and future-mid edge diagnostics.
