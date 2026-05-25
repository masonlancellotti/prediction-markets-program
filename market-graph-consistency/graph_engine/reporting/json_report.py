from __future__ import annotations

import json
from collections import Counter
from datetime import timezone
from pathlib import Path
from typing import Any

from graph_engine.models import ConsistencyViolation, GraphSnapshot, utc_now
from graph_engine.formula import build_formula_diagnostics_report
from graph_engine.reporting.multi_leg import build_multi_leg_constraints_report
from graph_engine.reporting.schema_validation import (
    validate_formula_diagnostics_contract,
    validate_multi_leg_constraints_contract,
)
from graph_engine.reporting.safety import PROHIBITED_REPORT_TOKENS, find_prohibited_report_keys

PROHIBITED_VIOLATION_FIELDS = PROHIBITED_REPORT_TOKENS


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


def _assert_safe_violation_schema(payload: Any) -> None:
    forbidden = find_prohibited_report_keys(payload)
    if forbidden:
        raise ValueError(f"prohibited violation fields present: {sorted(forbidden)}")


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
    multi_leg_report = build_multi_leg_constraints_report(snapshot)
    formula_report = build_formula_diagnostics_report(snapshot)
    report = {
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
            "multi_leg_constraint_count": multi_leg_report["constraint_count"],
            "formula_diagnostic_count": formula_report["comparison_count"],
            "formula_cluster_constraint_count": formula_report["formula_cluster_constraint_count"],
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
        "multi_leg_constraints": multi_leg_report,
        "formula_diagnostics": formula_report,
        "source_fixture_metadata": fixture_metadata or [],
    }
    _assert_safe_violation_schema(report)
    return report


def write_json_report(
    snapshot: GraphSnapshot,
    violations: list[ConsistencyViolation],
    path: Path | str,
    fixture_metadata: list[dict[str, Any]] | None = None,
) -> None:
    report = build_json_report(snapshot, violations, fixture_metadata)
    validate_multi_leg_constraints_contract(report["multi_leg_constraints"])
    validate_formula_diagnostics_contract(report["formula_diagnostics"])
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
