import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest
import scan
from venues.polymarket import (
    PolymarketGammaClient,
    PolymarketMarketFilterOptions,
    build_polymarket_market_snapshot,
    extract_markets_from_events,
    parse_gamma_events_response,
)


def _sample_gamma_response() -> dict:
    return {
        "events": [
            {
                "id": "event-1",
                "title": "Knicks vs Cavaliers",
                "slug": "knicks-cavaliers",
                "markets": [
                    {
                        "id": "market-1",
                        "conditionId": "condition-1",
                        "question": "Will the Knicks win?",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.43", "0.57"]',
                        "enableOrderBook": True,
                        "acceptingOrders": True,
                        "bestBid": "0.42",
                        "bestAsk": "0.44",
                        "active": True,
                        "closed": False,
                        "archived": False,
                        "endDate": "2026-05-20T20:00:00Z",
                        "liquidity": "123.45",
                        "volume": "456.78",
                    }
                ],
            }
        ]
    }


def test_parse_gamma_events_response_from_events_wrapper() -> None:
    events = parse_gamma_events_response(_sample_gamma_response())

    assert len(events) == 1
    assert events[0]["id"] == "event-1"


def test_parse_gamma_events_response_accepts_common_wrappers() -> None:
    bare = [{"id": "event-bare", "markets": []}]
    data = {"data": [{"id": "event-data", "markets": []}]}
    results = {"results": [{"id": "event-results", "markets": []}]}

    assert parse_gamma_events_response(bare)[0]["id"] == "event-bare"
    assert parse_gamma_events_response(data)[0]["id"] == "event-data"
    assert parse_gamma_events_response(results)[0]["id"] == "event-results"


def test_parse_gamma_events_response_rejects_invalid_input() -> None:
    with pytest.raises(ValueError, match="response must be a list of events"):
        parse_gamma_events_response({"unexpected": []})


def test_extract_markets_maps_outcomes_and_prices() -> None:
    events = parse_gamma_events_response(_sample_gamma_response())
    markets, skip_counts = extract_markets_from_events(
        events,
        captured_at=datetime(2026, 5, 19, 20, 0, tzinfo=timezone.utc),
    )

    assert len(markets) == 1
    assert skip_counts["skipped_closed_count"] == 0
    market = markets[0]
    assert market["event_id"] == "event-1"
    assert market["market_id"] == "market-1"
    assert market["condition_id"] == "condition-1"
    assert market["enable_order_book"] is True
    assert market["accepting_orders"] is True
    assert market["best_bid"] == 0.42
    assert market["best_ask"] == 0.44
    assert market["closed"] is False
    assert market["outcomes"] == [
        {"name": "Yes", "outcome_yes_token_price": 0.43},
        {"name": "No", "outcome_yes_token_price": 0.57},
    ]


def test_extract_markets_handles_missing_outcome_prices() -> None:
    response = _sample_gamma_response()
    response["events"][0]["markets"][0].pop("outcomePrices")

    markets, _skip_counts = extract_markets_from_events(
        parse_gamma_events_response(response),
        captured_at=datetime(2026, 5, 19, 20, 0, tzinfo=timezone.utc),
    )

    assert markets[0]["outcomes"] == [
        {"name": "Yes", "outcome_yes_token_price": None},
        {"name": "No", "outcome_yes_token_price": None},
    ]


def test_trailing_outcomes_without_prices_get_none() -> None:
    response = _sample_gamma_response()
    response["events"][0]["markets"][0]["outcomes"] = '["Yes", "No", "Maybe"]'
    response["events"][0]["markets"][0]["outcomePrices"] = '["0.43"]'

    markets, _skip_counts = extract_markets_from_events(
        parse_gamma_events_response(response),
        captured_at=datetime(2026, 5, 19, 20, 0, tzinfo=timezone.utc),
    )

    assert markets[0]["outcomes"] == [
        {"name": "Yes", "outcome_yes_token_price": 0.43},
        {"name": "No", "outcome_yes_token_price": None},
        {"name": "Maybe", "outcome_yes_token_price": None},
    ]


def test_closed_markets_are_skipped_by_default() -> None:
    response = _sample_gamma_response()
    response["events"][0]["markets"][0]["closed"] = True

    snapshot = build_polymarket_market_snapshot(
        response,
        fetched_at=datetime(2026, 5, 19, 20, 0, tzinfo=timezone.utc),
    )

    assert snapshot["market_count"] == 1
    assert snapshot["normalized_count"] == 0
    assert snapshot["skipped_closed_count"] == 1


def test_missing_accepting_orders_is_skipped_by_default() -> None:
    response = _sample_gamma_response()
    response["events"][0]["markets"][0].pop("acceptingOrders")

    snapshot = build_polymarket_market_snapshot(
        response,
        fetched_at=datetime(2026, 5, 19, 20, 0, tzinfo=timezone.utc),
    )

    assert snapshot["normalized_count"] == 0
    assert snapshot["skipped_not_accepting_orders_count"] == 1


def test_accepting_orders_false_markets_are_skipped_by_default() -> None:
    response = _sample_gamma_response()
    response["events"][0]["markets"][0]["acceptingOrders"] = False

    snapshot = build_polymarket_market_snapshot(
        response,
        fetched_at=datetime(2026, 5, 19, 20, 0, tzinfo=timezone.utc),
    )

    assert snapshot["normalized_count"] == 0
    assert snapshot["skipped_not_accepting_orders_count"] == 1


def test_accepting_orders_true_markets_are_kept() -> None:
    snapshot = build_polymarket_market_snapshot(
        _sample_gamma_response(),
        fetched_at=datetime(2026, 5, 19, 20, 0, tzinfo=timezone.utc),
    )

    assert snapshot["normalized_count"] == 1
    assert snapshot["skipped_not_accepting_orders_count"] == 0


def test_include_not_accepting_orders_keeps_true_false_and_missing() -> None:
    response = _sample_gamma_response()
    true_market = response["events"][0]["markets"][0]
    false_market = dict(true_market, id="market-false", acceptingOrders=False)
    missing_market = dict(true_market, id="market-missing")
    missing_market.pop("acceptingOrders")
    response["events"][0]["markets"] = [true_market, false_market, missing_market]

    snapshot = build_polymarket_market_snapshot(
        response,
        fetched_at=datetime(2026, 5, 19, 20, 0, tzinfo=timezone.utc),
        filter_options=PolymarketMarketFilterOptions(include_not_accepting_orders=True),
    )

    assert snapshot["normalized_count"] == 3
    assert snapshot["skipped_not_accepting_orders_count"] == 0


def test_include_flags_allow_closed_and_not_accepting_orders_markets() -> None:
    response = _sample_gamma_response()
    response["events"][0]["markets"][0]["closed"] = True
    response["events"][0]["markets"][0]["acceptingOrders"] = False

    snapshot = build_polymarket_market_snapshot(
        response,
        fetched_at=datetime(2026, 5, 19, 20, 0, tzinfo=timezone.utc),
        filter_options=PolymarketMarketFilterOptions(
            include_closed=True,
            include_not_accepting_orders=True,
        ),
    )

    assert snapshot["normalized_count"] == 1
    assert snapshot["skipped_closed_count"] == 0
    assert snapshot["skipped_not_accepting_orders_count"] == 0
    assert snapshot["normalized_markets"][0]["closed"] is True
    assert snapshot["normalized_markets"][0]["accepting_orders"] is False


def test_past_end_date_markets_are_skipped_by_default() -> None:
    response = _sample_gamma_response()
    response["events"][0]["markets"][0]["endDate"] = "2026-05-18T20:00:00Z"

    snapshot = build_polymarket_market_snapshot(
        response,
        fetched_at=datetime(2026, 5, 19, 20, 0, tzinfo=timezone.utc),
    )

    assert snapshot["normalized_count"] == 0
    assert snapshot["skipped_past_end_date_count"] == 1


def test_inactive_market_is_skipped() -> None:
    response = _sample_gamma_response()
    response["events"][0]["markets"][0]["active"] = False

    snapshot = build_polymarket_market_snapshot(
        response,
        fetched_at=datetime(2026, 5, 19, 20, 0, tzinfo=timezone.utc),
    )

    assert snapshot["normalized_count"] == 0
    assert snapshot["skipped_inactive_count"] == 1


def test_archived_market_is_skipped_without_override() -> None:
    response = _sample_gamma_response()
    response["events"][0]["markets"][0]["archived"] = True

    snapshot = build_polymarket_market_snapshot(
        response,
        fetched_at=datetime(2026, 5, 19, 20, 0, tzinfo=timezone.utc),
        filter_options=PolymarketMarketFilterOptions(
            include_closed=True,
            include_not_accepting_orders=True,
            include_past_end_date=True,
        ),
    )

    assert snapshot["normalized_count"] == 0
    assert snapshot["skipped_archived_count"] == 1


def test_build_polymarket_snapshot_rejects_naive_fetched_at() -> None:
    with pytest.raises(ValueError, match="fetched_at must include timezone information"):
        build_polymarket_market_snapshot(
            _sample_gamma_response(),
            fetched_at=datetime(2026, 5, 19, 20, 0),
        )


def test_build_polymarket_snapshot_counts_orderbook_markets() -> None:
    snapshot = build_polymarket_market_snapshot(
        _sample_gamma_response(),
        fetched_at=datetime(2026, 5, 19, 20, 0, tzinfo=timezone.utc),
    )

    assert snapshot["schema_version"] == 1
    assert snapshot["event_count"] == 1
    assert snapshot["market_count"] == 1
    assert snapshot["normalized_count"] == 1
    assert snapshot["orderbook_enabled_count"] == 1
    assert snapshot["captured_at"] == "2026-05-19T20:00:00+00:00"
    assert "raw_response" in snapshot
    assert "raw_market_count" not in snapshot


def test_fetch_events_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="limit must be positive"):
        PolymarketGammaClient().fetch_events(limit=0)


class _FakeResponse:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.payload.encode("utf-8")


def test_fetch_events_url_contract_and_user_agent(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return _FakeResponse("[]")

    monkeypatch.setattr("venues.polymarket.urlopen", fake_urlopen)

    result = PolymarketGammaClient(base_url="https://example.test", timeout_seconds=4.0).fetch_events(limit=7)

    assert result == []
    assert captured["url"].endswith("/events?active=true&closed=false&limit=7")
    assert captured["method"] == "GET"
    assert captured["user_agent"] == "relative-value-scanner/0.1 read-only"
    assert captured["timeout"] == 4.0


def test_fetch_events_targeted_tag_query_params(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return _FakeResponse("[]")

    monkeypatch.setattr("venues.polymarket.urlopen", fake_urlopen)

    result = PolymarketGammaClient(base_url="https://example.test").fetch_events(
        limit=7,
        tag_slug="nba",
        tag_id=100381,
    )

    query = parse_qs(urlparse(captured["url"]).query)
    assert result == []
    assert query["active"] == ["true"]
    assert query["closed"] == ["false"]
    assert query["limit"] == ["7"]
    assert query["tag_slug"] == ["nba"]
    assert query["tag_id"] == ["100381"]


def test_fetch_events_http_error_surfaces_runtime_error(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 429, "Too Many Requests", hdrs=None, fp=None)

    monkeypatch.setattr("venues.polymarket.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Polymarket Gamma API returned HTTP 429 for /events"):
        PolymarketGammaClient().fetch_events(limit=1)


def test_fetch_events_url_error_surfaces_runtime_error(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise URLError("network unavailable")

    monkeypatch.setattr("venues.polymarket.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Polymarket Gamma API request failed: network unavailable"):
        PolymarketGammaClient().fetch_events(limit=1)


def test_fetch_events_timeout_surfaces_runtime_error(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise TimeoutError()

    monkeypatch.setattr("venues.polymarket.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Polymarket Gamma API request timed out"):
        PolymarketGammaClient().fetch_events(limit=1)


def test_fetch_events_json_decode_error_surfaces_runtime_error(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        return _FakeResponse("{not json")

    monkeypatch.setattr("venues.polymarket.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Polymarket Gamma API returned invalid JSON"):
        PolymarketGammaClient().fetch_events(limit=1)


def test_fetch_polymarket_cli_uses_client_without_network(monkeypatch, tmp_path: Path, capsys) -> None:
    output = tmp_path / "polymarket_markets_snapshot.json"

    class FakeClient:
        def __init__(self, timeout_seconds: float) -> None:
            assert timeout_seconds == 3.0

        def fetch_market_snapshot(
            self,
            limit: int,
            filter_options: PolymarketMarketFilterOptions,
            *,
            tag_slug: str | None,
            tag_id: int | None,
        ) -> dict:
            assert limit == 2
            assert tag_slug == "nba"
            assert tag_id == 100381
            assert filter_options.include_closed is True
            assert filter_options.include_not_accepting_orders is True
            return build_polymarket_market_snapshot(
                _sample_gamma_response(),
                fetched_at=datetime(2026, 5, 19, 20, 0, tzinfo=timezone.utc),
                filter_options=filter_options,
            )

    monkeypatch.setattr(scan, "PolymarketGammaClient", FakeClient)

    result = scan.main(
        [
            "fetch-polymarket",
            "--limit",
            "2",
            "--timeout-seconds",
            "3",
            "--include-closed",
            "--include-not-accepting-orders",
            "--tag-slug",
            "nba",
            "--tag-id",
            "100381",
            "--output",
            str(output),
        ]
    )

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["normalized_count"] == 1
    output_text = capsys.readouterr().out
    assert "polymarket_fetch_status=OK events=1 markets=1 normalized=1" in output_text
    assert "(skip counters can overlap)" in output_text


def test_fetch_polymarket_cli_fails_gracefully(monkeypatch, tmp_path: Path, capsys) -> None:
    class FailingClient:
        def __init__(self, timeout_seconds: float) -> None:
            pass

        def fetch_market_snapshot(
            self,
            limit: int,
            filter_options: PolymarketMarketFilterOptions,
            **kwargs,
        ) -> dict:
            raise RuntimeError("network unavailable")

    monkeypatch.setattr(scan, "PolymarketGammaClient", FailingClient)

    result = scan.main(["fetch-polymarket", "--output", str(tmp_path / "snapshot.json")])

    assert result == 1
    assert "polymarket_fetch_status=FAILED message=network unavailable" in capsys.readouterr().out
