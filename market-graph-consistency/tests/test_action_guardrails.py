from __future__ import annotations

import pytest

from graph_engine.consistency.checks import check_ambiguous_wording, check_implication
from graph_engine.consistency.tolerances import action_for_violation
from graph_engine.models import Action, GraphSnapshot, RelationshipEdge, RelationshipType, ViolationKind
from graph_engine.relationships.confidence import combine_confidences
from graph_engine.relationships.llm_extractor import DeterministicLLMExtractor
from tests.conftest import make_node


def test_action_enum_tops_out_at_manual_review() -> None:
    assert [action.value for action in Action] == ["IGNORE", "WATCH", "MANUAL_REVIEW"]


def test_action_ladder_maps_confidence_and_magnitude() -> None:
    assert action_for_violation(ViolationKind.IMPLICATION_VIOLATION, 0.1, 0.3) == Action.IGNORE
    assert action_for_violation(ViolationKind.IMPLICATION_VIOLATION, 0.5, 0.02) == Action.WATCH
    assert action_for_violation(ViolationKind.IMPLICATION_VIOLATION, 0.8, 0.04) == Action.MANUAL_REVIEW


def test_confidence_multiplication_low_edge_confidence_produces_low_violation_confidence() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:a": make_node("test:a", 0.8),
            "test:b": make_node("test:b", 0.2),
        },
    )
    edge = RelationshipEdge(
        edge_id="edge_low",
        src_market_id="test:a",
        dst_market_id="test:b",
        relation=RelationshipType.IMPLICATION,
        confidence=0.2,
        source="manual",
        rationale="low confidence",
        evidence=[],
        created_at="2026-05-19T18:00:00+00:00",
    )

    violation = check_implication(snapshot, edge)

    assert violation is not None
    assert violation.confidence == combine_confidences(0.2, 0.95)
    assert violation.action == Action.IGNORE


def test_ambiguous_llm_relationship_stays_manual_review_or_lower() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:a": make_node("test:a", 0.4),
            "test:b": make_node("test:b", 0.6),
        },
    )
    edge = RelationshipEdge(
        edge_id="edge_ambiguous",
        src_market_id="test:a",
        dst_market_id="test:b",
        relation=RelationshipType.AMBIGUOUS,
        confidence=0.95,
        source="llm",
        rationale="ambiguous wording",
        evidence=[],
        created_at="2026-05-19T18:00:00+00:00",
        reviewed_by=None,
    )

    violation = check_ambiguous_wording(snapshot, edge)

    assert violation is not None
    assert violation.kind == ViolationKind.AMBIGUOUS_WORDING
    assert violation.action == Action.WATCH
    assert violation.confidence <= 0.6
    assert violation.raw_gap == 0.0


@pytest.mark.parametrize("confidence", [0.30, 0.65, 0.99])
def test_ambiguous_wording_never_exceeds_watch(confidence: float) -> None:
    assert action_for_violation(ViolationKind.AMBIGUOUS_WORDING, confidence, 0.0) == Action.WATCH


def test_llm_edge_unreviewed_clamps_to_0_6() -> None:
    edge = RelationshipEdge(
        edge_id="edge_unreviewed_llm",
        src_market_id="test:a",
        dst_market_id="test:b",
        relation=RelationshipType.AMBIGUOUS,
        confidence=0.95,
        source="llm",
        rationale="unreviewed llm edge",
        evidence=[],
        created_at="2026-05-19T18:00:00+00:00",
        reviewed_by=None,
    )

    assert edge.confidence == 0.6


def test_llm_edge_reviewed_keeps_full_confidence() -> None:
    edge = RelationshipEdge(
        edge_id="edge_reviewed_llm",
        src_market_id="test:a",
        dst_market_id="test:b",
        relation=RelationshipType.AMBIGUOUS,
        confidence=0.95,
        source="llm",
        rationale="reviewed llm edge",
        evidence=[],
        created_at="2026-05-19T18:00:00+00:00",
        reviewed_by="someone",
    )

    assert edge.confidence == 0.95


def test_fake_llm_extractor_is_deterministic_and_has_no_network_dependency(monkeypatch) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("network access should not be attempted")

    monkeypatch.setattr("socket.socket", fail_network)
    edge = RelationshipEdge(
        edge_id="edge_fixture",
        src_market_id="test:a",
        dst_market_id="test:b",
        relation=RelationshipType.AMBIGUOUS,
        confidence=0.5,
        source="llm",
        rationale="fixture suggestion",
        evidence=[],
        created_at="2026-05-19T18:00:00+00:00",
    )
    markets = [make_node("test:a", 0.4), make_node("test:b", 0.6)]
    extractor = DeterministicLLMExtractor([edge])

    assert extractor.suggest_relationships(markets) == [edge]
    assert extractor.suggest_relationships(list(reversed(markets))) == [edge]
