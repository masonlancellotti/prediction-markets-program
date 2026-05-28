from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph_engine.relationships.rv_edge_taxonomy import RELATIONSHIP_VERSION
from graph_engine.reporting.llm_graph_manual_evidence_review import (
    ALLOWED_LLM_RELATIONSHIP_TYPES,
    LLM_VERSION,
    build_llm_graph_manual_evidence_review_schema,
    validate_llm_graph_manual_evidence_review_output,
    write_llm_graph_manual_evidence_review_assets,
)
from graph_engine.reporting.manual_relationship_evidence import EVIDENCE_VERSION
from graph_engine.reporting.safety import find_prohibited_rendered_text


def _evidence(tmp_path: Path) -> Path:
    payload = {
        "diagnostic_only": True,
        "summary": {"total_records": 2},
        "records": [
            {
                "relationship_id": "evidence:crypto:1",
                "vertical": "crypto",
                "family": "payoff_calendar",
                "relationship_type": "DEADLINE_TOUCH_VS_POINT_IN_TIME",
                "left_market_or_source": "kalshi:KXBTC-26MAY2207-T68200",
                "right_market_or_source": "polymarket:1057883",
                "venues": ["kalshi", "polymarket"],
                "blockers": ["stale_quote", "settlement_source_mismatch"],
                "manual_evidence_needed": ["settlement_source_url", "payoff_shape_text_from_rules"],
                "current_action": "BASIS_RISK_REVIEW",
                "can_go_to_relative_value_now": False,
                "source_reports": ["crypto_payoff_calendar_audit.json"],
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
            },
            {
                "relationship_id": "evidence:title:1",
                "vertical": "structural",
                "family": "weak_signal",
                "relationship_type": "TITLE_SIMILARITY_ONLY",
                "left_market_or_source": "kalshi:KXIPOOPENAI",
                "right_market_or_source": "polymarket:1301184",
                "venues": ["kalshi", "polymarket"],
                "blockers": ["title_similarity_not_structural_evidence"],
                "manual_evidence_needed": ["title_evidence"],
                "current_action": "IGNORE_LOW_CONFIDENCE",
                "can_go_to_relative_value_now": False,
                "source_reports": ["non_sports_near_miss_diagnostics.json"],
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
            },
        ],
    }
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_prompt_and_schema_written_no_prohibited_vocab(tmp_path: Path) -> None:
    evidence_path = _evidence(tmp_path)
    write_llm_graph_manual_evidence_review_assets(
        relationships_path=evidence_path,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=5,
    )
    prompt = (tmp_path / "prompt.md").read_text(encoding="utf-8")
    assert "Graph Manual Evidence Review Prompt" in prompt
    assert find_prohibited_rendered_text(prompt) == []
    schema = json.loads((tmp_path / "schema.json").read_text(encoding="utf-8"))
    assert schema["$id"].endswith(".schema.json")


def _accepted_output(rel_id: str = "evidence:crypto:1") -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "llm_version": LLM_VERSION,
        "evidence_version": EVIDENCE_VERSION,
        "relationship_version": RELATIONSHIP_VERSION,
        "reviewer_id": "mason",
        "reviewed_records": [
            {
                "relationship_id": rel_id,
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
                "suggested_relationship_type": "DEADLINE_TOUCH_VS_POINT_IN_TIME",
                "suggested_blockers": [
                    "stale_quote",
                    "settlement_source_mismatch",
                    "payoff_shape_mismatch_review",
                ],
                "suggested_manual_checks": ["Open Polymarket rules page"],
                "reviewer_notes": "Looks like a routine deadline vs PIT mismatch.",
                "confidence_bucket": "low",
                "do_not_use_for_exact_gate": True,
            }
        ],
    }


def test_validator_accepts_clean_output(tmp_path: Path) -> None:
    evidence_path = _evidence(tmp_path)
    write_llm_graph_manual_evidence_review_assets(
        relationships_path=evidence_path,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=5,
    )
    output_path = tmp_path / "output.json"
    output_path.write_text(json.dumps(_accepted_output()), encoding="utf-8")
    report = validate_llm_graph_manual_evidence_review_output(
        output_path=output_path,
        schema_path=tmp_path / "schema.json",
        relationships_path=evidence_path,
        json_output=tmp_path / "validation.json",
    )
    assert report["validation_status"] == "ACCEPTED"
    assert report["summary"]["accepted_count"] == 1


def test_validator_rejects_paper_candidate_token_in_suggestion(tmp_path: Path) -> None:
    evidence_path = _evidence(tmp_path)
    write_llm_graph_manual_evidence_review_assets(
        relationships_path=evidence_path,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=5,
    )
    bad = _accepted_output()
    bad["reviewed_records"][0]["reviewer_notes"] = "Mark this as PAPER_CANDIDATE."
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    report = validate_llm_graph_manual_evidence_review_output(
        output_path=bad_path,
        schema_path=tmp_path / "schema.json",
        relationships_path=evidence_path,
    )
    assert report["validation_status"] == "REJECTED"


def test_validator_rejects_executable_true(tmp_path: Path) -> None:
    evidence_path = _evidence(tmp_path)
    write_llm_graph_manual_evidence_review_assets(
        relationships_path=evidence_path,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=5,
    )
    bad = _accepted_output()
    bad["reviewed_records"][0]["suggested_manual_checks"] = ["set executable=true"]
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    report = validate_llm_graph_manual_evidence_review_output(
        output_path=bad_path,
        schema_path=tmp_path / "schema.json",
        relationships_path=evidence_path,
    )
    assert report["validation_status"] == "REJECTED"


def test_validator_rejects_exact_equality_claim(tmp_path: Path) -> None:
    evidence_path = _evidence(tmp_path)
    write_llm_graph_manual_evidence_review_assets(
        relationships_path=evidence_path,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=5,
    )
    bad = _accepted_output()
    bad["reviewed_records"][0]["do_not_use_for_exact_gate"] = False
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    report = validate_llm_graph_manual_evidence_review_output(
        output_path=bad_path,
        schema_path=tmp_path / "schema.json",
        relationships_path=evidence_path,
    )
    assert report["validation_status"] == "REJECTED"


def test_validator_rejects_empty_blockers(tmp_path: Path) -> None:
    evidence_path = _evidence(tmp_path)
    write_llm_graph_manual_evidence_review_assets(
        relationships_path=evidence_path,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=5,
    )
    bad = _accepted_output()
    bad["reviewed_records"][0]["suggested_blockers"] = []
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    report = validate_llm_graph_manual_evidence_review_output(
        output_path=bad_path,
        schema_path=tmp_path / "schema.json",
        relationships_path=evidence_path,
    )
    assert report["validation_status"] == "REJECTED"


def test_validator_rejects_title_similarity_upgrade(tmp_path: Path) -> None:
    evidence_path = _evidence(tmp_path)
    write_llm_graph_manual_evidence_review_assets(
        relationships_path=evidence_path,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=5,
    )
    bad = _accepted_output(rel_id="evidence:title:1")
    bad["reviewed_records"][0]["confidence_bucket"] = "high"
    bad["reviewed_records"][0]["suggested_relationship_type"] = "TITLE_SIMILARITY_ONLY"
    bad["reviewed_records"][0]["suggested_blockers"] = ["title_similarity_not_structural_evidence"]
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    report = validate_llm_graph_manual_evidence_review_output(
        output_path=bad_path,
        schema_path=tmp_path / "schema.json",
        relationships_path=evidence_path,
    )
    assert report["validation_status"] == "REJECTED"


def test_schema_lists_allowed_taxonomy() -> None:
    schema = build_llm_graph_manual_evidence_review_schema()
    rt_enum = schema["$defs"]["ReviewedRecord"]["properties"]["suggested_relationship_type"]["enum"]
    assert "DEADLINE_TOUCH_VS_POINT_IN_TIME" in rt_enum
    assert "MIDPOINT_VS_UPPER_BOUND" in rt_enum
    assert "EVENT_WINNER_SAME_FIELD_REVIEW" in rt_enum
    assert "PAPER_CANDIDATE" not in rt_enum
