from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from venues.orderbooks import (
    KalshiOrderbookClient,
    OrderbookClientError,
    PolymarketOrderbookClient,
    parse_kalshi_orderbook_metrics,
    parse_polymarket_orderbook_metrics,
)


SUPPORTED_SCHEMA_VERSION = 1
DEFAULT_MAX_SNAPSHOT_AGE_HOURS = 24.0


def enrich_orderbook_snapshot_file(
    *,
    snapshot_path: Path,
    venue: str,
    output_path: Path | None = None,
    now: datetime | None = None,
    timeout_seconds: float = 10.0,
    max_snapshot_age_hours: float = DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
    kalshi_client: KalshiOrderbookClient | None = None,
    polymarket_client: PolymarketOrderbookClient | None = None,
) -> dict[str, Any]:
    captured_at = now or datetime.now(timezone.utc)
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("now must include timezone information")
    normalized_venue = venue.strip().lower()
    if normalized_venue not in {"kalshi", "polymarket"}:
        raise ValueError(f"unsupported_venue: {venue}")

    snapshot = _load_snapshot(snapshot_path)
    kalshi = kalshi_client or KalshiOrderbookClient(timeout_seconds=timeout_seconds)
    polymarket = polymarket_client or PolymarketOrderbookClient(timeout_seconds=timeout_seconds)
    enriched = enrich_orderbook_snapshot(
        snapshot,
        venue=normalized_venue,
        captured_at=captured_at,
        max_snapshot_age_hours=max_snapshot_age_hours,
        kalshi_client=kalshi,
        polymarket_client=polymarket,
    )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(enriched, indent=2, sort_keys=True), encoding="utf-8")
    return enriched


def enrich_orderbook_snapshot(
    snapshot: dict[str, Any],
    *,
    venue: str,
    captured_at: datetime,
    max_snapshot_age_hours: float = DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
    kalshi_client: KalshiOrderbookClient,
    polymarket_client: PolymarketOrderbookClient,
) -> dict[str, Any]:
    _validate_snapshot(snapshot)
    payload = copy.deepcopy(snapshot)
    stale_reasons = _snapshot_stale_reasons(payload, captured_at, max_snapshot_age_hours)
    markets = payload["normalized_markets"]

    enriched_count = 0
    existing_top_of_book_present_count = 0
    full_orderbook_missing_count = 0
    fetch_failed_count = 0
    stale_existing_top_of_book_count = 0
    for row in markets:
        if not isinstance(row, dict):
            continue
        enrichment = _enrich_market(
            row,
            venue=venue,
            captured_at=captured_at,
            stale_reasons=stale_reasons,
            kalshi_client=kalshi_client,
            polymarket_client=polymarket_client,
        )
        if enrichment.get("enrichment_status") == "enriched":
            enriched_count += 1
        if _top_of_book_present(row):
            existing_top_of_book_present_count += 1
            if enrichment.get("enrichment_status") != "enriched":
                stale_existing_top_of_book_count += 1
        if enrichment.get("enrichment_status") != "enriched":
            full_orderbook_missing_count += 1
        warnings = enrichment.get("enrichment_warnings") if isinstance(enrichment.get("enrichment_warnings"), list) else []
        if "orderbook_unavailable" in warnings or "parse_error" in warnings:
            fetch_failed_count += 1
        row["orderbook_enrichment"] = enrichment

    payload["orderbook_enrichment"] = {
        "schema_version": 1,
        "source": "read_only_orderbook_enrichment",
        "venue": venue,
        "captured_at": captured_at.isoformat(),
        "market_count": len(markets),
        "enriched_count": enriched_count,
        "fresh_orderbook_fetch_enriched_count": enriched_count,
        "unenriched_count": len(markets) - enriched_count,
        "existing_top_of_book_present_count": existing_top_of_book_present_count,
        "full_orderbook_missing_count": full_orderbook_missing_count,
        "fetch_failed_count": fetch_failed_count,
        "stale_existing_top_of_book_count": stale_existing_top_of_book_count,
        "snapshot_warnings": stale_reasons,
        "disclaimer": "Read-only depth enrichment only; no trading, scoring, or executable-liquidity claim.",
    }
    return payload


def _enrich_market(
    row: dict[str, Any],
    *,
    venue: str,
    captured_at: datetime,
    stale_reasons: list[str],
    kalshi_client: KalshiOrderbookClient,
    polymarket_client: PolymarketOrderbookClient,
) -> dict[str, Any]:
    if stale_reasons:
        return _unenriched(captured_at, warnings=stale_reasons)
    if venue == "kalshi":
        ticker = _string_or_none(row.get("ticker") or row.get("market_id"))
        if not ticker:
            return _unenriched(captured_at, warnings=["missing_market_id"])
        try:
            raw_orderbook = kalshi_client.fetch_orderbook(ticker)
            return parse_kalshi_orderbook_metrics(
                raw_orderbook,
                captured_at=captured_at,
                source_endpoint=kalshi_client.endpoint_for(ticker),
            )
        except OrderbookClientError as exc:
            return _unenriched(captured_at, source_endpoint=kalshi_client.endpoint_for(ticker), warnings=["orderbook_unavailable"], message=str(exc))
        except (TypeError, ValueError) as exc:
            return _unenriched(captured_at, source_endpoint=kalshi_client.endpoint_for(ticker), warnings=["parse_error"], message=str(exc))

    token_id = _polymarket_yes_token_id(row)
    if not token_id:
        return _unenriched(captured_at, warnings=["missing_token_id"])
    try:
        raw_orderbook = polymarket_client.fetch_orderbook(token_id)
        return parse_polymarket_orderbook_metrics(
            raw_orderbook,
            captured_at=captured_at,
            source_endpoint=polymarket_client.endpoint_for(token_id),
        )
    except OrderbookClientError as exc:
        return _unenriched(captured_at, source_endpoint=polymarket_client.endpoint_for(token_id), warnings=["orderbook_unavailable"], message=str(exc))
    except (TypeError, ValueError) as exc:
        return _unenriched(captured_at, source_endpoint=polymarket_client.endpoint_for(token_id), warnings=["parse_error"], message=str(exc))


def _load_snapshot(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"snapshot file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"snapshot JSON is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("snapshot JSON must be an object")
    return payload


def _validate_snapshot(snapshot: dict[str, Any]) -> None:
    if snapshot.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise ValueError("snapshot schema_version must be 1")
    if not isinstance(snapshot.get("normalized_markets"), list):
        raise ValueError("snapshot normalized_markets must be a list")


def _snapshot_stale_reasons(snapshot: dict[str, Any], now: datetime, max_snapshot_age_hours: float) -> list[str]:
    captured_at = snapshot.get("captured_at")
    parsed = _parse_datetime_or_none(captured_at)
    if parsed is None:
        return ["stale_snapshot"]
    age_hours = (now - parsed).total_seconds() / 3600.0
    if age_hours > max_snapshot_age_hours:
        return ["stale_snapshot"]
    return []


def _top_of_book_present(row: dict[str, Any]) -> bool:
    return row.get("best_bid") is not None or row.get("best_ask") is not None


def _polymarket_yes_token_id(row: dict[str, Any]) -> str | None:
    raw = row.get("raw")
    raw = raw if isinstance(raw, dict) else {}
    token_ids = _maybe_json_array(raw.get("clobTokenIds") or raw.get("clob_token_ids") or row.get("clobTokenIds"))
    if not isinstance(token_ids, list):
        return _string_or_none(row.get("token_id") or row.get("asset_id"))

    outcomes = row.get("outcomes")
    raw_outcomes = _maybe_json_array(raw.get("outcomes"))
    outcome_names: list[str] = []
    if isinstance(outcomes, list):
        for outcome in outcomes:
            if isinstance(outcome, dict):
                name = outcome.get("name")
                if name is not None:
                    outcome_names.append(str(name))
    if not outcome_names and isinstance(raw_outcomes, list):
        outcome_names = [str(item) for item in raw_outcomes if item is not None]

    yes_indexes = [index for index, name in enumerate(outcome_names) if name.strip().lower() == "yes"]
    if len(yes_indexes) != 1:
        return None
    yes_index = yes_indexes[0]
    if yes_index >= len(token_ids):
        return None
    return _string_or_none(token_ids[yes_index])


def _unenriched(
    captured_at: datetime,
    *,
    source_endpoint: str | None = None,
    warnings: list[str],
    message: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "orderbook_captured_at": captured_at.isoformat(),
        "best_bid": None,
        "best_ask": None,
        "spread": None,
        "depth_at_best_bid": None,
        "depth_at_best_ask": None,
        "depth_within_1c": {"bid": None, "ask": None, "total": None},
        "depth_within_3c": {"bid": None, "ask": None, "total": None},
        "depth_within_5c": {"bid": None, "ask": None, "total": None},
        "source_endpoint": source_endpoint,
        "enrichment_status": "unenriched",
        "enrichment_warnings": sorted(set(warnings)),
    }
    if message:
        payload["error_message"] = message
    return payload


def _maybe_json_array(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return []
    if stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _parse_datetime_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _string_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)
