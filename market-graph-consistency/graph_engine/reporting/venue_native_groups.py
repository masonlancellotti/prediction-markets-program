from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from graph_engine.models import GraphSnapshot, MarketNode
from graph_engine.reporting.schema_validation import DIAGNOSTIC_HINT_ACTIONS, SchemaValidationError, _reject_prohibited_tokens


BANNER = "Diagnostic-only venue-native exhaustive group packet. Native completeness must be verified before downstream use."
GROUP_MARKER = "complete"


def build_venue_native_exhaustive_groups_report(
    snapshot: GraphSnapshot,
    source_metadata: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metadata_by_snapshot = {
        str(item.get("source_snapshot_id")): dict(item)
        for item in source_metadata or []
        if item.get("source_snapshot_id") is not None
    }
    grouped: dict[tuple[str, str], list[MarketNode]] = defaultdict(list)
    for node in snapshot.nodes.values():
        group_id = _native_group_id(node)
        if group_id:
            grouped[(node.venue, group_id)].append(node)

    rows = [
        _build_group_row(snapshot, venue, group_id, nodes, metadata_by_snapshot)
        for (venue, group_id), nodes in grouped.items()
    ]
    rows = sorted(rows, key=lambda row: (_priority(row["max_action_cap"]), row["venue"], row["group_id"]))
    counts = Counter(row["max_action_cap"] for row in rows)
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "banner": BANNER,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "group_count": len(rows),
        "counts_by_max_action_cap": dict(sorted(counts.items())),
        "venue_native_exhaustive_groups": rows,
    }
    validate_venue_native_exhaustive_groups_report(report)
    return report


def write_venue_native_exhaustive_groups_report(
    snapshot: GraphSnapshot,
    source_metadata: list[dict[str, Any]] | None,
    json_output: Path | str,
    md_output: Path | str,
) -> dict[str, Any]:
    report = build_venue_native_exhaustive_groups_report(snapshot, source_metadata)
    validate_venue_native_exhaustive_groups_report(report)

    json_path = Path(json_output)
    md_path = Path(md_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_venue_native_exhaustive_groups_markdown(report), encoding="utf-8")
    return report


def validate_venue_native_exhaustive_groups_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("venue-native group report must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("venue-native group report must not affect evaluator gates")
    if report.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError("venue-native group report actions must be WATCH and MANUAL_REVIEW only")

    rows = report.get("venue_native_exhaustive_groups")
    if not isinstance(rows, list):
        raise SchemaValidationError("venue_native_exhaustive_groups must be a list")
    if report.get("group_count") != len(rows):
        raise SchemaValidationError("group_count must match venue_native_exhaustive_groups")
    for index, row in enumerate(rows):
        _validate_group_row(row, f"venue_native_exhaustive_groups[{index}]")


def render_venue_native_exhaustive_groups_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Venue-Native Exhaustive Groups",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Groups: {report['group_count']}",
        "",
        "| Venue | Group | Cap | Markets | Evidence | Blockers | Review Reason |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["venue_native_exhaustive_groups"]:
        evidence = row["completeness_evidence"]
        evidence_text = (
            f"event={evidence.get('event_id')}; "
            f"marker={evidence.get('completeness_marker') or 'missing'}; "
            f"outcomes={', '.join(evidence.get('outcome_list', []))}"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["venue"]),
                    _md(row["group_id"]),
                    _md(row["max_action_cap"]),
                    _md(", ".join(row["market_ids"])),
                    _md(evidence_text),
                    _md(", ".join(row["missing_outcome_blockers"]) or "none"),
                    _md(row["reason_for_review"]),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _build_group_row(
    snapshot: GraphSnapshot,
    venue: str,
    group_id: str,
    nodes: list[MarketNode],
    metadata_by_snapshot: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    sorted_nodes = sorted(nodes, key=lambda node: node.market_id)
    raw_items = [_native_metadata(node) for node in sorted_nodes]
    event_ids = sorted({str(raw.get("venue_native_event_id")) for raw in raw_items if raw.get("venue_native_event_id")})
    event_id = event_ids[0] if len(event_ids) == 1 else None
    outcome_list = _first_list(raw_items, "venue_native_outcome_list")
    outcome_by_market = {
        node.market_id: str(_native_metadata(node).get("venue_native_outcome"))
        for node in sorted_nodes
        if _native_metadata(node).get("venue_native_outcome")
    }
    outcomes_present = sorted(
        set(outcome_by_market.values()),
        key=lambda value: (0, outcome_list.index(value)) if value in outcome_list else (1, value),
    )
    marker_values = sorted({str(raw.get("venue_native_completeness")) for raw in raw_items if raw.get("venue_native_completeness")})
    marker = marker_values[0] if len(marker_values) == 1 else None
    blockers = _group_blockers(event_ids, outcome_list, outcomes_present, marker_values, marker)
    cap = "MANUAL_REVIEW" if not blockers else "WATCH"
    source_snapshot_ids = sorted({str(node.source_snapshot_id) for node in sorted_nodes if node.source_snapshot_id})
    source_fixture_metadata = [
        metadata_by_snapshot[source_id]
        for source_id in source_snapshot_ids
        if source_id in metadata_by_snapshot
    ]
    source_files = sorted(
        {
            str(item.get("file"))
            for item in source_fixture_metadata
            if item.get("file")
        }
    )
    evidence = {
        "event_id": event_id,
        "group_id": group_id,
        "outcome_list": outcome_list,
        "outcomes_present": outcomes_present,
        "completeness_marker": marker,
        "source_snapshot_ids": source_snapshot_ids,
        "source_files": source_files,
        "source_fixture_metadata": source_fixture_metadata,
        "has_other_or_none_outcome": _has_other_or_none(outcome_list),
    }
    reason = (
        "Explicit native event and group metadata with a complete outcome list requires manual review."
        if cap == "MANUAL_REVIEW"
        else "Explicit native group metadata is present, but completeness evidence needs review."
    )
    row = {
        "packet_id": f"venue_native_exhaustive_group:{venue}:{group_id}",
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "max_action_cap": cap,
        "venue": venue,
        "group_id": group_id,
        "market_ids": [node.market_id for node in sorted_nodes],
        "completeness_evidence": evidence,
        "missing_outcome_blockers": blockers,
        "requested_exact_keys_to_verify": [
            "venue",
            "native_event_id",
            "native_group_id",
            "native_outcome",
            "native_outcome_list",
            "native_completeness_marker",
            "resolution_rules",
        ],
        "reason_for_review": reason,
        "snapshot_id": snapshot.snapshot_id,
    }
    _validate_group_row(row, "venue_native_exhaustive_groups[]")
    return row


def _group_blockers(
    event_ids: list[str],
    outcome_list: list[str],
    outcomes_present: list[str],
    marker_values: list[str],
    marker: str | None,
) -> list[str]:
    blockers: list[str] = []
    if len(event_ids) != 1:
        blockers.append("missing_or_mixed_native_event_id")
    if not outcome_list:
        blockers.append("missing_native_outcome_list")
    missing = [outcome for outcome in outcome_list if outcome not in outcomes_present]
    if missing:
        blockers.append("missing_native_outcomes:" + ",".join(missing))
    if not marker_values:
        blockers.append("missing_native_completeness_marker")
    elif len(marker_values) != 1:
        blockers.append("mixed_native_completeness_marker")
    elif marker != GROUP_MARKER:
        blockers.append("native_completeness_marker_not_complete")
    return blockers


def _validate_group_row(row: dict[str, Any], path: str) -> None:
    required = [
        "diagnostic_only",
        "affects_evaluator_gates",
        "allowed_actions",
        "max_action_cap",
        "venue",
        "group_id",
        "market_ids",
        "completeness_evidence",
        "missing_outcome_blockers",
        "requested_exact_keys_to_verify",
        "reason_for_review",
    ]
    for key in required:
        if key not in row:
            raise SchemaValidationError(f"{path}.{key} is required")
    if row["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if row["affects_evaluator_gates"] is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if row["allowed_actions"] != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    if row["max_action_cap"] not in DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.max_action_cap must be WATCH or MANUAL_REVIEW")
    if not isinstance(row["venue"], str) or not row["venue"]:
        raise SchemaValidationError(f"{path}.venue must be a non-empty string")
    if not isinstance(row["group_id"], str) or not row["group_id"]:
        raise SchemaValidationError(f"{path}.group_id must be a non-empty string")
    if not isinstance(row["market_ids"], list) or not row["market_ids"] or not all(isinstance(item, str) for item in row["market_ids"]):
        raise SchemaValidationError(f"{path}.market_ids must contain market ids")
    if not isinstance(row["missing_outcome_blockers"], list) or not all(isinstance(item, str) for item in row["missing_outcome_blockers"]):
        raise SchemaValidationError(f"{path}.missing_outcome_blockers must be a list of strings")
    if not isinstance(row["requested_exact_keys_to_verify"], list) or not row["requested_exact_keys_to_verify"]:
        raise SchemaValidationError(f"{path}.requested_exact_keys_to_verify must contain strings")
    if not all(isinstance(item, str) and item for item in row["requested_exact_keys_to_verify"]):
        raise SchemaValidationError(f"{path}.requested_exact_keys_to_verify must contain strings")
    if not isinstance(row["reason_for_review"], str) or not row["reason_for_review"]:
        raise SchemaValidationError(f"{path}.reason_for_review must be a non-empty string")
    evidence = row["completeness_evidence"]
    if not isinstance(evidence, dict):
        raise SchemaValidationError(f"{path}.completeness_evidence must be an object")
    for evidence_key in ["group_id", "outcome_list", "outcomes_present", "source_snapshot_ids", "source_files"]:
        if evidence_key not in evidence:
            raise SchemaValidationError(f"{path}.completeness_evidence.{evidence_key} is required")
    if evidence.get("group_id") != row["group_id"]:
        raise SchemaValidationError(f"{path}.completeness_evidence.group_id must match row group_id")
    if not isinstance(evidence["outcome_list"], list):
        raise SchemaValidationError(f"{path}.completeness_evidence.outcome_list must be a list")
    if not isinstance(evidence["outcomes_present"], list):
        raise SchemaValidationError(f"{path}.completeness_evidence.outcomes_present must be a list")
    _reject_prohibited_tokens(row)


def _native_group_id(node: MarketNode) -> str | None:
    value = _native_metadata(node).get("venue_native_group_id")
    return str(value) if value else None


def _native_metadata(node: MarketNode) -> dict[str, Any]:
    row = node.raw.get("normalized_row")
    if isinstance(row, dict):
        merged = dict(node.raw)
        merged.update(row)
        return merged
    return dict(node.raw)


def _first_list(raw_items: list[dict[str, Any]], key: str) -> list[str]:
    for raw in raw_items:
        value = raw.get(key)
        if isinstance(value, list):
            return [str(item) for item in value]
    return []


def _has_other_or_none(outcomes: list[str]) -> bool:
    normalized = {outcome.lower().replace(" ", "") for outcome in outcomes}
    return any(outcome in {"other", "none", "other/none", "othernone"} for outcome in normalized)


def _priority(action: str) -> int:
    return {"MANUAL_REVIEW": 0, "WATCH": 1}.get(action, 2)


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
