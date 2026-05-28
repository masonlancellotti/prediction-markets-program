from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Sequence

from relative_value.models import NormalizedMarket, SourceKind
from relative_value.normalize import parse_datetime


class ReadOnlyVenueAdapter(ABC):
    name: str

    @abstractmethod
    def load_markets(self) -> Sequence[NormalizedMarket]:
        raise NotImplementedError


class JsonExchangeFixtureAdapter(ReadOnlyVenueAdapter):
    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.path = path

    def load_markets(self) -> Sequence[NormalizedMarket]:
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        return [self._market_from_row(row) for row in rows]

    def _market_from_row(self, row: dict[str, Any]) -> NormalizedMarket:
        return NormalizedMarket(
            venue=self.name,
            market_id=str(row["market_id"]),
            event_name=str(row["event_name"]),
            outcome_name=str(row["outcome_name"]),
            source_kind=SourceKind.EXCHANGE,
            yes_bid=row.get("yes_bid"),
            yes_ask=row.get("yes_ask"),
            liquidity_top_contracts=float(row.get("liquidity_top_contracts", 0.0)),
            volume_24h=float(row.get("volume_24h", 0.0)),
            settlement_time=parse_datetime(row.get("settlement_time")),
            captured_at=parse_datetime(row.get("captured_at")),
            settlement_rule=str(row.get("settlement_rule", "")),
            is_executable=bool(row.get("is_executable", False)),
            source_platform=row.get("source_platform"),
            access_platform=row.get("access_platform"),
            exchange_venue=row.get("exchange_venue"),
            executable_venue=row.get("executable_venue"),
            raw=row,
        )
