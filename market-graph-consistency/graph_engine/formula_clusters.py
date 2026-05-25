from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import Any

from graph_engine.formula import MarketFormula
from graph_engine.reporting.schema_validation import DIAGNOSTIC_HINT_ACTIONS, validate_formula_cluster_constraints_contract


def build_formula_cluster_constraints_report(formulas: list[MarketFormula]) -> dict[str, Any]:
    constraints = [
        *_threshold_ladder_constraints(formulas),
        *_fed_range_constraints(formulas),
        *_possible_group_constraints(formulas),
        *_complement_pair_constraints(formulas),
    ]
    constraints = sorted(
        constraints,
        key=lambda item: (
            0 if item["max_action_cap"] == "MANUAL_REVIEW" else 1,
            item["constraint_type"],
            item["source_market_ids"],
        ),
    )
    for index, constraint in enumerate(constraints, start=1):
        constraint["diagnostic_rank"] = index
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "cluster_constraint_count": len(constraints),
        "formula_cluster_constraints": constraints,
    }
    validate_formula_cluster_constraints_contract(report)
    return report


def _threshold_ladder_constraints(formulas: list[MarketFormula]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[MarketFormula]] = defaultdict(list)
    blocked: list[dict[str, Any]] = []
    for formula in formulas:
        if formula.family != "BTC_THRESHOLD":
            continue
        blockers = _required_key_blockers(formula, ["source", "date", "asset"])
        if blockers:
            blocked.append(
                _constraint(
                    constraint_id=f"formula_cluster:blocked_grouping:{formula.market_id}",
                    constraint_type="blocked_exact_grouping",
                    constraint_family="formula_cluster",
                    formulas=[formula],
                    max_action_cap="WATCH",
                    reason_for_review="Formula has missing source/date keys, so exact structural grouping is blocked.",
                    requested_exact_keys_to_verify=["family", "asset", "source", "date", "comparator", "threshold", "units"],
                    blockers=blockers,
                )
            )
            continue
        if formula.threshold is None or formula.comparator not in {">", ">="}:
            continue
        grouped[(formula.family, formula.asset, formula.source, formula.date, formula.units)].append(formula)

    constraints = blocked
    for key, items in grouped.items():
        distinct_thresholds = sorted({item.threshold for item in items if item.threshold is not None})
        if len(distinct_thresholds) < 3:
            continue
        ordered = sorted(items, key=lambda item: item.threshold or 0.0, reverse=True)
        constraints.append(
            _constraint(
                constraint_id=f"formula_cluster:threshold_ladder:{_key_id(key)}",
                constraint_type="synthesized_threshold_ladder",
                constraint_family="ordered_thresholds",
                formulas=ordered,
                max_action_cap="MANUAL_REVIEW",
                reason_for_review="Typed formulas form an ordered threshold ladder requiring wording and settlement review.",
                requested_exact_keys_to_verify=["family", "asset", "source", "date", "settlement_time", "comparator", "threshold", "units"],
                blockers=[],
                derived_structure={
                    "thresholds": [item.threshold for item in ordered],
                    "ordering": "stricter_threshold_should_not_exceed_looser_threshold_probability",
                },
            )
        )
    return constraints


def _fed_range_constraints(formulas: list[MarketFormula]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[MarketFormula]] = defaultdict(list)
    blocked: list[dict[str, Any]] = []
    for formula in formulas:
        if formula.family != "FED_MEETING_RANGE":
            continue
        blockers = _required_key_blockers(formula, ["source", "meeting_date", "subject"])
        if blockers:
            blocked.append(
                _constraint(
                    constraint_id=f"formula_cluster:blocked_grouping:{formula.market_id}",
                    constraint_type="blocked_exact_grouping",
                    constraint_family="formula_cluster",
                    formulas=[formula],
                    max_action_cap="WATCH",
                    reason_for_review="Formula has missing source/meeting keys, so exact structural grouping is blocked.",
                    requested_exact_keys_to_verify=["family", "subject", "source", "meeting_date", "lower_bound", "upper_bound", "units"],
                    blockers=blockers,
                )
            )
            continue
        if formula.lower_bound is None or formula.upper_bound is None:
            continue
        grouped[(formula.family, formula.subject, formula.source, formula.meeting_date, formula.units)].append(formula)

    constraints = blocked
    for key, items in grouped.items():
        ranges = sorted(
            [item for item in items if item.lower_bound is not None and item.upper_bound is not None],
            key=lambda item: (item.lower_bound or 0.0, item.upper_bound or 0.0, item.market_id),
        )
        for left, right in combinations(ranges, 2):
            if _ranges_overlap(left, right) and not _same_range(left, right):
                constraints.append(
                    _constraint(
                        constraint_id=f"formula_cluster:overlapping_ranges:{left.market_id}->{right.market_id}",
                        constraint_type="synthesized_overlapping_ranges",
                        constraint_family="range_overlap",
                        formulas=[left, right],
                        max_action_cap="WATCH",
                        reason_for_review="Typed Fed ranges overlap but are not identical.",
                        requested_exact_keys_to_verify=["family", "subject", "source", "meeting_date", "settlement_time", "lower_bound", "upper_bound", "units"],
                        blockers=["range_overlap_not_identical"],
                        derived_structure={
                            "left_range": [left.lower_bound, left.upper_bound],
                            "right_range": [right.lower_bound, right.upper_bound],
                        },
                    )
                )
        if len(ranges) >= 3 and _looks_like_partition(ranges):
            constraints.append(
                _constraint(
                    constraint_id=f"formula_cluster:range_bucket_partition:{_key_id(key)}",
                    constraint_type="synthesized_range_bucket_partition",
                    constraint_family="range_partition",
                    formulas=ranges,
                    max_action_cap="MANUAL_REVIEW",
                    reason_for_review="Typed ranges look like adjacent buckets for the same meeting and source.",
                    requested_exact_keys_to_verify=["family", "subject", "source", "meeting_date", "settlement_time", "lower_bound", "upper_bound", "bucket_boundary_rules"],
                    blockers=[],
                    derived_structure={"ranges": [[item.lower_bound, item.upper_bound] for item in ranges]},
                )
            )
    return constraints


def _possible_group_constraints(formulas: list[MarketFormula]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[MarketFormula]] = defaultdict(list)
    for formula in formulas:
        if formula.family != "SPORTS_CHAMPION" or formula.blockers:
            continue
        if not formula.source or not formula.date:
            continue
        grouped[(formula.family, formula.source, formula.date, formula.subject or "champion")].append(formula)

    constraints: list[dict[str, Any]] = []
    for key, items in grouped.items():
        teams = {item.team for item in items if item.team}
        if len(items) < 3 or len(teams) < 3:
            continue
        constraints.append(
            _constraint(
                constraint_id=f"formula_cluster:possible_exhaustive_group:{_key_id(key)}",
                constraint_type="synthesized_possible_exhaustive_group",
                constraint_family="outcome_partition",
                formulas=sorted(items, key=lambda item: item.market_id),
                max_action_cap="WATCH",
                reason_for_review="Typed sports winner formulas may form a reviewed outcome group.",
                requested_exact_keys_to_verify=["family", "team", "source", "date", "league_scope", "field_completeness"],
                blockers=["exhaustiveness_not_proven"],
                derived_structure={"teams": sorted(teams)},
            )
        )
        constraints.append(
            _constraint(
                constraint_id=f"formula_cluster:mutually_exclusive_group:{_key_id(key)}",
                constraint_type="synthesized_mutually_exclusive_group",
                constraint_family="mutual_exclusion",
                formulas=sorted(items, key=lambda item: item.market_id),
                max_action_cap="MANUAL_REVIEW",
                reason_for_review="Typed sports winner formulas for different teams should be reviewed as mutually exclusive outcomes.",
                requested_exact_keys_to_verify=["family", "team", "source", "date", "league_scope", "tie_or_cancellation_rules"],
                blockers=[],
                derived_structure={"teams": sorted(teams)},
            )
        )
    return constraints


def _complement_pair_constraints(formulas: list[MarketFormula]) -> list[dict[str, Any]]:
    constraints: list[dict[str, Any]] = []
    for left, right in combinations(formulas, 2):
        if left.family != right.family or left.family not in {"BTC_THRESHOLD", "FED_MEETING_RANGE"}:
            continue
        if left.side == right.side:
            continue
        if _cluster_key(left) != _cluster_key(right):
            continue
        constraints.append(
            _constraint(
                constraint_id=f"formula_cluster:complement_pair:{left.market_id}->{right.market_id}",
                constraint_type="synthesized_complement_pair",
                constraint_family="complement_pair",
                formulas=[left, right],
                max_action_cap="WATCH",
                reason_for_review="Typed formulas have opposite sides on matching structural keys; complement rules still require review.",
                requested_exact_keys_to_verify=["family", "source", "date_or_meeting", "settlement_time", "side", "cancellation_rules"],
                blockers=["complement_not_proven"],
            )
        )
    return constraints


def _constraint(
    *,
    constraint_id: str,
    constraint_type: str,
    constraint_family: str,
    formulas: list[MarketFormula],
    max_action_cap: str,
    reason_for_review: str,
    requested_exact_keys_to_verify: list[str],
    blockers: list[str],
    derived_structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "constraint_id": constraint_id,
        "constraint_type": constraint_type,
        "constraint_family": constraint_family,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "max_action_cap": max_action_cap,
        "diagnostic_priority": max_action_cap,
        "source_market_ids": [formula.market_id for formula in formulas],
        "formula_count": len(formulas),
        "cluster_key": _cluster_key(formulas[0]) if formulas else "unknown",
        "requested_exact_keys_to_verify": list(requested_exact_keys_to_verify),
        "blockers": list(blockers),
        "reason_for_review": reason_for_review,
        "derived_structure": derived_structure or {},
    }


def _cluster_key(formula: MarketFormula) -> str:
    subject = formula.subject or formula.asset or formula.team or formula.location
    date_or_meeting = formula.meeting_date or formula.date or formula.settlement_time
    range_key = _range_key(formula)
    return "|".join(
        str(part)
        for part in [formula.family, subject, formula.source, date_or_meeting, formula.comparator, range_key, formula.units]
        if part not in {None, ""}
    )


def _range_key(formula: MarketFormula) -> str | None:
    if formula.threshold is not None:
        return f"threshold={formula.threshold}"
    if formula.lower_bound is not None or formula.upper_bound is not None:
        return f"range={formula.lower_bound}:{formula.upper_bound}"
    return None


def _key_id(key: tuple[Any, ...]) -> str:
    return ":".join(str(item).replace(" ", "_").replace(":", "_") for item in key if item not in {None, ""})


def _required_key_blockers(formula: MarketFormula, names: list[str]) -> list[str]:
    blockers = list(formula.blockers)
    for name in names:
        if getattr(formula, name) in {None, ""}:
            blockers.append(f"missing_{name}")
    return sorted(set(blockers))


def _same_range(left: MarketFormula, right: MarketFormula) -> bool:
    return left.lower_bound == right.lower_bound and left.upper_bound == right.upper_bound


def _ranges_overlap(left: MarketFormula, right: MarketFormula) -> bool:
    if None in {left.lower_bound, left.upper_bound, right.lower_bound, right.upper_bound}:
        return False
    return max(left.lower_bound, right.lower_bound) < min(left.upper_bound, right.upper_bound)  # type: ignore[arg-type]


def _looks_like_partition(ranges: list[MarketFormula]) -> bool:
    if len(ranges) < 3:
        return False
    for left, right in zip(ranges, ranges[1:]):
        if left.upper_bound != right.lower_bound:
            return False
    return True
