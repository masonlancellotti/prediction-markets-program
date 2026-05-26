"""No-arb consistency engine over compiled :class:`PayoffMatrix` families.

The engine answers a single question for every fixture family: do there exist
non-negative state probabilities ``p_1 ... p_S`` summing to one such that, for
every contract ``i`` with payoff vector ``v_i`` and observed yes-probability
``q_i``, ``sum_s p_s * v_i[s] == q_i`` within a small tolerance?  A negative
answer means the family's observed prices cannot be rationalised by any
probability distribution over its declared finite states, which is a
structural inconsistency worth reviewing manually.

The engine is diagnostic-only.  It does not estimate executable trade size,
fills, paper-trade candidates, or evaluator-ready evidence.  Its review
outputs include ``feasibility_status``, ``violated_constraints``,
``bound_gap``, ``normalized_bound_gap``, and ``per_contract_repair`` -- all of
which feed reviewers, not automated execution.

Algorithm
---------

For small families (S, N <= 8) we enumerate every basic feasible vertex of
the linear system::

    [1   1   ...  1 ] [p_1]   [1   ]
    [v_1[1] ... v_1[S]] [p_2] = [q_1 ]
    [ ...              ] [...] = [... ]
    [v_N[1] ... v_N[S]] [p_S]   [q_N ]

A vertex is feasible iff every weight is non-negative.  We compute the worst
row residual of the (overdetermined) system; if any non-negative vertex
achieves residual within ``tolerance`` we declare the family feasible.  The
returned ``bound_gap`` is the best residual we could obtain across the search,
which is a strict lower bound on how far the observed prices deviate from any
state-consistent distribution.

When ``scipy`` is available the engine prefers ``scipy.optimize.linprog`` to
get a tighter dual residual; otherwise the enumeration approach above runs in
pure Python.  Either way the output is the same shape and the same
diagnostic-only contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from graph_engine.payoff_state import (
    ALLOWED_ACTIONS,
    BID_ASK_INTERVAL,
    DEFAULT_TOLERANCE,
    DIAGNOSTIC_MIDPOINT_FALLBACK,
    PayoffMatrix,
)


try:  # pragma: no cover - exercised only when scipy is installed
    from scipy.optimize import linprog  # type: ignore[import-untyped]
    _HAS_SCIPY = True
except Exception:  # noqa: BLE001 - scipy is optional
    linprog = None  # type: ignore[assignment]
    _HAS_SCIPY = False


FEASIBILITY_STATUSES = {"feasible", "infeasible", "blocked"}
MAX_VERTEX_ENUMERATION = 8
REQUIRED_REVIEW_QUESTIONS = [
    "Do fixture state definitions cover the family exhaustively under one settlement source?",
    "Do contract payoff vectors match native venue resolution rules?",
    "Do all contracts share compatible settlement timing, source, and units?",
    "Are observed yes-prices captured from comparable book snapshots?",
]
REPAIR_DIRECTION_INTERVAL_HIGH = "price_above_lp_feasible_value"
REPAIR_DIRECTION_INTERVAL_LOW = "price_below_lp_feasible_value"
REPAIR_DIRECTION_WITHIN = "within_lp_feasible_value"
REPAIR_DIRECTIONS = {
    REPAIR_DIRECTION_INTERVAL_HIGH,
    REPAIR_DIRECTION_INTERVAL_LOW,
    REPAIR_DIRECTION_WITHIN,
}


@dataclass(frozen=True)
class FeasibilityResult:
    family_id: str
    family_type: str
    feasibility_status: str
    violated_constraints: list[str]
    bound_gap: float
    normalized_bound_gap: float
    confidence_basis: dict[str, Any]
    blockers: list[str]
    state_count: int
    contract_count: int
    per_contract_repair: dict[str, float] = field(default_factory=dict)
    probability_input_mode: str = DIAGNOSTIC_MIDPOINT_FALLBACK
    bound_gap_semantics: str = "two_sided_repair_distance_after_tolerance"
    required_review_questions: list[str] = field(default_factory=lambda: list(REQUIRED_REVIEW_QUESTIONS))
    per_contract_repair_directions: dict[str, str] = field(default_factory=dict)
    worst_contract_id: str | None = None
    worst_contract_repair_gap: float = 0.0
    structural_bound_gap: float = 0.0
    lp_bound_gap: float = 0.0
    binding_structural_constraint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "family_type": self.family_type,
            "feasibility_status": self.feasibility_status,
            "violated_constraints": list(self.violated_constraints),
            "bound_gap": round(max(0.0, self.bound_gap), 6),
            "normalized_bound_gap": round(max(0.0, self.normalized_bound_gap), 6),
            "confidence_basis": dict(self.confidence_basis),
            "blockers": list(self.blockers),
            "state_count": self.state_count,
            "contract_count": self.contract_count,
            "per_contract_repair": {
                contract_id: round(max(0.0, float(gap)), 6)
                for contract_id, gap in self.per_contract_repair.items()
            },
            "per_contract_repair_directions": dict(self.per_contract_repair_directions),
            "worst_contract_id": self.worst_contract_id,
            "worst_contract_repair_gap": round(max(0.0, float(self.worst_contract_repair_gap)), 6),
            "structural_bound_gap": round(max(0.0, float(self.structural_bound_gap)), 6),
            "lp_bound_gap": round(max(0.0, float(self.lp_bound_gap)), 6),
            "binding_structural_constraint": self.binding_structural_constraint,
            "probability_input_mode": self.probability_input_mode,
            "bound_gap_semantics": self.bound_gap_semantics,
            "required_review_questions": list(self.required_review_questions),
        }


def check_no_arb_consistency(
    matrix: PayoffMatrix,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> FeasibilityResult:
    """Run the diagnostic no-arb consistency check for a single family."""

    if matrix.blockers:
        return FeasibilityResult(
            family_id=matrix.family_id,
            family_type=matrix.family_type,
            feasibility_status="blocked",
            violated_constraints=[],
            bound_gap=0.0,
            normalized_bound_gap=0.0,
            confidence_basis={
                "description": "Finite-state feasibility check blocked by missing or ambiguous fixture definitions.",
                "score": round(min(0.2, matrix.confidence_basis.get("score", 0.2)), 6),
            },
            blockers=list(matrix.blockers),
            state_count=matrix.state_count,
            contract_count=matrix.contract_count,
            probability_input_mode=_probability_input_mode(matrix.contracts),
        )

    state_ids = [state.state_id for state in matrix.states]
    contracts = matrix.contracts
    contract_bounds = [_contract_interval(contract) for contract in contracts]
    if any(value is None for value in contract_bounds):
        return FeasibilityResult(
            family_id=matrix.family_id,
            family_type=matrix.family_type,
            feasibility_status="blocked",
            violated_constraints=[],
            bound_gap=0.0,
            normalized_bound_gap=0.0,
            confidence_basis={
                "description": "Finite-state feasibility check blocked by missing observed probabilities.",
                "score": 0.2,
            },
            blockers=["missing_observed_probability"],
            state_count=matrix.state_count,
            contract_count=matrix.contract_count,
            probability_input_mode=_probability_input_mode(contracts),
        )

    intervals = [value for value in contract_bounds if value is not None]  # type: ignore[assignment]
    payoffs = [[contract.payoff_by_state.get(state_id, 0.0) for state_id in state_ids] for contract in contracts]
    input_mode = _probability_input_mode(contracts)
    input_blockers = _probability_input_blockers(contracts)

    structural_violations, structural_gap, binding_structural = _structural_violations(
        matrix, contracts, intervals, tolerance
    )
    hull_feasible, hull_gap, repair_values, repair_directions = _feasible_via_enumeration(
        payoffs, intervals, tolerance
    )
    aggregate_gap = max(0.0, max(hull_gap, structural_gap))
    violated_constraints = list(structural_violations)
    if not hull_feasible:
        violated_constraints.insert(0, "finite_state_feasibility")
    feasibility_status = "feasible" if hull_feasible and not structural_violations else "infeasible"
    confidence = _confidence(feasibility_status, hull_gap, structural_violations, input_mode)
    per_contract_repair = _per_contract_repair_dict(contracts, repair_values)
    per_contract_directions = _per_contract_repair_directions_dict(
        contracts, repair_values, repair_directions, feasibility_status
    )
    worst_id, worst_gap = _worst_contract(per_contract_repair)
    return FeasibilityResult(
        family_id=matrix.family_id,
        family_type=matrix.family_type,
        feasibility_status=feasibility_status,
        violated_constraints=sorted(set(violated_constraints)),
        bound_gap=aggregate_gap,
        normalized_bound_gap=_normalized_gap(aggregate_gap, contracts),
        confidence_basis=confidence,
        blockers=input_blockers,
        state_count=matrix.state_count,
        contract_count=matrix.contract_count,
        per_contract_repair=per_contract_repair,
        probability_input_mode=input_mode,
        bound_gap_semantics=_bound_gap_semantics(input_mode),
        per_contract_repair_directions=per_contract_directions,
        worst_contract_id=worst_id,
        worst_contract_repair_gap=worst_gap,
        structural_bound_gap=max(0.0, structural_gap),
        lp_bound_gap=max(0.0, hull_gap),
        binding_structural_constraint=binding_structural,
    )


def _structural_violations(
    matrix: PayoffMatrix,
    contracts: list,
    intervals: list[tuple[float, float]],
    tolerance: float,
) -> tuple[list[str], float, str | None]:
    """Return (named violations, structural gap, binding side label).

    The binding side label names the structural inequality face that was active
    at the violation. Reviewers can use it to decide whether prices need to fall
    (``..._sum_exceeds_target``) or rise (``..._sum_below_target``) for the
    family to become consistent with its declared finite states.
    """

    family_type = matrix.family_type
    if family_type == "exhaustive_group":
        gap, binding = _exact_sum_binding(intervals, 1.0, tolerance, "exhaustive")
        if gap > 0:
            return ["exhaustive_sum_bound"], gap, binding
        return [], 0.0, None
    if family_type == "mutually_exclusive_group":
        lower_sum = sum(lower for lower, _ in intervals)
        gap = max(0.0, lower_sum - 1.0 - tolerance)
        if gap > 0:
            return ["mutually_exclusive_sum_bound"], gap, "mutex_sum_exceeds_target"
        return [], 0.0, None
    if family_type == "complement_pair":
        if len(intervals) != 2:
            return ["complement_pair_requires_two_contracts"], 0.0, None
        gap, binding = _exact_sum_binding(intervals, 1.0, tolerance, "complement")
        if gap > 0:
            return ["complement_pair_sum_bound"], gap, binding
        return [], 0.0, None
    if family_type == "child_parent_chain":
        by_role = {contract.structural_role: contract for contract in contracts}
        child = by_role.get("child")
        parent = by_role.get("parent")
        if child is None or parent is None:
            return [], 0.0, None
        child_interval = _contract_interval(child)
        parent_interval = _contract_interval(parent)
        if child_interval is None or parent_interval is None:
            return [], 0.0, None
        gap = max(0.0, child_interval[0] - parent_interval[1] - tolerance)
        if gap > 0:
            return ["child_parent_bound"], gap, "child_lower_exceeds_parent_upper"
        return [], 0.0, None
    if family_type == "threshold_ladder":
        ordered = _ordered_by_threshold(matrix, contracts)
        worst = 0.0
        for stricter, looser in zip(ordered, ordered[1:]):
            stricter_interval = _contract_interval(stricter)
            looser_interval = _contract_interval(looser)
            if stricter_interval is None or looser_interval is None:
                continue
            gap = stricter_interval[0] - looser_interval[1] - tolerance
            worst = max(worst, gap)
        if worst > 0:
            return ["threshold_ladder_monotonicity"], worst, "stricter_lower_exceeds_looser_upper"
        return [], 0.0, None
    if family_type == "range_bucket_partition":
        gap, binding = _exact_sum_binding(intervals, 1.0, tolerance, "range_bucket")
        if gap > 0:
            return ["range_bucket_partition_sum_bound"], gap, binding
        return [], 0.0, None
    if family_type == "formula_cluster_exact":
        lower = max(item[0] for item in intervals)
        upper = min(item[1] for item in intervals)
        if len(intervals) >= 2 and lower - upper > tolerance:
            return (
                ["formula_cluster_price_divergence_review_only"],
                lower - upper - tolerance,
                "formula_cluster_interval_intersection_empty",
            )
        return [], 0.0, None
    return [], 0.0, None


def _ordered_by_threshold(matrix: PayoffMatrix, contracts: list) -> list:
    thresholds = matrix.structural_metadata.get("ladder_thresholds")
    if not thresholds:
        return contracts
    contracts_by_id = {contract.contract_id: contract for contract in contracts}
    pairs: list[tuple[float, Any]] = []
    seen_ids: set[str] = set()
    for contract in contracts:
        threshold = _contract_threshold(matrix, contract)
        if threshold is None:
            continue
        pairs.append((threshold, contract))
        seen_ids.add(contract.contract_id)
    pairs.sort(key=lambda item: item[0], reverse=True)
    ordered = [contract for _, contract in pairs]
    for contract in contracts:
        if contract.contract_id not in seen_ids:
            ordered.append(contract)
    return ordered if ordered else contracts


def _contract_threshold(matrix: PayoffMatrix, contract) -> float | None:
    metadata = matrix.structural_metadata
    thresholds = metadata.get("ladder_thresholds")
    if not thresholds:
        return None
    payoffs = contract.payoff_by_state
    matching = sum(1 for value in payoffs.values() if value > 0.5)
    if matching == 0:
        return None
    sorted_thresholds = sorted(thresholds)
    if matching > len(sorted_thresholds):
        matching = len(sorted_thresholds)
    return sorted_thresholds[-matching]


def _feasible_via_enumeration(
    payoffs: list[list[float]],
    intervals: list[tuple[float, float]],
    tolerance: float,
) -> tuple[bool, float, list[float], list[str]]:
    if not payoffs:
        return False, 1.0, [], []
    state_count = len(payoffs[0])
    if state_count == 0:
        return (
            False,
            1.0,
            [1.0 for _ in intervals],
            [REPAIR_DIRECTION_INTERVAL_HIGH for _ in intervals],
        )
    if _HAS_SCIPY:
        return _feasible_via_scipy(payoffs, intervals, tolerance)
    active_constraints: list[tuple[list[float], float]] = []
    for state_index in range(state_count):
        row = [0.0] * state_count
        row[state_index] = 1.0
        active_constraints.append((row, 0.0))
    for payoff_row, (lower, upper) in zip(payoffs, intervals):
        active_constraints.append((list(payoff_row), lower))
        active_constraints.append((list(payoff_row), upper))

    best_gap = float("inf")
    best_repairs = [1.0 for _ in intervals]
    best_directions = [REPAIR_DIRECTION_INTERVAL_HIGH for _ in intervals]
    candidates: list[list[float]] = []
    if state_count == 1:
        candidates.append([1.0])
    basis_size = max(0, state_count - 1)
    if basis_size <= len(active_constraints):
        for active_subset in combinations(active_constraints, basis_size):
            rows = [[1.0] * state_count]
            rhs = [1.0]
            for row, value in active_subset:
                rows.append(row)
                rhs.append(value)
            weights = _solve_square(rows, rhs)
            if weights is not None:
                candidates.append(weights)
    candidates.append([1.0 / state_count] * state_count)

    for weights in candidates:
        simplex_gap = abs(sum(weights) - 1.0)
        weight_deficit = max(0.0, -min(weights))
        repair_values, repair_directions = _interval_repair_values(payoffs, intervals, weights, tolerance)
        interval_gap = sum(repair_values)
        total_gap = interval_gap + max(0.0, simplex_gap - tolerance) + max(0.0, weight_deficit - tolerance)
        if total_gap < best_gap:
            best_gap = total_gap
            best_repairs = repair_values
            best_directions = repair_directions
        if total_gap <= 1e-12:
            return (
                True,
                0.0,
                [0.0 for _ in intervals],
                [REPAIR_DIRECTION_WITHIN for _ in intervals],
            )
    if best_gap == float("inf"):
        return (
            False,
            1.0,
            [1.0 for _ in intervals],
            [REPAIR_DIRECTION_INTERVAL_HIGH for _ in intervals],
        )
    return False, best_gap, best_repairs, best_directions


def _feasible_via_scipy(
    payoffs: list[list[float]],
    intervals: list[tuple[float, float]],
    tolerance: float,
) -> tuple[bool, float, list[float], list[str]]:  # pragma: no cover - exercised only with scipy installed
    state_count = len(payoffs[0])
    contract_count = len(intervals)
    n_vars = state_count + contract_count * 2
    cost = [0.0] * state_count + [1.0] * (contract_count * 2)
    rows: list[list[float]] = []
    rhs: list[float] = []
    for index, (target_row, (lower, upper)) in enumerate(zip(payoffs, intervals)):
        under = [0.0] * (contract_count * 2)
        over = [0.0] * (contract_count * 2)
        under[index * 2] = -1.0
        over[index * 2 + 1] = -1.0
        rows.append([-value for value in target_row] + under)
        rhs.append(-(lower - tolerance))
        rows.append(list(target_row) + over)
        rhs.append(upper + tolerance)
    equalities = [[1.0] * state_count + [0.0] * (contract_count * 2)]
    equality_rhs = [1.0]
    bounds = [(0.0, None)] * n_vars
    result = linprog(c=cost, A_ub=rows, b_ub=rhs, A_eq=equalities, b_eq=equality_rhs, bounds=bounds, method="highs")
    if result is None or not getattr(result, "success", False):
        return (
            False,
            1.0,
            [1.0 for _ in intervals],
            [REPAIR_DIRECTION_INTERVAL_HIGH for _ in intervals],
        )
    values = [float(value) for value in result.x]
    repairs: list[float] = []
    directions: list[str] = []
    for index in range(contract_count):
        under = max(0.0, values[state_count + index * 2])
        over = max(0.0, values[state_count + index * 2 + 1])
        repair = under + over
        repairs.append(repair)
        if repair <= 1e-12:
            directions.append(REPAIR_DIRECTION_WITHIN)
        elif under >= over:
            # ``under`` slack is positive when the LP-implied value is below the
            # observed bid bound. Observed price is too high relative to the LP
            # value; the contract's interval must shift down for feasibility.
            directions.append(REPAIR_DIRECTION_INTERVAL_HIGH)
        else:
            directions.append(REPAIR_DIRECTION_INTERVAL_LOW)
    residual = sum(repairs)
    return residual <= 1e-12, residual, repairs, directions


def _solve_square(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    n = len(rhs)
    if n == 0:
        return []
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
        observed = sum(row[col] * weight for col, weight in zip(state_subset, weights))
        worst = max(worst, abs(observed - expected))
    return worst


def _contract_interval(contract) -> tuple[float, float] | None:
    if contract.bid_bound is not None and contract.ask_bound is not None:
        return float(contract.bid_bound), float(contract.ask_bound)
    if contract.observed_probability is not None:
        value = float(contract.observed_probability)
        return value, value
    return None


def _probability_input_mode(contracts: list) -> str:
    if contracts and all(contract.bid_bound is not None and contract.ask_bound is not None for contract in contracts):
        return BID_ASK_INTERVAL
    return DIAGNOSTIC_MIDPOINT_FALLBACK


def _probability_input_blockers(contracts: list) -> list[str]:
    blockers: set[str] = set()
    for contract in contracts:
        blockers.update(contract.probability_input_blockers)
        if contract.observed_probability is not None and (
            contract.bid_bound is None or contract.ask_bound is None
        ):
            blockers.update({"diagnostic_midpoint_used", "non_actionable_input"})
    return sorted(blockers)


def _bound_gap_semantics(input_mode: str) -> str:
    if input_mode == BID_ASK_INTERVAL:
        return "sum_of_two_sided_bid_ask_interval_repair_after_tolerance"
    return "diagnostic_midpoint_equality_repair_after_tolerance_non_actionable"


def _exact_sum_interval_gap(
    intervals: list[tuple[float, float]],
    target: float,
    tolerance: float,
) -> float:
    lower_sum = sum(lower for lower, _ in intervals)
    upper_sum = sum(upper for _, upper in intervals)
    return max(0.0, target - upper_sum - tolerance, lower_sum - target - tolerance)


def _exact_sum_binding(
    intervals: list[tuple[float, float]],
    target: float,
    tolerance: float,
    label_prefix: str,
) -> tuple[float, str | None]:
    lower_sum = sum(lower for lower, _ in intervals)
    upper_sum = sum(upper for _, upper in intervals)
    over_gap = lower_sum - target - tolerance
    under_gap = target - upper_sum - tolerance
    if over_gap > 0 and over_gap >= under_gap:
        return max(0.0, over_gap), f"{label_prefix}_sum_exceeds_target"
    if under_gap > 0:
        return max(0.0, under_gap), f"{label_prefix}_sum_below_target"
    return 0.0, None


def _interval_repair_values(
    payoffs: list[list[float]],
    intervals: list[tuple[float, float]],
    weights: list[float],
    tolerance: float,
) -> tuple[list[float], list[str]]:
    """Return per-contract LP slack and a parallel list of direction labels.

    ``REPAIR_DIRECTION_INTERVAL_HIGH`` means the observed price interval lies
    above the LP-implied value (the family is over-priced on this contract;
    fees aside, the contract's interval would need to shift down to restore
    consistency). ``REPAIR_DIRECTION_INTERVAL_LOW`` means the inverse.
    """

    repairs: list[float] = []
    directions: list[str] = []
    for payoff_row, (lower, upper) in zip(payoffs, intervals):
        value = sum(payoff * weight for payoff, weight in zip(payoff_row, weights))
        under = max(0.0, lower - value - tolerance)
        over = max(0.0, value - upper - tolerance)
        repair = max(0.0, max(under, over))
        repairs.append(repair)
        if repair <= 1e-12:
            directions.append(REPAIR_DIRECTION_WITHIN)
        elif under >= over:
            directions.append(REPAIR_DIRECTION_INTERVAL_HIGH)
        else:
            directions.append(REPAIR_DIRECTION_INTERVAL_LOW)
    return repairs, directions


def _per_contract_repair_dict(contracts: list, repair_values: list[float]) -> dict[str, float]:
    return {
        contract.contract_id: round(max(0.0, float(repair_values[index])), 6)
        for index, contract in enumerate(contracts)
        if index < len(repair_values)
    }


def _per_contract_repair_directions_dict(
    contracts: list,
    repair_values: list[float],
    repair_directions: list[str],
    feasibility_status: str,
) -> dict[str, str]:
    directions: dict[str, str] = {}
    for index, contract in enumerate(contracts):
        if index >= len(repair_directions):
            directions[contract.contract_id] = REPAIR_DIRECTION_WITHIN
            continue
        direction = repair_directions[index]
        gap = repair_values[index] if index < len(repair_values) else 0.0
        if feasibility_status == "feasible" or gap <= 1e-12:
            directions[contract.contract_id] = REPAIR_DIRECTION_WITHIN
        elif direction in REPAIR_DIRECTIONS:
            directions[contract.contract_id] = direction
        else:
            directions[contract.contract_id] = REPAIR_DIRECTION_WITHIN
    return directions


def _worst_contract(per_contract_repair: dict[str, float]) -> tuple[str | None, float]:
    if not per_contract_repair:
        return None, 0.0
    positive = [
        (contract_id, float(gap))
        for contract_id, gap in per_contract_repair.items()
        if isinstance(gap, (int, float)) and not isinstance(gap, bool) and gap > 0
    ]
    if not positive:
        return None, 0.0
    contract_id, gap = sorted(positive, key=lambda item: (-item[1], item[0]))[0]
    return contract_id, gap


def _normalized_gap(bound_gap: float, contracts: list) -> float:
    if bound_gap <= 0:
        return 0.0
    scale = max(
        1.0,
        max(
            abs(float(contract.ask_bound if contract.ask_bound is not None else contract.observed_probability or 0.0))
            for contract in contracts
        )
        if contracts
        else 1.0,
    )
    return round(max(0.0, bound_gap) / scale, 6)


def _confidence(
    feasibility_status: str,
    hull_residual: float,
    structural_violations: list[str],
    input_mode: str,
) -> dict[str, Any]:
    mode_label = "bid/ask interval" if input_mode == BID_ASK_INTERVAL else "diagnostic midpoint fallback"
    if feasibility_status == "infeasible":
        if structural_violations:
            score = 0.88
            description = f"Structural inequality violated by {mode_label} inputs over fixture-defined finite states."
        else:
            score = 0.82
            description = f"Two-sided repair distance {hull_residual:.4f} exceeds tolerance for fixture-defined states."
    else:
        score = 0.78
        description = f"Probability inputs lie inside fixture-defined state payoff intervals."
    if input_mode == DIAGNOSTIC_MIDPOINT_FALLBACK:
        score = min(score, 0.55)
        description += " Midpoint fallback is non-actionable and requires external price bounds."
    return {"description": description, "score": round(score, 6)}


__all__ = [
    "ALLOWED_ACTIONS",
    "FEASIBILITY_STATUSES",
    "FeasibilityResult",
    "REPAIR_DIRECTIONS",
    "REPAIR_DIRECTION_INTERVAL_HIGH",
    "REPAIR_DIRECTION_INTERVAL_LOW",
    "REPAIR_DIRECTION_WITHIN",
    "REQUIRED_REVIEW_QUESTIONS",
    "check_no_arb_consistency",
]
