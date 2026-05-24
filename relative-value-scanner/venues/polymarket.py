from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from venues.base import JsonExchangeFixtureAdapter


GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"


@dataclass(frozen=True)
class PolymarketMarketFilterOptions:
    include_closed: bool = False
    include_not_accepting_orders: bool = False
    include_past_end_date: bool = False


class FixturePolymarketAdapter(JsonExchangeFixtureAdapter):
    def __init__(self, path: Path) -> None:
        super().__init__("polymarket", path)


class PolymarketGammaClient:
    """Small read-only client for Polymarket's public Gamma discovery API."""

    def __init__(
        self,
        base_url: str = GAMMA_API_BASE_URL,
        timeout_seconds: float = 10.0,
        user_agent: str = "relative-value-scanner/0.1 read-only",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def fetch_events(
        self,
        limit: int = 25,
        *,
        tag_slug: str | None = None,
        tag_id: int | None = None,
    ) -> Any:
        if limit <= 0:
            raise ValueError("limit must be positive")
        params = {
            "active": "true",
            "closed": "false",
            "limit": str(limit),
        }
        if tag_slug:
            params["tag_slug"] = tag_slug
        if tag_id is not None:
            params["tag_id"] = str(tag_id)
        query_string = urlencode(params)
        request = Request(
            f"{self.base_url}/events?{query_string}",
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
            raise RuntimeError(f"Polymarket Gamma API returned HTTP {exc.code} for /events") from exc
        except URLError as exc:
            raise RuntimeError(f"Polymarket Gamma API request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError("Polymarket Gamma API request timed out") from exc
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Polymarket Gamma API returned invalid JSON") from exc

    def fetch_market_snapshot(
        self,
        limit: int = 25,
        filter_options: PolymarketMarketFilterOptions | None = None,
        *,
        tag_slug: str | None = None,
        tag_id: int | None = None,
    ) -> dict[str, Any]:
        raw_response = self.fetch_events(limit=limit, tag_slug=tag_slug, tag_id=tag_id)
        return build_polymarket_market_snapshot(raw_response, filter_options=filter_options)

    def fetch_tag_inventory(self, limit: int = 500) -> Any:
        if limit <= 0:
            raise ValueError("limit must be positive")
        query_string = urlencode({"limit": str(limit)})
        request = Request(
            f"{self.base_url}/tags?{query_string}",
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
            raise RuntimeError(f"Polymarket Gamma API returned HTTP {exc.code} for /tags") from exc
        except URLError as exc:
            raise RuntimeError(f"Polymarket Gamma tags request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError("Polymarket Gamma tags request timed out") from exc
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Polymarket Gamma tags returned invalid JSON") from exc


def build_polymarket_market_snapshot(
    raw_response: Any,
    fetched_at: datetime | None = None,
    filter_options: PolymarketMarketFilterOptions | None = None,
) -> dict[str, Any]:
    captured_at = fetched_at or datetime.now(timezone.utc)
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("fetched_at must include timezone information")
    options = filter_options or PolymarketMarketFilterOptions()
    events = parse_gamma_events_response(raw_response)
    raw_market_count = count_raw_markets(events)
    markets, skip_counts = extract_markets_from_events(events, captured_at=captured_at, filter_options=options)
    orderbook_enabled_count = sum(1 for market in markets if market.get("enable_order_book") is True)
    return {
        "schema_version": 1,
        "source": "polymarket_gamma",
        "captured_at": captured_at.isoformat(),
        "raw_response": raw_response,
        "event_count": len(events),
        "market_count": raw_market_count,
        "normalized_count": len(markets),
        "orderbook_enabled_count": orderbook_enabled_count,
        **skip_counts,
        "normalized_markets": markets,
    }


def parse_gamma_events_response(raw_response: Any) -> list[dict[str, Any]]:
    if isinstance(raw_response, list):
        return [event for event in raw_response if isinstance(event, dict)]
    if isinstance(raw_response, dict):
        for key in ("events", "data", "results"):
            value = raw_response.get(key)
            if isinstance(value, list):
                return [event for event in value if isinstance(event, dict)]
        if "markets" in raw_response:
            return [raw_response]
    raise ValueError("Polymarket Gamma response must be a list of events or a dict containing events/data/results")


def count_raw_markets(events: list[dict[str, Any]]) -> int:
    return sum(1 for event in events for market in _as_list(event.get("markets")) if isinstance(market, dict))


def extract_markets_from_events(
    events: list[dict[str, Any]],
    captured_at: datetime | None = None,
    filter_options: PolymarketMarketFilterOptions | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    effective_captured_at = captured_at or datetime.now(timezone.utc)
    if effective_captured_at.tzinfo is None or effective_captured_at.utcoffset() is None:
        raise ValueError("captured_at must include timezone information")
    options = filter_options or PolymarketMarketFilterOptions()
    normalized: list[dict[str, Any]] = []
    skip_counts = {
        "skipped_closed_count": 0,
        "skipped_not_accepting_orders_count": 0,
        "skipped_inactive_count": 0,
        "skipped_archived_count": 0,
        "skipped_past_end_date_count": 0,
    }
    for event in events:
        event_id = _first_present(event, "id", "eventId", "slug", "ticker")
        event_title = _first_present(event, "title", "question", "name", "slug") or ""
        for market in _as_list(event.get("markets")):
            if not isinstance(market, dict):
                continue
            state = _market_state(event, market)
            skip_reasons = _skip_reasons(state, effective_captured_at, options)
            for reason in skip_reasons:
                skip_counts[reason] += 1
            if skip_reasons:
                continue
            outcomes = [_string_or_none(value) for value in _as_list(_maybe_json_array(market.get("outcomes")))]
            prices = [_float_or_none(value) for value in _as_list(_maybe_json_array(market.get("outcomePrices")))]
            outcome_rows = []
            for index, outcome in enumerate(outcomes):
                if outcome is None:
                    continue
                outcome_rows.append(
                    {
                        "name": outcome,
                        "outcome_yes_token_price": prices[index] if index < len(prices) else None,
                    }
                )
            normalized.append(
                {
                    "venue": "polymarket",
                    "event_id": None if event_id is None else str(event_id),
                    "event_title": str(event_title),
                    "event_slug": _string_or_none(event.get("slug")),
                    "market_id": str(_first_present(market, "id", "conditionId", "slug", "question") or ""),
                    "condition_id": _string_or_none(market.get("conditionId")),
                    "question": str(_first_present(market, "question", "title", "slug") or ""),
                    "active": state["active"],
                    "closed": state["closed"],
                    "accepting_orders": state["accepting_orders"],
                    "archived": state["archived"],
                    "enable_order_book": state["enable_order_book"],
                    "best_bid": _float_or_none(_first_present(market, "bestBid", "best_bid")),
                    "best_ask": _float_or_none(_first_present(market, "bestAsk", "best_ask")),
                    "outcomes": outcome_rows,
                    "volume": _float_or_none(_first_present(market, "volume", "volumeNum", "volume24hr")),
                    "liquidity": _float_or_none(_first_present(market, "liquidity", "liquidityNum")),
                    "end_date": state["end_date"],
                    "raw": market,
                }
            )
    return normalized, skip_counts


def _market_state(event: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    return {
        "active": _bool_or_none(_market_or_event_value(event, market, "active")),
        "closed": _bool_or_none(_market_or_event_value(event, market, "closed")),
        "archived": _bool_or_none(_market_or_event_value(event, market, "archived")),
        "accepting_orders": _bool_or_none(_first_present(market, "acceptingOrders", "accepting_orders")),
        "enable_order_book": _bool_or_none(_first_present(market, "enableOrderBook", "enable_order_book")),
        "end_date": _string_or_none(
            _first_present(market, "endDate", "end_date", "endDateIso")
            or _first_present(event, "endDate", "end_date", "endDateIso")
        ),
    }


def _market_or_event_value(event: dict[str, Any], market: dict[str, Any], key: str) -> Any:
    value = market.get(key)
    if value is not None:
        return value
    return event.get(key)


def _skip_reasons(
    state: dict[str, Any],
    captured_at: datetime,
    options: PolymarketMarketFilterOptions,
) -> list[str]:
    """Return independent skip-counter tags; counters can overlap and are not additive."""
    reasons: list[str] = []
    if state["active"] is not True:
        reasons.append("skipped_inactive_count")
    if state["closed"] is True and not options.include_closed:
        reasons.append("skipped_closed_count")
    if state["archived"] is True:
        reasons.append("skipped_archived_count")
    if state["accepting_orders"] is not True and not options.include_not_accepting_orders:
        reasons.append("skipped_not_accepting_orders_count")
    end_date = _parse_datetime_or_none(state["end_date"])
    if end_date is not None and end_date < captured_at and not options.include_past_end_date:
        reasons.append("skipped_past_end_date_count")
    return reasons


def write_polymarket_market_snapshot(snapshot: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


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


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


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
