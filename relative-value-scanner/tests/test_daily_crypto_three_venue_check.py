"""Daily crypto three-venue check — behavioral tests.

Covers the nine scenarios from the policy prompt:

  1. Same asset/threshold/time with source mismatch → PAPER_CANDIDATE in aggressive mode.
  2. Target time mismatch remains hard blocker (even in aggressive).
  3. Threshold mismatch remains hard blocker.
  4. Stale quote remains hard blocker.
  5. Missing ask remains hard blocker.
  6. CDNA row can become PAPER_CANDIDATE with class CDNA_FILL_FIRST.
  7. CDNA row never has strict_exact_arb=true pre-fill.
  8. No midpoint use.
  9. No automated execution code (audit of module source).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scan
from relative_value.daily_crypto_three_venue_check import (
    build_daily_crypto_three_venue_check_report,
)


NOW = datetime(2026, 5, 29, 9, 25, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------- #
# Helpers                                                                      #
# ---------------------------------------------------------------------------- #


def _write_btc_evidence(
    tmp_path: Path,
    *,
    polymarket_target_time: str = "12:00 ET",
    polymarket_threshold: int = 70000,
    kalshi_yes_ask: str = "0.40",
    kalshi_yes_ask_size: str = "100",
    polymarket_no_ask: str = "0.55",
    polymarket_no_ask_size: str = "100",
    kalshi_timestamp: str = "2026-05-29T09:20:00Z",
    polymarket_timestamp: str = "2026-05-29T09:20:00Z",
) -> Path:
    asset_dir = tmp_path / "btc_point_in_time_threshold"
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / "kalshi_polished_evidence.json").write_text(
        json.dumps(
            _kalshi_payload(
                yes_ask=kalshi_yes_ask,
                yes_ask_size=kalshi_yes_ask_size,
                quote_timestamp=kalshi_timestamp,
            )
        ),
        encoding="utf-8",
    )
    (asset_dir / "polymarket_polished_evidence.json").write_text(
        json.dumps(
            _polymarket_payload(
                no_ask=polymarket_no_ask,
                no_ask_size=polymarket_no_ask_size,
                target_time=polymarket_target_time,
                threshold=polymarket_threshold,
                quote_timestamp=polymarket_timestamp,
            )
        ),
        encoding="utf-8",
    )
    return tmp_path


def _write_cdna(
    tmp_path: Path,
    *,
    display_price: str = "0.20",
    display_no_price: str = "0.78",
    quote_timestamp: str = "2026-05-29T09:20:00Z",
) -> None:
    asset_dir = tmp_path / "btc_point_in_time_threshold"
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / "cdna_polished_evidence.json").write_text(
        json.dumps(_cdna_payload(display_price=display_price, display_no_price=display_no_price, quote_timestamp=quote_timestamp)),
        encoding="utf-8",
    )


def _kalshi_payload(*, yes_ask: str, yes_ask_size: str, quote_timestamp: str) -> dict:
    return {
        "schema_kind": "polished_crypto_market_family_evidence_v1",
        "diagnostic_only": True,
        "platform": "Kalshi",
        "category": "crypto",
        "market_family": "btc_price_threshold",
        "asset": "BTC",
        "market_shape": "point_in_time_threshold",
        "comparator": "above",
        "target_date": "2026-05-29",
        "target_time": "12:00 ET",
        "timezone": "ET",
        "price_source": "CF Benchmarks Bitcoin Real-Time Index (BRTI)",
        "settlement_source": "CF Benchmarks BRTI",
        "outcomes": [
            {
                "market_title": "Bitcoin price on May 29, 2026?",
                "market_ticker": "KXBTCD-26MAY2912-T69999.99",
                "outcome_name": "$70,000 or above",
                "yes_ask": yes_ask,
                "yes_ask_size": yes_ask_size,
                "no_ask": "0.60",
                "no_ask_size": "100",
                "strike_floor": 69999.99,
                "depth_status": "full_clob",
                "quote_timestamp": quote_timestamp,
            }
        ],
    }


def _polymarket_payload(
    *,
    no_ask: str,
    no_ask_size: str,
    target_time: str,
    threshold: int,
    quote_timestamp: str,
) -> dict:
    return {
        "schema_kind": "polished_crypto_market_family_evidence_v1",
        "diagnostic_only": True,
        "platform": "Polymarket",
        "category": "crypto",
        "market_family": "btc_price_threshold",
        "asset": "BTC",
        "market_shape": "point_in_time_threshold",
        "comparator": "above",
        "target_date": "2026-05-29",
        "target_time": target_time,
        "timezone": "ET",
        "price_source": "Binance",
        "settlement_source": "Binance BTC/USDT Close",
        "rules_text": f"This resolves using Binance BTC/USDT 1-minute candle close at {target_time}.",
        "outcomes": [
            {
                "market_title": f"Will the price of Bitcoin be above ${threshold:,} on May 29?",
                "platform_market_id": "2361673",
                "condition_id": "0xabc",
                "token_id_yes": "yes",
                "token_id_no": "no",
                "market_ticker": f"bitcoin-above-{threshold}-on-may-29-2026",
                "yes_ask": "0.45",
                "yes_ask_size": "100",
                "no_ask": no_ask,
                "no_ask_size": no_ask_size,
                "depth_status": "full_clob",
                "quote_timestamp": quote_timestamp,
            }
        ],
    }


def _cdna_payload(*, display_price: str, display_no_price: str, quote_timestamp: str) -> dict:
    return {
        "schema_kind": "polished_crypto_market_family_evidence_v1",
        "diagnostic_only": True,
        "platform": "Crypto.com Predict / CDNA",
        "category": "crypto",
        "market_family": "btc_price_threshold",
        "asset": "BTC",
        "market_shape": "point_in_time_threshold",
        "comparator": "above",
        "target_date": "2026-05-29",
        "target_time": "12:00 ET",
        "timezone": "ET",
        "price_source": "Crypto.com Predict display price",
        "settlement_source": "CDNA Rule 14.71 expiration value",
        "outcomes": [
            {
                "market_title": "BTC above $70,000 on May 29, 2026",
                "contract_id": "cdna-btc-70k",
                "symbol": "BTC-70K",
                "display_price": display_price,
                "display_no_price": display_no_price,
                "threshold": 70000,
                "depth_status": "display_price_only",
                "quote_timestamp": quote_timestamp,
            }
        ],
    }


def _build(
    root: Path,
    *,
    operator_risk_mode: str = "aggressive",
    include_cdna: bool = False,
    accept_cdna: bool = False,
    max_quote_age_seconds: float = 900.0,
    date: str | None = None,
) -> dict:
    return build_daily_crypto_three_venue_check_report(
        assets=["BTC"],
        date=date,
        operator_risk_mode=operator_risk_mode,
        include_cdna=include_cdna,
        operator_accept_cdna_display_price_risk=accept_cdna,
        cdna_operator_size_cap=1.0,
        max_quote_age_seconds=max_quote_age_seconds,
        min_available_notional=1.0,
        evidence_roots=[root],
        generated_at=NOW,
    )


# ---------------------------------------------------------------------------- #
# Test scenarios                                                               #
# ---------------------------------------------------------------------------- #


def test_same_typed_key_with_source_mismatch_becomes_paper_candidate_in_aggressive(tmp_path: Path) -> None:
    root = _write_btc_evidence(tmp_path)

    report = _build(root, operator_risk_mode="aggressive")
    paper = [r for r in report["rows"] if r.get("paper_candidate")]

    assert paper, "expected at least one paper candidate when only source/index differs"
    row = paper[0]
    assert row["action"] == "PAPER_CANDIDATE"
    assert row["paper_candidate_class"] == "OPERATOR_ACCEPTED_RISK"
    assert row["asset"] == "BTC"
    assert row["threshold"] == 70000.0
    assert row["net_edge_after_fees"] is not None and row["net_edge_after_fees"] > 0
    assert "source_index_basis_risk_accepted" in row["assumptions_accepted"]
    assert row["hard_blockers"] == []
    assert row["strict_exact_arb"] is False
    assert row["mathematical_strict_exact_arb"] is False


def test_target_time_mismatch_remains_hard_blocker_in_aggressive(tmp_path: Path) -> None:
    root = _write_btc_evidence(tmp_path, polymarket_target_time="17:00 ET")

    report = _build(root, operator_risk_mode="aggressive")

    assert all(not r.get("paper_candidate") for r in report["rows"]), \
        f"target_time_mismatch must block PAPER_CANDIDATE; got rows={report['rows']}"
    unmatched = report.get("unmatched_target_time_rows") or []
    assert unmatched, "expected at least one row in unmatched_target_time_rows"
    assert any("target_time_mismatch" in (r.get("hard_blockers") or []) for r in unmatched)
    assert report["summary_counts"]["unmatched_target_time_rows"] >= 1


def test_threshold_mismatch_remains_hard_blocker(tmp_path: Path) -> None:
    root = _write_btc_evidence(tmp_path, polymarket_threshold=72000)

    report = _build(root, operator_risk_mode="aggressive")

    assert all(not r.get("paper_candidate") for r in report["rows"])
    # The basis-review scout drops unmatched threshold rows into UNMATCHED action;
    # our daily-check filter removes those. Just check no candidate emerges.
    assert report["summary_counts"]["total_paper_candidate_rows"] == 0


def test_stale_quote_remains_hard_blocker(tmp_path: Path) -> None:
    root = _write_btc_evidence(
        tmp_path,
        kalshi_timestamp="2026-05-28T00:00:00Z",
        polymarket_timestamp="2026-05-28T00:00:00Z",
    )

    report = _build(root, operator_risk_mode="aggressive", max_quote_age_seconds=60)

    assert all(not r.get("paper_candidate") for r in report["rows"])
    assert any(
        "stale_or_missing_quote" in (r.get("hard_blockers") or [])
        for r in report["rows"]
    )


def test_missing_ask_remains_hard_blocker(tmp_path: Path) -> None:
    root = _write_btc_evidence(tmp_path)
    # Wipe the Polymarket no_ask so the K_YES_P_NO direction has no entry price.
    poly_file = root / "btc_point_in_time_threshold" / "polymarket_polished_evidence.json"
    payload = json.loads(poly_file.read_text(encoding="utf-8"))
    payload["outcomes"][0]["no_ask"] = None
    payload["outcomes"][0]["yes_ask"] = None
    poly_file.write_text(json.dumps(payload), encoding="utf-8")

    report = _build(root, operator_risk_mode="aggressive")

    assert all(not r.get("paper_candidate") for r in report["rows"])
    assert any("missing_quote" in (r.get("hard_blockers") or []) for r in report["rows"])


def test_cdna_row_can_become_paper_candidate_class_cdna_fill_first(tmp_path: Path) -> None:
    root = _write_btc_evidence(tmp_path)
    _write_cdna(root)

    report = _build(
        root,
        operator_risk_mode="aggressive",
        include_cdna=True,
        accept_cdna=True,
    )

    cdna_paper = [
        r
        for r in report["rows"]
        if r.get("paper_candidate") and r.get("paper_candidate_class") == "CDNA_FILL_FIRST"
    ]
    assert cdna_paper, f"expected CDNA fill-first paper candidate, got rows={report['rows']}"
    row = cdna_paper[0]
    assert row["action"] == "PAPER_CANDIDATE"
    assert row["candidate_action"] == "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
    assert row["available_notional_or_size_cap"] is not None
    assert row["available_notional_or_size_cap"] <= 1.0  # CDNA operator cap
    assert row["leg_1"]["venue"] == "cdna"
    assert row["leg_1"]["depth_status"] == "display_price_only"
    assert row["leg_1"]["executable_size_proven"] is False
    assert row["strict_exact_arb"] is False
    assert row["mathematical_strict_exact_arb"] is False


def test_cdna_row_never_has_strict_exact_arb_pre_fill(tmp_path: Path) -> None:
    root = _write_btc_evidence(tmp_path)
    _write_cdna(root)

    report = _build(
        root,
        operator_risk_mode="aggressive",
        include_cdna=True,
        accept_cdna=True,
    )

    for row in report["rows"]:
        if (row.get("direction") or "").startswith("CDNA_"):
            assert row["strict_exact_arb"] is False
            assert row["mathematical_strict_exact_arb"] is False
            assert row.get("paper_candidate_class") in {"CDNA_FILL_FIRST", "NONE"}


def test_no_midpoint_use_for_entry_or_net_edge(tmp_path: Path) -> None:
    # Kalshi YES ask = 0.40; Polymarket NO ask = 0.55. Sum of asks = 0.95.
    # Gross = 1 - 0.95 = 0.05. A midpoint between bid and ask would have been
    # different. Confirm the daily check uses true asks only.
    root = _write_btc_evidence(tmp_path, kalshi_yes_ask="0.40", polymarket_no_ask="0.55")

    report = _build(root, operator_risk_mode="aggressive")
    paper = [r for r in report["rows"] if r.get("paper_candidate")]
    assert paper
    row = paper[0]
    # Kalshi YES + Polymarket NO direction.
    assert row["leg_1"]["ask"] == 0.40
    assert row["leg_2"]["ask"] == 0.55
    # Net should be 0.05 minus conservative fees on each leg — must be strictly
    # less than 0.05 (proves fees were subtracted) and strictly greater than 0
    # (proves we used asks not midpoint, otherwise the math wouldn't work).
    assert 0 < row["net_edge_after_fees"] < 0.05


def test_module_source_contains_no_execution_code() -> None:
    src = Path("relative_value/daily_crypto_three_venue_check.py").read_text(encoding="utf-8")
    forbidden_patterns = [
        r"\brequests\.(get|post|put|delete|patch)\b",
        r"\bhttpx\.",
        r"\burllib\.request\.",
        r"\bplace_order\b",
        r"\bsubmit_order\b",
        r"\bcancel_order\b",
        r"\bsign_transaction\b",
        r"\bprivate_key\b",
        r"\bwallet\b",
        r"\bplaywright\b",
        r"\bselenium\b",
    ]
    for pattern in forbidden_patterns:
        assert re.search(pattern, src, re.IGNORECASE) is None, f"forbidden pattern {pattern} found in daily check"


def test_scan_command_runs_end_to_end(tmp_path: Path) -> None:
    root = _write_btc_evidence(tmp_path)
    json_output = tmp_path / "daily.json"
    md_output = tmp_path / "daily.md"

    rc = scan.main(
        [
            "run-daily-crypto-three-venue-check",
            "--assets",
            "BTC",
            "--operator-risk-mode",
            "aggressive",
            "--evidence-roots",
            str(root),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(md_output),
        ]
    )

    assert rc == 0
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "daily_crypto_three_venue_check_v1"
    assert payload["operator_risk_mode"] == "aggressive"
    assert payload["assets_requested"] == ["BTC"]
    assert payload["safety"]["uses_midpoint"] is False
    assert payload["safety"]["orders_or_execution_logic_added"] is False
    assert payload["safety"]["auth_or_account_logic_added"] is False
    md = md_output.read_text(encoding="utf-8")
    assert "# Daily Crypto Three-Venue Check" in md
    assert "Paper Candidates" in md
    assert "Hard Blockers" in md


def test_date_filter_drops_other_dates(tmp_path: Path) -> None:
    root = _write_btc_evidence(tmp_path)

    report = _build(root, operator_risk_mode="aggressive", date="2026-06-01")

    assert report["summary_counts"]["rows"] == 0


# ---------------------------------------------------------------------------- #
# New: depth-permissive + refresh + CDNA-missing scenarios                     #
# ---------------------------------------------------------------------------- #


def test_top_of_book_depth_accepted_with_flag_and_size_cap_promotes_to_paper_candidate(
    tmp_path: Path,
) -> None:
    # Wipe ask_size on both Kalshi and Polymarket so missing_quote_depth fires.
    root = _write_btc_evidence(tmp_path)
    for fname in ("kalshi_polished_evidence.json", "polymarket_polished_evidence.json"):
        path = root / "btc_point_in_time_threshold" / fname
        payload = json.loads(path.read_text(encoding="utf-8"))
        for outcome in payload["outcomes"]:
            outcome["yes_ask_size"] = None
            outcome["no_ask_size"] = None
        path.write_text(json.dumps(payload), encoding="utf-8")

    # Without the depth flag, no paper candidate.
    bare = build_daily_crypto_three_venue_check_report(
        assets=["BTC"],
        date=None,
        operator_risk_mode="aggressive",
        include_cdna=False,
        operator_accept_cdna_display_price_risk=False,
        cdna_operator_size_cap=1.0,
        max_quote_age_seconds=900.0,
        min_available_notional=1.0,
        evidence_roots=[root],
        allow_top_of_book_depth=False,
        operator_size_cap=0.0,
        generated_at=NOW,
    )
    assert all(not r.get("paper_candidate") for r in bare["rows"])

    # With the depth flag + size cap, the row graduates.
    permissive = build_daily_crypto_three_venue_check_report(
        assets=["BTC"],
        date=None,
        operator_risk_mode="aggressive",
        include_cdna=False,
        operator_accept_cdna_display_price_risk=False,
        cdna_operator_size_cap=1.0,
        max_quote_age_seconds=900.0,
        min_available_notional=1.0,
        evidence_roots=[root],
        allow_top_of_book_depth=True,
        operator_size_cap=10.0,
        generated_at=NOW,
    )
    paper = [r for r in permissive["rows"] if r.get("paper_candidate")]
    assert paper, f"expected promotion via top-of-book + size cap; got {permissive['rows']}"
    row = paper[0]
    assert row["paper_candidate_class"] == "OPERATOR_ACCEPTED_RISK"
    assert "limited_depth_operator_size_cap_applied" in row["assumptions_accepted"]
    assert "source_index_basis_risk_accepted" in row["assumptions_accepted"]
    assert row["available_notional_or_size_cap"] == 10.0
    assert row["hard_blockers"] == []


def test_top_of_book_flag_does_not_override_other_hard_blockers(tmp_path: Path) -> None:
    # Stale quote — top-of-book flag must NOT promote.
    root = _write_btc_evidence(
        tmp_path,
        kalshi_timestamp="2026-05-28T00:00:00Z",
        polymarket_timestamp="2026-05-28T00:00:00Z",
    )

    report = build_daily_crypto_three_venue_check_report(
        assets=["BTC"],
        date=None,
        operator_risk_mode="aggressive",
        include_cdna=False,
        operator_accept_cdna_display_price_risk=False,
        cdna_operator_size_cap=1.0,
        max_quote_age_seconds=60.0,
        min_available_notional=1.0,
        evidence_roots=[root],
        allow_top_of_book_depth=True,
        operator_size_cap=10.0,
        generated_at=NOW,
    )

    assert all(not r.get("paper_candidate") for r in report["rows"])


def test_cdna_missing_does_not_block_kalshi_polymarket(tmp_path: Path) -> None:
    # include_cdna=True but no CDNA file exists.
    root = _write_btc_evidence(tmp_path)

    report = build_daily_crypto_three_venue_check_report(
        assets=["BTC"],
        date=None,
        operator_risk_mode="aggressive",
        include_cdna=True,
        operator_accept_cdna_display_price_risk=True,
        cdna_operator_size_cap=1.0,
        max_quote_age_seconds=900.0,
        min_available_notional=1.0,
        evidence_roots=[root],
        generated_at=NOW,
    )

    # The K/P paper candidate should still exist.
    paper = [r for r in report["rows"] if r.get("paper_candidate") and r["paper_candidate_class"] == "OPERATOR_ACCEPTED_RISK"]
    assert paper, f"K/P paper candidate must not depend on CDNA; got {report['rows']}"

    asset_report = report["asset_reports"][0]
    assert asset_report["asset"] == "BTC"
    assert asset_report.get("cdna_evidence") is None
    assert "cdna_evidence_missing" in (asset_report.get("warnings") or [])


def test_refresh_flag_invokes_collector_and_writes_evidence(tmp_path: Path) -> None:
    captured_urls: list[str] = []

    def stub_http(url: str, timeout: float) -> Any:
        captured_urls.append(url)
        if "kalshi" in url and "/orderbook" in url:
            return {"orderbook": {"yes": [["40", "100"]], "no": [["60", "100"]]}}
        if "kalshi" in url and "/markets" in url:
            return {
                "markets": [
                    {
                        "ticker": "KXBTCD-26MAY2912-T69999.99",
                        "event_ticker": "KXBTCD-26MAY2912",
                        "title": "Bitcoin price on May 29, 2026?",
                        "yes_sub_title": "$70,000 or above",
                        "strike_floor": 69999.99,
                    }
                ]
            }
        if "gamma-api.polymarket.com" in url:
            return [
                {
                    "id": "2361673",
                    "question": "Will the price of Bitcoin be above $70,000 on May 29, 2026?",
                    "slug": "bitcoin-above-70000-on-may-29-2026",
                    "conditionId": "0xabc",
                    "clobTokenIds": "[\"yes-token\", \"no-token\"]",
                }
            ]
        if "clob.polymarket.com" in url and "yes-token" in url:
            return {"asks": [{"price": "0.45", "size": "100"}], "bids": []}
        if "clob.polymarket.com" in url and "no-token" in url:
            return {"asks": [{"price": "0.55", "size": "100"}], "bids": []}
        return None

    refresh_root = tmp_path / "refresh"
    report = build_daily_crypto_three_venue_check_report(
        assets=["BTC"],
        date="2026-05-29",
        operator_risk_mode="aggressive",
        include_cdna=False,
        operator_accept_cdna_display_price_risk=False,
        cdna_operator_size_cap=1.0,
        max_quote_age_seconds=3600.0,
        min_available_notional=1.0,
        refresh_kalshi_polymarket=True,
        write_refreshed_evidence_root=refresh_root,
        http_get=stub_http,
        generated_at=NOW,
    )

    assert report["refresh_kalshi_polymarket"] is True
    assert report["refresh_summary"] is not None
    assert (refresh_root / "btc" / "kalshi_polished_evidence.json").exists()
    assert (refresh_root / "btc" / "polymarket_polished_evidence.json").exists()
    # Asset report should pick up the refreshed evidence.
    assert report["asset_reports"][0]["status"] == "OK"
    # We hit at least one Kalshi markets URL and one Polymarket gamma URL.
    assert any("kalshi" in u and "/markets" in u for u in captured_urls)
    assert any("gamma-api.polymarket.com" in u for u in captured_urls)


def test_collector_module_contains_no_order_or_auth_code() -> None:
    # Strip module docstrings and comments before scanning — the prose explains
    # what is *not* allowed and would otherwise trigger forbidden-word matches.
    raw = Path("relative_value/daily_crypto_evidence_collector.py").read_text(encoding="utf-8")
    code = _strip_docstrings_and_comments(raw)
    forbidden_patterns = [
        r"\bplace_order\b",
        r"\bsubmit_order\b",
        r"\bcancel_order\b",
        r"\bsign_transaction\b",
        r"\bprivate_key\b",
        r"\bwallet\b",
        r"\bplaywright\b",
        r"\bselenium\b",
        r"\bAuthorization\b",
        r"\bAPI[_-]?KEY\b",
    ]
    for pattern in forbidden_patterns:
        assert re.search(pattern, code, re.IGNORECASE) is None, f"forbidden pattern {pattern} found in collector code"


def _strip_docstrings_and_comments(src: str) -> str:
    # Remove triple-quoted docstrings.
    src = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    src = re.sub(r"'''.*?'''", "", src, flags=re.DOTALL)
    # Remove line comments.
    src = re.sub(r"(?m)^\s*#.*$", "", src)
    src = re.sub(r"(?m)\s+#[^\n]*$", "", src)
    return src


def test_collector_default_http_uses_get_only() -> None:
    # Verify the urllib Request explicitly uses GET method.
    src = Path("relative_value/daily_crypto_evidence_collector.py").read_text(encoding="utf-8")
    assert 'method="GET"' in src, "default_http_get must use method=GET only"
    # And the Request constructor must not include POST/PUT/DELETE/PATCH.
    for verb in ("POST", "PUT", "DELETE", "PATCH"):
        assert re.search(rf'method="{verb}"', src) is None, f"collector must not issue {verb} requests"


# ---------------------------------------------------------------------------- #
# Polymarket discovery — fixture-driven stubs                                  #
# ---------------------------------------------------------------------------- #


def _btc_polymarket_event_fixture() -> dict[str, Any]:
    """A minimal real-shape Polymarket event payload for BTC threshold testing."""
    return {
        "id": "ev-btc-2026-05-29-12pm",
        "slug": "bitcoin-above-on-may-29-2026-12pm-et",
        "title": "Bitcoin above ___ on May 29, 12PM ET?",
        "description": (
            "This market will resolve to \"Yes\" if the \"Close\" price for the "
            "BTC/USDT 1 hour candle that ends at 12:00 PM ET on May 29, 2026 is "
            "higher than the price specified in the title. The resolution source "
            "for this market is Binance, specifically the BTC/USDT pair."
        ),
        "active": True,
        "closed": False,
        "markets": [
            {
                "id": "mkt-btc-70000",
                "question": "Bitcoin above 70,000 on May 29, 12PM ET?",
                "slug": "bitcoin-above-70000-on-may-29-2026-12pm-et",
                "conditionId": "0xabc",
                "clobTokenIds": "[\"yes-token-btc-70000\", \"no-token-btc-70000\"]",
                "bestBid": 0.44,
                "bestAsk": 0.45,
                "active": True,
                "closed": False,
                "groupItemThreshold": 70000,
            },
            {
                "id": "mkt-btc-72000",
                "question": "Bitcoin above 72,000 on May 29, 12PM ET?",
                "slug": "bitcoin-above-72000-on-may-29-2026-12pm-et",
                "conditionId": "0xdef",
                "clobTokenIds": "[\"yes-token-btc-72000\", \"no-token-btc-72000\"]",
                "bestBid": 0.10,
                "bestAsk": 0.12,
                "active": True,
                "closed": False,
                "groupItemThreshold": 72000,
            },
        ],
    }


def _polymarket_stub(events_by_slug: dict[str, list[dict]] | None = None, books_by_token: dict[str, dict] | None = None) -> "Any":
    events_by_slug = events_by_slug or {}
    books_by_token = books_by_token or {}

    def stub(url: str, timeout: float) -> Any:
        # Slug fetch via Polymarket gamma.
        if "gamma-api.polymarket.com/events" in url and "slug=" in url:
            for slug, items in events_by_slug.items():
                if f"slug={slug}" in url:
                    return items
            return []
        if "gamma-api.polymarket.com/events" in url:
            return []
        if "gamma-api.polymarket.com/markets" in url:
            return []
        if "clob.polymarket.com/book" in url:
            for token, body in books_by_token.items():
                if f"token_id={token}" in url:
                    return body
            return {"asks": [], "bids": []}
        return None

    return stub


def test_polymarket_discovery_parses_event_with_threshold_markets(tmp_path: Path) -> None:
    from relative_value.daily_crypto_evidence_collector import write_daily_crypto_live_evidence

    event = _btc_polymarket_event_fixture()
    stub = _polymarket_stub(
        events_by_slug={"bitcoin-above-on-may-29-2026-12pm-et": [event]},
        books_by_token={
            "yes-token-btc-70000": {"asks": [{"price": "0.45", "size": "100"}], "bids": [{"price": "0.44", "size": "50"}]},
            "no-token-btc-70000": {"asks": [{"price": "0.55", "size": "100"}], "bids": [{"price": "0.54", "size": "50"}]},
            "yes-token-btc-72000": {"asks": [{"price": "0.12", "size": "100"}], "bids": [{"price": "0.10", "size": "50"}]},
            "no-token-btc-72000": {"asks": [{"price": "0.88", "size": "100"}], "bids": [{"price": "0.86", "size": "50"}]},
        },
    )

    summary = write_daily_crypto_live_evidence(
        assets=["BTC"],
        output_root=tmp_path,
        generated_at=NOW,
        http_get=stub,
        target_date="2026-05-29",
    )

    payload_path = tmp_path / "btc" / "polymarket_polished_evidence.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert len(payload["outcomes"]) == 2, f"expected 2 threshold markets, got {payload['outcomes']}"
    first = payload["outcomes"][0]
    assert first["strike_floor"] == 70000.0
    assert first["target_date"] == "2026-05-29"
    assert first["target_time"] is not None and "12" in first["target_time"]
    assert "yes-token" in first["token_id_yes"]
    assert "no-token" in first["token_id_no"]
    assert first["yes_ask"] == 0.45
    assert first["yes_ask_size"] == 100.0
    assert first["no_ask"] == 0.55
    assert first["condition_id"] == "0xabc"
    assert first["rules_text"]  # description propagated
    record = summary["per_asset"][0]
    assert record["polymarket_markets_found"] == 2
    assert record["polymarket_events_found"] >= 1


def test_polymarket_discovery_records_diagnostics_when_zero_markets(tmp_path: Path) -> None:
    from relative_value.daily_crypto_evidence_collector import write_daily_crypto_live_evidence

    stub = _polymarket_stub()  # everything returns empty
    summary = write_daily_crypto_live_evidence(
        assets=["BTC"],
        output_root=tmp_path,
        generated_at=NOW,
        http_get=stub,
        target_date="2026-05-29",
    )
    record = summary["per_asset"][0]
    assert record["polymarket_markets_found"] == 0
    assert record["polymarket_search_queries_attempted"] > 0
    assert "direct_slug" in record["polymarket_query_strategies"]
    assert "search_keyword" in record["polymarket_query_strategies"]
    assert "polymarket_no_markets_found" in record["warnings"]


def test_polymarket_target_time_missing_surfaces_as_blocker(tmp_path: Path) -> None:
    """If the event title has no parseable AM/PM ET, the outcome must carry
    target_time_missing in blockers_remaining (not be silently dropped)."""
    from relative_value.daily_crypto_evidence_collector import write_daily_crypto_live_evidence

    event = _btc_polymarket_event_fixture()
    event["title"] = "Bitcoin above ___ on May 29, 2026?"  # no time component
    event["description"] = "BTC/USDT 1 hour candle. Resolution source Binance."  # no time either
    stub = _polymarket_stub(
        events_by_slug={"bitcoin-above-on-may-29-2026-12pm-et": [event]},
        books_by_token={
            "yes-token-btc-70000": {"asks": [], "bids": []},
            "no-token-btc-70000": {"asks": [], "bids": []},
            "yes-token-btc-72000": {"asks": [], "bids": []},
            "no-token-btc-72000": {"asks": [], "bids": []},
        },
    )

    write_daily_crypto_live_evidence(
        assets=["BTC"],
        output_root=tmp_path,
        generated_at=NOW,
        http_get=stub,
        target_date="2026-05-29",
    )

    payload = json.loads((tmp_path / "btc" / "polymarket_polished_evidence.json").read_text(encoding="utf-8"))
    assert payload["outcomes"], "outcomes must not be silently dropped when target_time is missing"
    for outcome in payload["outcomes"]:
        assert "target_time_missing" in (outcome.get("blockers_remaining") or [])


def test_polymarket_discovery_surfaces_diagnostics_in_daily_report(tmp_path: Path) -> None:
    """End-to-end: when refresh is on and Polymarket returns zero matches,
    the daily report's asset_reports must expose the search diagnostics."""
    stub = _polymarket_stub()
    report = build_daily_crypto_three_venue_check_report(
        assets=["BTC"],
        date="2026-05-29",
        operator_risk_mode="aggressive",
        include_cdna=False,
        operator_accept_cdna_display_price_risk=False,
        cdna_operator_size_cap=1.0,
        max_quote_age_seconds=300.0,
        min_available_notional=1.0,
        refresh_kalshi_polymarket=True,
        write_refreshed_evidence_root=tmp_path / "refresh",
        http_get=stub,
        generated_at=NOW,
    )
    asset_report = report["asset_reports"][0]
    assert asset_report["asset"] == "BTC"
    assert "polymarket_search_queries_attempted" in asset_report
    assert asset_report["polymarket_markets_found"] == 0
    # Markdown should mention diagnostics section so a P=0 outcome is explainable.
    md = (tmp_path / "out.md")
    json_out = (tmp_path / "out.json")
    from relative_value.daily_crypto_three_venue_check import (
        render_daily_crypto_three_venue_check_markdown,
    )
    rendered = render_daily_crypto_three_venue_check_markdown(report)
    assert "Polymarket Discovery Diagnostics" in rendered
    assert "direct_slug" in rendered


# ---------------------------------------------------------------------------- #
# Daily crypto refresh repair — Kalshi discovery, CLOB fallback, zero reasons   #
# ---------------------------------------------------------------------------- #


def _no_sleep(_seconds: float) -> None:
    """No-op sleep so retry/backoff never slows the test suite."""
    return None


def _kalshi_markets_stub(markets: list[dict], orderbook: dict | None = None):
    """Stub returning the given Kalshi daily markets for any series query."""
    ob = orderbook if orderbook is not None else {"orderbook": {"yes": [["40", "100"]], "no": [["55", "100"]]}}

    def stub(url: str, timeout: float) -> Any:
        if "kalshi" in url and "/markets" in url and "/orderbook" not in url:
            return {"markets": markets}
        if "kalshi" in url and "/events" in url:
            return {"events": []}
        if "kalshi" in url and "/orderbook" in url:
            return ob
        return None

    return stub


def test_kalshi_discovery_finds_events_from_series_list(tmp_path: Path) -> None:
    """Task 1: discovery finds daily markets via the candidate Kalshi series."""
    from relative_value.daily_crypto_evidence_collector import write_daily_crypto_live_evidence

    markets = [
        {
            "ticker": "KXBTCD-26MAY2912-T69999.99",
            "event_ticker": "KXBTCD-26MAY2912",
            "title": "Bitcoin price on May 29, 2026?",
            "yes_sub_title": "$70,000 or above",
            "strike_floor": 69999.99,
        },
        {
            "ticker": "KXBTCD-26MAY2912-T71999.99",
            "event_ticker": "KXBTCD-26MAY2912",
            "title": "Bitcoin price on May 29, 2026?",
            "yes_sub_title": "$72,000 or above",
            "strike_floor": 71999.99,
        },
    ]
    summary = write_daily_crypto_live_evidence(
        assets=["BTC"],
        output_root=tmp_path,
        generated_at=NOW,
        http_get=_kalshi_markets_stub(markets),
        target_date="2026-05-29",
        sleep=_no_sleep,
    )
    rec = summary["per_asset"][0]
    assert "KXBTCD" in rec["kalshi_series_queried"]
    assert rec["kalshi_events_found"] >= 1
    assert rec["kalshi_markets_discovered"] == 2
    assert rec["kalshi_markets_after_shape_filter"] == 2
    assert rec["kalshi_markets_found"] == 2  # usable outcomes

    payload = json.loads((tmp_path / "btc" / "kalshi_polished_evidence.json").read_text(encoding="utf-8"))
    assert len(payload["outcomes"]) == 2
    assert {o["strike_floor"] for o in payload["outcomes"]} == {69999.99, 71999.99}
    assert all(o["target_date"] == "2026-05-29" for o in payload["outcomes"])


def test_kalshi_discovery_accepts_et_date_when_utc_date_differs(tmp_path: Path) -> None:
    """Task 1: an event for the ET calendar date is kept even when the UTC clock
    has already rolled to the next day."""
    from relative_value.daily_crypto_evidence_collector import write_daily_crypto_live_evidence

    # 2026-05-30 01:30 UTC == 2026-05-29 21:30 ET. ET date is 05-29.
    generated = datetime(2026, 5, 30, 1, 30, tzinfo=timezone.utc)
    markets = [
        {
            "ticker": "KXBTCD-26MAY2912-T69999.99",
            "event_ticker": "KXBTCD-26MAY2912",  # ET target date 2026-05-29
            "yes_sub_title": "$70,000 or above",
            "strike_floor": 69999.99,
        },
        {
            "ticker": "KXBTCD-26MAY2812-T69999.99",
            "event_ticker": "KXBTCD-26MAY2812",  # 2026-05-28: neither ET nor UTC today
            "yes_sub_title": "$70,000 or above",
            "strike_floor": 69999.99,
        },
    ]
    summary = write_daily_crypto_live_evidence(
        assets=["BTC"],
        output_root=tmp_path,
        generated_at=generated,
        http_get=_kalshi_markets_stub(markets),
        target_date=None,  # derive acceptable dates from generated_at (ET + UTC)
        sleep=_no_sleep,
    )
    rec = summary["per_asset"][0]
    payload = json.loads((tmp_path / "btc" / "kalshi_polished_evidence.json").read_text(encoding="utf-8"))
    dates = {o["target_date"] for o in payload["outcomes"]}
    assert "2026-05-29" in dates, "ET-dated event must be kept, not dropped on UTC date"
    assert "2026-05-28" not in dates
    assert rec["kalshi_rejection_reasons"].get("target_date_mismatch", 0) >= 1


def _poly_event_stub(event: dict, *, book_raises: Exception | None = None, book_body: dict | None = None):
    """Stub serving one Polymarket event by slug; CLOB book raises or returns body."""

    def stub(url: str, timeout: float) -> Any:
        if "kalshi" in url and "/markets" in url and "/orderbook" not in url:
            return {"markets": []}
        if "kalshi" in url and "/events" in url:
            return {"events": []}
        if "gamma-api.polymarket.com/events" in url and "slug=bitcoin-above-on-may-29-2026-12pm-et" in url:
            return [event]
        if "gamma-api.polymarket.com/events" in url:
            return []
        if "clob.polymarket.com/book" in url:
            if book_raises is not None:
                raise book_raises
            return book_body if book_body is not None else {"asks": [], "bids": []}
        return None

    return stub


def test_polymarket_clob_runtimeerror_falls_back_to_gamma_top_of_book(tmp_path: Path) -> None:
    """Task 2: CLOB book RuntimeError → use Gamma bestBid/bestAsk as limited depth."""
    from relative_value.daily_crypto_evidence_collector import write_daily_crypto_live_evidence

    event = _btc_polymarket_event_fixture()  # 70000 market has bestBid 0.44 / bestAsk 0.45
    stub = _poly_event_stub(event, book_raises=RuntimeError("public CLOB endpoint returned HTTP 503"))

    write_daily_crypto_live_evidence(
        assets=["BTC"],
        output_root=tmp_path,
        generated_at=NOW,
        http_get=stub,
        target_date="2026-05-29",
        sleep=_no_sleep,
    )
    payload = json.loads((tmp_path / "btc" / "polymarket_polished_evidence.json").read_text(encoding="utf-8"))
    assert payload["outcomes"], "CLOB failure must not drop the market when Gamma has a quote"
    first = next(o for o in payload["outcomes"] if o["strike_floor"] == 70000.0)
    assert first["yes_ask"] == 0.45  # from Gamma bestAsk
    assert first["depth_status"] == "gamma_top_of_book_fallback"
    diags = first.get("quote_diagnostics") or []
    assert "polymarket_clob_fetch_failed" in diags
    assert "gamma_top_of_book_fallback_used" in diags
    assert "limited_depth_operator_size_cap_applied" in diags


def test_polymarket_clob_failure_without_gamma_ask_yields_missing_ask(tmp_path: Path) -> None:
    """Task 2: no CLOB and no Gamma ask anywhere → missing_ask hard blocker."""
    from relative_value.daily_crypto_evidence_collector import write_daily_crypto_live_evidence

    event = _btc_polymarket_event_fixture()
    for m in event["markets"]:
        m.pop("bestBid", None)
        m.pop("bestAsk", None)
        m.pop("outcomePrices", None)
    stub = _poly_event_stub(event, book_raises=RuntimeError("public CLOB endpoint returned HTTP 503"))

    write_daily_crypto_live_evidence(
        assets=["BTC"],
        output_root=tmp_path,
        generated_at=NOW,
        http_get=stub,
        target_date="2026-05-29",
        sleep=_no_sleep,
    )
    payload = json.loads((tmp_path / "btc" / "polymarket_polished_evidence.json").read_text(encoding="utf-8"))
    assert payload["outcomes"]
    for outcome in payload["outcomes"]:
        assert outcome["yes_ask"] is None and outcome["no_ask"] is None
        assert "missing_ask" in (outcome.get("blockers_remaining") or [])


def test_clob_404_is_not_retried_but_503_is() -> None:
    """Task 2: a settled-market 404 must fail fast (no retry); a transient 503 is
    retried up to the attempt budget. The real status is always surfaced."""
    from relative_value.daily_crypto_evidence_collector import (
        CLOB_FETCH_ATTEMPTS,
        HttpGetError,
        _http_get_with_retry,
    )

    calls_404 = {"n": 0}

    def getter_404(url: str, timeout: float) -> Any:
        calls_404["n"] += 1
        raise HttpGetError(url=url, status=404, message="Not Found")

    resp, err = _http_get_with_retry(getter_404, "u", 1.0, attempts=CLOB_FETCH_ATTEMPTS, sleep=_no_sleep)
    assert resp is None
    assert calls_404["n"] == 1, "404 must not be retried"
    assert "404" in (err or "")

    calls_503 = {"n": 0}

    def getter_503(url: str, timeout: float) -> Any:
        calls_503["n"] += 1
        raise HttpGetError(url=url, status=503, message="Service Unavailable")

    resp2, err2 = _http_get_with_retry(getter_503, "u", 1.0, attempts=CLOB_FETCH_ATTEMPTS, sleep=_no_sleep)
    assert resp2 is None
    assert calls_503["n"] == CLOB_FETCH_ATTEMPTS, "transient 503 must be retried up to the budget"
    assert "503" in (err2 or "")


def test_zero_row_report_includes_no_cross_venue_rows_reason(tmp_path: Path) -> None:
    """Task 3: a zero-row report must carry the top-level reason fields."""
    report = build_daily_crypto_three_venue_check_report(
        assets=["BTC"],
        date="2026-05-29",
        operator_risk_mode="aggressive",
        include_cdna=False,
        operator_accept_cdna_display_price_risk=False,
        cdna_operator_size_cap=1.0,
        max_quote_age_seconds=300.0,
        min_available_notional=1.0,
        evidence_roots=[tmp_path / "empty"],
        generated_at=NOW,
    )
    assert report["summary_counts"]["rows"] == 0
    assert report["no_cross_venue_rows_reason"] == "no_markets_discovered_on_any_venue"
    assert report["kalshi_zero_reason"]
    assert report["polymarket_zero_reason"]
    assert report["cdna_zero_reason"] == "cdna_not_requested"


def _kalshi_empty_poly_empty_stub(url: str, timeout: float) -> Any:
    if "kalshi" in url and "/markets" in url and "/orderbook" not in url:
        return {"markets": []}
    if "kalshi" in url and "/events" in url:
        return {"events": []}
    if "gamma-api.polymarket.com" in url:
        return []
    if "clob.polymarket.com/book" in url:
        return {"asks": [], "bids": []}
    return None


def test_kalshi_zero_reason_includes_series_queried_when_refresh_finds_nothing(tmp_path: Path) -> None:
    """Task 3/Task 1: K=0 after refresh must name the series queried in the reason
    and in the markdown."""
    from relative_value.daily_crypto_three_venue_check import (
        render_daily_crypto_three_venue_check_markdown,
    )

    report = build_daily_crypto_three_venue_check_report(
        assets=["BTC"],
        date="2026-05-29",
        operator_risk_mode="aggressive",
        include_cdna=False,
        operator_accept_cdna_display_price_risk=False,
        cdna_operator_size_cap=1.0,
        max_quote_age_seconds=300.0,
        min_available_notional=1.0,
        refresh_kalshi_polymarket=True,
        write_refreshed_evidence_root=tmp_path / "refresh",
        http_get=_kalshi_empty_poly_empty_stub,
        generated_at=NOW,
        sleep=_no_sleep,
    )
    assert report["venue_market_counts"]["kalshi_markets"] == 0
    assert report["kalshi_zero_reason"]
    assert "KXBTCD" in report["kalshi_zero_reason"]
    rendered = render_daily_crypto_three_venue_check_markdown(report)
    assert "Kalshi Discovery Diagnostics" in rendered
    assert "KXBTCD" in rendered


def test_polymarket_clob_failure_surfaces_real_message_and_fallback_in_report(tmp_path: Path) -> None:
    """Task 2/Task 7: report exposes the real CLOB error message and fallback usage."""
    from relative_value.daily_crypto_three_venue_check import (
        render_daily_crypto_three_venue_check_markdown,
    )

    event = _btc_polymarket_event_fixture()
    stub = _poly_event_stub(event, book_raises=RuntimeError("public CLOB endpoint returned HTTP 503 rate limited"))

    report = build_daily_crypto_three_venue_check_report(
        assets=["BTC"],
        date="2026-05-29",
        operator_risk_mode="aggressive",
        include_cdna=False,
        operator_accept_cdna_display_price_risk=False,
        cdna_operator_size_cap=1.0,
        max_quote_age_seconds=3600.0,
        min_available_notional=1.0,
        refresh_kalshi_polymarket=True,
        write_refreshed_evidence_root=tmp_path / "refresh",
        http_get=stub,
        generated_at=NOW,
        sleep=_no_sleep,
    )
    asset_report = report["asset_reports"][0]
    assert asset_report["polymarket_clob_fetch_failures"] >= 1
    assert asset_report["polymarket_gamma_fallback_used"] >= 1
    samples = asset_report.get("polymarket_clob_error_samples") or []
    assert any("rate limited" in s or "HTTP 503" in s for s in samples), samples
    # The actual message must reach the per-asset warnings, not a bare RuntimeError.
    warns = report["refresh_summary"]["per_asset"][0]["warnings"]
    assert any("polymarket_clob_fetch_failed" in w and "rate limited" in w for w in warns), warns
    rendered = render_daily_crypto_three_venue_check_markdown(report)
    assert "Gamma fallback" in rendered


def test_exact_match_rows_generated_when_shared_asset_threshold_date_time(tmp_path: Path) -> None:
    """Task 4: shared (asset, threshold, date, time) yields exact-time rows and the
    matching diagnostics report them."""
    root = _write_btc_evidence(tmp_path)  # Kalshi & Polymarket both 12:00 ET / 70000 / 2026-05-29
    report = _build(root, operator_risk_mode="aggressive")
    asset_report = report["asset_reports"][0]
    md = asset_report["matching_diagnostics"]
    assert md["typed_key_candidates"] >= 1
    assert md["exact_time_rows"] >= 1
    assert report["summary_counts"]["rows"] >= 1


def test_no_paper_candidate_when_net_edge_not_positive(tmp_path: Path) -> None:
    """Task 5(9): non-positive net edge after fees can never be a paper candidate."""
    # Kalshi YES ask 0.60 + Polymarket NO ask 0.55 → gross negative; reverse also negative.
    root = _write_btc_evidence(tmp_path, kalshi_yes_ask="0.60", polymarket_no_ask="0.55")
    report = _build(root, operator_risk_mode="aggressive")
    assert all(not r.get("paper_candidate") for r in report["rows"])
    assert report["summary_counts"]["total_paper_candidate_rows"] == 0


def test_gamma_fallback_row_can_become_paper_candidate_with_size_cap(tmp_path: Path) -> None:
    """Task 5(10): a fresh, exact-time, positive-net row sourced via Gamma top-of-book
    fallback graduates to PAPER_CANDIDATE in aggressive mode with an explicit cap."""
    event = _btc_polymarket_event_fixture()
    event["markets"] = [event["markets"][0]]
    event["markets"][0]["bestBid"] = 0.50
    event["markets"][0]["bestAsk"] = 0.52

    kalshi_market = {
        "ticker": "KXBTCD-26MAY2912-T69999.99",
        "event_ticker": "KXBTCD-26MAY2912",
        "yes_sub_title": "$70,000 or above",
        "strike_floor": 69999.99,
    }

    def stub(url: str, timeout: float) -> Any:
        if "kalshi" in url and "/markets" in url and "/orderbook" not in url:
            return {"markets": [kalshi_market]}
        if "kalshi" in url and "/events" in url:
            return {"events": []}
        if "kalshi" in url and "/orderbook" in url:
            # YES bid 0.38, NO bid 0.60 → Kalshi YES ask 0.40, NO ask 0.62.
            return {"orderbook": {"yes": [["38", "100"]], "no": [["60", "100"]]}}
        if "gamma-api.polymarket.com/events" in url and "slug=bitcoin-above-on-may-29-2026-12pm-et" in url:
            return [event]
        if "gamma-api.polymarket.com/events" in url:
            return []
        if "clob.polymarket.com/book" in url:
            raise RuntimeError("public CLOB endpoint returned HTTP 503")
        return None

    report = build_daily_crypto_three_venue_check_report(
        assets=["BTC"],
        date="2026-05-29",
        operator_risk_mode="aggressive",
        include_cdna=False,
        operator_accept_cdna_display_price_risk=False,
        cdna_operator_size_cap=1.0,
        max_quote_age_seconds=3600.0,
        min_available_notional=1.0,
        allow_top_of_book_depth=True,
        operator_size_cap=10.0,
        refresh_kalshi_polymarket=True,
        write_refreshed_evidence_root=tmp_path / "refresh",
        http_get=stub,
        generated_at=NOW,
        sleep=_no_sleep,
    )
    paper = [r for r in report["rows"] if r.get("paper_candidate")]
    assert paper, (
        "expected gamma-fallback row to graduate; "
        f"rows={report['rows']} reason={report.get('no_cross_venue_rows_reason')}"
    )
    row = paper[0]
    assert row["paper_candidate_class"] == "OPERATOR_ACCEPTED_RISK"
    assert "limited_depth_operator_size_cap_applied" in row["assumptions_accepted"]
    assert row["hard_blockers"] == []
    assert row["available_notional_or_size_cap"] == 10.0


def test_no_trading_auth_or_browser_code_in_daily_crypto_modules() -> None:
    """Task 5(11): neither daily-crypto module contains trading/auth/browser code."""
    paths = [
        Path("relative_value/daily_crypto_evidence_collector.py"),
        Path("relative_value/daily_crypto_three_venue_check.py"),
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
    # The only network getter is urllib GET; the three-venue module does no HTTP at all.
    collector_src = Path("relative_value/daily_crypto_evidence_collector.py").read_text(encoding="utf-8")
    assert 'method="GET"' in collector_src
    three_venue_src = Path("relative_value/daily_crypto_three_venue_check.py").read_text(encoding="utf-8")
    assert "urlopen" not in three_venue_src and "urllib.request" not in three_venue_src
