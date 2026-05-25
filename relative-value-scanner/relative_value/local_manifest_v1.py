from __future__ import annotations

from datetime import datetime
from typing import Any

from relative_value.exhaustive_evidence_trust import has_reference_only_flag


LOCAL_MANIFEST_SOURCE = "local_manifest_v1"

MANIFEST_BLOCKERS = frozenset(
    {
        "manifest_source_not_local_manifest_v1",
        "trusted_local_manifest_required",
        "missing_manifest_reviewer",
        "missing_manifest_reviewed_at",
        "invalid_manifest_reviewed_at",
        "missing_manifest_venue",
        "missing_manifest_group_id",
        "missing_manifest_market_tickers",
        "missing_manifest_outcome_list",
        "incomplete_manifest_outcome_list",
        "manifest_market_tickers_absent_from_snapshot",
        "manifest_not_marked_complete",
        "missing_manifest_evidence_text",
        "missing_manifest_settlement_source_evidence",
        "missing_manifest_rules_evidence",
        "title_only_manifest_evidence",
        "graph_hint_manifest_evidence",
        "reference_only_source",
    }
)

_TITLE_ONLY_MARKERS = (
    "title_only",
    "title-only",
    "title match",
    "title_match",
    "title similarity",
    "title_similarity",
    "inferred_from_title",
    "inferred from title",
)

_GRAPH_HINT_MARKERS = (
    "graph_hint",
    "graph hint",
    "market_graph",
    "market graph",
)


def validate_local_manifest_v1_group(group: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if has_reference_only_flag(group):
        blockers.append("reference_only_source")
    if _string_or_none(group.get("source") or group.get("evidence_source")) != LOCAL_MANIFEST_SOURCE:
        blockers.append("manifest_source_not_local_manifest_v1")
    if group.get("trusted_local_manifest") is not True:
        blockers.append("trusted_local_manifest_required")
    if not _string_or_none(group.get("reviewer")):
        blockers.append("missing_manifest_reviewer")
    reviewed_at = _string_or_none(group.get("reviewed_at"))
    if not reviewed_at:
        blockers.append("missing_manifest_reviewed_at")
    elif not _looks_like_datetime(reviewed_at):
        blockers.append("invalid_manifest_reviewed_at")
    if not _string_or_none(group.get("venue")):
        blockers.append("missing_manifest_venue")
    if not _manifest_group_id(group):
        blockers.append("missing_manifest_group_id")
    market_tickers = manifest_market_tickers(group)
    outcome_list = manifest_outcome_list(group)
    if not market_tickers:
        blockers.append("missing_manifest_market_tickers")
    if not outcome_list:
        blockers.append("missing_manifest_outcome_list")
    if market_tickers and outcome_list and len(set(market_tickers)) != len(set(outcome_list)):
        blockers.append("incomplete_manifest_outcome_list")
    if not _manifest_marked_complete(group):
        blockers.append("manifest_not_marked_complete")
    if not _evidence_text_or_notes(group):
        blockers.append("missing_manifest_evidence_text")
    if not _settlement_source_evidence(group):
        blockers.append("missing_manifest_settlement_source_evidence")
    if not _rules_evidence(group):
        blockers.append("missing_manifest_rules_evidence")
    evidence_text = _manifest_evidence_text(group)
    if _contains_any(evidence_text, _TITLE_ONLY_MARKERS):
        blockers.append("title_only_manifest_evidence")
    if _contains_any(evidence_text, _GRAPH_HINT_MARKERS):
        blockers.append("graph_hint_manifest_evidence")
    return sorted(set(blockers))


def manifest_market_tickers(group: dict[str, Any]) -> list[str]:
    return _string_list(
        group.get("market_tickers")
        or group.get("exact_market_tickers")
        or group.get("outcome_market_tickers")
        or group.get("outcome_market_ids")
        or group.get("market_ids")
    )


def manifest_outcome_list(group: dict[str, Any]) -> list[str]:
    return _string_list(group.get("outcome_list") or group.get("outcomes") or group.get("complete_outcome_list"))


def local_manifest_group_metadata(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "manifest_id": group.get("manifest_id") or group.get("id"),
        "reviewer": group.get("reviewer"),
        "reviewed_at": group.get("reviewed_at"),
        "market_tickers": manifest_market_tickers(group),
        "outcome_list": manifest_outcome_list(group),
        "evidence_text": _evidence_text_or_notes(group),
        "settlement_source_evidence": _settlement_source_evidence(group),
        "rules_evidence": _rules_evidence(group),
    }


def _manifest_group_id(group: dict[str, Any]) -> str | None:
    return _string_or_none(
        group.get("group_id")
        or group.get("event_id")
        or group.get("venue_native_group_id")
        or group.get("venue_native_event_id")
    )


def _manifest_marked_complete(group: dict[str, Any]) -> bool:
    return group.get("complete") is True or group.get("is_exhaustive") is True or group.get("exhaustive") is True


def _settlement_source_evidence(group: dict[str, Any]) -> str | None:
    return _string_or_none(
        group.get("settlement_source_evidence")
        or group.get("settlement_source_raw_evidence")
        or group.get("resolution_source_evidence")
        or group.get("resolution_source_raw_evidence")
    )


def _evidence_text_or_notes(group: dict[str, Any]) -> str | None:
    return _string_or_none(group.get("evidence_text") or group.get("evidence_notes") or group.get("evidence"))


def _rules_evidence(group: dict[str, Any]) -> str | None:
    return _string_or_none(
        group.get("rules_evidence")
        or group.get("resolution_rules_evidence")
        or group.get("resolution_criteria_evidence")
    )


def _manifest_evidence_text(group: dict[str, Any]) -> str:
    parts = []
    for key in (
        "evidence",
        "evidence_text",
        "evidence_notes",
        "evidence_detail",
        "evidence_type",
        "evidence_source_detail",
        "settlement_source_evidence",
        "settlement_source_raw_evidence",
        "resolution_source_evidence",
        "rules_evidence",
        "resolution_rules_evidence",
        "resolution_criteria_evidence",
    ):
        value = group.get(key)
        if isinstance(value, str):
            parts.append(value)
    return " ".join(parts).lower()


def _contains_any(value: str, markers: tuple[str, ...]) -> bool:
    return any(marker in value for marker in markers)


def _looks_like_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
