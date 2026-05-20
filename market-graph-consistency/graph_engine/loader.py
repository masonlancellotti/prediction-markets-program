from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from graph_engine.models import GraphSnapshot, MarketNode, parse_datetime


@dataclass(frozen=True)
class FixtureMetadata:
    file: str
    source_snapshot_id: str
    as_of: str
    market_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "source_snapshot_id": self.source_snapshot_id,
            "as_of": self.as_of,
            "market_count": self.market_count,
        }


def _read_fixture_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_fixture_markets(fixtures_dir: Path | str) -> tuple[GraphSnapshot, list[dict[str, Any]]]:
    root = Path(fixtures_dir)
    if not root.exists():
        raise FileNotFoundError(f"Fixture directory does not exist: {root}")

    nodes: dict[str, MarketNode] = {}
    metadata: list[FixtureMetadata] = []
    snapshot_times: list[datetime] = []

    for path in sorted(root.glob("*.json")):
        payload = _read_fixture_file(path)
        source_snapshot_id = payload.get("source_snapshot_id", path.stem)
        as_of = parse_datetime(payload["as_of"], f"{path.name}.as_of")
        snapshot_times.append(as_of)
        market_payloads = payload.get("markets", [])
        metadata.append(
            FixtureMetadata(
                file=path.name,
                source_snapshot_id=source_snapshot_id,
                as_of=as_of.isoformat(),
                market_count=len(market_payloads),
            )
        )

        for market_payload in market_payloads:
            raw = dict(market_payload)
            market_payload = dict(market_payload)
            market_payload.setdefault("as_of", as_of)
            market_payload.setdefault("source_snapshot_id", source_snapshot_id)
            market_payload.setdefault("raw", raw)
            node = MarketNode.from_dict(market_payload)
            if node.market_id in nodes:
                raise ValueError(f"Duplicate market_id in fixtures: {node.market_id}")
            nodes[node.market_id] = node

    if not nodes:
        raise ValueError(f"No fixture markets found in {root}")

    snapshot_as_of = max(snapshot_times)
    snapshot = GraphSnapshot(
        snapshot_id=f"fixture-snapshot-{snapshot_as_of.strftime('%Y%m%dT%H%M%SZ')}",
        as_of=snapshot_as_of,
        nodes=nodes,
        notes=["Offline fixture snapshot only; no live venue calls were made."],
    )
    return snapshot, [item.to_dict() for item in metadata]

