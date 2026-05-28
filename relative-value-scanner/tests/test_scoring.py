from datetime import datetime, timedelta, timezone

import pytest

from relative_value.config import ScannerConfig
from relative_value.fees import FlatFeeModel, KalshiTieredFeeModel, NoFeeModel
from relative_value.models import ACTION_SEVERITY
from relative_value.models import Action, NormalizedMarket, SourceKind
from relative_value.report import _REASON_PRIORITY
from relative_value.scanner import RelativeValueScanner
from relative_value.scoring import _reference_gap, score_pair


BASE_TIME = datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc)


def _exchange(
    venue: str,
    bid: float,
    ask: float,
    liquidity_top_contracts: float = 100.0,
    settlement_time: datetime | None = BASE_TIME,
    captured_at: datetime | None = BASE_TIME,
    event_name: str = "Cleveland Cavaliers vs New York Knicks Cleveland team total over 91.5",
    outcome_name: str = "Cleveland over 91.5 points",
) -> NormalizedMarket:
    return NormalizedMarket(
        venue=venue,
        market_id=f"{venue}:1",
        event_name=event_name,
        outcome_name=outcome_name,
        source_kind=SourceKind.EXCHANGE,
        yes_bid=bid,
        yes_ask=ask,
        liquidity_top_contracts=liquidity_top_contracts,
        settlement_time=settlement_time,
        captured_at=captured_at,
        settlement_rule="official NBA box score",
        is_executable=True,
    )


def _sportsbook(probability: float) -> NormalizedMarket:
    return NormalizedMarket(
        venue="book",
        market_id="book:1",
        event_name="Cleveland Cavaliers vs New York Knicks Cleveland team total over 91.5",
        outcome_name="Cleveland over 91.5 points",
        source_kind=SourceKind.SPORTSBOOK_REFERENCE,
        yes_reference_probability=probability,
        settlement_time=BASE_TIME,
        captured_at=BASE_TIME,
        settlement_rule="sportsbook grading may differ",
    )


def test_sportsbook_reference_pair_can_never_be_possible_arb() -> None:
    candidate = score_pair(_exchange("kalshi", 0.2, 0.25), _sportsbook(0.7))
    assert candidate.action != Action.POSSIBLE_ARB
    assert "sportsbook odds are reference-only" in candidate.reasons


def test_settlement_mismatch_reduces_action_severity() -> None:
    left = _exchange("kalshi", 0.2, 0.25)
    right = _exchange("polymarket", 0.5, 0.6, settlement_time=BASE_TIME + timedelta(days=3))
    candidate = score_pair(left, right)
    assert candidate.action == Action.WATCH
    assert candidate.match.settlement_mismatch_risk >= 0.60


def test_low_confidence_reduces_action_severity() -> None:
    left = _exchange("kalshi", 0.2, 0.25)
    right = _exchange(
        "polymarket",
        0.7,
        0.75,
        event_name="Completely unrelated presidential election market",
        outcome_name="different outcome",
    )
    candidate = score_pair(left, right)
    assert candidate.action == Action.IGNORE


def test_possible_arb_requires_hard_gates() -> None:
    left = _exchange("kalshi", 0.1, 0.2, liquidity_top_contracts=100)
    right = _exchange("polymarket", 0.32, 0.35, liquidity_top_contracts=100)
    candidate = score_pair(left, right)
    assert candidate.action == Action.POSSIBLE_ARB
    assert candidate.fee_adjusted_gap and candidate.fee_adjusted_gap >= 0.02
    assert candidate.fees_applied


def test_possible_arb_blocked_by_low_liquidity() -> None:
    left = _exchange("kalshi", 0.1, 0.2, liquidity_top_contracts=5)
    right = _exchange("polymarket", 0.32, 0.35, liquidity_top_contracts=100)
    candidate = score_pair(left, right)
    assert candidate.action != Action.POSSIBLE_ARB


def test_opposite_outcomes_never_possible_arb() -> None:
    left = _exchange(
        "kalshi",
        0.1,
        0.2,
        event_name="Example City vs Sample Town total over 100.5",
        outcome_name="total over 100.5 points",
    )
    right = _exchange(
        "polymarket",
        0.85,
        0.9,
        event_name="Example City vs Sample Town total under 100.5",
        outcome_name="total under 100.5 points",
    )
    candidate = score_pair(left, right)
    assert candidate.action != Action.POSSIBLE_ARB
    assert candidate.match.match_confidence <= 0.30
    assert any("opposite_polarity_detected" in reason for reason in candidate.match.reasons)


def test_different_settlement_dates_never_possible_arb() -> None:
    left = _exchange("kalshi", 0.1, 0.2)
    right = _exchange("polymarket", 0.32, 0.35, settlement_time=BASE_TIME + timedelta(days=2))
    candidate = score_pair(left, right)
    assert candidate.action != Action.POSSIBLE_ARB
    assert candidate.match.settlement_mismatch_risk >= 0.60


@pytest.mark.parametrize("fee_model", [FlatFeeModel(per_leg=0.015), KalshiTieredFeeModel()])
def test_realistic_fees_block_marginal_gap(fee_model) -> None:
    left = _exchange("kalshi", 0.1, 0.2)
    right = _exchange("polymarket", 0.225, 0.35)
    candidate = score_pair(left, right, ScannerConfig(fee_model=fee_model))
    assert candidate.gross_gap and candidate.gross_gap > 0
    assert candidate.action != Action.POSSIBLE_ARB


def test_action_severity_monotonic_under_penalty() -> None:
    left = _exchange("kalshi", 0.1, 0.2)
    right = _exchange("polymarket", 0.32, 0.35)
    severities = []
    for penalty in [0.0, 0.01, 0.03, 0.08]:
        candidate = score_pair(left, right, ScannerConfig(no_side_spread_penalty=penalty))
        severities.append(ACTION_SEVERITY[candidate.action])
    assert severities == sorted(severities, reverse=True)


def test_reference_gap_opposite_side_inverts_probability() -> None:
    exchange = _exchange("kalshi", 0.18, 0.22)
    sportsbook = NormalizedMarket(
        venue="book",
        market_id="book:1",
        event_name=exchange.event_name,
        outcome_name="Cleveland under 91.5 points",
        source_kind=SourceKind.SPORTSBOOK_REFERENCE,
        yes_reference_probability=0.70,
        settlement_time=BASE_TIME,
        captured_at=BASE_TIME,
        settlement_rule="sportsbook grading may differ",
    )
    gap, reasons = _reference_gap(exchange, sportsbook)
    assert gap == pytest.approx(0.10)
    assert "opposite_reference_outcome_inverted" in reasons


def test_liquidity_unit_gate_is_inclusive_at_min() -> None:
    left = _exchange("kalshi", 0.1, 0.2, liquidity_top_contracts=25)
    right = _exchange("polymarket", 0.32, 0.35, liquidity_top_contracts=25)
    candidate = score_pair(left, right)
    assert candidate.action == Action.POSSIBLE_ARB
    assert candidate.limiting_liquidity_top_contracts == 25


@pytest.mark.parametrize("fee_model", [NoFeeModel(), FlatFeeModel(per_leg=0.015), KalshiTieredFeeModel()])
@pytest.mark.parametrize("liquidity_top_contracts", [100.0, 25.0, 10.0])
def test_action_monotonic_over_fee_and_liquidity(fee_model, liquidity_top_contracts) -> None:
    left = _exchange("kalshi", 0.1, 0.2, liquidity_top_contracts=liquidity_top_contracts)
    right = _exchange("polymarket", 0.32, 0.35, liquidity_top_contracts=liquidity_top_contracts)
    candidate = score_pair(left, right, ScannerConfig(fee_model=fee_model))
    if liquidity_top_contracts < 25:
        assert candidate.action != Action.POSSIBLE_ARB
    if not isinstance(fee_model, NoFeeModel):
        no_fee_candidate = score_pair(left, right, ScannerConfig(fee_model=NoFeeModel()))
        assert ACTION_SEVERITY[candidate.action] <= ACTION_SEVERITY[no_fee_candidate.action]


def test_two_sportsbook_venues_no_reference_gap() -> None:
    left = _sportsbook(0.55)
    right = NormalizedMarket(
        venue="other_book",
        market_id="other:1",
        event_name=left.event_name,
        outcome_name=left.outcome_name,
        source_kind=SourceKind.SPORTSBOOK_REFERENCE,
        yes_reference_probability=0.45,
        settlement_time=BASE_TIME,
        captured_at=BASE_TIME,
        settlement_rule="sportsbook grading may differ",
    )
    gap, reasons = _reference_gap(left, right)
    assert gap is None
    assert reasons == ("both_sides_sportsbook_reference",)


def test_stale_quote_caps_action_at_manual_review() -> None:
    left = _exchange("kalshi", 0.1, 0.2, captured_at=BASE_TIME)
    right = _exchange("polymarket", 0.32, 0.35, captured_at=BASE_TIME + timedelta(minutes=5))
    candidate = score_pair(left, right)
    assert candidate.action == Action.MANUAL_REVIEW
    assert "stale_quote" in candidate.reasons


def test_quote_freshness_unverified_caps_action_below_paper() -> None:
    left = _exchange("kalshi", 0.1, 0.2, captured_at=None)
    right = _exchange("polymarket", 0.32, 0.35, captured_at=None)
    candidate = score_pair(left, right)
    assert ACTION_SEVERITY[candidate.action] < ACTION_SEVERITY[Action.PAPER]
    assert "quote_freshness_unverified" in candidate.reasons


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (0.05, 0.0075),
        (0.20, 0.014),
        (0.50, 0.02),
        (0.80, 0.014),
        (0.95, 0.0075),
    ],
)
def test_kalshi_tiered_fee_model_expected_values(price: float, expected: float) -> None:
    assert KalshiTieredFeeModel().fee_for_leg(price) == pytest.approx(expected)


def test_executable_pair_direction_includes_both_venue_names() -> None:
    left = _exchange("kalshi", 0.1, 0.2)
    right = _exchange("polymarket", 0.32, 0.35)
    candidate = score_pair(left, right)
    assert "kalshi" in candidate.direction
    assert "polymarket" in candidate.direction


def test_ibkr_accessed_kalshi_is_not_independent_from_direct_kalshi() -> None:
    direct = _exchange("kalshi", 0.1, 0.2)
    routed = NormalizedMarket(
        venue="IBKR_KALSHI",
        market_id="ibkr-kalshi:1",
        event_name=direct.event_name,
        outcome_name=direct.outcome_name,
        source_kind=SourceKind.EXCHANGE,
        yes_bid=0.32,
        yes_ask=0.35,
        liquidity_top_contracts=100,
        settlement_time=BASE_TIME,
        captured_at=BASE_TIME,
        settlement_rule=direct.settlement_rule,
        is_executable=True,
        source_platform="IBKR",
        access_platform="IBKR",
        exchange_venue="KALSHI",
        executable_venue="KALSHI",
    )

    candidate = score_pair(direct, routed, ScannerConfig(fee_model=NoFeeModel()))

    assert candidate.action == Action.IGNORE
    assert "ibkr_kalshi_is_same_exchange_as_direct_kalshi" in candidate.reasons
    assert "broker_route_not_independent_venue" in candidate.reasons
    assert "do_not_cross_compare_as_independent_arb" in candidate.reasons
    assert "PAPER_CANDIDATE" not in str(candidate.to_dict())
    default_scan = RelativeValueScanner(ScannerConfig(fee_model=NoFeeModel())).scan([direct, routed])
    assert default_scan == []


def test_ibkr_forecastex_remains_separate_from_direct_kalshi() -> None:
    direct = _exchange("kalshi", 0.1, 0.2, captured_at=None)
    forecastex = NormalizedMarket(
        venue="IBKR_FORECASTEX",
        market_id="ibkr-forecastex:1",
        event_name=direct.event_name,
        outcome_name=direct.outcome_name,
        source_kind=SourceKind.EXCHANGE,
        yes_bid=0.32,
        yes_ask=0.35,
        liquidity_top_contracts=100,
        settlement_time=BASE_TIME,
        captured_at=None,
        settlement_rule=direct.settlement_rule,
        is_executable=True,
        source_platform="IBKR",
        access_platform="IBKR",
        exchange_venue="FORECASTX",
        executable_venue="FORECASTX",
    )

    candidate = score_pair(direct, forecastex, ScannerConfig(fee_model=NoFeeModel()))

    assert candidate.action != Action.IGNORE
    assert "ibkr_kalshi_is_same_exchange_as_direct_kalshi" not in candidate.reasons
    assert candidate.right.to_dict()["source_platform"] == "IBKR"
    assert candidate.right.to_dict()["access_platform"] == "IBKR"
    assert candidate.right.to_dict()["exchange_venue"] == "FORECASTX"
    assert candidate.right.to_dict()["executable_venue"] == "FORECASTX"


def test_reason_priority_covers_representative_scoring_reasons() -> None:
    representative_pairs = [
        (_exchange("kalshi", 0.1, 0.2), _exchange("polymarket", 0.32, 0.35)),
        (_exchange("kalshi", 0.1, 0.2, captured_at=None), _exchange("polymarket", 0.32, 0.35, captured_at=None)),
        (_exchange("kalshi", 0.1, 0.2, captured_at=BASE_TIME), _exchange("polymarket", 0.32, 0.35, captured_at=BASE_TIME + timedelta(minutes=5))),
        (_exchange("kalshi", 0.1, 0.2), _sportsbook(0.7)),
        (
            _exchange("kalshi", 0.18, 0.22),
            NormalizedMarket(
                venue="book",
                market_id="book:1",
                event_name="Cleveland Cavaliers vs New York Knicks Cleveland team total over 91.5",
                outcome_name="Cleveland under 91.5 points",
                source_kind=SourceKind.SPORTSBOOK_REFERENCE,
                yes_reference_probability=0.70,
                settlement_time=BASE_TIME,
                captured_at=BASE_TIME,
                settlement_rule="sportsbook grading may differ",
            ),
        ),
        (
            _sportsbook(0.55),
            NormalizedMarket(
                venue="other_book",
                market_id="other:1",
                event_name="Cleveland Cavaliers vs New York Knicks Cleveland team total over 91.5",
                outcome_name="Cleveland over 91.5 points",
                source_kind=SourceKind.SPORTSBOOK_REFERENCE,
                yes_reference_probability=0.45,
                settlement_time=BASE_TIME,
                captured_at=BASE_TIME,
                settlement_rule="sportsbook grading may differ",
            ),
        ),
        (
            _exchange(
                "kalshi",
                0.1,
                0.2,
                event_name="Example City vs Sample Town total over 100.5",
                outcome_name="total over 100.5 points",
            ),
            _exchange(
                "polymarket",
                0.85,
                0.9,
                event_name="Example City vs Sample Town total under 100.5",
                outcome_name="total under 100.5 points",
            ),
        ),
        (
            _exchange("kalshi", 0.2, 0.25),
            _exchange(
                "polymarket",
                0.5,
                0.6,
                settlement_time=BASE_TIME + timedelta(days=3),
            ),
        ),
    ]
    reasons = {
        reason
        for left, right in representative_pairs
        for reason in score_pair(left, right).reasons
    }
    uncovered = [
        reason
        for reason in reasons
        if not any(tag in reason for tag in _REASON_PRIORITY)
    ]
    assert uncovered == []
