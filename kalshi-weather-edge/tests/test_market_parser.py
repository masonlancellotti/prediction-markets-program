from parsing.market_parser import WeatherMarketParser


def test_parse_high_temperature_market_with_rules_station():
    market = {
        "ticker": "KXHIGHNY-26APR27-T90",
        "event_ticker": "KXHIGHNY-26APR27",
        "title": "Will the highest temperature in New York be 90 degrees or higher on Apr 27?",
        "rules_primary": "This market resolves according to the National Weather Service observation at KNYC Central Park.",
        "close_time": "2026-04-27T23:00:00Z",
        "expiration_time": "2026-04-28T03:00:00Z",
        "occurrence_datetime": "2026-04-27T12:00:00Z",
    }
    contract = WeatherMarketParser().parse(market)
    assert contract.variable_type == "high_temp"
    assert contract.threshold == 90
    assert contract.comparator == "gte"
    assert contract.contract_type == "threshold_above"
    assert contract.city == "New York"
    assert contract.station_code == "KNYC"
    assert contract.settlement_source == "NWS/NOAA"
    assert contract.parse_confidence >= 0.9
    assert contract.is_tradable


def test_unclear_rules_are_not_tradable():
    market = {
        "ticker": "KXWEATHER-UNCLEAR",
        "event_ticker": "KXWEATHER",
        "title": "Will it be hot in the city today?",
    }
    contract = WeatherMarketParser().parse(market)
    assert not contract.is_tradable
    assert contract.threshold is None
    assert contract.warnings


def test_parse_temperature_range_bucket_market():
    market = {
        "ticker": "KXHIGHPHIL-26APR30-B66.5",
        "event_ticker": "KXHIGHPHIL-26APR30",
        "title": "Will the high temp in Philadelphia be 66-67 degrees on Apr 30?",
        "rules_primary": "This market resolves according to the National Weather Service observation at KPHL.",
        "occurrence_datetime": "2026-04-30T12:00:00Z",
    }
    contract = WeatherMarketParser().parse(market)
    assert contract.variable_type == "high_temp"
    assert contract.contract_type == "range_bucket"
    assert contract.range_low == 66
    assert contract.range_high == 67
    assert contract.threshold is None
    assert contract.comparator == "unknown"
    assert contract.is_tradable


def test_multivariate_combo_prefix_disables_weather_station_inference_for_player_name():
    market = {
        "ticker": "KXMVESPORTSMULTIGAMEEXTENDED-S20265B765FD9D82-69E1FF62A1B",
        "event_ticker": "KXMVESPORTSMULTIGAMEEXTENDED-S20265B765FD9D82",
        "title": "yes Ryan Weathers: 4+,yes Aaron Judge: 1+,yes Over 8.5 runs scored",
        "occurrence_datetime": "2026-05-24T12:00:00Z",
    }

    contract = WeatherMarketParser().parse(market)

    assert contract.variable_type == "unknown"
    assert contract.contract_type == "unknown"
    assert contract.city is None
    assert contract.station_code is None
    assert contract.station_confidence == 0
    assert not contract.is_tradable
    assert "not_weather_or_out_of_scope" in contract.warnings
    assert "station inference disabled for non-weather combo market" in contract.warnings


def test_cross_category_combo_prefix_does_not_infer_city_team_names_as_weather_stations():
    market = {
        "ticker": "KXMVECROSSCATEGORY-S202664A08EF748B-32EA149A12D",
        "event_ticker": "KXMVECROSSCATEGORY-S202664A08EF748B",
        "title": "yes New York Y,yes Philadelphia,yes Miami,yes Aaron Judge: 1+,yes Ryan Weathers: 3+",
        "occurrence_datetime": "2026-05-24T12:00:00Z",
    }

    contract = WeatherMarketParser().parse(market)

    assert contract.city is None
    assert contract.station_code is None
    assert contract.variable_type == "unknown"
    assert contract.contract_type == "unknown"
    assert "unsupported_market_format" in contract.warnings
