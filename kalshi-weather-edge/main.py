from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import date, datetime

from backtest.edge_report import EdgeReportGenerator
from backtest.recorded_audit import RecordedDataAuditor
from backtest.recorded_backtester import RecordedOrderbookBacktester
from backtest.recorded_replay import RecordedOrderbookReplayBuilder
from backtest.replay_builder import ReplayBuilder
from backtest.runner import BacktestRunner
from config import settings
from data.active_weather_station_resolver import ActiveWeatherStationResolver
from data.nws_climate_report_client import NWSClimateReportClient
from data.kalshi_historical_loader import KalshiHistoricalLoader
from data.kalshi_market_loader import KalshiMarketLoader
from data.storage import Storage
from data.weather_settlement_loader import WeatherSettlementLoader
from live.collector import LiveDataCollector
from live.orderbook_recorder import LiveOrderbookRecorder
from live.paper_market_maker import PaperMarketMaker, PaperMarketMakerConfig
from live.paper_market_making_basket import PaperMarketMakingBasket, PaperMarketMakingBasketConfig
from live.weather_recorder import WeatherForecastRecorder, WeatherObservationRecorder
from live.paper_trader import PaperTrader
from live.scanner import LiveScanner
from maintenance import ProjectMaintenance
from research.liquidity_analysis import LiquidityAnalyzer
from research.market_making_analysis import MarketMakingAnalyzer, MarketMakingConfig
from research.market_making_snapshot import MarketMakingSnapshotBuilder, MarketMakingSnapshotConfig
from research.paper_market_making_drilldown import PaperMarketMakingDrilldownConfig, PaperMarketMakingDrilldownReporter
from research.paper_market_making_evidence import PaperMarketMakingEvidenceConfig, PaperMarketMakingEvidenceReporter
from research.paper_market_making_target_review import PaperMarketMakingTargetReviewConfig, PaperMarketMakingTargetReviewer
from research.daily_weather_evidence import (
    DailyWeatherEvidenceConfig,
    DailyWeatherEvidenceDrilldownConfig,
    DailyWeatherEvidenceDrilldownReporter,
    DailyWeatherEvidenceRangeConfig,
    DailyWeatherEvidenceRangeReporter,
    DailyWeatherEvidenceRefreshConfig,
    DailyWeatherEvidenceRefreshReporter,
    DailyWeatherEvidenceReporter,
)
from research.market_making_replay import MarketMakingReplayBacktester, MarketMakingReplayConfig
from research.market_universe import MarketUniverseBuilder, MarketUniverseConfig
from research.opportunity_ranker import OpportunityRanker
from research.signal_validation import SignalValidator
from research.source_smoke import SourceSmokeReporter
from research.trading_readiness import TradingReadiness
from research.weather_edge_miner import WeatherEdgeMiner, WeatherEdgeMiningConfig
from research.weather_replay_coverage import WeatherReplayCoverageConfig, WeatherReplayCoverageReporter


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Kalshi research system: weather fair-value edge plus broad market-making data collection")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create local SQLite schema")
    load = sub.add_parser("load-markets", help="Load active Kalshi markets")
    load.add_argument("--max-pages", type=int, default=None)
    load.add_argument("--max-series", type=int, default=None)
    load.add_argument("--max-markets", type=int, default=None)
    load.add_argument("--all-markets", action="store_true", help="Load all open Kalshi markets as raw metadata. Default keeps weather-only parsing.")
    load.add_argument("--persist-snapshots", action="store_true", help="Also append raw market payloads to market_snapshots.")
    scan = sub.add_parser("scan-live", help="Run one live scanner cycle in paper-only mode")
    scan.add_argument("--max-markets", type=int, default=50)
    backtest = sub.add_parser("backtest", help="Run conservative backtest from local replay data")
    backtest.add_argument("--strategy", required=False)
    backtest.add_argument("--all", action="store_true")
    backtest.add_argument("--start", required=False)
    backtest.add_argument("--end", required=False)
    backtest.add_argument("--mode", choices=["taker", "signal", "passive"], default="taker")
    backtest.add_argument("--label-quality", choices=["primary", "exploratory", "all"], default="primary")
    history = sub.add_parser("load-history", help="Load settled weather markets plus historical Kalshi candlesticks/trades")
    history.add_argument("--start")
    history.add_argument("--end")
    history.add_argument("--limit", type=int, default=100)
    history.add_argument("--market-ticker")
    history.add_argument("--weather-only", action="store_true")
    history.add_argument("--period", type=int, default=60)
    history.add_argument("--no-trades", action="store_true")
    settlements = sub.add_parser("build-settlements", help="Build weather settlement labels from station observations")
    settlements.add_argument("--start")
    settlements.add_argument("--end")
    settlements.add_argument("--market-ticker")
    exact = sub.add_parser("build-exact-settlements", help="Build settlement labels, preferring exact NWS CLI reports when available")
    exact.add_argument("--start")
    exact.add_argument("--end")
    exact.add_argument("--market-ticker")
    climate = sub.add_parser("fetch-nws-climate-report", help="Fetch and parse one NWS Daily Climate Report")
    climate.add_argument("--station", required=True)
    climate.add_argument("--date", required=True)
    replay = sub.add_parser("build-replay", help="Build no-lookahead replay snapshots")
    replay.add_argument("--start")
    replay.add_argument("--end")
    replay.add_argument("--market-ticker")
    live_replay = sub.add_parser("build-live-orderbook-replay", help="Build replay snapshots from locally recorded full orderbooks")
    live_replay.add_argument("--start")
    live_replay.add_argument("--end")
    live_replay.add_argument("--market-ticker")
    sub.add_parser("audit-recorded-data", help="Audit locally recorded full orderbook data")
    recorded_replay = sub.add_parser("build-recorded-replay", help="Build no-lookahead replay rows from recorded full orderbooks")
    recorded_replay.add_argument("--start")
    recorded_replay.add_argument("--end")
    recorded_replay.add_argument("--last-days", type=int)
    recorded_replay.add_argument("--market-ticker")
    recorded_replay.add_argument("--min-settlement-confidence", type=float, default=0.85)
    recorded_replay.add_argument("--allow-unsettled", action="store_true")
    recorded_replay.add_argument("--store-depth-json", action="store_true", help="Duplicate full depth JSON into replay rows. Off by default to avoid bloating SQLite.")
    recorded_replay.add_argument("--max-markets", type=int, default=None, help="Limit parsed weather markets processed. Useful for quick smoke tests.")
    recorded_replay.add_argument("--recorded-weather-only", action="store_true", help="Do not fetch historical NWS weather; use only live-recorded weather snapshots and mark gaps.")
    recorded_backtest = sub.add_parser("backtest-recorded", help="Backtest using recorded full-orderbook replay rows")
    recorded_backtest.add_argument("--strategy")
    recorded_backtest.add_argument("--all", action="store_true")
    recorded_backtest.add_argument("--start")
    recorded_backtest.add_argument("--end")
    recorded_backtest.add_argument("--last-days", type=int)
    recorded_backtest.add_argument("--mode", choices=["default", "taker", "signal_only", "conservative_passive", "full_orderbook_passive_approx"], default="default")
    recorded_backtest.add_argument("--label-quality", choices=["primary", "exploratory", "all"], default="primary")
    sweep = sub.add_parser("sweep-recorded", help="Run recorded-orderbook strategy parameter sweeps")
    sweep.add_argument("--start")
    sweep.add_argument("--end")
    sweep.add_argument("--last-days", type=int)
    sweep.add_argument("--label-quality", choices=["primary", "exploratory", "all"], default="primary")
    report = sub.add_parser("edge-report", help="Generate markdown edge report from recorded data")
    report.add_argument("--start")
    report.add_argument("--end")
    report.add_argument("--last-days", type=int, default=3)
    recorder = sub.add_parser("record-orderbooks", help="Record current full Kalshi orderbooks; places no orders")
    recorder.add_argument("--market-ticker")
    recorder.add_argument("--weather-only", action="store_true", help="Restrict recording to active weather markets. This remains the default unless --all-markets or --market-ticker is used.")
    recorder.add_argument("--all-markets", action="store_true", help="Record open markets across all Kalshi categories for market-making research.")
    recorder.add_argument("--interval-seconds", type=int, default=None)
    recorder.add_argument("--duration-minutes", type=int, default=None)
    recorder.add_argument("--duration-hours", type=float, default=None)
    recorder.add_argument("--max-markets", type=int, default=None)
    recorder.add_argument("--max-market-pages", type=int, default=None, help="How many /markets pages to scan for --all-markets. Omit to scan enough for --max-markets, or all pages when --max-markets exceeds one page.")
    recorder.add_argument("--top-depth-only", action="store_true")
    recorder.add_argument("--verbose-orderbooks", action="store_true")
    recorder.add_argument("--no-trades", action="store_true", help="Skip trade-print polling; useful if prioritizing orderbook breadth under rate limits.")
    recorder.add_argument("--no-batch-orderbooks", action="store_true", help="Disable the multi-orderbook endpoint and fall back to one request per market.")
    recorder.add_argument("--max-trade-pages", type=int, default=1, help="Maximum global /markets/trades pages per all-market recorder cycle.")
    recorder.add_argument("--from-universe", choices=["high", "medium", "recordable", "all"], help="Record tickers from the latest ranked market universe instead of raw /markets pages.")
    recorder.add_argument("--persist-weather-markets", action="store_true", help="When recording --weather-only, persist discovered weather markets/parsed contracts so recorded orderbooks remain replay-eligible.")
    recorder.add_argument("--once", action="store_true", help="Record one cycle and exit. Mainly useful for smoke tests.")
    station_resolve = sub.add_parser("resolve-active-weather-stations", help="Map active Kalshi weather markets to likely settlement stations")
    station_resolve.add_argument("--max-markets", type=int, default=None)
    station_resolve.add_argument("--all-markets", action="store_true")
    obs_recorder = sub.add_parser("record-weather-observations", help="Record live station observations in a separate process")
    obs_recorder.add_argument("--weather-only", action="store_true")
    obs_recorder.add_argument("--from-active-markets", action="store_true")
    obs_recorder.add_argument("--stations")
    obs_recorder.add_argument("--interval-minutes", type=int, default=5)
    obs_recorder.add_argument("--duration-hours", type=float, default=None)
    obs_recorder.add_argument("--max-markets", type=int, default=None)
    obs_recorder.add_argument("--once", action="store_true")
    fcst_recorder = sub.add_parser("record-weather-forecasts", help="Record live forecast snapshots in a separate process")
    fcst_recorder.add_argument("--weather-only", action="store_true")
    fcst_recorder.add_argument("--from-active-markets", action="store_true")
    fcst_recorder.add_argument("--stations")
    fcst_recorder.add_argument("--interval-minutes", type=int, default=30)
    fcst_recorder.add_argument("--duration-hours", type=float, default=None)
    fcst_recorder.add_argument("--max-markets", type=int, default=None)
    fcst_recorder.add_argument("--once", action="store_true")
    collector = sub.add_parser("collect-live", help="Run the no-trading live data collector for hours/days")
    collector.add_argument("--duration-hours", type=float, default=settings.collector_default_duration_hours, help="How long to run. Use 0 for indefinite.")
    collector.add_argument("--interval-seconds", type=int, default=None, help="Orderbook recording interval.")
    collector.add_argument("--max-markets", type=int, default=None)
    collector.add_argument("--scan-interval-minutes", type=int, default=settings.collector_scan_interval_minutes)
    collector.add_argument("--maintenance-interval-minutes", type=int, default=settings.collector_maintenance_interval_minutes)
    collector.add_argument("--settlement-lookback-days", type=int, default=settings.collector_settlement_lookback_days)
    collector.add_argument("--all-markets", action="store_true", help="Record all active markets instead of weather-filtered markets.")
    collector.add_argument("--max-market-pages", type=int, default=None, help="How many /markets pages to scan for --all-markets.")
    collector.add_argument("--no-trades", action="store_true", help="Skip trade-print polling in the orderbook recorder.")
    collector.add_argument("--no-batch-orderbooks", action="store_true", help="Disable the multi-orderbook endpoint in the orderbook recorder.")
    collector.add_argument("--max-trade-pages", type=int, default=1, help="Maximum global /markets/trades pages per all-market recorder cycle.")
    sub.add_parser("project-status", help="Print durable handoff/project status")
    sub.add_parser("source-smoke", help="Safe read-only smoke report for live sources, DB tables, and readiness wiring")
    reparse = sub.add_parser("reparse-contracts", help="Reparse stored markets with current parser semantics")
    reparse.add_argument("--weather-only", action="store_true")
    reparse.add_argument("--parser-version", default=None)
    rebuild_labels = sub.add_parser("rebuild-settlement-labels", help="Rebuild settlement labels with current settlement semantics")
    rebuild_labels.add_argument("--start")
    rebuild_labels.add_argument("--end")
    rebuild_labels.add_argument("--market-ticker")
    rebuild_labels.add_argument("--weather-only", action="store_true")
    rebuild_labels.add_argument("--settlement-version", default=None)
    validate_labels = sub.add_parser("validate-settlement-labels", help="Validate settlement labels and semantic fields")
    validate_labels.add_argument("--weather-only", action="store_true")
    validate_sources = sub.add_parser("validate-settlement-sources", help="Validate/fix settlement source metadata")
    validate_sources.add_argument("--no-fix", action="store_true")
    stale = sub.add_parser("mark-stale-runs", help="Mark old parser/settlement runs stale")
    stale.add_argument("--before-parser-version", default=None)
    clean = sub.add_parser("rebuild-clean-edge-analysis", help="Run the v2 semantic cleanup pipeline")
    clean.add_argument("--last-days", type=int, default=3)
    clean.add_argument("--dry-run", action="store_true")
    skip_diag = sub.add_parser("diagnose-settlement-skips", help="Group skipped settlements by root cause")
    skip_diag.add_argument("--last-days", type=int, default=7)
    city_diag = sub.add_parser("debug-city-settlements", help="Debug settlement labels for a city/prefix")
    city_diag.add_argument("--city", required=True)
    city_diag.add_argument("--last-days", type=int, default=10)
    depth_diag = sub.add_parser("validate-orderbook-depths", help="Inspect extreme recorded orderbook depths")
    depth_diag.add_argument("--last-days", type=int, default=3)
    health = sub.add_parser("collector-health", help="Summarize live collector health from DB")
    health.add_argument("--last-hours", type=int, default=24)
    weather_health = sub.add_parser("weather-recorder-health", help="Summarize live weather/forecast recorder health from DB")
    weather_health.add_argument("--last-hours", type=int, default=24)
    readiness = sub.add_parser("trading-readiness", help="Score whether research is ready for paper/live trading")
    readiness.add_argument("--last-days", type=int, default=7)
    liquidity = sub.add_parser("analyze-liquidity", help="Analyze spread persistence, passive fill evidence, and adverse selection")
    liquidity.add_argument("--last-days", type=int, default=7)
    market_making = sub.add_parser("analyze-market-making", help="Analyze passive market-making fill evidence from live books and trades")
    market_making.add_argument("--last-days", type=int, default=7)
    market_making.add_argument("--min-spread-cents", type=float, default=float(settings.passive_min_spread_cents))
    market_making.add_argument("--fill-horizon-minutes", type=int, default=30)
    market_making.add_argument("--quote-spacing-seconds", type=int, default=300)
    market_making.add_argument("--weather-only", action="store_true", help="Restrict analysis to tickers with parsed weather contracts.")
    market_making.add_argument("--max-markets", type=int, default=None, help="Optional diagnostic cap on distinct markets loaded for analysis; defaults to full window.")
    market_making.add_argument("--max-snapshots", type=int, default=None, help="Optional diagnostic cap on latest two-sided orderbook rows loaded for analysis; defaults to full window.")
    market_making.add_argument("--profile-runtime", action="store_true", help="Include per-stage runtime timings in the exported summary.")
    market_making.add_argument("--no-export", action="store_true")
    mm_snapshot = sub.add_parser("build-market-making-snapshot", help="Build venue-agnostic market-making snapshot from local read-only research data")
    mm_snapshot.add_argument("--venue", choices=["kalshi"], required=True)
    mm_snapshot.add_argument("--start")
    mm_snapshot.add_argument("--end")
    mm_snapshot.add_argument("--last-days", type=int, default=7)
    mm_snapshot.add_argument("--max-output-markets", type=int, default=1000, help="Maximum detailed market rows to serialize; summary counts still cover the full window.")
    mm_snapshot.add_argument("--no-export", action="store_true")
    market_making_replay = sub.add_parser("backtest-market-making", help="Replay the paper market-maker loop over recorded books/trades; never sends orders")
    market_making_replay.add_argument("--start")
    market_making_replay.add_argument("--end")
    market_making_replay.add_argument("--last-days", type=int, default=1)
    market_making_replay.add_argument("--market-ticker")
    market_making_replay.add_argument("--side", choices=["BUY_YES", "BUY_NO"])
    market_making_replay.add_argument("--max-markets", type=int, default=None)
    market_making_replay.add_argument("--quantity", type=float, default=1.0)
    market_making_replay.add_argument("--max-position", type=float, default=5.0)
    market_making_replay.add_argument("--max-open-quotes", type=int, default=1)
    market_making_replay.add_argument("--improve-cents", type=float, default=1.0)
    market_making_replay.add_argument("--min-spread-cents", type=float, default=float(settings.passive_min_spread_cents))
    market_making_replay.add_argument("--min-depth", type=float, default=float(settings.passive_min_displayed_depth))
    market_making_replay.add_argument("--quote-ttl-seconds", type=int, default=300)
    market_making_replay.add_argument("--quote-spacing-seconds", type=int, default=300)
    market_making_replay.add_argument("--stale-current-seconds", type=int, default=180, help="Maximum latest-book age for current paper target recommendations.")
    market_making_replay.add_argument("--require-current-setup", action="store_true", help="Only print/export candidates whose latest book currently qualifies for the paper-maker filters.")
    market_making_replay.add_argument("--weather-only", action="store_true", help="Restrict replay screening to tickers with parsed weather contracts.")
    market_making_replay.add_argument("--max-quotes-per-market-side", type=int, default=500)
    market_making_replay.add_argument("--no-export", action="store_true")
    universe = sub.add_parser("rank-market-universe", help="Discover/probe open Kalshi markets and rank recorder usefulness")
    universe.add_argument("--max-pages", type=int, default=5, help="Number of /markets pages to scan. Use --all-pages to page until Kalshi stops.")
    universe.add_argument("--all-pages", action="store_true")
    universe.add_argument("--max-markets", type=int, default=None)
    universe.add_argument("--probe-limit", type=int, default=1000, help="Maximum discovered markets to probe with batch orderbook calls, ranked by metadata/recent activity.")
    universe.add_argument("--recent-hours", type=int, default=24)
    universe.add_argument("--min-spread-cents", type=float, default=float(settings.passive_min_spread_cents))
    universe.add_argument("--min-depth", type=float, default=float(settings.passive_min_displayed_depth))
    universe.add_argument("--include-multivariate", action="store_true", help="Allow KXMVE multivariate/combinatoric markets in probe and recorder rankings. Default excludes them from priority.")
    universe.add_argument("--exclude-prefix", action="append", default=[], help="Additional ticker prefix to exclude from recorder priority. Can be repeated.")
    universe.add_argument("--no-probe-orderbooks", action="store_true")
    universe.add_argument("--skip-local-stats", action="store_true", help="Do not read local orderbook/trade tables. Useful while the recorder is holding SQLite write locks.")
    universe.add_argument("--persist-markets", action="store_true", help="Also upsert raw discovered market metadata. Off by default because universe ranking only needs ranking rows and exports.")
    universe.add_argument("--no-persist", action="store_true")
    universe.add_argument("--no-export", action="store_true")
    weather_mine = sub.add_parser("mine-weather-edge", help="Mine recorded weather replay rows for executable fair-value dislocations")
    weather_mine.add_argument("--start")
    weather_mine.add_argument("--end")
    weather_mine.add_argument("--last-days", type=int, default=3)
    weather_mine.add_argument("--market-ticker", help="Restrict recorded replay mining to one market ticker.")
    weather_mine.add_argument("--target", choices=["range-bucket-buy-no"], help="Preset focused research slice. Current preset isolates range-bucket BUY_NO signals.")
    weather_mine.add_argument("--contract-type", choices=["threshold_above", "threshold_below", "range_bucket"])
    weather_mine.add_argument("--action", choices=["BUY_YES", "BUY_NO"])
    weather_mine.add_argument("--city")
    weather_mine.add_argument("--hypothesis", choices=["weather_locked", "forecast_fair_value", "asof_weather_fair_value"])
    weather_mine.add_argument("--min-entry-cents", type=float)
    weather_mine.add_argument("--max-entry-cents", type=float)
    weather_mine.add_argument("--min-local-hour", type=float)
    weather_mine.add_argument("--max-local-hour", type=float)
    weather_mine.add_argument("--min-edge-after-buffers-cents", type=float, default=5.0)
    weather_mine.add_argument("--min-data-quality", type=float, default=0.55)
    weather_mine.add_argument("--min-fair-confidence", type=float, default=0.55)
    weather_mine.add_argument("--min-settlement-confidence", type=float, default=0.65)
    weather_mine.add_argument("--max-observation-age-minutes", type=float, default=90.0)
    weather_mine.add_argument("--max-forecast-age-minutes", type=float, default=360.0)
    weather_mine.add_argument("--max-signals-per-market", type=int, default=2)
    weather_mine.add_argument("--signal-spacing-minutes", type=int, default=60)
    weather_mine.add_argument("--no-rule-search", action="store_true")
    weather_mine.add_argument("--no-export", action="store_true")
    rank = sub.add_parser("rank-opportunities", help="Rank current active markets by executable fair-value edge")
    rank.add_argument("--weather-only", action="store_true")
    rank.add_argument("--max-markets", type=int, default=100)
    validate_signals = sub.add_parser("validate-signals", help="Validate signals/trades against future recorded prices")
    validate_signals.add_argument("--last-days", type=int, default=7)
    daily = sub.add_parser("daily-trading-research-update", help="Run daily research update and save markdown report")
    daily.add_argument("--last-days", type=int, default=7)
    sub.add_parser("dashboard", help="Start Streamlit dashboard")
    paper = sub.add_parser("paper-trade", help="Run paper-only opportunity simulation; never sends real orders")
    paper.add_argument("--strategy", default="rank_opportunities")
    paper.add_argument("--weather-only", action="store_true")
    paper.add_argument("--mode", choices=["taker_paper", "passive_paper_conservative"], default="taker_paper")
    paper.add_argument("--max-markets", type=int, default=100)
    paper_mm = sub.add_parser("paper-market-making", help="Run a paper-only passive market-making tracker for one market/side; never sends real orders")
    paper_mm.add_argument("--market-ticker", required=True)
    paper_mm.add_argument("--side", choices=["BUY_YES", "BUY_NO"], required=True)
    paper_mm.add_argument("--quantity", type=float, default=1.0)
    paper_mm.add_argument("--max-position", type=float, default=5.0)
    paper_mm.add_argument("--max-open-quotes", type=int, default=1)
    paper_mm.add_argument("--improve-cents", type=float, default=1.0)
    paper_mm.add_argument("--min-spread-cents", type=float, default=float(settings.passive_min_spread_cents))
    paper_mm.add_argument("--min-depth", type=float, default=float(settings.passive_min_displayed_depth))
    paper_mm.add_argument("--quote-ttl-seconds", type=int, default=300)
    paper_mm.add_argument("--stale-orderbook-seconds", type=int, default=180)
    paper_mm.add_argument("--interval-seconds", type=int, default=30)
    paper_mm.add_argument("--duration-minutes", type=float, default=None)
    paper_mm.add_argument("--once", action="store_true")
    paper_mm.add_argument("--dry-run", action="store_true")
    paper_mm.add_argument("--no-export", action="store_true")
    paper_mm_basket = sub.add_parser("paper-market-making-basket", help="Run several paper-only market-making trackers across current candidates; never sends real orders")
    paper_mm_basket.add_argument("--last-days", type=int, default=1)
    paper_mm_basket.add_argument("--search-max-markets", type=int, default=100)
    paper_mm_basket.add_argument("--max-targets", type=int, default=5)
    paper_mm_basket.add_argument("--min-replay-fills", type=int, default=1)
    paper_mm_basket.add_argument("--min-recent-trades", type=int, default=0)
    paper_mm_basket.add_argument("--strict-only", action="store_true", help="Only include replay-supported current targets; default also includes exploratory current targets.")
    paper_mm_basket.add_argument("--refresh-candidates-minutes", type=float, default=15.0)
    paper_mm_basket.add_argument("--quantity", type=float, default=1.0)
    paper_mm_basket.add_argument("--max-position", type=float, default=5.0)
    paper_mm_basket.add_argument("--max-open-quotes", type=int, default=1)
    paper_mm_basket.add_argument("--improve-cents", type=float, default=1.0)
    paper_mm_basket.add_argument("--min-spread-cents", type=float, default=float(settings.passive_min_spread_cents))
    paper_mm_basket.add_argument("--min-depth", type=float, default=float(settings.passive_min_displayed_depth))
    paper_mm_basket.add_argument("--quote-ttl-seconds", type=int, default=300)
    paper_mm_basket.add_argument("--quote-spacing-seconds", type=int, default=300)
    paper_mm_basket.add_argument("--stale-orderbook-seconds", type=int, default=180)
    paper_mm_basket.add_argument("--interval-seconds", type=int, default=30)
    paper_mm_basket.add_argument("--duration-minutes", type=float, default=None)
    paper_mm_basket.add_argument("--once", action="store_true")
    paper_mm_basket.add_argument("--dry-run", action="store_true")
    paper_mm_basket.add_argument("--weather-only", action="store_true", help="Restrict basket target selection to tickers with parsed weather contracts.")
    paper_mm_basket.add_argument("--no-export", action="store_true")
    paper_mm_evidence = sub.add_parser("paper-market-making-evidence", help="Read-only cumulative paper market-making evidence report; never sends real orders")
    paper_mm_evidence.add_argument("--stale-open-seconds", type=int, default=600)
    paper_mm_evidence.add_argument("--too-few-fills-threshold", type=int, default=5)
    paper_mm_evidence.add_argument("--adverse-high-threshold", type=float, default=0.35)
    paper_mm_evidence.add_argument("--last-days", type=int, default=None, help="Only include paper quotes with quote_time in the last N days. Default is full history.")
    paper_mm_evidence.add_argument("--since", help="Only include paper quotes at or after this ISO timestamp. Overrides --last-days.")
    paper_mm_evidence.add_argument("--timestamped-export", action="store_true", help="Write timestamp-suffixed report files instead of overwriting default evidence report paths.")
    paper_mm_evidence.add_argument("--no-export", action="store_true")
    paper_mm_target_review = sub.add_parser("paper-market-making-target-review", help="Read-only reconciliation of analyzer candidates and paper quote evidence; never sends real orders")
    paper_mm_target_review.add_argument("--last-days", type=int, default=7)
    paper_mm_target_review.add_argument("--too-few-fills-threshold", type=int, default=5)
    paper_mm_target_review.add_argument("--adverse-high-threshold", type=float, default=0.35)
    paper_mm_target_review.add_argument("--adverse-caution-threshold", type=float, default=0.20)
    paper_mm_target_review.add_argument("--weather-only", action="store_true", help="Restrict target review to tickers with parsed weather contracts.")
    paper_mm_target_review.add_argument("--no-export", action="store_true")
    weather_coverage = sub.add_parser("weather-replay-coverage", help="Read-only report of parsed weather ticker overlap with recorded orderbooks")
    weather_coverage.add_argument("--last-days", type=int, default=7)
    weather_coverage.add_argument("--no-export", action="store_true")
    daily_weather = sub.add_parser("daily-weather-evidence", help="Research-only daily weather replay evidence report")
    daily_weather.add_argument("--date", required=True)
    daily_weather.add_argument("--max-markets", type=int, default=25)
    daily_weather.add_argument("--min-settlement-confidence", type=float, default=0.85)
    daily_weather.add_argument("--min-edge-after-buffers-cents", type=float, default=5.0)
    daily_weather.add_argument("--trading-readiness-last-days", type=int, default=7)
    daily_weather.add_argument("--force-rebuild-replay", action="store_true", help="Rebuild recorded replay even when rows already exist for the date.")
    daily_weather.add_argument("--no-export", action="store_true")
    daily_weather_range = sub.add_parser("daily-weather-evidence-range", help="Research-only multi-day weather replay evidence summary")
    daily_weather_range.add_argument("--start", required=True)
    daily_weather_range.add_argument("--end", required=True)
    daily_weather_range.add_argument("--max-markets", type=int, default=25)
    daily_weather_range.add_argument("--min-settlement-confidence", type=float, default=0.85)
    daily_weather_range.add_argument("--min-edge-after-buffers-cents", type=float, default=5.0)
    daily_weather_range.add_argument("--trading-readiness-last-days", type=int, default=7)
    daily_weather_range.add_argument("--force-rebuild-replay", action="store_true", help="Rebuild recorded replay for every date even when rows already exist.")
    daily_weather_range.add_argument("--no-export", action="store_true")
    daily_weather_drilldown = sub.add_parser("daily-weather-evidence-drilldown", help="Research-only drilldown for one daily weather evidence result")
    daily_weather_drilldown.add_argument("--date", required=True)
    daily_weather_drilldown.add_argument("--max-markets", type=int, default=25)
    daily_weather_drilldown.add_argument("--min-settlement-confidence", type=float, default=0.85)
    daily_weather_drilldown.add_argument("--min-edge-after-buffers-cents", type=float, default=5.0)
    daily_weather_drilldown.add_argument("--trading-readiness-last-days", type=int, default=7)
    daily_weather_drilldown.add_argument("--no-export", action="store_true")
    refresh_daily_weather = sub.add_parser("refresh-daily-weather-evidence", help="Research-only refresh of recent daily weather evidence")
    refresh_daily_weather.add_argument("--start", required=True)
    refresh_daily_weather.add_argument("--end", required=True)
    refresh_daily_weather.add_argument("--max-markets", type=int, default=25)
    refresh_daily_weather.add_argument("--min-settlement-confidence", type=float, default=0.85)
    refresh_daily_weather.add_argument("--min-edge-after-buffers-cents", type=float, default=5.0)
    refresh_daily_weather.add_argument("--trading-readiness-last-days", type=int, default=7)
    refresh_daily_weather.add_argument("--force-rebuild-replay", action="store_true", help="Rebuild recorded replay for dates even when rows already exist.")
    refresh_daily_weather.add_argument("--no-export", action="store_true")
    paper_mm_drilldown = sub.add_parser("paper-market-making-drilldown", help="Read-only paper quote/fill drilldown for one market/side")
    paper_mm_drilldown.add_argument("--ticker", required=True)
    paper_mm_drilldown.add_argument("--side", choices=["BUY_YES", "BUY_NO"], required=True)
    paper_mm_drilldown.add_argument("--stale-open-seconds", type=int, default=600)

    args = parser.parse_args(argv)
    if args.command == "init-db":
        Storage().init_db()
        print("Initialized SQLite schema.")
        return 0
    if args.command == "load-markets":
        loader = KalshiMarketLoader()
        if args.all_markets:
            markets = loader.load_active_markets(
                max_pages=args.max_pages,
                max_markets=args.max_markets,
                persist_snapshots=args.persist_snapshots,
            )
            print(f"Loaded {len(markets)} active Kalshi markets.")
        else:
            markets = loader.load_active_weather_markets(max_pages=args.max_pages, max_series=args.max_series)
            print(f"Loaded {len(markets)} likely weather markets.")
        return 0
    if args.command == "scan-live":
        rows = LiveScanner().scan_once(max_markets=args.max_markets)
        for row in rows:
            print(f"{row.get('action')} {row.get('market_ticker')}: {row.get('reason')}")
        return 0
    if args.command == "load-history":
        result = KalshiHistoricalLoader().load_history(
            start=_date_arg(args.start),
            end=_date_arg(args.end),
            limit=args.limit,
            market_ticker=args.market_ticker,
            weather_only=args.weather_only or not args.market_ticker,
            period_interval=args.period,
            include_trades=not args.no_trades,
        )
        print(result.to_dict())
        return 0
    if args.command == "build-settlements":
        result = WeatherSettlementLoader().build_settlements(start=_date_arg(args.start), end=_date_arg(args.end), market_ticker=args.market_ticker)
        print(result.to_dict())
        return 0
    if args.command == "build-exact-settlements":
        result = WeatherSettlementLoader().build_settlements(start=_date_arg(args.start), end=_date_arg(args.end), market_ticker=args.market_ticker)
        print(result.to_dict())
        return 0
    if args.command == "fetch-nws-climate-report":
        result = NWSClimateReportClient().fetch_report(args.station, _date_arg(args.date), persist=True)
        print(result.to_storage_row())
        return 0
    if args.command == "build-replay":
        result = ReplayBuilder().build(start=_date_arg(args.start), end=_date_arg(args.end), market_ticker=args.market_ticker)
        print(result.to_dict())
        return 0
    if args.command == "build-live-orderbook-replay":
        result = ReplayBuilder().build_from_live_orderbooks(start=_date_arg(args.start), end=_date_arg(args.end), market_ticker=args.market_ticker)
        print(result.to_dict())
        return 0
    if args.command == "audit-recorded-data":
        result = RecordedDataAuditor().audit(persist=True)
        print(result.to_text())
        return 0
    if args.command == "build-recorded-replay":
        result = RecordedOrderbookReplayBuilder().build(
            start=_date_arg(args.start),
            end=_date_arg(args.end),
            market_ticker=args.market_ticker,
            last_days=args.last_days,
            min_settlement_confidence=args.min_settlement_confidence,
            allow_unsettled=args.allow_unsettled,
            store_depth_json=args.store_depth_json,
            max_markets=args.max_markets,
            historical_weather_fallback=not args.recorded_weather_only,
        )
        print(result.to_dict())
        return 0
    if args.command == "backtest-recorded":
        result = RecordedOrderbookBacktester().run(
            "all" if args.all else (args.strategy or "already_hit"),
            start=_date_arg(args.start),
            end=_date_arg(args.end),
            last_days=args.last_days,
            mode=args.mode,
            label_quality=args.label_quality,
        )
        print_recorded_result(result)
        return 0
    if args.command == "sweep-recorded":
        result = RecordedOrderbookBacktester().sweep(
            start=_date_arg(args.start),
            end=_date_arg(args.end),
            last_days=args.last_days,
            label_quality=args.label_quality,
        )
        print_sweep_result(result)
        return 0
    if args.command == "edge-report":
        result = EdgeReportGenerator().generate(
            start=_date_arg(args.start),
            end=_date_arg(args.end),
            last_days=args.last_days,
        )
        print(result.to_dict())
        return 0
    if args.command == "record-orderbooks":
        weather_only = False if args.all_markets or args.from_universe else (args.weather_only or not args.market_ticker)
        result = LiveOrderbookRecorder().run(
            market_ticker=args.market_ticker,
            weather_only=weather_only,
            interval_seconds=args.interval_seconds,
            duration_minutes=args.duration_minutes,
            duration_hours=args.duration_hours,
            max_markets=args.max_markets,
            max_market_pages=args.max_market_pages,
            full_depth=not args.top_depth_only,
            verbose_orderbooks=args.verbose_orderbooks,
            record_trades=not args.no_trades,
            batch_orderbooks=not args.no_batch_orderbooks,
            max_global_trade_pages=args.max_trade_pages,
            universe_priority=args.from_universe,
            persist_weather_markets=args.persist_weather_markets,
            once=args.once,
        )
        print(result.to_dict())
        return 0
    if args.command == "resolve-active-weather-stations":
        result = ActiveWeatherStationResolver().resolve_active(weather_only=not args.all_markets, max_markets=args.max_markets, persist=True)
        print(result.to_text())
        return 0
    if args.command == "record-weather-observations":
        result = WeatherObservationRecorder().run(
            stations=_station_list_arg(args.stations),
            from_active_markets=args.from_active_markets or args.weather_only or not args.stations,
            weather_only=args.weather_only or not args.stations,
            interval_minutes=args.interval_minutes,
            duration_hours=args.duration_hours,
            max_markets=args.max_markets,
            once=args.once,
        )
        print(result.to_dict())
        return 0
    if args.command == "record-weather-forecasts":
        result = WeatherForecastRecorder().run(
            stations=_station_list_arg(args.stations),
            from_active_markets=args.from_active_markets or args.weather_only or not args.stations,
            weather_only=args.weather_only or not args.stations,
            interval_minutes=args.interval_minutes,
            duration_hours=args.duration_hours,
            max_markets=args.max_markets,
            once=args.once,
        )
        print(result.to_dict())
        return 0
    if args.command == "collect-live":
        result = LiveDataCollector().run(
            duration_hours=args.duration_hours,
            interval_seconds=args.interval_seconds,
            max_markets=args.max_markets,
            scan_interval_minutes=args.scan_interval_minutes,
            maintenance_interval_minutes=args.maintenance_interval_minutes,
            settlement_lookback_days=args.settlement_lookback_days,
            weather_only=not args.all_markets,
            max_market_pages=args.max_market_pages,
            record_trades=not args.no_trades,
            batch_orderbooks=not args.no_batch_orderbooks,
            max_global_trade_pages=args.max_trade_pages,
        )
        print(result.to_dict())
        return 0
    if args.command == "paper-trade":
        result = PaperTrader().run_once(strategy=args.strategy, weather_only=args.weather_only or True, mode=args.mode, max_markets=args.max_markets)
        print(result)
        return 0
    if args.command == "paper-market-making":
        cfg = PaperMarketMakerConfig(
            market_ticker=args.market_ticker,
            side=args.side,
            quantity=args.quantity,
            max_position=args.max_position,
            max_open_quotes=args.max_open_quotes,
            improve_cents=args.improve_cents,
            min_spread_cents=args.min_spread_cents,
            min_depth=args.min_depth,
            quote_ttl_seconds=args.quote_ttl_seconds,
            stale_orderbook_seconds=args.stale_orderbook_seconds,
            interval_seconds=args.interval_seconds,
            duration_minutes=args.duration_minutes,
            dry_run=args.dry_run,
        )
        print(PaperMarketMaker().run(cfg, persist_exports=not args.no_export, once=args.once or args.duration_minutes is None).to_text())
        return 0
    if args.command == "paper-market-making-basket":
        cfg = PaperMarketMakingBasketConfig(
            last_days=args.last_days,
            search_max_markets=args.search_max_markets,
            max_targets=args.max_targets,
            min_replay_fills=args.min_replay_fills,
            min_recent_trades=args.min_recent_trades,
            include_exploratory=not args.strict_only,
            refresh_candidates_minutes=args.refresh_candidates_minutes,
            quantity=args.quantity,
            max_position=args.max_position,
            max_open_quotes=args.max_open_quotes,
            improve_cents=args.improve_cents,
            min_spread_cents=args.min_spread_cents,
            min_depth=args.min_depth,
            quote_ttl_seconds=args.quote_ttl_seconds,
            quote_spacing_seconds=args.quote_spacing_seconds,
            stale_orderbook_seconds=args.stale_orderbook_seconds,
            interval_seconds=args.interval_seconds,
            duration_minutes=args.duration_minutes,
            dry_run=args.dry_run,
            weather_only=args.weather_only,
        )
        print(PaperMarketMakingBasket().run(cfg, persist_exports=not args.no_export, once=args.once or args.duration_minutes is None).to_text())
        return 0
    if args.command == "paper-market-making-evidence":
        cfg = PaperMarketMakingEvidenceConfig(
            stale_open_seconds=args.stale_open_seconds,
            too_few_fills_threshold=args.too_few_fills_threshold,
            adverse_high_threshold=args.adverse_high_threshold,
            last_days=args.last_days,
            since=_datetime_arg(args.since),
            timestamped_export=args.timestamped_export,
        )
        print(PaperMarketMakingEvidenceReporter().build(cfg, persist_exports=not args.no_export).to_text())
        return 0
    if args.command == "paper-market-making-target-review":
        cfg = PaperMarketMakingTargetReviewConfig(
            last_days=args.last_days,
            too_few_fills_threshold=args.too_few_fills_threshold,
            adverse_high_threshold=args.adverse_high_threshold,
            adverse_caution_threshold=args.adverse_caution_threshold,
            weather_only=args.weather_only,
        )
        print(PaperMarketMakingTargetReviewer().build(cfg, persist_exports=not args.no_export).to_text())
        return 0
    if args.command == "weather-replay-coverage":
        cfg = WeatherReplayCoverageConfig(last_days=args.last_days)
        print(WeatherReplayCoverageReporter().build(cfg, persist_exports=not args.no_export).to_text())
        return 0
    if args.command == "daily-weather-evidence":
        cfg = DailyWeatherEvidenceConfig(
            day=_date_arg(args.date),
            max_markets=args.max_markets,
            min_settlement_confidence=args.min_settlement_confidence,
            min_edge_after_buffers_cents=args.min_edge_after_buffers_cents,
            trading_readiness_last_days=args.trading_readiness_last_days,
            force_rebuild_replay=args.force_rebuild_replay,
        )
        print(DailyWeatherEvidenceReporter().build(cfg, persist_exports=not args.no_export).to_text())
        return 0
    if args.command == "daily-weather-evidence-range":
        cfg = DailyWeatherEvidenceRangeConfig(
            start=_date_arg(args.start),
            end=_date_arg(args.end),
            max_markets=args.max_markets,
            min_settlement_confidence=args.min_settlement_confidence,
            min_edge_after_buffers_cents=args.min_edge_after_buffers_cents,
            trading_readiness_last_days=args.trading_readiness_last_days,
            force_rebuild_replay=args.force_rebuild_replay,
        )
        print(DailyWeatherEvidenceRangeReporter().build(cfg, persist_exports=not args.no_export).to_text())
        return 0
    if args.command == "daily-weather-evidence-drilldown":
        cfg = DailyWeatherEvidenceDrilldownConfig(
            day=_date_arg(args.date),
            max_markets=args.max_markets,
            min_settlement_confidence=args.min_settlement_confidence,
            min_edge_after_buffers_cents=args.min_edge_after_buffers_cents,
            trading_readiness_last_days=args.trading_readiness_last_days,
        )
        print(DailyWeatherEvidenceDrilldownReporter().build(cfg, persist_exports=not args.no_export).to_text())
        return 0
    if args.command == "refresh-daily-weather-evidence":
        cfg = DailyWeatherEvidenceRefreshConfig(
            start=_date_arg(args.start),
            end=_date_arg(args.end),
            max_markets=args.max_markets,
            min_settlement_confidence=args.min_settlement_confidence,
            min_edge_after_buffers_cents=args.min_edge_after_buffers_cents,
            trading_readiness_last_days=args.trading_readiness_last_days,
            force_rebuild_replay=args.force_rebuild_replay,
        )
        print(DailyWeatherEvidenceRefreshReporter().build(cfg, persist_exports=not args.no_export).to_text())
        return 0
    if args.command == "paper-market-making-drilldown":
        cfg = PaperMarketMakingDrilldownConfig(
            ticker=args.ticker,
            side=args.side,
            stale_open_seconds=args.stale_open_seconds,
        )
        print(PaperMarketMakingDrilldownReporter().build(cfg).to_text())
        return 0
    if args.command == "project-status":
        print(ProjectMaintenance().project_status().to_text())
        return 0
    if args.command == "source-smoke":
        print(SourceSmokeReporter().build().to_text())
        return 0
    if args.command == "reparse-contracts":
        print(ProjectMaintenance().reparse_contracts(weather_only=args.weather_only).to_text())
        return 0
    if args.command == "rebuild-settlement-labels":
        print(ProjectMaintenance().rebuild_settlement_labels(start=_date_arg(args.start), end=_date_arg(args.end), market_ticker=args.market_ticker).to_text())
        return 0
    if args.command == "validate-settlement-labels":
        print(ProjectMaintenance().validate_settlement_labels(weather_only=args.weather_only).to_text())
        return 0
    if args.command == "validate-settlement-sources":
        print(ProjectMaintenance().validate_settlement_sources(fix=not args.no_fix).to_text())
        return 0
    if args.command == "mark-stale-runs":
        print(ProjectMaintenance().mark_stale_runs().to_text())
        return 0
    if args.command == "rebuild-clean-edge-analysis":
        print(ProjectMaintenance().rebuild_clean_edge_analysis(last_days=args.last_days, dry_run=args.dry_run).to_text())
        return 0
    if args.command == "diagnose-settlement-skips":
        print(ProjectMaintenance().diagnose_settlement_skips(last_days=args.last_days).to_text())
        return 0
    if args.command == "debug-city-settlements":
        print(ProjectMaintenance().debug_city_settlements(city=args.city, last_days=args.last_days).to_text())
        return 0
    if args.command == "validate-orderbook-depths":
        print(ProjectMaintenance().validate_orderbook_depths(last_days=args.last_days).to_text())
        return 0
    if args.command == "collector-health":
        print(ProjectMaintenance().collector_health(last_hours=args.last_hours).to_text())
        return 0
    if args.command == "weather-recorder-health":
        print(ProjectMaintenance().weather_recorder_health(last_hours=args.last_hours).to_text())
        return 0
    if args.command == "trading-readiness":
        print(TradingReadiness().evaluate(last_days=args.last_days).to_text())
        return 0
    if args.command == "analyze-liquidity":
        print(LiquidityAnalyzer().analyze(last_days=args.last_days).to_text())
        return 0
    if args.command == "analyze-market-making":
        cfg = MarketMakingConfig(
            min_spread_cents=args.min_spread_cents,
            fill_horizon_minutes=args.fill_horizon_minutes,
            quote_spacing_seconds=args.quote_spacing_seconds,
            weather_only=args.weather_only,
            max_markets=args.max_markets,
            max_snapshots=args.max_snapshots,
            profile_runtime=args.profile_runtime,
        )
        print(MarketMakingAnalyzer(config=cfg).analyze(last_days=args.last_days, persist_exports=not args.no_export).to_text())
        return 0
    if args.command == "build-market-making-snapshot":
        cfg = MarketMakingSnapshotConfig(venue=args.venue, max_output_markets=args.max_output_markets)
        result = MarketMakingSnapshotBuilder(config=cfg).build(
            start=_date_arg(args.start),
            end=_date_arg(args.end),
            last_days=args.last_days,
            persist_exports=not args.no_export,
        )
        print(result.to_text())
        return 0
    if args.command == "backtest-market-making":
        cfg = MarketMakingReplayConfig(
            side=args.side,
            quantity=args.quantity,
            max_position=args.max_position,
            max_open_quotes=args.max_open_quotes,
            improve_cents=args.improve_cents,
            min_spread_cents=args.min_spread_cents,
            min_depth=args.min_depth,
            quote_ttl_seconds=args.quote_ttl_seconds,
            quote_spacing_seconds=args.quote_spacing_seconds,
            stale_current_seconds=args.stale_current_seconds,
            require_current_setup=args.require_current_setup,
            weather_only=args.weather_only,
            max_quotes_per_market_side=args.max_quotes_per_market_side,
        )
        result = MarketMakingReplayBacktester(config=cfg).replay(
            start=_date_arg(args.start),
            end=_date_arg(args.end),
            last_days=args.last_days,
            market_ticker=args.market_ticker,
            max_markets=args.max_markets,
            persist_exports=not args.no_export,
        )
        print(result.to_text())
        return 0
    if args.command == "rank-market-universe":
        excluded_prefixes = tuple(args.exclude_prefix or ())
        if not args.include_multivariate:
            excluded_prefixes = ("KXMVE",) + excluded_prefixes
        cfg = MarketUniverseConfig(
            min_spread_cents=args.min_spread_cents,
            min_displayed_depth=args.min_depth,
            recent_hours=args.recent_hours,
            excluded_ticker_prefixes=excluded_prefixes,
        )
        max_pages = None if args.all_pages else args.max_pages
        result = MarketUniverseBuilder(config=cfg).build(
            max_pages=max_pages,
            max_markets=args.max_markets,
            probe_limit=args.probe_limit,
            probe_orderbooks=not args.no_probe_orderbooks,
            use_local_stats=not args.skip_local_stats,
            persist_markets=args.persist_markets,
            persist=not args.no_persist,
            export=not args.no_export,
        )
        print(result.to_text())
        return 0
    if args.command == "mine-weather-edge":
        contract_type = args.contract_type
        action = args.action
        if args.target == "range-bucket-buy-no":
            contract_type = "range_bucket"
            action = "BUY_NO"
        cfg = WeatherEdgeMiningConfig(
            target=args.target,
            market_ticker=args.market_ticker,
            contract_type=contract_type,
            action=action,
            city=args.city,
            hypothesis=args.hypothesis,
            min_entry_price_cents=args.min_entry_cents,
            max_entry_price_cents=args.max_entry_cents,
            min_local_hour=args.min_local_hour,
            max_local_hour=args.max_local_hour,
            min_edge_after_buffers_cents=args.min_edge_after_buffers_cents,
            min_data_quality=args.min_data_quality,
            min_fair_confidence=args.min_fair_confidence,
            min_settlement_confidence=args.min_settlement_confidence,
            max_observation_age_minutes=args.max_observation_age_minutes,
            max_forecast_age_minutes=args.max_forecast_age_minutes,
            max_signals_per_market=args.max_signals_per_market,
            signal_spacing_minutes=args.signal_spacing_minutes,
            run_rule_search=not args.no_rule_search,
        )
        print(
            WeatherEdgeMiner(config=cfg)
            .mine(
                start=_date_arg(args.start),
                end=_date_arg(args.end),
                last_days=args.last_days,
                market_ticker=args.market_ticker,
                persist_exports=not args.no_export,
            )
            .to_text()
        )
        return 0
    if args.command == "rank-opportunities":
        print(OpportunityRanker().rank(weather_only=args.weather_only or True, max_markets=args.max_markets).to_text())
        return 0
    if args.command == "validate-signals":
        print(SignalValidator().validate(last_days=args.last_days).to_text())
        return 0
    if args.command == "daily-trading-research-update":
        print(ProjectMaintenance().daily_trading_research_update(last_days=args.last_days).to_text())
        return 0
    if args.command == "backtest":
        result = BacktestRunner().run(
            "all" if args.all else (args.strategy or "late_day_high_fade"),
            start=_date_arg(args.start),
            end=_date_arg(args.end),
            mode=args.mode,
            label_quality=args.label_quality,
        )
        if "runs" in result:
            for item in result["runs"]:
                print_backtest_result(item)
        else:
            print_backtest_result(result)
        return 0
    if args.command == "dashboard":
        return subprocess.call([sys.executable, "-m", "streamlit", "run", "dashboard/app.py"])
    return 1


def _date_arg(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _datetime_arg(value: str | None):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _station_list_arg(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def print_backtest_result(result: dict) -> None:
    if "message" in result:
        print(f"run_id={result.get('run_id')} strategy={result.get('strategy')} mode={result.get('mode')}")
        print(f"markets={result.get('markets')} snapshots={result.get('replay_snapshots')} signals={result.get('signals')} filled={result.get('filled_trades')}")
        print(f"excluded_low_confidence={result.get('markets_excluded_low_confidence')} label_sources={result.get('label_source_breakdown')}")
        print(f"replay_data_type={result.get('replay_data_type')} execution={result.get('execution_assumption')} label_quality={result.get('settlement_label_quality')}")
        print(f"gross_pnl={result.get('gross_pnl')} fees={result.get('fees')} net_pnl={result.get('net_pnl')} roi={result.get('roi')}")
        print(f"win_rate={result.get('win_rate')} max_drawdown={result.get('max_drawdown')} avg_edge={result.get('average_edge')}")
        print(result["message"])
        if result.get("limitations"):
            print("limitations:")
            for item in result["limitations"]:
                print(f"- {item}")
    else:
        print(result.get("summary") or result)


def print_recorded_result(result: dict) -> None:
    runs = result.get("runs")
    if runs:
        for item in runs:
            print_recorded_result(item)
        return
    summary = result.get("summary", result)
    print(f"strategy={summary.get('strategy')} mode={summary.get('mode')} label_quality={summary.get('label_quality')}")
    print(f"markets={summary.get('markets')} snapshots={summary.get('snapshots')} signals={summary.get('signals')} fills={summary.get('fills')}")
    print(f"gross_pnl={summary.get('gross_pnl')} fees={summary.get('fees')} net_pnl={summary.get('net_pnl')} roi={summary.get('roi')}")
    print(f"win_rate={summary.get('win_rate')} max_drawdown={summary.get('max_drawdown')} avg_edge={summary.get('average_edge_cents')}")
    print(f"execution={summary.get('execution_assumption')} data_quality={summary.get('data_quality_score')}")
    print(summary.get("message"))
    for item in summary.get("limitations", []):
        print(f"- {item}")
    for item in summary.get("warnings", [])[:10]:
        print(f"WARNING: {item}")


def print_sweep_result(result: dict) -> None:
    print(result.get("message"))
    print(f"strategy_variants_tested={result.get('strategy_variants_tested')} recommendation={result.get('recommendation')}")
    print("Top candidates:")
    for item in result.get("top_candidates", [])[:10]:
        print(
            f"- {item.get('strategy')} {item.get('params')} mode={item.get('mode')} "
            f"net_pnl={item.get('net_pnl'):.2f} fills={item.get('fills')} robustness={item.get('robustness_verdict')}"
        )
    print("Rejected strategies:")
    for item in result.get("rejected_strategies", [])[:10]:
        print(f"- {item.get('strategy')} {item.get('params')}: {item.get('reason')}")


if __name__ == "__main__":
    raise SystemExit(main())
