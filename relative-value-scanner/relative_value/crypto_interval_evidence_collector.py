"""Public-read-only intraday crypto interval evidence collector.

Discovers live/upcoming intraday crypto interval / point-in-time threshold
contracts on Kalshi (intraday hourly series) and Polymarket (hourly slug events)
via public, unauthenticated GET endpoints, and loads optional saved CDNA
evidence. Every market is normalized into an *interval typed-key row* keyed on
the exact UTC settlement instant (Kalshi ``close_time`` / Polymarket ``endDate``)
so the downstream check can match by instant rather than by calendar date.

Strict scope (identical to the daily collector):
  - Public market-data GET only. No order placement/cancellation, no
    account/auth/session/balance/position/private-key/wallet/signing code. No
    browser automation, no headless browser, no Cloudflare bypass, no
    proxy/VPN/Tor.
  - CDNA is *saved-evidence-only*. It is never fetched over the network.
  - The HTTP getter and ``sleep`` are injectable so tests can stub them.

Reuses the hardened public-fetch discipline from
``daily_crypto_evidence_collector`` (retry/backoff, real-error capture, CLOB book
with Gamma top-of-book fallback, 404-not-retried) so there is one network code
path for both daily and interval scans.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from relative_value.daily_crypto_evidence_collector import (
    CLOB_FETCH_ATTEMPTS,
    EVENTS_FETCH_ATTEMPTS,
    POLYMARKET_CLOB_BASE_URL,
    POLYMARKET_GAMMA_BASE_URL,
    HttpGet,
    Sleep,
    _bump,
    _default_http_get,
    _http_get_with_retry,
    _kalshi_market_to_outcome,
    _kalshi_threshold,
    _polymarket_market_to_outcome,
    _polymarket_threshold,
    _polymarket_token_ids,
    _to_float,
)
from relative_value.daily_crypto_evidence_collector import (
    KALSHI_PUBLIC_BASE_URL,
)


SCHEMA_KIND = "crypto_interval_live_evidence_v1"
SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 10.0
# Safety valve: a Kalshi hourly event can list ~190 bucket markets. We fetch
# orderbooks for the whole family only at peer instants, capped per asset.
MAX_KALSHI_ORDERBOOK_FETCHES_PER_ASSET = 400

# Intraday (hourly) crypto series come first; the daily series is a secondary
# candidate so a daily market whose settlement instant falls inside the
# look-ahead window is still picked up.
KALSHI_INTERVAL_SERIES_BY_ASSET: dict[str, tuple[str, ...]] = {
    "BTC": ("KXBTC", "KXBTCD"),
    "ETH": ("KXETH", "KXETHD"),
    "SOL": ("KXSOL", "KXSOLD"),
    "XRP": ("KXXRP", "KXXRPD"),
    "DOGE": ("KXDOGE", "KXDOGED"),
}

POLYMARKET_SLUG_NAME_BY_ASSET: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "xrp",
    "DOGE": "dogecoin",
}

POLYMARKET_KEYWORDS_BY_ASSET: dict[str, list[str]] = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth"],
    "SOL": ["solana", "sol"],
    "XRP": ["xrp", "ripple"],
    "DOGE": ["dogecoin", "doge"],
}

KALSHI_SETTLEMENT_INDEX_BY_ASSET: dict[str, str] = {
    "BTC": "CF Benchmarks Bitcoin Real-Time Index (BRTI)",
    "ETH": "CF Benchmarks Ethereum Real-Time Index (ETHUSD_RTI)",
    "SOL": "CF Benchmarks SOLUSD_RTI",
    "XRP": "CF Benchmarks XRPUSD_RTI",
    "DOGE": "CF Benchmarks DOGEUSD_RTI",
}

POLYMARKET_SETTLEMENT_SOURCE_BY_ASSET: dict[str, str] = {
    "BTC": "Binance BTC/USDT",
    "ETH": "Binance ETH/USDT",
    "SOL": "Binance SOL/USDT",
    "XRP": "Binance XRP/USDT",
    "DOGE": "Binance DOGE/USDT",
}

_MONTH_SHORT_NAMES = (
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
)

SHAPE_POINT_IN_TIME = "point_in_time_threshold"
SHAPE_UP_DOWN = "up_down"
SHAPE_RANGE_BUCKET = "range_bucket"
SHAPE_DEADLINE_TOUCH = "deadline_touch"
SHAPE_UNKNOWN = "unknown"


# ---------------------------------------------------------------------------- #
# Public entry point                                                           #
# ---------------------------------------------------------------------------- #


def write_crypto_interval_live_evidence(
    *,
    assets: list[str],
    output_root: Path | None,
    lookahead_hours: float,
    generated_at: datetime | None = None,
    http_get: HttpGet | None = None,
    cdna_evidence_dir: Path | None = None,
    sleep: Sleep | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    window_end = generated + timedelta(hours=float(lookahead_hours))
    getter: HttpGet = http_get or _default_http_get
    sleeper: Sleep = sleep or _noop_sleep
    cdna_dir = Path(cdna_evidence_dir) if cdna_evidence_dir is not None else None

    per_asset: list[dict[str, Any]] = []
    for raw_asset in assets:
        asset = str(raw_asset).strip().upper()
        if not asset:
            continue
        # Discover Polymarket + CDNA first (they carry the strikes/instants we care
        # about), then fetch Kalshi orderbooks ONLY for strikes/instants that have a
        # peer. Kalshi hourly events list 50-188 strikes each; fetching every
        # orderbook would be thousands of calls per asset and impolite to the venue.
        poly_rows, poly_diag = _collect_polymarket(
            asset=asset, now=generated, lookahead_hours=lookahead_hours, window_end=window_end,
            http_get=getter, sleep=sleeper, timeout_seconds=timeout_seconds,
        )
        cdna_rows, cdna_diag = _collect_cdna(asset=asset, cdna_dir=cdna_dir, now=generated, window_end=window_end)
        peer_rows = [r for r in (poly_rows + cdna_rows) if r.get("market_shape") == SHAPE_POINT_IN_TIME]
        target_strikes = {r["threshold_or_strike"] for r in peer_rows if r.get("threshold_or_strike") is not None}
        target_instants = {r["target_instant_utc"] for r in peer_rows if r.get("target_instant_utc")}
        kalshi_rows, kalshi_diag = _collect_kalshi(
            asset=asset, now=generated, window_end=window_end, http_get=getter, sleep=sleeper,
            timeout_seconds=timeout_seconds, target_strikes=target_strikes, target_instants=target_instants,
        )
        per_asset.append(
            {
                "asset": asset,
                "kalshi_rows": kalshi_rows,
                "polymarket_rows": poly_rows,
                "cdna_rows": cdna_rows,
                "kalshi_diagnostics": kalshi_diag,
                "polymarket_diagnostics": poly_diag,
                "cdna_diagnostics": cdna_diag,
            }
        )

    summary = {
        "schema_kind": SCHEMA_KIND,
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated.isoformat(),
        "target_window_start_utc": generated.isoformat(),
        "target_window_end_utc": window_end.isoformat(),
        "lookahead_hours": float(lookahead_hours),
        "diagnostic_only": True,
        "public_read_only": True,
        "per_asset": per_asset,
        "safety": {
            "diagnostic_only": True,
            "public_read_only": True,
            "cdna_network_fetch_attempted": False,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
            "browser_automation_added": False,
        },
    }

    if output_root is not None:
        output_root = Path(output_root)
        output_root.mkdir(parents=True, exist_ok=True)
        for record in per_asset:
            asset_dir = output_root / str(record["asset"]).lower()
            asset_dir.mkdir(parents=True, exist_ok=True)
            (asset_dir / "interval_typed_keys.json").write_text(
                json.dumps(record, indent=2, sort_keys=True), encoding="utf-8"
            )
        (output_root / "collection_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
        )
    return summary


# ---------------------------------------------------------------------------- #
# Kalshi intraday discovery                                                    #
# ---------------------------------------------------------------------------- #


def _collect_kalshi(
    *,
    asset: str,
    now: datetime,
    window_end: datetime,
    http_get: HttpGet,
    sleep: Sleep,
    timeout_seconds: float,
    target_strikes: set[float] | None = None,
    target_instants: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostics: dict[str, Any] = {
        "series_queried": [],
        "events_found": 0,
        "markets_found": 0,
        "markets_in_window": 0,
        "rows_kept": 0,
        "orderbooks_fetched": 0,
        "rejection_reasons": {},
        "warnings": [],
    }
    target_strikes = target_strikes if target_strikes is not None else set()
    target_instants = target_instants if target_instants is not None else set()
    series_candidates = KALSHI_INTERVAL_SERIES_BY_ASSET.get(asset) or ()
    if not series_candidates:
        diagnostics["warnings"].append(f"kalshi_series_unknown_for_{asset}")
        return [], diagnostics

    rejection = diagnostics["rejection_reasons"]
    markets_by_ticker: dict[str, dict[str, Any]] = {}
    event_tickers: set[str] = set()
    for series in series_candidates:
        if markets_by_ticker and series != series_candidates[0]:
            break
        diagnostics["series_queried"].append(series)
        url = (
            f"{KALSHI_PUBLIC_BASE_URL}/events?series_ticker={series}"
            "&status=open&with_nested_markets=true&limit=200"
        )
        resp, err = _http_get_with_retry(http_get, url, timeout_seconds, attempts=EVENTS_FETCH_ATTEMPTS, sleep=sleep)
        if err:
            diagnostics["warnings"].append(f"kalshi_events_fetch_failed:{series}:{err}")
        for event in _events_from(resp):
            ev_ticker = event.get("event_ticker") or event.get("ticker")
            if ev_ticker:
                event_tickers.add(str(ev_ticker))
            for market in event.get("markets") or []:
                if isinstance(market, dict) and market.get("ticker"):
                    market.setdefault("event_ticker", ev_ticker)
                    markets_by_ticker.setdefault(str(market["ticker"]), market)

    diagnostics["events_found"] = len(event_tickers)
    diagnostics["markets_found"] = len(markets_by_ticker)

    rows: list[dict[str, Any]] = []
    for ticker, market in markets_by_ticker.items():
        instant = _parse_iso_instant(market.get("close_time"))
        if instant is None:
            _bump(rejection, "no_close_time")
            continue
        if not (now <= instant <= window_end):
            _bump(rejection, "out_of_lookahead_window")
            continue
        diagnostics["markets_in_window"] += 1
        shape, comparator = _kalshi_shape_and_comparator(market)
        strike = _kalshi_threshold(market)
        if shape == SHAPE_POINT_IN_TIME and strike is None:
            _bump(rejection, "threshold_not_parseable")
            continue
        canonical_strike = _round_strike(strike) if strike is not None else None
        instant_iso = instant.astimezone(timezone.utc).isoformat()
        # Fetch an orderbook for EVERY Kalshi market (threshold or bucket) at an
        # instant that a Polymarket/CDNA peer also settles on: the synthetic-basket
        # lane needs YES asks on the whole bucket family, not just exact-strike
        # matches. A per-asset cap bounds the work and is polite to the venue.
        want_quote = instant_iso in target_instants and diagnostics["orderbooks_fetched"] < MAX_KALSHI_ORDERBOOK_FETCHES_PER_ASSET
        if want_quote:
            orderbook, ob_err = _http_get_with_retry(
                http_get, f"{KALSHI_PUBLIC_BASE_URL}/markets/{ticker}/orderbook", timeout_seconds,
                attempts=EVENTS_FETCH_ATTEMPTS, sleep=sleep,
            )
            diagnostics["orderbooks_fetched"] += 1
            if ob_err:
                diagnostics["warnings"].append(f"kalshi_orderbook_fetch_failed:{ticker}:{ob_err}")
            outcome = _kalshi_market_to_outcome(
                market=market, orderbook=orderbook,
                target_date=instant.date().isoformat(),
                target_time=_et_clock_label(instant), generated_at=now,
            )
            quote = _kalshi_quote(outcome)
        else:
            quote = {
                "yes_ask": None, "no_ask": None, "yes_ask_size": None, "no_ask_size": None,
                "depth_status": "not_fetched_no_peer", "quote_timestamp": now.isoformat(),
                "quote_diagnostics": [], "blockers_remaining": [],
            }
        rows.append(
            _typed_key(
                platform="kalshi", asset=asset, market_shape=shape, comparator=comparator,
                threshold_or_strike=canonical_strike, instant=instant,
                price_source=KALSHI_SETTLEMENT_INDEX_BY_ASSET.get(asset, ""),
                settlement_source=KALSHI_SETTLEMENT_INDEX_BY_ASSET.get(asset, ""),
                market_id_or_ticker=ticker,
                condition_id=None,
                token_ids={},
                contract_id=None,
                quote=quote,
                bucket_floor=_to_float(market.get("floor_strike")),
                bucket_cap=_to_float(market.get("cap_strike")),
            )
        )
    diagnostics["rows_kept"] = len(rows)
    return rows, diagnostics


def _kalshi_shape_and_comparator(market: dict[str, Any]) -> tuple[str, str]:
    ticker = str(market.get("ticker") or "").upper()
    sub = str(market.get("yes_sub_title") or market.get("subtitle") or "").lower()
    if "-B" in ticker or "between" in sub or " to $" in sub or " and $" in sub:
        return SHAPE_RANGE_BUCKET, "range"
    if "or below" in sub or "below" in sub or "or less" in sub or "under" in sub:
        return SHAPE_POINT_IN_TIME, "below"
    if "or above" in sub or "above" in sub or "or more" in sub or "over" in sub:
        return SHAPE_POINT_IN_TIME, "above"
    if "-T" in ticker:
        # KXBTC `-T` threshold markets are "$X or below" by convention.
        return SHAPE_POINT_IN_TIME, "below"
    return SHAPE_UNKNOWN, "unknown"


def _kalshi_quote(outcome: dict[str, Any]) -> dict[str, Any]:
    return {
        "yes_ask": outcome.get("yes_ask"),
        "yes_ask_size": outcome.get("yes_ask_size"),
        "no_ask": outcome.get("no_ask"),
        "no_ask_size": outcome.get("no_ask_size"),
        # Bids are carried so downstream can derive a complement ask from an
        # executable bid (no_ask = 1 - yes_bid) when the direct ask is missing.
        "yes_bid": outcome.get("yes_bid"),
        "yes_bid_size": outcome.get("yes_bid_size"),
        "no_bid": outcome.get("no_bid"),
        "no_bid_size": outcome.get("no_bid_size"),
        "depth_status": outcome.get("depth_status"),
        "quote_timestamp": outcome.get("quote_timestamp"),
        "quote_diagnostics": [],
        "blockers_remaining": [],
    }


# ---------------------------------------------------------------------------- #
# Polymarket intraday discovery                                                #
# ---------------------------------------------------------------------------- #


def _collect_polymarket(
    *,
    asset: str,
    now: datetime,
    lookahead_hours: float,
    window_end: datetime,
    http_get: HttpGet,
    sleep: Sleep,
    timeout_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostics: dict[str, Any] = {
        "slugs_queried": [],
        "queries_attempted": 0,
        "events_found": 0,
        "markets_found": 0,
        "markets_in_window": 0,
        "rows_kept": 0,
        "clob_fetch_failures": 0,
        "gamma_fallback_used": 0,
        "missing_ask_outcomes": 0,
        "clob_error_samples": [],
        "rejection_reasons": {},
        "warnings": [],
    }
    name = POLYMARKET_SLUG_NAME_BY_ASSET.get(asset)
    keywords = POLYMARKET_KEYWORDS_BY_ASSET.get(asset)
    if not name or not keywords:
        diagnostics["warnings"].append(f"polymarket_slug_name_unknown_for_{asset}")
        return [], diagnostics

    rejection = diagnostics["rejection_reasons"]
    seen_event_ids: set[str] = set()
    events: list[dict[str, Any]] = []

    # Strategy 1: hourly threshold slugs for each clock hour in the window.
    for slug in _polymarket_hourly_slugs(name=name, now=now, lookahead_hours=lookahead_hours):
        diagnostics["slugs_queried"].append(slug)
        url = f"{POLYMARKET_GAMMA_BASE_URL}/events?slug={slug}"
        diagnostics["queries_attempted"] += 1
        for ev in _fetch_events(http_get, url, timeout_seconds, sleep, diagnostics):
            ev_id = str(ev.get("id") or ev.get("slug") or "")
            if ev_id and ev_id not in seen_event_ids:
                seen_event_ids.add(ev_id)
                events.append(ev)

    # Strategy 2: keyword fallback (filtered to threshold-ish events).
    for kw in keywords:
        url = f"{POLYMARKET_GAMMA_BASE_URL}/events?active=true&closed=false&search={kw}&limit=40"
        diagnostics["queries_attempted"] += 1
        for ev in _fetch_events(http_get, url, timeout_seconds, sleep, diagnostics):
            slug = str(ev.get("slug") or "").lower()
            title = str(ev.get("title") or "").lower()
            if name not in slug and name not in title:
                continue
            ev_id = str(ev.get("id") or ev.get("slug") or "")
            if ev_id and ev_id not in seen_event_ids:
                seen_event_ids.add(ev_id)
                events.append(ev)

    diagnostics["events_found"] = len(events)

    rows: list[dict[str, Any]] = []
    for ev in events:
        ev_end = ev.get("endDate") or ev.get("end_date_iso")
        ev_start = ev.get("startDate") or ev.get("start_date_iso") or ev.get("gameStartTime")
        ev_desc = str(ev.get("description") or "")
        for market in ev.get("markets") or []:
            if not isinstance(market, dict):
                continue
            diagnostics["markets_found"] += 1
            question = str(market.get("question") or "")
            slug = str(market.get("slug") or "")
            text = f"{question} {slug}"
            shape, comparator = _polymarket_shape_and_comparator(text)
            instant = _parse_iso_instant(market.get("endDate") or market.get("end_date_iso") or ev_end)
            if instant is None:
                _bump(rejection, "no_end_date")
                continue
            if not (now <= instant <= window_end):
                _bump(rejection, "out_of_lookahead_window")
                continue
            diagnostics["markets_in_window"] += 1
            threshold = _polymarket_threshold(text.lower()) if shape == SHAPE_POINT_IN_TIME else None
            if shape == SHAPE_POINT_IN_TIME and threshold is None:
                _bump(rejection, "threshold_not_parseable")
                continue
            yes_token_id, no_token_id = _polymarket_token_ids(market)
            yes_book = no_book = None
            yes_err = no_err = None
            if yes_token_id:
                yes_book, yes_err = _http_get_with_retry(
                    http_get, f"{POLYMARKET_CLOB_BASE_URL}/book?token_id={yes_token_id}", timeout_seconds,
                    attempts=CLOB_FETCH_ATTEMPTS, sleep=sleep,
                )
            if no_token_id:
                no_book, no_err = _http_get_with_retry(
                    http_get, f"{POLYMARKET_CLOB_BASE_URL}/book?token_id={no_token_id}", timeout_seconds,
                    attempts=CLOB_FETCH_ATTEMPTS, sleep=sleep,
                )
            for token_id, err in ((yes_token_id, yes_err), (no_token_id, no_err)):
                if err:
                    diagnostics["clob_fetch_failures"] += 1
                    if len(diagnostics["clob_error_samples"]) < 5:
                        diagnostics["clob_error_samples"].append(err)
            outcome, outcome_warnings = _polymarket_market_to_outcome(
                market=market, yes_book=yes_book, no_book=no_book,
                yes_book_failed=bool(yes_err), no_book_failed=bool(no_err),
                threshold=threshold if threshold is not None else 0.0,
                generated_at=now, target_date=instant.date().isoformat(),
                target_time=_et_clock_label(instant),
                yes_token_id=yes_token_id, no_token_id=no_token_id, rules_text=ev_desc,
            )
            diagnostics["warnings"].extend(outcome_warnings)
            if "gamma_top_of_book_fallback_used" in (outcome.get("quote_diagnostics") or []):
                diagnostics["gamma_fallback_used"] += 1
            if "missing_ask" in (outcome.get("blockers_remaining") or []):
                diagnostics["missing_ask_outcomes"] += 1
            reference_start, interval_len = _interval_window(
                shape=shape,
                instant=instant,
                start_iso=market.get("startDate") or ev_start,
                text=text,
            )
            rows.append(
                _typed_key(
                    platform="polymarket", asset=asset, market_shape=shape, comparator=comparator,
                    threshold_or_strike=_round_strike(threshold), instant=instant,
                    price_source=POLYMARKET_SETTLEMENT_SOURCE_BY_ASSET.get(asset, "Binance"),
                    settlement_source=POLYMARKET_SETTLEMENT_SOURCE_BY_ASSET.get(asset, "Binance"),
                    market_id_or_ticker=slug or str(market.get("id") or ""),
                    condition_id=market.get("conditionId"),
                    token_ids={"yes": yes_token_id, "no": no_token_id},
                    contract_id=None,
                    quote=_polymarket_quote(outcome),
                    rules_text=ev_desc,
                    reference_start_utc=reference_start,
                    interval_length_seconds=interval_len,
                )
            )
    diagnostics["rows_kept"] = len(rows)
    return rows, diagnostics


def _polymarket_shape_and_comparator(text: str) -> tuple[str, str]:
    lowered = text.lower()
    if "up or down" in lowered or "-up-or-down-" in lowered:
        return SHAPE_UP_DOWN, "up"
    if "reach" in lowered or "hit" in lowered or "touch" in lowered or "all-time high" in lowered or "all time high" in lowered:
        return SHAPE_DEADLINE_TOUCH, "touch"
    if "above" in lowered or "-above-" in lowered or "over" in lowered or "greater" in lowered:
        return SHAPE_POINT_IN_TIME, "above"
    if "below" in lowered or "under" in lowered or "less than" in lowered:
        return SHAPE_POINT_IN_TIME, "below"
    return SHAPE_UNKNOWN, "unknown"


def _polymarket_quote(outcome: dict[str, Any]) -> dict[str, Any]:
    return {
        "yes_ask": outcome.get("yes_ask"),
        "yes_ask_size": outcome.get("yes_ask_size"),
        "no_ask": outcome.get("no_ask"),
        "no_ask_size": outcome.get("no_ask_size"),
        # Bids carried for complement-ask derivation when a direct ask is missing.
        "yes_bid": outcome.get("yes_bid"),
        "yes_bid_size": outcome.get("yes_bid_size"),
        "no_bid": outcome.get("no_bid"),
        "no_bid_size": outcome.get("no_bid_size"),
        "depth_status": outcome.get("depth_status"),
        "quote_timestamp": outcome.get("quote_timestamp"),
        "quote_diagnostics": list(outcome.get("quote_diagnostics") or []),
        "blockers_remaining": list(outcome.get("blockers_remaining") or []),
    }


def _polymarket_hourly_slugs(*, name: str, now: datetime, lookahead_hours: float) -> list[str]:
    """``{name}-above-on-{month}-{day}-{year}-{hour}{ampm}-et`` for every clock
    hour boundary in ``[now, now + lookahead_hours]`` expressed in US Eastern."""
    slugs: list[str] = []
    seen: set[str] = set()
    hours = max(1, int(round(float(lookahead_hours)))) + 1
    start_et = _to_et(now).replace(minute=0, second=0, microsecond=0)
    for offset in range(hours + 1):
        et = start_et + timedelta(hours=offset)
        month = _MONTH_SHORT_NAMES[et.month - 1]
        hour12 = et.hour % 12 or 12
        ampm = "am" if et.hour < 12 else "pm"
        slug = f"{name}-above-on-{month}-{et.day}-{et.year}-{hour12}{ampm}-et"
        if slug not in seen:
            seen.add(slug)
            slugs.append(slug)
    return slugs


def _fetch_events(
    http_get: HttpGet,
    url: str,
    timeout_seconds: float,
    sleep: Sleep,
    diagnostics: dict[str, Any],
) -> list[dict[str, Any]]:
    resp, err = _http_get_with_retry(http_get, url, timeout_seconds, attempts=EVENTS_FETCH_ATTEMPTS, sleep=sleep)
    if err:
        diagnostics["warnings"].append(f"polymarket_events_fetch_failed:{err}")
        return []
    return _events_from(resp)


# ---------------------------------------------------------------------------- #
# CDNA saved-evidence load (never fetched)                                     #
# ---------------------------------------------------------------------------- #


def _collect_cdna(
    *,
    asset: str,
    cdna_dir: Path | None,
    now: datetime,
    window_end: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostics: dict[str, Any] = {
        "supplied": cdna_dir is not None,
        "rows_loaded": 0,
        "rows_in_window": 0,
        "rows_kept": 0,
        "rejection_reasons": {},
        "warnings": [],
    }
    if cdna_dir is None:
        diagnostics["warnings"].append("cdna_not_supplied")
        return [], diagnostics
    if not cdna_dir.exists():
        diagnostics["warnings"].append("cdna_evidence_dir_missing")
        return [], diagnostics

    # Local import keeps the saved-CDNA parser out of the hot path when CDNA is
    # not requested, and makes the network-free guarantee explicit.
    from relative_value.crypto_com_predict_cdna_saved_page_parser import (  # noqa: WPS433
        build_crypto_com_predict_cdna_research_snapshot,
    )

    snapshot = build_crypto_com_predict_cdna_research_snapshot(fixture_dir=cdna_dir, generated_at=now)
    rejection = diagnostics["rejection_reasons"]
    rows: list[dict[str, Any]] = []
    for raw in snapshot.get("rows") or []:
        if str(raw.get("asset") or "").upper() != asset:
            continue
        diagnostics["rows_loaded"] += 1
        instant = _cdna_instant(raw)
        if instant is None:
            _bump(rejection, "cdna_no_target_instant")
            # Still record as a discovered (un-matchable) row for diagnostics.
            rows.append(_cdna_typed_key(raw, asset=asset, instant=None, now=now))
            continue
        if not (now - timedelta(hours=1) <= instant <= window_end):
            _bump(rejection, "out_of_lookahead_window")
            continue
        diagnostics["rows_in_window"] += 1
        rows.append(_cdna_typed_key(raw, asset=asset, instant=instant, now=now))
    diagnostics["rows_kept"] = len(rows)
    return rows, diagnostics


def _cdna_typed_key(raw: dict[str, Any], *, asset: str, instant: datetime | None, now: datetime) -> dict[str, Any]:
    shape = _map_cdna_shape(raw.get("market_shape") or raw.get("shape_class"))
    comparator = _normalize_comparator(raw.get("comparator") or raw.get("threshold_operator"))
    rules = " ".join(
        str(raw.get(k) or "")
        for k in ("title", "settlement_rule_text", "source_methodology_text", "settlement_window")
    )
    # CDNA Rule 14.x "over/under strike at expiration value" contracts are
    # point-in-time at the target instant regardless of their 5m/20m/2h cadence —
    # unless the evidence clearly says up/down or touch.
    if shape in (SHAPE_UNKNOWN, SHAPE_POINT_IN_TIME) and comparator in ("above", "below"):
        if "up" in rules.lower() and "down" in rules.lower():
            shape = SHAPE_UP_DOWN
        else:
            shape = SHAPE_POINT_IN_TIME
    timeframe_text = " ".join(
        str(raw.get(k) or "") for k in ("title", "timeframe", "measurement_window", "market_type")
    )
    _, interval_len = _interval_window(shape=shape, instant=instant, start_iso=None, text=timeframe_text)
    yes_price = _to_float(raw.get("yes_display_price"))
    no_price = _to_float(raw.get("no_display_price"))
    quote = {
        "yes_ask": yes_price,
        "no_ask": no_price,
        "yes_ask_size": None,
        "no_ask_size": None,
        "depth_status": "display_price_only",
        "quote_timestamp": raw.get("captured_at") or raw.get("captured_at_utc") or now.isoformat(),
        "quote_diagnostics": ["cdna_display_price_only"],
        "blockers_remaining": [] if (yes_price is not None or no_price is not None) else ["missing_ask"],
    }
    return _typed_key(
        platform="cdna", asset=asset, market_shape=shape, comparator=comparator,
        threshold_or_strike=_round_strike(_to_float(raw.get("strike") or raw.get("threshold_value"))),
        instant=instant,
        interval_length_seconds=interval_len,
        price_source=str(raw.get("price_source_index") or "CDNA"),
        settlement_source=str(raw.get("settlement_source") or raw.get("price_source_index") or "CDNA"),
        market_id_or_ticker=str(raw.get("market_id") or raw.get("title") or ""),
        condition_id=None,
        token_ids={},
        contract_id=str(raw.get("market_id") or raw.get("platform_market_ref") or ""),
        quote=quote,
    )


def _cdna_instant(raw: dict[str, Any]) -> datetime | None:
    direct = _parse_iso_instant(raw.get("resolution_reference_time") or raw.get("target_instant_utc") or raw.get("deadline_or_expiry"))
    if direct is not None:
        return direct
    date_text = raw.get("measurement_date") or raw.get("target_date")
    time_text = raw.get("measurement_time")
    if not date_text:
        return None
    return _compose_et_instant(str(date_text), str(time_text) if time_text else None)


def _map_cdna_shape(value: Any) -> str:
    text = str(value or "").upper()
    if "POINT_IN_TIME" in text:
        return SHAPE_POINT_IN_TIME
    if "RANGE" in text:
        return SHAPE_RANGE_BUCKET
    if "DEADLINE" in text or "TOUCH" in text or "TIMEFRAME" in text or "HIGH" in text:
        return SHAPE_DEADLINE_TOUCH
    if "UP_DOWN" in text or "UP/DOWN" in text:
        return SHAPE_UP_DOWN
    return SHAPE_UNKNOWN


# ---------------------------------------------------------------------------- #
# Typed-key scaffolding + parsing helpers                                      #
# ---------------------------------------------------------------------------- #


def _typed_key(
    *,
    platform: str,
    asset: str,
    market_shape: str,
    comparator: str,
    threshold_or_strike: float | None,
    instant: datetime | None,
    price_source: str,
    settlement_source: str,
    market_id_or_ticker: str | None,
    condition_id: Any,
    token_ids: dict[str, Any],
    contract_id: str | None,
    quote: dict[str, Any],
    rules_text: str | None = None,
    reference_start_utc: str | None = None,
    interval_length_seconds: int | None = None,
    bucket_floor: float | None = None,
    bucket_cap: float | None = None,
    payoff_observation_type: str | None = None,
) -> dict[str, Any]:
    obs = payoff_observation_type or _observation_type(market_shape)
    return {
        "asset": asset,
        "platform": platform,
        "market_shape": market_shape,
        "payoff_observation_type": obs,
        "comparator": comparator,
        "threshold_or_strike": threshold_or_strike,
        "bucket_floor": bucket_floor,
        "bucket_cap": bucket_cap,
        "reference_start_utc": reference_start_utc,
        "target_instant_utc": instant.astimezone(timezone.utc).isoformat() if instant else None,
        "target_time_local": _et_clock_label(instant) if instant else None,
        "timezone": "America/New_York",
        "interval_length_seconds": interval_length_seconds,
        "alignment_group": _alignment_group(instant),
        "endpoint_alignment_status": "endpoint_anchored" if instant else "no_target_instant",
        "price_source": price_source,
        "settlement_source": settlement_source,
        "market_id_or_ticker": market_id_or_ticker,
        "condition_id": condition_id,
        "token_ids": token_ids or {},
        "contract_id": contract_id,
        "quote": quote,
        "rules_text": rules_text,
    }


# Maps the coarse ``market_shape`` to the payoff-observation taxonomy that drives
# cross-interval compatibility. A "20m" or "2h" CDNA threshold and a "15m" Kalshi
# threshold are both ``point_in_time_at_target`` and may match at a shared instant
# regardless of interval length; up/down is ``interval_start_to_end_change`` and
# must additionally share ``reference_start_utc``.
_OBSERVATION_BY_SHAPE: dict[str, str] = {
    SHAPE_POINT_IN_TIME: "point_in_time_at_target",
    SHAPE_RANGE_BUCKET: "range_at_target",
    SHAPE_UP_DOWN: "interval_start_to_end_change",
    SHAPE_DEADLINE_TOUCH: "touch_before_deadline",
    SHAPE_UNKNOWN: "unknown",
}


def _observation_type(market_shape: str) -> str:
    return _OBSERVATION_BY_SHAPE.get(market_shape, "unknown")


def _alignment_group(instant: datetime | None) -> str | None:
    """Clock-hour:minute endpoint label used to bucket harmonic alignment, e.g.
    ``2026-05-30T05:00``. Contracts of different interval length that settle on the
    same endpoint share an alignment group."""
    if instant is None:
        return None
    et = instant.astimezone(timezone.utc)
    return et.strftime("%Y-%m-%dT%H:%M")


def _events_from(resp: Any) -> list[dict[str, Any]]:
    if isinstance(resp, dict):
        items = resp.get("events") or resp.get("data") or []
    elif isinstance(resp, list):
        items = resp
    else:
        items = []
    return [item for item in items if isinstance(item, dict)]


def _parse_iso_instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _round_strike(value: float | None) -> float | None:
    if value is None:
        return None
    # Honor the Kalshi ``.99`` display offset so "$X.99 or below" and Polymarket
    # "above $X" collapse to the same canonical strike.
    if abs((value + 0.01) - round(value + 0.01)) < 1e-6 and str(value).endswith(".99"):
        value = value + 0.01
    return round(float(value), 2)


def _normalize_comparator(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"above", "greater_than", "greater than", "or above", "at_or_above", ">", ">="}:
        return "above"
    if text in {"below", "less_than", "less than", "or below", "at_or_below", "<", "<="}:
        return "below"
    if text in {"up", "down", "range", "touch"}:
        return text
    return text or "unknown"


def _to_et(dt: datetime):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo  # noqa: WPS433

        return dt.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001
        offset = -4 if 3 <= dt.month <= 11 else -5
        return dt.astimezone(timezone.utc) + timedelta(hours=offset)


def _et_clock_label(instant: datetime | None) -> str | None:
    if instant is None:
        return None
    et = _to_et(instant)
    hour12 = et.hour % 12 or 12
    ampm = "AM" if et.hour < 12 else "PM"
    return f"{hour12}:{et.minute:02d} {ampm} ET"


def _compose_et_instant(date_text: str, time_text: str | None) -> datetime | None:
    import re

    date_match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", date_text)
    if not date_match:
        return None
    year, month, day = (int(date_match.group(i)) for i in (1, 2, 3))
    hour = 0
    minute = 0
    if time_text:
        tmatch = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", time_text.lower())
        if tmatch:
            hour = int(tmatch.group(1))
            minute = int(tmatch.group(2) or 0)
            ampm = tmatch.group(3)
            if ampm == "pm" and hour < 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
    try:
        from zoneinfo import ZoneInfo  # noqa: WPS433

        naive = datetime(year, month, day, hour, minute)
        return naive.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        offset = -4 if 3 <= month <= 11 else -5
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc) - timedelta(hours=offset)


def _interval_window(
    *, shape: str, instant: datetime | None, start_iso: Any, text: str
) -> tuple[str | None, int | None]:
    """Return ``(reference_start_utc, interval_length_seconds)``.

    For ``up_down`` we need an actual reference start (interval bets are defined
    start->end). For point-in-time / range the interval length is only an
    informational contract-cadence descriptor (e.g. CDNA "20m"), never used for
    matching.
    """
    start_dt = _parse_iso_instant(start_iso)
    duration = _parse_duration_seconds(text)
    if shape == SHAPE_UP_DOWN:
        if start_dt is not None and instant is not None:
            return start_dt.isoformat(), int((instant - start_dt).total_seconds())
        if duration is not None and instant is not None:
            return (instant - timedelta(seconds=duration)).isoformat(), duration
        return None, duration
    if duration is not None:
        return None, duration
    if start_dt is not None and instant is not None:
        return None, int((instant - start_dt).total_seconds())
    return None, None


def _parse_duration_seconds(text: str | None) -> int | None:
    if not text:
        return None
    import re  # noqa: WPS433

    match = re.search(
        r"\b(\d{1,3})\s*-?\s*(minutes|minute|mins|min|hours|hour|hrs|hr|m|h)\b",
        str(text).lower(),
    )
    if not match:
        return None
    n = int(match.group(1))
    unit = match.group(2)
    return n * 60 if unit.startswith("m") else n * 3600


def _noop_sleep(_seconds: float) -> None:
    return None
