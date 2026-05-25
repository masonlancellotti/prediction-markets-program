from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ALLOWED_ACTIONS = ["WATCH", "MANUAL_REVIEW"]
DEFAULT_TOLERANCE = 0.03


@dataclass(frozen=True)
class BoundedMarket:
    market_id: str
    probability: float | None
    confidence: float = 0.9
    blockers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BoundedConstraint:
    constraint_id: str
    constraint_type: str
    market_ids: list[str]
    lower_bound: float | None = None
    upper_bound: float | None = None
    tolerance: float = DEFAULT_TOLERANCE
    confidence: float = 0.9
    review_note: str = "Review probability bounds and settlement wording."


def build_bounded_consistency_report(
    markets: dict[str, BoundedMarket],
    constraints: list[BoundedConstraint],
) -> dict[str, Any]:
    diagnostics = [evaluate_bounded_constraint(markets, constraint) for constraint in constraints]
    report = {
        "diagnostic_only": True,
        "allowed_actions": ALLOWED_ACTIONS,
        "diagnostic_count": len(diagnostics),
        "bounded_consistency_diagnostics": diagnostics,
    }
    _validate_report(report)
    return report


def evaluate_bounded_constraint(
    markets: dict[str, BoundedMarket],
    constraint: BoundedConstraint,
) -> dict[str, Any]:
    selected = [markets.get(market_id) for market_id in constraint.market_ids]
    blockers = _blockers(selected, constraint.market_ids)
    probabilities = [market.probability for market in selected if market is not None and market.probability is not None]

    if blockers:
        observed_value = sum(probabilities) if probabilities else 0.0
        return _diagnostic(
            constraint=constraint,
            observed_value=observed_value,
            lower_bound=constraint.lower_bound if constraint.lower_bound is not None else 0.0,
            upper_bound=constraint.upper_bound if constraint.upper_bound is not None else 1.0,
            bound_gap=0.0,
            violated=False,
            confidence_score=min(0.25, constraint.confidence),
            confidence_description="Bounded consistency check blocked by missing or ambiguous market data.",
            blockers=blockers,
        )

    if constraint.constraint_type == "sum_upper":
        observed = sum(probabilities)
        upper = _required_bound(constraint.upper_bound, "sum_upper")
        gap = observed - upper - constraint.tolerance
        return _diagnostic_for_gap(constraint, observed, 0.0, upper, gap, markets)

    if constraint.constraint_type == "sum_lower":
        observed = sum(probabilities)
        lower = _required_bound(constraint.lower_bound, "sum_lower")
        gap = lower - observed - constraint.tolerance
        return _diagnostic_for_gap(constraint, observed, lower, 1.0, gap, markets)

    if constraint.constraint_type == "child_parent":
        child, parent = probabilities[0], probabilities[1]
        gap = child - parent - constraint.tolerance
        return _diagnostic_for_gap(constraint, child, 0.0, parent, gap, markets)

    if constraint.constraint_type == "threshold_monotonicity":
        worst_gap = 0.0
        observed = probabilities[0]
        upper = min(probabilities[1:]) if len(probabilities) > 1 else probabilities[0]
        for stricter, looser in zip(probabilities, probabilities[1:]):
            worst_gap = max(worst_gap, stricter - looser - constraint.tolerance)
        return _diagnostic_for_gap(constraint, observed, 0.0, upper, worst_gap, markets)

    if constraint.constraint_type == "complement_sum":
        observed = sum(probabilities[:2])
        lower = 1.0 - constraint.tolerance
        upper = 1.0 + constraint.tolerance
        gap = max(lower - observed, observed - upper, 0.0)
        return _diagnostic_for_gap(constraint, observed, lower, upper, gap, markets, preadjusted=True)

    raise ValueError(f"unsupported bounded constraint type: {constraint.constraint_type}")


def _diagnostic_for_gap(
    constraint: BoundedConstraint,
    observed: float,
    lower: float,
    upper: float,
    raw_gap: float,
    markets: dict[str, BoundedMarket],
    *,
    preadjusted: bool = False,
) -> dict[str, Any]:
    gap = max(0.0, raw_gap if preadjusted else raw_gap)
    violated = gap > 1e-12
    confidence_score = _combined_confidence(constraint, markets)
    return _diagnostic(
        constraint=constraint,
        observed_value=observed,
        lower_bound=lower,
        upper_bound=upper,
        bound_gap=gap,
        violated=violated,
        confidence_score=confidence_score,
        confidence_description="Bounded probability consistency check over reviewed structural inputs.",
        blockers=[],
    )


def _diagnostic(
    *,
    constraint: BoundedConstraint,
    observed_value: float,
    lower_bound: float,
    upper_bound: float,
    bound_gap: float,
    violated: bool,
    confidence_score: float,
    confidence_description: str,
    blockers: list[str],
) -> dict[str, Any]:
    normalized_gap = _normalized_gap(bound_gap, lower_bound, upper_bound)
    action = "MANUAL_REVIEW" if violated and confidence_score >= 0.6 else "WATCH"
    return {
        "constraint_id": constraint.constraint_id,
        "constraint_type": constraint.constraint_type,
        "diagnostic_only": True,
        "allowed_actions": ALLOWED_ACTIONS,
        "max_action_cap": action,
        "diagnostic_priority": action,
        "market_ids": list(constraint.market_ids),
        "market_count": len(constraint.market_ids),
        "observed_value": round(observed_value, 6),
        "lower_bound": round(lower_bound, 6),
        "upper_bound": round(upper_bound, 6),
        "bound_gap": round(bound_gap, 6),
        "normalized_bound_gap": normalized_gap,
        "violated": violated,
        "confidence_basis": {
            "description": confidence_description,
            "score": round(max(0.0, min(1.0, confidence_score)), 6),
        },
        "blockers": list(blockers),
        "reason_for_review": constraint.review_note,
    }


def _blockers(selected: list[BoundedMarket | None], market_ids: list[str]) -> list[str]:
    blockers: list[str] = []
    if len(market_ids) < 2 or len(market_ids) > 8:
        blockers.append("market_count_outside_supported_range")
    for market_id, market in zip(market_ids, selected):
        if market is None:
            blockers.append(f"missing_market:{market_id}")
            continue
        if market.probability is None:
            blockers.append(f"missing_probability:{market_id}")
        blockers.extend(market.blockers)
    return sorted(set(blockers))


def _combined_confidence(constraint: BoundedConstraint, markets: dict[str, BoundedMarket]) -> float:
    scores = [constraint.confidence]
    for market_id in constraint.market_ids:
        if market_id in markets:
            scores.append(markets[market_id].confidence)
    return min(scores) if scores else constraint.confidence


def _required_bound(value: float | None, constraint_type: str) -> float:
    if value is None:
        raise ValueError(f"{constraint_type} requires a bound")
    return float(value)


def _normalized_gap(bound_gap: float, lower_bound: float, upper_bound: float) -> float:
    scale = max(abs(upper_bound - lower_bound), abs(upper_bound), 1.0)
    return round(max(0.0, bound_gap) / scale, 6)


def _validate_report(report: dict[str, Any]) -> None:
    if report["diagnostic_only"] is not True:
        raise ValueError("bounded consistency report must be diagnostic_only")
    if report["allowed_actions"] != ALLOWED_ACTIONS:
        raise ValueError("bounded consistency report actions must be WATCH and MANUAL_REVIEW only")
    for diagnostic in report["bounded_consistency_diagnostics"]:
        if diagnostic["diagnostic_only"] is not True:
            raise ValueError("bounded consistency diagnostic must be diagnostic_only")
        if diagnostic["max_action_cap"] not in ALLOWED_ACTIONS:
            raise ValueError("bounded consistency diagnostic action is not allowed")
