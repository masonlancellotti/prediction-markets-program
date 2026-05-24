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


def _label(storage: Storage, ticker: str, confidence: float = 0.9) -> None:
    storage.upsert_settlement_label(
        {
            "market_ticker": ticker,
            "event_ticker": "E",
            "city": "Austin",
            "station_code": "KAUS",
            "local_date": "2026-05-20",
            "variable_type": "high_temp",
            "contract_type": "range_bucket",
            "settlement_value": 90.0,
            "yes_result": 1,
            "source": "test",
            "primary_source_type": "test",
            "confidence": confidence,
            "warnings": "",
            "raw_json": "{}",
            "settlement_version": "test",
        }
    )


def test_weather_replay_coverage_reports_overlap_and_suggested_command(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    _parsed(storage, "KXHIGHAUS-26MAY20-B89.5")
    _parsed(storage, "KXHIGHNY-26MAY20-B75.5")
    _book(storage, "KXHIGHAUS-26MAY20-B89.5", datetime(2026, 5, 20, 18, tzinfo=timezone.utc))
    _book(storage, "KXNONWEATHER", datetime(2026, 5, 20, 18, tzinfo=timezone.utc))
    _label(storage, "KXHIGHAUS-26MAY20-B89.5", confidence=0.9)

    result = WeatherReplayCoverageReporter(storage=storage, today_fn=lambda: date(2026, 5, 20)).build(
        WeatherReplayCoverageConfig(last_days=1),
        persist_exports=False,
    )

    assert result.summary["status"] == "WEATHER_REPLAY_COVERAGE_OK"
    assert result.days[0]["recorded_orderbook_tickers"] == 2
    assert result.days[0]["overlap_tickers"] == 1
    assert result.days[0]["settlement_label_tickers"] == 1
    assert result.days[0]["high_confidence_settlement_label_tickers"] == 1
    assert result.days[0]["missing_settlement_label_tickers"] == 0
    assert result.summary["latest_known_overlap_ts"].startswith("2026-05-20")
    assert "KXHIGHAUS-26MAY20-B89.5" in result.summary["suggested_replay_command"]
    assert "build-recorded-replay" in result.summary["suggested_replay_command"]
    assert result.summary["latest_overlap_day"] == "2026-05-20"
    assert result.summary["latest_overlap_day_overlap_tickers"] == 1
    assert result.summary["latest_overlap_day_high_confidence_settlement_label_tickers"] == 1
    assert result.summary["latest_overlap_day_missing_settlement_label_tickers"] == 0
    assert result.top_overlapping_tickers[0]["settlement_confidence"] == 0.9


def test_weather_replay_coverage_fresh_unlabeled_overlap_gets_stale_labels_status(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    _parsed(storage, "KXHIGHAUS-26MAY19-B89.5")
    _parsed(storage, "KXHIGHNY-26MAY20-B75.5")
    _book(storage, "KXHIGHAUS-26MAY19-B89.5", datetime(2026, 5, 19, 18, tzinfo=timezone.utc))
    _book(storage, "KXHIGHNY-26MAY20-B75.5", datetime(2026, 5, 20, 18, tzinfo=timezone.utc))
    _label(storage, "KXHIGHAUS-26MAY19-B89.5", confidence=0.9)

    result = WeatherReplayCoverageReporter(storage=storage, today_fn=lambda: date(2026, 5, 20)).build(
        WeatherReplayCoverageConfig(last_days=2),
        persist_exports=False,
    )

    assert result.summary["status"] == "WEATHER_REPLAY_COVERAGE_OK_STALE_LABELS_ONLY"
    assert result.summary["latest_overlap_day"] == "2026-05-20"
    assert result.summary["latest_overlap_day_overlap_tickers"] == 1
    assert result.summary["latest_overlap_day_high_confidence_settlement_label_tickers"] == 0
    assert result.summary["latest_overlap_day_missing_settlement_label_tickers"] == 1
    assert "--start 2026-05-19 --end 2026-05-19" in result.summary["suggested_replay_command"]
    assert "KXHIGHAUS-26MAY19-B89.5" in result.summary["suggested_replay_command"]


def test_weather_replay_coverage_overlap_but_labels_missing_has_specific_status(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    _parsed(storage, "KXHIGHNY-26MAY20-B75.5")
    _book(storage, "KXHIGHNY-26MAY20-B75.5", datetime(2026, 5, 20, 18, tzinfo=timezone.utc))

    result = WeatherReplayCoverageReporter(storage=storage, today_fn=lambda: date(2026, 5, 20)).build(
        WeatherReplayCoverageConfig(last_days=1),
        persist_exports=False,
    )

    assert result.summary["status"] == "WEATHER_REPLAY_COVERAGE_TICKERS_OK_LABELS_MISSING"
    assert result.days[0]["overlap_tickers"] == 1
    assert result.days[0]["settlement_label_tickers"] == 0
    assert result.days[0]["high_confidence_settlement_label_tickers"] == 0
    assert result.days[0]["missing_settlement_label_tickers"] == 1
    assert result.summary["suggested_replay_command"] is None
    assert "No replay-ready ticker found" in result.summary["suggested_replay_reason"]


def test_weather_replay_coverage_low_confidence_label_behaves_like_missing_label(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    _parsed(storage, "KXHIGHNY-26MAY20-B75.5")
    _book(storage, "KXHIGHNY-26MAY20-B75.5", datetime(2026, 5, 20, 18, tzinfo=timezone.utc))
    _label(storage, "KXHIGHNY-26MAY20-B75.5", confidence=0.5)

    result = WeatherReplayCoverageReporter(storage=storage, today_fn=lambda: date(2026, 5, 20)).build(
        WeatherReplayCoverageConfig(last_days=1, min_settlement_confidence=0.85),
        persist_exports=False,
    )

    assert result.summary["status"] == "WEATHER_REPLAY_COVERAGE_TICKERS_OK_LABELS_MISSING"
    assert result.days[0]["settlement_label_tickers"] == 1
    assert result.days[0]["high_confidence_settlement_label_tickers"] == 0
    assert result.days[0]["missing_settlement_label_tickers"] == 1
    assert result.summary["latest_overlap_day_missing_settlement_label_tickers"] == 1
    assert result.summary["suggested_replay_command"] is None


def test_weather_replay_coverage_zero_recent_overlap_uses_latest_known_overlap(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    _parsed(storage, "KXHIGHAUS-26MAY16-B89.5")
    _parsed(storage, "KXHIGHNY-26MAY20-B75.5")
    _book(storage, "KXHIGHAUS-26MAY16-B89.5", datetime(2026, 5, 16, 22, 14, tzinfo=timezone.utc))
    _book(storage, "KXNONWEATHER", datetime(2026, 5, 20, 18, tzinfo=timezone.utc))
    _label(storage, "KXHIGHAUS-26MAY16-B89.5", confidence=0.9)

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
    _label(storage, "KX2", confidence=0.9)

    result = WeatherReplayCoverageReporter(storage=storage, today_fn=lambda: date(2026, 5, 20)).build(
        WeatherReplayCoverageConfig(last_days=7),
        persist_exports=False,
    )

    assert result.summary["latest_known_overlap_ts"].startswith("2026-05-16 22:15")
