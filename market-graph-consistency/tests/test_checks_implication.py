from __future__ import annotations

import pytest

from graph_engine.consistency.checks import check_implication
from graph_engine.models import GraphSnapshot, RelationshipEdge, RelationshipType, ViolationKind
from tests.conftest import make_node


def _edge(confidence: float = 0.9) -> RelationshipEdge:
    return RelationshipEdge(
        edge_id="edge_a_implies_b",
        src_market_id="test:a",
        dst_market_id="test:b",
        relation=RelationshipType.IMPLICATION,
        confidence=confidence,
        source="manual",
        rationale="a implies b",
        evidence=["test"],
        created_at="2026-05-19T18:00:00+00:00",
    )


def test_implication_violation_triggers_when_source_exceeds_destination_plus_tolerance() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:a": make_node("test:a", 0.60),
            "test:b": make_node("test:b", 0.50),
        },
    )

    violation = check_implication(snapshot, _edge())

    assert violation is not None
    assert violation.kind == ViolationKind.IMPLICATION_VIOLATION
    assert violation.raw_gap == pytest.approx(0.10)


def test_implication_does_not_trigger_at_or_below_tolerance() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:a": make_node("test:a", 0.53),
            "test:b": make_node("test:b", 0.50),
        },
    )

    assert check_implication(snapshot, _edge()) is None


def test_low_confidence_implication_does_not_trigger_strong_action() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:a": make_node("test:a", 0.70),
            "test:b": make_node("test:b", 0.40),
        },
    )

    violation = check_implication(snapshot, _edge(confidence=0.1))

    assert violation is not None
    assert violation.action.value == "IGNORE"
    assert violation.confidence < 0.25
