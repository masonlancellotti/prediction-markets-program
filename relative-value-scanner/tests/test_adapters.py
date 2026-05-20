import json

from venues.base import JsonExchangeFixtureAdapter


def test_is_executable_defaults_false_in_adapter(tmp_path) -> None:
    path = tmp_path / "markets.json"
    path.write_text(
        json.dumps(
            [
                {
                    "market_id": "m1",
                    "event_name": "Example event",
                    "outcome_name": "Example outcome",
                    "yes_bid": 0.2,
                    "yes_ask": 0.3,
                    "settlement_time": "2026-05-20T03:30:00Z",
                    "settlement_rule": "fixture rule",
                }
            ]
        ),
        encoding="utf-8",
    )
    adapter = JsonExchangeFixtureAdapter("fixture", path)
    market = adapter.load_markets()[0]
    assert market.is_executable is False
