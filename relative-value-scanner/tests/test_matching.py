from datetime import datetime, timezone

from relative_value.config import ScannerConfig
from relative_value.matching import assess_match, compare_text
from relative_value.models import NormalizedMarket, SourceKind


def _market(settlement_time: datetime | None) -> NormalizedMarket:
    return NormalizedMarket(
        venue="kalshi",
        market_id="m",
        event_name="Cleveland Cavaliers vs New York Knicks Cleveland team total over 91.5",
        outcome_name="Cleveland over 91.5 points",
        source_kind=SourceKind.EXCHANGE,
        yes_bid=0.2,
        yes_ask=0.3,
        settlement_time=settlement_time,
        settlement_rule="official NBA box score",
        is_executable=True,
    )


def test_missing_settlement_date_caps_match_confidence() -> None:
    assessment = assess_match(
        _market(datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc)),
        _market(None),
    )
    assert assessment.match_confidence <= 0.75
    assert assessment.settlement_mismatch_risk >= 0.25


def test_contradictory_settlement_date_caps_match_confidence() -> None:
    assessment = assess_match(
        _market(datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc)),
        _market(datetime(2026, 5, 24, 3, 30, tzinfo=timezone.utc)),
    )
    assert assessment.match_confidence <= 0.55
    assert assessment.settlement_mismatch_risk >= 0.60


def test_different_numeric_thresholds_capped() -> None:
    left = _market(datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc))
    right = NormalizedMarket(
        venue="polymarket",
        market_id="p",
        event_name="Cleveland Cavaliers vs New York Knicks Cleveland team total over 101.5",
        outcome_name="Cleveland over 101.5 points",
        source_kind=SourceKind.EXCHANGE,
        yes_bid=0.6,
        yes_ask=0.7,
        settlement_time=datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc),
        settlement_rule="official NBA box score",
        is_executable=True,
    )
    assessment = assess_match(left, right)
    assert assessment.match_confidence <= 0.30
    assert any("numeric_threshold_mismatch" in reason for reason in assessment.reasons)


def test_confidence_requires_both_event_and_outcome_high() -> None:
    left = _market(datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc))
    right = NormalizedMarket(
        venue="polymarket",
        market_id="p",
        event_name=left.event_name,
        outcome_name="New York Knicks over 91.5 points",
        source_kind=SourceKind.EXCHANGE,
        yes_bid=0.6,
        yes_ask=0.7,
        settlement_time=datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc),
        settlement_rule="official NBA box score",
        is_executable=True,
    )
    assessment = assess_match(left, right)
    assert assessment.match_confidence < 0.92
    assert "confidence_requires_both_event_and_outcome_high" in assessment.reasons


def test_confidence_cap_headroom_tracks_possible_arb_threshold() -> None:
    config = ScannerConfig(min_possible_arb_confidence=0.95)
    left = _market(datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc))
    right = NormalizedMarket(
        venue="polymarket",
        market_id="p",
        event_name=left.event_name,
        outcome_name="New York Knicks over 91.5 points",
        source_kind=SourceKind.EXCHANGE,
        yes_bid=0.6,
        yes_ask=0.7,
        settlement_time=datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc),
        settlement_rule="official NBA box score",
        is_executable=True,
    )
    assessment = assess_match(left, right, config)
    assert config.min_possible_arb_confidence - assessment.match_confidence >= 0.05


def test_polarity_win_lose_tagged_in_match_reasons() -> None:
    left = NormalizedMarket(
        venue="kalshi",
        market_id="m",
        event_name="Cavaliers win game",
        outcome_name="Cavaliers win game",
        source_kind=SourceKind.EXCHANGE,
        yes_bid=0.2,
        yes_ask=0.3,
        settlement_time=datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc),
        settlement_rule="official NBA box score",
        is_executable=True,
    )
    right = NormalizedMarket(
        venue="polymarket",
        market_id="p",
        event_name="Cavaliers lose game",
        outcome_name="Cavaliers lose game",
        source_kind=SourceKind.EXCHANGE,
        yes_bid=0.7,
        yes_ask=0.8,
        settlement_time=datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc),
        settlement_rule="official NBA box score",
        is_executable=True,
    )
    assessment = assess_match(left, right)
    assert any("opposite_polarity_detected" in reason for reason in assessment.reasons)


def test_polarity_contraction_doesnt_vs_wins() -> None:
    comparison = compare_text("Knicks doesn't win", "Knicks wins")
    assert comparison.opposite_side is True
    assert "opposite_polarity_detected" in comparison.reasons


def test_polarity_contraction_wont_vs_wins() -> None:
    comparison = compare_text("Knicks won't win", "Knicks wins")
    assert comparison.opposite_side is True
    assert "opposite_polarity_detected" in comparison.reasons


def test_settlement_rule_stub_token_rejected() -> None:
    left = _market(datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc))
    right = NormalizedMarket(
        venue="polymarket",
        market_id="p",
        event_name=left.event_name,
        outcome_name=left.outcome_name,
        source_kind=SourceKind.EXCHANGE,
        yes_bid=0.7,
        yes_ask=0.8,
        settlement_time=datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc),
        settlement_rule="official",
        is_executable=True,
    )
    assessment = assess_match(left, right)
    assert "side_definition_unverified" in assessment.reasons
    assert assessment.match_confidence <= 0.80


def test_compare_text_exact_match_short_circuits() -> None:
    comparison = compare_text("Cavaliers win game", "Cavaliers win game")
    assert comparison.score == 1.0
    assert comparison.same_side is True
