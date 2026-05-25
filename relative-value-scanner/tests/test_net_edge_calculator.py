from __future__ import annotations

import json
from datetime import datetime, timezone

from relative_value.fees import FlatFeeModel
from relative_value.net_edge_calculator import (
    BUY_NO,
    BUY_YES,
    NetEdgeConfig,
    calculate_exact_group_net_edge,
    calculate_manual_net_edge,
    calculate_structural_basket_net_edge,
)


NOW = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)


def _leg(
    venue: str = "kalshi",
    ask: float = 0.49,
    depth: float | None = 5.0,
    captured_at: str = "2026-05-24T11:59:30+00:00",
    side: str = BUY_YES,
    **extra,
) -> dict:
    ob = {
        "best_ask": ask,
        "depth_at_best_ask": depth,
        "midpoint": 0.01,
        "orderbook_captured_at": captured_at,
    }
    ob.update(extra.pop("orderbook_enrichment", {}))
    row = {"venue": venue, "side": side, "orderbook_enrichment": ob}
    row.update(extra)
    return row


def _cfg(**overrides) -> NetEdgeConfig:
    values = {"detected_at": NOW, "min_required_edge_cents": 1.0, "desired_quantity": 1.0}
    values.update(overrides)
    return NetEdgeConfig(**values)


def _trusted_pair() -> dict:
    return {
        "contract_relationship": {
            "relationship": "EQUIVALENT",
            "same_payoff": True,
            "source": "same_payoff_board_v1",
            "blocking_reasons": [],
            "same_payoff_board_evidence": {"passed": True},
        }
    }


def _exhaustive_evidence() -> dict:
    return {
        "is_exhaustive": True,
        "source": "kalshi_event_metadata",
        "venue_native": True,
        "evidence": "all outcomes included",
    }


def test_net_edge_positive_before_fees_negative_after_fees_is_blocked() -> None:
    result = calculate_manual_net_edge(
        legs=[_leg(ask=0.49), _leg(ask=0.49)],
        gross_payout_cents=100.0,
        config=_cfg(),
        fee_models={"kalshi": FlatFeeModel(0.02)},
    )

    assert result["gross_cost_cents"] == 98.0
    assert result["taker_fees_cents"] == 4.0
    assert result["conservative_net_edge_cents"] == -2.0
    assert result["status"] == "BLOCKED"
    assert "conservative_net_edge_below_minimum" in result["blockers"]
    assert result["safety"]["paper_candidate_emitted"] is False


def test_apparent_edge_with_insufficient_depth_is_blocked() -> None:
    result = calculate_manual_net_edge(
        legs=[_leg(ask=0.40, depth=0.5), _leg(ask=0.40, depth=5.0)],
        config=_cfg(desired_quantity=1.0),
        fee_models={"kalshi": FlatFeeModel(0.0)},
    )

    assert result["status"] == "BLOCKED"
    assert "leg_0_insufficient_top_of_book_depth" in result["depth_blockers"]
    assert result["max_fillable_quantity"] == 0.5


def test_l2_ladder_is_walked_conservatively_and_blocks_when_short() -> None:
    result = calculate_manual_net_edge(
        legs=[
            _leg(orderbook_enrichment={"yes_asks": [[0.40, 0.4], [0.45, 0.4]]}),
            _leg(ask=0.20, depth=5.0),
        ],
        config=_cfg(desired_quantity=1.0),
        fee_models={"kalshi": FlatFeeModel(0.0)},
    )

    assert result["legs"][0]["used_l2_ladder"] is True
    assert "leg_0_insufficient_l2_ask_depth" in result["depth_blockers"]
    assert result["max_fillable_quantity"] == 0.8


def test_stale_quotes_are_blocked() -> None:
    result = calculate_manual_net_edge(
        legs=[_leg(ask=0.30, captured_at="2026-05-24T10:00:00+00:00"), _leg(ask=0.30)],
        config=_cfg(max_quote_age_seconds=60.0),
        fee_models={"kalshi": FlatFeeModel(0.0)},
    )

    assert result["status"] == "BLOCKED"
    assert "leg_0_stale_quote" in result["freshness_blockers"]


def test_midpoint_is_never_used() -> None:
    result = calculate_manual_net_edge(
        legs=[_leg(ask=0.90, orderbook_enrichment={"midpoint": 0.10}), _leg(ask=0.05)],
        config=_cfg(),
        fee_models={"kalshi": FlatFeeModel(0.0)},
    )

    assert result["gross_cost_cents"] == 95.0
    assert result["legs"][0]["average_price"] == 0.90
    assert result["safety"]["uses_midpoint"] is False
    assert all(leg["uses_midpoint"] is False for leg in result["legs"])


def test_unknown_fee_model_fails_closed_or_uses_explicit_conservative_warning() -> None:
    blocked = calculate_manual_net_edge(
        legs=[_leg(venue="unknown", ask=0.30)],
        config=_cfg(),
        fee_models={},
    )
    warned = calculate_manual_net_edge(
        legs=[_leg(venue="unknown", ask=0.30)],
        config=_cfg(fail_on_unknown_fee_model=False, conservative_unknown_fee_cents_per_leg=3.0),
        fee_models={},
    )

    assert "unknown_fee_model:unknown" in blocked["blockers"]
    assert blocked["status"] == "BLOCKED"
    assert warned["warnings"] == ["unknown_fee_model_conservative_default:unknown"]
    assert warned["taker_fees_cents"] == 3.0
    assert warned["fee_model_names"]["unknown"] == "ConservativeUnknownFee"


def test_exact_pair_without_trusted_relationship_cannot_be_evaluated_as_exact() -> None:
    result = calculate_exact_group_net_edge(
        pair_or_group={"contract_relationship": {"relationship": "NEAR_EQUIVALENT", "same_payoff": False}},
        legs=[_leg(ask=0.30), _leg(ask=0.30)],
        config=_cfg(),
        fee_models={"kalshi": FlatFeeModel(0.0)},
    )

    assert result["status"] == "BLOCKED"
    assert "relationship_not_equivalent" in result["blockers"]
    assert "relationship_same_payoff_not_true" in result["blockers"]
    assert result["evidence"]["trusted_exact_same_payoff"] is False


def test_trusted_exact_pair_can_compute_review_net_edge_without_emitting_candidate() -> None:
    result = calculate_exact_group_net_edge(
        pair_or_group=_trusted_pair(),
        legs=[_leg(ask=0.30), _leg(ask=0.30)],
        config=_cfg(),
        fee_models={"kalshi": FlatFeeModel(0.0)},
    )

    assert result["status"] == "NET_EDGE_REVIEW"
    assert result["conservative_net_edge_cents"] == 40.0
    assert result["evidence"]["trusted_exact_same_payoff"] is True
    assert result["safety"]["paper_candidate_emitted"] is False


def test_structural_basket_without_exhaustive_evidence_cannot_be_evaluated() -> None:
    result = calculate_structural_basket_net_edge(
        exhaustive_evidence=None,
        legs=[_leg(ask=0.30), _leg(ask=0.30)],
        config=_cfg(),
        fee_models={"kalshi": FlatFeeModel(0.0)},
    )

    assert result["status"] == "BLOCKED"
    assert "missing_exhaustive_evidence" in result["blockers"]
    assert result["evidence"]["trusted_exhaustive_basket"] is False


def test_unknown_exhaustive_source_is_blocked() -> None:
    result = calculate_structural_basket_net_edge(
        exhaustive_evidence={"is_exhaustive": True, "source": "manual_guess"},
        legs=[_leg(ask=0.30), _leg(ask=0.30)],
        config=_cfg(),
        fee_models={"kalshi": FlatFeeModel(0.0)},
    )

    assert result["status"] == "BLOCKED"
    assert "exhaustive_evidence_source_not_trusted" in result["blockers"]
    assert result["evidence"]["trusted_exhaustive_basket"] is False


def test_reference_only_source_blocks_net_edge() -> None:
    result = calculate_structural_basket_net_edge(
        exhaustive_evidence={"is_exhaustive": True, "source": "local_manifest_v1", "trusted_local_manifest": True},
        legs=[_leg(ask=0.30, reference_only=True), _leg(ask=0.30)],
        config=_cfg(),
        fee_models={"kalshi": FlatFeeModel(0.0)},
    )

    assert result["status"] == "BLOCKED"
    assert "leg_0_reference_only_source" in result["blockers"]


def test_trusted_exhaustive_sources_still_pass_when_required_fields_present() -> None:
    native = calculate_structural_basket_net_edge(
        exhaustive_evidence={"is_exhaustive": True, "source": "kalshi_event_metadata", "venue_native": True},
        legs=[_leg(ask=0.30), _leg(ask=0.30)],
        config=_cfg(),
        fee_models={"kalshi": FlatFeeModel(0.0)},
    )
    manifest = calculate_structural_basket_net_edge(
        exhaustive_evidence={"is_exhaustive": True, "source": "local_manifest_v1", "trusted_local_manifest": True},
        legs=[_leg(ask=0.30), _leg(ask=0.30)],
        config=_cfg(),
        fee_models={"kalshi": FlatFeeModel(0.0)},
    )

    assert native["status"] == "NET_EDGE_REVIEW"
    assert manifest["status"] == "NET_EDGE_REVIEW"


def test_structural_basket_with_explicit_evidence_can_compute_review_net_edge() -> None:
    result = calculate_structural_basket_net_edge(
        exhaustive_evidence=_exhaustive_evidence(),
        legs=[_leg(ask=0.30), _leg(ask=0.30)],
        config=_cfg(slippage_budget_cents_per_leg=0.5),
        fee_models={"kalshi": FlatFeeModel(0.0)},
    )

    assert result["status"] == "NET_EDGE_REVIEW"
    assert result["slippage_budget_cents"] == 1.0
    assert result["conservative_net_edge_cents"] == 39.0
    assert result["evidence"]["trusted_exhaustive_basket"] is True
    assert '"paper_candidate_emitted": true' not in json.dumps(result)


def test_buy_no_uses_no_ask_not_yes_or_midpoint() -> None:
    result = calculate_manual_net_edge(
        legs=[
            _leg(
                side=BUY_NO,
                orderbook_enrichment={
                    "best_ask": 0.01,
                    "best_no_ask": 0.40,
                    "depth_at_best_ask": 10.0,
                    "depth_at_best_no_ask": 2.0,
                    "midpoint": 0.01,
                },
            )
        ],
        gross_payout_cents=100.0,
        config=_cfg(),
        fee_models={"kalshi": FlatFeeModel(0.0)},
    )

    assert result["gross_cost_cents"] == 40.0
    assert result["max_fillable_quantity"] == 2.0
    assert result["legs"][0]["side"] == BUY_NO
