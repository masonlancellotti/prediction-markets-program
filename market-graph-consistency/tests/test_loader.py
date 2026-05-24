from __future__ import annotations

import pytest

from graph_engine.loader import load_fixture_markets
from graph_engine.relationships.registry import load_relationship_registry
from tests.conftest import PROJECT_ROOT


def test_fixture_loader_loads_expected_markets_entities_and_themes() -> None:
    snapshot, metadata = load_fixture_markets(PROJECT_ROOT / "venues" / "fixtures")

    assert len(snapshot.nodes) == 35
    assert len(metadata) == 9
    openai_valuation = snapshot.nodes["polymarket:openai_valuation_1t_2027"]
    assert "OpenAI" in openai_valuation.entities
    assert "private-company-valuation" in openai_valuation.themes
    assert openai_valuation.source_snapshot_id == "fixture-polymarket-ai-2027-001"


def test_relationship_registry_validates_referenced_market_ids(fixture_snapshot) -> None:
    assert len(fixture_snapshot.edges) == 14
    assert len(fixture_snapshot.exclusion_sets) == 4


def test_relationship_registry_rejects_unknown_market_ids(tmp_path, fixture_snapshot) -> None:
    relationship_file = tmp_path / "bad.yaml"
    relationship_file.write_text(
        """
{
  "edges": [
    {
      "edge_id": "bad_edge",
      "src_market_id": "missing:market",
      "dst_market_id": "manifold:agi_by_2027",
      "relation": "IMPLICATION",
      "confidence": 0.9,
      "source": "manual",
      "rationale": "bad fixture",
      "evidence": [],
      "created_at": "2026-05-19T18:00:00+00:00"
    }
  ],
  "exclusion_sets": []
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown market ids"):
        load_relationship_registry(tmp_path, set(fixture_snapshot.nodes))
