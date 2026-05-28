from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph_engine.reporting.manual_discovery_backlog import (
    PAYOFF_OUTCOMES,
    REUSABLE_SCOPES,
    URGENCY_BUCKETS,
    build_graph_manual_discovery_backlog_report,
    render_graph_manual_discovery_backlog_markdown,
    write_graph_manual_discovery_backlog_report,
)
from graph_engine.reporting.safety import find_prohibited_rendered_text


def _evidence_payload() -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "records": [
            {
                "relationship_id": "manual-evidence:crypto:1",
                "vertical": "crypto",
                "family": "payoff_calendar",
                "left_market_or_source": "kalshi:KXBTC-26MAY2207-T68200",
                "right_market_or_source": "polymarket:1057883",
                "venues": ["kalshi", "polymarket"],
                "relationship_type": "DEADLINE_TOUCH_VS_POINT_IN_TIME",
                "why_related": "Same asset same date",
                "why_not_exact": "Touch vs PIT",
                "blockers": ["stale_quote", "settlement_source_mismatch"],
                "manual_evidence_needed": [
                    "settlement_source_url",
                    "payoff_shape_text_from_rules",
                ],
                "evidence_priority": "MEDIUM",
                "repeat_cadence": "per_venue_rules_version",
                "current_action": "BASIS_RISK_REVIEW",
                "can_go_to_relative_value_now": False,
                "rv_must_verify": [],
                "manual_info_missing": ["settlement_source_url"],
                "source_reports": ["crypto_payoff_calendar_audit.json"],
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
            },
            {
                "relationship_id": "manual-evidence:economics:1",
                "vertical": "economics",
                "family": "rate_definition",
                "left_market_or_source": "kalshi:KXFED-26JUN-T2.75",
                "right_market_or_source": "polymarket:fed_jun",
                "venues": ["kalshi", "polymarket"],
                "relationship_type": "MIDPOINT_VS_UPPER_BOUND",
                "why_related": "Same FOMC meeting",
                "why_not_exact": "Different rate definitions",
                "blockers": ["midpoint_vs_upper_bound_mismatch"],
                "manual_evidence_needed": ["venue_rate_definition_text"],
                "evidence_priority": "HIGH",
                "repeat_cadence": "per_meeting",
                "current_action": "BASIS_RISK_REVIEW",
                "can_go_to_relative_value_now": True,
                "rv_must_verify": ["independent_settlement_source_verification"],
                "manual_info_missing": [],
                "source_reports": ["cross_venue_opportunity_scout.json"],
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
            },
            {
                "relationship_id": "manual-evidence:sports:1",
                "vertical": "sports",
                "family": "event_winner",
                "left_market_or_source": "kalshi:KXMLB-26-NYY",
                "right_market_or_source": "polymarket:1235547",
                "venues": ["kalshi", "polymarket"],
                "relationship_type": "EVENT_WINNER_SAME_FIELD_REVIEW",
                "why_related": "Same championship",
                "why_not_exact": "Void rule differs",
                "blockers": ["kalshi_orderbook_not_enriched"],
                "manual_evidence_needed": ["championship_definition_text"],
                "evidence_priority": "HIGH",
                "repeat_cadence": "per_event_date",
                "current_action": "MANUAL_REVIEW",
                "can_go_to_relative_value_now": True,
                "rv_must_verify": ["team_list_and_tie_break_rule"],
                "manual_info_missing": [],
                "source_reports": ["mlb_world_series_same_payoff_board.json"],
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
            },
            {
                "relationship_id": "manual-evidence:weak:1",
                "vertical": "structural",
                "family": "weak_signal",
                "left_market_or_source": "kalshi:KXIPOOPENAI-27MAY01",
                "right_market_or_source": "polymarket:1301184",
                "venues": ["kalshi", "polymarket"],
                "relationship_type": "TITLE_SIMILARITY_ONLY",
                "why_related": "Title overlap",
                "why_not_exact": "Title is not structural",
                "blockers": ["title_similarity_not_structural_evidence"],
                "manual_evidence_needed": ["title_evidence"],
                "evidence_priority": "LOW",
                "repeat_cadence": "one_time",
                "current_action": "IGNORE_LOW_CONFIDENCE",
                "can_go_to_relative_value_now": False,
                "rv_must_verify": [],
                "manual_info_missing": ["structural_evidence_or_explicit_basis_risk_classification"],
                "source_reports": ["non_sports_near_miss_diagnostics.json"],
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
            },
        ],
    }


def _write_evidence(tmp_path: Path) -> Path:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(_evidence_payload()), encoding="utf-8")
    return path


def test_backlog_items_have_blocker_cleared_and_cadence(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path)
    report = build_graph_manual_discovery_backlog_report(relationships_path=evidence)
    assert report["items"]
    for item in report["items"]:
        assert item["blocker_cleared"]
        assert item["reusable_scope"] in REUSABLE_SCOPES
        assert item["expected_payoff"] in PAYOFF_OUTCOMES
        assert item["urgency"] in URGENCY_BUCKETS


def test_backlog_sections_cover_required_buckets(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path)
    report = build_graph_manual_discovery_backlog_report(relationships_path=evidence)
    sections = report["sections"]
    assert "top_10_overall" in sections
    assert "top_crypto" in sections
    assert "top_economics" in sections
    assert "top_sports" in sections
    assert "unblocks_most_graph_edges" in sections
    assert "unblocks_relative_value_review" in sections
    assert "ignore_for_now" in sections


def test_backlog_high_urgency_tracks_rv_ready_records(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path)
    report = build_graph_manual_discovery_backlog_report(relationships_path=evidence)
    # The fed + sports rows are RV-ready, so HIGH urgency must include their tasks.
    high = [item for item in report["items"] if item["urgency"] == "HIGH"]
    assert high
    high_payoffs = {item["expected_payoff"] for item in high}
    assert high_payoffs & {"enables_rv_source_review", "enables_exact_review_candidate"}


def test_backlog_writes_clean_markdown(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path)
    json_out = tmp_path / "backlog.json"
    md_out = tmp_path / "backlog.md"
    write_graph_manual_discovery_backlog_report(
        relationships_path=evidence, json_output=json_out, markdown_output=md_out
    )
    text = md_out.read_text(encoding="utf-8")
    assert "# Graph Manual Discovery Backlog" in text
    assert find_prohibited_rendered_text(text) == []


def test_backlog_handles_missing_input(tmp_path: Path) -> None:
    report = build_graph_manual_discovery_backlog_report(relationships_path=tmp_path / "missing.json")
    assert report["summary"]["total_items"] == 0
    assert report["inputs"]["missing_input_report"] is True


def test_backlog_markdown_renderer_is_safe(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path)
    report = build_graph_manual_discovery_backlog_report(relationships_path=evidence)
    text = render_graph_manual_discovery_backlog_markdown(report)
    assert find_prohibited_rendered_text(text) == []
