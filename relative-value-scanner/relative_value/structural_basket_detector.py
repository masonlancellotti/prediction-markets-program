from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.exhaustive_evidence_trust import exhaustive_evidence_trust_blockers, has_reference_only_flag
from relative_value.fees import FeeModel, KalshiTieredFeeModel, PolymarketConservativeFeeModel


STATUS_NOT_EXHAUSTIVE_EVIDENCE = "NOT_EXHAUSTIVE_EVIDENCE"
STATUS_MISSING_ORDERBOOK = "MISSING_ORDERBOOK"
STATUS_STALE_ORDERBOOK = "STALE_ORDERBOOK"
STATUS_INSUFFICIENT_DEPTH = "INSUFFICIENT_DEPTH"
STATUS_FEES_KILL = "FEES_KILL"
STATUS_STRUCTURAL_BASKET_REVIEW = "STRUCTURAL_BASKET_REVIEW"
STATUS_STOP_FOR_REVIEW = "STOP_FOR_REVIEW"

REVIEW_STATUSES = {STATUS_STRUCTURAL_BASKET_REVIEW, STATUS_STOP_FOR_REVIEW}


def build_structural_basket_review_report(
    *,
    snapshot_payloads: list[dict[str, Any]],
    manifest_payload: dict[str, Any] | None = None,
    detected_at: datetime | None = None,
    max_quote_age_seconds: float = 1800.0,
    min_depth: float = 1.0,
    fee_models: dict[str, FeeModel] | None = None,
    graph_hints_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detected = detected_at or datetime.now(timezone.utc)
    models = fee_models or _default_fee_models()
    rows = _market_rows(snapshot_payloads)
    group_specs = _explicit_group_specs(rows, manifest_payload)
    evaluations = [
        _evaluate_group(
            spec=spec,
            rows=rows,
            detected_at=detected,
            max_quote_age_seconds=max_quote_age_seconds,
            min_depth=min_depth,
            fee_models=models,
        )
        for spec in group_specs
    ]
    review_count = sum(1 for row in evaluations if row["status"] in REVIEW_STATUSES)
    stop_count = sum(1 for row in evaluations if row["status"] == STATUS_STOP_FOR_REVIEW)
    status_counts = Counter(row["status"] for row in evaluations)
    return {
        "schema_version": 1,
        "source": "structural_basket_saved_file_diagnostic_v1",
        "generated_at": detected.isoformat(),
        "summary": {
            "explicit_group_count": len(group_specs),
            "evaluated_group_count": len(evaluations),
            "review_count": review_count,
            "stop_for_review_count": stop_count,
            "paper_candidate_count": 0,
            "status_counts": dict(sorted(status_counts.items())),
        },
        "config": {
            "max_quote_age_seconds": max_quote_age_seconds,
            "min_depth": min_depth,
            "fee_models": {venue: model.__class__.__name__ for venue, model in models.items()},
        },
        "rows": evaluations,
        "safety": {
            "saved_file_only": True,
            "diagnostic_only": True,
            "places_orders": False,
            "paper_candidate_emitted": False,
            "paper_candidate_count": 0,
            "affects_evaluator_gates": False,
            "uses_title_similarity_for_exhaustiveness": False,
            "uses_graph_hints_for_exhaustiveness": False,
            "graph_hints_payload_ignored": graph_hints_payload is not None,
            "uses_midpoint": False,
            "uses_ask_side_only": True,
            "requires_explicit_exhaustive_evidence": True,
            "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        },
    }


def build_structural_basket_review_report_files(
    *,
    snapshot_paths: list[Path],
    manifest_path: Path | None = None,
    json_output: Path,
    markdown_output: Path,
    detected_at: datetime | None = None,
    max_quote_age_seconds: float = 1800.0,
    min_depth: float = 1.0,
) -> dict[str, Any]:
    snapshots = [_read_json(path) for path in snapshot_paths]
    manifest = _read_json(manifest_path) if manifest_path else None
    report = build_structural_basket_review_report(
        snapshot_payloads=snapshots,
        manifest_payload=manifest,
        detected_at=detected_at,
        max_quote_age_seconds=max_quote_age_seconds,
        min_depth=min_depth,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_structural_basket_review_markdown(report), encoding="utf-8")
    return report


def render_structural_basket_review_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Structural Basket Review",
        "",
        "Saved-file-only diagnostic. Exhaustiveness requires explicit venue-native metadata or a trusted local manifest.",
        "No orders are placed. Graph hints and title similarity are not trusted exhaustive evidence.",
        "",
        "| Venue | Group | Status | Settlement audit | Outcomes | Sum asks | Fees | Total cost | Min depth | Top blocker |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in report.get("rows", []):
        blockers = row.get("blockers") or []
        lines.append(
            "| {venue} | {group_id} | {status} | {settlement} | {outcomes} | {sum_asks:.4f} | {fees:.4f} | {total:.4f} | {depth:.4f} | {blocker} |".format(
                venue=row.get("venue") or "",
                group_id=str(row.get("group_id") or "").replace("|", "/"),
                status=row.get("status") or "",
                settlement=row.get("settlement_audit_status") or "",
                outcomes=len(row.get("outcomes") or []),
                sum_asks=float(row.get("sum_asks") or 0.0),
                fees=float(row.get("conservative_fees") or 0.0),
                total=float(row.get("total_cost_after_fees") or 0.0),
                depth=float(row.get("min_posted_depth") or 0.0),
                blocker=(blockers[0] if blockers else "").replace("|", "/"),
            )
        )
    if report.get("summary", {}).get("stop_for_review_count", 0) > 0:
        lines.extend(["", "STOP_FOR_REVIEW: at least one strict saved-file structural basket passed review gates."])
    return "\n".join(lines) + "\n"


def _evaluate_group(
    *,
    spec: dict[str, Any],
    rows: list[dict[str, Any]],
    detected_at: datetime,
    max_quote_age_seconds: float,
    min_depth: float,
    fee_models: dict[str, FeeModel],
) -> dict[str, Any]:
    venue = str(spec.get("venue") or "").lower()
    members = _group_members(spec, rows)
    blockers: list[str] = []
    expected_count = _int_or_none(spec.get("expected_outcome_count"))
    expected_ids = {str(value) for value in spec.get("outcome_market_ids") or [] if value is not None}
    if expected_count is not None and len(members) != expected_count:
        blockers.append("explicit_exhaustive_group_incomplete")
    if expected_ids and {str(_market_id(row)) for row in members} != expected_ids:
        blockers.append("explicit_exhaustive_group_member_mismatch")
    if not spec.get("is_exhaustive") or not spec.get("evidence_source"):
        blockers.append("missing_explicit_exhaustive_evidence")
    blockers.extend(
        exhaustive_evidence_trust_blockers(
            source=spec.get("evidence_source"),
            is_exhaustive=spec.get("is_exhaustive") is True,
            venue_native=spec.get("venue_native") is True,
            trusted_local_manifest=spec.get("trusted_local_manifest") is True,
        )
    )
    settlement_audit = _settlement_resolution_audit(members, spec)
    blockers.extend(settlement_audit["settlement_audit_blockers"])

    outcome_rows = []
    sum_asks = 0.0
    total_fees = 0.0
    min_posted_depth: float | None = None
    max_quote_age = 0.0
    model = fee_models.get(venue)
    if model is None:
        blockers.append("missing_fee_model")
    for row in members:
        if has_reference_only_flag(row):
            blockers.append("reference_only_source")
        ob = row.get("orderbook_enrichment")
        if not isinstance(ob, dict):
            blockers.append("missing_orderbook_enrichment")
            outcome_rows.append(_outcome_summary(row, None, None, None, None))
            continue
        ask = _float_or_none(ob.get("best_ask"))
        depth = _float_or_none(ob.get("depth_at_best_ask"))
        captured = _parse_datetime_or_none(ob.get("orderbook_captured_at"))
        age = (detected_at - captured).total_seconds() if captured else None
        if ask is None:
            blockers.append("missing_executable_ask")
        if depth is None:
            blockers.append("missing_ask_depth")
        elif depth < min_depth:
            blockers.append("insufficient_ask_depth")
        if age is None:
            blockers.append("missing_quote_timestamp")
        else:
            max_quote_age = max(max_quote_age, age)
            if age > max_quote_age_seconds:
                blockers.append("stale_orderbook")
        if ask is not None:
            sum_asks += ask
            if model is not None:
                total_fees += model.fee_for_leg(ask)
        if depth is not None:
            min_posted_depth = depth if min_posted_depth is None else min(min_posted_depth, depth)
        outcome_rows.append(_outcome_summary(row, ask, depth, captured, age))

    total_cost = round(sum_asks + total_fees, 6)
    if members and not blockers and total_cost >= 1.0:
        blockers.append("fees_kill_or_no_positive_basket_gap")
    status = _status_from_blockers(blockers)
    if status == STATUS_STRUCTURAL_BASKET_REVIEW:
        status = STATUS_STOP_FOR_REVIEW
    return {
        "venue": venue,
        "group_id": spec.get("group_id"),
        "status": status,
        "blockers": sorted(set(blockers)),
        "evidence": {
            "source": spec.get("evidence_source"),
            "detail": spec.get("evidence_detail"),
            "trusted_local_manifest": spec.get("trusted_local_manifest", False),
            "venue_native": spec.get("venue_native", False),
        },
        "settlement_audit_status": settlement_audit["settlement_audit_status"],
        "settlement_audit_blockers": settlement_audit["settlement_audit_blockers"],
        "resolution_metadata_complete": settlement_audit["resolution_metadata_complete"],
        "normalized_resolution_key": settlement_audit["normalized_resolution_key"],
        "resolution_summary": settlement_audit["per_leg_resolution_summary"],
        "outcomes": outcome_rows,
        "sum_asks": round(sum_asks, 6),
        "conservative_fees": round(total_fees, 6),
        "total_cost_after_fees": total_cost,
        "max_quote_age_seconds": round(max_quote_age, 6),
        "min_posted_depth": min_posted_depth,
        "uses_midpoint": False,
        "uses_ask_side_only": True,
        "paper_candidate_emitted": False,
    }


def _status_from_blockers(blockers: list[str]) -> str:
    blocker_set = set(blockers)
    if not blocker_set:
        return STATUS_STRUCTURAL_BASKET_REVIEW
    if blocker_set & {
        "missing_explicit_exhaustive_evidence",
        "explicit_exhaustive_group_incomplete",
        "explicit_exhaustive_group_member_mismatch",
        "exhaustive_evidence_source_not_trusted",
        "venue_native_exhaustive_evidence_required",
        "trusted_local_manifest_required",
        "reference_only_source",
        "mixed_resolution_timing",
        "mixed_resolution_criteria",
        "mixed_settlement_source",
        "missing_resolution_metadata",
        "mixed_event_group_metadata",
    }:
        return STATUS_NOT_EXHAUSTIVE_EVIDENCE
    if blocker_set & {"missing_orderbook_enrichment", "missing_executable_ask"}:
        return STATUS_MISSING_ORDERBOOK
    if blocker_set & {"stale_orderbook", "missing_quote_timestamp"}:
        return STATUS_STALE_ORDERBOOK
    if blocker_set & {"missing_ask_depth", "insufficient_ask_depth"}:
        return STATUS_INSUFFICIENT_DEPTH
    return STATUS_FEES_KILL


def _explicit_group_specs(rows: list[dict[str, Any]], manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    specs = []
    if manifest:
        for group in manifest.get("exhaustive_groups") or manifest.get("groups") or []:
            if not isinstance(group, dict):
                continue
            specs.append(
                {
                    "venue": group.get("venue"),
                    "group_id": group.get("group_id") or group.get("event_id"),
                    "is_exhaustive": group.get("is_exhaustive") is True or group.get("exhaustive") is True,
                    "evidence_source": group.get("source") or group.get("evidence_source"),
                    "evidence_detail": group.get("evidence") or group.get("evidence_detail"),
                    "trusted_local_manifest": group.get("trusted_local_manifest") is True,
                    "venue_native": False,
                    "outcome_market_ids": group.get("outcome_market_ids") or group.get("market_ids") or [],
                    "expected_outcome_count": group.get("expected_outcome_count"),
                }
            )
    native_seen = set()
    for row in rows:
        evidence = row.get("exhaustive_group")
        if not isinstance(evidence, dict):
            raw = row.get("raw")
            evidence = raw.get("exhaustive_group") if isinstance(raw, dict) else None
        if not isinstance(evidence, dict):
            continue
        venue = row.get("venue")
        group_id = evidence.get("group_id") or evidence.get("event_id") or row.get("event_id")
        key = (venue, group_id)
        if not venue or not group_id or key in native_seen:
            continue
        native_seen.add(key)
        specs.append(
            {
                "venue": venue,
                "group_id": group_id,
                "is_exhaustive": evidence.get("all_outcomes_included") is True or evidence.get("is_exhaustive") is True,
                "evidence_source": evidence.get("source") or evidence.get("evidence_source"),
                "evidence_detail": evidence.get("evidence") or evidence.get("evidence_detail"),
                "trusted_local_manifest": False,
                "venue_native": True,
                "outcome_market_ids": evidence.get("outcome_market_ids") or [],
                "expected_outcome_count": evidence.get("expected_outcome_count"),
            }
        )
    return specs


def _group_members(spec: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    venue = str(spec.get("venue") or "").lower()
    group_id = spec.get("group_id")
    expected_ids = {str(value) for value in spec.get("outcome_market_ids") or [] if value is not None}
    members = []
    for row in rows:
        if str(row.get("venue") or "").lower() != venue:
            continue
        if expected_ids and str(_market_id(row)) in expected_ids:
            members.append(row)
            continue
        if row.get("group_id") == group_id or row.get("event_id") == group_id:
            members.append(row)
            continue
        raw = row.get("raw")
        if isinstance(raw, dict) and (raw.get("group_id") == group_id or raw.get("event_id") == group_id):
            members.append(row)
    return members


def _settlement_resolution_audit(members: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    summaries = [_resolution_summary(row, spec) for row in members]
    blockers: list[str] = []
    required_fields = ("resolution_date", "settlement_time", "resolution_criteria", "event_id", "group_id", "settlement_source")
    if not summaries or any(any(not summary.get(field) for field in required_fields) for summary in summaries):
        blockers.append("missing_resolution_metadata")
    if _distinct_count(summaries, "resolution_date") > 1 or _distinct_count(summaries, "settlement_time") > 1:
        blockers.append("mixed_resolution_timing")
    if _distinct_count(summaries, "close_time") > 1:
        blockers.append("mixed_resolution_timing")
    if _distinct_count(summaries, "resolution_criteria_key") > 1:
        blockers.append("mixed_resolution_criteria")
    if _distinct_count(summaries, "settlement_source_key") > 1:
        blockers.append("mixed_settlement_source")
    if _distinct_count(summaries, "event_id") > 1 or _distinct_count(summaries, "group_id") > 1:
        blockers.append("mixed_event_group_metadata")
    blockers = sorted(set(blockers))
    key = None
    if not blockers and summaries:
        first = summaries[0]
        key = {
            "resolution_date": first["resolution_date"],
            "settlement_time": first["settlement_time"],
            "close_time": first.get("close_time"),
            "resolution_criteria_key": first["resolution_criteria_key"],
            "settlement_source_key": first["settlement_source_key"],
            "event_id": first["event_id"],
            "group_id": first["group_id"],
        }
    return {
        "settlement_audit_status": "PASS" if not blockers else "FAIL",
        "settlement_audit_blockers": blockers,
        "resolution_metadata_complete": not any("missing_resolution_metadata" == blocker for blocker in blockers),
        "normalized_resolution_key": key,
        "per_leg_resolution_summary": summaries,
    }


def _resolution_summary(row: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    criteria = _first_value(
        row,
        raw,
        keys=("resolution_criteria", "resolution_text", "rules", "rules_primary", "settlement_rule", "settlement_rules"),
    )
    settlement_source = _first_value(
        row,
        raw,
        keys=("settlement_source", "resolution_source", "settlement_basis", "settlement_rule_source"),
    )
    event_id = _first_value(row, raw, keys=("event_id", "venue_native_event_id", "event_ticker"))
    group_id = _first_value(row, raw, keys=("group_id", "venue_native_group_id", "event_ticker")) or spec.get("group_id")
    return {
        "market_id": _market_id(row),
        "resolution_date": _first_value(row, raw, keys=("resolution_date", "settlement_date")),
        "settlement_time": _first_value(row, raw, keys=("settlement_time", "expected_settlement_time", "expiration_time")),
        "close_time": _first_value(row, raw, keys=("close_time", "expected_expiration_time")),
        "resolution_criteria": criteria,
        "resolution_criteria_key": _normalize_text_key(criteria),
        "event_id": event_id,
        "group_id": group_id,
        "settlement_source": settlement_source,
        "settlement_source_key": _normalize_text_key(settlement_source),
    }


def _distinct_count(rows: list[dict[str, Any]], key: str) -> int:
    return len({row.get(key) for row in rows if row.get(key)})


def _first_value(*dicts: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for item in dicts:
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _normalize_text_key(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.lower().split())


def _market_rows(snapshot_payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for payload in snapshot_payloads:
        markets = payload.get("normalized_markets")
        if not isinstance(markets, list):
            markets = payload.get("markets") if isinstance(payload.get("markets"), list) else []
        for row in markets:
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _outcome_summary(row: dict[str, Any], ask: float | None, depth: float | None, captured: datetime | None, age: float | None) -> dict[str, Any]:
    return {
        "market_id": _market_id(row),
        "ticker": row.get("ticker"),
        "title": row.get("question") or row.get("title") or row.get("market_title"),
        "best_ask": ask,
        "depth_at_best_ask": depth,
        "orderbook_captured_at": captured.isoformat() if captured else None,
        "quote_age_seconds": None if age is None else round(age, 6),
    }


def _market_id(row: dict[str, Any]) -> Any:
    return row.get("market_id") or row.get("id") or row.get("ticker") or row.get("slug")


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_datetime_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _default_fee_models() -> dict[str, FeeModel]:
    return {
        "kalshi": KalshiTieredFeeModel(),
        "polymarket": PolymarketConservativeFeeModel(),
    }
