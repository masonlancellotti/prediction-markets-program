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
    Action,
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

MAX_NODE_AGE_SECONDS = 24 * 60 * 60


def _node(snapshot: GraphSnapshot, market_id: str) -> MarketNode:
    try:
        return snapshot.nodes[market_id]
    except KeyError as exc:
        raise ValueError(f"Unknown market_id in check: {market_id}") from exc


def _edge_confidence(edge: RelationshipEdge) -> float:
    return combine_confidences(edge.confidence, DEFAULT_MARKET_SIGNAL_CONFIDENCE)


def _is_stale(snapshot: GraphSnapshot, node: MarketNode, max_node_age_seconds: int = MAX_NODE_AGE_SECONDS) -> bool:
    return (snapshot.as_of - node.as_of).total_seconds() > max_node_age_seconds


def _pair_blockers(snapshot: GraphSnapshot, edge: RelationshipEdge, src: MarketNode, dst: MarketNode) -> list[str]:
    blockers: list[str] = []
    if src.reference_only or dst.reference_only:
        blockers.append("reference_only_node")
    if _is_stale(snapshot, src) or _is_stale(snapshot, dst):
        blockers.append("stale_input")
    if edge.source.value == "llm" and edge.reviewed_by is None:
        blockers.append("llm_edge_unreviewed")
    return blockers


def _cap_limit(blockers: list[str], kind: ViolationKind | None = None) -> Action:
    if "reference_only_node" in blockers or "llm_edge_unreviewed" in blockers:
        return Action.WATCH
    if kind == ViolationKind.AMBIGUOUS_WORDING:
        return Action.WATCH
    return Action.MANUAL_REVIEW


def _cap_action(action: Action, blockers: list[str], kind: ViolationKind | None = None) -> Action:
    if _cap_limit(blockers, kind) == Action.WATCH and action == Action.MANUAL_REVIEW:
        return Action.WATCH
    return action


def _cap_reason(blockers: list[str]) -> str:
    if "reference_only_node" in blockers:
        return "reference_only_diagnostic_only"
    if "stale_input" in blockers:
        return "stale_input_manual_review_cap"
    if "llm_edge_unreviewed" in blockers:
        return "llm_unreviewed_watch_cap"
    return "diagnostic_only"


def _review_status(edge: RelationshipEdge) -> str:
    if edge.reviewed_by:
        return "reviewed"
    if edge.source.value == "manual":
        return "manual_unreviewed"
    return "unreviewed"


def _diagnostic_wording_violation(
    snapshot: GraphSnapshot,
    edge: RelationshipEdge,
    explanation: str,
    review_questions: list[str],
    blockers: list[str],
) -> ConsistencyViolation:
    kind = ViolationKind.AMBIGUOUS_WORDING
    confidence = min(edge.confidence, 0.6)
    action = _cap_action(action_for_violation(kind, confidence, 0.0), blockers, kind)
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
        action=action,
        explanation=explanation,
        review_questions=review_questions,
        blockers=blockers,
        edge_source=edge.source.value,
        reviewed_by=edge.reviewed_by,
        review_status=_review_status(edge),
        max_action_cap=_cap_limit(blockers, kind).value,
        max_action_cap_reason=_cap_reason(blockers),
    )


def _has_settlement_proof(edge: RelationshipEdge, src: MarketNode, dst: MarketNode) -> bool:
    if edge.settlement_source_proven:
        return True
    return bool(
        src.settlement_source_proven
        and dst.settlement_source_proven
        and src.settlement_source
        and src.settlement_source == dst.settlement_source
    )


def _same_threshold_basis(edge: RelationshipEdge, src: MarketNode, dst: MarketNode) -> bool:
    if not src.observable or not dst.observable:
        return False
    if not src.settlement_source or not dst.settlement_source:
        return False
    if not src.window or not dst.window:
        return False
    if edge.observable and edge.observable != src.observable:
        return False
    if edge.window and edge.window != src.window:
        return False
    return (
        src.observable == dst.observable
        and src.settlement_source == dst.settlement_source
        and src.window == dst.window
    )


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
    src = _node(snapshot, edge.src_market_id)
    dst = _node(snapshot, edge.dst_market_id)
    blockers = _pair_blockers(snapshot, edge, src, dst)
    if "reference_only_node" in blockers:
        return _diagnostic_wording_violation(
            snapshot,
            edge,
            "One or more nodes is reference-only, so no hard probability-bound finding is emitted.",
            [
                "Should this reference-only source remain observability-only?",
                "Is there a non-reference node pair with reviewed settlement wording?",
            ],
            blockers,
        )
    confidence = _edge_confidence(edge)
    action = _cap_action(action_for_violation(kind, confidence, magnitude), blockers, kind)
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
        action=action,
        explanation=explanation,
        review_questions=review_questions,
        blockers=blockers,
        edge_source=edge.source.value,
        reviewed_by=edge.reviewed_by,
        review_status=_review_status(edge),
        max_action_cap=_cap_limit(blockers, kind).value,
        max_action_cap_reason=_cap_reason(blockers),
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

    blockers = _pair_blockers(snapshot, edge, narrower, broader)
    if not _same_threshold_basis(edge, narrower, broader):
        return _diagnostic_wording_violation(
            snapshot,
            edge,
            "Subset/superset edge lacks same observable, settlement source, or window proof.",
            [
                "Do both threshold markets use the same observable?",
                "Do both resolve from the same source and date window?",
            ],
            blockers + ["threshold_basis_mismatch"],
        )

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
    blockers = _pair_blockers(snapshot, edge, src, dst)
    if not _has_settlement_proof(edge, src, dst):
        return _diagnostic_wording_violation(
            snapshot,
            edge,
            "Same-event reworded edge lacks settlement-source proof and is downgraded to wording review.",
            [
                "What source proves both markets resolve identically?",
                "Do the contracts use the same settlement source and date window?",
            ],
            blockers + ["settlement_source_not_proven"],
        )
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
    src = _node(snapshot, edge.src_market_id)
    dst = _node(snapshot, edge.dst_market_id)
    blockers = _pair_blockers(snapshot, edge, src, dst)
    confidence = edge.confidence
    kind = ViolationKind.AMBIGUOUS_WORDING
    action = _cap_action(action_for_violation(kind, confidence, 0.0), blockers, kind)
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
        action=action,
        explanation="The relationship is intentionally marked ambiguous and needs human wording review before any hard constraint is used.",
        review_questions=[
            "What exact relationship, if any, should be promoted from this ambiguous edge?",
            "Which resolution words create the ambiguity?",
            "Should this remain documentation-only until more snapshots are available?",
        ],
        blockers=blockers,
        edge_source=edge.source.value,
        reviewed_by=edge.reviewed_by,
        review_status=_review_status(edge),
        max_action_cap=_cap_limit(blockers, kind).value,
        max_action_cap_reason=_cap_reason(blockers),
    )


def check_exclusion_set(snapshot: GraphSnapshot, exclusion: ExclusionSet) -> ConsistencyViolation | None:
    nodes = [_node(snapshot, market_id) for market_id in exclusion.member_market_ids]
    if any(node.reference_only for node in nodes):
        return None
    raw_sum = sum(node.probability for node in nodes)
    tolerance = exclusion.tolerance if exclusion.tolerance is not None else DEFAULT_EXCLUSION_TOLERANCE
    raw_gap = raw_sum - 1.0
    adjusted_gap = raw_gap - tolerance - spread_buffer(*(node.spread for node in nodes))
    if adjusted_gap <= 1e-12:
        return None

    base_confidence = 0.95 if exclusion.completeness == ExclusionCompleteness.PARTITION else 0.75
    confidence = combine_confidences(base_confidence, DEFAULT_MARKET_SIGNAL_CONFIDENCE)
    kind = ViolationKind.SUM_OVER_ONE
    blockers = ["stale_input"] if any(_is_stale(snapshot, node) for node in nodes) else []
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
        blockers=blockers,
        edge_source="manual",
        review_status="manual_unreviewed",
        max_action_cap=_cap_limit(blockers, kind).value,
        max_action_cap_reason=_cap_reason(blockers),
    )
