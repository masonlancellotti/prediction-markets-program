from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph_engine.relationships.rv_edge_taxonomy import make_rv_edge
from graph_engine.reporting.llm_graph_relationship_review import (
    ALLOWED_LLM_ACTIONS,
    ALLOWED_LLM_AGREEMENT,
    ALLOWED_LLM_CONFIDENCE,
    ALLOWED_LLM_RELATIONSHIP_TYPES,
    LLM_VERSION,
    build_llm_graph_relationship_review_schema,
    validate_llm_graph_relationship_review_output,
    write_llm_graph_relationship_review_assets,
)
from graph_engine.relationships.rv_edge_taxonomy import RELATIONSHIP_VERSION
from graph_engine.reporting.safety import find_prohibited_rendered_text


def _edges_path(tmp_path: Path) -> Path:
    edges = [
        make_rv_edge(
            edge_id="rv-edge:test:1",
            left_market_id="kalshi:KXBTC-26MAY2207-T68200",
            right_market_id="polymarket:1299974",
            left_venue="kalshi",
            right_venue="polymarket",
            relationship_type="DEADLINE_TOUCH_VS_POINT_IN_TIME",
        ),
        make_rv_edge(
            edge_id="rv-edge:test:reference",
            left_market_id="cdna:anchor",
            right_market_id="reference:fed",
            left_venue="cdna",
            right_venue="federalreserve",
            relationship_type="FAIR_VALUE_REFERENCE_ONLY",
        ),
        make_rv_edge(
            edge_id="rv-edge:test:title",
            left_market_id="kalshi:KXNFL-26-1",
            right_market_id="polymarket:nfl-1",
            left_venue="kalshi",
            right_venue="polymarket",
            relationship_type="TITLE_SIMILARITY_ONLY",
        ),
    ]
    payload = {"diagnostic_only": True, "affects_evaluator_gates": False, "edges": edges}
    path = tmp_path / "edges.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def test_prompt_and_schema_are_written(tmp_path: Path) -> None:
    edges = _edges_path(tmp_path)
    result = write_llm_graph_relationship_review_assets(
        edges_report_path=edges,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=2,
    )
    assert (tmp_path / "prompt.md").exists()
    assert (tmp_path / "schema.json").exists()
    assert result["sample_size_actual"] <= 2
    schema_payload = json.loads((tmp_path / "schema.json").read_text(encoding="utf-8"))
    assert schema_payload["$id"].endswith(".schema.json")


def test_prompt_includes_required_safety_language(tmp_path: Path) -> None:
    edges = _edges_path(tmp_path)
    write_llm_graph_relationship_review_assets(
        edges_report_path=edges,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=2,
    )
    prompt = (tmp_path / "prompt.md").read_text(encoding="utf-8")
    # The prompt uses graph-safe wording but must still cover the key
    # forbidden claims: evaluator-input promotion, execution-readiness,
    # exact-payoff equality, and dropping deterministic blockers.
    assert "evaluator-input" in prompt
    assert "execution-readiness" in prompt
    assert "exact-payoff equality" in prompt
    assert "blockers" in prompt.lower()


def test_prompt_markdown_has_no_prohibited_vocab(tmp_path: Path) -> None:
    edges = _edges_path(tmp_path)
    write_llm_graph_relationship_review_assets(
        edges_report_path=edges,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=2,
    )
    prompt = (tmp_path / "prompt.md").read_text(encoding="utf-8")
    assert find_prohibited_rendered_text(prompt) == []


def _accepted_llm_output(edge_id: str = "rv-edge:test:1") -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "llm_version": LLM_VERSION,
        "relationship_version": RELATIONSHIP_VERSION,
        "reviewer_id": "mason",
        "reviewed_edges": [
            {
                "edge_id": edge_id,
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
                "agreement": "agree",
                "suggested_relationship_type": "DEADLINE_TOUCH_VS_POINT_IN_TIME",
                "suggested_action": "BASIS_RISK_REVIEW",
                "confidence_bucket": "low",
                "reviewer_notes": "Looks like a typical deadline-touch row.",
                "suggested_blockers": [
                    "not_evaluator_input",
                    "requires_independent_payoff_verification",
                    "settlement_source_not_verified",
                    "settlement_time_not_verified",
                    "fee_model_not_verified",
                    "quote_freshness_not_verified",
                    "deadline_touch_not_point_in_time",
                ],
                "suggested_manual_checks": ["compare settlement source urls"],
                "fake_edge_risks": ["title similarity overstates equivalence"],
                "exact_payoff_claim": False,
                "can_create_evaluator_input_claim": False,
            }
        ],
    }


def test_validator_accepts_clean_output(tmp_path: Path) -> None:
    edges = _edges_path(tmp_path)
    write_llm_graph_relationship_review_assets(
        edges_report_path=edges,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=3,
    )
    output_path = tmp_path / "output.json"
    output_path.write_text(json.dumps(_accepted_llm_output()), encoding="utf-8")
    report = validate_llm_graph_relationship_review_output(
        output_path=output_path,
        schema_path=tmp_path / "schema.json",
        edges_report_path=edges,
        json_output=tmp_path / "validation.json",
    )
    assert report["validation_status"] == "ACCEPTED"
    assert report["summary"]["accepted_count"] == 1
    assert report["summary"]["rejected_count"] == 0


def test_validator_rejects_paper_candidate_action(tmp_path: Path) -> None:
    edges = _edges_path(tmp_path)
    write_llm_graph_relationship_review_assets(
        edges_report_path=edges,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=3,
    )
    bad = _accepted_llm_output()
    bad["reviewed_edges"][0]["suggested_action"] = "PAPER_CANDIDATE"
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    report = validate_llm_graph_relationship_review_output(
        output_path=bad_path,
        schema_path=tmp_path / "schema.json",
        edges_report_path=edges,
    )
    assert report["validation_status"] == "REJECTED"


def test_validator_rejects_executable_true_claim(tmp_path: Path) -> None:
    edges = _edges_path(tmp_path)
    write_llm_graph_relationship_review_assets(
        edges_report_path=edges,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=3,
    )
    # The schema enforces exact_payoff_claim/can_create_evaluator_input_claim
    # to be false; flipping one should fail.
    bad = _accepted_llm_output()
    bad["reviewed_edges"][0]["exact_payoff_claim"] = True
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    report = validate_llm_graph_relationship_review_output(
        output_path=bad_path,
        schema_path=tmp_path / "schema.json",
        edges_report_path=edges,
    )
    assert report["validation_status"] == "REJECTED"


def test_validator_rejects_exact_true_without_deterministic_proof(tmp_path: Path) -> None:
    edges = _edges_path(tmp_path)
    write_llm_graph_relationship_review_assets(
        edges_report_path=edges,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=3,
    )
    bad = _accepted_llm_output()
    bad["reviewed_edges"][0]["suggested_relationship_type"] = "SAME_PAYOFF_CANDIDATE_REVIEW"
    # Point at an edge that is not in the deterministic edges so the LLM
    # is "inventing" the upgrade — the validator must reject.
    bad["reviewed_edges"][0]["edge_id"] = "rv-edge:does-not-exist"
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    report = validate_llm_graph_relationship_review_output(
        output_path=bad_path,
        schema_path=tmp_path / "schema.json",
        edges_report_path=edges,
    )
    assert report["validation_status"] == "REJECTED"


def test_validator_rejects_dropping_blockers(tmp_path: Path) -> None:
    edges = _edges_path(tmp_path)
    write_llm_graph_relationship_review_assets(
        edges_report_path=edges,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=3,
    )
    bad = _accepted_llm_output()
    # Drop a deterministic blocker
    bad["reviewed_edges"][0]["suggested_blockers"] = ["only_one_thing"]
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    report = validate_llm_graph_relationship_review_output(
        output_path=bad_path,
        schema_path=tmp_path / "schema.json",
        edges_report_path=edges,
    )
    assert report["validation_status"] == "REJECTED"


def test_validator_rejects_title_similarity_upgrade(tmp_path: Path) -> None:
    edges = _edges_path(tmp_path)
    write_llm_graph_relationship_review_assets(
        edges_report_path=edges,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=3,
    )
    bad = _accepted_llm_output(edge_id="rv-edge:test:title")
    bad["reviewed_edges"][0]["confidence_bucket"] = "high"
    bad["reviewed_edges"][0]["suggested_relationship_type"] = "TITLE_SIMILARITY_ONLY"
    bad["reviewed_edges"][0]["suggested_blockers"] = [
        "not_evaluator_input",
        "requires_independent_payoff_verification",
        "settlement_source_not_verified",
        "settlement_time_not_verified",
        "fee_model_not_verified",
        "quote_freshness_not_verified",
        "title_similarity_not_structural_evidence",
    ]
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    report = validate_llm_graph_relationship_review_output(
        output_path=bad_path,
        schema_path=tmp_path / "schema.json",
        edges_report_path=edges,
    )
    assert report["validation_status"] == "REJECTED"


def test_validator_rejects_reference_only_upgrade(tmp_path: Path) -> None:
    edges = _edges_path(tmp_path)
    write_llm_graph_relationship_review_assets(
        edges_report_path=edges,
        prompt_output=tmp_path / "prompt.md",
        schema_output=tmp_path / "schema.json",
        sample_size=3,
    )
    bad = _accepted_llm_output(edge_id="rv-edge:test:reference")
    bad["reviewed_edges"][0]["suggested_relationship_type"] = "SAME_PAYOFF_CANDIDATE_REVIEW"
    bad["reviewed_edges"][0]["suggested_blockers"] = list(
        bad["reviewed_edges"][0]["suggested_blockers"]
    ) + ["reference_only_source"]
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    report = validate_llm_graph_relationship_review_output(
        output_path=bad_path,
        schema_path=tmp_path / "schema.json",
        edges_report_path=edges,
    )
    assert report["validation_status"] == "REJECTED"


def test_schema_lists_allowed_taxonomy_items() -> None:
    schema = build_llm_graph_relationship_review_schema()
    rt_enum = schema["$defs"]["ReviewedEdge"]["properties"]["suggested_relationship_type"]["enum"]
    assert "DEADLINE_TOUCH_VS_POINT_IN_TIME" in rt_enum
    assert "TITLE_SIMILARITY_ONLY" in rt_enum
    assert "SAME_PAYOFF_CANDIDATE_REVIEW" in rt_enum
    action_enum = schema["$defs"]["ReviewedEdge"]["properties"]["suggested_action"]["enum"]
    for action in ALLOWED_LLM_ACTIONS:
        assert action in action_enum
    assert "PAPER_CANDIDATE" not in action_enum
