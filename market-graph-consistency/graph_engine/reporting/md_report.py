from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from graph_engine.models import Action, ConsistencyViolation, GraphSnapshot, ViolationKind


def _price_line(snapshot: GraphSnapshot, market_id: str) -> str:
    node = snapshot.nodes[market_id]
    return f"- `{market_id}`: {node.title} | yes={node.probability:.3f} | as_of={node.as_of.isoformat()}"


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


def build_markdown_report(snapshot: GraphSnapshot, violations: list[ConsistencyViolation]) -> str:
    grouped: dict[ViolationKind, list[ConsistencyViolation]] = defaultdict(list)
    for violation in violations:
        grouped[violation.kind].append(violation)

    lines = [
        "# Graph Consistency Summary",
        "",
        f"- Snapshot: `{snapshot.snapshot_id}`",
        f"- Markets: {len(snapshot.nodes)}",
        f"- Relationships: {len(snapshot.edges)}",
        f"- Exclusion sets: {len(snapshot.exclusion_sets)}",
        f"- Findings: {len(violations)}",
        f"- Highest action: `{_highest_action(violations).value}`",
        f"- Scope: {_scope_text(snapshot)}",
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

    return "\n".join(lines)


def write_markdown_report(
    snapshot: GraphSnapshot,
    violations: list[ConsistencyViolation],
    path: Path | str,
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_markdown_report(snapshot, violations), encoding="utf-8")
