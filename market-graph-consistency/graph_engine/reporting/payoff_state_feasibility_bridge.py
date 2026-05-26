from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from graph_engine.models import GraphSnapshot, MarketNode
from graph_engine.payoff_state import (
    ALLOWED_ACTIONS,
    BID_ASK_INTERVAL,
    DIAGNOSTIC_MIDPOINT_FALLBACK,
    SUPPORTED_FAMILY_TYPES,
    PayoffMatrix,
    compile_payoff_families,
)
from graph_engine.payoff_state_feasibility import (
    REPAIR_DIRECTIONS,
    REPAIR_DIRECTION_WITHIN,
    FeasibilityResult,
    check_no_arb_consistency,
)
from graph_engine.reporting.probability_constraints import build_probability_constraints_report
from graph_engine.reporting.schema_validation import DIAGNOSTIC_HINT_ACTIONS, SchemaValidationError, _reject_prohibited_tokens


FEASIBILITY_BRIDGE_STATUSES = {
    "FEASIBLE",
    "INFEASIBLE_DIAGNOSTIC",
    "BLOCKED_MISSING_STATE_FAMILY",
    "BLOCKED_MISSING_PAYOFF_MATRIX",
    "BLOCKED_MISSING_PROBABILITY_INPUTS",
    "BLOCKED_UNSUPPORTED_CONSTRAINT_TYPE",
}
CONSTRAINT_TYPES_FOR_FEASIBILITY = {
    "complement_pair",
    "subset_superset",
    "threshold_ladder",
    "mutually_exclusive_group",
    "exhaustive_partition",
    "range_bucket_partition",
}
FAMILY_TYPE_TO_CONSTRAINT_TYPE = {
    "complement_pair": "complement_pair",
    "child_parent_chain": "subset_superset",
    "exhaustive_group": "exhaustive_partition",
    "formula_cluster_exact": "formula_cluster_exact",
    "mutually_exclusive_group": "mutually_exclusive_group",
    "range_bucket_partition": "range_bucket_partition",
    "threshold_ladder": "threshold_ladder",
}
BANNER = (
    "Saved-file-only payoff-state feasibility bridge. Rows are bounded diagnostic checks "
    "that do not affect evaluator gates or grant execution permission."
)
WHY_REVIEW_ONLY = (
    "Bridge output is review-only; native payoff vectors, settlement rules, probability input "
    "freshness, and independent downstream checks remain required."
)
DEFAULT_REVIEW_BLOCKERS = {
    "requires_native_payoff_vector_review",
    "requires_settlement_source_review",
    "requires_fee_depth_freshness_review",
    "not_evaluator_input",
    "no_execution_permission",
}
MISSING_PAYOFF_MATRIX_BLOCKERS = {
    "missing_state_definitions",
    "missing_payoff_matrix",
    "missing_payoff_vector",
}
MISSING_PROBABILITY_BLOCKERS = {
    "missing_probability",
    "missing_observed_probability",
    "missing_probability_input",
}
INTERVAL_BOUND_GAP_SEMANTICS = "sum_of_two_sided_bid_ask_interval_repair_after_tolerance"
MIDPOINT_BOUND_GAP_SEMANTICS = "diagnostic_midpoint_equality_repair_after_tolerance_non_actionable"


def build_payoff_state_feasibility_bridge_report(snapshot: GraphSnapshot) -> dict[str, Any]:
    probability_report = build_probability_constraints_report(snapshot)
    probability_rows = probability_report["probability_constraints"]
    matrices = {matrix.family_id: matrix for matrix in compile_payoff_families(snapshot)}

    rows: list[dict[str, Any]] = []
    probability_rows_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in probability_rows:
        state_family_id = row.get("state_family_id")
        if isinstance(state_family_id, str) and state_family_id:
            probability_rows_by_family[state_family_id].append(row)
        else:
            rows.append(_blocked_probability_constraint_row(snapshot, row, "BLOCKED_MISSING_STATE_FAMILY"))

    represented_family_ids = set(probability_rows_by_family)
    for family_id, matrix in matrices.items():
        rows.append(_matrix_row(snapshot, matrix, probability_rows_by_family.get(family_id, [])))

    for family_id in sorted(represented_family_ids - set(matrices)):
        rows.append(_missing_matrix_probability_group_row(snapshot, family_id, probability_rows_by_family[family_id]))

    rows = sorted(
        rows,
        key=lambda item: (
            _status_priority(item["feasibility_status"]),
            -float(item["infeasibility_gap"] or 0.0),
            item.get("state_family_id") or "",
            item["bridge_id"],
        ),
    )
    for index, row in enumerate(rows, start=1):
        row["diagnostic_rank"] = index

    status_counts = Counter(row["feasibility_status"] for row in rows)
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ALLOWED_ACTIONS,
        "banner": BANNER,
        "snapshot_id": snapshot.snapshot_id,
        "bridge_row_count": len(rows),
        "counts_by_feasibility_status": dict(sorted(status_counts.items())),
        "missing_state_family_summary": _missing_state_family_summary(rows),
        "payoff_state_feasibility_bridge": rows,
    }
    validate_payoff_state_feasibility_bridge_report(report)
    return report


def _missing_state_family_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    blocked = [row for row in rows if row["feasibility_status"] == "BLOCKED_MISSING_STATE_FAMILY"]
    constraint_types = sorted(
        {
            constraint_type
            for row in blocked
            for constraint_type in row.get("constraint_types_represented") or []
        }
    )
    market_ids = sorted({market_id for row in blocked for market_id in row.get("markets_involved") or []})
    return {
        "row_count": len(blocked),
        "constraint_types_represented": constraint_types,
        "unique_markets_involved": market_ids,
        "next_step": "ADD_FIXTURE_STATE_FAMILY_FOR_CONSTRAINT" if blocked else "NONE",
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
    }


def write_payoff_state_feasibility_bridge_report(
    snapshot: GraphSnapshot,
    output_path: Path | str,
) -> dict[str, Any]:
    report = build_payoff_state_feasibility_bridge_report(snapshot)
    validate_payoff_state_feasibility_bridge_report(report)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def validate_payoff_state_feasibility_bridge_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("payoff-state feasibility bridge must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("payoff-state feasibility bridge must not affect evaluator gates")
    if report.get("allowed_actions") != ALLOWED_ACTIONS:
        raise SchemaValidationError("payoff-state feasibility bridge actions must be WATCH and MANUAL_REVIEW only")
    rows = report.get("payoff_state_feasibility_bridge")
    if not isinstance(rows, list):
        raise SchemaValidationError("payoff_state_feasibility_bridge must be a list")
    if report.get("bridge_row_count") != len(rows):
        raise SchemaValidationError("bridge_row_count must match payoff_state_feasibility_bridge")
    summary = report.get("missing_state_family_summary")
    if not isinstance(summary, dict):
        raise SchemaValidationError("missing_state_family_summary must be an object")
    if summary.get("diagnostic_only") is not True or summary.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("missing_state_family_summary must remain diagnostic-only")
    expected_blocked = sum(
        1 for row in rows if row.get("feasibility_status") == "BLOCKED_MISSING_STATE_FAMILY"
    )
    if summary.get("row_count") != expected_blocked:
        raise SchemaValidationError("missing_state_family_summary.row_count must match bridge rows")
    for index, row in enumerate(rows):
        _validate_bridge_row(row, f"payoff_state_feasibility_bridge[{index}]")


def _matrix_row(
    snapshot: GraphSnapshot,
    matrix: PayoffMatrix,
    probability_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    nodes = _nodes_for_market_ids(snapshot, [contract.contract_id for contract in matrix.contracts])
    probability_inputs = _probability_inputs(nodes)
    source_constraint_ids = [row["constraint_id"] for row in probability_rows]
    constraint_types = sorted(
        {row["constraint_type"] for row in probability_rows}
        or {FAMILY_TYPE_TO_CONSTRAINT_TYPE.get(matrix.family_type, matrix.family_type)}
    )
    pre_blockers = _precheck_blockers(matrix, probability_rows, probability_inputs)
    if pre_blockers:
        status = _blocked_status_from_blockers(pre_blockers)
        return _bridge_row(
            snapshot=snapshot,
            bridge_id=f"payoff_state_bridge:{matrix.family_id}",
            state_family_id=matrix.family_id,
            markets_involved=[contract.contract_id for contract in matrix.contracts],
            constraint_types_represented=constraint_types,
            payoff_states_used=[state.to_dict() for state in matrix.states],
            probability_inputs_used=probability_inputs,
            feasibility_status=status,
            infeasibility_gap=0.0,
            minimal_repair_estimate=0.0,
            violated_constraints=[],
            confidence_basis=matrix.confidence_basis,
            review_blockers=pre_blockers,
            source_probability_constraint_ids=source_constraint_ids,
            bridge_source="compiled_payoff_state_family",
            per_contract_repair={},
        )

    result = check_no_arb_consistency(matrix)
    return _bridge_row_from_result(
        snapshot=snapshot,
        matrix=matrix,
        result=result,
        probability_inputs=probability_inputs,
        constraint_types_represented=constraint_types,
        source_probability_constraint_ids=source_constraint_ids,
    )


def _per_contract_repair_directions_for_row(
    result: FeasibilityResult,
    per_contract_repair: dict[str, float],
) -> dict[str, str]:
    out: dict[str, str] = {}
    for contract_id in per_contract_repair:
        out[contract_id] = result.per_contract_repair_directions.get(
            contract_id, REPAIR_DIRECTION_WITHIN
        )
    return out


def _bridge_row_from_result(
    *,
    snapshot: GraphSnapshot,
    matrix: PayoffMatrix,
    result: FeasibilityResult,
    probability_inputs: list[dict[str, Any]],
    constraint_types_represented: list[str],
    source_probability_constraint_ids: list[str],
) -> dict[str, Any]:
    if result.feasibility_status == "feasible":
        status = "FEASIBLE"
    elif result.feasibility_status == "infeasible":
        status = "INFEASIBLE_DIAGNOSTIC"
    else:
        status = _blocked_status_from_blockers(list(result.blockers) + list(matrix.blockers))
    per_contract_repair = (
        result.per_contract_repair if status == "INFEASIBLE_DIAGNOSTIC" else {}
    )
    per_contract_directions = (
        _per_contract_repair_directions_for_row(result, per_contract_repair)
        if status == "INFEASIBLE_DIAGNOSTIC"
        else {}
    )
    return _bridge_row(
        snapshot=snapshot,
        bridge_id=f"payoff_state_bridge:{matrix.family_id}",
        state_family_id=matrix.family_id,
        markets_involved=[contract.contract_id for contract in matrix.contracts],
        constraint_types_represented=constraint_types_represented,
        payoff_states_used=[state.to_dict() for state in matrix.states],
        probability_inputs_used=probability_inputs,
        feasibility_status=status,
        infeasibility_gap=result.bound_gap if status == "INFEASIBLE_DIAGNOSTIC" else 0.0,
        minimal_repair_estimate=result.bound_gap if status == "INFEASIBLE_DIAGNOSTIC" else 0.0,
        violated_constraints=list(result.violated_constraints),
        confidence_basis=dict(result.confidence_basis),
        review_blockers=list(matrix.blockers) + list(result.blockers),
        source_probability_constraint_ids=source_probability_constraint_ids,
        bridge_source="compiled_payoff_state_family",
        probability_input_mode=result.probability_input_mode,
        bound_gap_semantics=result.bound_gap_semantics,
        per_contract_repair=per_contract_repair,
        per_contract_repair_directions=per_contract_directions,
        worst_contract_id=result.worst_contract_id if status == "INFEASIBLE_DIAGNOSTIC" else None,
        worst_contract_repair_gap=(
            result.worst_contract_repair_gap if status == "INFEASIBLE_DIAGNOSTIC" else 0.0
        ),
        structural_bound_gap=(
            result.structural_bound_gap if status == "INFEASIBLE_DIAGNOSTIC" else 0.0
        ),
        lp_bound_gap=result.lp_bound_gap if status == "INFEASIBLE_DIAGNOSTIC" else 0.0,
        binding_structural_constraint=(
            result.binding_structural_constraint if status == "INFEASIBLE_DIAGNOSTIC" else None
        ),
    )


def _blocked_probability_constraint_row(
    snapshot: GraphSnapshot,
    probability_row: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    return _bridge_row(
        snapshot=snapshot,
        bridge_id=f"payoff_state_bridge:probability_constraint:{probability_row['constraint_id']}",
        state_family_id=None,
        markets_involved=list(probability_row["markets_involved"]),
        constraint_types_represented=[probability_row["constraint_type"]],
        payoff_states_used=[],
        probability_inputs_used=list(probability_row["probability_inputs"]),
        feasibility_status=status,
        infeasibility_gap=0.0,
        minimal_repair_estimate=0.0,
        violated_constraints=[],
        confidence_basis={
            "description": "Probability constraint has no fixture-declared state family for finite-state feasibility.",
            "score": 0.2,
        },
        review_blockers=list(probability_row.get("payoff_state_blockers") or ["missing_payoff_state_family_id"]),
        source_probability_constraint_ids=[probability_row["constraint_id"]],
        bridge_source="probability_constraint_row",
        probability_input_mode=_probability_input_mode(list(probability_row["probability_inputs"])),
        bound_gap_semantics=_bound_gap_semantics(_probability_input_mode(list(probability_row["probability_inputs"]))),
        per_contract_repair={},
        per_contract_repair_directions={},
        worst_contract_id=None,
        worst_contract_repair_gap=0.0,
        structural_bound_gap=0.0,
        lp_bound_gap=0.0,
        binding_structural_constraint=None,
    )


def _missing_matrix_probability_group_row(
    snapshot: GraphSnapshot,
    family_id: str,
    probability_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    market_ids = sorted({market_id for row in probability_rows for market_id in row["markets_involved"]})
    return _bridge_row(
        snapshot=snapshot,
        bridge_id=f"payoff_state_bridge:missing_matrix:{family_id}",
        state_family_id=family_id,
        markets_involved=market_ids,
        constraint_types_represented=sorted({row["constraint_type"] for row in probability_rows}),
        payoff_states_used=[],
        probability_inputs_used=[item for row in probability_rows for item in row["probability_inputs"]],
        feasibility_status="BLOCKED_MISSING_PAYOFF_MATRIX",
        infeasibility_gap=0.0,
        minimal_repair_estimate=0.0,
        violated_constraints=[],
        confidence_basis={
            "description": "State family id is present, but no compiled payoff matrix exists in saved fixtures.",
            "score": 0.2,
        },
        review_blockers=["missing_payoff_matrix"],
        source_probability_constraint_ids=[row["constraint_id"] for row in probability_rows],
        bridge_source="probability_constraint_row",
        probability_input_mode=_probability_input_mode([item for row in probability_rows for item in row["probability_inputs"]]),
        bound_gap_semantics=_bound_gap_semantics(
            _probability_input_mode([item for row in probability_rows for item in row["probability_inputs"]])
        ),
        per_contract_repair={},
        per_contract_repair_directions={},
        worst_contract_id=None,
        worst_contract_repair_gap=0.0,
        structural_bound_gap=0.0,
        lp_bound_gap=0.0,
        binding_structural_constraint=None,
    )


def _bridge_row(
    *,
    snapshot: GraphSnapshot,
    bridge_id: str,
    state_family_id: str | None,
    markets_involved: list[str],
    constraint_types_represented: list[str],
    payoff_states_used: list[dict[str, Any]],
    probability_inputs_used: list[dict[str, Any]],
    feasibility_status: str,
    infeasibility_gap: float,
    minimal_repair_estimate: float,
    violated_constraints: list[str],
    confidence_basis: dict[str, Any],
    review_blockers: list[str],
    source_probability_constraint_ids: list[str],
    bridge_source: str,
    probability_input_mode: str | None = None,
    bound_gap_semantics: str | None = None,
    per_contract_repair: dict[str, float] | None = None,
    per_contract_repair_directions: dict[str, str] | None = None,
    worst_contract_id: str | None = None,
    worst_contract_repair_gap: float = 0.0,
    structural_bound_gap: float = 0.0,
    lp_bound_gap: float = 0.0,
    binding_structural_constraint: str | None = None,
) -> dict[str, Any]:
    normalized_inputs = _normalize_probability_inputs(probability_inputs_used)
    input_mode = probability_input_mode or _probability_input_mode(normalized_inputs)
    normalized_repair = _normalize_per_contract_repair(per_contract_repair)
    row = {
        "bridge_id": bridge_id,
        "bridge_source": bridge_source,
        "state_family_id": state_family_id,
        "markets_involved": list(markets_involved),
        "constraint_types_represented": list(constraint_types_represented),
        "payoff_states_used": list(payoff_states_used),
        "probability_inputs_used": normalized_inputs,
        "feasibility_status": feasibility_status,
        "probability_input_mode": input_mode,
        "bound_gap_semantics": bound_gap_semantics or _bound_gap_semantics(input_mode),
        "infeasibility_gap": round(max(0.0, float(infeasibility_gap)), 6),
        "minimal_repair_estimate": round(max(0.0, float(minimal_repair_estimate)), 6),
        "per_contract_repair": normalized_repair,
        "per_contract_repair_directions": _normalize_per_contract_repair_directions(
            per_contract_repair_directions, normalized_repair
        ),
        "worst_contract_id": worst_contract_id if isinstance(worst_contract_id, str) and worst_contract_id else None,
        "worst_contract_repair_gap": round(max(0.0, float(worst_contract_repair_gap or 0.0)), 6),
        "structural_bound_gap": round(max(0.0, float(structural_bound_gap or 0.0)), 6),
        "lp_bound_gap": round(max(0.0, float(lp_bound_gap or 0.0)), 6),
        "binding_structural_constraint": (
            binding_structural_constraint
            if isinstance(binding_structural_constraint, str) and binding_structural_constraint
            else None
        ),
        "violated_constraints": list(violated_constraints),
        "confidence_basis": dict(confidence_basis),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ALLOWED_ACTIONS,
        "review_blockers": _review_blockers(review_blockers),
        "why_review_only_yet": WHY_REVIEW_ONLY,
        "source_probability_constraint_ids": list(source_probability_constraint_ids),
        "snapshot_id": snapshot.snapshot_id,
    }
    _validate_bridge_row(row, "payoff_state_feasibility_bridge[]")
    return row


def _precheck_blockers(
    matrix: PayoffMatrix,
    probability_rows: list[dict[str, Any]],
    probability_inputs: list[dict[str, Any]],
) -> list[str]:
    blockers = list(matrix.blockers)
    if any(item.get("probability") is None for item in probability_inputs):
        blockers.append("missing_probability_input")
    for row in probability_rows:
        blockers.extend(row.get("payoff_state_blockers") or [])
    return sorted(set(blockers))


def _blocked_status_from_blockers(blockers: list[str]) -> str:
    blocker_set = set(blockers)
    if "missing_payoff_state_family_id" in blocker_set:
        return "BLOCKED_MISSING_STATE_FAMILY"
    if blocker_set & MISSING_PROBABILITY_BLOCKERS:
        return "BLOCKED_MISSING_PROBABILITY_INPUTS"
    if "unsupported_constraint_type_for_feasibility" in blocker_set or "unsupported_family_type" in blocker_set:
        return "BLOCKED_UNSUPPORTED_CONSTRAINT_TYPE"
    if blocker_set & MISSING_PAYOFF_MATRIX_BLOCKERS or any(
        blocker.startswith("missing_payoff_vector:") or blocker.startswith("missing_state_payoff:")
        for blocker in blocker_set
    ):
        return "BLOCKED_MISSING_PAYOFF_MATRIX"
    return "BLOCKED_MISSING_PAYOFF_MATRIX"


def _probability_inputs(nodes: list[MarketNode]) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    for node in nodes:
        probability, source = _probability_and_source(node)
        midpoint = (node.bid + node.ask) / 2.0 if node.bid is not None and node.ask is not None else None
        input_mode = BID_ASK_INTERVAL if node.bid is not None and node.ask is not None else DIAGNOSTIC_MIDPOINT_FALLBACK
        inputs.append(
            {
                "market_id": node.market_id,
                "probability": round(probability, 6) if probability is not None else None,
                "probability_source": source,
                "bid_bound": node.bid,
                "ask_bound": node.ask,
                "midpoint": round(midpoint, 6) if midpoint is not None else None,
                "diagnostic_midpoint_used": input_mode == DIAGNOSTIC_MIDPOINT_FALLBACK and probability is not None,
                "non_actionable_input": input_mode == DIAGNOSTIC_MIDPOINT_FALLBACK,
                "probability_input_mode": input_mode,
            }
        )
    return inputs


def _probability_and_source(node: MarketNode) -> tuple[float | None, str]:
    if node.bid is not None and node.ask is not None:
        return (node.bid + node.ask) / 2.0, "bid_ask_interval_midpoint"
    if node.yes_price is not None:
        return node.yes_price, "diagnostic_midpoint_fallback"
    return None, "missing_probability"


def _probability_input_mode(probability_inputs: list[dict[str, Any]]) -> str:
    if probability_inputs and all(
        item.get("bid_bound") is not None and item.get("ask_bound") is not None
        for item in probability_inputs
    ):
        return BID_ASK_INTERVAL
    return DIAGNOSTIC_MIDPOINT_FALLBACK


def _normalize_probability_inputs(probability_inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in probability_inputs:
        bid = item.get("bid_bound", item.get("bid"))
        ask = item.get("ask_bound", item.get("ask"))
        mode = item.get("probability_input_mode")
        if mode not in {BID_ASK_INTERVAL, DIAGNOSTIC_MIDPOINT_FALLBACK}:
            mode = BID_ASK_INTERVAL if bid is not None and ask is not None else DIAGNOSTIC_MIDPOINT_FALLBACK
        normalized.append(
            {
                **item,
                "bid_bound": bid,
                "ask_bound": ask,
                "probability_input_mode": mode,
                "diagnostic_midpoint_used": bool(item.get("diagnostic_midpoint_used"))
                or (mode == DIAGNOSTIC_MIDPOINT_FALLBACK and item.get("probability") is not None),
                "non_actionable_input": bool(item.get("non_actionable_input"))
                or mode == DIAGNOSTIC_MIDPOINT_FALLBACK,
            }
        )
    return normalized


def _bound_gap_semantics(input_mode: str) -> str:
    if input_mode == BID_ASK_INTERVAL:
        return INTERVAL_BOUND_GAP_SEMANTICS
    return MIDPOINT_BOUND_GAP_SEMANTICS


def _nodes_for_market_ids(snapshot: GraphSnapshot, market_ids: list[str]) -> list[MarketNode]:
    return [snapshot.nodes[market_id] for market_id in market_ids if market_id in snapshot.nodes]


def _review_blockers(blockers: list[str]) -> list[str]:
    return sorted(DEFAULT_REVIEW_BLOCKERS | set(blockers))


def _normalize_per_contract_repair(per_contract_repair: dict[str, float] | None) -> dict[str, float]:
    if not isinstance(per_contract_repair, dict):
        return {}
    output: dict[str, float] = {}
    for contract_id, gap in per_contract_repair.items():
        if isinstance(contract_id, str) and isinstance(gap, (int, float)) and not isinstance(gap, bool):
            output[contract_id] = round(max(0.0, float(gap)), 6)
    return dict(sorted(output.items()))


def _normalize_per_contract_repair_directions(
    per_contract_repair_directions: dict[str, str] | None,
    per_contract_repair: dict[str, float],
) -> dict[str, str]:
    output: dict[str, str] = {}
    raw = per_contract_repair_directions if isinstance(per_contract_repair_directions, dict) else {}
    for contract_id in per_contract_repair:
        direction = raw.get(contract_id, REPAIR_DIRECTION_WITHIN)
        if not isinstance(direction, str) or direction not in REPAIR_DIRECTIONS:
            direction = REPAIR_DIRECTION_WITHIN
        if per_contract_repair[contract_id] <= 1e-12:
            direction = REPAIR_DIRECTION_WITHIN
        output[contract_id] = direction
    return dict(sorted(output.items()))


def _status_priority(status: str) -> int:
    return {
        "INFEASIBLE_DIAGNOSTIC": 0,
        "BLOCKED_UNSUPPORTED_CONSTRAINT_TYPE": 1,
        "BLOCKED_MISSING_PAYOFF_MATRIX": 2,
        "BLOCKED_MISSING_PROBABILITY_INPUTS": 3,
        "BLOCKED_MISSING_STATE_FAMILY": 4,
        "FEASIBLE": 5,
    }.get(status, 9)


def _validate_bridge_row(row: dict[str, Any], path: str) -> None:
    required = [
        "bridge_id",
        "bridge_source",
        "state_family_id",
        "markets_involved",
        "constraint_types_represented",
        "payoff_states_used",
        "probability_inputs_used",
        "feasibility_status",
        "probability_input_mode",
        "bound_gap_semantics",
        "infeasibility_gap",
        "minimal_repair_estimate",
        "per_contract_repair",
        "per_contract_repair_directions",
        "worst_contract_id",
        "worst_contract_repair_gap",
        "structural_bound_gap",
        "lp_bound_gap",
        "binding_structural_constraint",
        "violated_constraints",
        "confidence_basis",
        "diagnostic_only",
        "affects_evaluator_gates",
        "allowed_actions",
        "review_blockers",
        "why_review_only_yet",
        "source_probability_constraint_ids",
        "snapshot_id",
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
    if row["feasibility_status"] not in FEASIBILITY_BRIDGE_STATUSES:
        raise SchemaValidationError(f"{path}.feasibility_status is not supported")
    if row["probability_input_mode"] not in {BID_ASK_INTERVAL, DIAGNOSTIC_MIDPOINT_FALLBACK}:
        raise SchemaValidationError(f"{path}.probability_input_mode is not supported")
    if not isinstance(row["bound_gap_semantics"], str) or not row["bound_gap_semantics"]:
        raise SchemaValidationError(f"{path}.bound_gap_semantics must be a non-empty string")
    if row["state_family_id"] is None and row["feasibility_status"] != "BLOCKED_MISSING_STATE_FAMILY":
        raise SchemaValidationError(f"{path}.state_family_id may be null only for missing-state-family rows")
    if row["state_family_id"] is not None and not isinstance(row["state_family_id"], str):
        raise SchemaValidationError(f"{path}.state_family_id must be a string or null")
    for key in [
        "markets_involved",
        "constraint_types_represented",
        "payoff_states_used",
        "probability_inputs_used",
        "violated_constraints",
        "review_blockers",
        "source_probability_constraint_ids",
    ]:
        if not isinstance(row[key], list):
            raise SchemaValidationError(f"{path}.{key} must be a list")
    if not row["markets_involved"]:
        raise SchemaValidationError(f"{path}.markets_involved must not be empty")
    if not row["constraint_types_represented"]:
        raise SchemaValidationError(f"{path}.constraint_types_represented must not be empty")
    for constraint_type in row["constraint_types_represented"]:
        if constraint_type not in CONSTRAINT_TYPES_FOR_FEASIBILITY and constraint_type != "formula_cluster_exact":
            raise SchemaValidationError(f"{path}.constraint_types_represented contains unsupported value")
    for key in ["infeasibility_gap", "minimal_repair_estimate"]:
        value = row[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise SchemaValidationError(f"{path}.{key} must be a non-negative number")
    per_contract_repair = row["per_contract_repair"]
    if not isinstance(per_contract_repair, dict):
        raise SchemaValidationError(f"{path}.per_contract_repair must be an object")
    for contract_id, gap in per_contract_repair.items():
        if not isinstance(contract_id, str) or not contract_id:
            raise SchemaValidationError(f"{path}.per_contract_repair keys must be non-empty strings")
        if not isinstance(gap, (int, float)) or isinstance(gap, bool) or gap < 0:
            raise SchemaValidationError(f"{path}.per_contract_repair values must be non-negative numbers")
    confidence = row["confidence_basis"]
    if not isinstance(confidence, dict):
        raise SchemaValidationError(f"{path}.confidence_basis must be an object")
    if not isinstance(confidence.get("description"), str) or not confidence["description"]:
        raise SchemaValidationError(f"{path}.confidence_basis.description must be a non-empty string")
    score = confidence.get("score")
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 1:
        raise SchemaValidationError(f"{path}.confidence_basis.score must be between 0 and 1")
    if row["feasibility_status"] == "FEASIBLE" and row["infeasibility_gap"] != 0:
        raise SchemaValidationError(f"{path}.feasible rows must have zero infeasibility_gap")
    if row["feasibility_status"] == "INFEASIBLE_DIAGNOSTIC" and row["infeasibility_gap"] <= 0:
        raise SchemaValidationError(f"{path}.infeasible rows must have positive infeasibility_gap")
    if row["feasibility_status"] == "INFEASIBLE_DIAGNOSTIC" and not per_contract_repair:
        raise SchemaValidationError(f"{path}.infeasible rows must include per_contract_repair")
    for input_index, item in enumerate(row["probability_inputs_used"]):
        for key in [
            "market_id",
            "probability",
            "probability_source",
            "bid_bound",
            "ask_bound",
            "diagnostic_midpoint_used",
            "non_actionable_input",
            "probability_input_mode",
        ]:
            if key not in item:
                raise SchemaValidationError(f"{path}.probability_inputs_used[{input_index}].{key} is required")
        if item.get("probability_input_mode") not in {BID_ASK_INTERVAL, DIAGNOSTIC_MIDPOINT_FALLBACK}:
            raise SchemaValidationError(f"{path}.probability_inputs_used[{input_index}].probability_input_mode is unsupported")
        if item.get("diagnostic_midpoint_used") is True and item.get("non_actionable_input") is not True:
            raise SchemaValidationError(f"{path}.probability_inputs_used[{input_index}].midpoint must be non_actionable")
    directions = row["per_contract_repair_directions"]
    if not isinstance(directions, dict):
        raise SchemaValidationError(f"{path}.per_contract_repair_directions must be an object")
    for contract_id, direction in directions.items():
        if not isinstance(contract_id, str) or not contract_id:
            raise SchemaValidationError(f"{path}.per_contract_repair_directions keys must be non-empty strings")
        if direction not in REPAIR_DIRECTIONS:
            raise SchemaValidationError(f"{path}.per_contract_repair_directions values must be one of {sorted(REPAIR_DIRECTIONS)}")
    if set(directions) != set(per_contract_repair):
        raise SchemaValidationError(f"{path}.per_contract_repair_directions keys must match per_contract_repair keys")
    if row["worst_contract_id"] is not None and not isinstance(row["worst_contract_id"], str):
        raise SchemaValidationError(f"{path}.worst_contract_id must be a string or null")
    for key in ["worst_contract_repair_gap", "structural_bound_gap", "lp_bound_gap"]:
        value = row[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise SchemaValidationError(f"{path}.{key} must be a non-negative number")
    if row["feasibility_status"] == "INFEASIBLE_DIAGNOSTIC":
        if row["worst_contract_id"] is None or row["worst_contract_id"] not in per_contract_repair:
            raise SchemaValidationError(f"{path}.infeasible rows must name a worst_contract_id from per_contract_repair")
        if row["worst_contract_repair_gap"] <= 0:
            raise SchemaValidationError(f"{path}.infeasible rows must have positive worst_contract_repair_gap")
    if row["binding_structural_constraint"] is not None and not isinstance(row["binding_structural_constraint"], str):
        raise SchemaValidationError(f"{path}.binding_structural_constraint must be a string or null")
    _reject_prohibited_tokens(row)


__all__ = [
    "BANNER",
    "FEASIBILITY_BRIDGE_STATUSES",
    "build_payoff_state_feasibility_bridge_report",
    "validate_payoff_state_feasibility_bridge_report",
    "write_payoff_state_feasibility_bridge_report",
]
