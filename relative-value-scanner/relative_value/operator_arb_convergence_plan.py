from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_KIND = "operator_arb_convergence_plan_v1"

ACTION_OPERATOR_REVIEW = "OPERATOR_ARB_PAPER_REVIEW"
ACTION_RESIDUAL_REVIEW = "RESIDUAL_RISK_SHADOW_PAPER_REVIEW"
ACTION_MANUAL_REVIEW = "MANUAL_REVIEW"
ACTION_WATCH = "WATCH"

PLAN_ENTER_MONITOR = "ENTER_AND_MONITOR_FOR_CONVERGENCE"
PLAN_HOLD = "HOLD_TO_SETTLEMENT_ACCEPTABLE"
PLAN_EXIT_NOW = "EXIT_NOW_REVIEW"
PLAN_EXIT_TARGET_MET = "EXIT_TARGET_ALREADY_MET_REVIEW"
PLAN_WATCH = "WATCH_ONLY"
PLAN_IGNORE_LOW_RETURN = "IGNORE_LOW_RETURN"
PLAN_IGNORE_SIZE = "IGNORE_INSUFFICIENT_SIZE"
PLAN_MANUAL = "MANUAL_REVIEW_REQUIRED"

B_UNSUPPORTED_SCHEMA = "unsupported_operator_arb_report_schema"
B_MISSING_ENTRY_PRICE = "missing_entry_price"
B_MISSING_NET_EDGE = "missing_net_edge"
B_MISSING_FEE = "missing_fee_model"
B_MISSING_SIZE = "missing_available_notional"
B_EXIT_BIDS_MISSING = "exit_bid_data_missing"
B_EXIT_FEE_REVIEW = "exit_fee_review_required"
B_TARGET_EXIT_NOT_PLAUSIBLE = "target_exit_pair_value_above_normal_payoff"
B_SMALL_LONG_DATED_EDGE = "edge_too_small_for_long_hold"

SUPPORTED_SCHEMAS = {
    "sports_mlb_world_series_operator_arb_scout_v1",
    "sports_mlb_world_series_residual_risk_scout_v1",
    "sports_mlb_daily_residual_risk_scout_v1",
}

INCLUDED_ACTIONS = {ACTION_OPERATOR_REVIEW, ACTION_RESIDUAL_REVIEW, ACTION_MANUAL_REVIEW, ACTION_WATCH}
RESIDUAL_RISK_NOTE_BLOCKERS = {
    "proportional_payout_vs_other_outcome_mismatch",
    "remote_tail_risk_human_accepted_but_not_exact",
}
MAJOR_REVIEW_BLOCKERS = {
    "stale_or_missing_quote",
    "missing_quote",
    "missing_or_uncertain_fee_model",
    "missing_fee_model",
    "missing_kalshi_size",
    "missing_polymarket_size",
    "quote_size_unit_review_required",
    "unclear_kalshi_size_units",
    "unclear_polymarket_size_units",
    "partial_or_missing_depth",
    "missing_quote_depth",
    "missing_kalshi_no_quote",
    "missing_polymarket_no_quote",
    "ambiguous_polymarket_no_quote",
}
INSUFFICIENT_SIZE_BLOCKERS = {
    "insufficient_available_notional",
    "available_notional_below_minimum",
}


def write_operator_arb_convergence_plan_files(
    *,
    input_report: Path,
    json_output: Path,
    markdown_output: Path,
    target_exit_edge: float,
    min_hold_net_edge: float,
    min_annualized_return: float,
    settlement_date: str | None = None,
    max_capital_tieup_days: int | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_operator_arb_convergence_plan(
        input_report=input_report,
        target_exit_edge=target_exit_edge,
        min_hold_net_edge=min_hold_net_edge,
        min_annualized_return=min_annualized_return,
        settlement_date=settlement_date,
        max_capital_tieup_days=max_capital_tieup_days,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_operator_arb_convergence_plan_markdown(report), encoding="utf-8")
    return report


def build_operator_arb_convergence_plan(
    *,
    input_report: Path,
    target_exit_edge: float,
    min_hold_net_edge: float,
    min_annualized_return: float,
    settlement_date: str | None = None,
    max_capital_tieup_days: int | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    payload = _read_json(input_report)
    schema = str(payload.get("schema_kind") or "")
    settlement = _parse_date(settlement_date) if settlement_date else None
    days_to_settlement = _days_to_settlement(generated, settlement)
    parameters = {
        "target_exit_edge": target_exit_edge,
        "min_hold_net_edge": min_hold_net_edge,
        "min_annualized_return": min_annualized_return,
        "settlement_date": settlement.isoformat() if settlement else None,
        "max_capital_tieup_days": max_capital_tieup_days,
    }

    if schema not in SUPPORTED_SCHEMAS:
        rows = [_unsupported_schema_row(schema=schema, input_report=input_report)]
    else:
        rows = [
            _plan_row(
                source=row,
                source_schema=schema,
                report=payload,
                generated_at=generated,
                settlement=settlement,
                days_to_settlement=days_to_settlement,
                target_exit_edge=target_exit_edge,
                min_hold_net_edge=min_hold_net_edge,
                min_annualized_return=min_annualized_return,
                max_capital_tieup_days=max_capital_tieup_days,
            )
            for row in _candidate_source_rows(payload)
        ]
    rows.sort(key=_row_sort_key)
    summary = _summary(rows)
    return {
        "schema_kind": SCHEMA_KIND,
        "diagnostic_only": True,
        "execution_recommendation_only": True,
        "standard_paper_candidate_emitted": False,
        "exact_ready_rows": 0,
        "standard_paper_candidate_rows": 0,
        "input_report": str(input_report),
        "input_schema_kind": schema or None,
        "generated_at": generated.isoformat(),
        "parameters": parameters,
        "rows": rows,
        "summary_counts": summary,
        "top_blockers": summary["top_blockers"],
        "safety": {
            "diagnostic_only": True,
            "execution_recommendation_only": True,
            "orders_or_execution_logic_added": False,
            "candidate_pair_creation": False,
            "global_exact_arb_gates_changed": False,
            "standard_paper_candidate_emitted": False,
            "exact_ready_rows": 0,
        },
    }


def render_operator_arb_convergence_plan_markdown(report: dict[str, Any]) -> str:
    counts = report.get("summary_counts") or {}
    params = report.get("parameters") or {}
    rows = report.get("rows") or []
    lines = [
        "# Operator Arb Convergence / Exit Plan",
        "",
        "This report separates hold-to-settlement edge from convergence/early-exit attractiveness.",
        "It is diagnostic only and does not recommend or place trades.",
        "",
        "## Summary",
        "",
        f"- input_report: `{_md(report.get('input_report'))}`",
        f"- rows: `{counts.get('rows', 0)}`",
        f"- exit_now_review_rows: `{counts.get('exit_now_review_rows', 0)}`",
        f"- exit_target_already_met_rows: `{counts.get('exit_target_already_met_rows', 0)}`",
        f"- enter_and_monitor_rows: `{counts.get('enter_and_monitor_rows', 0)}`",
        f"- hold_to_settlement_rows: `{counts.get('hold_to_settlement_rows', 0)}`",
        f"- manual_review_rows: `{counts.get('manual_review_rows', 0)}`",
        f"- watch_rows: `{counts.get('watch_rows', 0)}`",
        f"- ignore_low_return_rows: `{counts.get('ignore_low_return_rows', 0)}`",
        f"- ignore_insufficient_size_rows: `{counts.get('ignore_insufficient_size_rows', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- standard_paper_candidate_rows: `0`",
        f"- target_exit_edge: `{_md(params.get('target_exit_edge'))}`",
        f"- min_holding_annualized_return: `{_md(params.get('min_annualized_return'))}`",
        "",
        "## Plan Rows",
        "",
        "| Plan | Net hold edge | Immediate unwind after fees | Current exit value | Target exit distance | Annualized return | Available notional | Team/Outcome | Direction | Blockers |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in rows[:40]:
        lines.append(
            "| "
            f"{_md(row.get('recommended_plan'))} | "
            f"{_fmt(row.get('net_hold_edge'))} | "
            f"{_fmt(row.get('immediate_unwind_pnl_after_estimated_fees'))} | "
            f"{_fmt(row.get('current_exit_value_if_available'))} | "
            f"{_fmt(row.get('target_exit_distance'))} | "
            f"{_fmt(row.get('annualized_return_estimate'))} | "
            f"{_fmt(row.get('available_notional'))} | "
            f"{_md(row.get('team_or_outcome'))} | "
            f"{_md(row.get('direction'))} | "
            f"{_md(', '.join(row.get('blockers') or []))} |"
        )
    if not rows:
        lines.append("| none |  |  |  |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Exit And Target Distance",
            "",
            "Immediate unwind values use bid-side data only. Target exit distance is the additional pair value needed to reach the configured target exit value.",
            "",
            "## Tiny Long-Dated Edges",
            "",
            "A small positive edge held for months can be weak after fees and capital tie-up. "
            "Rows with low annualized hold return are only interesting as convergence monitors, "
            "and early exit still requires real bid-side data on both legs.",
            "",
            "## Top Blockers",
            "",
            "| Blocker | Count |",
            "|---|---:|",
        ]
    )
    for item in report.get("top_blockers") or []:
        lines.append(f"| {_md(item.get('blocker'))} | {_md(item.get('count'))} |")
    if not report.get("top_blockers"):
        lines.append("| none | 0 |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- execution_recommendation_only: `true`",
            "- no order placement, cancellation, account, balance, position, auth, or execution logic",
            "- exact_ready_rows: `0`",
            "- standard_paper_candidate_emitted: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _candidate_source_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in report.get("rows") or []:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "")
        gross = _float_or_none(row.get("gross_edge"))
        if action in {ACTION_OPERATOR_REVIEW, ACTION_RESIDUAL_REVIEW, ACTION_MANUAL_REVIEW}:
            rows.append(row)
        elif action == ACTION_WATCH and gross is not None and gross > 0:
            rows.append(row)
    return rows


def _plan_row(
    *,
    source: dict[str, Any],
    source_schema: str,
    report: dict[str, Any],
    generated_at: datetime,
    settlement: date | None,
    days_to_settlement: int | None,
    target_exit_edge: float,
    min_hold_net_edge: float,
    min_annualized_return: float,
    max_capital_tieup_days: int | None,
) -> dict[str, Any]:
    source_row_id = str(source.get("row_id") or source.get("cross_platform_game_key") or "unknown")
    entry_legs = _entry_legs(source)
    entry_prices = [leg.get("ask") for leg in entry_legs if leg.get("ask") is not None]
    entry_cost = round(sum(entry_prices), 6) if len(entry_prices) == 2 else None
    gross_hold_edge = _float_or_none(source.get("gross_edge"))
    if gross_hold_edge is None and entry_cost is not None:
        gross_hold_edge = round(1.0 - entry_cost, 6)
    fee_estimate = _float_or_none(source.get("conservative_fee_estimate"))
    net_hold_edge = _float_or_none(source.get("net_edge"))
    available_notional = _float_or_none(source.get("available_notional") or source.get("available_size"))
    return_on_capital = (
        round(net_hold_edge / entry_cost, 8)
        if net_hold_edge is not None and entry_cost is not None and entry_cost > 0
        else None
    )
    annualized = (
        round(return_on_capital * 365.0 / days_to_settlement, 8)
        if return_on_capital is not None and days_to_settlement and days_to_settlement > 0
        else None
    )
    estimated_total_net_profit = (
        round(net_hold_edge * available_notional, 6)
        if net_hold_edge is not None and available_notional is not None
        else None
    )
    target_exit_pair_value = round(entry_cost + target_exit_edge, 6) if entry_cost is not None else None
    estimated_exit_fees = fee_estimate
    target_exit_profit_per_unit = (
        round(target_exit_pair_value - entry_cost - estimated_exit_fees, 6)
        if target_exit_pair_value is not None and entry_cost is not None and estimated_exit_fees is not None
        else None
    )
    target_exit_return = (
        round(target_exit_profit_per_unit / entry_cost, 8)
        if target_exit_profit_per_unit is not None and entry_cost is not None and entry_cost > 0
        else None
    )
    current_exit_value, exit_bid_status = _current_exit_value(source)
    immediate_unwind_before = _float_or_none(source.get("immediate_unwind_pnl_before_fees"))
    if immediate_unwind_before is None and current_exit_value is not None and entry_cost is not None:
        immediate_unwind_before = round(current_exit_value - entry_cost, 6)
    immediate_unwind_after = _float_or_none(source.get("immediate_unwind_pnl_after_estimated_fees"))
    exit_fee_status = str(source.get("exit_fee_status") or "NOT_CALCULATED")
    target_exit_distance = (
        round(target_exit_pair_value - current_exit_value, 6)
        if target_exit_pair_value is not None and current_exit_value is not None
        else None
    )
    target_exit_distance_status = "available" if target_exit_distance is not None else "exit_bid_data_missing"
    blockers = list(dict.fromkeys([*source.get("blockers", []), *source.get("quote_blockers", [])]))
    if entry_cost is None:
        blockers.append(B_MISSING_ENTRY_PRICE)
    if net_hold_edge is None:
        blockers.append(B_MISSING_NET_EDGE)
    if fee_estimate is None:
        blockers.append(B_MISSING_FEE)
    if available_notional is None:
        blockers.append(B_MISSING_SIZE)
    if exit_bid_status != "available":
        blockers.append(B_EXIT_BIDS_MISSING)
    if immediate_unwind_before is not None and immediate_unwind_before > 0 and immediate_unwind_after is None:
        blockers.append(B_EXIT_FEE_REVIEW)
    if target_exit_pair_value is not None and target_exit_pair_value > 1.0:
        blockers.append(B_TARGET_EXIT_NOT_PLAUSIBLE)
    recommended_plan, notes = _recommended_plan(
        net_hold_edge=net_hold_edge,
        annualized=annualized,
        available_notional=available_notional,
        blockers=blockers,
        current_exit_value=current_exit_value,
        immediate_unwind_before=immediate_unwind_before,
        immediate_unwind_after=immediate_unwind_after,
        exit_fee_status=exit_fee_status,
        target_exit_distance=target_exit_distance,
        target_exit_pair_value=target_exit_pair_value,
        min_hold_net_edge=min_hold_net_edge,
        min_annualized_return=min_annualized_return,
        days_to_settlement=days_to_settlement,
        max_capital_tieup_days=max_capital_tieup_days,
    )
    blockers = list(dict.fromkeys(blockers))
    if recommended_plan == PLAN_ENTER_MONITOR and B_SMALL_LONG_DATED_EDGE not in blockers:
        blockers.append(B_SMALL_LONG_DATED_EDGE)
    return {
        "row_id": f"convergence:{source_row_id}",
        "source_row_id": source_row_id,
        "source_action": source.get("action"),
        "market_family": _market_family(source_schema, report),
        "event_key": _event_key(source_schema, report, source),
        "team_or_outcome": source.get("team_name") or source.get("team_or_outcome") or source.get("game"),
        "direction": source.get("direction"),
        "entry_legs": entry_legs,
        "entry_cost": entry_cost,
        "normal_settlement_payoff": 1.0,
        "gross_hold_edge": gross_hold_edge,
        "fee_estimate": fee_estimate,
        "estimated_exit_fees": estimated_exit_fees,
        "net_hold_edge": net_hold_edge,
        "available_notional": available_notional,
        "estimated_total_net_profit_at_size": estimated_total_net_profit,
        "settlement_date": settlement.isoformat() if settlement else None,
        "days_to_settlement": days_to_settlement,
        "return_on_capital": return_on_capital,
        "annualized_return_estimate": annualized,
        "target_exit_pair_value": target_exit_pair_value,
        "target_exit_edge": target_exit_edge,
        "target_exit_profit_per_unit": target_exit_profit_per_unit,
        "target_exit_return_on_capital": target_exit_return,
        "current_exit_value_if_available": current_exit_value,
        "immediate_unwind_pnl_before_fees": immediate_unwind_before,
        "immediate_unwind_pnl_after_estimated_fees": immediate_unwind_after,
        "exit_bid_data_status": exit_bid_status,
        "target_exit_distance": target_exit_distance,
        "target_exit_distance_status": target_exit_distance_status,
        "convergence_required": target_exit_distance,
        "exit_bid_legs": source.get("exit_bid_legs") or [],
        "recommended_plan": recommended_plan,
        "blockers": blockers,
        "notes": [*notes, *_string_list(source.get("residual_risk_notes"))],
        "operator_arb_review_only": True,
        "standard_paper_candidate": False,
        "exact_ready": False,
    }


def _unsupported_schema_row(*, schema: str, input_report: Path) -> dict[str, Any]:
    return {
        "row_id": "convergence:unsupported_schema",
        "source_row_id": None,
        "source_action": None,
        "market_family": "unknown",
        "event_key": None,
        "team_or_outcome": None,
        "direction": None,
        "entry_legs": [],
        "entry_cost": None,
        "normal_settlement_payoff": 1.0,
        "gross_hold_edge": None,
        "fee_estimate": None,
        "net_hold_edge": None,
        "available_notional": None,
        "estimated_total_net_profit_at_size": None,
        "days_to_settlement": None,
        "return_on_capital": None,
        "annualized_return_estimate": None,
        "target_exit_pair_value": None,
        "target_exit_edge": None,
        "target_exit_profit_per_unit": None,
        "current_exit_value_if_available": None,
        "immediate_unwind_pnl_before_fees": None,
        "immediate_unwind_pnl_after_estimated_fees": None,
        "exit_bid_data_status": "not_calculated",
        "target_exit_distance": None,
        "target_exit_distance_status": "not_calculated",
        "exit_bid_legs": [],
        "recommended_plan": PLAN_MANUAL,
        "blockers": [B_UNSUPPORTED_SCHEMA],
        "notes": [f"Unsupported report schema `{schema or 'missing'}` in {input_report}; failed closed."],
        "operator_arb_review_only": True,
        "standard_paper_candidate": False,
        "exact_ready": False,
    }


def _recommended_plan(
    *,
    net_hold_edge: float | None,
    annualized: float | None,
    available_notional: float | None,
    blockers: list[str],
    current_exit_value: float | None,
    immediate_unwind_before: float | None,
    immediate_unwind_after: float | None,
    exit_fee_status: str,
    target_exit_distance: float | None,
    target_exit_pair_value: float | None,
    min_hold_net_edge: float,
    min_annualized_return: float,
    days_to_settlement: int | None,
    max_capital_tieup_days: int | None,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if available_notional is None or available_notional <= 0 or any(blocker in blockers for blocker in INSUFFICIENT_SIZE_BLOCKERS):
        return PLAN_IGNORE_SIZE, notes
    if any(blocker in blockers for blocker in MAJOR_REVIEW_BLOCKERS) or B_MISSING_FEE in blockers:
        return PLAN_MANUAL, notes
    if immediate_unwind_after is not None and immediate_unwind_after > 0:
        return PLAN_EXIT_NOW, notes
    if immediate_unwind_before is not None and immediate_unwind_before > 0 and (immediate_unwind_after is None or exit_fee_status != "OK"):
        return PLAN_MANUAL, notes
    if current_exit_value is not None and target_exit_distance is not None and target_exit_distance <= 0:
        return PLAN_EXIT_TARGET_MET, notes
    if net_hold_edge is None:
        return PLAN_MANUAL, notes
    if net_hold_edge <= 0:
        return PLAN_IGNORE_LOW_RETURN, notes
    if net_hold_edge >= min_hold_net_edge and annualized is not None and annualized >= min_annualized_return:
        return PLAN_HOLD, notes
    long_tieup = bool(days_to_settlement and max_capital_tieup_days and days_to_settlement > max_capital_tieup_days)
    low_annualized = annualized is None or annualized < min_annualized_return
    if long_tieup and low_annualized:
        notes.append("edge too small for long hold; only attractive if expected convergence/exit occurs sooner")
    if target_exit_pair_value is not None and target_exit_pair_value <= 1.0:
        return PLAN_ENTER_MONITOR, notes
    return PLAN_WATCH, notes


def _entry_legs(row: dict[str, Any]) -> list[dict[str, Any]]:
    kalshi_ask = _float_or_none(row.get("kalshi_ask") or row.get("kalshi_price"))
    poly_ask = _float_or_none(row.get("polymarket_ask") or row.get("polymarket_price"))
    kalshi_leg = row.get("kalshi_leg") if isinstance(row.get("kalshi_leg"), dict) else {}
    poly_leg = row.get("polymarket_leg") if isinstance(row.get("polymarket_leg"), dict) else {}
    return [
        {
            "platform": "Kalshi",
            "side": kalshi_leg.get("side"),
            "ask": kalshi_ask,
            "market_ticker": kalshi_leg.get("market_ticker") or row.get("kalshi_ticker"),
        },
        {
            "platform": "Polymarket",
            "side": poly_leg.get("side"),
            "ask": poly_ask,
            "market_id": poly_leg.get("market_id") or row.get("polymarket_market_id"),
            "condition_id": poly_leg.get("condition_id") or row.get("polymarket_condition_id"),
            "yes_token_id": poly_leg.get("yes_token_id") or row.get("polymarket_yes_token_id"),
            "no_token_id": poly_leg.get("no_token_id") or row.get("polymarket_no_token_id"),
        },
    ]


def _current_exit_value(row: dict[str, Any]) -> tuple[float | None, str]:
    direct_value = _float_or_none(row.get("current_exit_pair_value"))
    direct_status = str(row.get("current_exit_pair_value_status") or "").strip().lower()
    if direct_value is not None and direct_status == "available":
        return direct_value, "available"
    if direct_status in {"missing", "partial"}:
        return None, f"exit_bid_data_{direct_status}"
    kalshi_bid = _float_or_none(row.get("kalshi_exit_bid") or row.get("kalshi_bid"))
    poly_bid = _float_or_none(row.get("polymarket_exit_bid") or row.get("polymarket_bid"))
    if kalshi_bid is None or poly_bid is None:
        return None, "exit_bid_data_missing"
    return round(kalshi_bid + poly_bid, 6), "available"


def _market_family(schema: str, report: dict[str, Any]) -> str:
    if schema in {"sports_mlb_world_series_operator_arb_scout_v1", "sports_mlb_world_series_residual_risk_scout_v1"}:
        return "sports_mlb_world_series_championship_futures"
    if schema == "sports_mlb_daily_residual_risk_scout_v1":
        return "sports_mlb_daily_game_winner"
    return str(report.get("market_family") or "operator_arb")


def _event_key(schema: str, report: dict[str, Any], row: dict[str, Any]) -> str | None:
    if schema in {"sports_mlb_world_series_operator_arb_scout_v1", "sports_mlb_world_series_residual_risk_scout_v1"}:
        season = report.get("season")
        return f"MLB_WORLD_SERIES_{season}" if season else "MLB_WORLD_SERIES"
    return row.get("cross_platform_game_key") or row.get("event_key")


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    plans = Counter(row.get("recommended_plan") for row in rows)
    blockers = Counter()
    for row in rows:
        blockers.update(row.get("blockers") or [])
    return {
        "rows": len(rows),
        "exit_now_review_rows": plans[PLAN_EXIT_NOW],
        "exit_target_already_met_rows": plans[PLAN_EXIT_TARGET_MET],
        "enter_and_monitor_rows": plans[PLAN_ENTER_MONITOR],
        "hold_to_settlement_rows": plans[PLAN_HOLD],
        "manual_review_rows": plans[PLAN_MANUAL],
        "watch_rows": plans[PLAN_WATCH],
        "ignore_low_return_rows": plans[PLAN_IGNORE_LOW_RETURN],
        "ignore_insufficient_size_rows": plans[PLAN_IGNORE_SIZE],
        "rows_with_exit_bid_data": sum(1 for row in rows if row.get("exit_bid_data_status") == "available"),
        "exact_ready_rows": 0,
        "standard_paper_candidate_rows": 0,
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(15)],
    }


def _row_sort_key(row: dict[str, Any]) -> tuple[int, float, float]:
    priority = {
        PLAN_EXIT_NOW: 0,
        PLAN_EXIT_TARGET_MET: 1,
        PLAN_ENTER_MONITOR: 2,
        PLAN_HOLD: 3,
        PLAN_MANUAL: 4,
        PLAN_WATCH: 5,
        PLAN_IGNORE_SIZE: 6,
        PLAN_IGNORE_LOW_RETURN: 7,
    }.get(str(row.get("recommended_plan")), 9)
    net = _float_or_none(row.get("net_hold_edge"))
    size = _float_or_none(row.get("available_notional"))
    return (priority, -(net if net is not None else -999.0), -(size if size is not None else -999.0))


def _days_to_settlement(generated: datetime, settlement: date | None) -> int | None:
    if settlement is None:
        return None
    return max(0, (settlement - generated.date()).days)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if value is None:
        return []
    return [str(value)]


def _fmt(value: Any) -> str:
    number = _float_or_none(value)
    if number is None:
        return ""
    return f"{number:.6g}"


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")
