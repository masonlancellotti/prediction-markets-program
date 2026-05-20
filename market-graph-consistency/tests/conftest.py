from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from graph_engine.loader import load_fixture_markets
from graph_engine.models import MarketNode
from graph_engine.relationships.registry import load_relationship_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_node(market_id: str, yes_price: float, bid: float | None = None, ask: float | None = None) -> MarketNode:
    venue = market_id.split(":", 1)[0]
    return MarketNode(
        market_id=market_id,
        venue=venue,
        title=f"{market_id} title",
        canonical_text=f"{market_id} canonical",
        resolution_criteria="Synthetic test criterion.",
        resolution_date="2027-12-31",
        entities=["Test Entity"],
        themes=["test"],
        yes_price=yes_price,
        no_price=1.0 - yes_price,
        bid=bid,
        ask=ask,
        volume_24h=100.0,
        liquidity_score=0.5,
        as_of=datetime(2026, 5, 19, tzinfo=timezone.utc),
        raw={"fixture": True},
        source_snapshot_id="test-snapshot",
    )


@pytest.fixture
def fixture_snapshot():
    snapshot, _ = load_fixture_markets(PROJECT_ROOT / "venues" / "fixtures")
    registry = load_relationship_registry(PROJECT_ROOT / "relationships", set(snapshot.nodes))
    snapshot.edges = registry.edges
    snapshot.exclusion_sets = registry.exclusion_sets
    return snapshot

