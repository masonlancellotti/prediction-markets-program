import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import scan
from venues.kalshi import (
    KalshiMarketFilterOptions,
    KalshiReadOnlyClient,
    build_kalshi_market_snapshot,
    normalize_kalshi_markets,
    parse_kalshi_markets_response,
)


def _sample_kalshi_response() -> dict:
    return {
        "markets": [
            {
                "ticker": "KXNBA-26MAY20-NYK",
                "event_ticker": "KXNBA-26MAY20",
                "title": "Will the Knicks win?",
                "subtitle": "Knicks vs Cavaliers",
                "status": "open",
                "yes_bid_dollars": "0.4200",
                "yes_ask_dollars": "0.4400",
                "no_ask_dollars": "0.5900",
                "volume_fp": "123.00",
                "liquidity_dollars": "456.78",
                "close_time": "2026-05-21T00:00:00Z",
            }
        ],
        "cursor": "",
    }


class _FakeResponse:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.payload.encode("utf-8")


def test_parse_kalshi_markets_response_from_markets_wrapper() -> None:
    markets = parse_kalshi_markets_response(_sample_kalshi_response())

    assert len(markets) == 1
    assert markets[0]["ticker"] == "KXNBA-26MAY20-NYK"


def test_normalize_kalshi_schema_and_prices() -> None:
    markets = parse_kalshi_markets_response(_sample_kalshi_response())
    normalized, skip_counts = normalize_kalshi_markets(
        markets,
        captured_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert skip_counts == {
        "skipped_closed_count": 0,
        "skipped_inactive_count": 0,
        "skipped_past_close_time_count": 0,
    }
    row = normalized[0]
    assert row["venue"] == "kalshi"
    assert row["event_id"] == "KXNBA-26MAY20"
    assert row["market_id"] == "KXNBA-26MAY20-NYK"
    assert row["ticker"] == "KXNBA-26MAY20-NYK"
    assert row["best_bid"] == 0.42
    assert row["best_ask"] == 0.44
    assert row["volume"] == 123.0
    assert row["liquidity"] == 456.78
    assert row["active"] is True
    assert row["closed"] is False
    assert row["status"] == "open"
    assert row["outcomes"] == [
        {"name": "Yes", "outcome_yes_token_price": 0.44},
        {"name": "No", "outcome_yes_token_price": 0.59},
    ]


def test_build_kalshi_snapshot_schema_version() -> None:
    snapshot = build_kalshi_market_snapshot(
        _sample_kalshi_response(),
        fetched_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert snapshot["schema_version"] == 1
    assert snapshot["source"] == "kalshi_markets"
    assert snapshot["market_count"] == 1
    assert snapshot["normalized_count"] == 1
    assert snapshot["event_count"] is None
    assert snapshot["captured_at"] == "2026-05-20T12:00:00+00:00"


def test_closed_and_settled_markets_are_skipped_by_default() -> None:
    response = _sample_kalshi_response()
    closed = dict(response["markets"][0], ticker="closed", status="closed")
    settled = dict(response["markets"][0], ticker="settled", status="settled")
    response["markets"] = [closed, settled]

    snapshot = build_kalshi_market_snapshot(
        response,
        fetched_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert snapshot["market_count"] == 2
    assert snapshot["normalized_count"] == 0
    assert snapshot["skipped_closed_count"] == 2
    assert snapshot["skipped_inactive_count"] == 2


def test_past_close_time_market_is_skipped_by_default() -> None:
    response = _sample_kalshi_response()
    response["markets"][0]["close_time"] = "2026-05-19T00:00:00Z"

    snapshot = build_kalshi_market_snapshot(
        response,
        fetched_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert snapshot["normalized_count"] == 0
    assert snapshot["skipped_past_close_time_count"] == 1


def test_active_status_is_kept_like_open_status() -> None:
    response = _sample_kalshi_response()
    response["markets"][0]["status"] = "active"

    snapshot = build_kalshi_market_snapshot(
        response,
        fetched_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert snapshot["normalized_count"] == 1
    assert snapshot["normalized_markets"][0]["active"] is True
    assert snapshot["normalized_markets"][0]["status"] == "active"


def test_include_flags_allow_closed_and_past_close_time() -> None:
    response = _sample_kalshi_response()
    response["markets"][0]["status"] = "closed"
    response["markets"][0]["close_time"] = "2026-05-19T00:00:00Z"

    snapshot = build_kalshi_market_snapshot(
        response,
        fetched_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        filter_options=KalshiMarketFilterOptions(
            include_closed=True,
            include_past_close_time=True,
        ),
    )

    assert snapshot["normalized_count"] == 1
    assert snapshot["skipped_closed_count"] == 0
    assert snapshot["skipped_past_close_time_count"] == 0
    assert snapshot["skipped_inactive_count"] == 0


def test_build_kalshi_snapshot_rejects_naive_fetched_at() -> None:
    with pytest.raises(ValueError, match="fetched_at must include timezone information"):
        build_kalshi_market_snapshot(_sample_kalshi_response(), fetched_at=datetime(2026, 5, 20, 12, 0))


def test_fetch_markets_url_contract_and_user_agent(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return _FakeResponse('{"markets": []}')

    monkeypatch.setattr("venues.kalshi.urlopen", fake_urlopen)

    result = KalshiReadOnlyClient(base_url="https://example.test/trade-api/v2", timeout_seconds=4.0).fetch_markets(limit=7)

    assert result == {"markets": []}
    assert captured["url"].endswith("/markets?status=open&limit=7")
    assert captured["method"] == "GET"
    assert captured["user_agent"] == "relative-value-scanner/0.1 read-only"
    assert captured["timeout"] == 4.0


def test_fetch_markets_targeted_query_params(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return _FakeResponse('{"markets": []}')

    monkeypatch.setattr("venues.kalshi.urlopen", fake_urlopen)

    result = KalshiReadOnlyClient(base_url="https://example.test/trade-api/v2").fetch_markets(
        limit=7,
        series_ticker="KXNBA",
        event_ticker="KXNBA-26MAY20",
        cursor="cursor-1",
    )

    query = parse_qs(urlparse(captured["url"]).query)
    assert result == {"markets": []}
    assert query["status"] == ["open"]
    assert query["limit"] == ["7"]
    assert query["series_ticker"] == ["KXNBA"]
    assert query["event_ticker"] == ["KXNBA-26MAY20"]
    assert query["cursor"] == ["cursor-1"]


def test_fetch_markets_can_follow_response_cursor(monkeypatch) -> None:
    urls = []
    responses = [
        _FakeResponse('{"markets": [{"ticker": "first", "status": "open"}], "next_cursor": "cursor-2"}'),
        _FakeResponse('{"markets": [{"ticker": "second", "status": "open"}], "cursor": ""}'),
    ]

    def fake_urlopen(request, timeout):
        urls.append(request.full_url)
        return responses.pop(0)

    monkeypatch.setattr("venues.kalshi.urlopen", fake_urlopen)

    raw_response = KalshiReadOnlyClient(base_url="https://example.test/trade-api/v2").fetch_markets(
        limit=1,
        series_ticker="KXNBA",
        max_pages=2,
    )
    snapshot = build_kalshi_market_snapshot(
        raw_response,
        fetched_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert len(urls) == 2
    assert "cursor" not in parse_qs(urlparse(urls[0]).query)
    assert parse_qs(urlparse(urls[1]).query)["cursor"] == ["cursor-2"]
    assert raw_response["page_count"] == 2
    assert [market["ticker"] for market in raw_response["markets"]] == ["first", "second"]
    assert snapshot["schema_version"] == 1


def test_fetch_markets_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="limit must be positive"):
        KalshiReadOnlyClient().fetch_markets(limit=0)


def test_fetch_markets_rejects_non_positive_max_pages() -> None:
    with pytest.raises(ValueError, match="max_pages must be positive"):
        KalshiReadOnlyClient().fetch_markets(limit=1, max_pages=0)


def test_fetch_kalshi_cli_uses_client_without_network(monkeypatch, tmp_path: Path, capsys) -> None:
    output = tmp_path / "kalshi_markets_snapshot.json"

    class FakeClient:
        def __init__(self, timeout_seconds: float) -> None:
            assert timeout_seconds == 3.0

        def fetch_market_snapshot(
            self,
            limit: int,
            filter_options: KalshiMarketFilterOptions,
            *,
            series_ticker: str | None,
            event_ticker: str | None,
            cursor: str | None,
            max_pages: int,
        ) -> dict:
            assert limit == 2
            assert filter_options.include_closed is True
            assert series_ticker == "KXNBA"
            assert event_ticker == "KXNBA-26MAY20"
            assert cursor == "cursor-1"
            assert max_pages == 3
            return build_kalshi_market_snapshot(
                _sample_kalshi_response(),
                fetched_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
                filter_options=filter_options,
            )

    monkeypatch.setattr(scan, "KalshiReadOnlyClient", FakeClient)

    result = scan.main(
        [
            "fetch-kalshi",
            "--limit",
            "2",
            "--timeout-seconds",
            "3",
            "--include-closed",
            "--series-ticker",
            "KXNBA",
            "--event-ticker",
            "KXNBA-26MAY20",
            "--cursor",
            "cursor-1",
            "--max-pages",
            "3",
            "--output",
            str(output),
        ]
    )

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert "source" in payload
    assert "captured_at" in payload
    assert "market_count" in payload
    assert "normalized_markets" in payload
    assert payload["normalized_count"] == 1
    output_text = capsys.readouterr().out
    assert "kalshi_fetch_status=OK markets=1 normalized=1" in output_text
    assert "(skip counters can overlap)" in output_text


def test_fetch_kalshi_cli_fails_gracefully(monkeypatch, tmp_path: Path, capsys) -> None:
    class FailingClient:
        def __init__(self, timeout_seconds: float) -> None:
            pass

        def fetch_market_snapshot(
            self,
            limit: int,
            filter_options: KalshiMarketFilterOptions,
            **kwargs,
        ) -> dict:
            raise RuntimeError("public endpoint unavailable")

    monkeypatch.setattr(scan, "KalshiReadOnlyClient", FailingClient)

    result = scan.main(["fetch-kalshi", "--output", str(tmp_path / "snapshot.json")])

    assert result == 1
    assert "kalshi_fetch_status=FAILED message=public endpoint unavailable" in capsys.readouterr().out
