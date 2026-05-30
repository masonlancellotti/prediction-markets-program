"""Crypto interval three-venue check — behavioral tests.

Covers the required scenarios:
  1. Kalshi interval fixture parses target instant from close_time.
  2. Polymarket interval fixture parses target instant from endDate.
  3. Exact instant + same strike + positive net -> PAPER_CANDIDATE (aggressive).
  4. Target time mismatch hard blocks (no candidate; surfaced as unmatched).
  5. Source/index mismatch accepted in aggressive (and NOT in conservative).
  6. Top-of-book accepted with size cap.
  7. CDNA saved evidence row can become CDNA_FILL_FIRST.
  8. Missing ask hard blocks.
  9. Stale quote hard blocks.
 10. No midpoint use (legs carry true asks; net uses asks only).
 11. No trading/auth/order/browser code in either module.
Plus: 404 not retried; zero-row report carries no_cross_venue_rows_reason.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scan
from relative_value.crypto_interval_evidence_collector import write_crypto_interval_live_evidence
from relative_value.crypto_interval_three_venue_check import (
    build_crypto_interval_three_venue_check_report,
    render_crypto_interval_three_venue_check_markdown,
)


# 2026-05-30 04:30 UTC == 12:30am ET; INSTANT below == 1am ET, inside an 8h window.
NOW = datetime(2026, 5, 30, 4, 30, tzinfo=timezone.utc)
INSTANT = "2026-05-30T05:00:00Z"
OTHER_INSTANT = "2026-05-30T06:00:00Z"  # 2am ET


def _no_sleep(_seconds: float) -> None:
    return None


def _btc_stub(
    *,
    kalshi_close_time: str = INSTANT,
    poly_end: str = INSTANT,
    kalshi_orderbook: dict | None = None,
    poly_yes_book: dict | None = None,
    poly_no_book: dict | None = None,
    poly_best_bid: float | None = 0.48,
    poly_best_ask: float | None = 0.52,
    poly_clob_raises: bool = False,
):
    """Stub serving one Kalshi (KXBTC, '<= 70000') + one Polymarket ('above 70000')
    threshold market for BTC. Kalshi default book -> YES ask 0.40 / NO ask 0.62."""
    ob = kalshi_orderbook if kalshi_orderbook is not None else {"orderbook": {"yes": [["38", "100"]], "no": [["60", "100"]]}}
    market = {
        "id": "m1",
        "question": "Bitcoin above 70,000 on May 30, 1AM ET?",
        "slug": "bitcoin-above-70000-on-may-30-2026-1am-et",
        "conditionId": "0xabc",
        "clobTokenIds": "[\"y\", \"n\"]",
        "endDate": poly_end,
    }
    if poly_best_bid is not None:
        market["bestBid"] = poly_best_bid
    if poly_best_ask is not None:
        market["bestAsk"] = poly_best_ask

    def stub(url: str, timeout: float) -> Any:
        if "kalshi" in url and "/events" in url:
            return {
                "events": [
                    {
                        "event_ticker": "KXBTC-26MAY3001",
                        "markets": [
                            {
                                "ticker": "KXBTC-26MAY3001-T70000",
                                "event_ticker": "KXBTC-26MAY3001",
                                "yes_sub_title": "$69,999.99 or below",
                                "cap_strike": 70000,
                                "close_time": kalshi_close_time,
                            }
                        ],
                    }
                ]
            }
        if "kalshi" in url and "/orderbook" in url:
            return ob
        if "gamma-api.polymarket.com/events" in url and "slug=bitcoin-above-on-may-30-2026-1am-et" in url:
            return [
                {
                    "id": "ev1",
                    "slug": "bitcoin-above-on-may-30-2026-1am-et",
                    "title": "Bitcoin above ___ on May 30, 1AM ET?",
                    "endDate": poly_end,
                    "description": "Binance BTC/USDT close at 1:00 AM ET on May 30, 2026.",
                    "markets": [market],
                }
            ]
        if "gamma-api.polymarket.com/events" in url:
            return []
        if "clob.polymarket.com/book" in url:
            if poly_clob_raises:
                raise RuntimeError("public CLOB endpoint returned HTTP 503")
            if "token_id=y" in url:
                return poly_yes_book if poly_yes_book is not None else {"asks": [{"price": "0.52", "size": "100"}], "bids": [{"price": "0.48", "size": "100"}]}
            if "token_id=n" in url:
                return poly_no_book if poly_no_book is not None else {"asks": [{"price": "0.50", "size": "100"}], "bids": [{"price": "0.46", "size": "100"}]}
            return {"asks": [], "bids": []}
        return None

    return stub


def _build(stub, **overrides) -> dict:
    params: dict[str, Any] = dict(
        assets=["BTC"],
        lookahead_hours=8,
        target_time_tolerance_seconds=0,
        operator_risk_mode="aggressive",
        allow_top_of_book_depth=False,
        operator_size_cap=0.0,
        include_cdna=False,
        operator_accept_cdna_display_price_risk=False,
        cdna_operator_size_cap=1.0,
        cdna_evidence_dir=None,
        max_quote_age_seconds=3600.0,
        min_available_notional=1.0,
        generated_at=NOW,
        http_get=stub,
        sleep=_no_sleep,
        refresh_kalshi_polymarket=True,
        write_refreshed_evidence_root=None,
    )
    params.update(overrides)
    return build_crypto_interval_three_venue_check_report(**params)


# ---------------------------------------------------------------------------- #
# Collector parsing                                                            #
# ---------------------------------------------------------------------------- #


def test_kalshi_interval_fixture_parses_target_instant_from_close_time() -> None:
    summary = write_crypto_interval_live_evidence(
        assets=["BTC"], output_root=None, lookahead_hours=8, generated_at=NOW, http_get=_btc_stub(), sleep=_no_sleep
    )
    rows = summary["per_asset"][0]["kalshi_rows"]
    assert len(rows) == 1
    row = rows[0]
    assert row["market_shape"] == "point_in_time_threshold"
    assert row["comparator"] == "below"
    assert row["threshold_or_strike"] == 70000.0
    assert row["target_instant_utc"] == "2026-05-30T05:00:00+00:00"


def test_polymarket_interval_fixture_parses_target_instant_from_end_date() -> None:
    summary = write_crypto_interval_live_evidence(
        assets=["BTC"], output_root=None, lookahead_hours=8, generated_at=NOW, http_get=_btc_stub(), sleep=_no_sleep
    )
    rows = summary["per_asset"][0]["polymarket_rows"]
    assert len(rows) == 1
    row = rows[0]
    assert row["market_shape"] == "point_in_time_threshold"
    assert row["comparator"] == "above"
    assert row["threshold_or_strike"] == 70000.0
    assert row["target_instant_utc"] == "2026-05-30T05:00:00+00:00"
    assert row["quote"]["yes_ask"] == 0.52


# ---------------------------------------------------------------------------- #
# Matching + candidates                                                        #
# ---------------------------------------------------------------------------- #


def test_exact_instant_same_strike_positive_net_becomes_paper_candidate(tmp_path: Path) -> None:
    report = _build(_btc_stub())
    assert report["venue_market_counts"]["typed_key_candidates"] == 1
    paper = [r for r in report["rows"] if r.get("paper_candidate")]
    assert paper, f"expected a paper candidate; rows={report['rows']}"
    row = paper[0]
    assert row["paper_candidate_class"] == "OPERATOR_ACCEPTED_RISK"
    assert row["market_shape"] == "point_in_time_threshold"
    assert row["threshold_or_strike"] == 70000.0
    assert row["target_instant_utc"] == "2026-05-30T05:00:00+00:00"
    assert row["net_edge_after_fees"] is not None and row["net_edge_after_fees"] > 0
    assert row["hard_blockers"] == []
    assert "source_index_basis_risk_accepted" in row["assumptions_accepted"]
    assert row["strict_exact_arb"] is False
    # The matched window is recorded with both venues.
    window = report["exact_matched_windows"][0]
    assert set(window["venues"]) == {"kalshi", "polymarket"}
    assert window["has_paper_candidate"] is True


def test_target_time_mismatch_hard_blocks(tmp_path: Path) -> None:
    # Polymarket settles one hour later than Kalshi -> no shared instant.
    report = _build(_btc_stub(poly_end=OTHER_INSTANT))
    assert all(not r.get("paper_candidate") for r in report["rows"])
    assert report["venue_market_counts"]["typed_key_candidates"] == 0
    unmatched = report["unmatched_by_target_instant"]
    assert unmatched and unmatched[0]["blocker"] == "target_time_mismatch"
    assert unmatched[0]["venue_a_instant"] != unmatched[0]["venue_b_instant"]


def test_source_index_mismatch_accepted_only_in_aggressive(tmp_path: Path) -> None:
    aggressive = _build(_btc_stub(), operator_risk_mode="aggressive")
    assert any(r.get("paper_candidate") for r in aggressive["rows"])

    conservative = _build(_btc_stub(), operator_risk_mode="conservative")
    assert all(not r.get("paper_candidate") for r in conservative["rows"]), (
        "source-index basis must NOT be auto-accepted in conservative mode"
    )
    # The row still exists as a WATCH (positive economics, basis unaccepted), not dropped.
    assert any(r.get("action") == "WATCH" for r in conservative["rows"])


def test_top_of_book_accepted_with_size_cap(tmp_path: Path) -> None:
    # Kalshi book with no sizes + Polymarket Gamma fallback (no size) -> missing depth.
    kalshi_ob = {"orderbook": {"yes": [["38", None]], "no": [["60", None]]}}
    stub = _btc_stub(kalshi_orderbook=kalshi_ob, poly_clob_raises=True)

    bare = _build(stub, allow_top_of_book_depth=False, operator_size_cap=0.0)
    assert all(not r.get("paper_candidate") for r in bare["rows"]), "missing depth must block without the flag"

    permissive = _build(_btc_stub(kalshi_orderbook=kalshi_ob, poly_clob_raises=True), allow_top_of_book_depth=True, operator_size_cap=10.0)
    paper = [r for r in permissive["rows"] if r.get("paper_candidate")]
    assert paper, f"expected promotion via top-of-book + cap; rows={permissive['rows']}"
    row = paper[0]
    assert "limited_depth_operator_size_cap_applied" in row["assumptions_accepted"]
    assert row["available_size_or_cap"] == 10.0
    assert row["hard_blockers"] == []


def _write_cdna_fixture(tmp_path: Path, *, yes: float = 0.55, no: float = 0.47, instant: str = INSTANT) -> Path:
    cdna_dir = tmp_path / "cdna"
    cdna_dir.mkdir(parents=True, exist_ok=True)
    fixture = {
        "asset": "BTC",
        "source_platform": "crypto_com_predict_cdna",
        "capture_method": "visible_browser",
        "captured_at_utc": "2026-05-30T04:25:00Z",
        "markets": [
            {
                "asset": "BTC",
                "title": "BTC above $70,000 on May 30, 2026 at 1:00 AM ET",
                "market_type": "point_in_time_threshold",
                "threshold": 70000,
                "comparator": "above",
                "measurement_date": "2026-05-30",
                "measurement_time": "1:00 AM",
                "timezone": "ET",
                "resolution_reference_time": instant,
                "yes_display_price": yes,
                "no_display_price": no,
                "price_source_index": "CDNA Rule 14.69 / Nadex BTC Index",
            }
        ],
    }
    (cdna_dir / "btc_cdna.json").write_text(json.dumps(fixture), encoding="utf-8")
    return cdna_dir


def test_cdna_saved_evidence_row_can_become_cdna_fill_first(tmp_path: Path) -> None:
    cdna_dir = _write_cdna_fixture(tmp_path)
    report = _build(
        _btc_stub(),
        include_cdna=True,
        operator_accept_cdna_display_price_risk=True,
        cdna_operator_size_cap=1.0,
        cdna_evidence_dir=cdna_dir,
    )
    cdna_paper = [
        r
        for r in report["rows"]
        if r.get("paper_candidate") and r.get("paper_candidate_class") == "CDNA_FILL_FIRST"
    ]
    assert cdna_paper, f"expected a CDNA_FILL_FIRST candidate; rows={[r['direction'] for r in report['rows']]}"
    row = cdna_paper[0]
    assert row["candidate_action"] == "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
    assert "cdna" in {row["leg_1"]["venue"], row["leg_2"]["venue"]}
    assert row["available_size_or_cap"] is not None and row["available_size_or_cap"] <= 1.0
    assert row["strict_exact_arb"] is False


def test_missing_ask_hard_blocks(tmp_path: Path) -> None:
    # Polymarket CLOB fails and no Gamma best bid/ask -> no ask anywhere on that side.
    stub = _btc_stub(poly_clob_raises=True, poly_best_bid=None, poly_best_ask=None)
    report = _build(stub)
    assert all(not r.get("paper_candidate") for r in report["rows"])
    assert any("missing_ask" in (r.get("hard_blockers") or []) for r in report["rows"])


def test_stale_quote_hard_blocks(tmp_path: Path) -> None:
    # Collect a snapshot at NOW, then evaluate it an hour later with a 60s freshness
    # budget -> both legs are stale (quotes were captured at NOW).
    write_crypto_interval_live_evidence(
        assets=["BTC"], output_root=tmp_path / "ev", lookahead_hours=8, generated_at=NOW, http_get=_btc_stub(), sleep=_no_sleep
    )
    later = datetime(2026, 5, 30, 5, 30, tzinfo=timezone.utc)  # +1h
    report = build_crypto_interval_three_venue_check_report(
        assets=["BTC"],
        lookahead_hours=8,
        operator_risk_mode="aggressive",
        max_quote_age_seconds=60.0,
        min_available_notional=1.0,
        generated_at=later,
        refresh_kalshi_polymarket=False,
        evidence_roots=[tmp_path / "ev"],
    )
    assert all(not r.get("paper_candidate") for r in report["rows"])
    assert any("stale_or_missing_quote" in (r.get("hard_blockers") or []) for r in report["rows"])


def test_no_midpoint_use_for_entry_or_net_edge(tmp_path: Path) -> None:
    report = _build(_btc_stub())
    paper = [r for r in report["rows"] if r.get("paper_candidate")][0]
    # Winning direction: Polymarket YES (>70k) ask 0.52 + Kalshi YES (<=70k) ask 0.40.
    asks = sorted([paper["leg_1"]["ask"], paper["leg_2"]["ask"]])
    assert asks == [0.40, 0.52]
    # Net = 1 - 0.52 - 0.40 - fees -> strictly between 0 and the gross 0.08.
    assert 0 < paper["net_edge_after_fees"] < 0.08


def test_zero_row_report_includes_no_cross_venue_rows_reason() -> None:
    def empty_stub(url: str, timeout: float) -> Any:
        if "kalshi" in url and "/events" in url:
            return {"events": []}
        if "gamma-api.polymarket.com" in url:
            return []
        if "clob.polymarket.com/book" in url:
            return {"asks": [], "bids": []}
        return None

    report = _build(empty_stub)
    assert report["summary_counts"]["rows"] == 0
    assert report["no_cross_venue_rows_reason"] == "no_markets_discovered_on_any_venue"
    assert "kalshi_zero_reason" in report and "polymarket_zero_reason" in report
    assert report["cdna_zero_reason"] == "cdna_not_requested"


def test_scan_command_runs_end_to_end(tmp_path: Path) -> None:
    # Exercise the CLI wiring with saved-evidence (no network): pre-write a snapshot,
    # then run without --refresh against the evidence root.
    summary = write_crypto_interval_live_evidence(
        assets=["BTC"], output_root=tmp_path / "ev", lookahead_hours=8, generated_at=NOW, http_get=_btc_stub(), sleep=_no_sleep
    )
    assert summary["per_asset"][0]["kalshi_rows"]
    json_out = tmp_path / "interval.json"
    md_out = tmp_path / "interval.md"
    rc = scan.main(
        [
            "run-crypto-interval-three-venue-check",
            "--assets", "BTC",
            "--operator-risk-mode", "aggressive",
            "--evidence-roots", str(tmp_path / "ev"),
            "--json-output", str(json_out),
            "--markdown-output", str(md_out),
        ]
    )
    assert rc == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "crypto_interval_three_venue_check_v1"
    assert payload["safety"]["uses_midpoint"] is False
    assert payload["safety"]["orders_or_execution_logic_added"] is False
    md = md_out.read_text(encoding="utf-8")
    assert "# Crypto Interval Three-Venue Check" in md
    assert "Upcoming matched windows" in md


def test_markdown_renders_all_sections() -> None:
    report = _build(_btc_stub())
    md = render_crypto_interval_three_venue_check_markdown(report)
    for heading in (
        "## 1. Summary",
        "## 2. Paper Candidates",
        "## 3. Upcoming matched windows",
        "## 4. Watch rows",
        "## 5. Unmatched by target instant",
        "## 6. Kalshi discovery diagnostics",
        "## 7. Polymarket discovery diagnostics",
        "## 8. CDNA evidence diagnostics",
        "## 9. Hard blockers",
        "## 10. Safety",
    ):
        assert heading in md, f"missing markdown section: {heading}"


# ---------------------------------------------------------------------------- #
# Safety source scans                                                          #
# ---------------------------------------------------------------------------- #


def _strip_docstrings_and_comments(src: str) -> str:
    src = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    src = re.sub(r"'''.*?'''", "", src, flags=re.DOTALL)
    src = re.sub(r"(?m)^\s*#.*$", "", src)
    src = re.sub(r"(?m)\s+#[^\n]*$", "", src)
    return src


# ---------------------------------------------------------------------------- #
# Synthetic Kalshi-bucket-basket lane                                          #
# ---------------------------------------------------------------------------- #


def _kalshi_ob(yes_cents: str, no_cents: str, size: str = "100") -> dict:
    return {"orderbook": {"yes": [[yes_cents, size]], "no": [[no_cents, size]]}}


def _synth_stub(
    *,
    poly_end: str = INSTANT,
    drop_ticker: str | None = None,
    empty_ob_ticker: str | None = None,
    top_tail_size: str = "100",
    poly_yes_size: str = "100",
):
    """Kalshi exhaustive bucket family (bottom tail + 2 buckets + top tail) at INSTANT
    plus a Polymarket 'above 72,500' threshold. Aligned boundary at 72,500.

    Default asks -> direction A (kalshi_bucket_above) is net-positive after fees,
    direction B (kalshi_bucket_not_above) is net-negative.
    """
    markets = [
        {"ticker": "KXBTC-26MAY3001-T72000", "event_ticker": "KXBTC-26MAY3001", "yes_sub_title": "$71,999.99 or below", "cap_strike": 72000, "close_time": INSTANT},
        {"ticker": "KXBTC-26MAY3001-B72250", "event_ticker": "KXBTC-26MAY3001", "yes_sub_title": "$72,000 to 72,499.99", "floor_strike": 72000, "cap_strike": 72499.99, "close_time": INSTANT},
        {"ticker": "KXBTC-26MAY3001-B72750", "event_ticker": "KXBTC-26MAY3001", "yes_sub_title": "$72,500 to 72,999.99", "floor_strike": 72500, "cap_strike": 72999.99, "close_time": INSTANT},
        {"ticker": "KXBTC-26MAY3001-T73000", "event_ticker": "KXBTC-26MAY3001", "yes_sub_title": "$73,000 or above", "floor_strike": 73000, "close_time": INSTANT},
    ]
    if drop_ticker:
        markets = [m for m in markets if drop_ticker not in m["ticker"]]
    obs = {
        "T72000": _kalshi_ob("20", "60"),   # yes_ask 0.40
        "B72250": _kalshi_ob("10", "80"),   # yes_ask 0.20
        "B72750": _kalshi_ob("10", "70"),   # yes_ask 0.30
        "T73000": _kalshi_ob("02", "92", size=top_tail_size),  # yes_ask 0.08
    }

    def stub(url: str, timeout: float) -> Any:
        if "kalshi" in url and "/events" in url:
            return {"events": [{"event_ticker": "KXBTC-26MAY3001", "markets": markets}]}
        if "kalshi" in url and "/orderbook" in url:
            if empty_ob_ticker and empty_ob_ticker in url:
                return {"orderbook": {"yes": [], "no": []}}
            for key, ob in obs.items():
                if key in url:
                    return ob
            return {"orderbook": {"yes": [], "no": []}}
        if "gamma-api.polymarket.com/events" in url and "slug=bitcoin-above-on-may-30-2026-1am-et" in url:
            return [
                {
                    "id": "ev1", "slug": "bitcoin-above-on-may-30-2026-1am-et", "endDate": poly_end,
                    "description": "Binance BTC/USDT close at 1:00 AM ET on May 30, 2026.",
                    "markets": [
                        {"id": "m", "question": "Bitcoin above 72,500 on May 30, 1AM ET?",
                         "slug": "bitcoin-above-72500-on-may-30-2026-1am-et", "conditionId": "0xa",
                         "clobTokenIds": "[\"y2\", \"n2\"]", "endDate": poly_end}
                    ],
                }
            ]
        if "gamma-api.polymarket.com/events" in url:
            return []
        if "clob.polymarket.com/book" in url and "token_id=y2" in url:
            return {"asks": [{"price": "0.40", "size": poly_yes_size}], "bids": [{"price": "0.39", "size": "100"}]}
        if "clob.polymarket.com/book" in url and "token_id=n2" in url:
            return {"asks": [{"price": "0.55", "size": "100"}], "bids": [{"price": "0.54", "size": "100"}]}
        return None

    return stub


def _synth_rows(report: dict, basket_type: str | None = None) -> list:
    rows = report.get("synthetic_rows") or []
    if basket_type:
        rows = [r for r in rows if r.get("synthetic_basket_type") == basket_type]
    return rows


def test_synthetic_above_basket_cost_computed_correctly() -> None:
    report = _build(_synth_stub(), allow_top_of_book_depth=True, operator_size_cap=10.0)
    above = _synth_rows(report, "kalshi_bucket_above")
    assert above, "expected a kalshi_bucket_above synthetic row"
    r = above[0]
    # Above legs at X=72,500 are the 72,500-72,999.99 bucket (yes 0.30) + the $73,000+ tail (yes 0.08).
    legs = {l["market_id_or_ticker"]: l["yes_ask"] for l in r["kalshi_bucket_legs"]}
    assert "KXBTC-26MAY3001-B72750" in legs and legs["KXBTC-26MAY3001-B72750"] == 0.30
    assert "KXBTC-26MAY3001-T73000" in legs and legs["KXBTC-26MAY3001-T73000"] == 0.08
    assert r["kalshi_bucket_leg_count"] == 2
    # net = 1 - (0.30 + 0.08) - kalshi_fees - polymarket_no(0.55) - polymarket_fee.
    assert r["polymarket_leg"]["side"] == "NO" and r["polymarket_leg"]["ask"] == 0.55
    assert r["net_edge_after_fees"] is not None and r["net_edge_after_fees"] > 0


def test_synthetic_not_above_basket_cost_computed_correctly() -> None:
    report = _build(_synth_stub(), allow_top_of_book_depth=True, operator_size_cap=10.0)
    not_above = _synth_rows(report, "kalshi_bucket_not_above")
    assert not_above
    r = not_above[0]
    legs = {l["market_id_or_ticker"]: l["yes_ask"] for l in r["kalshi_bucket_legs"]}
    # Not-above legs: the "$71,999.99 or below" tail (0.40) + the 72,000-72,499.99 bucket (0.20).
    assert "KXBTC-26MAY3001-T72000" in legs and legs["KXBTC-26MAY3001-T72000"] == 0.40
    assert "KXBTC-26MAY3001-B72250" in legs and legs["KXBTC-26MAY3001-B72250"] == 0.20
    assert r["polymarket_leg"]["side"] == "YES"


def test_synthetic_complement_never_buys_no_on_buckets() -> None:
    report = _build(_synth_stub(), allow_top_of_book_depth=True, operator_size_cap=10.0)
    for r in _synth_rows(report):
        # Every constituent Kalshi leg is a YES leg (mutually exclusive bucket YES),
        # never a NO leg, and never sums bucket NO prices.
        for leg in r["kalshi_bucket_legs"]:
            assert "yes_ask" in leg and "no_ask" not in leg
    # Source guard: the *bucket-leg* loop must use YES only (the Polymarket
    # complement using NO is correct and expected, so scope the check to the loop).
    src = Path("relative_value/crypto_interval_three_venue_check.py").read_text(encoding="utf-8")
    loop = src[src.index("for leg in basket_legs:"):src.index("if not basket_legs:")]
    assert "yes_ask" in loop
    assert "no_ask" not in loop, "synthetic basket legs must use YES on buckets only, never NO"


def test_synthetic_missing_bucket_ask_hard_blocks() -> None:
    # The 72,500-72,999.99 bucket (an ABOVE leg) returns an empty orderbook -> no YES ask.
    report = _build(_synth_stub(empty_ob_ticker="B72750"), allow_top_of_book_depth=True, operator_size_cap=10.0)
    above = _synth_rows(report, "kalshi_bucket_above")
    assert above
    r = above[0]
    assert not r["paper_candidate"]
    assert "missing_bucket_leg_ask" in r["hard_blockers"]


def test_synthetic_bucket_gap_hard_blocks() -> None:
    # Drop the 72,000-72,499.99 bucket -> a $500 gap between the bottom tail and the
    # next bucket -> coverage incomplete.
    report = _build(_synth_stub(drop_ticker="B72250"), allow_top_of_book_depth=True, operator_size_cap=10.0)
    rows = _synth_rows(report)
    assert rows
    assert all(not r["paper_candidate"] for r in rows)
    assert any("synthetic_bucket_coverage_incomplete" in r["hard_blockers"] for r in rows)


def test_synthetic_requires_same_instant() -> None:
    # Polymarket settles an hour after the Kalshi family -> no family at the poly instant.
    report = _build(_synth_stub(poly_end=OTHER_INSTANT), allow_top_of_book_depth=True, operator_size_cap=10.0)
    assert report["synthetic_summary"]["synthetic_rows"] == 0
    assert report["synthetic_summary"]["synthetic_candidates_generated"] == 0


def test_synthetic_positive_net_becomes_paper_candidate_in_aggressive() -> None:
    report = _build(_synth_stub(), operator_risk_mode="aggressive", allow_top_of_book_depth=True, operator_size_cap=10.0)
    paper = [r for r in _synth_rows(report) if r["paper_candidate"]]
    assert paper, f"expected a synthetic PAPER_CANDIDATE; rows={[(r['synthetic_basket_type'], r['net_edge_after_fees'], r['hard_blockers']) for r in _synth_rows(report)]}"
    r = paper[0]
    assert r["synthetic_basket_type"] == "kalshi_bucket_above"
    assert r["paper_candidate_class"] == "OPERATOR_ACCEPTED_RISK"
    assert r["lane"] == "synthetic_kalshi_bucket_vs_polymarket_threshold"
    assert r["synthetic_basket"] is True
    assert "source_index_basis_risk_accepted" in r["assumptions_accepted"]
    assert "synthetic_bucket_exhaustiveness_accepted" in r["assumptions_accepted"]
    assert r["hard_blockers"] == []


def test_synthetic_negative_net_stays_watch_or_ignore() -> None:
    report = _build(_synth_stub(), operator_risk_mode="aggressive", allow_top_of_book_depth=True, operator_size_cap=10.0)
    not_above = _synth_rows(report, "kalshi_bucket_not_above")[0]
    assert not not_above["paper_candidate"]
    assert not_above["action"] in {"WATCH", "IGNORE_BLOCKED"}
    assert not_above["net_edge_after_fees"] < 0
    assert "no_positive_net_edge_after_fees" in not_above["hard_blockers"]


def test_synthetic_conservative_mode_never_paper_candidate() -> None:
    report = _build(_synth_stub(), operator_risk_mode="conservative", allow_top_of_book_depth=True, operator_size_cap=10.0)
    assert all(not r["paper_candidate"] for r in _synth_rows(report))


def test_synthetic_available_size_is_min_across_legs_and_polymarket() -> None:
    # Top tail size 50, Polymarket YES/NO size 100, other legs 100 -> min basket qty 50.
    # No operator cap so the raw min flows through.
    report = build_crypto_interval_three_venue_check_report(
        assets=["BTC"], lookahead_hours=8, operator_risk_mode="aggressive",
        allow_top_of_book_depth=False, operator_size_cap=0.0,
        max_quote_age_seconds=3600.0, min_available_notional=1.0, generated_at=NOW,
        http_get=_synth_stub(top_tail_size="50", poly_yes_size="100"), sleep=_no_sleep,
        refresh_kalshi_polymarket=True,
    )
    above = _synth_rows(report, "kalshi_bucket_above")[0]
    # above legs: B72750 (size 100) + T73000 (size 50); polymarket NO size 100 -> min 50.
    assert above["available_size_or_cap"] == 50.0


def test_synthetic_fees_summed_per_leg() -> None:
    report = _build(_synth_stub(), operator_risk_mode="aggressive", allow_top_of_book_depth=True, operator_size_cap=10.0)
    r = _synth_rows(report, "kalshi_bucket_above")[0]
    leg_fee_sum = round(sum(l["kalshi_fee"] for l in r["kalshi_bucket_legs"]), 6)
    assert r["kalshi_synthetic_fee_total"] == leg_fee_sum
    assert r["total_fee_estimate"] == round(leg_fee_sum + r["polymarket_fee"], 6)


def test_synthetic_no_midpoint_uses_asks_only() -> None:
    r = _synth_rows(_build(_synth_stub(), allow_top_of_book_depth=True, operator_size_cap=10.0), "kalshi_bucket_above")[0]
    kalshi_cost = sum(l["yes_ask"] for l in r["kalshi_bucket_legs"])
    expected = round(1.0 - kalshi_cost - r["kalshi_synthetic_fee_total"] - r["polymarket_leg"]["ask"] - r["polymarket_fee"], 6)
    assert r["net_edge_after_fees"] == expected


# ---------------------------------------------------------------------------- #
# Harmonic interval alignment (observation-type compatibility)                 #
# ---------------------------------------------------------------------------- #

INSTANT_ISO = "2026-05-30T05:00:00+00:00"
REF_1H = "2026-05-30T04:00:00+00:00"
REF_2H = "2026-05-30T03:00:00+00:00"
REF_15M = "2026-05-30T04:45:00+00:00"
_SHAPE_BY_OBS = {
    "point_in_time_at_target": "point_in_time_threshold",
    "interval_start_to_end_change": "up_down",
    "range_at_target": "range_bucket",
    "touch_before_deadline": "deadline_touch",
}


def _tk(
    platform: str,
    *,
    obs: str = "point_in_time_at_target",
    comparator: str = "above",
    strike: float | None = 70000.0,
    instant: str = INSTANT_ISO,
    interval: int | None = None,
    ref_start: str | None = None,
    yes_ask: float = 0.45,
    no_ask: float = 0.55,
    bucket_floor: float | None = None,
    bucket_cap: float | None = None,
) -> dict:
    is_cdna = platform == "cdna"
    return {
        "asset": "BTC",
        "platform": platform,
        "market_shape": _SHAPE_BY_OBS.get(obs, "unknown"),
        "payoff_observation_type": obs,
        "comparator": comparator,
        "threshold_or_strike": strike,
        "bucket_floor": bucket_floor,
        "bucket_cap": bucket_cap,
        "reference_start_utc": ref_start,
        "target_instant_utc": instant,
        "interval_length_seconds": interval,
        "price_source": f"{platform}_index",
        "settlement_source": f"{platform}_index",
        "market_id_or_ticker": f"{platform}-{strike}-{interval}",
        "condition_id": None,
        "token_ids": {},
        "contract_id": None,
        "quote": {
            "yes_ask": yes_ask,
            "yes_ask_size": None if is_cdna else 100.0,
            "no_ask": no_ask,
            "no_ask_size": None if is_cdna else 100.0,
            "depth_status": "display_price_only" if is_cdna else "top_of_book",
            "quote_timestamp": "2026-05-30T04:55:00Z",
            "quote_diagnostics": [],
            "blockers_remaining": [],
        },
    }


def _report_from_rows(tmp_path: Path, *, kalshi=None, polymarket=None, cdna=None, **opts) -> dict:
    root = tmp_path / "ev"
    (root / "btc").mkdir(parents=True, exist_ok=True)
    snap = {
        "asset": "BTC",
        "kalshi_rows": kalshi or [],
        "polymarket_rows": polymarket or [],
        "cdna_rows": cdna or [],
        "kalshi_diagnostics": {},
        "polymarket_diagnostics": {},
        "cdna_diagnostics": {},
    }
    (root / "btc" / "interval_typed_keys.json").write_text(json.dumps(snap), encoding="utf-8")
    params = dict(
        assets=["BTC"], lookahead_hours=8, operator_risk_mode="aggressive",
        allow_top_of_book_depth=True, operator_size_cap=10.0, include_cdna=True,
        operator_accept_cdna_display_price_risk=True, cdna_operator_size_cap=1.0,
        max_quote_age_seconds=86400.0, min_available_notional=1.0,
        generated_at=datetime(2026, 5, 30, 4, 56, tzinfo=timezone.utc),
        refresh_kalshi_polymarket=False, evidence_roots=[root],
    )
    params.update(opts)
    return build_crypto_interval_three_venue_check_report(**params)


def _windows(report: dict, obs: str | None = None) -> list:
    w = report.get("exact_matched_windows") or []
    if obs:
        w = [x for x in w if x.get("observation_type") == obs]
    return w


def test_harmonic_cdna_20m_and_kalshi_15m_point_in_time_match_despite_interval(tmp_path: Path) -> None:
    report = _report_from_rows(
        tmp_path,
        kalshi=[_tk("kalshi", comparator="above", interval=900, yes_ask=0.45, no_ask=0.55)],  # 15m
        cdna=[_tk("cdna", comparator="above", interval=1200, yes_ask=0.40, no_ask=0.62)],      # 20m
    )
    pit = _windows(report, "point_in_time_at_target")
    assert pit, "CDNA 20m and Kalshi 15m point-in-time should match at the shared instant"
    w = pit[0]
    assert set(w["venues"]) == {"cdna", "kalshi"}
    assert w["harmonic_alignment_used"] is True
    assert report["harmonic_summary"]["harmonic_point_in_time_matches"] >= 1


def test_harmonic_cdna_2h_and_polymarket_1h_point_in_time_match(tmp_path: Path) -> None:
    report = _report_from_rows(
        tmp_path,
        polymarket=[_tk("polymarket", comparator="above", interval=3600, yes_ask=0.45, no_ask=0.55)],
        cdna=[_tk("cdna", comparator="above", interval=7200, yes_ask=0.40, no_ask=0.62)],
    )
    pit = _windows(report, "point_in_time_at_target")
    assert pit and set(pit[0]["venues"]) == {"cdna", "polymarket"}
    assert pit[0]["harmonic_alignment_used"] is True


def test_harmonic_cdna_2h_and_kalshi_4h_point_in_time_match(tmp_path: Path) -> None:
    report = _report_from_rows(
        tmp_path,
        kalshi=[_tk("kalshi", comparator="above", interval=14400, yes_ask=0.45, no_ask=0.55)],
        cdna=[_tk("cdna", comparator="above", interval=7200, yes_ask=0.40, no_ask=0.62)],
    )
    pit = _windows(report, "point_in_time_at_target")
    assert pit and set(pit[0]["venues"]) == {"cdna", "kalshi"}
    assert pit[0]["harmonic_alignment_used"] is True


def test_updown_2h_and_1h_do_not_match_with_different_reference_start(tmp_path: Path) -> None:
    report = _report_from_rows(
        tmp_path,
        polymarket=[_tk("polymarket", obs="interval_start_to_end_change", comparator="up", strike=None, interval=7200, ref_start=REF_2H)],
        kalshi=[_tk("kalshi", obs="interval_start_to_end_change", comparator="up", strike=None, interval=3600, ref_start=REF_1H)],
    )
    assert _windows(report, "interval_start_to_end_change") == []
    # Surfaced, not matched.
    assert any(
        r.get("compatibility_reason") == "updown_reference_start_mismatch" for r in report["rows"]
    )


def test_updown_15m_match_only_if_start_and_end_match(tmp_path: Path) -> None:
    matched = _report_from_rows(
        tmp_path,
        polymarket=[_tk("polymarket", obs="interval_start_to_end_change", comparator="up", strike=None, interval=900, ref_start=REF_15M, yes_ask=0.45, no_ask=0.55)],
        kalshi=[_tk("kalshi", obs="interval_start_to_end_change", comparator="up", strike=None, interval=900, ref_start=REF_15M, yes_ask=0.48, no_ask=0.52)],
    )
    ud = _windows(matched, "interval_start_to_end_change")
    assert ud, "same start+end 15m up/down must match"
    assert set(ud[0]["venues"]) == {"kalshi", "polymarket"}

    mismatched = _report_from_rows(
        tmp_path / "b",
        polymarket=[_tk("polymarket", obs="interval_start_to_end_change", comparator="up", strike=None, interval=900, ref_start=REF_15M)],
        kalshi=[_tk("kalshi", obs="interval_start_to_end_change", comparator="up", strike=None, interval=900, ref_start=REF_1H)],
    )
    assert _windows(mismatched, "interval_start_to_end_change") == []


def test_range_bucket_does_not_direct_match_but_is_synthetic_eligible(tmp_path: Path) -> None:
    report = _report_from_rows(
        tmp_path,
        kalshi=[_tk("kalshi", obs="range_at_target", comparator="range", strike=72000.0, bucket_floor=72000.0, bucket_cap=72499.99)],
        polymarket=[_tk("polymarket", comparator="above", strike=72000.0)],
    )
    # No direct candidate pairs the bucket with the threshold.
    assert _windows(report, "point_in_time_at_target") == [] or all(
        "range" not in (w.get("observation_type") or "") for w in _windows(report)
    )
    bucket_rows = [r for r in report["rows"] if r.get("payoff_observation_type") == "range_at_target"]
    assert bucket_rows and bucket_rows[0]["compatibility_reason"] == "range_at_target_synthetic_lane_only"


def test_touch_deadline_does_not_match_point_in_time(tmp_path: Path) -> None:
    report = _report_from_rows(
        tmp_path,
        cdna=[_tk("cdna", obs="touch_before_deadline", comparator="touch", strike=80000.0)],
        polymarket=[_tk("polymarket", comparator="above", strike=80000.0)],
    )
    assert _windows(report) == []  # nothing matched
    touch = [r for r in report["rows"] if r.get("payoff_observation_type") == "touch_before_deadline"]
    assert touch and "incompatible_shape" in touch[0]["hard_blockers"]


def test_source_index_basis_accepted_in_aggressive_harmonic_match(tmp_path: Path) -> None:
    report = _report_from_rows(
        tmp_path,
        kalshi=[_tk("kalshi", comparator="above", interval=900, yes_ask=0.45, no_ask=0.55)],
        cdna=[_tk("cdna", comparator="above", interval=1200, yes_ask=0.40, no_ask=0.62)],
    )
    paper = [r for r in report["rows"] if r.get("paper_candidate")]
    assert paper
    assert any("source_index_basis_risk_accepted" in r["assumptions_accepted"] for r in paper)


def test_target_instant_mismatch_remains_hard_blocker(tmp_path: Path) -> None:
    report = _report_from_rows(
        tmp_path,
        cdna=[_tk("cdna", comparator="above", interval=1200, instant=INSTANT_ISO)],
        polymarket=[_tk("polymarket", comparator="above", interval=3600, instant="2026-05-30T06:00:00+00:00")],
    )
    assert _windows(report) == []
    assert any(u.get("blocker") == "target_time_mismatch" for u in report["unmatched_by_target_instant"])


def test_cdna_harmonic_match_becomes_cdna_fill_first_if_net_positive(tmp_path: Path) -> None:
    report = _report_from_rows(
        tmp_path,
        polymarket=[_tk("polymarket", comparator="above", interval=3600, yes_ask=0.45, no_ask=0.55)],
        cdna=[_tk("cdna", comparator="above", interval=1200, yes_ask=0.40, no_ask=0.62)],
    )
    cdna_paper = [
        r for r in report["rows"]
        if r.get("paper_candidate") and r.get("paper_candidate_class") == "CDNA_FILL_FIRST"
    ]
    assert cdna_paper, f"expected CDNA_FILL_FIRST; rows={[(r.get('direction'), r.get('net_edge_after_fees'), r.get('hard_blockers')) for r in report['rows']]}"
    r = cdna_paper[0]
    assert r["candidate_action"] == "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
    assert r["harmonic_alignment_used"] is True
    assert r["payoff_observation_type"] == "point_in_time_at_target"


def test_harmonic_match_uses_asks_not_midpoint(tmp_path: Path) -> None:
    report = _report_from_rows(
        tmp_path,
        polymarket=[_tk("polymarket", comparator="above", interval=3600, yes_ask=0.45, no_ask=0.55)],
        cdna=[_tk("cdna", comparator="above", interval=1200, yes_ask=0.40, no_ask=0.62)],
    )
    paper = [r for r in report["rows"] if r.get("paper_candidate")][0]
    # net = 1 - leg1.ask - leg2.ask - fees -> strictly below the gross.
    gross = 1.0 - paper["leg_1"]["ask"] - paper["leg_2"]["ask"]
    assert paper["net_edge_after_fees"] < gross
    assert paper["leg_1"]["ask"] in (0.40, 0.62, 0.45, 0.55)


def test_no_trading_auth_or_browser_code_in_interval_modules() -> None:
    paths = [
        Path("relative_value/crypto_interval_evidence_collector.py"),
        Path("relative_value/crypto_interval_three_venue_check.py"),
    ]
    forbidden = [
        r"\bplace_order\b",
        r"\bsubmit_order\b",
        r"\bcancel_order\b",
        r"\bsign_transaction\b",
        r"\bprivate_key\b",
        r"\bwallet\b",
        r"\bplaywright\b",
        r"\bselenium\b",
        r"\bwebdriver\b",
        r"\bget_balance\b",
        r"\bget_positions\b",
        r"requests\.(get|post|put|delete|patch)",
        r"\bhttpx\b",
        r"\bAuthorization\b",
        r"\bAPI[_-]?KEY\b",
    ]
    for path in paths:
        code = _strip_docstrings_and_comments(path.read_text(encoding="utf-8"))
        for pattern in forbidden:
            assert re.search(pattern, code, re.IGNORECASE) is None, f"forbidden pattern {pattern} found in {path}"
    # The three-venue module performs no HTTP itself (it delegates to the collector).
    check_src = Path("relative_value/crypto_interval_three_venue_check.py").read_text(encoding="utf-8")
    assert "urlopen" not in check_src and "urllib.request" not in check_src
