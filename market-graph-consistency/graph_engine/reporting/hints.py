from __future__ import annotations

import json
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any

from graph_engine.models import ConsistencyViolation, ExclusionCompleteness, GraphSnapshot, RelationshipEdge, RelationshipType, ViolationKind
from graph_engine.reporting.json_report import _assert_safe_violation_schema


BANNER = "Research-only graph hint. Not paper-trade permission."
ALLOWED_ACTIONS = ["WATCH", "MANUAL_REVIEW"]


def build_relative_value_hints_report(snapshot: GraphSnapshot, violations: list[ConsistencyViolation]) -> dict[str, Any]:
    hints = _build_hints(snapshot, violations)
    _assert_safe_violation_schema(hints)
    relation_counts = Counter(hint["relation_type"] for hint in hints)
    action_counts = Counter(hint["max_action_cap"] for hint in hints)
    return {
        "diagnostic_only": True,
        "banner": BANNER,
        "snapshot_id": snapshot.snapshot_id,
        "allowed_actions": ALLOWED_ACTIONS,
        "hint_count": len(hints),
        "counts_by_relation_type": dict(sorted(relation_counts.items())),
        "counts_by_max_action_cap": dict(sorted(action_counts.items())),
        "hints": hints,
    }


def write_relative_value_hints_report(
    snapshot: GraphSnapshot,
    violations: list[ConsistencyViolation],
    json_path: Path | str,
    markdown_path: Path | str,
) -> None:
    report = build_relative_value_hints_report(snapshot, violations)
    json_output = Path(json_path)
    md_output = Path(markdown_path)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_output.write_text(render_relative_value_hints_markdown(report), encoding="utf-8")


def render_relative_value_hints_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Market Graph Relative Value Hints",
        "",
        report["banner"],
        "",
        f"- Snapshot: `{report['snapshot_id']}`",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Hints: {report['hint_count']}",
        "",
        "| Hint | Relation | Cap | Source | Target | Direction | Bound | Blockers |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for hint in report["hints"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(hint["graph_hint_id"]),
                    _md(hint["relation_type"]),
                    _md(hint["max_action_cap"]),
                    _md(hint["source_market_id"]),
                    _md(hint["target_market_id"]),
                    _md(hint["direction"]),
                    _md(hint.get("hard_bound_type")),
                    _md(", ".join(hint.get("blockers") or []) or "none"),
                ]
            )
            + " |"
        )
    lines.extend(["", "These hints are not evaluator input and do not grant execution permission.", ""])
    return "\n".join(lines)


def _build_hints(snapshot: GraphSnapshot, violations: list[ConsistencyViolation]) -> list[dict[str, Any]]:
    edge_by_id = {edge.edge_id: edge for edge in snapshot.edges}
    exclusion_by_id = {exclusion.set_id: exclusion for exclusion in snapshot.exclusion_sets}
    hints: list[dict[str, Any]] = []
    for violation in violations:
        if violation.kind == ViolationKind.SUM_OVER_ONE:
            exclusion_id = violation.violation_id.split(":", 1)[1]
            exclusion = exclusion_by_id.get(exclusion_id)
            if exclusion is None:
                continue
            for source_id, target_id in combinations(exclusion.member_market_ids, 2):
                hints.append(_exclusion_hint(violation, exclusion, source_id, target_id))
            continue

        edge = edge_by_id.get(violation.involved_edge_ids[0]) if violation.involved_edge_ids else None
        if edge is None:
            continue
        hints.append(_edge_hint(violation, edge))
    return sorted(hints, key=lambda item: item["graph_hint_id"])


def _edge_hint(violation: ConsistencyViolation, edge: RelationshipEdge) -> dict[str, Any]:
    return {
        "graph_hint_id": f"hint:{violation.violation_id}",
        "source_market_id": edge.src_market_id,
        "target_market_id": edge.dst_market_id,
        "relation_type": _relation_type(edge.relation, violation),
        "direction": _direction(edge.relation),
        "hard_bound_type": _hard_bound_type(edge.relation, violation),
        "diagnostic_only": True,
        "allowed_actions": ALLOWED_ACTIONS,
        "max_action_cap": violation.max_action_cap,
        "max_action_cap_reason": violation.max_action_cap_reason,
        "blockers": list(violation.blockers),
        "edge_source": edge.source.value,
        "reviewed_by": edge.reviewed_by,
        "banner": BANNER,
    }


def _exclusion_hint(
    violation: ConsistencyViolation,
    exclusion,
    source_market_id: str,
    target_market_id: str,
) -> dict[str, Any]:
    complete = exclusion.completeness == ExclusionCompleteness.PARTITION
    blockers = list(violation.blockers)
    if not complete:
        blockers.append("exhaustive_group_not_complete")
    return {
        "graph_hint_id": f"hint:{violation.violation_id}:{source_market_id}->{target_market_id}",
        "source_market_id": source_market_id,
        "target_market_id": target_market_id,
        "relation_type": "EXHAUSTIVE_GROUP" if complete else "MUTUALLY_EXCLUSIVE",
        "direction": "group_level",
        "hard_bound_type": "sum_to_one_only_if_complete" if complete else "mutual_exclusion_sum_only",
        "diagnostic_only": True,
        "allowed_actions": ALLOWED_ACTIONS,
        "max_action_cap": violation.max_action_cap,
        "max_action_cap_reason": violation.max_action_cap_reason,
        "blockers": sorted(set(blockers)),
        "edge_source": violation.edge_source or "manual",
        "reviewed_by": violation.reviewed_by,
        "banner": BANNER,
    }


def _relation_type(relation: RelationshipType, violation: ConsistencyViolation) -> str:
    if violation.kind == ViolationKind.AMBIGUOUS_WORDING:
        return "MANUAL_REVIEW"
    if relation == RelationshipType.SAME_EVENT_REWORDED:
        return "SAME_PAYOFF"
    if relation == RelationshipType.IMPLICATION:
        return "SUBSET"
    if relation == RelationshipType.SUBSET:
        return "SUBSET"
    if relation == RelationshipType.SUPERSET:
        return "SUPERSET"
    return "MANUAL_REVIEW"


def _direction(relation: RelationshipType) -> str:
    if relation in {RelationshipType.IMPLICATION, RelationshipType.SUBSET}:
        return "source_implies_target"
    if relation == RelationshipType.SUPERSET:
        return "target_implies_source"
    if relation == RelationshipType.SAME_EVENT_REWORDED:
        return "bidirectional"
    return "none"


def _hard_bound_type(relation: RelationshipType, violation: ConsistencyViolation) -> str:
    if violation.kind == ViolationKind.AMBIGUOUS_WORDING:
        return "none"
    if relation in {RelationshipType.IMPLICATION, RelationshipType.SUBSET, RelationshipType.SUPERSET}:
        return "upper_probability_bound"
    if relation == RelationshipType.SAME_EVENT_REWORDED:
        return "same_payoff_equality_if_settlement_proven"
    return "none"


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")
