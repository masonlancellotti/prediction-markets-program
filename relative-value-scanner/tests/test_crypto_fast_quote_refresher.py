"""Tests for the public/read-only fast quote refresher (mocked http_get; no network)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.crypto_fast_quote_refresher import (
    dedupe_watched_legs, make_public_live_refresher, refresh_watched_quotes, refresh_one_leg,
)
from relative_value.crypto_fast_path_executor import build_active_candidate_universe, run_crypto_fast_path_trigger

NOW = datetime(2026, 5, 30, 16, 0, 0, tzinfo=timezone.utc)


def _kalshi_leg(side="NO", ticker="K-A", fee=0.01):
    return {"leg_key": f"kalshi::{ticker}::{side}", "platform": "kalshi", "market_id_or_ticker": ticker,
            "side": side, "token_id": None, "contract_id": None, "condition_id": None,
            "reference_ask": 0.30, "fee": fee, "available_size_or_cap": 75.0}


def _poly_leg(side="NO", token="TKN-B", fee=0.01):
    return {"leg_key": f"polymarket::P-B::{side}", "platform": "polymarket", "market_id_or_ticker": "P-B",
            "side": side, "token_id": token, "contract_id": None, "condition_id": None,
            "reference_ask": 0.50, "fee": fee, "available_size_or_cap": 75.0}


def _fake_http_get(*, kalshi=None, poly=None, gamma=None, record=None):
    def get(url, timeout):
        if record is not None:
            record.append(url)
        if "/markets/" in url and "/orderbook" in url:
            if kalshi is None:
                raise RuntimeError("no kalshi book")
            return kalshi
        if "clob.polymarket.com/book" in url:
            if poly is None:
                raise RuntimeError("no clob book")
            return poly
        if "gamma-api.polymarket.com/markets" in url:
            if gamma is None:
                raise RuntimeError("no gamma")
            return gamma
        raise RuntimeError(f"unexpected url {url}")
    return get


def test_refresher_only_fetches_watched_legs() -> None:
    urls: list[str] = []
    http = _fake_http_get(kalshi={"orderbook": {"yes": [[70, 100]], "no": [[28, 80]]}},
                          poly={"asks": [{"price": "0.50", "size": "100"}], "bids": [{"price": "0.49", "size": "50"}]},
                          record=urls)
    quotes, diag = refresh_watched_quotes(watched_legs=[_kalshi_leg(), _poly_leg()], http_get=http, now=NOW)
    # exactly two fetches: the watched kalshi orderbook and the watched poly token book.
    assert len(urls) == 2
    assert any("/markets/K-A/orderbook" in u for u in urls)
    assert any("clob.polymarket.com/book" in u and "TKN-B" in u for u in urls)
    assert not any("/events" in u or "series_ticker" in u for u in urls)  # no full scan
    assert diag["legs_requested"] == 2 and diag["legs_refreshed"] == 2 and diag["legs_missing_quote"] == 0


def test_kalshi_parses_yes_no_ask() -> None:
    http = _fake_http_get(kalshi={"orderbook": {"yes": [[70, 100]], "no": [[28, 80]]}})
    # NO leg: ask = 1 - top_yes_bid(0.70) = 0.30
    q_no = refresh_one_leg(leg=_kalshi_leg("NO"), now=NOW, http_get=http, sleep=lambda _s: None, timeout_seconds=5.0, cache={})
    assert q_no["ask"] == 0.30 and q_no["source"] == "kalshi_public_orderbook" and q_no["complement_quote_used"] is True
    # YES leg: ask = 1 - top_no_bid(0.28) = 0.72
    q_yes = refresh_one_leg(leg=_kalshi_leg("YES"), now=NOW, http_get=http, sleep=lambda _s: None, timeout_seconds=5.0, cache={})
    assert q_yes["ask"] == 0.72


def test_polymarket_parses_token_ask() -> None:
    http = _fake_http_get(poly={"asks": [{"price": "0.50", "size": "120"}], "bids": [{"price": "0.49", "size": "30"}]})
    q = refresh_one_leg(leg=_poly_leg("NO"), now=NOW, http_get=http, sleep=lambda _s: None, timeout_seconds=5.0, cache={})
    assert q["ask"] == 0.50 and q["ask_size"] == 120.0 and q["source"] == "polymarket_clob"
    assert q.get("gamma_top_of_book_fallback_used") is not True


def test_gamma_fallback_labeled() -> None:
    # CLOB returns no asks -> explicit Gamma fallback with bestAsk on the NO side.
    # NO ask via Gamma = 1 - bestBid = 1 - 0.45 = 0.55.
    http = _fake_http_get(poly={"asks": [], "bids": []},
                          gamma=[{"clobTokenIds": json.dumps(["TKN-A", "TKN-B"]), "bestAsk": "0.52", "bestBid": "0.45",
                                  "outcomePrices": json.dumps(["0.45", "0.55"]), "outcomes": json.dumps(["Yes", "No"])}])
    q = refresh_one_leg(leg=_poly_leg("NO"), now=NOW, http_get=http, sleep=lambda _s: None, timeout_seconds=5.0, cache={})
    assert q["ask"] == 0.55
    assert q["source"] == "polymarket_gamma_top_of_book" and q.get("gamma_top_of_book_fallback_used") is True
    assert q["depth_status"] == "gamma_top_of_book_fallback"


def test_missing_quote_labeled() -> None:
    http = _fake_http_get(kalshi={"orderbook": {"yes": [], "no": []}})  # empty book
    q = refresh_one_leg(leg=_kalshi_leg("NO"), now=NOW, http_get=http, sleep=lambda _s: None, timeout_seconds=5.0, cache={})
    assert q["ask"] is None and "missing_kalshi_no_ask" in q["hard_blockers"]


def test_dedupe_watched_legs() -> None:
    legs = [_kalshi_leg("NO"), _kalshi_leg("NO"), _poly_leg("NO")]
    assert len(dedupe_watched_legs({"watched_legs": legs})) == 2


def test_fast_path_public_live_detects_one_tick_edge(tmp_path: Path) -> None:
    # Universe with a kalshi NO + polymarket NO leg; mocked public quotes give net ~0.18.
    legs = [
        {"platform": "kalshi", "side": "NO", "market_id_or_ticker": "K-A", "ask": 0.30, "fee": 0.01,
         "all_in_cost": 0.31, "available_size_or_cap": 75.0, "source_index": "brti",
         "quote_timestamp": "2026-05-30T16:00:00Z", "market_shape": "point_in_time_threshold"},
        {"platform": "polymarket", "side": "NO", "market_id_or_ticker": "P-B", "ask": 0.50, "fee": 0.01,
         "all_in_cost": 0.51, "available_size_or_cap": 75.0, "source_index": "brti", "token_id_no": "TKN-B",
         "quote_timestamp": "2026-05-30T16:00:00Z", "market_shape": "point_in_time_threshold"},
    ]
    row = {"asset": "BTC", "candidate_type": "LONG_ONLY_GUARANTEED_PAYOFF", "paper_candidate": True,
           "tradable_buy_only": True, "requires_short_or_sell": False, "dedup_key": "K1",
           "target_instant_utc": "2026-05-30T17:00:00+00:00", "iteration_timestamp": "20260530T160000Z",
           "payoff_vector": [1, 1, 1], "min_payoff": 1.0, "net_edge_after_fees": 0.18,
           "adjusted_net_edge_after_fees": 0.18, "total_cost_after_fees": 0.82, "assumptions_accepted": [],
           "source_indexes": ["brti"], "hard_blockers": [], "basket_legs": legs}
    uni = tmp_path / "u.json"
    build_active_candidate_universe(assets=["BTC"], report_builder=lambda **k: {"rows": [row], "generated_at": "t"},
                                    output_path=uni, generated_at=NOW)
    http = _fake_http_get(kalshi={"orderbook": {"yes": [[70, 100]], "no": [[28, 100]]}},   # NO ask = 1-0.70 = 0.30
                          poly={"asks": [{"price": "0.50", "size": "100"}], "bids": [{"price": "0.49", "size": "50"}]})
    summary = run_crypto_fast_path_trigger(
        candidate_universe=uni, quote_source="public_live", iterations=1, min_net_edge=0.10,
        execution_style="manual", output_dir=tmp_path / "fp", dry_run=True, http_get=http,
        clock=lambda: NOW, sleep=lambda _s: None, console=lambda _m: None, env={}, kill_switch_path=tmp_path / "KILL",
    )
    assert summary["quote_source"] == "public_live"
    assert summary["quote_refresh_metrics"]["legs_refreshed_last"] == 2
    assert summary["decisions"] == 1  # one-tick edge detected from real (mocked) public quotes


def test_no_trading_auth_or_browser_code() -> None:
    src = Path("relative_value/crypto_fast_quote_refresher.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code = re.sub(r"(?m)^\s*#.*$", "", code)
    for pat in (r"\bplace_order\b", r"\bsubmit_order\b", r"\bcancel_order\b", r"\bsign_transaction\b",
                r"\bprivate_key\b", r"\bwallet\b", r"\bplaywright\b", r"\bselenium\b", r"\bwebdriver\b",
                r"\bAuthorization\b", r"\bapi_key\b", r"\bgetenv\b", r"\bdotenv\b", r"\.env\b",
                r"\bsmtp\b", r"\bslack\b", r"\bwebhook\b", r"\bmidpoint\b"):
        assert re.search(pat, code, re.IGNORECASE) is None, f"forbidden {pat}"


# ---------------------------------------------------------------------------- #
# Bounded-concurrency parallel refresh (refresh_all)                           #
# ---------------------------------------------------------------------------- #
import threading  # noqa: E402
import time  # noqa: E402


def _concurrency_tracking_http(*, sleep_s=0.03, fail_tickers=()):
    state = {"calls": 0, "concurrent": 0, "max_concurrent": 0, "urls": []}
    lock = threading.Lock()

    def get(url, timeout):
        with lock:
            state["calls"] += 1
            state["concurrent"] += 1
            state["max_concurrent"] = max(state["max_concurrent"], state["concurrent"])
            state["urls"].append(url)
        try:
            time.sleep(sleep_s)
            if any(t in url for t in fail_tickers):
                raise RuntimeError("simulated timeout")
            if "/markets/" in url and "/orderbook" in url:
                return {"orderbook": {"yes": [[70, 100]], "no": [[28, 100]]}}
            if "clob.polymarket.com/book" in url:
                return {"asks": [{"price": "0.50", "size": "100"}], "bids": [{"price": "0.49", "size": "50"}]}
            raise RuntimeError(f"unexpected {url}")
        finally:
            with lock:
                state["concurrent"] -= 1
    return get, state


def _kalshi_legs(n):
    return [{"platform": "kalshi", "side": "NO", "market_id_or_ticker": f"K-{i}", "fee": 0.01} for i in range(n)]


def test_parallel_refresh_faster_than_sequential() -> None:
    legs = _kalshi_legs(8)
    http, _s = _concurrency_tracking_http(sleep_s=0.03)
    par = make_public_live_refresher(http_get=http, workers=8)
    t0 = time.perf_counter(); par.refresh_all(legs, now=NOW); par_ms = (time.perf_counter() - t0) * 1000
    http2, _s2 = _concurrency_tracking_http(sleep_s=0.03)
    seq = make_public_live_refresher(http_get=http2, workers=1)
    t1 = time.perf_counter(); seq.refresh_all(legs, now=NOW); seq_ms = (time.perf_counter() - t1) * 1000
    assert par_ms < seq_ms * 0.6, f"parallel {par_ms:.0f}ms not faster than sequential {seq_ms:.0f}ms"


def test_bounded_workers_honored() -> None:
    http, state = _concurrency_tracking_http(sleep_s=0.02)
    r = make_public_live_refresher(http_get=http, workers=2)
    r.refresh_all(_kalshi_legs(8), now=NOW)
    assert state["max_concurrent"] <= 2, f"max_concurrent={state['max_concurrent']} exceeded workers=2"


def test_dedupe_fetches_one_per_unique_market() -> None:
    http, state = _concurrency_tracking_http(sleep_s=0.0)
    legs = [{"platform": "kalshi", "side": "NO", "market_id_or_ticker": "K-A", "fee": 0.01},
            {"platform": "kalshi", "side": "YES", "market_id_or_ticker": "K-A", "fee": 0.01},  # same ticker
            {"platform": "polymarket", "side": "NO", "market_id_or_ticker": "P-A", "token_id": "T1", "fee": 0.01}]
    quotes, diag = make_public_live_refresher(http_get=http, workers=8).refresh_all(legs, now=NOW)
    assert sum(1 for u in state["urls"] if "/markets/K-A/orderbook" in u) == 1  # one fetch for the shared ticker
    assert diag["unique_kalshi_fetches"] == 1 and diag["unique_polymarket_fetches"] == 1
    assert diag["legs_requested"] == 3 and diag["legs_refreshed"] == 3  # both K-A sides priced from one book


def test_timeout_marks_missing_quote_without_crashing() -> None:
    http, _s = _concurrency_tracking_http(sleep_s=0.0, fail_tickers=("K-1",))
    legs = _kalshi_legs(3)  # K-0, K-1 (fails), K-2
    quotes, diag = make_public_live_refresher(http_get=http, workers=4).refresh_all(legs, now=NOW)
    failed = quotes["kalshi::K-1::NO"]
    assert failed["ask"] is None and "missing_kalshi_no_ask" in failed["hard_blockers"]
    assert quotes["kalshi::K-0::NO"]["ask"] is not None  # other legs unaffected
    assert diag["rate_limit_or_timeout_errors"] >= 1 and diag["legs_missing_quote"] >= 1


def test_prioritization_fetches_priority_legs_first() -> None:
    http, state = _concurrency_tracking_http(sleep_s=0.0)
    legs = _kalshi_legs(4)  # K-0..K-3
    # ask for K-3 first via priority_keys; workers=1 makes submission order deterministic.
    make_public_live_refresher(http_get=http, workers=1).refresh_all(
        legs, now=NOW, priority_keys=["kalshi::K-3::NO", "kalshi::K-2::NO"])
    assert "/markets/K-3/orderbook" in state["urls"][0]
    assert "/markets/K-2/orderbook" in state["urls"][1]
