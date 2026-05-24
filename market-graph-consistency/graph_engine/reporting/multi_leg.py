from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from graph_engine.models import ExclusionCompleteness, GraphSnapshot, MarketNode, RelationshipType
from graph_engine.reporting.schema_validation import validate_multi_leg_constraints_contract


ALLOWED_ACTIONS = ["WATCH", "MANUAL_REVIEW"]
DEFAULT_TOLERANCE = 0.03


def build_multi_leg_constraints_report(snapshot: GraphSnapshot) -> dict[str, Any]:
    constraints = [
        *_exclusion_constraints(snapshot),
        *_threshold_ladder_constraints(snapshot),
        *_complement_parent_child_constraints(snapshot),
    ]
    constraints = sorted(
        constraints,
        key=lambda item: (-item["normalized_bound_gap"], -item["confidence_basis"]["score"], item["constraint_id"]),
    )
    for index, constraint in enumerate(constraints, start=1):
        constraint["diagnostic_rank"] = index
    report = {
        "diagnostic_only": True,
        "allowed_actions": ALLOWED_ACTIONS,
        "constraint_count": len(constraints),
        "multi_leg_constraints": constraints,
    }
    validate_multi_leg_constraints_contract(report)
    return report


def _exclusion_constraints(snapshot: GraphSnapshot) -> list[dict[str, Any]]:
    constraints: list[dict[str, Any]] = []
    for exclusion in snapshot.exclusion_sets:
        if len(exclusion.member_market_ids) < 3:
            continue
        nodes = [snapshot.nodes[market_id] for market_id in exclusion.member_market_ids]
        raw_sum = sum(node.probability for node in nodes)
        tolerance = exclusion.tolerance if exclusion.tolerance is not None else DEFAULT_TOLERANCE
        bound_gap = max(0.0, raw_sum - 1.0 - tolerance)
        if bound_gap <= 1e-12:
            continue
        if _is_range_bucket(nodes):
            constraint_type = "range_bucket_partition"
            family = "range_partition"
            reason = "Range bucket partition has aggregate probability above its structural upper bound; review bucket overlap or missing gap handling."
            questions = [
                "Do the listed buckets cover distinct non-overlapping ranges?",
                "Is any bucket boundary inclusive on both neighboring markets?",
                "Is the fixture missing a bucket that changes partition interpretation?",
            ]
        elif exclusion.completeness == ExclusionCompleteness.PARTITION:
            constraint_type = "exhaustive_group"
            family = "outcome_partition"
            reason = "Exhaustive group sums above one after tolerance."
            questions = [
                "Are all outcomes in the group mutually exclusive?",
                "Is the group truly exhaustive under one settlement source?",
                "Are any markets duplicated or using a different settlement window?",
            ]
        else:
            constraint_type = "mutually_exclusive_group"
            family = "mutual_exclusion"
            reason = "Mutually exclusive group sums above one after tolerance."
            questions = [
                "Can more than one listed outcome resolve yes under the rules?",
                "Do all markets share the same event scope?",
                "Are any members correlated rather than mutually exclusive?",
            ]
        constraints.append(
            _constraint(
                constraint_id=f"multi_leg:{constraint_type}:{exclusion.set_id}",
                constraint_type=constraint_type,
                constraint_family=family,
                market_ids=exclusion.member_market_ids,
                bound_gap=bound_gap,
                diagnostic_priority="MANUAL_REVIEW",
                review_reason=reason,
                observed_value=raw_sum,
                expected_lower_bound=1.0 if exclusion.completeness == ExclusionCompleteness.PARTITION else 0.0,
                expected_upper_bound=1.0,
                confidence_score=0.9 if exclusion.completeness == ExclusionCompleteness.PARTITION else 0.8,
                confidence_basis="Saved fixture exclusion set with three or more markets.",
                required_review_questions=questions,
                blockers=[] if exclusion.completeness == ExclusionCompleteness.PARTITION else ["exhaustiveness_not_required"],
            )
        )
    return constraints


def _threshold_ladder_constraints(snapshot: GraphSnapshot) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[MarketNode]] = defaultdict(list)
    for node in snapshot.nodes.values():
        if "threshold" not in node.themes:
            continue
        threshold = _threshold_value(node)
        if threshold is None or not node.observable or not node.settlement_source or not node.window:
            continue
        grouped[(node.observable, node.settlement_source, node.window)].append(node)

    constraints: list[dict[str, Any]] = []
    for (observable, settlement_source, window), nodes in grouped.items():
        if len(nodes) < 3:
            continue
        ordered = sorted(nodes, key=lambda item: _threshold_value(item) or 0.0, reverse=True)
        worst_gap = 0.0
        for narrower, broader in zip(ordered, ordered[1:]):
            worst_gap = max(worst_gap, narrower.probability - broader.probability - DEFAULT_TOLERANCE)
        if worst_gap <= 1e-12:
            continue
        constraints.append(
            _constraint(
                constraint_id=f"multi_leg:threshold_ladder:{observable}:{settlement_source}:{window}",
                constraint_type="threshold_ladder",
                constraint_family="ordered_thresholds",
                market_ids=[node.market_id for node in ordered],
                bound_gap=worst_gap,
                diagnostic_priority="MANUAL_REVIEW",
                review_reason="Ordered threshold ladder has a stricter threshold above a looser threshold after tolerance.",
                observed_value=max(node.probability for node in ordered),
                expected_lower_bound=0.0,
                expected_upper_bound=min(node.probability for node in ordered),
                confidence_score=0.85,
                confidence_basis="Saved fixture markets share observable, settlement source, and window with ordered numeric thresholds.",
                required_review_questions=[
                    "Do all ladder markets share the same settlement source and window?",
                    "Are the threshold comparators oriented the same way?",
                    "Does each stricter threshold imply the looser threshold?",
                ],
                blockers=[],
            )
        )
    return constraints


def _complement_parent_child_constraints(snapshot: GraphSnapshot) -> list[dict[str, Any]]:
    subset_edges = [
        edge
        for edge in snapshot.edges
        if edge.relation == RelationshipType.SUBSET
        and edge.src_market_id in snapshot.nodes
        and edge.dst_market_id in snapshot.nodes
    ]
    complement_edges = [
        edge
        for edge in snapshot.edges
        if edge.relation == RelationshipType.COMPLEMENT
        and edge.src_market_id in snapshot.nodes
        and edge.dst_market_id in snapshot.nodes
    ]
    constraints: list[dict[str, Any]] = []
    for complement in complement_edges:
        complement_members = {complement.src_market_id, complement.dst_market_id}
        for subset in subset_edges:
            if subset.dst_market_id not in complement_members:
                continue
            child = snapshot.nodes[subset.src_market_id]
            parent = snapshot.nodes[subset.dst_market_id]
            other_member = next(iter(complement_members - {subset.dst_market_id}))
            other = snapshot.nodes[other_member]
            complement_sum = parent.probability + other.probability
            subset_gap = child.probability - parent.probability
            complement_gap = complement_sum - 1.0
            bound_gap = max(0.0, subset_gap, complement_gap) - DEFAULT_TOLERANCE
            if bound_gap <= 1e-12:
                continue
            constraints.append(
                _constraint(
                    constraint_id=f"multi_leg:complement_parent_child:{subset.edge_id}:{complement.edge_id}",
                    constraint_type="complement_parent_child",
                    constraint_family="compound_bound",
                    market_ids=[child.market_id, parent.market_id, other.market_id],
                    bound_gap=bound_gap,
                    diagnostic_priority="MANUAL_REVIEW",
                    review_reason="Child subset and complement pair are jointly inconsistent after tolerance.",
                    observed_value=max(child.probability, complement_sum),
                    expected_lower_bound=0.0,
                    expected_upper_bound=max(parent.probability, 1.0),
                    confidence_score=min(subset.confidence, complement.confidence),
                    confidence_basis="Saved fixture combines a subset relation with a complement relation.",
                    required_review_questions=[
                        "Is the child market strictly contained by the parent market?",
                        "Are the parent and paired market true complements under one rule set?",
                        "Do all three markets share compatible settlement timing?",
                    ],
                    blockers=[],
                )
            )
    return constraints


def _constraint(
    *,
    constraint_id: str,
    constraint_type: str,
    constraint_family: str,
    market_ids: list[str],
    bound_gap: float,
    diagnostic_priority: str,
    review_reason: str,
    observed_value: float,
    expected_lower_bound: float,
    expected_upper_bound: float,
    confidence_score: float,
    confidence_basis: str,
    required_review_questions: list[str],
    blockers: list[str],
) -> dict[str, Any]:
    rounded_gap = round(max(0.0, bound_gap), 6)
    normalized_bound_gap = _normalized_gap(rounded_gap, expected_lower_bound, expected_upper_bound)
    return {
        "constraint_id": constraint_id,
        "constraint_type": constraint_type,
        "constraint_family": constraint_family,
        "market_ids": list(market_ids),
        "market_count": len(market_ids),
        "diagnostic_only": True,
        "allowed_actions": ALLOWED_ACTIONS,
        "max_action_cap": diagnostic_priority,
        "diagnostic_priority": diagnostic_priority,
        "constraint_violation": True,
        "structural_inconsistency": True,
        "bound_gap": rounded_gap,
        "normalized_bound_gap": normalized_bound_gap,
        "observed_value": round(observed_value, 6),
        "expected_lower_bound": round(expected_lower_bound, 6),
        "expected_upper_bound": round(expected_upper_bound, 6),
        "expected_bound": round(expected_upper_bound, 6),
        "confidence_basis": {
            "description": confidence_basis,
            "score": round(max(0.0, min(1.0, confidence_score)), 6),
        },
        "required_review_questions": list(required_review_questions),
        "blockers": list(blockers),
        "review_reason": review_reason,
    }


def _normalized_gap(bound_gap: float, expected_lower_bound: float, expected_upper_bound: float) -> float:
    scale = max(abs(expected_upper_bound - expected_lower_bound), abs(expected_upper_bound), 1.0)
    return round(bound_gap / scale, 6)


def _is_range_bucket(nodes: list[MarketNode]) -> bool:
    return all("range-bucket" in node.themes for node in nodes)


def _threshold_value(node: MarketNode) -> float | None:
    match = re.search(r"above\s+([0-9]+(?:\.[0-9]+)?)", node.canonical_text.lower())
    if not match:
        return None
    return float(match.group(1))
