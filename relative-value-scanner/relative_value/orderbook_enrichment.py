from __future__ import annotations

import copy
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from venues.orderbooks import (
    KalshiOrderbookClient,
    OrderbookClientError,
    PolymarketOrderbookClient,
    parse_kalshi_orderbook_metrics,
    parse_polymarket_orderbook_metrics,
)


SUPPORTED_SCHEMA_VERSION = 1
DEFAULT_MAX_SNAPSHOT_AGE_HOURS = 24.0
DEFAULT_FAILURE_SAMPLE_LIMIT = 10

# Per-row failure-reason vocabulary. Always one of these strings — never an
# inferred or guessed value. ``unknown`` is reserved for the rare case where
# the row was not enriched but neither an exception, a settled-state hint, nor
# an empty book was observed.
FAILURE_MISSING_TICKER = "missing_ticker"
FAILURE_INVALID_TICKER = "invalid_ticker"
FAILURE_STALE_SNAPSHOT = "stale_snapshot"
FAILURE_PARSE_ERROR = "parse_error"
FAILURE_HTTP_404 = "http_404_not_found_or_settled"
FAILURE_HTTP_429 = "http_429_rate_limited"
FAILURE_HTTP_4XX = "http_4xx_client_error"
FAILURE_HTTP_5XX = "http_5xx_server_error"
FAILURE_TIMEOUT = "timeout"
FAILURE_NETWORK = "network_error"
FAILURE_INVALID_JSON = "invalid_json"
FAILURE_ENDPOINT_OTHER = "endpoint_error_other"
FAILURE_CLOSED_OR_SETTLED = "closed_or_settled_empty_book"
FAILURE_EMPTY_BOOK_NO_LEVELS = "empty_book_no_levels"
FAILURE_UNKNOWN = "unknown"


ProgressCallback = Callable[[dict[str, Any]], None]


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
    preserve_raw_orderbook: bool = False,
    max_markets: int | None = None,
    progress_every: int = 0,
    retry_failed_once: bool = False,
    progress_callback: ProgressCallback | None = None,
    failure_sample_limit: int = DEFAULT_FAILURE_SAMPLE_LIMIT,
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
        source_snapshot_path=str(snapshot_path),
        preserve_raw_orderbook=preserve_raw_orderbook,
        max_markets=max_markets,
        progress_every=progress_every,
        retry_failed_once=retry_failed_once,
        progress_callback=progress_callback,
        failure_sample_limit=failure_sample_limit,
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
    source_snapshot_path: str | None = None,
    preserve_raw_orderbook: bool = False,
    max_markets: int | None = None,
    progress_every: int = 0,
    retry_failed_once: bool = False,
    progress_callback: ProgressCallback | None = None,
    failure_sample_limit: int = DEFAULT_FAILURE_SAMPLE_LIMIT,
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
    skipped_due_to_max_markets = 0
    retry_attempts = 0
    retry_successes = 0
    failure_reason_counts: Counter[str] = Counter()
    sample_failed_markets: list[dict[str, Any]] = []
    fetched_market_count = 0
    for index, row in enumerate(markets):
        if not isinstance(row, dict):
            continue
        if max_markets is not None and fetched_market_count >= max_markets:
            row["orderbook_enrichment"] = _unenriched(
                captured_at,
                warnings=["max_markets_reached"],
                failure_reason="max_markets_reached",
            )
            skipped_due_to_max_markets += 1
            continue
        enrichment = _enrich_market(
            row,
            venue=venue,
            captured_at=captured_at,
            stale_reasons=stale_reasons,
            kalshi_client=kalshi_client,
            polymarket_client=polymarket_client,
            source_snapshot_path=source_snapshot_path,
            preserve_raw_orderbook=preserve_raw_orderbook,
        )
        # Retry transient failures once when requested.
        if (
            retry_failed_once
            and enrichment.get("enrichment_status") != "enriched"
            and (enrichment.get("failure_reason") in {FAILURE_TIMEOUT, FAILURE_NETWORK, FAILURE_HTTP_5XX, FAILURE_HTTP_429})
        ):
            retry_attempts += 1
            retry_enrichment = _enrich_market(
                row,
                venue=venue,
                captured_at=captured_at,
                stale_reasons=stale_reasons,
                kalshi_client=kalshi_client,
                polymarket_client=polymarket_client,
                source_snapshot_path=source_snapshot_path,
                preserve_raw_orderbook=preserve_raw_orderbook,
            )
            if retry_enrichment.get("enrichment_status") == "enriched":
                enrichment = retry_enrichment
                retry_successes += 1
            else:
                # Keep the better-classified retry payload (preserves retry context).
                retry_enrichment["retry_attempted"] = True
                enrichment = retry_enrichment
        fetched_market_count += 1
        if enrichment.get("enrichment_status") == "enriched":
            enriched_count += 1
        if _top_of_book_present(row):
            existing_top_of_book_present_count += 1
            if enrichment.get("enrichment_status") != "enriched":
                stale_existing_top_of_book_count += 1
        if enrichment.get("enrichment_status") != "enriched":
            full_orderbook_missing_count += 1
            reason = enrichment.get("failure_reason") or FAILURE_UNKNOWN
            failure_reason_counts[reason] += 1
            if len(sample_failed_markets) < failure_sample_limit:
                sample_failed_markets.append(
                    {
                        "ticker": _string_or_none(row.get("ticker") or row.get("market_id")),
                        "market_id": _string_or_none(row.get("market_id")),
                        "failure_reason": reason,
                        "source_endpoint": enrichment.get("source_endpoint"),
                        "error_message": enrichment.get("error_message"),
                        "close_time": _string_or_none(row.get("close_time")),
                        "status": _string_or_none(row.get("status")),
                        "active": row.get("active"),
                        "closed": row.get("closed"),
                        "market_settled": enrichment.get("market_settled"),
                    }
                )
        warnings = enrichment.get("enrichment_warnings") if isinstance(enrichment.get("enrichment_warnings"), list) else []
        if "orderbook_unavailable" in warnings or "parse_error" in warnings:
            fetch_failed_count += 1
        row["orderbook_enrichment"] = enrichment
        if progress_callback is not None and progress_every > 0 and (index + 1) % progress_every == 0:
            progress_callback(
                {
                    "processed": index + 1,
                    "total": len(markets),
                    "enriched_count": enriched_count,
                    "fetch_failed_count": fetch_failed_count,
                }
            )

    by_reason = dict(failure_reason_counts)
    payload["orderbook_enrichment"] = {
        "schema_version": 1,
        "source": "read_only_orderbook_enrichment",
        "venue": venue,
        "captured_at": captured_at.isoformat(),
        "enrichment_generated_at": captured_at.isoformat(),
        "source_snapshot_path": source_snapshot_path,
        "market_count": len(markets),
        "enriched_count": enriched_count,
        "fresh_orderbook_fetch_enriched_count": enriched_count,
        "unenriched_count": len(markets) - enriched_count,
        "existing_top_of_book_present_count": existing_top_of_book_present_count,
        "full_orderbook_missing_count": full_orderbook_missing_count,
        "fetch_failed_count": fetch_failed_count,
        "stale_existing_top_of_book_count": stale_existing_top_of_book_count,
        "skipped_due_to_max_markets_count": skipped_due_to_max_markets,
        "retry_attempts": retry_attempts,
        "retry_successes": retry_successes,
        "fetch_failed_by_reason": by_reason,
        "missing_ticker_count": by_reason.get(FAILURE_MISSING_TICKER, 0),
        "invalid_ticker_count": by_reason.get(FAILURE_INVALID_TICKER, 0),
        "endpoint_error_count": (
            by_reason.get(FAILURE_HTTP_404, 0)
            + by_reason.get(FAILURE_HTTP_429, 0)
            + by_reason.get(FAILURE_HTTP_4XX, 0)
            + by_reason.get(FAILURE_HTTP_5XX, 0)
            + by_reason.get(FAILURE_ENDPOINT_OTHER, 0)
        ),
        "timeout_count": by_reason.get(FAILURE_TIMEOUT, 0),
        "network_error_count": by_reason.get(FAILURE_NETWORK, 0),
        "parse_error_count": by_reason.get(FAILURE_PARSE_ERROR, 0),
        "closed_or_settled_count": by_reason.get(FAILURE_CLOSED_OR_SETTLED, 0),
        "empty_book_no_levels_count": by_reason.get(FAILURE_EMPTY_BOOK_NO_LEVELS, 0),
        "unknown_failure_count": by_reason.get(FAILURE_UNKNOWN, 0),
        "sample_failed_markets": sample_failed_markets,
        "snapshot_warnings": stale_reasons,
        "disclaimer": "Read-only depth enrichment only; no trading, scoring, or executable-liquidity claim.",
        "diagnostics": {
            "max_markets": max_markets,
            "progress_every": progress_every,
            "retry_failed_once": retry_failed_once,
            "failure_sample_limit": failure_sample_limit,
        },
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
    source_snapshot_path: str | None,
    preserve_raw_orderbook: bool,
) -> dict[str, Any]:
    market_settled = _market_is_settled(row, captured_at)
    if stale_reasons:
        return _unenriched(
            captured_at,
            warnings=stale_reasons,
            failure_reason=FAILURE_STALE_SNAPSHOT,
            market_settled=market_settled,
        )
    if venue == "kalshi":
        ticker = _string_or_none(row.get("ticker") or row.get("market_id"))
        if not ticker:
            return _unenriched(
                captured_at,
                warnings=["missing_market_id"],
                failure_reason=FAILURE_MISSING_TICKER,
                market_settled=market_settled,
            )
        if not _looks_like_valid_kalshi_ticker(ticker):
            return _with_market_context(
                _unenriched(
                    captured_at,
                    source_endpoint=kalshi_client.endpoint_for(ticker),
                    warnings=["invalid_ticker"],
                    failure_reason=FAILURE_INVALID_TICKER,
                    market_settled=market_settled,
                ),
                row=row,
                venue=venue,
                source_snapshot_path=source_snapshot_path,
            )
        try:
            raw_orderbook = kalshi_client.fetch_orderbook(ticker)
            metrics = parse_kalshi_orderbook_metrics(
                raw_orderbook,
                captured_at=captured_at,
                source_endpoint=kalshi_client.endpoint_for(ticker),
            )
            payload = _with_market_context(
                metrics,
                row=row,
                venue=venue,
                source_snapshot_path=source_snapshot_path,
                raw_orderbook=raw_orderbook if preserve_raw_orderbook else None,
            )
            if payload.get("enrichment_status") != "enriched":
                payload["failure_reason"] = (
                    FAILURE_CLOSED_OR_SETTLED if market_settled else FAILURE_EMPTY_BOOK_NO_LEVELS
                )
                payload["market_settled"] = market_settled
            return payload
        except OrderbookClientError as exc:
            return _with_market_context(
                _unenriched(
                    captured_at,
                    source_endpoint=kalshi_client.endpoint_for(ticker),
                    warnings=["orderbook_unavailable"],
                    message=str(exc),
                    failure_reason=_classify_client_error(str(exc), market_settled=market_settled),
                    market_settled=market_settled,
                ),
                row=row,
                venue=venue,
                source_snapshot_path=source_snapshot_path,
            )
        except (TypeError, ValueError) as exc:
            return _with_market_context(
                _unenriched(
                    captured_at,
                    source_endpoint=kalshi_client.endpoint_for(ticker),
                    warnings=["parse_error"],
                    message=str(exc),
                    failure_reason=FAILURE_PARSE_ERROR,
                    market_settled=market_settled,
                ),
                row=row,
                venue=venue,
                source_snapshot_path=source_snapshot_path,
            )

    token_id = _polymarket_yes_token_id(row)
    if not token_id:
        return _with_market_context(
            _unenriched(
                captured_at,
                warnings=["missing_token_id"],
                failure_reason=FAILURE_MISSING_TICKER,
                market_settled=market_settled,
            ),
            row=row,
            venue=venue,
            source_snapshot_path=source_snapshot_path,
        )
    try:
        raw_orderbook = polymarket_client.fetch_orderbook(token_id)
        metrics = parse_polymarket_orderbook_metrics(
            raw_orderbook,
            captured_at=captured_at,
            source_endpoint=polymarket_client.endpoint_for(token_id),
        )
        payload = _with_market_context(
            metrics,
            row=row,
            venue=venue,
            source_snapshot_path=source_snapshot_path,
            raw_orderbook=raw_orderbook if preserve_raw_orderbook else None,
        )
        if payload.get("enrichment_status") != "enriched":
            payload["failure_reason"] = (
                FAILURE_CLOSED_OR_SETTLED if market_settled else FAILURE_EMPTY_BOOK_NO_LEVELS
            )
            payload["market_settled"] = market_settled
        return payload
    except OrderbookClientError as exc:
        return _with_market_context(
            _unenriched(
                captured_at,
                source_endpoint=polymarket_client.endpoint_for(token_id),
                warnings=["orderbook_unavailable"],
                message=str(exc),
                failure_reason=_classify_client_error(str(exc), market_settled=market_settled),
                market_settled=market_settled,
            ),
            row=row,
            venue=venue,
            source_snapshot_path=source_snapshot_path,
        )
    except (TypeError, ValueError) as exc:
        return _with_market_context(
            _unenriched(
                captured_at,
                source_endpoint=polymarket_client.endpoint_for(token_id),
                warnings=["parse_error"],
                message=str(exc),
                failure_reason=FAILURE_PARSE_ERROR,
                market_settled=market_settled,
            ),
            row=row,
            venue=venue,
            source_snapshot_path=source_snapshot_path,
        )


def _market_is_settled(row: dict[str, Any], now: datetime) -> bool:
    if row.get("closed") is True:
        return True
    status = row.get("status")
    if isinstance(status, str) and status.strip().lower() in {
        "settled",
        "closed",
        "expired",
        "finalized",
        "resolved",
        "inactive",
    }:
        return True
    for key in ("close_time", "expiration_time", "settlement_time"):
        close = _parse_datetime_or_none(row.get(key))
        if close is not None and close < now:
            return True
    return False


def _looks_like_valid_kalshi_ticker(ticker: str) -> bool:
    return bool(ticker) and len(ticker) <= 128 and " " not in ticker


def _classify_client_error(message: str, *, market_settled: bool) -> str:
    lower = (message or "").lower()
    if "http 404" in lower:
        return FAILURE_HTTP_404
    if "http 429" in lower:
        return FAILURE_HTTP_429
    if "http 5" in lower:
        return FAILURE_HTTP_5XX
    if "http 4" in lower:
        return FAILURE_HTTP_4XX
    if "timed out" in lower or "timeout" in lower:
        return FAILURE_TIMEOUT
    if "request failed" in lower or "network" in lower:
        return FAILURE_NETWORK
    if "invalid json" in lower:
        return FAILURE_INVALID_JSON
    if market_settled:
        return FAILURE_CLOSED_OR_SETTLED
    return FAILURE_ENDPOINT_OTHER


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
    failure_reason: str | None = None,
    market_settled: bool | None = None,
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
        "failure_reason": failure_reason or FAILURE_UNKNOWN,
    }
    if message:
        payload["error_message"] = message
    if market_settled is not None:
        payload["market_settled"] = bool(market_settled)
    return payload


def _with_market_context(
    enrichment: dict[str, Any],
    *,
    row: dict[str, Any],
    venue: str,
    source_snapshot_path: str | None,
    raw_orderbook: Any | None = None,
) -> dict[str, Any]:
    enriched = dict(enrichment)
    enriched["venue"] = venue
    enriched["market_id"] = _string_or_none(row.get("market_id"))
    enriched["ticker"] = _string_or_none(row.get("ticker") or row.get("market_id"))
    enriched["source_snapshot_path"] = source_snapshot_path
    if raw_orderbook is not None:
        enriched["raw_orderbook"] = raw_orderbook
    return enriched


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
