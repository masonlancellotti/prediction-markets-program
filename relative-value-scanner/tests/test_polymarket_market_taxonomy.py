from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from relative_value.polymarket_market_taxonomy import (
    FAMILY_CRYPTO,
    FAMILY_POLITICS_ELECTION_RESULT,
    FAMILY_SPORTS_GAME,
    FAMILY_TECH_AI,
    PUBLIC_READ_HEADERS,
    SHAPE_ELECTION_WINNER,
    SHAPE_POINT_IN_TIME_THRESHOLD,
    SHAPE_SPORTS_MONEYLINE,
    SHAPE_TECH_RELEASE_OR_PRODUCT_EVENT,
    SHAPE_UNKNOWN_OR_COMPOUND,
    build_polymarket_market_universe_report,
)


NOW = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)


def _build(tmp_path: Path, fake_http, *, include_books: bool = False) -> dict:
    return build_polymarket_market_universe_report(
        output_dir=tmp_path / "manual_snapshots" / "polymarket_universe",
        limit=20,
        include_books=include_books,
        generated_at=NOW,
        http_get=fake_http,
        max_pages=1,
    )


def test_universe_classifies_crypto_threshold_row(tmp_path: Path) -> None:
    payload = {
        "markets": [
            {
                "id": "m-btc",
                "slug": "bitcoin-above-100k-on-may-26-2026-1am-et",
                "question": "Will Bitcoin be above $100k at 1AM ET on May 26, 2026?",
                "rules": "This market resolves using Binance BTC/USDT 1-minute candles.",
                "clobTokenIds": '["yes-token", "no-token"]',
            }
        ]
    }

    def fake_http(url: str, timeout_seconds: float):
        if "/markets?" in url:
            return payload
        return []

    report = _build(tmp_path, fake_http)
    rows = [row for row in report["taxonomy_rows"] if row.get("market_id") == "m-btc"]

    assert len(rows) == 1
    row = rows[0]
    assert row["family"] == FAMILY_CRYPTO
    assert row["market_shape"] == SHAPE_POINT_IN_TIME_THRESHOLD
    assert row["typed_keys"]["asset"] == "BTC"
    assert row["typed_keys"]["threshold_value"] == 100000.0
    assert row["typed_keys"]["price_source_index"] == "Binance"
    assert row["typed_key_complete"] is True
    assert row["token_ids"] == ["yes-token", "no-token"]
    assert Path(row["raw_source_file"]).exists()


def test_universe_classifies_election_winner_row(tmp_path: Path) -> None:
    payload = {
        "events": [
            {
                "id": "e-election",
                "slug": "will-trump-win-the-2028-presidential-election",
                "title": "Will Trump win the 2028 presidential election?",
                "markets": [
                    {
                        "id": "m-election",
                        "question": "Will Trump win the 2028 presidential election?",
                        "rules": "This market resolves according to Associated Press projection or certified results.",
                    }
                ],
            }
        ]
    }

    def fake_http(url: str, timeout_seconds: float):
        if "/events?" in url:
            return payload
        return []

    report = _build(tmp_path, fake_http)
    row = next(row for row in report["taxonomy_rows"] if row.get("market_id") == "m-election")

    assert row["family"] == FAMILY_POLITICS_ELECTION_RESULT
    assert row["market_shape"] == SHAPE_ELECTION_WINNER
    assert row["typed_keys"]["office_or_contest"] == "PRESIDENT"
    assert row["typed_keys"]["candidate_or_party"] == "Trump"
    assert row["typed_keys"]["result_basis"] == "certified_result"


def test_universe_classifies_tech_ai_release_row(tmp_path: Path) -> None:
    payload = {
        "markets": [
            {
                "id": "m-ai",
                "slug": "will-openai-release-gpt-5-before-2027",
                "question": "Will OpenAI release GPT-5 before 2027?",
                "rules": "Resolves based on an official OpenAI product release announcement.",
            }
        ]
    }

    def fake_http(url: str, timeout_seconds: float):
        if "/markets?" in url:
            return payload
        return []

    report = _build(tmp_path, fake_http)
    row = next(row for row in report["taxonomy_rows"] if row.get("market_id") == "m-ai")

    assert row["family"] == FAMILY_TECH_AI
    assert row["market_shape"] == SHAPE_TECH_RELEASE_OR_PRODUCT_EVENT
    assert row["typed_keys"]["entity"] == "OpenAI"


def test_universe_classifies_sports_game_row(tmp_path: Path) -> None:
    payload = {
        "markets": [
            {
                "id": "m-sports",
                "slug": "nba-lakers-vs-celtics-moneyline",
                "question": "NBA Lakers vs Celtics moneyline",
                "rules": "Overtime counts. Void if the game is canceled.",
            }
        ]
    }

    def fake_http(url: str, timeout_seconds: float):
        if "/markets?" in url:
            return payload
        return []

    report = _build(tmp_path, fake_http)
    row = next(row for row in report["taxonomy_rows"] if row.get("market_id") == "m-sports")

    assert row["family"] == FAMILY_SPORTS_GAME
    assert row["market_shape"] == SHAPE_SPORTS_MONEYLINE
    assert row["typed_keys"]["league"] == "NBA"
    assert row["typed_keys"]["market_type"] == SHAPE_SPORTS_MONEYLINE


def test_vague_news_and_unknown_rows_are_discovery_only(tmp_path: Path) -> None:
    payload = {
        "markets": [
            {
                "id": "m-news",
                "slug": "will-congress-pass-a-major-bill",
                "question": "Will Congress pass a major bill?",
            },
            {
                "id": "m-unknown",
                "slug": "will-something-weird-happen",
                "question": "Will something weird happen?",
            },
        ]
    }

    def fake_http(url: str, timeout_seconds: float):
        if "/markets?" in url:
            return payload
        return []

    report = _build(tmp_path, fake_http)
    rows = {row["market_id"]: row for row in report["taxonomy_rows"]}

    assert "vague_news_or_policy_discovery_only" in rows["m-news"]["blockers"]
    assert rows["m-unknown"]["market_shape"] == SHAPE_UNKNOWN_OR_COMPOUND
    assert report["unknown_shape_clusters"]


def test_no_auth_headers_and_no_book_calls_by_default(tmp_path: Path) -> None:
    assert "Authorization" not in PUBLIC_READ_HEADERS
    assert "X-API-KEY" not in {key.upper() for key in PUBLIC_READ_HEADERS}
    urls: list[str] = []

    def fake_http(url: str, timeout_seconds: float):
        urls.append(url)
        if "/markets?" in url:
            return {
                "markets": [
                    {
                        "id": "m-btc",
                        "slug": "bitcoin-above-100k-on-may-26-2026-1am-et",
                        "question": "Will Bitcoin be above $100k at 1AM ET on May 26, 2026?",
                        "rules": "Resolves using Binance BTC/USDT.",
                        "clobTokenIds": '["token-a"]',
                    }
                ]
            }
        return []

    report = _build(tmp_path, fake_http, include_books=False)

    assert report["summary"]["books_saved"] == 0
    assert not any("/book?" in url for url in urls)
    assert report["safety"]["authenticated_endpoints_used"] is False
    assert report["safety"]["orders_or_cancellations"] is False
    assert report["safety"]["paper_candidate_emitted"] is False
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_include_books_calls_public_book_endpoint_only(tmp_path: Path) -> None:
    urls: list[str] = []

    def fake_http(url: str, timeout_seconds: float):
        urls.append(url)
        if "/book?" in url:
            return {"bids": [], "asks": []}
        if "/markets?" in url:
            return {
                "markets": [
                    {
                        "id": "m-btc",
                        "slug": "bitcoin-above-100k-on-may-26-2026-1am-et",
                        "question": "Will Bitcoin be above $100k at 1AM ET on May 26, 2026?",
                        "rules": "Resolves using Binance BTC/USDT.",
                        "clobTokenIds": '["token-a"]',
                    }
                ]
            }
        return []

    report = _build(tmp_path, fake_http, include_books=True)

    assert report["summary"]["books_saved"] == 1
    assert "https://clob.polymarket.com/book?token_id=token-a" in urls
    assert not any("/order" in url.lower() or "/cancel" in url.lower() for url in urls)


def test_title_only_source_does_not_complete_typed_keys(tmp_path: Path) -> None:
    payload = {
        "markets": [
            {
                "id": "m-title-only",
                "slug": "bitcoin-binance-above-100k-on-may-26-2026-1am-et",
                "question": "Will Bitcoin on Binance be above $100k at 1AM ET on May 26, 2026?",
            }
        ]
    }

    def fake_http(url: str, timeout_seconds: float):
        if "/markets?" in url:
            return payload
        return []

    report = _build(tmp_path, fake_http)
    row = next(row for row in report["taxonomy_rows"] if row.get("market_id") == "m-title-only")

    assert row["typed_keys"].get("price_source_index") is None
    assert row["typed_key_complete"] is False
    assert "unknown_source_or_rules" in row["blockers"]
