from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import text

from config import PROJECT_ROOT
from data.storage import Storage


@dataclass(frozen=True)
class WeatherReplayCoverageConfig:
    last_days: int = 7
    min_settlement_confidence: float = 0.85


@dataclass(frozen=True)
class WeatherReplayCoverageResult:
    summary: dict[str, Any]
    days: list[dict[str, Any]]
    top_overlapping_tickers: list[dict[str, Any]]
    missing_weather_tickers: list[dict[str, Any]]
    exports: dict[str, str] | None

    def to_text(self) -> str:
        lines = [
            f"weather_replay_coverage_status={self.summary.get('status')}",
            f"message={self.summary.get('message')}",
            f"parsed_weather_tickers={self.summary.get('parsed_weather_tickers')} latest_known_overlap_ts={self.summary.get('latest_known_overlap_ts')}",
            f"latest_overlap_day={self.summary.get('latest_overlap_day')} "
            f"latest_overlap_day_overlap_tickers={self.summary.get('latest_overlap_day_overlap_tickers')} "
            f"latest_overlap_day_high_confidence_labels={self.summary.get('latest_overlap_day_high_confidence_settlement_label_tickers')} "
            f"latest_overlap_day_missing_labels={self.summary.get('latest_overlap_day_missing_settlement_label_tickers')}",
            f"suggested_replay_command={self.summary.get('suggested_replay_command')}",
            f"suggested_replay_reason={self.summary.get('suggested_replay_reason')}",
            f"exports={self.exports}",
            "Daily coverage:",
        ]
        for row in self.days:
            lines.append(
                f"- {row.get('day')}: recorded_orderbook_tickers={row.get('recorded_orderbook_tickers')} "
                f"overlap_tickers={row.get('overlap_tickers')} settlement_labels={row.get('settlement_label_tickers')} "
                f"high_confidence_labels={row.get('high_confidence_settlement_label_tickers')} "
                f"missing_labels={row.get('missing_settlement_label_tickers')} "
                f"likely_replay_markets_gt0={row.get('likely_replay_markets_gt0')}"
            )
        lines.append("Top overlapping tickers:")
        for row in self.top_overlapping_tickers[:10]:
            lines.append(
                f"- {row.get('market_ticker')} snapshots={row.get('snapshots')} "
                f"label_confidence={row.get('settlement_confidence')} first={row.get('first_ts')} latest={row.get('latest_ts')}"
            )
        lines.append("Top parsed weather tickers missing from recorded orderbooks:")
        for row in self.missing_weather_tickers[:10]:
            lines.append(
                f"- {row.get('market_ticker')} status={row.get('market_status')} close_time={row.get('close_time')}"
            )
        return "\n".join(lines)


class WeatherReplayCoverageReporter:
    """Read-only coverage report for weather replay orderbook overlap."""

    def __init__(self, storage: Storage | None = None, today_fn: Callable[[], date] | None = None):
        self.storage = storage or Storage()
        self.today_fn = today_fn or (lambda: datetime.now(timezone.utc).date())

    def build(
        self,
        config: WeatherReplayCoverageConfig | None = None,
        *,
        persist_exports: bool = True,
    ) -> WeatherReplayCoverageResult:
        config = config or WeatherReplayCoverageConfig()
        self.storage.init_db()
        end_day = self.today_fn()
        start_day = end_day - timedelta(days=max(int(config.last_days), 1) - 1)
        parsed_count = self._parsed_weather_count()
        days = [
            self._day_coverage(start_day + timedelta(days=offset), config.min_settlement_confidence)
            for offset in range((end_day - start_day).days + 1)
        ]
        top_overlap = self._top_overlapping_tickers(start_day, end_day, config.min_settlement_confidence)
        missing = self._missing_weather_tickers(start_day, end_day)
        latest_overlap = self._latest_known_overlap_ts()
        suggestion, suggestion_reason = self._suggested_command(
            start_day,
            end_day,
            top_overlap,
            latest_overlap,
            config.min_settlement_confidence,
        )
        any_recent_overlap = any(int(row["overlap_tickers"]) > 0 for row in days)
        any_recent_replay_ready = any(int(row["high_confidence_settlement_label_tickers"]) > 0 for row in days)
        total_overlap = sum(int(row["overlap_tickers"]) for row in days)
        total_labels = sum(int(row["settlement_label_tickers"]) for row in days)
        total_high_confidence = sum(int(row["high_confidence_settlement_label_tickers"]) for row in days)
        total_missing_labels = sum(int(row["missing_settlement_label_tickers"]) for row in days)
        latest_overlap_day = _latest_overlap_day(days)
        latest_high_confidence = int(latest_overlap_day["high_confidence_settlement_label_tickers"]) if latest_overlap_day else 0
        status = _status(any_recent_overlap, any_recent_replay_ready, latest_high_confidence)
        summary = {
            "status": status,
            "message": _message(status),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "last_days": config.last_days,
            "window_start": start_day.isoformat(),
            "window_end": end_day.isoformat(),
            "parsed_weather_tickers": parsed_count,
            "overlap_tickers_in_window": total_overlap,
            "settlement_label_tickers_in_window": total_labels,
            "high_confidence_settlement_label_tickers_in_window": total_high_confidence,
            "missing_settlement_label_tickers_in_window": total_missing_labels,
            "min_settlement_confidence": config.min_settlement_confidence,
            "latest_overlap_day": latest_overlap_day.get("day") if latest_overlap_day else None,
            "latest_overlap_day_overlap_tickers": int(latest_overlap_day["overlap_tickers"]) if latest_overlap_day else 0,
            "latest_overlap_day_high_confidence_settlement_label_tickers": latest_high_confidence,
            "latest_overlap_day_missing_settlement_label_tickers": int(latest_overlap_day["missing_settlement_label_tickers"]) if latest_overlap_day else 0,
            "latest_known_overlap_ts": latest_overlap,
            "suggested_replay_command": suggestion,
            "suggested_replay_reason": suggestion_reason,
        }
        exports = self._export(summary, days, top_overlap, missing) if persist_exports else None
        return WeatherReplayCoverageResult(
            summary=summary,
            days=days,
            top_overlapping_tickers=top_overlap,
            missing_weather_tickers=missing,
            exports=exports,
        )

    def _parsed_weather_count(self) -> int:
        frame = self.storage.fetch_sql("SELECT COUNT(DISTINCT market_ticker) AS count FROM parsed_contracts")
        return int(frame.iloc[0]["count"]) if not frame.empty else 0

    def _day_coverage(self, day: date, min_settlement_confidence: float) -> dict[str, Any]:
        params = {"day": day.isoformat(), "min_confidence": min_settlement_confidence}
        frame = self.storage.fetch_sql(
            """
            WITH recorded AS (
                SELECT DISTINCT market_ticker
                FROM orderbook_snapshots_live
                WHERE date(ts) = :day
            ),
            parsed AS (
                SELECT DISTINCT market_ticker
                FROM parsed_contracts
            ),
            overlap AS (
                SELECT r.market_ticker
                FROM recorded r
                JOIN parsed p ON p.market_ticker = r.market_ticker
            )
            SELECT
                (SELECT COUNT(*) FROM recorded) AS recorded_orderbook_tickers,
                (SELECT COUNT(*) FROM overlap) AS overlap_tickers,
                (SELECT COUNT(*) FROM overlap o JOIN settlement_labels s ON s.market_ticker = o.market_ticker) AS settlement_label_tickers,
                (
                    SELECT COUNT(*)
                    FROM overlap o
                    JOIN settlement_labels s ON s.market_ticker = o.market_ticker
                    WHERE COALESCE(s.confidence, 0.0) >= :min_confidence
                ) AS high_confidence_settlement_label_tickers
            """,
            params,
        )
        recorded = int(frame.iloc[0]["recorded_orderbook_tickers"]) if not frame.empty else 0
        overlap = int(frame.iloc[0]["overlap_tickers"]) if not frame.empty else 0
        labeled = int(frame.iloc[0]["settlement_label_tickers"]) if not frame.empty else 0
        high_confidence = int(frame.iloc[0]["high_confidence_settlement_label_tickers"]) if not frame.empty else 0
        return {
            "day": day.isoformat(),
            "recorded_orderbook_tickers": recorded,
            "overlap_tickers": overlap,
            "settlement_label_tickers": labeled,
            "high_confidence_settlement_label_tickers": high_confidence,
            "missing_settlement_label_tickers": max(overlap - high_confidence, 0),
            "likely_replay_markets_gt0": high_confidence > 0,
        }

    def _top_overlapping_tickers(self, start_day: date, end_day: date, min_settlement_confidence: float) -> list[dict[str, Any]]:
        frame = self.storage.fetch_sql(
            """
            WITH parsed AS (
                SELECT DISTINCT market_ticker
                FROM parsed_contracts
            )
            SELECT
                o.market_ticker,
                COUNT(*) AS snapshots,
                MIN(o.ts) AS first_ts,
                MAX(o.ts) AS latest_ts,
                s.confidence AS settlement_confidence,
                CASE WHEN s.market_ticker IS NOT NULL THEN 1 ELSE 0 END AS has_settlement_label,
                CASE WHEN COALESCE(s.confidence, 0.0) >= :min_confidence THEN 1 ELSE 0 END AS has_high_confidence_settlement_label
            FROM orderbook_snapshots_live o
            JOIN parsed p ON p.market_ticker = o.market_ticker
            LEFT JOIN settlement_labels s ON s.market_ticker = o.market_ticker
            WHERE date(o.ts) BETWEEN :start_day AND :end_day
            GROUP BY o.market_ticker
            ORDER BY has_high_confidence_settlement_label DESC, latest_ts DESC, snapshots DESC
            LIMIT 25
            """,
            {
                "start_day": start_day.isoformat(),
                "end_day": end_day.isoformat(),
                "min_confidence": min_settlement_confidence,
            },
        )
        return _rows(frame)

    def _missing_weather_tickers(self, start_day: date, end_day: date) -> list[dict[str, Any]]:
        frame = self.storage.fetch_sql(
            """
            WITH parsed AS (
                SELECT market_ticker, MAX(id) AS latest_id
                FROM parsed_contracts
                GROUP BY market_ticker
            ),
            recorded AS (
                SELECT DISTINCT market_ticker
                FROM orderbook_snapshots_live
                WHERE date(ts) BETWEEN :start_day AND :end_day
            )
            SELECT
                p.market_ticker,
                json_extract(m.payload, '$.status') AS market_status,
                json_extract(m.payload, '$.close_time') AS close_time
            FROM parsed p
            LEFT JOIN recorded r ON r.market_ticker = p.market_ticker
            LEFT JOIN markets m ON m.ticker = p.market_ticker
            WHERE r.market_ticker IS NULL
            ORDER BY CASE json_extract(m.payload, '$.status') WHEN 'active' THEN 0 WHEN 'open' THEN 0 ELSE 1 END,
                     p.latest_id DESC
            LIMIT 25
            """,
            {"start_day": start_day.isoformat(), "end_day": end_day.isoformat()},
        )
        return _rows(frame)

    def _latest_known_overlap_ts(self) -> str | None:
        frame = self.storage.fetch_sql(
            """
            WITH parsed AS (
                SELECT DISTINCT market_ticker
                FROM parsed_contracts
            )
            SELECT MAX(o.ts) AS latest_ts
            FROM orderbook_snapshots_live o
            JOIN parsed p ON p.market_ticker = o.market_ticker
            """
        )
        if frame.empty:
            return None
        value = frame.iloc[0].get("latest_ts")
        return None if value in (None, "") else str(value)

    def _suggested_command(
        self,
        start_day: date,
        end_day: date,
        top_overlap: list[dict[str, Any]],
        latest_overlap: str | None,
        min_settlement_confidence: float,
    ) -> tuple[str | None, str]:
        candidate = next(
            (row for row in top_overlap if int(row.get("has_high_confidence_settlement_label") or 0) == 1),
            None,
        )
        if candidate is None and latest_overlap:
            overlap_day = str(latest_overlap)[:10]
            frame = self.storage.fetch_sql(
                """
                WITH parsed AS (
                    SELECT DISTINCT market_ticker
                    FROM parsed_contracts
                )
                SELECT o.market_ticker, COUNT(*) AS snapshots, MAX(o.ts) AS latest_ts, s.confidence AS settlement_confidence
                FROM orderbook_snapshots_live o
                JOIN parsed p ON p.market_ticker = o.market_ticker
                JOIN settlement_labels s ON s.market_ticker = o.market_ticker
                WHERE date(o.ts) = :day
                  AND COALESCE(s.confidence, 0.0) >= :min_confidence
                GROUP BY o.market_ticker
                ORDER BY snapshots DESC
                LIMIT 1
                """,
                {"day": overlap_day, "min_confidence": min_settlement_confidence},
            )
            if not frame.empty:
                candidate = {
                    "market_ticker": str(frame.iloc[0]["market_ticker"]),
                    "latest_ts": str(frame.iloc[0].get("latest_ts") or latest_overlap),
                    "settlement_confidence": frame.iloc[0].get("settlement_confidence"),
                }
        if candidate is None:
            return None, "No replay-ready ticker found: recorded weather overlap exists only without settlement labels or below the required confidence threshold."
        ticker = candidate.get("market_ticker")
        day = str(candidate.get("latest_ts") or start_day.isoformat())[:10]
        command = f"python main.py build-recorded-replay --start {day} --end {day} --market-ticker {ticker} --recorded-weather-only"
        return command, "Suggested ticker has recorded weather orderbooks and a high-confidence settlement label."

    def _export(
        self,
        summary: dict[str, Any],
        days: list[dict[str, Any]],
        top_overlap: list[dict[str, Any]],
        missing: list[dict[str, Any]],
    ) -> dict[str, str]:
        reports = PROJECT_ROOT / "reports"
        reports.mkdir(exist_ok=True)
        json_path = reports / "weather_replay_coverage.json"
        md_path = reports / "weather_replay_coverage.md"
        payload = {
            "summary": summary,
            "daily_coverage": days,
            "top_overlapping_tickers": top_overlap,
            "missing_weather_tickers": missing,
            "disclaimer": "Read-only replay coverage diagnostic; no replay build, trading, or readiness promotion is performed.",
        }
        json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        md_path.write_text(_markdown(payload), encoding="utf-8")
        return {"json": str(json_path), "markdown": str(md_path)}


def _rows(frame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return [row.to_dict() for _, row in frame.iterrows()]


def _latest_overlap_day(days: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in reversed(days):
        if int(row.get("overlap_tickers") or 0) > 0:
            return row
    return None


def _status(any_recent_overlap: bool, any_recent_replay_ready: bool, latest_overlap_high_confidence: int) -> str:
    if not any_recent_overlap:
        return "WEATHER_REPLAY_COVERAGE_ZERO_RECENT_OVERLAP"
    if not any_recent_replay_ready:
        return "WEATHER_REPLAY_COVERAGE_TICKERS_OK_LABELS_MISSING"
    if latest_overlap_high_confidence <= 0:
        return "WEATHER_REPLAY_COVERAGE_OK_STALE_LABELS_ONLY"
    return "WEATHER_REPLAY_COVERAGE_OK"


def _message(status: str) -> str:
    if status == "WEATHER_REPLAY_COVERAGE_ZERO_RECENT_OVERLAP":
        return "No recent recorded orderbook tickers overlap parsed weather contracts in the selected window."
    if status == "WEATHER_REPLAY_COVERAGE_TICKERS_OK_LABELS_MISSING":
        return "Recent recorded orderbooks overlap parsed weather contracts, but settlement labels are missing or below confidence threshold."
    if status == "WEATHER_REPLAY_COVERAGE_OK_STALE_LABELS_ONLY":
        return "Older overlap days have usable settlement labels, but the freshest overlap day is not replay-ready because labels are missing or below confidence threshold."
    return "Recent recorded orderbooks overlap parsed weather contracts with usable settlement labels."


def _markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Weather Replay Coverage",
        "",
        str(payload["disclaimer"]),
        "",
        "## Summary",
        "",
        f"- Status: `{summary.get('status')}`",
        f"- Window: {summary.get('window_start')} to {summary.get('window_end')}",
        f"- Parsed weather tickers: {summary.get('parsed_weather_tickers')}",
        f"- Overlap tickers in window: {summary.get('overlap_tickers_in_window')}",
        f"- Settlement labels in window: {summary.get('settlement_label_tickers_in_window')}",
        f"- High-confidence settlement labels in window: {summary.get('high_confidence_settlement_label_tickers_in_window')}",
        f"- Missing settlement labels in window: {summary.get('missing_settlement_label_tickers_in_window')}",
        f"- Minimum settlement confidence: {summary.get('min_settlement_confidence')}",
        f"- Latest overlap day: {summary.get('latest_overlap_day')}",
        f"- Latest overlap day overlap tickers: {summary.get('latest_overlap_day_overlap_tickers')}",
        f"- Latest overlap day high-confidence settlement labels: {summary.get('latest_overlap_day_high_confidence_settlement_label_tickers')}",
        f"- Latest overlap day missing settlement labels: {summary.get('latest_overlap_day_missing_settlement_label_tickers')}",
        f"- Latest known overlap: {summary.get('latest_known_overlap_ts')}",
        f"- Suggested replay command: `{summary.get('suggested_replay_command')}`",
        f"- Suggested replay reason: {summary.get('suggested_replay_reason')}",
        "",
        "## Daily Coverage",
        "",
        "| Day | Recorded Orderbook Tickers | Overlap Tickers | Labels | High-Confidence Labels | Missing Labels | Likely Replay >0 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload["daily_coverage"]:
        lines.append(
            f"| {row.get('day')} | {row.get('recorded_orderbook_tickers')} | "
            f"{row.get('overlap_tickers')} | {row.get('settlement_label_tickers')} | "
            f"{row.get('high_confidence_settlement_label_tickers')} | "
            f"{row.get('missing_settlement_label_tickers')} | {row.get('likely_replay_markets_gt0')} |"
        )
    lines.extend(["", "## Top Overlapping Tickers", "", "| Ticker | Snapshots | Label Confidence | First | Latest |", "|---|---:|---:|---|---|"])
    for row in payload["top_overlapping_tickers"]:
        lines.append(f"| {row.get('market_ticker')} | {row.get('snapshots')} | {row.get('settlement_confidence')} | {row.get('first_ts')} | {row.get('latest_ts')} |")
    lines.extend(["", "## Missing Parsed Weather Tickers", "", "| Ticker | Status | Close Time |", "|---|---|---|"])
    for row in payload["missing_weather_tickers"]:
        lines.append(f"| {row.get('market_ticker')} | {row.get('market_status')} | {row.get('close_time')} |")
    return "\n".join(lines) + "\n"
