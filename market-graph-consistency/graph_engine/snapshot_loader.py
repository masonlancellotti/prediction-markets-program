from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from graph_engine.models import GraphSnapshot, MarketNode, parse_datetime


class SnapshotLoadError(ValueError):
    """Raised when explicit saved snapshot inputs are malformed."""


class NoUsableSnapshotsFound(ValueError):
    """Raised when a directory/path set contains no schema-v1 snapshots."""


@dataclass(frozen=True)
class SavedSnapshotMetadata:
    file: str
    source_snapshot_id: str
    schema_version: int
    source: str | None
    venue: str | None
    as_of: str
    market_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "source_snapshot_id": self.source_snapshot_id,
            "schema_version": self.schema_version,
            "source": self.source,
            "venue": self.venue,
            "as_of": self.as_of,
            "market_count": self.market_count,
        }


def _coerce_probability(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _coerce_nonnegative(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _first_present(payload: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return value
    return None


def _slug(value: str) -> str:
    lowered = value.lower().strip()
    slugged = re.sub(r"[^a-z0-9_.-]+", "_", lowered)
    return slugged.strip("_") or "unknown"


def _market_id(row: dict[str, Any], venue: str, index: int) -> str:
    existing = _first_present(row, ["market_id", "id", "ticker"])
    if existing is not None:
        existing_text = str(existing)
        if ":" in existing_text:
            return existing_text
        return f"{venue}:{_slug(existing_text)}"

    native_id = _first_present(row, ["native_id", "slug", "question_id"])
    if native_id is not None:
        return f"{venue}:{_slug(str(native_id))}"
    return f"{venue}:row_{index}"


def _snapshot_as_of(payload: dict[str, Any], path: Path) -> datetime:
    raw_as_of = _first_present(
        payload,
        ["as_of", "generated_at", "snapshot_time", "captured_at", "created_at"],
    )
    if raw_as_of is None:
        raise SnapshotLoadError(f"{path.name}: missing snapshot timestamp")
    return parse_datetime(raw_as_of, f"{path.name}.as_of")


def _row_as_of(row: dict[str, Any], snapshot_as_of: datetime, path: Path, index: int) -> datetime:
    raw_as_of = _first_present(row, ["as_of", "updated_at", "last_updated"])
    if raw_as_of is None:
        return snapshot_as_of
    return parse_datetime(raw_as_of, f"{path.name}.normalized_markets[{index}].as_of")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise SnapshotLoadError(f"{path.name}: invalid JSON") from exc
    if not isinstance(payload, dict):
        raise SnapshotLoadError(f"{path.name}: snapshot JSON must be an object")
    return payload


def _convert_row(
    row: dict[str, Any],
    *,
    path: Path,
    index: int,
    source_snapshot_id: str,
    snapshot_as_of: datetime,
    top_source: str | None,
    top_venue: str | None,
) -> MarketNode:
    venue = _first_present(row, ["venue", "source"]) or top_venue or top_source
    if venue is None:
        raise SnapshotLoadError(f"{path.name}: missing source/venue for normalized_markets[{index}]")
    venue = str(venue)

    title = str(_first_present(row, ["title", "question", "market_title", "name"]) or f"Snapshot row {index}")
    yes_price = _coerce_probability(
        _first_present(row, ["yes_price", "probability", "yes_probability", "mid", "last_price"])
    )
    no_price = _coerce_probability(_first_present(row, ["no_price", "no_probability"]))
    if no_price is None and yes_price is not None:
        no_price = 1.0 - yes_price

    return MarketNode(
        market_id=_market_id(row, venue, index),
        venue=venue,
        title=title,
        canonical_text=str(_first_present(row, ["canonical_text", "normalized_title", "question"]) or title),
        resolution_criteria=str(_first_present(row, ["resolution_criteria", "description", "rules"]) or ""),
        resolution_date=str(_first_present(row, ["resolution_date", "close_time", "end_date"]) or "unknown"),
        entities=_string_list(row.get("entities")),
        themes=_string_list(_first_present(row, ["themes", "tags", "categories"])),
        yes_price=yes_price,
        no_price=no_price,
        bid=_coerce_probability(_first_present(row, ["bid", "best_bid", "yes_bid"])),
        ask=_coerce_probability(_first_present(row, ["ask", "best_ask", "yes_ask"])),
        volume_24h=_coerce_nonnegative(_first_present(row, ["volume_24h", "volume24h", "volume"])),
        liquidity_score=_coerce_probability(row.get("liquidity_score")),
        as_of=_row_as_of(row, snapshot_as_of, path, index),
        raw={"source_snapshot_file": path.name, "normalized_row": row},
        source_snapshot_id=source_snapshot_id,
    )


def _load_one_snapshot(path: Path) -> tuple[list[MarketNode], SavedSnapshotMetadata]:
    payload = _load_json(path)
    if payload.get("schema_version") != 1:
        raise SnapshotLoadError(f"{path.name}: schema_version must be 1")

    normalized_markets = payload.get("normalized_markets")
    if not isinstance(normalized_markets, list):
        raise SnapshotLoadError(f"{path.name}: normalized_markets must be a list")

    top_source = _first_present(payload, ["source", "snapshot_source"])
    top_venue = _first_present(payload, ["venue", "market_venue"])
    snapshot_as_of = _snapshot_as_of(payload, path)
    source_snapshot_id = str(_first_present(payload, ["snapshot_id", "source_snapshot_id"]) or path.stem)

    nodes: list[MarketNode] = []
    for index, row in enumerate(normalized_markets):
        if not isinstance(row, dict):
            continue
        try:
            nodes.append(
                _convert_row(
                    row,
                    path=path,
                    index=index,
                    source_snapshot_id=source_snapshot_id,
                    snapshot_as_of=snapshot_as_of,
                    top_source=str(top_source) if top_source is not None else None,
                    top_venue=str(top_venue) if top_venue is not None else None,
                )
            )
        except (TypeError, ValueError) as exc:
            raise SnapshotLoadError(f"{path.name}: invalid normalized_markets[{index}]: {exc}") from exc

    metadata = SavedSnapshotMetadata(
        file=path.name,
        source_snapshot_id=source_snapshot_id,
        schema_version=1,
        source=str(top_source) if top_source is not None else None,
        venue=str(top_venue) if top_venue is not None else None,
        as_of=snapshot_as_of.isoformat(),
        market_count=len(nodes),
    )
    return nodes, metadata


def _candidate_paths(
    snapshots_dir: Path | str | None,
    snapshot_paths: Iterable[Path | str] | None,
) -> tuple[list[Path], bool]:
    paths: list[Path] = []
    strict = False

    if snapshots_dir is not None:
        root = Path(snapshots_dir)
        if root.exists() and root.is_dir():
            paths.extend(sorted(root.glob("*.json")))

    if snapshot_paths:
        strict = True
        paths.extend(Path(path) for path in snapshot_paths)

    unique: dict[str, Path] = {}
    for path in paths:
        unique[str(path.resolve()) if path.exists() else str(path)] = path
    return list(unique.values()), strict


def load_schema_v1_snapshots(
    *,
    snapshots_dir: Path | str | None = None,
    snapshot_paths: Iterable[Path | str] | None = None,
) -> tuple[GraphSnapshot, list[dict[str, Any]]]:
    paths, strict = _candidate_paths(snapshots_dir, snapshot_paths)
    if not paths:
        raise NoUsableSnapshotsFound("no candidate snapshot files found")

    nodes: dict[str, MarketNode] = {}
    metadata: list[SavedSnapshotMetadata] = []
    snapshot_times: list[datetime] = []
    errors: list[str] = []

    for path in paths:
        if not path.exists():
            message = f"{path}: file does not exist"
            if strict:
                raise SnapshotLoadError(message)
            errors.append(message)
            continue
        try:
            loaded_nodes, loaded_metadata = _load_one_snapshot(path)
        except SnapshotLoadError as exc:
            if strict:
                raise
            errors.append(str(exc))
            continue

        metadata.append(loaded_metadata)
        snapshot_times.append(parse_datetime(loaded_metadata.as_of, f"{path.name}.as_of"))
        for node in loaded_nodes:
            existing = nodes.get(node.market_id)
            if existing is None or node.as_of >= existing.as_of:
                nodes[node.market_id] = node

    if not nodes:
        detail = "; ".join(errors[:3])
        raise NoUsableSnapshotsFound(detail or "no usable schema-v1 snapshots found")

    snapshot_as_of = max(snapshot_times)
    snapshot = GraphSnapshot(
        snapshot_id=f"saved-schema-v1-snapshot-{snapshot_as_of.strftime('%Y%m%dT%H%M%SZ')}",
        as_of=snapshot_as_of,
        nodes=nodes,
        notes=[
            "Read-only schema-v1 saved snapshot prototype.",
            "No live ingestion or relationship extraction was performed.",
        ],
    )
    return snapshot, [item.to_dict() for item in metadata]
