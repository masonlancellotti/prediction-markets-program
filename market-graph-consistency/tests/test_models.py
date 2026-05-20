from __future__ import annotations

import pytest

from graph_engine.models import GraphSnapshot, MarketNode
from tests.conftest import make_node


def test_model_construction_and_snapshot_round_trip() -> None:
    node = make_node("test:market_a", 0.41)
    snapshot = GraphSnapshot(
        snapshot_id="snapshot-a",
        as_of=node.as_of,
        nodes={node.market_id: node},
        notes=["round trip"],
    )

    restored = GraphSnapshot.from_dict(snapshot.to_dict())

    assert restored.snapshot_id == snapshot.snapshot_id
    assert restored.nodes["test:market_a"].yes_price == 0.41
    assert restored.nodes["test:market_a"].as_of.tzinfo is not None


def test_probabilities_must_be_in_unit_interval() -> None:
    payload = make_node("test:market_b", 0.5).to_dict()
    payload["yes_price"] = 1.2

    with pytest.raises(ValueError, match="yes_price"):
        MarketNode.from_dict(payload)


def test_market_id_requires_venue_prefix() -> None:
    payload = make_node("test:market_c", 0.5).to_dict()
    payload["market_id"] = "missing_prefix"

    with pytest.raises(ValueError, match="market_id"):
        MarketNode.from_dict(payload)

