from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from relative_value.models import NormalizedMarket, SourceKind
from relative_value.normalize import parse_datetime
from relative_value.reference_odds import no_vig_probabilities
from venues.base import ReadOnlyVenueAdapter


class FixtureTheOddsApiAdapter(ReadOnlyVenueAdapter):
    name = "the_odds_api_fixture"

    def __init__(self, path: Path) -> None:
        self.path = path

    def load_markets(self) -> Sequence[NormalizedMarket]:
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        markets: list[NormalizedMarket] = []
        for row in rows:
            sportsbook = str(row.get("sportsbook", "fixture_book"))
            probabilities = no_vig_probabilities(row["american_odds"])
            for outcome_name, probability in probabilities.items():
                markets.append(
                    NormalizedMarket(
                        venue=sportsbook,
                        market_id=f"{sportsbook}:{row['event_id']}:{outcome_name}",
                        event_name=str(row["event_name"]),
                        outcome_name=str(outcome_name),
                        source_kind=SourceKind.SPORTSBOOK_REFERENCE,
                        yes_reference_probability=probability,
                        liquidity_top_contracts=0.0,
                        volume_24h=0.0,
                        settlement_time=parse_datetime(row.get("settlement_time")),
                        captured_at=parse_datetime(row.get("captured_at")),
                        settlement_rule=str(row.get("settlement_rule", "")),
                        is_executable=False,
                        raw=row,
                    )
                )
        return markets
