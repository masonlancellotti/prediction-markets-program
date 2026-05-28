from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph_engine.reporting.ops_status import (
    build_market_graph_ops_status_report,
    validate_market_graph_ops_status_report,
    write_market_graph_ops_status_report,
)


def _empty_trade_indicators() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "snapshot_id": "snapshot-1",
        "signals": [],
    }


def _empty_constraints() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "snapshot_id": "snapshot-1",
        "probability_constraints": [],
    }


def _empty_bridge() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "payoff_state_feasibility_bridge": [],
    }


def _empty_persistence() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "summary": {"new_count": 0, "worsened_count": 0},
        "signal_persistence_rows": [],
    }


def _empty_rv_packets() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "investigation_packets": [],
    }


def _empty_stale_lag() -> dict:
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


def _evidence_report() -> dict:
    return {
        "diagnostic_only": True,
        "summary": {
            "total_records": 9,
            "ready_for_rv_now": 3,
            "blocked_on_manual_evidence": 6,
            "records_by_vertical": [
                {"vertical": "crypto", "count": 5},
                {"vertical": "economics", "count": 2},
                {"vertical": "sports", "count": 2},
            ],
            "top_blockers": [
                {"blocker": "settlement_source_not_verified", "count": 9},
                {"blocker": "stale_quote", "count": 3},
            ],
        },
    }


def _backlog_report() -> dict:
    return {
        "diagnostic_only": True,
        "summary": {
            "total_items": 5,
            "by_urgency": {"HIGH": 2, "MEDIUM": 2, "LOW": 1},
            "by_vertical": [
                {"vertical": "crypto", "count": 3},
                {"vertical": "economics", "count": 1},
                {"vertical": "sports", "count": 1},
            ],
        },
    }


def test_ops_status_includes_manual_evidence_counts() -> None:
    report = build_market_graph_ops_status_report(
        snapshot_id="snapshot-1",
        as_of="2026-05-25T12:00:00+00:00",
        trade_indicator_report=_empty_trade_indicators(),
        probability_constraints_report=_empty_constraints(),
        payoff_state_feasibility_bridge_report=_empty_bridge(),
        signal_persistence_report=_empty_persistence(),
        rv_investigation_packets_report=_empty_rv_packets(),
        stale_lag_watchlist_report=_empty_stale_lag(),
        graph_manual_relationship_evidence_report=_evidence_report(),
        graph_manual_discovery_backlog_report=_backlog_report(),
        llm_graph_manual_evidence_review_status={
            "prompt_present": True,
            "schema_present": True,
            "prompt_path": "reports/llm_graph_manual_evidence_prompt.md",
            "schema_path": "reports/llm_graph_manual_evidence_schema.json",
        },
    )
    summary = report["summary"]
    assert summary["manual_evidence_records_total"] == 9
    assert summary["manual_evidence_ready_for_rv_now"] == 3
    assert summary["manual_evidence_blocked_count"] == 6
    assert summary["manual_evidence_backlog_total"] == 5
    assert summary["manual_evidence_backlog_high_urgency"] == 2
    assert summary["llm_graph_manual_evidence_prompt_present"] is True
    assert summary["llm_graph_manual_evidence_schema_present"] is True
    manual = report["manual_evidence_summary"]
    assert manual["top_vertical_by_manual_work"] == "crypto"
    assert any(entry["blocker"] == "settlement_source_not_verified" for entry in manual["top_blockers"])
    assert report["safety_summary"]["manual_evidence_layer_diagnostic_only"] is True
    validate_market_graph_ops_status_report(report)


def test_ops_status_markdown_renders_manual_evidence_block(tmp_path: Path) -> None:
    json_out = tmp_path / "ops_status.json"
    md_out = tmp_path / "ops_status.md"
    evidence_path = tmp_path / "evidence.json"
    backlog_path = tmp_path / "backlog.json"
    evidence_path.write_text(json.dumps(_evidence_report()), encoding="utf-8")
    backlog_path.write_text(json.dumps(_backlog_report()), encoding="utf-8")
    write_market_graph_ops_status_report(
        json_output=json_out,
        markdown_output=md_out,
        snapshot_id="snapshot-1",
        as_of="2026-05-25T12:00:00+00:00",
        graph_manual_relationship_evidence_path=evidence_path,
        graph_manual_discovery_backlog_path=backlog_path,
    )
    text = md_out.read_text(encoding="utf-8")
    assert "## Manual Evidence Layer" in text
    assert "Manual evidence records: 9" in text
    assert "Manual discovery backlog items: 5" in text


def test_ops_status_handles_missing_manual_evidence_inputs() -> None:
    report = build_market_graph_ops_status_report(
        snapshot_id="snapshot-1",
        as_of="2026-05-25T12:00:00+00:00",
        trade_indicator_report=_empty_trade_indicators(),
        probability_constraints_report=_empty_constraints(),
        payoff_state_feasibility_bridge_report=_empty_bridge(),
        signal_persistence_report=_empty_persistence(),
        rv_investigation_packets_report=_empty_rv_packets(),
        stale_lag_watchlist_report=_empty_stale_lag(),
    )
    summary = report["summary"]
    assert summary["manual_evidence_records_total"] == 0
    assert summary["manual_evidence_backlog_total"] == 0
    assert summary["llm_graph_manual_evidence_prompt_present"] is False
    validate_market_graph_ops_status_report(report)
