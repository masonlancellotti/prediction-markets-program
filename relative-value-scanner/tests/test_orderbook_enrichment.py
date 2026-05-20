import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

import pytest
import scan
from relative_value.orderbook_enrichment import enrich_orderbook_snapshot
from venues.orderbooks import (
    KalshiOrderbookClient,
    PolymarketOrderbookClient,
    parse_kalshi_orderbook_metrics,
    parse_polymarket_orderbook_metrics,
)


NOW = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)


def _kalshi_snapshot() -> dict:
    return {
        "schema_version": 1,
        "source": "kalshi_markets",
        "captured_at": "2026-05-20T11:30:00+00:00",
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [
            {
                "venue": "kalshi",
                "market_id": "KXTEST-YES",
                "ticker": "KXTEST-YES",
                "question": "Will the test happen?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "active": True,
                "closed": False,
            }
        ],
    }


def _polymarket_snapshot(include_token_ids: bool = True) -> dict:
    raw = {
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.43", "0.57"]',
    }
    if include_token_ids:
        raw["clobTokenIds"] = '["yes-token", "no-token"]'
    return {
        "schema_version": 1,
        "source": "polymarket_gamma",
        "captured_at": "2026-05-20T11:30:00+00:00",
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [
            {
                "venue": "polymarket",
                "market_id": "poly-test",
                "question": "Will the test happen?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "active": True,
                "closed": False,
                "raw": raw,
            }
        ],
    }


class FakeKalshiOrderbookClient:
    def endpoint_for(self, ticker: str) -> str:
        return f"https://example.test/markets/{ticker}/orderbook"

    def fetch_orderbook(self, ticker: str) -> dict:
        assert ticker == "KXTEST-YES"
        return {
            "orderbook": {
                "yes": [[0.42, 10], [0.40, 20]],
                "no": [[0.56, 7], [0.53, 5]],
            }
        }


class FakePolymarketOrderbookClient:
    def endpoint_for(self, token_id: str) -> str:
        return f"https://example.test/book?token_id={token_id}"

    def fetch_orderbook(self, token_id: str) -> dict:
        assert token_id == "yes-token"
        return {
            "bids": [{"price": "0.42", "size": "10"}, {"price": "0.40", "size": "20"}],
            "asks": [{"price": "0.44", "size": "7"}, {"price": "0.47", "size": "5"}],
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


def test_parse_kalshi_orderbook_binary_yes_space_depth() -> None:
    metrics = parse_kalshi_orderbook_metrics(
        {
            "orderbook": {
                "yes": [[0.42, 10], [0.40, 20]],
                "no": [[0.56, 7], [0.53, 5]],
            }
        },
        captured_at=NOW,
        source_endpoint="https://example.test/kalshi",
    )

    assert metrics["best_bid"] == 0.42
    assert metrics["best_ask"] == 0.44
    assert metrics["spread"] == 0.02
    assert metrics["depth_at_best_bid"] == 10
    assert metrics["depth_at_best_ask"] == 7
    assert metrics["depth_within_3c"] == {"bid": 30, "ask": 12, "total": 42}
    assert metrics["enrichment_status"] == "enriched"


def test_parse_polymarket_orderbook_depth() -> None:
    metrics = parse_polymarket_orderbook_metrics(
        {
            "bids": [{"price": "0.42", "size": "10"}, {"price": "0.40", "size": "20"}],
            "asks": [{"price": "0.44", "size": "7"}, {"price": "0.47", "size": "5"}],
        },
        captured_at=NOW,
        source_endpoint="https://example.test/poly",
    )

    assert metrics["best_bid"] == 0.42
    assert metrics["best_ask"] == 0.44
    assert metrics["depth_within_1c"] == {"bid": 10, "ask": 7, "total": 17}
    assert metrics["depth_within_5c"] == {"bid": 30, "ask": 12, "total": 42}


def test_kalshi_orderbook_enrichment_preserves_rows_and_adds_metrics() -> None:
    enriched = enrich_orderbook_snapshot(
        _kalshi_snapshot(),
        venue="kalshi",
        captured_at=NOW,
        kalshi_client=FakeKalshiOrderbookClient(),
        polymarket_client=FakePolymarketOrderbookClient(),
    )

    row = enriched["normalized_markets"][0]
    assert row["ticker"] == "KXTEST-YES"
    assert row["orderbook_enrichment"]["best_bid"] == 0.42
    assert row["orderbook_enrichment"]["enrichment_status"] == "enriched"
    assert enriched["orderbook_enrichment"]["enriched_count"] == 1


def test_polymarket_orderbook_enrichment_uses_yes_token_id() -> None:
    enriched = enrich_orderbook_snapshot(
        _polymarket_snapshot(),
        venue="polymarket",
        captured_at=NOW,
        kalshi_client=FakeKalshiOrderbookClient(),
        polymarket_client=FakePolymarketOrderbookClient(),
    )

    row = enriched["normalized_markets"][0]
    assert row["orderbook_enrichment"]["source_endpoint"].endswith("token_id=yes-token")
    assert row["orderbook_enrichment"]["depth_at_best_ask"] == 7
    assert enriched["orderbook_enrichment"]["enriched_count"] == 1


def test_polymarket_missing_token_id_is_unenriched_without_guessing() -> None:
    enriched = enrich_orderbook_snapshot(
        _polymarket_snapshot(include_token_ids=False),
        venue="polymarket",
        captured_at=NOW,
        kalshi_client=FakeKalshiOrderbookClient(),
        polymarket_client=FakePolymarketOrderbookClient(),
    )

    enrichment = enriched["normalized_markets"][0]["orderbook_enrichment"]
    assert enrichment["enrichment_status"] == "unenriched"
    assert enrichment["enrichment_warnings"] == ["missing_token_id"]


def test_stale_snapshot_marks_market_unenriched() -> None:
    snapshot = _kalshi_snapshot()
    snapshot["captured_at"] = "2026-05-18T11:30:00+00:00"

    enriched = enrich_orderbook_snapshot(
        snapshot,
        venue="kalshi",
        captured_at=NOW,
        max_snapshot_age_hours=24,
        kalshi_client=FakeKalshiOrderbookClient(),
        polymarket_client=FakePolymarketOrderbookClient(),
    )

    enrichment = enriched["normalized_markets"][0]["orderbook_enrichment"]
    assert enrichment["enrichment_status"] == "unenriched"
    assert enrichment["enrichment_warnings"] == ["stale_snapshot"]


def test_kalshi_orderbook_client_url_contract_and_user_agent(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return _FakeResponse('{"orderbook": {"yes": [], "no": []}}')

    monkeypatch.setattr("venues.orderbooks.urlopen", fake_urlopen)

    result = KalshiOrderbookClient(base_url="https://example.test/trade-api/v2", timeout_seconds=4).fetch_orderbook("KXTEST")

    assert result == {"orderbook": {"yes": [], "no": []}}
    assert captured["url"].endswith("/markets/KXTEST/orderbook")
    assert captured["method"] == "GET"
    assert captured["user_agent"] == "relative-value-scanner/0.1 read-only"
    assert captured["timeout"] == 4


def test_polymarket_orderbook_client_url_contract_and_user_agent(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return _FakeResponse('{"bids": [], "asks": []}')

    monkeypatch.setattr("venues.orderbooks.urlopen", fake_urlopen)

    result = PolymarketOrderbookClient(base_url="https://example.test", timeout_seconds=4).fetch_orderbook("token-1")

    assert result == {"bids": [], "asks": []}
    assert captured["url"].endswith("/book?token_id=token-1")
    assert captured["method"] == "GET"
    assert captured["user_agent"] == "relative-value-scanner/0.1 read-only"
    assert captured["timeout"] == 4


def test_orderbook_clients_surface_http_errors(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=None)

    monkeypatch.setattr("venues.orderbooks.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Kalshi orderbook API returned HTTP 404"):
        KalshiOrderbookClient().fetch_orderbook("KXTEST")
    with pytest.raises(RuntimeError, match="Polymarket CLOB orderbook API returned HTTP 404"):
        PolymarketOrderbookClient().fetch_orderbook("token")


def test_enrich_orderbooks_cli_uses_saved_json_without_network(monkeypatch, tmp_path: Path, capsys) -> None:
    snapshot_path = tmp_path / "kalshi_snapshot.json"
    output = tmp_path / "enriched.json"
    snapshot_path.write_text(json.dumps(_kalshi_snapshot()), encoding="utf-8")

    def fake_enrich_orderbook_snapshot_file(**kwargs):
        assert kwargs["snapshot_path"] == snapshot_path
        payload = _kalshi_snapshot()
        payload["orderbook_enrichment"] = {
            "market_count": 1,
            "enriched_count": 1,
            "unenriched_count": 0,
        }
        kwargs["output_path"].write_text(json.dumps(payload), encoding="utf-8")
        return payload

    monkeypatch.setattr(scan, "enrich_orderbook_snapshot_file", fake_enrich_orderbook_snapshot_file)

    result = scan.main(
        [
            "enrich-orderbooks",
            "--snapshot",
            str(snapshot_path),
            "--venue",
            "kalshi",
            "--output",
            str(output),
        ]
    )

    assert result == 0
    assert output.exists()
    assert "orderbook_enrichment_status=OK venue=kalshi markets=1 enriched=1 unenriched=0" in capsys.readouterr().out
