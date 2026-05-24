from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from config import settings
from data.storage import Storage
from research.source_smoke import SourceSmokeReporter


def _storage(tmp_path) -> Storage:
    return Storage(replace(settings, database_url=f"sqlite:///{tmp_path / 'test.db'}"))


class _FakeKalshi:
    def get_markets(self, **kwargs):
        return {"markets": [{"ticker": "KXTEST-26MAY22-T50"}]}

    def get_orderbook(self, ticker, depth=1):
        return {"orderbook": {"ticker": ticker, "yes": []}}


class _FailingKalshi:
    def get_markets(self, **kwargs):
        raise RuntimeError("network failure with PRIVATEVALUE")

    def get_orderbook(self, ticker, depth=1):
        raise RuntimeError("orderbook failure with PRIVATEVALUE")


class _FakeWeather:
    def latest_observation_payload(self, station_code):
        return (object(), {"properties": {}}, "https://example.invalid")

    def hourly_forecast_snapshot_rows(self, mapping):
        return [{"station_code": mapping.station_code}]


class _FakeClimateResult:
    report_url = "https://example.invalid/cli"
    parsed = object()


class _FakeClimate:
    def fetch_report(self, station_code, local_date, persist=True):
        return _FakeClimateResult()


class _FakeReadinessResult:
    status = "NOT_READY_NO_EDGE"


class _FakeReadiness:
    def evaluate(self, last_days=7):
        return _FakeReadinessResult()


def test_source_smoke_reports_missing_env_safely_without_values(tmp_path):
    result = SourceSmokeReporter(
        storage=_storage(tmp_path),
        env={},
        kalshi_client=_FakeKalshi(),
        weather_client=_FakeWeather(),
        climate_client=_FakeClimate(),
        trading_readiness=_FakeReadiness(),
    ).build(persist_exports=False, attempt_live_fetches=False)

    kalshi = next(row for row in result.components if row["component"].startswith("Kalshi API credentials"))
    assert kalshi["env_configured"] is False
    assert kalshi["details"]["kalshi_api_key_id_configured"] is False
    assert "KALSHI_API_KEY_ID" in kalshi["required_env_vars"]
    assert "PRIVATEVALUE" not in result.to_text()
    assert result.summary["secrets_printed"] is False
    assert result.summary["research_only"] is True
    assert result.summary["readiness_promotion"] == "none"


def test_source_smoke_private_key_path_check_is_boolean_only(tmp_path):
    key_path = tmp_path / "fake_private_key.pem"
    key_path.write_text("PRIVATEVALUE", encoding="utf-8")
    env = {
        "KALSHI_API_KEY_ID": "SECRET_KEY_ID",
        "KALSHI_API_PRIVATE_KEY_PATH": str(key_path),
        "NOAA_TOKEN": "SECRET_NOAA_TOKEN",
    }
    result = SourceSmokeReporter(
        storage=_storage(tmp_path),
        env=env,
        kalshi_client=_FakeKalshi(),
        weather_client=_FakeWeather(),
        climate_client=_FakeClimate(),
        trading_readiness=_FakeReadiness(),
    ).build(persist_exports=False, attempt_live_fetches=False)

    payload = str(result.to_text()) + str(result.summary) + str(result.components)
    kalshi = next(row for row in result.components if row["component"].startswith("Kalshi API credentials"))
    assert kalshi["env_configured"] is True
    assert kalshi["details"]["kalshi_private_key_path_exists"] is True
    assert "PRIVATEVALUE" not in payload
    assert "SECRET_KEY_ID" not in payload
    assert str(key_path) not in payload
    assert "SECRET_NOAA_TOKEN" not in payload


def test_source_smoke_db_missing_table_cases_do_not_crash(tmp_path):
    result = SourceSmokeReporter(
        storage=_storage(tmp_path),
        env={},
        kalshi_client=_FakeKalshi(),
        weather_client=_FakeWeather(),
        climate_client=_FakeClimate(),
        trading_readiness=_FakeReadiness(),
    ).build(persist_exports=False, attempt_live_fetches=False)

    table_rows = [row for row in result.components if row["component"] in {"settlement_labels", "orderbook_snapshots_live table"}]
    assert table_rows
    assert all(row["db_table_available"] is False for row in table_rows)


def test_source_smoke_component_failure_does_not_abort_report(tmp_path):
    result = SourceSmokeReporter(
        storage=_storage(tmp_path),
        env={},
        kalshi_client=_FailingKalshi(),
        weather_client=_FakeWeather(),
        climate_client=_FakeClimate(),
        trading_readiness=_FakeReadiness(),
    ).build(persist_exports=False, attempt_live_fetches=True)

    assert result.summary["status"] == "SOURCE_SMOKE_ATTENTION"
    assert result.summary["components_failed"] >= 1
    assert len(result.components) >= 10
    assert any(row["last_error_category"] == "RuntimeError" for row in result.components)
    assert "PRIVATEVALUE" not in result.to_text()


def test_source_smoke_exports_research_only_reports(tmp_path, monkeypatch):
    import research.source_smoke as smoke_mod

    monkeypatch.setattr(smoke_mod, "PROJECT_ROOT", tmp_path)
    storage = _storage(tmp_path)
    storage.init_db()
    result = SourceSmokeReporter(
        storage=storage,
        env={},
        kalshi_client=_FakeKalshi(),
        weather_client=_FakeWeather(),
        climate_client=_FakeClimate(),
        trading_readiness=_FakeReadiness(),
    ).build(persist_exports=True, attempt_live_fetches=False)

    assert result.exports is not None
    json_path = Path(result.exports["json"])
    md_path = Path(result.exports["markdown"])
    assert json_path.exists()
    assert md_path.exists()
    content = json_path.read_text(encoding="utf-8") + md_path.read_text(encoding="utf-8")
    assert '"research_only": true' in content
    assert '"order_placement_enabled": false' in content
    assert "PRIVATEVALUE" not in content

