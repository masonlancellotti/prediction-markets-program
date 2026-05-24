from __future__ import annotations

import json
import re
from copy import deepcopy

from graph_engine.models import GraphSnapshot, RelationshipEdge, RelationshipType
from graph_engine.reporting.json_report import PROHIBITED_VIOLATION_FIELDS, build_json_report
from graph_engine.reporting.md_report import build_markdown_report
from graph_engine.reporting.multi_leg import build_multi_leg_constraints_report
from graph_engine.reporting.schema_validation import SchemaValidationError, validate_multi_leg_constraints_contract
from tests.conftest import make_node


PROHIBITED_TOKENS = sorted(
    PROHIBITED_VIOLATION_FIELDS
    | {
        "PAPER_CANDIDATE",
        "POSSIBLE_ARB",
        "executable-arb",
        "fill-size",
        "trade-permission",
    }
)


def _by_type(report: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for constraint in report["multi_leg_constraints"]:
        grouped.setdefault(constraint["constraint_type"], []).append(constraint)
    return grouped


def test_multi_leg_fixture_constraints_export(fixture_snapshot) -> None:
    report = build_multi_leg_constraints_report(fixture_snapshot)
    grouped = _by_type(report)

    assert report["diagnostic_only"] is True
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert report["constraint_count"] >= 5
    assert {"exhaustive_group", "mutually_exclusive_group", "threshold_ladder", "range_bucket_partition", "complement_parent_child"} <= set(grouped)
    for constraint in report["multi_leg_constraints"]:
        assert constraint["diagnostic_only"] is True
        assert constraint["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"}
        assert constraint["diagnostic_priority"] in {"WATCH", "MANUAL_REVIEW"}
        assert constraint["constraint_violation"] is True
        assert constraint["structural_inconsistency"] is True
        assert constraint["bound_gap"] > 0
        assert constraint["constraint_family"]
        assert constraint["market_count"] == len(constraint["market_ids"])
        assert constraint["market_count"] >= 3
        assert constraint["observed_value"] > constraint["expected_upper_bound"] or constraint["constraint_type"] == "threshold_ladder"
        assert constraint["expected_lower_bound"] <= constraint["expected_upper_bound"]
        assert constraint["normalized_bound_gap"] > 0
        assert isinstance(constraint["confidence_basis"]["description"], str)
        assert 0 <= constraint["confidence_basis"]["score"] <= 1
        assert constraint["required_review_questions"]
        assert isinstance(constraint["blockers"], list)


def test_multi_leg_constraints_are_in_json_report(fixture_snapshot) -> None:
    report = build_json_report(fixture_snapshot, [])

    assert report["diagnostic_only"] is True
    assert report["summary"]["multi_leg_constraint_count"] == report["multi_leg_constraints"]["constraint_count"]
    assert report["multi_leg_constraints"]["multi_leg_constraints"]


def test_multi_leg_contract_rejects_prohibited_field() -> None:
    report = {
        "diagnostic_only": True,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "constraint_count": 1,
        "multi_leg_constraints": [
            {
                "constraint_id": "bad",
                "constraint_type": "threshold_ladder",
                "constraint_family": "ordered_thresholds",
                "market_ids": ["test:a", "test:b", "test:c"],
                "market_count": 3,
                "diagnostic_only": True,
                "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
                "max_action_cap": "WATCH",
                "diagnostic_priority": "WATCH",
                "constraint_violation": True,
                "structural_inconsistency": True,
                "bound_gap": 0.1,
                "normalized_bound_gap": 0.1,
                "observed_value": 0.9,
                "expected_lower_bound": 0.0,
                "expected_upper_bound": 0.8,
                "expected_bound": 0.8,
                "confidence_basis": {"description": "fixture", "score": 0.5},
                "required_review_questions": ["review fixture"],
                "blockers": [],
                "review_reason": "fixture",
                "profit_usd": 1,
            }
        ],
    }

    try:
        validate_multi_leg_constraints_contract(report)
    except SchemaValidationError:
        return
    raise AssertionError("prohibited multi-leg field should fail validation")


def test_multi_leg_contract_rejects_prohibited_value(fixture_snapshot) -> None:
    report = build_multi_leg_constraints_report(fixture_snapshot)
    mutated = deepcopy(report)
    mutated["multi_leg_constraints"][0]["review_reason"] = "POSSIBLE" + "_" + "ARB"

    try:
        validate_multi_leg_constraints_contract(mutated)
    except SchemaValidationError:
        return
    raise AssertionError("prohibited multi-leg value should fail validation")


def test_two_leg_simple_pair_does_not_create_paperable_output() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:a": make_node("test:a", 0.55),
            "test:b": make_node("test:b", 0.50),
        },
        edges=[
            RelationshipEdge(
                edge_id="edge_two_leg_complement",
                src_market_id="test:a",
                dst_market_id="test:b",
                relation=RelationshipType.COMPLEMENT,
                confidence=0.9,
                source="manual",
                rationale="two leg fixture",
                evidence=["fixture"],
                created_at="2026-05-19T18:00:00+00:00",
            )
        ],
    )

    report = build_multi_leg_constraints_report(snapshot)
    serialized = json.dumps(report).lower()

    assert report["multi_leg_constraints"] == []
    for token in PROHIBITED_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", serialized, flags=re.IGNORECASE) is None


def test_multi_leg_ranking_is_stable_by_normalized_gap_and_confidence(fixture_snapshot) -> None:
    report = build_multi_leg_constraints_report(fixture_snapshot)
    constraints = report["multi_leg_constraints"]
    expected = sorted(
        constraints,
        key=lambda item: (-item["normalized_bound_gap"], -item["confidence_basis"]["score"], item["constraint_id"]),
    )

    assert constraints == expected
    assert [item["diagnostic_rank"] for item in constraints] == list(range(1, len(constraints) + 1))


def test_multi_leg_bound_math_for_fixture_examples(fixture_snapshot) -> None:
    report = build_multi_leg_constraints_report(fixture_snapshot)
    by_type = _by_type(report)

    exhaustive = by_type["exhaustive_group"][0]
    assert exhaustive["observed_value"] == 1.26
    assert exhaustive["expected_upper_bound"] == 1.0
    assert exhaustive["bound_gap"] == 0.23
    assert exhaustive["normalized_bound_gap"] == 0.23

    threshold = by_type["threshold_ladder"][0]
    assert threshold["observed_value"] == 0.74
    assert threshold["expected_upper_bound"] == 0.5
    assert threshold["bound_gap"] == 0.17
    assert threshold["normalized_bound_gap"] == 0.17


def test_multi_leg_output_uses_neutral_terminology_only(fixture_snapshot) -> None:
    report = build_multi_leg_constraints_report(fixture_snapshot)
    serialized = json.dumps(report).lower()

    for token in PROHIBITED_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", serialized, flags=re.IGNORECASE) is None
    assert "diagnostic_priority" in serialized
    assert "diagnostic_rank" in serialized


def test_generated_markdown_contains_multi_leg_review_questions(fixture_snapshot) -> None:
    markdown = build_markdown_report(fixture_snapshot, [])

    assert "## Multi-Leg Constraints" in markdown
    assert "- Required review questions:" in markdown
    assert "Do all ladder markets share the same settlement source and window?" in markdown
