from datetime import datetime, timezone

from backtest.execution import NormalizedOrderBook
from live.orderbook_recorder import LiveOrderbookRecorder, extract_multi_orderbooks, normalize_live_orderbook_snapshot


def test_live_orderbook_snapshot_math_and_depth():
    raw = {"orderbook_fp": {"yes_dollars": [["0.42", "10"], ["0.40", "5"]], "no_dollars": [["0.53", "7"], ["0.50", "3"]]}}
    book = NormalizedOrderBook.from_kalshi("T", raw)
    row = normalize_live_orderbook_snapshot("T", datetime(2026, 1, 1, tzinfo=timezone.utc), book, raw)
    assert row["yes_best_bid"] == 42
    assert row["yes_best_ask"] == 47
    assert row["no_best_bid"] == 53
    assert row["no_best_ask"] == 58
    assert row["spread_cents"] == 5
    assert row["mid_cents"] == 44.5
    assert row["depth_yes_bid_1"] == 10
    assert row["depth_yes_ask_1"] == 7
    assert row["total_yes_bid_depth"] == 15
    assert row["total_no_bid_depth"] == 10
    # Fields are present even when no market payload is provided.
    assert row["last_price_cents"] is None
    assert row["volume_24h"] is None
    assert row["open_interest"] is None


def test_live_orderbook_snapshot_enriches_with_market_payload():
    raw = {"orderbook_fp": {"yes_dollars": [["0.42", "10"]], "no_dollars": [["0.53", "7"]]}}
    book = NormalizedOrderBook.from_kalshi("T", raw)
    market_payload = {
        "ticker": "T",
        "status": "open",
        "last_price_dollars": "0.45",
        "previous_yes_bid_dollars": "0.41",
        "previous_yes_ask_dollars": "0.48",
        "volume_fp": 1234.5,
        "volume_24h_fp": 5678.0,
        "open_interest_fp": 42.0,
        "liquidity_dollars": "1234.56",
        "close_time": "2026-05-01T18:00:00Z",
    }
    row = normalize_live_orderbook_snapshot(
        "T", datetime(2026, 1, 1, tzinfo=timezone.utc), book, raw, market_payload=market_payload
    )
    assert row["last_price_cents"] == 45.0
    assert row["previous_yes_bid_cents"] == 41.0
    assert row["previous_yes_ask_cents"] == 48.0
    assert row["volume"] == 1234.5
    assert row["volume_24h"] == 5678.0
    assert row["open_interest"] == 42.0
    assert row["liquidity_cents"] is not None
    assert row["market_status"] == "open"
    assert row["market_close_time"] is not None


def test_extract_multi_orderbooks_accepts_list_payload():
    payload = {
        "orderbooks": [
            {"ticker": "T1", "orderbook": {"yes": [[42, 10]], "no": [[53, 7]]}},
            {"market_ticker": "T2", "orderbook_fp": {"yes_dollars": [["0.40", "3"]], "no_dollars": [["0.55", "4"]]}},
        ]
    }

    rows = extract_multi_orderbooks(payload, ["T1", "T2"])

    assert rows == [
        ("T1", {"yes": [[42, 10]], "no": [[53, 7]]}),
        ("T2", {"yes_dollars": [["0.40", "3"]], "no_dollars": [["0.55", "4"]]}),
    ]


def test_extract_multi_orderbooks_falls_back_to_requested_order():
    payload = {"orderbooks": [{"yes": [[42, 10]], "no": [[53, 7]]}]}

    rows = extract_multi_orderbooks(payload, ["REQUESTED"])

    assert rows == [("REQUESTED", {"yes": [[42, 10]], "no": [[53, 7]]})]


class FakeMarketLoader:
    def __init__(self):
        self.active_weather_calls = []
        self.active_market_calls = []

    def load_active_weather_markets(self, **kwargs):
        self.active_weather_calls.append(kwargs)
        return [{"ticker": "KXWEATHER-TEST"}]

    def load_active_markets(self, **kwargs):
        self.active_market_calls.append(kwargs)
        return [{"ticker": "KXALL-TEST"}]


def _recorder_with_fake_loader() -> tuple[LiveOrderbookRecorder, FakeMarketLoader]:
    recorder = LiveOrderbookRecorder()
    loader = FakeMarketLoader()
    recorder.loader = loader
    return recorder, loader


def test_weather_only_recorder_persists_weather_markets_when_enabled():
    recorder, loader = _recorder_with_fake_loader()

    tickers = recorder._active_tickers(
        weather_only=True,
        max_markets=5,
        persist_weather_markets=True,
    )

    assert tickers == ["KXWEATHER-TEST"]
    assert loader.active_weather_calls[0]["persist"] is True


def test_weather_only_recorder_default_does_not_persist_weather_markets():
    recorder, loader = _recorder_with_fake_loader()

    recorder._active_tickers(weather_only=True, max_markets=5)

    assert loader.active_weather_calls[0]["persist"] is False


def test_all_market_recorder_ignores_persist_weather_markets_flag():
    recorder, loader = _recorder_with_fake_loader()

    tickers = recorder._active_tickers(
        weather_only=False,
        max_markets=5,
        persist_weather_markets=True,
    )

    assert tickers == ["KXALL-TEST"]
    assert loader.active_weather_calls == []
    assert loader.active_market_calls[0]["persist"] is True
