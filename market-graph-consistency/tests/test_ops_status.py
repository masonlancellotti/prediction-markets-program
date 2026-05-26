from __future__ import annotations

import json

import pytest

from graph_engine.reporting.ops_status import (
    build_market_graph_ops_status_report,
    validate_market_graph_ops_status_report,
    write_market_graph_ops_status_report,
)
from graph_engine.reporting.schema_validation import SchemaValidationError


def _trade_indicators_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "snapshot_id": "snapshot-1",
        "signals": [
            {
                "signal_id": "signal:subset",
                "signal_type": "SUBSET_SUPERSET_PRICE_VIOLATION",
                "markets_involved": ["fixture:subset", "fixture:superset"],
                "confidence_tier": "HIGH",
                "severity_score": 88.0,
                "review_blockers": ["not_evaluator_input"],
            },
            {
                "signal_id": "signal:midpoint",
                "signal_type": "THRESHOLD_LADDER_INVERSION",
                "markets_involved": ["fixture:a", "fixture:b"],
                "confidence_tier": "MEDIUM",
                "severity_score": 55.0,
                "review_blockers": ["diagnostic_midpoint_not_actionable"],
            },
        ],
    }


def _probability_constraints_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "snapshot_id": "snapshot-1",
        "probability_constraints": [
            {
                "constraint_id": "constraint:subset",
                "constraint_type": "subset_superset",
                "markets_involved": ["fixture:subset", "fixture:superset"],
                "confidence_tier": "HIGH",
                "severity_score": 82.0,
                "observed_gap": 0.14,
                "violated": True,
                "midpoint_only": False,
                "has_stale_or_missing_quote": False,
                "review_blockers": ["not_evaluator_input"],
            },
            {
                "constraint_id": "constraint:stale",
                "constraint_type": "complement_pair",
                "markets_involved": ["fixture:yes", "fixture:no"],
                "confidence_tier": "LOW",
                "severity_score": 31.0,
                "observed_gap": 0.05,
                "violated": True,
                "midpoint_only": False,
                "has_stale_or_missing_quote": True,
                "review_blockers": ["stale_quote"],
            },
        ],
    }


def _payoff_bridge_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "snapshot_id": "snapshot-1",
        "payoff_state_feasibility_bridge": [
            _bridge_row("bridge:feasible", "FEASIBLE", "family:feasible"),
            _bridge_row("bridge:gap-small", "INFEASIBLE_DIAGNOSTIC", "family:small", gap=0.11),
            _bridge_row("bridge:gap-large", "INFEASIBLE_DIAGNOSTIC", "family:large", gap=0.23),
            _bridge_row("bridge:missing-matrix", "BLOCKED_MISSING_PAYOFF_MATRIX", "family:missing-matrix"),
            _bridge_row("bridge:missing-probability", "BLOCKED_MISSING_PROBABILITY_INPUTS", "family:missing-probability"),
            _bridge_row("bridge:missing-family", "BLOCKED_MISSING_STATE_FAMILY", None),
            _bridge_row("bridge:unsupported", "BLOCKED_UNSUPPORTED_CONSTRAINT_TYPE", "family:unsupported"),
        ],
    }


def _bridge_row(
    bridge_id: str,
    status: str,
    state_family_id: str | None,
    *,
    gap: float = 0.0,
) -> dict:
    markets = [f"fixture:{bridge_id}:left", f"fixture:{bridge_id}:right"]
    per_contract_repair = (
        {markets[0]: round(gap / 2, 6), markets[1]: round(gap, 6)}
        if status == "INFEASIBLE_DIAGNOSTIC"
        else {}
    )
    return {
        "bridge_id": bridge_id,
        "state_family_id": state_family_id,
        "markets_involved": markets,
        "constraint_types_represented": ["exhaustive_partition"],
        "feasibility_status": status,
        "infeasibility_gap": gap,
        "per_contract_repair": per_contract_repair,
        "review_blockers": ["requires_settlement_source_review", "no_" + "execution_permission"],
        "why_review_only_yet": "Bridge output is review-only; settlement review remains required.",
    }


def _many_infeasible_bridge_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "snapshot_id": "snapshot-1",
        "payoff_state_feasibility_bridge": [
            _bridge_row(f"bridge:gap-{index}", "INFEASIBLE_DIAGNOSTIC", f"family:{index}", gap=gap)
            for index, gap in enumerate([0.01, 0.44, 0.12, 0.51, 0.07, 0.33], start=1)
        ],
    }


def _signal_persistence_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "summary": {
            "new_count": 1,
            "worsened_count": 1,
            "top_persistent_high_confidence_signals": [
                {
                    "signal_key": "signal-key-subset",
                    "signal_type": "SUBSET_SUPERSET_PRICE_VIOLATION",
                    "markets_involved": ["fixture:subset", "fixture:superset"],
                    "current_severity": 88.0,
                    "previous_severity": 88.0,
                    "severity_delta": 0.0,
                    "current_confidence": "HIGH",
                    "persistence_status": "PERSISTENT_SIGNAL",
                }
            ],
            "top_worsening_signals": [
                {
                    "signal_key": "signal-key-worse",
                    "signal_type": "COMPLEMENT_PRICE_DIVERGENCE",
                    "markets_involved": ["fixture:yes", "fixture:no"],
                    "current_severity": 70.0,
                    "previous_severity": 50.0,
                    "severity_delta": 20.0,
                    "current_confidence": "MEDIUM",
                    "persistence_status": "WORSENED_SIGNAL",
                }
            ],
        },
        "signal_persistence_rows": [],
    }


def _rv_packets_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "investigation_packets": [
            {
                "packet_id": "packet:subset",
                "signal_types": ["SUBSET_SUPERSET_PRICE_VIOLATION"],
                "probability_constraint_types": ["subset_superset"],
                "markets_involved": ["fixture:subset", "fixture:superset"],
                "confidence_tier": "HIGH",
                "priority_score": 91.0,
                "packet_blockers": ["graph_packet_review_only"],
                "allowed_next_action": "MANUAL_REVIEW",
            }
        ],
    }


def _stale_lag_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "stale_lag_watch_count": 1,
        "stale_lag_blocked_count": 2,
        "uniform_timestamps_blocked_count": 1,
        "freshness_buckets": {
            "fresh": 0,
            "maybe_stale": 0,
            "stale": 1,
            "missing_timestamp": 1,
            "uniform_timestamps_suspicious": 1,
        },
        "stale_lag_watchlist": [
            {
                "watchlist_id": "stale_lag:watch",
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
                "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
                "markets_involved": ["fixture:stale", "fixture:fresh"],
                "venues_involved": ["fixture"],
                "quote_age_seconds": 3601,
                "related_market_quote_age_seconds": 60,
                "probability_delta": 0.22,
                "deterministic_lag_evidence": True,
                "llm_stale_lag_cowitness": False,
                "blockers": [],
                "why_review_only_yet": "diagnostic only",
            },
            {
                "watchlist_id": "stale_lag:blocked",
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
                "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
                "markets_involved": ["fixture:blocked", "fixture:fresh"],
                "venues_involved": ["fixture"],
                "quote_age_seconds": None,
                "related_market_quote_age_seconds": 60,
                "probability_delta": 0.22,
                "deterministic_lag_evidence": False,
                "llm_stale_lag_cowitness": False,
                "blockers": ["missing_quote_timestamp"],
                "why_review_only_yet": "diagnostic only",
            },
            {
                "watchlist_id": "stale_lag:uniform",
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
                "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
                "markets_involved": ["fixture:uniform-left", "fixture:uniform-right"],
                "venues_involved": ["fixture"],
                "quote_age_seconds": 120,
                "related_market_quote_age_seconds": 60,
                "probability_delta": 0.22,
                "deterministic_lag_evidence": False,
                "llm_stale_lag_cowitness": False,
                "blockers": [
                    "timestamp_skew_below_threshold",
                    "uniform_fixture_or_snapshot_timestamps_no_skew_detectable",
                ],
                "why_review_only_yet": "diagnostic only",
            },
        ],
    }


def test_ops_status_summary_and_top_sections() -> None:
    report = build_market_graph_ops_status_report(
        snapshot_id="snapshot-1",
        as_of="2026-05-25T12:00:00+00:00",
        trade_indicator_report=_trade_indicators_report(),
        probability_constraints_report=_probability_constraints_report(),
        payoff_state_feasibility_bridge_report=_payoff_bridge_report(),
        signal_persistence_report=_signal_persistence_report(),
        rv_investigation_packets_report=_rv_packets_report(),
        stale_lag_watchlist_report=_stale_lag_report(),
    )

    assert report["diagnostic_only"] is True
    assert report["affects_evaluator_gates"] is False
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert report["summary"]["total_signals"] == 2
    assert report["summary"]["high_confidence_signals"] == 1
    assert report["summary"]["new_signals_since_last_run"] == 1
    assert report["summary"]["worsened_signals"] == 1
    assert report["summary"]["persistent_high_confidence_signals"] == 1
    assert report["summary"]["midpoint_blocked_signals"] == 1
    assert report["summary"]["stale_lag_watch_count"] == 1
    assert report["summary"]["stale_lag_blocked_count"] == 2
    assert report["summary"]["uniform_timestamp_stale_blocked_count"] == 1
    # stale_lag rows live in their own metric so the stale signal/constraint
    # count must not double count them.
    assert report["summary"]["stale_blocked_signal_constraint_packet_rows"] == 1
    assert report["summary"]["rv_handoff_packets_ready"] == 1
    assert report["summary"]["platform_expansion_gap_rows"] == 0
    assert report["summary"]["ontology_entity_count"] == 0
    assert report["summary"]["yes_price_equal_to_midpoint_rows"] == 0
    assert report["summary"]["bridge_row_count"] == 7
    assert report["summary"]["bridge_feasible_count"] == 1
    assert report["summary"]["bridge_infeasible_diagnostic_count"] == 2
    assert report["summary"]["bridge_blocked_missing_payoff_matrix_count"] == 1
    assert report["summary"]["bridge_blocked_missing_probability_inputs_count"] == 1
    assert report["summary"]["bridge_blocked_missing_state_family_count"] == 1
    assert report["summary"]["bridge_blocked_unsupported_constraint_type_count"] == 1
    assert report["top_probability_constraints"][0]["row_id"] == "constraint:subset"
    assert report["top_rv_handoff_packets"][0]["row_id"] == "packet:subset"
    assert report["top_infeasibility_diagnostic"][0]["row_id"] == "bridge:gap-large"
    assert report["top_infeasibility_diagnostic"][0]["score"] == 23.0
    assert report["top_infeasibility_diagnostic"][0]["state_family_id"] == "family:large"
    assert report["top_infeasibility_diagnostic"][0]["feasibility_status"] == "INFEASIBLE_DIAGNOSTIC"
    assert report["top_infeasibility_diagnostic"][0]["blockers"] == [
        "requires_settlement_source_review",
        "no_execution_permission",
    ]
    assert report["top_infeasibility_diagnostic"][0]["worst_contract_id"] == "fixture:bridge:gap-large:right"
    assert report["top_infeasibility_diagnostic"][0]["worst_contract_repair_gap"] == 0.23
    assert "REVIEW_PERSISTENT_HIGH_CONFIDENCE" in report["next_recommended_actions"]
    assert "REVIEW_WORSENING_DIAGNOSTICS" in report["next_recommended_actions"]
    assert "REVIEW_TOP_INFEASIBILITY" in report["next_recommended_actions"]
    validate_market_graph_ops_status_report(report)


def test_ops_status_bridge_count_invariants() -> None:
    report = build_market_graph_ops_status_report(
        snapshot_id="snapshot-1",
        as_of="2026-05-25T12:00:00+00:00",
        trade_indicator_report=_trade_indicators_report(),
        probability_constraints_report=_probability_constraints_report(),
        payoff_state_feasibility_bridge_report=_payoff_bridge_report(),
        signal_persistence_report=_signal_persistence_report(),
        rv_investigation_packets_report=_rv_packets_report(),
        stale_lag_watchlist_report=_stale_lag_report(),
    )
    summary = report["summary"]
    bridge_status_total = sum(
        summary[key]
        for key in [
            "bridge_feasible_count",
            "bridge_infeasible_diagnostic_count",
            "bridge_blocked_missing_payoff_matrix_count",
            "bridge_blocked_missing_probability_inputs_count",
            "bridge_blocked_missing_state_family_count",
            "bridge_blocked_unsupported_constraint_type_count",
        ]
    )

    assert summary["bridge_row_count"] == bridge_status_total
    assert summary["bridge_infeasible_diagnostic_count"] <= 5
    assert summary["bridge_infeasible_diagnostic_count"] == len(report["top_infeasibility_diagnostic"])


def test_missing_input_reports_produce_blockers_without_crashing(tmp_path) -> None:
    output = tmp_path / "market_graph_ops_status.json"
    markdown = tmp_path / "market_graph_ops_status.md"

    report = write_market_graph_ops_status_report(
        json_output=output,
        markdown_output=markdown,
        trade_indicators_path=tmp_path / "missing_trade_indicators.json",
        probability_constraints_path=tmp_path / "missing_probability_constraints.json",
        payoff_state_feasibility_bridge_path=tmp_path / "missing_bridge.json",
        signal_persistence_path=tmp_path / "missing_persistence.json",
        rv_investigation_packets_path=tmp_path / "missing_packets.json",
        stale_lag_watchlist_path=tmp_path / "missing_stale_lag.json",
    )

    assert output.exists()
    assert markdown.exists()
    assert report["summary"]["total_signals"] == 0
    assert report["summary"]["uniform_timestamp_stale_blocked_count"] == 0
    assert len([blocker for blocker in report["blockers"] if blocker.startswith("missing_input_report:")]) == 6
    assert "REBUILD_MISSING_INPUT_REPORTS" in report["next_recommended_actions"]
    validate_market_graph_ops_status_report(json.loads(output.read_text(encoding="utf-8")))


def test_ops_status_report_validates_before_writing(tmp_path) -> None:
    trade_path = tmp_path / "market_graph_trade_indicators.json"
    probability_path = tmp_path / "market_graph_probability_constraints.json"
    bridge_path = tmp_path / "market_graph_payoff_state_feasibility_bridge.json"
    persistence_path = tmp_path / "market_graph_signal_persistence.json"
    packets_path = tmp_path / "graph_to_relative_value_investigation_packets.json"
    stale_lag_path = tmp_path / "market_graph_stale_lag_watchlist.json"
    output = tmp_path / "market_graph_ops_status.json"
    markdown = tmp_path / "market_graph_ops_status.md"

    trade_path.write_text(json.dumps(_trade_indicators_report()), encoding="utf-8")
    probability_path.write_text(json.dumps(_probability_constraints_report()), encoding="utf-8")
    bridge_path.write_text(json.dumps(_payoff_bridge_report()), encoding="utf-8")
    persistence_path.write_text(json.dumps(_signal_persistence_report()), encoding="utf-8")
    packets_path.write_text(json.dumps(_rv_packets_report()), encoding="utf-8")
    stale_lag_path.write_text(json.dumps(_stale_lag_report()), encoding="utf-8")

    report = write_market_graph_ops_status_report(
        json_output=output,
        markdown_output=markdown,
        trade_indicators_path=trade_path,
        probability_constraints_path=probability_path,
        payoff_state_feasibility_bridge_path=bridge_path,
        signal_persistence_path=persistence_path,
        rv_investigation_packets_path=packets_path,
        stale_lag_watchlist_path=stale_lag_path,
    )

    assert json.loads(output.read_text(encoding="utf-8")) == report
    rendered = markdown.read_text(encoding="utf-8")
    assert "# Market Graph Ops Status" in rendered
    assert "Uniform-timestamp stale/lag blocked rows" in rendered
    assert "## Top Infeasibility (Diagnostic)" in rendered
    validate_market_graph_ops_status_report(report)


def test_ops_status_limits_top_infeasibility_by_gap_descending() -> None:
    report = build_market_graph_ops_status_report(
        snapshot_id="snapshot-1",
        as_of="2026-05-25T12:00:00+00:00",
        trade_indicator_report=_trade_indicators_report(),
        probability_constraints_report=_probability_constraints_report(),
        payoff_state_feasibility_bridge_report=_many_infeasible_bridge_report(),
        signal_persistence_report=_signal_persistence_report(),
        rv_investigation_packets_report=_rv_packets_report(),
        stale_lag_watchlist_report=_stale_lag_report(),
    )

    top_rows = report["top_infeasibility_diagnostic"]
    assert len(top_rows) == 5
    assert [row["row_id"] for row in top_rows] == [
        "bridge:gap-4",
        "bridge:gap-2",
        "bridge:gap-6",
        "bridge:gap-3",
        "bridge:gap-5",
    ]
    assert [row["score"] for row in top_rows] == [51.0, 44.0, 33.0, 12.0, 7.0]
    assert top_rows[0]["worst_contract_id"] == "fixture:bridge:gap-4:right"
    assert top_rows[0]["worst_contract_repair_gap"] == 0.51
    assert all(isinstance(row["blockers"], list) for row in top_rows)
    validate_market_graph_ops_status_report(report)


def _platform_radar_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "platform_gap_rows": [
            {
                "family": "BTC_THRESHOLD",
                "missing_platform_or_venue": "polymarket",
                "expected_value_of_fetch": "HIGH",
                "allowed_next_action": "FETCH_SAVED_MARKET_SNAPSHOT",
                "ontology_priority_score": 3,
            },
            {
                "family": "FED_MEETING_RANGE",
                "missing_platform_or_venue": "polymarket",
                "expected_value_of_fetch": "MEDIUM",
                "allowed_next_action": "FETCH_SAVED_MARKET_SNAPSHOT",
                "ontology_priority_score": 1,
            },
        ],
        "recommended_platform_fetches": [
            {
                "family": "BTC_THRESHOLD",
                "missing_platform_or_venue": "polymarket",
                "expected_value_of_fetch": "HIGH",
                "allowed_next_action": "FETCH_SAVED_MARKET_SNAPSHOT",
                "ontology_priority_score": 3,
                "ontology_priority_reasons": ["high_confidence_entity"],
            }
        ],
    }


def _ontology_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "entity_count": 3,
        "ontology_rows": [
            {"entity_id": "entity:crypto_asset:btc", "entity_type": "CRYPTO_ASSET"},
            {"entity_id": "entity:fed_meeting:fomc_2026_06", "entity_type": "FED_MEETING"},
            {"entity_id": "entity:other:low", "entity_type": "OTHER_UNKNOWN"},
        ],
        "summary": {
            "entities_by_type": {"CRYPTO_ASSET": 1, "FED_MEETING": 1, "OTHER_UNKNOWN": 1},
            "low_confidence_entities": ["entity:other:low"],
            "cross_venue_entity_candidates": ["entity:crypto_asset:btc"],
            "families_with_missing_entity_coverage": ["WEATHER_STATION"],
            "recommended_next_entity_normalization_tasks": ["ADD_WEATHER_STATION_METADATA"],
        },
    }


def test_ops_status_surfaces_platform_radar_and_ontology() -> None:
    report = build_market_graph_ops_status_report(
        snapshot_id="snapshot-1",
        as_of="2026-05-25T12:00:00+00:00",
        trade_indicator_report=_trade_indicators_report(),
        probability_constraints_report=_probability_constraints_report(),
        payoff_state_feasibility_bridge_report=_payoff_bridge_report(),
        signal_persistence_report=_signal_persistence_report(),
        rv_investigation_packets_report=_rv_packets_report(),
        stale_lag_watchlist_report=_stale_lag_report(),
        platform_expansion_radar_report=_platform_radar_report(),
        event_entity_ontology_report=_ontology_report(),
    )

    assert report["summary"]["platform_expansion_gap_rows"] == 2
    assert report["summary"]["platform_expansion_high_value_rows"] == 1
    assert report["summary"]["ontology_entity_count"] == 3
    assert report["summary"]["ontology_low_confidence_count"] == 1
    assert report["summary"]["ontology_cross_venue_candidate_count"] == 1
    assert "REVIEW_PLATFORM_EXPANSION_GAPS" in report["next_recommended_actions"]
    assert "REVIEW_ONTOLOGY_COVERAGE" in report["next_recommended_actions"]
    top_recs = report["top_platform_expansion_recommendations"]
    assert top_recs
    assert top_recs[0]["row_id"] == "platform:polymarket:BTC_THRESHOLD"
    assert top_recs[0]["confidence_tier"] == "HIGH"
    validate_market_graph_ops_status_report(report)


def test_ops_status_surfaces_freshness_buckets_and_packet_kinds_and_top_blockers() -> None:
    rv_packets = _rv_packets_report()
    rv_packets["summary"] = {
        "by_packet_kind": {
            "STRUCTURAL_VIOLATION": 1,
            "FAIR_VALUE_REFERENCE_ONLY": 2,
            "BTC_BASIS_RISK_REVIEW": 1,
            "SIMILARITY_RESEARCH": 0,
            "LLM_ONLY": 0,
        }
    }
    # Add a second packet to exercise the shared blocker count below.
    rv_packets["investigation_packets"].append(
        {
            "packet_id": "packet:reference",
            "signal_types": [],
            "probability_constraint_types": ["complement_pair"],
            "markets_involved": ["fixture:yes", "fixture:no"],
            "confidence_tier": "LOW",
            "priority_score": 32.0,
            "packet_blockers": ["graph_packet_review_only", "reference_only_source"],
            "allowed_next_action": "IGNORE_LOW_CONFIDENCE",
        }
    )

    report = build_market_graph_ops_status_report(
        snapshot_id="snapshot-1",
        as_of="2026-05-25T12:00:00+00:00",
        trade_indicator_report=_trade_indicators_report(),
        probability_constraints_report=_probability_constraints_report(),
        payoff_state_feasibility_bridge_report=_payoff_bridge_report(),
        signal_persistence_report=_signal_persistence_report(),
        rv_investigation_packets_report=rv_packets,
        stale_lag_watchlist_report=_stale_lag_report(),
    )

    assert report["summary"]["freshness_buckets"] == {
        "fresh": 0,
        "maybe_stale": 0,
        "stale": 1,
        "missing_timestamp": 1,
        "uniform_timestamps_suspicious": 1,
    }
    assert report["summary"]["packet_kind_counts"] == {
        "STRUCTURAL_VIOLATION": 1,
        "FAIR_VALUE_REFERENCE_ONLY": 2,
        "BTC_BASIS_RISK_REVIEW": 1,
        "SIMILARITY_RESEARCH": 0,
        "LLM_ONLY": 0,
    }
    # The ladder excludes diagnostic invariants (no_execution_permission,
    # not_evaluator_input, etc.) and surfaces actionable bottlenecks instead.
    top_blockers = report["top_blockers_by_frequency"]
    assert top_blockers
    blocker_names = {row["blocker"] for row in top_blockers}
    # Boilerplate invariants must not appear in the ladder; they would crowd
    # out the actionable issues operators can fix.
    assert "not_evaluator_input" not in blocker_names
    assert "no_execution_permission" not in blocker_names
    assert "requires_settlement_source_review" not in blocker_names
    # The midpoint-blocked signal is one fixable bottleneck the ladder must
    # expose so reviewers know fresh real quotes would unblock work.
    assert "diagnostic_midpoint_not_actionable" in blocker_names
    # The stale_quote blocker came from a constraint and should also appear.
    assert "stale_quote" in blocker_names
    validate_market_graph_ops_status_report(report)


def test_ops_status_packet_kind_counts_fall_back_to_row_scan() -> None:
    rv_packets = _rv_packets_report()
    # Drop the summary so the radar must re-count from packet rows.
    rv_packets.pop("summary", None)
    rv_packets["investigation_packets"][0]["packet_kind"] = "FAIR_VALUE_REFERENCE_ONLY"

    report = build_market_graph_ops_status_report(
        snapshot_id="snapshot-1",
        as_of="2026-05-25T12:00:00+00:00",
        trade_indicator_report=_trade_indicators_report(),
        probability_constraints_report=_probability_constraints_report(),
        payoff_state_feasibility_bridge_report=_payoff_bridge_report(),
        signal_persistence_report=_signal_persistence_report(),
        rv_investigation_packets_report=rv_packets,
        stale_lag_watchlist_report=_stale_lag_report(),
    )

    assert report["summary"]["packet_kind_counts"]["FAIR_VALUE_REFERENCE_ONLY"] == 1
    validate_market_graph_ops_status_report(report)


def test_ops_status_rejects_unsafe_actions() -> None:
    report = build_market_graph_ops_status_report(
        trade_indicator_report=_trade_indicators_report(),
        probability_constraints_report=_probability_constraints_report(),
        payoff_state_feasibility_bridge_report=_payoff_bridge_report(),
        signal_persistence_report=_signal_persistence_report(),
        rv_investigation_packets_report=_rv_packets_report(),
        stale_lag_watchlist_report=_stale_lag_report(),
    )
    report["allowed_actions"] = ["WATCH", "BUY"]

    with pytest.raises(SchemaValidationError):
        validate_market_graph_ops_status_report(report)
