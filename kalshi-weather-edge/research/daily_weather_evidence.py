from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
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
