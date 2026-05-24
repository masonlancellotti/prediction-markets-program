from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graph_engine.models import ExclusionSet, RelationshipEdge


@dataclass
class RelationshipRegistry:
    edges: list[RelationshipEdge] = field(default_factory=list)
    exclusion_sets: list[ExclusionSet] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)

    def validate(self, known_market_ids: set[str]) -> None:
        edge_ids: set[str] = set()
        exclusion_ids: set[str] = set()

        for edge in self.edges:
            if edge.edge_id in edge_ids:
                raise ValueError(f"Duplicate edge_id: {edge.edge_id}")
            edge_ids.add(edge.edge_id)
            missing = {edge.src_market_id, edge.dst_market_id} - known_market_ids
            if missing:
                raise ValueError(f"Relationship {edge.edge_id} references unknown market ids: {sorted(missing)}")

        for exclusion in self.exclusion_sets:
            if exclusion.set_id in exclusion_ids:
                raise ValueError(f"Duplicate exclusion set_id: {exclusion.set_id}")
            exclusion_ids.add(exclusion.set_id)
            missing = set(exclusion.member_market_ids) - known_market_ids
            if missing:
                raise ValueError(f"Exclusion set {exclusion.set_id} references unknown market ids: {sorted(missing)}")


def _load_yaml_or_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError:
            return json.loads(text)
        loaded = yaml.safe_load(text)
        return loaded or {}
    return json.loads(text)


def load_relationship_registry(
    relationships_dir: Path | str,
    known_market_ids: set[str],
) -> RelationshipRegistry:
    root = Path(relationships_dir)
    if not root.exists():
        raise FileNotFoundError(f"Relationship directory does not exist: {root}")

    registry = RelationshipRegistry()
    paths = sorted(
        [
            *root.glob("*.yaml"),
            *root.glob("*.yml"),
            *root.glob("*.json"),
        ]
    )
    for path in paths:
        payload = _load_yaml_or_json(path)
        registry.source_files.append(path.name)
        registry.edges.extend(
            RelationshipEdge.from_dict(edge_payload)
            for edge_payload in payload.get("edges", [])
        )
        registry.exclusion_sets.extend(
            ExclusionSet.from_dict(exclusion_payload)
            for exclusion_payload in payload.get("exclusion_sets", [])
        )

    registry.validate(known_market_ids)
    return registry

