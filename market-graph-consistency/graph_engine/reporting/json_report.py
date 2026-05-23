from __future__ import annotations

import json
from collections import Counter
from datetime import timezone
from pathlib import Path
from typing import Any

from graph_engine.models import ConsistencyViolation, GraphSnapshot, utc_now

PROHIBITED_VIOLATION_FIELDS = {
    "profit",
    "pnl",
    "dollars",
    "fill",
    "size",
    "edge_bps",
    "executable",
    "paper",
    "possible_arb",
}


def _stale_nodes(snapshot: GraphSnapshot, max_node_age_seconds: int = 24 * 60 * 60) -> list[str]:
    return [
        market_id
        for market_id, node in sorted(snapshot.nodes.items())
        if (snapshot.as_of - node.as_of).total_seconds() > max_node_age_seconds
    ]


def _reference_only_nodes(snapshot: GraphSnapshot) -> list[str]:
    return [market_id for market_id, node in sorted(snapshot.nodes.items()) if node.reference_only]


def _reviewers(snapshot: GraphSnapshot) -> list[str]:
    return sorted({edge.reviewed_by for edge in snapshot.edges if edge.reviewed_by})


def _assert_safe_violation_schema(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        keys = {str(key).lower() for key in row}
        forbidden = sorted(keys & PROHIBITED_VIOLATION_FIELDS)
        if forbidden:
            raise ValueError(f"prohibited violation fields present: {forbidden}")


def build_json_report(
    snapshot: GraphSnapshot,
    violations: list[ConsistencyViolation],
    fixture_metadata: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    kind_counts = Counter(violation.kind.value for violation in violations)
    action_counts = Counter(violation.action.value for violation in violations)
    edge_source_counts = Counter(edge.source.value for edge in snapshot.edges)
    cap_counts = Counter(violation.max_action_cap_reason for violation in violations)
    violation_rows = [violation.to_dict() for violation in violations]
    _assert_safe_violation_schema(violation_rows)
    return {
        "generated_at": utc_now().astimezone(timezone.utc).isoformat(),
        "snapshot_id": snapshot.snapshot_id,
        "notes": list(snapshot.notes),
        "diagnostic_only": True,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "summary": {
            "market_count": len(snapshot.nodes),
            "edge_count": len(snapshot.edges),
            "exclusion_set_count": len(snapshot.exclusion_sets),
            "violation_count": len(violations),
            "counts_by_kind": dict(sorted(kind_counts.items())),
            "counts_by_action": dict(sorted(action_counts.items())),
            "highest_action": "MANUAL_REVIEW" if any(v.action.value == "MANUAL_REVIEW" for v in violations) else "WATCH" if violations else "IGNORE",
            "edge_sources": dict(sorted(edge_source_counts.items())),
            "reviewed_by": _reviewers(snapshot),
            "reference_only_node_count": len(_reference_only_nodes(snapshot)),
            "reference_only_nodes": _reference_only_nodes(snapshot),
            "stale_node_count": len(_stale_nodes(snapshot)),
            "stale_nodes": _stale_nodes(snapshot),
            "max_action_cap_reasons": dict(sorted(cap_counts.items())),
        },
        "violations": violation_rows,
        "source_fixture_metadata": fixture_metadata or [],
    }


def write_json_report(
    snapshot: GraphSnapshot,
    violations: list[ConsistencyViolation],
    path: Path | str,
    fixture_metadata: list[dict[str, Any]] | None = None,
) -> None:
    report = build_json_report(snapshot, violations, fixture_metadata)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
