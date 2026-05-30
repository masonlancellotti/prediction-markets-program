from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.cdna_fill_log import load_cdna_fill_log
from relative_value.fees import KalshiTieredFeeModel, PolymarketConservativeFeeModel
from relative_value.operator_paper_candidate_policy import (
    CLASS_CDNA,
    ACTION_PAPER,
    ACTION_WATCH as VISIBLE_WATCH,
    ACTION_IGNORE as VISIBLE_IGNORE,
    apply_operator_candidate_fields,
    candidate_counts,
    normalize_operator_risk_mode,
)


SCHEMA_VERSION = 1
SCHEMA_KIND = "cdna_fill_first_scout_v1"
REPORT_SOURCE = "cdna_fill_first_scout_v1"

ACTION_REFERENCE = "CDNA_REFERENCE_ONLY"
ACTION_DISPLAY_REVIEW = "CDNA_DISPLAY_PRICE_OPERATOR_REVIEW"
ACTION_FILL_FIRST = "CDNA_FILL_FIRST_REVIEW"
ACTION_FILL_CONFIRMED = "CDNA_FILL_CONFIRMED_HEDGE_REQUIRED"
ACTION_HEDGED_COMPLETE = "CDNA_HEDGED_COMPLETE"
ACTION_WATCH = "WATCH"
ACTION_IGNORE = "IGNORE_BLOCKED"

DEFAULT_CDNA_FEE_PER_CONTRACT = 0.02
DEFAULT_MAX_QUOTE_AGE_SECONDS = 900.0

B_DISPLAY_PRICE_ONLY = "cdna_display_price_only"
B_EXECUTABLE_SIZE_UNVERIFIED = "cdna_executable_size_unverified"
B_NO_ORDERBOOK_DEPTH = "cdna_no_orderbook_depth"
B_NO_SERVER_SIDE_QUOTE = "cdna_no_server_side_quote"
B_FILL_REQUIRED = "cdna_fill_required_before_hedge"
B_PARTIAL_FILL_RISK = "cdna_partial_fill_risk"
B_OPERATOR_SIZE_CAP_REQUIRED = "cdna_operator_size_cap_required"
B_FILL_HISTORY_INSUFFICIENT = "cdna_fill_history_insufficient"
B_PARTNER_DEPTH_INSUFFICIENT = "partner_hedge_depth_insufficient"
B_PARTNER_SLIPPAGE_RISK = "partner_hedge_slippage_risk"
B_QUOTE_STALE = "quote_stale"
B_FEE_REVIEW = "fee_review_required"
B_PARTNER_COMPLEMENT_MISSING = "partner_complement_outcome_unavailable"
B_CDNA_SETTLEMENT_MISSING = "cdna_settlement_source_missing"
B_CDNA_RULES_INCOMPLETE = "cdna_rules_incomplete"
B_MISSING_CDNA_DISPLAY_PRICE = "missing_cdna_display_price"
B_MISSING_PARTNER_QUOTE = "missing_partner_quote"
B_MISSING_PARTNER_DEPTH = "missing_partner_depth"
B_NO_POSITIVE_EDGE = "no_positive_indicative_edge"

INFO_BLOCKERS = {
    B_DISPLAY_PRICE_ONLY,
    B_EXECUTABLE_SIZE_UNVERIFIED,
    B_NO_ORDERBOOK_DEPTH,
    B_NO_SERVER_SIDE_QUOTE,
    B_FILL_REQUIRED,
    B_PARTIAL_FILL_RISK,
    B_FILL_HISTORY_INSUFFICIENT,
    B_PARTNER_SLIPPAGE_RISK,
    B_CDNA_SETTLEMENT_MISSING,
    B_CDNA_RULES_INCOMPLETE,
}


def write_cdna_fill_first_scout_files(
    *,
    cdna_evidence: Path,
    partner_evidence: Path,
    partner_platform: str,
    market_family: str,
    league: str,
    season: str | int,
    operator_accept_display_price_risk: bool,
    cdna_operator_size_cap: float,
    max_partner_hedge_slippage: float,
    max_quote_age_seconds: float,
    json_output: Path,
    markdown_output: Path,
    fill_log: Path | None = None,
    generated_at: datetime | None = None,
    operator_risk_mode: str = "conservative",
) -> dict[str, Any]:
    report = build_cdna_fill_first_scout_report(
        cdna_evidence=cdna_evidence,
        partner_evidence=partner_evidence,
        partner_platform=partner_platform,
        market_family=market_family,
        league=league,
        season=season,
        operator_accept_display_price_risk=operator_accept_display_price_risk,
        cdna_operator_size_cap=cdna_operator_size_cap,
        max_partner_hedge_slippage=max_partner_hedge_slippage,
        max_quote_age_seconds=max_quote_age_seconds,
        fill_log=fill_log,
        generated_at=generated_at,
        operator_risk_mode=operator_risk_mode,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_cdna_fill_first_scout_markdown(report), encoding="utf-8")
    return report


def build_cdna_fill_first_scout_report(
    *,
    cdna_evidence: Path,
    partner_evidence: Path,
    partner_platform: str,
    market_family: str,
    league: str,
    season: str | int,
    operator_accept_display_price_risk: bool = False,
    cdna_operator_size_cap: float = 1.0,
    max_partner_hedge_slippage: float = 0.01,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    fill_log: Path | None = None,
    generated_at: datetime | None = None,
    operator_risk_mode: str = "conservative",
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    risk_mode = normalize_operator_risk_mode(operator_risk_mode)
    cdna_payload = _read_json(cdna_evidence)
    partner_payload = _read_json(partner_evidence)
    partner_platform_normalized = _normalize_partner_platform(partner_platform)
    event_key = _event_key(cdna_payload, league=league, season=season)
    fill_records = load_cdna_fill_log(fill_log).get("records") if fill_log is not None else []
    fill_records = fill_records if isinstance(fill_records, list) else []
    cdna_outcomes = _extract_cdna_outcomes(cdna_payload)
    partner_rows = _extract_partner_outcomes(partner_payload, partner_platform=partner_platform_normalized)
    partner_by_team = {row["canonical_team_key"]: row for row in partner_rows if row.get("canonical_team_key")}

    rows: list[dict[str, Any]] = []
    inactive_or_reference = 0
    for outcome in cdna_outcomes:
        if not _is_active_cdna_outcome(outcome):
            inactive_or_reference += 1
            continue
        team_key = _canonical_team_key_from_outcome(outcome)
        partner = partner_by_team.get(team_key or "")
        rows.append(
            _scout_row(
                cdna=outcome,
                partner=partner,
                team_key=team_key,
                direction="CDNA_YES_PARTNER_NO",
                cdna_side="YES",
                partner_side="NO",
                partner_platform=partner_platform_normalized,
                market_family=market_family,
                league=league,
                season=str(season),
                event_key=event_key,
                operator_accept_display_price_risk=operator_accept_display_price_risk,
                cdna_operator_size_cap=cdna_operator_size_cap,
                max_partner_hedge_slippage=max_partner_hedge_slippage,
                max_quote_age_seconds=max_quote_age_seconds,
                generated_at=generated,
                fill_records=fill_records,
                operator_risk_mode=risk_mode,
            )
        )
        rows.append(
            _scout_row(
                cdna=outcome,
                partner=partner,
                team_key=team_key,
                direction="CDNA_NO_PARTNER_YES",
                cdna_side="NO",
                partner_side="YES",
                partner_platform=partner_platform_normalized,
                market_family=market_family,
                league=league,
                season=str(season),
                event_key=event_key,
                operator_accept_display_price_risk=operator_accept_display_price_risk,
                cdna_operator_size_cap=cdna_operator_size_cap,
                max_partner_hedge_slippage=max_partner_hedge_slippage,
                max_quote_age_seconds=max_quote_age_seconds,
                generated_at=generated,
                fill_records=fill_records,
                operator_risk_mode=risk_mode,
            )
        )
    rows.sort(key=_row_sort_key, reverse=True)
    summary = _summary(rows, cdna_outcomes=cdna_outcomes, partner_rows=partner_rows, inactive_or_reference=inactive_or_reference)
    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "saved_files_only": True,
        "strict_exact_arb": False,
        "mathematical_strict_exact_arb": False,
        "exact_ready": False,
        "paper_candidate": summary.get("total_paper_candidate_rows", 0) > 0,
        "global_paper_candidate_emitted": summary.get("total_paper_candidate_rows", 0) > 0,
        "affects_global_evaluator_gates": False,
        "orders_or_execution_logic_added": False,
        "auth_or_account_logic_added": False,
        "operator_accept_display_price_risk": bool(operator_accept_display_price_risk),
        "operator_risk_mode": risk_mode,
        "cdna_evidence": str(cdna_evidence),
        "partner_evidence": str(partner_evidence),
        "partner_platform": partner_platform_normalized,
        "market_family": market_family,
        "league": league,
        "season": str(season),
        "event_key": event_key,
        "parameters": {
            "cdna_operator_size_cap": cdna_operator_size_cap,
            "max_partner_hedge_slippage": max_partner_hedge_slippage,
            "max_quote_age_seconds": max_quote_age_seconds,
            "fill_log": str(fill_log) if fill_log is not None else None,
            "operator_risk_mode": risk_mode,
        },
        "rows": rows,
        "summary_counts": summary,
        "top_blockers": summary["top_blockers"],
        "safety": _safety_block(),
    }


def render_cdna_fill_first_scout_markdown(report: dict[str, Any]) -> str:
    counts = report.get("summary_counts") or {}
    rows = report.get("rows") or []
    lines = [
        "# CDNA Fill-First Scout",
        "",
        "Saved-file-only diagnostic. CDNA display prices are indicative and have no proven pre-fill depth; the CDNA leg must be filled manually before any partner hedge quantity is determined.",
        "",
        "## Summary",
        "",
        f"- partner_platform: `{_md(report.get('partner_platform'))}`",
        f"- market_family: `{_md(report.get('market_family'))}`",
        f"- league: `{_md(report.get('league'))}`",
        f"- season: `{_md(report.get('season'))}`",
        f"- rows: `{counts.get('rows', 0)}`",
        f"- fill_first_review_rows: `{counts.get('cdna_fill_first_review_rows', 0)}`",
        f"- display_price_review_rows: `{counts.get('cdna_display_price_operator_review_rows', 0)}`",
        f"- fill_confirmed_hedge_required_rows: `{counts.get('cdna_fill_confirmed_hedge_required_rows', 0)}`",
        f"- hedged_complete_rows: `{counts.get('cdna_hedged_complete_rows', 0)}`",
        f"- watch_rows: `{counts.get('watch_rows', 0)}`",
        f"- ignore_blocked_rows: `{counts.get('ignore_blocked_rows', 0)}`",
        f"- strict_paper_candidate_rows: `{counts.get('strict_paper_candidate_rows', 0)}`",
        f"- operator_paper_candidate_rows: `{counts.get('operator_paper_candidate_rows', 0)}`",
        f"- cdna_fill_first_paper_candidate_rows: `{counts.get('cdna_fill_first_paper_candidate_rows', 0)}`",
        f"- total_paper_candidate_rows: `{counts.get('total_paper_candidate_rows', 0)}`",
        f"- exact_ready_rows: `0`",
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
        edge = row.get("pre_fill_edge") or {}
        cap = row.get("mirror_liquidity_cap") or {}
        lines.append(
            "| "
            f"{_md(row.get('paper_candidate_class'))} | {_md(row.get('candidate_action'))} | "
            f"{_md(edge.get('gross'))} | {_md(edge.get('net'))} | {_md(cap.get('max_operator_quantity'))} | "
            f"{_md(', '.join(row.get('assumptions_accepted') or []))} | "
            f"{_md(', '.join((row.get('blockers') or []) + (row.get('risk_notes') or [])))} |"
        )
    lines.extend(["", "## Watch Rows", ""])
    lines.extend(_visible_row_list([row for row in rows if row.get("action") == VISIBLE_WATCH][:30]))
    lines.extend(["", "## Ignored/Blocked Rows", ""])
    lines.extend(_visible_row_list([row for row in rows if row.get("action") == VISIBLE_IGNORE][:30]))
    lines.extend(["", "## Top Blockers", "", "| Blocker | Count |", "|---|---:|"])
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
            "- saved_files_only: `true`",
            "- strict_exact_arb: `false`",
            "- mathematical_strict_exact_arb: `false`",
            "- exact_ready: `false`",
            f"- total_paper_candidate_rows: `{counts.get('total_paper_candidate_rows', 0)}`",
            "- evaluator gates affected: `false`",
            "- orders_or_execution_logic_added: `false`",
            "- auth_or_account_logic_added: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _scout_row(
    *,
    cdna: dict[str, Any],
    partner: dict[str, Any] | None,
    team_key: str | None,
    direction: str,
    cdna_side: str,
    partner_side: str,
    partner_platform: str,
    market_family: str,
    league: str,
    season: str,
    event_key: str,
    operator_accept_display_price_risk: bool,
    cdna_operator_size_cap: float,
    max_partner_hedge_slippage: float,
    max_quote_age_seconds: float,
    generated_at: datetime,
    fill_records: list[dict[str, Any]],
    operator_risk_mode: str,
) -> dict[str, Any]:
    cdna_price = _cdna_display_price(cdna, cdna_side)
    cdna_fee = _cdna_fee(cdna)
    cdna_all_in = round(cdna_price + cdna_fee, 8) if cdna_price is not None else None
    partner_leg = _partner_leg(partner, platform=partner_platform, side=partner_side) if partner else None
    partner_ask = (partner_leg or {}).get("ask")
    gross = round(1.0 - cdna_all_in - partner_ask, 8) if cdna_all_in is not None and partner_ask is not None else None
    partner_fee, partner_fee_status = _partner_fee(partner_platform, partner_ask)
    net = round(gross - partner_fee, 8) if gross is not None and partner_fee is not None else None
    fill_confidence = _fill_confidence(cdna, cap=cdna_operator_size_cap)
    partner_quantity = _partner_executable_quantity(partner_leg, max_slippage=max_partner_hedge_slippage)
    max_operator_quantity = (
        round(min(partner_quantity["quantity"], fill_confidence["assumed_fill_quantity"]), 8)
        if partner_quantity["quantity"] is not None
        else 0.0
    )
    fill_record = _matching_fill_record(
        fill_records,
        event_key=event_key,
        cdna=cdna,
        side=cdna_side,
        team_key=team_key,
    )
    hedge_record = _matching_hedge_record(fill_record, fill_records, event_key=event_key, cdna=cdna, team_key=team_key)
    blockers = [
        B_DISPLAY_PRICE_ONLY,
        B_EXECUTABLE_SIZE_UNVERIFIED,
        B_NO_ORDERBOOK_DEPTH,
        B_NO_SERVER_SIDE_QUOTE,
        B_FILL_REQUIRED,
        B_PARTIAL_FILL_RISK,
    ]
    risk_notes = [
        "CDNA display prices are indicative until a manual fill is confirmed.",
        "CDNA executable depth and server-side quote validation are not proven pre-fill.",
        "Hedge quantity must be based on actual filled quantity, not requested quantity.",
    ]
    if _blank(cdna.get("settlement_source")) or "not_visible" in str(cdna.get("settlement_source") or "").lower():
        blockers.append(B_CDNA_SETTLEMENT_MISSING)
    if _blank(cdna.get("rules_text")) or "not_visible" in str(cdna.get("rules_text") or "").lower():
        blockers.append(B_CDNA_RULES_INCOMPLETE)
    if cdna_price is None:
        blockers.append(B_MISSING_CDNA_DISPLAY_PRICE)
    if partner is None:
        blockers.append(B_PARTNER_COMPLEMENT_MISSING)
    if partner_ask is None:
        blockers.append(B_MISSING_PARTNER_QUOTE)
    if partner_quantity["quantity"] is None or partner_quantity["quantity"] <= 0:
        blockers.append(B_MISSING_PARTNER_DEPTH)
        blockers.append(B_PARTNER_DEPTH_INSUFFICIENT)
    if partner_quantity["depth_status"] == "top_of_book_only":
        blockers.append(B_PARTNER_SLIPPAGE_RISK)
    if cdna_operator_size_cap <= 0:
        blockers.append(B_OPERATOR_SIZE_CAP_REQUIRED)
    if fill_confidence["status"] == "insufficient_history_operator_cap":
        blockers.append(B_FILL_HISTORY_INSUFFICIENT)
    blockers.extend(
        _quote_freshness_blockers(
            cdna_timestamp=cdna.get("quote_timestamp"),
            partner_timestamp=(partner_leg or {}).get("quote_timestamp"),
            generated_at=generated_at,
            max_quote_age_seconds=max_quote_age_seconds,
        )
    )
    if partner_fee_status != "OK":
        blockers.append(B_FEE_REVIEW)
    if gross is not None and gross <= 0:
        blockers.append(B_NO_POSITIVE_EDGE)
    blockers = list(dict.fromkeys(blockers))
    action = _recommended_action(
        blockers=blockers,
        gross=gross,
        net=net,
        operator_accept_display_price_risk=operator_accept_display_price_risk,
        max_operator_quantity=max_operator_quantity,
        fill_record=fill_record,
        hedge_record=hedge_record,
    )
    if fill_record:
        risk_notes.append("Manual CDNA fill record found; hedge should use actual filled quantity only.")
    if hedge_record:
        risk_notes.append("Partner hedge record found; row is treated as manually hedged complete.")
    realized = _realized_edge(fill_record, hedge_record)
    row = {
        "row_id": f"{team_key or 'UNKNOWN'}:{direction}",
        "platform_pair": f"CDNA/{partner_platform.upper()}",
        "market_family": market_family,
        "league": league,
        "season": season,
        "event_key": event_key,
        "outcome": _team_name(cdna, team_key),
        "canonical_team_key": team_key,
        "direction": direction,
        "cdna_leg": {
            "side": cdna_side,
            "contract_id": _string_or_none(cdna.get("contract_id")),
            "symbol": _string_or_none(cdna.get("symbol")),
            "display_price": _float_or_none(cdna.get("display_price")),
            "display_no_price": _float_or_none(cdna.get("display_no_price")),
            "fee_per_contract": cdna_fee,
            "all_in_cost_per_contract": cdna_all_in,
            "quote_timestamp": _string_or_none(cdna.get("quote_timestamp")),
            "depth_status": "display_price_only",
            "executable_size_proven": False,
            "assumed_fill_quantity": fill_confidence["assumed_fill_quantity"],
            "fill_confidence_status": fill_confidence["status"],
            "requires_fill_confirmation": True,
        },
        "partner_leg": {
            "platform": partner_platform,
            "side": partner_side,
            "ticker_or_token": (partner_leg or {}).get("ticker_or_token"),
            "ask": partner_ask,
            "bid": (partner_leg or {}).get("bid"),
            "ask_size": (partner_leg or {}).get("ask_size"),
            "available_notional": partner_quantity["available_notional"],
            "depth_status": (partner_leg or {}).get("depth_status"),
            "quote_timestamp": (partner_leg or {}).get("quote_timestamp"),
            "fee_model": partner_fee_status,
        },
        "pre_fill_edge": {
            "gross": gross,
            "partner_fee_estimate": partner_fee,
            "net": net,
            "indicative_only": True,
        },
        "mirror_liquidity_cap": {
            "partner_executable_quantity_within_slippage_cap": partner_quantity["quantity"],
            "max_partner_hedge_slippage": max_partner_hedge_slippage,
            "cdna_assumed_fill_quantity": fill_confidence["assumed_fill_quantity"],
            "max_operator_quantity": max_operator_quantity,
            "partner_depth_basis": partner_quantity["depth_status"],
        },
        "fill_record": fill_record,
        "partner_hedge_record": hedge_record,
        "realized_edge": realized,
        "recommended_action": action,
        "action": _visible_action(action),
        "blockers": blockers,
        "risk_notes": risk_notes,
        "strict_exact_arb": False,
        "mathematical_strict_exact_arb": False,
        "paper_candidate": False,
        "exact_ready": False,
        "affects_evaluator_gates": False,
        "diagnostic_only": True,
    }
    make_candidate = action == ACTION_FILL_FIRST and operator_risk_mode == "aggressive"
    return apply_operator_candidate_fields(
        row,
        paper_class=CLASS_CDNA,
        assumptions_accepted=[
            "cdna_display_price_assumed_fillable_at_operator_cap",
            "cdna_executable_size_unverified_pre_fill",
        ],
        candidate_action="FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY",
        make_candidate=make_candidate,
        mathematical_strict_exact_arb=False,
    )


def _recommended_action(
    *,
    blockers: list[str],
    gross: float | None,
    net: float | None,
    operator_accept_display_price_risk: bool,
    max_operator_quantity: float,
    fill_record: dict[str, Any] | None,
    hedge_record: dict[str, Any] | None,
) -> str:
    hard_ignore = {B_PARTNER_COMPLEMENT_MISSING, B_MISSING_CDNA_DISPLAY_PRICE}
    if any(blocker in blockers for blocker in hard_ignore):
        return ACTION_IGNORE
    if fill_record and hedge_record:
        return ACTION_HEDGED_COMPLETE
    if fill_record:
        return ACTION_FILL_CONFIRMED
    if B_QUOTE_STALE in blockers:
        return ACTION_WATCH
    if any(blocker in blockers for blocker in (B_MISSING_PARTNER_QUOTE, B_MISSING_PARTNER_DEPTH, B_PARTNER_DEPTH_INSUFFICIENT)):
        return ACTION_WATCH
    if gross is None or gross <= 0 or net is None or net <= 0:
        return ACTION_REFERENCE
    if max_operator_quantity <= 0:
        return ACTION_WATCH
    if not operator_accept_display_price_risk:
        return ACTION_DISPLAY_REVIEW
    return ACTION_FILL_FIRST


def _extract_cdna_outcomes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = payload.get("outcomes")
    if isinstance(outcomes, list):
        inherited = {
            "settlement_source": payload.get("settlement_source"),
            "rules_text": payload.get("rules_text"),
        }
        rows: list[dict[str, Any]] = []
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            row = dict(outcome)
            for key, value in inherited.items():
                if _blank(row.get(key)) and value is not None:
                    row[key] = value
            rows.append(row)
        return rows
    return []


def _extract_partner_outcomes(payload: dict[str, Any], *, partner_platform: str) -> list[dict[str, Any]]:
    outcomes = payload.get("outcomes")
    if not isinstance(outcomes, list):
        return []
    rows: list[dict[str, Any]] = []
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        team_key = _canonical_team_key_from_outcome(outcome)
        rows.append(
            {
                "canonical_team_key": team_key,
                "team_name": _string_or_none(outcome.get("team_name") or outcome.get("outcome_name")),
                "market_ticker": _string_or_none(outcome.get("market_ticker") or outcome.get("ticker")),
                "market_id": _string_or_none(outcome.get("market_id")),
                "condition_id": _string_or_none(outcome.get("condition_id")),
                "token_id_yes": _string_or_none(outcome.get("token_id_yes") or outcome.get("yes_token_id")),
                "token_id_no": _string_or_none(outcome.get("token_id_no") or outcome.get("no_token_id")),
                "yes_bid": _float_or_none(outcome.get("yes_bid")),
                "yes_ask": _float_or_none(outcome.get("yes_ask")),
                "yes_bid_size": _float_or_none(outcome.get("yes_bid_size")),
                "yes_ask_size": _float_or_none(outcome.get("yes_ask_size")),
                "no_bid": _float_or_none(outcome.get("no_bid")),
                "no_ask": _float_or_none(outcome.get("no_ask")),
                "no_bid_size": _float_or_none(outcome.get("no_bid_size")),
                "no_ask_size": _float_or_none(outcome.get("no_ask_size")),
                "depth_status": _string_or_none(outcome.get("depth_status")),
                "quote_timestamp": _string_or_none(outcome.get("quote_timestamp")),
                "platform": partner_platform,
            }
        )
    return rows


def _partner_leg(partner: dict[str, Any] | None, *, platform: str, side: str) -> dict[str, Any] | None:
    if partner is None:
        return None
    prefix = side.lower()
    if platform == "kalshi":
        ticker_or_token = partner.get("market_ticker")
    elif side == "YES":
        ticker_or_token = partner.get("token_id_yes") or partner.get("market_id")
    else:
        ticker_or_token = partner.get("token_id_no") or partner.get("market_id")
    return {
        "platform": platform,
        "side": side,
        "ticker_or_token": ticker_or_token,
        "ask": partner.get(f"{prefix}_ask"),
        "bid": partner.get(f"{prefix}_bid"),
        "ask_size": partner.get(f"{prefix}_ask_size"),
        "depth_status": partner.get("depth_status"),
        "quote_timestamp": partner.get("quote_timestamp"),
    }


def _partner_executable_quantity(leg: dict[str, Any] | None, *, max_slippage: float) -> dict[str, Any]:
    if leg is None:
        return {"quantity": None, "available_notional": None, "depth_status": "missing"}
    ask = leg.get("ask")
    size = leg.get("ask_size")
    if ask is None or size is None:
        return {"quantity": None, "available_notional": None, "depth_status": "missing"}
    depth_text = str(leg.get("depth_status") or "").lower()
    status = "full_clob" if "full_clob" in depth_text or "full clob" in depth_text else "top_of_book_only"
    return {
        "quantity": round(size, 8),
        "available_notional": round(ask * size, 8),
        "depth_status": status,
        "max_slippage": max_slippage,
    }


def _fill_confidence(cdna: dict[str, Any], *, cap: float) -> dict[str, Any]:
    samples = cdna.get("fill_confidence_samples")
    values: list[float] = []
    if isinstance(samples, list):
        values = sorted(value for value in (_float_or_none(item) for item in samples) if value is not None and value >= 0)
    if len(values) >= 5:
        index = max(0, min(len(values) - 1, int(len(values) * 0.25)))
        return {"assumed_fill_quantity": round(min(values[index], cap), 8), "status": "p25_fill_history_capped"}
    return {"assumed_fill_quantity": max(0.0, float(cap)), "status": "insufficient_history_operator_cap"}


def _matching_fill_record(
    records: list[dict[str, Any]],
    *,
    event_key: str,
    cdna: dict[str, Any],
    side: str,
    team_key: str | None,
) -> dict[str, Any] | None:
    contract_id = _string_or_none(cdna.get("contract_id"))
    symbol = _string_or_none(cdna.get("symbol"))
    for record in records:
        if not isinstance(record, dict):
            continue
        if "hedge" in str(record.get("schema_kind") or record.get("record_type") or "").lower():
            continue
        if _string_or_none(record.get("event_key")) not in {None, event_key}:
            continue
        if str(record.get("side") or "").upper() != side:
            continue
        if contract_id and _string_or_none(record.get("contract_id")) == contract_id:
            return record
        if symbol and _string_or_none(record.get("symbol")) == symbol:
            return record
        if team_key and _canonical_team_key_any(record.get("team")) == team_key:
            return record
    return None


def _matching_hedge_record(
    fill_record: dict[str, Any] | None,
    records: list[dict[str, Any]],
    *,
    event_key: str,
    cdna: dict[str, Any],
    team_key: str | None,
) -> dict[str, Any] | None:
    if not fill_record:
        return None
    nested = fill_record.get("partner_hedge_record")
    if isinstance(nested, dict):
        return nested
    contract_id = _string_or_none(cdna.get("contract_id"))
    for record in records:
        if not isinstance(record, dict):
            continue
        text = str(record.get("schema_kind") or record.get("record_type") or "").lower()
        if "hedge" not in text:
            continue
        if _string_or_none(record.get("event_key")) not in {None, event_key}:
            continue
        if contract_id and _string_or_none(record.get("cdna_contract_id") or record.get("contract_id")) == contract_id:
            return record
        if team_key and _canonical_team_key_any(record.get("team")) == team_key:
            return record
    return None


def _realized_edge(fill_record: dict[str, Any] | None, hedge_record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not fill_record or not hedge_record:
        return None
    fill_qty = _float_or_none(fill_record.get("filled_quantity"))
    fill_cost = _float_or_none(fill_record.get("all_in_filled_cost"))
    hedge_qty = _float_or_none(hedge_record.get("filled_quantity") or hedge_record.get("hedged_quantity"))
    hedge_price = _float_or_none(hedge_record.get("filled_price_per_contract") or hedge_record.get("hedge_price"))
    hedge_fee = _float_or_none(hedge_record.get("fee_per_contract") or hedge_record.get("hedge_fee_per_contract")) or 0.0
    if fill_qty is None or fill_cost is None or hedge_qty is None or hedge_price is None:
        return {"status": "not_calculated"}
    qty = min(fill_qty, hedge_qty)
    hedge_cost = qty * (hedge_price + hedge_fee)
    return {
        "status": "calculated" if qty > 0 else "not_calculated",
        "hedged_quantity": qty,
        "all_in_cost": round(fill_cost + hedge_cost, 8),
        "normal_state_payoff": round(qty, 8),
        "edge": round(qty - fill_cost - hedge_cost, 8),
    }


def _cdna_display_price(cdna: dict[str, Any], side: str) -> float | None:
    key = "display_price" if side == "YES" else "display_no_price"
    return _float_or_none(cdna.get(key))


def _cdna_fee(cdna: dict[str, Any]) -> float:
    for key in ("fee_per_contract", "exchange_fee", "exchange_fee_per_contract"):
        value = _float_or_none(cdna.get(key))
        if value is not None:
            return value
    return DEFAULT_CDNA_FEE_PER_CONTRACT


def _partner_fee(platform: str, ask: float | None) -> tuple[float | None, str]:
    if ask is None:
        return None, "NOT_CALCULATED"
    if platform == "kalshi":
        return KalshiTieredFeeModel().fee_for_leg(ask), "OK"
    if platform == "polymarket":
        return PolymarketConservativeFeeModel().fee_for_leg_for_category(ask, category="sports"), "OK"
    return None, "FEE_REVIEW_REQUIRED"


def _quote_freshness_blockers(
    *,
    cdna_timestamp: Any,
    partner_timestamp: Any,
    generated_at: datetime,
    max_quote_age_seconds: float,
) -> list[str]:
    blockers: list[str] = []
    for value in (cdna_timestamp, partner_timestamp):
        parsed = _parse_datetime(value)
        if parsed is None:
            blockers.append(B_QUOTE_STALE)
            continue
        if (generated_at - parsed).total_seconds() > max_quote_age_seconds:
            blockers.append(B_QUOTE_STALE)
    return list(dict.fromkeys(blockers))


def _summary(
    rows: list[dict[str, Any]],
    *,
    cdna_outcomes: list[dict[str, Any]],
    partner_rows: list[dict[str, Any]],
    inactive_or_reference: int,
) -> dict[str, Any]:
    actions = Counter(row.get("recommended_action") for row in rows)
    blockers = Counter()
    for row in rows:
        blockers.update(row.get("blockers") or [])
    return {
        "rows": len(rows),
        "cdna_outcomes_loaded": len(cdna_outcomes),
        "cdna_active_outcomes": sum(1 for row in cdna_outcomes if _is_active_cdna_outcome(row)),
        "cdna_inactive_or_reference_outcomes": inactive_or_reference,
        "partner_rows_loaded": len(partner_rows),
        "matched_rows": sum(1 for row in rows if B_PARTNER_COMPLEMENT_MISSING not in (row.get("blockers") or [])),
        "positive_gross_rows": sum(1 for row in rows if _float_or_none((row.get("pre_fill_edge") or {}).get("gross")) is not None and _float_or_none((row.get("pre_fill_edge") or {}).get("gross")) > 0),
        "positive_net_rows": sum(1 for row in rows if _float_or_none((row.get("pre_fill_edge") or {}).get("net")) is not None and _float_or_none((row.get("pre_fill_edge") or {}).get("net")) > 0),
        "cdna_reference_only_rows": actions.get(ACTION_REFERENCE, 0),
        "cdna_display_price_operator_review_rows": actions.get(ACTION_DISPLAY_REVIEW, 0),
        "cdna_fill_first_review_rows": actions.get(ACTION_FILL_FIRST, 0),
        "cdna_fill_confirmed_hedge_required_rows": actions.get(ACTION_FILL_CONFIRMED, 0),
        "cdna_hedged_complete_rows": actions.get(ACTION_HEDGED_COMPLETE, 0),
        "watch_rows": actions.get(ACTION_WATCH, 0),
        "ignore_blocked_rows": actions.get(ACTION_IGNORE, 0),
        "exact_ready_rows": 0,
        "paper_candidate_rows": candidate_counts(rows)["total_paper_candidate_rows"],
        "global_paper_candidate_emitted": candidate_counts(rows)["total_paper_candidate_rows"] > 0,
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(15)],
        **candidate_counts(rows),
    }


def _safety_block() -> dict[str, Any]:
    return {
        "diagnostic_only": True,
        "saved_files_only": True,
        "strict_exact_arb": False,
        "mathematical_strict_exact_arb": False,
        "exact_ready": False,
        "paper_candidate": False,
        "global_paper_candidate_emitted": False,
        "affects_global_evaluator_gates": False,
        "orders_or_execution_logic_added": False,
        "auth_or_account_logic_added": False,
    }


def _visible_action(recommended_action: str) -> str:
    if recommended_action in {ACTION_IGNORE, ACTION_HEDGED_COMPLETE}:
        return VISIBLE_IGNORE
    return VISIBLE_WATCH


def _visible_row_list(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["_None._"]
    return [
        f"- `{_md(row.get('row_id'))}` action=`{_md(row.get('action'))}` recommended_action=`{_md(row.get('recommended_action'))}` blockers=`{_md(', '.join(row.get('blockers') or []))}`"
        for row in rows
    ]


def _event_key(payload: dict[str, Any], *, league: str, season: str | int) -> str:
    explicit = _string_or_none(payload.get("event_key"))
    if explicit:
        return explicit
    return f"{str(league).upper()}_CHAMPION_{season}"


def _is_active_cdna_outcome(outcome: dict[str, Any]) -> bool:
    status = str(outcome.get("outcome_status") or outcome.get("status") or "").lower()
    if not status:
        return True
    return not any(token in status for token in ("inactive", "expired", "resolved", "closed", "finalized", "settled"))


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _normalize_partner_platform(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in {"kalshi", "polymarket"}:
        return normalized
    return normalized


_TOKEN_RE = re.compile(r"[^a-z0-9]+")

TEAM_ALIASES = {
    "okc": "OKC",
    "oklahoma city": "OKC",
    "oklahoma city thunder": "OKC",
    "thunder": "OKC",
    "nyk": "NYK",
    "ny": "NYK",
    "new york": "NYK",
    "new york knicks": "NYK",
    "knicks": "NYK",
    "sas": "SAS",
    "sa": "SAS",
    "san antonio": "SAS",
    "san antonio spurs": "SAS",
    "spurs": "SAS",
    "bos": "BOS",
    "boston celtics": "BOS",
    "celtics": "BOS",
    "lal": "LAL",
    "los angeles lakers": "LAL",
    "lakers": "LAL",
    "lac": "LAC",
    "los angeles clippers": "LAC",
    "clippers": "LAC",
    "gsw": "GSW",
    "gs": "GSW",
    "golden state warriors": "GSW",
    "warriors": "GSW",
    "phi": "PHI",
    "philadelphia 76ers": "PHI",
    "sixers": "PHI",
    "76ers": "PHI",
    "nop": "NOP",
    "no": "NOP",
    "new orleans pelicans": "NOP",
    "pelicans": "NOP",
    "den": "DEN",
    "denver nuggets": "DEN",
    "min": "MIN",
    "minnesota timberwolves": "MIN",
    "dal": "DAL",
    "dallas mavericks": "DAL",
    "ind": "IND",
    "indiana pacers": "IND",
    "cle": "CLE",
    "cleveland cavaliers": "CLE",
    "cavaliers": "CLE",
    "mil": "MIL",
    "milwaukee bucks": "MIL",
    "mia": "MIA",
    "miami heat": "MIA",
    "orl": "ORL",
    "orlando magic": "ORL",
    "chi": "CHI",
    "chicago bulls": "CHI",
    "det": "DET",
    "detroit pistons": "DET",
    "atl": "ATL",
    "atlanta hawks": "ATL",
    "cha": "CHA",
    "charlotte hornets": "CHA",
    "was": "WAS",
    "wsh": "WAS",
    "washington wizards": "WAS",
    "tor": "TOR",
    "toronto raptors": "TOR",
    "bkn": "BKN",
    "brooklyn nets": "BKN",
    "mem": "MEM",
    "memphis grizzlies": "MEM",
    "hou": "HOU",
    "houston rockets": "HOU",
    "uta": "UTA",
    "utah jazz": "UTA",
    "phx": "PHX",
    "phoenix suns": "PHX",
    "sac": "SAC",
    "sacramento kings": "SAC",
    "por": "POR",
    "portland trail blazers": "POR",
}


def _canonical_team_key_from_outcome(outcome: dict[str, Any]) -> str | None:
    values = [outcome.get("team_name"), outcome.get("outcome_name"), outcome.get("market_ticker"), outcome.get("symbol")]
    aliases = outcome.get("team_aliases")
    if isinstance(aliases, list):
        values.extend(aliases)
    for value in values:
        key = _canonical_team_key_any(value)
        if key:
            return key
    return None


def _canonical_team_key_any(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    if "-" in text:
        parts = [part for part in re.split(r"[-_]", text) if part]
        for part in reversed(parts):
            key = _canonical_team_key_any(part)
            if key:
                return key
    normalized = _TOKEN_RE.sub(" ", text.lower()).strip()
    return TEAM_ALIASES.get(normalized)


def _team_name(outcome: dict[str, Any], team_key: str | None) -> str:
    return str(outcome.get("team_name") or outcome.get("outcome_name") or team_key or "unknown")


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _blank(value: Any) -> bool:
    return _string_or_none(value) is None


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


def _row_sort_key(row: dict[str, Any]) -> tuple[float, float, float]:
    action_score = {
        ACTION_FILL_FIRST: 6,
        ACTION_FILL_CONFIRMED: 5,
        ACTION_HEDGED_COMPLETE: 4,
        ACTION_DISPLAY_REVIEW: 3,
        ACTION_WATCH: 2,
        ACTION_REFERENCE: 1,
        ACTION_IGNORE: 0,
    }.get(row.get("recommended_action"), 0)
    net = _float_or_none((row.get("pre_fill_edge") or {}).get("net"))
    qty = _float_or_none((row.get("mirror_liquidity_cap") or {}).get("max_operator_quantity"))
    return (action_score, net if net is not None else -999.0, qty if qty is not None else 0.0)


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _row_md(row: dict[str, Any]) -> str:
    edge = row.get("pre_fill_edge") or {}
    cdna_leg = row.get("cdna_leg") or {}
    partner_leg = row.get("partner_leg") or {}
    cap = row.get("mirror_liquidity_cap") or {}
    return (
        "| "
        f"{_md(row.get('outcome'))} | "
        f"{_md(row.get('direction'))} | "
        f"{_md(cdna_leg.get('all_in_cost_per_contract'))} | "
        f"{_md(partner_leg.get('ask'))} | "
        f"{_md(edge.get('gross'))} | "
        f"{_md(edge.get('net'))} | "
        f"{_md(cap.get('max_operator_quantity'))} | "
        f"{_md(row.get('recommended_action'))} | "
        f"{_md(', '.join(row.get('blockers') or []))} |"
    )
