from __future__ import annotations

from graph_engine.consistency.checks import check_subset
from graph_engine.models import GraphSnapshot, RelationshipEdge, RelationshipType, ViolationKind
from tests.conftest import make_node


def test_subset_over_superset_violation() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:narrow": make_node(
                "test:narrow",
                0.65,
                observable="test_observable",
                settlement_source="test_source",
                window="test_window",
            ),
            "test:broad": make_node(
                "test:broad",
                0.50,
                observable="test_observable",
                settlement_source="test_source",
                window="test_window",
            ),
        },
    )
    edge = RelationshipEdge(
        edge_id="edge_subset",
        src_market_id="test:narrow",
        dst_market_id="test:broad",
        relation=RelationshipType.SUBSET,
        confidence=0.9,
        source="manual",
        rationale="narrower event is contained in broader event",
        evidence=["test"],
        created_at="2026-05-19T18:00:00+00:00",
    )

    violation = check_subset(snapshot, edge)

    assert violation is not None
    assert violation.kind == ViolationKind.SUBSET_OVER_SUPERSET
    assert violation.action.value == "MANUAL_REVIEW"
