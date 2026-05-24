from datetime import datetime, timezone

import pytest

from relative_value.models import NormalizedMarket, SourceKind


def test_market_midpoint_uses_yes_bid_ask() -> None:
    market = NormalizedMarket(
        venue="kalshi",
        market_id="m1",
        event_name="Event",
        outcome_name="Outcome",
        source_kind=SourceKind.EXCHANGE,
        yes_bid=0.2,
        yes_ask=0.4,
        liquidity_top_contracts=10,
        settlement_time=datetime(2026, 5, 20, tzinfo=timezone.utc),
        is_executable=True,
    )
    assert market.midpoint == pytest.approx(0.3)


def test_market_rejects_invalid_probability() -> None:
    with pytest.raises(ValueError):
        NormalizedMarket(
            venue="kalshi",
            market_id="m1",
            event_name="Event",
            outcome_name="Outcome",
            source_kind=SourceKind.EXCHANGE,
            yes_bid=1.2,
        )


def test_sportsbook_reference_cannot_be_executable() -> None:
    with pytest.raises(ValueError):
        NormalizedMarket(
            venue="book",
            market_id="b1",
            event_name="Event",
            outcome_name="Outcome",
            source_kind=SourceKind.SPORTSBOOK_REFERENCE,
            yes_reference_probability=0.5,
            is_executable=True,
        )


def test_naive_datetime_raises_clearly() -> None:
    with pytest.raises(ValueError, match="timezone"):
        NormalizedMarket(
            venue="kalshi",
            market_id="m1",
            event_name="Event",
            outcome_name="Outcome",
            source_kind=SourceKind.EXCHANGE,
            yes_bid=0.2,
            yes_ask=0.3,
            settlement_time=datetime(2026, 5, 20),
            is_executable=True,
        )


def test_naive_captured_at_raises_clearly() -> None:
    with pytest.raises(ValueError, match="captured_at"):
        NormalizedMarket(
            venue="kalshi",
            market_id="m1",
            event_name="Event",
            outcome_name="Outcome",
            source_kind=SourceKind.EXCHANGE,
            yes_bid=0.2,
            yes_ask=0.3,
            captured_at=datetime(2026, 5, 20),
            is_executable=True,
        )
