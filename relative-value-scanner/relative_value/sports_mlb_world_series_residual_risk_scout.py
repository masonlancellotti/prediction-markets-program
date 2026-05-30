from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.fees import KalshiTieredFeeModel, PolymarketConservativeFeeModel
from relative_value.operator_paper_candidate_policy import (
    CLASS_OPERATOR,
    ACTION_WATCH as VISIBLE_WATCH,
    apply_operator_candidate_fields,
    candidate_counts,
    ensure_candidate_fields,
    normalize_operator_risk_mode,
)
from relative_value.sports_mlb_world_series_evidence_collector import TEAM_CODE_TO_NAME, team_code


SCHEMA_VERSION = 1
SCHEMA_KIND = "sports_mlb_world_series_operator_arb_scout_v1"
REPORT_SOURCE = "sports_mlb_world_series_operator_arb_scout_v1"

ACTION_OPERATOR_REVIEW = "OPERATOR_ARB_PAPER_REVIEW"
ACTION_RESIDUAL_REVIEW = "RESIDUAL_RISK_SHADOW_PAPER_REVIEW"
ACTION_MANUAL_REVIEW = "MANUAL_REVIEW"
ACTION_WATCH = "WATCH"
ACTION_IGNORE_BLOCKED = "IGNORE_BLOCKED"

RESIDUAL_RISK_TYPE = "mlb_world_series_no_champion_other_vs_proportional_tail_risk"
DEFAULT_MAX_QUOTE_AGE_SECONDS = 3600.0
DEFAULT_MIN_AVAILABLE_NOTIONAL = 10.0

B_SCOPE_INVALID = "invalid_or_unsupported_world_series_scope"
B_NOT_MLB = "not_mlb_scope"
B_WRONG_PLATFORM = "wrong_platform_scope"
B_WRONG_SEASON = "season_mismatch"
B_NOT_CHAMPIONSHIP = "not_championship_futures_scope"
B_UNSUPPORTED_SCOPE = "unsupported_market_scope"
B_TEAM_COUNT = "team_outcome_count_not_30"
B_TEAM_MAPPING = "team_mapping_missing_or_ambiguous"
B_MISSING_KALSHI_TICKER = "missing_kalshi_ticker"
B_MISSING_POLY_TOKENS = "missing_polymarket_token_ids"
B_MISSING_QUOTE = "missing_quote"
B_MISSING_POLY_NO = "missing_polymarket_no_quote"
B_AMBIGUOUS_POLY_NO = "ambiguous_polymarket_no_quote"
B_MISSING_KALSHI_NO = "missing_kalshi_no_quote"
B_MISSING_DEPTH = "missing_quote_depth"
B_MISSING_KALSHI_SIZE = "missing_kalshi_size"
B_MISSING_POLYMARKET_SIZE = "missing_polymarket_size"
B_UNCLEAR_KALSHI_SIZE_UNITS = "unclear_kalshi_size_units"
B_UNCLEAR_POLYMARKET_SIZE_UNITS = "unclear_polymarket_size_units"
B_PARTIAL_DEPTH = "partial_or_missing_depth"
B_STALE_QUOTE = "stale_or_missing_quote"
B_SIZE_UNIT = "quote_size_unit_review_required"
B_INSUFFICIENT_DEPTH = "insufficient_available_notional"
B_MISSING_FEE = "missing_or_uncertain_fee_model"
B_NO_GROSS = "no_positive_gross_edge"
B_NO_NET = "no_positive_net_edge_after_fees"
B_REMOTE_NOT_ACCEPTED = "remote_tail_risk_not_accepted"
B_PROPORTIONAL_MISMATCH = "proportional_payout_vs_other_outcome_mismatch"
B_REMOTE_ACCEPTED_NOT_EXACT = "remote_tail_risk_human_accepted_but_not_exact"

_UNSUPPORTED_SCOPE_RE = re.compile(
    r"\b(daily|game[_ -]?winner|spread|total|player[_ -]?prop|prop|run[_ -]?line|inning|innings)\b",
    re.IGNORECASE,
)
_CHAMPIONSHIP_RE = re.compile(r"\b(world\s+series|pro\s+baseball\s+champion|championship_futures)\b", re.IGNORECASE)


def write_sports_mlb_world_series_residual_risk_files(
    *,
    kalshi_evidence: Path,
    polymarket_evidence: Path,
    season: int | str,
    accept_world_series_remote_tail_risk: bool,
    max_quote_age_seconds: float,
    min_available_notional: float,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
    fee_models_available: bool = True,
    operator_accepted_as_arb: bool = False,
    operator_risk_mode: str = "conservative",
) -> dict[str, Any]:
    report = build_sports_mlb_world_series_residual_risk_report(
        kalshi_evidence=kalshi_evidence,
        polymarket_evidence=polymarket_evidence,
        season=season,
        accept_world_series_remote_tail_risk=accept_world_series_remote_tail_risk,
        operator_accepted_as_arb=operator_accepted_as_arb,
        max_quote_age_seconds=max_quote_age_seconds,
        min_available_notional=min_available_notional,
        generated_at=generated_at,
        fee_models_available=fee_models_available,
        operator_risk_mode=operator_risk_mode,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_sports_mlb_world_series_residual_risk_markdown(report), encoding="utf-8")
    return report


def build_sports_mlb_world_series_residual_risk_report(
    *,
    kalshi_evidence: Path,
    polymarket_evidence: Path,
    season: int | str,
    accept_world_series_remote_tail_risk: bool = False,
    operator_accepted_as_arb: bool = False,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    min_available_notional: float = DEFAULT_MIN_AVAILABLE_NOTIONAL,
    generated_at: datetime | None = None,
    fee_models_available: bool = True,
    operator_risk_mode: str = "conservative",
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    risk_mode = normalize_operator_risk_mode(operator_risk_mode)
    mode_accepts_tail_risk = risk_mode in {"standard", "aggressive"}
    effective_accept_tail_risk = bool(accept_world_series_remote_tail_risk or mode_accepts_tail_risk)
    effective_operator_accepted = bool(operator_accepted_as_arb or mode_accepts_tail_risk)
    season_label = str(season).strip()
    kalshi_payload = _read_json(kalshi_evidence)
    polymarket_payload = _read_json(polymarket_evidence)
    kalshi_scope = _scope_validation(kalshi_payload, expected_platform="Kalshi", season=season_label)
    polymarket_scope = _scope_validation(polymarket_payload, expected_platform="Polymarket", season=season_label)
    scope_blockers = sorted(set(kalshi_scope["blockers"] + polymarket_scope["blockers"]))

    kalshi_rows = _extract_kalshi_rows(kalshi_payload)
    polymarket_rows = _extract_polymarket_rows(polymarket_payload)
    kalshi_by_code = {row["canonical_team_key"]: row for row in kalshi_rows if row.get("canonical_team_key")}
    poly_by_code = {row["canonical_team_key"]: row for row in polymarket_rows if row.get("canonical_team_key")}
    matched_codes = sorted(set(kalshi_by_code) & set(poly_by_code), key=_team_sort_key)

    rows: list[dict[str, Any]] = []
    if scope_blockers and not matched_codes:
        rows.append(
            _blocked_scope_row(
                season=season_label,
                blockers=scope_blockers,
                accept_world_series_remote_tail_risk=effective_accept_tail_risk,
                operator_accepted_as_arb=effective_operator_accepted,
            )
        )
    for code in matched_codes:
        kalshi = kalshi_by_code[code]
        polymarket = poly_by_code[code]
        rows.append(
            _basket_row(
                code=code,
                direction="KALSHI_YES_POLYMARKET_NO",
                kalshi=kalshi,
                polymarket=polymarket,
                kalshi_side="YES",
                polymarket_side="NO",
                scope_blockers=scope_blockers,
                accept_world_series_remote_tail_risk=effective_accept_tail_risk,
                operator_accepted_as_arb=effective_operator_accepted,
                operator_risk_mode=risk_mode,
                generated_at=generated,
                max_quote_age_seconds=max_quote_age_seconds,
                min_available_notional=min_available_notional,
                fee_models_available=fee_models_available,
            )
        )
        rows.append(
            _basket_row(
                code=code,
                direction="POLYMARKET_YES_KALSHI_NO",
                kalshi=kalshi,
                polymarket=polymarket,
                kalshi_side="NO",
                polymarket_side="YES",
                scope_blockers=scope_blockers,
                accept_world_series_remote_tail_risk=effective_accept_tail_risk,
                operator_accepted_as_arb=effective_operator_accepted,
                operator_risk_mode=risk_mode,
                generated_at=generated,
                max_quote_age_seconds=max_quote_age_seconds,
                min_available_notional=min_available_notional,
                fee_models_available=fee_models_available,
            )
        )
    rows.extend(
        _unmatched_rows(
            kalshi_by_code=kalshi_by_code,
            poly_by_code=poly_by_code,
            matched_codes=set(matched_codes),
            scope_blockers=scope_blockers,
            accept_world_series_remote_tail_risk=effective_accept_tail_risk,
            operator_accepted_as_arb=effective_operator_accepted,
        )
    )
    rows.sort(key=_row_sort_key, reverse=True)
    summary = _summary(rows)
    return ensure_candidate_fields({
        "schema_version": SCHEMA_VERSION,
        "schema_kind": SCHEMA_KIND,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "shadow_paper_only": True,
        "operator_arb_mode": bool(effective_accept_tail_risk and effective_operator_accepted),
        "strict_exact_arb": False,
        "mathematical_strict_exact_arb": False,
        "human_accepted_remote_tail_risk": bool(effective_accept_tail_risk),
        "operator_accepted_as_arb": bool(effective_operator_accepted),
        "operator_risk_mode": risk_mode,
        "residual_risk_type": RESIDUAL_RISK_TYPE if effective_accept_tail_risk else None,
        "season": season_label,
        "kalshi_evidence": str(kalshi_evidence),
        "polymarket_evidence": str(polymarket_evidence),
        "scope_validation": {
            "kalshi": kalshi_scope,
            "polymarket": polymarket_scope,
            "valid": not scope_blockers,
        },
        "kalshi_rows_loaded": len(kalshi_rows),
        "polymarket_rows_loaded": len(polymarket_rows),
        "matched_team_rows": len(matched_codes),
        "rows": rows,
        "summary_counts": summary,
        "top_blockers": summary["top_blockers"],
        "exact_ready_rows": 0,
        "paper_candidate_rows": summary.get("total_paper_candidate_rows", 0),
        "standard_paper_candidate_rows": summary.get("total_paper_candidate_rows", 0),
        "operator_arb_review_rows": summary["operator_arb_review_rows"],
        "operator_paper_review_rows": summary["operator_arb_review_rows"],
        "paper_candidate_emitted": summary.get("total_paper_candidate_rows", 0) > 0,
        "global_paper_candidate_emitted": summary.get("total_paper_candidate_rows", 0) > 0,
        "safety": {
            "diagnostic_only": True,
            "shadow_paper_only": True,
            "operator_arb_mode": bool(effective_accept_tail_risk and effective_operator_accepted),
            "strict_exact_arb": False,
            "mathematical_strict_exact_arb": False,
            "exact_ready": False,
            "paper_candidate": summary.get("total_paper_candidate_rows", 0) > 0,
            "standard_paper_candidate_rows": summary.get("total_paper_candidate_rows", 0),
            "paper_candidate_emitted": summary.get("total_paper_candidate_rows", 0) > 0,
            "global_paper_candidate_emitted": summary.get("total_paper_candidate_rows", 0) > 0,
            "candidate_pair_creation": False,
            "evaluator_invoked": False,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
            "saved_files_only": True,
        },
    })


def render_sports_mlb_world_series_residual_risk_markdown(report: dict[str, Any]) -> str:
    counts = report.get("summary_counts") or {}
    rows = report.get("rows") or []
    lines = [
        "# MLB World Series Operator-Approved Arb Scout",
        "",
        "Residual-risk/operator scout only. This is not mathematically strict exact arb because the remote no-champion/no-contest tail risk is human-accepted, not eliminated.",
        "",
        "## Summary",
        "",
        f"- season: `{_md(report.get('season'))}`",
        f"- human_accepted_remote_tail_risk: `{str(bool(report.get('human_accepted_remote_tail_risk'))).lower()}`",
        f"- operator_accepted_as_arb: `{str(bool(report.get('operator_accepted_as_arb'))).lower()}`",
        f"- operator_arb_mode: `{str(bool(report.get('operator_arb_mode'))).lower()}`",
        f"- kalshi_rows_loaded: `{report.get('kalshi_rows_loaded', 0)}`",
        f"- polymarket_rows_loaded: `{report.get('polymarket_rows_loaded', 0)}`",
        f"- matched_team_rows: `{report.get('matched_team_rows', 0)}`",
        f"- rows: `{counts.get('rows', 0)}`",
        f"- strict_paper_candidate_rows: `{counts.get('strict_paper_candidate_rows', 0)}`",
        f"- operator_paper_candidate_rows: `{counts.get('operator_paper_candidate_rows', 0)}`",
        f"- cdna_fill_first_paper_candidate_rows: `{counts.get('cdna_fill_first_paper_candidate_rows', 0)}`",
        f"- total_paper_candidate_rows: `{counts.get('total_paper_candidate_rows', 0)}`",
        f"- manual_review_rows: `{counts.get('manual_review_rows', 0)}`",
        f"- watch_rows: `{counts.get('watch_rows', 0)}`",
        f"- ignore_blocked_rows: `{counts.get('ignore_blocked_rows', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- total_paper_candidate_rows: `{counts.get('total_paper_candidate_rows', 0)}`",
        "",
        "## Paper Candidates",
        "",
        "| Class | Candidate action | Gross edge | Net edge | Size/notional | Assumptions accepted | Blockers/risk notes |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    paper_rows = [row for row in rows if row.get("paper_candidate")]
    if not paper_rows:
        lines.append("| none |  |  |  |  |  |  |")
    for row in paper_rows[:25]:
        lines.append(_candidate_row_md(row))
    lines.extend(["", "## Watch Rows", ""])
    lines.extend(_row_list([row for row in rows if row.get("action") == ACTION_WATCH][:25]))
    lines.extend(["", "## Ignored/Blocked Rows", ""])
    lines.extend(_row_list([row for row in rows if row.get("action") == ACTION_IGNORE_BLOCKED][:25]))
    lines.extend(["", "## Top Blockers", "", "| Blocker | Count |", "|---|---:|"])
    blockers = report.get("top_blockers") or []
    if blockers:
        for item in blockers:
            lines.append(f"| {_md(item.get('blocker'))} | {_md(item.get('count'))} |")
    else:
        lines.append("| none | 0 |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- diagnostic_only: `true`",
            "- shadow_paper_only: `true`",
            f"- operator_arb_mode: `{str(bool(report.get('operator_arb_mode'))).lower()}`",
            "- strict_exact_arb: `false`",
            "- mathematical_strict_exact_arb: `false`",
            "- candidate_pair_creation: `false`",
            "- exact_ready: `false`",
            f"- total_paper_candidate_rows: `{counts.get('total_paper_candidate_rows', 0)}`",
            f"- global_paper_candidate_emitted: `{str(bool(report.get('global_paper_candidate_emitted'))).lower()}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _basket_row(
    *,
    code: str,
    direction: str,
    kalshi: dict[str, Any],
    polymarket: dict[str, Any],
    kalshi_side: str,
    polymarket_side: str,
    scope_blockers: list[str],
    accept_world_series_remote_tail_risk: bool,
    operator_accepted_as_arb: bool,
    generated_at: datetime,
    max_quote_age_seconds: float,
    min_available_notional: float,
    fee_models_available: bool,
    operator_risk_mode: str,
) -> dict[str, Any]:
    kalshi_leg = _leg(kalshi, venue="kalshi", side=kalshi_side)
    poly_leg = _leg(polymarket, venue="polymarket", side=polymarket_side)
    kalshi_exit_leg = _bid_leg(kalshi, venue="kalshi", side=kalshi_side)
    poly_exit_leg = _bid_leg(polymarket, venue="polymarket", side=polymarket_side)
    kalshi_price = kalshi_leg["price"]
    poly_price = poly_leg["price"]
    entry_cost = round(kalshi_price + poly_price, 6) if kalshi_price is not None and poly_price is not None else None
    gross_edge = round(1.0 - kalshi_price - poly_price, 6) if kalshi_price is not None and poly_price is not None else None
    kalshi_size = _normalize_leg_notional(kalshi_leg, venue="kalshi", row=kalshi)
    poly_size = _normalize_leg_notional(poly_leg, venue="polymarket", row=polymarket)
    kalshi_notional = kalshi_size["notional"]
    poly_notional = poly_size["notional"]
    available_notional = (
        round(min(kalshi_notional, poly_notional), 6)
        if kalshi_notional is not None and poly_notional is not None
        else None
    )
    depth_gate_passed = _depth_is_acceptable(kalshi_leg) and _depth_is_acceptable(poly_leg)
    size_gate_passed = (
        kalshi_size["status"] == "normalized"
        and poly_size["status"] == "normalized"
        and depth_gate_passed
        and available_notional is not None
        and available_notional >= min_available_notional
    )
    size_unit_status = _size_unit_status(kalshi_size, poly_size, depth_gate_passed=depth_gate_passed)
    fee_estimate, net_edge, net_edge_status = _fee_estimate(
        kalshi_price=kalshi_price,
        polymarket_price=poly_price,
        gross_edge=gross_edge,
        fee_models_available=fee_models_available,
    )
    exit_fields = _exit_value_fields(
        kalshi_exit_leg=kalshi_exit_leg,
        polymarket_exit_leg=poly_exit_leg,
        kalshi=kalshi,
        polymarket=polymarket,
        entry_cost=entry_cost,
        entry_fee_estimate=fee_estimate,
        fee_models_available=fee_models_available,
    )
    quote_timestamps = {
        "kalshi": kalshi.get("quote_timestamp"),
        "polymarket": polymarket.get("quote_timestamp"),
    }
    blockers = list(scope_blockers)
    blockers.append(B_PROPORTIONAL_MISMATCH)
    if accept_world_series_remote_tail_risk:
        blockers.append(B_REMOTE_ACCEPTED_NOT_EXACT)
    else:
        blockers.append(B_REMOTE_NOT_ACCEPTED)
    if not kalshi.get("kalshi_market_ticker"):
        blockers.append(B_MISSING_KALSHI_TICKER)
    if not polymarket.get("polymarket_yes_token_id") or not polymarket.get("polymarket_no_token_id"):
        blockers.append(B_MISSING_POLY_TOKENS)
    if kalshi_price is None or poly_price is None:
        blockers.append(B_MISSING_QUOTE)
    if direction == "KALSHI_YES_POLYMARKET_NO" and poly_price is None:
        blockers.append(B_MISSING_POLY_NO)
    if direction == "POLYMARKET_YES_KALSHI_NO" and kalshi_price is None:
        blockers.append(B_MISSING_KALSHI_NO)
    if polymarket_side == "NO" and not polymarket.get("polymarket_no_token_id"):
        blockers.append(B_AMBIGUOUS_POLY_NO)
    if kalshi_size["missing_size"]:
        blockers.append(B_MISSING_KALSHI_SIZE)
    if poly_size["missing_size"]:
        blockers.append(B_MISSING_POLYMARKET_SIZE)
    if kalshi_size["status"] == "blocked_unclear":
        blockers.append(B_UNCLEAR_KALSHI_SIZE_UNITS)
    if poly_size["status"] == "blocked_unclear":
        blockers.append(B_UNCLEAR_POLYMARKET_SIZE_UNITS)
    if not depth_gate_passed:
        blockers.append(B_PARTIAL_DEPTH)
    if kalshi_size["missing_size"] or poly_size["missing_size"]:
        blockers.append(B_MISSING_DEPTH)
    if available_notional is not None and available_notional <= 0:
        blockers.append(B_MISSING_DEPTH)
    elif available_notional is not None and available_notional < min_available_notional:
        blockers.append(B_INSUFFICIENT_DEPTH)
    blockers.extend(
        _timestamp_blockers(
            quote_timestamps=quote_timestamps,
            generated_at=generated_at,
            max_quote_age_seconds=max_quote_age_seconds,
        )
    )
    if net_edge_status == "FEE_REVIEW_REQUIRED":
        blockers.append(B_MISSING_FEE)
    if gross_edge is not None and gross_edge <= 0:
        blockers.append(B_NO_GROSS)
    if net_edge is not None and net_edge <= 0:
        blockers.append(B_NO_NET)
    blockers = list(dict.fromkeys(blockers))
    action = _action(
        blockers,
        net_edge=net_edge,
        gross_edge=gross_edge,
        net_edge_status=net_edge_status,
        operator_accepted_as_arb=bool(operator_accepted_as_arb),
    )
    quote_timestamp_status = "stale_or_missing" if B_STALE_QUOTE in blockers else "fresh"
    row = {
        "row_id": f"{code}:{direction}",
        "canonical_team_key": code,
        "team_name": _canonical_team_name(code),
        "direction": direction,
        "kalshi_leg": {
            "side": kalshi_side,
            "market_ticker": kalshi.get("kalshi_market_ticker"),
            "team_name": kalshi.get("team_name"),
        },
        "polymarket_leg": {
            "side": polymarket_side,
            "market_id": polymarket.get("polymarket_market_id"),
            "condition_id": polymarket.get("polymarket_condition_id"),
            "yes_token_id": polymarket.get("polymarket_yes_token_id"),
            "no_token_id": polymarket.get("polymarket_no_token_id"),
            "team_name": polymarket.get("team_name"),
        },
        "kalshi_price": kalshi_price,
        "polymarket_price": poly_price,
        "kalshi_ask": kalshi_price,
        "polymarket_ask": poly_price,
        "entry_cost": entry_cost,
        "gross_edge": gross_edge,
        "conservative_fee_estimate": fee_estimate,
        "net_edge": net_edge,
        "net_edge_status": net_edge_status,
        "kalshi_available_notional": kalshi_notional,
        "polymarket_available_notional": poly_notional,
        "kalshi_leg_notional": kalshi_notional,
        "polymarket_leg_notional": poly_notional,
        "available_notional": available_notional,
        "kalshi_size_units": kalshi_size["units"],
        "polymarket_size_units": poly_size["units"],
        "kalshi_size_unit_interpretation": kalshi_size["interpretation"],
        "polymarket_size_unit_interpretation": poly_size["interpretation"],
        "size_unit_status": size_unit_status,
        "size_gate_passed": size_gate_passed,
        **exit_fields,
        "quote_timestamps": quote_timestamps,
        "quote_timestamp_status": quote_timestamp_status,
        "freshness_status": quote_timestamp_status,
        "blockers": blockers,
        "action": action,
        "human_accepted_remote_tail_risk": bool(accept_world_series_remote_tail_risk),
        "operator_accepted_as_arb": bool(operator_accepted_as_arb),
        "residual_risk_type": RESIDUAL_RISK_TYPE if accept_world_series_remote_tail_risk else None,
        "residual_risk_notes": [
            "Kalshi cancellation/no-contest/multiple-winner tail states use proportional payout among eligible listed teams.",
            "Polymarket no-winner/canceled-by-deadline tail states use Other/no-champion handling.",
            "Acceptance of this mismatch is diagnostic-only and cannot clear strict exact-arb or paper-candidate gates.",
        ],
        "mathematical_strict_exact_arb": False,
        "strict_exact_arb": False,
        "exact_ready": False,
        "paper_candidate": False,
        "standard_paper_candidate": False,
        "operator_paper_review": action == ACTION_OPERATOR_REVIEW,
        "diagnostic_only": True,
        "shadow_paper_only": True,
    }
    make_candidate = (
        operator_risk_mode in {"standard", "aggressive"}
        and action == ACTION_OPERATOR_REVIEW
        and net_edge is not None
        and net_edge > 0
    )
    if not make_candidate and row["action"] != ACTION_IGNORE_BLOCKED:
        row["action"] = VISIBLE_WATCH
    return apply_operator_candidate_fields(
        row,
        paper_class=CLASS_OPERATOR,
        assumptions_accepted=["championship_remote_tail_risk"],
        candidate_action="PAPER_CANDIDATE",
        make_candidate=make_candidate,
        mathematical_strict_exact_arb=False,
    )


def _blocked_scope_row(
    *,
    season: str,
    blockers: list[str],
    accept_world_series_remote_tail_risk: bool,
    operator_accepted_as_arb: bool,
) -> dict[str, Any]:
    row_blockers = list(dict.fromkeys([*blockers, B_SCOPE_INVALID]))
    if not accept_world_series_remote_tail_risk:
        row_blockers.append(B_REMOTE_NOT_ACCEPTED)
    return ensure_candidate_fields({
        "row_id": "SCOPE:UNSUPPORTED",
        "canonical_team_key": None,
        "team_name": None,
        "direction": "UNSUPPORTED_SCOPE",
        "kalshi_leg": {},
        "polymarket_leg": {},
        "kalshi_price": None,
        "polymarket_price": None,
        "kalshi_ask": None,
        "polymarket_ask": None,
        "entry_cost": None,
        "gross_edge": None,
        "conservative_fee_estimate": None,
        "net_edge": None,
        "net_edge_status": "NOT_CALCULATED",
        "kalshi_available_notional": None,
        "polymarket_available_notional": None,
        "kalshi_leg_notional": None,
        "polymarket_leg_notional": None,
        "available_notional": None,
        "kalshi_size_units": None,
        "polymarket_size_units": None,
        "kalshi_size_unit_interpretation": None,
        "polymarket_size_unit_interpretation": None,
        "size_unit_status": "not_calculated",
        "size_gate_passed": False,
        "current_exit_pair_value": None,
        "current_exit_pair_value_status": "not_calculated",
        "exit_bid_legs": [],
        "immediate_unwind_pnl_before_fees": None,
        "immediate_unwind_pnl_after_estimated_fees": None,
        "exit_fee_status": "NOT_CALCULATED",
        "exit_size_available_notional": None,
        "exit_data_blockers": [],
        "quote_timestamps": {},
        "quote_timestamp_status": "not_calculated",
        "freshness_status": "not_calculated",
        "blockers": row_blockers,
        "action": ACTION_IGNORE_BLOCKED,
        "human_accepted_remote_tail_risk": bool(accept_world_series_remote_tail_risk),
        "operator_accepted_as_arb": bool(operator_accepted_as_arb),
        "residual_risk_type": RESIDUAL_RISK_TYPE if accept_world_series_remote_tail_risk else None,
        "residual_risk_notes": [f"Season {season} input scope failed closed."],
        "mathematical_strict_exact_arb": False,
        "strict_exact_arb": False,
        "exact_ready": False,
        "paper_candidate": False,
        "standard_paper_candidate": False,
        "operator_paper_review": False,
        "diagnostic_only": True,
        "shadow_paper_only": True,
    })


def _unmatched_rows(
    *,
    kalshi_by_code: dict[str, dict[str, Any]],
    poly_by_code: dict[str, dict[str, Any]],
    matched_codes: set[str],
    scope_blockers: list[str],
    accept_world_series_remote_tail_risk: bool,
    operator_accepted_as_arb: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code in sorted(set(kalshi_by_code) - matched_codes, key=_team_sort_key):
        blockers = list(dict.fromkeys([*scope_blockers, B_TEAM_MAPPING, "missing_polymarket_team_row"]))
        rows.append(_unmatched_row(code, blockers, accept_world_series_remote_tail_risk, operator_accepted_as_arb))
    for code in sorted(set(poly_by_code) - matched_codes, key=_team_sort_key):
        blockers = list(dict.fromkeys([*scope_blockers, B_TEAM_MAPPING, "missing_kalshi_team_row"]))
        rows.append(_unmatched_row(code, blockers, accept_world_series_remote_tail_risk, operator_accepted_as_arb))
    return rows


def _unmatched_row(
    code: str,
    blockers: list[str],
    accept_world_series_remote_tail_risk: bool,
    operator_accepted_as_arb: bool,
) -> dict[str, Any]:
    if not accept_world_series_remote_tail_risk:
        blockers.append(B_REMOTE_NOT_ACCEPTED)
    return ensure_candidate_fields({
        "row_id": f"{code}:UNMATCHED",
        "canonical_team_key": code,
        "team_name": _canonical_team_name(code),
        "direction": "UNMATCHED",
        "kalshi_leg": {},
        "polymarket_leg": {},
        "kalshi_price": None,
        "polymarket_price": None,
        "kalshi_ask": None,
        "polymarket_ask": None,
        "entry_cost": None,
        "gross_edge": None,
        "conservative_fee_estimate": None,
        "net_edge": None,
        "net_edge_status": "NOT_CALCULATED",
        "kalshi_available_notional": None,
        "polymarket_available_notional": None,
        "kalshi_leg_notional": None,
        "polymarket_leg_notional": None,
        "available_notional": None,
        "kalshi_size_units": None,
        "polymarket_size_units": None,
        "kalshi_size_unit_interpretation": None,
        "polymarket_size_unit_interpretation": None,
        "size_unit_status": "not_calculated",
        "size_gate_passed": False,
        "current_exit_pair_value": None,
        "current_exit_pair_value_status": "not_calculated",
        "exit_bid_legs": [],
        "immediate_unwind_pnl_before_fees": None,
        "immediate_unwind_pnl_after_estimated_fees": None,
        "exit_fee_status": "NOT_CALCULATED",
        "exit_size_available_notional": None,
        "exit_data_blockers": [],
        "quote_timestamps": {},
        "quote_timestamp_status": "not_calculated",
        "freshness_status": "not_calculated",
        "blockers": list(dict.fromkeys(blockers)),
        "action": ACTION_IGNORE_BLOCKED,
        "human_accepted_remote_tail_risk": bool(accept_world_series_remote_tail_risk),
        "operator_accepted_as_arb": bool(operator_accepted_as_arb),
        "residual_risk_type": RESIDUAL_RISK_TYPE if accept_world_series_remote_tail_risk else None,
        "residual_risk_notes": [],
        "mathematical_strict_exact_arb": False,
        "strict_exact_arb": False,
        "exact_ready": False,
        "paper_candidate": False,
        "standard_paper_candidate": False,
        "operator_paper_review": False,
        "diagnostic_only": True,
        "shadow_paper_only": True,
    })


def _scope_validation(payload: dict[str, Any], *, expected_platform: str, season: str) -> dict[str, Any]:
    blockers: list[str] = []
    platform = _payload_field(payload, "platform")
    if platform != expected_platform:
        blockers.append(B_WRONG_PLATFORM)
    league = _payload_field(payload, "league")
    if league != "MLB":
        blockers.append(B_NOT_MLB)
    payload_season = _payload_field(payload, "season")
    if str(payload_season or "").strip() != season:
        blockers.append(B_WRONG_SEASON)
    batch = _payload_field(payload, "batch")
    text = " ".join(
        str(value or "")
        for value in (
            payload.get("schema_kind"),
            payload.get("date_label"),
            payload.get("market_type"),
            payload.get("market_title"),
            _payload_field(payload, "market_title"),
            batch,
        )
    )
    if batch and batch != "championship_futures":
        blockers.append(B_NOT_CHAMPIONSHIP)
    elif not batch and not _CHAMPIONSHIP_RE.search(text):
        blockers.append(B_NOT_CHAMPIONSHIP)
    if "games" in payload or _UNSUPPORTED_SCOPE_RE.search(text):
        blockers.append(B_UNSUPPORTED_SCOPE)
    outcomes = payload.get("outcomes")
    if not isinstance(outcomes, list):
        blockers.append(B_SCOPE_INVALID)
        observed_count = None
    else:
        observed_count = _declared_team_count(payload) or len(outcomes)
        if observed_count != 30:
            blockers.append(B_TEAM_COUNT)
    return {
        "platform": platform,
        "league": league,
        "season": str(payload_season or "") or None,
        "batch": batch,
        "team_outcomes_observed": observed_count,
        "valid": not blockers,
        "blockers": sorted(set(blockers)),
    }


def _extract_kalshi_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for outcome in payload.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        code = _team_code_from_outcome(outcome)
        if not code:
            continue
        quote = outcome.get("quote") if isinstance(outcome.get("quote"), dict) else {}
        rows.append(
            {
                "canonical_team_key": code,
                "team_name": outcome.get("team_name") or _canonical_team_name(code),
                "kalshi_market_ticker": _string_or_none(outcome.get("market_ticker")),
                "yes_bid": _float_or_none(quote.get("yes_bid")),
                "no_bid": _float_or_none(quote.get("no_bid")),
                "yes_ask": _float_or_none(quote.get("yes_ask")),
                "no_ask": _float_or_none(quote.get("no_ask")),
                "yes_bid_size": _float_or_none(quote.get("yes_bid_size")),
                "no_bid_size": _float_or_none(quote.get("no_bid_size")),
                "yes_ask_size": _float_or_none(quote.get("yes_ask_size")),
                "no_ask_size": _float_or_none(quote.get("no_ask_size")),
                "depth_status": _string_or_none(quote.get("depth_status") or outcome.get("quote_status")),
                "quote_timestamp": _string_or_none(quote.get("quote_timestamp") or quote.get("fetch_time_utc")),
                "required_quote_fields_present": bool(quote.get("required_quote_fields_present")),
                "size_unit_text": _size_unit_text(payload, outcome, quote),
                "blockers": outcome.get("blockers_remaining") or [],
            }
        )
    return rows


def _extract_polymarket_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for outcome in payload.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        code = _team_code_from_outcome(outcome)
        if not code:
            continue
        quote = outcome.get("quote") if isinstance(outcome.get("quote"), dict) else {}
        rows.append(
            {
                "canonical_team_key": code,
                "team_name": outcome.get("team_name") or outcome.get("outcome_name") or _canonical_team_name(code),
                "polymarket_market_id": _string_or_none(outcome.get("market_id")),
                "polymarket_condition_id": _string_or_none(outcome.get("condition_id")),
                "polymarket_yes_token_id": _string_or_none(outcome.get("token_id_yes")),
                "polymarket_no_token_id": _string_or_none(outcome.get("token_id_no")),
                "yes_bid": _float_or_none(quote.get("yes_bid")),
                "no_bid": _float_or_none(quote.get("no_bid")),
                "yes_ask": _float_or_none(quote.get("yes_ask")),
                "no_ask": _float_or_none(quote.get("no_ask")),
                "yes_bid_size": _float_or_none(quote.get("yes_bid_size")),
                "no_bid_size": _float_or_none(quote.get("no_bid_size")),
                "yes_ask_size": _float_or_none(quote.get("yes_ask_size")),
                "no_ask_size": _float_or_none(quote.get("no_ask_size")),
                "depth_status": _string_or_none(quote.get("depth_status") or outcome.get("quote_status")),
                "quote_timestamp": _string_or_none(quote.get("quote_timestamp")),
                "required_quote_fields_present": bool(quote.get("required_quote_fields_present")),
                "size_unit_text": _size_unit_text(payload, outcome, quote),
                "blockers": outcome.get("blockers_remaining") or quote.get("quote_blockers_remaining") or [],
            }
        )
    return rows


def _leg(row: dict[str, Any], *, venue: str, side: str) -> dict[str, Any]:
    prefix = side.lower()
    return {
        "venue": venue,
        "side": side,
        "quote_side": "ask",
        "price": row.get(f"{prefix}_ask"),
        "size": row.get(f"{prefix}_ask_size"),
        "depth_status": row.get("depth_status"),
        "quote_timestamp": row.get("quote_timestamp"),
        "size_unit_text": row.get("size_unit_text"),
    }


def _bid_leg(row: dict[str, Any], *, venue: str, side: str) -> dict[str, Any]:
    prefix = side.lower()
    return {
        "venue": venue,
        "side": side,
        "quote_side": "bid",
        "price": row.get(f"{prefix}_bid"),
        "size": row.get(f"{prefix}_bid_size"),
        "depth_status": row.get("depth_status"),
        "quote_timestamp": row.get("quote_timestamp"),
        "size_unit_text": row.get("size_unit_text"),
    }


def _normalize_leg_notional(leg: dict[str, Any], *, venue: str, row: dict[str, Any]) -> dict[str, Any]:
    price = leg.get("price")
    size = leg.get("size")
    if price is None or size is None:
        return {
            "notional": None,
            "status": "missing",
            "missing_size": size is None,
            "units": None,
            "interpretation": "missing_price_or_size",
        }
    unit_text = " ".join(
        str(value or "")
        for value in (
            leg.get("size_unit_text"),
            row.get("size_unit_text"),
            leg.get("depth_status"),
            row.get("depth_status"),
        )
    ).lower()
    if venue == "kalshi":
        if any(token in unit_text for token in ("notional", "dollar", "usdc-equivalent", "usd-equivalent")):
            return {
                "notional": round(size, 6),
                "status": "normalized",
                "missing_size": False,
                "units": "dollar_notional",
                "interpretation": "explicit_dollar_notional_size_used_directly",
            }
        if any(token in unit_text for token in ("kalshi public clob", "public clob api", "orderbook", "resting level")):
            return {
                "notional": round(price * size, 6),
                "status": "normalized",
                "missing_size": False,
                "units": "orderbook_contract_quantity",
                "interpretation": "raw_orderbook_size_converted_to_notional_as_ask_price_times_size",
            }
        return {
            "notional": None,
            "status": "blocked_unclear",
            "missing_size": False,
            "units": _string_or_none(unit_text),
            "interpretation": "kalshi_size_units_unclear",
        }
    if venue == "polymarket":
        if any(token in unit_text for token in ("shares", "contracts", "tokens", "clob", "polymarket public clob")):
            return {
                "notional": round(price * size, 6),
                "status": "normalized",
                "missing_size": False,
                "units": "token_or_share_quantity",
                "interpretation": "token_or_share_quantity_converted_to_notional_as_ask_price_times_size",
            }
        return {
            "notional": None,
            "status": "blocked_unclear",
            "missing_size": False,
            "units": _string_or_none(unit_text),
            "interpretation": "polymarket_size_units_unclear",
        }
    return {
        "notional": None,
        "status": "blocked_unclear",
        "missing_size": False,
        "units": _string_or_none(unit_text),
        "interpretation": "unknown_venue_size_units",
    }


def _exit_value_fields(
    *,
    kalshi_exit_leg: dict[str, Any],
    polymarket_exit_leg: dict[str, Any],
    kalshi: dict[str, Any],
    polymarket: dict[str, Any],
    entry_cost: float | None,
    entry_fee_estimate: float | None,
    fee_models_available: bool,
) -> dict[str, Any]:
    kalshi_bid = kalshi_exit_leg.get("price")
    poly_bid = polymarket_exit_leg.get("price")
    kalshi_exit_size = _normalize_leg_notional(kalshi_exit_leg, venue="kalshi", row=kalshi)
    poly_exit_size = _normalize_leg_notional(polymarket_exit_leg, venue="polymarket", row=polymarket)
    current_exit_value = round(kalshi_bid + poly_bid, 6) if kalshi_bid is not None and poly_bid is not None else None
    if current_exit_value is not None:
        exit_value_status = "available"
    elif kalshi_bid is None and poly_bid is None:
        exit_value_status = "missing"
    else:
        exit_value_status = "partial"
    exit_size_available = (
        round(min(kalshi_exit_size["notional"], poly_exit_size["notional"]), 6)
        if kalshi_exit_size["notional"] is not None and poly_exit_size["notional"] is not None
        else None
    )
    immediate_before_fees = (
        round(current_exit_value - entry_cost, 6)
        if current_exit_value is not None and entry_cost is not None
        else None
    )
    exit_fee_estimate, exit_fee_status = _exit_fee_estimate(
        kalshi_bid=kalshi_bid,
        polymarket_bid=poly_bid,
        fee_models_available=fee_models_available,
    )
    immediate_after_fees = (
        round(immediate_before_fees - entry_fee_estimate - exit_fee_estimate, 6)
        if immediate_before_fees is not None and entry_fee_estimate is not None and exit_fee_estimate is not None
        else None
    )
    exit_blockers: list[str] = []
    if kalshi_bid is None:
        exit_blockers.append("missing_kalshi_exit_bid")
    if poly_bid is None:
        exit_blockers.append("missing_polymarket_exit_bid")
    if kalshi_exit_size["notional"] is None:
        exit_blockers.append("missing_or_unclear_kalshi_exit_size")
    if poly_exit_size["notional"] is None:
        exit_blockers.append("missing_or_unclear_polymarket_exit_size")
    if exit_fee_status != "OK":
        exit_blockers.append("exit_fee_review_required")
    return {
        "current_exit_pair_value": current_exit_value,
        "current_exit_pair_value_status": exit_value_status,
        "exit_bid_legs": [
            {
                "platform": "Kalshi",
                "side": kalshi_exit_leg.get("side"),
                "bid": kalshi_bid,
                "size": kalshi_exit_leg.get("size"),
                "notional_estimate": kalshi_exit_size["notional"],
                "size_unit_interpretation": kalshi_exit_size["interpretation"],
            },
            {
                "platform": "Polymarket",
                "side": polymarket_exit_leg.get("side"),
                "bid": poly_bid,
                "size": polymarket_exit_leg.get("size"),
                "notional_estimate": poly_exit_size["notional"],
                "size_unit_interpretation": poly_exit_size["interpretation"],
            },
        ],
        "immediate_unwind_pnl_before_fees": immediate_before_fees,
        "immediate_unwind_pnl_after_estimated_fees": immediate_after_fees,
        "exit_fee_estimate": exit_fee_estimate,
        "exit_fee_status": exit_fee_status,
        "exit_size_available_notional": exit_size_available,
        "exit_data_blockers": exit_blockers,
    }


def _exit_fee_estimate(
    *,
    kalshi_bid: float | None,
    polymarket_bid: float | None,
    fee_models_available: bool,
) -> tuple[float | None, str]:
    if kalshi_bid is None or polymarket_bid is None:
        return None, "NOT_CALCULATED"
    if not fee_models_available:
        return None, "FEE_REVIEW_REQUIRED"
    kalshi_fee = KalshiTieredFeeModel().fee_for_leg(kalshi_bid)
    polymarket_fee = PolymarketConservativeFeeModel().fee_for_leg_for_category(polymarket_bid, category="sports")
    return round(kalshi_fee + polymarket_fee, 6), "OK"


def _depth_is_acceptable(leg: dict[str, Any]) -> bool:
    text = str(leg.get("depth_status") or "").strip().lower()
    if not text:
        return False
    return "full_clob" in text or "full clob" in text or "present_full_clob" in text


def _size_unit_status(kalshi_size: dict[str, Any], poly_size: dict[str, Any], *, depth_gate_passed: bool) -> str:
    if kalshi_size["status"] == "missing" or poly_size["status"] == "missing" or not depth_gate_passed:
        return "missing"
    if kalshi_size["status"] != "normalized" or poly_size["status"] != "normalized":
        return "blocked_unclear"
    return "normalized"


def _fee_estimate(
    *,
    kalshi_price: float | None,
    polymarket_price: float | None,
    gross_edge: float | None,
    fee_models_available: bool,
) -> tuple[float | None, float | None, str]:
    if kalshi_price is None or polymarket_price is None or gross_edge is None:
        return None, None, "NOT_CALCULATED"
    if not fee_models_available:
        return None, None, "FEE_REVIEW_REQUIRED"
    kalshi_fee = KalshiTieredFeeModel().fee_for_leg(kalshi_price)
    polymarket_fee = PolymarketConservativeFeeModel().fee_for_leg_for_category(polymarket_price, category="sports")
    fee = round(kalshi_fee + polymarket_fee, 6)
    return fee, round(gross_edge - fee, 6), "OK"


def _timestamp_blockers(
    *,
    quote_timestamps: dict[str, Any],
    generated_at: datetime,
    max_quote_age_seconds: float,
) -> list[str]:
    blockers: list[str] = []
    for value in quote_timestamps.values():
        parsed = _parse_datetime(value)
        if parsed is None:
            blockers.append(B_STALE_QUOTE)
            continue
        if (generated_at - parsed).total_seconds() > max_quote_age_seconds:
            blockers.append(B_STALE_QUOTE)
    return list(dict.fromkeys(blockers))


def _action(
    blockers: list[str],
    *,
    net_edge: float | None,
    gross_edge: float | None,
    net_edge_status: str,
    operator_accepted_as_arb: bool,
) -> str:
    if any(blocker in blockers for blocker in (B_SCOPE_INVALID, B_NOT_MLB, B_WRONG_PLATFORM, B_WRONG_SEASON, B_NOT_CHAMPIONSHIP, B_UNSUPPORTED_SCOPE)):
        return ACTION_IGNORE_BLOCKED
    if B_REMOTE_NOT_ACCEPTED in blockers:
        return ACTION_WATCH
    if any(
        blocker in blockers
        for blocker in (
            B_MISSING_QUOTE,
            B_MISSING_POLY_NO,
            B_AMBIGUOUS_POLY_NO,
            B_MISSING_KALSHI_NO,
            B_MISSING_DEPTH,
            B_MISSING_KALSHI_SIZE,
            B_MISSING_POLYMARKET_SIZE,
            B_UNCLEAR_KALSHI_SIZE_UNITS,
            B_UNCLEAR_POLYMARKET_SIZE_UNITS,
            B_PARTIAL_DEPTH,
            B_STALE_QUOTE,
            B_INSUFFICIENT_DEPTH,
        )
    ):
        return ACTION_WATCH
    if net_edge_status == "FEE_REVIEW_REQUIRED" or B_MISSING_FEE in blockers or B_SIZE_UNIT in blockers:
        return ACTION_MANUAL_REVIEW if gross_edge is not None and gross_edge > 0 else ACTION_WATCH
    blocking = [
        blocker
        for blocker in blockers
        if blocker
        not in {
            B_PROPORTIONAL_MISMATCH,
            B_REMOTE_ACCEPTED_NOT_EXACT,
        }
    ]
    if blocking:
        return ACTION_WATCH
    if net_edge is not None and net_edge > 0:
        return ACTION_OPERATOR_REVIEW if operator_accepted_as_arb else ACTION_RESIDUAL_REVIEW
    return ACTION_WATCH


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actions = Counter(row.get("action") for row in rows)
    blockers = Counter()
    for row in rows:
        blockers.update(row.get("blockers") or [])
    summary = {
        "rows": len(rows),
        "operator_arb_review_rows": actions.get(ACTION_OPERATOR_REVIEW, 0),
        "operator_paper_review_rows": actions.get(ACTION_OPERATOR_REVIEW, 0),
        "residual_review_rows": actions.get(ACTION_RESIDUAL_REVIEW, 0),
        "manual_review_rows": actions.get(ACTION_MANUAL_REVIEW, 0),
        "watch_rows": actions.get(ACTION_WATCH, 0),
        "ignore_blocked_rows": actions.get(ACTION_IGNORE_BLOCKED, 0),
        "positive_gross_rows": sum(1 for row in rows if _float_or_none(row.get("gross_edge")) is not None and _float_or_none(row.get("gross_edge")) > 0),
        "positive_net_rows": sum(1 for row in rows if _float_or_none(row.get("net_edge")) is not None and _float_or_none(row.get("net_edge")) > 0),
        "fee_review_required_rows": sum(1 for row in rows if row.get("net_edge_status") == "FEE_REVIEW_REQUIRED"),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "standard_paper_candidate_rows": 0,
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(15)],
    }
    summary.update(candidate_counts(rows))
    summary["paper_candidate_rows"] = summary["total_paper_candidate_rows"]
    summary["standard_paper_candidate_rows"] = summary["total_paper_candidate_rows"]
    return summary


def _scope_is_valid(report: dict[str, Any]) -> bool:
    return bool(report.get("scope_validation", {}).get("valid"))


def _candidate_row_md(row: dict[str, Any]) -> str:
    return (
        "| "
        f"{_md(row.get('paper_candidate_class'))} | {_md(row.get('candidate_action'))} | "
        f"{_md(row.get('gross_edge'))} | {_md(row.get('net_edge'))} | {_md(row.get('available_notional'))} | "
        f"{_md(', '.join(row.get('assumptions_accepted') or []))} | "
        f"{_md(', '.join((row.get('blockers') or []) + (row.get('risk_notes') or [])))} |"
    )


def _row_list(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["_None._"]
    return [
        f"- `{_md(row.get('row_id'))}` action=`{_md(row.get('action'))}` net=`{_md(row.get('net_edge'))}` blockers=`{_md(', '.join(row.get('blockers') or []))}`"
        for row in rows
    ]


def _declared_team_count(payload: dict[str, Any]) -> int | None:
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    summary = payload.get("summary_counts") if isinstance(payload.get("summary_counts"), dict) else {}
    structure = payload.get("market_structure") if isinstance(payload.get("market_structure"), dict) else {}
    for container in (validation, summary, structure):
        for key in ("team_outcomes_observed", "listed_team_count", "outcomes"):
            value = _int_or_none(container.get(key))
            if value is not None:
                return value
    return None


def _payload_field(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is not None:
        return value
    market = payload.get("market")
    if isinstance(market, dict):
        return market.get(key)
    return None


def _size_unit_text(payload: dict[str, Any], outcome: dict[str, Any], quote: dict[str, Any]) -> str:
    parts: list[str] = []
    for source in (quote, outcome):
        for key in ("size_unit", "ask_size_unit", "yes_ask_size_unit", "no_ask_size_unit", "size_units"):
            if source.get(key):
                parts.append(str(source.get(key)))
    quote_notes = payload.get("quote_collection_notes") if isinstance(payload.get("quote_collection_notes"), dict) else {}
    quote_collection = payload.get("quote_collection") if isinstance(payload.get("quote_collection"), dict) else {}
    for source in (quote_notes, quote_collection):
        for key in ("size_units", "size_unit_note", "quote_source", "depth_status_observed", "orderbook_structure", "api_endpoint"):
            if source.get(key):
                parts.append(str(source.get(key)))
    if quote.get("quote_source"):
        parts.append(str(quote.get("quote_source")))
    return " ".join(parts)


def _team_code_from_outcome(outcome: dict[str, Any]) -> str | None:
    values: list[Any] = [
        outcome.get("team_name"),
        outcome.get("outcome_name"),
        outcome.get("market_ticker"),
    ]
    aliases = outcome.get("team_aliases")
    if isinstance(aliases, list):
        values.extend(aliases)
    for value in values:
        code = _team_code_any(value)
        if code:
            return code
    return None


def _team_code_any(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    if "-" in text and text.upper().startswith("KXMLB-"):
        text = text.rsplit("-", 1)[-1]
    cleaned = text.replace("A's", "Athletics").replace("As", "Athletics")
    return team_code(cleaned)


def _canonical_team_name(code: str) -> str:
    return TEAM_CODE_TO_NAME.get(code, code)


def _team_sort_key(code: str) -> str:
    return _canonical_team_name(code)


def _row_sort_key(row: dict[str, Any]) -> tuple[float, float]:
    net = _float_or_none(row.get("net_edge"))
    gross = _float_or_none(row.get("gross_edge"))
    return (net if net is not None else -999.0, gross if gross is not None else -999.0)


def _parse_datetime(value: Any) -> datetime | None:
    text = _string_or_none(value)
    if not text:
        return None
    if re.fullmatch(r"\d{10,}", text):
        number = int(text)
        if number > 10_000_000_000:
            return datetime.fromtimestamp(number / 1000.0, tz=timezone.utc)
        return datetime.fromtimestamp(number, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")
