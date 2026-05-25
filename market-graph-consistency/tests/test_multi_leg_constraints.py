from __future__ import annotations

import json
import re
from copy import deepcopy

import pytest

from graph_engine.bounded_consistency import BoundedConstraint, BoundedMarket, build_bounded_consistency_report
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


def _nested_subset_snapshot() -> GraphSnapshot:
    return GraphSnapshot(
        snapshot_id="nested-subset-test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:city_supermajority": make_node(
                "test:city_supermajority",
                0.72,
                title="Measure passes by supermajority",
                canonical_text="Measure passes by supermajority.",
                observable="city_measure_result",
                settlement_source="fixture_election_board",
                window="2026-11-03",
            ),
            "test:city_passes": make_node(
                "test:city_passes",
                0.64,
                title="Measure passes",
                canonical_text="Measure passes.",
                observable="city_measure_result",
                settlement_source="fixture_election_board",
                window="2026-11-03",
            ),
            "test:city_vote_held": make_node(
                "test:city_vote_held",
                0.60,
                title="Measure vote held",
                canonical_text="Measure vote is held.",
                observable="city_measure_result",
                settlement_source="fixture_election_board",
                window="2026-11-03",
            ),
        },
        edges=[
            RelationshipEdge(
                edge_id="edge_supermajority_subset_passes",
                src_market_id="test:city_supermajority",
                dst_market_id="test:city_passes",
                relation=RelationshipType.SUBSET,
                confidence=0.91,
                source="manual",
                rationale="supermajority is narrower than passes",
                evidence=["fixture"],
                observable="city_measure_result",
                window="2026-11-03",
                created_at="2026-05-19T18:00:00+00:00",
            ),
            RelationshipEdge(
                edge_id="edge_passes_subset_vote_held",
                src_market_id="test:city_passes",
                dst_market_id="test:city_vote_held",
                relation=RelationshipType.SUBSET,
                confidence=0.88,
                source="manual",
                rationale="passing requires vote held",
                evidence=["fixture"],
                observable="city_measure_result",
                window="2026-11-03",
                created_at="2026-05-19T18:00:00+00:00",
            ),
        ],
    )


def _bounded_markets(values: dict[str, float | None]) -> dict[str, BoundedMarket]:
    return {
        market_id: BoundedMarket(market_id=market_id, probability=probability, confidence=0.9)
        for market_id, probability in values.items()
    }


def _bounded_first(values: dict[str, float | None], constraint: BoundedConstraint) -> dict:
    return build_bounded_consistency_report(_bounded_markets(values), [constraint])["bounded_consistency_diagnostics"][0]


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


def test_nested_subset_chain_constraint_bound_math() -> None:
    report = build_multi_leg_constraints_report(_nested_subset_snapshot())
    grouped = _by_type(report)
    chain = grouped["nested_subset_chain"][0]

    assert chain["constraint_family"] == "compound_bound"
    assert chain["market_count"] == 3
    assert chain["market_ids"] == ["test:city_supermajority", "test:city_passes", "test:city_vote_held"]
    assert chain["observed_value"] == 0.72
    assert chain["expected_lower_bound"] == 0.0
    assert chain["expected_upper_bound"] == 0.6
    assert chain["bound_gap"] == 0.05
    assert chain["normalized_bound_gap"] == 0.05
    assert chain["confidence_basis"]["score"] == 0.88
    assert chain["blockers"] == []
    assert chain["diagnostic_only"] is True
    assert chain["max_action_cap"] == "MANUAL_REVIEW"


def test_nested_subset_chain_with_basis_mismatch_is_watch_only() -> None:
    snapshot = _nested_subset_snapshot()
    snapshot.nodes["test:city_vote_held"].settlement_source = "different_fixture_source"

    report = build_multi_leg_constraints_report(snapshot)
    chain = _by_type(report)["nested_subset_chain"][0]

    assert chain["max_action_cap"] == "WATCH"
    assert "settlement_source_mismatch" in chain["blockers"]


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


@pytest.mark.parametrize("token", ["trade", "fill", "size", "paper"])
def test_multi_leg_contract_rejects_bare_prohibited_values(fixture_snapshot, token: str) -> None:
    report = build_multi_leg_constraints_report(fixture_snapshot)
    mutated = deepcopy(report)
    mutated["multi_leg_constraints"][0]["review_reason"] = token

    try:
        validate_multi_leg_constraints_contract(mutated)
    except SchemaValidationError:
        return
    raise AssertionError("bare prohibited multi-leg value should fail validation")


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
    for token in PROHIBITED_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", markdown, flags=re.IGNORECASE) is None


def test_bounded_consistency_no_violation() -> None:
    diagnostic = _bounded_first(
        {"test:a": 0.30, "test:b": 0.25, "test:c": 0.20},
        BoundedConstraint(
            constraint_id="bounded:no_violation",
            constraint_type="sum_upper",
            market_ids=["test:a", "test:b", "test:c"],
            upper_bound=1.0,
        ),
    )

    assert diagnostic["diagnostic_only"] is True
    assert diagnostic["violated"] is False
    assert diagnostic["observed_value"] == 0.75
    assert diagnostic["upper_bound"] == 1.0
    assert diagnostic["bound_gap"] == 0.0


def test_bounded_consistency_upper_bound_violation() -> None:
    diagnostic = _bounded_first(
        {"test:a": 0.50, "test:b": 0.40, "test:c": 0.20},
        BoundedConstraint(
            constraint_id="bounded:upper",
            constraint_type="sum_upper",
            market_ids=["test:a", "test:b", "test:c"],
            upper_bound=1.0,
        ),
    )

    assert diagnostic["violated"] is True
    assert diagnostic["observed_value"] == 1.1
    assert diagnostic["bound_gap"] == 0.07
    assert diagnostic["normalized_bound_gap"] == 0.07


def test_bounded_consistency_lower_bound_violation() -> None:
    diagnostic = _bounded_first(
        {"test:a": 0.20, "test:b": 0.15, "test:c": 0.10},
        BoundedConstraint(
            constraint_id="bounded:lower",
            constraint_type="sum_lower",
            market_ids=["test:a", "test:b", "test:c"],
            lower_bound=0.60,
        ),
    )

    assert diagnostic["violated"] is True
    assert diagnostic["observed_value"] == 0.45
    assert diagnostic["lower_bound"] == 0.60
    assert diagnostic["bound_gap"] == 0.12


def test_bounded_consistency_child_parent_violation() -> None:
    diagnostic = _bounded_first(
        {"test:child": 0.62, "test:parent": 0.54},
        BoundedConstraint(
            constraint_id="bounded:child_parent",
            constraint_type="child_parent",
            market_ids=["test:child", "test:parent"],
        ),
    )

    assert diagnostic["violated"] is True
    assert diagnostic["observed_value"] == 0.62
    assert diagnostic["upper_bound"] == 0.54
    assert diagnostic["bound_gap"] == 0.05


def test_bounded_consistency_ladder_violation() -> None:
    diagnostic = _bounded_first(
        {"test:strict": 0.72, "test:middle": 0.68, "test:loose": 0.60},
        BoundedConstraint(
            constraint_id="bounded:ladder",
            constraint_type="threshold_monotonicity",
            market_ids=["test:strict", "test:middle", "test:loose"],
        ),
    )

    assert diagnostic["violated"] is True
    assert diagnostic["observed_value"] == 0.72
    assert diagnostic["upper_bound"] == 0.60
    assert diagnostic["bound_gap"] == 0.05


def test_bounded_consistency_complement_violation() -> None:
    diagnostic = _bounded_first(
        {"test:yes": 0.65, "test:no": 0.43},
        BoundedConstraint(
            constraint_id="bounded:complement",
            constraint_type="complement_sum",
            market_ids=["test:yes", "test:no"],
        ),
    )

    assert diagnostic["violated"] is True
    assert diagnostic["observed_value"] == 1.08
    assert diagnostic["lower_bound"] == 0.97
    assert diagnostic["upper_bound"] == 1.03
    assert diagnostic["bound_gap"] == 0.05


def test_bounded_consistency_missing_or_ambiguous_data_blocks_output() -> None:
    markets = {
        "test:a": BoundedMarket("test:a", 0.40, confidence=0.9),
        "test:b": BoundedMarket("test:b", None, confidence=0.9, blockers=["ambiguous_probability"]),
        "test:c": BoundedMarket("test:c", 0.30, confidence=0.9),
    }
    report = build_bounded_consistency_report(
        markets,
        [
            BoundedConstraint(
                constraint_id="bounded:blocked",
                constraint_type="sum_upper",
                market_ids=["test:a", "test:b", "test:c"],
                upper_bound=1.0,
            )
        ],
    )
    diagnostic = report["bounded_consistency_diagnostics"][0]

    assert diagnostic["violated"] is False
    assert diagnostic["bound_gap"] == 0.0
    assert diagnostic["confidence_basis"]["score"] <= 0.25
    assert "missing_probability:test:b" in diagnostic["blockers"]
    assert "ambiguous_probability" in diagnostic["blockers"]


def test_bounded_consistency_uses_neutral_terminology_only() -> None:
    report = build_bounded_consistency_report(
        _bounded_markets({"test:a": 0.50, "test:b": 0.40, "test:c": 0.20}),
        [
            BoundedConstraint(
                constraint_id="bounded:neutral",
                constraint_type="sum_upper",
                market_ids=["test:a", "test:b", "test:c"],
                upper_bound=1.0,
            )
        ],
    )
    serialized = json.dumps(report).lower()

    assert report["diagnostic_only"] is True
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    for token in PROHIBITED_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", serialized, flags=re.IGNORECASE) is None
