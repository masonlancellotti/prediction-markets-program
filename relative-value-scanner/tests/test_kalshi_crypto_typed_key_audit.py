from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import scan
from relative_value.kalshi_crypto_typed_key_audit import (
    B_AMBIGUOUS_SHAPE,
    B_DEADLINE_NOT_POINT_IN_TIME,
    B_MISSING_ASSET,
    B_MISSING_QUOTE,
    B_MISSING_SETTLEMENT_SOURCE,
    B_MISSING_TARGET_DATE,
    B_MISSING_TARGET_TIME,
    B_MISSING_THRESHOLD,
    B_MISSING_TIMEZONE,
    B_FULL_ORDERBOOK_MISSING,
    B_KALSHI_LIVE_ORDERBOOK_FETCH_NOT_ENABLED_OR_MISSING,
    B_STALE_TOP_OF_BOOK,
    B_STALE_OR_MISSING_QUOTE,
    HINT_NO_SAVED_PEER,
    HINT_POSSIBLE_CDNA_PEER,
    HINT_POSSIBLE_POLYMARKET_PEER,
    SHAPE_AMBIGUOUS,
    SHAPE_DEADLINE_TOUCH,
    SHAPE_POINT_IN_TIME,
    SHAPE_RANGE_BUCKET,
    build_kalshi_crypto_typed_key_audit_report,
    write_kalshi_crypto_typed_key_audit_files,
)


_NOW = datetime(2026, 5, 27, 0, 0, 0, tzinfo=timezone.utc)


def _kalshi_btc_pit_row(
    *,
    ticker: str = "KXBTC-26MAY2517-T86249.99",
    event_ticker: str = "KXBTC-26MAY2517",
    title: str = "Bitcoin price range  on May 25, 2026?",
    rules_text: str | None = None,
    close_time: str = "2026-05-25T21:00:00Z",
    settlement_source_url: str | None = None,
    quote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "venue": "kalshi",
        "title": title,
        "market_id": ticker,
        "source_file": "reports/kalshi_markets_snapshot.json",
        "row_index": 0,
        "settlement": {
            "close_time": close_time,
            "resolution_time": close_time,
            "resolution_time_kind": "expected",
            "settlement_rules_text": rules_text
            if rules_text is not None
            else (
                "If the simple average of the sixty seconds of CF Benchmarks' Bitcoin Real-Time Index (BRTI) "
                "before 5 PM EDT is above 86249.99 at 5 PM EDT on May 25, 2026, then the market resolves to Yes."
            ),
            "settlement_source_url": settlement_source_url,
            "settlement_source_kind": "rules_text_only",
        },
        "quote_depth": quote if quote is not None else {},
    }


def _normalized_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 0,
        "source": "normalized_markets_v0",
        "generated_at": "2026-05-26T00:00:00+00:00",
        "input_dir": "reports",
        "normalized_markets": rows,
        "coverage": {},
        "safety": {},
        "warnings": [],
    }


def _cdna_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "crypto_com_predict_cdna_research_snapshot_v1",
        "rows": rows,
        "summary": {},
        "safety": {},
    }


def _polymarket_pit_audit_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "polymarket_point_in_time_typed_key_audit_v1",
        "rows": rows,
        "summary": {},
        "safety": {},
    }


def _setup(
    tmp_path: Path,
    *,
    kalshi_rows: list[dict[str, Any]],
    cdna_rows: list[dict[str, Any]] | None = None,
    polymarket_pit_rows: list[dict[str, Any]] | None = None,
) -> Path:
    (tmp_path / "normalized_markets_v0.json").write_text(
        json.dumps(_normalized_payload(kalshi_rows)), encoding="utf-8"
    )
    if cdna_rows is not None:
        (tmp_path / "crypto_com_predict_cdna_research_snapshot.json").write_text(
            json.dumps(_cdna_payload(cdna_rows)), encoding="utf-8"
        )
    if polymarket_pit_rows is not None:
        (tmp_path / "polymarket_point_in_time_typed_key_audit.json").write_text(
            json.dumps(_polymarket_pit_audit_payload(polymarket_pit_rows)), encoding="utf-8"
        )
    return tmp_path


def _write_kalshi_orderbook_enriched_file(
    tmp_path: Path,
    *,
    ticker: str = "KXBTC-26MAY2517-T86249.99",
    source_snapshot_path: str = "reports/kalshi_markets_snapshot.json",
    enrichment_status: str = "enriched",
    warnings: list[str] | None = None,
    best_bid: float | None = 0.42,
    best_ask: float | None = 0.46,
    bid_size: float | None = 100.0,
    ask_size: float | None = 50.0,
    captured_at: str = "2026-05-26T19:00:00+00:00",
) -> Path:
    path = tmp_path / "btc_kalshi_live_readonly_snapshot_orderbook_enriched.json"
    row_best_bid = 0.41 if enrichment_status != "enriched" else best_bid
    row_best_ask = 0.47 if enrichment_status != "enriched" else best_ask
    payload = {
        "schema_version": 1,
        "source": "kalshi_markets",
        "captured_at": "2026-05-26T00:00:00+00:00",
        "normalized_markets": [
            {
                "ticker": ticker,
                "venue": "kalshi",
                "best_bid": row_best_bid,
                "best_ask": row_best_ask,
                "orderbook_enrichment": {
                    "enrichment_status": enrichment_status,
                    "enrichment_warnings": warnings or [],
                    "best_bid": best_bid if enrichment_status == "enriched" else None,
                    "best_ask": best_ask if enrichment_status == "enriched" else None,
                    "depth_at_best_bid": bid_size if enrichment_status == "enriched" else None,
                    "depth_at_best_ask": ask_size if enrichment_status == "enriched" else None,
                    "orderbook_captured_at": captured_at,
                    "source_endpoint": f"https://external-api.kalshi.com/trade-api/v2/markets/{ticker}/orderbook",
                },
            }
        ],
        "orderbook_enrichment": {
            "schema_version": 1,
            "source": "read_only_orderbook_enrichment",
            "venue": "kalshi",
            "source_snapshot_path": source_snapshot_path,
            "market_count": 1,
            "enriched_count": 1 if enrichment_status == "enriched" else 0,
            "fresh_orderbook_fetch_enriched_count": 1 if enrichment_status == "enriched" else 0,
            "existing_top_of_book_present_count": 1,
            "full_orderbook_missing_count": 0 if enrichment_status == "enriched" else 1,
            "fetch_failed_count": 0,
            "stale_existing_top_of_book_count": 0 if enrichment_status == "enriched" else 1,
            "snapshot_warnings": warnings or [],
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _FakeKalshiCryptoClient:
    def __init__(self, by_series: dict[str, list[dict[str, Any]]]) -> None:
        self.by_series = by_series
        self.calls: list[str] = []

    def fetch_market_snapshot(self, *, limit: int, max_pages: int, series_ticker: str, filter_options=None) -> dict[str, Any]:
        self.calls.append(series_ticker)
        rows = self.by_series.get(series_ticker, [])
        return {
            "schema_version": 1,
            "source": "kalshi_markets",
            "captured_at": _NOW.isoformat(),
            "market_count": len(rows),
            "normalized_count": len(rows),
            "skipped_closed_count": 0,
            "skipped_inactive_count": 0,
            "skipped_past_close_time_count": 0,
            "normalized_markets": rows,
            "raw_response": {"markets": [row.get("raw", {}) for row in rows]},
        }


class _FakeKalshiOrderbookClient:
    def endpoint_for(self, ticker: str) -> str:
        return f"https://external-api.kalshi.com/trade-api/v2/markets/{ticker}/orderbook"

    def fetch_orderbook(self, ticker: str) -> dict[str, Any]:
        return {"orderbook": {"yes": [[0.4, 11]], "no": [[0.55, 13]]}}


def _live_crypto_row(*, ticker: str, close_time: str, status: str = "active") -> dict[str, Any]:
    return {
        "venue": "kalshi",
        "event_id": "KXBTC-26MAY2917",
        "market_id": ticker,
        "ticker": ticker,
        "title": "Bitcoin price range on May 29, 2026?",
        "question": "Bitcoin price range on May 29, 2026?",
        "best_bid": 0.4,
        "best_ask": 0.45,
        "close_time": close_time,
        "active": status in {"active", "open"},
        "closed": status in {"closed", "settled"},
        "status": status,
        "raw": {
            "event_ticker": "KXBTC-26MAY2917",
            "ticker": ticker,
            "title": "Bitcoin price range on May 29, 2026?",
            "close_time": close_time,
            "status": status,
            "rules_primary": (
                "If the simple average of the sixty seconds of CF Benchmarks' Bitcoin Real-Time Index "
                "(BRTI) before 5 PM EDT is above 100000 at 5 PM EDT on May 29, 2026, then the market resolves to Yes."
            ),
            "rules_secondary": "The price used to determine this market is based on CF Benchmarks' corresponding Real Time Index (RTI).",
        },
    }


def test_parses_btc_threshold_date_comparator_from_fixture_row(tmp_path: Path) -> None:
    row = _kalshi_btc_pit_row(
        quote={
            "best_bid": 0.42,
            "best_ask": 0.46,
            "depth_at_best_bid": 100.0,
            "depth_at_best_ask": 50.0,
            "orderbook_captured_at": "2026-05-26T19:00:00+00:00",
        }
    )
    input_dir = _setup(tmp_path, kalshi_rows=[row])
    report = build_kalshi_crypto_typed_key_audit_report(input_dir=input_dir, generated_at=_NOW)
    out = report["rows"][0]
    assert out["asset"] == "BTC"
    assert out["threshold"] == 86249.99
    assert out["comparator"] == "above"
    assert out["target_date"] == "2026-05-25"
    assert out["target_time"] == "21:00"
    assert out["timezone"] == "UTC"
    assert out["settlement_source"] == "CF Benchmarks BRTI"
    assert out["market_shape"] == SHAPE_POINT_IN_TIME
    # Quote present, with explicit bid/ask/size/timestamp.
    assert out["quote"]["bid"] == 0.42
    assert out["quote"]["ask"] == 0.46
    assert out["quote"]["bid_size"] == 100.0
    assert out["quote"]["ask_size"] == 50.0
    assert out["quote"]["present"] is True
    # No required blockers triggered.
    for blocker in (
        B_MISSING_ASSET,
        B_MISSING_THRESHOLD,
        B_MISSING_TARGET_DATE,
        B_MISSING_TARGET_TIME,
        B_MISSING_TIMEZONE,
        B_MISSING_SETTLEMENT_SOURCE,
        B_AMBIGUOUS_SHAPE,
        B_DEADLINE_NOT_POINT_IN_TIME,
        B_MISSING_QUOTE,
    ):
        assert blocker not in out["blockers"], f"unexpected blocker {blocker}"
    assert out["typed_complete"] is True
    # Safety.
    assert out["exact_ready"] is False
    assert out["paper_candidate"] is False
    assert out["can_create_candidate_pair"] is False


def test_audit_reads_enriched_kalshi_orderbook_file_and_counts_explicit_quotes(tmp_path: Path) -> None:
    row = _kalshi_btc_pit_row(quote={})
    input_dir = _setup(tmp_path, kalshi_rows=[row])
    _write_kalshi_orderbook_enriched_file(
        input_dir,
        ticker=row["ticker"],
        source_snapshot_path=row["source_file"],
        enrichment_status="enriched",
        best_bid=0.31,
        best_ask=0.34,
        bid_size=12,
        ask_size=14,
    )

    report = build_kalshi_crypto_typed_key_audit_report(input_dir=input_dir, generated_at=_NOW)
    out = report["rows"][0]
    assert out["quote"]["present"] is True
    assert out["quote"]["source"] == "kalshi_orderbook_enrichment"
    assert out["quote"]["bid"] == 0.31
    assert out["quote"]["ask"] == 0.34
    assert out["quote"]["bid_size"] == 12.0
    assert out["quote"]["ask_size"] == 14.0
    assert out["quote"]["quote_timestamp"] == "2026-05-26T19:00:00+00:00"
    assert out["quote"]["fresh_orderbook"] is True
    assert B_MISSING_QUOTE not in out["blockers"]

    summary = report["summary"]
    assert summary["enriched_files_read"] == 1
    assert summary["rows_with_existing_top_of_book"] == 1
    assert summary["rows_with_fresh_orderbook"] == 1
    assert summary["rows_with_bid_ask_size_timestamp"] == 1
    assert summary["rows_with_quote"] == 1


def test_fetch_kalshi_crypto_readonly_excludes_settled_and_preserves_typed_fields() -> None:
    active = _live_crypto_row(
        ticker="KXBTC-26MAY2917-T100000",
        close_time="2026-05-29T21:00:00Z",
    )
    settled = _live_crypto_row(
        ticker="KXBTC-26MAY2517-T100000",
        close_time="2026-05-25T21:00:00Z",
    )
    fake_client = _FakeKalshiCryptoClient({"KXBTC": [active, settled], "KXBTCD": [], "KXETH": [], "ETHD": []})

    report = scan.build_kalshi_crypto_readonly_snapshot(
        assets="BTC,ETH",
        limit=1000,
        max_pages=20,
        timeout_seconds=5,
        include_orderbooks=True,
        generated_at=_NOW,
        kalshi_client=fake_client,
        kalshi_orderbook_client=_FakeKalshiOrderbookClient(),
    )

    rows = report["normalized_markets"]
    assert [row["ticker"] for row in rows] == ["KXBTC-26MAY2917-T100000"]
    row = rows[0]
    assert row["asset"] == "BTC"
    assert row["threshold"] == 100000.0
    assert row["comparator"] == "above"
    assert row["target_date"] == "2026-05-29"
    assert row["target_time"] == "21:00"
    assert row["timezone"] == "UTC"
    assert row["settlement_source"] == "CF Benchmarks BRTI"
    assert row["typed_complete"] is True
    assert row["orderbook_enrichment"]["enrichment_status"] == "enriched"
    assert row["orderbook_enrichment"]["best_bid"] == 0.4
    assert row["orderbook_enrichment"]["best_ask"] == 0.45

    summary = report["kalshi_crypto_readonly_summary"]
    assert summary["markets_fetched"] == 2
    assert summary["active_markets"] == 1
    assert summary["future_markets"] == 1
    assert summary["btc_rows"] == 1
    assert summary["eth_rows"] == 0
    assert summary["typed_complete_rows"] == 1
    assert summary["orderbooks_fetched"] == 1
    assert summary["orderbooks_enriched"] == 1
    assert summary["settled_rows_excluded"] == 1
    assert report["safety"]["private_or_auth_endpoints_used"] is False
    text = json.dumps(report)
    assert "PAPER" + "_CANDIDATE" not in text
    for forbidden in ("/portfolio", "/positions", "/balance", "/orders", "/fills", "/auth"):
        assert forbidden not in text


def test_audit_prefers_fresh_crypto_snapshot_when_present(tmp_path: Path) -> None:
    stale = _kalshi_btc_pit_row(ticker="KXBTC-26MAY2917-T100000", quote={})
    input_dir = _setup(tmp_path, kalshi_rows=[stale])
    fresh_dir = input_dir / "live_readonly" / "crypto"
    fresh_dir.mkdir(parents=True)
    fresh_row = _live_crypto_row(
        ticker="KXBTC-26MAY2917-T100000",
        close_time="2026-05-29T21:00:00Z",
    )
    fresh_row["orderbook_enrichment"] = {
        "enrichment_status": "enriched",
        "enrichment_warnings": [],
        "best_bid": 0.4,
        "best_ask": 0.45,
        "depth_at_best_bid": 11,
        "depth_at_best_ask": 13,
        "orderbook_captured_at": "2026-05-27T00:00:00+00:00",
    }
    fresh_payload = {
        "schema_version": 1,
        "source": "kalshi_markets",
        "captured_at": _NOW.isoformat(),
        "normalized_markets": [fresh_row],
        "normalized_count": 1,
        "market_count": 1,
        "kalshi_crypto_readonly_summary": {"markets_retained": 1},
    }
    (fresh_dir / "kalshi_live_readonly_snapshot.json").write_text(json.dumps(fresh_payload), encoding="utf-8")

    report = build_kalshi_crypto_typed_key_audit_report(input_dir=input_dir, generated_at=_NOW)
    assert report["summary"]["fresh_crypto_snapshot_present"] is True
    assert report["summary"]["fresh_crypto_snapshot_rows_loaded"] == 1
    assert report["summary"]["kalshi_crypto_rows"] == 1
    out = report["rows"][0]
    assert out["fresh_crypto_snapshot_preferred"] is True
    assert out["target_date"] == "2026-05-29"
    assert out["settlement_source"] == "CF Benchmarks BRTI"
    assert out["quote"]["present"] is True
    assert report["summary"]["rows_with_quote"] == 1


def test_stale_top_of_book_from_enriched_file_remains_blocked(tmp_path: Path) -> None:
    row = _kalshi_btc_pit_row(quote={})
    input_dir = _setup(tmp_path, kalshi_rows=[row])
    _write_kalshi_orderbook_enriched_file(
        input_dir,
        ticker=row["ticker"],
        source_snapshot_path=row["source_file"],
        enrichment_status="unenriched",
        warnings=["stale_snapshot"],
    )

    report = build_kalshi_crypto_typed_key_audit_report(input_dir=input_dir, generated_at=_NOW)
    out = report["rows"][0]
    assert out["quote"]["present"] is False
    assert out["quote"]["existing_top_of_book_present"] is True
    assert out["quote"]["stale_top_of_book"] is True
    assert out["quote"]["full_orderbook_missing"] is True
    assert B_MISSING_QUOTE in out["blockers"]
    assert B_STALE_OR_MISSING_QUOTE in out["blockers"]
    assert B_STALE_TOP_OF_BOOK in out["blockers"]
    assert B_FULL_ORDERBOOK_MISSING in out["blockers"]
    assert B_KALSHI_LIVE_ORDERBOOK_FETCH_NOT_ENABLED_OR_MISSING in out["blockers"]

    summary = report["summary"]
    assert summary["enriched_files_read"] == 1
    assert summary["rows_with_quote"] == 0
    assert summary["rows_with_existing_top_of_book"] == 1
    assert summary["rows_with_fresh_orderbook"] == 0
    assert summary["rows_with_stale_top_of_book"] == 1
    assert summary["rows_with_full_orderbook_missing"] == 1
    assert summary["rows_with_bid_ask_size_timestamp"] == 0
    assert summary["kalshi_live_orderbook_fetch_not_enabled_or_missing_count"] == 1


def test_full_orderbook_missing_remains_blocked_without_stale_warning(tmp_path: Path) -> None:
    row = _kalshi_btc_pit_row(quote={})
    input_dir = _setup(tmp_path, kalshi_rows=[row])
    _write_kalshi_orderbook_enriched_file(
        input_dir,
        ticker=row["ticker"],
        source_snapshot_path=row["source_file"],
        enrichment_status="unenriched",
        warnings=[],
    )

    report = build_kalshi_crypto_typed_key_audit_report(input_dir=input_dir, generated_at=_NOW)
    out = report["rows"][0]
    assert out["quote"]["present"] is False
    assert B_FULL_ORDERBOOK_MISSING in out["blockers"]
    assert B_MISSING_QUOTE in out["blockers"]
    assert report["summary"]["rows_with_full_orderbook_missing"] == 1
    assert report["summary"]["rows_with_bid_ask_size_timestamp"] == 0


def test_missing_target_time_blocks_typed_complete(tmp_path: Path) -> None:
    row = _kalshi_btc_pit_row()
    # Strip the close_time and remove the explicit hour from the rules so no
    # target_time can be parsed.
    row["settlement"]["close_time"] = None
    row["settlement"]["resolution_time"] = None
    row["settlement"]["settlement_rules_text"] = (
        "If the CF Benchmarks BRTI is above 86249.99 on May 25, 2026, the market resolves to Yes."
    )
    input_dir = _setup(tmp_path, kalshi_rows=[row])
    report = build_kalshi_crypto_typed_key_audit_report(input_dir=input_dir, generated_at=_NOW)
    out = report["rows"][0]
    assert out["target_time"] is None
    assert B_MISSING_TARGET_TIME in out["blockers"]
    assert out["typed_complete"] is False


def test_missing_settlement_source_blocks_typed_complete(tmp_path: Path) -> None:
    row = _kalshi_btc_pit_row(
        rules_text="If BTC is above 86249.99 at 5 PM EDT on May 25, 2026, the market resolves Yes."
    )
    input_dir = _setup(tmp_path, kalshi_rows=[row])
    report = build_kalshi_crypto_typed_key_audit_report(input_dir=input_dir, generated_at=_NOW)
    out = report["rows"][0]
    assert out["settlement_source"] is None
    assert B_MISSING_SETTLEMENT_SOURCE in out["blockers"]
    assert out["typed_complete"] is False


def test_deadline_row_does_not_become_point_in_time(tmp_path: Path) -> None:
    row = _kalshi_btc_pit_row(
        ticker="KXBTC-26DEC3117-T200000",
        title="Will Bitcoin touch $200,000 before December 31, 2026?",
        rules_text=(
            "If Bitcoin's price touches $200,000 any time before December 31, 2026 according to "
            "CF Benchmarks' Bitcoin Real-Time Index (BRTI), then the market resolves to Yes."
        ),
        close_time="2026-12-31T21:00:00Z",
    )
    input_dir = _setup(tmp_path, kalshi_rows=[row])
    report = build_kalshi_crypto_typed_key_audit_report(input_dir=input_dir, generated_at=_NOW)
    out = report["rows"][0]
    assert out["market_shape"] == SHAPE_DEADLINE_TOUCH
    assert B_DEADLINE_NOT_POINT_IN_TIME in out["blockers"]
    assert out["treats_deadline_or_range_hit_as_point_in_time"] is False
    assert out["exact_ready"] is False
    assert out["paper_candidate"] is False


def test_range_bucket_row_does_not_become_point_in_time(tmp_path: Path) -> None:
    row = _kalshi_btc_pit_row(
        ticker="KXBTC-26MAY2517-B86125",
        title="Bitcoin price range on May 25, 2026?",
        rules_text=(
            "If the simple average of the sixty seconds of CF Benchmarks' Bitcoin Real-Time Index "
            "(BRTI) before 5 PM EDT is between 86000-86249.99 at 5 PM EDT on May 25, 2026, then "
            "the market resolves to Yes."
        ),
        close_time="2026-05-25T21:00:00Z",
    )
    input_dir = _setup(tmp_path, kalshi_rows=[row])
    report = build_kalshi_crypto_typed_key_audit_report(input_dir=input_dir, generated_at=_NOW)
    out = report["rows"][0]
    assert out["market_shape"] == SHAPE_RANGE_BUCKET
    assert B_DEADLINE_NOT_POINT_IN_TIME in out["blockers"]
    assert out["exact_ready"] is False


def test_possible_cdna_peer_hint_does_not_create_candidate_pair(tmp_path: Path) -> None:
    kalshi_row = _kalshi_btc_pit_row()
    cdna_row = {
        "asset": "BTC",
        "market_type": "point_in_time_threshold",
        "market_shape_conservative": "point_in_time_threshold",
        "comparator": ">",
        "threshold_value": 86249.99,
        "target_date": "May 25, 2026",
        "title": "Bitcoin price on 25 May at 5 PM EDT",
        "source_url": "https://web.crypto.com/explore/predict/events/details/bitcoin-25-may-5pm-et",
    }
    input_dir = _setup(tmp_path, kalshi_rows=[kalshi_row], cdna_rows=[cdna_row])
    report = build_kalshi_crypto_typed_key_audit_report(input_dir=input_dir, generated_at=_NOW)
    out = report["rows"][0]
    assert HINT_POSSIBLE_CDNA_PEER in out["peer_hints"]
    # Hint does NOT pair-creates: safety flags stay false.
    assert out["can_create_candidate_pair"] is False
    assert out["can_create_paper_candidate"] is False
    assert out["paper_candidate"] is False
    assert out["exact_ready"] is False
    # Overlap counted.
    assert out["date_threshold_comparator_overlap_present"] is True
    summary = report["summary"]
    assert summary["possible_cdna_peer_rows"] >= 1
    assert summary["date_threshold_comparator_overlap_rows"] >= 1
    # No PAPER_CANDIDATE strings anywhere.
    assert summary["exact_ready_rows"] == 0
    assert summary["paper_candidate_rows"] == 0


def test_possible_polymarket_peer_hint_via_audit_input(tmp_path: Path) -> None:
    kalshi_row = _kalshi_btc_pit_row(
        ticker="KXBTC-26DEC3122-T100000",
        title="Bitcoin price on December 31, 2026?",
        rules_text=(
            "If the simple average of the sixty seconds of CF Benchmarks' Bitcoin Real-Time Index (BRTI) "
            "before 4 PM ET is above 100000 at 4 PM ET on December 31, 2026, then the market resolves to Yes."
        ),
        close_time="2026-12-31T21:00:00Z",
    )
    pm_row = {
        "row_id": "poly_x",
        "market_shape": "point_in_time_threshold",
        "asset_or_family": "BTC",
        "comparator": ">",
        "threshold": 100000.0,
        "target_date": "December 31, 2026",
        "target_time": "4:00 PM ET",
        "timezone": "ET",
        "settlement_source_present": True,
    }
    input_dir = _setup(tmp_path, kalshi_rows=[kalshi_row], polymarket_pit_rows=[pm_row])
    report = build_kalshi_crypto_typed_key_audit_report(input_dir=input_dir, generated_at=_NOW)
    out = report["rows"][0]
    assert HINT_POSSIBLE_POLYMARKET_PEER in out["peer_hints"]
    assert out["can_create_candidate_pair"] is False
    assert report["summary"]["possible_polymarket_peer_rows"] >= 1


def test_no_saved_peer_when_no_inputs(tmp_path: Path) -> None:
    row = _kalshi_btc_pit_row()
    input_dir = _setup(tmp_path, kalshi_rows=[row])
    report = build_kalshi_crypto_typed_key_audit_report(input_dir=input_dir, generated_at=_NOW)
    out = report["rows"][0]
    assert HINT_NO_SAVED_PEER in out["peer_hints"]
    assert HINT_POSSIBLE_CDNA_PEER not in out["peer_hints"]
    assert HINT_POSSIBLE_POLYMARKET_PEER not in out["peer_hints"]


def test_no_paper_candidate_emitted(tmp_path: Path) -> None:
    rows = [_kalshi_btc_pit_row(ticker=f"KXBTC-26MAY{i:02d}17-T86249.99") for i in range(1, 4)]
    input_dir = _setup(tmp_path, kalshi_rows=rows)
    json_output = tmp_path / "audit.json"
    md_output = tmp_path / "audit.md"
    write_kalshi_crypto_typed_key_audit_files(
        input_dir=input_dir,
        json_output=json_output,
        markdown_output=md_output,
        generated_at=_NOW,
    )
    forbidden = "PAPER" + "_CANDIDATE"
    for path in (json_output, md_output):
        text = path.read_text(encoding="utf-8")
        assert forbidden not in text
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["summary"]["exact_ready_rows"] == 0
    assert payload["summary"]["paper_candidate_rows"] == 0
    for row in payload["rows"]:
        assert row["can_create_candidate_pair"] is False
        assert row["can_create_paper_candidate"] is False
        assert row["exact_ready"] is False
        assert row["paper_candidate"] is False
        assert row["execution_ready"] is False


def test_no_private_or_auth_strings_in_source_or_outputs(tmp_path: Path) -> None:
    row = _kalshi_btc_pit_row()
    input_dir = _setup(tmp_path, kalshi_rows=[row])
    json_output = tmp_path / "audit.json"
    md_output = tmp_path / "audit.md"
    write_kalshi_crypto_typed_key_audit_files(
        input_dir=input_dir,
        json_output=json_output,
        markdown_output=md_output,
        generated_at=_NOW,
    )
    module_path = Path("relative_value") / "kalshi_crypto_typed_key_audit.py"
    source_text = module_path.read_text(encoding="utf-8")
    output_text = json_output.read_text(encoding="utf-8") + md_output.read_text(encoding="utf-8")
    # Strict patterns that would only appear in *real* code touching private endpoints,
    # auth headers, wallet/signing surfaces, or HTTP write methods. We do NOT match the
    # natural-language list of things the module promises to never do.
    forbidden_patterns = (
        '"Authorization"',
        "'Authorization'",
        "Bearer ",
        "X-API-Key",
        "x-api-key",
        "PRIVATE_KEY",
        "private_key=",
        "signTypedData",
        "eth_signTypedData",
        "mnemonic_phrase",
        "seed_phrase=",
        "Cloudflare-Bypass-Token",
        'method="POST"',
        "method='POST'",
        'method="DELETE"',
        "method='DELETE'",
        "urlopen(",
        "requests.post(",
        "requests.put(",
        "requests.delete(",
        "/auth/api-key",
        "/clob/auth",
        "kalshi.com/trade-api/v2/orders",
        "kalshi.com/trade-api/v2/positions",
        "kalshi.com/trade-api/v2/balance",
        "kalshi.com/trade-api/v2/fills",
    )
    for forbidden in forbidden_patterns:
        assert forbidden not in source_text, f"forbidden token in module source: {forbidden}"
        assert forbidden not in output_text, f"forbidden token in outputs: {forbidden}"


def test_cli_writes_outputs_with_safe_summary_line(tmp_path: Path, capsys) -> None:
    row = _kalshi_btc_pit_row()
    input_dir = _setup(tmp_path, kalshi_rows=[row])
    result = scan.main(
        [
            "kalshi-crypto-typed-key-audit",
            "--input-dir",
            str(input_dir),
            "--json-output",
            str(tmp_path / "audit.json"),
            "--markdown-output",
            str(tmp_path / "audit.md"),
        ]
    )
    assert result == 0
    stdout = capsys.readouterr().out
    assert "kalshi_crypto_typed_key_audit=OK" in stdout
    assert "diagnostic_only=true" in stdout
    assert "exact_ready_rows=0" in stdout
    assert "paper_candidate_rows=0" in stdout
    payload = json.loads((tmp_path / "audit.json").read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "kalshi_crypto_typed_key_audit_v1"
    assert payload["summary"]["kalshi_crypto_rows"] == 1
    assert payload["summary"]["point_in_time_rows"] == 1


def test_ops_status_surfaces_kalshi_crypto_typed_key_audit(tmp_path: Path) -> None:
    audit_payload = {
        "schema_kind": "kalshi_crypto_typed_key_audit_v1",
        "schema_version": 1,
        "source": "kalshi_crypto_typed_key_audit_v1",
        "generated_at": "2026-05-27T00:00:00+00:00",
        "input_dir": "reports",
        "diagnostic_only": True,
        "saved_files_only": True,
        "summary": {
            "kalshi_crypto_rows": 1237,
            "typed_complete_rows": 540,
            "point_in_time_rows": 800,
            "deadline_or_range_hit_rows": 400,
            "ambiguous_rows": 37,
            "rows_with_asset": 1237,
            "rows_with_threshold": 1100,
            "rows_with_comparator": 1100,
            "rows_with_target_date": 1237,
            "rows_with_target_time": 1100,
            "rows_with_timezone": 1100,
            "rows_with_settlement_source": 1000,
            "rows_with_settlement_source_url": 0,
            "rows_with_quote": 50,
            "enriched_files_read": 3,
            "rows_with_existing_top_of_book": 1237,
            "rows_with_fresh_orderbook": 50,
            "rows_with_stale_top_of_book": 1187,
            "rows_with_full_orderbook_missing": 1187,
            "rows_with_bid_ask_size_timestamp": 50,
            "kalshi_live_orderbook_fetch_supported": True,
            "kalshi_live_orderbook_fetch_not_enabled_or_missing_count": 1187,
            "possible_cdna_peer_rows": 4,
            "possible_polymarket_peer_rows": 1,
            "no_saved_peer_rows": 1232,
            "date_threshold_comparator_overlap_rows": 3,
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "next_action": "MANUAL_REVIEW_DATE_THRESHOLD_COMPARATOR_OVERLAPS",
            "next_action_reason": "...",
            "asset_counts": {"BTC": 1112, "ETH": 125},
            "shape_counts": {"point_in_time_threshold": 800, "range_bucket": 400, "ambiguous": 37},
            "top_blockers": [
                {"blocker": "kalshi_crypto_missing_settlement_source", "count": 237},
                {"blocker": "missing_quote", "count": 1187},
            ],
            "top_20_by_completeness": [],
            "top_10_peer_hint_rows": [],
        },
        "rows": [],
        "warnings": [],
        "safety": {"diagnostic_only": True},
    }
    (tmp_path / "kalshi_crypto_typed_key_audit.json").write_text(json.dumps(audit_payload), encoding="utf-8")
    from relative_value.relative_value_ops_status import (
        build_relative_value_ops_status_report,
        render_relative_value_ops_status_markdown,
    )

    report = build_relative_value_ops_status_report(input_dir=tmp_path, generated_at=_NOW)
    block = report["summary"]["kalshi_crypto_typed_key_audit"]
    assert block["present"] is True
    assert block["kalshi_crypto_rows"] == 1237
    assert block["typed_complete_rows"] == 540
    assert block["possible_cdna_peer_rows"] == 4
    assert block["date_threshold_comparator_overlap_rows"] == 3
    assert block["enriched_files_read"] == 3
    assert block["rows_with_fresh_orderbook"] == 50
    assert block["rows_with_stale_top_of_book"] == 1187
    assert block["rows_with_bid_ask_size_timestamp"] == 50
    assert block["kalshi_live_orderbook_fetch_supported"] is True
    assert block["exact_ready_rows"] == 0
    assert block["paper_candidate_rows"] == 0
    md = render_relative_value_ops_status_markdown(report)
    assert "kalshi_crypto_typed_key_audit" in md
    assert "typed_complete_rows: `540`" in md
    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in md
