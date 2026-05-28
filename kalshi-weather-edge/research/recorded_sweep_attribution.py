from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import text

from backtest.recorded_backtester import TRADABLE_REPLAY_WHERE
from data.storage import Storage
from data.weather_settlement_loader import SETTLEMENT_VERSION
from parsing.weather_contract import PARSER_VERSION
from research.signal_validation import SignalValidator


@dataclass(frozen=True)
class RecordedSweepAttributionResult:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.payload

    def to_text(self) -> str:
        p = self.payload
        lines = [
            f"Recorded sweep attribution: {p['verdict']}",
            f"window={p['window']} label_quality={p['label_quality']}",
            f"strategy_variants_tested={p['strategy_variants_tested']}",
            f"variants_with_trades={p['variants_with_trades']}",
            f"variants_with_zero_trades={p['variants_with_zero_trades']}",
            f"variants_blocked_by_no_signal={p['variants_blocked_by_no_signal']}",
            f"variants_blocked_by_threshold_already_hit={p['variants_blocked_by_threshold_already_hit']}",
            f"variants_blocked_by_spread_liquidity={p['variants_blocked_by_spread_liquidity']}",
            f"variants_blocked_by_missing_future_mid_validation={p['variants_blocked_by_missing_future_mid_validation']}",
            f"variants_blocked_by_too_few_observations={p['variants_blocked_by_too_few_observations']}",
            f"variants_with_positive_gross_edge_but_negative_after_costs={p['variants_with_positive_gross_edge_but_negative_after_costs']}",
            f"variants_with_positive_apparent_edge_but_too_few_samples={p['variants_with_positive_apparent_edge_but_too_few_samples']}",
            f"variants_with_too_much_adverse_selection={p['variants_with_too_much_adverse_selection']}",
            f"validate_signals_empty_reason={p['validate_signals'].get('empty_reason')}",
        ]
        lines.append("Blocker reason counts:")
        for reason, count in p["blocker_reason_counts"]:
            lines.append(f"- {reason}: {count}")
        lines.append("Per-strategy family:")
        for row in p["per_strategy_family"]:
            lines.append(
                f"- {row['family']}: variants={row['variants']} signals={row['signals']} fills={row['fills']} "
                f"gross={row['gross_pnl']:.2f} net={row['net_pnl']:.2f} win_rate={row['win_rate']:.2%} "
                f"beat_30m={_fmt_optional_pct(row.get('future_mid_30m_beat_rate'))} blocker={row['blocker_reason']}"
            )
        for title, key in [
            ("Best gross edge but not trusted", "best_gross_edge_but_not_trusted"),
            ("Best net edge but under sample threshold", "best_net_edge_but_under_sample_threshold"),
            ("Most active but no edge", "most_active_but_no_edge"),
            ("Highest adverse selection", "highest_adverse_selection"),
            ("Least bad candidates", "least_bad_candidates"),
        ]:
            lines.append(f"{title}:")
            rows = p["top_tables"].get(key, [])
            if not rows:
                lines.append("- none")
                continue
            for row in rows:
                lines.append(
                    f"- {row['strategy']} {row['mode']} fills={row['fills']} signals={row['signals']} "
                    f"gross={row['gross_pnl']:.2f} net={row['net_pnl']:.2f} "
                    f"blocker={row['primary_blocker']}"
                )
        lines.append("Policy: diagnostic only; paper readiness still comes only from trading-readiness.")
        return "\n".join(lines)


class RecordedSweepAttributionReporter:
    def __init__(self, storage: Storage | None = None):
        self.storage = storage or Storage()

    def report(
        self,
        start: date | None = None,
        end: date | None = None,
        last_days: int | None = 7,
        label_quality: str = "primary",
        top_n: int = 10,
    ) -> RecordedSweepAttributionResult:
        start, end = _date_window(start, end, last_days)
        sweeps = self._load_sweeps(start, end, label_quality)
        validation = SignalValidator(self.storage).validate(start=start, end=end, last_days=None)
        validation_by_strategy = {str(row.get("strategy")): row for row in validation.summary_by_strategy}
        if sweeps.empty:
            payload = {
                "verdict": "NO_PAPER_EDGE_FOUND",
                "window": _window_payload(start, end),
                "label_quality": label_quality,
                "strategy_variants_tested": 0,
                "empty_reason": "no_current_version_recorded_strategy_sweeps_in_window",
                "validate_signals": validation.to_dict(),
                "policy_note": "Diagnostic only; run trading-readiness for paper policy.",
            }
            return RecordedSweepAttributionResult(payload)

        variants = _latest_variant_rows(sweeps)
        threshold_blocked = self._threshold_already_hit_variant_keys(variants, start, end, label_quality)
        rows = [
            _variant_from_row(row, validation_by_strategy, validation.empty_reason, threshold_blocked)
            for row in variants.to_dict("records")
        ]
        blocker_counter: Counter[str] = Counter()
        flag_counter: Counter[str] = Counter()
        for row in rows:
            blocker_counter[row["primary_blocker"]] += 1
            flag_counter.update(row["blocker_flags"])

        payload = {
            "verdict": "NO_PAPER_EDGE_FOUND",
            "window": _window_payload(start, end),
            "label_quality": label_quality,
            "strategy_variants_tested": len(rows),
            "variants_with_trades": sum(1 for row in rows if row["fills"] > 0),
            "variants_with_zero_trades": sum(1 for row in rows if row["fills"] == 0),
            "variants_blocked_by_no_signal": flag_counter["no_signal"],
            "variants_blocked_by_threshold_already_hit": flag_counter["threshold_already_hit"],
            "variants_blocked_by_spread_liquidity": flag_counter["spread_liquidity"],
            "variants_blocked_by_missing_future_mid_validation": flag_counter["missing_future_mid_validation"],
            "variants_blocked_by_too_few_observations": flag_counter["too_few_observations"],
            "variants_with_positive_gross_edge_but_negative_after_costs": flag_counter["gross_positive_net_negative"],
            "variants_with_positive_apparent_edge_but_too_few_samples": flag_counter["apparent_edge_low_sample"],
            "variants_with_too_much_adverse_selection": flag_counter["too_much_adverse_selection"],
            "blocker_reason_counts": blocker_counter.most_common(),
            "per_strategy_family": _family_summary(rows, validation_by_strategy),
            "top_tables": _top_tables(rows, top_n),
            "validate_signals": validation.to_dict(),
            "policy_note": "Diagnostic only; does not create paper candidates and does not override trading-readiness.",
        }
        return RecordedSweepAttributionResult(payload)

    def _load_sweeps(self, start: date | None, end: date | None, label_quality: str) -> pd.DataFrame:
        clauses = [
            "COALESCE(is_stale, 0) = 0",
            "COALESCE(parser_version, '') = :parser_version",
            "COALESCE(settlement_version, '') = :settlement_version",
            "COALESCE(label_quality, '') = :label_quality",
        ]
        params: dict[str, Any] = {
            "parser_version": PARSER_VERSION,
            "settlement_version": SETTLEMENT_VERSION,
            "label_quality": label_quality,
        }
        if start:
            clauses.append("date(ts) >= :start")
            params["start"] = start.isoformat()
        if end:
            clauses.append("date(ts) <= :end")
            params["end"] = end.isoformat()
        return _read_sql(
            self.storage,
            f"SELECT * FROM recorded_strategy_sweeps WHERE {' AND '.join(clauses)} ORDER BY ts ASC, id ASC",
            params,
        )

    def _threshold_already_hit_variant_keys(
        self,
        variants: pd.DataFrame,
        start: date | None,
        end: date | None,
        label_quality: str,
    ) -> set[str]:
        late_day = variants[
            variants.get("strategy", pd.Series(dtype=object)).eq("late_day_high_fade")
            & pd.to_numeric(variants.get("signals", pd.Series(dtype=float)), errors="coerce").fillna(0).eq(0)
        ]
        if late_day.empty:
            return set()
        replay = self._load_replay_context(start, end, label_quality)
        if replay.empty:
            return set()
        result: set[str] = set()
        for row in late_day.to_dict("records"):
            params = _params_for_row(row)
            frame = replay.copy()
            frame = frame[
                frame.get("variable_type", pd.Series(dtype=object)).eq("high_temp")
                & frame.get("contract_type", pd.Series(dtype=object)).eq("threshold_above")
                & frame.get("comparator", pd.Series(dtype=object)).isin(["gt", "gte"])
                & pd.to_numeric(frame.get("local_hour", pd.Series(dtype=float)), errors="coerce").ge(float(params.get("min_local_hour", 14)))
                & pd.to_numeric(frame.get("threshold_gap_max_so_far", pd.Series(dtype=float)), errors="coerce").ge(float(params.get("min_gap", 2)))
                & pd.to_numeric(frame.get("temp_trend_1h", pd.Series(dtype=float)), errors="coerce").fillna(0).le(float(params.get("max_trend_1h", 0.5)))
                & frame.get("no_best_ask", pd.Series(dtype=object)).notna()
            ]
            if frame.empty:
                continue
            hit = pd.to_numeric(frame.get("is_threshold_already_hit_asof", pd.Series(dtype=float)), errors="coerce").fillna(0).eq(1)
            if float(hit.mean()) >= 0.8:
                result.add(_variant_key(row))
        return result

    def _load_replay_context(self, start: date | None, end: date | None, label_quality: str) -> pd.DataFrame:
        clauses = [TRADABLE_REPLAY_WHERE]
        params: dict[str, Any] = {}
        if start:
            clauses.append("date(ts) >= :start")
            params["start"] = start.isoformat()
        if end:
            clauses.append("date(ts) <= :end")
            params["end"] = end.isoformat()
        threshold = {"primary": 0.85, "exploratory": 0.65, "all": -1.0}.get(label_quality, 0.85)
        if threshold >= 0:
            clauses.append("settlement_confidence >= :settlement_confidence")
            params["settlement_confidence"] = threshold
        return _read_sql(
            self.storage,
            f"""
            SELECT market_ticker, ts, variable_type, contract_type, comparator, local_hour,
                   threshold_gap_max_so_far, temp_trend_1h, no_best_ask, is_threshold_already_hit_asof
            FROM recorded_orderbook_replay_snapshots
            WHERE {' AND '.join(clauses)}
            """,
            params,
        )


def _variant_from_row(
    row: dict[str, Any],
    validation_by_strategy: dict[str, dict[str, Any]],
    validation_empty_reason: str | None,
    threshold_blocked: set[str],
) -> dict[str, Any]:
    summary, trades = _raw_summary_and_trades(row)
    strategy = str(row.get("strategy") or summary.get("strategy") or "unknown")
    mode = str(row.get("mode") or summary.get("mode") or "unknown")
    fills = int(_num(row.get("fills")) or 0)
    signals = int(_num(row.get("signals")) or 0)
    gross = float(_num(row.get("gross_pnl")) or 0.0)
    fees = float(_num(row.get("fees")) or 0.0)
    net = float(_num(row.get("net_pnl")) or 0.0)
    win_rate = float(_num(row.get("win_rate")) or 0.0)
    avg_edge = float(_num(summary.get("average_edge_cents")) or 0.0)
    validation = validation_by_strategy.get(strategy)
    adverse_proxy = _adverse_selection_proxy(row, summary, trades)
    flags: list[str] = []
    if signals == 0:
        flags.append("no_signal")
    if _variant_key(row) in threshold_blocked:
        flags.append("threshold_already_hit")
    if strategy == "wide_spread_passive" and (signals == 0 or fills == 0):
        flags.append("spread_liquidity")
    if fills > 0 and (validation is None or validation_empty_reason):
        flags.append("missing_future_mid_validation")
    if fills > 0 and fills < 30:
        flags.append("too_few_observations")
    if gross > 0 and net <= 0:
        flags.append("gross_positive_net_negative")
    if net > 0 and fills < 30:
        flags.append("apparent_edge_low_sample")
    if adverse_proxy >= 2.0:
        flags.append("too_much_adverse_selection")
    if fills > 0 and net <= 0:
        flags.append("no_net_edge")
    if mode == "signal_only":
        flags.append("signal_only_no_pnl")
    if not flags:
        flags.append("data_insufficient")
    primary = _primary_blocker(flags)
    return {
        "variant_key": _variant_key(row),
        "strategy": strategy,
        "mode": mode,
        "params": _params_for_row(row),
        "signals": signals,
        "fills": fills,
        "gross_pnl": gross,
        "fees": fees,
        "net_pnl": net,
        "roi": float(_num(row.get("roi")) or 0.0),
        "win_rate": win_rate,
        "average_edge_cents": avg_edge,
        "recommendation": str(row.get("recommendation") or ""),
        "robustness_verdict": str(row.get("robustness_verdict") or summary.get("robustness_verdict") or ""),
        "adverse_selection_proxy_cents": adverse_proxy,
        "blocker_flags": flags,
        "primary_blocker": primary,
    }


def _primary_blocker(flags: list[str]) -> str:
    priority = [
        "no_signal",
        "threshold_already_hit",
        "spread_liquidity",
        "gross_positive_net_negative",
        "too_much_adverse_selection",
        "no_net_edge",
        "too_few_observations",
        "apparent_edge_low_sample",
        "missing_future_mid_validation",
        "signal_only_no_pnl",
    ]
    for item in priority:
        if item in flags:
            return item
    return flags[0] if flags else "data_insufficient"


def _family_summary(rows: list[dict[str, Any]], validation_by_strategy: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    families: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        families.setdefault(f"{row['strategy']}/{row['mode']}", []).append(row)
    output: list[dict[str, Any]] = []
    for family, items in families.items():
        fills = sum(row["fills"] for row in items)
        weighted_win = sum(row["win_rate"] * row["fills"] for row in items)
        blocker = Counter(row["primary_blocker"] for row in items).most_common(1)[0][0]
        strategy = items[0]["strategy"]
        validation = validation_by_strategy.get(strategy, {})
        output.append(
            {
                "family": family,
                "variants": len(items),
                "signals": sum(row["signals"] for row in items),
                "fills": fills,
                "gross_pnl": sum(row["gross_pnl"] for row in items),
                "fees": sum(row["fees"] for row in items),
                "net_pnl": sum(row["net_pnl"] for row in items),
                "average_edge_cents": sum(row["average_edge_cents"] for row in items) / len(items),
                "win_rate": weighted_win / fills if fills else 0.0,
                "sample_count": fills,
                "future_mid_30m_beat_rate": validation.get("beat_30m_pct"),
                "blocker_reason": blocker,
            }
        )
    return sorted(output, key=lambda row: (row["net_pnl"], row["fills"]), reverse=True)


def _top_tables(rows: list[dict[str, Any]], top_n: int) -> dict[str, list[dict[str, Any]]]:
    top_n = max(int(top_n or 10), 1)
    least_bad_source = [row for row in rows if row["signals"] > 0 or row["fills"] > 0] or rows
    return {
        "best_gross_edge_but_not_trusted": _compact(
            sorted([row for row in rows if row["gross_pnl"] > 0 and row["primary_blocker"] != "data_insufficient"], key=lambda row: row["gross_pnl"], reverse=True)[:top_n]
        ),
        "best_net_edge_but_under_sample_threshold": _compact(
            sorted([row for row in rows if row["net_pnl"] > 0 and row["fills"] < 30], key=lambda row: row["net_pnl"], reverse=True)[:top_n]
        ),
        "most_active_but_no_edge": _compact(
            sorted([row for row in rows if row["fills"] > 0 and row["net_pnl"] <= 0], key=lambda row: (row["fills"], row["net_pnl"]), reverse=True)[:top_n]
        ),
        "highest_adverse_selection": _compact(
            sorted([row for row in rows if row["adverse_selection_proxy_cents"] > 0], key=lambda row: row["adverse_selection_proxy_cents"], reverse=True)[:top_n]
        ),
        "least_bad_candidates": _compact(sorted(least_bad_source, key=lambda row: (row["net_pnl"], row["fills"], row["signals"]), reverse=True)[:top_n]),
    }


def _compact(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = [
        "strategy",
        "mode",
        "params",
        "signals",
        "fills",
        "gross_pnl",
        "fees",
        "net_pnl",
        "win_rate",
        "average_edge_cents",
        "adverse_selection_proxy_cents",
        "primary_blocker",
        "blocker_flags",
    ]
    return [{key: row.get(key) for key in keys} for row in rows]


def _latest_variant_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    rows = frame.copy()
    rows["_variant_key"] = rows.apply(lambda row: _variant_key(row.to_dict()), axis=1)
    rows["_parsed_ts"] = pd.to_datetime(rows.get("ts"), errors="coerce", utc=True)
    rows = rows.sort_values(["_parsed_ts", "id"], na_position="first")
    return rows.groupby("_variant_key", as_index=False, dropna=False).tail(1).drop(columns=["_variant_key", "_parsed_ts"], errors="ignore")


def _raw_summary_and_trades(row: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = row.get("raw_json")
    if not raw:
        return {}, []
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}, []
    if not isinstance(payload, dict):
        return {}, []
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    trades = payload.get("trades") if isinstance(payload.get("trades"), list) else []
    return summary, [item for item in trades if isinstance(item, dict)]


def _params_for_row(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("params_json")
    if raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}
    summary, _ = _raw_summary_and_trades(row)
    params = summary.get("params")
    return params if isinstance(params, dict) else {}


def _adverse_selection_proxy(row: dict[str, Any], summary: dict[str, Any], trades: list[dict[str, Any]]) -> float:
    future_edges = [_num(trade.get("future_price_edge_cents")) for trade in trades]
    negative_edges = [abs(value) for value in future_edges if value is not None and value < 0]
    if negative_edges:
        return float(sum(negative_edges) / len(negative_edges))
    robustness = str(row.get("robustness_verdict") or summary.get("robustness_verdict") or "").lower()
    robust = summary.get("robustness") if isinstance(summary.get("robustness"), dict) else {}
    if "fails 1-cent worse fills" in robustness or "fails 1-cent worse fills" in str(robust.get("verdict", "")).lower():
        return 2.0
    fills = int(_num(row.get("fills")) or 0)
    net = float(_num(row.get("net_pnl")) or 0.0)
    if fills > 0 and net < 0 and "passive" in str(row.get("mode") or ""):
        return abs(net) / fills
    return 0.0


def _variant_key(row: dict[str, Any]) -> str:
    value = row.get("parameter_hash")
    if value:
        return str(value)
    params = json.dumps(_params_for_row(row), sort_keys=True, default=str)
    return f"{row.get('strategy')}|{row.get('mode')}|{row.get('label_quality')}|{params}"


def _read_sql(storage: Storage, sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    try:
        with storage.engine.connect() as conn:
            return pd.read_sql_query(text(sql), conn, params=params or {})
    except Exception:
        return pd.DataFrame()


def _date_window(start: date | None, end: date | None, last_days: int | None) -> tuple[date | None, date | None]:
    if last_days is None:
        return start, end
    end_date = end or date.today()
    return end_date - timedelta(days=max(last_days, 1)), end_date


def _window_payload(start: date | None, end: date | None) -> dict[str, str | None]:
    return {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None}


def _fmt_optional_pct(value: Any) -> str:
    number = _num(value)
    return "missing" if number is None else f"{number:.2%}"


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
