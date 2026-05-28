from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph_engine.reporting.ops_status import (
    build_market_graph_ops_status_report,
    validate_market_graph_ops_status_report,
    write_market_graph_ops_status_report,
)


def _trade_indicators_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "snapshot_id": "snapshot-1",
        "signals": [],
    }


def _probability_constraints_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "snapshot_id": "snapshot-1",
        "probability_constraints": [],
    }


def _bridge_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "payoff_state_feasibility_bridge": [],
    }


def _signal_persistence_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "summary": {"new_count": 0, "worsened_count": 0},
        "signal_persistence_rows": [],
    }


def _rv_packets_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "investigation_packets": [],
    }


def _stale_lag_report() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "stale_lag_watch_count": 0,
        "stale_lag_blocked_count": 0,
        "uniform_timestamps_blocked_count": 0,
        "freshness_buckets": {
            "fresh": 0,
            "maybe_stale": 0,
            "stale": 0,
            "missing_timestamp": 0,
            "uniform_timestamps_suspicious": 0,
        },
        "stale_lag_watchlist": [],
    }


def _rv_edges_report() -> dict:
    return {
        "diagnostic_only": True,
        "summary": {
            "total_edges": 5,
            "edges_by_family": [
                {"relationship_family": "basis_risk", "count": 3},
                {"relationship_family": "near_exact_review", "count": 1},
                {"relationship_family": "structural", "count": 1},
            ],
            "top_blockers": [
                {"blocker": "settlement_source_not_verified", "count": 5},
                {"blocker": "deadline_touch_not_point_in_time", "count": 2},
            ],
        },
        "core_trio_crypto_section": {
            "edge_count": 4,
            "venues": ["kalshi", "polymarket", "cdna"],
            "edges_by_relationship_type": [],
        },
        "manual_discovery_priorities": [
            {
                "family": "crypto_price_threshold",
                "priority": "HIGH",
                "reason": "Fetch Kalshi BTC/ETH series for matching threshold.",
            }
        ],
    }


def _rv_worklist_report() -> dict:
    return {
        "diagnostic_only": True,
        "summary": {"total_rows": 3, "rows_by_action": [], "rows_by_relationship_type": []},
        "rows": [
            {"edge_id": "a", "rv_can_inspect_now": True},
            {"edge_id": "b", "rv_can_inspect_now": False},
            {"edge_id": "c", "rv_can_inspect_now": True},
        ],
    }


def test_ops_status_includes_rv_relationship_counts() -> None:
    report = build_market_graph_ops_status_report(
        snapshot_id="snapshot-1",
        as_of="2026-05-25T12:00:00+00:00",
        trade_indicator_report=_trade_indicators_report(),
        probability_constraints_report=_probability_constraints_report(),
        payoff_state_feasibility_bridge_report=_bridge_report(),
        signal_persistence_report=_signal_persistence_report(),
        rv_investigation_packets_report=_rv_packets_report(),
        stale_lag_watchlist_report=_stale_lag_report(),
        rv_diagnostic_relationship_edges_report=_rv_edges_report(),
        rv_review_worklist_report=_rv_worklist_report(),
        llm_graph_relationship_review_status={"prompt_present": True, "schema_present": True},
    )
    summary = report["summary"]
    assert summary["rv_diagnostic_edges_total"] == 5
    assert summary["rv_diagnostic_edges_basis_risk"] == 3
    assert summary["rv_diagnostic_edges_near_exact_review"] == 1
    assert summary["rv_diagnostic_edges_structural"] == 1
    assert summary["rv_diagnostic_crypto_payoff_calendar_edges"] == 4
    assert summary["rv_review_worklist_row_count"] == 3
    assert summary["rv_review_worklist_rv_can_inspect_now_count"] == 2
    assert summary["llm_graph_relationship_review_prompt_present"] is True
    assert summary["llm_graph_relationship_review_schema_present"] is True
    assert report["rv_relationship_summary"]["top_manual_discovery_priority"] == {
        "family": "crypto_price_threshold",
        "reason": "Fetch Kalshi BTC/ETH series for matching threshold.",
    }
    safety = report["safety_summary"]
    assert safety["diagnostic_only"] is True
    assert safety["affects_evaluator_gates"] is False
    assert safety["graph_emits_evaluator_input"] is False
    assert safety["graph_can_create_candidate_pair"] is False
    assert safety["graph_can_claim_exact_payoff"] is False
    assert safety["llm_advisory_only"] is True
    validate_market_graph_ops_status_report(report)


def test_ops_status_markdown_renders_rv_relationship_block(tmp_path: Path) -> None:
    output = tmp_path / "ops_status.json"
    markdown = tmp_path / "ops_status.md"
    edges_path = tmp_path / "edges.json"
    worklist_path = tmp_path / "worklist.json"
    edges_path.write_text(json.dumps(_rv_edges_report()), encoding="utf-8")
    worklist_path.write_text(json.dumps(_rv_worklist_report()), encoding="utf-8")

    write_market_graph_ops_status_report(
        json_output=output,
        markdown_output=markdown,
        snapshot_id="snapshot-1",
        as_of="2026-05-25T12:00:00+00:00",
        rv_diagnostic_relationship_edges_path=edges_path,
        rv_review_worklist_path=worklist_path,
    )
    text = markdown.read_text(encoding="utf-8")
    assert "## RV Relationship Layer" in text
    assert "Total RV-ingested edges: 5" in text
    assert "Crypto payoff-calendar edges: 4" in text
    assert "## Safety Summary" in text


def test_ops_status_handles_missing_rv_optional_inputs(tmp_path: Path) -> None:
    report = build_market_graph_ops_status_report(
        snapshot_id="snapshot-1",
        as_of="2026-05-25T12:00:00+00:00",
        trade_indicator_report=_trade_indicators_report(),
        probability_constraints_report=_probability_constraints_report(),
        payoff_state_feasibility_bridge_report=_bridge_report(),
        signal_persistence_report=_signal_persistence_report(),
        rv_investigation_packets_report=_rv_packets_report(),
        stale_lag_watchlist_report=_stale_lag_report(),
    )
    summary = report["summary"]
    assert summary["rv_diagnostic_edges_total"] == 0
    assert summary["rv_review_worklist_row_count"] == 0
    assert summary["llm_graph_relationship_review_prompt_present"] is False
    assert summary["llm_graph_relationship_review_schema_present"] is False
    validate_market_graph_ops_status_report(report)
