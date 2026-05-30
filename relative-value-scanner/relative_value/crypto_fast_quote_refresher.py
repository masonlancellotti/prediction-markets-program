"""Public/read-only fast quote refresher for the crypto fast-path trigger.

Refreshes ONLY the watched legs of an ``active_crypto_candidate_universe.json`` via
public, unauthenticated GETs:
  - Kalshi public orderbook for the exact watched ticker (top-of-book);
  - Polymarket public CLOB book for the exact watched token id (top-of-book), with
    an explicit Gamma top-of-book fallback only when CLOB is unavailable.

It runs NO structural scan, renders NO markdown, opens NO authenticated/order
endpoints, reads NO ``.env``, and drives NO browser. It reuses the single hardened
public-fetch path (retry/backoff, 404-not-retried) from the daily collector.

Per leg it returns a normalized quote dict; per tick it also reports the refresh
latency and legs_requested / legs_refreshed / legs_missing_quote.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlencode

from relative_value.daily_crypto_evidence_collector import (
    KALSHI_PUBLIC_BASE_URL, POLYMARKET_CLOB_BASE_URL, POLYMARKET_GAMMA_BASE_URL,
    CLOB_FETCH_ATTEMPTS, EVENTS_FETCH_ATTEMPTS,
    _default_http_get, _http_get_with_retry, _to_float,
    _kalshi_top_bids, _polymarket_top_of_book, _gamma_top_of_book,
)

DEFAULT_QUOTE_REFRESH_WORKERS = 8
DEFAULT_MAX_QUOTE_REFRESH_LATENCY_MS = 1500.0
# Conservative per-platform concurrency ceilings for public endpoints (be polite;
# never exceed these regardless of worker count).
_PLATFORM_CONCURRENCY_CAP = {"kalshi": 6, "polymarket": 6, "gamma": 4}


def dedupe_watched_legs(universe: dict[str, Any]) -> list[dict[str, Any]]:
    """Unique watched legs (one fetch target per platform+market/token+side)."""
    watched = universe.get("watched_legs")
    if not watched:
        seen: dict[str, dict[str, Any]] = {}
        for c in universe.get("candidates") or []:
            for leg in c.get("legs") or []:
                seen.setdefault(_leg_key(leg), leg)
        watched = list(seen.values())
    out: dict[str, dict[str, Any]] = {}
    for leg in watched:
        out.setdefault(_leg_key(leg), leg)
    return list(out.values())


def make_public_live_refresher(
    *, http_get: Callable[..., Any] | None = None, sleep: Callable[[float], None] | None = None,
    timeout_seconds: float = 10.0, workers: int = DEFAULT_QUOTE_REFRESH_WORKERS,
    max_latency_ms: float = DEFAULT_MAX_QUOTE_REFRESH_LATENCY_MS,
) -> "PublicLiveRefresher":
    """Public/read-only refresher. Callable per-leg (back-compat) AND exposes
    ``refresh_all`` for bounded-concurrency batch refresh of all watched legs."""
    return PublicLiveRefresher(http_get=http_get, sleep=sleep, timeout_seconds=timeout_seconds,
                               workers=workers, max_latency_ms=max_latency_ms)


class PublicLiveRefresher:
    """Bounded-concurrency public quote refresher.

    ``refresh_all`` deduplicates watched legs to unique fetch targets (one orderbook
    per Kalshi ticker, one book per Polymarket token, per tick), fetches them with a
    bounded thread pool + per-platform concurrency caps + per-request timeout, then
    maps each leg to its quote from the populated per-tick cache (no re-fetch). Irrelevant
    legs are never fetched. Public GETs only — no auth/orders/browser/secrets."""

    def __init__(self, *, http_get=None, sleep=None, timeout_seconds: float = 10.0,
                 workers: int = DEFAULT_QUOTE_REFRESH_WORKERS,
                 max_latency_ms: float = DEFAULT_MAX_QUOTE_REFRESH_LATENCY_MS) -> None:
        self._getter = http_get or _default_http_get
        self._sleeper = sleep or (lambda _s: None)
        self._timeout = float(timeout_seconds)
        self._workers = max(1, int(workers))
        self._max_latency_ms = float(max_latency_ms)
        self.cache: dict[tuple, Any] = {}
        self._sems = {p: threading.Semaphore(min(self._workers, cap)) for p, cap in _PLATFORM_CONCURRENCY_CAP.items()}

    def __call__(self, *, leg: dict[str, Any], now: datetime) -> dict[str, Any]:
        return refresh_one_leg(leg=leg, now=now, http_get=self._getter, sleep=self._sleeper,
                               timeout_seconds=self._timeout, cache=self.cache)

    def refresh_all(self, watched_legs: list[dict[str, Any]], *, now: datetime | None = None,
                    priority_keys: list[str] | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        self.cache.clear()  # each tick is a fresh refresh; keep the cache bounded
        legs = dedupe_watched_legs({"watched_legs": watched_legs})
        ordered = _order_by_priority(legs, priority_keys)

        started = time.perf_counter()
        # Wave 1: primary public books (Kalshi orderbook, Polymarket CLOB), deduped.
        primary, seen = [], set()
        for leg in ordered:
            tgt = self._primary_target(leg, now)
            if tgt and tgt[1] not in seen:
                seen.add(tgt[1])
                primary.append(tgt)
        err1, lat1 = self._fetch_parallel(primary)
        # Wave 2: Gamma top-of-book fallback ONLY for Polymarket tokens with no CLOB ask.
        gamma, gseen = [], set()
        for leg in ordered:
            if str(leg.get("platform") or "").lower() != "polymarket":
                continue
            token = str(leg.get("token_id") or "")
            if not token or token in gseen:
                continue
            book, _berr = self.cache.get(("polymarket", token, now.isoformat()), (None, "missing"))
            top_ask, _tb = _polymarket_top_of_book(book if isinstance(book, dict) else None)
            if top_ask:
                continue
            gseen.add(token)
            gamma.append(("gamma", ("gamma", token, now.isoformat()),
                          f"{POLYMARKET_GAMMA_BASE_URL}/markets?{urlencode({'clob_token_ids': token})}", EVENTS_FETCH_ATTEMPTS))
        err2, lat2 = self._fetch_parallel(gamma)

        quotes = {_leg_key(leg): refresh_one_leg(
            leg=leg, now=now, http_get=self._getter, sleep=self._sleeper,
            timeout_seconds=self._timeout, cache=self.cache) for leg in legs}
        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        refreshed = sum(1 for q in quotes.values() if _to_float(q.get("ask")) is not None)
        per_platform = {}
        for k, v in list(lat1.items()) + list(lat2.items()):
            per_platform[k] = round(per_platform.get(k, 0.0) + v, 3)
        diagnostics = {
            "quote_refresh_started_at": now.isoformat(),
            "quote_refresh_latency_ms": latency_ms,
            "quote_refresh_workers": self._workers,
            "legs_requested": len(legs), "legs_refreshed": refreshed, "legs_missing_quote": len(legs) - refreshed,
            "unique_kalshi_fetches": sum(1 for t in primary if t[0] == "kalshi"),
            "unique_polymarket_fetches": sum(1 for t in primary if t[0] == "polymarket"),
            "unique_gamma_fetches": len(gamma),
            "per_platform_latency_ms": per_platform,
            "rate_limit_or_timeout_errors": int(err1 + err2),
            "max_quote_refresh_latency_ms": self._max_latency_ms,
            "quote_refresh_latency_exceeds_max": bool(latency_ms > self._max_latency_ms),
        }
        return quotes, diagnostics

    def _primary_target(self, leg: dict[str, Any], now: datetime):
        plat = str(leg.get("platform") or "").lower()
        if plat == "kalshi":
            ticker = str(leg.get("market_id_or_ticker") or "")
            if not ticker:
                return None
            return ("kalshi", ("kalshi", ticker, now.isoformat()),
                    f"{KALSHI_PUBLIC_BASE_URL}/markets/{ticker}/orderbook", EVENTS_FETCH_ATTEMPTS)
        if plat == "polymarket":
            token = str(leg.get("token_id") or "")
            if not token:
                return None
            return ("polymarket", ("polymarket", token, now.isoformat()),
                    f"{POLYMARKET_CLOB_BASE_URL}/book?{urlencode({'token_id': token})}", CLOB_FETCH_ATTEMPTS)
        return None  # cdna / unsupported -> no public fetch

    def _fetch_parallel(self, targets) -> tuple[int, dict[str, float]]:
        if not targets:
            return 0, {}
        errors = 0
        latency: dict[str, float] = {}

        def _one(t):
            kind, _cache_key, url, attempts = t
            sem = self._sems.get(kind)
            if sem is not None:
                sem.acquire()
            try:
                s = time.perf_counter()
                resp, err = _http_get_with_retry(self._getter, url, self._timeout, attempts=attempts, sleep=self._sleeper)
                return kind, (resp, err), round((time.perf_counter() - s) * 1000.0, 3)
            finally:
                if sem is not None:
                    sem.release()

        with ThreadPoolExecutor(max_workers=self._workers) as ex:
            futures = [(t[1], ex.submit(_one, t)) for t in targets]
            for cache_key, fut in futures:
                kind, val, dt = fut.result()
                self.cache[cache_key] = val  # written only in the main thread (no race)
                latency[kind] = round(latency.get(kind, 0.0) + dt, 3)
                if val[1]:
                    errors += 1
        return errors, latency


def _order_by_priority(legs: list[dict[str, Any]], priority_keys: list[str] | None) -> list[dict[str, Any]]:
    if not priority_keys:
        return legs
    rank = {k: i for i, k in enumerate(priority_keys)}
    return sorted(legs, key=lambda leg: rank.get(_leg_key(leg), 1_000_000))


def refresh_watched_quotes(
    *, watched_legs: list[dict[str, Any]], http_get: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] | None = None, timeout_seconds: float = 10.0,
    now: datetime | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Refresh every watched leg once. Returns ``(quotes_by_leg_key, diagnostics)``
    with refresh latency and legs_requested/refreshed/missing counts."""
    getter = http_get or _default_http_get
    sleeper = sleep or (lambda _s: None)
    started = now or datetime.now(timezone.utc)
    cache: dict[tuple, Any] = {}
    quotes: dict[str, dict[str, Any]] = {}
    legs = dedupe_watched_legs({"watched_legs": watched_legs})
    for leg in legs:
        quotes[_leg_key(leg)] = refresh_one_leg(
            leg=leg, now=started, http_get=getter, sleep=sleeper, timeout_seconds=timeout_seconds, cache=cache)
    completed = datetime.now(timezone.utc)
    refreshed = sum(1 for q in quotes.values() if _to_float(q.get("ask")) is not None)
    diagnostics = {
        "quote_refresh_started_at": started.isoformat(),
        "quote_refresh_completed_at": completed.isoformat(),
        "quote_refresh_latency_ms": round((completed - started).total_seconds() * 1000.0, 3),
        "legs_requested": len(legs), "legs_refreshed": refreshed, "legs_missing_quote": len(legs) - refreshed,
    }
    return quotes, diagnostics


def refresh_one_leg(
    *, leg: dict[str, Any], now: datetime, http_get: Callable[..., Any], sleep: Callable[[float], None],
    timeout_seconds: float, cache: dict[tuple, Any] | None = None,
) -> dict[str, Any]:
    platform = str(leg.get("platform") or "").lower()
    side = str(leg.get("side") or "").upper()
    base = {
        "platform": leg.get("platform"), "market_id_or_ticker": leg.get("market_id_or_ticker"),
        "token_id": leg.get("token_id"), "side": leg.get("side"),
        "bid": None, "ask": None, "bid_size": None, "ask_size": None,
        "quote_timestamp_utc": now.isoformat(), "quote_timestamp": now.isoformat(), "quote_age_ms": 0.0,
        "source": "", "depth_status": "", "complement_quote_used": False, "hard_blockers": [],
    }
    if platform == "kalshi":
        return _refresh_kalshi(base, leg, side, now, http_get, sleep, timeout_seconds, cache)
    if platform == "polymarket":
        return _refresh_polymarket(base, leg, side, now, http_get, sleep, timeout_seconds, cache)
    if platform == "cdna":
        # CDNA is display-price/fill-first reference only; no live public order book.
        base.update({"ask": _to_float(leg.get("reference_ask")), "source": "cdna_display_reference",
                     "depth_status": "display_price_only", "hard_blockers": ["cdna_manual_fill_first_no_live_quote"]})
        if base["ask"] is None:
            base["hard_blockers"].append("missing_cdna_display_price")
        return base
    base.update({"source": "unsupported_platform", "hard_blockers": [f"unsupported_platform:{platform}"]})
    return base


def _refresh_kalshi(base, leg, side, now, http_get, sleep, timeout, cache) -> dict[str, Any]:
    ticker = str(leg.get("market_id_or_ticker") or "")
    raw, err = _cached_fetch(cache, ("kalshi", ticker, now.isoformat()),
                             http_get, f"{KALSHI_PUBLIC_BASE_URL}/markets/{ticker}/orderbook",
                             timeout, EVENTS_FETCH_ATTEMPTS, sleep)
    base["source"] = "kalshi_public_orderbook"
    base["depth_status"] = "top_of_book_only"
    if err or not isinstance(raw, dict):
        base["hard_blockers"].append("kalshi_orderbook_fetch_failed")
        base["hard_blockers"].append(_missing_label("kalshi", side))
        return base
    top_yes_bid, top_no_bid = _kalshi_top_bids(raw)
    # Kalshi books ship bids only; the side's ASK is the binary complement of the
    # opposite side's executable bid (yes_ask = 1 - top_no_bid). This is the native
    # Kalshi ask, flagged complement_quote_used for the audit trail.
    if side.endswith("YES"):
        base["bid"] = top_yes_bid["price"] if top_yes_bid else None
        base["bid_size"] = top_yes_bid["size"] if top_yes_bid else None
        if top_no_bid and top_no_bid["price"] is not None:
            base["ask"] = round(1.0 - top_no_bid["price"], 6)
            base["ask_size"] = top_no_bid["size"]
            base["complement_quote_used"] = True
    else:
        base["bid"] = top_no_bid["price"] if top_no_bid else None
        base["bid_size"] = top_no_bid["size"] if top_no_bid else None
        if top_yes_bid and top_yes_bid["price"] is not None:
            base["ask"] = round(1.0 - top_yes_bid["price"], 6)
            base["ask_size"] = top_yes_bid["size"]
            base["complement_quote_used"] = True
    if base["ask"] is None:
        base["hard_blockers"].append(_missing_label("kalshi", side))
    return base


def _refresh_polymarket(base, leg, side, now, http_get, sleep, timeout, cache) -> dict[str, Any]:
    token_id = str(leg.get("token_id") or "")
    base["source"] = "polymarket_clob"
    base["depth_status"] = "top_of_book_only"
    if not token_id:
        base["hard_blockers"].append("missing_polymarket_token_id")
        base["hard_blockers"].append(_missing_label("polymarket", side))
        return base
    book, err = _cached_fetch(cache, ("polymarket", token_id, now.isoformat()),
                              http_get, f"{POLYMARKET_CLOB_BASE_URL}/book?{urlencode({'token_id': token_id})}",
                              timeout, CLOB_FETCH_ATTEMPTS, sleep)
    top_ask, top_bid = _polymarket_top_of_book(book if isinstance(book, dict) else None)
    if top_ask:
        base["ask"] = top_ask.get("price")
        base["ask_size"] = top_ask.get("size")
    if top_bid:
        base["bid"] = top_bid.get("price")
        base["bid_size"] = top_bid.get("size")
    if base["ask"] is None:
        # Explicit Gamma top-of-book fallback (only when CLOB gave no ask).
        gamma_market, gerr = _cached_fetch(
            cache, ("gamma", token_id, now.isoformat()), http_get,
            f"{POLYMARKET_GAMMA_BASE_URL}/markets?{urlencode({'clob_token_ids': token_id})}",
            timeout, EVENTS_FETCH_ATTEMPTS, sleep)
        market = _first_market(gamma_market)
        if market is not None:
            g = _gamma_top_of_book(market)
            g_ask = g.get("yes_ask") if side.endswith("YES") else g.get("no_ask")
            g_bid = g.get("yes_bid") if side.endswith("YES") else g.get("no_bid")
            if g_ask is not None:
                base["ask"] = g_ask
                base["bid"] = base["bid"] if base["bid"] is not None else g_bid
                base["source"] = "polymarket_gamma_top_of_book"
                base["depth_status"] = "gamma_top_of_book_fallback"
                base["gamma_top_of_book_fallback_used"] = True
        if err:
            base["hard_blockers"].append("polymarket_clob_fetch_failed")
    if base["ask"] is None:
        base["hard_blockers"].append(_missing_label("polymarket", side))
    return base


# ---------------------------------------------------------------------------- #
# Helpers                                                                       #
# ---------------------------------------------------------------------------- #


def _cached_fetch(cache, key, http_get, url, timeout, attempts, sleep):
    if cache is not None and key in cache:
        return cache[key]
    resp, err = _http_get_with_retry(http_get, url, timeout, attempts=attempts, sleep=sleep)
    if cache is not None:
        cache[key] = (resp, err)
    return resp, err


def _first_market(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list):
        for m in payload:
            if isinstance(m, dict):
                return m
        return None
    if isinstance(payload, dict):
        data = payload.get("data") or payload.get("markets")
        if isinstance(data, list):
            for m in data:
                if isinstance(m, dict):
                    return m
        return payload if (payload.get("clobTokenIds") or payload.get("bestAsk") or payload.get("outcomePrices")) else None
    return None


def _missing_label(platform: str, side: str) -> str:
    s = "yes" if side.endswith("YES") else "no"
    return f"missing_{platform}_{s}_ask"


def _leg_key(leg: dict[str, Any]) -> str:
    return f"{str(leg.get('platform') or '').lower()}::{leg.get('market_id_or_ticker') or ''}::{str(leg.get('side') or '').upper()}"
