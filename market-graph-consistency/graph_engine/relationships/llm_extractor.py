from __future__ import annotations

from dataclasses import dataclass, field

from graph_engine.models import MarketNode, RelationshipEdge


@dataclass
class DeterministicLLMExtractor:
    """Offline interface stub for future relationship extraction.

    This class intentionally has no model client, environment variables, network access,
    or price/probability handling. Tests can inject fixture suggestions directly.
    """

    fixture_suggestions: list[RelationshipEdge] = field(default_factory=list)

    def suggest_relationships(self, markets: list[MarketNode]) -> list[RelationshipEdge]:
        known_ids = {market.market_id for market in markets}
        return [
            suggestion
            for suggestion in sorted(self.fixture_suggestions, key=lambda edge: edge.edge_id)
            if suggestion.src_market_id in known_ids and suggestion.dst_market_id in known_ids
        ]


LLMRelationshipExtractor = DeterministicLLMExtractor

