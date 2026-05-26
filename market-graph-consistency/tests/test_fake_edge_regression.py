"""Regression fixtures: tampered graph reports must fail closed.

These tests simulate a hostile or buggy module trying to slip prohibited
vocabulary, exact-same-payoff claims, executable labels, or paper-candidate
labels through the safety envelope. Each tampered report must raise
:class:`SchemaValidationError` before it can be written.

If any test here passes silently (no error raised), the central safety
contract has regressed and a future graph artifact could be misinterpreted as
executable evidence.
"""

from __future__ import annotations

import pytest

from graph_engine.reporting.ops_status import (
    build_market_graph_ops_status_report,
    validate_market_graph_ops_status_report,
)
from graph_engine.reporting.relative_value_investigation_packets import (
    validate_graph_to_relative_value_investigation_packets_report as _validate_packets_report,
)
from graph_engine.reporting.schema_validation import (
    DIAGNOSTIC_HINT_ACTIONS,
    SchemaValidationError,
    validate_hint_diff_contract,
    validate_relative_value_hint_contract,
)
from graph_engine.reporting.stale_lag_watchlist import (
    FRESHNESS_BUCKET_FRESH,
    FRESHNESS_BUCKET_STALE,
    validate_stale_lag_watchlist_report,
)


def _baseline_hint_report() -> dict:
    return {
        "diagnostic_only": True,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "hints": [
            {
                "diagnostic_only": True,
                "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
                "max_action_cap": "MANUAL_REVIEW",
                "relation_type": "SUBSET",
                "hard_bound_type": "upper_probability_bound",
            }
        ],
    }


@pytest.mark.parametrize(
    "tampered_relation_type",
    [
        # Caught by the DISALLOWED_HINT_RELATION_TYPES set.
        "EXACT_SAME_PAYOFF",
        # Caught by the PROHIBITED_REPORT_PHRASES compound substring detector.
        "PAPER_CANDIDATE",
        "POSSIBLE_ARB",
        "EXECUTABLE_ARB",
        # Caught by the PROHIBITED_REPORT_TOKENS bare-word detector ("trade").
        "TRADE",
    ],
)
def test_hint_contract_rejects_disallowed_relation_types(tampered_relation_type: str) -> None:
    report = _baseline_hint_report()
    report["hints"][0]["relation_type"] = tampered_relation_type
    with pytest.raises(SchemaValidationError):
        validate_relative_value_hint_contract(report)


def test_hint_contract_rejects_paper_candidate_nested_in_compound_field_name() -> None:
    """Substring tightness: ``graph_hint_is_paper_candidate_v2`` is still
    rejected even when nested inside a compound key.
    """

    report = _baseline_hint_report()
    report["hints"][0]["graph_hint_is_paper_candidate_v2"] = True
    with pytest.raises(SchemaValidationError):
        validate_relative_value_hint_contract(report)


def test_hint_contract_rejects_executable_arb_nested_in_compound_field_name() -> None:
    report = _baseline_hint_report()
    report["hints"][0]["is_executable_arb_after_review"] = True
    with pytest.raises(SchemaValidationError):
        validate_relative_value_hint_contract(report)


@pytest.mark.parametrize(
    "structural_relation",
    ["SUBSET", "SUPERSET", "COMPLEMENT", "MUTUALLY_EXCLUSIVE", "EXHAUSTIVE_GROUP"],
)
def test_structural_relation_cannot_claim_same_payoff_bound(structural_relation: str) -> None:
    report = _baseline_hint_report()
    report["hints"][0]["relation_type"] = structural_relation
    report["hints"][0]["hard_bound_type"] = "same_payoff_equality_if_settlement_proven"
    with pytest.raises(SchemaValidationError):
        validate_relative_value_hint_contract(report)


def test_hint_contract_rejects_paper_candidate_in_review_reason() -> None:
    report = _baseline_hint_report()
    report["hints"][0]["review_reason"] = "graph_hint_is_paper_candidate_v2"
    with pytest.raises(SchemaValidationError):
        validate_relative_value_hint_contract(report)


def test_hint_contract_rejects_executable_arb_phrase_anywhere() -> None:
    report = _baseline_hint_report()
    report["hints"][0]["why_this_is_interesting"] = "executable_arb across venues"
    with pytest.raises(SchemaValidationError):
        validate_relative_value_hint_contract(report)


def test_hint_contract_rejects_max_action_cap_outside_diagnostic_actions() -> None:
    report = _baseline_hint_report()
    report["hints"][0]["max_action_cap"] = "EXECUTE"
    with pytest.raises(SchemaValidationError):
        validate_relative_value_hint_contract(report)


def test_hint_contract_rejects_allowed_actions_drift() -> None:
    report = _baseline_hint_report()
    report["hints"][0]["allowed_actions"] = ["WATCH", "PAPER_CANDIDATE"]
    with pytest.raises(SchemaValidationError):
        validate_relative_value_hint_contract(report)


def test_hint_contract_rejects_top_level_diagnostic_only_flip() -> None:
    report = _baseline_hint_report()
    report["diagnostic_only"] = False
    with pytest.raises(SchemaValidationError):
        validate_relative_value_hint_contract(report)


def _baseline_diff_report() -> dict:
    return {
        "diagnostic_only": True,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "added_hints": [
            {
                "diagnostic_only": True,
                "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
                "max_action_cap": "WATCH",
                "relation_type": "SUBSET",
            }
        ],
    }


@pytest.mark.parametrize("tampered_relation", ["PAPER_CANDIDATE", "EXACT_SAME_PAYOFF", "EXECUTE"])
def test_hint_diff_rejects_disallowed_relation_in_added_hints(tampered_relation: str) -> None:
    report = _baseline_diff_report()
    report["added_hints"][0]["relation_type"] = tampered_relation
    with pytest.raises(SchemaValidationError):
        validate_hint_diff_contract(report)


def _baseline_packet_report() -> dict:
    from graph_engine.reporting.relative_value_investigation_packets import (
        DISALLOWED_SHORTCUTS,
        REQUIRED_EVIDENCE_BEFORE_RV_REVIEW,
    )

    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "banner": "test banner",
        "packet_count": 1,
        "summary": {
            "total_packets": 1,
            "by_allowed_next_action": {"MANUAL_REVIEW": 1},
            "by_packet_kind": {"STRUCTURAL_VIOLATION": 1},
            "by_signal_type": {"SUBSET_SUPERSET_PRICE_VIOLATION": 1},
            "high_confidence_count": 1,
            "midpoint_blocked_count": 0,
            "stale_or_missing_quote_count": 0,
        },
        "investigation_packets": [
            {
                "packet_id": "graph_rv_packet:subset",
                "packet_kind": "STRUCTURAL_VIOLATION",
                "source_signal_ids": ["signal:subset"],
                "source_constraint_ids": [],
                "source_hypothesis_ids": [],
                "markets_involved": ["fixture:subset", "fixture:superset"],
                "venues_involved": ["fixture"],
                "signal_types": ["SUBSET_SUPERSET_PRICE_VIOLATION"],
                "relationship_hypothesis_type": None,
                "relationship_hypothesis_types": [],
                "probability_constraint_type": None,
                "probability_constraint_types": [],
                "observed_gap": 0.15,
                "severity_score": 80.0,
                "confidence_tier": "HIGH",
                "priority_score": 90.0,
                "entity_ids": [],
                "persistence_status": None,
                "persistence_count": 0,
                "why_this_is_interesting": "Subset/superset violation requires manual RV review.",
                "why_review_only_yet": "Diagnostic only",
                "required_evidence_before_rv_review": list(REQUIRED_EVIDENCE_BEFORE_RV_REVIEW),
                "disallowed_shortcuts": list(DISALLOWED_SHORTCUTS),
                "allowed_next_action": "MANUAL_REVIEW",
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
                "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
                "packet_blockers": ["graph_packet_review_only"],
                "probability_inputs_used": [],
            }
        ],
    }


def test_packet_validator_rejects_paper_candidate_blocker() -> None:
    report = _baseline_packet_report()
    report["investigation_packets"][0]["packet_blockers"].append("paper_candidate_v2")
    with pytest.raises(SchemaValidationError):
        _validate_packets_report(report)


def test_packet_validator_rejects_exact_same_payoff_anywhere() -> None:
    report = _baseline_packet_report()
    report["investigation_packets"][0]["why_this_is_interesting"] = "Markets are exact_same_payoff"
    with pytest.raises(SchemaValidationError):
        _validate_packets_report(report)


def test_packet_validator_rejects_diagnostic_flip() -> None:
    report = _baseline_packet_report()
    report["investigation_packets"][0]["diagnostic_only"] = False
    with pytest.raises(SchemaValidationError):
        _validate_packets_report(report)


def test_packet_validator_rejects_affects_evaluator_gates_flip() -> None:
    report = _baseline_packet_report()
    report["affects_evaluator_gates"] = True
    with pytest.raises(SchemaValidationError):
        _validate_packets_report(report)


def test_packet_validator_rejects_unsupported_packet_kind() -> None:
    report = _baseline_packet_report()
    report["investigation_packets"][0]["packet_kind"] = "PAPER_CANDIDATE"
    with pytest.raises(SchemaValidationError):
        _validate_packets_report(report)


def test_ops_status_rejects_paper_candidate_token_in_blockers() -> None:
    report = build_market_graph_ops_status_report(
        snapshot_id="snapshot-fake",
        as_of="2026-05-25T12:00:00+00:00",
        trade_indicator_report=None,
        probability_constraints_report=None,
        payoff_state_feasibility_bridge_report=None,
        signal_persistence_report=None,
        rv_investigation_packets_report=None,
        stale_lag_watchlist_report=None,
    )
    report["blockers"].append("force_paper_candidate_now")
    with pytest.raises(SchemaValidationError):
        validate_market_graph_ops_status_report(report)


def _baseline_stale_lag_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "banner": "test banner",
        "snapshot_id": "snap-1",
        "stale_seconds": 1800,
        "related_fresh_seconds": 300,
        "probability_delta_threshold": 0.1,
        "stale_lag_watch_count": 1,
        "stale_lag_blocked_count": 0,
        "uniform_timestamps_blocked_count": 0,
        "freshness_buckets": {
            "fresh": 0,
            "maybe_stale": 0,
            "stale": 1,
            "missing_timestamp": 0,
            "uniform_timestamps_suspicious": 0,
        },
        "llm_stale_lag_cowitness_count": 0,
        "stale_lag_watchlist": [
            {
                "watchlist_id": "stale_lag:left:right",
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
                "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
                "markets_involved": ["fixture:left", "fixture:right"],
                "venues_involved": ["fixture"],
                "quote_age_seconds": 3601,
                "related_market_quote_age_seconds": 60,
                "probability_delta": 0.22,
                "deterministic_lag_evidence": True,
                "llm_stale_lag_cowitness": False,
                "blockers": [],
                "freshness_bucket": FRESHNESS_BUCKET_STALE,
                "why_review_only_yet": "Diagnostic only",
            }
        ],
    }


def test_stale_lag_validator_rejects_deterministic_row_outside_stale_bucket() -> None:
    report = _baseline_stale_lag_report()
    # Deterministic WATCH rows must be in the stale bucket; forcing the row
    # to fresh should fail closed.
    report["stale_lag_watchlist"][0]["freshness_bucket"] = FRESHNESS_BUCKET_FRESH
    report["freshness_buckets"]["stale"] = 0
    report["freshness_buckets"]["fresh"] = 1
    with pytest.raises(SchemaValidationError):
        validate_stale_lag_watchlist_report(report)


def test_stale_lag_validator_rejects_unknown_freshness_bucket() -> None:
    report = _baseline_stale_lag_report()
    report["stale_lag_watchlist"][0]["freshness_bucket"] = "executable_freshness"
    report["freshness_buckets"]["stale"] = 0
    report["freshness_buckets"]["fresh"] = 0
    with pytest.raises(SchemaValidationError):
        validate_stale_lag_watchlist_report(report)
