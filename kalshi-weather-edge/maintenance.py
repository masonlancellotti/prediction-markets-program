from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.recorded_backtester import RecordedOrderbookBacktester
from backtest.recorded_replay import RecordedOrderbookReplayBuilder
from backtest.edge_report import EdgeReportGenerator
from config import PROJECT_ROOT, settings
from data.kalshi_market_loader import is_likely_weather_market
from data.storage import Storage
from data.weather_settlement_loader import SETTLEMENT_VERSION, WeatherSettlementLoader, evaluate_contract_result
from data.weather_station_mapper import StationMapper
from parsing.market_parser import WeatherMarketParser, is_out_of_scope_combo_ticker
from parsing.weather_contract import PARSER_VERSION, WeatherContract
from research.liquidity_analysis import LiquidityAnalyzer
from research.market_making_analysis import MarketMakingAnalyzer
from research.signal_validation import SignalValidator
from research.trading_readiness import TradingReadiness


@dataclass(frozen=True)
class CommandResult:
    payload: dict[str, Any]

    def to_text(self) -> str:
        return json.dumps(self.payload, indent=2, default=str)


class ProjectMaintenance:
    def __init__(self, storage: Storage | None = None):
        self.storage = storage or Storage()

    def project_status(self) -> CommandResult:
        self.storage.init_db()
        counts = {table: self._count(table) for table in _key_tables()}
        latest_audit = self._latest_value("recorded_data_audits", "verdict")
        reports = sorted((PROJECT_ROOT / "reports").glob("edge_report_*.md"), reverse=True)
        latest_snapshot = self._latest_value("orderbook_snapshots_live", "ts")
        latest_trade = self._latest_value("historical_trades", "ts")
        stale_runs = self._count_where("backtest_runs", "COALESCE(is_stale, 0) = 1")
        clean_runs = self._count_where("backtest_runs", "COALESCE(is_stale, 0) = 0")
        clean_sweeps = self._count_where(
            "recorded_strategy_sweeps",
            f"COALESCE(is_stale, 0) = 0 AND COALESCE(parser_version,'') = '{PARSER_VERSION}' AND COALESCE(settlement_version,'') = '{SETTLEMENT_VERSION}'"
        )
        stale_sweeps = self._count_where("recorded_strategy_sweeps", "COALESCE(is_stale, 0) = 1")
        now = datetime.now(timezone.utc)
        recent_warning = None
        if latest_snapshot:
            parsed = _parse_ts(latest_snapshot)
            if parsed and now - parsed > timedelta(minutes=30):
                recent_warning = "No orderbook snapshot in 30+ minutes; recorder may be stopped."
        trade_print_warning = None
        if latest_trade:
            parsed_trade = _parse_ts(latest_trade)
            if parsed_trade and now - parsed_trade > timedelta(hours=4):
                trade_print_warning = "No trade print recorded in 4+ hours; market-making fill evidence may be stale."
        mm_summary_warning = _market_making_summary_warning()
        payload = {
            "docs": str(PROJECT_ROOT / "docs"),
            "database": settings.sqlite_path,
            "counts": counts,
            "latest_audit_verdict": latest_audit,
            "latest_edge_report": str(reports[0]) if reports else None,
            "latest_recommendation": self._latest_sweep_recommendation(),
            "latest_orderbook_snapshot": latest_snapshot,
            "latest_trade_print": latest_trade,
            "recorder_warning": recent_warning,
            "trade_print_warning": trade_print_warning,
            "market_making_summary_warning": mm_summary_warning,
            "stale_backtest_runs": stale_runs,
            "clean_backtest_runs": clean_runs,
            "stale_recorded_sweeps": stale_sweeps,
            "clean_recorded_sweeps": clean_sweeps,
            "stale_strategy_sweeps": stale_sweeps,
            "clean_strategy_sweeps_current_version": clean_sweeps,
            "parser_version": PARSER_VERSION,
            "settlement_version": SETTLEMENT_VERSION,
            "warning": "Read docs/CODEX_HANDOFF.md before trusting or changing strategy output.",
        }
        return CommandResult(payload)

    def reparse_contracts(self, weather_only: bool = True) -> CommandResult:
        self.storage.init_db()
        frame = self.storage.fetch_table("markets", limit=500000)
        parser = WeatherMarketParser()
        total = 0
        parsed = 0
        range_buckets = 0
        unknown = 0
        for _, row in frame.iterrows():
            market = row.get("payload")
            if not isinstance(market, dict):
                continue
            if weather_only and not is_likely_weather_market(market):
                continue
            total += 1
            contract = parser.parse(market)
            self.storage.save_parsed_contract(contract)
            parsed += 1
            if contract.contract_type == "range_bucket":
                range_buckets += 1
            if contract.contract_type == "unknown":
                unknown += 1
        return CommandResult(
            {
                "markets_considered": total,
                "contracts_parsed": parsed,
                "range_bucket_contracts": range_buckets,
                "unknown_contracts": unknown,
                "parser_version": PARSER_VERSION,
            }
        )

    def rebuild_settlement_labels(self, start: date | None = None, end: date | None = None, market_ticker: str | None = None) -> CommandResult:
        before = self._settlement_yes_results()
        result = WeatherSettlementLoader(storage=self.storage).build_settlements(start=start, end=end, market_ticker=market_ticker)
        after = self._settlement_yes_results()
        changed = []
        for ticker, old in before.items():
            if ticker in after and after[ticker] != old:
                changed.append({"market_ticker": ticker, "old_yes_result": old, "new_yes_result": after[ticker]})
        self._export_changed_labels(changed)
        return CommandResult({**result.to_dict(), "yes_results_changed": len(changed), "settlement_version": SETTLEMENT_VERSION})

    def validate_settlement_labels(self, weather_only: bool = True) -> CommandResult:
        labels = self.storage.fetch_table("settlement_labels", limit=500000)
        if labels.empty:
            return CommandResult({"total_labels": 0, "errors": ["no settlement labels"]})
        errors: list[dict[str, Any]] = []
        for _, row in labels.iterrows():
            if row.get("yes_result") is None:
                errors.append({"market_ticker": row.get("market_ticker"), "reason": "missing yes_result"})
            if str(row.get("contract_type") or "") == "range_bucket" and (pd.isna(row.get("range_low")) or pd.isna(row.get("range_high"))):
                errors.append({"market_ticker": row.get("market_ticker"), "reason": "range bucket missing bounds"})
        return CommandResult(
            {
                "total_labels": int(len(labels)),
                "primary_confidence_labels": int((pd.to_numeric(labels["confidence"], errors="coerce").fillna(0) >= 0.85).sum()),
                "range_bucket_labels": int(labels.get("contract_type", pd.Series(dtype=object)).eq("range_bucket").sum()),
                "errors": errors[:100],
                "excluded_from_primary": int((pd.to_numeric(labels["confidence"], errors="coerce").fillna(0) < 0.85).sum()),
            }
        )

    def validate_settlement_sources(self, fix: bool = True) -> CommandResult:
        self.storage.init_db()
        labels = self.storage.fetch_table("settlement_labels", limit=500000)
        if labels.empty:
            return CommandResult({"total_labels": 0})
        exact_mask = labels.get("exact_source_available", pd.Series(dtype=int)).fillna(0).astype(int).eq(1)
        source = labels.get("source", pd.Series(dtype=object)).fillna("")
        exact_type = labels.get("exact_source_type", pd.Series(dtype=object)).fillna("")
        inconsistent = labels[exact_mask & (source.ne(exact_type))]
        if fix and not inconsistent.empty:
            self.storage.execute(
                """
                UPDATE settlement_labels
                SET source = COALESCE(NULLIF(exact_source_type, ''), 'nws_daily_climate_report'),
                    primary_source_type = COALESCE(NULLIF(exact_source_type, ''), 'nws_daily_climate_report')
                WHERE COALESCE(exact_source_available, 0) = 1
                """
            )
        diffs = labels[pd.to_numeric(labels.get("exact_vs_fallback_diff", pd.Series(dtype=float)), errors="coerce").fillna(0).abs() > 0.01]
        return CommandResult(
            {
                "total_labels": int(len(labels)),
                "exact_nws_labels": int(exact_mask.sum()),
                "fallback_labels": int((~exact_mask).sum()),
                "inconsistent_source_metadata": int(len(inconsistent)),
                "fixed_inconsistent_source_metadata": int(len(inconsistent)) if fix else 0,
                "exact_and_fallback_differ": int(len(diffs)),
                "excluded_from_primary": int((pd.to_numeric(labels["confidence"], errors="coerce").fillna(0) < 0.85).sum()),
            }
        )

    def mark_stale_runs(self, reason: str = "Invalidated by v2 range/bucket contract semantics fix.") -> CommandResult:
        self.storage.init_db()
        self.storage.execute(
            """
            UPDATE backtest_runs
            SET is_stale = 1, stale_reason = :reason
            WHERE COALESCE(parser_version, '') != :parser_version
               OR COALESCE(settlement_version, '') != :settlement_version
            """,
            {"reason": reason, "parser_version": PARSER_VERSION, "settlement_version": SETTLEMENT_VERSION},
        )
        self.storage.execute(
            """
            UPDATE recorded_strategy_sweeps
            SET is_stale = 1, stale_reason = :reason
            WHERE COALESCE(parser_version, '') != :parser_version
               OR COALESCE(settlement_version, '') != :settlement_version
            """,
            {"reason": reason, "parser_version": PARSER_VERSION, "settlement_version": SETTLEMENT_VERSION},
        )
        return CommandResult(
            {
                "stale_backtest_runs": self._count_where("backtest_runs", "COALESCE(is_stale, 0) = 1"),
                "stale_recorded_sweeps": self._count_where("recorded_strategy_sweeps", "COALESCE(is_stale, 0) = 1"),
                "reason": reason,
            }
        )

    def diagnose_settlement_skips(self, last_days: int = 7) -> CommandResult:
        contracts = self._latest_contracts(last_days=last_days)
        labels = set(self.storage.fetch_sql("SELECT market_ticker FROM settlement_labels")["market_ticker"].astype(str)) if self._count("settlement_labels") else set()
        skipped = contracts[~contracts["market_ticker"].isin(labels)].copy() if not contracts.empty else contracts
        if skipped.empty:
            return CommandResult({"total_skipped": 0, "verdict": "No skipped settlement labels in selected window."})
        for column in ["city", "station_code", "variable_type", "contract_type", "local_date", "parse_confidence"]:
            if column not in skipped.columns:
                skipped[column] = None
        skipped["reason"] = skipped.apply(_skip_reason, axis=1)
        payload = {
            "total_skipped": int(len(skipped)),
            "skipped_by_city": skipped.get("city", pd.Series(dtype=object)).fillna("unknown").value_counts().to_dict(),
            "skipped_by_station": skipped.get("station_code", pd.Series(dtype=object)).fillna("missing").value_counts().to_dict(),
            "skipped_by_reason": skipped["reason"].value_counts().to_dict(),
            "top_skipped": skipped[["market_ticker", "city", "station_code", "variable_type", "contract_type", "local_date", "reason", "parse_confidence"]].head(20).to_dict("records"),
            "suggested_fixes": _suggested_skip_fixes(skipped),
        }
        return CommandResult(payload)

    def debug_city_settlements(self, city: str, last_days: int = 10) -> CommandResult:
        contracts = self._latest_contracts(last_days=last_days)
        if contracts.empty:
            return CommandResult({"city": city, "markets": []})
        for column in ["market_ticker", "title", "city", "station_code", "local_date", "variable_type", "contract_type"]:
            if column not in contracts.columns:
                contracts[column] = None
        mask = contracts.get("city", pd.Series(dtype=object)).fillna("").str.lower().str.contains(city.lower()) | contracts["market_ticker"].str.lower().str.contains(city.lower())
        rows = contracts[mask].copy()
        labels = self.storage.fetch_table("settlement_labels", limit=500000)
        if not labels.empty:
            rows = rows.merge(labels[["market_ticker", "source", "primary_source_type", "confidence", "warnings"]], on="market_ticker", how="left")
        for column in ["source", "primary_source_type", "confidence", "warnings"]:
            if column not in rows.columns:
                rows[column] = None
        return CommandResult(
            {
                "city_query": city,
                "markets_found": int(len(rows)),
                "markets": rows[["market_ticker", "title", "city", "station_code", "local_date", "variable_type", "contract_type", "source", "primary_source_type", "confidence", "warnings"]].head(100).to_dict("records") if not rows.empty else [],
                "note": "For Austin/AUS, expected default station is KAUS unless Kalshi rules explicitly name another station.",
            }
        )

    def validate_orderbook_depths(self, last_days: int = 3) -> CommandResult:
        start = (date.today() - timedelta(days=max(last_days, 1))).isoformat()
        frame = self.storage.fetch_sql(
            """
            SELECT market_ticker, ts, yes_best_bid, yes_best_ask, depth_yes_bid_1, depth_yes_ask_1,
                   total_yes_bid_depth, total_no_bid_depth, yes_bids_json, no_bids_json, raw_json
            FROM orderbook_snapshots_live
            WHERE date(ts) >= :start
            ORDER BY MAX(COALESCE(depth_yes_bid_1,0), COALESCE(depth_yes_ask_1,0), COALESCE(total_yes_bid_depth,0), COALESCE(total_no_bid_depth,0)) DESC
            LIMIT 50
            """,
            {"start": start},
        )
        if frame.empty:
            return CommandResult({"rows_checked": 0, "verdict": "No orderbook rows in selected window."})
        max_depth = float(frame[["depth_yes_bid_1", "depth_yes_ask_1", "total_yes_bid_depth", "total_no_bid_depth"]].fillna(0).max().max())
        extreme_10k = int(((frame[["depth_yes_bid_1", "depth_yes_ask_1", "total_yes_bid_depth", "total_no_bid_depth"]].fillna(0)) > 10000).any(axis=1).sum())
        rows = []
        for _, row in frame.iterrows():
            rows.append(
                {
                    "market_ticker": row["market_ticker"],
                    "ts": row["ts"],
                    "yes_best_bid": row["yes_best_bid"],
                    "yes_best_ask": row["yes_best_ask"],
                    "depth_yes_bid_1": row["depth_yes_bid_1"],
                    "depth_yes_ask_1": row["depth_yes_ask_1"],
                    "total_yes_bid_depth": row["total_yes_bid_depth"],
                    "total_no_bid_depth": row["total_no_bid_depth"],
                    "raw_yes_bids_snippet": _json_snippet(row.get("yes_bids_json")),
                    "raw_no_bids_snippet": _json_snippet(row.get("no_bids_json")),
                }
            )
        verdict = "Depth fields appear to be Kalshi contract quantities; queue position remains unknown."
        if extreme_10k:
            verdict = "Extreme depth values detected. Treat passive fill estimates cautiously until raw rows are manually reviewed."
        return CommandResult({"rows_checked": int(len(frame)), "max_depth": max_depth, "rows_over_10000": extreme_10k, "top_depth_rows": rows, "verdict": verdict})

    def collector_health(self, last_hours: int = 24) -> CommandResult:
        self.storage.init_db()
        since = (datetime.now(timezone.utc) - timedelta(hours=last_hours)).strftime("%Y-%m-%d %H:%M:%S")
        frame = self.storage.fetch_sql("SELECT * FROM orderbook_snapshots_live WHERE ts >= :since", {"since": since})
        last_ts = self._latest_value("orderbook_snapshots_live", "ts")
        state_frame = self.storage.fetch_sql(
            """
            SELECT * FROM collector_state
            WHERE collector_name LIKE 'orderbook_recorder%'
            ORDER BY updated_at DESC, id DESC
            LIMIT 10
            """
        )
        now = datetime.now(timezone.utc)
        state = {}
        state_rows: list[dict] = []
        if not state_frame.empty:
            state_rows = state_frame.to_dict(orient="records")
            for candidate in state_rows:
                status = str(candidate.get("status") or "").upper()
                updated = _parse_ts(candidate.get("updated_at"))
                if status != "STOPPED" and updated and now - updated < timedelta(minutes=10):
                    state = candidate
                    break
            if not state:
                state = state_rows[0]
        parsed_last = _parse_ts(last_ts)
        seconds_since_last = (now - parsed_last).total_seconds() if parsed_last else None
        expected_interval = settings.orderbook_record_interval_seconds
        state_heartbeat_ts = _parse_ts(state.get("last_heartbeat_at")) if state else None
        state_updated = _parse_ts(state.get("updated_at")) if state else None
        heartbeat_age = (now - state_heartbeat_ts).total_seconds() if state_heartbeat_ts else None
        state_update_age = (now - state_updated).total_seconds() if state_updated else None
        stale_heartbeat = bool(
            state
            and str(state.get("status") or "").upper() not in {"STOPPED", ""}
            and (heartbeat_age is None or heartbeat_age > max(expected_interval * 8, 600))
        )
        process_appears_stale = bool(
            seconds_since_last is None
            or seconds_since_last > max(expected_interval * 2, 120)
            or stale_heartbeat
        )
        status = "BROKEN_NO_RECENT_SNAPSHOTS"
        recommendation = "Recorder appears stuck. Restart with pure orderbook mode: python main.py record-orderbooks --weather-only --interval-seconds 30"
        if parsed_last:
            if now - parsed_last < timedelta(minutes=5):
                status = "HEALTHY"
                recommendation = "HEALTHY: orderbook snapshots are recent."
            elif now - parsed_last < timedelta(minutes=30):
                status = "DEGRADED_BUT_COLLECTING"
                recommendation = "DEGRADED_BUT_COLLECTING: snapshots are recent enough, but check the recorder window."
        missing_intervals = []
        if not frame.empty:
            by_hour = pd.to_datetime(frame["ts"]).dt.floor("h").value_counts().sort_index()
            missing_intervals = [str(idx) for idx, count in by_hour.items() if int(count) == 0]
        return CommandResult(
            {
                "last_hours": last_hours,
                "snapshots_collected": int(len(frame)),
                "unique_markets": int(frame["market_ticker"].nunique()) if not frame.empty else 0,
                "snapshots_per_hour": int(len(frame) / max(last_hours, 1)),
                "last_snapshot_time": last_ts,
                "seconds_since_last_snapshot": seconds_since_last,
                "expected_interval_seconds": expected_interval,
                "process_appears_stale": process_appears_stale,
                "collector_state": {
                    "collector_name": state.get("collector_name"),
                    "status": state.get("status"),
                    "current_task": state.get("current_task"),
                    "last_heartbeat_at": state.get("last_heartbeat_at"),
                    "heartbeat_age_seconds": heartbeat_age,
                    "state_update_age_seconds": state_update_age,
                    "stale_heartbeat": stale_heartbeat,
                    "cycles_completed": state.get("cycles_completed"),
                    "snapshots_this_run": state.get("snapshots_this_run"),
                    "markets_tracked": state.get("markets_tracked"),
                    "error_message": state.get("error_message"),
                },
                "recent_collector_states": [
                    {
                        "collector_name": row.get("collector_name"),
                        "status": row.get("status"),
                        "current_task": row.get("current_task"),
                        "updated_at": row.get("updated_at"),
                        "markets_tracked": row.get("markets_tracked"),
                    }
                    for row in state_rows[:5]
                ],
                "missing_intervals": missing_intervals[:20],
                "kalshi_429_count": "not persisted; check current process logs",
                "nws_timeout_count": "not persisted; check current process logs",
                "settlement_skipped_count": len(self.diagnose_settlement_skips(last_days=7).payload.get("top_skipped", [])),
                "recommendation": recommendation,
                "health": status,
            }
        )

    def weather_recorder_health(self, last_hours: int = 24) -> CommandResult:
        self.storage.init_db()
        since = (datetime.now(timezone.utc) - timedelta(hours=last_hours)).strftime("%Y-%m-%d %H:%M:%S")
        obs = self.storage.fetch_sql("SELECT * FROM weather_observation_snapshots_live WHERE ts_recorded >= :since", {"since": since})
        forecasts = self.storage.fetch_sql("SELECT * FROM weather_forecast_snapshots_live WHERE ts_recorded >= :since", {"since": since})
        station_map = self.storage.fetch_table("active_weather_station_map", limit=100000)
        recent_station_map = station_map
        station_mapping_scope = "all_mappings"
        if not station_map.empty and "updated_at" in station_map:
            updated = pd.to_datetime(station_map["updated_at"], errors="coerce", utc=True)
            recent = station_map[updated >= pd.Timestamp(since, tz="UTC")]
            if not recent.empty:
                recent_station_map = recent
                station_mapping_scope = "updates_within_health_window"
            elif updated.notna().any():
                latest_update = updated.max()
                recent_station_map = station_map[updated >= latest_update - pd.Timedelta(minutes=15)]
                station_mapping_scope = "latest_update_batch"
        obs_last = _last_by_station(obs, "ts_recorded")
        fcst_last = _last_by_station(forecasts, "ts_recorded")
        mapped_stations = set(recent_station_map.get("station_code", pd.Series(dtype=object)).dropna().astype(str)) if not recent_station_map.empty else set()
        observed_stations = set(obs.get("station_code", pd.Series(dtype=object)).dropna().astype(str)) if not obs.empty else set()
        forecasted_stations = set(forecasts.get("station_code", pd.Series(dtype=object)).dropna().astype(str)) if not forecasts.empty else set()
        low_conf = []
        if not recent_station_map.empty and "mapping_confidence" in recent_station_map:
            low_conf = recent_station_map[pd.to_numeric(recent_station_map["mapping_confidence"], errors="coerce").fillna(0) < 0.75][["market_ticker", "city", "station_code", "mapping_confidence", "warnings"]].head(50).to_dict("records")
        health = "HEALTHY"
        recommendation = "HEALTHY: observations and forecasts are recording."
        if obs.empty and forecasts.empty:
            health = "BROKEN_NO_RECENT_WEATHER"
            recommendation = "No recent weather rows. Start: python main.py record-weather-observations --from-active-markets --interval-minutes 5"
        elif obs.empty:
            health = "BROKEN_NO_RECENT_WEATHER"
            recommendation = "Observations are not recording. Start record-weather-observations in a separate terminal."
        elif forecasts.empty:
            health = "FORECASTS_NOT_RECORDING"
            recommendation = "Forecasts are not recording. Start record-weather-forecasts in a separate terminal."
        missing_observations = mapped_stations - observed_stations
        missing_forecasts = mapped_stations - forecasted_stations
        if health == "HEALTHY" and mapped_stations and missing_observations:
            health = "DEGRADED_BUT_COLLECTING"
            recommendation = "Some mapped stations have no recent observations; check station mappings and NWS availability."
        elif health == "HEALTHY" and mapped_stations and missing_forecasts:
            health = "FORECAST_STATION_GAPS"
            recommendation = "Some mapped stations have no recent forecasts; check station mappings and forecast recorder logs."
        station_mapping_status = "LOW_CONFIDENCE_MAPPINGS_PRESENT" if low_conf else "OK"
        return CommandResult(
            {
                "last_hours": last_hours,
                "observation_rows_collected": int(len(obs)),
                "forecast_rows_collected": int(len(forecasts)),
                "unique_stations_observed": int(len(observed_stations)),
                "unique_stations_forecasted": int(len(forecasted_stations)),
                "last_observation_timestamp_by_station": obs_last,
                "last_forecast_timestamp_by_station": fcst_last,
                "stations_missing_observations": sorted(missing_observations),
                "stations_missing_forecasts": sorted(missing_forecasts),
                "station_mapping_confidence_issues": low_conf,
                "station_mapping_status": station_mapping_status,
                "station_mapping_scope": station_mapping_scope,
                "nws_timeout_or_error_counts": "not persisted by weather recorder; check current recorder process logs",
                "recommendation": recommendation,
                "health": health,
            }
        )

    def weather_ops_status(self, last_days: int = 7) -> CommandResult:
        """Operator-facing consolidated health snapshot.

        Read-only. Surfaces collector/weather recorder freshness, replay
        coverage, settlement-label availability, future-mid validation strength,
        paper-market-making evidence availability, the existing readiness gate
        result, and the exact next PowerShell commands to run.

        This command does not weaken any readiness gate or no-lookahead check;
        every verdict comes from the underlying readiness/health helpers.
        """
        self.storage.init_db()
        now = datetime.now(timezone.utc)
        since_iso = (now - timedelta(days=max(last_days, 1))).strftime("%Y-%m-%d %H:%M:%S")
        next_commands: list[str] = []
        blockers: list[str] = []

        db_path = settings.sqlite_path
        db_exists = Path(db_path).exists()
        markets_count = self._count("markets")
        parsed_contracts_count = self._count("parsed_contracts")
        if not db_exists or markets_count == 0:
            blockers.append("DB schema is empty: run init-db and load-markets before recording.")
            next_commands.append("python main.py init-db")
            next_commands.append("python main.py load-markets")

        latest_orderbook = self._latest_value("orderbook_snapshots_live", "ts")
        latest_orderbook_dt = _parse_ts(latest_orderbook)
        orderbook_age_seconds = (now - latest_orderbook_dt).total_seconds() if latest_orderbook_dt else None
        orderbook_count_since = self._count_in_window("orderbook_snapshots_live", "ts", since_iso)
        orderbook_status = "MISSING"
        if latest_orderbook_dt is None:
            blockers.append("No orderbook snapshots have ever been recorded.")
            next_commands.append("python main.py record-orderbooks --weather-only --interval-seconds 30 --duration-hours 12")
        else:
            if orderbook_age_seconds is None or orderbook_age_seconds > 1800:
                orderbook_status = "STALE"
                blockers.append(
                    f"Orderbook recorder appears stopped (last snapshot {orderbook_age_seconds/60:.1f} min ago)."
                    " Restart record-orderbooks."
                )
                next_commands.append("python main.py record-orderbooks --weather-only --interval-seconds 30 --duration-hours 12")
            elif orderbook_age_seconds > 300:
                orderbook_status = "DEGRADED"
            else:
                orderbook_status = "FRESH"

        latest_obs = self._latest_value("weather_observation_snapshots_live", "ts_recorded")
        latest_obs_dt = _parse_ts(latest_obs)
        obs_age_seconds = (now - latest_obs_dt).total_seconds() if latest_obs_dt else None
        obs_count_since = self._count_in_window("weather_observation_snapshots_live", "ts_recorded", since_iso)
        obs_status = "MISSING"
        if latest_obs_dt is None:
            blockers.append("No weather observations have ever been recorded.")
            next_commands.append("python main.py record-weather-observations --from-active-markets --interval-minutes 5 --duration-hours 12")
        else:
            if obs_age_seconds is None or obs_age_seconds > 3600:
                obs_status = "STALE"
                blockers.append(
                    f"Weather observation recorder appears stopped (last row {obs_age_seconds/60:.1f} min ago)."
                )
                next_commands.append("python main.py record-weather-observations --from-active-markets --interval-minutes 5 --duration-hours 12")
            elif obs_age_seconds > 900:
                obs_status = "DEGRADED"
            else:
                obs_status = "FRESH"

        latest_fcst = self._latest_value("weather_forecast_snapshots_live", "ts_recorded")
        latest_fcst_dt = _parse_ts(latest_fcst)
        fcst_age_seconds = (now - latest_fcst_dt).total_seconds() if latest_fcst_dt else None
        fcst_count_since = self._count_in_window("weather_forecast_snapshots_live", "ts_recorded", since_iso)
        fcst_status = "MISSING"
        if latest_fcst_dt is None:
            blockers.append("No weather forecasts have ever been recorded.")
            next_commands.append("python main.py record-weather-forecasts --from-active-markets --interval-minutes 30 --duration-hours 12")
        else:
            if fcst_age_seconds is None or fcst_age_seconds > 7200:
                fcst_status = "STALE"
                blockers.append(
                    f"Weather forecast recorder appears stopped (last row {fcst_age_seconds/60:.1f} min ago)."
                )
                next_commands.append("python main.py record-weather-forecasts --from-active-markets --interval-minutes 30 --duration-hours 12")
            elif fcst_age_seconds > 3600:
                fcst_status = "DEGRADED"
            else:
                fcst_status = "FRESH"

        replay_24h_window = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        replay_rows_1d = self._count_in_window("recorded_orderbook_replay_snapshots", "ts", replay_24h_window)
        replay_rows_window = self._count_in_window("recorded_orderbook_replay_snapshots", "ts", since_iso)
        replay_status = "MISSING"
        if replay_rows_window > 0:
            replay_status = "PRESENT_RECENT" if replay_rows_1d > 0 else "PRESENT_OLDER"
        else:
            blockers.append(
                "No recorded replay rows. Need settlement labels first, then build replay."
                f" Settlement labels in DB: {self._count('settlement_labels')}."
            )
            if self._count("settlement_labels") == 0:
                next_commands.append("python main.py build-exact-settlements")
            next_commands.append(f"python main.py build-recorded-replay --last-days {last_days} --min-settlement-confidence 0.85")

        primary_labels = self._count_where("settlement_labels", "confidence >= 0.85")
        all_labels = self._count("settlement_labels")
        labels_status = "MISSING"
        if primary_labels > 0:
            labels_status = "PRIMARY_CONFIDENCE_AVAILABLE"
        elif all_labels > 0:
            labels_status = "ONLY_LOW_CONFIDENCE"
            blockers.append(
                f"{all_labels} settlement labels exist, but none reach the >=0.85 confidence gate."
                " Rerun build-exact-settlements once NWS climate reports are loaded."
            )
            next_commands.append("python main.py build-exact-settlements")
        else:
            blockers.append("No settlement labels exist. Build-exact-settlements after weather observations exist.")

        future_mid_strength = "UNKNOWN"
        future_mid_observations = 0
        future_mid_beat_rate = None
        try:
            trades_frame = self.storage.fetch_sql(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN beat_30m = 1 THEN 1 ELSE 0 END) AS beat30,
                       SUM(CASE WHEN beat_30m IS NOT NULL THEN 1 ELSE 0 END) AS observed30
                FROM backtest_trades
                WHERE date(ts) >= :since
                """,
                {"since": since_iso[:10]},
            )
        except Exception:
            trades_frame = pd.DataFrame()
        if not trades_frame.empty:
            total_signals = int(trades_frame.iloc[0].get("total") or 0)
            observed30 = int(trades_frame.iloc[0].get("observed30") or 0)
            beat30 = int(trades_frame.iloc[0].get("beat30") or 0)
            future_mid_observations = observed30
            if observed30 == 0:
                future_mid_strength = "NO_OBSERVATIONS" if total_signals == 0 else "NO_FUTURE_MID_COMPARISONS"
            else:
                future_mid_beat_rate = beat30 / observed30
                if observed30 < 10:
                    future_mid_strength = "TOO_FEW_OBSERVATIONS"
                elif future_mid_beat_rate >= 0.55:
                    future_mid_strength = "STRONG"
                elif future_mid_beat_rate >= 0.50:
                    future_mid_strength = "MARGINAL"
                else:
                    future_mid_strength = "WEAK"
        if future_mid_strength in {"WEAK", "TOO_FEW_OBSERVATIONS", "NO_FUTURE_MID_COMPARISONS", "NO_OBSERVATIONS", "UNKNOWN"}:
            blockers.append(
                f"Future-mid validation is {future_mid_strength}"
                f" (observations={future_mid_observations}, beat_rate={future_mid_beat_rate})."
                " Do not rely on signals as executable until validation is STRONG with >=10 observations."
            )

        paper_quote_count = self._count("paper_market_making_quotes")
        paper_filled_count = self._count_where("paper_market_making_quotes", "status = 'FILLED'")
        if paper_quote_count == 0:
            paper_evidence_status = "NO_PAPER_QUOTES"
            blockers.append("No paper market-making evidence yet. Run paper-market-making-basket to gather paper quotes.")
            next_commands.append(
                "python main.py paper-market-making-basket --last-days 1 --search-max-markets 100 --max-targets 5 --duration-minutes 60"
            )
        elif paper_filled_count == 0:
            paper_evidence_status = "QUOTES_ONLY_NO_FILLS"
            blockers.append(
                f"{paper_quote_count} paper quotes recorded but no fills yet."
                " Keep paper-market-making-basket running across multiple market-side candidates."
            )
        elif paper_filled_count < 20:
            paper_evidence_status = "FILLS_TOO_FEW"
        else:
            paper_evidence_status = "FILLS_RECORDED"

        try:
            readiness = TradingReadiness(self.storage).evaluate(last_days=last_days)
            readiness_status = readiness.status
            readiness_message = readiness.message
            readiness_reasons = readiness.reasons
            readiness_next = readiness.next_command
            blockers.extend(reason for reason in readiness_reasons if reason not in blockers)
            if readiness_next and readiness_next not in next_commands:
                next_commands.append(readiness_next)
        except Exception as exc:
            readiness_status = "READINESS_EVALUATION_FAILED"
            readiness_message = f"trading-readiness evaluator raised {type(exc).__name__}: {exc}"
            readiness_reasons = [readiness_message]
            readiness_next = "python main.py trading-readiness --last-days 7"
            blockers.append(readiness_message)
            next_commands.append(readiness_next)

        statuses = {orderbook_status, obs_status, fcst_status}
        verdict = "RED_BROKEN_OR_NO_DATA"
        if "MISSING" not in statuses and "STALE" not in statuses:
            if replay_status == "PRESENT_RECENT" and labels_status == "PRIMARY_CONFIDENCE_AVAILABLE":
                if readiness_status in {"PAPER_READY_SPECIFIC_STRATEGY", "TINY_LIVE_READY_SPECIFIC_STRATEGY"}:
                    verdict = "GREEN_PAPER_READY"
                elif readiness_status == "RESEARCH_READY_MORE_DATA_NEEDED":
                    verdict = "AMBER_RESEARCH_PROGRESS"
                else:
                    verdict = "AMBER_DATA_OK_NO_EDGE"
            elif "DEGRADED" not in statuses:
                verdict = "AMBER_COLLECTING_NEEDS_LABELS_OR_REPLAY"
            else:
                verdict = "AMBER_COLLECTING_DEGRADED"

        deduped_next: list[str] = []
        seen = set()
        for cmd in next_commands:
            if cmd and cmd not in seen:
                seen.add(cmd)
                deduped_next.append(cmd)

        payload = {
            "verdict": verdict,
            "now": now.isoformat(),
            "database": {
                "path": db_path,
                "exists": db_exists,
                "markets": markets_count,
                "parsed_contracts": parsed_contracts_count,
            },
            "orderbook_recorder": {
                "status": orderbook_status,
                "latest_snapshot_at": str(latest_orderbook) if latest_orderbook else None,
                "age_seconds": orderbook_age_seconds,
                "rows_in_last_days": orderbook_count_since,
            },
            "weather_observations": {
                "status": obs_status,
                "latest_recorded_at": str(latest_obs) if latest_obs else None,
                "age_seconds": obs_age_seconds,
                "rows_in_last_days": obs_count_since,
            },
            "weather_forecasts": {
                "status": fcst_status,
                "latest_recorded_at": str(latest_fcst) if latest_fcst else None,
                "age_seconds": fcst_age_seconds,
                "rows_in_last_days": fcst_count_since,
            },
            "replay_coverage": {
                "status": replay_status,
                "rows_last_24h": replay_rows_1d,
                "rows_last_days_window": replay_rows_window,
                "window_days": last_days,
            },
            "settlement_labels": {
                "status": labels_status,
                "primary_confidence_count": primary_labels,
                "total_count": all_labels,
            },
            "future_mid_validation": {
                "strength": future_mid_strength,
                "observed_signals_with_30m_mid": future_mid_observations,
                "beat_rate_30m": future_mid_beat_rate,
            },
            "paper_market_making_evidence": {
                "status": paper_evidence_status,
                "total_paper_quotes": paper_quote_count,
                "filled_paper_quotes": paper_filled_count,
            },
            "trading_readiness": {
                "status": readiness_status,
                "message": readiness_message,
                "reasons": readiness_reasons,
                "next_command_from_readiness": readiness_next,
            },
            "blockers": blockers,
            "next_commands": deduped_next,
            "runbook_commands": _weather_runbook_commands(),
            "runbook_doc": "docs/WEATHER_OPERATOR_RUNBOOK.md",
            "safety": {
                "live_trading": "DISABLED — this command and the project never place real orders.",
                "no_lookahead_protection": "Unchanged — readiness/replay gates evaluated here unmodified.",
                "midpoint_as_executable": "NOT_USED — future-mid is research signal only.",
                "real_money_justification": "NONE — paper trading only.",
            },
        }
        return CommandResult(payload)

    def weather_data_audit(self, search_roots: list[Path] | None = None) -> CommandResult:
        """Read-only DB discovery and weather data lineage audit."""
        active_db_error = None
        try:
            active_db = settings.sqlite_path.resolve()
        except Exception as exc:
            active_db = None
            active_db_error = f"{type(exc).__name__}: {exc}"

        env_db_paths = _configured_db_env_paths(active_db)
        roots = _safe_db_search_roots(active_db, search_roots)
        db_paths = _discover_db_files(roots, active_db)
        db_reports = [_inspect_sqlite_db(path, active_db) for path in db_paths]
        db_reports.sort(key=lambda row: (not row.get("is_active_configured_db", False), str(row.get("path") or "")))

        best_historical = _best_historical_db(db_reports)
        freshest = _freshest_db(db_reports)
        likely_wrong_path, wrong_path_reasons = _wrong_path_status(active_db, db_reports, freshest)
        active_report = _find_db_report(db_reports, active_db)

        missing_expected_tables = []
        if active_report and active_report.get("sqlite_openable"):
            active_tables = set(active_report.get("relevant_tables") or [])
            missing_expected_tables = [table for table in _AUDIT_KNOWN_TABLES if table not in active_tables]
        elif active_db is not None:
            missing_expected_tables = list(_AUDIT_KNOWN_TABLES)

        blockers: list[str] = []
        if active_db_error:
            blockers.append(f"Configured DB path could not be resolved: {active_db_error}")
        if active_report is None:
            blockers.append("Configured active DB was not found in discovered DB files.")
        elif not active_report.get("sqlite_openable"):
            blockers.append("Configured active DB exists but could not be opened read-only as SQLite.")
        else:
            counts = active_report.get("row_counts") or {}
            if int(counts.get("settlement_labels") or 0) == 0:
                blockers.append("Active DB has zero settlement_labels rows.")
            if int(counts.get("recorded_orderbook_replay_snapshots") or 0) == 0:
                blockers.append("Active DB has zero recorded_orderbook_replay_snapshots rows.")
        blockers.extend(wrong_path_reasons)

        next_commands = [
            "python main.py weather-data-audit",
            "python main.py weather-ops-status --last-days 7",
            "python main.py trading-readiness --last-days 7",
        ]
        if active_report and active_report.get("sqlite_openable"):
            counts = active_report.get("row_counts") or {}
            if int(counts.get("settlement_labels") or 0) == 0:
                next_commands.append("python main.py build-exact-settlements")
            if int(counts.get("settlement_labels") or 0) > 0 and int(counts.get("recorded_orderbook_replay_snapshots") or 0) == 0:
                next_commands.append("python main.py build-recorded-replay --last-days 7 --min-settlement-confidence 0.85")
        next_commands.append("python main.py analyze-market-making --last-days 7")

        payload = {
            "active_configured_db": str(active_db) if active_db else None,
            "active_configured_db_error": active_db_error,
            "database_url_source": "environment" if os.getenv("DATABASE_URL") else "default",
            "configured_db_environment_paths": env_db_paths,
            "search_roots": [str(path) for path in roots],
            "discovered_db_count": len(db_reports),
            "databases": db_reports,
            "summary": {
                "active_configured_db": str(active_db) if active_db else None,
                "best_historical_db_candidate": best_historical,
                "freshest_db_candidate": freshest,
                "likely_wrong_path_detected": likely_wrong_path,
                "wrong_path_reasons": wrong_path_reasons,
                "missing_expected_tables": missing_expected_tables,
                "suspected_data_gap_ranges": _collect_gap_summaries(db_reports),
                "blockers": blockers,
                "next_commands": _dedupe_strings(next_commands),
            },
            "safety": {
                "read_only": True,
                "sqlite_open_mode": "mode=ro with PRAGMA query_only=ON",
                "no_database_mutation": True,
                "no_secret_values_printed": True,
                "no_trading_or_private_api": True,
            },
        }
        return CommandResult(payload)

    def weather_settlement_coverage(self, as_of: date | None = None, min_confidence: float = 0.85) -> CommandResult:
        """Read-only taxonomy for settlement label and replay coverage blockers."""
        self.storage.init_db()
        as_of = as_of or date.today()
        contracts = _latest_weather_contracts(self.storage)
        labels = _settlement_label_map(self.storage)
        climate = _climate_report_map(self.storage)
        observations = _observation_source_index(self.storage)
        orderbooks = _orderbook_snapshot_index(self.storage)
        replay_rows = _replay_snapshot_index(self.storage)
        active_station_map = _active_station_map(self.storage)

        blocker_counts: Counter[str] = Counter()
        station_counts: dict[str, Counter[str]] = defaultdict(Counter)
        date_counts: dict[str, Counter[str]] = defaultdict(Counter)
        market_type_counts: dict[str, Counter[str]] = defaultdict(Counter)
        maturity_counts: dict[str, Counter[str]] = defaultdict(Counter)
        examples: dict[str, list[str]] = defaultdict(list)
        exact_eligible = 0
        deterministic_buildable = 0
        not_yet_mature = 0
        already_labeled = 0
        high_confidence_labels = 0
        low_confidence_labels = 0
        labels_with_orderbooks = 0
        high_conf_labels_with_orderbooks = 0
        snapshot_contract_overlap = 0

        rows: list[dict[str, Any]] = []
        mapper = StationMapper()
        for contract in contracts:
            label = labels.get(contract.market_ticker)
            out_of_scope = _is_out_of_scope_weather(contract)
            mapping = None if out_of_scope else mapper.resolve(contract.city, contract.station_code)
            station = None if out_of_scope else (mapping.station_code if mapping else contract.station_code)
            maturity = _maturity_status(contract, as_of)
            blockers = _settlement_blockers(contract, mapping, label, climate, observations, active_station_map, maturity, min_confidence)
            if _exact_settlement_eligible(contract, mapping):
                exact_eligible += 1
            if maturity == "not_yet_mature":
                not_yet_mature += 1
            if label is not None:
                already_labeled += 1
                if float(label.get("confidence") or 0.0) >= min_confidence:
                    high_confidence_labels += 1
                else:
                    low_confidence_labels += 1
            if _deterministic_buildable(contract, mapping, climate, label, maturity, min_confidence):
                deterministic_buildable += 1
            snapshots = int(orderbooks.get(contract.market_ticker, {}).get("count") or 0)
            replay = int(replay_rows.get(contract.market_ticker, {}).get("count") or 0)
            if snapshots > 0:
                snapshot_contract_overlap += 1
                if label is not None:
                    labels_with_orderbooks += 1
                    if float(label.get("confidence") or 0.0) >= min_confidence:
                        high_conf_labels_with_orderbooks += 1
            reason = blockers[0] if blockers else "ready_exact_label_available"
            for blocker in blockers or ["ready_exact_label_available"]:
                blocker_counts[blocker] += 1
                if len(examples[blocker]) < 10:
                    examples[blocker].append(contract.market_ticker)
            station_key = station or "missing"
            date_key = contract.local_date.isoformat() if contract.local_date else "missing"
            type_key = f"{contract.variable_type}:{contract.contract_type}:{contract.comparator}"
            station_counts[station_key][reason] += 1
            date_counts[date_key][reason] += 1
            market_type_counts[type_key][reason] += 1
            maturity_counts[maturity][reason] += 1
            if len(rows) < 200:
                climate_key = (station or "", contract.local_date.isoformat() if contract.local_date else "")
                report = climate.get(climate_key)
                rows.append(
                    {
                        "market_ticker": contract.market_ticker,
                        "station": station,
                        "local_date": contract.local_date.isoformat() if contract.local_date else None,
                        "variable_type": contract.variable_type,
                        "contract_type": contract.contract_type,
                        "comparator": contract.comparator,
                        "maturity_status": maturity,
                        "has_label": label is not None,
                        "label_confidence": label.get("confidence") if label else None,
                        "label_source": label.get("primary_source_type") if label else None,
                        "exact_source_available": bool(label and int(label.get("exact_source_available") or 0) == 1),
                        "existing_climate_report_confidence": report.get("parser_confidence") if report else None,
                        "orderbook_snapshots": snapshots,
                        "replay_rows": replay,
                        "blockers": blockers,
                    }
                )

        replay_blockers = _replay_blocker_summary(contracts, labels, orderbooks, replay_rows, min_confidence)
        payload = {
            "as_of_date": as_of.isoformat(),
            "min_settlement_confidence": min_confidence,
            "parsed_contracts_count": len(contracts),
            "contracts_eligible_for_exact_settlement": exact_eligible,
            "contracts_not_yet_mature": not_yet_mature,
            "labels_already_present": already_labeled,
            "high_confidence_labels": high_confidence_labels,
            "low_confidence_labels": low_confidence_labels,
            "labels_newly_buildable_from_existing_exact_sources": deterministic_buildable,
            "snapshot_contract_overlap": snapshot_contract_overlap,
            "labels_with_orderbook_snapshots": labels_with_orderbooks,
            "high_confidence_labels_with_orderbook_snapshots": high_conf_labels_with_orderbooks,
            "replay_rows": sum(int(item.get("count") or 0) for item in replay_rows.values()),
            "top_blocker_reason_codes": blocker_counts.most_common(25),
            "blocker_examples": dict(examples),
            "breakdown_by_station": _counter_breakdown(station_counts),
            "breakdown_by_contract_date": _counter_breakdown(date_counts),
            "breakdown_by_market_type_condition": _counter_breakdown(market_type_counts),
            "breakdown_by_maturity_status": _counter_breakdown(maturity_counts),
            "sample_contracts": rows,
            "replay_coverage": replay_blockers,
            "next_commands": _dedupe_strings(
                [
                    "python main.py build-exact-settlements",
                    "python main.py weather-settlement-coverage",
                    "python main.py build-recorded-replay --last-days 7 --min-settlement-confidence 0.85"
                    if high_conf_labels_with_orderbooks > 0
                    else "",
                    "python main.py weather-ops-status --last-days 7",
                    "python main.py trading-readiness --last-days 7",
                ]
            ),
            "safety": {
                "read_only": True,
                "labels_created": 0,
                "deterministic_only": True,
                "no_trading_or_private_api": True,
                "readiness_gates_changed": False,
            },
        }
        return CommandResult(payload)

    def weather_label_expansion_plan(self, as_of: date | None = None, min_confidence: float = 0.85, top_n: int = 10) -> CommandResult:
        """Read-only operator plan for expanding exact high-confidence weather labels."""
        self.storage.init_db()
        as_of = as_of or date.today()
        contracts = _latest_weather_contracts(self.storage)
        labels = _settlement_label_map(self.storage)
        climate = _climate_report_map(self.storage)
        observations = _observation_source_index(self.storage)
        active_station_map = _active_station_map(self.storage)
        mapper = StationMapper()

        groups: dict[str, Counter[str]] = {
            "by_market_ticker_prefix": Counter(),
            "by_detected_station": Counter(),
            "by_missing_station_pattern": Counter(),
            "by_local_date": Counter(),
            "by_contract_condition_operator": Counter(),
            "by_threshold": Counter(),
            "by_observed_weather_variable": Counter(),
            "by_required_source_type": Counter(),
            "by_blocker_reason": Counter(),
            "by_expansion_classification": Counter(),
        }
        unsupported: dict[str, dict[str, Any]] = {}
        parser_task_counts: Counter[str] = Counter()
        near_term_tickers: set[str] = set()
        parser_expansion_tickers: set[str] = set()
        out_of_scope_or_ambiguous: set[str] = set()
        future_not_mature: set[str] = set()
        low_conf_fallback_only: set[str] = set()
        high_conf_exact_labels = 0
        deterministic_now = 0
        contract_rows: list[dict[str, Any]] = []

        for contract in contracts:
            label = labels.get(contract.market_ticker)
            out_of_scope = _is_out_of_scope_weather(contract)
            mapping = None if out_of_scope else mapper.resolve(contract.city, contract.station_code)
            station = None if out_of_scope else (mapping.station_code if mapping else contract.station_code)
            maturity = _maturity_status(contract, as_of)
            blockers = _settlement_blockers(contract, mapping, label, climate, observations, active_station_map, maturity, min_confidence)
            classification = _label_expansion_classification(contract, mapping, label, blockers, maturity, min_confidence)
            source_type = _required_source_type(contract)
            primary_blocker = "ready_exact_label_available" if classification == "already_high_confidence" else _primary_label_expansion_blocker(blockers)
            suggested_task = _suggested_parser_task(contract, classification)
            if suggested_task:
                parser_task_counts[suggested_task] += 1
            if maturity == "not_yet_mature":
                future_not_mature.add(contract.market_ticker)

            if label is not None and int(label.get("exact_source_available") or 0) == 1 and float(label.get("confidence") or 0.0) >= min_confidence:
                high_conf_exact_labels += 1
            if _deterministic_buildable(contract, mapping, climate, label, maturity, min_confidence):
                deterministic_now += 1
            if classification == "source_missing_but_parser_ok":
                near_term_tickers.add(contract.market_ticker)
            elif classification == "parser_missing_but_settleable":
                parser_expansion_tickers.add(contract.market_ticker)
            elif classification in {"not_weather_or_out_of_scope", "ambiguous_rules"}:
                out_of_scope_or_ambiguous.add(contract.market_ticker)
            elif classification == "low_conf_fallback_only":
                low_conf_fallback_only.add(contract.market_ticker)

            groups["by_market_ticker_prefix"][_ticker_prefix(contract.market_ticker)] += 1
            groups["by_detected_station"][station or "missing"] += 1
            groups["by_missing_station_pattern"][_missing_station_pattern(contract, mapping)] += 1
            groups["by_local_date"][contract.local_date.isoformat() if contract.local_date else "missing"] += 1
            groups["by_contract_condition_operator"][f"{contract.contract_type}:{contract.comparator}"] += 1
            groups["by_threshold"][_threshold_key(contract)] += 1
            groups["by_observed_weather_variable"][contract.variable_type] += 1
            groups["by_required_source_type"][source_type] += 1
            groups["by_blocker_reason"][primary_blocker] += 1
            groups["by_expansion_classification"][classification] += 1

            if "unsupported_market_format" in blockers or classification in {"parser_missing_but_settleable", "not_weather_or_out_of_scope", "ambiguous_rules"}:
                key = _unsupported_format_key(contract, classification)
                item = unsupported.setdefault(
                    key,
                    {
                        "format_key": key,
                        "classification": classification,
                        "count": 0,
                        "ticker_prefixes": Counter(),
                        "examples": [],
                        "suggested_parser_task": _suggested_parser_task(contract, classification),
                    },
                )
                item["count"] += 1
                item["ticker_prefixes"][_ticker_prefix(contract.market_ticker)] += 1
                if len(item["examples"]) < 5:
                    item["examples"].append(
                        {
                            "market_ticker": contract.market_ticker,
                            "title": contract.title,
                            "rules": _short_text(contract.rules, 500),
                            "variable_type": contract.variable_type,
                            "contract_type": contract.contract_type,
                            "comparator": contract.comparator,
                            "local_date": contract.local_date.isoformat() if contract.local_date else None,
                            "station": station,
                            "blockers": blockers,
                        }
                )

            if len(contract_rows) < 250:
                contract_rows.append(
                    {
                        "market_ticker": contract.market_ticker,
                        "ticker_prefix": _ticker_prefix(contract.market_ticker),
                        "title": contract.title,
                        "detected_station": station,
                        "missing_station_pattern": _missing_station_pattern(contract, mapping),
                        "local_date": contract.local_date.isoformat() if contract.local_date else None,
                        "contract_condition_operator": f"{contract.contract_type}:{contract.comparator}",
                        "threshold": _threshold_key(contract),
                        "observed_weather_variable": contract.variable_type,
                        "required_source_type": source_type,
                        "blocker_reason": primary_blocker,
                        "all_blockers": blockers,
                        "expansion_classification": classification,
                        "label_confidence": label.get("confidence") if label else None,
                        "exact_source_available": bool(label and int(label.get("exact_source_available") or 0) == 1),
                        "suggested_parser_task": suggested_task,
                    }
                )

        unsupported_rows = []
        for item in unsupported.values():
            unsupported_rows.append(
                {
                    **{key: value for key, value in item.items() if key != "ticker_prefixes"},
                    "ticker_prefixes": item["ticker_prefixes"].most_common(10),
                }
            )
        unsupported_rows = sorted(unsupported_rows, key=lambda row: row["count"], reverse=True)[:top_n]

        payload = {
            "as_of_date": as_of.isoformat(),
            "min_settlement_confidence": min_confidence,
            "current_high_conf_labels": high_conf_exact_labels,
            "current_high_conf_exact_labels": high_conf_exact_labels,
            "low_conf_fallback_only_labels": len(low_conf_fallback_only),
            "deterministic_exact_labels_buildable_now": deterministic_now,
            "possible_near_term_high_conf_labels_if_sources_available": len(near_term_tickers),
            "possible_parser_expansion_labels": len(parser_expansion_tickers),
            "out_of_scope_or_ambiguous": len(out_of_scope_or_ambiguous),
            "future_not_mature": len(future_not_mature),
            "top_unsupported_market_formats": unsupported_rows,
            "suggested_parser_or_source_tasks": [
                {"task": task, "affected_contracts": count, "advisory_only": True}
                for task, count in parser_task_counts.most_common(20)
            ],
            "grouped_blockers": {key: counter.most_common(100) for key, counter in groups.items()},
            "sample_contracts": contract_rows,
            "next_commands": _dedupe_strings(
                [
                    "python main.py weather-settlement-coverage",
                    "python main.py build-exact-settlements --limit 25" if deterministic_now > 0 else "",
                    "python main.py weather-data-audit",
                    "python main.py trading-readiness --last-days 7",
                ]
            ),
            "safety": {
                "read_only": True,
                "labels_created": 0,
                "confidence_threshold_lowered": False,
                "parser_changes_applied": False,
                "suggested_parser_tasks_are_advisory_only": True,
                "no_trading_or_private_api": True,
                "readiness_gates_changed": False,
            },
        }
        return CommandResult(payload)

    def weather_replay_build_coverage(
        self,
        start: date | None = None,
        end: date | None = None,
        last_days: int | None = None,
        market_ticker: str | None = None,
        min_confidence: float = 0.85,
        as_of: date | None = None,
    ) -> CommandResult:
        """Read-only dry-run for build-recorded-replay candidate filtering."""
        start, end = _replay_date_window(start, end, last_days, as_of=as_of)
        return CommandResult(
            _replay_build_coverage_readonly(
                db_path=self.storage.cfg.sqlite_path,
                start=start,
                end=end,
                market_ticker=market_ticker,
                min_confidence=min_confidence,
            )
        )

    def _count_in_window(self, table: str, column: str, since_iso: str) -> int:
        try:
            frame = self.storage.fetch_sql(
                f"SELECT COUNT(*) AS count FROM {table} WHERE {column} >= :since",
                {"since": since_iso},
            )
        except Exception:
            return 0
        return int(frame.iloc[0]["count"]) if not frame.empty else 0

    def rebuild_clean_edge_analysis(self, last_days: int = 3, dry_run: bool = False) -> CommandResult:
        commands = [
            f"python main.py reparse-contracts --weather-only --parser-version {PARSER_VERSION}",
            f"python main.py rebuild-settlement-labels --weather-only --settlement-version {SETTLEMENT_VERSION}",
            "python main.py validate-settlement-labels --weather-only",
            "python main.py validate-settlement-sources",
            f"python main.py mark-stale-runs --before-parser-version {PARSER_VERSION}",
            f"python main.py build-recorded-replay --last-days {last_days} --min-settlement-confidence 0.85",
            f"python main.py sweep-recorded --last-days {last_days}",
            f"python main.py edge-report --last-days {last_days}",
        ]
        if dry_run:
            return CommandResult({"dry_run": True, "commands": commands})
        results = {
            "reparse": self.reparse_contracts(weather_only=True).payload,
            "settlements": self.rebuild_settlement_labels().payload,
            "validate_labels": self.validate_settlement_labels(weather_only=True).payload,
            "validate_sources": self.validate_settlement_sources(fix=True).payload,
            "mark_stale": self.mark_stale_runs().payload,
        }
        replay = RecordedOrderbookReplayBuilder(storage=self.storage).build(last_days=last_days, min_settlement_confidence=0.85)
        sweep = RecordedOrderbookBacktester(storage=self.storage).sweep(last_days=last_days, label_quality="primary")
        report = EdgeReportGenerator(storage=self.storage).generate(last_days=last_days)
        results.update({"replay": replay.to_dict(), "sweep_recommendation": sweep.get("recommendation"), "report": report.to_dict()})
        return CommandResult(results)

    def daily_trading_research_update(self, last_days: int = 7) -> CommandResult:
        reports = PROJECT_ROOT / "reports"
        reports.mkdir(exist_ok=True)
        results: dict[str, Any] = {}
        results["collector_health"] = self.collector_health(last_hours=24).payload
        results["weather_recorder_health"] = self.weather_recorder_health(last_hours=24).payload
        try:
            from backtest.recorded_audit import RecordedDataAuditor

            audit = RecordedDataAuditor(storage=self.storage).audit(persist=True)
            results["audit_recorded_data"] = audit.to_dict()
        except Exception as exc:
            results["audit_recorded_data"] = {"error": str(exc)}
        results["settlement_sources"] = self.validate_settlement_sources(fix=True).payload
        results["orderbook_depths"] = self.validate_orderbook_depths(last_days=last_days).payload
        try:
            replay = RecordedOrderbookReplayBuilder(storage=self.storage).build(last_days=last_days, min_settlement_confidence=0.85)
            results["build_recorded_replay"] = replay.to_dict()
        except Exception as exc:
            results["build_recorded_replay"] = {"error": str(exc)}
        try:
            results["market_making"] = MarketMakingAnalyzer(storage=self.storage).analyze(last_days=last_days, persist_exports=True).to_dict()
        except Exception as exc:
            results["market_making"] = {"error": str(exc)}
        results["validate_signals"] = SignalValidator(self.storage).validate(last_days=last_days).to_dict()
        results["liquidity"] = LiquidityAnalyzer(self.storage).analyze(last_days=last_days, persist_exports=True).to_dict()
        try:
            results["sweep_recorded"] = RecordedOrderbookBacktester(storage=self.storage).sweep(last_days=last_days, label_quality="primary")
        except Exception as exc:
            results["sweep_recorded"] = {"error": str(exc)}
        results["trading_readiness"] = TradingReadiness(self.storage).evaluate(last_days=last_days).to_dict()
        try:
            report = EdgeReportGenerator(storage=self.storage).generate(last_days=last_days)
            results["edge_report"] = report.to_dict()
        except Exception as exc:
            results["edge_report"] = {"error": str(exc)}
        self._export_daily_manual_review_placeholders()
        path = reports / f"daily_trading_research_update_{datetime.now().strftime('%Y%m%d')}.md"
        path.write_text(_daily_report_markdown(results), encoding="utf-8")
        results["report_path"] = str(path)
        return CommandResult(results)

    def _latest_contracts(self, last_days: int | None = None) -> pd.DataFrame:
        frame = self.storage.fetch_table("parsed_contracts", limit=500000)
        if frame.empty:
            return frame
        rows = []
        seen: set[str] = set()
        cutoff = date.today() - timedelta(days=max(last_days or 0, 1)) if last_days else None
        for _, row in frame.sort_values("id", ascending=False).iterrows():
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            ticker = str(payload.get("market_ticker") or "")
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            local_date = payload.get("local_date")
            if cutoff and local_date:
                try:
                    if date.fromisoformat(str(local_date)) < cutoff:
                        continue
                except ValueError:
                    pass
            rows.append(payload)
        return pd.DataFrame(rows)

    def _settlement_yes_results(self) -> dict[str, int | None]:
        labels = self.storage.fetch_table("settlement_labels", limit=500000)
        if labels.empty:
            return {}
        return {str(row["market_ticker"]): (None if pd.isna(row.get("yes_result")) else int(row["yes_result"])) for _, row in labels.iterrows()}

    def _export_changed_labels(self, changed: list[dict[str, Any]]) -> None:
        reports = PROJECT_ROOT / "reports"
        reports.mkdir(exist_ok=True)
        pd.DataFrame(changed).to_csv(reports / "manual_review_changed_labels.csv", index=False)

    def _export_daily_manual_review_placeholders(self) -> None:
        reports = PROJECT_ROOT / "reports"
        reports.mkdir(exist_ok=True)
        for name in ["paper_ready_candidates.csv", "fair_value_candidates.csv", "skipped_due_to_data_quality.csv"]:
            path = reports / name
            if not path.exists():
                pd.DataFrame().to_csv(path, index=False)

    def _count(self, table_name: str) -> int:
        frame = self.storage.fetch_sql(f"SELECT COUNT(*) AS count FROM {table_name}")
        return int(frame.iloc[0]["count"]) if not frame.empty else 0

    def _count_where(self, table_name: str, clause: str) -> int:
        frame = self.storage.fetch_sql(f"SELECT COUNT(*) AS count FROM {table_name} WHERE {clause}")
        return int(frame.iloc[0]["count"]) if not frame.empty else 0

    def _latest_value(self, table_name: str, column: str) -> Any:
        frame = self.storage.fetch_sql(f"SELECT {column} FROM {table_name} WHERE {column} IS NOT NULL ORDER BY id DESC LIMIT 1")
        return None if frame.empty else frame.iloc[0][column]

    def _latest_sweep_recommendation(self) -> str | None:
        frame = self.storage.fetch_sql("SELECT recommendation FROM recorded_strategy_sweeps WHERE COALESCE(is_stale, 0) = 0 ORDER BY id DESC LIMIT 1")
        return None if frame.empty else str(frame.iloc[0]["recommendation"])


def _key_tables() -> list[str]:
    return [
        "markets",
        "parsed_contracts",
        "settlement_labels",
        "nws_daily_climate_reports",
        "orderbook_snapshots_live",
        "active_weather_station_map",
        "weather_observation_snapshots_live",
        "weather_forecast_snapshots_live",
        "recorded_orderbook_replay_snapshots",
        "signals",
        "backtest_runs",
        "backtest_trades",
        "recorded_data_audits",
        "recorded_strategy_sweeps",
        "collector_state",
        "historical_trades",
        "market_universe_rankings",
    ]


_AUDIT_KNOWN_TABLES = [
    "markets",
    "parsed_contracts",
    "orderbook_snapshots_live",
    "weather_observation_snapshots_live",
    "weather_forecast_snapshots_live",
    "historical_trades",
    "settlement_labels",
    "recorded_orderbook_replay_snapshots",
    "recorded_strategy_sweeps",
    "backtest_runs",
    "backtest_trades",
    "collector_state",
    "nws_daily_climate_reports",
    "market_universe_rankings",
]

_AUDIT_TIMESTAMP_COLUMNS = {
    "orderbook_snapshots_live": "ts",
    "weather_observation_snapshots_live": "ts_recorded",
    "weather_forecast_snapshots_live": "ts_recorded",
    "historical_trades": "ts",
    "recorded_orderbook_replay_snapshots": "ts",
    "recorded_strategy_sweeps": "ts",
    "backtest_trades": "ts",
    "collector_state": "updated_at",
    "nws_daily_climate_reports": "issued_at",
    "markets": "created_at",
    "parsed_contracts": "created_at",
    "settlement_labels": "created_at",
}

_AUDIT_SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".cache",
    "reports",
    "build",
    "dist",
}


def _configured_db_env_paths(active_db: Path | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        rows.append(_env_db_path_row("DATABASE_URL", database_url, "environment"))
    else:
        rows.append(
            {
                "variable": "DATABASE_URL",
                "source": "default",
                "resolved_path": str(active_db) if active_db else None,
                "is_active_configured_db": True,
            }
        )
    for name, value in sorted(os.environ.items()):
        upper = name.upper()
        if name == "DATABASE_URL" or not value:
            continue
        looks_db_path = (
            upper.endswith("_DB_PATH")
            or upper.endswith("_DATABASE_PATH")
            or upper.endswith("_DATABASE_URL")
            or upper.endswith("_SQLITE_PATH")
        )
        if looks_db_path:
            rows.append(_env_db_path_row(name, value, "environment"))
    return rows


def _env_db_path_row(name: str, value: str, source: str) -> dict[str, Any]:
    resolved = _resolve_db_path_value(value)
    return {
        "variable": name,
        "source": source,
        "resolved_path": str(resolved) if resolved else None,
        "is_active_configured_db": bool(resolved and _same_path(resolved, settings.sqlite_path)),
    }


def _resolve_db_path_value(value: str) -> Path | None:
    raw = value.strip()
    if not raw:
        return None
    if raw.startswith("sqlite:///"):
        path_text = raw[len("sqlite:///") :]
        path = Path(path_text)
    elif raw.startswith("sqlite://"):
        return None
    else:
        path = Path(raw)
    try:
        path = path.expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()
    except Exception:
        return path


def _safe_db_search_roots(active_db: Path | None, override: list[Path] | None = None) -> list[Path]:
    candidates = list(override or [])
    if override is None:
        candidates.extend([PROJECT_ROOT, PROJECT_ROOT.parent, settings.cache_dir])
        if active_db:
            candidates.append(active_db.parent)
    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = Path(candidate).expanduser().resolve()
        except Exception:
            continue
        if not resolved.exists() or not resolved.is_dir():
            continue
        key = str(resolved).lower()
        if key not in seen:
            seen.add(key)
            roots.append(resolved)
    return roots


def _discover_db_files(roots: list[Path], active_db: Path | None) -> list[Path]:
    found: dict[str, Path] = {}
    if active_db:
        found[str(active_db).lower()] = active_db
    for root in roots:
        for current, dirs, files in os.walk(root):
            dirs[:] = [name for name in dirs if name not in _AUDIT_SKIP_DIRS]
            for name in files:
                lower = name.lower()
                if lower.endswith((".db", ".sqlite", ".sqlite3")):
                    path = Path(current) / name
                    try:
                        resolved = path.resolve()
                    except Exception:
                        resolved = path
                    found[str(resolved).lower()] = resolved
    return sorted(found.values(), key=lambda item: str(item).lower())


def _inspect_sqlite_db(path: Path, active_db: Path | None) -> dict[str, Any]:
    warnings: list[str] = []
    try:
        stat = path.stat()
    except OSError as exc:
        return {
            "path": str(path),
            "exists": False,
            "sqlite_openable": False,
            "error": f"{type(exc).__name__}: {exc}",
            "warnings": ["File could not be statted."],
            "is_active_configured_db": bool(active_db and _same_path(path, active_db)),
        }

    report: dict[str, Any] = {
        "path": str(path),
        "exists": True,
        "size_bytes": int(stat.st_size),
        "modified_time": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "is_active_configured_db": bool(active_db and _same_path(path, active_db)),
        "sqlite_openable": False,
        "relevant_tables": [],
        "row_counts": {},
        "time_ranges": {},
        "latest_orderbook_timestamp": None,
        "latest_observation_timestamp": None,
        "latest_forecast_timestamp": None,
        "latest_trade_timestamp": None,
        "settlement_label_count": 0,
        "replay_row_count": 0,
        "strategy_sweep_count": 0,
        "suspected_data_gap_ranges": [],
        "warnings": warnings,
    }
    if stat.st_size == 0:
        warnings.append("File is empty.")
        return report

    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=1.0)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        warnings.append("File could not be opened read-only as SQLite.")
        return report

    try:
        conn.execute("PRAGMA query_only=ON")
        table_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        tables = sorted(str(row[0]) for row in table_rows)
        relevant = [table for table in _AUDIT_KNOWN_TABLES if table in tables]
        report["sqlite_openable"] = True
        report["table_count"] = len(tables)
        report["relevant_tables"] = relevant
        if not relevant:
            warnings.append("SQLite DB opens, but none of the known project tables were found.")
        for table in relevant:
            columns = _sqlite_columns(conn, table)
            count = _sqlite_count(conn, table)
            report["row_counts"][table] = count
            ts_col = _AUDIT_TIMESTAMP_COLUMNS.get(table)
            if ts_col and ts_col in columns:
                report["time_ranges"][table] = _sqlite_time_range(conn, table, ts_col)
                gaps = _sqlite_date_gaps(conn, table, ts_col, count)
                if gaps:
                    report["suspected_data_gap_ranges"].append({"table": table, "column": ts_col, "gaps": gaps})
            elif ts_col:
                warnings.append(f"{table} is missing expected timestamp column {ts_col}.")
        counts = report["row_counts"]
        ranges = report["time_ranges"]
        report["latest_orderbook_timestamp"] = _range_max(ranges, "orderbook_snapshots_live")
        report["latest_observation_timestamp"] = _range_max(ranges, "weather_observation_snapshots_live")
        report["latest_forecast_timestamp"] = _range_max(ranges, "weather_forecast_snapshots_live")
        report["latest_trade_timestamp"] = _range_max(ranges, "historical_trades")
        report["settlement_label_count"] = int(counts.get("settlement_labels") or 0)
        report["replay_row_count"] = int(counts.get("recorded_orderbook_replay_snapshots") or 0)
        report["strategy_sweep_count"] = int(counts.get("recorded_strategy_sweeps") or 0)
        if report["is_active_configured_db"]:
            if report["settlement_label_count"] == 0:
                warnings.append("Active configured DB has zero settlement labels.")
            if report["replay_row_count"] == 0:
                warnings.append("Active configured DB has zero recorded replay rows.")
    except Exception as exc:
        report["sqlite_openable"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        warnings.append("SQLite metadata inspection failed.")
    finally:
        conn.close()
    return report


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _sqlite_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except Exception:
        return 0


def _sqlite_time_range(conn: sqlite3.Connection, table: str, column: str) -> dict[str, Any]:
    row = conn.execute(
        f'SELECT MIN("{column}") AS min_ts, MAX("{column}") AS max_ts FROM "{table}" WHERE "{column}" IS NOT NULL'
    ).fetchone()
    return {"min": row[0], "max": row[1]} if row else {"min": None, "max": None}


def _sqlite_date_gaps(conn: sqlite3.Connection, table: str, column: str, count: int) -> list[dict[str, str]]:
    if count <= 0 or count > 1_000_000:
        return []
    rows = conn.execute(
        f'SELECT date("{column}") AS day FROM "{table}" WHERE "{column}" IS NOT NULL GROUP BY day ORDER BY day'
    ).fetchall()
    days = []
    for row in rows:
        try:
            if row[0]:
                days.append(date.fromisoformat(str(row[0])[:10]))
        except ValueError:
            continue
    if len(days) < 2:
        return []
    span = (days[-1] - days[0]).days
    if span > 120:
        return []
    present = set(days)
    missing = [days[0] + timedelta(days=offset) for offset in range(span + 1) if days[0] + timedelta(days=offset) not in present]
    return _date_ranges(missing)


def _date_ranges(days: list[date]) -> list[dict[str, str]]:
    if not days:
        return []
    ranges = []
    start = prev = days[0]
    for item in days[1:]:
        if item == prev + timedelta(days=1):
            prev = item
            continue
        ranges.append({"start": start.isoformat(), "end": prev.isoformat()})
        start = prev = item
    ranges.append({"start": start.isoformat(), "end": prev.isoformat()})
    return ranges


def _range_max(ranges: dict[str, Any], table: str) -> Any:
    value = ranges.get(table) or {}
    return value.get("max")


def _best_historical_db(db_reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    scored = []
    for report in db_reports:
        if not report.get("sqlite_openable"):
            continue
        counts = report.get("row_counts") or {}
        score = sum(int(counts.get(table) or 0) for table in _AUDIT_KNOWN_TABLES)
        scored.append((score, report))
    if not scored:
        return None
    score, report = max(scored, key=lambda item: item[0])
    return {"path": report.get("path"), "score_rows": score}


def _freshest_db(db_reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: tuple[datetime, dict[str, Any], str] | None = None
    latest_keys = [
        "latest_orderbook_timestamp",
        "latest_observation_timestamp",
        "latest_forecast_timestamp",
        "latest_trade_timestamp",
    ]
    for report in db_reports:
        if not report.get("sqlite_openable"):
            continue
        for key in latest_keys:
            parsed = _parse_ts(report.get(key))
            if parsed and (best is None or parsed > best[0]):
                best = (parsed, report, key)
    if best is None:
        return None
    return {"path": best[1].get("path"), "latest_timestamp": best[0].isoformat(), "source": best[2]}


def _wrong_path_status(active_db: Path | None, reports: list[dict[str, Any]], freshest: dict[str, Any] | None) -> tuple[str, list[str]]:
    if active_db is None:
        return "unknown", ["Active configured DB path is unavailable."]
    active = _find_db_report(reports, active_db)
    if active is None or not active.get("sqlite_openable"):
        return "unknown", ["Active configured DB was not openable, so wrong-path status is unknown."]
    if not freshest:
        return "unknown", ["No openable DB had timestamped weather/orderbook/trade rows."]
    freshest_path = Path(str(freshest.get("path")))
    if _same_path(freshest_path, active_db):
        return "false", []
    active_latest = max(
        [dt for dt in (_parse_ts(active.get(key)) for key in [
            "latest_orderbook_timestamp",
            "latest_observation_timestamp",
            "latest_forecast_timestamp",
            "latest_trade_timestamp",
        ]) if dt],
        default=None,
    )
    freshest_latest = _parse_ts(freshest.get("latest_timestamp"))
    if freshest_latest and (active_latest is None or freshest_latest > active_latest + timedelta(minutes=5)):
        return "true", [f"Another DB has fresher data than the active configured DB: {freshest.get('path')}."]
    return "unknown", ["Another DB ties or nearly ties the active DB freshness; inspect discovered DB list."]


def _find_db_report(db_reports: list[dict[str, Any]], path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    for report in db_reports:
        if _same_path(Path(str(report.get("path"))), path):
            return report
    return None


def _collect_gap_summaries(db_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in db_reports:
        gaps = report.get("suspected_data_gap_ranges") or []
        if gaps:
            rows.append({"path": report.get("path"), "gaps": gaps})
    return rows


def _dedupe_strings(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


_WEATHER_RUNBOOK_SHIM = r".\python.cmd"


def render_weather_runbook_commands(runbook_commands: dict[str, list[str]]) -> str:
    """Render the weather runbook command bundle for PowerShell operators."""
    sections: list[tuple[str, list[str]]] = [
        ("CONTINUOUS COLLECTORS", runbook_commands.get("continuous_background_collection", [])),
        (
            "EVERY FEW HOURS",
            [
                *_pick_runbook_commands(
                    runbook_commands,
                    "read_only_checks",
                    [
                        "weather-ops-status",
                        "weather-recorder-health",
                        "collector-health",
                        "trading-readiness",
                    ],
                ),
                *_pick_runbook_commands(
                    runbook_commands,
                    "safe_idempotent_builders",
                    ["build-recorded-replay"],
                ),
            ],
        ),
        (
            "DAILY",
            [
                *_pick_runbook_commands(
                    runbook_commands,
                    "safe_idempotent_builders",
                    ["load-markets", "resolve-active-weather-stations", "build-exact-settlements --limit 200"],
                ),
                *_pick_runbook_commands(
                    runbook_commands,
                    "analysis_and_sweeps",
                    ["sweep-recorded", "recorded-sweep-attribution", "analyze-liquidity", "analyze-market-making", "validate-signals"],
                ),
            ],
        ),
        (
            "WEEKLY",
            [
                *_pick_runbook_commands(
                    runbook_commands,
                    "read_only_checks",
                    ["weather-data-audit", "weather-settlement-coverage", "weather-replay-build-coverage", "weather-label-expansion-plan", "source-smoke", "project-status"],
                ),
                *_pick_runbook_commands(runbook_commands, "analysis_and_sweeps", ["daily-trading-research-update"]),
            ],
        ),
        ("SWITCHING COMPUTERS", runbook_commands.get("on_env_breakage", [])),
        (
            "MINIMUM MAINTENANCE LOOP",
            [
                r".\pytest.cmd tests\test_settlement_labels.py tests\test_trading_research_engine.py -q",
                r".\pytest.cmd",
                *_pick_runbook_commands(
                    runbook_commands,
                    "read_only_checks",
                    ["weather-ops-status", "weather-settlement-coverage", "trading-readiness"],
                ),
                *_pick_runbook_commands(runbook_commands, "safe_idempotent_builders", ["build-exact-settlements --limit 200"]),
                *_pick_runbook_commands(runbook_commands, "analysis_and_sweeps", ["sweep-recorded", "recorded-sweep-attribution"]),
            ],
        ),
        (
            "NEVER RUN CASUALLY",
            [
                *runbook_commands.get("never_run_casually", []),
                *runbook_commands.get("caution_research_only_paper", []),
            ],
        ),
    ]

    lines = [
        "Weather operator runbook commands",
        "Source: docs/WEATHER_OPERATOR_RUNBOOK.md",
        "These commands are display-only; readiness gates and next_commands are unchanged.",
        "",
    ]
    for title, commands in sections:
        lines.append(title)
        if commands:
            for command in _dedupe_strings(commands):
                lines.append(f"  - {command}")
        else:
            lines.append("  - none")
        lines.append("")
    return "\n".join(lines).rstrip()


def _pick_runbook_commands(
    runbook_commands: dict[str, list[str]],
    section: str,
    needles: list[str],
) -> list[str]:
    commands = runbook_commands.get(section, [])
    picked: list[str] = []
    for needle in needles:
        for command in commands:
            if needle in command and command not in picked:
                picked.append(command)
                break
    return picked


def _weather_runbook_commands() -> dict[str, list[str]]:
    r"""Categorized PowerShell command bundle for the durable weather operator runbook.

    The commands here intentionally use the .\python.cmd shim so PowerShell users
    get the repo-local virtual environment instead of the WindowsApps Python that
    is missing dependencies. The list mirrors docs/WEATHER_OPERATOR_RUNBOOK.md.

    Important: this section never includes live trading or order placement. The
    only paper-trade commands surfaced are read-only or research-only paper
    quoters that the project documents as no-order-placement loops, and they are
    listed under "caution" so the operator does not run them casually.
    """
    shim = _WEATHER_RUNBOOK_SHIM
    return {
        "read_only_checks": [
            f"{shim} scripts\\env_doctor.py",
            f"{shim} main.py weather-ops-status --last-days 7",
            f"{shim} main.py weather-data-audit",
            f"{shim} main.py weather-settlement-coverage",
            f"{shim} main.py weather-replay-build-coverage --last-days 7 --min-settlement-confidence 0.85",
            f"{shim} main.py weather-label-expansion-plan",
            f"{shim} main.py weather-recorder-health --last-hours 24",
            f"{shim} main.py collector-health --last-hours 24",
            f"{shim} main.py trading-readiness --last-days 7",
            f"{shim} main.py recorded-sweep-attribution --last-days 7 --label-quality primary",
            f"{shim} main.py source-smoke",
            f"{shim} main.py project-status",
        ],
        "safe_idempotent_builders": [
            f"{shim} main.py init-db",
            f"{shim} main.py load-markets",
            f"{shim} main.py resolve-active-weather-stations",
            f"{shim} main.py build-exact-settlements --limit 200",
            f"{shim} main.py build-exact-settlements",
            f"{shim} main.py build-recorded-replay --last-days 7 --min-settlement-confidence 0.85",
        ],
        "analysis_and_sweeps": [
            f"{shim} main.py sweep-recorded --last-days 7 --label-quality primary",
            f"{shim} main.py recorded-sweep-attribution --last-days 7 --label-quality primary",
            f"{shim} main.py analyze-liquidity --last-days 7",
            f"{shim} main.py analyze-market-making --last-days 7",
            f"{shim} main.py validate-signals --last-days 7",
            f"{shim} main.py daily-trading-research-update",
        ],
        "continuous_background_collection": [
            f"{shim} main.py record-orderbooks --weather-only --interval-seconds 30 --duration-hours 12",
            f"{shim} main.py record-weather-observations --from-active-markets --interval-minutes 5 --duration-hours 12",
            f"{shim} main.py record-weather-forecasts --from-active-markets --interval-minutes 30 --duration-hours 12",
        ],
        "caution_research_only_paper": [
            f"{shim} main.py paper-market-making-basket --last-days 1 --search-max-markets 100 --max-targets 5 --duration-minutes 60",
            f"{shim} main.py paper-market-making --market-ticker <TICKER> --side BUY_YES --interval-seconds 30 --duration-minutes 60 --quantity 1 --max-position 5 --max-open-quotes 1",
        ],
        "never_run_casually": [
            f"{shim} main.py rebuild-clean-edge-analysis",
            f"{shim} main.py reparse-contracts --weather-only --parser-version v2_range_bucket_semantics",
            f"{shim} main.py rebuild-settlement-labels --weather-only",
            f"{shim} main.py mark-stale-runs",
            f"{shim} main.py load-history",
        ],
        "on_env_breakage": [
            r".\scripts\setup-dev.ps1",
            f"{shim} scripts\\env_doctor.py",
        ],
    }


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except Exception:
        return str(left).lower() == str(right).lower()


def _replay_date_window(
    start: date | None,
    end: date | None,
    last_days: int | None,
    as_of: date | None = None,
) -> tuple[date | None, date | None]:
    if last_days is None:
        return start, end
    end_date = end or as_of or date.today()
    return end_date - timedelta(days=max(last_days, 1)), end_date


def _replay_build_coverage_readonly(
    db_path: Path,
    start: date | None,
    end: date | None,
    market_ticker: str | None,
    min_confidence: float,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "active_configured_db": str(db_path),
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "market_ticker": market_ticker,
        "min_settlement_confidence": min_confidence,
        "total_labels": 0,
        "labels_above_confidence_threshold": 0,
        "labels_within_last_days_window": 0,
        "labels_excluded_by_last_days": 0,
        "labels_with_matching_parsed_contract": 0,
        "labels_with_matching_orderbook_ticker": 0,
        "labels_with_orderbook_snapshots_before_close": 0,
        "labels_with_orderbook_snapshots_inside_replay_window": 0,
        "labels_excluded_by_ticker_mismatch": 0,
        "labels_excluded_by_market_id_mismatch": 0,
        "labels_excluded_by_timestamp_date_parsing": 0,
        "labels_excluded_by_confidence_field_mismatch": 0,
        "labels_excluded_by_missing_label_outcome": 0,
        "labels_excluded_by_missing_snapshot": 0,
        "labels_excluded_by_unsupported_contract": 0,
        "labels_excluded_by_missing_station_or_date": 0,
        "labels_with_non_exact_source": 0,
        "rows_expected_to_be_buildable": 0,
        "rows_actually_present": 0,
        "candidate_details": [],
        "top_blocker_reason_codes": [],
        "zero_replay_blocker": None,
        "builder_filter_notes": {
            "date_window_source": "build-recorded-replay filters orderbook_snapshots_live.ts by date window, not settlement_labels.created_at.",
            "confidence_field": "build-recorded-replay reads settlement_labels.confidence as a 0-1 float.",
            "ticker_join": "build-recorded-replay joins by exact market_ticker strings.",
            "market_id_join": "not_applicable: project replay schema does not have market_id columns.",
            "before_close_filter": "diagnostic reports before-close coverage; current builder does not require market_close_time.",
        },
        "next_commands": [
            "python main.py weather-replay-build-coverage --last-days 7 --min-settlement-confidence 0.85",
            "python main.py build-recorded-replay --last-days 7 --min-settlement-confidence 0.85",
            "python main.py weather-data-audit",
            "python main.py trading-readiness --last-days 7",
        ],
        "safety": {
            "read_only": True,
            "sqlite_open_mode": "mode=ro with PRAGMA query_only=ON",
            "no_database_mutation": True,
            "no_trading_or_private_api": True,
            "readiness_gates_changed": False,
        },
    }
    if not db_path.exists():
        payload["error"] = "active configured DB does not exist"
        payload["zero_replay_blocker"] = "active_configured_db_missing"
        return payload

    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
        payload["zero_replay_blocker"] = "active_configured_db_not_openable_read_only"
        return payload

    try:
        conn.execute("PRAGMA query_only=ON")
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        required = {"settlement_labels", "parsed_contracts", "orderbook_snapshots_live", "recorded_orderbook_replay_snapshots"}
        missing = sorted(required - tables)
        payload["missing_required_tables"] = missing
        if missing:
            payload["zero_replay_blocker"] = "missing_required_tables"
            return payload

        parsed_contracts = _replay_latest_contracts(conn)
        orderbooks_any = _replay_orderbook_index(conn, start=None, end=None)
        orderbooks_window = _replay_orderbook_index(conn, start=start, end=end)
        replay_window = _replay_existing_index(conn, start=start, end=end)
        replay_tradability = _replay_tradability_summary(conn, start=start, end=end)
        labels = _replay_label_rows(conn, market_ticker=market_ticker)
        payload["total_labels"] = len(labels)
        payload["rows_actually_present"] = sum(int(row.get("count") or 0) for row in replay_window.values())
        payload["replay_tradability"] = replay_tradability

        blocker_counts: Counter[str] = Counter()
        mapper = StationMapper()
        details: list[dict[str, Any]] = []
        expected_rows = 0
        for label in labels:
            ticker = str(label.get("market_ticker") or "")
            confidence, confidence_issue = _replay_confidence(label.get("confidence"))
            label_date = _parse_date_value(label.get("local_date"))
            label_in_window = _date_in_window(label_date, start, end)
            parsed_contract = parsed_contracts.get(ticker)
            parsed_present = parsed_contract is not None
            builder_contract_eligible = bool(
                parsed_contract
                and parsed_contract.variable_type in {"high_temp", "low_temp"}
                and parsed_contract.local_date is not None
            )
            mapping = mapper.resolve(parsed_contract.city, parsed_contract.station_code) if parsed_contract else None
            any_books = orderbooks_any.get(ticker, {})
            window_books = orderbooks_window.get(ticker, {})
            replay_books = replay_window.get(ticker, {})
            any_rows = int(any_books.get("count") or 0)
            window_rows = int(window_books.get("count") or 0)
            before_close_rows = int(window_books.get("before_close_count") or 0)
            invalid_ts_rows = int(any_books.get("invalid_ts_count") or 0)
            blockers: list[str] = []

            if confidence is None:
                blockers.append("confidence_field_mismatch")
            elif confidence < min_confidence:
                blockers.append("confidence_below_threshold")
            if confidence_issue:
                blockers.append(confidence_issue)
            if label.get("yes_result") is None:
                blockers.append("missing_label_outcome")
            if not parsed_present:
                blockers.append("ticker_mismatch_no_matching_parsed_contract")
            elif not builder_contract_eligible:
                blockers.append("unsupported_or_incomplete_parsed_contract")
            if parsed_contract and (mapping is None or parsed_contract.local_date is None):
                blockers.append("missing_station_or_contract_date")
            if any_rows == 0:
                blockers.append("missing_orderbook_ticker")
            elif window_rows == 0:
                blockers.append("excluded_by_last_days")
            if invalid_ts_rows > 0 and window_rows == 0:
                blockers.append("timestamp_date_parsing_exclusion")
            blockers = list(dict.fromkeys(blockers))

            source_exact = int(label.get("exact_source_available") or 0) == 1
            if not source_exact:
                payload["labels_with_non_exact_source"] += 1

            if confidence is not None and confidence >= min_confidence:
                payload["labels_above_confidence_threshold"] += 1
            if label_in_window:
                payload["labels_within_last_days_window"] += 1
            if parsed_present:
                payload["labels_with_matching_parsed_contract"] += 1
            if any_rows > 0:
                payload["labels_with_matching_orderbook_ticker"] += 1
            if before_close_rows > 0:
                payload["labels_with_orderbook_snapshots_before_close"] += 1
            if window_rows > 0:
                payload["labels_with_orderbook_snapshots_inside_replay_window"] += 1

            if "confidence_field_mismatch" in blockers:
                payload["labels_excluded_by_confidence_field_mismatch"] += 1
            if "missing_label_outcome" in blockers:
                payload["labels_excluded_by_missing_label_outcome"] += 1
            if "ticker_mismatch_no_matching_parsed_contract" in blockers:
                payload["labels_excluded_by_ticker_mismatch"] += 1
            if "unsupported_or_incomplete_parsed_contract" in blockers:
                payload["labels_excluded_by_unsupported_contract"] += 1
            if "missing_station_or_contract_date" in blockers:
                payload["labels_excluded_by_missing_station_or_date"] += 1
            if "missing_orderbook_ticker" in blockers:
                payload["labels_excluded_by_missing_snapshot"] += 1
            if "excluded_by_last_days" in blockers:
                payload["labels_excluded_by_last_days"] += 1
            if "timestamp_date_parsing_exclusion" in blockers:
                payload["labels_excluded_by_timestamp_date_parsing"] += 1

            buildable = not blockers
            if buildable:
                expected_rows += window_rows
                blocker_counts["buildable"] += 1
            else:
                for blocker in blockers:
                    blocker_counts[blocker] += 1

            details.append(
                {
                    "market_ticker": ticker,
                    "confidence": confidence,
                    "label_local_date": label.get("local_date"),
                    "label_local_date_in_window": label_in_window,
                    "yes_result": label.get("yes_result"),
                    "has_matching_parsed_contract": parsed_present,
                    "builder_contract_eligible": builder_contract_eligible,
                    "has_matching_orderbook_ticker": any_rows > 0,
                    "orderbook_rows_total": any_rows,
                    "orderbook_rows_inside_replay_window": window_rows,
                    "orderbook_rows_before_close_inside_window": before_close_rows,
                    "orderbook_invalid_timestamp_rows": invalid_ts_rows,
                    "existing_replay_rows_inside_window": int(replay_books.get("count") or 0),
                    "buildable_rows": window_rows if buildable else 0,
                    "blockers": blockers,
                }
            )

        payload["rows_expected_to_be_buildable"] = expected_rows
        payload["candidate_details"] = sorted(
            details,
            key=lambda item: (0 if item["buildable_rows"] else 1, str(item["market_ticker"])),
        )[:300]
        payload["top_blocker_reason_codes"] = blocker_counts.most_common(25)
        if payload["rows_actually_present"] == 0:
            if expected_rows > 0:
                payload["zero_replay_blocker"] = "buildable_rows_exist_but_replay_table_empty_run_build_recorded_replay"
            elif blocker_counts:
                payload["zero_replay_blocker"] = blocker_counts.most_common(1)[0][0]
            else:
                payload["zero_replay_blocker"] = "no_settlement_labels_available"
        else:
            payload["zero_replay_blocker"] = None
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
        payload["zero_replay_blocker"] = "diagnostic_failed"
    finally:
        conn.close()
    return payload


def _replay_label_rows(conn: sqlite3.Connection, market_ticker: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM settlement_labels"
    params: dict[str, Any] = {}
    if market_ticker:
        sql += " WHERE market_ticker = :market_ticker"
        params["market_ticker"] = market_ticker
    sql += " ORDER BY market_ticker"
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _replay_latest_contracts(conn: sqlite3.Connection) -> dict[str, WeatherContract]:
    rows = conn.execute(
        """
        SELECT market_ticker, payload
        FROM parsed_contracts
        WHERE market_ticker IS NOT NULL
        ORDER BY id DESC
        """
    ).fetchall()
    out: dict[str, WeatherContract] = {}
    for row in rows:
        ticker = str(row["market_ticker"] or "")
        if not ticker or ticker in out:
            continue
        payload = _json_payload(row["payload"])
        if not isinstance(payload, dict):
            continue
        try:
            out[ticker] = WeatherContract.model_validate(payload)
        except Exception:
            continue
    return out


def _replay_orderbook_index(conn: sqlite3.Connection, start: date | None, end: date | None) -> dict[str, dict[str, Any]]:
    clauses = ["market_ticker IS NOT NULL"]
    params: dict[str, Any] = {}
    if start:
        clauses.append("date(ts) >= :start")
        params["start"] = start.isoformat()
    if end:
        clauses.append("date(ts) <= :end")
        params["end"] = end.isoformat()
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"""
        SELECT market_ticker,
               COUNT(*) AS count,
               MIN(ts) AS first_ts,
               MAX(ts) AS latest_ts,
               SUM(CASE WHEN date(ts) IS NULL THEN 1 ELSE 0 END) AS invalid_ts_count,
               SUM(CASE WHEN market_close_time IS NOT NULL AND datetime(ts) <= datetime(market_close_time) THEN 1 ELSE 0 END) AS before_close_count,
               SUM(CASE WHEN market_close_time IS NOT NULL THEN 1 ELSE 0 END) AS close_metadata_count
        FROM orderbook_snapshots_live
        WHERE {where}
        GROUP BY market_ticker
        """,
        params,
    ).fetchall()
    return {str(row["market_ticker"]): dict(row) for row in rows}


def _replay_existing_index(conn: sqlite3.Connection, start: date | None, end: date | None) -> dict[str, dict[str, Any]]:
    clauses = ["market_ticker IS NOT NULL"]
    params: dict[str, Any] = {}
    if start:
        clauses.append("date(ts) >= :start")
        params["start"] = start.isoformat()
    if end:
        clauses.append("date(ts) <= :end")
        params["end"] = end.isoformat()
    rows = conn.execute(
        f"""
        SELECT market_ticker, COUNT(*) AS count, MIN(ts) AS first_ts, MAX(ts) AS latest_ts
        FROM recorded_orderbook_replay_snapshots
        WHERE {' AND '.join(clauses)}
        GROUP BY market_ticker
        """,
        params,
    ).fetchall()
    return {str(row["market_ticker"]): dict(row) for row in rows}


def _replay_tradability_summary(conn: sqlite3.Connection, start: date | None, end: date | None) -> dict[str, Any]:
    clauses = ["market_ticker IS NOT NULL"]
    params: dict[str, Any] = {}
    if start:
        clauses.append("date(ts) >= :start")
        params["start"] = start.isoformat()
    if end:
        clauses.append("date(ts) <= :end")
        params["end"] = end.isoformat()
    where = " AND ".join(clauses)
    tradable_expr = """
        (
            (minutes_to_settlement IS NULL OR minutes_to_settlement > 0)
            AND (
                minutes_to_close > 0
                OR (
                    minutes_to_close IS NULL
                    AND local_date IS NOT NULL
                    AND date(ts) IS NOT NULL
                    AND date(ts) <= local_date
                )
            )
        )
    """
    post_noise_expr = """
        (
            minutes_to_settlement <= 0
            OR minutes_to_close <= 0
            OR (
                minutes_to_close IS NULL
                AND local_date IS NOT NULL
                AND date(ts) IS NOT NULL
                AND date(ts) > local_date
            )
        )
    """
    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS total_replay_rows,
            SUM(CASE WHEN {tradable_expr} THEN 1 ELSE 0 END) AS tradable_pre_settlement_or_pre_day_end,
            SUM(CASE WHEN {post_noise_expr} THEN 1 ELSE 0 END) AS post_day_or_post_settlement_noise,
            SUM(CASE WHEN NOT ({tradable_expr}) AND NOT ({post_noise_expr}) THEN 1 ELSE 0 END) AS unknown_tradability_window,
            COUNT(DISTINCT CASE WHEN {post_noise_expr} THEN market_ticker END) AS affected_contracts
        FROM recorded_orderbook_replay_snapshots
        WHERE {where}
        """,
        params,
    ).fetchone()
    sample_rows = conn.execute(
        f"""
        SELECT market_ticker, COUNT(*) AS rows, MIN(ts) AS first_ts, MAX(ts) AS latest_ts, local_date
        FROM recorded_orderbook_replay_snapshots
        WHERE {where} AND {post_noise_expr}
        GROUP BY market_ticker, local_date
        ORDER BY rows DESC, market_ticker
        LIMIT 25
        """,
        params,
    ).fetchall()
    unknown_rows = conn.execute(
        f"""
        SELECT market_ticker, COUNT(*) AS rows, MIN(ts) AS first_ts, MAX(ts) AS latest_ts, local_date
        FROM recorded_orderbook_replay_snapshots
        WHERE {where} AND NOT ({tradable_expr}) AND NOT ({post_noise_expr})
        GROUP BY market_ticker, local_date
        ORDER BY rows DESC, market_ticker
        LIMIT 25
        """,
        params,
    ).fetchall()
    return {
        "total_replay_rows": int(row["total_replay_rows"] or 0) if row else 0,
        "tradable_pre_settlement_or_pre_day_end": int(row["tradable_pre_settlement_or_pre_day_end"] or 0) if row else 0,
        "post_day_or_post_settlement_noise": int(row["post_day_or_post_settlement_noise"] or 0) if row else 0,
        "unknown_tradability_window": int(row["unknown_tradability_window"] or 0) if row else 0,
        "affected_contracts": int(row["affected_contracts"] or 0) if row else 0,
        "sample_post_day_or_post_settlement_noise": [dict(item) for item in sample_rows],
        "sample_unknown_tradability_window": [dict(item) for item in unknown_rows],
        "filter_note": (
            "Recorded backtests/sweeps treat rows as tradable only when minutes_to_close > 0, "
            "or when close_time is missing and date(ts) <= local_date. Existing replay rows are left intact."
        ),
    }


def _json_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return value
    if value is None:
        return None
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return None


def _replay_confidence(value: Any) -> tuple[float | None, str | None]:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None, "confidence_field_mismatch"
    if confidence != confidence:
        return None, "confidence_field_mismatch"
    if confidence > 1.0:
        return confidence, "confidence_scale_suspicious"
    if confidence < 0.0:
        return confidence, "confidence_field_mismatch"
    return confidence, None


def _parse_date_value(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _date_in_window(value: date | None, start: date | None, end: date | None) -> bool:
    if value is None:
        return False
    if start and value < start:
        return False
    if end and value > end:
        return False
    return True


def _latest_weather_contracts(storage: Storage) -> list[WeatherContract]:
    frame = storage.fetch_table("parsed_contracts", limit=500000)
    if frame.empty:
        return []
    contracts: list[WeatherContract] = []
    seen: set[str] = set()
    for _, row in frame.sort_values("id", ascending=False).iterrows():
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = None
        if not isinstance(payload, dict):
            continue
        ticker = str(payload.get("market_ticker") or "")
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        try:
            contracts.append(WeatherContract.model_validate(payload))
        except Exception:
            continue
    return contracts


def _settlement_label_map(storage: Storage) -> dict[str, dict[str, Any]]:
    frame = storage.fetch_table("settlement_labels", limit=500000)
    if frame.empty:
        return {}
    return {str(row["market_ticker"]): row.to_dict() for _, row in frame.iterrows() if row.get("market_ticker")}


def _climate_report_map(storage: Storage) -> dict[tuple[str, str], dict[str, Any]]:
    frame = storage.fetch_table("nws_daily_climate_reports", limit=500000)
    if frame.empty:
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for _, row in frame.sort_values("id").iterrows():
        station = str(row.get("station_code") or "").upper()
        day = str(row.get("local_date") or "")
        if station and day:
            out[(station, day)] = row.to_dict()
    return out


def _observation_source_index(storage: Storage) -> set[tuple[str, str]]:
    frame = storage.fetch_sql(
        """
        SELECT station_code, date(ts_observed) AS day, COUNT(*) AS rows
        FROM weather_observation_snapshots_live
        WHERE station_code IS NOT NULL AND ts_observed IS NOT NULL
        GROUP BY station_code, day
        """
    )
    if frame.empty:
        return set()
    return {(str(row["station_code"]).upper(), str(row["day"])) for _, row in frame.iterrows() if row.get("day")}


def _orderbook_snapshot_index(storage: Storage) -> dict[str, dict[str, Any]]:
    frame = storage.fetch_sql(
        """
        SELECT market_ticker, COUNT(*) AS count, MIN(ts) AS first_ts, MAX(ts) AS latest_ts
        FROM orderbook_snapshots_live
        WHERE market_ticker IS NOT NULL
        GROUP BY market_ticker
        """
    )
    if frame.empty:
        return {}
    return {str(row["market_ticker"]): row.to_dict() for _, row in frame.iterrows()}


def _replay_snapshot_index(storage: Storage) -> dict[str, dict[str, Any]]:
    frame = storage.fetch_sql(
        """
        SELECT market_ticker, COUNT(*) AS count, MIN(ts) AS first_ts, MAX(ts) AS latest_ts
        FROM recorded_orderbook_replay_snapshots
        WHERE market_ticker IS NOT NULL
        GROUP BY market_ticker
        """
    )
    if frame.empty:
        return {}
    return {str(row["market_ticker"]): row.to_dict() for _, row in frame.iterrows()}


def _active_station_map(storage: Storage) -> dict[str, str]:
    frame = storage.fetch_sql(
        """
        SELECT market_ticker, station_code
        FROM active_weather_station_map
        WHERE market_ticker IS NOT NULL AND station_code IS NOT NULL
        ORDER BY id DESC
        """
    )
    out: dict[str, str] = {}
    if frame.empty:
        return out
    for _, row in frame.iterrows():
        ticker = str(row.get("market_ticker") or "")
        if ticker and ticker not in out:
            out[ticker] = str(row.get("station_code") or "").upper()
    return out


def _maturity_status(contract: WeatherContract, as_of: date) -> str:
    if contract.local_date is None:
        return "missing_date"
    if contract.local_date >= as_of:
        return "not_yet_mature"
    return "mature"


def _exact_settlement_eligible(contract: WeatherContract, mapping: Any | None) -> bool:
    if mapping is None or contract.local_date is None:
        return False
    if contract.variable_type not in {"high_temp", "low_temp"}:
        return False
    if contract.contract_type == "unknown":
        return False
    if contract.contract_type in {"threshold_above", "threshold_below"}:
        return contract.threshold is not None and contract.comparator in {"gt", "gte", "lt", "lte"}
    if contract.contract_type == "range_bucket":
        return contract.range_low is not None and contract.range_high is not None
    return False


def _settlement_blockers(
    contract: WeatherContract,
    mapping: Any | None,
    label: dict[str, Any] | None,
    climate: dict[tuple[str, str], dict[str, Any]],
    observations: set[tuple[str, str]],
    active_station_map: dict[str, str],
    maturity: str,
    min_confidence: float,
) -> list[str]:
    blockers: list[str] = []
    if label is not None:
        if int(label.get("exact_source_available") or 0) != 1:
            blockers.append("label_present_but_not_exact_source")
        if float(label.get("confidence") or 0.0) < min_confidence:
            blockers.append("label_present_but_below_primary_confidence")
    if _is_out_of_scope_weather(contract):
        blockers.append("unsupported_market_format")
        return list(dict.fromkeys(blockers))
    if contract.variable_type not in {"high_temp", "low_temp"}:
        blockers.append("unsupported_market_format")
    if contract.contract_type == "unknown":
        blockers.append("unsupported_market_format")
    if contract.local_date is None:
        blockers.append("missing_date")
    if mapping is None:
        blockers.append("missing_station")
    if contract.contract_type in {"threshold_above", "threshold_below"} and contract.threshold is None:
        blockers.append("missing_threshold")
    if contract.contract_type in {"threshold_above", "threshold_below"} and contract.comparator not in {"gt", "gte", "lt", "lte"}:
        blockers.append("missing_condition_operator")
    if contract.contract_type == "range_bucket" and (contract.range_low is None or contract.range_high is None):
        blockers.append("missing_threshold")
    if maturity == "not_yet_mature":
        blockers.append("not_yet_mature_settleable")
    if mapping is not None and not getattr(mapping, "timezone", None):
        blockers.append("timezone_date_ambiguity")
    if mapping is not None:
        mapped_station = active_station_map.get(contract.market_ticker)
        if mapped_station and mapped_station.upper() != mapping.station_code.upper():
            blockers.append("station_mismatch")
    if mapping is not None and contract.local_date is not None:
        day = contract.local_date.isoformat()
        station = mapping.station_code.upper()
        report = climate.get((station, day))
        if report is None:
            blockers.append("missing_climate_report_source")
        elif not _report_has_exact_value(contract, report):
            blockers.append("climate_report_missing_required_value")
        if (station, day) not in observations:
            blockers.append("missing_required_observation_source")
    if label is None and not blockers:
        blockers.append("eligible_but_unlabeled")
    return list(dict.fromkeys(blockers))


def _report_has_exact_value(contract: WeatherContract, report: dict[str, Any]) -> bool:
    if float(report.get("parser_confidence") or 0.0) < 0.85:
        return False
    if contract.variable_type == "high_temp":
        return report.get("parsed_high_temp") is not None and not pd.isna(report.get("parsed_high_temp"))
    if contract.variable_type == "low_temp":
        return report.get("parsed_low_temp") is not None and not pd.isna(report.get("parsed_low_temp"))
    return False


def _deterministic_buildable(
    contract: WeatherContract,
    mapping: Any | None,
    climate: dict[tuple[str, str], dict[str, Any]],
    label: dict[str, Any] | None,
    maturity: str,
    min_confidence: float,
) -> bool:
    if label is not None and float(label.get("confidence") or 0.0) >= min_confidence:
        return False
    if maturity != "mature" or not _exact_settlement_eligible(contract, mapping):
        return False
    report = climate.get((mapping.station_code.upper(), contract.local_date.isoformat())) if mapping and contract.local_date else None
    return bool(report and _report_has_exact_value(contract, report))


def _label_expansion_classification(
    contract: WeatherContract,
    mapping: Any | None,
    label: dict[str, Any] | None,
    blockers: list[str],
    maturity: str,
    min_confidence: float,
) -> str:
    if label is not None and int(label.get("exact_source_available") or 0) == 1 and float(label.get("confidence") or 0.0) >= min_confidence:
        return "already_high_confidence"
    if label is not None and int(label.get("exact_source_available") or 0) == 0 and float(label.get("confidence") or 0.0) < min_confidence:
        return "low_conf_fallback_only"
    if maturity == "not_yet_mature":
        return "future_not_mature"
    if _is_out_of_scope_weather(contract):
        return "not_weather_or_out_of_scope"
    if _looks_like_temperature_market(contract) and (
        contract.variable_type == "unknown"
        or contract.contract_type == "unknown"
        or "missing_threshold" in blockers
        or "missing_condition_operator" in blockers
    ):
        if "missing_date" in blockers or mapping is None:
            return "ambiguous_rules"
        return "parser_missing_but_settleable"
    if any(item in blockers for item in {"missing_date", "missing_station", "timezone_date_ambiguity", "station_mismatch"}):
        return "ambiguous_rules"
    if _exact_settlement_eligible(contract, mapping) and any(
        item in blockers
        for item in {
            "missing_climate_report_source",
            "missing_required_observation_source",
            "climate_report_missing_required_value",
            "eligible_but_unlabeled",
            "label_present_but_not_exact_source",
            "label_present_but_below_primary_confidence",
        }
    ):
        return "source_missing_but_parser_ok"
    if "unsupported_market_format" in blockers:
        return "ambiguous_rules"
    return "ambiguous_rules" if blockers else "source_missing_but_parser_ok"


def _primary_label_expansion_blocker(blockers: list[str]) -> str:
    priority = [
        "label_present_but_not_exact_source",
        "label_present_but_below_primary_confidence",
        "unsupported_market_format",
        "missing_station",
        "missing_date",
        "missing_threshold",
        "missing_condition_operator",
        "not_yet_mature_settleable",
        "station_mismatch",
        "timezone_date_ambiguity",
        "missing_climate_report_source",
        "climate_report_missing_required_value",
        "missing_required_observation_source",
        "eligible_but_unlabeled",
    ]
    for item in priority:
        if item in blockers:
            return item
    return blockers[0] if blockers else "ready_exact_label_available"


def _required_source_type(contract: WeatherContract) -> str:
    if contract.variable_type in {"high_temp", "low_temp"}:
        return "nws_daily_climate_report_exact_temperature"
    if contract.variable_type == "precipitation":
        return "unsupported_precipitation_exact_source"
    if contract.variable_type == "snowfall":
        return "unsupported_snowfall_exact_source"
    if contract.variable_type == "wind":
        return "unsupported_wind_exact_source"
    return "unknown_required_source"


def _ticker_prefix(ticker: str) -> str:
    text = str(ticker or "")
    if "-" in text:
        return text.split("-", 1)[0]
    return text[:12] if text else "missing"


def _threshold_key(contract: WeatherContract) -> str:
    if contract.contract_type == "range_bucket":
        if contract.range_low is None or contract.range_high is None:
            return "range_missing"
        return f"{contract.range_low:g}-{contract.range_high:g}{contract.unit or ''}"
    if contract.threshold is None:
        return "missing"
    return f"{contract.threshold:g}{contract.unit or ''}"


def _missing_station_pattern(contract: WeatherContract, mapping: Any | None) -> str:
    if _is_out_of_scope_weather(contract):
        return "station_inference_disabled_out_of_scope_or_non_weather"
    if mapping is not None:
        if contract.station_code:
            return "explicit_station_resolved"
        return "inferred_station_from_city"
    if contract.station_code:
        return "explicit_station_unresolved"
    if contract.city:
        return "city_present_no_station_mapping"
    if _looks_like_temperature_market(contract):
        return "temperature_market_missing_city_and_station"
    return "station_missing_out_of_scope_or_non_weather"


def _unsupported_format_key(contract: WeatherContract, classification: str) -> str:
    return "|".join(
        [
            _ticker_prefix(contract.market_ticker),
            contract.variable_type,
            contract.contract_type,
            contract.comparator,
            _threshold_key(contract),
            classification,
        ]
    )


def _suggested_parser_task(contract: WeatherContract, classification: str) -> str | None:
    if classification == "parser_missing_but_settleable":
        if contract.variable_type in {"high_temp", "low_temp"} and contract.contract_type == "unknown":
            return "Add parser coverage for explicit high/low temperature condition wording in title/rules; keep exact NWS source requirement unchanged."
        if contract.variable_type == "unknown" and _looks_like_temperature_market(contract):
            return "Add parser coverage to distinguish high vs low temperature wording before any label building."
        return "Review unsupported temperature format and add a parser fixture only if title/rules are unambiguous."
    if classification == "source_missing_but_parser_ok":
        return "Fetch or parse exact NWS Daily Climate Report rows for parser-valid mature contracts; do not promote hourly fallback labels."
    if classification == "low_conf_fallback_only":
        return "Fetch exact NWS Daily Climate Report evidence; do not promote fallback observation labels."
    if classification == "ambiguous_rules":
        return "Manual parser review required; do not label until station/date/condition ambiguity is resolved."
    return None


def _looks_like_temperature_market(contract: WeatherContract) -> bool:
    text = f"{contract.title} {contract.rules}".lower()
    return any(term in text for term in ["temperature", "temp", "degrees", "degree", "fahrenheit", "high", "low"])


def _is_out_of_scope_weather(contract: WeatherContract) -> bool:
    if contract.variable_type in {"precipitation", "snowfall", "wind"}:
        return True
    ticker = str(contract.market_ticker or "").upper()
    if is_out_of_scope_combo_ticker(ticker) or ticker.startswith(("KXNCA", "KXNBA", "KXMLB", "KXNFL", "KXNHL", "KXEPL")):
        return True
    text = f"{contract.title} {contract.rules}".lower()
    sports_terms = [
        "runs scored",
        "points scored",
        "wins by",
        "goals scored",
        "strikeouts",
        "rebounds",
        "assists",
        "hits",
        "home runs",
    ]
    if any(term in text for term in sports_terms):
        return True
    if any(term in text for term in ["rain", "precip", "snow", "wind"]):
        return True
    return False


def _short_text(value: str | None, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _counter_breakdown(groups: dict[str, Counter[str]]) -> list[dict[str, Any]]:
    rows = []
    for key, counter in groups.items():
        total = sum(counter.values())
        rows.append({"key": key, "total": total, "top_reasons": counter.most_common(10)})
    return sorted(rows, key=lambda row: row["total"], reverse=True)[:100]


def _replay_blocker_summary(
    contracts: list[WeatherContract],
    labels: dict[str, dict[str, Any]],
    orderbooks: dict[str, dict[str, Any]],
    replay_rows: dict[str, dict[str, Any]],
    min_confidence: float,
) -> dict[str, Any]:
    by_ticker = {contract.market_ticker: contract for contract in contracts}
    recorded_tickers = set(orderbooks)
    label_tickers = set(labels)
    overlap = recorded_tickers & set(by_ticker)
    labels_with_books = overlap & label_tickers
    high_conf_with_books = {
        ticker for ticker in labels_with_books if float(labels[ticker].get("confidence") or 0.0) >= min_confidence
    }
    low_conf_with_books = labels_with_books - high_conf_with_books
    no_label_with_books = overlap - label_tickers
    return {
        "parsed_contract_tickers": len(by_ticker),
        "recorded_orderbook_tickers": len(recorded_tickers),
        "snapshot_contract_overlap": len(overlap),
        "labels_available": len(label_tickers),
        "labels_with_orderbook_snapshots": len(labels_with_books),
        "high_confidence_labels_with_orderbook_snapshots": len(high_conf_with_books),
        "low_confidence_labels_with_orderbook_snapshots": len(low_conf_with_books),
        "recorded_overlap_missing_labels": len(no_label_with_books),
        "replay_tickers": len(replay_rows),
        "replay_rows": sum(int(item.get("count") or 0) for item in replay_rows.values()),
        "sample_missing_label_tickers": sorted(no_label_with_books)[:25],
        "sample_low_confidence_label_tickers": sorted(low_conf_with_books)[:25],
        "sample_high_confidence_replay_ready_tickers": sorted(high_conf_with_books)[:25],
    }


def _skip_reason(row) -> str:
    if not row.get("station_code"):
        return "station_mapping_missing"
    if row.get("contract_type") == "unknown":
        return "parser_unknown_contract_type"
    if row.get("variable_type") not in {"high_temp", "low_temp"}:
        return "unsupported_variable_type"
    if not row.get("local_date"):
        return "settlement_value_missing"
    try:
        if date.fromisoformat(str(row.get("local_date"))) >= date.today():
            return "date_not_resolved_yet"
    except ValueError:
        return "settlement_value_missing"
    return "nws_cli_report_not_found_or_hourly_observations_missing"


def _suggested_skip_fixes(skipped: pd.DataFrame) -> list[str]:
    reasons = skipped["reason"].value_counts().to_dict()
    fixes = []
    if reasons.get("station_mapping_missing"):
        fixes.append("Add or validate station mappings for the skipped cities.")
    if reasons.get("parser_unknown_contract_type"):
        fixes.append("Inspect skipped market titles/rules and extend parser tests.")
    if reasons.get("nws_cli_report_not_found_or_hourly_observations_missing"):
        fixes.append("Debug NWS CLI report lookup and IEM/ASOS fallback for the top skipped stations.")
    return fixes or ["No obvious single fix; inspect top skipped tickers manually."]


def _json_snippet(value: Any) -> Any:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return str(value)[:200]
    return parsed[:3] if isinstance(parsed, list) else parsed


def _market_making_summary_warning(path: Path | None = None) -> str | None:
    try:
        p = path or (PROJECT_ROOT / "reports" / "market_making_summary.json")
        if not p.exists():
            return "No market_making_summary.json found; run python main.py analyze-market-making to generate it."
        age_hours = (time.time() - p.stat().st_mtime) / 3600.0
        if age_hours > 24:
            return f"market_making_summary.json is {age_hours:.1f}h old; run python main.py analyze-market-making to refresh."
        return None
    except Exception:
        return None


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _last_by_station(frame: pd.DataFrame, column: str) -> dict[str, str]:
    if frame.empty or "station_code" not in frame or column not in frame:
        return {}
    values = frame.dropna(subset=["station_code", column]).copy()
    if values.empty:
        return {}
    values[column] = pd.to_datetime(values[column], errors="coerce")
    grouped = values.sort_values(column).groupby("station_code").tail(1)
    return {str(row["station_code"]): str(row[column]) for _, row in grouped.iterrows()}


def _daily_report_markdown(results: dict[str, Any]) -> str:
    readiness = results.get("trading_readiness", {})
    liquidity = results.get("liquidity", {}).get("summary", {})
    signal_summary = results.get("validate_signals", {}).get("summary_by_strategy", [])
    mm_summary = results.get("market_making", {}).get("summary", {})
    mm_verdict = mm_summary.get("market_making_verdict", "not_run")
    mm_candidates = int(mm_summary.get("paper_watchlist_candidates") or 0)
    mm_fills = int(mm_summary.get("trade_evidence_fills") or 0)
    lines = [
        "# Daily Trading Research Update",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Executive Summary",
        "",
        f"- Trading readiness: {readiness.get('status')}",
        f"- Message: {readiness.get('message')}",
        f"- Passive liquidity verdict: {liquidity.get('passive_verdict')}",
        f"- Market-making verdict: {mm_verdict} (paper_watchlist_candidates={mm_candidates}, trade_evidence_fills={mm_fills})",
        "- Live trading justified: no",
        "",
        "## Signal Validation",
        "",
    ]
    if signal_summary:
        for row in signal_summary:
            lines.append(f"- {row.get('strategy')}: signals={row.get('signal_count')} beat_30m={row.get('beat_30m_pct')} avg_future_edge={row.get('average_future_price_edge_cents')}")
    else:
        lines.append("- No validated signal rows.")
    lines.extend(
        [
            "",
            "## Data / Health",
            "",
            "```json",
            json.dumps(
                {
                    "collector_health": results.get("collector_health"),
                    "weather_recorder_health": results.get("weather_recorder_health"),
                    "audit": results.get("audit_recorded_data"),
                },
                indent=2,
                default=str,
            ),
            "```",
            "",
            "## What To Do Tomorrow",
            "",
            f"- Next command: {readiness.get('next_command')}",
            f"- Market-making track: {mm_verdict} — {mm_candidates} paper watchlist candidate(s), {mm_fills} trade-evidence fills.",
            "- Keep orderbook/weather recorders running if data gaps remain.",
            "- Do not trade real money.",
        ]
    )
    return "\n".join(lines)
