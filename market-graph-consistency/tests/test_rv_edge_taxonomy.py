from __future__ import annotations

import pytest

from graph_engine.relationships.rv_edge_taxonomy import (
    ACTION_BASIS_RISK_REVIEW,
    ACTION_IGNORE_LOW_CONFIDENCE,
    ACTION_MANUAL_REVIEW,
    ACTION_SOURCE_REVIEW,
    ACTION_WATCH,
    ALLOWED_EDGE_ACTIONS,
    EdgeTaxonomyError,
    REQUIRED_RV_EDGE_BLOCKERS,
    RV_RELATIONSHIP_TYPES,
    default_action_for,
    make_rv_edge,
    validate_rv_edge,
)


def _kalshi_node() -> str:
    return "kalshi:KXBTC-26MAY2207-T68200"


def _polymarket_node() -> str:
    return "polymarket:1299974"


def test_default_actions_are_diagnostic_only():
    assert default_action_for("DEADLINE_TOUCH_VS_POINT_IN_TIME") == ACTION_BASIS_RISK_REVIEW
    assert default_action_for("SAME_PAYOFF_CANDIDATE_REVIEW") == ACTION_MANUAL_REVIEW
    assert default_action_for("FAIR_VALUE_REFERENCE_ONLY") == ACTION_SOURCE_REVIEW
    assert default_action_for("TITLE_SIMILARITY_ONLY") == ACTION_IGNORE_LOW_CONFIDENCE
    assert default_action_for("SUBSET_SUPERSET") == ACTION_MANUAL_REVIEW


def test_deadline_touch_vs_pit_creates_basis_risk_review():
    edge = make_rv_edge(
        edge_id="rv-edge:test:dl-pit",
        left_market_id=_kalshi_node(),
        right_market_id=_polymarket_node(),
        left_venue="kalshi",
        right_venue="polymarket",
        relationship_type="DEADLINE_TOUCH_VS_POINT_IN_TIME",
    )
    assert edge["action"] == ACTION_BASIS_RISK_REVIEW
    assert edge["relationship_family"] == "basis_risk"
    assert edge["exact_payoff"] is False
    assert edge["can_emit_evaluator_input"] is False
    assert edge["can_create_candidate_pair"] is False
    assert "deadline_touch_not_point_in_time" in edge["blockers"]


def test_daily_direction_vs_threshold_is_basis_risk_or_manual():
    edge = make_rv_edge(
        edge_id="rv-edge:test:dir-thresh",
        left_market_id=_kalshi_node(),
        right_market_id=_polymarket_node(),
        left_venue="kalshi",
        right_venue="polymarket",
        relationship_type="DAILY_DIRECTION_VS_PRICE_THRESHOLD",
    )
    assert edge["action"] == ACTION_BASIS_RISK_REVIEW
    assert edge["exact_payoff"] is False
    for required in REQUIRED_RV_EDGE_BLOCKERS:
        assert required in edge["blockers"]


def test_pit_vs_pit_with_same_typed_keys_is_source_review_not_exact():
    edge = make_rv_edge(
        edge_id="rv-edge:test:pit-pit",
        left_market_id=_kalshi_node(),
        right_market_id="cdna:eth-2026-05-22",
        left_venue="kalshi",
        right_venue="cdna",
        relationship_type="SAME_EVENT_DIFFERENT_SOURCE_REVIEW",
    )
    assert edge["action"] in {ACTION_MANUAL_REVIEW, ACTION_SOURCE_REVIEW}
    assert edge["exact_payoff"] is False
    assert edge["relationship_family"] == "near_exact_review"


def test_reference_only_edge_cannot_become_executable():
    edge = make_rv_edge(
        edge_id="rv-edge:test:ref-only",
        left_market_id="cdna:eth-anchor",
        right_market_id="reference:fed_dot_plot",
        left_venue="cdna",
        right_venue="federalreserve",
        relationship_type="FAIR_VALUE_REFERENCE_ONLY",
    )
    assert edge["action"] == ACTION_SOURCE_REVIEW
    assert edge["can_create_candidate_pair"] is False
    assert edge["can_emit_evaluator_input"] is False
    assert "reference_only_source" in edge["blockers"]


def test_title_similarity_only_must_be_low_confidence():
    edge = make_rv_edge(
        edge_id="rv-edge:test:title",
        left_market_id=_kalshi_node(),
        right_market_id=_polymarket_node(),
        left_venue="kalshi",
        right_venue="polymarket",
        relationship_type="TITLE_SIMILARITY_ONLY",
    )
    assert edge["confidence_bucket"] == "low"
    assert edge["action"] == ACTION_IGNORE_LOW_CONFIDENCE
    assert "title_similarity_not_structural_evidence" in edge["blockers"]


def test_title_similarity_medium_confidence_rejected():
    with pytest.raises(EdgeTaxonomyError):
        make_rv_edge(
            edge_id="rv-edge:test:title-medium",
            left_market_id=_kalshi_node(),
            right_market_id=_polymarket_node(),
            left_venue="kalshi",
            right_venue="polymarket",
            relationship_type="TITLE_SIMILARITY_ONLY",
            confidence_bucket="medium",
        )


def test_unknown_relationship_type_rejected():
    with pytest.raises(EdgeTaxonomyError):
        make_rv_edge(
            edge_id="rv-edge:test:bad",
            left_market_id=_kalshi_node(),
            right_market_id=_polymarket_node(),
            left_venue="kalshi",
            right_venue="polymarket",
            relationship_type="THIS_IS_NOT_A_TYPE",
        )


def test_validate_rv_edge_requires_required_blockers():
    edge = make_rv_edge(
        edge_id="rv-edge:test:required-blockers",
        left_market_id=_kalshi_node(),
        right_market_id=_polymarket_node(),
        left_venue="kalshi",
        right_venue="polymarket",
        relationship_type="BASIS_RISK_SAME_ASSET_DIFFERENT_SOURCE",
    )
    # Tamper with the edge to drop a required blocker and ensure validate fails.
    edge["blockers"] = [b for b in edge["blockers"] if b != "not_evaluator_input"]
    with pytest.raises(EdgeTaxonomyError):
        validate_rv_edge(edge)


def test_allowed_edge_actions_set_is_stable():
    assert ALLOWED_EDGE_ACTIONS == (
        ACTION_WATCH,
        ACTION_MANUAL_REVIEW,
        ACTION_BASIS_RISK_REVIEW,
        ACTION_SOURCE_REVIEW,
        ACTION_IGNORE_LOW_CONFIDENCE,
    )


def test_relationship_types_have_known_families():
    for rt, family in RV_RELATIONSHIP_TYPES.items():
        assert family in {"near_exact_review", "basis_risk", "structural", "reference_only", "weak_signal"}
