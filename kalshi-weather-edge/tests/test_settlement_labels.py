from datetime import date, datetime, timezone

from dataclasses import replace
from sqlalchemy import text

from config import settings
from data.nws_climate_report_client import ClimateReportResult
from data.storage import Storage
from data.weather_client import WeatherClient
from data.weather_settlement_loader import WeatherSettlementLoader, evaluate_condition, evaluate_contract_result
from maintenance import ProjectMaintenance
from parsing.market_parser import WeatherMarketParser
from parsing.weather_contract import WeatherContract


def _storage(tmp_path) -> Storage:
    return Storage(replace(settings, database_url=f"sqlite:///{tmp_path / 'test.db'}"))


def _contract(ticker: str, day: date = date(2026, 5, 20)) -> WeatherContract:
    return WeatherContract(
        event_ticker="E1",
        market_ticker=ticker,
        city="New York",
        station_code="KNYC",
        local_date=day,
        variable_type="high_temp",
        contract_type="threshold_above",
        threshold=70,
        comparator="gt",
    )


def _save_label(storage: Storage, contract: WeatherContract, confidence: float = 0.95, yes_result: int | None = 1) -> None:
    storage.upsert_settlement_label({
        "market_ticker": contract.market_ticker,
        "event_ticker": contract.event_ticker,
        "city": contract.city,
        "station_code": contract.station_code,
        "local_date": contract.local_date.isoformat() if contract.local_date else None,
        "variable_type": contract.variable_type,
        "contract_type": contract.contract_type,
        "threshold": contract.threshold,
        "comparator": contract.comparator,
        "range_low": None,
        "range_high": None,
        "unit": "F",
        "settlement_value": 75.0,
        "yes_result": yes_result,
        "source": "nws_daily_climate_report",
        "primary_source_type": "nws_daily_climate_report",
        "confidence": confidence,
        "warnings": "",
        "raw_json": "{}",
        "exact_source_available": 1,
        "exact_source_type": "nws_daily_climate_report",
        "exact_source_report_id": "CLI-NYC-1",
        "exact_settlement_value": 75.0,
        "fallback_source_type": None,
        "fallback_settlement_value": None,
        "exact_vs_fallback_diff": None,
        "settlement_version": "test",
    })


def _save_book(storage: Storage, ticker: str, ts: datetime = datetime(2026, 5, 20, 12, 0)) -> None:
    storage.upsert_live_orderbook_snapshot({
        "market_ticker": ticker,
        "ts": ts,
        "yes_best_bid": 40,
        "yes_best_ask": 42,
        "no_best_bid": 58,
        "no_best_ask": 60,
    })


class _FakeHTTPResponse:
    def __init__(self, status_code: int, text_value: str = "", payload: dict | None = None):
        self.status_code = status_code
        self.text = text_value
        self._payload = payload or {}
        self.url = "https://example.test/weather"

    def json(self) -> dict:
        return self._payload


class _FakeHTTPSession:
    def __init__(self, responses: list[_FakeHTTPResponse]):
        self.responses = responses
        self.calls = 0
        self.headers: dict[str, str] = {}

    def get(self, *args, **kwargs) -> _FakeHTTPResponse:
        self.calls += 1
        if self.responses:
            return self.responses.pop(0)
        return _FakeHTTPResponse(500)


def _missing_climate_report(station_code: str, local_date: date) -> ClimateReportResult:
    return ClimateReportResult(
        station_code=station_code,
        local_date=local_date,
        report_product_id=None,
        office=None,
        report_url=None,
        issued_at=None,
        raw_text=None,
        parsed=None,
        warnings=["no exact report fixture"],
    )


def test_settlement_comparator_logic():
    assert evaluate_condition(90, 89, "gt") == 1
    assert evaluate_condition(90, 90, "gt") == 0
    assert evaluate_condition(90, 90, "gte") == 1
    assert evaluate_condition(32, 33, "lt") == 1
    assert evaluate_condition(32, 32, "lt") == 0
    assert evaluate_condition(32, 32, "lte") == 1


def test_range_bucket_settlement_logic():
    contract = WeatherContract(
        event_ticker="KXHIGHPHIL-26APR30",
        market_ticker="KXHIGHPHIL-26APR30-B66.5",
        title="Will the high temp in Philadelphia be 66-67 degrees?",
        local_date=date(2026, 4, 30),
        variable_type="high_temp",
        contract_type="range_bucket",
        range_low=66,
        range_high=67,
    )
    assert evaluate_contract_result(contract, 66) == 1
    assert evaluate_contract_result(contract, 67) == 1
    assert evaluate_contract_result(contract, 68) == 0


def test_strict_threshold_settlement_logic():
    above = WeatherContract(
        event_ticker="E",
        market_ticker="M",
        variable_type="high_temp",
        contract_type="threshold_above",
        threshold=73,
        comparator="gt",
    )
    at_least = above.model_copy(update={"comparator": "gte"})
    assert evaluate_contract_result(above, 73) == 0
    assert evaluate_contract_result(at_least, 73) == 1


def test_weather_settlement_coverage_reports_exact_buildable_and_blockers(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    mature_day = date(2026, 5, 20)
    future_day = date(2026, 5, 26)
    contracts = [
        WeatherContract(
            event_ticker="E1",
            market_ticker="KXHIGHNY-26MAY20-T70",
            city="NYC",
            station_code="KNYC",
            local_date=mature_day,
            variable_type="high_temp",
            contract_type="threshold_above",
            threshold=70,
            comparator="gt",
        ),
        WeatherContract(
            event_ticker="E2",
            market_ticker="KXHIGHNY-26MAY26-T70",
            city="NYC",
            station_code="KNYC",
            local_date=future_day,
            variable_type="high_temp",
            contract_type="threshold_above",
            threshold=70,
            comparator="gt",
        ),
        WeatherContract(
            event_ticker="E3",
            market_ticker="KXUNKNOWN-26MAY20-T70",
            local_date=mature_day,
            variable_type="high_temp",
            contract_type="threshold_above",
            threshold=70,
            comparator="gt",
        ),
        WeatherContract(
            event_ticker="E4",
            market_ticker="KXRAINNYC-26MAY20-T0",
            city="NYC",
            station_code="KNYC",
            local_date=mature_day,
            variable_type="precipitation",
            contract_type="unknown",
        ),
    ]
    for contract in contracts:
        storage.save_parsed_contract(contract)
    storage.upsert_nws_daily_climate_report({
        "station_code": "KNYC",
        "local_date": mature_day.isoformat(),
        "report_product_id": "CLI-NYC-1",
        "office": "OKX",
        "report_url": "https://example.test/cli",
        "issued_at": None,
        "raw_text": "deterministic fixture",
        "parsed_high_temp": 75.0,
        "parsed_low_temp": 60.0,
        "parsed_precip": None,
        "parsed_snowfall": None,
        "parser_confidence": 0.95,
        "warnings": "",
    })
    with storage.engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO weather_observation_snapshots_live (station_code, ts_observed, ts_recorded, raw_json, created_at) "
            "VALUES ('KNYC', '2026-05-20 12:00:00', '2026-05-20 12:05:00', '{}', '2026-05-20 12:05:00')"
        ))

    result = ProjectMaintenance(storage).weather_settlement_coverage(as_of=date(2026, 5, 25))
    payload = result.payload

    assert payload["parsed_contracts_count"] == 4
    assert payload["contracts_eligible_for_exact_settlement"] == 2
    assert payload["labels_newly_buildable_from_existing_exact_sources"] == 1
    assert payload["contracts_not_yet_mature"] == 1
    blockers = dict(payload["top_blocker_reason_codes"])
    assert blockers["eligible_but_unlabeled"] == 1
    assert blockers["not_yet_mature_settleable"] == 1
    assert blockers["missing_station"] == 1
    assert blockers["unsupported_market_format"] == 1


def test_combo_prefix_out_of_scope_contract_is_not_counted_as_missing_station_weather(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    contract = WeatherMarketParser().parse(
        {
            "ticker": "KXMVECROSSCATEGORY-S202664A08EF748B-32EA149A12D",
            "event_ticker": "KXMVECROSSCATEGORY-S202664A08EF748B",
            "title": "yes New York Y,yes Philadelphia,yes Miami,yes Aaron Judge: 1+,yes Ryan Weathers: 3+",
            "occurrence_datetime": "2026-05-24T12:00:00Z",
        }
    )
    storage.save_parsed_contract(contract)

    payload = ProjectMaintenance(storage).weather_settlement_coverage(as_of=date(2026, 5, 25)).payload
    blockers = dict(payload["top_blocker_reason_codes"])

    assert blockers["unsupported_market_format"] == 1
    assert "missing_station" not in blockers
    assert payload["sample_contracts"][0]["station"] is None
    assert payload["sample_contracts"][0]["blockers"] == ["unsupported_market_format"]


def test_weather_settlement_coverage_counts_label_and_replay_overlap(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    contract = WeatherContract(
        event_ticker="E1",
        market_ticker="KXHIGHNY-26MAY20-T70",
        city="NYC",
        station_code="KNYC",
        local_date=date(2026, 5, 20),
        variable_type="high_temp",
        contract_type="threshold_above",
        threshold=70,
        comparator="gt",
    )
    storage.save_parsed_contract(contract)
    storage.upsert_settlement_label({
        "market_ticker": contract.market_ticker,
        "event_ticker": contract.event_ticker,
        "city": contract.city,
        "station_code": contract.station_code,
        "local_date": contract.local_date.isoformat(),
        "variable_type": contract.variable_type,
        "contract_type": contract.contract_type,
        "threshold": contract.threshold,
        "comparator": contract.comparator,
        "range_low": None,
        "range_high": None,
        "unit": "F",
        "settlement_value": 75.0,
        "yes_result": 1,
        "source": "nws_daily_climate_report",
        "primary_source_type": "nws_daily_climate_report",
        "confidence": 0.95,
        "warnings": "",
        "raw_json": "{}",
        "exact_source_available": 1,
        "exact_source_type": "nws_daily_climate_report",
        "exact_source_report_id": "CLI-NYC-1",
        "exact_settlement_value": 75.0,
        "fallback_source_type": None,
        "fallback_settlement_value": None,
        "exact_vs_fallback_diff": None,
        "settlement_version": "test",
    })
    storage.upsert_live_orderbook_snapshot({
        "market_ticker": contract.market_ticker,
        "ts": datetime(2026, 5, 20, 12, 0),
        "yes_best_bid": 40,
        "yes_best_ask": 42,
        "no_best_bid": 58,
        "no_best_ask": 60,
    })

    payload = ProjectMaintenance(storage).weather_settlement_coverage(as_of=date(2026, 5, 25)).payload

    assert payload["labels_already_present"] == 1
    assert payload["high_confidence_labels"] == 1
    assert payload["labels_with_orderbook_snapshots"] == 1
    assert payload["high_confidence_labels_with_orderbook_snapshots"] == 1
    assert payload["replay_coverage"]["sample_high_confidence_replay_ready_tickers"] == [contract.market_ticker]


def test_weather_label_expansion_plan_groups_blockers_without_promoting_labels(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    mature_day = date(2026, 5, 20)
    future_day = date(2026, 5, 26)
    high_conf = _contract("KXHIGHNY-26MAY20-T70", mature_day)
    parser_missing = WeatherContract(
        event_ticker="E2",
        market_ticker="KXPARSE-26MAY20-T70",
        title="Will the high temperature in New York be above 70 degrees?",
        rules="Settlement uses KNYC and the National Weather Service daily climate report.",
        city="New York",
        station_code="KNYC",
        local_date=mature_day,
        variable_type="unknown",
        contract_type="unknown",
    )
    source_missing = _contract("KXSOURCENY-26MAY20-T72", mature_day)
    missing_station = WeatherContract(
        event_ticker="E4",
        market_ticker="KXMISSINGSTATION-26MAY20-T70",
        title="Will the high temp be above 70 degrees?",
        local_date=mature_day,
        variable_type="high_temp",
        contract_type="threshold_above",
        threshold=70,
        comparator="gt",
    )
    missing_date = WeatherContract(
        event_ticker="E5",
        market_ticker="KXMISSINGDATE-T70",
        title="Will the high temp in New York be above 70 degrees?",
        city="New York",
        station_code="KNYC",
        variable_type="high_temp",
        contract_type="threshold_above",
        threshold=70,
        comparator="gt",
    )
    low_conf_fallback = _contract("KXLOWCONF-26MAY20-T70", mature_day)
    future = _contract("KXFUTURE-26MAY26-T70", future_day)
    for contract in [high_conf, parser_missing, source_missing, missing_station, missing_date, low_conf_fallback, future]:
        storage.save_parsed_contract(contract)
    _save_label(storage, high_conf, confidence=0.95)
    storage.upsert_settlement_label({
        "market_ticker": low_conf_fallback.market_ticker,
        "event_ticker": low_conf_fallback.event_ticker,
        "city": low_conf_fallback.city,
        "station_code": low_conf_fallback.station_code,
        "local_date": low_conf_fallback.local_date.isoformat(),
        "variable_type": low_conf_fallback.variable_type,
        "contract_type": low_conf_fallback.contract_type,
        "threshold": low_conf_fallback.threshold,
        "comparator": low_conf_fallback.comparator,
        "range_low": None,
        "range_high": None,
        "unit": "F",
        "settlement_value": 71.0,
        "yes_result": 1,
        "source": "hourly_station_observations",
        "primary_source_type": "hourly_station_observations",
        "confidence": 0.75,
        "warnings": "fallback only",
        "raw_json": "{}",
        "exact_source_available": 0,
        "exact_source_type": None,
        "exact_source_report_id": None,
        "exact_settlement_value": None,
        "fallback_source_type": "hourly_station_observations",
        "fallback_settlement_value": 71.0,
        "exact_vs_fallback_diff": None,
        "settlement_version": "test",
    })

    payload = ProjectMaintenance(storage).weather_label_expansion_plan(as_of=date(2026, 5, 25)).payload
    classifications = dict(payload["grouped_blockers"]["by_expansion_classification"])
    blockers = dict(payload["grouped_blockers"]["by_blocker_reason"])
    station_patterns = dict(payload["grouped_blockers"]["by_missing_station_pattern"])

    assert payload["current_high_conf_labels"] == 1
    assert payload["possible_near_term_high_conf_labels_if_sources_available"] == 1
    assert payload["possible_parser_expansion_labels"] == 1
    assert payload["low_conf_fallback_only_labels"] == 1
    assert payload["future_not_mature"] == 1
    assert classifications["parser_missing_but_settleable"] == 1
    assert classifications["source_missing_but_parser_ok"] == 1
    assert classifications["low_conf_fallback_only"] == 1
    assert blockers["missing_station"] == 1
    assert blockers["missing_date"] == 1
    assert blockers["missing_climate_report_source"] == 1
    assert station_patterns["temperature_market_missing_city_and_station"] == 1
    unsupported = payload["top_unsupported_market_formats"]
    assert unsupported
    parser_row = next(row for row in unsupported if row["classification"] == "parser_missing_but_settleable")
    assert parser_row["examples"][0]["market_ticker"] == parser_missing.market_ticker
    assert parser_row["suggested_parser_task"]
    assert payload["suggested_parser_or_source_tasks"][0]["advisory_only"] is True
    assert payload["safety"]["labels_created"] == 0
    assert len(storage.fetch_table("settlement_labels")) == 2


def test_weather_replay_build_coverage_reports_buildable_high_confidence_overlap(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    contract = _contract("KXHIGHNY-26MAY20-T70")
    storage.save_parsed_contract(contract)
    _save_label(storage, contract, confidence=0.95)
    _save_book(storage, contract.market_ticker)

    payload = ProjectMaintenance(storage).weather_replay_build_coverage(
        start=date(2026, 5, 20),
        end=date(2026, 5, 20),
        min_confidence=0.85,
    ).payload

    assert payload["total_labels"] == 1
    assert payload["labels_above_confidence_threshold"] == 1
    assert payload["labels_with_matching_parsed_contract"] == 1
    assert payload["labels_with_matching_orderbook_ticker"] == 1
    assert payload["labels_with_orderbook_snapshots_inside_replay_window"] == 1
    assert payload["rows_expected_to_be_buildable"] == 1
    assert payload["rows_actually_present"] == 0
    assert payload["zero_replay_blocker"] == "buildable_rows_exist_but_replay_table_empty_run_build_recorded_replay"
    assert payload["candidate_details"][0]["blockers"] == []


def test_weather_replay_build_coverage_respects_confidence_and_missing_snapshot(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    low_conf = _contract("KXHIGHNY-26MAY20-T70")
    missing_snapshot = _contract("KXHIGHNY-26MAY20-T72")
    storage.save_parsed_contract(low_conf)
    storage.save_parsed_contract(missing_snapshot)
    _save_label(storage, low_conf, confidence=0.75)
    _save_label(storage, missing_snapshot, confidence=0.95)
    _save_book(storage, low_conf.market_ticker)

    payload = ProjectMaintenance(storage).weather_replay_build_coverage(
        start=date(2026, 5, 20),
        end=date(2026, 5, 20),
        min_confidence=0.85,
    ).payload
    blockers = dict(payload["top_blocker_reason_codes"])

    assert payload["labels_above_confidence_threshold"] == 1
    assert payload["rows_expected_to_be_buildable"] == 0
    assert blockers["confidence_below_threshold"] == 1
    assert blockers["missing_orderbook_ticker"] == 1
    assert payload["labels_excluded_by_missing_snapshot"] == 1
    assert payload["zero_replay_blocker"] in {"confidence_below_threshold", "missing_orderbook_ticker"}


def test_weather_replay_build_coverage_reports_ticker_mismatch_and_last_days_exclusion(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    outside_window = _contract("KXHIGHNY-26MAY19-T70", date(2026, 5, 19))
    storage.save_parsed_contract(outside_window)
    _save_label(storage, outside_window, confidence=0.95)
    _save_book(storage, outside_window.market_ticker, datetime(2026, 5, 19, 12, 0))

    mismatch = _contract("KXHIGHNY-26MAY20-T70")
    _save_label(storage, mismatch, confidence=0.95)

    missing_outcome = _contract("KXHIGHNY-26MAY20-T72")
    storage.save_parsed_contract(missing_outcome)
    _save_label(storage, missing_outcome, confidence=0.95, yes_result=None)
    _save_book(storage, missing_outcome.market_ticker)

    payload = ProjectMaintenance(storage).weather_replay_build_coverage(
        start=date(2026, 5, 20),
        end=date(2026, 5, 20),
        min_confidence=0.85,
    ).payload
    blockers = dict(payload["top_blocker_reason_codes"])

    assert payload["labels_excluded_by_ticker_mismatch"] == 1
    assert payload["labels_excluded_by_last_days"] == 1
    assert payload["labels_excluded_by_missing_label_outcome"] == 1
    assert blockers["ticker_mismatch_no_matching_parsed_contract"] == 1
    assert blockers["excluded_by_last_days"] == 1
    assert blockers["missing_label_outcome"] == 1


def test_weather_replay_build_coverage_counts_post_day_replay_noise(tmp_path):
    storage = _storage(tmp_path)
    storage.init_db()
    contract = _contract("KXHIGHNY-26MAY20-T70")
    storage.save_parsed_contract(contract)
    _save_label(storage, contract, confidence=0.95)
    _save_book(storage, contract.market_ticker)
    base = {
        "market_ticker": contract.market_ticker,
        "event_ticker": contract.event_ticker,
        "local_date": contract.local_date.isoformat(),
        "variable_type": contract.variable_type,
        "contract_type": contract.contract_type,
        "settlement_confidence": 0.95,
        "yes_result": 1,
        "minutes_to_close": None,
        "minutes_to_settlement": None,
    }
    storage.upsert_recorded_orderbook_replay_snapshot({**base, "ts": datetime(2026, 5, 20, 18, 0)})
    storage.upsert_recorded_orderbook_replay_snapshot({**base, "ts": datetime(2026, 5, 21, 1, 0)})

    payload = ProjectMaintenance(storage).weather_replay_build_coverage(
        start=date(2026, 5, 20),
        end=date(2026, 5, 21),
        min_confidence=0.85,
    ).payload
    tradability = payload["replay_tradability"]

    assert tradability["total_replay_rows"] == 2
    assert tradability["tradable_pre_settlement_or_pre_day_end"] == 1
    assert tradability["post_day_or_post_settlement_noise"] == 1
    assert tradability["unknown_tradability_window"] == 0
    assert tradability["affected_contracts"] == 1


def test_build_exact_settlements_progress_output_preserves_label_semantics(tmp_path, monkeypatch, capsys):
    storage = _storage(tmp_path)
    storage.init_db()
    contract = _contract("KXHIGHNY-26MAY20-T70")
    loader = WeatherSettlementLoader(storage=storage)
    monkeypatch.setattr(loader, "_contracts", lambda start, end, market_ticker: [contract])
    monkeypatch.setattr(
        loader,
        "_label_contract",
        lambda item: {
            "market_ticker": item.market_ticker,
            "event_ticker": item.event_ticker,
            "city": item.city,
            "station_code": item.station_code,
            "local_date": item.local_date.isoformat(),
            "variable_type": item.variable_type,
            "contract_type": item.contract_type,
            "threshold": item.threshold,
            "comparator": item.comparator,
            "settlement_value": 75.0,
            "yes_result": 1,
            "source": "nws_daily_climate_report",
            "primary_source_type": "nws_daily_climate_report",
            "confidence": 0.95,
            "exact_source_available": 1,
            "exact_source_type": "nws_daily_climate_report",
            "settlement_version": "test",
        },
    )

    result = loader.build_settlements(limit=1, progress_interval=1)
    output = capsys.readouterr().out
    labels = storage.fetch_table("settlement_labels")

    assert result.labels == 1
    assert result.skipped == 0
    assert result.processed == 1
    assert len(labels) == 1
    assert int(labels.iloc[0]["yes_result"]) == 1
    assert "build-exact-settlements progress processed=1/1 labels_created_or_updated=1 skipped=0 errors=0" in output


def test_iem_asos_rate_limit_retries_then_uses_successful_response(monkeypatch):
    import data.weather_client as weather_client_module

    csv_text = "station,valid,tmpf\nDEN,2026-05-20T12:00:00+00:00,70.0\n"
    session = _FakeHTTPSession([
        _FakeHTTPResponse(429, "rate limited"),
        _FakeHTTPResponse(200, csv_text),
    ])
    monkeypatch.setattr(weather_client_module, "settings", replace(settings, nws_max_retries=2, nws_backoff_max_seconds=0))
    monkeypatch.setattr(weather_client_module.random, "uniform", lambda start, end: 0.0)
    monkeypatch.setattr(weather_client_module.time, "sleep", lambda seconds: None)

    client = WeatherClient(session=session)
    observations = client.iem_asos_observations("KDEN", date(2026, 5, 20), "America/Denver")

    assert session.calls == 2
    assert len(observations) == 1
    assert observations[0].temp_f == 70.0
    assert client.stats["weather_api_rate_limited_total"] == 1
    assert client.stats["weather_api_skipped_due_to_rate_limit_total"] == 0


def test_build_exact_settlements_dedupes_source_requests_and_skips_rate_limited_fallback(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    storage.init_db()
    day = date(2026, 5, 20)
    contracts = [_contract("KXHIGHNY-26MAY20-T70", day), _contract("KXHIGHNY-26MAY20-T72", day)]

    class FakeClimateClient:
        def __init__(self):
            self.calls = 0

        def fetch_report(self, station_code: str, local_date: date, persist: bool = True):
            self.calls += 1
            return _missing_climate_report(station_code, local_date)

    class FakeWeatherClient:
        def __init__(self):
            self.calls = 0
            self.stats = {
                "weather_api_rate_limited_total": 0,
                "weather_api_skipped_due_to_rate_limit_total": 0,
            }

        def historical_hourly_observations(self, station_code: str, local_date: date, timezone_name: str):
            self.calls += 1
            self.stats["weather_api_rate_limited_total"] += 2
            self.stats["weather_api_skipped_due_to_rate_limit_total"] += 1
            return []

    fake_weather = FakeWeatherClient()
    fake_climate = FakeClimateClient()
    loader = WeatherSettlementLoader(storage=storage, weather_client=fake_weather)
    loader.climate_client = fake_climate
    monkeypatch.setattr(loader, "_contracts", lambda start, end, market_ticker: contracts)

    result = loader.build_settlements(limit=2, progress_interval=1)
    labels = storage.fetch_table("settlement_labels")

    assert result.labels == 0
    assert result.skipped == 2
    assert len(labels) == 0
    assert fake_climate.calls == 1
    assert fake_weather.calls == 1
    assert result.source_requests == 2
    assert result.cache_hits == 2
    assert result.rate_limited == 2
    assert result.skipped_due_to_rate_limit == 1
    assert result.source_errors == 0
