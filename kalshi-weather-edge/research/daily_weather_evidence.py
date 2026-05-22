from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from backtest.recorded_replay import RecordedOrderbookReplayBuilder
from config import PROJECT_ROOT
from data.storage import Storage
from research.market_making_analysis import MarketMakingAnalyzer, MarketMakingConfig
from research.trading_readiness import TradingReadiness
from research.weather_edge_miner import WeatherEdgeMiner, WeatherEdgeMiningConfig
from research.weather_replay_coverage import WeatherReplayCoverageConfig, WeatherReplayCoverageReporter


@dataclass(frozen=True)
class DailyWeatherEvidenceConfig:
    day: date
    max_markets: int = 25
    min_settlement_confidence: float = 0.85
    min_edge_after_buffers_cents: float = 5.0
    trading_readiness_last_days: int = 7
    force_rebuild_replay: bool = False


@dataclass(frozen=True)
class DailyWeatherEvidenceResult:
    summary: dict[str, Any]
    exports: dict[str, str] | None

    def to_text(self) -> str:
        miner = self.summary.get("miner") or {}
        replay = self.summary.get("recorded_replay") or {}
        coverage = self.summary.get("coverage") or {}
        market_making = self.summary.get("weather_market_making") or {}
        readiness = self.summary.get("trading_readiness") or {}
        lines = [
            f"daily_weather_evidence_status={self.summary.get('status')}",
            f"message={self.summary.get('message')}",
            f"date={self.summary.get('date')} research_only={str(self.summary.get('research_only')).lower()}",
            f"high_confidence_labels={coverage.get('high_confidence_settlement_label_tickers')} "
            f"overlap_tickers={coverage.get('overlap_tickers')} missing_labels={coverage.get('missing_settlement_label_tickers')}",
            f"replay_markets={replay.get('markets')} replay_snapshots={replay.get('snapshots')} "
            f"skipped_markets={replay.get('skipped_markets')}",
            f"miner_verdict={miner.get('verdict')} signals={miner.get('signals')} "
            f"settled_signals={miner.get('settled_signals')} net_pnl={_fmt(miner.get('net_pnl_cents'))} "
            f"stress={miner.get('stress_verdict')}",
            f"weather_market_making_verdict={market_making.get('market_making_verdict')} "
            f"trade_evidence_fills={market_making.get('trade_evidence_fills')} "
            f"paper_watchlist_candidates={market_making.get('paper_watchlist_candidates')}",
            f"trading_readiness={readiness.get('status')}",
            f"exports={self.exports}",
            f"disclaimer={self.summary.get('disclaimer')}",
        ]
        warnings = replay.get("warnings") or []
        if warnings:
            lines.append("Replay warnings:")
            for warning in warnings[:10]:
                lines.append(f"- {warning}")
        return "\n".join(lines)


@dataclass(frozen=True)
class DailyWeatherEvidenceRangeConfig:
    start: date
    end: date
    max_markets: int = 25
    min_settlement_confidence: float = 0.85
    min_edge_after_buffers_cents: float = 5.0
    trading_readiness_last_days: int = 7
    force_rebuild_replay: bool = False


@dataclass(frozen=True)
class DailyWeatherEvidenceRangeResult:
    summary: dict[str, Any]
    days: list[dict[str, Any]]
    exports: dict[str, str] | None

    def to_text(self) -> str:
        lines = [
            f"daily_weather_evidence_range_status={self.summary.get('status')}",
            f"message={self.summary.get('message')}",
            f"window={self.summary.get('start')}..{self.summary.get('end')} days_analyzed={self.summary.get('days_analyzed')}",
            f"days_with_replay_snapshots={self.summary.get('days_with_replay_snapshots')} "
            f"days_with_enough_labels={self.summary.get('days_with_enough_labels')} "
            f"days_with_positive_miner_net_pnl={self.summary.get('days_with_positive_miner_net_pnl')} "
            f"days_failing_stress={self.summary.get('days_failing_stress')} "
            f"days_review_or_no_edge={self.summary.get('days_review_or_no_edge')}",
            f"exports={self.exports}",
            "Daily rows:",
        ]
        for row in self.days:
            lines.append(
                f"- {row.get('date')} status={row.get('status')} "
                f"high_confidence_labels={row.get('high_confidence_labels')} "
                f"overlap_tickers={row.get('overlap_tickers')} "
                f"replay_snapshots={row.get('replay_snapshots')} "
                f"markets={row.get('markets')} "
                f"miner_signals={row.get('miner_signals')} "
                f"settled_signals={row.get('settled_signals')} "
                f"net_pnl={_fmt(row.get('net_pnl_cents'))} "
                f"stress={row.get('stress_verdict')} "
                f"weather_market_making={row.get('weather_market_making_verdict')} "
                f"trading_readiness={row.get('trading_readiness_status')}"
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class DailyWeatherEvidenceDrilldownConfig:
    day: date
    max_markets: int = 25
    min_settlement_confidence: float = 0.85
    min_edge_after_buffers_cents: float = 5.0
    trading_readiness_last_days: int = 7


@dataclass(frozen=True)
class DailyWeatherEvidenceDrilldownResult:
    summary: dict[str, Any]
    top_signals: list[dict[str, Any]]
    worst_signals: list[dict[str, Any]]
    signals_by_ticker: list[dict[str, Any]]
    signals_by_city_station: list[dict[str, Any]]
    exports: dict[str, str] | None

    def to_text(self) -> str:
        stress = self.summary.get("stress") or {}
        future = self.summary.get("future_mid_confirmation") or {}
        lines = [
            f"daily_weather_drilldown_status={self.summary.get('status')}",
            f"message={self.summary.get('message')}",
            f"date={self.summary.get('date')} research_only={str(self.summary.get('research_only')).lower()}",
            f"signals={self.summary.get('signals')} settled_signals={self.summary.get('settled_signals')} "
            f"net_pnl={_fmt(self.summary.get('net_pnl_cents'))} "
            f"depends_on_one_best_signal={str(self.summary.get('depends_on_one_best_signal')).lower()}",
            f"fee_stress={_fmt(stress.get('two_x_fees_net_pnl'))} "
            f"worse_fill_stress={_fmt(stress.get('worse_fill_1c_net_pnl'))} "
            f"exclude_best_signal={_fmt(stress.get('exclude_best_signal_net_pnl'))}",
            f"future_mid_30m_beat_rate={_fmt(future.get('future_mid_30m_beat_rate'))} "
            f"future_mid_60m_beat_rate={_fmt(future.get('future_mid_60m_beat_rate'))} "
            f"future_mid_final_beat_rate={_fmt(future.get('future_mid_final_beat_rate'))}",
            f"exports={self.exports}",
            "Warnings:",
        ]
        lines.extend(f"- {warning}" for warning in self.summary.get("warnings", []))
        lines.append("Top mined signals by PnL:")
        for row in self.top_signals[:5]:
            lines.append(_signal_line(row))
        lines.append("Worst mined signals by PnL:")
        for row in self.worst_signals[:5]:
            lines.append(_signal_line(row))
        return "\n".join(lines)


class DailyWeatherEvidenceDrilldownReporter:
    """Research-only one-day explanation of mined weather evidence drivers."""

    def __init__(self, daily_reporter: Any | None = None, miner: Any | None = None):
        self.daily_reporter = daily_reporter or DailyWeatherEvidenceReporter()
        self.miner = miner

    def build(
        self,
        config: DailyWeatherEvidenceDrilldownConfig,
        *,
        persist_exports: bool = True,
    ) -> DailyWeatherEvidenceDrilldownResult:
        daily = self.daily_reporter.build(
            DailyWeatherEvidenceConfig(
                day=config.day,
                max_markets=config.max_markets,
                min_settlement_confidence=config.min_settlement_confidence,
                min_edge_after_buffers_cents=config.min_edge_after_buffers_cents,
                trading_readiness_last_days=config.trading_readiness_last_days,
                force_rebuild_replay=False,
            ),
            persist_exports=False,
        )
        miner = self.miner or WeatherEdgeMiner(
            config=WeatherEdgeMiningConfig(
                target="range-bucket-buy-no",
                contract_type="range_bucket",
                action="BUY_NO",
                min_edge_after_buffers_cents=config.min_edge_after_buffers_cents,
            )
        )
        mining = miner.mine(
            start=config.day,
            end=config.day,
            last_days=None,
            persist_exports=False,
        )
        signals = list(getattr(mining, "signals", []) or [])
        miner_summary = dict(getattr(mining, "summary", {}) or {})
        top = [_signal_view(row) for row in _sorted_signals(signals, reverse=True)[:10]]
        worst = [_signal_view(row) for row in _sorted_signals(signals, reverse=False)[:10]]
        by_ticker = _aggregate_signals(signals, ["market_ticker"])
        by_city_station = _aggregate_signals(signals, ["city", "station_code"])
        summary = _drilldown_summary(config, daily.summary, miner_summary, signals, by_ticker, by_city_station)
        exports = _export_drilldown(summary, top, worst, by_ticker, by_city_station) if persist_exports else None
        return DailyWeatherEvidenceDrilldownResult(
            summary=summary,
            top_signals=top,
            worst_signals=worst,
            signals_by_ticker=by_ticker,
            signals_by_city_station=by_city_station,
            exports=exports,
        )


class DailyWeatherEvidenceRangeReporter:
    """Research-only multi-day summary built from daily weather evidence reports."""

    def __init__(self, daily_reporter: Any | None = None):
        self.daily_reporter = daily_reporter or DailyWeatherEvidenceReporter()

    def build(
        self,
        config: DailyWeatherEvidenceRangeConfig,
        *,
        persist_exports: bool = True,
    ) -> DailyWeatherEvidenceRangeResult:
        if config.end < config.start:
            raise ValueError("--end must be on or after --start")
        rows: list[dict[str, Any]] = []
        for day in _date_range(config.start, config.end):
            try:
                daily = self.daily_reporter.build(
                    DailyWeatherEvidenceConfig(
                        day=day,
                        max_markets=config.max_markets,
                        min_settlement_confidence=config.min_settlement_confidence,
                        min_edge_after_buffers_cents=config.min_edge_after_buffers_cents,
                        trading_readiness_last_days=config.trading_readiness_last_days,
                        force_rebuild_replay=config.force_rebuild_replay,
                    ),
                    persist_exports=False,
                )
                rows.append(_range_row(daily.summary))
            except Exception as exc:
                rows.append(_error_range_row(day, exc))
        summary = _range_summary(config, rows)
        exports = _export_range(summary, rows) if persist_exports else None
        return DailyWeatherEvidenceRangeResult(summary=summary, days=rows, exports=exports)


class DailyWeatherEvidenceReporter:
    """Research-only orchestration report for one weather replay day."""

    def __init__(
        self,
        *,
        coverage_reporter: Any | None = None,
        replay_builder: Any | None = None,
        miner: Any | None = None,
        market_making_analyzer: Any | None = None,
        trading_readiness: Any | None = None,
        storage: Storage | None = None,
    ):
        self.coverage_reporter = coverage_reporter
        self.replay_builder = replay_builder
        self.miner = miner
        self.market_making_analyzer = market_making_analyzer
        self.trading_readiness = trading_readiness
        self.storage = storage or Storage()

    def build(
        self,
        config: DailyWeatherEvidenceConfig,
        *,
        persist_exports: bool = True,
    ) -> DailyWeatherEvidenceResult:
        coverage_reporter = self.coverage_reporter or WeatherReplayCoverageReporter(today_fn=lambda: config.day)
        replay_builder = self.replay_builder or RecordedOrderbookReplayBuilder()
        miner = self.miner or WeatherEdgeMiner(
            config=WeatherEdgeMiningConfig(
                target="range-bucket-buy-no",
                contract_type="range_bucket",
                action="BUY_NO",
                min_edge_after_buffers_cents=config.min_edge_after_buffers_cents,
            )
        )
        market_making = self.market_making_analyzer or MarketMakingAnalyzer(config=MarketMakingConfig(weather_only=True))
        readiness = self.trading_readiness or TradingReadiness()

        coverage = coverage_reporter.build(
            WeatherReplayCoverageConfig(
                last_days=1,
                min_settlement_confidence=config.min_settlement_confidence,
            ),
            persist_exports=False,
        )
        replay = self._replay_result(config, replay_builder)
        mining = miner.mine(
            start=config.day,
            end=config.day,
            last_days=None,
            persist_exports=False,
        )
        mm = market_making.analyze(
            start=config.day,
            end=config.day,
            last_days=None,
            persist_exports=False,
        )
        readiness_result = readiness.evaluate(last_days=config.trading_readiness_last_days)

        summary = _summary(config, coverage, replay, mining, mm, readiness_result)
        exports = _export(summary) if persist_exports else None
        return DailyWeatherEvidenceResult(summary=summary, exports=exports)

    def _replay_result(self, config: DailyWeatherEvidenceConfig, replay_builder: Any) -> Any:
        if not config.force_rebuild_replay and self.replay_builder is None:
            existing = _existing_replay_summary(self.storage, config.day)
            if int(existing.get("snapshots") or 0) > 0:
                return _ExistingReplayResult(
                    markets=int(existing.get("markets") or 0),
                    snapshots=int(existing.get("snapshots") or 0),
                    skipped_markets=0,
                    warnings=["replay_build_skipped_existing_snapshots_present"],
                    build_skipped=True,
                )
        replay = replay_builder.build(
            start=config.day,
            end=config.day,
            last_days=None,
            market_ticker=None,
            min_settlement_confidence=config.min_settlement_confidence,
            allow_unsettled=False,
            max_markets=config.max_markets,
            historical_weather_fallback=False,
        )
        return replay


@dataclass
class _ExistingReplayResult:
    markets: int
    snapshots: int
    skipped_markets: int
    warnings: list[str]
    build_skipped: bool = True


def _summary(
    config: DailyWeatherEvidenceConfig,
    coverage: Any,
    replay: Any,
    mining: Any,
    market_making: Any,
    readiness: Any,
) -> dict[str, Any]:
    coverage_day = _coverage_day(coverage, config.day)
    replay_summary = _replay_summary(replay)
    miner_summary = _miner_summary(mining)
    mm_summary = dict(getattr(market_making, "summary", {}) or {})
    readiness_summary = {
        "status": getattr(readiness, "status", None),
        "message": getattr(readiness, "message", None),
        "reasons": list(getattr(readiness, "reasons", []) or []),
        "next_command": getattr(readiness, "next_command", None),
    }
    status = _status(coverage_day, replay_summary, miner_summary)
    return {
        "schema_version": 1,
        "source": "daily_weather_evidence",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": config.day.isoformat(),
        "status": status,
        "message": _message(status),
        "research_only": True,
        "config": {
            "max_markets": config.max_markets,
            "min_settlement_confidence": config.min_settlement_confidence,
            "min_edge_after_buffers_cents": config.min_edge_after_buffers_cents,
            "trading_readiness_last_days": config.trading_readiness_last_days,
            "force_rebuild_replay": config.force_rebuild_replay,
        },
        "coverage": coverage_day,
        "recorded_replay": replay_summary,
        "miner": miner_summary,
        "weather_market_making": {
            "market_making_verdict": mm_summary.get("market_making_verdict"),
            "message": mm_summary.get("message"),
            "weather_only": bool(mm_summary.get("weather_only")),
            "snapshots": int(mm_summary.get("snapshots") or 0),
            "markets_analyzed": int(mm_summary.get("markets_analyzed") or 0),
            "candidate_markets": int(mm_summary.get("candidate_markets") or 0),
            "trade_evidence_fills": int(mm_summary.get("trade_evidence_fills") or 0),
            "trade_evidence_fill_rate": float(mm_summary.get("trade_evidence_fill_rate") or 0.0),
            "avg_future_edge_30m_cents": float(mm_summary.get("avg_future_edge_30m_cents") or 0.0),
            "adverse_fill_rate_30m": float(mm_summary.get("adverse_fill_rate_30m") or 0.0),
            "paper_watchlist_candidates": int(mm_summary.get("paper_watchlist_candidates") or 0),
        },
        "trading_readiness": readiness_summary,
        "disclaimer": (
            "Research-only daily weather replay evidence. This command may build local recorded replay rows, "
            "but it does not trade, does not place orders, does not change readiness gates, and does not prove live profitability."
        ),
    }


def _coverage_day(coverage: Any, day: date) -> dict[str, Any]:
    days = list(getattr(coverage, "days", []) or [])
    target = next((row for row in days if str(row.get("day")) == day.isoformat()), None)
    if target is None:
        summary = dict(getattr(coverage, "summary", {}) or {})
        target = {
            "day": day.isoformat(),
            "overlap_tickers": summary.get("latest_overlap_day_overlap_tickers", 0),
            "settlement_label_tickers": summary.get("settlement_label_tickers_in_window", 0),
            "high_confidence_settlement_label_tickers": summary.get("latest_overlap_day_high_confidence_settlement_label_tickers", 0),
            "missing_settlement_label_tickers": summary.get("latest_overlap_day_missing_settlement_label_tickers", 0),
        }
    return {
        "status": (getattr(coverage, "summary", {}) or {}).get("status"),
        "day": target.get("day"),
        "recorded_orderbook_tickers": int(target.get("recorded_orderbook_tickers") or 0),
        "overlap_tickers": int(target.get("overlap_tickers") or 0),
        "settlement_label_tickers": int(target.get("settlement_label_tickers") or 0),
        "high_confidence_settlement_label_tickers": int(target.get("high_confidence_settlement_label_tickers") or 0),
        "missing_settlement_label_tickers": int(target.get("missing_settlement_label_tickers") or 0),
        "likely_replay_markets_gt0": bool(target.get("likely_replay_markets_gt0")),
        "suggested_replay_command": (getattr(coverage, "summary", {}) or {}).get("suggested_replay_command"),
    }


def _replay_summary(replay: Any) -> dict[str, Any]:
    return {
        "markets": int(getattr(replay, "markets", 0) or 0),
        "snapshots": int(getattr(replay, "snapshots", 0) or 0),
        "skipped_markets": int(getattr(replay, "skipped_markets", 0) or 0),
        "warnings": list(getattr(replay, "warnings", []) or []),
        "build_skipped": bool(getattr(replay, "build_skipped", False)),
    }


def _existing_replay_summary(storage: Storage, day: date) -> dict[str, int]:
    storage.init_db()
    frame = storage.fetch_sql(
        """
        SELECT COUNT(*) AS snapshots,
               COUNT(DISTINCT market_ticker) AS markets
        FROM recorded_orderbook_replay_snapshots
        WHERE date(ts) = :day
        """,
        {"day": day.isoformat()},
    )
    if frame.empty:
        return {"snapshots": 0, "markets": 0}
    row = frame.iloc[0]
    return {"snapshots": int(row.get("snapshots") or 0), "markets": int(row.get("markets") or 0)}


def _miner_summary(mining: Any) -> dict[str, Any]:
    summary = dict(getattr(mining, "summary", {}) or {})
    stress = summary.get("stress") or {}
    return {
        "verdict": summary.get("verdict"),
        "message": summary.get("message"),
        "rows_scanned": int(summary.get("rows_scanned") or 0),
        "markets_scanned": int(summary.get("markets_scanned") or 0),
        "eligible_rows": int(summary.get("eligible_rows") or 0),
        "signals": int(summary.get("signals") or 0),
        "settled_signals": int(summary.get("settled_signals") or 0),
        "gross_pnl_cents": float(summary.get("gross_pnl_cents") or 0.0),
        "fees_cents": float(summary.get("fees_cents") or 0.0),
        "net_pnl_cents": float(summary.get("net_pnl_cents") or 0.0),
        "win_rate": float(summary.get("win_rate") or 0.0),
        "stress_verdict": stress.get("verdict"),
        "stress": stress,
    }


def _status(coverage: dict[str, Any], replay: dict[str, Any], miner: dict[str, Any]) -> str:
    if int(coverage.get("high_confidence_settlement_label_tickers") or 0) <= 0:
        return "DAILY_WEATHER_EVIDENCE_NO_REPLAY_READY_LABELS"
    if int(replay.get("snapshots") or 0) <= 0:
        return "DAILY_WEATHER_EVIDENCE_NO_REPLAY_SNAPSHOTS"
    if int(miner.get("settled_signals") or 0) <= 0:
        return "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_TOO_FEW_SIGNALS"
    if float(miner.get("net_pnl_cents") or 0.0) <= 0:
        return "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_NO_EDGE"
    if (miner.get("stress_verdict") or "") not in {"passes basic stress", ""}:
        return "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_STRESS_FAILED"
    if int(miner.get("settled_signals") or 0) < 30:
        return "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_SMALL_SAMPLE"
    return "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_REVIEW"


def _message(status: str) -> str:
    messages = {
        "DAILY_WEATHER_EVIDENCE_NO_REPLAY_READY_LABELS": "No high-confidence settlement labels were available for this day; replay evidence is not ready.",
        "DAILY_WEATHER_EVIDENCE_NO_REPLAY_SNAPSHOTS": "Labels exist, but recorded replay wrote zero snapshots; no edge conclusion should be drawn.",
        "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_TOO_FEW_SIGNALS": "Replay exists, but mined settled signals are too sparse for an edge conclusion.",
        "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_NO_EDGE": "Mined weather signals lost after fees on settled rows; no edge.",
        "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_STRESS_FAILED": "Mined weather signals were positive but failed conservative stress checks.",
        "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_SMALL_SAMPLE": "Mined weather signals were positive but the settled sample is still too small.",
        "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_REVIEW": "Daily evidence is positive enough for research review only; readiness gates remain unchanged.",
    }
    return messages.get(status, "Research-only daily weather evidence.")


def _export(summary: dict[str, Any]) -> dict[str, str]:
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    day = str(summary.get("date"))
    json_path = reports / f"daily_weather_evidence_{day}.json"
    md_path = reports / f"daily_weather_evidence_{day}.md"
    json_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_markdown(summary), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def _drilldown_summary(
    config: DailyWeatherEvidenceDrilldownConfig,
    daily_summary: dict[str, Any],
    miner_summary: dict[str, Any],
    signals: list[dict[str, Any]],
    by_ticker: list[dict[str, Any]],
    by_city_station: list[dict[str, Any]],
) -> dict[str, Any]:
    stress = dict(miner_summary.get("stress") or {})
    settled = int(miner_summary.get("settled_signals") or 0)
    net = float(miner_summary.get("net_pnl_cents") or 0.0)
    depends_on_best = _depends_on_one_best_signal(signals, stress)
    warnings = _drilldown_warnings(settled, net, depends_on_best, len(signals))
    return {
        "schema_version": 1,
        "source": "daily_weather_evidence_drilldown",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": config.day.isoformat(),
        "status": "DAILY_WEATHER_DRILLDOWN_RESEARCH_ONLY",
        "message": _drilldown_message(settled, net, depends_on_best, len(signals)),
        "research_only": True,
        "daily_status": daily_summary.get("status"),
        "miner_verdict": miner_summary.get("verdict"),
        "signals": int(miner_summary.get("signals") or len(signals)),
        "settled_signals": settled,
        "net_pnl_cents": net,
        "gross_pnl_cents": float(miner_summary.get("gross_pnl_cents") or 0.0),
        "fees_cents": float(miner_summary.get("fees_cents") or 0.0),
        "win_rate": float(miner_summary.get("win_rate") or 0.0),
        "depends_on_one_best_signal": depends_on_best,
        "best_signal_net_pnl_cents": _best_signal_net(signals),
        "stress": stress,
        "future_mid_confirmation": {
            "future_mid_30m_beat_rate": miner_summary.get("future_mid_30m_beat_rate"),
            "future_mid_60m_beat_rate": miner_summary.get("future_mid_60m_beat_rate"),
            "future_mid_final_beat_rate": miner_summary.get("future_mid_final_beat_rate"),
            "future_mid_30m_observations": _non_null_signal_count(signals, "beat_future_30m"),
            "future_mid_60m_observations": _non_null_signal_count(signals, "beat_future_60m"),
            "future_mid_final_observations": _non_null_signal_count(signals, "beat_future_final"),
        },
        "entry_price_distribution": _bucket_counts(signals, "entry_price_bucket", "entry_price_cents", _entry_bucket),
        "edge_after_buffers_distribution": _bucket_counts(signals, None, "edge_after_buffers_cents", _edge_bucket),
        "fair_value_distribution": {
            "fair_yes_price_cents": _numeric_stats(signals, "fair_yes_price_cents"),
            "fair_no_price_cents": _numeric_stats(signals, "fair_no_price_cents"),
            "edge_after_buffers_cents": _numeric_stats(signals, "edge_after_buffers_cents"),
        },
        "top_ticker": by_ticker[0] if by_ticker else None,
        "top_city_station": by_city_station[0] if by_city_station else None,
        "warnings": warnings,
        "disclaimer": (
            "Research-only weather evidence drilldown. It explains mined replay signals and stress checks; "
            "it does not trade, does not grant paper/live permission, and does not change readiness gates."
        ),
    }


def _drilldown_message(settled: int, net: float, depends_on_best: bool, signals: int) -> str:
    if signals <= 0:
        return "No mined signals were available for this day."
    if settled <= 0:
        return "Signals exist, but none have settlement labels for P&L/stress evaluation."
    if net <= 0:
        return "Settled mined signals lost after fees; inspect worst signals and inputs before further research."
    if depends_on_best:
        return "Settled mined signals are positive, but the day is fragile because net P&L depends on one best signal."
    if settled < 30:
        return "Settled mined signals are positive, but the sample is still small."
    return "Settled mined signals are positive for research review only."


def _drilldown_warnings(settled: int, net: float, depends_on_best: bool, signals: int) -> list[str]:
    warnings = [
        "no_paper_or_live_trade_permission",
        "forecasts_and_observations_are_not_settlement_truth",
        "settlement_labels_drive_realized_pnl",
    ]
    if signals <= 0:
        warnings.append("no_mined_signals")
    if settled < 30:
        warnings.append("small_sample")
    if settled <= 0:
        warnings.append("no_settled_signals")
    if net <= 0 and settled > 0:
        warnings.append("negative_net_pnl_after_fees")
    if depends_on_best:
        warnings.append("fragile_depends_on_one_best_signal")
    return warnings


def _sorted_signals(signals: list[dict[str, Any]], *, reverse: bool) -> list[dict[str, Any]]:
    return sorted(signals, key=lambda row: _sort_net(row), reverse=reverse)


def _sort_net(row: dict[str, Any]) -> float:
    value = row.get("net_pnl_cents")
    if value is None:
        return -1_000_000.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1_000_000.0


def _signal_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "market_ticker": row.get("market_ticker"),
        "city": row.get("city"),
        "station_code": row.get("station_code"),
        "ts": str(row.get("ts")) if row.get("ts") is not None else None,
        "action": row.get("action"),
        "entry_price_cents": row.get("entry_price_cents"),
        "fair_yes_price_cents": row.get("fair_yes_price_cents"),
        "fair_no_price_cents": row.get("fair_no_price_cents"),
        "edge_after_buffers_cents": row.get("edge_after_buffers_cents"),
        "gross_pnl_cents": row.get("gross_pnl_cents"),
        "fees_cents": row.get("fees_cents"),
        "net_pnl_cents": row.get("net_pnl_cents"),
        "future_edge_30m_cents": row.get("future_edge_30m_cents"),
        "beat_future_30m": row.get("beat_future_30m"),
        "reason": row.get("reason"),
    }


def _signal_line(row: dict[str, Any]) -> str:
    return (
        f"- {row.get('market_ticker')} {row.get('action')} ts={row.get('ts')} "
        f"entry={_fmt(row.get('entry_price_cents'))} edge_after_buffers={_fmt(row.get('edge_after_buffers_cents'))} "
        f"net={_fmt(row.get('net_pnl_cents'))} future30={_fmt(row.get('future_edge_30m_cents'))}"
    )


def _aggregate_signals(signals: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in signals:
        key = tuple(row.get(k) or "unknown" for k in keys)
        groups.setdefault(key, []).append(row)
    rows: list[dict[str, Any]] = []
    for key, group in groups.items():
        net_values = [_num_value(row.get("net_pnl_cents")) for row in group if row.get("net_pnl_cents") is not None]
        out = {keys[idx]: key[idx] for idx in range(len(keys))}
        out.update(
            {
                "signals": len(group),
                "settled_signals": len(net_values),
                "net_pnl_cents": sum(net_values),
                "best_signal_net_pnl_cents": max(net_values) if net_values else None,
                "worst_signal_net_pnl_cents": min(net_values) if net_values else None,
            }
        )
        rows.append(out)
    rows.sort(key=lambda row: (row["net_pnl_cents"], row["signals"]), reverse=True)
    return rows


def _depends_on_one_best_signal(signals: list[dict[str, Any]], stress: dict[str, Any]) -> bool:
    exclude_best = stress.get("exclude_best_signal_net_pnl")
    if exclude_best is not None:
        return _num_value(exclude_best) <= 0.0 and any(row.get("net_pnl_cents") is not None for row in signals)
    net_values = sorted([_num_value(row.get("net_pnl_cents")) for row in signals if row.get("net_pnl_cents") is not None], reverse=True)
    return bool(net_values) and sum(net_values) > 0.0 and sum(net_values[1:]) <= 0.0


def _best_signal_net(signals: list[dict[str, Any]]) -> float | None:
    values = [_num_value(row.get("net_pnl_cents")) for row in signals if row.get("net_pnl_cents") is not None]
    return max(values) if values else None


def _bucket_counts(signals: list[dict[str, Any]], label_key: str | None, value_key: str, bucket_fn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in signals:
        bucket = row.get(label_key) if label_key else None
        if not bucket:
            bucket = bucket_fn(row.get(value_key))
        bucket = str(bucket)
        counts[bucket] = counts.get(bucket, 0) + 1
    return dict(sorted(counts.items()))


def _numeric_stats(signals: list[dict[str, Any]], key: str) -> dict[str, float | None]:
    values = [_num_value(row.get(key)) for row in signals if row.get(key) is not None]
    if not values:
        return {"min": None, "avg": None, "max": None}
    return {"min": min(values), "avg": sum(values) / len(values), "max": max(values)}


def _non_null_signal_count(signals: list[dict[str, Any]], key: str) -> int:
    return sum(1 for row in signals if row.get(key) is not None)


def _entry_bucket(value: Any) -> str:
    num = _num_value(value)
    if num <= 5:
        return "0_5"
    if num <= 10:
        return "5_10"
    if num <= 20:
        return "10_20"
    if num <= 40:
        return "20_40"
    if num <= 60:
        return "40_60"
    return "60_plus"


def _edge_bucket(value: Any) -> str:
    num = _num_value(value)
    if num < 0:
        return "negative"
    if num < 5:
        return "0_5"
    if num < 10:
        return "5_10"
    if num < 20:
        return "10_20"
    return "20_plus"


def _num_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _export_drilldown(
    summary: dict[str, Any],
    top: list[dict[str, Any]],
    worst: list[dict[str, Any]],
    by_ticker: list[dict[str, Any]],
    by_city_station: list[dict[str, Any]],
) -> dict[str, str]:
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    day = str(summary.get("date"))
    json_path = reports / f"daily_weather_evidence_drilldown_{day}.json"
    md_path = reports / f"daily_weather_evidence_drilldown_{day}.md"
    payload = {
        "summary": summary,
        "top_signals_by_pnl": top,
        "worst_signals_by_pnl": worst,
        "signals_by_ticker": by_ticker,
        "signals_by_city_station": by_city_station,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_markdown_drilldown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def _markdown_drilldown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    stress = summary.get("stress") or {}
    lines = [
        "# Daily Weather Evidence Drilldown",
        "",
        str(summary.get("disclaimer")),
        "",
        "## Summary",
        "",
        f"- Date: {summary.get('date')}",
        f"- Status: `{summary.get('status')}`",
        f"- Message: {summary.get('message')}",
        f"- Signals: {summary.get('signals')}",
        f"- Settled signals: {summary.get('settled_signals')}",
        f"- Net P&L cents: {_fmt(summary.get('net_pnl_cents'))}",
        f"- Depends on one best signal: {summary.get('depends_on_one_best_signal')}",
        f"- 2x fee stress net: {_fmt(stress.get('two_x_fees_net_pnl'))}",
        f"- Worse-fill 1c stress net: {_fmt(stress.get('worse_fill_1c_net_pnl'))}",
        f"- Exclude-best-signal net: {_fmt(stress.get('exclude_best_signal_net_pnl'))}",
        "",
        "## Warnings",
        "",
    ]
    lines.extend(f"- `{warning}`" for warning in summary.get("warnings", []))
    lines.extend(["", "## Top Signals By P&L", "", "| Ticker | Action | Entry | Edge After Buffers | Net P&L | Future 30m |", "|---|---|---:|---:|---:|---:|"])
    for row in payload["top_signals_by_pnl"]:
        lines.append(f"| {row.get('market_ticker')} | {row.get('action')} | {_fmt(row.get('entry_price_cents'))} | {_fmt(row.get('edge_after_buffers_cents'))} | {_fmt(row.get('net_pnl_cents'))} | {_fmt(row.get('future_edge_30m_cents'))} |")
    lines.extend(["", "## Worst Signals By P&L", "", "| Ticker | Action | Entry | Edge After Buffers | Net P&L | Future 30m |", "|---|---|---:|---:|---:|---:|"])
    for row in payload["worst_signals_by_pnl"]:
        lines.append(f"| {row.get('market_ticker')} | {row.get('action')} | {_fmt(row.get('entry_price_cents'))} | {_fmt(row.get('edge_after_buffers_cents'))} | {_fmt(row.get('net_pnl_cents'))} | {_fmt(row.get('future_edge_30m_cents'))} |")
    lines.extend(["", "## Signals By Ticker", "", "| Ticker | Signals | Settled | Net P&L | Best | Worst |", "|---|---:|---:|---:|---:|---:|"])
    for row in payload["signals_by_ticker"][:25]:
        lines.append(f"| {row.get('market_ticker')} | {row.get('signals')} | {row.get('settled_signals')} | {_fmt(row.get('net_pnl_cents'))} | {_fmt(row.get('best_signal_net_pnl_cents'))} | {_fmt(row.get('worst_signal_net_pnl_cents'))} |")
    return "\n".join(lines) + "\n"


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _range_row(summary: dict[str, Any]) -> dict[str, Any]:
    coverage = summary.get("coverage") or {}
    replay = summary.get("recorded_replay") or {}
    miner = summary.get("miner") or {}
    mm = summary.get("weather_market_making") or {}
    readiness = summary.get("trading_readiness") or {}
    return {
        "date": summary.get("date"),
        "status": summary.get("status"),
        "high_confidence_labels": int(coverage.get("high_confidence_settlement_label_tickers") or 0),
        "overlap_tickers": int(coverage.get("overlap_tickers") or 0),
        "replay_snapshots": int(replay.get("snapshots") or 0),
        "markets": int(replay.get("markets") or 0),
        "miner_signals": int(miner.get("signals") or 0),
        "settled_signals": int(miner.get("settled_signals") or 0),
        "net_pnl_cents": float(miner.get("net_pnl_cents") or 0.0),
        "stress_verdict": miner.get("stress_verdict"),
        "weather_market_making_verdict": mm.get("market_making_verdict"),
        "trading_readiness_status": readiness.get("status"),
        "research_only": bool(summary.get("research_only")),
        "error": None,
        "is_error": False,
    }


def _error_range_row(day: date, exc: Exception) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "status": "DAILY_WEATHER_EVIDENCE_ERROR",
        "high_confidence_labels": 0,
        "overlap_tickers": 0,
        "replay_snapshots": 0,
        "markets": 0,
        "miner_signals": 0,
        "settled_signals": 0,
        "net_pnl_cents": None,
        "stress_verdict": None,
        "weather_market_making_verdict": None,
        "trading_readiness_status": None,
        "research_only": True,
        "error": str(exc),
        "is_error": True,
    }


def _range_summary(config: DailyWeatherEvidenceRangeConfig, rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid_rows = [row for row in rows if not row.get("is_error")]
    positive_days = sum(1 for row in valid_rows if float(row.get("net_pnl_cents") or 0.0) > 0.0)
    failing_stress = sum(1 for row in valid_rows if _is_stress_failure(row))
    review_or_no_edge = sum(
        1
        for row in valid_rows
        if str(row.get("status") or "") in {
            "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_REVIEW",
            "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_NO_EDGE",
            "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_STRESS_FAILED",
            "DAILY_WEATHER_EVIDENCE_RESEARCH_ONLY_SMALL_SAMPLE",
        }
    )
    statuses = _counts(rows, "status")
    return {
        "schema_version": 1,
        "source": "daily_weather_evidence_range",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start": config.start.isoformat(),
        "end": config.end.isoformat(),
        "status": "DAILY_WEATHER_EVIDENCE_RANGE_RESEARCH_ONLY",
        "message": "Multi-day daily weather evidence summary is research-only and does not change readiness gates.",
        "research_only": True,
        "days_analyzed": len(rows),
        "days_with_errors": sum(1 for row in rows if row.get("is_error")),
        "days_with_replay_snapshots": sum(1 for row in valid_rows if int(row.get("replay_snapshots") or 0) > 0),
        "days_with_enough_labels": sum(1 for row in valid_rows if int(row.get("high_confidence_labels") or 0) > 0),
        "days_with_positive_miner_net_pnl": positive_days,
        "days_failing_stress": failing_stress,
        "days_review_or_no_edge": review_or_no_edge,
        "counts_by_status": statuses,
        "config": {
            "max_markets": config.max_markets,
            "min_settlement_confidence": config.min_settlement_confidence,
            "min_edge_after_buffers_cents": config.min_edge_after_buffers_cents,
            "trading_readiness_last_days": config.trading_readiness_last_days,
            "force_rebuild_replay": config.force_rebuild_replay,
        },
        "disclaimer": (
            "Research-only multi-day weather evidence. It summarizes saved labels/replay/mining/market-making evidence, "
            "does not trade, and does not promote paper or live readiness."
        ),
    }


def _is_stress_failure(row: dict[str, Any]) -> bool:
    if int(row.get("settled_signals") or 0) <= 0:
        return False
    verdict = str(row.get("stress_verdict") or "").strip().lower()
    return verdict not in {"", "passes basic stress", "no settled signals"}


def _counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "UNKNOWN")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _export_range(summary: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, str]:
    reports = PROJECT_ROOT / "reports"
    reports.mkdir(exist_ok=True)
    start = str(summary.get("start"))
    end = str(summary.get("end"))
    json_path = reports / f"daily_weather_evidence_range_{start}_{end}.json"
    md_path = reports / f"daily_weather_evidence_range_{start}_{end}.md"
    payload = {
        "summary": summary,
        "days": rows,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_markdown_range(summary, rows), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def _markdown_range(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Daily Weather Evidence Range",
        "",
        str(summary.get("disclaimer")),
        "",
        "## Summary",
        "",
        f"- Window: {summary.get('start')} to {summary.get('end')}",
        f"- Status: `{summary.get('status')}`",
        f"- Days analyzed: {summary.get('days_analyzed')}",
        f"- Days with errors: {summary.get('days_with_errors')}",
        f"- Days with replay snapshots: {summary.get('days_with_replay_snapshots')}",
        f"- Days with enough labels: {summary.get('days_with_enough_labels')}",
        f"- Days with positive miner net P&L: {summary.get('days_with_positive_miner_net_pnl')}",
        f"- Days failing stress: {summary.get('days_failing_stress')}",
        f"- Days with review/no-edge status: {summary.get('days_review_or_no_edge')}",
        "",
        "## Daily Rows",
        "",
        "| Date | Status | Labels | Overlap | Replay Snapshots | Markets | Signals | Settled | Net P&L | Stress | Market-Making | Readiness | Error |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('date')} | `{row.get('status')}` | {row.get('high_confidence_labels')} | "
            f"{row.get('overlap_tickers')} | {row.get('replay_snapshots')} | {row.get('markets')} | "
            f"{row.get('miner_signals')} | {row.get('settled_signals')} | {_fmt(row.get('net_pnl_cents'))} | "
            f"{row.get('stress_verdict')} | `{row.get('weather_market_making_verdict')}` | "
            f"`{row.get('trading_readiness_status')}` | {row.get('error') or ''} |"
        )
    return "\n".join(lines) + "\n"


def _markdown(summary: dict[str, Any]) -> str:
    coverage = summary.get("coverage") or {}
    replay = summary.get("recorded_replay") or {}
    miner = summary.get("miner") or {}
    mm = summary.get("weather_market_making") or {}
    readiness = summary.get("trading_readiness") or {}
    lines = [
        "# Daily Weather Evidence",
        "",
        str(summary.get("disclaimer")),
        "",
        "## Summary",
        "",
        f"- Date: {summary.get('date')}",
        f"- Status: `{summary.get('status')}`",
        f"- Message: {summary.get('message')}",
        f"- Trading readiness: `{readiness.get('status')}`",
        "",
        "## Coverage",
        "",
        f"- High-confidence labels: {coverage.get('high_confidence_settlement_label_tickers')}",
        f"- Overlap tickers: {coverage.get('overlap_tickers')}",
        f"- Missing labels: {coverage.get('missing_settlement_label_tickers')}",
        "",
        "## Recorded Replay",
        "",
        f"- Markets: {replay.get('markets')}",
        f"- Snapshots: {replay.get('snapshots')}",
        f"- Skipped markets: {replay.get('skipped_markets')}",
        "",
        "## Miner",
        "",
        f"- Verdict: `{miner.get('verdict')}`",
        f"- Signals: {miner.get('signals')}",
        f"- Settled signals: {miner.get('settled_signals')}",
        f"- Net P&L cents: {_fmt(miner.get('net_pnl_cents'))}",
        f"- Stress: {miner.get('stress_verdict')}",
        "",
        "## Weather Market-Making",
        "",
        f"- Verdict: `{mm.get('market_making_verdict')}`",
        f"- Trade-evidence fills: {mm.get('trade_evidence_fills')}",
        f"- Paper watchlist candidates: {mm.get('paper_watchlist_candidates')}",
    ]
    warnings = replay.get("warnings") or []
    if warnings:
        lines.extend(["", "## Replay Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings[:25])
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if value is None:
        return "none"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)
