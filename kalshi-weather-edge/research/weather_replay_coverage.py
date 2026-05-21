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
            f"suggested_replay_command={self.summary.get('suggested_replay_command')}",
            f"exports={self.exports}",
            "Daily coverage:",
        ]
        for row in self.days:
            lines.append(
                f"- {row.get('day')}: recorded_orderbook_tickers={row.get('recorded_orderbook_tickers')} "
                f"overlap_tickers={row.get('overlap_tickers')} likely_replay_markets_gt0={row.get('likely_replay_markets_gt0')}"
            )
        lines.append("Top overlapping tickers:")
        for row in self.top_overlapping_tickers[:10]:
            lines.append(
                f"- {row.get('market_ticker')} snapshots={row.get('snapshots')} "
                f"first={row.get('first_ts')} latest={row.get('latest_ts')}"
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
            self._day_coverage(start_day + timedelta(days=offset))
            for offset in range((end_day - start_day).days + 1)
        ]
        top_overlap = self._top_overlapping_tickers(start_day, end_day)
        missing = self._missing_weather_tickers(start_day, end_day)
        latest_overlap = self._latest_known_overlap_ts()
        suggestion = self._suggested_command(start_day, end_day, top_overlap, latest_overlap)
        any_recent_overlap = any(int(row["overlap_tickers"]) > 0 for row in days)
        summary = {
            "status": "WEATHER_REPLAY_COVERAGE_OK" if any_recent_overlap else "WEATHER_REPLAY_COVERAGE_ZERO_RECENT_OVERLAP",
            "message": (
                "Recent recorded orderbooks overlap parsed weather contracts."
                if any_recent_overlap
                else "No recent recorded orderbook tickers overlap parsed weather contracts in the selected window."
            ),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "last_days": config.last_days,
            "window_start": start_day.isoformat(),
            "window_end": end_day.isoformat(),
            "parsed_weather_tickers": parsed_count,
            "latest_known_overlap_ts": latest_overlap,
            "suggested_replay_command": suggestion,
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

    def _day_coverage(self, day: date) -> dict[str, Any]:
        params = {"day": day.isoformat()}
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
            )
            SELECT
                (SELECT COUNT(*) FROM recorded) AS recorded_orderbook_tickers,
                (SELECT COUNT(*) FROM recorded r JOIN parsed p ON p.market_ticker = r.market_ticker) AS overlap_tickers
            """,
            params,
        )
        recorded = int(frame.iloc[0]["recorded_orderbook_tickers"]) if not frame.empty else 0
        overlap = int(frame.iloc[0]["overlap_tickers"]) if not frame.empty else 0
        return {
            "day": day.isoformat(),
            "recorded_orderbook_tickers": recorded,
            "overlap_tickers": overlap,
            "likely_replay_markets_gt0": overlap > 0,
        }

    def _top_overlapping_tickers(self, start_day: date, end_day: date) -> list[dict[str, Any]]:
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
                MAX(o.ts) AS latest_ts
            FROM orderbook_snapshots_live o
            JOIN parsed p ON p.market_ticker = o.market_ticker
            WHERE date(o.ts) BETWEEN :start_day AND :end_day
            GROUP BY o.market_ticker
            ORDER BY latest_ts DESC, snapshots DESC
            LIMIT 25
            """,
            {"start_day": start_day.isoformat(), "end_day": end_day.isoformat()},
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
    ) -> str | None:
        candidate = top_overlap[0] if top_overlap else None
        if candidate is None and latest_overlap:
            overlap_day = str(latest_overlap)[:10]
            frame = self.storage.fetch_sql(
                """
                WITH parsed AS (
                    SELECT DISTINCT market_ticker
                    FROM parsed_contracts
                )
                SELECT o.market_ticker, COUNT(*) AS snapshots
                FROM orderbook_snapshots_live o
                JOIN parsed p ON p.market_ticker = o.market_ticker
                WHERE date(o.ts) = :day
                GROUP BY o.market_ticker
                ORDER BY snapshots DESC
                LIMIT 1
                """,
                {"day": overlap_day},
            )
            if not frame.empty:
                candidate = {"market_ticker": str(frame.iloc[0]["market_ticker"]), "latest_ts": latest_overlap}
        if candidate is None:
            return None
        ticker = candidate.get("market_ticker")
        day = str(candidate.get("latest_ts") or start_day.isoformat())[:10]
        return f"python main.py build-recorded-replay --start {day} --end {day} --market-ticker {ticker} --recorded-weather-only"

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
        f"- Latest known overlap: {summary.get('latest_known_overlap_ts')}",
        f"- Suggested replay command: `{summary.get('suggested_replay_command')}`",
        "",
        "## Daily Coverage",
        "",
        "| Day | Recorded Orderbook Tickers | Overlap Tickers | Likely Replay >0 |",
        "|---|---:|---:|---|",
    ]
    for row in payload["daily_coverage"]:
        lines.append(
            f"| {row.get('day')} | {row.get('recorded_orderbook_tickers')} | "
            f"{row.get('overlap_tickers')} | {row.get('likely_replay_markets_gt0')} |"
        )
    lines.extend(["", "## Top Overlapping Tickers", "", "| Ticker | Snapshots | First | Latest |", "|---|---:|---|---|"])
    for row in payload["top_overlapping_tickers"]:
        lines.append(f"| {row.get('market_ticker')} | {row.get('snapshots')} | {row.get('first_ts')} | {row.get('latest_ts')} |")
    lines.extend(["", "## Missing Parsed Weather Tickers", "", "| Ticker | Status | Close Time |", "|---|---|---|"])
    for row in payload["missing_weather_tickers"]:
        lines.append(f"| {row.get('market_ticker')} | {row.get('market_status')} | {row.get('close_time')} |")
    return "\n".join(lines) + "\n"
