from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from relative_value.polymarket_public_discovery import (
    PUBLIC_READ_HEADERS,
    build_polymarket_crypto_discovery_report,
)
from relative_value.polymarket_crypto_discovery_normalizer import (
    SHAPE_DEADLINE_OR_DATE_RANGE_HIT,
    SHAPE_MONTHLY_EXTREME_HIGH_LOW,
    SHAPE_POINT_IN_TIME,
    build_polymarket_crypto_discovery_normalization_report,
)
from relative_value.settlement_evidence_burden import (
    TIER_DISCOVERY_READY,
    TIER_EXACT_PAYOFF_REVIEW_READY,
    TIER_EXECUTION_EVALUATION_READY,
    build_settlement_evidence_burden_report,
)


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def _candidate_event() -> dict:
    return {
        "events": [
            {
                "id": "evt-btc-may",
                "slug": "what-price-will-bitcoin-hit-in-may-2026",
                "title": "What price will Bitcoin hit in May 2026?",
                "markets": [
                    {
                        "id": "market-btc-100k",
                        "conditionId": "cond-btc-100k",
                        "slug": "bitcoin-above-100k-in-may-2026",
                        "question": "Will Bitcoin be above $100k in May 2026?",
                        "rules": (
                            "This market resolves based on the BTC price threshold. "
                            "If Binance BTC/USDT candles trade above $100k during May 2026, it resolves Yes."
                        ),
                        "clobTokenIds": '["token-yes", "token-no"]',
                    }
                ],
            }
        ]
    }


def _build(tmp_path: Path, fake_http, *, include_books: bool = False) -> dict:
    return build_polymarket_crypto_discovery_report(
        output_dir=tmp_path / "manual_snapshots" / "polymarket_crypto",
        limit=20,
        include_books=include_books,
        generated_at=NOW,
        http_get=fake_http,
        max_pages=1,
    )


def _write_discovery(path: Path, candidates: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "polymarket_crypto_public_discovery_v1",
                "generated_at": NOW.isoformat(),
                "candidates": candidates,
            }
        ),
        encoding="utf-8",
    )


def _normalize_discovery(tmp_path: Path, candidates: list[dict]) -> dict:
    discovery = tmp_path / "reports" / "polymarket_crypto_discovery.json"
    _write_discovery(discovery, candidates)
    return build_polymarket_crypto_discovery_normalization_report(
        discovery_path=discovery,
        output_dir=tmp_path / "reports" / "manual_snapshots" / "polymarket_crypto" / "normalized",
        generated_at=NOW,
    )


def _burden(tmp_path: Path) -> dict:
    return build_settlement_evidence_burden_report(input_dir=tmp_path / "reports", generated_at=NOW)


def test_endpoint_candidate_btc_event_is_saved_and_reported(tmp_path: Path) -> None:
    def fake_http(url: str, timeout_seconds: float):
        if "/events?" in url:
            return _candidate_event()
        return []

    report = _build(tmp_path, fake_http)

    assert report["source"] == "polymarket_crypto_public_discovery_v1"
    assert report["summary"]["seed_url_candidates"] == 6
    assert report["summary"]["candidate_events"] >= 1
    assert report["summary"]["candidate_markets"] >= 1
    assert report["summary"]["threshold_like_candidates"] >= 1
    candidate = next(row for row in report["candidates"] if row.get("market_slug") == "bitcoin-above-100k-in-may-2026")
    assert candidate["event_slug"] == "what-price-will-bitcoin-hit-in-may-2026"
    assert candidate["market_slug"] == "bitcoin-above-100k-in-may-2026"
    assert candidate["token_ids"] == ["token-yes", "token-no"]
    assert candidate["source_url"] == "https://polymarket.com/event/what-price-will-bitcoin-hit-in-may-2026"
    assert Path(candidate["candidate_file"]).exists()
    assert report["raw_files_written"]


def test_compound_or_non_price_rows_are_excluded(tmp_path: Path) -> None:
    payload = {
        "events": [
            {
                "id": "evt-gta",
                "slug": "will-bitcoin-hit-1m-before-gta-vi",
                "title": "Will Bitcoin hit $1m before GTA VI?",
                "markets": [
                    {
                        "id": "market-gta",
                        "slug": "will-bitcoin-hit-1m-before-gta-vi",
                        "question": "Will Bitcoin hit $1m before GTA VI?",
                    }
                ],
            },
            {
                "id": "evt-album",
                "slug": "will-ethereum-price-be-mentioned-on-an-album",
                "title": "Will Ethereum price be mentioned on an album?",
                "markets": [{"id": "market-album", "slug": "ethereum-price-album-2026"}],
            },
        ]
    }

    def fake_http(url: str, timeout_seconds: float):
        if "/events?" in url:
            return payload
        return []

    report = _build(tmp_path, fake_http)

    assert report["summary"]["threshold_like_candidates"] == report["summary"]["seed_url_candidates"]
    assert report["summary"]["excluded_candidates_by_reason"]["excluded_compound_or_non_price_market"] >= 1


def test_empty_public_arrays_warn_without_crashing(tmp_path: Path) -> None:
    def fake_http(url: str, timeout_seconds: float):
        return []

    report = _build(tmp_path, fake_http)

    assert report["summary"]["threshold_like_candidates"] == report["summary"]["seed_url_candidates"]
    assert report["summary"]["warning_count"] >= 1
    assert {warning["reason_code"] for warning in report["warnings"]} == {"empty_public_response"}


def test_token_ids_do_not_trigger_clob_when_include_books_false(tmp_path: Path) -> None:
    urls: list[str] = []

    def fake_http(url: str, timeout_seconds: float):
        urls.append(url)
        if "/events?" in url:
            return _candidate_event()
        return []

    report = _build(tmp_path, fake_http, include_books=False)

    assert report["summary"]["token_ids_available"] == 1
    assert report["summary"]["books_saved"] == 0
    assert not any("/book?" in url for url in urls)


def test_include_books_calls_public_clob_book_endpoint(tmp_path: Path) -> None:
    urls: list[str] = []

    def fake_http(url: str, timeout_seconds: float):
        urls.append(url)
        if "/book?" in url:
            return {"bids": [], "asks": []}
        if "/events?" in url:
            return _candidate_event()
        return []

    report = _build(tmp_path, fake_http, include_books=True)

    assert report["summary"]["books_saved"] == 2
    assert any("https://clob.polymarket.com/book?token_id=token-yes" == url for url in urls)
    assert any("https://clob.polymarket.com/book?token_id=token-no" == url for url in urls)
    # Book attachment diagnostic: each candidate carries a token_id->book_file map.
    candidate = next(row for row in report["candidates"] if row.get("token_ids"))
    book_map = candidate["book_files_by_token_id"]
    assert set(book_map.keys()) == {"token-yes", "token-no"}
    for path in book_map.values():
        assert Path(path).exists()
    assert report["summary"]["book_token_ids_saved_count"] == 2
    assert report["summary"]["book_token_ids_failed_count"] == 0
    assert report["summary"]["candidates_with_all_books_attached_count"] == 1


def test_include_books_records_failures_separately_from_saved(tmp_path: Path) -> None:
    def fake_http(url: str, timeout_seconds: float):
        if "/book?" in url and "token-no" in url:
            raise RuntimeError("public Polymarket endpoint returned HTTP 404")
        if "/book?" in url:
            return {"bids": [], "asks": []}
        if "/events?" in url:
            return _candidate_event()
        return []

    report = _build(tmp_path, fake_http, include_books=True)

    assert report["summary"]["books_saved"] == 1
    assert report["summary"]["book_token_ids_saved_count"] == 1
    assert report["summary"]["book_token_ids_failed_count"] == 1
    assert report["summary"]["candidates_with_any_book_attached_count"] == 1
    assert report["summary"]["candidates_with_all_books_attached_count"] == 0
    candidate = next(row for row in report["candidates"] if row.get("token_ids"))
    assert "token-yes" in candidate["book_files_by_token_id"]
    assert "token-no" not in candidate["book_files_by_token_id"]
    statuses = {item["status"] for item in report["book_files_attempted"]}
    assert statuses == {"saved", "failed"}


def test_no_auth_headers_or_candidate_status_are_emitted(tmp_path: Path) -> None:
    assert "Authorization" not in PUBLIC_READ_HEADERS
    assert "X-API-KEY" not in {key.upper() for key in PUBLIC_READ_HEADERS}

    def fake_http(url: str, timeout_seconds: float):
        if "/events?" in url:
            return _candidate_event()
        return []

    report = _build(tmp_path, fake_http)
    encoded = json.dumps(report)

    assert report["safety"]["authenticated_endpoints_used"] is False
    assert report["safety"]["orders_or_cancellations"] is False
    assert report["safety"]["candidate_pair_creation"] is False
    assert report["safety"]["paper_candidate_emitted"] is False
    assert "PAPER_CANDIDATE" not in encoded


def test_discovery_monthly_extreme_normalizes_to_ingestible_fixture(tmp_path: Path) -> None:
    report = _normalize_discovery(
        tmp_path,
        [
            {
                "row_index": 0,
                "event_slug": "what-price-will-bitcoin-hit-in-may-2026",
                "market_slug": "will-bitcoin-hit-100k-in-may-2026",
                "title": "What price will Bitcoin hit in May 2026?",
                "question": "Will Bitcoin hit $100k in May 2026?",
                "description": "Resolution uses Binance BTC/USDT 1-minute candles at https://www.binance.com/en/trade/BTC_USDT.",
                "source_url": "https://polymarket.com/event/what-price-will-bitcoin-hit-in-may-2026",
                "token_ids": ["yes-token", "no-token"],
            }
        ],
    )

    assert report["summary"]["discovery_candidates_read"] == 1
    assert report["summary"]["normalized_fixtures_written"] == 1
    assert report["summary"]["markets_expanded"] == 1
    assert report["summary"]["monthly_extreme_count"] == 1
    assert report["summary"]["token_ids_carried"] == 2
    fixture = json.loads(Path(report["normalized_fixtures"][0]["fixture_path"]).read_text(encoding="utf-8"))
    assert fixture["fixture_kind"] == "manual_polymarket_crypto_event_page_snapshot"
    assert fixture["settlement_shape"] == SHAPE_MONTHLY_EXTREME_HIGH_LOW
    assert fixture["settlement_source"] == "Binance BTC/USDT 1-minute candles"
    assert fixture["markets"][0]["token_ids"] == ["yes-token", "no-token"]

    burden = _burden(tmp_path)
    rows = [row for row in burden["markets"] if row["event_slug"] == "what-price-will-bitcoin-hit-in-may-2026"]
    assert len(rows) == 1
    row = rows[0]
    assert row["typed_key_evidence"]["asset"]["value"] == "BTC"
    assert row["typed_key_evidence"]["threshold_value"]["value"] == 100000.0
    assert row["typed_key_evidence"]["price_source_index"]["value"] == "Binance BTC/USDT 1-minute candles"
    assert "monthly_extreme_window_not_point_in_time" in row["blockers"]
    assert "deadline_or_date_range_hit_window_not_point_in_time" not in row["blockers"]
    assert "not_same_payoff_with_kalshi_point_in_time" in row["blockers"]
    assert burden["summary"]["by_review_readiness_tier"].get(TIER_EXACT_PAYOFF_REVIEW_READY, 0) == 0
    assert burden["summary"]["by_review_readiness_tier"].get(TIER_EXECUTION_EVALUATION_READY, 0) == 0


def test_discovery_point_in_time_normalizes_to_ingestible_fixture(tmp_path: Path) -> None:
    report = _normalize_discovery(
        tmp_path,
        [
            {
                "row_index": 1,
                "event_slug": "bitcoin-above-100k-on-may-25-2026",
                "market_slug": "bitcoin-above-100k-on-may-25-2026",
                "title": "Bitcoin above $100,000 at 5 PM EDT on May 25, 2026?",
                "question": "Will Bitcoin be above $100,000 at 5 PM EDT on May 25, 2026?",
                "description": "The resolution source is Chainlink BTC/USD at https://data.chain.link/streams/btc-usd.",
                "source_url": "https://polymarket.com/event/bitcoin-above-100k-on-may-25-2026",
                "token_ids": ["yes-token"],
            }
        ],
    )

    fixture = json.loads(Path(report["normalized_fixtures"][0]["fixture_path"]).read_text(encoding="utf-8"))
    assert fixture["settlement_shape"] == SHAPE_POINT_IN_TIME
    assert fixture["measurement_date"] == "May 25, 2026"
    assert fixture["measurement_time"] == "5 PM EDT"
    assert fixture["timezone"] == "EDT"
    assert fixture["settlement_window"] == "point_in_time"
    assert fixture["settlement_source"] == "Chainlink BTC/USD data stream"
    rows = [row for row in _burden(tmp_path)["markets"] if row["event_slug"] == "bitcoin-above-100k-on-may-25-2026"]
    assert len(rows) == 1
    assert rows[0]["settlement_window"] == "point_in_time"
    assert rows[0]["typed_key_evidence"]["measurement_date"]["value"] == "May 25, 2026"
    assert rows[0]["typed_key_evidence"]["price_source_index"]["value"] == "Chainlink BTC/USD data stream"


def test_discovery_compound_market_is_skipped(tmp_path: Path) -> None:
    report = _normalize_discovery(
        tmp_path,
        [
            {
                "event_slug": "what-will-happen-before-gta-vi",
                "market_slug": "will-bitcoin-hit-1m-before-gta-vi",
                "title": "What will happen before GTA VI?",
                "question": "Will Bitcoin hit $1m before GTA VI?",
                "description": "This is not a standalone BTC price threshold settlement source.",
            }
        ],
    )

    assert report["summary"]["normalized_fixtures_written"] == 0
    assert report["summary"]["skipped_count_by_reason"] == {"compound_or_non_price_market": 1}


def test_discovery_missing_source_stays_blocked(tmp_path: Path) -> None:
    report = _normalize_discovery(
        tmp_path,
        [
            {
                "event_slug": "bitcoin-above-100k-on-may-25-2026",
                "market_slug": "bitcoin-above-100k-on-may-25-2026",
                "title": "Bitcoin above $100,000 at 5 PM EDT on May 25, 2026?",
                "question": "Will Bitcoin be above $100,000 at 5 PM EDT on May 25, 2026?",
                "description": "This market resolves based on the referenced BTC price.",
            }
        ],
    )

    assert report["summary"]["normalized_fixtures_written"] == 1
    rows = [row for row in _burden(tmp_path)["markets"] if row["event_slug"] == "bitcoin-above-100k-on-may-25-2026"]
    assert len(rows) == 1
    assert rows[0]["review_readiness_tier"] == TIER_DISCOVERY_READY
    assert "price_source_index" in rows[0]["missing_typed_keys"]
    assert "missing_price_source_index" in rows[0]["blockers"]


def test_discovery_range_hit_shape_and_safety_do_not_emit_paper(tmp_path: Path) -> None:
    report = _normalize_discovery(
        tmp_path,
        [
            {
                "event_slug": "when-will-ethereum-hit-10k",
                "market_slug": "will-ethereum-reach-10000-by-december-31-2026",
                "title": "When will Ethereum hit $10k?",
                "question": "Will Ethereum reach $10,000 by December 31, 2026?",
                "description": "Resolution uses Binance ETH/USDT 1-minute candles at https://www.binance.com/en/trade/ETH_USDT.",
            }
        ],
    )

    fixture = json.loads(Path(report["normalized_fixtures"][0]["fixture_path"]).read_text(encoding="utf-8"))
    assert fixture["settlement_shape"] == SHAPE_DEADLINE_OR_DATE_RANGE_HIT
    assert fixture["asset"] == "ETH"
    assert fixture["markets"][0]["threshold"] == 10000.0
    # Shape-specific blocker should be used for the deadline/range-hit row, not the
    # monthly-extreme blocker label.
    assert "deadline_or_date_range_hit_window_not_point_in_time" in fixture["blockers"]
    assert "monthly_extreme_window_not_point_in_time" not in fixture["blockers"]
    assert report["safety"]["paper_candidate_emitted"] is False
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_discovery_book_attachment_carries_token_paths_into_normalized_fixture(tmp_path: Path) -> None:
    report = _normalize_discovery(
        tmp_path,
        [
            {
                "row_index": 0,
                "event_slug": "when-will-bitcoin-hit-150k",
                "market_slug": "will-bitcoin-hit-150k-by-december-31-2026",
                "title": "When will Bitcoin hit $150k?",
                "question": "Will Bitcoin hit $150k by December 31, 2026?",
                "description": "Resolution uses Binance BTC/USDT 1-minute candles at https://www.binance.com/en/trade/BTC_USDT.",
                "token_ids": ["token-yes", "token-no"],
                "book_files_by_token_id": {
                    "token-yes": "reports/manual_snapshots/polymarket_crypto/20260101_000000Z/book_token-yes.json",
                },
            }
        ],
    )

    assert report["summary"]["book_files_attached_total"] == 1
    assert report["summary"]["fixtures_with_any_book_attached"] == 1
    assert report["summary"]["fixtures_with_all_tokens_with_books"] == 0
    fixture = json.loads(Path(report["normalized_fixtures"][0]["fixture_path"]).read_text(encoding="utf-8"))
    attached = fixture["markets"][0]["book_files_by_token_id"]
    assert set(attached.keys()) == {"token-yes"}
    assert attached["token-yes"].endswith("book_token-yes.json")


# ---------------------------------------------------------------------------
# Targeted Polymarket discovery (Part B)
# ---------------------------------------------------------------------------


def test_targeted_query_uses_server_side_search_and_filters_client_side(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_http(url: str, timeout_seconds: float):
        calls.append(url)
        # Server-side: respond to the targeted search with two BTC events on different dates.
        if "/events?" in url:
            return {
                "events": [
                    {
                        "id": "evt-btc-may-29",
                        "slug": "bitcoin-above-on-may-29-2026",
                        "title": "Will Bitcoin be above $100k on May 29, 2026?",
                        "markets": [
                            {
                                "id": "m-btc-may-29",
                                "conditionId": "cond-btc-may-29",
                                "slug": "bitcoin-above-100k-may-29-2026",
                                "question": "Will Bitcoin be above $100k on May 29, 2026?",
                                "rules": (
                                    "Resolves Yes if Binance BTC/USDT price is above $100k at 5pm ET on May 29, 2026."
                                ),
                                "clobTokenIds": '["token-yes-may-29", "token-no-may-29"]',
                            }
                        ],
                    },
                    {
                        "id": "evt-btc-jul-01",
                        "slug": "bitcoin-above-on-july-01-2026",
                        "title": "Will Bitcoin be above $100k on July 01, 2026?",
                        "markets": [
                            {
                                "id": "m-btc-jul-01",
                                "conditionId": "cond-btc-jul-01",
                                "slug": "bitcoin-above-100k-july-01-2026",
                                "question": "Will Bitcoin be above $100k on July 01, 2026?",
                                "rules": (
                                    "Resolves Yes if Binance BTC/USDT price is above $100k at 5pm ET on July 01, 2026."
                                ),
                                "clobTokenIds": '["token-yes-jul-01", "token-no-jul-01"]',
                            }
                        ],
                    },
                ]
            }
        # /markets endpoint - return empty so we only count event side.
        return {"markets": []}

    report = build_polymarket_crypto_discovery_report(
        output_dir=tmp_path / "manual_snapshots" / "polymarket_crypto",
        limit=20,
        include_books=False,
        generated_at=NOW,
        http_get=fake_http,
        max_pages=1,
        targeted_query="bitcoin May 29, 2026",
        targeted_asset="BTC",
        targeted_target_date="2026-05-29",
    )
    # Server-side: at least one targeted search URL.
    assert any("search=bitcoin" in url.lower() for url in calls)
    # Client-side filter dropped the July 1 event but kept the May 29 event.
    candidate_slugs = {row["event_slug"] for row in report["candidates"] if row.get("event_slug")}
    assert "bitcoin-above-on-may-29-2026" in candidate_slugs
    assert "bitcoin-above-on-july-01-2026" not in candidate_slugs
    excluded = report["excluded_candidates_by_reason"]
    assert excluded.get("targeted_client_side_filter_excluded", 0) >= 1
    # Targeted block in summary.
    targeted = report["targeted_filter"]
    assert targeted["active"] is True
    assert targeted["targeted_filter_mode"] == "client_side_public_discovery_plus_server_search"
    assert targeted["asset"] == "BTC"
    assert targeted["target_date"] == "2026-05-29"
    assert targeted["rows_found"] >= 1
    assert targeted["rows_with_token_ids"] >= 1
    assert targeted["deadline_or_range_hit_treated_as_point_in_time"] is False
    # Targeted summary surfaced.
    assert report["summary"]["targeted_filter_active"] is True
    assert report["summary"]["targeted_asset"] == "BTC"
    # No paper candidate emitted anywhere.
    forbidden = "PAPER" + "_CANDIDATE"
    text = json.dumps(report)
    assert forbidden not in text
    # No auth header keys added.
    assert "Authorization" not in PUBLIC_READ_HEADERS


def test_targeted_query_does_not_treat_deadline_as_point_in_time(tmp_path: Path) -> None:
    def fake_http(url: str, timeout_seconds: float):
        if "/events?" in url:
            return {
                "events": [
                    {
                        "id": "evt-btc-touch",
                        "slug": "will-bitcoin-touch-200k-before-2027",
                        "title": "Will Bitcoin touch $200k before 2027?",
                        "markets": [
                            {
                                "id": "m-btc-touch",
                                "conditionId": "cond-btc-touch",
                                "slug": "bitcoin-touch-200k-before-2027",
                                "question": "Will Bitcoin touch $200k any time before December 31, 2026?",
                                "rules": (
                                    "Resolves Yes if Binance BTC/USDT price touches $200k any time before "
                                    "the end of 2026."
                                ),
                                "clobTokenIds": '["tok-touch-yes", "tok-touch-no"]',
                            }
                        ],
                    }
                ]
            }
        return {"markets": []}

    report = build_polymarket_crypto_discovery_report(
        output_dir=tmp_path / "manual_snapshots" / "polymarket_crypto",
        limit=20,
        include_books=False,
        generated_at=NOW,
        http_get=fake_http,
        max_pages=1,
        targeted_query="bitcoin touch",
        targeted_asset="BTC",
    )
    targeted = report["targeted_filter"]
    assert targeted["deadline_or_range_hit_rows"] >= 1
    assert targeted["point_in_time_rows"] == 0
    assert targeted["deadline_or_range_hit_treated_as_point_in_time"] is False
