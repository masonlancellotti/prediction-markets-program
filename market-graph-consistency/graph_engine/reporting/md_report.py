from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from graph_engine.models import Action, ConsistencyViolation, GraphSnapshot, ViolationKind
from graph_engine.formula import build_formula_diagnostics_report
from graph_engine.reporting.multi_leg import build_multi_leg_constraints_report
from graph_engine.reporting.safety import find_prohibited_rendered_text
from graph_engine.reporting.schema_validation import SchemaValidationError


def _price_line(snapshot: GraphSnapshot, market_id: str) -> str:
    node = snapshot.nodes[market_id]
    flags = []
    if node.reference_only:
        flags.append("reference_only")
    if flags:
        return f"- `{market_id}` | yes={node.probability:.3f} | as_of={node.as_of.isoformat()} | {', '.join(flags)}"
    return f"- `{market_id}` | yes={node.probability:.3f} | as_of={node.as_of.isoformat()}"


def _highest_action(violations: list[ConsistencyViolation]) -> Action:
    if any(violation.action == Action.MANUAL_REVIEW for violation in violations):
        return Action.MANUAL_REVIEW
    if any(violation.action == Action.WATCH for violation in violations):
        return Action.WATCH
    return Action.IGNORE


def _scope_text(snapshot: GraphSnapshot) -> str:
    if snapshot.notes:
        return snapshot.notes[0]
    return "Offline scan"


def _reference_only_nodes(snapshot: GraphSnapshot) -> list[str]:
    return [market_id for market_id, node in sorted(snapshot.nodes.items()) if node.reference_only]


def _stale_nodes(snapshot: GraphSnapshot, max_node_age_seconds: int = 24 * 60 * 60) -> list[str]:
    return [
        market_id
        for market_id, node in sorted(snapshot.nodes.items())
        if (snapshot.as_of - node.as_of).total_seconds() > max_node_age_seconds
    ]


def build_markdown_report(snapshot: GraphSnapshot, violations: list[ConsistencyViolation]) -> str:
    grouped: dict[ViolationKind, list[ConsistencyViolation]] = defaultdict(list)
    for violation in violations:
        grouped[violation.kind].append(violation)
    multi_leg_report = build_multi_leg_constraints_report(snapshot)
    formula_report = build_formula_diagnostics_report(snapshot)

    lines = [
        "# Graph Consistency Summary",
        "",
        f"- Snapshot: `{snapshot.snapshot_id}`",
        f"- Markets: {len(snapshot.nodes)}",
        f"- Relationships: {len(snapshot.edges)}",
        f"- Exclusion sets: {len(snapshot.exclusion_sets)}",
        f"- Findings: {len(violations)}",
        f"- Multi-leg constraints: {multi_leg_report['constraint_count']}",
        f"- Formula diagnostics: {formula_report['comparison_count']}",
        f"- Formula cluster constraints: {formula_report['formula_cluster_constraint_count']}",
        f"- Highest action: `{_highest_action(violations).value}`",
        f"- Scope: {_scope_text(snapshot)}",
        f"- Diagnostic only: true",
        f"- Reference-only nodes: {len(_reference_only_nodes(snapshot))}",
        f"- Stale nodes: {len(_stale_nodes(snapshot))}",
        "",
    ]
    if len(snapshot.notes) > 1:
        lines.extend(["## Notes", ""])
        lines.extend(f"- {note}" for note in snapshot.notes[1:])
        lines.append("")

    for kind in sorted(grouped, key=lambda item: item.value):
        findings = sorted(grouped[kind], key=lambda item: (-item.rank_score, item.violation_id))
        lines.extend([f"## {kind.value}", ""])
        for violation in findings:
            lines.extend(
                [
                    f"### `{violation.violation_id}`",
                    "",
                    f"- Action: `{violation.action.value}`",
                    f"- Confidence: {violation.confidence:.3f}",
                    f"- Raw gap: {violation.raw_gap:.3f}",
                    f"- Spread-adjusted gap: {violation.spread_adjusted_gap:.3f}",
                    f"- Magnitude: {violation.magnitude:.3f}",
                    "- Magnitude unit: probability",
                    f"- Edge source: `{violation.edge_source or 'unknown'}`",
                    f"- Review status: `{violation.review_status}`",
                    f"- Reviewed by: `{violation.reviewed_by or 'none'}`",
                    f"- Max action cap: `{violation.max_action_cap}` via `{violation.max_action_cap_reason}`",
                    f"- Blockers: {', '.join(violation.blockers) if violation.blockers else 'none'}",
                    "- Involved markets:",
                ]
            )
            lines.extend(_price_line(snapshot, market_id) for market_id in violation.involved_market_ids)
            lines.extend(
                [
                    f"- Explanation: {violation.explanation}",
                    "- Review questions:",
                ]
            )
            lines.extend(f"  - {question}" for question in violation.review_questions)
            lines.append("")

    if not violations:
        lines.extend(["No findings in this snapshot.", ""])

    lines.extend(["## Multi-Leg Constraints", ""])
    if not multi_leg_report["multi_leg_constraints"]:
        lines.extend(["No multi-leg structural inconsistencies in this snapshot.", ""])
    for constraint in multi_leg_report["multi_leg_constraints"]:
        lines.extend(
            [
                f"### `{constraint['constraint_id']}`",
                "",
                f"- Constraint type: `{constraint['constraint_type']}`",
                f"- Constraint family: `{constraint['constraint_family']}`",
                f"- Diagnostic rank: {constraint['diagnostic_rank']}",
                f"- Diagnostic priority: `{constraint['diagnostic_priority']}`",
                f"- Bound gap: {constraint['bound_gap']:.3f}",
                f"- Normalized bound gap: {constraint['normalized_bound_gap']:.3f}",
                f"- Observed value: {constraint['observed_value']:.3f}",
                f"- Expected lower bound: {constraint['expected_lower_bound']:.3f}",
                f"- Expected upper bound: {constraint['expected_upper_bound']:.3f}",
                f"- Confidence basis: {constraint['confidence_basis']['description']} ({constraint['confidence_basis']['score']:.3f})",
                f"- Blockers: {', '.join(constraint['blockers']) if constraint['blockers'] else 'none'}",
                f"- Structural inconsistency: `{str(constraint['structural_inconsistency']).lower()}`",
                "- Involved markets:",
            ]
        )
        lines.extend(_price_line(snapshot, market_id) for market_id in constraint["market_ids"])
        lines.extend([f"- Review reason: {constraint['review_reason']}", "- Required review questions:"])
        lines.extend(f"  - {question}" for question in constraint["required_review_questions"])
        lines.append("")

    lines.extend(["## Formula Diagnostics", ""])
    if not formula_report["formula_diagnostics"]:
        lines.extend(["No formula comparison diagnostics in this snapshot.", ""])
    for diagnostic in formula_report["formula_diagnostics"]:
        lines.extend(
            [
                f"### `{diagnostic['comparison_id']}`",
                "",
                f"- Family: `{diagnostic['family']}`",
                f"- Formula relation: `{diagnostic['formula_relation']}`",
                f"- Diagnostic priority: `{diagnostic['diagnostic_priority']}`",
                f"- Affects evaluator gates: `{str(diagnostic['affects_evaluator_gates']).lower()}`",
                f"- Blockers: {', '.join(diagnostic['blockers']) if diagnostic['blockers'] else 'none'}",
                "- Involved markets:",
            ]
        )
        lines.extend(_price_line(snapshot, market_id) for market_id in diagnostic["market_ids"])
        lines.extend([f"- Review reason: {diagnostic['review_reason']}", ""])

    lines.extend(["## Formula Cluster Constraints", ""])
    if not formula_report["formula_cluster_constraints"]:
        lines.extend(["No derived formula cluster constraints in this snapshot.", ""])
    for constraint in formula_report["formula_cluster_constraints"]:
        lines.extend(
            [
                f"### `{constraint['constraint_id']}`",
                "",
                f"- Constraint type: `{constraint['constraint_type']}`",
                f"- Constraint family: `{constraint['constraint_family']}`",
                f"- Diagnostic rank: {constraint['diagnostic_rank']}",
                f"- Diagnostic priority: `{constraint['diagnostic_priority']}`",
                f"- Affects evaluator gates: `{str(constraint['affects_evaluator_gates']).lower()}`",
                f"- Blockers: {', '.join(constraint['blockers']) if constraint['blockers'] else 'none'}",
                "- Source markets:",
            ]
        )
        lines.extend(f"- `{market_id}`" for market_id in constraint["source_market_ids"])
        lines.extend(
            [
                f"- Review reason: {constraint['reason_for_review']}",
                f"- Exact keys to verify: {', '.join(constraint['requested_exact_keys_to_verify'])}",
                "",
            ]
        )

    return "\n".join(lines)


def write_markdown_report(
    snapshot: GraphSnapshot,
    violations: list[ConsistencyViolation],
    path: Path | str,
) -> None:
    markdown = build_markdown_report(snapshot, violations)
    hits = find_prohibited_rendered_text(markdown)
    if hits:
        raise SchemaValidationError(
            "graph consistency Markdown contains prohibited diagnostic vocabulary: "
            + ", ".join(hits)
        )
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")
