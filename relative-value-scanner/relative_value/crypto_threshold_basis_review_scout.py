from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.fees import KalshiTieredFeeModel, PolymarketConservativeFeeModel
from relative_value.operator_paper_candidate_policy import (
    ACTION_PAPER,
    CLASS_CDNA,
    CLASS_OPERATOR,
    apply_operator_candidate_fields,
    candidate_counts,
    ensure_candidate_fields,
    has_hard_blocker,
    normalize_operator_risk_mode,
)


SCHEMA_KIND = "crypto_threshold_basis_review_scout_v1"
SCHEMA_VERSION = 1

ACTION_BASIS_RISK_REVIEW = "BASIS_RISK_REVIEW"
ACTION_CDNA_FILL_FIRST = "CDNA_FILL_FIRST_REVIEW"
ACTION_MANUAL_REVIEW = "MANUAL_REVIEW"
ACTION_WATCH = "WATCH"
ACTION_IGNORE_BLOCKED = "IGNORE_BLOCKED"

B_SOURCE_MISMATCH = "source_index_mismatch"
B_BASIS_REVIEW = "basis_risk_review_required"
B_TIME_MISMATCH = "target_time_mismatch"
B_GRID_MISMATCH = "threshold_grid_mismatch"
B_STALE_QUOTE = "stale_or_missing_quote"
B_MISSING_DEPTH = "missing_quote_depth"
B_FEE_REVIEW = "fee_review_required"
B_SCOPE = "unsupported_crypto_threshold_scope"
B_MISSING_QUOTE = "missing_quote"
B_DATE_MISMATCH = "target_date_mismatch"
B_CDNA_DISPLAY = "cdna_display_price_only"
B_CDNA_SIZE = "cdna_executable_size_unverified"
B_CDNA_DEPTH = "cdna_no_orderbook_depth"
B_CDNA_SERVER = "cdna_no_server_side_quote"

DEFAULT_MAX_QUOTE_AGE_SECONDS = 1800.0
DEFAULT_CDNA_FEE_PER_CONTRACT = 0.02


def write_crypto_threshold_basis_review_scout_files(
    *,
    kalshi_evidence: Path,
    polymarket_evidence: Path,
    cdna_evidence: Path | None = None,
    asset: str,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    operator_risk_mode: str = "conservative",
) -> dict[str, Any]:
    report = build_crypto_threshold_basis_review_scout_report(
        kalshi_evidence=kalshi_evidence,
        polymarket_evidence=polymarket_evidence,
        cdna_evidence=cdna_evidence,
        asset=asset,
        generated_at=generated_at,
        max_quote_age_seconds=max_quote_age_seconds,
        operator_risk_mode=operator_risk_mode,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_crypto_threshold_basis_review_scout_markdown(report), encoding="utf-8")
    return report


def build_crypto_threshold_basis_review_scout_report(
    *,
    kalshi_evidence: Path,
    polymarket_evidence: Path,
    cdna_evidence: Path | None = None,
    asset: str,
    generated_at: datetime | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    operator_risk_mode: str = "conservative",
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    expected_asset = str(asset).strip().upper()
    risk_mode = normalize_operator_risk_mode(operator_risk_mode)
    kalshi_payload = _read_json(kalshi_evidence)
    poly_payload = _read_json(polymarket_evidence)
    cdna_payload = _read_json(cdna_evidence) if cdna_evidence is not None else None
    scope_blockers = _scope_blockers(kalshi_payload, "Kalshi", expected_asset) + _scope_blockers(poly_payload, "Polymarket", expected_asset)
    kalshi_rows = _extract_rows(kalshi_payload, platform="kalshi", expected_asset=expected_asset)
    poly_rows = _extract_rows(poly_payload, platform="polymarket", expected_asset=expected_asset)
    cdna_rows = _extract_cdna_rows(cdna_payload, expected_asset=expected_asset) if cdna_payload is not None else []
    poly_by_key: dict[tuple[str, float], list[dict[str, Any]]] = {}
    for row in poly_rows:
        if row.get("target_date") and row.get("threshold") is not None:
            poly_by_key.setdefault((row["target_date"], row["threshold"]), []).append(row)
    rows: list[dict[str, Any]] = []
    for kalshi in kalshi_rows:
        matches = poly_by_key.get((kalshi.get("target_date"), kalshi.get("threshold")), [])
        for polymarket in matches:
            rows.append(
                _basis_row(
                    kalshi=kalshi,
                    polymarket=polymarket,
                    direction="KALSHI_YES_POLYMARKET_NO",
                    kalshi_side="YES",
                    poly_side="NO",
                    scope_blockers=scope_blockers,
                    generated_at=generated,
                    max_quote_age_seconds=max_quote_age_seconds,
                    operator_risk_mode=risk_mode,
                )
            )
            rows.append(
                _basis_row(
                    kalshi=kalshi,
                    polymarket=polymarket,
                    direction="POLYMARKET_YES_KALSHI_NO",
                    kalshi_side="NO",
                    poly_side="YES",
                    scope_blockers=scope_blockers,
                    generated_at=generated,
                    max_quote_age_seconds=max_quote_age_seconds,
                    operator_risk_mode=risk_mode,
                )
            )
    rows.extend(_unmatched_rows(kalshi_rows, poly_rows, scope_blockers=scope_blockers))
    if cdna_rows:
        rows.extend(
            _cdna_rows(
                cdna_rows=cdna_rows,
                kalshi_rows=kalshi_rows,
                poly_rows=poly_rows,
                generated_at=generated,
                max_quote_age_seconds=max_quote_age_seconds,
                operator_risk_mode=risk_mode,
            )
        )
    rows.sort(key=_row_sort_key, reverse=True)
    summary = _summary(rows, kalshi_rows, poly_rows, cdna_rows)
    return {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "source": SCHEMA_KIND,
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "strict_exact_arb": False,
        "basis_risk_only": True,
        "operator_risk_mode": risk_mode,
        "exact_ready_rows": 0,
        "paper_candidate_rows": summary.get("total_paper_candidate_rows", 0),
        "standard_paper_candidate_emitted": summary.get("total_paper_candidate_rows", 0) > 0,
        "candidate_pair_creation": False,
        "evaluator_invoked": False,
        "asset": expected_asset,
        "kalshi_evidence": str(kalshi_evidence),
        "polymarket_evidence": str(polymarket_evidence),
        "cdna_evidence": str(cdna_evidence) if cdna_evidence is not None else None,
        "scope_blockers": sorted(set(scope_blockers)),
        "kalshi_rows_loaded": len(kalshi_rows),
        "polymarket_rows_loaded": len(poly_rows),
        "cdna_rows_loaded": len(cdna_rows),
        "kalshi_market_keys": _market_keys(kalshi_rows),
        "polymarket_market_keys": _market_keys(poly_rows),
        "cdna_market_keys": _market_keys(cdna_rows),
        "kalshi_comparator": _normalize_comparator(kalshi_payload.get("comparator")),
        "polymarket_comparator": _normalize_comparator(poly_payload.get("comparator")),
        "rows": rows,
        "summary_counts": summary,
        "top_blockers": summary["top_blockers"],
        "safety": {
            "diagnostic_only": True,
            "saved_files_only": True,
            "strict_exact_arb": False,
            "basis_risk_only": True,
            "exact_ready_rows": 0,
            "paper_candidate_rows": summary.get("total_paper_candidate_rows", 0),
            "strict_paper_candidate_rows": summary.get("strict_paper_candidate_rows", 0),
            "operator_paper_candidate_rows": summary.get("operator_paper_candidate_rows", 0),
            "cdna_fill_first_paper_candidate_rows": summary.get("cdna_fill_first_paper_candidate_rows", 0),
            "total_paper_candidate_rows": summary.get("total_paper_candidate_rows", 0),
            "candidate_pair_creation": False,
            "uses_midpoint": False,
            "uses_asks_for_entry": True,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
        },
    }


def render_crypto_threshold_basis_review_scout_markdown(report: dict[str, Any]) -> str:
    counts = report.get("summary_counts") or {}
    lines = [
        "# Crypto Threshold Basis-Review Scout",
        "",
        "Basis-risk review only, not exact arb. Kalshi and Polymarket may share broad point-in-time threshold shape, but different price indexes, observation methodology, and time grids prevent exact-payoff treatment.",
        "",
        "## Summary",
        "",
        f"- asset: `{_md(report.get('asset'))}`",
        f"- rows: `{counts.get('rows', 0)}`",
        f"- matched_threshold_rows: `{counts.get('matched_threshold_rows', 0)}`",
        f"- basis_risk_review_rows: `{counts.get('basis_risk_review_rows', 0)}`",
        f"- operator_paper_candidate_rows: `{counts.get('operator_paper_candidate_rows', 0)}`",
        f"- cdna_fill_first_paper_candidate_rows: `{counts.get('cdna_fill_first_paper_candidate_rows', 0)}`",
        f"- total_paper_candidate_rows: `{counts.get('total_paper_candidate_rows', 0)}`",
        f"- manual_review_rows: `{counts.get('manual_review_rows', 0)}`",
        f"- watch_rows: `{counts.get('watch_rows', 0)}`",
        f"- cdna_fill_first_review_rows: `{counts.get('cdna_fill_first_review_rows', 0)}`",
        f"- ignore_blocked_rows: `{counts.get('ignore_blocked_rows', 0)}`",
        f"- exact_ready_rows: `0`",
        f"- total_paper_candidate_rows: `{counts.get('total_paper_candidate_rows', 0)}`",
        "",
        "## Paper Candidates",
        "",
        "| Class | Candidate action | Gross edge | Net edge | Size/notional | Assumptions accepted | Blockers/risk notes |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    paper_rows = [row for row in report.get("rows") or [] if row.get("paper_candidate")]
    if not paper_rows:
        lines.append("| none |  |  |  |  |  |  |  |")
    for row in paper_rows[:30]:
        lines.append(
            "| "
            f"{_md(row.get('paper_candidate_class'))} | {_md(row.get('candidate_action'))} | "
            f"{_md(row.get('gross_edge'))} | {_md(row.get('net_edge'))} | {_md(row.get('available_notional'))} | "
            f"{_md(', '.join(row.get('assumptions_accepted') or []))} | "
            f"{_md(', '.join((row.get('blockers') or []) + (row.get('risk_notes') or [])))} |"
        )
    lines.extend([
        "",
        "## Watch Rows",
        "",
        "| Direction | Threshold | Date | Kalshi ask | Polymarket ask | Gross edge | Net edge | Action | Blockers |",
        "|---|---:|---|---:|---:|---:|---:|---|---|",
    ])
    for row in (report.get("rows") or [])[:30]:
        if row.get("direction") == "UNMATCHED":
            continue
        if row.get("paper_candidate"):
            continue
        lines.append(
            "| "
            f"{_md(row.get('direction'))} | "
            f"{_md(row.get('threshold'))} | "
            f"{_md(row.get('target_date'))} | "
            f"{_md(row.get('kalshi_ask'))} | "
            f"{_md(row.get('polymarket_ask'))} | "
            f"{_md(row.get('gross_edge'))} | "
            f"{_md(row.get('net_edge'))} | "
            f"{_md(row.get('action'))} | "
            f"{_md(', '.join(row.get('blockers') or []))} |"
        )
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
            "- strict_exact_arb: `false`",
            "- basis_risk_only: `true`",
            "- exact_ready_rows: `0`",
            f"- total_paper_candidate_rows: `{counts.get('total_paper_candidate_rows', 0)}`",
            "- candidate_pair_creation: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _basis_row(
    *,
    kalshi: dict[str, Any],
    polymarket: dict[str, Any],
    direction: str,
    kalshi_side: str,
    poly_side: str,
    scope_blockers: list[str],
    generated_at: datetime,
    max_quote_age_seconds: float,
    operator_risk_mode: str,
) -> dict[str, Any]:
    kalshi_leg = _leg(kalshi, kalshi_side)
    poly_leg = _leg(polymarket, poly_side)
    kalshi_ask = kalshi_leg["ask"]
    poly_ask = poly_leg["ask"]
    gross = round(1.0 - kalshi_ask - poly_ask, 8) if kalshi_ask is not None and poly_ask is not None else None
    fee, net, net_status = _fee_estimate(kalshi_ask, poly_ask, gross)
    available_notional = _available_notional(kalshi_leg, poly_leg)
    blockers = list(scope_blockers)
    blockers.extend([B_SOURCE_MISMATCH, B_BASIS_REVIEW])
    if kalshi.get("price_source_key") != polymarket.get("price_source_key"):
        blockers.append(B_SOURCE_MISMATCH)
    if kalshi.get("target_time") != polymarket.get("target_time"):
        blockers.append(B_TIME_MISMATCH)
    if kalshi.get("threshold") != polymarket.get("threshold"):
        blockers.append(B_GRID_MISMATCH)
    if kalshi.get("target_date") != polymarket.get("target_date"):
        blockers.append(B_DATE_MISMATCH)
    if kalshi_ask is None or poly_ask is None:
        blockers.append(B_MISSING_QUOTE)
    if available_notional is None:
        blockers.append(B_MISSING_DEPTH)
    if _stale(kalshi.get("quote_timestamp"), generated_at, max_quote_age_seconds) or _stale(polymarket.get("quote_timestamp"), generated_at, max_quote_age_seconds):
        blockers.append(B_STALE_QUOTE)
    if net_status != "OK":
        blockers.append(B_FEE_REVIEW)
    blockers = sorted(set(blockers))
    action = _action(blockers, gross_edge=gross, net_edge=net)
    row = {
        "asset": kalshi.get("asset"),
        "threshold": kalshi.get("threshold"),
        "target_date": kalshi.get("target_date"),
        "target_time_kalshi": kalshi.get("target_time"),
        "target_time_polymarket": polymarket.get("target_time"),
        "timezone_kalshi": kalshi.get("timezone"),
        "timezone_polymarket": polymarket.get("timezone"),
        "kalshi_ticker": kalshi.get("market_ticker"),
        "polymarket_market_id": polymarket.get("platform_market_id"),
        "polymarket_condition_id": polymarket.get("condition_id"),
        "direction": direction,
        "kalshi_ask": kalshi_ask,
        "polymarket_ask": poly_ask,
        "gross_edge": gross,
        "conservative_fee_estimate": fee,
        "net_edge": net,
        "net_edge_status": net_status,
        "available_notional": available_notional,
        "price_sources": {"kalshi": kalshi.get("price_source"), "polymarket": polymarket.get("price_source")},
        "quote_timestamps": {"kalshi": kalshi.get("quote_timestamp"), "polymarket": polymarket.get("quote_timestamp")},
        "blockers": blockers,
        "action": action,
        "strict_exact_arb": False,
        "basis_risk_only": True,
        "exact_ready": False,
        "paper_candidate": False,
        "standard_paper_candidate": False,
        "diagnostic_only": True,
    }
    make_candidate = (
        operator_risk_mode == "aggressive"
        and net is not None
        and net > 0
        and not has_hard_blocker(blockers, accepted_basis=True)
    )
    return apply_operator_candidate_fields(
        row,
        paper_class=CLASS_OPERATOR,
        assumptions_accepted=["crypto_source_index_basis_risk", "target_time_basis_risk"],
        candidate_action="PAPER_CANDIDATE",
        make_candidate=make_candidate,
        mathematical_strict_exact_arb=False,
    )


def _cdna_rows(
    *,
    cdna_rows: list[dict[str, Any]],
    kalshi_rows: list[dict[str, Any]],
    poly_rows: list[dict[str, Any]],
    generated_at: datetime,
    max_quote_age_seconds: float,
    operator_risk_mode: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    kalshi_by_key = {(row.get("target_date"), row.get("threshold")): row for row in kalshi_rows}
    poly_by_key = {(row.get("target_date"), row.get("threshold")): row for row in poly_rows}
    for cdna in cdna_rows:
        key = (cdna.get("target_date"), cdna.get("threshold"))
        for partner_name, partner in (("polymarket", poly_by_key.get(key)), ("kalshi", kalshi_by_key.get(key))):
            if partner is None:
                continue
            out.append(_cdna_row(cdna=cdna, partner=partner, partner_platform=partner_name, cdna_side="YES", partner_side="NO", generated_at=generated_at, max_quote_age_seconds=max_quote_age_seconds, operator_risk_mode=operator_risk_mode))
            out.append(_cdna_row(cdna=cdna, partner=partner, partner_platform=partner_name, cdna_side="NO", partner_side="YES", generated_at=generated_at, max_quote_age_seconds=max_quote_age_seconds, operator_risk_mode=operator_risk_mode))
    return out


def _cdna_row(
    *,
    cdna: dict[str, Any],
    partner: dict[str, Any],
    partner_platform: str,
    cdna_side: str,
    partner_side: str,
    generated_at: datetime,
    max_quote_age_seconds: float,
    operator_risk_mode: str,
) -> dict[str, Any]:
    display = cdna.get("display_price") if cdna_side == "YES" else cdna.get("display_no_price")
    partner_leg = _leg(partner, partner_side)
    partner_ask = partner_leg["ask"]
    all_in = round(display + DEFAULT_CDNA_FEE_PER_CONTRACT, 8) if display is not None else None
    gross = round(1.0 - all_in - partner_ask, 8) if all_in is not None and partner_ask is not None else None
    blockers = [B_CDNA_DISPLAY, B_CDNA_SIZE, B_CDNA_DEPTH, B_CDNA_SERVER, B_BASIS_REVIEW]
    if display is None or partner_ask is None:
        blockers.append(B_MISSING_QUOTE)
    if partner_leg.get("notional") is None:
        blockers.append(B_MISSING_DEPTH)
    if _stale(cdna.get("quote_timestamp"), generated_at, max_quote_age_seconds) or _stale(partner.get("quote_timestamp"), generated_at, max_quote_age_seconds):
        blockers.append(B_STALE_QUOTE)
    blockers = sorted(set(blockers))
    action = ACTION_WATCH
    row = {
        "asset": cdna.get("asset"),
        "threshold": cdna.get("threshold"),
        "target_date": cdna.get("target_date"),
        "target_time_cdna": cdna.get("target_time"),
        f"target_time_{partner_platform}": partner.get("target_time"),
        "direction": f"CDNA_{cdna_side}_{partner_platform.upper()}_{partner_side}",
        "cdna_leg": {
            "side": cdna_side,
            "contract_id": cdna.get("contract_id"),
            "symbol": cdna.get("symbol"),
            "display_price": display,
            "fee_per_contract": DEFAULT_CDNA_FEE_PER_CONTRACT,
            "all_in_cost_per_contract": all_in,
            "quote_timestamp": cdna.get("quote_timestamp"),
            "depth_status": "display_price_only",
            "executable_size_proven": False,
        },
        "partner_leg": {
            "platform": partner_platform,
            "side": partner_side,
            "ask": partner_ask,
            "ask_size": partner_leg.get("size"),
            "available_notional": partner_leg.get("notional"),
            "quote_timestamp": partner.get("quote_timestamp"),
            "depth_status": partner.get("depth_status"),
        },
        "kalshi_ask": partner_ask if partner_platform == "kalshi" else None,
        "polymarket_ask": partner_ask if partner_platform == "polymarket" else None,
        "gross_edge": gross,
        "net_edge": None,
        "net_edge_status": "CDNA_DISPLAY_PRICE_ONLY",
        "available_notional": partner_leg.get("notional"),
        "blockers": blockers,
        "action": action,
        "strict_exact_arb": False,
        "basis_risk_only": True,
        "exact_ready": False,
        "paper_candidate": False,
        "standard_paper_candidate": False,
        "diagnostic_only": True,
    }
    make_candidate = (
        operator_risk_mode == "aggressive"
        and gross is not None
        and gross > 0
        and not has_hard_blocker(blockers, ignore_cdna_info=True, accepted_basis=True)
    )
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


def _unmatched_rows(kalshi_rows: list[dict[str, Any]], poly_rows: list[dict[str, Any]], *, scope_blockers: list[str]) -> list[dict[str, Any]]:
    kalshi_keys = {(row.get("target_date"), row.get("threshold")) for row in kalshi_rows}
    poly_keys = {(row.get("target_date"), row.get("threshold")) for row in poly_rows}
    rows: list[dict[str, Any]] = []
    for source, source_rows, other_keys in (("kalshi", kalshi_rows, poly_keys), ("polymarket", poly_rows, kalshi_keys)):
        for item in source_rows:
            key = (item.get("target_date"), item.get("threshold"))
            if key in other_keys:
                continue
            blockers = sorted(set(scope_blockers + [B_GRID_MISMATCH]))
            rows.append(
                ensure_candidate_fields({
                    "asset": item.get("asset"),
                    "threshold": item.get("threshold"),
                    "target_date": item.get("target_date"),
                    "direction": "UNMATCHED",
                    "source_platform": source,
                    "blockers": blockers,
                    "action": ACTION_IGNORE_BLOCKED,
                    "strict_exact_arb": False,
                    "basis_risk_only": True,
                    "exact_ready": False,
                    "paper_candidate": False,
                    "standard_paper_candidate": False,
                    "diagnostic_only": True,
                })
            )
    return rows


def _extract_rows(payload: dict[str, Any], *, platform: str, expected_asset: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    outcomes = payload.get("outcomes")
    if not isinstance(outcomes, list):
        return rows
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        threshold = _threshold(outcome)
        date = _target_date(outcome, payload)
        if threshold is None or date is None:
            continue
        rows.append(
            {
                "platform": platform,
                "asset": expected_asset,
                "threshold": threshold,
                "target_date": date,
                "target_time": _target_time(outcome, payload, platform),
                "timezone": _string_or_none(payload.get("timezone")),
                "comparator": _normalize_comparator(payload.get("comparator")),
                "settlement_source": _string_or_none(payload.get("settlement_source")),
                "price_source": _string_or_none(payload.get("price_source")),
                "price_source_key": _price_source_key(payload.get("price_source")),
                "platform_market_id": _string_or_none(outcome.get("platform_market_id")),
                "market_ticker": _string_or_none(outcome.get("market_ticker")),
                "condition_id": _string_or_none(outcome.get("condition_id")),
                "token_id_yes": _string_or_none(outcome.get("token_id_yes")),
                "token_id_no": _string_or_none(outcome.get("token_id_no")),
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
            }
        )
    return rows


def _extract_cdna_rows(payload: dict[str, Any] | None, *, expected_asset: str) -> list[dict[str, Any]]:
    if not payload or str(payload.get("platform") or "").lower().find("cdna") < 0 and "crypto.com" not in str(payload.get("platform") or "").lower():
        return []
    if str(payload.get("category") or "").lower() != "crypto" or str(payload.get("asset") or "").upper() != expected_asset:
        return []
    if payload.get("market_found") is False:
        return []
    rows: list[dict[str, Any]] = []
    for outcome in payload.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        threshold = _threshold(outcome)
        date = _target_date(outcome, payload)
        if threshold is None or date is None:
            continue
        rows.append(
            {
                "platform": "cdna",
                "asset": expected_asset,
                "threshold": threshold,
                "target_date": date,
                "target_time": _string_or_none(payload.get("target_time")),
                "timezone": _string_or_none(payload.get("timezone")),
                "contract_id": _string_or_none(outcome.get("contract_id")),
                "symbol": _string_or_none(outcome.get("symbol")),
                "display_price": _float_or_none(outcome.get("display_price")),
                "display_no_price": _float_or_none(outcome.get("display_no_price")),
                "quote_timestamp": _string_or_none(outcome.get("quote_timestamp")),
                "depth_status": _string_or_none(outcome.get("depth_status")) or "display_price_only",
            }
        )
    return rows


def _leg(row: dict[str, Any], side: str) -> dict[str, Any]:
    prefix = side.lower()
    ask = row.get(f"{prefix}_ask")
    size = row.get(f"{prefix}_ask_size")
    return {
        "ask": ask,
        "size": size,
        "notional": round(ask * size, 8) if ask is not None and size is not None else None,
    }


def _available_notional(kalshi_leg: dict[str, Any], poly_leg: dict[str, Any]) -> float | None:
    if kalshi_leg.get("notional") is None or poly_leg.get("notional") is None:
        return None
    return round(min(kalshi_leg["notional"], poly_leg["notional"]), 8)


def _fee_estimate(kalshi_ask: float | None, poly_ask: float | None, gross: float | None) -> tuple[float | None, float | None, str]:
    if kalshi_ask is None or poly_ask is None or gross is None:
        return None, None, "NOT_CALCULATED"
    fee = round(KalshiTieredFeeModel().fee_for_leg(kalshi_ask) + PolymarketConservativeFeeModel().fee_for_leg_for_category(poly_ask, category="crypto"), 8)
    return fee, round(gross - fee, 8), "OK"


def _action(blockers: list[str], *, gross_edge: float | None, net_edge: float | None) -> str:
    if B_SCOPE in blockers or B_GRID_MISMATCH in blockers or B_DATE_MISMATCH in blockers:
        return ACTION_IGNORE_BLOCKED
    if B_MISSING_QUOTE in blockers or B_STALE_QUOTE in blockers or B_MISSING_DEPTH in blockers:
        return ACTION_WATCH
    if B_FEE_REVIEW in blockers:
        return ACTION_WATCH
    if gross_edge is not None and gross_edge > 0:
        return ACTION_WATCH
    return ACTION_WATCH


def _scope_blockers(payload: dict[str, Any], expected_platform: str, expected_asset: str) -> list[str]:
    blockers: list[str] = []
    if str(payload.get("platform") or "").lower() != expected_platform.lower():
        blockers.append(B_SCOPE)
    if str(payload.get("category") or "").lower() != "crypto":
        blockers.append(B_SCOPE)
    if str(payload.get("asset") or "").upper() != expected_asset:
        blockers.append(B_SCOPE)
    if str(payload.get("market_shape") or "").lower() != "point_in_time_threshold":
        blockers.append(B_SCOPE)
    if _normalize_comparator(payload.get("comparator")) != "above":
        blockers.append(B_SCOPE)
    return blockers


def _threshold(outcome: dict[str, Any]) -> float | None:
    value = _float_or_none(outcome.get("strike_floor") or outcome.get("threshold"))
    if value is None:
        title = str(outcome.get("market_title") or outcome.get("outcome_name") or "")
        match = re.search(r"\$?\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?|[0-9]+(?:\.\d+)?)\s*(?:k)?", title, re.IGNORECASE)
        if match:
            value = float(match.group(1).replace(",", ""))
            if "k" in title[match.start():match.end() + 1].lower() and value < 1000:
                value *= 1000
    if value is None:
        return None
    if abs((value + 0.01) - round(value + 0.01)) < 1e-6 and str(value).endswith(".99"):
        value = value + 0.01
    return round(value, 2)


def _target_date(outcome: dict[str, Any], payload: dict[str, Any]) -> str | None:
    # Prefer an explicit ISO target_date the upstream collector already resolved.
    # Text/slug parsing is a fragile fallback: e.g. a Polymarket slug ending in
    # ``-2026-12pm-et`` would otherwise be misread as 2026-01-02.
    for explicit in (outcome.get("target_date"), payload.get("target_date")):
        iso = _iso_date(explicit)
        if iso:
            return iso
    text = " ".join(str(value or "") for value in (outcome.get("market_title"), outcome.get("market_ticker"), payload.get("event_ticker"), payload.get("target_date")))
    match = re.search(r"(20\d{2})[-_/ ]?([A-Za-z]{3,9}|\d{1,2})[-_/ ]?(\d{1,2})", text)
    if match and match.group(2).isdigit():
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    match = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(20\d{2})", text)
    if match:
        month = _month_number(match.group(1))
        if month:
            return f"{match.group(3)}-{month:02d}-{int(match.group(2)):02d}"
    match = re.search(r"(\d{2})([A-Z]{3})(\d{2})(\d{2})?", text.upper())
    if match:
        month = _month_number(match.group(2))
        if month:
            return f"20{match.group(1)}-{month:02d}-{int(match.group(3)):02d}"
    return None


def _iso_date(value: Any) -> str | None:
    text = _string_or_none(value)
    if not text:
        return None
    return text if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", text) else None


def _target_time(outcome: dict[str, Any], payload: dict[str, Any], platform: str) -> str | None:
    if platform == "polymarket":
        rules = " ".join(str(payload.get(key) or "") for key in ("rules_text", "settlement_source", "target_time")).lower()
        if "12:00" in rules or "noon" in rules:
            return "12:00 ET"
    ticker = str(outcome.get("market_ticker") or payload.get("event_ticker") or "").upper()
    match = re.search(r"\d{2}[A-Z]{3}\d{2}(\d{2})", ticker)
    if match:
        return f"{int(match.group(1)):02d}:00 ET"
    return _string_or_none(payload.get("target_time"))


def _price_source_key(value: Any) -> str | None:
    text = str(value or "").lower()
    if "brti" in text or "cf benchmarks" in text:
        return "cf_benchmarks_brti"
    if "ethusd_rti" in text or "ethusd" in text:
        return "cf_benchmarks_ethusd_rti"
    if "binance" in text:
        return "binance"
    return text.strip() or None


def _normalize_comparator(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"above", "greater_than", "greater than", "or above", "at_or_above"}:
        return "above"
    return text or None


def _stale(value: Any, generated_at: datetime, max_quote_age_seconds: float) -> bool:
    parsed = _parse_datetime(value)
    if parsed is None:
        return True
    return (generated_at - parsed).total_seconds() > max_quote_age_seconds


def _parse_datetime(value: Any) -> datetime | None:
    text = _string_or_none(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _summary(rows: list[dict[str, Any]], kalshi_rows: list[dict[str, Any]], poly_rows: list[dict[str, Any]], cdna_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    actions = Counter(row.get("action") for row in rows)
    blockers = Counter()
    for row in rows:
        blockers.update(row.get("blockers") or [])
    summary = {
        "rows": len(rows),
        "kalshi_rows_loaded": len(kalshi_rows),
        "polymarket_rows_loaded": len(poly_rows),
        "cdna_rows_loaded": len(cdna_rows or []),
        "matched_threshold_rows": sum(1 for row in rows if row.get("direction") != "UNMATCHED"),
        "basis_risk_review_rows": actions.get(ACTION_BASIS_RISK_REVIEW, 0),
        "cdna_fill_first_review_rows": actions.get(ACTION_CDNA_FILL_FIRST, 0),
        "manual_review_rows": actions.get(ACTION_MANUAL_REVIEW, 0),
        "watch_rows": actions.get(ACTION_WATCH, 0),
        "ignore_blocked_rows": actions.get(ACTION_IGNORE_BLOCKED, 0),
        "positive_gross_rows": sum(1 for row in rows if (_float_or_none(row.get("gross_edge")) or 0) > 0),
        "positive_net_rows": sum(1 for row in rows if (_float_or_none(row.get("net_edge")) or 0) > 0),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(15)],
    }
    summary.update(candidate_counts(rows))
    summary["paper_candidate_rows"] = summary["total_paper_candidate_rows"]
    return summary


def _row_sort_key(row: dict[str, Any]) -> tuple[int, float, float]:
    action_score = {ACTION_PAPER: 6, ACTION_BASIS_RISK_REVIEW: 5, ACTION_CDNA_FILL_FIRST: 4, ACTION_MANUAL_REVIEW: 3, ACTION_WATCH: 2, ACTION_IGNORE_BLOCKED: 1}.get(row.get("action"), 0)
    return (action_score, _float_or_none(row.get("net_edge")) or -999.0, _float_or_none(row.get("gross_edge")) or -999.0)


def _month_number(value: str) -> int | None:
    months = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12}
    return months.get(value.lower())


def _market_keys(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact per-venue market descriptors for downstream matching diagnostics.

    Each entry carries the threshold/date/time actually used for typed-key
    matching plus whether a usable ask exists, so a consumer can explain why a
    venue pair did or did not produce an exact-time row.
    """
    keys: list[dict[str, Any]] = []
    for row in rows:
        has_ask = (
            row.get("yes_ask") is not None
            or row.get("no_ask") is not None
            or row.get("display_price") is not None
            or row.get("display_no_price") is not None
        )
        keys.append(
            {
                "threshold": row.get("threshold"),
                "target_date": row.get("target_date"),
                "target_time": row.get("target_time"),
                "has_ask": bool(has_ask),
            }
        )
    return keys


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


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")
