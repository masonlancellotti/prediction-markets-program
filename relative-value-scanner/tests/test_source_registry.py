import pytest

from relative_value.source_registry import (
    EFFECT_CANDIDATE_PAIR,
    EFFECT_DISCOVERY_CLUSTERING,
    EFFECT_WATCH_DIAGNOSTICS,
    ImplementationStatus,
    SourceType,
    UnknownSourceError,
    can_create_tradable_candidate_pair,
    get_source_entry,
    is_executable_candidate_source,
    source_registry_report,
)


def test_kalshi_and_polymarket_are_executable_candidate_sources() -> None:
    for source_id in ("kalshi", "polymarket"):
        entry = get_source_entry(source_id)

        assert entry.source_type == SourceType.EXECUTABLE_VENUE
        assert entry.implementation_status == ImplementationStatus.IMPLEMENTED_READ_ONLY
        assert entry.can_create_candidate_pair is True
        assert EFFECT_CANDIDATE_PAIR in entry.allowed_effects

    assert can_create_tradable_candidate_pair("kalshi", "polymarket") is True


@pytest.mark.parametrize("source_id", ["manifold", "metaculus", "the_odds_api", "sportsbooks"])
def test_signal_and_reference_sources_are_not_executable_candidate_sources(source_id: str) -> None:
    entry = get_source_entry(source_id)

    assert entry.source_type in {SourceType.REFERENCE_ONLY, SourceType.SIGNAL_ONLY}
    assert entry.can_create_candidate_pair is False
    assert is_executable_candidate_source(source_id) is False


def test_forecastex_ibkr_is_planned_but_not_candidate_enabled() -> None:
    entry = get_source_entry("forecastex_ibkr")

    assert entry.source_type == SourceType.EXECUTABLE_VENUE
    assert entry.implementation_status == ImplementationStatus.PLANNED_NOT_IMPLEMENTED
    assert entry.is_implemented is False
    assert entry.can_create_candidate_pair is False


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("kalshi", "the_odds_api"),
        ("polymarket", "manifold"),
        ("metaculus", "manifold"),
        ("forecastex_ibkr", "kalshi"),
    ],
)
def test_non_executable_or_planned_sources_cannot_create_tradable_pairs(left: str, right: str) -> None:
    assert can_create_tradable_candidate_pair(left, right) is False


def test_reference_and_signal_effects_are_limited() -> None:
    odds = get_source_entry("the odds api")
    manifold = get_source_entry("manifold")

    assert odds.can_inform_watch_or_diagnostics is True
    assert odds.can_inform_discovery_or_clustering is False
    assert EFFECT_WATCH_DIAGNOSTICS in odds.allowed_effects
    assert EFFECT_DISCOVERY_CLUSTERING not in odds.allowed_effects

    assert manifold.can_inform_watch_or_diagnostics is False
    assert manifold.can_inform_discovery_or_clustering is True
    assert manifold.allowed_effects == (EFFECT_DISCOVERY_CLUSTERING,)


def test_unknown_source_raises_clear_error() -> None:
    with pytest.raises(UnknownSourceError, match="unknown source: unknown_api"):
        get_source_entry("unknown_api")


def test_source_registry_report_serializes_policy_fields() -> None:
    report = source_registry_report()
    kalshi = next(row for row in report if row["source_id"] == "kalshi")
    odds = next(row for row in report if row["source_id"] == "the_odds_api")

    assert kalshi["source_type"] == "EXECUTABLE_VENUE"
    assert kalshi["can_create_candidate_pair"] is True
    assert odds["source_type"] == "REFERENCE_ONLY"
    assert odds["can_create_candidate_pair"] is False
