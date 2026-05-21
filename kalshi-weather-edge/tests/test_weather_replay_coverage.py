from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from config import settings
from data.storage import Storage
from research.weather_replay_coverage import WeatherReplayCoverageConfig, WeatherReplayCoverageReporter


def _storage(tmp_path) -> Storage:
    return Storage(replace(settings, database_url=f"sqlite:///{tmp_path / 'test.db'}"))


def _parsed(storage: Storage, ticker: str) -> None:
    storage.insert_json(
        "parsed_contracts",
        {
            "market_ticker": ticker,
            "event_ticker": "E",
            "city": "Austin",
            "variable_type": "high_temp",
            "contract_type": "range_bucket",
            "parse_confidence": 0.95,
        },
        market_ticker=ticker,
        event_ticker="E",
        parse_confidence=0.95,
    )
    storage.save_market({"ticker": ticker, "event_ticker": "E", "status": "active", "close_time": "2026-05-20T23:00:00Z"})


def _book(storage: Storage, ticker: str, ts: datetime) -> None:
    storage.upsert_live_orderbook_snapshot(
        {
            "market_ticker": ticker,
            "ts": ts,
            "yes_best_bid": 40,
            "yes_best_ask": 45,
            "no_best_bid": 55,
            "no_best_ask": 60,
            "spread_cents": 5,
            "mid_cents": 42.5,
            "source": "test",
        }
    )


def test_weather_replay_coverage_reports_overlap_and_suggested_command(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    _parsed(storage, "KXHIGHAUS-26MAY20-B89.5")
    _parsed(storage, "KXHIGHNY-26MAY20-B75.5")
    _book(storage, "KXHIGHAUS-26MAY20-B89.5", datetime(2026, 5, 20, 18, tzinfo=timezone.utc))
    _book(storage, "KXNONWEATHER", datetime(2026, 5, 20, 18, tzinfo=timezone.utc))

    result = WeatherReplayCoverageReporter(storage=storage, today_fn=lambda: date(2026, 5, 20)).build(
        WeatherReplayCoverageConfig(last_days=1),
        persist_exports=False,
    )

    assert result.summary["status"] == "WEATHER_REPLAY_COVERAGE_OK"
    assert result.days[0]["recorded_orderbook_tickers"] == 2
    assert result.days[0]["overlap_tickers"] == 1
    assert result.summary["latest_known_overlap_ts"].startswith("2026-05-20")
    assert "KXHIGHAUS-26MAY20-B89.5" in result.summary["suggested_replay_command"]
    assert "build-recorded-replay" in result.summary["suggested_replay_command"]


def test_weather_replay_coverage_zero_recent_overlap_uses_latest_known_overlap(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    _parsed(storage, "KXHIGHAUS-26MAY16-B89.5")
    _parsed(storage, "KXHIGHNY-26MAY20-B75.5")
    _book(storage, "KXHIGHAUS-26MAY16-B89.5", datetime(2026, 5, 16, 22, 14, tzinfo=timezone.utc))
    _book(storage, "KXNONWEATHER", datetime(2026, 5, 20, 18, tzinfo=timezone.utc))

    result = WeatherReplayCoverageReporter(storage=storage, today_fn=lambda: date(2026, 5, 20)).build(
        WeatherReplayCoverageConfig(last_days=1),
        persist_exports=False,
    )

    assert result.summary["status"] == "WEATHER_REPLAY_COVERAGE_ZERO_RECENT_OVERLAP"
    assert result.days[0]["overlap_tickers"] == 0
    assert result.summary["latest_known_overlap_ts"].startswith("2026-05-16")
    assert "--start 2026-05-16 --end 2026-05-16" in result.summary["suggested_replay_command"]
    assert "KXHIGHAUS-26MAY16-B89.5" in result.summary["suggested_replay_command"]


def test_weather_replay_coverage_latest_overlap_chooses_latest_timestamp(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    _parsed(storage, "KX1")
    _parsed(storage, "KX2")
    _book(storage, "KX1", datetime(2026, 5, 15, 12, tzinfo=timezone.utc))
    _book(storage, "KX2", datetime(2026, 5, 16, 22, 14, tzinfo=timezone.utc))
    _book(storage, "KX2", datetime(2026, 5, 16, 22, 15, tzinfo=timezone.utc))

    result = WeatherReplayCoverageReporter(storage=storage, today_fn=lambda: date(2026, 5, 20)).build(
        WeatherReplayCoverageConfig(last_days=7),
        persist_exports=False,
    )

    assert result.summary["latest_known_overlap_ts"].startswith("2026-05-16 22:15")
