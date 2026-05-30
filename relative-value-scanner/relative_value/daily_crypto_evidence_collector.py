"""Public-read-only fresh evidence collector for daily crypto three-venue checking.

This module fetches *current* daily crypto point-in-time threshold evidence from
public, unauthenticated Kalshi and Polymarket endpoints and writes it to the
polished ``polished_crypto_market_family_evidence_v1`` schema so the existing
``crypto_threshold_basis_review_scout`` can consume it without further parsing.

Strict scope:
  - Public market-data GET only. No order placement, no order cancellation, no
    account/auth/session/balance/position/private-key/wallet code. No browser
    automation, no headless browser, no Cloudflare-bypass, no proxy/VPN/Tor.
  - The HTTP function is injectable; ``http_get`` allows callers (notably tests)
    to substitute a stub. When omitted, a small ``urllib`` wrapper is used.
  - CDNA fresh fetching is NOT supported here. If the caller supplies an
    ``cdna_evidence_dir`` (saved file or per-asset folder), files are *copied*
    into the per-asset output folder. CDNA missing must not block the
    Kalshi/Polymarket scan.

Output layout:
    <output_root>/<asset>/kalshi_polished_evidence.json
    <output_root>/<asset>/polymarket_polished_evidence.json
    <output_root>/<asset>/cdna_polished_evidence.json   (only if copied in)
"""
from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


HttpGet = Callable[[str, float], Any]
Sleep = Callable[[float], None]


POLISHED_SCHEMA = "polished_crypto_market_family_evidence_v1"
DEFAULT_USER_AGENT = "relative-value-scanner/0.1 public-read-only"
DEFAULT_TIMEOUT_SECONDS = 10.0

KALSHI_PUBLIC_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
POLYMARKET_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_BASE_URL = "https://clob.polymarket.com"

# Public-read-only fetch resilience knobs. The CLOB book endpoint is flaky and
# rate-limited; a couple of polite retries with linear backoff recovers most
# transient failures without ever issuing a write.
CLOB_FETCH_ATTEMPTS = 3
EVENTS_FETCH_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 0.5


class HttpGetError(RuntimeError):
    """Raised by the default urllib getter when a public read fails.

    Preserves the *original* exception class name, the HTTP status (when known)
    and the underlying message so callers can log something more useful than a
    bare ``RuntimeError``.
    """

    def __init__(self, *, url: str, original: BaseException | None = None, status: int | None = None, message: str | None = None) -> None:
        self.url = url
        self.status = status
        self.original_type = type(original).__name__ if original is not None else "HttpGetError"
        self.message = message if message is not None else str(original)
        detail = self.message or self.original_type
        if status is not None:
            detail = f"HTTP {status}: {detail}"
        super().__init__(detail)


# Series ticker candidates used by Kalshi for daily point-in-time crypto markets.
# The first entry is the canonical daily series Mason listed; the remaining
# entries are queried only as fallbacks when the canonical series returns zero,
# so a series rename does not silently zero the scan.
KALSHI_DAILY_SERIES_CANDIDATES_BY_ASSET: dict[str, tuple[str, ...]] = {
    "BTC": ("KXBTCD", "KXBTC"),
    "ETH": ("KXETHD", "KXETH"),
    "SOL": ("KXSOLD", "KXSOL"),
    "XRP": ("KXXRPD", "KXXRP"),
    "DOGE": ("KXDOGED", "KXDOGE"),
}

# Back-compat: single canonical series per asset (first candidate).
KALSHI_DAILY_SERIES_BY_ASSET: dict[str, str] = {
    asset: candidates[0] for asset, candidates in KALSHI_DAILY_SERIES_CANDIDATES_BY_ASSET.items()
}

# Kalshi's `event_ticker` for daily threshold markets typically encodes the
# observation date and time slot like `-26MAY2917-` (YYMMMDDHH). We use this to
# extract a target_date and target_time for typed-key matching.
_KALSHI_EVENT_RE = re.compile(
    r"-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<hh>\d{2})"
)
_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

POLYMARKET_KEYWORDS_BY_ASSET: dict[str, list[str]] = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth"],
    "SOL": ["solana", "sol"],
    "XRP": ["xrp", "ripple"],
    "DOGE": ["dogecoin", "doge"],
}


# Slug name used by Polymarket's daily threshold events. Real example:
#   bitcoin-above-on-may-29-2026-12pm-et
POLYMARKET_SLUG_NAME_BY_ASSET: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "xrp",
    "DOGE": "dogecoin",
}


# Polymarket's daily crypto threshold events publish per-hour expiries.
# We probe a conservative set of hour slots that match common Kalshi cadence.
POLYMARKET_HOUR_SLOTS: tuple[tuple[int, str], ...] = (
    (12, "pm"),  # noon — the most common slot
    (4, "pm"),
    (8, "pm"),
    (11, "pm"),
    (1, "am"),
    (8, "am"),
)


# Seed slugs Mason has collected via prior batches; used as last-resort hints.
POLYMARKET_SEED_SLUGS: tuple[str, ...] = (
    "bitcoin-above-on-may-29-2026-12pm-et",
    "ethereum-above-on-may-29-2026-12pm-et",
    "solana-above-on-may-29-2026-12pm-et",
    "xrp-above-on-may-29-2026-12pm-et",
    "dogecoin-above-on-may-29-2026-12pm-et",
)


_POLY_TARGET_TIME_RE = re.compile(
    r"\b(?P<hour>\d{1,2})\s*(?P<ampm>am|pm)\s*(?P<tz>et|est|edt|utc)?\b",
    re.IGNORECASE,
)
_POLY_THRESHOLD_RE = re.compile(
    r"above\s+\$?\s*(?P<amount>[0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?|[0-9]+(?:\.\d+)?)\s*(?P<k>k)?",
    re.IGNORECASE,
)
_MONTH_SHORT_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

KALSHI_SETTLEMENT_INDEX_BY_ASSET: dict[str, str] = {
    "BTC": "CF Benchmarks Bitcoin Real-Time Index (BRTI)",
    "ETH": "CF Benchmarks Ethereum Real-Time Index (ETHUSD_RTI)",
    "SOL": "CF Benchmarks SOLUSD_RTI",
    "XRP": "CF Benchmarks XRPUSD_RTI",
    "DOGE": "CF Benchmarks DOGEUSD_RTI",
}

POLYMARKET_SETTLEMENT_SOURCE_BY_ASSET: dict[str, str] = {
    "BTC": "Binance BTC/USDT 1-minute candle Close at 12:00 ET (noon)",
    "ETH": "Binance ETH/USDT 1-minute candle Close at 12:00 ET (noon)",
    "SOL": "Binance SOL/USDT 1-minute candle Close at 12:00 ET (noon)",
    "XRP": "Binance XRP/USDT 1-minute candle Close at 12:00 ET (noon)",
    "DOGE": "Binance DOGE/USDT 1-minute candle Close at 12:00 ET (noon)",
}


# ---------------------------------------------------------------------------- #
# Public entry point                                                           #
# ---------------------------------------------------------------------------- #


def write_daily_crypto_live_evidence(
    *,
    assets: list[str],
    output_root: Path,
    generated_at: datetime | None = None,
    http_get: HttpGet | None = None,
    cdna_evidence_dir: Path | None = None,
    target_date: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    sleep: Sleep | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    getter: HttpGet = http_get or _default_http_get
    sleeper: Sleep = sleep or time.sleep
    per_asset_records: list[dict[str, Any]] = []
    for asset in assets:
        asset_record = _refresh_asset(
            asset=asset,
            output_root=output_root,
            generated_at=generated,
            http_get=getter,
            cdna_evidence_dir=cdna_evidence_dir,
            target_date=target_date,
            timeout_seconds=timeout_seconds,
            sleep=sleeper,
        )
        per_asset_records.append(asset_record)
    summary = {
        "schema_kind": "daily_crypto_live_evidence_collection_v1",
        "schema_version": 1,
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "public_no_auth_only": True,
        "output_root": str(output_root),
        "target_date": target_date,
        "per_asset": per_asset_records,
        "safety": {
            "diagnostic_only": True,
            "public_no_auth_only": True,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
            "browser_automation_added": False,
        },
    }
    (output_root / "collection_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary


# ---------------------------------------------------------------------------- #
# Per-asset refresh                                                            #
# ---------------------------------------------------------------------------- #


def _refresh_asset(
    *,
    asset: str,
    output_root: Path,
    generated_at: datetime,
    http_get: HttpGet,
    cdna_evidence_dir: Path | None,
    target_date: str | None,
    timeout_seconds: float,
    sleep: Sleep,
) -> dict[str, Any]:
    asset_dir = output_root / asset.lower()
    asset_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    record: dict[str, Any] = {
        "asset": asset,
        "asset_dir": str(asset_dir),
        "kalshi_markets_found": 0,
        "polymarket_markets_found": 0,
        "cdna_files_copied": 0,
        "warnings": warnings,
    }

    kalshi_payload, k_warns, kalshi_diagnostics = _build_kalshi_payload(
        asset=asset,
        generated_at=generated_at,
        http_get=http_get,
        target_date=target_date,
        timeout_seconds=timeout_seconds,
        sleep=sleep,
    )
    warnings.extend(k_warns)
    (asset_dir / "kalshi_polished_evidence.json").write_text(
        json.dumps(kalshi_payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    # ``kalshi_markets_found`` keeps its historical meaning: usable markets that
    # survived the shape filter (== number of polished outcomes). Raw discovery
    # and rejection counts are reported alongside so a zero is explainable.
    record["kalshi_markets_found"] = len(kalshi_payload.get("outcomes") or [])
    record["kalshi_evidence"] = str(asset_dir / "kalshi_polished_evidence.json")
    record["kalshi_series_queried"] = kalshi_diagnostics.get("kalshi_series_queried", [])
    record["kalshi_endpoints_queried"] = kalshi_diagnostics.get("kalshi_endpoints_queried", [])
    record["kalshi_events_found"] = kalshi_diagnostics.get("kalshi_events_found", 0)
    record["kalshi_markets_discovered"] = kalshi_diagnostics.get("kalshi_markets_found", 0)
    record["kalshi_markets_after_shape_filter"] = kalshi_diagnostics.get("kalshi_markets_after_shape_filter", 0)
    record["kalshi_rejection_reasons"] = kalshi_diagnostics.get("kalshi_rejection_reasons", {})

    poly_payload, p_warns, poly_diagnostics = _build_polymarket_payload(
        asset=asset,
        generated_at=generated_at,
        http_get=http_get,
        target_date=target_date,
        timeout_seconds=timeout_seconds,
        sleep=sleep,
    )
    warnings.extend(p_warns)
    (asset_dir / "polymarket_polished_evidence.json").write_text(
        json.dumps(poly_payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    record["polymarket_markets_found"] = len(poly_payload.get("outcomes") or [])
    record["polymarket_evidence"] = str(asset_dir / "polymarket_polished_evidence.json")
    record["polymarket_search_queries_attempted"] = len(poly_diagnostics.get("queries_attempted") or [])
    record["polymarket_events_found"] = poly_diagnostics.get("events_found", 0)
    record["polymarket_candidate_markets_found"] = poly_diagnostics.get("candidate_markets_found", 0)
    record["polymarket_markets_after_shape_filter"] = poly_diagnostics.get("markets_after_shape_filter", 0)
    record["polymarket_rejection_reasons"] = poly_diagnostics.get("rejection_reasons", {})
    record["polymarket_clob_fetch_failures"] = poly_diagnostics.get("clob_fetch_failures", 0)
    record["polymarket_gamma_fallback_used"] = poly_diagnostics.get("gamma_fallback_used", 0)
    record["polymarket_missing_ask_outcomes"] = poly_diagnostics.get("missing_ask_outcomes", 0)
    record["polymarket_clob_error_samples"] = poly_diagnostics.get("clob_error_samples", [])
    record["polymarket_query_strategies"] = sorted(
        {entry.get("strategy") for entry in (poly_diagnostics.get("queries_attempted") or []) if entry.get("strategy")}
    )

    if cdna_evidence_dir is not None:
        copied = _copy_cdna_for_asset(asset=asset, cdna_dir=Path(cdna_evidence_dir), asset_dir=asset_dir)
        record["cdna_files_copied"] = copied
        if copied:
            record["cdna_evidence"] = str(asset_dir / "cdna_polished_evidence.json")
        else:
            warnings.append("cdna_evidence_missing_for_asset")
    else:
        warnings.append("cdna_evidence_missing_for_asset")
    return record


def _copy_cdna_for_asset(*, asset: str, cdna_dir: Path, asset_dir: Path) -> int:
    if not cdna_dir.exists():
        return 0
    candidates: list[Path] = []
    if cdna_dir.is_file() and cdna_dir.suffix == ".json":
        candidates.append(cdna_dir)
    else:
        asset_l = asset.lower()
        for child in cdna_dir.rglob("*.json"):
            name = child.name.lower()
            if "cdna" not in name and "crypto.com" not in name:
                continue
            text_l = child.parent.name.lower()
            if asset_l in name or asset_l in text_l or asset_l in (child.parent.parent.name.lower() if child.parent.parent else ""):
                candidates.append(child)
    if not candidates:
        return 0
    chosen = candidates[0]
    target = asset_dir / "cdna_polished_evidence.json"
    shutil.copyfile(chosen, target)
    return 1


# ---------------------------------------------------------------------------- #
# Kalshi public-read-only fetch                                                #
# ---------------------------------------------------------------------------- #


def _build_kalshi_payload(
    *,
    asset: str,
    generated_at: datetime,
    http_get: HttpGet,
    target_date: str | None,
    timeout_seconds: float,
    sleep: Sleep,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Discover Kalshi daily crypto threshold markets across candidate series.

    Returns ``(payload, warnings, diagnostics)``. ``diagnostics`` records every
    series and endpoint queried plus per-stage counts so a K=0 outcome is always
    explainable in the markdown (which series/endpoints were hit, how many raw
    markets were found, and why each was dropped).
    """
    warnings: list[str] = []
    diagnostics: dict[str, Any] = {
        "asset": asset,
        "kalshi_series_queried": [],
        "kalshi_endpoints_queried": [],
        "kalshi_events_found": 0,
        "kalshi_markets_found": 0,
        "kalshi_markets_after_shape_filter": 0,
        "kalshi_rejection_reasons": {},
    }
    payload: dict[str, Any] = _new_polished_payload(
        platform="Kalshi",
        asset=asset,
        target_date=target_date,
        price_source=KALSHI_SETTLEMENT_INDEX_BY_ASSET.get(asset.upper(), ""),
        settlement_source=KALSHI_SETTLEMENT_INDEX_BY_ASSET.get(asset.upper(), ""),
        target_time=None,
        timezone_label="ET",
        generated_at=generated_at,
    )
    series_candidates = KALSHI_DAILY_SERIES_CANDIDATES_BY_ASSET.get(asset.upper()) or ()
    if not series_candidates:
        warnings.append(f"kalshi_series_ticker_unknown_for_{asset}")
        return payload, warnings, diagnostics

    acceptable_dates = _acceptable_target_dates(target_date, generated_at)
    markets_by_ticker: dict[str, dict[str, Any]] = {}
    event_tickers: set[str] = set()
    rejection_reasons: dict[str, int] = {}

    # Query each candidate series by both the markets and events endpoints. The
    # canonical series is first; fallback series are only queried while we still
    # have zero markets, so a rename does not silently zero the scan.
    for series in series_candidates:
        if markets_by_ticker and series != series_candidates[0]:
            break
        diagnostics["kalshi_series_queried"].append(series)
        markets_url = f"{KALSHI_PUBLIC_BASE_URL}/markets?{urlencode({'series_ticker': series, 'status': 'open', 'limit': 1000})}"
        diagnostics["kalshi_endpoints_queried"].append(markets_url)
        markets_response, markets_err = _http_get_with_retry(
            http_get, markets_url, timeout_seconds, attempts=EVENTS_FETCH_ATTEMPTS, sleep=sleep
        )
        if markets_err:
            warnings.append(f"kalshi_markets_fetch_failed:{series}:{markets_err}")
        for market in _kalshi_markets_from_response(markets_response):
            ticker = market.get("ticker")
            if ticker:
                markets_by_ticker.setdefault(str(ticker), market)
                ev = market.get("event_ticker")
                if ev:
                    event_tickers.add(str(ev))

        # Events endpoint: confirms event-level discovery and surfaces nested
        # markets the markets endpoint may have paged past.
        events_url = f"{KALSHI_PUBLIC_BASE_URL}/events?{urlencode({'series_ticker': series, 'status': 'open', 'with_nested_markets': 'true', 'limit': 200})}"
        diagnostics["kalshi_endpoints_queried"].append(events_url)
        events_response, events_err = _http_get_with_retry(
            http_get, events_url, timeout_seconds, attempts=EVENTS_FETCH_ATTEMPTS, sleep=sleep
        )
        if events_err:
            warnings.append(f"kalshi_events_fetch_failed:{series}:{events_err}")
        for event in _kalshi_events_from_response(events_response):
            ev_ticker = event.get("event_ticker") or event.get("ticker")
            if ev_ticker:
                event_tickers.add(str(ev_ticker))
            for market in event.get("markets") or []:
                if isinstance(market, dict) and market.get("ticker"):
                    market.setdefault("event_ticker", ev_ticker)
                    markets_by_ticker.setdefault(str(market["ticker"]), market)

    diagnostics["kalshi_markets_found"] = len(markets_by_ticker)

    outcomes: list[dict[str, Any]] = []
    payload_target_time: str | None = None
    for ticker, market in markets_by_ticker.items():
        event_ticker = market.get("event_ticker") or ""
        target_date_extracted, target_time_extracted = _parse_kalshi_event_ticker(event_ticker)
        if target_date_extracted is None:
            target_date_extracted, target_time_extracted = _parse_kalshi_event_ticker(ticker)
        if target_date_extracted and acceptable_dates and target_date_extracted not in acceptable_dates:
            _bump(rejection_reasons, "target_date_mismatch")
            continue
        threshold = _kalshi_threshold(market)
        if threshold is None and _kalshi_threshold_from_text(market) is None:
            _bump(rejection_reasons, "threshold_not_parseable")
            continue
        if payload_target_time is None:
            payload_target_time = target_time_extracted
        orderbook, ob_err = _http_get_with_retry(
            http_get,
            f"{KALSHI_PUBLIC_BASE_URL}/markets/{ticker}/orderbook",
            timeout_seconds,
            attempts=EVENTS_FETCH_ATTEMPTS,
            sleep=sleep,
        )
        if ob_err:
            warnings.append(f"kalshi_orderbook_fetch_failed:{ticker}:{ob_err}")
        outcomes.append(
            _kalshi_market_to_outcome(
                market=market,
                orderbook=orderbook,
                target_date=target_date_extracted,
                target_time=target_time_extracted,
                generated_at=generated_at,
            )
        )

    diagnostics["kalshi_events_found"] = len(event_tickers)
    diagnostics["kalshi_markets_after_shape_filter"] = len(outcomes)
    diagnostics["kalshi_rejection_reasons"] = rejection_reasons

    payload["outcomes"] = outcomes
    if payload_target_time:
        payload["target_time"] = payload_target_time
    if not outcomes:
        if diagnostics["kalshi_markets_found"] == 0:
            warnings.append("kalshi_no_markets_found")
        else:
            warnings.append("kalshi_markets_found_but_all_filtered")
    return payload, warnings, diagnostics


def _kalshi_markets_from_response(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, dict):
        markets = response.get("markets") or []
    elif isinstance(response, list):
        markets = response
    else:
        markets = []
    return [m for m in markets if isinstance(m, dict)]


def _kalshi_events_from_response(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, dict):
        events = response.get("events") or []
    elif isinstance(response, list):
        events = response
    else:
        events = []
    return [e for e in events if isinstance(e, dict)]


def _kalshi_threshold_from_text(market: dict[str, Any]) -> float | None:
    text = " ".join(
        str(market.get(key) or "")
        for key in ("yes_sub_title", "subtitle", "title", "ticker")
    ).lower()
    return _polymarket_threshold(text)


def _acceptable_target_dates(target_date: str | None, generated_at: datetime) -> set[str]:
    """Dates a discovered market may legitimately settle on.

    When the operator passes an explicit ``--date`` (already an ET calendar
    date) we honour it. Otherwise we accept both the ET *and* UTC calendar date
    of ``generated_at`` so an event whose ET target date trails the UTC clock
    (late-evening ET / after-midnight UTC) is not wrongly dropped.
    """
    if target_date:
        return {target_date}
    return {_et_date_iso(generated_at), generated_at.astimezone(timezone.utc).date().isoformat()}


def _et_date_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo  # noqa: WPS433 (optional stdlib dependency)

        return dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:  # noqa: BLE001 (tzdata may be unavailable on some hosts)
        # Approximate US Eastern: EDT (UTC-4) Mar–Nov, EST (UTC-5) otherwise.
        offset = -4 if 3 <= dt.month <= 11 else -5
        return (dt.astimezone(timezone.utc) + timedelta(hours=offset)).date().isoformat()


def _parse_kalshi_event_ticker(event_ticker: str) -> tuple[str | None, str | None]:
    match = _KALSHI_EVENT_RE.search(event_ticker or "")
    if not match:
        return None, None
    yy = int(match.group("yy"))
    mon = _MONTHS.get(match.group("mon"))
    dd = int(match.group("dd"))
    hh = int(match.group("hh"))
    if mon is None:
        return None, None
    target_date = f"20{yy:02d}-{mon:02d}-{dd:02d}"
    target_time = f"{hh:02d}:00 ET"
    return target_date, target_time


def _kalshi_market_to_outcome(
    *,
    market: dict[str, Any],
    orderbook: dict[str, Any] | None,
    target_date: str | None,
    target_time: str | None,
    generated_at: datetime,
) -> dict[str, Any]:
    # Kalshi orderbook returns YES bids and NO bids (not asks). The YES ask is
    # derived from the top NO bid (1 - no_bid). Likewise NO ask from top YES bid.
    top_yes_bid, top_no_bid = _kalshi_top_bids(orderbook)
    yes_ask_price = None
    yes_ask_size = None
    no_ask_price = None
    no_ask_size = None
    if top_no_bid is not None:
        yes_ask_price = round(1.0 - top_no_bid["price"], 6) if top_no_bid["price"] is not None else None
        yes_ask_size = top_no_bid["size"]
    if top_yes_bid is not None:
        no_ask_price = round(1.0 - top_yes_bid["price"], 6) if top_yes_bid["price"] is not None else None
        no_ask_size = top_yes_bid["size"]
    threshold = _kalshi_threshold(market)
    return {
        "market_title": market.get("title") or market.get("subtitle"),
        "market_ticker": market.get("ticker"),
        "platform_market_id": market.get("ticker"),
        "outcome_name": market.get("yes_sub_title") or market.get("subtitle"),
        "strike_floor": threshold,
        "yes_ask": yes_ask_price,
        "yes_ask_size": yes_ask_size,
        "yes_bid": top_yes_bid["price"] if top_yes_bid else None,
        "yes_bid_size": top_yes_bid["size"] if top_yes_bid else None,
        "no_ask": no_ask_price,
        "no_ask_size": no_ask_size,
        "no_bid": top_no_bid["price"] if top_no_bid else None,
        "no_bid_size": top_no_bid["size"] if top_no_bid else None,
        "depth_status": "top_of_book_only",
        "quote_source": "kalshi_public_orderbook",
        "quote_timestamp": generated_at.isoformat(),
        "target_date": target_date,
        "target_time": target_time,
    }


def _kalshi_top_bids(orderbook: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return the top YES bid and top NO bid from a Kalshi public orderbook.

    Kalshi public orderbook ships two shapes:
      - the legacy v2 ``orderbook`` envelope with ``yes`` / ``no`` arrays in cents;
      - the current ``orderbook_fp`` envelope with ``yes_dollars`` / ``no_dollars``
        arrays of (price_str_in_dollars, size_str) tuples sorted ascending.
    Both ship bid sides only; the YES ask is derived by the caller via 1 - top NO bid.
    """
    if not isinstance(orderbook, dict):
        return None, None
    fp = orderbook.get("orderbook_fp") or {}
    if fp:
        yes_bids = fp.get("yes_dollars") or fp.get("yes") or []
        no_bids = fp.get("no_dollars") or fp.get("no") or []
    else:
        ob = orderbook.get("orderbook") or orderbook
        yes_bids = ob.get("yes_dollars") or ob.get("yes") or []
        no_bids = ob.get("no_dollars") or ob.get("no") or []
    return _kalshi_top_level(yes_bids), _kalshi_top_level(no_bids)


def _kalshi_top_level(levels: Any) -> dict[str, Any] | None:
    if not isinstance(levels, list) or not levels:
        return None
    top = levels[-1]
    if isinstance(top, list | tuple) and len(top) >= 2:
        price = _to_float(top[0])
        size = _to_float(top[1])
    elif isinstance(top, dict):
        price = _to_float(top.get("price"))
        size = _to_float(top.get("size"))
    else:
        return None
    if price is not None and price > 1.0:
        price = price / 100.0
    return {"price": price, "size": size}


def _kalshi_threshold(market: dict[str, Any]) -> float | None:
    for key in ("strike_floor", "strike", "cap_strike", "floor_strike"):
        value = _to_float(market.get(key))
        if value is not None:
            return value
    return None


# ---------------------------------------------------------------------------- #
# Polymarket public-read-only fetch                                            #
# ---------------------------------------------------------------------------- #


def _build_polymarket_payload(
    *,
    asset: str,
    generated_at: datetime,
    http_get: HttpGet,
    target_date: str | None,
    timeout_seconds: float,
    sleep: Sleep,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Discover Polymarket daily crypto threshold markets.

    Returns ``(payload, warnings, diagnostics)``. ``diagnostics`` is a structured
    record of every search strategy tried — so the markdown can explain a zero
    result instead of silently dropping rows.
    """
    warnings: list[str] = []
    diagnostics: dict[str, Any] = {
        "asset": asset,
        "queries_attempted": [],
        "events_found": 0,
        "candidate_markets_found": 0,
        "markets_after_shape_filter": 0,
        "rejection_reasons": {},
        "clob_fetch_failures": 0,
        "gamma_fallback_used": 0,
        "missing_ask_outcomes": 0,
        "clob_error_samples": [],
    }
    payload: dict[str, Any] = _new_polished_payload(
        platform="Polymarket",
        asset=asset,
        target_date=target_date,
        price_source="Binance",
        settlement_source=POLYMARKET_SETTLEMENT_SOURCE_BY_ASSET.get(asset.upper(), ""),
        target_time=None,  # parsed from event title per market
        timezone_label="ET",
        generated_at=generated_at,
    )
    keywords = POLYMARKET_KEYWORDS_BY_ASSET.get(asset.upper())
    slug_name = POLYMARKET_SLUG_NAME_BY_ASSET.get(asset.upper())
    if not keywords or not slug_name:
        warnings.append(f"polymarket_keywords_unknown_for_{asset}")
        return payload, warnings, diagnostics

    seen_event_ids: set[str] = set()
    events: list[dict[str, Any]] = []

    # Strategy 1: direct slug fetch per (date, hour) combination.
    for slug in _polymarket_candidate_slugs(slug_name=slug_name, target_date=target_date):
        url = f"{POLYMARKET_GAMMA_BASE_URL}/events?{urlencode({'slug': slug})}"
        diagnostics["queries_attempted"].append({"strategy": "direct_slug", "url": url})
        items = _polymarket_fetch_events(url, http_get=http_get, timeout_seconds=timeout_seconds, warnings=warnings, sleep=sleep)
        for ev in items:
            ev_id = str(ev.get("id") or ev.get("slug") or "")
            if ev_id and ev_id not in seen_event_ids:
                seen_event_ids.add(ev_id)
                events.append(ev)

    # Strategy 2: events search by keyword (returns broad results — we filter).
    for kw in keywords:
        url = f"{POLYMARKET_GAMMA_BASE_URL}/events?{urlencode({'active': 'true', 'closed': 'false', 'search': kw, 'limit': 30})}"
        diagnostics["queries_attempted"].append({"strategy": "search_keyword", "url": url})
        items = _polymarket_fetch_events(url, http_get=http_get, timeout_seconds=timeout_seconds, warnings=warnings, sleep=sleep)
        for ev in items:
            if not _event_looks_like_threshold(ev, slug_name=slug_name):
                continue
            ev_id = str(ev.get("id") or ev.get("slug") or "")
            if ev_id and ev_id not in seen_event_ids:
                seen_event_ids.add(ev_id)
                events.append(ev)

    # Strategy 3: crypto tag — slow path, only if nothing yet found.
    if not events:
        url = f"{POLYMARKET_GAMMA_BASE_URL}/events?{urlencode({'active': 'true', 'closed': 'false', 'tag_slug': 'crypto', 'limit': 100})}"
        diagnostics["queries_attempted"].append({"strategy": "crypto_tag", "url": url})
        items = _polymarket_fetch_events(url, http_get=http_get, timeout_seconds=timeout_seconds, warnings=warnings, sleep=sleep)
        for ev in items:
            if not _event_looks_like_threshold(ev, slug_name=slug_name):
                continue
            ev_id = str(ev.get("id") or ev.get("slug") or "")
            if ev_id and ev_id not in seen_event_ids:
                seen_event_ids.add(ev_id)
                events.append(ev)

    # Strategy 4: seed slugs (last-resort hints from prior batches).
    if not events:
        for seed_slug in POLYMARKET_SEED_SLUGS:
            if slug_name not in seed_slug:
                continue
            url = f"{POLYMARKET_GAMMA_BASE_URL}/events?{urlencode({'slug': seed_slug})}"
            diagnostics["queries_attempted"].append({"strategy": "seed_slug", "url": url})
            items = _polymarket_fetch_events(url, http_get=http_get, timeout_seconds=timeout_seconds, warnings=warnings, sleep=sleep)
            for ev in items:
                ev_id = str(ev.get("id") or ev.get("slug") or "")
                if ev_id and ev_id not in seen_event_ids:
                    seen_event_ids.add(ev_id)
                    events.append(ev)

    diagnostics["events_found"] = len(events)
    rejection_reasons: dict[str, int] = {}

    outcomes: list[dict[str, Any]] = []
    payload_target_time: str | None = None
    payload_rules_text: str | None = None
    for ev in events:
        ev_title = str(ev.get("title") or "")
        ev_description = str(ev.get("description") or "")
        target_time_label = _parse_polymarket_target_time(ev_title) or _parse_polymarket_target_time(ev_description)
        ev_markets = ev.get("markets") or []
        for market in ev_markets:
            if not isinstance(market, dict):
                continue
            diagnostics["candidate_markets_found"] += 1
            mk_question = str(market.get("question") or "")
            mk_slug = str(market.get("slug") or "")
            mk_text = (mk_question + " " + mk_slug).lower()
            if not _polymarket_question_matches_asset(mk_text, keywords):
                _bump(rejection_reasons, "asset_keyword_not_in_question")
                continue
            if "above" not in mk_text and "below" not in mk_text and "over" not in mk_text and "under" not in mk_text:
                _bump(rejection_reasons, "no_threshold_comparator_in_question")
                continue
            threshold = _polymarket_threshold_from_market(market)
            if threshold is None:
                _bump(rejection_reasons, "threshold_not_parseable")
                continue
            # NB: we deliberately do not filter on market["closed"] — Polymarket
            # uses that field for daily settlement of the prior session, not as
            # an "is this market tradeable" signal. CLOB book emptiness is the
            # real liveness gate (handled by the downstream scout via
            # missing_quote / stale_or_missing_quote).
            if target_date and not _polymarket_event_matches_date(ev_title, mk_text, target_date):
                _bump(rejection_reasons, "target_date_mismatch")
                continue
            yes_token_id, no_token_id = _polymarket_token_ids(market)
            yes_book = None
            no_book = None
            yes_err: str | None = None
            no_err: str | None = None
            if yes_token_id:
                yes_book, yes_err = _http_get_with_retry(
                    http_get,
                    f"{POLYMARKET_CLOB_BASE_URL}/book?{urlencode({'token_id': yes_token_id})}",
                    timeout_seconds,
                    attempts=CLOB_FETCH_ATTEMPTS,
                    sleep=sleep,
                )
            if no_token_id:
                no_book, no_err = _http_get_with_retry(
                    http_get,
                    f"{POLYMARKET_CLOB_BASE_URL}/book?{urlencode({'token_id': no_token_id})}",
                    timeout_seconds,
                    attempts=CLOB_FETCH_ATTEMPTS,
                    sleep=sleep,
                )
            for token_id, err in ((yes_token_id, yes_err), (no_token_id, no_err)):
                if err:
                    diagnostics["clob_fetch_failures"] += 1
                    # Log the real exception type *and* message, not a bare
                    # RuntimeError, so a CLOB outage is diagnosable from the report.
                    warnings.append(f"polymarket_clob_fetch_failed:{token_id}:{err}")
                    if len(diagnostics["clob_error_samples"]) < 5:
                        diagnostics["clob_error_samples"].append(err)
            outcome, outcome_warnings = _polymarket_market_to_outcome(
                market=market,
                yes_book=yes_book,
                no_book=no_book,
                yes_book_failed=bool(yes_err),
                no_book_failed=bool(no_err),
                threshold=threshold,
                generated_at=generated_at,
                target_date=target_date,
                target_time=target_time_label,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                rules_text=ev_description,
            )
            warnings.extend(outcome_warnings)
            if "gamma_top_of_book_fallback_used" in (outcome.get("quote_diagnostics") or []):
                diagnostics["gamma_fallback_used"] += 1
            if "missing_ask" in (outcome.get("blockers_remaining") or []):
                diagnostics["missing_ask_outcomes"] += 1
            if outcome.get("target_time") is None:
                outcome.setdefault("blockers_remaining", []).append("target_time_missing")
            outcomes.append(outcome)
            if payload_target_time is None and target_time_label:
                payload_target_time = target_time_label
            if payload_rules_text is None and ev_description:
                payload_rules_text = ev_description

    diagnostics["markets_after_shape_filter"] = len(outcomes)
    diagnostics["rejection_reasons"] = rejection_reasons

    payload["outcomes"] = outcomes
    if payload_target_time:
        payload["target_time"] = payload_target_time
    if payload_rules_text:
        payload["rules_text"] = payload_rules_text
    if not outcomes:
        warnings.append("polymarket_no_markets_found")
    return payload, warnings, diagnostics


# ---- helpers -------------------------------------------------------------- #


def _polymarket_candidate_slugs(*, slug_name: str, target_date: str | None) -> list[str]:
    """Build per-(date, hour) candidate slugs of the form
    ``<slug_name>-above-on-<month>-<day>-<year>-<hour><ampm>-et``.

    Falls back to seed slugs when no target_date is supplied so the discovery
    has at least one shot at a canonical event.
    """
    slugs: list[str] = []
    if not target_date:
        return list(POLYMARKET_SEED_SLUGS)
    try:
        d = datetime.fromisoformat(target_date)
    except ValueError:
        return list(POLYMARKET_SEED_SLUGS)
    month_short = _MONTH_SHORT_NAMES[d.month - 1]
    day = d.day
    year = d.year
    for hour, ampm in POLYMARKET_HOUR_SLOTS:
        slug = f"{slug_name}-above-on-{month_short}-{day}-{year}-{hour}{ampm}-et"
        slugs.append(slug)
    return slugs


def _polymarket_fetch_events(
    url: str,
    *,
    http_get: HttpGet,
    timeout_seconds: float,
    warnings: list[str],
    sleep: Sleep,
) -> list[dict[str, Any]]:
    resp, err = _http_get_with_retry(
        http_get, url, timeout_seconds, attempts=EVENTS_FETCH_ATTEMPTS, sleep=sleep
    )
    if err:
        warnings.append(f"polymarket_events_fetch_failed:{err}")
        return []
    if isinstance(resp, list):
        items = resp
    elif isinstance(resp, dict):
        items = resp.get("events") or resp.get("data") or []
    else:
        items = []
    return [ev for ev in items if isinstance(ev, dict)]


def _event_looks_like_threshold(event: dict[str, Any], *, slug_name: str) -> bool:
    title = str(event.get("title") or "").lower()
    slug = str(event.get("slug") or "").lower()
    if slug_name not in title and slug_name not in slug:
        return False
    return ("above" in title or "above" in slug or "over" in title or "over" in slug)


def _polymarket_question_matches_asset(text: str, keywords: list[str]) -> bool:
    if not text:
        return False
    for kw in keywords:
        if kw.lower() in text:
            return True
    return False


def _polymarket_threshold_from_market(market: dict[str, Any]) -> float | None:
    # Polymarket's groupItemThreshold is the ordinal index of the threshold
    # within the event group (1, 2, 3, ...), not the dollar threshold itself.
    # Always parse the dollar threshold from the question text or slug.
    text = " ".join(str(market.get(k) or "") for k in ("question", "slug"))
    parsed = _polymarket_threshold(text.lower())
    if parsed is not None:
        return parsed
    return _to_float(market.get("groupItemThreshold"))


def _parse_polymarket_target_time(text: str) -> str | None:
    if not text:
        return None
    match = _POLY_TARGET_TIME_RE.search(text)
    if not match:
        return None
    hour = int(match.group("hour"))
    ampm = match.group("ampm").lower()
    tz = (match.group("tz") or "ET").upper()
    if tz in ("EST", "EDT"):
        tz = "ET"
    return f"{hour:02d}:00 {ampm.upper()} {tz}".replace(" PM ", "PM ").replace(" AM ", "AM ").strip()


def _polymarket_event_matches_date(event_title: str, market_text: str, target_date: str) -> bool:
    text = f"{event_title} {market_text}".lower()
    try:
        d = datetime.fromisoformat(target_date)
    except ValueError:
        return True
    month_short = d.strftime("%b").lower()
    month_long = d.strftime("%B").lower()
    day = d.day
    year = d.year
    has_month = month_short in text or month_long in text
    has_day = re.search(rf"\b{day}\b", text) is not None
    has_year = str(year) in text
    if has_month and has_day:
        return True
    if target_date in text:
        return True
    if has_year and has_month:
        return True
    return False


def _bump(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _polymarket_token_ids(market: dict[str, Any]) -> tuple[str | None, str | None]:
    token_ids_raw = market.get("clobTokenIds")
    if isinstance(token_ids_raw, str):
        try:
            token_ids = json.loads(token_ids_raw)
        except json.JSONDecodeError:
            token_ids = []
    else:
        token_ids = token_ids_raw or []
    if isinstance(token_ids, list) and len(token_ids) >= 2:
        return str(token_ids[0]), str(token_ids[1])
    if isinstance(token_ids, list) and len(token_ids) == 1:
        return str(token_ids[0]), None
    return None, None


def _polymarket_threshold(text_blob: str) -> float | None:
    # Match the dollar amount that follows an "above"/"over"/"below"/"under"
    # comparator so we don't accidentally pick up the day number from "May 29".
    match = _POLY_THRESHOLD_RE.search(text_blob)
    if match:
        value = float(match.group("amount").replace(",", ""))
        if match.group("k"):
            if value < 1000:
                value *= 1000
        return value
    # Fallback: look for a dollar-prefixed amount anywhere.
    match = re.search(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?|[0-9]+(?:\.\d+)?)\s*(k?)", text_blob)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    if match.group(2) == "k" and value < 1000:
        value *= 1000
    return value


def _polymarket_question_matches_date(text_blob: str, target_date: str) -> bool:
    try:
        d = datetime.fromisoformat(target_date)
    except ValueError:
        return True
    month_short = d.strftime("%b").lower()
    month_long = d.strftime("%B").lower()
    day = d.day
    year = d.year
    if month_short in text_blob or month_long in text_blob:
        if str(day) in text_blob:
            return True
        if f"{month_short} {day}" in text_blob:
            return True
    if target_date in text_blob:
        return True
    if str(year) in text_blob and (month_short in text_blob or month_long in text_blob):
        return True
    return False


def _polymarket_market_to_outcome(
    *,
    market: dict[str, Any],
    yes_book: dict[str, Any] | None,
    no_book: dict[str, Any] | None,
    yes_book_failed: bool,
    no_book_failed: bool,
    threshold: float,
    generated_at: datetime,
    target_date: str | None,
    target_time: str | None,
    yes_token_id: str | None,
    no_token_id: str | None,
    rules_text: str | None,
) -> tuple[dict[str, Any], list[str]]:
    """Build a polished outcome, falling back to Gamma top-of-book on CLOB failure.

    Returns ``(outcome, warnings)``. When the CLOB book is unavailable for a side
    but Gamma exposes ``bestBid``/``bestAsk`` (or ``outcomePrices``), that quote
    is used as *limited-depth* top-of-book and the outcome is tagged so the
    operator size cap is applied downstream. If neither CLOB nor Gamma yields an
    ask on either side, ``missing_ask`` is recorded as a hard blocker.
    """
    warnings: list[str] = []
    quote_diagnostics: list[str] = []
    blockers_remaining: list[str] = []

    yes_top_ask, yes_top_bid = _polymarket_top_of_book(yes_book)
    no_top_ask, no_top_bid = _polymarket_top_of_book(no_book)

    yes_ask = yes_top_ask.get("price") if yes_top_ask else None
    yes_ask_size = yes_top_ask.get("size") if yes_top_ask else None
    yes_bid = yes_top_bid.get("price") if yes_top_bid else None
    yes_bid_size = yes_top_bid.get("size") if yes_top_bid else None
    no_ask = no_top_ask.get("price") if no_top_ask else None
    no_ask_size = no_top_ask.get("size") if no_top_ask else None
    no_bid = no_top_bid.get("price") if no_top_bid else None
    no_bid_size = no_top_bid.get("size") if no_top_bid else None

    clob_failed = bool(yes_book_failed or no_book_failed)
    gamma = _gamma_top_of_book(market)
    used_fallback = False
    if yes_ask is None and gamma.get("yes_ask") is not None:
        yes_ask = gamma["yes_ask"]
        if yes_bid is None:
            yes_bid = gamma.get("yes_bid")
        used_fallback = True
    if no_ask is None and gamma.get("no_ask") is not None:
        no_ask = gamma["no_ask"]
        if no_bid is None:
            no_bid = gamma.get("no_bid")
        used_fallback = True

    depth_status = "top_of_book_only"
    quote_source = "polymarket_public_clob"
    if clob_failed:
        quote_diagnostics.append("polymarket_clob_fetch_failed")
    if used_fallback:
        quote_diagnostics.append("gamma_top_of_book_fallback_used")
        quote_diagnostics.append("limited_depth_operator_size_cap_applied")
        depth_status = "gamma_top_of_book_fallback"
        quote_source = "polymarket_gamma_top_of_book"
        warnings.append(
            f"polymarket_gamma_top_of_book_fallback_used:{yes_token_id or no_token_id or 'unknown'}"
        )
    if yes_ask is None and no_ask is None:
        blockers_remaining.append("missing_ask")
        warnings.append(
            f"polymarket_missing_ask:{yes_token_id or no_token_id or 'unknown'}"
        )

    outcome: dict[str, Any] = {
        "market_title": market.get("question") or market.get("title"),
        "market_ticker": market.get("slug"),
        "platform_market_id": str(market.get("id") or market.get("conditionId") or ""),
        "condition_id": market.get("conditionId"),
        "token_id_yes": yes_token_id,
        "token_id_no": no_token_id,
        "strike_floor": threshold,
        "yes_ask": yes_ask,
        "yes_ask_size": yes_ask_size,
        "yes_bid": yes_bid,
        "yes_bid_size": yes_bid_size,
        "no_ask": no_ask,
        "no_ask_size": no_ask_size,
        "no_bid": no_bid,
        "no_bid_size": no_bid_size,
        "depth_status": depth_status,
        "quote_source": quote_source,
        "quote_timestamp": generated_at.isoformat(),
        "target_date": target_date,
        "target_time": target_time,
        "rules_text": rules_text,
    }
    if quote_diagnostics:
        outcome["quote_diagnostics"] = quote_diagnostics
    if blockers_remaining:
        outcome["blockers_remaining"] = blockers_remaining
    return outcome, warnings


def _gamma_top_of_book(market: dict[str, Any]) -> dict[str, float | None]:
    """Derive a limited-depth top-of-book from Gamma market fields.

    Polymarket's Gamma ``market`` object exposes ``bestBid``/``bestAsk`` for the
    YES (first) token. The NO side is the binary complement. ``outcomePrices`` is
    used as a last-resort indicative price when no best bid/ask is published.
    """
    result: dict[str, float | None] = {"yes_ask": None, "yes_bid": None, "no_ask": None, "no_bid": None}
    best_ask = _to_float(market.get("bestAsk"))
    best_bid = _to_float(market.get("bestBid"))
    if best_ask is None and best_bid is None:
        parsed = _parse_outcome_prices(market.get("outcomePrices"))
        if parsed is not None:
            best_ask = parsed
            best_bid = parsed
    if best_ask is not None:
        result["yes_ask"] = round(best_ask, 6)
        result["no_bid"] = round(1.0 - best_ask, 6)
    if best_bid is not None:
        result["yes_bid"] = round(best_bid, 6)
        result["no_ask"] = round(1.0 - best_bid, 6)
    return result


def _parse_outcome_prices(value: Any) -> float | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if isinstance(value, list) and value:
        return _to_float(value[0])
    return None


def _polymarket_top_of_book(book: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(book, dict):
        return None, None
    asks = book.get("asks") or []
    bids = book.get("bids") or []
    top_ask = None
    if isinstance(asks, list) and asks:
        first_ask = asks[0]
        if isinstance(first_ask, dict):
            top_ask = {
                "price": _to_float(first_ask.get("price")),
                "size": _to_float(first_ask.get("size")),
            }
    top_bid = None
    if isinstance(bids, list) and bids:
        first_bid = bids[0]
        if isinstance(first_bid, dict):
            top_bid = {
                "price": _to_float(first_bid.get("price")),
                "size": _to_float(first_bid.get("size")),
            }
    return top_ask, top_bid


# ---------------------------------------------------------------------------- #
# Polished schema scaffolding                                                  #
# ---------------------------------------------------------------------------- #


def _new_polished_payload(
    *,
    platform: str,
    asset: str,
    target_date: str | None,
    price_source: str,
    settlement_source: str,
    target_time: str | None,
    timezone_label: str,
    generated_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_kind": POLISHED_SCHEMA,
        "schema_version": 1,
        "diagnostic_only": True,
        "platform": platform,
        "category": "crypto",
        "market_family": f"{asset.lower()}_price_threshold",
        "asset": asset.upper(),
        "market_shape": "point_in_time_threshold",
        "comparator": "above",
        "target_date": target_date,
        "target_time": target_time,
        "timezone": timezone_label,
        "price_source": price_source,
        "settlement_source": settlement_source,
        "collected_at": generated_at.isoformat(),
        "outcomes": [],
    }


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------- #
# Public read-only fetch with retry/backoff                                    #
# ---------------------------------------------------------------------------- #


def _http_get_with_retry(
    http_get: HttpGet,
    url: str,
    timeout_seconds: float,
    *,
    attempts: int,
    sleep: Sleep,
) -> tuple[Any, str | None]:
    """Call ``http_get`` up to ``attempts`` times with linear backoff.

    Returns ``(response, None)`` on success or ``(None, detail)`` on exhaustion,
    where ``detail`` is a human-readable ``"<ExceptionType>:<message>"`` string
    (never just ``RuntimeError``). Read-only; never mutates remote state.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return http_get(url, timeout_seconds), None
        except Exception as exc:  # noqa: BLE001 (diagnostic: any failure is captured + reported)
            last_exc = exc
            # A 404 (and most 4xx) is not transient — a settled/closed market has
            # no CLOB book, so retrying just wastes time. Only back off and retry
            # transient errors (timeouts, connection drops, 5xx, 429 rate limits).
            if attempt >= attempts or not _is_retryable(exc):
                break
            sleep(RETRY_BACKOFF_SECONDS * attempt)
    return None, _describe_http_error(last_exc)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, HttpGetError) and exc.status is not None:
        if exc.status in (408, 425, 429, 500, 502, 503, 504):
            return True
        if 400 <= exc.status < 500:
            return False
        return True
    # Unknown / connection / timeout errors (no HTTP status) — worth one retry.
    return True


def _describe_http_error(exc: BaseException | None) -> str:
    if exc is None:
        return "unknown_error"
    if isinstance(exc, HttpGetError):
        label = exc.original_type
        if exc.status is not None:
            label = f"{label}:HTTP{exc.status}"
        return f"{label}:{_truncate(exc.message or str(exc), 200)}"
    return f"{type(exc).__name__}:{_truncate(str(exc), 200)}"


def _truncate(text: str, limit: int) -> str:
    text = str(text).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ---------------------------------------------------------------------------- #
# Default HTTP via urllib (public, read-only, GET only)                        #
# ---------------------------------------------------------------------------- #


def _default_http_get(url: str, timeout_seconds: float) -> Any:
    request = Request(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 (public read-only)
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        raise HttpGetError(url=url, original=exc, status=getattr(exc, "code", None), message=str(exc)) from exc
    except (URLError, TimeoutError) as exc:
        raise HttpGetError(url=url, original=exc, message=str(exc)) from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None
