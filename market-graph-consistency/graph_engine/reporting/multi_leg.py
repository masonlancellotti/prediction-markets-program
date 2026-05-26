from __future__ import annotations

from collections import defaultdict
from typing import Any

from graph_engine.models import ExclusionCompleteness, GraphSnapshot, MarketNode, RelationshipType
from graph_engine.reporting.schema_validation import validate_multi_leg_constraints_contract
from graph_engine.thresholds import ordered_ladder_candidates, threshold_candidate_from_node, threshold_group_blockers


ALLOWED_ACTIONS = ["WATCH", "MANUAL_REVIEW"]
DEFAULT_TOLERANCE = 0.03


def build_multi_leg_constraints_report(snapshot: GraphSnapshot) -> dict[str, Any]:
    constraints = [
        *_exclusion_constraints(snapshot),
        *_threshold_ladder_constraints(snapshot),
        *_complement_parent_child_constraints(snapshot),
        *_nested_subset_chain_constraints(snapshot),
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
    grouped: dict[tuple[str, str, str, str], list] = defaultdict(list)
    for node in snapshot.nodes.values():
        if "threshold" not in node.themes:
            continue
        candidate = threshold_candidate_from_node(node)
        if candidate.threshold is None or not candidate.observable or not candidate.source or not candidate.window:
            continue
        grouped[(candidate.family, candidate.observable, candidate.source, candidate.window)].append(candidate)

    constraints: list[dict[str, Any]] = []
    for (family, observable, settlement_source, window), candidates in grouped.items():
        if len(candidates) < 3:
            continue
        blockers = threshold_group_blockers(candidates)
        ordered_candidates = (
            ordered_ladder_candidates(candidates)
            if not blockers
            else sorted(candidates, key=lambda item: item.threshold or 0.0, reverse=True)
        )
        ordered = [candidate.node for candidate in ordered_candidates]
        unit = ordered_candidates[0].unit if ordered_candidates else None
        comparator = ordered_candidates[0].comparator if ordered_candidates else None
        if blockers:
            constraints.append(
                _constraint(
                    constraint_id=f"multi_leg:threshold_ladder_blocked:{_threshold_key_id((family, observable, settlement_source, window))}",
                    constraint_type="threshold_ladder",
                    constraint_family="threshold_sequence",
                    market_ids=[node.market_id for node in ordered],
                    bound_gap=0.0,
                    diagnostic_priority="WATCH",
                    review_reason="Threshold sequence is blocked because comparator orientation or units are not review-ready.",
                    observed_value=0.0,
                    expected_lower_bound=0.0,
                    expected_upper_bound=0.0,
                    confidence_score=0.2,
                    confidence_basis="Saved fixture threshold markets require comparator and unit review before monotonic diagnostics.",
                    required_review_questions=[
                        "Do all ladder markets use the same threshold comparator orientation?",
                        "Do all ladder markets use the same explicit threshold unit?",
                        "Can the typed threshold keys be verified without relying on title similarity?",
                    ],
                    blockers=blockers,
                    constraint_violation=False,
                    structural_inconsistency=False,
                )
            )
            continue
        worst_gap = 0.0
        for narrower, broader in zip(ordered, ordered[1:]):
            worst_gap = max(worst_gap, narrower.probability - broader.probability - DEFAULT_TOLERANCE)
        if worst_gap <= 1e-12:
            continue
        constraints.append(
            _constraint(
                constraint_id=f"multi_leg:threshold_ladder:{observable}:{settlement_source}:{window}",
                constraint_type="threshold_ladder",
                constraint_family="threshold_sequence",
                market_ids=[node.market_id for node in ordered],
                bound_gap=worst_gap,
                diagnostic_priority="MANUAL_REVIEW",
                review_reason="Monotonic threshold sequence has a stricter threshold above a looser threshold after tolerance.",
                observed_value=max(node.probability for node in ordered),
                expected_lower_bound=0.0,
                expected_upper_bound=min(node.probability for node in ordered),
                confidence_score=0.85,
                confidence_basis=(
                    "Saved fixture markets share formula family, observable, settlement source, window, "
                    f"comparator {comparator}, and unit {unit} with a monotonic numeric threshold sequence."
                ),
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


def _nested_subset_chain_constraints(snapshot: GraphSnapshot) -> list[dict[str, Any]]:
    child_to_parent = _subset_parent_map(snapshot)
    constraints: list[dict[str, Any]] = []
    seen_chain_ids: set[str] = set()

    for start_id in sorted(child_to_parent):
        chain = _subset_chain_from(start_id, child_to_parent)
        if len(chain) < 3:
            continue
        chain_key = "->".join(chain)
        if chain_key in seen_chain_ids:
            continue
        seen_chain_ids.add(chain_key)

        nodes = [snapshot.nodes[market_id] for market_id in chain]
        worst_gap = 0.0
        for child, parent in zip(nodes, nodes[1:]):
            worst_gap = max(worst_gap, child.probability - parent.probability - DEFAULT_TOLERANCE)
        if worst_gap <= 1e-12:
            continue

        blockers = _chain_blockers(nodes)
        confidence_score = min(
            edge.confidence
            for child_id, parent_id in zip(chain, chain[1:])
            for edge in child_to_parent[child_id]
            if edge.dst_market_id == parent_id
        )
        constraints.append(
            _constraint(
                constraint_id=f"multi_leg:nested_subset_chain:{chain_key}",
                constraint_type="nested_subset_chain",
                constraint_family="compound_bound",
                market_ids=chain,
                bound_gap=worst_gap,
                diagnostic_priority="WATCH" if blockers else "MANUAL_REVIEW",
                review_reason="Nested subset chain has a narrower child above a broader parent after tolerance.",
                observed_value=max(node.probability for node in nodes),
                expected_lower_bound=0.0,
                expected_upper_bound=min(node.probability for node in nodes[1:]),
                confidence_score=confidence_score,
                confidence_basis="Saved fixture subset edges form a three-or-more-market nested chain.",
                required_review_questions=[
                    "Does each child outcome strictly imply the next broader parent outcome?",
                    "Do all markets in the chain share compatible source and settlement timing?",
                    "Is any edge in the chain only a wording similarity rather than a subset relation?",
                ],
                blockers=blockers,
            )
        )
    return constraints


def _subset_parent_map(snapshot: GraphSnapshot):
    child_to_parent = defaultdict(list)
    for edge in snapshot.edges:
        if edge.relation == RelationshipType.SUBSET and edge.src_market_id in snapshot.nodes and edge.dst_market_id in snapshot.nodes:
            child_to_parent[edge.src_market_id].append(edge)
    return child_to_parent


def _subset_chain_from(start_id: str, child_to_parent) -> list[str]:
    chain = [start_id]
    seen = {start_id}
    current = start_id
    while current in child_to_parent:
        parents = sorted(child_to_parent[current], key=lambda edge: edge.dst_market_id)
        parent_id = parents[0].dst_market_id
        if parent_id in seen:
            break
        chain.append(parent_id)
        seen.add(parent_id)
        current = parent_id
    return chain


def _chain_blockers(nodes: list[MarketNode]) -> list[str]:
    blockers: list[str] = []
    observables = {node.observable for node in nodes if node.observable}
    sources = {node.settlement_source for node in nodes if node.settlement_source}
    windows = {node.window for node in nodes if node.window}
    if any(not node.observable for node in nodes) or len(observables) > 1:
        blockers.append("observable_mismatch")
    if any(not node.settlement_source for node in nodes) or len(sources) > 1:
        blockers.append("settlement_source_mismatch")
    if any(not node.window for node in nodes) or len(windows) > 1:
        blockers.append("window_mismatch")
    if any(node.reference_only for node in nodes):
        blockers.append("reference_only_node")
    return sorted(set(blockers))


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
    constraint_violation: bool = True,
    structural_inconsistency: bool = True,
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
        "constraint_violation": constraint_violation,
        "structural_inconsistency": structural_inconsistency,
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


def _threshold_key_id(key: tuple[Any, ...]) -> str:
    return ":".join(str(item).replace(" ", "_").replace(":", "_").replace("/", "_") for item in key if item not in {None, ""})
