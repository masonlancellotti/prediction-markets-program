from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha1
from pathlib import Path
from typing import Any, Iterable

from graph_engine.models import GraphSnapshot, MarketNode, parse_datetime
from graph_engine.reporting.safety import contains_prohibited_report_token


class SnapshotLoadError(ValueError):
    """Raised when explicit saved snapshot inputs are malformed."""


class NoUsableSnapshotsFound(ValueError):
    """Raised when a directory/path set contains no schema-v1 snapshots."""


RV_SAVED_FILE_ONLY_BLOCKER = "rv_saved_file_only"
MISSING_BID_BLOCKER = "missing_bid"
MISSING_ASK_BLOCKER = "missing_ask"
MISSING_QUOTE_TIMESTAMP_BLOCKER = "missing_quote_timestamp"
REFERENCE_ONLY_SOURCE_BLOCKER = "reference_only_source"
DIAGNOSTIC_GRAPH_HANDOFF_BLOCKER = "diagnostic_only_graph_handoff"
SETTLEMENT_NOT_VERIFIED_BLOCKER = "settlement_not_verified_by_graph"
NOT_EVALUATOR_INPUT_BLOCKER = "not_evaluator_input"
MIDPOINT_ONLY_SAVED_ROW_BLOCKER = "midpoint_only_saved_row"
RV_SOURCE_BLOCKERS = (
    RV_SAVED_FILE_ONLY_BLOCKER,
    DIAGNOSTIC_GRAPH_HANDOFF_BLOCKER,
    SETTLEMENT_NOT_VERIFIED_BLOCKER,
    NOT_EVALUATOR_INPUT_BLOCKER,
)
REFERENCE_ONLY_VENUES = {"the_odds_api", "odds_api"}
RV_ROW_LIST_KEYS = (
    "normalized_markets",
    "quote_diagnostics",
    "quote_rows",
    "quotes",
    "rows",
    "markets",
    "market_rows",
    "snapshots",
    "results",
    "candidates",
)
NESTED_ROW_CONTAINERS = ("quote", "best_quote", "market", "contract", "metadata")


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


@dataclass(frozen=True)
class RealQuoteFixtureOverlayMetadata:
    directory: str
    files_read: list[str]
    files_scanned: list[str]
    files_imported: list[str]
    schema_version: int
    snapshot_id: str
    as_of: str
    markets_read: int
    markets_imported: int
    quote_rows_imported: int
    saved_quote_freshness_buckets: dict[str, int]
    markets_overlayed: int
    markets_added: int
    skipped_market_count: int
    applied_market_ids: list[str]
    blockers: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "directory": self.directory,
            "files_read": list(self.files_read),
            "files_scanned": list(self.files_scanned),
            "files_imported": list(self.files_imported),
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "as_of": self.as_of,
            "markets_read": self.markets_read,
            "markets_imported": self.markets_imported,
            "quote_rows_imported": self.quote_rows_imported,
            "saved_quote_freshness_buckets": dict(self.saved_quote_freshness_buckets),
            "markets_overlayed": self.markets_overlayed,
            "markets_added": self.markets_added,
            "skipped_market_count": self.skipped_market_count,
            "applied_market_ids": list(self.applied_market_ids),
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class SavedQuoteRow:
    node: MarketNode
    source_file: str
    source_kind: str
    blockers: list[str]
    quote_row_imported: bool


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


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "y"}:
            return True
        if normalized in {"0", "false", "no", "off", "n"}:
            return False
        return False
    return bool(value)


def payload_flag(value: str | None, flag: str) -> bool:
    return str(value or "").strip().upper() == flag


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
        reference_only=(
            _bool_value(_first_present(row, ["reference_only", "is_reference_only"]))
            or payload_flag(_first_present(row, ["source_type", "permission"]), "REFERENCE_ONLY")
            or payload_flag(top_source, "REFERENCE_ONLY")
        ),
        settlement_source=_first_present(row, ["settlement_source", "resolution_source"]),
        settlement_source_proven=_bool_value(_first_present(row, ["settlement_source_proven", "resolution_source_proven"])),
        observable=_first_present(row, ["observable", "underlying", "asset"]),
        window=_first_present(row, ["window", "settlement_window", "date_window"]),
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


def _load_saved_quote_rows(root: Path | str, fallback_as_of: datetime) -> tuple[list[SavedQuoteRow], dict[str, Any]]:
    directory = Path(root)
    if not directory.exists() or not directory.is_dir():
        raise FileNotFoundError(f"Saved quote fixture directory does not exist: {directory}")

    rows: list[SavedQuoteRow] = []
    files_scanned: list[str] = []
    files_imported: set[str] = set()
    blockers: list[str] = []
    candidate_rows = 0
    quote_rows_imported = 0
    paths = sorted(directory.rglob("*.json"))
    for path in paths:
        file_label = _safe_file_label(path, directory)
        files_scanned.append(file_label)
        try:
            payload = _load_json(path)
        except SnapshotLoadError:
            blockers.append(f"invalid_saved_quote_json:{file_label}")
            continue

        if payload.get("schema_version") == 1 and isinstance(payload.get("normalized_markets"), list):
            try:
                loaded_nodes, _ = _load_one_snapshot(path)
            except SnapshotLoadError:
                blockers.append(f"invalid_schema_v1_quote_file:{file_label}")
                continue
            candidate_rows += len(loaded_nodes)
            if loaded_nodes:
                files_imported.add(file_label)
            for node in loaded_nodes:
                quote_imported = _has_bid_ask_and_timestamp(node)
                quote_rows_imported += int(quote_imported)
                rows.append(
                    SavedQuoteRow(
                        node=_stamp_schema_v1_quote_node(node, file_label),
                        source_file=file_label,
                        source_kind="schema_v1_normalized_snapshot",
                        blockers=[],
                        quote_row_imported=quote_imported,
                    )
                )
            continue

        file_rows = _rv_saved_quote_rows(payload)
        candidate_rows += len(file_rows)
        imported_from_file = 0
        top_as_of = _optional_datetime(
            _first_present(payload, ["as_of", "generated_at", "snapshot_time", "captured_at", "created_at"])
        )
        for index, row in enumerate(file_rows):
            converted = _convert_rv_saved_quote_row(
                row,
                path=path,
                file_label=file_label,
                index=index,
                top_payload=payload,
                snapshot_as_of=top_as_of or fallback_as_of,
            )
            if converted is None:
                blockers.append(f"unsupported_saved_quote_row:{file_label}:{index}")
                continue
            imported_from_file += 1
            quote_rows_imported += int(converted.quote_row_imported)
            rows.append(converted)
        if imported_from_file:
            files_imported.add(file_label)

    metadata = {
        "directory": str(directory),
        "files_read": sorted(files_imported),
        "files_scanned": files_scanned,
        "files_imported": sorted(files_imported),
        "markets_read": candidate_rows,
        "markets_imported": len(rows),
        "quote_rows_imported": quote_rows_imported,
        "blockers": sorted(set(blockers)),
    }
    return rows, metadata


def _stamp_schema_v1_quote_node(node: MarketNode, file_label: str) -> MarketNode:
    raw = dict(node.raw)
    raw.setdefault("saved_quote_source_type", "schema_v1_normalized_snapshot")
    raw.setdefault("source_snapshot_file", file_label)
    return replace(node, raw=raw)


def _rv_saved_quote_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if _looks_like_rv_saved_quote_row(row)]
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    if _looks_like_rv_saved_quote_row(payload):
        rows.append(payload)
    for key in RV_ROW_LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(row for row in value if _looks_like_rv_saved_quote_row(row))
    return rows


def _looks_like_rv_saved_quote_row(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    has_market_id = _first_present_deep(value, ["market_id", "id", "conid", "ticker", "native_id"]) is not None
    has_quote_field = _first_present_deep(
        value,
        [
            "bid",
            "best_bid",
            "yes_bid",
            "ask",
            "best_ask",
            "yes_ask",
            "bid_size",
            "ask_size",
            "quote_timestamp",
            "observed_at",
            "midpoint",
            "mid",
            "chance",
            "probability",
            "ui_probability",
            "market_family",
            "family",
            "market_type",
            "blockers",
        ],
    ) is not None
    return has_market_id and has_quote_field


def _convert_rv_saved_quote_row(
    row: dict[str, Any],
    *,
    path: Path,
    file_label: str,
    index: int,
    top_payload: dict[str, Any],
    snapshot_as_of: datetime,
) -> SavedQuoteRow | None:
    raw_venue = _first_present_deep(
        row,
        ["venue", "source_platform", "access_platform", "exchange_venue", "source", "platform", "market_venue"],
    ) or _first_present(top_payload, ["venue", "source_platform", "access_platform", "exchange_venue", "source", "platform"])
    if raw_venue is None:
        return None
    venue = _slug(str(raw_venue))
    market_id = _rv_market_id(row, venue, index)
    title = str(_first_present_deep(row, ["title", "question", "market_title", "name"]) or market_id)
    bid = _coerce_probability(_first_present_deep(row, ["bid", "best_bid", "yes_bid"]))
    ask = _coerce_probability(_first_present_deep(row, ["ask", "best_ask", "yes_ask"]))
    timestamp_value = _first_present_deep(row, ["quote_timestamp", "observed_at", "as_of", "updated_at", "last_updated"])
    row_as_of = _optional_datetime(timestamp_value) if timestamp_value is not None else None

    blockers = set(RV_SOURCE_BLOCKERS)
    row_blockers = set(_string_list(_first_present_deep(row, ["blockers", "review_blockers"])))
    blockers.update(row_blockers)
    if bid is None:
        blockers.add(MISSING_BID_BLOCKER)
    if ask is None:
        blockers.add(MISSING_ASK_BLOCKER)
    if row_as_of is None:
        blockers.add(MISSING_QUOTE_TIMESTAMP_BLOCKER)
    reference_only = _is_reference_only_rv_row(row, venue)
    if reference_only:
        blockers.add(REFERENCE_ONLY_SOURCE_BLOCKER)
    midpoint_only = (
        _first_present_deep(row, ["midpoint", "mid", "chance", "probability", "ui_probability", "last_price"]) is not None
        and (bid is None or ask is None)
    )
    if midpoint_only:
        blockers.add(MIDPOINT_ONLY_SAVED_ROW_BLOCKER)

    raw = {
        "saved_quote_source_type": "rv_saved_report",
        "source_snapshot_file": file_label,
        "source_row_index": index,
        "review_blockers": sorted(blockers),
        "bid_size": _coerce_nonnegative(_first_present_deep(row, ["bid_size", "best_bid_size"])),
        "ask_size": _coerce_nonnegative(_first_present_deep(row, ["ask_size", "best_ask_size"])),
        "exchange_venue": _first_present_deep(row, ["exchange_venue"]),
        "market_family": _first_present_deep(row, ["market_family", "family"]),
        "market_type": _first_present_deep(row, ["market_type", "type"]),
    }
    family = _first_present_deep(row, ["market_family", "family", "event_family", "group_id"])
    if family is not None:
        raw["stale_lag_family"] = str(family)
        raw["family"] = str(family)
    if row_as_of is None:
        raw["quote_timestamp_missing"] = True
    if midpoint_only:
        raw["non_actionable_input"] = True
        raw["diagnostic_midpoint_used"] = True

    node = MarketNode(
        market_id=market_id,
        venue=venue,
        title=title,
        canonical_text=str(_first_present_deep(row, ["canonical_text", "normalized_title", "question"]) or title),
        resolution_criteria=str(_first_present_deep(row, ["resolution_criteria", "description", "rules"]) or ""),
        resolution_date=str(_first_present_deep(row, ["resolution_date", "close_time", "end_date"]) or "unknown"),
        entities=_string_list(_first_present_deep(row, ["entities"])),
        themes=_string_list(_first_present_deep(row, ["themes", "tags", "categories", "market_type", "type"])),
        yes_price=None,
        no_price=None,
        bid=bid,
        ask=ask,
        volume_24h=_coerce_nonnegative(_first_present_deep(row, ["volume_24h", "volume24h", "volume"])),
        liquidity_score=_coerce_probability(_first_present_deep(row, ["liquidity_score"])),
        as_of=row_as_of or snapshot_as_of,
        raw=raw,
        source_snapshot_id=str(_first_present(top_payload, ["snapshot_id", "source_snapshot_id"]) or path.stem),
        reference_only=reference_only,
        settlement_source=None,
        settlement_source_proven=False,
        observable=_first_present_deep(row, ["observable", "underlying", "asset"]),
        window=_first_present_deep(row, ["window", "settlement_window", "date_window"]),
    )
    return SavedQuoteRow(
        node=node,
        source_file=file_label,
        source_kind="rv_saved_report",
        blockers=sorted(blockers),
        quote_row_imported=bid is not None and ask is not None and row_as_of is not None,
    )


def _rv_market_id(row: dict[str, Any], venue: str, index: int) -> str:
    raw_id = _first_present_deep(row, ["market_id", "id", "conid", "ticker", "native_id"])
    if raw_id is None:
        return f"{venue}:rv_saved_row_{index}"
    raw_text = str(raw_id)
    if ":" in raw_text:
        candidate = raw_text
    else:
        candidate = f"{venue}:{_slug(raw_text)}"
    if contains_prohibited_report_token(candidate):
        digest = sha1(candidate.encode("utf-8")).hexdigest()[:12]
        return f"{venue}:rv_saved_market_{digest}"
    return candidate


def _first_present_deep(row: dict[str, Any], keys: Iterable[str]) -> Any:
    value = _first_present(row, keys)
    if value is not None:
        return value
    for container in NESTED_ROW_CONTAINERS:
        nested = row.get(container)
        if isinstance(nested, dict):
            value = _first_present(nested, keys)
            if value is not None:
                return value
    return None


def _optional_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        return parse_datetime(value, "saved_quote_timestamp")
    except (TypeError, ValueError):
        return None


def _is_reference_only_rv_row(row: dict[str, Any], venue: str) -> bool:
    venue_key = _slug(venue)
    return (
        venue_key in REFERENCE_ONLY_VENUES
        or _bool_value(_first_present_deep(row, ["reference_only", "is_reference_only"]))
        or payload_flag(_first_present_deep(row, ["source_type", "permission"]), "REFERENCE_ONLY")
    )


def _has_bid_ask_and_timestamp(node: MarketNode) -> bool:
    return node.bid is not None and node.ask is not None and node.as_of is not None


def _safe_file_label(path: Path, root: Path) -> str:
    try:
        label = path.relative_to(root).as_posix()
    except ValueError:
        label = path.name
    if contains_prohibited_report_token(label):
        digest = sha1(label.encode("utf-8")).hexdigest()[:12]
        return f"saved_quote_file_{digest}.json"
    return label


def apply_real_quote_fixture_overlay(
    snapshot: GraphSnapshot,
    fixtures_dir: Path | str,
) -> tuple[GraphSnapshot, dict[str, Any]]:
    """Overlay or add saved quote rows from schema-v1 and RV report files.

    Existing fixture nodes are only overwritten by rows that clear the narrow
    quote overlay checks. Unmatched saved rows are added as diagnostic graph
    nodes with source-specific blockers so downstream reports can route them
    for WATCH/MANUAL_REVIEW without trusting them as evaluator evidence.
    """

    quote_rows, quote_metadata = _load_saved_quote_rows(fixtures_dir, snapshot.as_of)
    nodes = dict(snapshot.nodes)
    applied_market_ids: list[str] = []
    blockers: list[str] = []
    added_market_ids: list[str] = []
    latest_quote_as_of = snapshot.as_of

    for quote_row in sorted(quote_rows, key=lambda item: item.node.market_id):
        quote_node = quote_row.node
        market_id = quote_node.market_id
        latest_quote_as_of = max(latest_quote_as_of, quote_node.as_of)
        base_node = nodes.get(market_id)
        if base_node is None:
            nodes[market_id] = quote_node
            added_market_ids.append(market_id)
            continue
        row_blockers = _real_quote_overlay_blockers(quote_node)
        if row_blockers:
            blockers.extend(f"{blocker}:{market_id}" for blocker in row_blockers)
            continue
        nodes[market_id] = _overlay_real_quote_fields(base_node, quote_node)
        applied_market_ids.append(market_id)

    if not applied_market_ids and not added_market_ids:
        blockers.append("no_real_quote_fixture_rows_matched_or_added_graph_markets")

    snapshot_as_of = max(snapshot.as_of, latest_quote_as_of)
    overlayed = replace(
        snapshot,
        snapshot_id=f"{snapshot.snapshot_id}+real-quote-fixtures",
        as_of=snapshot_as_of,
        nodes=nodes,
        notes=[
            *snapshot.notes,
            "Read-only saved schema-v1 quote fixture overlay applied; no live calls were made.",
        ],
    )
    metadata = RealQuoteFixtureOverlayMetadata(
        directory=str(Path(fixtures_dir)),
        files_read=list(quote_metadata["files_read"]),
        files_scanned=list(quote_metadata["files_scanned"]),
        files_imported=list(quote_metadata["files_imported"]),
        schema_version=1,
        snapshot_id=overlayed.snapshot_id,
        as_of=snapshot_as_of.isoformat(),
        markets_read=int(quote_metadata["markets_read"]),
        markets_imported=int(quote_metadata["markets_imported"]),
        quote_rows_imported=int(quote_metadata["quote_rows_imported"]),
        saved_quote_freshness_buckets=_saved_quote_freshness_buckets(quote_rows, snapshot_as_of),
        markets_overlayed=len(applied_market_ids),
        markets_added=len(added_market_ids),
        skipped_market_count=max(0, int(quote_metadata["markets_read"]) - len(applied_market_ids) - len(added_market_ids)),
        applied_market_ids=_safe_text_list([*applied_market_ids, *added_market_ids]),
        blockers=sorted(set(blockers) | set(quote_metadata["blockers"])),
    )
    return overlayed, metadata.to_dict()


def _saved_quote_freshness_buckets(rows: list[SavedQuoteRow], snapshot_as_of: datetime) -> dict[str, int]:
    buckets = {"fresh": 0, "maybe_stale": 0, "stale": 0, "missing_timestamp": 0}
    for row in rows:
        if row.node.raw.get("quote_timestamp_missing") is True:
            buckets["missing_timestamp"] += 1
            continue
        age_seconds = max(0, int((snapshot_as_of - row.node.as_of).total_seconds()))
        if age_seconds > 30 * 60:
            buckets["stale"] += 1
        elif age_seconds > 5 * 60:
            buckets["maybe_stale"] += 1
        else:
            buckets["fresh"] += 1
    return buckets


def _real_quote_overlay_blockers(node: MarketNode) -> list[str]:
    blockers: list[str] = []
    if node.bid is None or node.ask is None:
        blockers.append("real_quote_fixture_missing_bid_or_ask")
    elif node.bid > node.ask:
        blockers.append("real_quote_fixture_crossed_bid_ask")
    if node.yes_price is None and node.raw.get("saved_quote_source_type") == "schema_v1_normalized_snapshot":
        blockers.append("real_quote_fixture_missing_observed_yes_price")
    row = node.raw.get("normalized_row")
    if node.raw.get("saved_quote_source_type") == "schema_v1_normalized_snapshot" and not isinstance(row, dict):
        blockers.append("real_quote_fixture_missing_normalized_row")
    return blockers


def _overlay_real_quote_fields(base_node: MarketNode, quote_node: MarketNode) -> MarketNode:
    raw = dict(base_node.raw)
    raw["real_quote_fixture_overlay"] = {
        "source_snapshot_id": quote_node.source_snapshot_id,
        "source_snapshot_file": quote_node.raw.get("source_snapshot_file"),
        "bid": quote_node.bid,
        "ask": quote_node.ask,
        "yes_price": quote_node.yes_price,
        "as_of": quote_node.as_of.isoformat(),
    }
    raw.pop("diagnostic_midpoint_used", None)
    raw.pop("non_actionable_input", None)
    raw.pop("quote_timestamp_missing", None)
    raw.pop("quote_age_seconds", None)
    source_blockers = _string_list(quote_node.raw.get("review_blockers"))
    if source_blockers:
        raw["review_blockers"] = sorted(set(_string_list(raw.get("review_blockers"))) | set(source_blockers))
    family = quote_node.raw.get("stale_lag_family")
    if isinstance(family, str) and family:
        raw["stale_lag_family"] = family
        raw["family"] = family

    yes_price = quote_node.yes_price if quote_node.yes_price is not None else base_node.yes_price
    no_price = quote_node.no_price
    if no_price is None and yes_price is not None:
        no_price = 1.0 - yes_price

    return replace(
        base_node,
        yes_price=yes_price,
        no_price=no_price,
        bid=quote_node.bid,
        ask=quote_node.ask,
        volume_24h=quote_node.volume_24h if quote_node.volume_24h is not None else base_node.volume_24h,
        liquidity_score=(
            quote_node.liquidity_score if quote_node.liquidity_score is not None else base_node.liquidity_score
        ),
        as_of=quote_node.as_of,
        raw=raw,
        source_snapshot_id=quote_node.source_snapshot_id or base_node.source_snapshot_id,
        reference_only=base_node.reference_only or quote_node.reference_only,
        settlement_source=quote_node.settlement_source or base_node.settlement_source,
        settlement_source_proven=base_node.settlement_source_proven or quote_node.settlement_source_proven,
        observable=quote_node.observable or base_node.observable,
        window=quote_node.window or base_node.window,
    )


def _safe_text_list(values: list[str]) -> list[str]:
    safe: list[str] = []
    for value in values:
        if contains_prohibited_report_token(value):
            digest = sha1(value.encode("utf-8")).hexdigest()[:12]
            safe.append(f"saved_quote_value_{digest}")
        else:
            safe.append(value)
    return safe


__all__ = [
    "NoUsableSnapshotsFound",
    "SnapshotLoadError",
    "apply_real_quote_fixture_overlay",
    "load_schema_v1_snapshots",
]
