from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.exhaustive_evidence_trust import exhaustive_evidence_trust_blockers, has_reference_only_flag


SOURCE = "kalshi_event_metadata"


def audit_kalshi_native_groups(snapshot_payload: dict[str, Any], *, generated_at: datetime | None = None) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    records = [_extract_record(row, event_meta) for row, event_meta in _iter_market_records(snapshot_payload)]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["venue_native_group_id"] or f"title_only:{record.get('event_title') or record.get('title') or record['market_ticker']}"].append(record)
    groups = [_audit_group(group_id, rows) for group_id, rows in sorted(grouped.items())]
    candidate_rows = [candidate for group in groups for candidate in group["structural_basket_input_rows"]]
    status_counts = Counter(group["status"] for group in groups)
    return {
        "schema_version": 1,
        "source": "kalshi_native_group_saved_snapshot_audit_v1",
        "generated_at": generated.isoformat(),
        "summary": {
            "groups_discovered": len(groups),
            "complete_groups": sum(1 for group in groups if group["status"] == "COMPLETE_EXHAUSTIVE_GROUP"),
            "incomplete_groups": sum(1 for group in groups if group["status"] == "INCOMPLETE_GROUP"),
            "blocked_groups": sum(1 for group in groups if group["blockers"]),
            "candidate_input_row_count": len(candidate_rows),
            "paper_candidate_count": 0,
            "stop_for_review_count": 0,
            "status_counts": dict(sorted(status_counts.items())),
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


def audit_kalshi_native_groups_file(*, snapshot_path: Path, json_output: Path, markdown_output: Path | None = None) -> dict[str, Any]:
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    report = audit_kalshi_native_groups(payload)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_kalshi_native_groups_markdown(report), encoding="utf-8")
    return report


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
                status=group.get("status") or "",
                markets=group.get("market_count") or 0,
                outcomes=len(group.get("outcome_list") or []),
                blockers="; ".join(group.get("blockers") or []).replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"


def _audit_group(group_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    first = rows[0] if rows else {}
    outcome_list = _first_list(*(row.get("outcome_list") for row in rows))
    completeness = _first_bool(*(row.get("completeness") for row in rows))
    blockers: list[str] = []
    if not first.get("venue_native_event_id"):
        blockers.append("missing_venue_native_event_id")
    if not first.get("venue_native_group_id"):
        blockers.append("missing_venue_native_group_id")
    if outcome_list is None:
        blockers.extend(["missing_outcome_list", "partial_event_metadata"])
    if completeness is not True:
        blockers.extend(["missing_completeness_evidence", "partial_event_metadata"])
    if any(row.get("title_only_group") for row in rows):
        blockers.append("title_only_group_not_trusted")
    if any(row.get("reference_only") for row in rows):
        blockers.append("reference_only_source")
    if outcome_list is not None and len(rows) != len(outcome_list):
        blockers.append("partial_event_metadata")
    blockers.extend(
        exhaustive_evidence_trust_blockers(
            source=SOURCE,
            is_exhaustive=completeness is True,
            venue_native=bool(first.get("venue_native_event_id") and first.get("venue_native_group_id")),
            trusted_local_manifest=False,
        )
    )
    blockers = sorted(set(blockers))
    group = {
        "venue": "kalshi",
        "venue_native_event_id": first.get("venue_native_event_id"),
        "venue_native_group_id": first.get("venue_native_group_id") or group_id,
        "source": SOURCE,
        "venue_native": bool(first.get("venue_native_event_id") and first.get("venue_native_group_id")),
        "status": "COMPLETE_EXHAUSTIVE_GROUP" if not blockers else "INCOMPLETE_GROUP",
        "blockers": blockers,
        "market_count": len(rows),
        "outcome_list": outcome_list or [],
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
        "settlement_time": row.get("settlement_time"),
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
    event = event_meta or row.get("event") if isinstance(row.get("event"), dict) else event_meta
    event = event or {}
    event_id = _first_string(row, raw, event, keys=("venue_native_event_id", "event_id", "event_ticker", "id"))
    group_id = _first_string(row, raw, event, keys=("venue_native_group_id", "group_id", "event_ticker", "series_ticker"))
    title = _first_string(row, raw, event, keys=("question", "title", "market_title", "event_title", "name"))
    return {
        "venue": "kalshi",
        "venue_native_event_id": event_id,
        "venue_native_group_id": group_id,
        "market_ticker": _first_string(row, raw, keys=("market_ticker", "ticker")) or "",
        "outcome": _first_string(row, raw, keys=("outcome", "outcome_label", "yes_sub_title", "subtitle", "sub_title")),
        "outcome_list": _first_list(
            _list_from(row.get("outcome_list")),
            _list_from(raw.get("outcome_list")),
            _list_from(event.get("outcome_list")),
            _list_from(event.get("outcomes")),
        ),
        "completeness": _first_bool(
            row.get("is_exhaustive"),
            row.get("all_outcomes_included"),
            raw.get("is_exhaustive"),
            raw.get("all_outcomes_included"),
            event.get("is_exhaustive"),
            event.get("all_outcomes_included"),
            _completeness_bool(row.get("completeness")),
            _completeness_bool(raw.get("completeness")),
            _completeness_bool(event.get("completeness")),
        ),
        "rules": _first_string(row, raw, keys=("rules", "rules_primary", "resolution_text", "description")),
        "title": title,
        "event_title": _first_string(row, raw, event, keys=("event_title", "title", "name")),
        "close_time": _first_string(row, raw, keys=("close_time", "expected_expiration_time")),
        "settlement_time": _first_string(row, raw, keys=("settlement_time", "expected_expiration_time")),
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


def _first_string(*dicts: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for item in dicts:
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


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
