"""Contract-grammar classifier tests (Part A / Part H 1-4, 8)."""
from __future__ import annotations

from relative_value.crypto_contract_grammar import (
    CONTRACT_FAMILY_BARRIER_TOUCH,
    CONTRACT_FAMILY_DIRECTIONAL_RETURN,
    CONTRACT_FAMILY_TERMINAL_RANGE,
    CONTRACT_FAMILY_TERMINAL_THRESHOLD,
    classify_contract_family,
    families_compatible,
    normalize_contract_row,
)


def test_classifies_terminal_threshold() -> None:
    assert classify_contract_family(
        payoff_observation_type="point_in_time_at_target", comparator="above", threshold_value=70000,
        title="Will Bitcoin be above $70,000 at 1:00 PM ET?",
    ) == CONTRACT_FAMILY_TERMINAL_THRESHOLD


def test_classifies_terminal_range() -> None:
    assert classify_contract_family(
        payoff_observation_type="range_at_target", comparator="range", lower_bound=72000, upper_bound=72500,
        title="Bitcoin between $72,000 and $72,500 at 5pm EDT?",
    ) == CONTRACT_FAMILY_TERMINAL_RANGE


def test_classifies_directional_return_updown() -> None:
    assert classify_contract_family(
        payoff_observation_type="interval_start_to_end_change", comparator="up",
        title="Bitcoin Up or Down — May 30, 3PM ET (1h)?",
    ) == CONTRACT_FAMILY_DIRECTIONAL_RETURN
    # Even without the obs type, the title's up/down + open-price wording wins.
    assert classify_contract_family(
        comparator="unknown", title="Will ETH finish higher than its open price this hour?",
    ) == CONTRACT_FAMILY_DIRECTIONAL_RETURN


def test_classifies_barrier_touch() -> None:
    # Path-dependent "hit" wins even though a strike is present.
    assert classify_contract_family(
        payoff_observation_type="point_in_time_at_target", comparator="above", threshold_value=80000,
        title="Will Bitcoin hit $80,000 at any point this week?",
    ) == CONTRACT_FAMILY_BARRIER_TOUCH
    assert classify_contract_family(payoff_observation_type="touch_before_deadline", comparator="touch") == CONTRACT_FAMILY_BARRIER_TOUCH


def test_classifies_cdna_over_strike_as_terminal_threshold() -> None:
    assert classify_contract_family(
        comparator="above", threshold_value=70000,
        title="BTC Over $70,000 20m", rules_text="Settles to the Rule 14.69 expiration value.",
    ) == CONTRACT_FAMILY_TERMINAL_THRESHOLD


def test_families_compatible_terminal_share_state_space() -> None:
    assert families_compatible(CONTRACT_FAMILY_TERMINAL_THRESHOLD, CONTRACT_FAMILY_TERMINAL_RANGE) is True
    assert families_compatible(CONTRACT_FAMILY_TERMINAL_THRESHOLD, CONTRACT_FAMILY_DIRECTIONAL_RETURN) is False
    assert families_compatible(CONTRACT_FAMILY_BARRIER_TOUCH, CONTRACT_FAMILY_BARRIER_TOUCH) is False


def test_normalize_contract_row_projects_full_schema() -> None:
    row = {
        "asset": "BTC", "platform": "kalshi", "market_shape": "point_in_time_threshold",
        "payoff_observation_type": "point_in_time_at_target", "comparator": "above",
        "threshold_or_strike": 70000.0, "bucket_floor": None, "bucket_cap": None,
        "reference_start_utc": None, "target_instant_utc": "2026-05-30T06:00:00+00:00",
        "price_source": "cf_benchmarks_brti", "market_id_or_ticker": "KXBTC-x",
        "quote": {"yes_ask": 0.4, "no_ask": 0.62, "yes_ask_size": 100.0, "quote_timestamp": "t", "depth_status": "top"},
    }
    n = normalize_contract_row(row)
    assert n["contract_family"] == CONTRACT_FAMILY_TERMINAL_THRESHOLD
    assert n["threshold_value"] == 70000.0
    assert n["direction"] == "above" and n["inclusivity"] == ">"
    assert n["yes_ask"] == 0.4 and n["no_ask"] == 0.62
    assert n["settlement_source"] == "cf_benchmarks_brti"
    for key in (
        "platform", "asset", "contract_family", "payoff_observation_type", "observation_start_utc",
        "reference_start_utc", "target_instant_utc", "settlement_time_utc", "timezone", "settlement_source",
        "price_source", "threshold_value", "lower_bound", "upper_bound", "reference_price", "reference_lock_time",
        "direction", "inclusivity", "tie_rule", "yes_bid", "yes_ask", "no_bid", "no_ask", "bid_size", "ask_size",
        "quote_timestamp", "depth_status", "blockers",
    ):
        assert key in n, f"normalized row missing {key}"
