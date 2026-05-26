from __future__ import annotations

import json
import re

import pytest

from graph_engine.reporting.relative_value_investigation_packets import (
    REQUIRED_EVIDENCE_BEFORE_RV_REVIEW,
    build_graph_to_relative_value_investigation_packets_report,
    validate_graph_to_relative_value_investigation_packets_report,
    write_graph_to_relative_value_investigation_packets_report,
)
from graph_engine.reporting.schema_validation import SchemaValidationError


def _indicator_report(*rows: dict) -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "signals": list(rows),
    }


def _signal(
    signal_id: str,
    signal_type: str,
    markets: list[str],
    *,
    severity: float = 75.0,
    confidence: str = "HIGH",
    blockers: list[str] | None = None,
    probability_inputs: list[dict] | None = None,
    market_formulas: list[dict] | None = None,
) -> dict:
    return {
        "signal_id": signal_id,
        "signal_type": signal_type,
        "markets_involved": list(markets),
        "venues_involved": sorted({market.split(":", 1)[0] for market in markets}),
        "relationship_evidence_type": "graph_edge:fixture",
        "severity_score": severity,
        "confidence_tier": confidence,
        "market_formulas": market_formulas or [],
        "probability_inputs_used": probability_inputs or [_probability_input(market) for market in markets],
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "review_blockers": blockers or ["not_evaluator_input"],
    }


def _probability_report(*rows: dict) -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "probability_constraints": list(rows),
    }


def _constraint(
    constraint_id: str,
    constraint_type: str,
    markets: list[str],
    *,
    severity: float = 70.0,
    confidence: str = "HIGH",
    gap: float = 0.12,
    blockers: list[str] | None = None,
    probability_inputs: list[dict] | None = None,
    midpoint_only: bool = False,
    stale_or_missing: bool = False,
    market_formulas: list[dict] | None = None,
) -> dict:
    return {
        "constraint_id": constraint_id,
        "constraint_type": constraint_type,
        "markets_involved": list(markets),
        "venues_involved": sorted({market.split(":", 1)[0] for market in markets}),
        "severity_score": severity,
        "confidence_tier": confidence,
        "observed_gap": gap,
        "probability_inputs": probability_inputs or [_probability_input(market) for market in markets],
        "market_formulas": market_formulas or [],
        "review_blockers": blockers or ["not_evaluator_input"],
        "midpoint_only": midpoint_only,
        "has_stale_or_missing_quote": stale_or_missing,
    }


def _llm_report(*rows: dict) -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "validated_hypotheses": list(rows),
    }


def _hypothesis(
    hypothesis_id: str,
    relationship_type: str,
    markets: list[str],
    *,
    confidence: str = "HIGH",
    deterministic_support: bool = False,
    strength_tier: str = "LOGICAL_HYPOTHESIS_ONLY",
) -> dict:
    return {
        "hypothesis_id": hypothesis_id,
        "relationship_type": relationship_type,
        "relationship_strength_tier": strength_tier,
        "source_market_ids": list(markets),
        "confidence_tier": confidence,
        "deterministic_support": deterministic_support,
        "review_blockers": ["llm_hypothesis_advisory_only"],
    }


def _probability_input(
    market_id: str,
    *,
    source: str = "yes_price",
    quote_age_seconds: int | None = 60,
    probability: float | None = 0.55,
) -> dict:
    return {
        "market_id": market_id,
        "probability": probability,
        "probability_source": source,
        "bid_bound": 0.53,
        "ask_bound": 0.57,
        "midpoint": 0.55,
        "diagnostic_midpoint_used": source == "diagnostic_midpoint",
        "non_actionable_input": source != "yes_price" or probability is None,
        "quote_age_seconds": quote_age_seconds,
    }


def _single_packet(report: dict) -> dict:
    assert report["investigation_packets"]
    return report["investigation_packets"][0]


def _btc_formula(
    market_id: str,
    source: str | None,
    *,
    threshold: float = 120000.0,
    comparator: str = ">",
    date: str = "2026-06-30",
    window: str = "2026-06-30 16:00:00Z",
) -> dict:
    return {
        "market_id": market_id,
        "family": "BTC_THRESHOLD",
        "asset": "BTC",
        "source": source,
        "date": date,
        "window": window,
        "threshold": threshold,
        "comparator": comparator,
        "unit": "USD",
    }


def _ontology_report(*rows: dict) -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "ontology_rows": list(rows),
        "summary": {},
    }


def test_deterministic_structural_signal_produces_packet_with_required_evidence() -> None:
    markets = ["fixture:btc_over_120k", "fixture:btc_over_100k"]
    report = build_graph_to_relative_value_investigation_packets_report(
        trade_indicator_report=_indicator_report(
            _signal("signal:subset", "SUBSET_SUPERSET_PRICE_VIOLATION", markets)
        ),
        probability_constraints_report=_probability_report(
            _constraint("constraint:subset", "subset_superset", markets, gap=0.17)
        ),
    )
    packet = _single_packet(report)

    assert packet["packet_kind"] == "STRUCTURAL_VIOLATION"
    assert packet["diagnostic_only"] is True
    assert packet["affects_evaluator_gates"] is False
    assert packet["source_signal_ids"] == ["signal:subset"]
    assert packet["source_constraint_ids"] == ["constraint:subset"]
    assert packet["required_evidence_before_rv_review"] == REQUIRED_EVIDENCE_BEFORE_RV_REVIEW
    assert "payoff_relationship_proof" in packet["required_evidence_before_rv_review"]
    assert packet["observed_gap"] == pytest.approx(0.17)
    assert packet["priority_score"] > 50
    validate_graph_to_relative_value_investigation_packets_report(report)


def test_similarity_only_signal_is_low_priority() -> None:
    markets = ["fixture:a", "fixture:b"]
    report = build_graph_to_relative_value_investigation_packets_report(
        trade_indicator_report=_indicator_report(
            _signal(
                "signal:similarity",
                "SIMILARITY_ONLY_RESEARCH",
                markets,
                severity=95.0,
                confidence="LOW",
            )
        )
    )
    packet = _single_packet(report)

    assert packet["packet_kind"] == "SIMILARITY_RESEARCH"
    assert packet["priority_score"] <= 35
    assert packet["confidence_tier"] == "LOW"
    assert packet["allowed_next_action"] == "IGNORE_LOW_CONFIDENCE"
    assert "title_similarity_not_structural_evidence" in packet["packet_blockers"]


def test_llm_only_exact_hypothesis_cannot_create_high_priority_exact_packet() -> None:
    markets = ["fixture:a", "fixture:b"]
    report = build_graph_to_relative_value_investigation_packets_report(
        llm_hypotheses_report=_llm_report(
            _hypothesis("hyp:exact", "EXACT_EQUALITY_HYPOTHESIS", markets)
        )
    )
    packet = _single_packet(report)

    assert packet["packet_kind"] == "LLM_ONLY"
    assert packet["relationship_hypothesis_type"] == "EXACT_EQUALITY_HYPOTHESIS"
    assert packet["source_signal_ids"] == []
    assert packet["source_constraint_ids"] == []
    assert packet["confidence_tier"] == "MEDIUM"
    assert packet["priority_score"] <= 45
    assert "llm_assertion_not_deterministic_evidence" in packet["packet_blockers"]
    assert "structural_hypothesis_requires_deterministic_backing" in packet["packet_blockers"]


def test_midpoint_only_probability_gap_adds_blocker() -> None:
    markets = ["fixture:subset", "fixture:superset"]
    inputs = [_probability_input(market, source="diagnostic_midpoint") for market in markets]
    report = build_graph_to_relative_value_investigation_packets_report(
        probability_constraints_report=_probability_report(
            _constraint(
                "constraint:midpoint",
                "subset_superset",
                markets,
                probability_inputs=inputs,
                midpoint_only=True,
            )
        )
    )
    packet = _single_packet(report)

    assert "midpoint_only_gap" in packet["packet_blockers"]
    assert "midpoint_input_not_rv_ready" in packet["packet_blockers"]
    assert packet["allowed_next_action"] == "FETCH_OR_ENRICH_ORDERBOOKS"
    assert packet["confidence_tier"] == "MEDIUM"


def test_stale_or_missing_quote_adds_blocker() -> None:
    markets = ["fixture:subset", "fixture:superset"]
    inputs = [
        _probability_input(markets[0], quote_age_seconds=None),
        _probability_input(markets[1], probability=None),
    ]
    report = build_graph_to_relative_value_investigation_packets_report(
        probability_constraints_report=_probability_report(
            _constraint(
                "constraint:stale",
                "subset_superset",
                markets,
                probability_inputs=inputs,
                blockers=["missing_quote_timestamp", "missing_probability_input"],
                stale_or_missing=True,
            )
        )
    )
    packet = _single_packet(report)

    assert "stale_or_missing_quote" in packet["packet_blockers"]
    assert packet["allowed_next_action"] == "FETCH_OR_ENRICH_ORDERBOOKS"
    assert packet["confidence_tier"] == "MEDIUM"


def test_reference_only_packet_routes_to_ignore_low_confidence() -> None:
    markets = ["kalshi:reference_context_a", "kalshi:reference_context_b"]
    report = build_graph_to_relative_value_investigation_packets_report(
        trade_indicator_report=_indicator_report(
            _signal(
                "signal:reference_only",
                "SUBSET_SUPERSET_PRICE_VIOLATION",
                markets,
                severity=90.0,
                blockers=["reference_only_source"],
            )
        )
    )
    packet = _single_packet(report)

    assert packet["packet_kind"] == "FAIR_VALUE_REFERENCE_ONLY"
    assert packet["allowed_next_action"] == "IGNORE_LOW_CONFIDENCE"
    assert "reference_only_source" in packet["packet_blockers"]
    assert packet["priority_score"] <= 35
    assert packet["diagnostic_only"] is True
    assert packet["affects_evaluator_gates"] is False
    validate_graph_to_relative_value_investigation_packets_report(report)


def test_reference_only_packet_blocks_basis_risk_kind() -> None:
    markets = ["the_odds_api:btc_index_a_120k", "the_odds_api:btc_index_b_120k"]
    formulas = [
        _btc_formula(markets[0], "fixture_btc_index_a"),
        _btc_formula(markets[1], "fixture_btc_index_b"),
    ]
    report = build_graph_to_relative_value_investigation_packets_report(
        trade_indicator_report=_indicator_report(
            _signal(
                "signal:reference_basis",
                "EXACT_RELATIONSHIP_WATCH",
                markets,
                severity=92.0,
                market_formulas=formulas,
            )
        )
    )
    packet = _single_packet(report)

    assert packet["packet_kind"] == "FAIR_VALUE_REFERENCE_ONLY"
    assert packet["allowed_next_action"] == "IGNORE_LOW_CONFIDENCE"
    assert "reference_only_source" in packet["packet_blockers"]
    assert "requires_basis_source_distinction" not in packet["packet_blockers"]
    assert packet["priority_score"] <= 35
    validate_graph_to_relative_value_investigation_packets_report(report)


def test_packet_contains_no_disallowed_permission_values() -> None:
    report = build_graph_to_relative_value_investigation_packets_report(
        trade_indicator_report=_indicator_report(
            _signal("signal:subset", "SUBSET_SUPERSET_PRICE_VIOLATION", ["fixture:a", "fixture:b"])
        )
    )

    assert report["diagnostic_only"] is True
    assert report["affects_evaluator_gates"] is False
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    text = json.dumps(report)
    for token in [
        "PAPER" + "_CANDIDATE",
        "TR" + "ADE",
        "EXE" + "CUTE",
        "ORD" + "ER",
        "B" + "UY",
        "S" + "ELL",
    ]:
        assert re.search(rf"\b{token}\b", text, flags=re.IGNORECASE) is None

    report["investigation_packets"][0]["allowed_next_action"] = "B" + "UY"
    with pytest.raises(SchemaValidationError):
        validate_graph_to_relative_value_investigation_packets_report(report)


def test_btc_same_source_window_does_not_create_basis_risk_packet() -> None:
    markets = ["fixture:btc_source_a", "fixture:btc_source_b"]
    formulas = [_btc_formula(market, "fixture_btc_index") for market in markets]

    report = build_graph_to_relative_value_investigation_packets_report(
        trade_indicator_report=_indicator_report(
            _signal(
                "signal:btc_same_source",
                "EXACT_RELATIONSHIP_WATCH",
                markets,
                market_formulas=formulas,
            )
        )
    )
    packet = _single_packet(report)

    assert packet["packet_kind"] == "STRUCTURAL_VIOLATION"
    assert "requires_basis_source_distinction" not in packet["packet_blockers"]
    validate_graph_to_relative_value_investigation_packets_report(report)


def test_btc_different_source_same_threshold_window_creates_basis_risk_packet() -> None:
    markets = ["fixture:btc_index_a_120k", "fixture:btc_index_b_120k"]
    formulas = [
        _btc_formula(markets[0], "fixture_btc_index_a"),
        _btc_formula(markets[1], "fixture_btc_index_b"),
    ]

    report = build_graph_to_relative_value_investigation_packets_report(
        trade_indicator_report=_indicator_report(
            _signal(
                "signal:btc_basis",
                "EXACT_RELATIONSHIP_WATCH",
                markets,
                severity=82.0,
                market_formulas=formulas,
            )
        )
    )
    packet = _single_packet(report)

    assert packet["packet_kind"] == "BTC_BASIS_RISK_REVIEW"
    assert packet["allowed_next_action"] == "MANUAL_REVIEW"
    assert "requires_basis_source_distinction" in packet["packet_blockers"]
    assert packet["priority_score"] <= 45
    assert packet["diagnostic_only"] is True
    assert packet["affects_evaluator_gates"] is False
    validate_graph_to_relative_value_investigation_packets_report(report)


def test_unknown_btc_source_does_not_create_basis_risk_packet() -> None:
    markets = ["fixture:btc_known_120k", "fixture:btc_unknown_120k"]
    formulas = [
        _btc_formula(markets[0], "fixture_btc_index"),
        _btc_formula(markets[1], "unknown"),
    ]

    report = build_graph_to_relative_value_investigation_packets_report(
        trade_indicator_report=_indicator_report(
            _signal(
                "signal:btc_unknown_source",
                "EXACT_RELATIONSHIP_WATCH",
                markets,
                market_formulas=formulas,
            )
        )
    )
    packet = _single_packet(report)

    assert packet["packet_kind"] == "STRUCTURAL_VIOLATION"
    assert "requires_basis_source_distinction" not in packet["packet_blockers"]
    validate_graph_to_relative_value_investigation_packets_report(report)


def test_generic_threshold_formula_does_not_create_basis_risk_packet() -> None:
    markets = ["fixture:openai_500b", "fixture:openai_1t"]
    formulas = [
        {
            "market_id": markets[0],
            "family": "GENERIC_THRESHOLD",
            "asset": "openai_valuation_usd",
            "source": "business_filing",
            "date": "2027-12-31",
            "window": "2027-12-31",
            "threshold": 500_000_000_000.0,
            "comparator": ">=",
            "unit": "USD",
        },
        {
            "market_id": markets[1],
            "family": "GENERIC_THRESHOLD",
            "asset": "openai_valuation_usd",
            "source": "business_press",
            "date": "2027-12-31",
            "window": "2027-12-31",
            "threshold": 1_000_000_000_000.0,
            "comparator": ">=",
            "unit": "USD",
        },
    ]

    report = build_graph_to_relative_value_investigation_packets_report(
        trade_indicator_report=_indicator_report(
            _signal("signal:generic_threshold", "EXACT_RELATIONSHIP_WATCH", markets, market_formulas=formulas)
        )
    )
    packet = _single_packet(report)

    assert packet["packet_kind"] == "STRUCTURAL_VIOLATION"
    assert "requires_basis_source_distinction" not in packet["packet_blockers"]
    validate_graph_to_relative_value_investigation_packets_report(report)


def test_entity_ids_populate_from_event_entity_ontology() -> None:
    markets = ["fixture:btc_index_a_120k", "fixture:btc_index_b_120k"]
    formulas = [
        _btc_formula(markets[0], "fixture_btc_index_a"),
        _btc_formula(markets[1], "fixture_btc_index_b"),
    ]

    report = build_graph_to_relative_value_investigation_packets_report(
        trade_indicator_report=_indicator_report(
            _signal("signal:btc_basis", "EXACT_RELATIONSHIP_WATCH", markets, market_formulas=formulas)
        ),
        event_entity_ontology_report=_ontology_report(
            {
                "entity_id": "entity:crypto_asset:btc",
                "source_market_ids": [markets[0], markets[1]],
            },
            {
                "entity_id": "entity:crypto_threshold_event:btc_120k_june",
                "source_market_ids": [markets[1]],
            },
        ),
    )
    packet = _single_packet(report)

    assert packet["entity_ids"] == [
        "entity:crypto_asset:btc",
        "entity:crypto_threshold_event:btc_120k_june",
    ]
    validate_graph_to_relative_value_investigation_packets_report(report)


def test_packet_report_omits_restricted_action_vocabulary() -> None:
    markets = ["fixture:btc_index_a_120k", "fixture:btc_index_b_120k"]
    formulas = [
        _btc_formula(markets[0], "fixture_btc_index_a"),
        _btc_formula(markets[1], "fixture_btc_index_b"),
    ]
    report = build_graph_to_relative_value_investigation_packets_report(
        trade_indicator_report=_indicator_report(
            _signal("signal:btc_basis", "EXACT_RELATIONSHIP_WATCH", markets, market_formulas=formulas)
        )
    )
    rendered = json.dumps(report, sort_keys=True)

    for token in [
        "PAPER" + "_CANDIDATE",
        "GUARANTEED" + "_PNL",
        "EXACT" + "_ARBITRAGE",
        "ORD" + "ER",
        "TR" + "ADE",
    ]:
        assert token not in rendered


def test_report_validates_before_writing(tmp_path) -> None:
    trade_path = tmp_path / "market_graph_trade_indicators.json"
    probability_path = tmp_path / "market_graph_probability_constraints.json"
    output = tmp_path / "graph_to_relative_value_investigation_packets.json"
    markdown = tmp_path / "graph_to_relative_value_investigation_packets.md"
    markets = ["fixture:btc_over_120k", "fixture:btc_over_100k"]
    trade_path.write_text(
        json.dumps(
            _indicator_report(_signal("signal:subset", "SUBSET_SUPERSET_PRICE_VIOLATION", markets))
        ),
        encoding="utf-8",
    )
    probability_path.write_text(
        json.dumps(_probability_report(_constraint("constraint:subset", "subset_superset", markets))),
        encoding="utf-8",
    )

    report = write_graph_to_relative_value_investigation_packets_report(
        trade_indicator_path=trade_path,
        probability_constraints_path=probability_path,
        json_output=output,
        markdown_output=markdown,
    )

    assert output.exists()
    assert markdown.exists()
    assert json.loads(output.read_text(encoding="utf-8")) == report
    validate_graph_to_relative_value_investigation_packets_report(report)
