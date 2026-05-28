from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph_engine.reporting.manual_relationship_evidence import (
    build_graph_manual_relationship_evidence_report,
    render_graph_manual_relationship_evidence_markdown,
    validate_graph_manual_relationship_evidence_report,
    write_graph_manual_relationship_evidence_report,
)
from graph_engine.reporting.safety import find_prohibited_rendered_text


def _crypto_payoff_payload() -> dict:
    return {
        "rows": [
            {
                "asset": "BTC",
                "market_id": "KXBTC-26MAY2207-T68200",
                "market_title": "Will BTC close above 68k on May 22?",
                "payoff_shape": "daily_5pm_price_threshold",
                "comparator": ">=",
                "threshold": 68200,
                "observation_time": "5:00 PM ET",
                "observation_timezone": "ET",
                "blockers": [
                    "stale_quote",
                    "settlement_source_mismatch",
                ],
                "best_peer": {
                    "row_id": "crypto_payoff::polymarket::1057883",
                    "payoff_shape": "deadline_touch_threshold",
                    "venue": "polymarket",
                    "settlement_source": "Binance",
                    "date_match": True,
                    "score": 0,
                    "threshold": 68000,
                },
                "comparability_class": "basis_risk_only",
            }
        ]
    }


def _fed_manifest_payload() -> dict:
    return {
        "rows": [
            {
                "venue": "kalshi",
                "event_ticker": "KXFOMCDISSENTCOUNT-26JUN",
                "candidate_type": "manifest_candidate_scout",
                "market_count": 5,
                "apparent_outcome_count": 5,
                "has_shared_rules": False,
                "missing_metadata_blockers": ["missing_outcome_definition"],
            }
        ]
    }


def _sports_payload() -> dict:
    return {
        "rows": [
            {
                "kalshi": {"ticker": "KXMLB-26-NYY", "question": "Will the New York Yankees win the 2026 [redacted]?"},
                "polymarket": {"market_id": "1235547", "question": "Will the Yankees win the 2026 [redacted]?"},
                "blockers": ["kalshi_orderbook_not_enriched"],
                "info_blockers": [],
                "strict_blockers": ["depth_freshness_missing"],
                "same_payoff": True,
                "recommended_next_action": "ENRICH_IF_APPROVED",
                "similarity_score": 0.97,
                "strict_pass_count": 4,
            }
        ]
    }


def _non_sports_near_miss_payload() -> dict:
    return {
        "near_misses": [
            {
                "category": "companies",
                "blocker_labels": ["text_similarity_below_threshold"],
                "kalshi": {"ticker": "KXIPOOPENAI-27MAY01", "title_or_question": "When will OpenAI IPO?"},
                "polymarket": {"market_id": "1301184", "title_or_question": "Anthropic or OpenAI IPO first?"},
                "matched_fields": {"final_similarity_score": 0.6314},
                "recommended_next_step": "watch_only",
            }
        ]
    }


def _write_reports(tmp_path: Path) -> Path:
    rv = tmp_path / "rv_reports"
    rv.mkdir()
    (rv / "crypto_payoff_calendar_audit.json").write_text(json.dumps(_crypto_payoff_payload()), encoding="utf-8")
    (rv / "manifest_candidate_scout_fed.json").write_text(json.dumps(_fed_manifest_payload()), encoding="utf-8")
    (rv / "mlb_world_series_same_payoff_board.json").write_text(json.dumps(_sports_payload()), encoding="utf-8")
    (rv / "non_sports_near_miss_diagnostics.json").write_text(json.dumps(_non_sports_near_miss_payload()), encoding="utf-8")
    return rv


def test_evidence_inventory_has_records_per_vertical(tmp_path: Path) -> None:
    rv = _write_reports(tmp_path)
    report = build_graph_manual_relationship_evidence_report(rv_reports_dir=rv)
    verticals = {entry["vertical"] for entry in report["summary"]["records_by_vertical"]}
    assert {"crypto", "economics", "sports"}.issubset(verticals)


def test_every_record_has_why_not_exact(tmp_path: Path) -> None:
    rv = _write_reports(tmp_path)
    report = build_graph_manual_relationship_evidence_report(rv_reports_dir=rv)
    assert report["records"]
    for record in report["records"]:
        assert isinstance(record["why_not_exact"], str)
        assert record["why_not_exact"]
        assert isinstance(record["why_related"], str)
        assert record["why_related"]
        assert record["diagnostic_only"] is True
        assert record["affects_evaluator_gates"] is False


def test_crypto_touch_vs_pit_requires_manual_evidence_not_exact(tmp_path: Path) -> None:
    rv = _write_reports(tmp_path)
    report = build_graph_manual_relationship_evidence_report(rv_reports_dir=rv)
    crypto_records = [record for record in report["records"] if record["vertical"] == "crypto"]
    assert crypto_records
    sample = crypto_records[0]
    assert sample["relationship_type"] not in {"SAME_PAYOFF_CANDIDATE_REVIEW"}
    assert sample["can_go_to_relative_value_now"] is False
    assert "settlement_source_url" in sample["manual_evidence_needed"] or "fresh_orderbook_capture" in sample["manual_evidence_needed"]


def test_economics_midpoint_vs_upper_bound_stays_basis_risk(tmp_path: Path) -> None:
    rv = _write_reports(tmp_path)
    report = build_graph_manual_relationship_evidence_report(rv_reports_dir=rv)
    fed_records = [record for record in report["records"] if record["vertical"] == "economics"]
    assert fed_records
    for record in fed_records:
        assert record["current_action"] in {"BASIS_RISK_REVIEW", "MANUAL_REVIEW", "SOURCE_REVIEW"}
        assert record["relationship_type"] in {
            "SAME_MEETING_DIFFERENT_RATE_DEFINITION",
            "MIDPOINT_VS_UPPER_BOUND",
            "UPPER_BOUND_VS_EFFECTIVE_RATE",
            "EXHAUSTIVE_GROUP_MEMBER",
        }


def test_sports_reference_only_cannot_be_executable(tmp_path: Path) -> None:
    rv = _write_reports(tmp_path)
    report = build_graph_manual_relationship_evidence_report(rv_reports_dir=rv)
    for record in report["records"]:
        if record["relationship_type"] in {"FAIR_VALUE_REFERENCE_ONLY", "SPORTSBOOK_REFERENCE_ONLY", "TRUTH_FEED_ANCHOR_ONLY"}:
            assert record["current_action"] == "SOURCE_REVIEW"
            assert record["can_go_to_relative_value_now"] is False


def test_evidence_writer_writes_markdown_and_no_prohibited_vocab(tmp_path: Path) -> None:
    rv = _write_reports(tmp_path)
    json_path = tmp_path / "evidence.json"
    md_path = tmp_path / "evidence.md"
    write_graph_manual_relationship_evidence_report(
        rv_reports_dir=rv, json_output=json_path, markdown_output=md_path
    )
    text = md_path.read_text(encoding="utf-8")
    assert "# Graph Manual Relationship Evidence" in text
    assert find_prohibited_rendered_text(text) == []
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    validate_graph_manual_relationship_evidence_report(payload)


def test_evidence_report_handles_missing_directory(tmp_path: Path) -> None:
    report = build_graph_manual_relationship_evidence_report(rv_reports_dir=tmp_path / "missing")
    assert report["summary"]["total_records"] == 0
    assert "crypto_payoff_calendar_audit.json" in report["inputs"]["missing"]


def test_evidence_report_passes_safety_summary(tmp_path: Path) -> None:
    rv = _write_reports(tmp_path)
    report = build_graph_manual_relationship_evidence_report(rv_reports_dir=rv)
    safety = report["safety_summary"]
    assert safety["graph_emits_evaluator_input"] is False
    assert safety["graph_can_claim_exact_payoff"] is False
    assert safety["manual_evidence_layer_diagnostic_only"] is True


def test_evidence_markdown_renderer_is_safe(tmp_path: Path) -> None:
    rv = _write_reports(tmp_path)
    report = build_graph_manual_relationship_evidence_report(rv_reports_dir=rv)
    text = render_graph_manual_relationship_evidence_markdown(report)
    assert find_prohibited_rendered_text(text) == []
