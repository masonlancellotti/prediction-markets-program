from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from graph_engine.models import ExclusionCompleteness, GraphSnapshot, MarketNode, RelationshipType
from graph_engine.reporting.schema_validation import DIAGNOSTIC_HINT_ACTIONS, SchemaValidationError
from graph_engine.thresholds import (
    compile_market_formula_rows,
    ordered_ladder_candidates,
    threshold_candidate_from_node,
    threshold_group_blockers,
)


CONSTRAINT_TYPES = {
    "complement_pair",
    "subset_superset",
    "threshold_ladder",
    "mutually_exclusive_group",
    "exhaustive_partition",
    "range_bucket_partition",
}
CONSTRAINT_TO_PAYOFF_STATE_TYPES = {
    "complement_pair": {"complement_pair"},
    "subset_superset": {"child_parent_chain"},
    "threshold_ladder": {"threshold_ladder"},
    "mutually_exclusive_group": {"mutually_exclusive_group"},
    "exhaustive_partition": {"exhaustive_group"},
    "range_bucket_partition": {"range_bucket_partition"},
}
CONFIDENCE_TIERS = {"HIGH", "MEDIUM", "LOW"}
DEFAULT_TOLERANCE = 0.03
DEFAULT_STALE_SECONDS = 24 * 60 * 60
STALE_OR_MISSING_QUOTE_BLOCKERS = {
    "missing_probability_input",
    "diagnostic_midpoint_not_actionable",
    "missing_bid_or_ask",
    "missing_quote_timestamp",
    "stale_quote",
}
REPORT_BANNER = (
    "Saved-file-only bounded probability consistency diagnostics. Rows are formal review checks, "
    "not evaluator input or execution permission."
)
WHY_REVIEW_ONLY = (
    "Probability constraint diagnostic only; settlement evidence, source equality, fee/depth review, "
    "and freshness review must be completed outside this report."
)


def build_probability_constraints_report(
    snapshot: GraphSnapshot,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    rows.extend(_edge_constraints(snapshot, tolerance=tolerance, stale_seconds=stale_seconds))
    rows.extend(_threshold_ladder_constraints(snapshot, tolerance=tolerance, stale_seconds=stale_seconds))
    rows.extend(_exclusion_constraints(snapshot, tolerance=tolerance, stale_seconds=stale_seconds))
    rows = _dedupe_rows(rows)
    rows = sorted(rows, key=lambda row: (-int(row["violated"]), -row["severity_score"], row["constraint_id"]))
    for index, row in enumerate(rows, start=1):
        row["diagnostic_rank"] = index
    counts = Counter(row["constraint_type"] for row in rows)
    summary = _report_summary(rows, counts)
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "banner": REPORT_BANNER,
        "snapshot_id": snapshot.snapshot_id,
        "summary": summary,
        "constraint_count": len(rows),
        "violation_count": sum(1 for row in rows if row["violated"]),
        "counts_by_constraint_type": dict(sorted(counts.items())),
        "probability_constraints": rows,
    }
    validate_probability_constraints_report(report)
    return report


def write_probability_constraints_report(
    snapshot: GraphSnapshot,
    output_path: Path | str,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
) -> dict[str, Any]:
    report = build_probability_constraints_report(snapshot, tolerance=tolerance, stale_seconds=stale_seconds)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def validate_probability_constraints_report(report: dict[str, Any]) -> None:
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("probability constraints report must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("probability constraints report must not affect evaluator gates")
    if report.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError("probability constraints actions must be WATCH and MANUAL_REVIEW only")
    rows = report.get("probability_constraints")
    if not isinstance(rows, list):
        raise SchemaValidationError("probability_constraints must be a list")
    if report.get("constraint_count") != len(rows):
        raise SchemaValidationError("constraint_count must match probability_constraints")
    if report.get("violation_count") != sum(1 for row in rows if row.get("violated") is True):
        raise SchemaValidationError("violation_count must match violated rows")
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise SchemaValidationError("summary must be an object")
    if summary.get("total_constraints_checked") != len(rows):
        raise SchemaValidationError("summary.total_constraints_checked must match probability_constraints")
    if summary.get("total_violations") != report.get("violation_count"):
        raise SchemaValidationError("summary.total_violations must match violation_count")
    for index, row in enumerate(rows):
        _validate_row(row, f"probability_constraints[{index}]")


def _edge_constraints(snapshot: GraphSnapshot, *, tolerance: float, stale_seconds: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_complements: set[frozenset[str]] = set()
    for edge in snapshot.edges:
        if edge.src_market_id not in snapshot.nodes or edge.dst_market_id not in snapshot.nodes:
            continue
        if edge.relation == RelationshipType.COMPLEMENT:
            pair_key = frozenset({edge.src_market_id, edge.dst_market_id})
            if pair_key in seen_complements:
                continue
            seen_complements.add(pair_key)
            nodes = [snapshot.nodes[edge.src_market_id], snapshot.nodes[edge.dst_market_id]]
            rows.append(
                _sum_constraint_row(
                    snapshot,
                    constraint_id=f"probability:complement_pair:{edge.edge_id}",
                    constraint_type="complement_pair",
                    nodes=nodes,
                    lower_bound=1.0,
                    upper_bound=1.0,
                    inequality_checked="P(A) + P(not A) = 1",
                    evidence=f"graph_edge:{edge.edge_id}:{edge.source.value}",
                    base_confidence=edge.confidence,
                    tolerance=tolerance,
                    stale_seconds=stale_seconds,
                    implied_review_direction="COMPLEMENT_SUM_REVIEW",
                )
            )
        elif edge.relation in {RelationshipType.SUBSET, RelationshipType.IMPLICATION, RelationshipType.SUPERSET}:
            if edge.relation == RelationshipType.SUPERSET:
                subset = snapshot.nodes[edge.dst_market_id]
                superset = snapshot.nodes[edge.src_market_id]
            else:
                subset = snapshot.nodes[edge.src_market_id]
                superset = snapshot.nodes[edge.dst_market_id]
            rows.append(
                _subset_row(
                    snapshot,
                    constraint_id=f"probability:subset_superset:{edge.edge_id}",
                    subset=subset,
                    superset=superset,
                    evidence=f"graph_edge:{edge.edge_id}:{edge.source.value}",
                    base_confidence=edge.confidence,
                    tolerance=tolerance,
                    stale_seconds=stale_seconds,
                )
            )
    return rows


def _threshold_ladder_constraints(snapshot: GraphSnapshot, *, tolerance: float, stale_seconds: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list] = defaultdict(list)
    for node in snapshot.nodes.values():
        if "threshold" not in node.themes:
            continue
        candidate = threshold_candidate_from_node(node)
        if candidate.threshold is None or not candidate.observable or not candidate.source or not candidate.window:
            continue
        grouped[(candidate.family, candidate.observable, candidate.source, candidate.window)].append(candidate)
    rows: list[dict[str, Any]] = []
    for (family, observable, source, window), candidates in grouped.items():
        if len(candidates) < 3 or len(candidates) > 8:
            continue
        blockers = threshold_group_blockers(candidates)
        ordered_candidates = (
            ordered_ladder_candidates(candidates)
            if not blockers
            else sorted(candidates, key=lambda candidate: candidate.threshold or 0.0, reverse=True)
        )
        ordered = [candidate.node for candidate in ordered_candidates]
        unit = ordered_candidates[0].unit if ordered_candidates else None
        comparator = ordered_candidates[0].comparator if ordered_candidates else None
        price_blockers = _price_blockers(snapshot, ordered, stale_seconds=stale_seconds)
        if blockers:
            gap_details = _upper_bound_gap_details(
                None,
                upper_bound=0.0,
                tolerance=tolerance,
                gap_formula="blocked: threshold ladder requires one comparator orientation and explicit matching units",
            )
            rows.append(
                _row(
                    snapshot=snapshot,
                    constraint_id=(
                        f"probability:threshold_ladder_blocked:{_threshold_key_id((family, observable, source, window))}"
                    ),
                    constraint_type="threshold_ladder",
                    nodes=ordered,
                    probability_inputs=_probability_inputs(snapshot, ordered, stale_seconds=stale_seconds),
                    inequality_checked="Threshold ladder blocked pending comparator and unit review",
                    observed_value=None,
                    expected_lower_bound=None,
                    expected_upper_bound=0.0,
                    observed_gap=0.0,
                    gap_details=gap_details,
                    violated=False,
                    evidence=f"threshold_family_blocked:{family}:{observable}:{source}:{window}",
                    base_confidence=0.2,
                    review_blockers=blockers + price_blockers,
                    implied_review_direction="NO_REVIEW_DIRECTION",
                )
            )
            continue
        values = [_probability_and_source(node)[0] for node in ordered]
        if any(value is None for value in values):
            raw_difference = None
        else:
            raw_differences = [float(stricter) - float(looser) for stricter, looser in zip(values, values[1:])]
            raw_difference = max(raw_differences) if raw_differences else 0.0
        gap_details = _upper_bound_gap_details(
            raw_difference,
            upper_bound=0.0,
            tolerance=tolerance,
            gap_formula="max(0, max(P(stricter_threshold) - P(looser_threshold)) - tolerance)",
        )
        observed_gap = gap_details["violation_amount_after_tolerance"]
        observed_value = gap_details["raw_sum_or_difference"]
        violated = observed_gap > 1e-12
        rows.append(
            _row(
                snapshot=snapshot,
                constraint_id=(
                    "probability:threshold_ladder:"
                    f"{_threshold_key_id((family, observable, source, window, comparator, unit))}"
                ),
                constraint_type="threshold_ladder",
                nodes=ordered,
                probability_inputs=_probability_inputs(snapshot, ordered, stale_seconds=stale_seconds),
                inequality_checked="For ordered thresholds, P(stricter threshold) <= P(looser threshold)",
                observed_value=observed_value,
                expected_lower_bound=None,
                expected_upper_bound=0.0,
                observed_gap=observed_gap,
                gap_details=gap_details,
                violated=violated,
                evidence=f"threshold_family:{family}:{observable}:{source}:{window}:{comparator}:{unit}",
                base_confidence=0.86,
                review_blockers=price_blockers,
                implied_review_direction="STRICTER_THRESHOLD_HIGH_RELATIVE_TO_LOOSER" if violated else "NO_REVIEW_DIRECTION",
            )
        )
    return rows


def _exclusion_constraints(snapshot: GraphSnapshot, *, tolerance: float, stale_seconds: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for exclusion in snapshot.exclusion_sets:
        if len(exclusion.member_market_ids) < 3 or len(exclusion.member_market_ids) > 8:
            continue
        nodes = [snapshot.nodes[market_id] for market_id in exclusion.member_market_ids if market_id in snapshot.nodes]
        if len(nodes) != len(exclusion.member_market_ids):
            continue
        if exclusion.completeness == ExclusionCompleteness.PARTITION and _is_range_bucket(nodes):
            constraint_type = "range_bucket_partition"
            lower_bound = 1.0
            upper_bound = 1.0
            inequality = "Range bucket partition requires sum(P(bucket_i)) = 1"
            direction = "RANGE_BUCKET_SUM_REVIEW"
            confidence = 0.9
        elif exclusion.completeness == ExclusionCompleteness.PARTITION:
            constraint_type = "exhaustive_partition"
            lower_bound = 1.0
            upper_bound = 1.0
            inequality = "Explicit exhaustive partition requires sum(P(outcome_i)) = 1"
            direction = "EXHAUSTIVE_GROUP_SUM_REVIEW"
            confidence = 0.9
        else:
            constraint_type = "mutually_exclusive_group"
            lower_bound = 0.0
            upper_bound = 1.0
            inequality = "Mutually exclusive group requires sum(P(outcome_i)) <= 1"
            direction = "MUTUALLY_EXCLUSIVE_GROUP_SUM_REVIEW"
            confidence = 0.82
        rows.append(
            _sum_constraint_row(
                snapshot,
                constraint_id=f"probability:{constraint_type}:{exclusion.set_id}",
                constraint_type=constraint_type,
                nodes=nodes,
                lower_bound=lower_bound,
                upper_bound=upper_bound,
                inequality_checked=inequality,
                evidence=f"exclusion_set:{exclusion.set_id}:{exclusion.completeness.value}",
                base_confidence=confidence,
                tolerance=tolerance,
                stale_seconds=stale_seconds,
                implied_review_direction=direction,
            )
        )
    return rows


def _subset_row(
    snapshot: GraphSnapshot,
    *,
    constraint_id: str,
    subset: MarketNode,
    superset: MarketNode,
    evidence: str,
    base_confidence: float,
    tolerance: float,
    stale_seconds: int,
) -> dict[str, Any]:
    nodes = [subset, superset]
    subset_probability = _probability_and_source(subset)[0]
    superset_probability = _probability_and_source(superset)[0]
    blockers = _price_blockers(snapshot, nodes, stale_seconds=stale_seconds)
    if subset_probability is None or superset_probability is None:
        raw_difference = None
    else:
        raw_difference = subset_probability - superset_probability
    gap_details = _upper_bound_gap_details(
        raw_difference,
        upper_bound=0.0,
        tolerance=tolerance,
        gap_formula="max(0, (P(subset) - P(superset)) - tolerance)",
    )
    interval_bound_check = _subset_superset_interval_bounds(subset, superset, tolerance=tolerance)
    observed_gap = gap_details["violation_amount_after_tolerance"]
    observed_value = gap_details["raw_sum_or_difference"]
    violated = observed_gap > 1e-12
    row = _row(
        snapshot=snapshot,
        constraint_id=constraint_id,
        constraint_type="subset_superset",
        nodes=nodes,
        probability_inputs=_probability_inputs(snapshot, nodes, stale_seconds=stale_seconds),
        inequality_checked="P(subset) <= P(superset)",
        observed_value=observed_value,
        expected_lower_bound=None,
        expected_upper_bound=0.0,
        observed_gap=observed_gap,
        gap_details=gap_details,
        violated=violated,
        evidence=evidence,
        base_confidence=base_confidence,
        review_blockers=blockers,
        implied_review_direction="SUBSET_HIGH_RELATIVE_TO_SUPERSET" if violated else "NO_REVIEW_DIRECTION",
    )
    row["interval_bound_check"] = interval_bound_check
    return row


def _subset_superset_interval_bounds(
    subset: MarketNode,
    superset: MarketNode,
    *,
    tolerance: float,
) -> dict[str, Any]:
    """Return interval-aware optimistic/conservative gaps for P(subset) - P(superset).

    The conservative gap pairs the highest plausible subset probability with the
    lowest plausible superset probability (worst case for the inequality), while
    the optimistic gap pairs the lowest plausible subset with the highest
    plausible superset (best case). When both gaps exceed tolerance, the
    violation is robust to bid/ask uncertainty and not an artefact of a
    midpoint snapshot.
    """

    subset_bounds = _interval_bounds(subset)
    superset_bounds = _interval_bounds(superset)
    if subset_bounds is None or superset_bounds is None:
        return {
            "available": False,
            "conservative_gap": None,
            "optimistic_gap": None,
            "interval_violation_robust_to_bid_ask_uncertainty": False,
            "blocker": "missing_bid_or_ask_interval_on_one_or_both_markets",
            "tolerance": round(tolerance, 6),
        }
    subset_low, subset_high = subset_bounds
    superset_low, superset_high = superset_bounds
    conservative_gap = max(0.0, (subset_high - superset_low) - tolerance)
    optimistic_gap = max(0.0, (subset_low - superset_high) - tolerance)
    return {
        "available": True,
        "subset_interval": [round(subset_low, 6), round(subset_high, 6)],
        "superset_interval": [round(superset_low, 6), round(superset_high, 6)],
        "conservative_gap": round(conservative_gap, 6),
        "optimistic_gap": round(optimistic_gap, 6),
        "interval_violation_robust_to_bid_ask_uncertainty": optimistic_gap > 1e-12,
        "tolerance": round(tolerance, 6),
    }


def _interval_bounds(node: MarketNode) -> tuple[float, float] | None:
    if node.bid is None or node.ask is None:
        return None
    lower = min(float(node.bid), float(node.ask))
    upper = max(float(node.bid), float(node.ask))
    return lower, upper


def _sum_constraint_row(
    snapshot: GraphSnapshot,
    *,
    constraint_id: str,
    constraint_type: str,
    nodes: list[MarketNode],
    lower_bound: float,
    upper_bound: float,
    inequality_checked: str,
    evidence: str,
    base_confidence: float,
    tolerance: float,
    stale_seconds: int,
    implied_review_direction: str,
) -> dict[str, Any]:
    values = [_probability_and_source(node)[0] for node in nodes]
    blockers = _price_blockers(snapshot, nodes, stale_seconds=stale_seconds)
    if any(value is None for value in values):
        observed_value = None
    else:
        observed_value = sum(float(value) for value in values if value is not None)
    gap_details = _sum_gap_details(
        observed_value,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        tolerance=tolerance,
    )
    observed_gap = gap_details["violation_amount_after_tolerance"]
    violated = observed_gap > 1e-12
    return _row(
        snapshot=snapshot,
        constraint_id=constraint_id,
        constraint_type=constraint_type,
        nodes=nodes,
        probability_inputs=_probability_inputs(snapshot, nodes, stale_seconds=stale_seconds),
        inequality_checked=inequality_checked,
        observed_value=observed_value,
        expected_lower_bound=lower_bound,
        expected_upper_bound=upper_bound,
        observed_gap=observed_gap,
        gap_details=gap_details,
        violated=violated,
        evidence=evidence,
        base_confidence=base_confidence,
        review_blockers=blockers,
        implied_review_direction=implied_review_direction if violated else "NO_REVIEW_DIRECTION",
    )


def _row(
    *,
    snapshot: GraphSnapshot,
    constraint_id: str,
    constraint_type: str,
    nodes: list[MarketNode],
    probability_inputs: list[dict[str, Any]],
    inequality_checked: str,
    observed_value: float | None,
    expected_lower_bound: float | None,
    expected_upper_bound: float | None,
    observed_gap: float,
    gap_details: dict[str, Any],
    violated: bool,
    evidence: str,
    base_confidence: float,
    review_blockers: list[str],
    implied_review_direction: str,
) -> dict[str, Any]:
    confidence_tier = _confidence_tier(violated, observed_gap, review_blockers)
    severity_score = _severity_score(violated, observed_gap, base_confidence, review_blockers)
    bridge = _payoff_state_bridge(constraint_type, nodes, probability_inputs)
    row = {
        "constraint_id": constraint_id,
        "constraint_type": constraint_type,
        "markets_involved": [node.market_id for node in nodes],
        "venues_involved": sorted({node.venue for node in nodes}),
        "market_formulas": _market_formula_rows(nodes),
        "probability_inputs": probability_inputs,
        "inequality_checked": inequality_checked,
        "observed_value": round(observed_value, 6) if observed_value is not None else None,
        "expected_lower_bound": expected_lower_bound,
        "expected_upper_bound": expected_upper_bound,
        "observed_gap": round(max(0.0, observed_gap), 6),
        "gap_formula": gap_details["gap_formula"],
        "expected_bound": gap_details["expected_bound"],
        "raw_sum_or_difference": _round_optional(gap_details["raw_sum_or_difference"]),
        "tolerance": gap_details["tolerance"],
        "violation_amount_after_tolerance": gap_details["violation_amount_after_tolerance"],
        "violated": bool(violated),
        "evidence_basis": evidence,
        "confidence_tier": confidence_tier,
        "severity_score": severity_score,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "review_blockers": _default_blockers(review_blockers),
        "why_review_only_yet": WHY_REVIEW_ONLY,
        "implied_review_direction": implied_review_direction,
        "snapshot_id": snapshot.snapshot_id,
        "uses_diagnostic_midpoint": any(item["diagnostic_midpoint_used"] for item in probability_inputs),
        "uses_yes_price_equal_to_midpoint": any(
            item.get("yes_price_equals_midpoint") is True for item in probability_inputs
        ),
        "midpoint_only": _midpoint_only(probability_inputs),
        "has_stale_or_missing_quote": _has_stale_or_missing_quote(review_blockers),
        "explicit_partition_evidence": constraint_type in {"exhaustive_partition", "range_bucket_partition"},
        "eligible_for_payoff_state_feasibility": bridge["eligible_for_payoff_state_feasibility"],
        "payoff_state_blockers": bridge["payoff_state_blockers"],
        "state_family_id": bridge["state_family_id"],
    }
    _validate_row(row, "probability_constraints[]")
    return row


def _probability_inputs(snapshot: GraphSnapshot, nodes: list[MarketNode], *, stale_seconds: int) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    for node in nodes:
        probability, source = _probability_and_source(node)
        quote_age = _quote_age_seconds(snapshot, node)
        midpoint = (node.bid + node.ask) / 2.0 if node.bid is not None and node.ask is not None else None
        diagnostic_midpoint_used = source == "diagnostic_midpoint"
        yes_price_equals_midpoint = (
            source == "yes_price"
            and midpoint is not None
            and node.yes_price is not None
            and abs(float(node.yes_price) - midpoint) <= 1e-9
        )
        non_actionable = _non_actionable_input(
            source=source,
            bid=node.bid,
            ask=node.ask,
            quote_age=quote_age,
            stale_seconds=stale_seconds,
        )
        if yes_price_equals_midpoint:
            non_actionable = True
        inputs.append(
            {
                "market_id": node.market_id,
                "probability": round(probability, 6) if probability is not None else None,
                "probability_source": source,
                "bid": node.bid,
                "ask": node.ask,
                "bid_bound": node.bid,
                "ask_bound": node.ask,
                "midpoint": round(midpoint, 6) if midpoint is not None else None,
                "diagnostic_midpoint_used": diagnostic_midpoint_used,
                "yes_price_equals_midpoint": yes_price_equals_midpoint,
                "bid_ask_implied_bounds": {
                    "lower": node.bid,
                    "upper": node.ask,
                },
                "quote_age_seconds": quote_age,
                "non_actionable_input": non_actionable,
                "price_label": (
                    "diagnostic_midpoint"
                    if source == "diagnostic_midpoint"
                    else "yes_price_equals_midpoint"
                    if yes_price_equals_midpoint
                    else "diagnostic_probability"
                ),
            }
        )
    return inputs


def _market_formula_rows(nodes: list[MarketNode]) -> list[dict[str, Any]]:
    return compile_market_formula_rows(nodes)


def _probability_and_source(node: MarketNode) -> tuple[float | None, str]:
    if node.yes_price is not None:
        return node.yes_price, "yes_price"
    if node.bid is not None and node.ask is not None:
        return (node.bid + node.ask) / 2.0, "diagnostic_midpoint"
    return None, "missing_probability"


def _price_blockers(snapshot: GraphSnapshot, nodes: list[MarketNode], *, stale_seconds: int) -> list[str]:
    blockers: list[str] = []
    for node in nodes:
        blockers.extend(_source_blockers(node))
        probability, source = _probability_and_source(node)
        if probability is None:
            blockers.append("missing_probability_input")
        if source == "diagnostic_midpoint":
            blockers.append("diagnostic_midpoint_not_actionable")
        if node.bid is None or node.ask is None:
            blockers.append("missing_bid_or_ask")
        quote_age = _quote_age_seconds(snapshot, node)
        if quote_age is None:
            blockers.append("missing_quote_timestamp")
        elif quote_age > stale_seconds:
            blockers.append("stale_quote")
    return sorted(set(blockers))


def _quote_age_seconds(snapshot: GraphSnapshot, node: MarketNode) -> int | None:
    if node.raw.get("quote_timestamp_missing") is True:
        return None
    if node.as_of is None:
        return None
    return max(0, int((snapshot.as_of - node.as_of).total_seconds()))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float))]


def _source_blockers(node: MarketNode) -> list[str]:
    blockers = _string_list(node.raw.get("review_blockers"))
    if node.reference_only and "reference_only_source" not in blockers:
        blockers.append("reference_only_source")
    return blockers


def _confidence_tier(violated: bool, observed_gap: float, blockers: list[str]) -> str:
    if blockers:
        return "LOW"
    if not violated:
        return "MEDIUM"
    if observed_gap >= 0.08:
        return "HIGH"
    return "MEDIUM"


def _severity_score(violated: bool, observed_gap: float, base_confidence: float, blockers: list[str]) -> float:
    if not violated:
        return 0.0
    score = 45.0 + min(45.0, observed_gap * 100.0) + 10.0 * max(0.0, min(1.0, base_confidence))
    if "diagnostic_midpoint_not_actionable" in blockers:
        score -= 8.0
    if "stale_quote" in blockers:
        score -= 12.0
    if "missing_quote_timestamp" in blockers:
        score -= 6.0
    if "missing_probability_input" in blockers:
        score -= 25.0
    return round(max(0.0, min(100.0, score)), 3)


def _sum_gap_details(
    raw_sum: float | None,
    *,
    lower_bound: float,
    upper_bound: float,
    tolerance: float,
) -> dict[str, Any]:
    if lower_bound == upper_bound:
        gap_formula = f"max(0, abs(sum(probability_i) - {upper_bound:g}) - tolerance)"
        gap = 0.0 if raw_sum is None else max(0.0, abs(raw_sum - upper_bound) - tolerance)
        bound_type = "exact"
    else:
        gap_formula = "max(0, lower_bound - sum(probability_i) - tolerance, sum(probability_i) - upper_bound - tolerance)"
        gap = (
            0.0
            if raw_sum is None
            else max(0.0, lower_bound - raw_sum - tolerance, raw_sum - upper_bound - tolerance)
        )
        bound_type = "bounded"
    return {
        "gap_formula": gap_formula,
        "expected_bound": _expected_bound(lower_bound, upper_bound, tolerance, bound_type=bound_type),
        "raw_sum_or_difference": raw_sum,
        "tolerance": round(tolerance, 6),
        "violation_amount_after_tolerance": round(gap, 6),
    }


def _upper_bound_gap_details(
    raw_difference: float | None,
    *,
    upper_bound: float,
    tolerance: float,
    gap_formula: str,
) -> dict[str, Any]:
    gap = 0.0 if raw_difference is None else max(0.0, raw_difference - upper_bound - tolerance)
    return {
        "gap_formula": gap_formula,
        "expected_bound": _expected_bound(None, upper_bound, tolerance, bound_type="upper"),
        "raw_sum_or_difference": raw_difference,
        "tolerance": round(tolerance, 6),
        "violation_amount_after_tolerance": round(gap, 6),
    }


def _expected_bound(
    lower_bound: float | None,
    upper_bound: float,
    tolerance: float,
    *,
    bound_type: str,
) -> dict[str, Any]:
    return {
        "type": bound_type,
        "lower": lower_bound,
        "upper": upper_bound,
        "tolerance": round(tolerance, 6),
        "tolerance_adjusted_lower": round(lower_bound - tolerance, 6) if lower_bound is not None else None,
        "tolerance_adjusted_upper": round(upper_bound + tolerance, 6),
    }


def _round_optional(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 6)
    return None


def _non_actionable_input(
    *,
    source: str,
    bid: float | None,
    ask: float | None,
    quote_age: int | None,
    stale_seconds: int,
) -> bool:
    return (
        source in {"diagnostic_midpoint", "missing_probability"}
        or bid is None
        or ask is None
        or quote_age is None
        or quote_age > stale_seconds
    )


def _midpoint_only(probability_inputs: list[dict[str, Any]]) -> bool:
    populated = [item for item in probability_inputs if item.get("probability") is not None]
    return bool(populated) and all(
        item.get("diagnostic_midpoint_used") is True
        or item.get("yes_price_equals_midpoint") is True
        for item in populated
    )


def _has_stale_or_missing_quote(blockers: list[str]) -> bool:
    return bool(set(blockers) & STALE_OR_MISSING_QUOTE_BLOCKERS)


def _report_summary(rows: list[dict[str, Any]], counts: Counter[str]) -> dict[str, Any]:
    return {
        "total_constraints_checked": len(rows),
        "total_violations": sum(1 for row in rows if row["violated"]),
        "by_constraint_type": dict(sorted(counts.items())),
        "high_confidence_count": sum(1 for row in rows if row["confidence_tier"] == "HIGH"),
        "midpoint_only_count": sum(1 for row in rows if row.get("midpoint_only") is True),
        "yes_price_equal_to_midpoint_count": sum(
            1 for row in rows if row.get("uses_yes_price_equal_to_midpoint") is True
        ),
        "stale_or_missing_quote_count": sum(
            1 for row in rows if row.get("has_stale_or_missing_quote") is True
        ),
        "explicit_partition_count": sum(
            1
            for row in rows
            if row["constraint_type"] in {"exhaustive_partition", "range_bucket_partition"}
        ),
        "blocked_constraint_count": sum(
            1
            for row in rows
            if row.get("raw_sum_or_difference") is None
            or _has_stale_or_missing_quote(row.get("review_blockers", []))
        ),
    }


def _payoff_state_bridge(
    constraint_type: str,
    nodes: list[MarketNode],
    probability_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    metadata = [_metadata(node) for node in nodes]
    family_ids = sorted(
        {
            str(value)
            for value in (raw.get("payoff_state_family_id") for raw in metadata)
            if isinstance(value, str) and value
        }
    )
    family_types = sorted(
        {
            str(value)
            for value in (raw.get("payoff_state_family_type") for raw in metadata)
            if isinstance(value, str) and value
        }
    )
    blockers: list[str] = []
    if not family_ids:
        blockers.append("missing_payoff_state_family_id")
    elif len(family_ids) > 1:
        blockers.append("multiple_payoff_state_family_ids")
    if family_ids:
        if not family_types:
            blockers.append("missing_payoff_state_family_type")
        elif len(family_types) > 1:
            blockers.append("multiple_payoff_state_family_types")
        elif family_types[0] not in CONSTRAINT_TO_PAYOFF_STATE_TYPES.get(constraint_type, set()):
            blockers.append("unsupported_constraint_type_for_feasibility")
        if not any(isinstance(raw.get("payoff_state_states"), list) and raw.get("payoff_state_states") for raw in metadata):
            blockers.append("missing_state_definitions")
        if any(not isinstance(raw.get("payoff_state_payoffs"), dict) for raw in metadata):
            blockers.append("missing_payoff_matrix")
    if len(nodes) < 2 or len(nodes) > 8:
        blockers.append("contract_count_outside_payoff_state_bounds")
    if any(item.get("probability") is None for item in probability_inputs):
        blockers.append("missing_probability_input")
    if not all(item.get("bid_bound") is not None and item.get("ask_bound") is not None for item in probability_inputs):
        if any(item.get("diagnostic_midpoint_used") is True for item in probability_inputs):
            blockers.append("diagnostic_midpoint_used")
        if _midpoint_only(probability_inputs):
            blockers.append("non_actionable_input")
    state_family_id = family_ids[0] if len(family_ids) == 1 else None
    return {
        "eligible_for_payoff_state_feasibility": bool(state_family_id) and not blockers,
        "payoff_state_blockers": sorted(set(blockers)),
        "state_family_id": state_family_id,
    }


def _metadata(node: MarketNode) -> dict[str, Any]:
    row = node.raw.get("normalized_row")
    if isinstance(row, dict):
        merged = dict(node.raw)
        merged.update(row)
        return merged
    return dict(node.raw)


def _default_blockers(blockers: list[str]) -> list[str]:
    base = {
        "requires_independent_settlement_review",
        "requires_fee_depth_freshness_review",
        "not_evaluator_input",
        "no_execution_permission",
    }
    return sorted(base | set(blockers))


def _validate_row(row: dict[str, Any], path: str) -> None:
    required = [
        "constraint_id",
        "constraint_type",
        "markets_involved",
        "venues_involved",
        "probability_inputs",
        "inequality_checked",
        "observed_gap",
        "gap_formula",
        "expected_bound",
        "raw_sum_or_difference",
        "tolerance",
        "violation_amount_after_tolerance",
        "confidence_tier",
        "severity_score",
        "diagnostic_only",
        "allowed_actions",
        "review_blockers",
        "why_review_only_yet",
        "eligible_for_payoff_state_feasibility",
        "payoff_state_blockers",
    ]
    for key in required:
        if key not in row:
            raise SchemaValidationError(f"{path}.{key} is required")
    if row["constraint_type"] not in CONSTRAINT_TYPES:
        raise SchemaValidationError(f"{path}.constraint_type is not supported")
    if row["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if row.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if row["allowed_actions"] != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    if row["confidence_tier"] not in CONFIDENCE_TIERS:
        raise SchemaValidationError(f"{path}.confidence_tier is not supported")
    if not isinstance(row["observed_gap"], (int, float)) or isinstance(row["observed_gap"], bool):
        raise SchemaValidationError(f"{path}.observed_gap must be numeric")
    if not isinstance(row["violation_amount_after_tolerance"], (int, float)) or isinstance(row["violation_amount_after_tolerance"], bool):
        raise SchemaValidationError(f"{path}.violation_amount_after_tolerance must be numeric")
    if round(float(row["observed_gap"]), 6) != round(float(row["violation_amount_after_tolerance"]), 6):
        raise SchemaValidationError(f"{path}.observed_gap must equal violation_amount_after_tolerance")
    if not isinstance(row["gap_formula"], str) or not row["gap_formula"]:
        raise SchemaValidationError(f"{path}.gap_formula must be a non-empty string")
    if not isinstance(row["expected_bound"], dict):
        raise SchemaValidationError(f"{path}.expected_bound must be an object")
    if not isinstance(row["severity_score"], (int, float)) or isinstance(row["severity_score"], bool):
        raise SchemaValidationError(f"{path}.severity_score must be numeric")
    if not 0 <= row["severity_score"] <= 100:
        raise SchemaValidationError(f"{path}.severity_score must be in [0, 100]")
    for key in ["markets_involved", "venues_involved", "probability_inputs", "review_blockers"]:
        if not isinstance(row[key], list):
            raise SchemaValidationError(f"{path}.{key} must be a list")
    if not isinstance(row["payoff_state_blockers"], list):
        raise SchemaValidationError(f"{path}.payoff_state_blockers must be a list")
    if row.get("violated") not in {True, False}:
        raise SchemaValidationError(f"{path}.violated must be boolean")
    if row.get("eligible_for_payoff_state_feasibility") not in {True, False}:
        raise SchemaValidationError(f"{path}.eligible_for_payoff_state_feasibility must be boolean")
    if row["constraint_type"] in {"exhaustive_partition", "range_bucket_partition"} and row.get("explicit_partition_evidence") is not True:
        raise SchemaValidationError(f"{path}.explicit_partition_evidence is required for partition constraints")
    if row["confidence_tier"] == "HIGH" and any(
        item.get("diagnostic_midpoint_used") is True for item in row["probability_inputs"]
    ):
        raise SchemaValidationError(f"{path}.midpoint-derived rows cannot be HIGH confidence")
    for input_index, item in enumerate(row["probability_inputs"]):
        for key in [
            "bid_bound",
            "ask_bound",
            "midpoint",
            "diagnostic_midpoint_used",
            "yes_price_equals_midpoint",
            "non_actionable_input",
        ]:
            if key not in item:
                raise SchemaValidationError(f"{path}.probability_inputs[{input_index}].{key} is required")
        if item.get("diagnostic_midpoint_used") is True and item.get("non_actionable_input") is not True:
            raise SchemaValidationError(f"{path}.probability_inputs[{input_index}].midpoint must be non_actionable")
        if item.get("yes_price_equals_midpoint") is True and item.get("non_actionable_input") is not True:
            raise SchemaValidationError(
                f"{path}.probability_inputs[{input_index}].yes_price_equals_midpoint requires non_actionable_input=true"
            )


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        existing = by_id.get(row["constraint_id"])
        if existing is None or row["severity_score"] > existing["severity_score"]:
            by_id[row["constraint_id"]] = row
    return list(by_id.values())


def _is_range_bucket(nodes: list[MarketNode]) -> bool:
    return all("range-bucket" in node.themes for node in nodes)


def _threshold_key_id(key: tuple[Any, ...]) -> str:
    return ":".join(str(item).replace(" ", "_").replace(":", "_").replace("/", "_") for item in key if item not in {None, ""})


__all__ = [
    "CONSTRAINT_TYPES",
    "build_probability_constraints_report",
    "validate_probability_constraints_report",
    "write_probability_constraints_report",
]
