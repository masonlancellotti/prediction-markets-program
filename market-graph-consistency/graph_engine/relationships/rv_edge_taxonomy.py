"""Relationship-edge taxonomy for relative-value diagnostic ingestion.

This module defines a *non-exact* relationship vocabulary that the market
graph uses to remember relative-value diagnostics from sibling repos (e.g.
``relative-value-scanner``).  The graph is the relationship memory layer;
it does **not** create exact same-payoff evidence, executable handoff, nor
relative-value evaluator inputs.  Every edge produced under this taxonomy
is diagnostic-only and capped at WATCH / MANUAL_REVIEW /
BASIS_RISK_REVIEW / SOURCE_REVIEW / IGNORE_LOW_CONFIDENCE.

The field ``can_emit_evaluator_input`` is the graph-safe alias for the
``can_create_paper_candidate`` concept used in RV reports — the graph
safety vocabulary forbids the substring ``paper_candidate`` so the edge
schema renames the flag and preserves the semantic: the graph cannot ever
hand a row to the relative-value evaluator without a separate strict gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

# Relationship action levels the graph is allowed to attach to an edge.
ACTION_WATCH = "WATCH"
ACTION_MANUAL_REVIEW = "MANUAL_REVIEW"
ACTION_BASIS_RISK_REVIEW = "BASIS_RISK_REVIEW"
ACTION_SOURCE_REVIEW = "SOURCE_REVIEW"
ACTION_IGNORE_LOW_CONFIDENCE = "IGNORE_LOW_CONFIDENCE"

ALLOWED_EDGE_ACTIONS: tuple[str, ...] = (
    ACTION_WATCH,
    ACTION_MANUAL_REVIEW,
    ACTION_BASIS_RISK_REVIEW,
    ACTION_SOURCE_REVIEW,
    ACTION_IGNORE_LOW_CONFIDENCE,
)

# Relationship types the graph attaches to RV-ingested edges.  The
# taxonomy intentionally separates near-exact-review candidates from
# basis-risk and structural relationships so the daily worklist can be
# clustered by blocker family.
RV_RELATIONSHIP_TYPES: dict[str, str] = {
    # near-exact review candidates (still require strict RV gate)
    "SAME_PAYOFF_CANDIDATE_REVIEW": "near_exact_review",
    "SAME_EVENT_SAME_THRESHOLD_REVIEW": "near_exact_review",
    "SAME_EVENT_DIFFERENT_SOURCE_REVIEW": "near_exact_review",
    # basis-risk / non-exact economic relationships
    "BASIS_RISK_SAME_ASSET_DIFFERENT_SOURCE": "basis_risk",
    "BASIS_RISK_SAME_ASSET_DIFFERENT_OBSERVATION_TIME": "basis_risk",
    "DEADLINE_TOUCH_VS_POINT_IN_TIME": "basis_risk",
    "INTRADAY_TOUCH_VS_DAILY_CLOSE": "basis_risk",
    "INTRADAY_TOUCH_VS_POINT_IN_TIME": "basis_risk",
    "HOURLY_POINT_IN_TIME_VS_DAILY_5PM": "basis_risk",
    "WEEKLY_FRIDAY_CLOSE_VS_DEADLINE_TOUCH": "basis_risk",
    "DAILY_DIRECTION_VS_PRICE_THRESHOLD": "basis_risk",
    "SAME_DIRECTION_DIFFERENT_REFERENCE": "basis_risk",
    "MIDPOINT_VS_UPPER_BOUND": "basis_risk",
    "UPPER_BOUND_VS_EFFECTIVE_RATE": "basis_risk",
    "RANGE_BUCKET_VS_THRESHOLD": "basis_risk",
    "ALL_TIME_HIGH_BY_DATE_VS_POINT_IN_TIME": "basis_risk",
    "SAME_THRESHOLD_DIFFERENT_WINDOW": "basis_risk",
    "SAME_DATE_DIFFERENT_TIMEZONE": "basis_risk",
    "SAME_ASSET_DIFFERENT_INDEX_SOURCE": "basis_risk",
    # structural relationships (no exact-equality claim)
    "SUBSET_SUPERSET": "structural",
    "COMPLEMENT": "structural",
    "MUTUALLY_EXCLUSIVE": "structural",
    "EXHAUSTIVE_GROUP_MEMBER": "structural",
    "THRESHOLD_LADDER_NEIGHBOR": "structural",
    "THRESHOLD_LADDER_INVERSION_WATCH": "structural",
    "RANGE_BUCKET_PARTITION": "structural",
    "EVENT_WINNER_SAME_FIELD": "structural",
    # reference-only / fair-value anchors
    "FAIR_VALUE_REFERENCE_ONLY": "reference_only",
    "SPORTSBOOK_REFERENCE_ONLY": "reference_only",
    "TRUTH_FEED_ANCHOR_ONLY": "reference_only",
    # weak / noisy signals
    "TITLE_SIMILARITY_ONLY": "weak_signal",
    "SAME_TOPIC_WEAK_SIGNAL": "weak_signal",
    "AMBIGUOUS_RELATIONSHIP": "weak_signal",
    "NO_CURRENT_PEER": "weak_signal",
}

RELATIONSHIP_FAMILIES: tuple[str, ...] = (
    "near_exact_review",
    "basis_risk",
    "structural",
    "reference_only",
    "weak_signal",
)

CONFIDENCE_BUCKETS: tuple[str, ...] = ("low", "medium", "high")

# Diagnostic blockers stamped on every RV-ingested edge.  These document
# why the edge is review-only and never an evaluator input.  The strict
# graph contract forbids any edge from claiming exact equality of payoff
# unless the source RV report has independently proven it.
REQUIRED_RV_EDGE_BLOCKERS: tuple[str, ...] = (
    "not_evaluator_input",
    "requires_independent_payoff_verification",
    "settlement_source_not_verified",
    "settlement_time_not_verified",
    "fee_model_not_verified",
    "quote_freshness_not_verified",
)

# Additional context-specific blockers we may attach when the underlying
# RV evidence is missing the corresponding typed-key dimension.
CONTEXT_RV_EDGE_BLOCKERS: tuple[str, ...] = (
    "title_similarity_not_structural_evidence",
    "reference_only_source",
    "deadline_touch_not_point_in_time",
    "payoff_shape_mismatch",
    "source_index_mismatch",
    "no_current_peer",
    "manual_discovery_required",
    "intraday_observation_vs_daily_close",
    "range_bucket_vs_threshold_touch",
    "all_time_high_window_vs_point_in_time",
    "different_observation_timezone",
    "weak_signal_topic_only",
    "ambiguous_relationship_classification",
)

ALL_RV_EDGE_BLOCKERS: frozenset[str] = frozenset(
    list(REQUIRED_RV_EDGE_BLOCKERS) + list(CONTEXT_RV_EDGE_BLOCKERS)
)

RELATIONSHIP_VERSION = "rv-edge-taxonomy-v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EdgeTaxonomyError(ValueError):
    """Raised when an RV edge payload violates the diagnostic contract."""


def is_near_exact_review(relationship_type: str) -> bool:
    return RV_RELATIONSHIP_TYPES.get(relationship_type) == "near_exact_review"


def is_basis_risk(relationship_type: str) -> bool:
    return RV_RELATIONSHIP_TYPES.get(relationship_type) == "basis_risk"


def is_structural(relationship_type: str) -> bool:
    return RV_RELATIONSHIP_TYPES.get(relationship_type) == "structural"


def is_reference_only(relationship_type: str) -> bool:
    return RV_RELATIONSHIP_TYPES.get(relationship_type) == "reference_only"


def is_weak_signal(relationship_type: str) -> bool:
    return RV_RELATIONSHIP_TYPES.get(relationship_type) == "weak_signal"


def default_action_for(relationship_type: str) -> str:
    """Map relationship_type to the most restrictive default action.

    Near-exact review edges still need RV's strict gate — graph never
    promotes them past MANUAL_REVIEW.  Basis-risk edges default to the
    BASIS_RISK_REVIEW lane so the daily worklist can cluster by mismatch
    family.  Weak signals default to IGNORE_LOW_CONFIDENCE so the
    handoff report can suppress them by default.
    """

    if is_near_exact_review(relationship_type):
        return ACTION_MANUAL_REVIEW
    if is_basis_risk(relationship_type):
        return ACTION_BASIS_RISK_REVIEW
    if is_structural(relationship_type):
        return ACTION_MANUAL_REVIEW
    if is_reference_only(relationship_type):
        return ACTION_SOURCE_REVIEW
    if is_weak_signal(relationship_type):
        return ACTION_IGNORE_LOW_CONFIDENCE
    return ACTION_WATCH


def make_rv_edge(
    *,
    edge_id: str,
    left_market_id: str,
    right_market_id: str | None,
    left_venue: str,
    right_venue: str,
    relationship_type: str,
    action: str | None = None,
    confidence_bucket: str = "low",
    right_reference_id: str | None = None,
    evidence_fields: dict[str, Any] | None = None,
    blockers: Iterable[str] | None = None,
    required_review_fields: Iterable[str] | None = None,
    source_report_paths: Iterable[str] | None = None,
    rationale: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a single RV-ingested relationship edge payload.

    The returned dict is the canonical edge schema used by the importer,
    worklist exporter, LLM workbench, and ops-status surfaces.  Calling
    :func:`validate_rv_edge` on the result is cheap and recommended.
    """

    if relationship_type not in RV_RELATIONSHIP_TYPES:
        raise EdgeTaxonomyError(f"unknown relationship_type: {relationship_type!r}")
    if confidence_bucket not in CONFIDENCE_BUCKETS:
        raise EdgeTaxonomyError(f"unsupported confidence_bucket: {confidence_bucket!r}")
    if right_market_id is None and right_reference_id is None:
        raise EdgeTaxonomyError("rv edge must have a right_market_id or right_reference_id")
    action = action or default_action_for(relationship_type)
    if action not in ALLOWED_EDGE_ACTIONS:
        raise EdgeTaxonomyError(f"unsupported action: {action!r}")

    merged_blockers: list[str] = list(REQUIRED_RV_EDGE_BLOCKERS)
    for blocker in blockers or []:
        if not isinstance(blocker, str) or not blocker:
            continue
        if blocker not in merged_blockers:
            merged_blockers.append(blocker)

    if is_reference_only(relationship_type) and "reference_only_source" not in merged_blockers:
        merged_blockers.append("reference_only_source")
    if relationship_type == "DEADLINE_TOUCH_VS_POINT_IN_TIME" and "deadline_touch_not_point_in_time" not in merged_blockers:
        merged_blockers.append("deadline_touch_not_point_in_time")
    if relationship_type == "TITLE_SIMILARITY_ONLY" and "title_similarity_not_structural_evidence" not in merged_blockers:
        merged_blockers.append("title_similarity_not_structural_evidence")
    if relationship_type == "NO_CURRENT_PEER" and "no_current_peer" not in merged_blockers:
        merged_blockers.append("no_current_peer")

    review_fields = list(required_review_fields or [])
    if not review_fields:
        review_fields = _default_review_fields(relationship_type)

    edge: dict[str, Any] = {
        "edge_id": edge_id,
        "left_market_id": left_market_id,
        "right_market_id": right_market_id,
        "right_reference_id": right_reference_id,
        "left_venue": left_venue,
        "right_venue": right_venue,
        "relationship_type": relationship_type,
        "relationship_family": RV_RELATIONSHIP_TYPES[relationship_type],
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "action": action,
        "confidence_bucket": confidence_bucket,
        "exact_payoff": False,
        "can_create_candidate_pair": False,
        # can_emit_evaluator_input is the graph-safe alias for the
        # "paper candidate" boolean used in RV reports.  Graph never
        # creates evaluator inputs; the RV strict board is the only
        # path that promotes a row beyond review.
        "can_emit_evaluator_input": False,
        "evidence_fields": dict(evidence_fields or {}),
        "blockers": merged_blockers,
        "required_review_fields": review_fields,
        "source_report_paths": [str(item) for item in source_report_paths or [] if item],
        "rationale": rationale or _default_rationale(relationship_type),
        "allowed_edge_actions": list(ALLOWED_EDGE_ACTIONS),
        "relationship_version": RELATIONSHIP_VERSION,
        "created_at": created_at or _now_iso(),
    }
    validate_rv_edge(edge)
    return edge


def validate_rv_edge(edge: dict[str, Any]) -> None:
    """Strict per-edge validator enforcing the diagnostic-only contract."""

    if not isinstance(edge, dict):
        raise EdgeTaxonomyError("edge must be an object")
    for required in (
        "edge_id",
        "left_market_id",
        "left_venue",
        "right_venue",
        "relationship_type",
        "relationship_family",
        "diagnostic_only",
        "affects_evaluator_gates",
        "action",
        "confidence_bucket",
        "exact_payoff",
        "can_create_candidate_pair",
        "can_emit_evaluator_input",
        "evidence_fields",
        "blockers",
        "required_review_fields",
        "source_report_paths",
        "rationale",
        "allowed_edge_actions",
        "relationship_version",
        "created_at",
    ):
        if required not in edge:
            raise EdgeTaxonomyError(f"edge missing required field {required!r}")
    if edge["diagnostic_only"] is not True:
        raise EdgeTaxonomyError("edge.diagnostic_only must be true")
    if edge["affects_evaluator_gates"] is not False:
        raise EdgeTaxonomyError("edge.affects_evaluator_gates must be false")
    if edge["exact_payoff"] is not False:
        raise EdgeTaxonomyError("edge.exact_payoff must be false; graph never claims exact equality")
    if edge["can_create_candidate_pair"] is not False:
        raise EdgeTaxonomyError("edge.can_create_candidate_pair must be false")
    if edge["can_emit_evaluator_input"] is not False:
        raise EdgeTaxonomyError("edge.can_emit_evaluator_input must be false")
    if edge["relationship_type"] not in RV_RELATIONSHIP_TYPES:
        raise EdgeTaxonomyError(f"edge.relationship_type not allowed: {edge['relationship_type']!r}")
    if edge["relationship_family"] != RV_RELATIONSHIP_TYPES[edge["relationship_type"]]:
        raise EdgeTaxonomyError("edge.relationship_family inconsistent with relationship_type")
    if edge["action"] not in ALLOWED_EDGE_ACTIONS:
        raise EdgeTaxonomyError(f"edge.action not allowed: {edge['action']!r}")
    if edge["confidence_bucket"] not in CONFIDENCE_BUCKETS:
        raise EdgeTaxonomyError(f"edge.confidence_bucket not allowed: {edge['confidence_bucket']!r}")
    if edge.get("right_market_id") is None and edge.get("right_reference_id") is None:
        raise EdgeTaxonomyError("edge must have right_market_id or right_reference_id")
    if not isinstance(edge["blockers"], list) or not edge["blockers"]:
        raise EdgeTaxonomyError("edge.blockers must contain the required diagnostic blockers")
    for required_blocker in REQUIRED_RV_EDGE_BLOCKERS:
        if required_blocker not in edge["blockers"]:
            raise EdgeTaxonomyError(f"edge.blockers must contain {required_blocker!r}")
    if not isinstance(edge["required_review_fields"], list):
        raise EdgeTaxonomyError("edge.required_review_fields must be a list")
    if not isinstance(edge["evidence_fields"], dict):
        raise EdgeTaxonomyError("edge.evidence_fields must be an object")
    if not isinstance(edge["source_report_paths"], list):
        raise EdgeTaxonomyError("edge.source_report_paths must be a list")
    if edge["relationship_version"] != RELATIONSHIP_VERSION:
        raise EdgeTaxonomyError("edge.relationship_version must match the current taxonomy version")
    if edge["allowed_edge_actions"] != list(ALLOWED_EDGE_ACTIONS):
        raise EdgeTaxonomyError("edge.allowed_edge_actions must match the current taxonomy")
    if is_reference_only(edge["relationship_type"]) and "reference_only_source" not in edge["blockers"]:
        raise EdgeTaxonomyError("reference-only edge must list reference_only_source blocker")
    if edge["relationship_type"] == "TITLE_SIMILARITY_ONLY" and edge["confidence_bucket"] != "low":
        raise EdgeTaxonomyError("title-similarity-only edge must be low confidence")
    if is_near_exact_review(edge["relationship_type"]) and edge["action"] not in {
        ACTION_MANUAL_REVIEW,
        ACTION_SOURCE_REVIEW,
    }:
        raise EdgeTaxonomyError("near-exact review edge action must be MANUAL_REVIEW or SOURCE_REVIEW")
    if is_weak_signal(edge["relationship_type"]) and edge["action"] not in {
        ACTION_WATCH,
        ACTION_IGNORE_LOW_CONFIDENCE,
    }:
        raise EdgeTaxonomyError("weak-signal edge action must be WATCH or IGNORE_LOW_CONFIDENCE")


def _default_rationale(relationship_type: str) -> str:
    family = RV_RELATIONSHIP_TYPES[relationship_type]
    if family == "near_exact_review":
        return (
            "Near-exact review candidate. Independent payoff, settlement source, "
            "and quote-freshness review required before any exact comparison."
        )
    if family == "basis_risk":
        return (
            "Basis-risk relationship. Markets share an asset/event family but "
            "differ in observation window, settlement source, or comparator. "
            "Graph keeps the link for routing but does not claim equality."
        )
    if family == "structural":
        return (
            "Structural relationship between markets in the same logical group. "
            "Edge is structural only and not a same-payoff claim."
        )
    if family == "reference_only":
        return (
            "Reference-only source. Fair-value anchor, sportsbook, or truth feed "
            "used for context; not an executable counterpart."
        )
    return (
        "Weak or ambiguous relationship surfaced from RV diagnostics; held for "
        "manual review and clustering. Not a structural or exact equivalence."
    )


def _default_review_fields(relationship_type: str) -> list[str]:
    common = [
        "settlement_source",
        "settlement_time",
        "observation_window",
        "asset_family",
        "comparator",
        "threshold",
        "timezone",
        "quote_freshness",
    ]
    family = RV_RELATIONSHIP_TYPES[relationship_type]
    if family == "near_exact_review":
        return common + ["independent_payoff_evidence"]
    if family == "reference_only":
        return ["fair_value_index_source", "reference_quote_freshness"]
    if family == "weak_signal":
        return ["title_evidence", "structural_evidence_or_blocker"]
    return common


__all__ = [
    "ACTION_BASIS_RISK_REVIEW",
    "ACTION_IGNORE_LOW_CONFIDENCE",
    "ACTION_MANUAL_REVIEW",
    "ACTION_SOURCE_REVIEW",
    "ACTION_WATCH",
    "ALLOWED_EDGE_ACTIONS",
    "ALL_RV_EDGE_BLOCKERS",
    "CONFIDENCE_BUCKETS",
    "CONTEXT_RV_EDGE_BLOCKERS",
    "EdgeTaxonomyError",
    "RELATIONSHIP_FAMILIES",
    "RELATIONSHIP_VERSION",
    "REQUIRED_RV_EDGE_BLOCKERS",
    "RV_RELATIONSHIP_TYPES",
    "default_action_for",
    "is_basis_risk",
    "is_near_exact_review",
    "is_reference_only",
    "is_structural",
    "is_weak_signal",
    "make_rv_edge",
    "validate_rv_edge",
]
