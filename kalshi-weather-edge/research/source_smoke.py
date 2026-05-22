from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import inspect, text

from config import PROJECT_ROOT, Settings, settings
from data.kalshi_client import KalshiClient
from data.nws_climate_report_client import NWSClimateReportClient
from data.storage import Storage
from data.weather_client import WeatherClient
from data.weather_station_mapper import StationMapper
from research.trading_readiness import TradingReadiness


KEY_TABLES = {
    "orderbook_snapshots_live": "ts",
    "settlement_labels": "created_at",
    "weather_observation_snapshots_live": "ts_recorded",
    "weather_forecast_snapshots_live": "ts_recorded",
    "recorded_orderbook_replay_snapshots": "ts",
}


@dataclass(frozen=True)
class SourceSmokeResult:
    summary: dict[str, Any]
    components: list[dict[str, Any]]
    exports: dict[str, str] | None

    def to_text(self) -> str:
        lines = [
            f"source_smoke_status={self.summary.get('status')}",
            f"research_only={str(self.summary.get('research_only')).lower()} "
            f"live_trading_enabled={str(self.summary.get('live_trading_enabled')).lower()} "
            f"order_placement_enabled={str(self.summary.get('order_placement_enabled')).lower()} "
            f"secrets_printed={str(self.summary.get('secrets_printed')).lower()}",
            f"components={self.summary.get('component_count')} succeeded={self.summary.get('components_succeeded')} "
            f"failed={self.summary.get('components_failed')} not_attempted={self.summary.get('components_not_attempted')}",
            f"exports={self.exports}",
            "Components:",
        ]
        for row in self.components:
            lines.append(
                f"- {row.get('component')}: env_configured={str(row.get('env_configured')).lower()} "
                f"live_fetch_implemented={str(row.get('live_fetch_implemented')).lower()} "
                f"live_fetch_attempted={str(row.get('live_fetch_attempted')).lower()} "
                f"live_fetch_succeeded={str(row.get('live_fetch_succeeded')).lower()} "
                f"result_count={row.get('result_count')} db_table_available={row.get('db_table_available')} "
                f"latest_recorded_at={row.get('latest_recorded_at')} error={row.get('last_error_category')} "
                f"next={row.get('next_required_step')}"
            )
        return "\n".join(lines)


class SourceSmokeReporter:
    """Safe source/pipeline smoke diagnostics.

    This command is intentionally read-only. It does not call order/account
    endpoints and does not initialize, migrate, or mutate the database.
    """

    def __init__(
        self,
        *,
        storage: Storage | None = None,
        cfg: Settings = settings,
        env: Mapping[str, str] | None = None,
        kalshi_client: Any | None = None,
        weather_client: Any | None = None,
        climate_client: Any | None = None,
        trading_readiness: Any | None = None,
    ):
        self.storage = storage or Storage()
        self.cfg = cfg
        self.env = env if env is not None else os.environ
        self.kalshi_client = kalshi_client
        self.weather_client = weather_client
        self.climate_client = climate_client
        self.trading_readiness = trading_readiness

    def build(self, *, persist_exports: bool = True, attempt_live_fetches: bool = True) -> SourceSmokeResult:
        generated_at = datetime.now(timezone.utc)
        components = [
            self._kalshi_market_access(attempt_live_fetches),
            self._kalshi_orderbook_access(attempt_live_fetches),
            self._weather_observations(attempt_live_fetches),
            self._weather_forecasts(attempt_live_fetches),
            self._nws_climate_reports(attempt_live_fetches),
            self._table_component("settlement_labels", "settlement_labels", "created_at", "Build exact settlements for recent weather dates."),
            self._table_component("orderbook_snapshots_live table", "orderbook_snapshots_live", "ts", "Run record-orderbooks in read-only collection mode."),
            self._table_component("weather observations table", "weather_observation_snapshots_live", "ts_recorded", "Run record-weather-observations."),
            self._table_component("weather forecasts table", "weather_forecast_snapshots_live", "ts_recorded", "Run record-weather-forecasts."),
            self._table_component("recorded replay availability", "recorded_orderbook_replay_snapshots", "ts", "Run build-recorded-replay after labels and orderbooks exist."),
            self._daily_evidence_reports(),
            self._trading_readiness_component(),
        ]
        failed = sum(1 for row in components if row.get("last_error_category"))
        succeeded = sum(1 for row in components if row.get("live_fetch_attempted") and row.get("live_fetch_succeeded"))
        not_attempted = sum(1 for row in components if row.get("live_fetch_implemented") and not row.get("live_fetch_attempted"))
        summary = {
            "status": "SOURCE_SMOKE_ATTENTION" if failed else "SOURCE_SMOKE_OK",
            "generated_at": generated_at.isoformat(),
            "research_only": True,
            "live_trading_enabled": False,
            "order_placement_enabled": False,
            "secrets_printed": False,
            "readiness_promotion": "none",
            "trading_readiness_unchanged": True,
            "database_path_exists": self._database_path_exists(),
            "component_count": len(components),
            "components_succeeded": succeeded,
            "components_failed": failed,
            "components_not_attempted": not_attempted,
        }
        exports = self._export(summary, components) if persist_exports else None
        return SourceSmokeResult(summary=summary, components=components, exports=exports)

    def _kalshi_market_access(self, attempt: bool) -> dict[str, Any]:
        row = self._base_component(
            "Kalshi API credentials/read-only market access",
            required_env_vars=["KALSHI_API_KEY_ID", "KALSHI_API_PRIVATE_KEY_PATH"],
            live_fetch_implemented=True,
            next_required_step="Set read-only Kalshi credentials if private endpoints are later needed; public market metadata can be checked without order endpoints.",
        )
        key_id = bool(str(self.env.get("KALSHI_API_KEY_ID") or "").strip())
        key_path_raw = str(self.env.get("KALSHI_API_PRIVATE_KEY_PATH") or "").strip()
        key_path_exists = bool(key_path_raw and Path(key_path_raw).expanduser().exists())
        row["env_configured"] = bool(key_id and key_path_exists)
        row["details"] = {
            "kalshi_api_key_id_configured": key_id,
            "kalshi_private_key_path_configured": bool(key_path_raw),
            "kalshi_private_key_path_exists": key_path_exists,
            "auth_not_used_for_smoke": True,
        }
        if not attempt:
            return row
        row["live_fetch_attempted"] = True
        try:
            payload = self._kalshi().get_markets(limit=1, status="open")
            markets = payload.get("markets", []) if isinstance(payload, dict) else []
            row["live_fetch_succeeded"] = True
            row["result_count"] = len(markets)
            row["latest_recorded_at"] = None
            row["next_required_step"] = "Kalshi public market metadata read succeeded; keep using read-only collection commands."
        except Exception as exc:
            row["last_error_category"] = _error_category(exc)
            row["next_required_step"] = "Check network/Kalshi API availability; do not test with order/account endpoints."
        return row

    def _kalshi_orderbook_access(self, attempt: bool) -> dict[str, Any]:
        table = self._table_stats("orderbook_snapshots_live", "ts")
        row = self._base_component(
            "Kalshi orderbook snapshot collection",
            required_env_vars=[],
            live_fetch_implemented=True,
            db_table_available=table["available"],
            result_count=table["row_count"],
            latest_recorded_at=table["latest_recorded_at"],
            next_required_step="Run record-orderbooks --weather-only --persist-weather-markets or all-market recorder as needed.",
        )
        if not attempt:
            return row
        ticker = self._latest_ticker("orderbook_snapshots_live", "market_ticker", "ts")
        if not ticker:
            try:
                markets = self._kalshi().get_markets(limit=1, status="open").get("markets", [])
                ticker = markets[0].get("ticker") if markets else None
            except Exception as exc:
                row["last_error_category"] = _error_category(exc)
                return row
        if not ticker:
            row["last_error_category"] = "no_ticker_available"
            return row
        row["live_fetch_attempted"] = True
        try:
            payload = self._kalshi().get_orderbook(str(ticker), depth=1)
            row["live_fetch_succeeded"] = isinstance(payload, dict)
            row["next_required_step"] = "Kalshi public orderbook read succeeded; continue read-only recorder collection."
        except Exception as exc:
            row["last_error_category"] = _error_category(exc)
        return row

    def _weather_observations(self, attempt: bool) -> dict[str, Any]:
        table = self._table_stats("weather_observation_snapshots_live", "ts_recorded")
        row = self._base_component(
            "weather observations",
            required_env_vars=[],
            live_fetch_implemented=True,
            db_table_available=table["available"],
            result_count=table["row_count"],
            latest_recorded_at=table["latest_recorded_at"],
            next_required_step="Run record-weather-observations for mapped stations.",
        )
        row["details"] = {
            "nws_user_agent_env_configured": bool(str(self.env.get("NWS_USER_AGENT") or "").strip()),
            "project_uses_builtin_nws_user_agent": True,
            "noaa_token_configured_optional": bool(str(self.env.get("NOAA_TOKEN") or "").strip()),
        }
        if not attempt:
            return row
        row["live_fetch_attempted"] = True
        try:
            result = self._weather().latest_observation_payload("KNYC")
            row["live_fetch_succeeded"] = bool(result and result[0])
            row["next_required_step"] = "Observation fetch path works; keep recorder running for current mapped stations."
        except Exception as exc:
            row["last_error_category"] = _error_category(exc)
        return row

    def _weather_forecasts(self, attempt: bool) -> dict[str, Any]:
        table = self._table_stats("weather_forecast_snapshots_live", "ts_recorded")
        row = self._base_component(
            "weather forecasts",
            required_env_vars=[],
            live_fetch_implemented=True,
            db_table_available=table["available"],
            result_count=table["row_count"],
            latest_recorded_at=table["latest_recorded_at"],
            next_required_step="Run record-weather-forecasts for mapped stations.",
        )
        row["details"] = {
            "nws_user_agent_env_configured": bool(str(self.env.get("NWS_USER_AGENT") or "").strip()),
            "project_uses_builtin_nws_user_agent": True,
            "noaa_token_configured_optional": bool(str(self.env.get("NOAA_TOKEN") or "").strip()),
        }
        if not attempt:
            return row
        row["live_fetch_attempted"] = True
        try:
            mapping = StationMapper().resolve_station_code("KNYC")
            rows = self._weather().hourly_forecast_snapshot_rows(mapping)
            row["live_fetch_succeeded"] = bool(rows)
            row["live_fetch_result_count"] = len(rows)
            row["next_required_step"] = "Forecast fetch path works; keep recorder running for no-lookahead feature snapshots."
        except Exception as exc:
            row["last_error_category"] = _error_category(exc)
        return row

    def _nws_climate_reports(self, attempt: bool) -> dict[str, Any]:
        table = self._table_stats("nws_daily_climate_reports", "created_at")
        row = self._base_component(
            "NWS daily climate reports / settlement labels",
            required_env_vars=[],
            live_fetch_implemented=True,
            db_table_available=table["available"],
            result_count=table["row_count"],
            latest_recorded_at=table["latest_recorded_at"],
            next_required_step="Run build-exact-settlements for dates with recorded weather orderbooks.",
        )
        row["details"] = {
            "nws_user_agent_env_configured": bool(str(self.env.get("NWS_USER_AGENT") or "").strip()),
            "project_uses_builtin_nws_user_agent": True,
        }
        if not attempt:
            return row
        row["live_fetch_attempted"] = True
        try:
            result = self._climate().fetch_report("KNYC", date.today() - timedelta(days=1), persist=False)
            row["live_fetch_succeeded"] = bool(result.report_url or result.parsed)
            row["next_required_step"] = "Climate report fetch path works; use exact settlement builder for target dates."
        except Exception as exc:
            row["last_error_category"] = _error_category(exc)
        return row

    def _table_component(self, component: str, table: str, latest_column: str, next_step: str) -> dict[str, Any]:
        stats = self._table_stats(table, latest_column)
        return self._base_component(
            component,
            required_env_vars=[],
            live_fetch_implemented=False,
            db_table_available=stats["available"],
            result_count=stats["row_count"],
            latest_recorded_at=stats["latest_recorded_at"],
            next_required_step=next_step if not stats["row_count"] else "Local table is present and populated.",
        )

    def _daily_evidence_reports(self) -> dict[str, Any]:
        reports = PROJECT_ROOT / "reports"
        patterns = ["daily_weather_evidence*.json", "daily_weather_evidence_range*.json", "daily_weather_evidence_drilldown*.json"]
        files: list[Path] = []
        for pattern in patterns:
            files.extend(reports.glob(pattern))
        latest = max((datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) for path in files), default=None)
        return self._base_component(
            "daily evidence reports",
            required_env_vars=[],
            live_fetch_implemented=False,
            result_count=len(files),
            latest_recorded_at=latest.isoformat() if latest else None,
            next_required_step="Run daily-weather-evidence or daily-weather-evidence-range after replay rows exist." if not files else "Daily evidence reports exist.",
        )

    def _trading_readiness_component(self) -> dict[str, Any]:
        row = self._base_component(
            "trading-readiness",
            required_env_vars=[],
            live_fetch_implemented=False,
            next_required_step="Keep trading-readiness blocked unless existing gates pass; source-smoke never promotes readiness.",
        )
        try:
            result = (self.trading_readiness or TradingReadiness(self.storage)).evaluate(last_days=7)
            row["live_fetch_succeeded"] = False
            row["result_count"] = 1
            row["latest_recorded_at"] = None
            row["details"] = {"status": result.status, "readiness_promotion": "none"}
        except Exception as exc:
            row["last_error_category"] = _error_category(exc)
        return row

    def _base_component(
        self,
        component: str,
        *,
        required_env_vars: list[str],
        live_fetch_implemented: bool,
        live_fetch_attempted: bool = False,
        live_fetch_succeeded: bool = False,
        result_count: int | None = None,
        db_table_available: bool | None = None,
        latest_recorded_at: str | None = None,
        last_error_category: str | None = None,
        next_required_step: str,
    ) -> dict[str, Any]:
        return {
            "component": component,
            "required_env_vars": required_env_vars,
            "env_configured": all(bool(str(self.env.get(name) or "").strip()) for name in required_env_vars),
            "live_fetch_implemented": live_fetch_implemented,
            "live_fetch_attempted": live_fetch_attempted,
            "live_fetch_succeeded": live_fetch_succeeded,
            "result_count": result_count,
            "db_table_available": db_table_available,
            "latest_recorded_at": latest_recorded_at,
            "last_error_category": last_error_category,
            "next_required_step": next_required_step,
            "details": {},
        }

    def _table_stats(self, table: str, latest_column: str) -> dict[str, Any]:
        try:
            inspector = inspect(self.storage.engine)
            if not inspector.has_table(table):
                return {"available": False, "row_count": 0, "latest_recorded_at": None}
            with self.storage.engine.connect() as conn:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                latest = conn.execute(text(f"SELECT MAX({latest_column}) FROM {table}")).scalar_one()
            return {
                "available": True,
                "row_count": int(count or 0),
                "latest_recorded_at": None if latest is None else str(latest),
            }
        except Exception:
            return {"available": False, "row_count": 0, "latest_recorded_at": None}

    def _latest_ticker(self, table: str, ticker_column: str, order_column: str) -> str | None:
        stats = self._table_stats(table, order_column)
        if not stats["available"]:
            return None
        try:
            with self.storage.engine.connect() as conn:
                value = conn.execute(
                    text(f"SELECT {ticker_column} FROM {table} WHERE {ticker_column} IS NOT NULL ORDER BY {order_column} DESC LIMIT 1")
                ).scalar_one_or_none()
            return None if value is None else str(value)
        except Exception:
            return None

    def _database_path_exists(self) -> bool:
        try:
            return self.cfg.sqlite_path.exists()
        except Exception:
            return False

    def _kalshi(self) -> Any:
        if self.kalshi_client is None:
            self.kalshi_client = KalshiClient(self.cfg)
        return self.kalshi_client

    def _weather(self) -> Any:
        if self.weather_client is None:
            self.weather_client = WeatherClient()
        return self.weather_client

    def _climate(self) -> Any:
        if self.climate_client is None:
            self.climate_client = NWSClimateReportClient(storage=self.storage)
        return self.climate_client

    def _export(self, summary: dict[str, Any], components: list[dict[str, Any]]) -> dict[str, str]:
        reports = PROJECT_ROOT / "reports"
        reports.mkdir(exist_ok=True)
        payload = {"summary": summary, "components": components}
        json_path = reports / "source_smoke.json"
        md_path = reports / "source_smoke.md"
        json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        md_path.write_text(_markdown(summary, components), encoding="utf-8")
        return {"json": str(json_path), "markdown": str(md_path)}


def _markdown(summary: dict[str, Any], components: list[dict[str, Any]]) -> str:
    lines = [
        "# Source Smoke",
        "",
        f"- Status: {summary.get('status')}",
        f"- Generated at: {summary.get('generated_at')}",
        f"- Research only: {summary.get('research_only')}",
        f"- Live trading enabled: {summary.get('live_trading_enabled')}",
        f"- Order placement enabled: {summary.get('order_placement_enabled')}",
        f"- Secrets printed: {summary.get('secrets_printed')}",
        "",
        "| Component | Env configured | Live attempted | Live succeeded | Rows/results | Latest recorded | Error | Next step |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for row in components:
        lines.append(
            "| {component} | {env} | {attempted} | {succeeded} | {count} | {latest} | {error} | {next_step} |".format(
                component=_md(row.get("component")),
                env=row.get("env_configured"),
                attempted=row.get("live_fetch_attempted"),
                succeeded=row.get("live_fetch_succeeded"),
                count="" if row.get("result_count") is None else row.get("result_count"),
                latest=_md(row.get("latest_recorded_at")),
                error=_md(row.get("last_error_category")),
                next_step=_md(row.get("next_required_step")),
            )
        )
    return "\n".join(lines) + "\n"


def _md(value: Any) -> str:
    text_value = "" if value is None else str(value)
    return text_value.replace("|", "/")


def _error_category(exc: Exception) -> str:
    return exc.__class__.__name__

