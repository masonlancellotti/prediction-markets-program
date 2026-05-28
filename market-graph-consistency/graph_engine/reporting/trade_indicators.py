from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from graph_engine.consistency.runner import run_consistency_checks
from graph_engine.formula import build_formula_diagnostics_report
from graph_engine.models import ConsistencyViolation, GraphSnapshot, MarketNode, RelationshipType, ViolationKind
from graph_engine.reporting.multi_leg import build_multi_leg_constraints_report
from graph_engine.reporting.schema_validation import DIAGNOSTIC_HINT_ACTIONS, SchemaValidationError
from graph_engine.thresholds import threshold_candidate_from_node


SIGNAL_TYPES = {
    "EXACT_RELATIONSHIP_WATCH",
    "COMPLEMENT_PRICE_DIVERGENCE",
    "SUBSET_SUPERSET_PRICE_VIOLATION",
    "THRESHOLD_LADDER_INVERSION",
    "RANGE_BUCKET_INCONSISTENCY",
    "EVENT_FAMILY_OUTLIER",
    "CROSS_VENUE_DIVERGENCE",
    "STALE_OR_LAG_WATCH",
    "SIMILARITY_ONLY_RESEARCH",
    "THEMATIC_CORRELATION_WATCH",
}
ADVISORY_EVIDENCE_TIER_PRIORITY = [
    "DETERMINISTIC_SUPPORTED",
    "LOGICAL_HYPOTHESIS_ONLY",
    "PROBABILISTIC_HYPOTHESIS_ONLY",
    "STALE_OR_LAG_HYPOTHESIS_ONLY",
    "THEMATIC_HYPOTHESIS_ONLY",
    "SIMILARITY_ONLY_RESEARCH",
]
LOGICAL_ADVISORY_TIERS = {"DETERMINISTIC_SUPPORTED", "LOGICAL_HYPOTHESIS_ONLY"}
LLM_ADVISORY_EVIDENCE_ROLE = "llm_hypothesis_advisory"
LLM_ADVISORY_SEVERITY_BOOST = 0.0
LLM_ADVISORY_MAX_BOOST = 0.0
# Maps an LLM relationship_type to the signal_types whose structural meaning
# it actually corroborates. A SUBSET_HYPOTHESIS only counts as supporting
# evidence for a SUBSET_SUPERSET_PRICE_VIOLATION (and similar). This blocks
# fake-edge boosts where a market-set match alone would otherwise rubber-stamp
# an unrelated signal type.
LLM_RELATIONSHIP_TO_COMPATIBLE_SIGNALS: dict[str, frozenset[str]] = {
    "EXACT_EQUALITY_HYPOTHESIS": frozenset({"EXACT_RELATIONSHIP_WATCH", "CROSS_VENUE_DIVERGENCE"}),
    "COMPLEMENT_HYPOTHESIS": frozenset({"COMPLEMENT_PRICE_DIVERGENCE"}),
    "SUBSET_HYPOTHESIS": frozenset({"SUBSET_SUPERSET_PRICE_VIOLATION"}),
    "SUPERSET_HYPOTHESIS": frozenset({"SUBSET_SUPERSET_PRICE_VIOLATION"}),
    "MUTUALLY_EXCLUSIVE_HYPOTHESIS": frozenset({"EVENT_FAMILY_OUTLIER"}),
    "EXHAUSTIVE_PARTITION_HYPOTHESIS": frozenset({"EVENT_FAMILY_OUTLIER", "RANGE_BUCKET_INCONSISTENCY"}),
    "THRESHOLD_LADDER_HYPOTHESIS": frozenset({"THRESHOLD_LADDER_INVERSION", "SUBSET_SUPERSET_PRICE_VIOLATION"}),
    "RANGE_BUCKET_HYPOTHESIS": frozenset({"RANGE_BUCKET_INCONSISTENCY"}),
    "STALE_OR_LAG_HYPOTHESIS": frozenset({"STALE_OR_LAG_WATCH", "CROSS_VENUE_DIVERGENCE"}),
}
CONFIDENCE_TIERS = {"HIGH", "MEDIUM", "LOW"}
DISALLOWED_ACTIONS = {"PAPER_CANDIDATE", "TRADE", "EXECUTE", "ORDER", "BUY", "SELL"}
DISALLOWED_OUTPUT_TOKENS = {
    "PAPER_CANDIDATE",
    "GUARANTEED_PNL",
    "PNL",
    "PROFIT_USD",
    "EXACT_ARBITRAGE",
    "EXECUTABLE_ARBITRAGE",
}
REPORT_BANNER = (
    "Saved-file-only market graph signal review report. Signals are diagnostic review inputs, "
    "not execution permission or evaluator input."
)
DEFAULT_STALE_SECONDS = 24 * 60 * 60


def build_trade_indicator_report(
    snapshot: GraphSnapshot,
    violations: list[ConsistencyViolation] | None = None,
    *,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    llm_hypotheses_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    violations = list(violations) if violations is not None else run_consistency_checks(snapshot)
    signals: list[dict[str, Any]] = []
    signals.extend(_signals_from_violations(snapshot, violations, stale_seconds=stale_seconds))
    signals.extend(_signals_from_multi_leg(snapshot, stale_seconds=stale_seconds))
    signals.extend(_signals_from_formula_diagnostics(snapshot, stale_seconds=stale_seconds))
    signals.extend(_stale_or_missing_price_signals(snapshot, stale_seconds=stale_seconds))
    signals = _dedupe_signals(signals)
    for signal in signals:
        _attach_empty_advisory_fields(signal)
    advisory_hypotheses = _advisory_hypotheses(llm_hypotheses_report)
    unmatched = _attach_llm_advisory_evidence(signals, advisory_hypotheses)
    signals.extend(_signals_from_unmatched_advisory(snapshot, unmatched, stale_seconds=stale_seconds))
    signals = sorted(signals, key=lambda row: (-row["severity_score"], row["signal_type"], row["signal_id"]))
    for index, signal in enumerate(signals, start=1):
        signal["diagnostic_rank"] = index

    counts = Counter(signal["signal_type"] for signal in signals)
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "banner": REPORT_BANNER,
        "snapshot_id": snapshot.snapshot_id,
        "signal_count": len(signals),
        "counts_by_signal_type": dict(sorted(counts.items())),
        "signals": signals,
    }
    validate_trade_indicator_report(report)
    return report


def write_trade_indicator_report(
    snapshot: GraphSnapshot,
    json_output: Path | str,
    csv_output: Path | str | None = None,
    violations: list[ConsistencyViolation] | None = None,
    llm_hypotheses_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = build_trade_indicator_report(snapshot, violations, llm_hypotheses_report=llm_hypotheses_report)
    validate_trade_indicator_report(report)
    json_path = Path(json_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if csv_output is not None:
        write_trade_indicator_csv(report, csv_output)
    return report


def write_trade_indicator_csv(report: dict[str, Any], csv_output: Path | str) -> None:
    validate_trade_indicator_report(report)
    csv_path = Path(csv_output)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "signal_id",
        "signal_type",
        "confidence_tier",
        "severity_score",
        "relationship_evidence_type",
        "implied_direction",
        "markets_involved",
        "venues_involved",
        "review_blockers",
        "why_review_only_yet",
        "llm_hypothesis_ids",
        "llm_advisory_evidence_strength_tier",
        "llm_advisory_severity_boost",
        "corroborating_llm_evidence",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report["signals"]:
            writer.writerow(
                {
                    "signal_id": row["signal_id"],
                    "signal_type": row["signal_type"],
                    "confidence_tier": row["confidence_tier"],
                    "severity_score": row["severity_score"],
                    "relationship_evidence_type": row["relationship_evidence_type"],
                    "implied_direction": row["implied_direction"],
                    "markets_involved": ";".join(row["markets_involved"]),
                    "venues_involved": ";".join(row["venues_involved"]),
                    "review_blockers": ";".join(row["review_blockers"]),
                    "why_review_only_yet": row["why_review_only_yet"],
                    "llm_hypothesis_ids": ";".join(row.get("llm_hypothesis_ids") or []),
                    "llm_advisory_evidence_strength_tier": row.get("llm_advisory_evidence_strength_tier") or "",
                    "llm_advisory_severity_boost": row.get("llm_advisory_severity_boost", 0.0),
                    "corroborating_llm_evidence": row.get("corroborating_llm_evidence", False),
                }
            )


def validate_trade_indicator_report(report: dict[str, Any]) -> None:
    _reject_disallowed_output_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("trade indicator report must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("trade indicator report must not affect evaluator gates")
    if report.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError("trade indicator actions must be WATCH and MANUAL_REVIEW only")
    if any(action in DISALLOWED_ACTIONS for action in report.get("allowed_actions", [])):
        raise SchemaValidationError("trade indicator report contains a disallowed action")
    signals = report.get("signals")
    if not isinstance(signals, list):
        raise SchemaValidationError("signals must be a list")
    if report.get("signal_count") != len(signals):
        raise SchemaValidationError("signal_count must match signals")
    for index, signal in enumerate(signals):
        _validate_signal(signal, f"signals[{index}]")


def _signals_from_violations(
    snapshot: GraphSnapshot,
    violations: list[ConsistencyViolation],
    *,
    stale_seconds: int,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for violation in violations:
        signal_type = _signal_type_from_violation(snapshot, violation)
        if signal_type is None:
            continue
        nodes = _nodes(snapshot, violation.involved_market_ids)
        base_score = _base_score(signal_type)
        price_blockers = _price_blockers(snapshot, nodes, stale_seconds=stale_seconds)
        merged_blockers = sorted(set(violation.blockers + price_blockers))
        severity = _score(base_score, violation.magnitude, nodes, merged_blockers, _evidence_strength_from_violation(violation))
        signals.append(
            _signal_row(
                snapshot=snapshot,
                signal_id=f"indicator:{violation.violation_id}",
                signal_type=signal_type,
                nodes=nodes,
                relationship_evidence_type=_violation_evidence_type(violation),
                probability_inputs=_probability_inputs(snapshot, nodes, stale_seconds=stale_seconds),
                implied_direction=_implied_direction_for_violation(violation),
                confidence_tier=_confidence_tier(signal_type, severity, merged_blockers),
                severity_score=severity,
                blockers=merged_blockers,
                why_review_only_yet=_not_tradeable_reason(),
            )
        )
    return signals


def _signals_from_multi_leg(snapshot: GraphSnapshot, *, stale_seconds: int) -> list[dict[str, Any]]:
    report = build_multi_leg_constraints_report(snapshot)
    signals: list[dict[str, Any]] = []
    for constraint in report["multi_leg_constraints"]:
        signal_type = _signal_type_from_constraint(constraint["constraint_type"])
        nodes = _nodes(snapshot, constraint["market_ids"])
        price_blockers = _price_blockers(snapshot, nodes, stale_seconds=stale_seconds)
        merged_blockers = sorted(set(constraint["blockers"] + price_blockers))
        severity = _score(
            _base_score(signal_type),
            float(constraint["normalized_bound_gap"]),
            nodes,
            merged_blockers,
            float(constraint["confidence_basis"]["score"]),
        )
        signals.append(
            _signal_row(
                snapshot=snapshot,
                signal_id=f"indicator:{constraint['constraint_id']}",
                signal_type=signal_type,
                nodes=nodes,
                relationship_evidence_type=f"multi_leg_constraint:{constraint['constraint_type']}",
                probability_inputs=_probability_inputs(snapshot, nodes, stale_seconds=stale_seconds),
                implied_direction=_implied_direction_for_constraint(constraint["constraint_type"]),
                confidence_tier=_confidence_tier(signal_type, severity, merged_blockers),
                severity_score=severity,
                blockers=merged_blockers,
                why_review_only_yet=_not_tradeable_reason(),
            )
        )
    return signals


def _signals_from_formula_diagnostics(snapshot: GraphSnapshot, *, stale_seconds: int) -> list[dict[str, Any]]:
    formula_report = build_formula_diagnostics_report(snapshot)
    signals: list[dict[str, Any]] = []
    for diagnostic in formula_report["formula_diagnostics"]:
        nodes = _nodes(snapshot, diagnostic["market_ids"])
        if not nodes:
            continue
        signal_type = _signal_type_from_formula(diagnostic, nodes)
        probability_delta = _max_probability_delta(nodes)
        price_blockers = _price_blockers(snapshot, nodes, stale_seconds=stale_seconds)
        merged_blockers = sorted(set(diagnostic["blockers"] + price_blockers))
        severity = _score(_base_score(signal_type), probability_delta, nodes, merged_blockers, _formula_evidence_strength(diagnostic))
        signals.append(
            _signal_row(
                snapshot=snapshot,
                signal_id=f"indicator:{diagnostic['comparison_id']}",
                signal_type=signal_type,
                nodes=nodes,
                relationship_evidence_type=f"formula_diagnostic:{diagnostic['formula_relation']}",
                probability_inputs=_probability_inputs(snapshot, nodes, stale_seconds=stale_seconds),
                implied_direction=_implied_direction_for_formula(signal_type, nodes),
                confidence_tier=_confidence_tier(signal_type, severity, merged_blockers),
                severity_score=severity,
                blockers=merged_blockers,
                why_review_only_yet=_not_tradeable_reason(),
            )
        )
    return signals


def _stale_or_missing_price_signals(snapshot: GraphSnapshot, *, stale_seconds: int) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for node in sorted(snapshot.nodes.values(), key=lambda item: item.market_id):
        blockers = _price_blockers(snapshot, [node], stale_seconds=stale_seconds)
        if not any(
            blocker in blockers
            for blocker in ["stale_quote", "missing_probability_input", "missing_quote_timestamp", "reference_only_source"]
        ):
            continue
        signals.append(
            _signal_row(
                snapshot=snapshot,
                signal_id=f"indicator:stale_or_lag:{node.market_id}",
                signal_type="STALE_OR_LAG_WATCH",
                nodes=[node],
                relationship_evidence_type="snapshot_freshness_check",
                probability_inputs=_probability_inputs(snapshot, [node], stale_seconds=stale_seconds),
                implied_direction="NO_SAFE_DIRECTION",
                confidence_tier="LOW",
                severity_score=_score(25.0, 0.0, [node], blockers, 0.3),
                blockers=blockers,
                why_review_only_yet=_not_tradeable_reason(),
            )
        )
    return signals


def _signal_row(
    *,
    snapshot: GraphSnapshot,
    signal_id: str,
    signal_type: str,
    nodes: list[MarketNode],
    relationship_evidence_type: str,
    probability_inputs: list[dict[str, Any]],
    implied_direction: str,
    confidence_tier: str,
    severity_score: float,
    blockers: list[str],
    why_review_only_yet: str,
    llm_hypothesis_ids: list[str] | None = None,
    llm_advisory_evidence_strength_tier: str | None = None,
) -> dict[str, Any]:
    row = {
        "signal_id": signal_id,
        "signal_type": signal_type,
        "markets_involved": [node.market_id for node in nodes],
        "venues_involved": sorted({node.venue for node in nodes}),
        "relationship_evidence_type": relationship_evidence_type,
        "market_formulas": _market_formula_rows(nodes),
        "probability_inputs_used": probability_inputs,
        "implied_direction": implied_direction,
        "confidence_tier": confidence_tier,
        "severity_score": round(max(0.0, min(100.0, severity_score)), 3),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "review_blockers": _default_blockers(blockers),
        "why_review_only_yet": why_review_only_yet,
        "snapshot_id": snapshot.snapshot_id,
        "llm_hypothesis_ids": list(llm_hypothesis_ids) if llm_hypothesis_ids else [],
        "llm_advisory_evidence_strength_tier": llm_advisory_evidence_strength_tier,
        "llm_advisory_evidence_role": LLM_ADVISORY_EVIDENCE_ROLE if llm_hypothesis_ids else None,
        "llm_advisory_severity_boost": 0.0,
        "corroborating_llm_evidence": False,
    }
    _validate_signal(row, "signals[]")
    return row


def _attach_empty_advisory_fields(signal: dict[str, Any]) -> None:
    signal.setdefault("llm_hypothesis_ids", [])
    signal.setdefault("llm_advisory_evidence_strength_tier", None)
    signal.setdefault("llm_advisory_evidence_role", None)
    signal.setdefault("llm_advisory_severity_boost", 0.0)
    signal.setdefault("corroborating_llm_evidence", False)


def _market_formula_rows(nodes: list[MarketNode]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in nodes:
        candidate = threshold_candidate_from_node(node)
        rows.append(
            {
                "market_id": node.market_id,
                "family": candidate.family,
                "asset": "BTC" if str(candidate.observable or "").upper() == "BTC" else candidate.observable,
                "source": candidate.source,
                "date": candidate.window,
                "window": node.window,
                "comparator": candidate.comparator,
                "threshold": candidate.threshold,
                "unit": candidate.unit,
            }
        )
    return rows


def _advisory_hypotheses(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    return [
        row
        for row in report.get("validated_hypotheses", [])
        if isinstance(row, dict)
        and row.get("llm_evidence_role") == LLM_ADVISORY_EVIDENCE_ROLE
        and row.get("relationship_strength_tier") in {*ADVISORY_EVIDENCE_TIER_PRIORITY}
    ]


def _attach_llm_advisory_evidence(
    signals: list[dict[str, Any]],
    hypotheses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not hypotheses:
        return []
    by_markets: dict[frozenset[str], list[dict[str, Any]]] = {}
    for signal in signals:
        key = frozenset(signal["markets_involved"])
        by_markets.setdefault(key, []).append(signal)
    unmatched: list[dict[str, Any]] = []
    for hypothesis in hypotheses:
        market_ids = hypothesis.get("source_market_ids") or []
        key = frozenset(str(item) for item in market_ids)
        matched = by_markets.get(key, [])
        if not matched:
            unmatched.append(hypothesis)
            continue
        for signal in matched:
            _apply_advisory_to_signal(signal, hypothesis)
    return unmatched


def _apply_advisory_to_signal(signal: dict[str, Any], hypothesis: dict[str, Any]) -> None:
    tier = hypothesis.get("relationship_strength_tier")
    hypothesis_id = hypothesis.get("hypothesis_id")
    relationship_type = hypothesis.get("relationship_type")
    if not tier or not hypothesis_id or tier not in ADVISORY_EVIDENCE_TIER_PRIORITY:
        return
    ids = list(signal.get("llm_hypothesis_ids") or [])
    if hypothesis_id not in ids:
        ids.append(hypothesis_id)
    signal["llm_hypothesis_ids"] = sorted(ids)
    current_tier = signal.get("llm_advisory_evidence_strength_tier")
    if _is_stronger_tier(tier, current_tier):
        signal["llm_advisory_evidence_strength_tier"] = tier
    signal["llm_advisory_evidence_role"] = LLM_ADVISORY_EVIDENCE_ROLE
    compatible_signals = LLM_RELATIONSHIP_TO_COMPATIBLE_SIGNALS.get(str(relationship_type), frozenset())
    type_compatible = signal.get("signal_type") in compatible_signals
    signal["llm_advisory_severity_boost"] = 0.0
    if tier in LOGICAL_ADVISORY_TIERS and type_compatible:
        signal["corroborating_llm_evidence"] = True
    blockers = set(signal.get("review_blockers") or [])
    blockers.add("advisory_llm_evidence_requires_independent_verification")
    if tier in LOGICAL_ADVISORY_TIERS and not type_compatible:
        blockers.add("advisory_llm_relationship_type_does_not_match_signal_type")
    signal["review_blockers"] = sorted(blockers)


def _is_stronger_tier(new_tier: str, current_tier: str | None) -> bool:
    if current_tier is None:
        return True
    try:
        return ADVISORY_EVIDENCE_TIER_PRIORITY.index(new_tier) < ADVISORY_EVIDENCE_TIER_PRIORITY.index(current_tier)
    except ValueError:
        return False


def _signals_from_unmatched_advisory(
    snapshot: GraphSnapshot,
    hypotheses: list[dict[str, Any]],
    *,
    stale_seconds: int,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, frozenset[str]]] = set()
    for hypothesis in hypotheses:
        tier = hypothesis.get("relationship_strength_tier")
        if tier not in {
            "THEMATIC_HYPOTHESIS_ONLY",
            "PROBABILISTIC_HYPOTHESIS_ONLY",
            "STALE_OR_LAG_HYPOTHESIS_ONLY",
            "SIMILARITY_ONLY_RESEARCH",
        }:
            continue
        market_ids = [str(item) for item in hypothesis.get("source_market_ids") or []]
        nodes = _nodes(snapshot, market_ids)
        if len(nodes) < 2:
            continue
        if tier == "STALE_OR_LAG_HYPOTHESIS_ONLY":
            signal_type = "STALE_OR_LAG_WATCH"
        elif tier == "SIMILARITY_ONLY_RESEARCH":
            signal_type = "SIMILARITY_ONLY_RESEARCH"
        else:
            signal_type = "THEMATIC_CORRELATION_WATCH"
        key = (signal_type, frozenset(market_ids))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if tier == "SIMILARITY_ONLY_RESEARCH":
            confidence_tier = "LOW"
            severity = 22.0
        elif tier == "PROBABILISTIC_HYPOTHESIS_ONLY":
            confidence_tier = "MEDIUM" if hypothesis.get("confidence_tier") == "MEDIUM" else "LOW"
            severity = 32.0
        elif tier == "STALE_OR_LAG_HYPOTHESIS_ONLY":
            confidence_tier = "MEDIUM" if hypothesis.get("confidence_tier") == "MEDIUM" else "LOW"
            severity = 30.0
        else:
            confidence_tier = "MEDIUM" if hypothesis.get("confidence_tier") == "MEDIUM" else "LOW"
            severity = 28.0
        signals.append(
            _signal_row(
                snapshot=snapshot,
                signal_id=f"indicator:llm_advisory:{hypothesis['hypothesis_id']}",
                signal_type=signal_type,
                nodes=nodes,
                relationship_evidence_type=f"llm_advisory:{hypothesis['relationship_type']}",
                probability_inputs=_probability_inputs(snapshot, nodes, stale_seconds=stale_seconds),
                implied_direction="NO_SAFE_DIRECTION",
                confidence_tier=confidence_tier,
                severity_score=severity,
                blockers=sorted(
                    set(
                        ["advisory_llm_evidence_requires_independent_verification"]
                        + _price_blockers(snapshot, nodes, stale_seconds=stale_seconds)
                    )
                ),
                why_review_only_yet=_not_tradeable_reason(),
                llm_hypothesis_ids=[hypothesis["hypothesis_id"]],
                llm_advisory_evidence_strength_tier=tier,
            )
        )
    return signals


def _validate_signal(signal: dict[str, Any], path: str) -> None:
    required = [
        "signal_id",
        "signal_type",
        "markets_involved",
        "venues_involved",
        "relationship_evidence_type",
        "probability_inputs_used",
        "implied_direction",
        "confidence_tier",
        "severity_score",
        "diagnostic_only",
        "allowed_actions",
        "review_blockers",
        "why_review_only_yet",
    ]
    for key in required:
        if key not in signal:
            raise SchemaValidationError(f"{path}.{key} is required")
    if signal["signal_type"] not in SIGNAL_TYPES:
        raise SchemaValidationError(f"{path}.signal_type is not supported")
    if signal["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if signal.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if signal["allowed_actions"] != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    if any(action in DISALLOWED_ACTIONS for action in signal["allowed_actions"]):
        raise SchemaValidationError(f"{path}.allowed_actions contains a disallowed permission")
    if signal["confidence_tier"] not in CONFIDENCE_TIERS:
        raise SchemaValidationError(f"{path}.confidence_tier is not supported")
    if not isinstance(signal["severity_score"], (int, float)) or isinstance(signal["severity_score"], bool):
        raise SchemaValidationError(f"{path}.severity_score must be numeric")
    if not 0 <= float(signal["severity_score"]) <= 100:
        raise SchemaValidationError(f"{path}.severity_score must be in [0, 100]")
    for key in ["markets_involved", "venues_involved", "probability_inputs_used", "review_blockers"]:
        if not isinstance(signal[key], list):
            raise SchemaValidationError(f"{path}.{key} must be a list")
    if signal["signal_type"] == "SIMILARITY_ONLY_RESEARCH" and signal["confidence_tier"] == "HIGH":
        raise SchemaValidationError(f"{path}.similarity_only cannot be high confidence")
    if signal["signal_type"] == "THEMATIC_CORRELATION_WATCH" and signal["confidence_tier"] == "HIGH":
        raise SchemaValidationError(f"{path}.thematic_correlation cannot be high confidence")
    if signal["signal_type"] == "STALE_OR_LAG_WATCH" and signal["confidence_tier"] == "HIGH":
        raise SchemaValidationError(f"{path}.stale_or_lag cannot be high confidence")
    ids = signal.get("llm_hypothesis_ids")
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(item, str) for item in ids)):
        raise SchemaValidationError(f"{path}.llm_hypothesis_ids must be a list of strings")
    advisory_tier = signal.get("llm_advisory_evidence_strength_tier")
    if advisory_tier is not None and advisory_tier not in {*ADVISORY_EVIDENCE_TIER_PRIORITY}:
        raise SchemaValidationError(f"{path}.llm_advisory_evidence_strength_tier is not supported")
    advisory_role = signal.get("llm_advisory_evidence_role")
    if advisory_role not in {None, LLM_ADVISORY_EVIDENCE_ROLE}:
        raise SchemaValidationError(f"{path}.llm_advisory_evidence_role must be advisory")
    boost = signal.get("llm_advisory_severity_boost", 0.0)
    if not isinstance(boost, (int, float)) or isinstance(boost, bool) or float(boost) != 0.0:
        raise SchemaValidationError(f"{path}.llm_advisory_severity_boost must remain 0")
    if not isinstance(signal.get("corroborating_llm_evidence", False), bool):
        raise SchemaValidationError(f"{path}.corroborating_llm_evidence must be boolean")
    if signal["signal_type"] == "THEMATIC_CORRELATION_WATCH" and not signal.get("llm_hypothesis_ids"):
        raise SchemaValidationError(f"{path}.thematic_correlation requires llm_hypothesis_ids")
    _reject_disallowed_output_tokens(signal)


def _signal_type_from_violation(snapshot: GraphSnapshot, violation: ConsistencyViolation) -> str | None:
    if violation.kind == ViolationKind.COMPLEMENT_MISMATCH:
        return "COMPLEMENT_PRICE_DIVERGENCE"
    if violation.kind in {ViolationKind.SUBSET_OVER_SUPERSET, ViolationKind.IMPLICATION_VIOLATION}:
        return "SUBSET_SUPERSET_PRICE_VIOLATION"
    if violation.kind == ViolationKind.SUM_OVER_ONE:
        nodes = _nodes(snapshot, violation.involved_market_ids)
        if nodes and all("range-bucket" in node.themes for node in nodes):
            return "RANGE_BUCKET_INCONSISTENCY"
        return "EVENT_FAMILY_OUTLIER"
    if violation.kind == ViolationKind.REWORD_MISMATCH:
        return "EXACT_RELATIONSHIP_WATCH"
    if violation.kind == ViolationKind.AMBIGUOUS_WORDING:
        return "SIMILARITY_ONLY_RESEARCH"
    if violation.kind == ViolationKind.STALE_DIVERGENCE:
        return "STALE_OR_LAG_WATCH"
    return None


def _signal_type_from_constraint(constraint_type: str) -> str:
    return {
        "threshold_ladder": "THRESHOLD_LADDER_INVERSION",
        "range_bucket_partition": "RANGE_BUCKET_INCONSISTENCY",
        "exhaustive_group": "EVENT_FAMILY_OUTLIER",
        "mutually_exclusive_group": "EVENT_FAMILY_OUTLIER",
        "complement_parent_child": "COMPLEMENT_PRICE_DIVERGENCE",
        "nested_subset_chain": "SUBSET_SUPERSET_PRICE_VIOLATION",
    }.get(constraint_type, "EVENT_FAMILY_OUTLIER")


def _signal_type_from_formula(diagnostic: dict[str, Any], nodes: list[MarketNode]) -> str:
    relation = diagnostic["formula_relation"]
    if relation == "typed_formula_match_review_only":
        return "CROSS_VENUE_DIVERGENCE" if len({node.venue for node in nodes}) > 1 else "EXACT_RELATIONSHIP_WATCH"
    if relation == "threshold_ladder":
        return "THRESHOLD_LADDER_INVERSION"
    if relation == "overlap_not_identical":
        return "RANGE_BUCKET_INCONSISTENCY"
    return "SIMILARITY_ONLY_RESEARCH"


def _probability_inputs(snapshot: GraphSnapshot, nodes: list[MarketNode], *, stale_seconds: int) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    for node in nodes:
        probability, source = _probability_and_source(node)
        midpoint = (node.bid + node.ask) / 2.0 if node.bid is not None and node.ask is not None else None
        yes_price_equals_midpoint = (
            source == "yes_price"
            and midpoint is not None
            and node.yes_price is not None
            and abs(float(node.yes_price) - midpoint) <= 1e-9
        )
        inputs.append(
            {
                "market_id": node.market_id,
                "probability": round(probability, 6) if probability is not None else None,
                "probability_source": source,
                "bid": node.bid,
                "ask": node.ask,
                "quote_age_seconds": _quote_age_seconds(snapshot, node),
                "yes_price_equals_midpoint": yes_price_equals_midpoint,
                "non_actionable_input": True,
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


def _probability_and_source(node: MarketNode) -> tuple[float | None, str]:
    if node.yes_price is not None:
        return node.yes_price, "yes_price"
    if node.bid is not None and node.ask is not None:
        return (node.bid + node.ask) / 2, "diagnostic_midpoint"
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
        quote_age = _quote_age_seconds(snapshot, node)
        if quote_age is None:
            blockers.append("missing_quote_timestamp")
        elif quote_age > stale_seconds:
            blockers.append("stale_quote")
        if node.bid is None or node.ask is None:
            blockers.append("missing_bid_or_ask")
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


def _nodes(snapshot: GraphSnapshot, market_ids: list[str]) -> list[MarketNode]:
    return [snapshot.nodes[market_id] for market_id in market_ids if market_id in snapshot.nodes]


def _max_probability_delta(nodes: list[MarketNode]) -> float:
    values = []
    for node in nodes:
        probability, _ = _probability_and_source(node)
        if probability is not None:
            values.append(probability)
    if len(values) < 2:
        return 0.0
    return max(values) - min(values)


def _base_score(signal_type: str) -> float:
    return {
        "EXACT_RELATIONSHIP_WATCH": 60.0,
        "COMPLEMENT_PRICE_DIVERGENCE": 74.0,
        "SUBSET_SUPERSET_PRICE_VIOLATION": 78.0,
        "THRESHOLD_LADDER_INVERSION": 76.0,
        "RANGE_BUCKET_INCONSISTENCY": 72.0,
        "EVENT_FAMILY_OUTLIER": 68.0,
        "CROSS_VENUE_DIVERGENCE": 58.0,
        "STALE_OR_LAG_WATCH": 35.0,
        "SIMILARITY_ONLY_RESEARCH": 22.0,
    }[signal_type]


def _score(
    base_score: float,
    magnitude: float,
    nodes: list[MarketNode],
    blockers: list[str],
    evidence_strength: float,
) -> float:
    score = base_score + min(20.0, max(0.0, magnitude) * 100.0)
    score *= max(0.25, min(1.0, evidence_strength))
    price_blockers = set(blockers)
    if any(_probability_and_source(node)[1] == "diagnostic_midpoint" for node in nodes):
        score -= 8.0
    if {"stale_input", "stale_quote"} & price_blockers:
        score -= 12.0
    if "missing_quote_timestamp" in price_blockers:
        score -= 6.0
    if "missing_probability_input" in price_blockers:
        score -= 25.0
    if any("mismatch" in blocker or "ambiguous" in blocker for blocker in blockers):
        score -= 15.0
    return round(max(0.0, min(100.0, score)), 3)


def _confidence_tier(signal_type: str, severity: float, blockers: list[str]) -> str:
    if signal_type == "SIMILARITY_ONLY_RESEARCH" or blockers:
        return "LOW"
    if severity >= 70:
        return "HIGH"
    if severity >= 40:
        return "MEDIUM"
    return "LOW"


def _evidence_strength_from_violation(violation: ConsistencyViolation) -> float:
    strength = float(violation.confidence)
    if violation.edge_source == "llm":
        strength = min(strength, 0.45)
    if violation.kind == ViolationKind.AMBIGUOUS_WORDING:
        strength = min(strength, 0.35)
    return strength


def _formula_evidence_strength(diagnostic: dict[str, Any]) -> float:
    relation = diagnostic["formula_relation"]
    if relation == "typed_formula_match_review_only":
        return 0.65
    if relation in {"threshold_ladder", "overlap_not_identical"}:
        return 0.6
    return 0.3


def _violation_evidence_type(violation: ConsistencyViolation) -> str:
    if violation.involved_edge_ids:
        return f"graph_edge:{violation.kind.value}:{violation.edge_source or 'unknown'}"
    return f"exclusion_set:{violation.kind.value}"


def _implied_direction_for_violation(violation: ConsistencyViolation) -> str:
    if violation.kind in {ViolationKind.SUBSET_OVER_SUPERSET, ViolationKind.IMPLICATION_VIOLATION}:
        return "NARROWER_HIGH_RELATIVE_TO_BROADER"
    if violation.kind == ViolationKind.COMPLEMENT_MISMATCH:
        return "COMPLEMENT_SUM_REVIEW"
    if violation.kind == ViolationKind.SUM_OVER_ONE:
        return "GROUP_SUM_HIGH_REVIEW"
    if violation.kind == ViolationKind.REWORD_MISMATCH:
        return "RELATED_MARKET_DIVERGENCE_REVIEW"
    return "NO_SAFE_DIRECTION"


def _implied_direction_for_constraint(constraint_type: str) -> str:
    return {
        "threshold_ladder": "STRICTER_THRESHOLD_HIGH_RELATIVE_TO_LOOSER",
        "range_bucket_partition": "RANGE_BUCKET_GROUP_HIGH_REVIEW",
        "exhaustive_group": "EVENT_FAMILY_GROUP_HIGH_REVIEW",
        "mutually_exclusive_group": "EVENT_FAMILY_GROUP_HIGH_REVIEW",
        "complement_parent_child": "COMPLEMENT_OR_CHILD_PARENT_REVIEW",
        "nested_subset_chain": "NARROWER_HIGH_RELATIVE_TO_BROADER",
    }.get(constraint_type, "NO_SAFE_DIRECTION")


def _implied_direction_for_formula(signal_type: str, nodes: list[MarketNode]) -> str:
    if signal_type == "THRESHOLD_LADDER_INVERSION":
        return "THRESHOLD_SEQUENCE_REVIEW"
    if signal_type == "RANGE_BUCKET_INCONSISTENCY":
        return "RANGE_RELATION_REVIEW"
    if signal_type in {"CROSS_VENUE_DIVERGENCE", "EXACT_RELATIONSHIP_WATCH"} and len(nodes) >= 2:
        ordered = sorted(nodes, key=lambda node: (_probability_and_source(node)[0] or 0.0), reverse=True)
        return f"HIGHER_DIAGNOSTIC_PROBABILITY:{ordered[0].market_id}"
    return "NO_SAFE_DIRECTION"


def _default_blockers(blockers: list[str]) -> list[str]:
    base = {
        "requires_independent_payoff_verification",
        "requires_fee_depth_freshness_review",
        "no_execution_permission",
        "not_evaluator_input",
    }
    return sorted(base | set(blockers))


def _not_tradeable_reason() -> str:
    return (
        "Graph signal only; relative-value evidence, settlement checks, fee/depth review, "
        "and freshness review must be completed outside this report."
    )


def _dedupe_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for signal in signals:
        key = (signal["signal_type"], tuple(sorted(signal["markets_involved"])))
        existing = by_key.get(key)
        if existing is None or signal["severity_score"] > existing["severity_score"]:
            by_key[key] = signal
    return list(by_key.values())


def _reject_disallowed_output_tokens(payload: Any) -> None:
    findings: list[str] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized_key = str(key).upper().replace("-", "_")
                if normalized_key in DISALLOWED_OUTPUT_TOKENS:
                    findings.append(f"{path}.{key}" if path else str(key))
                visit(nested, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")
        elif isinstance(value, str):
            normalized = value.upper().replace("-", "_").replace(" ", "_")
            if any(token in normalized for token in DISALLOWED_OUTPUT_TOKENS):
                findings.append(path)

    visit(payload, "")
    if findings:
        raise SchemaValidationError(f"trade indicator contains disallowed output token: {sorted(set(findings))}")


__all__ = [
    "SIGNAL_TYPES",
    "build_trade_indicator_report",
    "validate_trade_indicator_report",
    "write_trade_indicator_csv",
    "write_trade_indicator_report",
]
