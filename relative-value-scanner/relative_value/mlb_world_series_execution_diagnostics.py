from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value._numeric import float_or_none
from relative_value.fees import KalshiTieredFeeModel, PolymarketConservativeFeeModel


SCHEMA_VERSION = 1
DEFAULT_MAX_QUOTE_AGE_SECONDS = 1800.0
DISCLAIMER = (
    "Saved-file execution readiness diagnostic only. It does not call live APIs, "
    "does not mutate inputs, and does not authorize execution."
)


def diagnose_mlb_world_series_execution_blockers_files(
    *,
    pairs_path: Path,
    polymarket_enriched_path: Path,
    kalshi_enriched_path: Path,
    json_output_path: Path,
    markdown_output_path: Path,
    evaluator_path: Path | None = None,
    generated_at: datetime | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
) -> dict[str, Any]:
    payload = diagnose_mlb_world_series_execution_blockers(
        pairs_payload=_load_json_object(pairs_path, "pairs"),
        polymarket_payload=_load_json_object(polymarket_enriched_path, "polymarket_enriched"),
        kalshi_payload=_load_json_object(kalshi_enriched_path, "kalshi_enriched"),
        evaluator_payload=_load_json_object(evaluator_path, "evaluator") if evaluator_path is not None else None,
        inputs={
            "pairs": str(pairs_path),
            "polymarket_enriched": str(polymarket_enriched_path),
            "kalshi_enriched": str(kalshi_enriched_path),
            "evaluator": str(evaluator_path) if evaluator_path is not None else None,
        },
        generated_at=generated_at,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output_path.write_text(render_mlb_world_series_execution_blockers_markdown(payload), encoding="utf-8")
    return payload


def diagnose_mlb_world_series_evaluator_blockers_files(
    *,
    evaluator_path: Path,
    pairs_path: Path,
    polymarket_enriched_path: Path,
    kalshi_enriched_path: Path,
    json_output_path: Path,
    markdown_output_path: Path,
) -> dict[str, Any]:
    payload = diagnose_mlb_world_series_evaluator_blockers(
        evaluator_payload=_load_json_object(evaluator_path, "evaluator"),
        pairs_payload=_load_json_object(pairs_path, "pairs"),
        polymarket_payload=_load_json_object(polymarket_enriched_path, "polymarket_enriched"),
        kalshi_payload=_load_json_object(kalshi_enriched_path, "kalshi_enriched"),
        inputs={
            "evaluator": str(evaluator_path),
            "pairs": str(pairs_path),
            "polymarket_enriched": str(polymarket_enriched_path),
            "kalshi_enriched": str(kalshi_enriched_path),
        },
    )
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output_path.write_text(render_mlb_world_series_evaluator_blockers_markdown(payload), encoding="utf-8")
    return payload


def diagnose_mlb_world_series_evaluator_blockers(
    *,
    evaluator_payload: dict[str, Any],
    pairs_payload: dict[str, Any],
    polymarket_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    inputs: dict[str, str] | None = None,
) -> dict[str, Any]:
    _validate_schema_one("evaluator", evaluator_payload)
    _validate_schema_one("pairs", pairs_payload)
    _validate_schema_one("polymarket_enriched", polymarket_payload)
    _validate_schema_one("kalshi_enriched", kalshi_payload)
    ledger = evaluator_payload.get("ledger")
    if not isinstance(ledger, list):
        raise ValueError("evaluator input must contain ledger list")
    pairs_by_identity = _pairs_by_identity(pairs_payload)

    rows = []
    action_counts = Counter()
    missed_counts = Counter()
    blocker_categories = Counter()
    trusted_counts = Counter()
    for entry in ledger:
        if not isinstance(entry, dict):
            continue
        row = _evaluator_diagnostic_row(entry, pairs_by_identity)
        rows.append(row)
        action_counts.update([row["action_label"]])
        missed_counts.update([row["missed_fill_reason"] or "none"])
        blocker_categories.update(row["blocker_categories"])
        trusted_counts.update(["trusted" if row["trusted_same_payoff_board_v1"] else "not_trusted"])

    payload = {
        "schema_version": SCHEMA_VERSION,
        "source": "mlb_world_series_evaluator_blockers_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": inputs or {
            "evaluator": "<in-memory>",
            "pairs": "<in-memory>",
            "polymarket_enriched": "<in-memory>",
            "kalshi_enriched": "<in-memory>",
        },
        "row_count": len(rows),
        "summary": {
            "action_counts": dict(sorted(action_counts.items())),
            "missed_fill_reason_counts": dict(sorted(missed_counts.items())),
            "blocker_category_counts": dict(sorted(blocker_categories.items())),
            "trusted_relationship_counts": dict(sorted(trusted_counts.items())),
            "dominant_blocker": _dominant_from_counter(missed_counts),
            "rows_with_positive_gross_gap": sum(1 for row in rows if _positive(row["gross_gap"])),
            "rows_with_estimated_net_gap": sum(1 for row in rows if row["estimated_net_gap"] is not None),
            "rows_close_to_candidate_after_current_gate": _close_to_candidate_count(rows),
        },
        "rows": rows,
        "safety": {
            "saved_file_only": True,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "thresholds_or_relationship_gates_lowered": False,
        },
        "disclaimer": DISCLAIMER,
    }
    return payload


def render_mlb_world_series_evaluator_blockers_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# MLB World Series Evaluator Blockers",
        "",
        payload["disclaimer"],
        "",
        "## Summary",
        "",
        f"- Row count: `{payload['row_count']}`",
        f"- Actions: `{summary['action_counts']}`",
        f"- Missed fill reasons: `{summary['missed_fill_reason_counts']}`",
        f"- Blocker categories: `{summary['blocker_category_counts']}`",
        f"- Trusted relationships: `{summary['trusted_relationship_counts']}`",
        f"- Dominant blocker: `{summary['dominant_blocker']}`",
        f"- Rows with positive gross gap: `{summary['rows_with_positive_gross_gap']}`",
        f"- Rows with estimated net gap: `{summary['rows_with_estimated_net_gap']}`",
        f"- Rows close after current gate: `{summary['rows_close_to_candidate_after_current_gate']}`",
        "",
        "## Rows",
        "",
        "| Team/Ticker | Action | Primary Blocker | PM Bid/Ask/Depth | Kalshi Bid/Ask/Depth | Gross | Fees | Net | Trusted |",
        "|---|---|---|---|---|---:|---:|---:|---|",
    ]
    for row in payload["rows"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["team_or_ticker"]),
                    _md(row["action_label"]),
                    _md(row["missed_fill_reason"]),
                    _md(_quote_summary(row["polymarket"])),
                    _md(_quote_summary(row["kalshi"])),
                    _md(row["gross_gap"]),
                    _md(row["estimated_total_fees"]),
                    _md(row["estimated_net_gap"]),
                    _md(row["trusted_same_payoff_board_v1"]),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def diagnose_mlb_world_series_execution_blockers(
    *,
    pairs_payload: dict[str, Any],
    polymarket_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    evaluator_payload: dict[str, Any] | None = None,
    inputs: dict[str, str] | None = None,
    generated_at: datetime | None = None,
    max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
) -> dict[str, Any]:
    now = generated_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("generated_at must include timezone information")
    _validate_schema_one("pairs", pairs_payload)
    _validate_schema_one("polymarket_enriched", polymarket_payload)
    _validate_schema_one("kalshi_enriched", kalshi_payload)
    if evaluator_payload is not None:
        _validate_schema_one("evaluator", evaluator_payload)
    pairs = pairs_payload.get("pairs")
    if not isinstance(pairs, list):
        raise ValueError("pairs input must contain pairs list")

    polymarket_by_id = {
        str(row.get("market_id")): row
        for row in _market_rows(polymarket_payload, "polymarket_enriched")
        if row.get("market_id")
    }
    kalshi_by_ticker = {
        str(row.get("ticker") or row.get("market_id")): row
        for row in _market_rows(kalshi_payload, "kalshi_enriched")
        if row.get("ticker") or row.get("market_id")
    }
    evaluator_by_identity = _evaluator_rows_by_identity(evaluator_payload)

    rows: list[dict[str, Any]] = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        poly_id = _pair_polymarket_id(pair)
        kalshi_ticker = _pair_kalshi_ticker(pair)
        poly = polymarket_by_id.get(poly_id)
        kalshi = kalshi_by_ticker.get(kalshi_ticker)
        rows.append(_diagnostic_row(pair, poly, kalshi, evaluator_by_identity.get((poly_id, kalshi_ticker)), now, max_quote_age_seconds))

    quote_blockers = Counter()
    orderbook_status_blockers = Counter()
    enrichment_warning_counts = Counter()
    missing_fields = Counter()
    no_liquidity = Counter()
    evaluator_reasons = Counter()
    evaluator_actions = Counter()
    evaluator_ineligibility = Counter()
    for row in rows:
        quote_blockers.update(row["quote_blockers"])
        orderbook_status_blockers.update(row["orderbook_status_blockers"])
        enrichment_warning_counts.update(row["enrichment_warnings"])
        missing_fields.update(row["missing_fields"])
        no_liquidity.update(row["no_liquidity_fields"])
        if row.get("evaluator_action"):
            evaluator_actions.update([row["evaluator_action"]])
        if row.get("missed_fill_reason"):
            evaluator_reasons.update([row["missed_fill_reason"]])
        evaluator_ineligibility.update(row.get("evaluator_ineligibility_reasons") or [])

    payload = {
        "schema_version": SCHEMA_VERSION,
        "source": "mlb_world_series_execution_blockers_v1",
        "generated_at": now.isoformat(),
        "inputs": inputs or {
            "pairs": "<in-memory>",
            "polymarket_enriched": "<in-memory>",
            "kalshi_enriched": "<in-memory>",
        },
        "max_quote_age_seconds": max_quote_age_seconds,
        "pair_count": len(rows),
        "snapshot_provenance": {
            "polymarket": _snapshot_provenance(polymarket_payload, now),
            "kalshi": _snapshot_provenance(kalshi_payload, now),
        },
        "fee_model_diagnosis": _fee_model_diagnosis(polymarket_payload, kalshi_payload),
        "enrichment_diagnosis": {
            "polymarket": _enrichment_diagnosis(polymarket_payload),
            "kalshi": _enrichment_diagnosis(kalshi_payload),
            "explanation": "enriched=0 means fresh full orderbook enrichment did not succeed. Top-level bid/ask may still exist from the market snapshot, but evaluator requires fresh orderbook_enrichment with depth and freshness metadata.",
        },
        "summary": {
            "evaluator_actions": dict(sorted(evaluator_actions.items())),
            "missed_fill_reasons": dict(sorted(evaluator_reasons.items())),
            "evaluator_ineligibility_reasons": dict(sorted(evaluator_ineligibility.items())),
            "orderbook_status_blockers": dict(sorted(orderbook_status_blockers.items())),
            "enrichment_warnings": dict(sorted(enrichment_warning_counts.items())),
            "stale_quote_blockers": dict(sorted(quote_blockers.items())),
            "missing_fields": dict(sorted(missing_fields.items())),
            "no_liquidity_fields": dict(sorted(no_liquidity.items())),
            "rows_with_all_bid_ask": sum(1 for row in rows if row["bid_ask_complete"]),
            "rows_with_all_depth": sum(1 for row in rows if row["depth_complete"]),
            "dominant_blocker": _dominant_blocker(orderbook_status_blockers, quote_blockers, missing_fields, no_liquidity, len(rows)),
        },
        "rows": rows,
        "recommended_fresh_readonly_commands": _recommended_commands(),
        "safety": {
            "saved_file_only": True,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "thresholds_or_relationship_gates_lowered": False,
        },
        "disclaimer": DISCLAIMER,
    }
    return payload


def render_mlb_world_series_execution_blockers_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    fee = payload["fee_model_diagnosis"]
    lines = [
        "# MLB World Series Execution Blocker Diagnostic",
        "",
        payload["disclaimer"],
        "",
        "## Summary",
        "",
        f"- Pair count: `{payload['pair_count']}`",
        f"- Rows with complete bid/ask: `{summary['rows_with_all_bid_ask']}`",
        f"- Rows with complete depth: `{summary['rows_with_all_depth']}`",
        f"- Evaluator actions: `{summary.get('evaluator_actions')}`",
        f"- Missed fill reasons: `{summary.get('missed_fill_reasons')}`",
        f"- Evaluator ineligibility reasons: `{summary.get('evaluator_ineligibility_reasons')}`",
        f"- Orderbook status blockers: `{summary.get('orderbook_status_blockers')}`",
        f"- Enrichment warnings: `{summary.get('enrichment_warnings')}`",
        f"- Stale quote blockers: `{summary['stale_quote_blockers']}`",
        f"- Missing fields: `{summary['missing_fields']}`",
        f"- No-liquidity fields: `{summary['no_liquidity_fields']}`",
        f"- Dominant blocker: `{summary.get('dominant_blocker')}`",
        "",
        "## Enrichment Wording",
        "",
        payload["enrichment_diagnosis"]["explanation"],
        "",
        f"- Polymarket: `{payload['enrichment_diagnosis']['polymarket']}`",
        f"- Kalshi: `{payload['enrichment_diagnosis']['kalshi']}`",
        "",
        "## Provenance",
        "",
    ]
    for venue, row in payload["snapshot_provenance"].items():
        lines.append(
            f"- {venue}: source=`{row.get('source')}` captured_at=`{row.get('captured_at')}` "
            f"snapshot_age_seconds=`{row.get('snapshot_age_seconds')}` normalized_count=`{row.get('normalized_count')}`"
        )
    lines.extend(
        [
            "",
            "## Fee Models",
            "",
            f"- Kalshi row fee fields present: `{fee['kalshi_row_fee_fields_present_count']}`",
            f"- Kalshi conservative fee model available: `{fee['kalshi_conservative_fee_model_available']}`",
            f"- Polymarket row fee fields present: `{fee['polymarket_row_fee_fields_present_count']}`",
            f"- Polymarket conservative fee model available: `{fee['polymarket_conservative_fee_model_available']}`",
            "",
            "## Rows With Missing Bid/Depth",
            "",
        ]
    )
    missing_rows = [
        row
        for row in payload["rows"]
        if row["missing_fields"] or row["no_liquidity_fields"] or row["join_blockers"]
    ]
    if not missing_rows:
        lines.append("none")
    else:
        lines.append("| Polymarket | Kalshi | Evaluator | Missing | No Liquidity | Diagnosis |")
        lines.append("|---|---|---|---|---|---|")
        for row in missing_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(row["polymarket_id"]),
                        _md(row["kalshi_ticker"]),
                        _md(row.get("missed_fill_reason") or "none"),
                        _md(", ".join(row["missing_fields"]) or "none"),
                        _md(", ".join(row["no_liquidity_fields"]) or "none"),
                        _md(row["diagnosis"]),
                    ]
                )
                + " |"
            )
    lines.extend(["", "## Fresh Read-Only Rerun Commands", ""])
    for command in payload["recommended_fresh_readonly_commands"]:
        lines.extend(["```powershell", command, "```", ""])
    return "\n".join(lines)


def _diagnostic_row(
    pair: dict[str, Any],
    polymarket: dict[str, Any] | None,
    kalshi: dict[str, Any] | None,
    evaluator_row: dict[str, Any] | None,
    generated_at: datetime,
    max_quote_age_seconds: float,
) -> dict[str, Any]:
    poly_id = _pair_polymarket_id(pair)
    kalshi_ticker = _pair_kalshi_ticker(pair)
    join_blockers: list[str] = []
    if polymarket is None:
        join_blockers.append("missing_polymarket_enriched_market")
    if kalshi is None:
        join_blockers.append("missing_kalshi_enriched_market")
    poly_enrichment = _enrichment(polymarket or {})
    kalshi_enrichment = _enrichment(kalshi or {})
    quote_blockers: list[str] = []
    orderbook_status_blockers: list[str] = []
    enrichment_warnings: list[str] = []
    missing_fields: list[str] = []
    no_liquidity_fields: list[str] = []
    for venue, enrichment, market in (
        ("polymarket", poly_enrichment, polymarket or {}),
        ("kalshi", kalshi_enrichment, kalshi or {}),
    ):
        if enrichment.get("enrichment_status") != "enriched":
            orderbook_status_blockers.append(f"{venue}_orderbook_not_enriched")
            warnings = enrichment.get("enrichment_warnings") if isinstance(enrichment.get("enrichment_warnings"), list) else []
            enrichment_warnings.extend(f"{venue}_{warning}" for warning in warnings if warning)
        quote_age = _quote_age_seconds(enrichment.get("orderbook_captured_at"), generated_at)
        if quote_age is None:
            missing_fields.append(f"{venue}_orderbook_captured_at")
        elif quote_age > max_quote_age_seconds:
            quote_blockers.append(f"{venue}_stale_quote")
        for field in ("best_bid", "best_ask", "depth_at_best_bid", "depth_at_best_ask"):
            if enrichment.get(field) is None:
                field_name = f"{venue}_{field}"
                if _top_level_zero_liquidity(market, field):
                    no_liquidity_fields.append(field_name)
                else:
                    missing_fields.append(field_name)
    diagnosis = "ready_from_saved_fields"
    if orderbook_status_blockers:
        diagnosis = "fresh_full_orderbook_not_enriched"
    if quote_blockers:
        diagnosis = "stale_saved_orderbook_snapshot"
    if no_liquidity_fields:
        diagnosis = "visible_no_bid_or_depth_liquidity"
    if missing_fields or join_blockers:
        diagnosis = "missing_saved_enrichment_field"
    return {
        "polymarket_id": poly_id,
        "kalshi_ticker": kalshi_ticker,
        "evaluator_action": evaluator_row.get("action") if isinstance(evaluator_row, dict) else None,
        "missed_fill_reason": evaluator_row.get("missed_fill_reason") if isinstance(evaluator_row, dict) else None,
        "evaluator_ineligibility_reasons": evaluator_row.get("ineligibility_reasons") if isinstance(evaluator_row, dict) else [],
        "trusted_same_payoff": _trusted_same_payoff(pair),
        "join_blockers": join_blockers,
        "orderbook_enrichment_status": {
            "polymarket": poly_enrichment.get("enrichment_status"),
            "kalshi": kalshi_enrichment.get("enrichment_status"),
        },
        "quote_age_seconds": {
            "polymarket": _quote_age_seconds(poly_enrichment.get("orderbook_captured_at"), generated_at),
            "kalshi": _quote_age_seconds(kalshi_enrichment.get("orderbook_captured_at"), generated_at),
        },
        "top_level_quote": {
            "polymarket": _top_level_quote(polymarket or {}),
            "kalshi": _top_level_quote(kalshi or {}),
        },
        "orderbook_enrichment_quote": {
            "polymarket": _enrichment_quote(poly_enrichment),
            "kalshi": _enrichment_quote(kalshi_enrichment),
        },
        "quote_blockers": sorted(quote_blockers),
        "orderbook_status_blockers": sorted(orderbook_status_blockers),
        "enrichment_warnings": sorted(enrichment_warnings),
        "missing_fields": sorted(missing_fields),
        "no_liquidity_fields": sorted(no_liquidity_fields),
        "bid_ask_complete": not any(field.endswith(("best_bid", "best_ask")) for field in missing_fields + no_liquidity_fields),
        "depth_complete": not any(field.endswith(("depth_at_best_bid", "depth_at_best_ask")) for field in missing_fields + no_liquidity_fields),
        "diagnosis": diagnosis,
        "kalshi_top_level_best_bid": (kalshi or {}).get("best_bid"),
        "kalshi_top_level_best_ask": (kalshi or {}).get("best_ask"),
    }


def _evaluator_diagnostic_row(entry: dict[str, Any], pairs_by_identity: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    candidate_id = str(entry.get("candidate_id") or "")
    poly_id, kalshi_ticker = _split_candidate_id(candidate_id)
    pair = pairs_by_identity.get((poly_id, kalshi_ticker), {})
    gap = entry.get("gap") if isinstance(entry.get("gap"), dict) else {}
    polymarket = entry.get("polymarket") if isinstance(entry.get("polymarket"), dict) else {}
    kalshi = entry.get("kalshi") if isinstance(entry.get("kalshi"), dict) else {}
    missed = str(entry.get("missed_fill_reason") or "")
    ineligibility = entry.get("ineligibility_reasons") if isinstance(entry.get("ineligibility_reasons"), list) else []
    total_fees = _sum_optional(gap.get("polymarket_fee"), gap.get("kalshi_fee"))
    return {
        "candidate_id": candidate_id,
        "team_or_ticker": kalshi_ticker or _question_team(kalshi.get("question")),
        "action": _safe_action_label(entry.get("action")),
        "action_label": _safe_action_label(entry.get("action")),
        "missed_fill_reason": missed,
        "blocker_categories": _blocker_categories(missed, ineligibility),
        "ineligibility_reasons": sorted(str(reason) for reason in ineligibility if reason is not None),
        "trusted_same_payoff_board_v1": _trusted_same_payoff(pair),
        "polymarket": _evaluator_venue_quote(polymarket),
        "kalshi": _evaluator_venue_quote(kalshi),
        "gross_gap": gap.get("gross_gap"),
        "polymarket_fee": gap.get("polymarket_fee"),
        "kalshi_fee": gap.get("kalshi_fee"),
        "estimated_total_fees": total_fees,
        "estimated_net_gap": gap.get("estimated_net_gap"),
        "settlement_delta_seconds": gap.get("settlement_delta_seconds"),
        "unit_warning": gap.get("size_unit_warning"),
        "relationship_after_evaluator": entry.get("contract_relationship") if isinstance(entry.get("contract_relationship"), dict) else None,
    }


def _evaluator_venue_quote(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("market_id") or row.get("ticker"),
        "question": row.get("question"),
        "quote_captured_at": row.get("quote_captured_at"),
        "best_bid": row.get("best_bid"),
        "best_ask": row.get("best_ask"),
        "depth_at_best_bid": row.get("depth_at_best_bid"),
        "depth_at_best_ask": row.get("depth_at_best_ask"),
        "would_enter_side": row.get("would_enter_side"),
        "would_enter_price": row.get("would_enter_price"),
        "would_enter_size": row.get("would_enter_size"),
    }


def _blocker_categories(missed_fill_reason: str, ineligibility_reasons: list[Any]) -> list[str]:
    reasons = {missed_fill_reason, *(str(reason) for reason in ineligibility_reasons if reason is not None)}
    categories: set[str] = set()
    if any("missing_best_bid_or_ask" in reason or "best_bid" in reason or "best_ask" in reason for reason in reasons):
        categories.add("missing_bid_or_ask")
    if any("depth" in reason for reason in reasons):
        categories.add("missing_or_insufficient_depth")
    if any("stale_quote" in reason or "quote_time" in reason for reason in reasons):
        categories.add("stale_quote")
    if any("settlement_delta" in reason for reason in reasons):
        categories.add("settlement_delta")
    if any("unit_mismatch" in reason for reason in reasons):
        categories.add("unit_mismatch")
    if any("fee" in reason for reason in reasons):
        categories.add("fee_or_model")
    if any("no_positive_bid_ask_gap" in reason for reason in reasons):
        categories.add("no_positive_bid_ask_gap")
    if any("estimated_net_gap_below_minimum" in reason for reason in reasons):
        categories.add("estimated_net_gap_below_minimum")
    if not categories:
        categories.add("unknown_or_other")
    return sorted(categories)


def _pairs_by_identity(payload: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    pairs = payload.get("pairs")
    if not isinstance(pairs, list):
        raise ValueError("pairs input must contain pairs list")
    rows = {}
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        rows[(_pair_polymarket_id(pair), _pair_kalshi_ticker(pair))] = pair
    return rows


def _split_candidate_id(candidate_id: str) -> tuple[str, str]:
    if "__" not in candidate_id:
        return candidate_id, ""
    left, right = candidate_id.split("__", 1)
    return left, right


def _sum_optional(left: Any, right: Any) -> float | None:
    left_num = float_or_none(left)
    right_num = float_or_none(right)
    if left_num is None or right_num is None:
        return None
    return round(left_num + right_num, 6)


def _dominant_from_counter(counter: Counter) -> str | None:
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def _close_to_candidate_count(rows: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if row.get("trusted_same_payoff_board_v1") is True
        and row.get("action_label") == "watch"
        and row.get("missed_fill_reason") in {"settlement_delta_exceeds_limit", "estimated_net_gap_below_minimum"}
        and row.get("gross_gap") is not None
    )


def _positive(value: Any) -> bool:
    number = float_or_none(value)
    return number is not None and number > 0


def _question_team(question: Any) -> str:
    if not question:
        return ""
    return str(question).replace("Will ", "").replace(" win the 2026 Pro Baseball Championship?", "")


def _quote_summary(row: dict[str, Any]) -> str:
    return (
        f"{row.get('best_bid')}/{row.get('best_ask')} "
        f"depth {row.get('depth_at_best_bid')}/{row.get('depth_at_best_ask')} "
        f"fresh {row.get('quote_captured_at')}"
    )


def _safe_action_label(action: Any) -> str:
    return str(action or "").strip().lower()


def _fee_model_diagnosis(polymarket_payload: dict[str, Any], kalshi_payload: dict[str, Any]) -> dict[str, Any]:
    polymarket_rows = _market_rows(polymarket_payload, "polymarket_enriched")
    kalshi_rows = _market_rows(kalshi_payload, "kalshi_enriched")
    return {
        "kalshi_row_fee_fields_present_count": sum(1 for row in kalshi_rows if _row_fee_available(row)),
        "polymarket_row_fee_fields_present_count": sum(1 for row in polymarket_rows if _row_fee_available(row)),
        "kalshi_conservative_fee_model_available": isinstance(KalshiTieredFeeModel(), KalshiTieredFeeModel),
        "kalshi_conservative_fee_model": "KalshiTieredFeeModel",
        "polymarket_conservative_fee_model_available": isinstance(PolymarketConservativeFeeModel(), PolymarketConservativeFeeModel),
        "polymarket_conservative_fee_model": "PolymarketConservativeFeeModel",
        "diagnosis": "Evaluator can use reviewed conservative fee models even when saved rows lack explicit fee fields.",
    }


def _enrichment_diagnosis(payload: dict[str, Any]) -> dict[str, Any]:
    rows = _market_rows(payload, "enriched")
    summary = payload.get("orderbook_enrichment") if isinstance(payload.get("orderbook_enrichment"), dict) else {}
    warnings = summary.get("snapshot_warnings") if isinstance(summary.get("snapshot_warnings"), list) else []
    top_level_present = sum(1 for row in rows if row.get("best_bid") is not None or row.get("best_ask") is not None)
    enriched = int(summary.get("enriched_count") or 0)
    if enriched == 0 and "stale_snapshot" in warnings and top_level_present:
        diagnosis = "fresh_fetch_not_attempted_stale_snapshot_existing_top_of_book_present"
    elif enriched == 0 and top_level_present:
        diagnosis = "fresh_fetch_failed_or_unavailable_existing_top_of_book_present"
    elif enriched == 0:
        diagnosis = "fresh_full_orderbook_missing"
    else:
        diagnosis = "fresh_full_orderbook_enriched"
    return {
        "diagnosis": diagnosis,
        "fresh_orderbook_fetch_enriched": int(summary.get("fresh_orderbook_fetch_enriched_count") or summary.get("enriched_count") or 0),
        "existing_top_of_book_present": int(summary.get("existing_top_of_book_present_count") or top_level_present),
        "full_orderbook_missing": int(summary.get("full_orderbook_missing_count") or summary.get("unenriched_count") or 0),
        "fetch_failed": int(summary.get("fetch_failed_count") or 0),
        "stale_existing_top_of_book": int(summary.get("stale_existing_top_of_book_count") or (top_level_present if "stale_snapshot" in warnings else 0)),
        "snapshot_warnings": warnings,
    }


def _dominant_blocker(orderbook_status_blockers: Counter, quote_blockers: Counter, missing_fields: Counter, no_liquidity: Counter, pair_count: int) -> str | None:
    if pair_count and sum(orderbook_status_blockers.values()) >= pair_count:
        return "orderbook_not_enriched"
    if pair_count and sum(quote_blockers.values()) >= pair_count:
        return "stale_quote"
    if missing_fields:
        return "missing_fields"
    if no_liquidity:
        return "no_liquidity"
    return None


def _evaluator_rows_by_identity(payload: dict[str, Any] | None) -> dict[tuple[str, str], dict[str, Any]]:
    if payload is None:
        return {}
    ledger = payload.get("ledger")
    if not isinstance(ledger, list):
        return {}
    rows = {}
    for row in ledger:
        if not isinstance(row, dict):
            continue
        candidate_id = str(row.get("candidate_id") or "")
        if "__" not in candidate_id:
            continue
        poly_id, kalshi_ticker = candidate_id.split("__", 1)
        rows[(poly_id, kalshi_ticker)] = row
    return rows


def _top_level_quote(market: dict[str, Any]) -> dict[str, Any]:
    return {
        "best_bid": market.get("best_bid"),
        "best_ask": market.get("best_ask"),
    }


def _enrichment_quote(enrichment: dict[str, Any]) -> dict[str, Any]:
    return {
        "best_bid": enrichment.get("best_bid"),
        "best_ask": enrichment.get("best_ask"),
        "depth_at_best_bid": enrichment.get("depth_at_best_bid"),
        "depth_at_best_ask": enrichment.get("depth_at_best_ask"),
        "orderbook_captured_at": enrichment.get("orderbook_captured_at"),
    }


def _recommended_commands() -> list[str]:
    return [
        "python scan.py fetch-live-overlap-universe --category sports --query MLB --max-markets 1000 --kalshi-max-pages 20 --output-dir reports/live_readonly --report-dir reports/live_readonly --label mlb",
        "python scan.py enrich-orderbooks --snapshot reports/live_readonly/polymarket_live_readonly_snapshot.json --venue polymarket --output reports/mlb_fresh_polymarket_enriched.json",
        "python scan.py enrich-orderbooks --snapshot reports/live_readonly/kalshi_live_readonly_snapshot.json --venue kalshi --output reports/mlb_fresh_kalshi_enriched.json",
        "python scan.py build-mlb-world-series-pairs --polymarket-snapshot reports/mlb_fresh_polymarket_enriched.json --kalshi-snapshot reports/mlb_fresh_kalshi_enriched.json --json-output reports/mlb_world_series_pairs_fresh.json --markdown-output reports/mlb_world_series_pairs_fresh.md",
        "python scan.py same-payoff-board --pairs reports/mlb_world_series_pairs_fresh.json --polymarket-enriched reports/mlb_fresh_polymarket_enriched.json --kalshi-enriched reports/mlb_fresh_kalshi_enriched.json --json-output reports/mlb_world_series_same_payoff_board_fresh.json --markdown-output reports/mlb_world_series_same_payoff_board_fresh.md",
        "python scan.py attach-same-payoff-evidence --pairs reports/mlb_world_series_pairs_fresh.json --board reports/mlb_world_series_same_payoff_board_fresh.json --output reports/mlb_world_series_pairs_with_evidence_fresh.json",
        "python scan.py evaluate-paper-candidates --pairs reports/mlb_world_series_pairs_with_evidence_fresh.json --polymarket-enriched reports/mlb_fresh_polymarket_enriched.json --kalshi-enriched reports/mlb_fresh_kalshi_enriched.json --output reports/mlb_world_series_evaluator_fresh.json --accept-unit-mismatch",
    ]


def _snapshot_provenance(payload: dict[str, Any], generated_at: datetime) -> dict[str, Any]:
    captured_at = payload.get("captured_at")
    captured = _parse_datetime_or_none(captured_at)
    return {
        "source": payload.get("source"),
        "captured_at": captured_at,
        "snapshot_age_seconds": (generated_at - captured).total_seconds() if captured is not None else None,
        "normalized_count": payload.get("normalized_count"),
    }


def _row_fee_available(market: dict[str, Any]) -> bool:
    raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
    enrichment = _enrichment(market)
    return bool(
        market.get("fee_model")
        or market.get("fee_rate") is not None
        or market.get("fees")
        or market.get("fee_cents") is not None
        or raw.get("fee_model")
        or raw.get("fee_rate") is not None
        or raw.get("feeSchedule")
        or raw.get("fee_schedule")
        or raw.get("feeType")
        or raw.get("fee_type")
        or enrichment.get("fee_model")
        or enrichment.get("fee_rate") is not None
    )


def _top_level_zero_liquidity(market: dict[str, Any], enrichment_field: str) -> bool:
    if enrichment_field == "best_bid":
        return float_or_none(market.get("best_bid")) == 0.0
    if enrichment_field == "depth_at_best_bid":
        return float_or_none(market.get("best_bid")) == 0.0
    if enrichment_field == "best_ask":
        return float_or_none(market.get("best_ask")) == 0.0
    if enrichment_field == "depth_at_best_ask":
        return float_or_none(market.get("best_ask")) == 0.0
    return False


def _trusted_same_payoff(pair: dict[str, Any]) -> bool:
    relationship = pair.get("contract_relationship")
    return isinstance(relationship, dict) and relationship.get("same_payoff") is True and not relationship.get("blocking_reasons")


def _pair_polymarket_id(pair: dict[str, Any]) -> str:
    polymarket = pair.get("polymarket") if isinstance(pair.get("polymarket"), dict) else {}
    return str(polymarket.get("market_id") or polymarket.get("condition_id") or "")


def _pair_kalshi_ticker(pair: dict[str, Any]) -> str:
    kalshi = pair.get("kalshi") if isinstance(pair.get("kalshi"), dict) else {}
    return str(kalshi.get("ticker") or kalshi.get("market_ticker") or kalshi.get("market_id") or "")


def _enrichment(market: dict[str, Any]) -> dict[str, Any]:
    enrichment = market.get("orderbook_enrichment")
    return enrichment if isinstance(enrichment, dict) else {}


def _quote_age_seconds(value: Any, generated_at: datetime) -> float | None:
    captured = _parse_datetime_or_none(value)
    if captured is None:
        return None
    return (generated_at - captured).total_seconds()


def _parse_datetime_or_none(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _market_rows(payload: dict[str, Any], label: str) -> list[dict[str, Any]]:
    rows = payload.get("normalized_markets")
    if not isinstance(rows, list):
        raise ValueError(f"{label} input must contain normalized_markets list")
    return [row for row in rows if isinstance(row, dict)]


def _validate_schema_one(label: str, payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{label} schema_version must be 1")


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} JSON is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must be an object")
    return payload


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")
