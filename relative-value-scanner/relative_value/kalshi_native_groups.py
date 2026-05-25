from __future__ import annotations

import json
import hashlib
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.exhaustive_evidence_trust import exhaustive_evidence_trust_blockers, has_reference_only_flag
from relative_value.local_manifest_v1 import (
    LOCAL_MANIFEST_SOURCE,
    manifest_outcome_list,
    validate_local_manifest_v1_group,
)


SOURCE = "kalshi_event_metadata"
KNOWN_AUDIT_LABELS = ("fed", "btc", "mlb", "nba", "nhl")
CLASS_THRESHOLD_LADDER_NOT_EXHAUSTIVE = "THRESHOLD_LADDER_NOT_EXHAUSTIVE"
CLASS_RANGE_LADDER_NOT_EXHAUSTIVE = "RANGE_LADDER_NOT_EXHAUSTIVE"
CLASS_COMPLETE_EVENT_GROUP = "COMPLETE_EVENT_GROUP"
CLASS_INCOMPLETE_GROUP = "INCOMPLETE_GROUP"
CLASS_PARTIAL_EVENT_METADATA = "PARTIAL_EVENT_METADATA"


def audit_kalshi_native_groups(snapshot_payload: dict[str, Any], *, generated_at: datetime | None = None) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    records = [_extract_record(row, event_meta) for row, event_meta in _iter_market_records(snapshot_payload)]
    manifest_groups = _trusted_manifest_groups(snapshot_payload)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["venue_native_group_id"] or f"title_only:{record.get('event_title') or record.get('title') or record['market_ticker']}"].append(record)
    groups = [_audit_group(group_id, rows, manifest_groups.get(group_id)) for group_id, rows in sorted(grouped.items())]
    candidate_rows = [candidate for group in groups for candidate in group["structural_basket_input_rows"]]
    status_counts = Counter(group["status"] for group in groups)
    classification_counts = Counter(group["group_classification"] for group in groups)
    return {
        "schema_version": 1,
        "source": "kalshi_native_group_saved_snapshot_audit_v1",
        "generated_at": generated.isoformat(),
        "summary": {
            "groups_discovered": len(groups),
            "complete_groups": sum(1 for group in groups if group["status"] == "COMPLETE_EXHAUSTIVE_GROUP"),
            "status_incomplete_groups": sum(1 for group in groups if group["status"] == "INCOMPLETE_GROUP"),
            "blocked_groups": sum(1 for group in groups if group["blockers"]),
            "candidate_input_row_count": len(candidate_rows),
            "threshold_ladder_groups": classification_counts.get(CLASS_THRESHOLD_LADDER_NOT_EXHAUSTIVE, 0),
            "range_ladder_groups": classification_counts.get(CLASS_RANGE_LADDER_NOT_EXHAUSTIVE, 0),
            "complete_event_groups": classification_counts.get(CLASS_COMPLETE_EVENT_GROUP, 0),
            "incomplete_groups": classification_counts.get(CLASS_INCOMPLETE_GROUP, 0),
            "partial_event_metadata_groups": classification_counts.get(CLASS_PARTIAL_EVENT_METADATA, 0),
            "groups_with_shared_rules": sum(1 for group in groups if group.get("shared_rules") is True),
            "groups_with_shared_times": sum(1 for group in groups if group.get("shared_times") is True),
            "groups_missing_completeness": sum(1 for group in groups if "missing_completeness_evidence" in group["blockers"]),
            "paper_candidate_count": 0,
            "stop_for_review_count": 0,
            "status_counts": dict(sorted(status_counts.items())),
            "classification_counts": dict(sorted(classification_counts.items())),
        },
        "groups": groups,
        "structural_basket_detector_inputs": candidate_rows,
        "safety": {
            "saved_file_only": True,
            "diagnostic_only": True,
            "live_fetch_attempted": False,
            "paper_candidate_emitted": False,
            "paper_candidate_count": 0,
            "stop_for_review_emitted": False,
            "uses_title_similarity_for_exhaustiveness": False,
            "uses_graph_hints_for_exhaustiveness": False,
            "allowed_evidence_source": SOURCE,
            "requires_venue_native": True,
        },
    }


def audit_kalshi_native_groups_file(*, snapshot_path: Path, json_output: Path | None = None, markdown_output: Path | None = None) -> dict[str, Any]:
    if json_output is None:
        paths = kalshi_native_group_audit_paths(snapshot_path)
        json_output = paths["json_output"]
        if markdown_output is None:
            markdown_output = paths["markdown_output"]
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    report = audit_kalshi_native_groups(payload)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_kalshi_native_groups_markdown(report), encoding="utf-8")
    return report


def kalshi_native_group_audit_paths(
    snapshot_path: Path,
    *,
    output_dir: Path = Path("reports") / "native_group_audits",
    label: str | None = None,
) -> dict[str, Path]:
    safe_label = safe_kalshi_native_group_audit_label(snapshot_path, label=label)
    return {
        "json_output": output_dir / f"{safe_label}.json",
        "markdown_output": output_dir / f"{safe_label}.md",
    }


def safe_kalshi_native_group_audit_label(snapshot_path: Path, *, label: str | None = None, max_length: int = 40) -> str:
    if label:
        base = label
    else:
        parts = [part.lower() for part in snapshot_path.parts]
        base = next((known for known in KNOWN_AUDIT_LABELS if known in parts), "")
        if not base:
            stem = snapshot_path.stem or "snapshot"
            digest = hashlib.sha256(str(snapshot_path).encode("utf-8")).hexdigest()[:8]
            base = f"{stem}_{digest}"
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_").lower()
    if not safe:
        safe = "snapshot"
    if len(safe) > max_length:
        digest = hashlib.sha256(safe.encode("utf-8")).hexdigest()[:8]
        safe = f"{safe[: max_length - 9].rstrip('_')}_{digest}"
    return safe


def render_kalshi_native_groups_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Kalshi Native Groups Audit",
        "",
        "Saved-file-only audit. Complete groups become structural basket detector inputs only; this report never emits paper candidates or review-stop statuses.",
        "",
        "| Group | Status | Markets | Outcomes | Blockers |",
        "|---|---:|---:|---:|---|",
    ]
    for group in report.get("groups", []):
        lines.append(
            "| {group_id} | {status} | {markets} | {outcomes} | {blockers} |".format(
                group_id=str(group.get("venue_native_group_id") or "").replace("|", "/"),
                status=group.get("group_classification") or group.get("status") or "",
                markets=group.get("market_count") or 0,
                outcomes=len(group.get("outcome_list") or []),
                blockers="; ".join(group.get("blockers") or []).replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"


def _audit_group(group_id: str, rows: list[dict[str, Any]], manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    first = rows[0] if rows else {}
    explicit_outcome_list = _first_list(*(row.get("outcome_list") for row in rows))
    manifest_blockers = validate_local_manifest_v1_group(manifest) if _is_local_manifest(manifest) else []
    manifest_outcome_list = (
        manifest_outcome_list_from(manifest) if isinstance(manifest, dict) and not manifest_blockers else None
    )
    row_outcome_labels = [row["outcome"] for row in rows if row.get("outcome")]
    outcome_list = explicit_outcome_list or manifest_outcome_list
    completeness = _first_bool(*(row.get("completeness") for row in rows))
    trusted_manifest_complete = bool(manifest_outcome_list and _is_local_manifest(manifest) and not manifest_blockers)
    if trusted_manifest_complete:
        completeness = True
    blockers: list[str] = []
    if not first.get("venue_native_event_id"):
        blockers.append("missing_venue_native_event_id")
    if not first.get("venue_native_group_id"):
        blockers.append("missing_venue_native_group_id")
    if explicit_outcome_list is None and manifest_outcome_list is None:
        blockers.extend(["missing_outcome_list", "partial_event_metadata"])
    if any(row.get("per_market_binary_outcomes") for row in rows) and explicit_outcome_list is None and manifest_outcome_list is None:
        blockers.append("per_market_binary_outcomes_not_event_outcome_list")
    if completeness is not True:
        blockers.extend(["missing_completeness_evidence", "partial_event_metadata"])
    if any(row.get("title_only_group") for row in rows):
        blockers.append("title_only_group_not_trusted")
    if any(row.get("reference_only") for row in rows):
        blockers.append("reference_only_source")
    if any(row.get("range_ladder") for row in rows) and not trusted_manifest_complete:
        blockers.append("range_ladder_not_exhaustive")
    elif any(row.get("threshold_ladder") for row in rows) and not trusted_manifest_complete:
        blockers.append("threshold_ladder_not_exhaustive")
    if outcome_list is not None and len(rows) != len(outcome_list):
        blockers.append("partial_event_metadata")
    if any(not row.get("rules") for row in rows):
        blockers.append("missing_resolution_metadata")
    if any(not row.get("settlement_source_raw_evidence") for row in rows):
        blockers.append("missing_settlement_source")
    if _distinct_count(rows, "rules_key") > 1:
        blockers.append("mixed_resolution_criteria")
    if _distinct_count(rows, "time_key") > 1:
        blockers.append("mixed_time_metadata")
    blockers.extend(manifest_blockers)
    blockers.extend(
        exhaustive_evidence_trust_blockers(
            source=SOURCE,
            is_exhaustive=completeness is True,
            venue_native=bool(first.get("venue_native_event_id") and first.get("venue_native_group_id")),
            trusted_local_manifest=False,
        )
    )
    blockers = sorted(set(blockers))
    shared_rules = bool(rows) and _distinct_count(rows, "rules_key") == 1 and all(row.get("rules_key") for row in rows)
    shared_times = bool(rows) and _distinct_count(rows, "time_key") == 1 and all(row.get("time_key") for row in rows)
    group_classification = _group_classification(blockers, rows)
    group = {
        "venue": "kalshi",
        "venue_native_event_id": first.get("venue_native_event_id"),
        "venue_native_group_id": first.get("venue_native_group_id") or group_id,
        "source": SOURCE,
        "venue_native": bool(first.get("venue_native_event_id") and first.get("venue_native_group_id")),
        "status": "COMPLETE_EXHAUSTIVE_GROUP" if not blockers else "INCOMPLETE_GROUP",
        "group_classification": group_classification,
        "blockers": blockers,
        "market_count": len(rows),
        "outcome_list": outcome_list or [],
        "row_outcome_labels": row_outcome_labels,
        "per_market_binary_outcomes": [row.get("per_market_binary_outcomes") for row in rows if row.get("per_market_binary_outcomes")],
        "outcome_list_source": "explicit" if explicit_outcome_list else ("trusted_local_manifest" if manifest_outcome_list else None),
        "trusted_local_manifest_complete": trusted_manifest_complete,
        "shared_rules": shared_rules,
        "shared_times": shared_times,
        "rules_primary": first.get("rules_primary") if shared_rules else None,
        "rules_secondary": first.get("rules_secondary") if shared_rules else None,
        "close_time": first.get("close_time") if shared_times else None,
        "expected_expiration_time": first.get("expected_expiration_time") if shared_times else None,
        "expiration_time": first.get("expiration_time") if shared_times else None,
        "latest_expiration_time": first.get("latest_expiration_time") if shared_times else None,
        "settlement_source_status": first.get("settlement_source_status") if shared_rules else "missing",
        "settlement_source_raw_evidence": first.get("settlement_source_raw_evidence") if shared_rules else None,
        "markets": rows,
        "structural_basket_input_rows": [],
        "paper_candidate_emitted": False,
        "stop_for_review_emitted": False,
    }
    if not blockers:
        group["structural_basket_input_rows"] = [_structural_input_row(row, rows, outcome_list or []) for row in rows]
    return group


def _structural_input_row(row: dict[str, Any], rows: list[dict[str, Any]], outcome_list: list[str]) -> dict[str, Any]:
    market_ids = [market["market_ticker"] for market in rows]
    return {
        "venue": "kalshi",
        "market_id": row["market_ticker"],
        "ticker": row["market_ticker"],
        "event_id": row["venue_native_group_id"],
        "group_id": row["venue_native_group_id"],
        "question": row.get("title") or row.get("rules") or row["market_ticker"],
        "outcome": row.get("outcome"),
        "close_time": row.get("close_time"),
        "expected_expiration_time": row.get("expected_expiration_time"),
        "expiration_time": row.get("expiration_time"),
        "latest_expiration_time": row.get("latest_expiration_time"),
        "settlement_time": row.get("settlement_time"),
        "resolution_date": row.get("resolution_date"),
        "rules": row.get("rules"),
        "rules_primary": row.get("rules_primary"),
        "rules_secondary": row.get("rules_secondary"),
        "resolution_criteria": row.get("rules"),
        "settlement_source": row.get("settlement_source_raw_evidence"),
        "settlement_source_status": row.get("settlement_source_status"),
        "settlement_source_raw_evidence": row.get("settlement_source_raw_evidence"),
        "orderbook_enrichment": row.get("orderbook_enrichment"),
        "exhaustive_group": {
            "source": SOURCE,
            "venue_native": True,
            "all_outcomes_included": True,
            "group_id": row["venue_native_group_id"],
            "event_id": row["venue_native_event_id"],
            "outcome_market_ids": market_ids,
            "expected_outcome_count": len(outcome_list),
            "outcome_list": outcome_list,
            "evidence": "saved Kalshi venue-native event metadata includes completeness and outcome_list",
        },
    }


def _extract_record(row: dict[str, Any], event_meta: dict[str, Any] | None) -> dict[str, Any]:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    event = event_meta or (row.get("event") if isinstance(row.get("event"), dict) else None)
    event = event or {}
    event_id = _first_string(row, raw, event, keys=("venue_native_event_id", "event_id", "event_ticker", "id"))
    group_id = _first_string(row, raw, event, keys=("venue_native_group_id", "group_id", "event_ticker", "series_ticker"))
    title = _first_string(row, raw, event, keys=("question", "title", "market_title", "event_title", "name"))
    rules_primary = _first_string(row, raw, event, keys=("rules_primary", "rules", "resolution_text", "description"))
    rules_secondary = _first_string(row, raw, event, keys=("rules_secondary", "settlement_rules", "resolution_secondary"))
    rules = _combined_rules(rules_primary, rules_secondary)
    settlement_source = _settlement_source_from_rules(row, raw, event, rules=rules)
    close_time = _first_string(row, raw, event, keys=("close_time",))
    expected_expiration_time = _first_string(row, raw, event, keys=("expected_expiration_time",))
    expiration_time = _first_string(row, raw, event, keys=("expiration_time",))
    latest_expiration_time = _first_string(row, raw, event, keys=("latest_expiration_time",))
    floor_strike = _first_present(row, raw, keys=("floor_strike",))
    cap_strike = _first_present(row, raw, keys=("cap_strike",))
    strike_type = _first_string(row, raw, event, keys=("strike_type",))
    range_ladder = _is_range_ladder(row, raw, floor_strike=floor_strike, cap_strike=cap_strike, strike_type=strike_type)
    threshold_ladder = False if range_ladder else _is_threshold_ladder(row, raw, floor_strike=floor_strike, cap_strike=cap_strike, strike_type=strike_type)
    return {
        "venue": "kalshi",
        "venue_native_event_id": event_id,
        "venue_native_group_id": group_id,
        "event_id": event_id,
        "event_ticker": _first_string(row, raw, event, keys=("event_ticker",)),
        "series_ticker": _first_string(row, raw, event, keys=("series_ticker",)),
        "market_ticker": _first_string(row, raw, keys=("market_ticker", "ticker")) or "",
        "title": title,
        "yes_sub_title": _first_string(row, raw, keys=("yes_sub_title",)),
        "no_sub_title": _first_string(row, raw, keys=("no_sub_title",)),
        "outcome": _first_string(row, raw, keys=("outcome", "outcome_label", "yes_sub_title", "subtitle", "sub_title")),
        "per_market_binary_outcomes": _per_market_binary_outcomes(row, raw),
        "outcome_list": _explicit_event_outcome_list(row, raw, event),
        "completeness": _first_bool(
            row.get("is_exhaustive"),
            row.get("all_outcomes_included"),
            row.get("complete"),
            raw.get("is_exhaustive"),
            raw.get("all_outcomes_included"),
            raw.get("complete"),
            event.get("is_exhaustive"),
            event.get("all_outcomes_included"),
            event.get("complete"),
            _completeness_bool(row.get("completeness")),
            _completeness_bool(raw.get("completeness")),
            _completeness_bool(event.get("completeness")),
        ),
        "rules_primary": rules_primary,
        "rules_secondary": rules_secondary,
        "rules": rules,
        "rules_key": _normalize_text_key(rules),
        "event_title": _first_string(row, raw, event, keys=("event_title", "title", "name")),
        "close_time": close_time,
        "expected_expiration_time": expected_expiration_time,
        "expiration_time": expiration_time,
        "latest_expiration_time": latest_expiration_time,
        "settlement_time": _first_string(row, raw, event, keys=("settlement_time", "expected_settlement_time", "expected_expiration_time")),
        "resolution_date": _resolution_date(row, raw, event),
        "time_key": _normalize_time_key(
            close_time=close_time,
            expected_expiration_time=expected_expiration_time,
            expiration_time=expiration_time,
            latest_expiration_time=latest_expiration_time,
        ),
        "settlement_source_status": "explicit" if settlement_source else "missing",
        "settlement_source_raw_evidence": settlement_source,
        "floor_strike": floor_strike,
        "cap_strike": cap_strike,
        "strike_type": strike_type,
        "threshold_ladder": threshold_ladder,
        "range_ladder": range_ladder,
        "orderbook_enrichment": row.get("orderbook_enrichment") if isinstance(row.get("orderbook_enrichment"), dict) else None,
        "reference_only": has_reference_only_flag(row) or has_reference_only_flag(raw) or has_reference_only_flag(event),
        "title_only_group": not event_id and not group_id and bool(title),
    }


def _iter_market_records(payload: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    events = [event for event in payload.get("events", []) if isinstance(event, dict)]
    event_index = {}
    for event in events:
        for key in ("event_ticker", "event_id", "id", "venue_native_event_id"):
            value = event.get(key)
            if isinstance(value, str) and value:
                event_index[value] = event
    rows: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for event in events:
        for market in event.get("markets", []) if isinstance(event.get("markets"), list) else []:
            if isinstance(market, dict):
                merged = {**market}
                merged.setdefault("event_ticker", event.get("event_ticker"))
                merged.setdefault("event_id", event.get("event_id") or event.get("id"))
                rows.append((merged, event))
    markets = payload.get("normalized_markets")
    if not isinstance(markets, list):
        markets = payload.get("markets") if isinstance(payload.get("markets"), list) else []
    for market in markets:
        if not isinstance(market, dict):
            continue
        raw = market.get("raw") if isinstance(market.get("raw"), dict) else {}
        event_key = market.get("event_ticker") or market.get("event_id") or raw.get("event_ticker") or raw.get("event_id")
        rows.append((market, event_index.get(event_key)))
    return rows


def _trusted_manifest_groups(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    groups = {}
    raw_groups = payload.get("trusted_exhaustive_groups") or payload.get("exhaustive_groups") or []
    if not isinstance(raw_groups, list):
        return groups
    for group in raw_groups:
        if not isinstance(group, dict) or not _is_local_manifest(group):
            continue
        if validate_local_manifest_v1_group(group):
            continue
        group_id = group.get("group_id") or group.get("event_ticker") or group.get("event_id")
        if isinstance(group_id, str) and group_id:
            groups[group_id] = group
    return groups


def manifest_outcome_list_from(manifest: dict[str, Any]) -> list[str] | None:
    return manifest_outcome_list(manifest) or _list_from(manifest.get("outcome_list") or manifest.get("outcomes"))


def _is_local_manifest(manifest: Any) -> bool:
    return isinstance(manifest, dict) and manifest.get("source") == LOCAL_MANIFEST_SOURCE


def _group_classification(blockers: list[str], rows: list[dict[str, Any]]) -> str:
    if not blockers:
        return CLASS_COMPLETE_EVENT_GROUP
    if any(row.get("range_ladder") for row in rows):
        return CLASS_RANGE_LADDER_NOT_EXHAUSTIVE
    if any(row.get("threshold_ladder") for row in rows):
        return CLASS_THRESHOLD_LADDER_NOT_EXHAUSTIVE
    if "partial_event_metadata" in blockers:
        return CLASS_PARTIAL_EVENT_METADATA
    return CLASS_INCOMPLETE_GROUP


def _first_string(*dicts: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for item in dicts:
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _first_present(*dicts: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for item in dicts:
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = item.get(key)
            if value is not None:
                return value
    return None


def _combined_rules(primary: str | None, secondary: str | None) -> str | None:
    parts = [part for part in (primary, secondary) if isinstance(part, str) and part.strip()]
    if not parts:
        return None
    return "\n".join(parts)


def _first_list(*values: list[str] | None) -> list[str] | None:
    for value in values:
        if value:
            return value
    return None


def _list_from(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    result = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        elif isinstance(item, dict):
            label = item.get("label") or item.get("name") or item.get("outcome") or item.get("yes_sub_title")
            if isinstance(label, str) and label.strip():
                result.append(label.strip())
    return result or None


def _first_bool(*values: Any) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
    return None


def _completeness_bool(value: Any) -> bool | None:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"complete", "all_outcomes_included", "exhaustive"}:
            return True
        if normalized in {"partial", "incomplete"}:
            return False
    return value if isinstance(value, bool) else None


def _distinct_count(rows: list[dict[str, Any]], key: str) -> int:
    return len({row.get(key) for row in rows if row.get(key)})


def _normalize_text_key(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.lower().split())


def _per_market_binary_outcomes(row: dict[str, Any], raw: dict[str, Any]) -> list[str]:
    values = []
    for source in (row.get("outcomes"), raw.get("outcomes"), row.get("outcome_list"), raw.get("outcome_list")):
        parsed = _list_from(source)
        if parsed:
            values.extend(parsed)
    normalized = {_normalize_text_key(value) for value in values}
    if {"yes", "no"}.issubset(normalized):
        return ["Yes", "No"]
    return []


def _explicit_event_outcome_list(row: dict[str, Any], raw: dict[str, Any], event: dict[str, Any]) -> list[str] | None:
    event_level = _first_list(_list_from(event.get("outcome_list")), _list_from(event.get("outcomes")))
    if event_level:
        return event_level
    for source in (row.get("outcome_list"), raw.get("outcome_list")):
        parsed = _list_from(source)
        if parsed and not _is_binary_yes_no_list(parsed):
            return parsed
    return None


def _is_binary_yes_no_list(values: list[str]) -> bool:
    normalized = {_normalize_text_key(value) for value in values}
    return normalized == {"yes", "no"}


def _normalize_time_key(
    *,
    close_time: str | None,
    expected_expiration_time: str | None,
    expiration_time: str | None,
    latest_expiration_time: str | None,
) -> str | None:
    values = [close_time, expected_expiration_time, expiration_time, latest_expiration_time]
    if not any(values):
        return None
    return "|".join(_normalize_text_key(value) or "" for value in values)


def _is_range_ladder(
    row: dict[str, Any],
    raw: dict[str, Any],
    *,
    floor_strike: Any,
    cap_strike: Any,
    strike_type: str | None,
) -> bool:
    text = _ladder_text(row, raw)
    if floor_strike is not None and cap_strike is not None:
        return True
    if isinstance(strike_type, str) and strike_type.strip().lower() in {"range", "between"}:
        return True
    return any(term in text for term in ("between", "range bucket"))


def _is_threshold_ladder(
    row: dict[str, Any],
    raw: dict[str, Any],
    *,
    floor_strike: Any,
    cap_strike: Any,
    strike_type: str | None,
) -> bool:
    if floor_strike is not None or cap_strike is not None:
        return True
    if isinstance(strike_type, str) and strike_type.strip().lower() in {"greater", "less", "above", "below"}:
        return True
    text = _ladder_text(row, raw)
    return any(term in text for term in ("above", "below", " or above", " or below", "greater than", "less than"))


def _ladder_text(row: dict[str, Any], raw: dict[str, Any]) -> str:
    text = " ".join(str(value or "") for value in (row.get("title"), row.get("question"), raw.get("title"), raw.get("yes_sub_title"), raw.get("subtitle"))).lower()
    return text


def _settlement_source_from_rules(*dicts: dict[str, Any], rules: str | None) -> str | None:
    explicit = _first_string(*dicts, keys=("settlement_source", "resolution_source", "settlement_basis", "settlement_rule_source"))
    if explicit:
        return explicit
    if not isinstance(rules, str):
        return None
    lower = rules.lower()
    source_markers = (
        "published on",
        "resolution source",
        "price used to determine",
        "based on",
        "official website",
        "cf benchmarks",
        "federal reserve",
        "wins the",
    )
    if any(marker in lower for marker in source_markers):
        return rules
    return None


def _resolution_date(row: dict[str, Any], raw: dict[str, Any], event: dict[str, Any]) -> str | None:
    value = _first_string(row, raw, event, keys=("resolution_date", "settlement_date"))
    if value:
        return value
    timing = _first_string(row, raw, event, keys=("expected_expiration_time", "settlement_time", "close_time"))
    if isinstance(timing, str) and len(timing) >= 10:
        return timing[:10]
    return None
