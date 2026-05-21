from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.fees import ConservativeFixedFeeModel
from config import PROJECT_ROOT
from data.storage import Storage
from models.weather_fair_value import WeatherFairValueModel
from parsing.weather_contract import WeatherContract
from research.edge_types import ALREADY_GUARANTEED_OR_IMPOSSIBLE_EDGE, FAIR_VALUE_TAKER_EDGE, confidence_level


@dataclass(frozen=True)
class WeatherEdgeMiningConfig:
    target: str | None = None
    market_ticker: str | None = None
    contract_type: str | None = None
    action: str | None = None
    city: str | None = None
    hypothesis: str | None = None
    min_entry_price_cents: float | None = None
    max_entry_price_cents: float | None = None
    min_local_hour: float | None = None
    max_local_hour: float | None = None
    min_edge_after_buffers_cents: float = 5.0
    min_data_quality: float = 0.55
    min_fair_confidence: float = 0.55
    min_settlement_confidence: float = 0.65
    max_observation_age_minutes: float = 90.0
    max_forecast_age_minutes: float = 360.0
    fee_cents: float = 1.0
    max_signals_per_market: int = 2
    signal_spacing_minutes: int = 60
    run_rule_search: bool = True
    rule_search_min_settled_signals: int = 5


@dataclass(frozen=True)
class WeatherEdgeMiningResult:
    summary: dict[str, Any]
    signals: list[dict[str, Any]]

    def to_text(self) -> str:
        lines = [
            f"weather_edge_mining_verdict={self.summary.get('verdict')}",
            f"message={self.summary.get('message')}",
            f"rows={self.summary.get('rows_scanned')} markets={self.summary.get('markets_scanned')} "
            f"eligible_rows={self.summary.get('eligible_rows')} signals={self.summary.get('signals')} "
            f"settled_signals={self.summary.get('settled_signals')}",
            f"market_ticker_filter={self.summary.get('market_ticker_filter')}",
            f"net_pnl={self.summary.get('net_pnl_cents'):.2f} fees={self.summary.get('fees_cents'):.2f} "
            f"win_rate={self.summary.get('win_rate'):.3f} avg_edge_after_buffers={self.summary.get('avg_edge_after_buffers_cents'):.2f}",
            f"future_mid_30m_beat_rate={_fmt_rate(self.summary.get('future_mid_30m_beat_rate'))} "
            f"future_mid_60m_beat_rate={_fmt_rate(self.summary.get('future_mid_60m_beat_rate'))} "
            f"future_mid_final_beat_rate={_fmt_rate(self.summary.get('future_mid_final_beat_rate'))}",
            f"stress={self.summary.get('stress')}",
            f"exports={self.summary.get('exports')}",
        ]
        segments = self.summary.get("segments_by_contract_action") or self.summary.get("settled_segments") or []
        if segments:
            lines.append("Top settled segments:")
            for row in segments[:5]:
                lines.append(
                    f"- contract_type={row.get('contract_type')} action={row.get('action')} "
                    f"signals={row.get('signals')} net={_fmt(row.get('net_pnl_cents'))} "
                    f"win_rate={_fmt_rate(row.get('win_rate'))} "
                    f"future30={_fmt_rate(row.get('future_mid_30m_beat_rate'))}"
                )
        rules = self.summary.get("rule_search_top") or []
        if rules:
            lines.append("Top discovery rule-search candidates:")
            for row in rules[:5]:
                lines.append(
                    f"- {row.get('rule')} all_net={_fmt(row.get('all_net_pnl_cents'))} "
                    f"all_signals={row.get('all_signals')} validation_net={_fmt(row.get('validation_net_pnl_cents'))} "
                    f"validation_signals={row.get('validation_signals')}"
                )
        lines.append("Top mined weather signals:")
        for row in self.signals[:15]:
            lines.append(
                f"- {row['market_ticker']} {row['action']} ts={row['ts']} "
                f"entry={_fmt(row.get('entry_price_cents'))} fair_yes={_fmt(row.get('fair_yes_price_cents'))} "
                f"edge_after_buffers={_fmt(row.get('edge_after_buffers_cents'))} hypothesis={row.get('hypothesis')} "
                f"future30={_fmt(row.get('future_edge_30m_cents'))} "
                f"pnl={_fmt(row.get('net_pnl_cents'))} reason={row.get('reason')}"
            )
        return "\n".join(lines)


class WeatherEdgeMiner:
    """Mine recorded weather replay rows for direct executable dislocations.

    This is deliberately simple and interpretable: apply the weather fair-value
    model to each as-of replay row, require real bid/ask execution, throttle
    repeated signals per market, and evaluate only against settlement labels
    that already exist. It is a proof-of-concept miner, not an optimizer.
    """

    def __init__(
        self,
        storage: Storage | None = None,
        model: WeatherFairValueModel | None = None,
        config: WeatherEdgeMiningConfig | None = None,
    ):
        self.storage = storage or Storage()
        self.model = model or WeatherFairValueModel()
        self.config = config or WeatherEdgeMiningConfig()
        self.fees = ConservativeFixedFeeModel(per_contract_cents=self.config.fee_cents)

    def mine(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        last_days: int | None = 3,
        market_ticker: str | None = None,
        persist_exports: bool = True,
    ) -> WeatherEdgeMiningResult:
        ticker_filter = market_ticker or self.config.market_ticker
        snapshots = self._load_snapshots(start=start, end=end, last_days=last_days, market_ticker=ticker_filter)
        signals = self._mine_signals(snapshots)
        summary = self._summary(snapshots, signals, market_ticker=ticker_filter)
        if persist_exports:
            summary["exports"] = self._export(summary, signals)
        else:
            summary["exports"] = None
        return WeatherEdgeMiningResult(summary=summary, signals=signals)

    def _load_snapshots(self, *, start: date | None, end: date | None, last_days: int | None, market_ticker: str | None = None) -> pd.DataFrame:
        self.storage.init_db()
        start, end = _date_window(start, end, last_days)
        clauses = ["variable_type IN ('high_temp', 'low_temp')"]
        params: dict[str, Any] = {}
        if market_ticker:
            clauses.append("market_ticker = :market_ticker")
            params["market_ticker"] = market_ticker
        if start:
            clauses.append("date(ts) >= :start")
            params["start"] = start.isoformat()
        if end:
            clauses.append("date(ts) <= :end")
            params["end"] = end.isoformat()
        return self.storage.fetch_sql(
            f"""
            SELECT *
            FROM recorded_orderbook_replay_snapshots
            WHERE {' AND '.join(clauses)}
            ORDER BY market_ticker, ts
            """,
            params,
        )

    def _mine_signals(self, snapshots: pd.DataFrame) -> list[dict[str, Any]]:
        if snapshots.empty:
            return []
        signals: list[dict[str, Any]] = []
        for ticker, group in snapshots.groupby("market_ticker", sort=False):
            accepted = 0
            last_signal_ts: datetime | None = None
            ordered_group = group.sort_values("ts")
            for _, row in ordered_group.iterrows():
                if accepted >= self.config.max_signals_per_market:
                    break
                ts = _parse_ts(row.get("ts"))
                if ts is None:
                    continue
                if last_signal_ts is not None and (ts - last_signal_ts).total_seconds() < self.config.signal_spacing_minutes * 60:
                    continue
                signal = self._signal_from_row(row)
                if signal is None:
                    continue
                self._attach_future_validation(signal, ordered_group, ts)
                signals.append(signal)
                accepted += 1
                last_signal_ts = ts
        signals.sort(key=lambda row: (row.get("edge_after_buffers_cents") or -999.0, row.get("edge_cents") or -999.0), reverse=True)
        return signals

    def _signal_from_row(self, row) -> dict[str, Any] | None:
        contract = _contract_from_row(row)
        if contract is None:
            return None
        if not _row_passes_static_filters(row, self.config):
            return None
        minutes_to_close = _num(row.get("minutes_to_close"))
        if minutes_to_close is not None and minutes_to_close <= 0:
            return None
        ts = _parse_ts(row.get("ts"))
        if ts is not None and _after_weather_day(row, ts):
            return None
        quality = _num(row.get("data_quality_score")) or 0.0
        settlement_conf = _num(row.get("settlement_confidence"))
        obs_age = _age_minutes(row.get("latest_observation_recorded_at"), ts)
        forecast_age = _age_minutes(row.get("latest_forecast_recorded_at"), ts)
        if obs_age is None or obs_age > self.config.max_observation_age_minutes:
            return None
        if forecast_age is not None and forecast_age > self.config.max_forecast_age_minutes:
            return None
        if quality < self.config.min_data_quality:
            return None
        if settlement_conf is not None and settlement_conf < self.config.min_settlement_confidence:
            return None
        fair = self.model.estimate(contract, row.to_dict())
        if fair.confidence < self.config.min_fair_confidence:
            return None
        yes_ask = _num(row.get("yes_best_ask"))
        no_ask = _num(row.get("no_best_ask"))
        buy_yes_edge = fair.fair_yes_price_cents - yes_ask if yes_ask is not None else None
        buy_no_edge = fair.fair_no_price_cents - no_ask if no_ask is not None else None
        side = _best_side(yes_ask, buy_yes_edge, no_ask, buy_no_edge)
        if side is None:
            return None
        action, entry, edge = side
        if self.config.action and action != self.config.action:
            return None
        if self.config.min_entry_price_cents is not None and entry < self.config.min_entry_price_cents:
            return None
        if self.config.max_entry_price_cents is not None and entry > self.config.max_entry_price_cents:
            return None
        edge_after_buffers = edge - fair.uncertainty_cents - self.config.fee_cents
        if edge_after_buffers < self.config.min_edge_after_buffers_cents:
            return None
        hypothesis = _hypothesis(row, fair.fair_yes_price_cents)
        if self.config.hypothesis and hypothesis != self.config.hypothesis:
            return None
        pnl = _settled_pnl(row, action, entry, self.fees)
        local_hour = _num(row.get("local_hour"))
        return {
            "market_ticker": str(row.get("market_ticker") or ""),
            "event_ticker": str(row.get("event_ticker") or ""),
            "ts": _parse_ts(row.get("ts")),
            "city": row.get("city"),
            "station_code": row.get("station_code"),
            "local_date": row.get("local_date"),
            "variable_type": row.get("variable_type"),
            "contract_type": row.get("contract_type"),
            "local_hour": local_hour,
            "local_hour_bucket": _hour_bucket(local_hour),
            "action": action,
            "entry_price_cents": entry,
            "entry_price_bucket": _price_bucket(entry),
            "fair_yes_price_cents": fair.fair_yes_price_cents,
            "fair_no_price_cents": fair.fair_no_price_cents,
            "edge_cents": edge,
            "uncertainty_cents": fair.uncertainty_cents,
            "edge_after_buffers_cents": edge_after_buffers,
            "fair_confidence": fair.confidence,
            "data_quality_score": quality,
            "observation_age_minutes": obs_age,
            "forecast_age_minutes": forecast_age,
            "settlement_confidence": settlement_conf,
            "yes_result": _int_or_none(row.get("yes_result")),
            "settled": pnl is not None,
            "gross_pnl_cents": pnl["gross_pnl_cents"] if pnl else None,
            "fees_cents": pnl["fees_cents"] if pnl else None,
            "net_pnl_cents": pnl["net_pnl_cents"] if pnl else None,
            "hypothesis": hypothesis,
            "edge_type": ALREADY_GUARANTEED_OR_IMPOSSIBLE_EDGE if hypothesis == "weather_locked" else FAIR_VALUE_TAKER_EDGE,
            "confidence_level": confidence_level(fair.confidence),
            "reason": fair.explanation,
            "raw_json": json.dumps({"fair": fair.to_dict(), "source_replay_id": _int_or_none(row.get("id"))}, default=str),
        }

    def _attach_future_validation(self, signal: dict[str, Any], group: pd.DataFrame, ts: datetime) -> None:
        if group.empty or "ts" not in group:
            return
        future = group.copy()
        future["_parsed_ts"] = future["ts"].map(_parse_ts)
        future = future[future["_parsed_ts"].map(lambda value: value is not None and value > ts)].sort_values("_parsed_ts")
        if future.empty:
            return
        for minutes in (30, 60):
            row = _first_future_row(future, ts + timedelta(minutes=minutes))
            if row is None:
                continue
            yes_mid = _yes_mid_from_row(row)
            _set_future_mid(signal, yes_mid, f"{minutes}m")
        final_yes_mid = _yes_mid_from_row(future.iloc[-1])
        _set_future_mid(signal, final_yes_mid, "final")

    def _summary(self, snapshots: pd.DataFrame, signals: list[dict[str, Any]], market_ticker: str | None = None) -> dict[str, Any]:
        settled = [row for row in signals if row.get("settled")]
        gross = sum(float(row.get("gross_pnl_cents") or 0.0) for row in settled)
        fees = sum(float(row.get("fees_cents") or 0.0) for row in settled)
        net = sum(float(row.get("net_pnl_cents") or 0.0) for row in settled)
        wins = sum(1 for row in settled if float(row.get("net_pnl_cents") or 0.0) > 0)
        stress = _stress(settled)
        verdict, message = _verdict(signals, settled, net, stress)
        segments_by_contract_action = _segments(settled, ["contract_type", "action"])
        rule_search = _rule_search(signals, self.config) if self.config.run_rule_search else []
        return {
            "verdict": verdict,
            "message": message,
            "rows_scanned": int(len(snapshots)),
            "markets_scanned": int(snapshots["market_ticker"].nunique()) if not snapshots.empty else 0,
            "market_ticker_filter": market_ticker,
            "eligible_rows": int(_eligible_count(snapshots, self.config)),
            "signals": len(signals),
            "settled_signals": len(settled),
            "gross_pnl_cents": gross,
            "fees_cents": fees,
            "net_pnl_cents": net,
            "win_rate": wins / len(settled) if settled else 0.0,
            "avg_edge_after_buffers_cents": sum(float(row.get("edge_after_buffers_cents") or 0.0) for row in signals) / len(signals) if signals else 0.0,
            "signals_by_hypothesis": _counts(signals, "hypothesis"),
            "settled_net_by_hypothesis": _net_by(settled, "hypothesis"),
            "settled_segments": segments_by_contract_action,
            "segments_by_contract_action": segments_by_contract_action,
            "segments_by_city_action": _segments(settled, ["city", "action"]),
            "segments_by_date_action": _segments(settled, ["local_date", "action"]),
            "segments_by_hour_action": _segments(settled, ["local_hour_bucket", "action"]),
            "segments_by_entry_bucket_action": _segments(settled, ["entry_price_bucket", "action"]),
            "future_mid_30m_beat_rate": _beat_rate(signals, "beat_future_30m"),
            "future_mid_60m_beat_rate": _beat_rate(signals, "beat_future_60m"),
            "future_mid_final_beat_rate": _beat_rate(signals, "beat_future_final"),
            "rule_search_top": rule_search[:20],
            "rule_search_note": "Discovery-only grid over mined signals; use validation columns and new dates before trusting any rule.",
            "stress": stress,
            "config": self.config.__dict__,
        }

    def _export(self, summary: dict[str, Any], signals: list[dict[str, Any]]) -> dict[str, str]:
        reports = PROJECT_ROOT / "reports"
        reports.mkdir(exist_ok=True)
        signals_path = reports / "weather_edge_mining_signals.csv"
        summary_path = reports / "weather_edge_mining_summary.json"
        rules_path = reports / "weather_edge_rule_search.csv"
        exports = {"signals": str(signals_path), "summary": str(summary_path), "rule_search": str(rules_path)}
        summary["exports"] = exports
        pd.DataFrame(signals).to_csv(signals_path, index=False)
        pd.DataFrame(summary.get("rule_search_top") or []).to_csv(rules_path, index=False)
        summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        return exports


def _contract_from_row(row) -> WeatherContract | None:
    try:
        return WeatherContract(
            event_ticker=str(row.get("event_ticker") or ""),
            market_ticker=str(row.get("market_ticker") or ""),
            city=_str_or_none(row.get("city")),
            station_code=_str_or_none(row.get("station_code")),
            local_date=_date_or_none(row.get("local_date")),
            variable_type=str(row.get("variable_type") or "unknown"),
            contract_type=str(row.get("contract_type") or "unknown"),
            threshold=_num(row.get("threshold")),
            comparator=str(row.get("comparator") or "unknown"),
            range_low=_num(row.get("range_low")),
            range_high=_num(row.get("range_high")),
            parse_confidence=_num(row.get("data_quality_score")) or 0.75,
            station_confidence=_num(row.get("settlement_confidence")) or _num(row.get("data_quality_score")) or 0.75,
            parser_version=str(row.get("parser_version") or ""),
        )
    except Exception:
        return None


def _row_passes_static_filters(row, cfg: WeatherEdgeMiningConfig) -> bool:
    if cfg.contract_type and str(row.get("contract_type") or "") != cfg.contract_type:
        return False
    if cfg.city and str(row.get("city") or "").lower() != cfg.city.lower():
        return False
    local_hour = _num(row.get("local_hour"))
    if cfg.min_local_hour is not None and (local_hour is None or local_hour < cfg.min_local_hour):
        return False
    if cfg.max_local_hour is not None and (local_hour is None or local_hour > cfg.max_local_hour):
        return False
    return True


def _best_side(
    yes_ask: float | None,
    buy_yes_edge: float | None,
    no_ask: float | None,
    buy_no_edge: float | None,
) -> tuple[str, float, float] | None:
    choices: list[tuple[str, float, float]] = []
    if yes_ask is not None and buy_yes_edge is not None:
        choices.append(("BUY_YES", yes_ask, buy_yes_edge))
    if no_ask is not None and buy_no_edge is not None:
        choices.append(("BUY_NO", no_ask, buy_no_edge))
    if not choices:
        return None
    return max(choices, key=lambda item: item[2])


def _settled_pnl(row, action: str, entry_price: float, fees: ConservativeFixedFeeModel) -> dict[str, float] | None:
    yes_result = _int_or_none(row.get("yes_result"))
    if yes_result is None:
        return None
    if action == "BUY_YES":
        gross = 100.0 - entry_price if yes_result == 1 else -entry_price
    else:
        gross = 100.0 - entry_price if yes_result == 0 else -entry_price
    fee = fees.fee_cents(int(round(entry_price)), 1.0)
    return {"gross_pnl_cents": gross, "fees_cents": fee, "net_pnl_cents": gross - fee}


def _hypothesis(row, fair_yes: float) -> str:
    already = int(_num(row.get("is_threshold_already_hit_asof")) or 0) == 1
    impossible_bucket = row.get("contract_type") == "range_bucket" and fair_yes <= 2.0
    near_certain = fair_yes >= 98.0 or fair_yes <= 2.0
    if already or impossible_bucket or near_certain:
        return "weather_locked"
    if _num(row.get("forecast_high_remaining_f")) is not None or _num(row.get("forecast_low_remaining_f")) is not None:
        return "forecast_fair_value"
    return "asof_weather_fair_value"


def _after_weather_day(row, ts: datetime) -> bool:
    local_date = _date_or_none(row.get("local_date"))
    if local_date is None:
        return False
    if ts.date() > local_date + timedelta(days=1):
        return True
    local_hour = _num(row.get("local_hour"))
    if ts.date() > local_date and local_hour is not None and local_hour < 6:
        return True
    return False


def _first_future_row(future: pd.DataFrame, target: datetime):
    rows = future[future["_parsed_ts"].map(lambda value: value is not None and value >= target)]
    if rows.empty:
        return None
    return rows.iloc[0]


def _yes_mid_from_row(row) -> float | None:
    mid = _num(row.get("yes_mid"))
    if mid is not None:
        return mid
    bid = _num(row.get("yes_best_bid"))
    ask = _num(row.get("yes_best_ask"))
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def _set_future_mid(signal: dict[str, Any], yes_mid: float | None, label: str) -> None:
    if yes_mid is None:
        return
    key = label.replace("m", "")
    if label == "final":
        mid_key = "future_yes_mid_final"
        edge_key = "future_edge_final_cents"
        beat_key = "beat_future_final"
    else:
        mid_key = f"future_yes_mid_{key}m"
        edge_key = f"future_edge_{key}m_cents"
        beat_key = f"beat_future_{key}m"
    signal[mid_key] = yes_mid
    edge = _future_edge(signal.get("action"), _num(signal.get("entry_price_cents")), yes_mid)
    signal[edge_key] = edge
    signal[beat_key] = None if edge is None else edge > 0


def _future_edge(action: Any, entry: float | None, future_yes_mid: float | None) -> float | None:
    if entry is None or future_yes_mid is None:
        return None
    if action == "BUY_YES":
        return future_yes_mid - entry
    if action == "BUY_NO":
        return 100.0 - future_yes_mid - entry
    return None


def _hour_bucket(local_hour: float | None) -> str:
    if local_hour is None:
        return "unknown"
    if local_hour < 9:
        return "pre_9"
    if local_hour < 12:
        return "9_12"
    if local_hour < 15:
        return "12_15"
    if local_hour < 18:
        return "15_18"
    return "18_plus"


def _price_bucket(price: float | None) -> str:
    if price is None:
        return "unknown"
    if price <= 5:
        return "0_5"
    if price <= 10:
        return "5_10"
    if price <= 20:
        return "10_20"
    if price <= 40:
        return "20_40"
    if price <= 60:
        return "40_60"
    return "60_plus"


def _eligible_count(frame: pd.DataFrame, cfg: WeatherEdgeMiningConfig) -> int:
    if frame.empty:
        return 0
    quality = pd.to_numeric(frame.get("data_quality_score", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    yes_ask = pd.to_numeric(frame.get("yes_best_ask", pd.Series(dtype=float)), errors="coerce")
    no_ask = pd.to_numeric(frame.get("no_best_ask", pd.Series(dtype=float)), errors="coerce")
    tradable = (quality >= cfg.min_data_quality).astype(bool) & (yes_ask.notna() | no_ask.notna())
    if cfg.contract_type and "contract_type" in frame:
        tradable = tradable & (frame["contract_type"].astype(str) == cfg.contract_type)
    if cfg.city and "city" in frame:
        tradable = tradable & (frame["city"].astype(str).str.lower() == cfg.city.lower())
    if "local_hour" in frame:
        local_hour = pd.to_numeric(frame["local_hour"], errors="coerce")
        if cfg.min_local_hour is not None:
            tradable = tradable & local_hour.ge(cfg.min_local_hour)
        if cfg.max_local_hour is not None:
            tradable = tradable & local_hour.le(cfg.max_local_hour)
    if "latest_observation_recorded_at" in frame and "ts" in frame:
        ts = pd.to_datetime(frame["ts"], errors="coerce", utc=True)
        obs_ts = pd.to_datetime(frame["latest_observation_recorded_at"], errors="coerce", utc=True)
        obs_age = (ts - obs_ts).dt.total_seconds() / 60.0
        tradable = tradable & obs_age.notna() & obs_age.le(cfg.max_observation_age_minutes)
    if "latest_forecast_recorded_at" in frame and "ts" in frame:
        ts = pd.to_datetime(frame["ts"], errors="coerce", utc=True)
        forecast_ts = pd.to_datetime(frame["latest_forecast_recorded_at"], errors="coerce", utc=True)
        forecast_age = (ts - forecast_ts).dt.total_seconds() / 60.0
        tradable = tradable & (forecast_age.isna() | forecast_age.le(cfg.max_forecast_age_minutes))
    return int(tradable.sum())


def _stress(settled: list[dict[str, Any]]) -> dict[str, float | str | None]:
    if not settled:
        return {
            "two_x_fees_net_pnl": None,
            "worse_fill_1c_net_pnl": None,
            "exclude_best_signal_net_pnl": None,
            "verdict": "no settled signals",
        }
    net_values = sorted([float(row.get("net_pnl_cents") or 0.0) for row in settled], reverse=True)
    gross = sum(float(row.get("gross_pnl_cents") or 0.0) for row in settled)
    fees = sum(float(row.get("fees_cents") or 0.0) for row in settled)
    two_x_fees = gross - 2.0 * fees
    worse_fill = sum(float(row.get("net_pnl_cents") or 0.0) - 1.0 for row in settled)
    exclude_best = sum(net_values) - net_values[0]
    verdict = "passes basic stress"
    if two_x_fees <= 0:
        verdict = "fails 2x fees"
    elif worse_fill <= 0:
        verdict = "fails 1c worse fills"
    elif exclude_best <= 0:
        verdict = "depends on best signal"
    return {
        "two_x_fees_net_pnl": two_x_fees,
        "worse_fill_1c_net_pnl": worse_fill,
        "exclude_best_signal_net_pnl": exclude_best,
        "verdict": verdict,
    }


def _verdict(signals: list[dict[str, Any]], settled: list[dict[str, Any]], net: float, stress: dict[str, Any]) -> tuple[str, str]:
    if not signals:
        return "NO_MINED_WEATHER_SIGNALS", "No executable weather dislocations passed the current quality and buffer gates."
    if len(settled) < 10:
        return "RESEARCH_ONLY_NEED_MORE_SETTLED_SIGNALS", "Signals exist, but too few have settlement labels for a useful P&L read."
    if net <= 0:
        return "REJECTED_LOSES_AFTER_FEES", "Mined signals lose after fees on settled rows."
    if len(settled) < 30:
        return "RESEARCH_ONLY_SMALL_SAMPLE", "Positive settled P&L, but sample size is too small for paper readiness."
    if stress.get("verdict") != "passes basic stress":
        return "RESEARCH_ONLY_STRESS_FAILED", "Positive P&L did not survive simple stress checks."
    future30 = _beat_rate(signals, "beat_future_30m")
    future_final = _beat_rate(signals, "beat_future_final")
    if _non_null_count(signals, "beat_future_30m") >= 10 and future30 is not None and future30 < 0.50:
        return "RESEARCH_ONLY_WEAK_FUTURE_PRICE_CONFIRMATION", "Positive settlement P&L, but signals usually did not beat the 30-minute future mid."
    if _non_null_count(signals, "beat_future_final") >= 10 and future_final is not None and future_final < 0.50:
        return "RESEARCH_ONLY_WEAK_FUTURE_PRICE_CONFIRMATION", "Positive settlement P&L, but signals usually did not beat later recorded mids."
    return "PAPER_WATCHLIST_CANDIDATE", "Mined weather edge candidate found; paper only, no live trading."


def _counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        item = str(row.get(key) or "unknown")
        result[item] = result.get(item, 0) + 1
    return result


def _net_by(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in rows:
        item = str(row.get(key) or "unknown")
        result[item] = result.get(item, 0.0) + float(row.get("net_pnl_cents") or 0.0)
    return result


def _segments(rows: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = tuple(str(row.get(item) or "unknown") for item in keys)
        buckets.setdefault(key, []).append(row)
    result: list[dict[str, Any]] = []
    for key, items in buckets.items():
        net_values = [float(item.get("net_pnl_cents") or 0.0) for item in items]
        result.append(
            {
                **{keys[idx]: key[idx] for idx in range(len(keys))},
                "signals": len(items),
                "net_pnl_cents": sum(net_values),
                "win_rate": sum(1 for value in net_values if value > 0) / len(net_values) if net_values else 0.0,
                "avg_edge_after_buffers_cents": sum(float(item.get("edge_after_buffers_cents") or 0.0) for item in items) / len(items),
                "future_mid_30m_beat_rate": _beat_rate(items, "beat_future_30m"),
                "future_mid_60m_beat_rate": _beat_rate(items, "beat_future_60m"),
                "future_mid_final_beat_rate": _beat_rate(items, "beat_future_final"),
                "stress": _stress(items),
            }
        )
    return sorted(result, key=lambda item: (item["net_pnl_cents"], item["signals"]), reverse=True)


def _rule_search(signals: list[dict[str, Any]], cfg: WeatherEdgeMiningConfig) -> list[dict[str, Any]]:
    settled = [row for row in signals if row.get("settled")]
    if len(settled) < cfg.rule_search_min_settled_signals:
        return []
    pairs = sorted(
        {
            (str(row.get("contract_type") or "unknown"), str(row.get("action") or "unknown"))
            for row in settled
        }
    )
    edge_levels = sorted({cfg.min_edge_after_buffers_cents, 10.0, 15.0, 20.0, 25.0})
    max_entries = [5.0, 10.0, 20.0, 30.0, 40.0, 60.0]
    min_hours = [None, 9.0, 12.0, 14.0, 16.0]
    rows: list[dict[str, Any]] = []
    discovery_dates, validation_dates = _date_split(settled)
    for contract_type, action in pairs:
        for min_edge in edge_levels:
            for max_entry in max_entries:
                for min_hour in min_hours:
                    filtered = [
                        row
                        for row in settled
                        if row.get("contract_type") == contract_type
                        and row.get("action") == action
                        and float(row.get("edge_after_buffers_cents") or 0.0) >= min_edge
                        and float(row.get("entry_price_cents") or 999.0) <= max_entry
                        and (min_hour is None or (_num(row.get("local_hour")) is not None and _num(row.get("local_hour")) >= min_hour))
                    ]
                    if len(filtered) < cfg.rule_search_min_settled_signals:
                        continue
                    discovery = [row for row in filtered if _date_or_none(row.get("local_date")) in discovery_dates]
                    validation = [row for row in filtered if _date_or_none(row.get("local_date")) in validation_dates]
                    all_metrics = _rule_metrics(filtered)
                    discovery_metrics = _rule_metrics(discovery)
                    validation_metrics = _rule_metrics(validation)
                    rule = {
                        "rule": _rule_name(contract_type, action, min_edge, max_entry, min_hour),
                        "contract_type": contract_type,
                        "action": action,
                        "min_edge_after_buffers_cents": min_edge,
                        "max_entry_price_cents": max_entry,
                        "min_local_hour": min_hour,
                        **{f"all_{key}": value for key, value in all_metrics.items()},
                        **{f"discovery_{key}": value for key, value in discovery_metrics.items()},
                        **{f"validation_{key}": value for key, value in validation_metrics.items()},
                    }
                    rows.append(rule)
    return sorted(
        rows,
        key=lambda item: (
            item.get("validation_net_pnl_cents") if item.get("validation_signals") else item.get("all_net_pnl_cents"),
            item.get("validation_signals") or 0,
            item.get("all_net_pnl_cents") or 0.0,
        ),
        reverse=True,
    )


def _date_split(rows: list[dict[str, Any]]) -> tuple[set[date], set[date]]:
    dates = sorted({item for item in (_date_or_none(row.get("local_date")) for row in rows) if item is not None})
    if len(dates) < 2:
        return set(dates), set()
    split = max(1, len(dates) // 2)
    return set(dates[:split]), set(dates[split:])


def _rule_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    net_values = [float(row.get("net_pnl_cents") or 0.0) for row in rows]
    return {
        "signals": len(rows),
        "net_pnl_cents": sum(net_values),
        "win_rate": sum(1 for value in net_values if value > 0) / len(net_values) if net_values else 0.0,
        "avg_edge_after_buffers_cents": sum(float(row.get("edge_after_buffers_cents") or 0.0) for row in rows) / len(rows) if rows else 0.0,
        "future_mid_30m_beat_rate": _beat_rate(rows, "beat_future_30m"),
        "stress_verdict": _stress(rows)["verdict"] if rows else "no settled signals",
    }


def _rule_name(contract_type: str, action: str, min_edge: float, max_entry: float, min_hour: float | None) -> str:
    hour = "any_hour" if min_hour is None else f"hour>={min_hour:g}"
    return f"{contract_type} {action} edge>={min_edge:g} entry<={max_entry:g} {hour}"


def _beat_rate(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row.get(key) for row in rows if row.get(key) is not None]
    if not values:
        return None
    return sum(1 for value in values if bool(value)) / len(values)


def _non_null_count(rows: list[dict[str, Any]], key: str) -> int:
    return sum(1 for row in rows if row.get(key) is not None)


def _date_window(start: date | None, end: date | None, last_days: int | None) -> tuple[date | None, date | None]:
    if last_days is None:
        return start, end
    end_date = end or date.today()
    return end_date - timedelta(days=max(last_days, 1)), end_date


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age_minutes(then_value: Any, now: datetime) -> float | None:
    then = _parse_ts(then_value)
    if then is None:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then).total_seconds() / 60.0


def _date_or_none(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _str_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if value != value:
        return None
    return str(value)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _fmt(value: Any) -> str:
    number = _num(value)
    return "none" if number is None else f"{number:.2f}"


def _fmt_rate(value: Any) -> str:
    number = _num(value)
    return "none" if number is None else f"{number:.3f}"
