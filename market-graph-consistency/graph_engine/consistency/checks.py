from __future__ import annotations

from graph_engine.consistency.tolerances import (
    DEFAULT_EDGE_TOLERANCE,
    DEFAULT_EXCLUSION_TOLERANCE,
    DEFAULT_MARKET_SIGNAL_CONFIDENCE,
    DEFAULT_REWORD_TOLERANCE,
    action_for_violation,
    spread_buffer,
)
from graph_engine.models import (
    ConsistencyViolation,
    ExclusionCompleteness,
    ExclusionSet,
    GraphSnapshot,
    MarketNode,
    RelationshipEdge,
    RelationshipType,
    ViolationKind,
)
from graph_engine.relationships.confidence import combine_confidences


def _node(snapshot: GraphSnapshot, market_id: str) -> MarketNode:
    try:
        return snapshot.nodes[market_id]
    except KeyError as exc:
        raise ValueError(f"Unknown market_id in check: {market_id}") from exc


def _edge_confidence(edge: RelationshipEdge) -> float:
    return combine_confidences(edge.confidence, DEFAULT_MARKET_SIGNAL_CONFIDENCE)


def _make_pair_violation(
    snapshot: GraphSnapshot,
    edge: RelationshipEdge,
    kind: ViolationKind,
    raw_gap: float,
    adjusted_gap: float,
    explanation: str,
    review_questions: list[str],
) -> ConsistencyViolation | None:
    magnitude = max(0.0, adjusted_gap)
    if magnitude <= 1e-12:
        return None
    confidence = _edge_confidence(edge)
    return ConsistencyViolation(
        violation_id=f"{kind.value}:{edge.edge_id}",
        snapshot_id=snapshot.snapshot_id,
        kind=kind,
        involved_market_ids=[edge.src_market_id, edge.dst_market_id],
        involved_edge_ids=[edge.edge_id],
        magnitude=magnitude,
        raw_gap=raw_gap,
        spread_adjusted_gap=adjusted_gap,
        confidence=confidence,
        action=action_for_violation(kind, confidence, magnitude),
        explanation=explanation,
        review_questions=review_questions,
    )


def check_implication(snapshot: GraphSnapshot, edge: RelationshipEdge) -> ConsistencyViolation | None:
    if edge.relation != RelationshipType.IMPLICATION:
        return None
    src = _node(snapshot, edge.src_market_id)
    dst = _node(snapshot, edge.dst_market_id)
    raw_gap = src.probability - dst.probability
    adjusted_gap = raw_gap - DEFAULT_EDGE_TOLERANCE - spread_buffer(src.spread, dst.spread)
    return _make_pair_violation(
        snapshot=snapshot,
        edge=edge,
        kind=ViolationKind.IMPLICATION_VIOLATION,
        raw_gap=raw_gap,
        adjusted_gap=adjusted_gap,
        explanation=(
            f"{src.market_id} is modeled as implying {dst.market_id}, but its probability "
            f"is higher after tolerance and spread buffer."
        ),
        review_questions=[
            "Does the source market truly imply the destination market under resolution wording?",
            "Are both snapshots fresh enough to compare?",
            "Could fees, wide spreads, or venue-specific wording explain the gap?",
        ],
    )


def check_subset(snapshot: GraphSnapshot, edge: RelationshipEdge) -> ConsistencyViolation | None:
    if edge.relation not in {RelationshipType.SUBSET, RelationshipType.SUPERSET}:
        return None

    if edge.relation == RelationshipType.SUBSET:
        narrower = _node(snapshot, edge.src_market_id)
        broader = _node(snapshot, edge.dst_market_id)
    else:
        narrower = _node(snapshot, edge.dst_market_id)
        broader = _node(snapshot, edge.src_market_id)

    raw_gap = narrower.probability - broader.probability
    adjusted_gap = raw_gap - DEFAULT_EDGE_TOLERANCE - spread_buffer(narrower.spread, broader.spread)
    return _make_pair_violation(
        snapshot=snapshot,
        edge=edge,
        kind=ViolationKind.SUBSET_OVER_SUPERSET,
        raw_gap=raw_gap,
        adjusted_gap=adjusted_gap,
        explanation=(
            f"{narrower.market_id} is modeled as narrower than {broader.market_id}, but "
            "the narrower outcome has a higher probability after buffers."
        ),
        review_questions=[
            "Is the subset relationship valid across venues and resolution sources?",
            "Does the broader market include all cases covered by the narrower market?",
            "Are there stale prices or sparse liquidity behind either quote?",
        ],
    )


def check_same_event_reworded(snapshot: GraphSnapshot, edge: RelationshipEdge) -> ConsistencyViolation | None:
    if edge.relation != RelationshipType.SAME_EVENT_REWORDED:
        return None
    src = _node(snapshot, edge.src_market_id)
    dst = _node(snapshot, edge.dst_market_id)
    raw_gap = abs(src.probability - dst.probability)
    adjusted_gap = raw_gap - DEFAULT_REWORD_TOLERANCE - spread_buffer(src.spread, dst.spread)
    return _make_pair_violation(
        snapshot=snapshot,
        edge=edge,
        kind=ViolationKind.REWORD_MISMATCH,
        raw_gap=raw_gap,
        adjusted_gap=adjusted_gap,
        explanation=(
            f"{src.market_id} and {dst.market_id} are modeled as rewordings of the same event, "
            "but their probabilities differ beyond configured buffers."
        ),
        review_questions=[
            "Do both contracts resolve from the same source and date window?",
            "Is one market using a materially different threshold or definition?",
            "Could stale or low-liquidity quotes explain the mismatch?",
        ],
    )


def check_ambiguous_wording(snapshot: GraphSnapshot, edge: RelationshipEdge) -> ConsistencyViolation | None:
    if edge.relation != RelationshipType.AMBIGUOUS:
        return None
    confidence = edge.confidence
    kind = ViolationKind.AMBIGUOUS_WORDING
    return ConsistencyViolation(
        violation_id=f"{kind.value}:{edge.edge_id}",
        snapshot_id=snapshot.snapshot_id,
        kind=kind,
        involved_market_ids=[edge.src_market_id, edge.dst_market_id],
        involved_edge_ids=[edge.edge_id],
        magnitude=0.0,
        raw_gap=0.0,
        spread_adjusted_gap=0.0,
        confidence=confidence,
        action=action_for_violation(kind, confidence, 0.0),
        explanation="The relationship is intentionally marked ambiguous and needs human wording review before any hard constraint is used.",
        review_questions=[
            "What exact relationship, if any, should be promoted from this ambiguous edge?",
            "Which resolution words create the ambiguity?",
            "Should this remain documentation-only until more snapshots are available?",
        ],
    )


def check_exclusion_set(snapshot: GraphSnapshot, exclusion: ExclusionSet) -> ConsistencyViolation | None:
    nodes = [_node(snapshot, market_id) for market_id in exclusion.member_market_ids]
    raw_sum = sum(node.probability for node in nodes)
    tolerance = exclusion.tolerance if exclusion.tolerance is not None else DEFAULT_EXCLUSION_TOLERANCE
    raw_gap = raw_sum - 1.0
    adjusted_gap = raw_gap - tolerance - spread_buffer(*(node.spread for node in nodes))
    if adjusted_gap <= 1e-12:
        return None

    base_confidence = 0.95 if exclusion.completeness == ExclusionCompleteness.PARTITION else 0.75
    confidence = combine_confidences(base_confidence, DEFAULT_MARKET_SIGNAL_CONFIDENCE)
    kind = ViolationKind.SUM_OVER_ONE
    return ConsistencyViolation(
        violation_id=f"{kind.value}:{exclusion.set_id}",
        snapshot_id=snapshot.snapshot_id,
        kind=kind,
        involved_market_ids=list(exclusion.member_market_ids),
        involved_edge_ids=[],
        magnitude=max(0.0, adjusted_gap),
        raw_gap=raw_gap,
        spread_adjusted_gap=adjusted_gap,
        confidence=confidence,
        action=action_for_violation(kind, confidence, adjusted_gap),
        explanation=(
            f"Exclusion set {exclusion.set_id} sums to {raw_sum:.3f}, above 1.0 after "
            "configured tolerance and spread buffer."
        ),
        review_questions=[
            "Are all listed outcomes truly mutually exclusive?",
            "Is the set complete or only a subset of possible winners?",
            "Do venue rules allow ties, cancellations, or different resolution windows?",
        ],
    )
