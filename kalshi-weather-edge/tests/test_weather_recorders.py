from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

import pandas as pd

from backtest.recorded_replay import recorded_weather_features_asof, weather_features_asof
from config import settings
from data.active_weather_station_resolver import ActiveWeatherStationResolver
from data.storage import Storage
from data.weather_client import WeatherClient
from data.weather_station_mapper import StationMapper
from live.weather_recorder import (
    WeatherObservationRecorder,
    _length_to_inches,
    _length_to_miles,
    _observation_row,
)
from parsing.weather_contract import WeatherContract


def test_precipitation_mm_is_converted_to_inches_not_treated_as_meters():
    """Regression test for the unit-conversion bug that produced ~31 inches of
    hourly rainfall when NWS actually reported 0.8 millimetres."""
    assert _length_to_inches({"value": 0.8, "unitCode": "wmoUnit:mm"}) == pytest_approx(0.0314961)
    assert _length_to_inches({"value": 0.0254, "unitCode": "wmoUnit:m"}) == pytest_approx(1.0)
    assert _length_to_inches({"value": None, "unitCode": "wmoUnit:mm"}) is None
    assert _length_to_inches(None) is None
    # Unitless huge magnitude should be assumed to be mm (heuristic fallback).
    assert _length_to_inches({"value": 12.7}) == pytest_approx(0.5, rel=1e-3)


def test_visibility_handles_metres_and_kilometres():
    assert _length_to_miles({"value": 12870, "unitCode": "wmoUnit:m"}) == pytest_approx(8.0, rel=1e-2)
    assert _length_to_miles({"value": 16, "unitCode": "wmoUnit:km"}) == pytest_approx(9.94, rel=1e-2)
    assert _length_to_miles({"value": None}) is None


def test_observation_row_records_realistic_precip_in_inches():
    from data.weather_client import WeatherObservation
    from data.weather_station_mapper import StationMapping

    mapping = StationMapping(
        city="Atlanta", station_code="KATL", timezone="America/New_York",
        latitude=33.6407, longitude=-84.4277, confidence=0.8,
        station_name="Atlanta Hartsfield-Jackson",
    )
    obs = WeatherObservation(
        station_code="KATL",
        observed_at=datetime(2026, 5, 1, 18, tzinfo=timezone.utc),
        temp_f=72.0, source="NWS",
    )
    payload = {
        "properties": {
            "precipitationLastHour": {"value": 0.8, "unitCode": "wmoUnit:mm"},
            "precipitationLast3Hours": {"value": 25.4, "unitCode": "wmoUnit:mm"},
            "visibility": {"value": 12870, "unitCode": "wmoUnit:m"},
            "textDescription": "Light rain",
        }
    }
    row = _observation_row(mapping, obs, payload, "https://example/test")
    assert row["precip_1h"] < 0.1, f"precip_1h should be ~0.03 inches, got {row['precip_1h']}"
    assert 0.95 <= row["precip_3h"] <= 1.05, f"precip_3h should be ~1.0 inches, got {row['precip_3h']}"
    assert 7.9 <= row["visibility_miles"] <= 8.1


def pytest_approx(value, rel: float = 1e-4):
    """Inline approx helper to avoid a pytest.approx import order issue."""
    from pytest import approx

    return approx(value, rel=rel)


def _temp_storage(tmp_path) -> Storage:
    return Storage(replace(settings, database_url=f"sqlite:///{tmp_path / 'test.db'}"))


def test_active_weather_market_station_mapping_prefers_explicit_station():
    contract = WeatherContract(
        event_ticker="E",
        market_ticker="M",
        city="Philadelphia",
        station_code="KPHL",
        local_date=date(2026, 4, 30),
        variable_type="high_temp",
        contract_type="range_bucket",
        range_low=66,
        range_high=67,
        settlement_source="NWS Daily Climate Report",
        station_confidence=0.95,
        parse_confidence=0.95,
    )
    row = ActiveWeatherStationResolver()._row_for_contract(contract)
    assert row["station_code"] == "KPHL"
    assert row["mapping_reason"] == "explicit_station_from_rules"
    assert row["mapping_confidence"] >= 0.9


def test_station_mapper_has_midway_forecast_metadata():
    mapping = StationMapper().resolve_station_code("KMDW")
    assert mapping.station_code == "KMDW"
    assert mapping.latitude is not None
    assert mapping.longitude is not None
    assert mapping.timezone == "America/Chicago"


def test_weather_snapshot_inserts(tmp_path):
    storage = _temp_storage(tmp_path)
    storage.insert_weather_observation_snapshot(
        {
            "station_code": "KPHL",
            "station_name": "Philadelphia International",
            "ts_observed": datetime(2026, 4, 30, 18, tzinfo=timezone.utc),
            "ts_recorded": datetime(2026, 4, 30, 18, 1, tzinfo=timezone.utc),
            "source": "test",
            "temp_f": 66,
            "quality_score": 0.9,
        }
    )
    storage.insert_weather_forecast_snapshots(
        [
            {
                "station_code": "KPHL",
                "ts_recorded": datetime(2026, 4, 30, 18, 1, tzinfo=timezone.utc),
                "forecast_valid_start": datetime(2026, 4, 30, 19, tzinfo=timezone.utc),
                "source": "test_forecast",
                "forecast_hour": 0,
                "temp_f": 67,
                "quality_score": 0.8,
            }
        ]
    )
    assert len(storage.fetch_table("weather_observation_snapshots_live")) == 1
    assert len(storage.fetch_table("weather_forecast_snapshots_live")) == 1


def test_recorded_weather_asof_blocks_future_forecast_leakage():
    contract = WeatherContract(
        event_ticker="E",
        market_ticker="M",
        city="Philadelphia",
        station_code="KPHL",
        local_date=date(2026, 4, 30),
        variable_type="high_temp",
        contract_type="threshold_above",
        threshold=70,
        comparator="gte",
    )
    observations = pd.DataFrame(
        [
            {
                "station_code": "KPHL",
                "ts_observed": datetime(2026, 4, 30, 18, tzinfo=timezone.utc),
                "ts_recorded": datetime(2026, 4, 30, 18, 1, tzinfo=timezone.utc),
                "temp_f": 65,
                "dewpoint_f": 55,
                "humidity": 70,
                "wind_speed_mph": 8,
                "wind_direction_degrees": 240,
                "wind_gust_mph": 14,
                "pressure_mb": 1012,
                "visibility_miles": 9,
                "precip_1h": 0.02,
                "precip_3h": 0.05,
                "quality_score": 0.9,
            }
        ]
    )
    forecasts = pd.DataFrame(
        [
            {
                "station_code": "KPHL",
                "ts_recorded": datetime(2026, 4, 30, 18, 5, tzinfo=timezone.utc),
                "forecast_valid_start": datetime(2026, 4, 30, 20, tzinfo=timezone.utc),
                "source": "future_forecast",
                "temp_f": 90,
                "dewpoint_f": 75,
                "humidity": 95,
                "wind_speed_mph": 30,
                "precip_probability": 90,
                "quantitative_precip": 1.5,
                "sky_cover": 100,
                "quality_score": 0.9,
            },
            {
                "station_code": "KPHL",
                "ts_recorded": datetime(2026, 4, 30, 17, 55, tzinfo=timezone.utc),
                "forecast_valid_start": datetime(2026, 4, 30, 20, tzinfo=timezone.utc),
                "source": "known_forecast",
                "temp_f": 68,
                "dewpoint_f": 58,
                "humidity": 60,
                "wind_speed_mph": 12,
                "precip_probability": 20,
                "quantitative_precip": 0.1,
                "sky_cover": 40,
                "quality_score": 0.8,
            },
        ]
    )
    features = recorded_weather_features_asof(contract, observations, forecasts, "America/New_York", datetime(2026, 4, 30, 18, 2, tzinfo=timezone.utc))
    assert features["weather_feature_source"] == "recorded_live_asof"
    assert features["forecast_high_remaining_f"] == 68
    assert features["forecast_source"] == "known_forecast"
    assert features["dewpoint_f_asof"] == 55
    assert features["humidity_asof"] == 70
    assert features["wind_speed_mph_asof"] == 8
    assert features["precip_1h_asof"] == pytest_approx(0.02)
    assert features["precip_accum_today_asof"] == pytest_approx(0.02)
    assert features["forecast_dewpoint_high_remaining_f"] == 58
    assert features["forecast_precip_probability_max_remaining"] == 20
    assert features["forecast_quantitative_precip_remaining"] == pytest_approx(0.1)
    assert features["forecast_sky_cover_avg_remaining"] == 40
    assert features["month"] == 4
    assert features["season"] == "spring"


def test_missing_weather_data_does_not_crash_feature_builder():
    contract = WeatherContract(event_ticker="E", market_ticker="M", threshold=70, comparator="gte")
    features = weather_features_asof(contract, [], "America/New_York", datetime(2026, 4, 30, 18, tzinfo=timezone.utc))
    assert features["current_temp_asof"] is None
    assert features["observations_count_so_far"] == 0


class FailingWeatherClient(WeatherClient):
    def latest_observation_payload(self, station_code: str):
        raise RuntimeError("weather source down")


def test_weather_recorder_failure_does_not_touch_orderbook_recorder(tmp_path):
    storage = _temp_storage(tmp_path)
    result = WeatherObservationRecorder(storage=storage, weather_client=FailingWeatherClient()).run(stations=["KPHL"], once=True)
    assert result.failures == 1
    assert len(storage.fetch_table("orderbook_snapshots_live")) == 0
