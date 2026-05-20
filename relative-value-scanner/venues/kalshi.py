from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from venues.base import JsonExchangeFixtureAdapter


KALSHI_API_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


@dataclass(frozen=True)
class KalshiMarketFilterOptions:
    include_closed: bool = False
    include_past_close_time: bool = False


class FixtureKalshiAdapter(JsonExchangeFixtureAdapter):
    def __init__(self, path: Path) -> None:
        super().__init__("kalshi", path)


class KalshiReadOnlyClient:
    """Small read-only client for Kalshi public market discovery."""

    def __init__(
        self,
        base_url: str = KALSHI_API_BASE_URL,
        timeout_seconds: float = 10.0,
        user_agent: str = "relative-value-scanner/0.1 read-only",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def fetch_markets(
        self,
        limit: int = 25,
        *,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        cursor: str | None = None,
        max_pages: int = 1,
    ) -> Any:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")

        pages: list[Any] = []
        next_cursor = cursor
        for _page_number in range(max_pages):
            response = self._fetch_markets_page(
                limit=limit,
                series_ticker=series_ticker,
                event_ticker=event_ticker,
                cursor=next_cursor,
            )
            pages.append(response)
            next_cursor = _response_cursor(response)
            if not next_cursor:
                break
        if len(pages) == 1:
            return pages[0]
        return _combine_paginated_markets_response(pages, next_cursor)

    def _fetch_markets_page(
        self,
        *,
        limit: int,
        series_ticker: str | None,
        event_ticker: str | None,
        cursor: str | None,
    ) -> Any:
        params = {
            "status": "open",
            "limit": str(limit),
        }
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if cursor:
            params["cursor"] = cursor
        query_string = urlencode(params)
        request = Request(
            f"{self.base_url}/markets?{query_string}",
            headers={
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            raise RuntimeError(f"Kalshi markets API returned HTTP {exc.code} for /markets") from exc
        except URLError as exc:
            raise RuntimeError(f"Kalshi markets API request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError("Kalshi markets API request timed out") from exc
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Kalshi markets API returned invalid JSON") from exc

    def fetch_market_snapshot(
        self,
        limit: int = 25,
        filter_options: KalshiMarketFilterOptions | None = None,
        *,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        cursor: str | None = None,
        max_pages: int = 1,
    ) -> dict[str, Any]:
        raw_response = self.fetch_markets(
            limit=limit,
            series_ticker=series_ticker,
            event_ticker=event_ticker,
            cursor=cursor,
            max_pages=max_pages,
        )
        return build_kalshi_market_snapshot(raw_response, filter_options=filter_options)


def build_kalshi_market_snapshot(
    raw_response: Any,
    fetched_at: datetime | None = None,
    filter_options: KalshiMarketFilterOptions | None = None,
) -> dict[str, Any]:
    captured_at = fetched_at or datetime.now(timezone.utc)
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("fetched_at must include timezone information")
    options = filter_options or KalshiMarketFilterOptions()
    markets = parse_kalshi_markets_response(raw_response)
    normalized, skip_counts = normalize_kalshi_markets(markets, captured_at=captured_at, filter_options=options)
    return {
        "schema_version": 1,
        "source": "kalshi_markets",
        "captured_at": captured_at.isoformat(),
        "raw_response": raw_response,
        "event_count": None,
        "market_count": len(markets),
        "normalized_count": len(normalized),
        **skip_counts,
        "normalized_markets": normalized,
    }


def parse_kalshi_markets_response(raw_response: Any) -> list[dict[str, Any]]:
    if isinstance(raw_response, list):
        return [market for market in raw_response if isinstance(market, dict)]
    if isinstance(raw_response, dict) and isinstance(raw_response.get("markets"), list):
        return [market for market in raw_response["markets"] if isinstance(market, dict)]
    raise ValueError("Kalshi markets response must be a list or a dict containing a markets list")


def _response_cursor(raw_response: Any) -> str | None:
    if not isinstance(raw_response, dict):
        return None
    cursor = raw_response.get("cursor") or raw_response.get("next_cursor")
    if cursor is None:
        return None
    cursor_string = str(cursor).strip()
    return cursor_string or None


def _combine_paginated_markets_response(pages: list[Any], next_cursor: str | None) -> dict[str, Any]:
    markets: list[dict[str, Any]] = []
    for page in pages:
        markets.extend(parse_kalshi_markets_response(page))
    return {
        "markets": markets,
        "cursor": next_cursor or "",
        "pages": pages,
        "page_count": len(pages),
    }


def normalize_kalshi_markets(
    markets: list[dict[str, Any]],
    captured_at: datetime | None = None,
    filter_options: KalshiMarketFilterOptions | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    effective_captured_at = captured_at or datetime.now(timezone.utc)
    if effective_captured_at.tzinfo is None or effective_captured_at.utcoffset() is None:
        raise ValueError("captured_at must include timezone information")
    options = filter_options or KalshiMarketFilterOptions()
    skip_counts = {
        "skipped_closed_count": 0,
        "skipped_inactive_count": 0,
        "skipped_past_close_time_count": 0,
    }
    normalized: list[dict[str, Any]] = []
    for market in markets:
        state = _market_state(market)
        skip_reasons = _skip_reasons(state, effective_captured_at, options)
        for reason in skip_reasons:
            skip_counts[reason] += 1
        if skip_reasons:
            continue
        ticker = _string_or_none(_first_present(market, "ticker", "market_ticker"))
        yes_bid = _price_or_none(_first_present(market, "yes_bid_dollars", "yes_bid"))
        yes_ask = _price_or_none(_first_present(market, "yes_ask_dollars", "yes_ask"))
        no_ask = _price_or_none(_first_present(market, "no_ask_dollars", "no_ask"))
        normalized.append(
            {
                "venue": "kalshi",
                "event_id": _string_or_none(_first_present(market, "event_ticker", "event_id")),
                "event_title": _string_or_none(_first_present(market, "event_title", "event_subtitle")),
                "market_id": ticker,
                "ticker": ticker,
                "question": str(_first_present(market, "title", "subtitle", "ticker") or ""),
                "title": _string_or_none(market.get("title")),
                "outcomes": [
                    {"name": "Yes", "outcome_yes_token_price": yes_ask},
                    {"name": "No", "outcome_yes_token_price": no_ask if no_ask is not None else _complement_or_none(yes_bid)},
                ],
                "best_bid": yes_bid,
                "best_ask": yes_ask,
                "volume": _float_or_none(_first_present(market, "volume_fp", "volume", "volume_24h_fp")),
                "liquidity": _float_or_none(_first_present(market, "liquidity_dollars", "liquidity")),
                "end_date": _normalized_end_date(market),
                "close_time": _string_or_none(_first_present(market, "close_time", "expiration_time", "expected_expiration_time")),
                "active": state["active"],
                "closed": state["closed"],
                "status": state["status"],
                "raw": market,
            }
        )
    return normalized, skip_counts


def write_kalshi_market_snapshot(snapshot: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")


def _market_state(market: dict[str, Any]) -> dict[str, Any]:
    status = str(_first_present(market, "status", "market_status") or "").strip().lower()
    return {
        "status": status or None,
        "active": status in {"open", "active"},
        "closed": status in {"closed", "settled", "expired"},
        "close_time": _string_or_none(_first_present(market, "close_time", "expiration_time", "expected_expiration_time")),
    }


def _skip_reasons(
    state: dict[str, Any],
    captured_at: datetime,
    options: KalshiMarketFilterOptions,
) -> list[str]:
    """Return independent skip-counter tags; counters can overlap and are not additive."""
    reasons: list[str] = []
    include_closed_status = state["closed"] is True and options.include_closed
    if state["active"] is not True and not include_closed_status:
        reasons.append("skipped_inactive_count")
    if state["closed"] is True and not options.include_closed:
        reasons.append("skipped_closed_count")
    close_time = _parse_datetime_or_none(state["close_time"])
    if close_time is not None and close_time < captured_at and not options.include_past_close_time:
        reasons.append("skipped_past_close_time_count")
    return reasons


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _normalized_end_date(market: dict[str, Any]) -> str | None:
    expected_expiration_time = _string_or_none(market.get("expected_expiration_time"))
    if _bool_or_none(market.get("can_close_early")) is True and _parse_datetime_or_none(expected_expiration_time) is not None:
        return expected_expiration_time
    return _string_or_none(_first_present(market, "close_time", "expiration_time", "expected_expiration_time"))


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _price_or_none(value: Any) -> float | None:
    number = _float_or_none(value)
    if number is None:
        return None
    if number > 1.0:
        return number / 100.0
    return number


def _complement_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return round(max(0.0, min(1.0, 1.0 - value)), 10)


def _parse_datetime_or_none(value: str | None) -> datetime | None:
    if not value:
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
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
