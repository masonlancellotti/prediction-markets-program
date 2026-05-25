from __future__ import annotations

import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

from graph_engine.models import GraphSnapshot, MarketNode
from graph_engine.reporting.schema_validation import DIAGNOSTIC_HINT_ACTIONS, SchemaValidationError, _reject_prohibited_tokens


DEFAULT_TOLERANCE = 0.03
SUPPORTED_FAMILIES = {
    "exhaustive_group",
    "mutually_exclusive_group",
    "child_parent",
    "threshold_ladder",
}
FEASIBILITY_STATUSES = {"feasible", "infeasible", "blocked"}
BANNER = "Diagnostic-only no_arb_consistency feasibility report over fixture-defined finite states."


def build_bounded_noarb_report(snapshot: GraphSnapshot) -> dict[str, Any]:
    families: dict[str, list[MarketNode]] = defaultdict(list)
    for node in snapshot.nodes.values():
        family_id = _family_id(node)
        if family_id:
            families[family_id].append(node)

    diagnostics = [_family_diagnostic(snapshot, family_id, nodes) for family_id, nodes in sorted(families.items())]
    diagnostics = sorted(
        diagnostics,
        key=lambda item: (
            _priority(item["max_action_cap"]),
            -item["normalized_bound_gap"],
            item["family_id"],
        ),
    )
    for index, diagnostic in enumerate(diagnostics, start=1):
        diagnostic["diagnostic_rank"] = index

    counts = Counter(item["feasibility_status"] for item in diagnostics)
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "banner": BANNER,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "diagnostic_count": len(diagnostics),
        "counts_by_feasibility_status": dict(sorted(counts.items())),
        "no_arb_consistency_diagnostics": diagnostics,
    }
    validate_bounded_noarb_report(report)
    return report


def write_bounded_noarb_report(
    snapshot: GraphSnapshot,
    json_output: Path | str,
    md_output: Path | str,
) -> dict[str, Any]:
    report = build_bounded_noarb_report(snapshot)
    validate_bounded_noarb_report(report)

    json_path = Path(json_output)
    md_path = Path(md_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_bounded_noarb_markdown(report), encoding="utf-8")
    return report


def validate_bounded_noarb_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("bounded no_arb_consistency report must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("bounded no_arb_consistency report must not affect evaluator gates")
    if report.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError("bounded no_arb_consistency actions must be WATCH and MANUAL_REVIEW only")
    diagnostics = report.get("no_arb_consistency_diagnostics")
    if not isinstance(diagnostics, list):
        raise SchemaValidationError("no_arb_consistency_diagnostics must be a list")
    if report.get("diagnostic_count") != len(diagnostics):
        raise SchemaValidationError("diagnostic_count must match no_arb_consistency_diagnostics")
    for index, diagnostic in enumerate(diagnostics):
        _validate_diagnostic(diagnostic, f"no_arb_consistency_diagnostics[{index}]")


def render_bounded_noarb_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Bounded no_arb_consistency Diagnostics",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Diagnostics: {report['diagnostic_count']}",
        "",
        "| Family | Type | Status | Cap | States | Contracts | Bound Gap | Violations | Blockers |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in report["no_arb_consistency_diagnostics"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(item["family_id"]),
                    _md(item["family_type"]),
                    _md(item["feasibility_status"]),
                    _md(item["max_action_cap"]),
                    _md(item["state_count"]),
                    _md(item["contract_count"]),
                    _md(item["bound_gap"]),
                    _md(", ".join(item["violated_constraints"]) or "none"),
                    _md(", ".join(item["blockers"]) or "none"),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _family_diagnostic(snapshot: GraphSnapshot, family_id: str, nodes: list[MarketNode]) -> dict[str, Any]:
    ordered_nodes = sorted(nodes, key=lambda node: node.market_id)
    metadata = [_metadata(node) for node in ordered_nodes]
    family_type = _first_text(metadata, "no_arb_family_type") or "unknown"
    states = _first_list(metadata, "no_arb_states")
    blockers = _base_blockers(ordered_nodes, metadata, states, family_type)
    if family_type == "child_parent":
        blockers.extend(_child_parent_blockers(metadata))
    if family_type == "threshold_ladder":
        blockers.extend(_threshold_ladder_blockers(metadata))
    blockers = sorted(set(blockers))

    if blockers:
        return _diagnostic(
            snapshot=snapshot,
            family_id=family_id,
            family_type=family_type,
            nodes=ordered_nodes,
            states=states,
            feasibility_status="blocked",
            violated_constraints=[],
            bound_gap=0.0,
            confidence_score=0.2,
            confidence_description="Finite-state feasibility check blocked by missing or ambiguous fixture definitions.",
            blockers=blockers,
        )

    probabilities = [float(node.probability) for node in ordered_nodes]
    state_vectors = _state_vectors(metadata, states)
    feasible, residual_gap = _convex_hull_feasible(state_vectors, probabilities, DEFAULT_TOLERANCE)
    family_violations, family_gap = _family_violations(family_type, ordered_nodes, metadata)
    violated_constraints = list(family_violations)
    hull_gap = max(0.0, residual_gap - DEFAULT_TOLERANCE)
    if not feasible:
        violated_constraints.insert(0, "finite_state_feasibility")
    bound_gap = max(hull_gap, family_gap)
    status = "feasible" if feasible and not family_violations else "infeasible"
    confidence = 0.88 if status == "infeasible" else 0.78
    return _diagnostic(
        snapshot=snapshot,
        family_id=family_id,
        family_type=family_type,
        nodes=ordered_nodes,
        states=states,
        feasibility_status=status,
        violated_constraints=sorted(set(violated_constraints)),
        bound_gap=bound_gap,
        confidence_score=confidence,
        confidence_description="Fixture-defined finite-state payoff matrix with bounded feasibility check.",
        blockers=[],
    )


def _diagnostic(
    *,
    snapshot: GraphSnapshot,
    family_id: str,
    family_type: str,
    nodes: list[MarketNode],
    states: list[str],
    feasibility_status: str,
    violated_constraints: list[str],
    bound_gap: float,
    confidence_score: float,
    confidence_description: str,
    blockers: list[str],
) -> dict[str, Any]:
    action = "MANUAL_REVIEW" if feasibility_status == "infeasible" and confidence_score >= 0.6 else "WATCH"
    market_ids = [node.market_id for node in nodes]
    row = {
        "diagnostic_id": f"no_arb_consistency:{family_id}",
        "family_id": family_id,
        "family_type": family_type,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "max_action_cap": action,
        "diagnostic_priority": action,
        "feasibility_status": feasibility_status,
        "violated_constraints": list(violated_constraints),
        "bound_gap": round(max(0.0, bound_gap), 6),
        "normalized_bound_gap": round(max(0.0, bound_gap), 6),
        "state_count": len(states),
        "state_ids": list(states),
        "contract_count": len(nodes),
        "market_ids": market_ids,
        "state_payoff_matrix": _payoff_matrix(nodes, states),
        "confidence_basis": {
            "description": confidence_description,
            "score": round(max(0.0, min(1.0, confidence_score)), 6),
        },
        "blockers": list(blockers),
        "required_review_questions": [
            "Do fixture states cover the full event family under one settlement source?",
            "Do contract payoff vectors match native resolution rules?",
            "Do all contracts share compatible settlement timing and source?",
        ],
        "reason_for_review": _reason(feasibility_status),
        "snapshot_id": snapshot.snapshot_id,
    }
    _validate_diagnostic(row, "no_arb_consistency_diagnostics[]")
    return row


def _base_blockers(
    nodes: list[MarketNode],
    metadata: list[dict[str, Any]],
    states: list[str],
    family_type: str,
) -> list[str]:
    blockers: list[str] = []
    if family_type not in SUPPORTED_FAMILIES:
        blockers.append("unsupported_family_type")
    if len(nodes) < 3 or len(nodes) > 8:
        blockers.append("contract_count_outside_supported_range")
    if not states:
        blockers.append("missing_state_definitions")
    for node, raw in zip(nodes, metadata):
        try:
            node.probability
        except ValueError:
            blockers.append(f"missing_probability:{node.market_id}")
        payoffs = raw.get("no_arb_payoffs")
        if not isinstance(payoffs, dict):
            blockers.append(f"missing_payoff_vector:{node.market_id}")
            continue
        for state in states:
            if state not in payoffs:
                blockers.append(f"missing_state_payoff:{node.market_id}:{state}")
            elif not isinstance(payoffs[state], (int, float)) or isinstance(payoffs[state], bool):
                blockers.append(f"non_numeric_state_payoff:{node.market_id}:{state}")
    return blockers


def _child_parent_blockers(metadata: list[dict[str, Any]]) -> list[str]:
    roles = [raw.get("no_arb_contract_role") for raw in metadata]
    blockers: list[str] = []
    if roles.count("child") != 1:
        blockers.append("missing_child_contract_role")
    if roles.count("parent") != 1:
        blockers.append("missing_parent_contract_role")
    return blockers


def _threshold_ladder_blockers(metadata: list[dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    thresholds = [raw.get("no_arb_threshold") for raw in metadata]
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in thresholds):
        blockers.append("missing_numeric_threshold")
    return blockers


def _family_violations(
    family_type: str,
    nodes: list[MarketNode],
    metadata: list[dict[str, Any]],
) -> tuple[list[str], float]:
    probabilities = [float(node.probability) for node in nodes]
    if family_type == "exhaustive_group":
        observed = sum(probabilities)
        gap = max(0.0, abs(observed - 1.0) - DEFAULT_TOLERANCE)
        return (["exhaustive_sum_bound"] if gap > 0 else []), gap
    if family_type == "mutually_exclusive_group":
        observed = sum(probabilities)
        gap = max(0.0, observed - 1.0 - DEFAULT_TOLERANCE)
        return (["mutual_exclusion_upper_bound"] if gap > 0 else []), gap
    if family_type == "child_parent":
        by_role = {str(raw.get("no_arb_contract_role")): node for node, raw in zip(nodes, metadata)}
        gap = max(0.0, by_role["child"].probability - by_role["parent"].probability - DEFAULT_TOLERANCE)
        return (["child_parent_bound"] if gap > 0 else []), gap
    if family_type == "threshold_ladder":
        ordered = sorted(zip(nodes, metadata), key=lambda item: float(item[1]["no_arb_threshold"]), reverse=True)
        gap = 0.0
        for stricter, looser in zip(ordered, ordered[1:]):
            gap = max(gap, stricter[0].probability - looser[0].probability - DEFAULT_TOLERANCE)
        return (["threshold_ladder_bound"] if gap > 0 else []), gap
    return [], 0.0


def _convex_hull_feasible(
    state_vectors: list[list[float]],
    target: list[float],
    tolerance: float,
) -> tuple[bool, float]:
    if not state_vectors:
        return False, 1.0
    state_count = len(state_vectors)
    contract_count = len(target)
    rows = [[1.0 for _ in range(state_count)]]
    rows.extend([[state_vectors[state_index][contract_index] for state_index in range(state_count)] for contract_index in range(contract_count)])
    rhs = [1.0, *target]
    best_residual = float("inf")
    max_subset = min(state_count, contract_count + 1)

    for subset_width in range(1, max_subset + 1):
        for state_subset in combinations(range(state_count), subset_width):
            for row_subset in combinations(range(len(rows)), subset_width):
                square = [[rows[row_index][state_index] for state_index in state_subset] for row_index in row_subset]
                selected_rhs = [rhs[row_index] for row_index in row_subset]
                weights = _solve_square(square, selected_rhs)
                if weights is None:
                    continue
                residual = _residual(rows, rhs, state_subset, weights)
                weight_deficit = max(0.0, -min(weights))
                best_residual = min(best_residual, max(residual, weight_deficit))
                if residual <= tolerance and min(weights) >= -tolerance:
                    return True, residual

    if best_residual == float("inf"):
        return False, 1.0
    return False, best_residual


def _solve_square(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    n = len(rhs)
    augmented = [list(row) + [float(value)] for row, value in zip(matrix, rhs)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        pivot_value = augmented[column][column]
        augmented[column] = [value / pivot_value for value in augmented[column]]
        for row in range(n):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                current - factor * pivot_current
                for current, pivot_current in zip(augmented[row], augmented[column])
            ]
    return [augmented[row][-1] for row in range(n)]


def _residual(
    rows: list[list[float]],
    rhs: list[float],
    state_subset: tuple[int, ...],
    weights: list[float],
) -> float:
    worst = 0.0
    for row, expected in zip(rows, rhs):
        observed = sum(row[state_index] * weight for state_index, weight in zip(state_subset, weights))
        worst = max(worst, abs(observed - expected))
    return worst


def _state_vectors(metadata: list[dict[str, Any]], states: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for state in states:
        vectors.append([float(raw["no_arb_payoffs"][state]) for raw in metadata])
    return vectors


def _payoff_matrix(nodes: list[MarketNode], states: list[str]) -> dict[str, list[float | None]]:
    matrix: dict[str, list[float | None]] = {}
    for node in nodes:
        raw = _metadata(node)
        payoffs = raw.get("no_arb_payoffs")
        if not isinstance(payoffs, dict):
            matrix[node.market_id] = [None for _ in states]
        else:
            matrix[node.market_id] = [
                float(payoffs[state]) if isinstance(payoffs.get(state), (int, float)) and not isinstance(payoffs.get(state), bool) else None
                for state in states
            ]
    return matrix


def _validate_diagnostic(item: dict[str, Any], path: str) -> None:
    required = [
        "diagnostic_only",
        "affects_evaluator_gates",
        "allowed_actions",
        "max_action_cap",
        "diagnostic_priority",
        "feasibility_status",
        "violated_constraints",
        "bound_gap",
        "normalized_bound_gap",
        "state_count",
        "contract_count",
        "confidence_basis",
        "blockers",
        "required_review_questions",
    ]
    for key in required:
        if key not in item:
            raise SchemaValidationError(f"{path}.{key} is required")
    if item["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if item["affects_evaluator_gates"] is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if item["allowed_actions"] != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    if item["max_action_cap"] not in DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.max_action_cap must be WATCH or MANUAL_REVIEW")
    if item["diagnostic_priority"] not in DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.diagnostic_priority must be WATCH or MANUAL_REVIEW")
    if item["feasibility_status"] not in FEASIBILITY_STATUSES:
        raise SchemaValidationError(f"{path}.feasibility_status is not allowed")
    if not isinstance(item["violated_constraints"], list):
        raise SchemaValidationError(f"{path}.violated_constraints must be a list")
    for key in ["bound_gap", "normalized_bound_gap"]:
        if not isinstance(item[key], (int, float)) or isinstance(item[key], bool) or item[key] < 0:
            raise SchemaValidationError(f"{path}.{key} must be a non-negative number")
    for key in ["state_count", "contract_count"]:
        if not isinstance(item[key], int) or isinstance(item[key], bool) or item[key] < 0:
            raise SchemaValidationError(f"{path}.{key} must be a non-negative integer")
    confidence = item["confidence_basis"]
    if not isinstance(confidence, dict):
        raise SchemaValidationError(f"{path}.confidence_basis must be an object")
    if not isinstance(confidence.get("description"), str) or not confidence["description"]:
        raise SchemaValidationError(f"{path}.confidence_basis.description must be a non-empty string")
    score = confidence.get("score")
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 1:
        raise SchemaValidationError(f"{path}.confidence_basis.score must be between 0 and 1")
    if not isinstance(item["blockers"], list) or not all(isinstance(value, str) for value in item["blockers"]):
        raise SchemaValidationError(f"{path}.blockers must be a list of strings")
    questions = item["required_review_questions"]
    if not isinstance(questions, list) or not questions or not all(isinstance(value, str) and value for value in questions):
        raise SchemaValidationError(f"{path}.required_review_questions must contain strings")
    _reject_prohibited_tokens(item)


def _family_id(node: MarketNode) -> str | None:
    value = _metadata(node).get("no_arb_family_id")
    return str(value) if value else None


def _metadata(node: MarketNode) -> dict[str, Any]:
    row = node.raw.get("normalized_row")
    if isinstance(row, dict):
        merged = dict(node.raw)
        merged.update(row)
        return merged
    return dict(node.raw)


def _first_text(items: list[dict[str, Any]], key: str) -> str | None:
    for item in items:
        value = item.get(key)
        if value not in {None, ""}:
            return str(value)
    return None


def _first_list(items: list[dict[str, Any]], key: str) -> list[str]:
    for item in items:
        value = item.get(key)
        if isinstance(value, list):
            return [str(entry) for entry in value]
    return []


def _reason(status: str) -> str:
    if status == "infeasible":
        return "Finite-state feasibility violation needs manual review."
    if status == "blocked":
        return "Finite-state feasibility diagnostic is blocked by incomplete fixture definitions."
    return "Finite-state feasibility diagnostic is consistent within tolerance."


def _priority(action: str) -> int:
    return {"MANUAL_REVIEW": 0, "WATCH": 1}.get(action, 2)


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
