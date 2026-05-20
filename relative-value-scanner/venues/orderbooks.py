from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


KALSHI_API_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
POLYMARKET_CLOB_BASE_URL = "https://clob.polymarket.com"


class OrderbookClientError(RuntimeError):
    """Raised when a read-only orderbook request fails."""


class KalshiOrderbookClient:
    """Small read-only client for Kalshi market orderbooks."""

    def __init__(
        self,
        base_url: str = KALSHI_API_BASE_URL,
        timeout_seconds: float = 10.0,
        user_agent: str = "relative-value-scanner/0.1 read-only",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def endpoint_for(self, ticker: str) -> str:
        return f"{self.base_url}/markets/{quote(ticker, safe='')}/orderbook"

    def fetch_orderbook(self, ticker: str) -> Any:
        if not ticker:
            raise ValueError("ticker is required")
        request = Request(
            self.endpoint_for(ticker),
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
            raise OrderbookClientError(f"Kalshi orderbook API returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise OrderbookClientError(f"Kalshi orderbook API request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise OrderbookClientError("Kalshi orderbook API request timed out") from exc
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise OrderbookClientError("Kalshi orderbook API returned invalid JSON") from exc


class PolymarketOrderbookClient:
    """Small read-only client for Polymarket public CLOB orderbooks."""

    def __init__(
        self,
        base_url: str = POLYMARKET_CLOB_BASE_URL,
        timeout_seconds: float = 10.0,
        user_agent: str = "relative-value-scanner/0.1 read-only",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def endpoint_for(self, token_id: str) -> str:
        return f"{self.base_url}/book?{urlencode({'token_id': token_id})}"

    def fetch_orderbook(self, token_id: str) -> Any:
        if not token_id:
            raise ValueError("token_id is required")
        request = Request(
            self.endpoint_for(token_id),
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
            raise OrderbookClientError(f"Polymarket CLOB orderbook API returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise OrderbookClientError(f"Polymarket CLOB orderbook API request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise OrderbookClientError("Polymarket CLOB orderbook API request timed out") from exc
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise OrderbookClientError("Polymarket CLOB orderbook API returned invalid JSON") from exc


def parse_kalshi_orderbook_metrics(raw_orderbook: Any, *, captured_at: datetime, source_endpoint: str) -> dict[str, Any]:
    """Normalize Kalshi's bid-only YES/NO book into conservative YES-space depth metrics."""
    if not isinstance(raw_orderbook, dict):
        raise ValueError("Kalshi orderbook response must be an object")
    container = raw_orderbook.get("orderbook_fp") or raw_orderbook.get("orderbook") or raw_orderbook
    if not isinstance(container, dict):
        raise ValueError("Kalshi orderbook payload missing orderbook object")

    yes_bids = _levels_from_pairs(container.get("yes_dollars") or container.get("yes") or container.get("yes_bids"))
    no_bids = _levels_from_pairs(container.get("no_dollars") or container.get("no") or container.get("no_bids"))
    yes_asks = [(round(1.0 - price, 10), size) for price, size in no_bids]
    return _metrics_from_levels(
        bids=yes_bids,
        asks=yes_asks,
        captured_at=captured_at,
        source_endpoint=source_endpoint,
        extra_warnings=[],
    )


def parse_polymarket_orderbook_metrics(raw_orderbook: Any, *, captured_at: datetime, source_endpoint: str) -> dict[str, Any]:
    """Normalize Polymarket CLOB token book depth metrics in token YES-price space."""
    if not isinstance(raw_orderbook, dict):
        raise ValueError("Polymarket orderbook response must be an object")
    bids = _levels_from_dicts(raw_orderbook.get("bids"))
    asks = _levels_from_dicts(raw_orderbook.get("asks"))
    return _metrics_from_levels(
        bids=bids,
        asks=asks,
        captured_at=captured_at,
        source_endpoint=source_endpoint,
        extra_warnings=[],
    )


def write_enriched_snapshot(snapshot: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")


def _metrics_from_levels(
    *,
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    captured_at: datetime,
    source_endpoint: str,
    extra_warnings: list[str],
) -> dict[str, Any]:
    clean_bids = sorted([(price, size) for price, size in bids if price >= 0 and size >= 0], reverse=True)
    clean_asks = sorted([(price, size) for price, size in asks if price >= 0 and size >= 0])
    best_bid = clean_bids[0][0] if clean_bids else None
    best_ask = clean_asks[0][0] if clean_asks else None
    warnings = list(extra_warnings)
    if best_bid is None and best_ask is None:
        warnings.append("orderbook_unavailable")

    return {
        "orderbook_captured_at": captured_at.isoformat(),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": _spread(best_bid, best_ask),
        "depth_at_best_bid": _depth_at_price(clean_bids, best_bid),
        "depth_at_best_ask": _depth_at_price(clean_asks, best_ask),
        "depth_within_1c": _depth_within(clean_bids, clean_asks, best_bid, best_ask, 0.01),
        "depth_within_3c": _depth_within(clean_bids, clean_asks, best_bid, best_ask, 0.03),
        "depth_within_5c": _depth_within(clean_bids, clean_asks, best_bid, best_ask, 0.05),
        "source_endpoint": source_endpoint,
        "enrichment_status": "unenriched" if "orderbook_unavailable" in warnings else "enriched",
        "enrichment_warnings": warnings,
    }


def _levels_from_pairs(value: Any) -> list[tuple[float, float]]:
    levels: list[tuple[float, float]] = []
    if not isinstance(value, list):
        return levels
    for level in value:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        price = _float_or_none(level[0])
        size = _float_or_none(level[1])
        if price is not None and size is not None:
            levels.append((price, size))
    return levels


def _levels_from_dicts(value: Any) -> list[tuple[float, float]]:
    levels: list[tuple[float, float]] = []
    if not isinstance(value, list):
        return levels
    for level in value:
        if not isinstance(level, dict):
            continue
        price = _float_or_none(level.get("price"))
        size = _float_or_none(level.get("size"))
        if price is not None and size is not None:
            levels.append((price, size))
    return levels


def _depth_at_price(levels: list[tuple[float, float]], price: float | None) -> float | None:
    if price is None:
        return None
    return round(sum(size for level_price, size in levels if level_price == price), 10)


def _depth_within(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    best_bid: float | None,
    best_ask: float | None,
    width: float,
) -> dict[str, float | None]:
    bid_depth = None
    ask_depth = None
    if best_bid is not None:
        bid_depth = round(sum(size for price, size in bids if best_bid - price <= width + 1e-12), 10)
    if best_ask is not None:
        ask_depth = round(sum(size for price, size in asks if price - best_ask <= width + 1e-12), 10)
    total = None if bid_depth is None and ask_depth is None else round((bid_depth or 0.0) + (ask_depth or 0.0), 10)
    return {"bid": bid_depth, "ask": ask_depth, "total": total}


def _spread(best_bid: float | None, best_ask: float | None) -> float | None:
    if best_bid is None or best_ask is None:
        return None
    return round(best_ask - best_bid, 10)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
