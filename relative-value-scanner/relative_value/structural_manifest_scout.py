from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from relative_value.exhaustive_evidence_trust import has_reference_only_flag
from relative_value.fees import FeeModel, KalshiTieredFeeModel
from relative_value.kalshi_native_groups import _extract_record, _iter_market_records


STATUS_MANIFEST_REVIEW_CANDIDATE = "MANIFEST_REVIEW_CANDIDATE"
STATUS_BLOCKED_METADATA = "BLOCKED_METADATA"
STATUS_BLOCKED_STALE = "BLOCKED_STALE"
STATUS_BLOCKED_DEPTH = "BLOCKED_DEPTH"
STATUS_BLOCKED_REFERENCE_ONLY = "BLOCKED_REFERENCE_ONLY"

WARNING = "This is not exhaustive evidence. Do not paper-simulate without a valid local_manifest_v1 or explicit venue-native completeness."


def scout_structural_manifest_candidates(
    snapshot_payload: dict[str, Any],
    *,
    generated_at: datetime | None = None,
    max_quote_age_seconds: float = 1800.0,
    min_depth: float = 1.0,
    fee_model: FeeModel | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    model = fee_model or KalshiTieredFeeModel()
    records = [_scout_record(row, event_meta) for row, event_meta in _iter_market_records(snapshot_payload)]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        group_id = record.get("venue_native_group_id")
        if group_id:
            grouped[str(group_id)].append(record)
        else:
            grouped[f"title_only:{record.get('event_title') or record.get('title') or record.get('market_ticker') or 'unknown'}"].append(record)
    rows = [
        _scout_group(
            group_id=group_id,
            records=group_records,
            generated_at=generated,
            max_quote_age_seconds=max_quote_age_seconds,
            min_depth=min_depth,
            fee_model=model,
        )
        for group_id, group_records in grouped.items()
    ]
    rows.sort(key=_rank_key)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    status_counts = Counter(row["status"] for row in rows)
    return {
        "schema_version": 1,
        "source": "structural_manifest_candidate_scout_v1",
        "generated_at": generated.isoformat(),
        "warning": WARNING,
        "summary": {
            "groups_discovered": len(rows),
            "manifest_review_candidate_count": sum(1 for row in rows if row["status"] == STATUS_MANIFEST_REVIEW_CANDIDATE),
            "blocked_count": sum(1 for row in rows if row["status"] != STATUS_MANIFEST_REVIEW_CANDIDATE),
            "paper_candidate_count": 0,
            "gated_row_count": 0,
            "status_counts": dict(sorted(status_counts.items())),
        },
        "rows": rows,
        "safety": {
            "saved_file_only": True,
            "diagnostic_only": True,
            "not_exhaustive_evidence": True,
            "requires_local_manifest": True,
            "affects_evaluator_gates": False,
            "paper_candidate_emitted": False,
            "gated_review_row_emitted": False,
            "uses_title_similarity_for_exhaustiveness": False,
            "uses_graph_hints_for_exhaustiveness": False,
            "allowed_actions": ["MANIFEST_REVIEW"],
        },
    }


def scout_structural_manifest_candidates_file(
    *,
    snapshot_path: Path,
    json_output: Path,
    markdown_output: Path,
    max_quote_age_seconds: float = 1800.0,
    min_depth: float = 1.0,
) -> dict[str, Any]:
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    report = scout_structural_manifest_candidates(
        payload,
        max_quote_age_seconds=max_quote_age_seconds,
        min_depth=min_depth,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_structural_manifest_scout_markdown(report), encoding="utf-8")
    return report


def render_structural_manifest_scout_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Structural Manifest Candidate Scout",
        "",
        WARNING,
        "",
        "| Rank | Group | Status | Markets | Apparent outcomes | Shared rules | Shared times | Sum asks | Min depth | Max quote age | Top blockers |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in report.get("rows", []):
        quote_age = row.get("quote_age_summary") or {}
        depth = row.get("depth_summary") or {}
        lines.append(
            "| {rank} | {group} | {status} | {markets} | {outcomes} | {rules} | {times} | {sum_asks:.4f} | {min_depth:.4f} | {max_age:.4f} | {blockers} |".format(
                rank=row.get("rank") or "",
                group=str(row.get("venue_native_group_id") or "").replace("|", "/"),
                status=row.get("status") or "",
                markets=row.get("market_count") or 0,
                outcomes=row.get("apparent_outcome_count") or 0,
                rules=str(row.get("has_shared_rules")).lower(),
                times=str(row.get("has_shared_times")).lower(),
                sum_asks=float(row.get("provisional_sum_asks") or 0.0),
                min_depth=float(depth.get("min") or 0.0),
                max_age=float(quote_age.get("max_seconds") or 0.0),
                blockers="; ".join((row.get("missing_metadata_blockers") or [])[:5]).replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"


def _scout_record(row: dict[str, Any], event_meta: dict[str, Any] | None) -> dict[str, Any]:
    record = _extract_record(row, event_meta)
    ob = row.get("orderbook_enrichment") if isinstance(row.get("orderbook_enrichment"), dict) else {}
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    record["best_ask"] = _float_or_none(ob.get("best_ask")) or _float_or_none(row.get("best_ask")) or _float_or_none(raw.get("yes_ask_dollars"))
    record["depth_at_best_ask"] = (
        _float_or_none(ob.get("depth_at_best_ask"))
        or _float_or_none(row.get("depth_at_best_ask"))
        or _float_or_none(raw.get("yes_ask_size_fp"))
    )
    record["orderbook_captured_at"] = ob.get("orderbook_captured_at") or row.get("orderbook_captured_at") or raw.get("updated_time")
    record["reference_only"] = record.get("reference_only") or has_reference_only_flag(row) or has_reference_only_flag(raw)
    return record


def _scout_group(
    *,
    group_id: str,
    records: list[dict[str, Any]],
    generated_at: datetime,
    max_quote_age_seconds: float,
    min_depth: float,
    fee_model: FeeModel,
) -> dict[str, Any]:
    blockers: list[str] = []
    first = records[0] if records else {}
    has_title_only = str(group_id).startswith("title_only:") or any(record.get("title_only_group") for record in records)
    if has_title_only:
        blockers.append("title_only_group_not_trusted")
    if not first.get("venue_native_event_id"):
        blockers.append("missing_venue_native_event_id")
    if not first.get("venue_native_group_id"):
        blockers.append("missing_venue_native_group_id")
    if any(record.get("reference_only") for record in records):
        blockers.append("reference_only_source")
    rules_keys = {record.get("rules_key") for record in records if record.get("rules_key")}
    time_keys = {record.get("time_key") for record in records if record.get("time_key")}
    has_shared_rules = len(rules_keys) == 1 and len(rules_keys) == len({record.get("rules_key") for record in records})
    has_shared_times = len(time_keys) == 1 and len(time_keys) == len({record.get("time_key") for record in records})
    if not has_shared_rules:
        blockers.append("missing_or_mixed_rules")
    if not has_shared_times:
        blockers.append("missing_or_mixed_times")
    has_orderbooks = all(record.get("best_ask") is not None for record in records)
    if not has_orderbooks:
        blockers.append("missing_orderbook_ask")
    depths = [record["depth_at_best_ask"] for record in records if record.get("depth_at_best_ask") is not None]
    if len(depths) != len(records):
        blockers.append("missing_depth")
    elif any(depth < min_depth for depth in depths):
        blockers.append("insufficient_depth")
    ages = []
    for record in records:
        captured = _parse_datetime_or_none(record.get("orderbook_captured_at"))
        if captured is None:
            blockers.append("missing_quote_timestamp")
            continue
        ages.append(max(0.0, (generated_at - captured).total_seconds()))
    if any(age > max_quote_age_seconds for age in ages):
        blockers.append("stale_quote")
    asks = [record["best_ask"] for record in records if record.get("best_ask") is not None]
    provisional_sum = sum(asks)
    provisional_fee = sum(fee_model.fee_for_leg(ask) for ask in asks)
    status = _status(blockers)
    return {
        "venue": "kalshi",
        "candidate_type": "manifest_candidate_scout",
        "venue_native_event_id": first.get("venue_native_event_id"),
        "venue_native_group_id": first.get("venue_native_group_id") or group_id,
        "event_ticker": first.get("event_ticker"),
        "series_ticker": first.get("series_ticker"),
        "status": status,
        "diagnostic_only": True,
        "not_exhaustive_evidence": True,
        "requires_local_manifest": True,
        "affects_evaluator_gates": False,
        "market_count": len(records),
        "apparent_outcome_count": len({record.get("outcome") for record in records if record.get("outcome")}),
        "has_shared_rules": has_shared_rules,
        "has_shared_times": has_shared_times,
        "has_orderbooks": has_orderbooks,
        "quote_age_summary": _age_summary(ages),
        "depth_summary": _number_summary(depths),
        "provisional_sum_asks": _round(provisional_sum),
        "provisional_fee_estimate": _round(provisional_fee),
        "missing_metadata_blockers": sorted(set(blockers)),
        "markets": [_market_summary(record) for record in records],
    }


def _status(blockers: list[str]) -> str:
    blocker_set = set(blockers)
    if "reference_only_source" in blocker_set:
        return STATUS_BLOCKED_REFERENCE_ONLY
    if "stale_quote" in blocker_set or "missing_quote_timestamp" in blocker_set:
        return STATUS_BLOCKED_STALE
    if "missing_depth" in blocker_set or "insufficient_depth" in blocker_set:
        return STATUS_BLOCKED_DEPTH
    metadata_blockers = blocker_set - {"missing_depth", "insufficient_depth", "stale_quote", "missing_quote_timestamp"}
    if metadata_blockers:
        return STATUS_BLOCKED_METADATA
    return STATUS_MANIFEST_REVIEW_CANDIDATE


def _rank_key(row: dict[str, Any]) -> tuple[Any, ...]:
    status_rank = 0 if row.get("status") == STATUS_MANIFEST_REVIEW_CANDIDATE else 1
    blockers = len(row.get("missing_metadata_blockers") or [])
    sum_asks = float(row.get("provisional_sum_asks") or 999.0)
    depth_min = float((row.get("depth_summary") or {}).get("min") or 0.0)
    max_age = float((row.get("quote_age_summary") or {}).get("max_seconds") or 999999.0)
    return (status_rank, blockers, sum_asks, -depth_min, max_age, str(row.get("venue_native_group_id") or ""))


def _market_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "market_ticker": record.get("market_ticker"),
        "title": record.get("title"),
        "outcome": record.get("outcome"),
        "yes_sub_title": record.get("yes_sub_title"),
        "no_sub_title": record.get("no_sub_title"),
        "best_ask": record.get("best_ask"),
        "depth_at_best_ask": record.get("depth_at_best_ask"),
        "orderbook_captured_at": record.get("orderbook_captured_at"),
        "rules_primary": record.get("rules_primary"),
        "rules_secondary": record.get("rules_secondary"),
        "close_time": record.get("close_time"),
        "expected_expiration_time": record.get("expected_expiration_time"),
        "expiration_time": record.get("expiration_time"),
        "latest_expiration_time": record.get("latest_expiration_time"),
        "floor_strike": record.get("floor_strike"),
        "cap_strike": record.get("cap_strike"),
        "strike_type": record.get("strike_type"),
    }


def _age_summary(ages: list[float]) -> dict[str, Any]:
    if not ages:
        return {"count": 0, "min_seconds": None, "max_seconds": None, "avg_seconds": None}
    return {
        "count": len(ages),
        "min_seconds": _round(min(ages)),
        "max_seconds": _round(max(ages)),
        "avg_seconds": _round(mean(ages)),
    }


def _number_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "max": None, "avg": None}
    return {"count": len(values), "min": _round(min(values)), "max": _round(max(values)), "avg": _round(mean(values))}


def _parse_datetime_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)
