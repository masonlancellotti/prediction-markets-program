from __future__ import annotations

from datetime import datetime, timezone

import pytest

from graph_engine.loader import load_fixture_markets
from graph_engine.models import ExclusionSet, GraphSnapshot, RelationshipEdge, RelationshipType
from graph_engine.relationships.registry import load_relationship_registry
from graph_engine.reporting.probability_constraints import (
    build_probability_constraints_report,
    validate_probability_constraints_report,
    write_probability_constraints_report,
)
from tests.conftest import PROJECT_ROOT, make_node


def _edge(relation: RelationshipType, src: str, dst: str, *, confidence: float = 0.95) -> RelationshipEdge:
    return RelationshipEdge(
        edge_id=f"edge_{src.replace(':', '_')}_{dst.replace(':', '_')}",
        src_market_id=src,
        dst_market_id=dst,
        relation=relation,
        confidence=confidence,
        source="manual",
        rationale="test relationship",
        evidence=["fixture"],
        created_at="2026-05-19T18:00:00+00:00",
        reviewed_by="fixture-reviewer",
        observable="TEST",
        window="2026-12-31",
    )


def _snapshot(nodes, edges=None, exclusion_sets=None, *, as_of="2026-05-19T18:00:00+00:00") -> GraphSnapshot:
    return GraphSnapshot(
        snapshot_id="probability-constraint-test",
        as_of=as_of,
        nodes={node.market_id: node for node in nodes},
        edges=list(edges or []),
        exclusion_sets=list(exclusion_sets or []),
    )


def test_subset_violation_is_detected() -> None:
    subset = make_node("test:subset", 0.72, bid=0.70, ask=0.74)
    superset = make_node("test:superset", 0.50, bid=0.48, ask=0.52)
    snapshot = _snapshot([subset, superset], [_edge(RelationshipType.SUBSET, "test:subset", "test:superset")])

    report = build_probability_constraints_report(snapshot)
    row = next(item for item in report["probability_constraints"] if item["constraint_type"] == "subset_superset")

    assert row["violated"] is True
    assert row["raw_sum_or_difference"] == pytest.approx(0.22)
    assert row["observed_gap"] == pytest.approx(0.19)
    assert row["violation_amount_after_tolerance"] == pytest.approx(0.19)
    assert row["expected_bound"]["upper"] == 0.0
    assert row["tolerance"] == pytest.approx(0.03)
    assert row["inequality_checked"] == "P(subset) <= P(superset)"
    assert row["implied_review_direction"] == "SUBSET_HIGH_RELATIVE_TO_SUPERSET"


def test_subset_non_violation_is_not_flagged() -> None:
    subset = make_node("test:subset", 0.30, bid=0.28, ask=0.32)
    superset = make_node("test:superset", 0.50, bid=0.48, ask=0.52)
    snapshot = _snapshot([subset, superset], [_edge(RelationshipType.SUBSET, "test:subset", "test:superset")])

    report = build_probability_constraints_report(snapshot)
    row = next(item for item in report["probability_constraints"] if item["constraint_type"] == "subset_superset")

    assert row["violated"] is False
    assert row["observed_gap"] == 0
    assert row["violation_amount_after_tolerance"] == 0
    assert row["severity_score"] == 0


def test_complement_divergence_is_detected() -> None:
    yes = make_node("test:yes", 0.70, bid=0.68, ask=0.72)
    no = make_node("test:no", 0.45, bid=0.43, ask=0.47)
    snapshot = _snapshot([yes, no], [_edge(RelationshipType.COMPLEMENT, "test:yes", "test:no")])

    report = build_probability_constraints_report(snapshot)
    row = next(item for item in report["probability_constraints"] if item["constraint_type"] == "complement_pair")

    assert row["violated"] is True
    assert row["observed_value"] == 1.15
    assert row["raw_sum_or_difference"] == pytest.approx(1.15)
    assert row["observed_gap"] == pytest.approx(0.12)
    assert row["gap_formula"] == "max(0, abs(sum(probability_i) - 1) - tolerance)"
    assert row["expected_bound"]["type"] == "exact"
    assert row["expected_bound"]["tolerance_adjusted_upper"] == pytest.approx(1.03)
    assert row["inequality_checked"] == "P(A) + P(not A) = 1"


def test_yes_price_equal_to_midpoint_is_flagged_and_marks_constraint_synthetic() -> None:
    # Both nodes have yes_price exactly equal to (bid+ask)/2 — the fixture
    # author synthesized yes_price from the spread, so the diagnostic should
    # mark the input as non_actionable and the constraint as midpoint-only.
    subset = make_node("test:subset", 0.72, bid=0.70, ask=0.74)
    superset = make_node("test:superset", 0.50, bid=0.48, ask=0.52)
    snapshot = _snapshot([subset, superset], [_edge(RelationshipType.SUBSET, "test:subset", "test:superset")])

    report = build_probability_constraints_report(snapshot)
    row = next(item for item in report["probability_constraints"] if item["constraint_type"] == "subset_superset")

    assert all(item["yes_price_equals_midpoint"] is True for item in row["probability_inputs"])
    assert all(item["non_actionable_input"] is True for item in row["probability_inputs"])
    assert row["uses_yes_price_equal_to_midpoint"] is True
    assert row["midpoint_only"] is True
    assert report["summary"]["yes_price_equal_to_midpoint_count"] >= 1
    validate_probability_constraints_report(report)


def test_yes_price_differs_from_midpoint_is_not_flagged_as_synthetic() -> None:
    # When yes_price is meaningfully different from (bid+ask)/2 the input is
    # treated as a real diagnostic probability and not flagged as synthetic.
    subset = make_node("test:subset", 0.72, bid=0.69, ask=0.74)
    superset = make_node("test:superset", 0.50, bid=0.48, ask=0.52)
    snapshot = _snapshot([subset, superset], [_edge(RelationshipType.SUBSET, "test:subset", "test:superset")])

    report = build_probability_constraints_report(snapshot)
    row = next(item for item in report["probability_constraints"] if item["constraint_type"] == "subset_superset")

    subset_input = next(item for item in row["probability_inputs"] if item["market_id"] == "test:subset")
    superset_input = next(item for item in row["probability_inputs"] if item["market_id"] == "test:superset")

    assert subset_input["yes_price_equals_midpoint"] is False  # 0.72 != midpoint(0.69, 0.74) = 0.715
    assert superset_input["yes_price_equals_midpoint"] is True  # 0.50 == midpoint(0.48, 0.52)
    # Mixed inputs → midpoint_only stays False (since one input is real)
    assert row["midpoint_only"] is False
    validate_probability_constraints_report(report)


def test_complement_under_sum_gap_is_after_tolerance() -> None:
    yes = make_node("test:yes", 0.40, bid=0.38, ask=0.42)
    no = make_node("test:no", 0.45, bid=0.43, ask=0.47)
    snapshot = _snapshot([yes, no], [_edge(RelationshipType.COMPLEMENT, "test:yes", "test:no")])

    report = build_probability_constraints_report(snapshot)
    row = next(item for item in report["probability_constraints"] if item["constraint_type"] == "complement_pair")

    assert row["violated"] is True
    assert row["raw_sum_or_difference"] == pytest.approx(0.85)
    assert row["observed_gap"] == pytest.approx(0.12)
    assert row["violation_amount_after_tolerance"] == pytest.approx(0.12)


def test_threshold_ladder_inversion_is_detected() -> None:
    strict = make_node(
        "test:above_140",
        0.74,
        bid=0.72,
        ask=0.76,
        title="Metric above 140",
        canonical_text="Metric is above 140 by the window.",
        themes=["threshold"],
        observable="TEST_METRIC",
        settlement_source="fixture_metric",
        window="2026-12-31",
    )
    middle = make_node(
        "test:above_120",
        0.64,
        bid=0.62,
        ask=0.66,
        title="Metric above 120",
        canonical_text="Metric is above 120 by the window.",
        themes=["threshold"],
        observable="TEST_METRIC",
        settlement_source="fixture_metric",
        window="2026-12-31",
    )
    loose = make_node(
        "test:above_100",
        0.50,
        bid=0.48,
        ask=0.52,
        title="Metric above 100",
        canonical_text="Metric is above 100 by the window.",
        themes=["threshold"],
        observable="TEST_METRIC",
        settlement_source="fixture_metric",
        window="2026-12-31",
    )
    snapshot = _snapshot([strict, middle, loose])

    report = build_probability_constraints_report(snapshot)
    row = next(item for item in report["probability_constraints"] if item["constraint_type"] == "threshold_ladder")

    assert row["violated"] is True
    assert row["raw_sum_or_difference"] == pytest.approx(0.14)
    assert row["observed_gap"] == pytest.approx(0.11)
    assert row["expected_bound"]["upper"] == 0.0
    assert row["gap_formula"] == "max(0, max(P(stricter_threshold) - P(looser_threshold)) - tolerance)"
    assert row["implied_review_direction"] == "STRICTER_THRESHOLD_HIGH_RELATIVE_TO_LOOSER"
    assert row["market_formulas"]
    assert set(row["market_formulas"][0]) == {
        "market_id",
        "family",
        "asset",
        "source",
        "date",
        "window",
        "comparator",
        "threshold",
        "unit",
    }
    assert all(item["family"] != "BTC_THRESHOLD" for item in row["market_formulas"])


def test_consistent_btc_thresholds_use_typed_ladder_keys() -> None:
    nodes = [
        make_node(
            "test:btc_above_140k",
            0.74,
            bid=0.72,
            ask=0.76,
            title="BTC above 140k by June 30",
            canonical_text="Bitcoin is above 140000 USD by 2026-06-30.",
            themes=["threshold"],
            observable="BTC",
            settlement_source="fixture_btc_index",
            window="2026-06-30",
        ),
        make_node(
            "test:btc_above_120k",
            0.64,
            bid=0.62,
            ask=0.66,
            title="BTC above 120k by June 30",
            canonical_text="Bitcoin is above 120000 USD by 2026-06-30.",
            themes=["threshold"],
            observable="BTC",
            settlement_source="fixture_btc_index",
            window="2026-06-30",
        ),
        make_node(
            "test:btc_above_100k",
            0.50,
            bid=0.48,
            ask=0.52,
            title="BTC above 100k by June 30",
            canonical_text="Bitcoin is above 100000 USD by 2026-06-30.",
            themes=["threshold"],
            observable="BTC",
            settlement_source="fixture_btc_index",
            window="2026-06-30",
        ),
    ]

    report = build_probability_constraints_report(_snapshot(nodes))
    row = next(item for item in report["probability_constraints"] if item["constraint_type"] == "threshold_ladder")

    assert row["violated"] is True
    assert row["markets_involved"] == ["test:btc_above_140k", "test:btc_above_120k", "test:btc_above_100k"]
    assert all(item["family"] == "BTC_THRESHOLD" for item in row["market_formulas"])
    assert all(item["asset"] == "BTC" for item in row["market_formulas"])
    assert "mixed_threshold_comparators" not in row["review_blockers"]
    assert "mixed_or_missing_threshold_units" not in row["review_blockers"]
    assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]


def test_mixed_threshold_comparators_block_ladder_without_violation() -> None:
    nodes = [
        make_node(
            "test:btc_above_100k",
            0.50,
            bid=0.48,
            ask=0.52,
            title="BTC above 100k by June 30",
            canonical_text="Bitcoin is above 100000 USD by 2026-06-30.",
            themes=["threshold"],
            observable="BTC",
            settlement_source="fixture_btc_index",
            window="2026-06-30",
        ),
        make_node(
            "test:btc_above_120k",
            0.64,
            bid=0.62,
            ask=0.66,
            title="BTC above 120k by June 30",
            canonical_text="Bitcoin is above 120000 USD by 2026-06-30.",
            themes=["threshold"],
            observable="BTC",
            settlement_source="fixture_btc_index",
            window="2026-06-30",
        ),
        make_node(
            "test:btc_at_or_below_140k",
            0.74,
            bid=0.72,
            ask=0.76,
            title="BTC at or below 140k by June 30",
            canonical_text="Bitcoin is at or below 140000 USD by 2026-06-30.",
            themes=["threshold"],
            observable="BTC",
            settlement_source="fixture_btc_index",
            window="2026-06-30",
        ),
    ]

    report = build_probability_constraints_report(_snapshot(nodes))
    row = next(item for item in report["probability_constraints"] if item["constraint_type"] == "threshold_ladder")

    assert row["violated"] is False
    assert row["observed_gap"] == 0
    assert "mixed_threshold_comparators" in row["review_blockers"]
    assert row["implied_review_direction"] == "NO_REVIEW_DIRECTION"


def test_mixed_or_missing_threshold_units_block_ladder_without_violation() -> None:
    nodes = [
        make_node(
            "test:index_above_100",
            0.50,
            bid=0.48,
            ask=0.52,
            title="Custom index above 100",
            canonical_text="Custom index is above 100.",
            themes=["threshold"],
            observable="CUSTOM_INDEX",
            settlement_source="fixture_custom_index",
            window="2026-06-30",
        ),
        make_node(
            "test:index_above_120",
            0.64,
            bid=0.62,
            ask=0.66,
            title="Custom index above 120",
            canonical_text="Custom index is above 120.",
            themes=["threshold"],
            observable="CUSTOM_INDEX",
            settlement_source="fixture_custom_index",
            window="2026-06-30",
        ),
        make_node(
            "test:index_above_140k",
            0.74,
            bid=0.72,
            ask=0.76,
            title="Custom index above 140k",
            canonical_text="Custom index is above 140k.",
            themes=["threshold"],
            observable="CUSTOM_INDEX",
            settlement_source="fixture_custom_index",
            window="2026-06-30",
        ),
    ]

    report = build_probability_constraints_report(_snapshot(nodes))
    row = next(item for item in report["probability_constraints"] if item["constraint_type"] == "threshold_ladder")

    assert row["violated"] is False
    assert row["observed_gap"] == 0
    assert "mixed_or_missing_threshold_units" in row["review_blockers"]
    assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]


def test_exhaustive_partition_only_runs_with_explicit_completeness() -> None:
    nodes = [
        make_node("test:a", 0.40, bid=0.38, ask=0.42),
        make_node("test:b", 0.35, bid=0.33, ask=0.37),
        make_node("test:c", 0.20, bid=0.18, ask=0.22),
    ]
    subset_exclusion = ExclusionSet(
        set_id="mutex_only",
        member_market_ids=[node.market_id for node in nodes],
        completeness="subset",
        tolerance=0.03,
    )
    partition_exclusion = ExclusionSet(
        set_id="complete_partition",
        member_market_ids=[node.market_id for node in nodes],
        completeness="partition",
        tolerance=0.03,
    )

    subset_report = build_probability_constraints_report(_snapshot(nodes, exclusion_sets=[subset_exclusion]))
    partition_report = build_probability_constraints_report(_snapshot(nodes, exclusion_sets=[partition_exclusion]))
    partition_row = next(
        row for row in partition_report["probability_constraints"] if row["constraint_type"] == "exhaustive_partition"
    )

    assert not any(row["constraint_type"] == "exhaustive_partition" for row in subset_report["probability_constraints"])
    assert any(row["constraint_type"] == "mutually_exclusive_group" for row in subset_report["probability_constraints"])
    assert partition_row["explicit_partition_evidence"] is True
    assert partition_row["raw_sum_or_difference"] == pytest.approx(0.95)
    assert partition_row["tolerance"] == pytest.approx(0.03)
    assert partition_row["observed_gap"] == pytest.approx(0.02)


def test_midpoint_only_constraints_are_marked_non_actionable() -> None:
    subset = make_node("test:subset", 0.50, bid=0.70, ask=0.74)
    subset.yes_price = None
    subset.no_price = None
    superset = make_node("test:superset", 0.50, bid=0.48, ask=0.52)
    superset.yes_price = None
    superset.no_price = None
    snapshot = _snapshot([subset, superset], [_edge(RelationshipType.SUBSET, "test:subset", "test:superset")])

    report = build_probability_constraints_report(snapshot)
    row = next(item for item in report["probability_constraints"] if item["constraint_type"] == "subset_superset")

    assert row["violated"] is True
    assert row["midpoint_only"] is True
    assert row["uses_diagnostic_midpoint"] is True
    assert all(item["non_actionable_input"] is True for item in row["probability_inputs"])
    assert all(item["probability_source"] == "diagnostic_midpoint" for item in row["probability_inputs"])
    assert all(item["diagnostic_midpoint_used"] is True for item in row["probability_inputs"])
    assert [item["midpoint"] for item in row["probability_inputs"]] == [pytest.approx(0.72), pytest.approx(0.50)]
    assert "diagnostic_midpoint_not_actionable" in row["review_blockers"]
    assert row["confidence_tier"] == "LOW"
    assert report["summary"]["midpoint_only_count"] == 1


def test_stale_and_missing_quote_inputs_lower_confidence_and_add_blockers() -> None:
    old_time = datetime(2026, 5, 17, 18, 0, tzinfo=timezone.utc)
    subset = make_node("test:subset", 0.72, bid=None, ask=None, as_of=old_time)
    superset = make_node("test:superset", 0.50, bid=None, ask=None, as_of=old_time)
    snapshot = _snapshot(
        [subset, superset],
        [_edge(RelationshipType.SUBSET, "test:subset", "test:superset")],
        as_of="2026-05-19T18:00:00+00:00",
    )

    report = build_probability_constraints_report(snapshot)
    row = next(item for item in report["probability_constraints"] if item["constraint_type"] == "subset_superset")

    assert row["violated"] is True
    assert row["confidence_tier"] == "LOW"
    assert row["has_stale_or_missing_quote"] is True
    assert "stale_quote" in row["review_blockers"]
    assert "missing_bid_or_ask" in row["review_blockers"]
    assert report["summary"]["stale_or_missing_quote_count"] == 1


def test_outputs_remain_diagnostic_only_with_capped_actions(fixture_snapshot) -> None:
    report = build_probability_constraints_report(fixture_snapshot)

    validate_probability_constraints_report(report)
    assert report["diagnostic_only"] is True
    assert report["affects_evaluator_gates"] is False
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert report["probability_constraints"]
    for row in report["probability_constraints"]:
        assert row["diagnostic_only"] is True
        assert row["affects_evaluator_gates"] is False
        assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
        assert row["observed_gap"] == row["violation_amount_after_tolerance"]
        assert row["gap_formula"]
        assert "eligible_for_payoff_state_feasibility" in row
        assert "payoff_state_blockers" in row
        assert row["constraint_type"] in {
            "complement_pair",
            "subset_superset",
            "threshold_ladder",
            "mutually_exclusive_group",
            "exhaustive_partition",
            "range_bucket_partition",
        }


def test_fixture_report_contains_range_bucket_partition() -> None:
    snapshot, _ = load_fixture_markets(PROJECT_ROOT / "venues" / "fixtures")
    registry = load_relationship_registry(PROJECT_ROOT / "relationships", set(snapshot.nodes))
    snapshot.edges = registry.edges
    snapshot.exclusion_sets = registry.exclusion_sets

    report = build_probability_constraints_report(snapshot)

    assert any(row["constraint_type"] == "range_bucket_partition" for row in report["probability_constraints"])
    election = next(
        row
        for row in report["probability_constraints"]
        if row["constraint_id"] == "probability:exhaustive_partition:example_election_complete_partition"
    )
    assert election["raw_sum_or_difference"] == pytest.approx(1.26)
    assert election["tolerance"] == pytest.approx(0.03)
    assert election["observed_gap"] == pytest.approx(0.23)
    assert election["violation_amount_after_tolerance"] == pytest.approx(0.23)
    assert election["gap_formula"] == "max(0, abs(sum(probability_i) - 1) - tolerance)"
    assert report["summary"]["explicit_partition_count"] >= 2


def test_payoff_state_bridge_fields_are_review_only() -> None:
    states = [
        {"state_id": "subset", "state_description": "Subset state occurs"},
        {"state_id": "other", "state_description": "Other state occurs"},
    ]
    subset = make_node(
        "test:subset",
        0.30,
        bid=0.28,
        ask=0.32,
        raw={
            "normalized_row": {
                "payoff_state_family_id": "payoff_test_family",
                "payoff_state_family_type": "child_parent_chain",
                "payoff_state_states": states,
                "payoff_state_payoffs": {"subset": 1, "other": 0},
            }
        },
    )
    superset = make_node(
        "test:superset",
        0.50,
        bid=0.48,
        ask=0.52,
        raw={
            "normalized_row": {
                "payoff_state_family_id": "payoff_test_family",
                "payoff_state_family_type": "child_parent_chain",
                "payoff_state_states": states,
                "payoff_state_payoffs": {"subset": 1, "other": 1},
            }
        },
    )
    snapshot = _snapshot([subset, superset], [_edge(RelationshipType.SUBSET, "test:subset", "test:superset")])

    report = build_probability_constraints_report(snapshot)
    row = next(item for item in report["probability_constraints"] if item["constraint_type"] == "subset_superset")

    assert row["state_family_id"] == "payoff_test_family"
    assert row["eligible_for_payoff_state_feasibility"] is True
    assert row["payoff_state_blockers"] == []
    assert row["diagnostic_only"] is True
    assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert row["affects_evaluator_gates"] is False


def test_payoff_state_bridge_reports_missing_state_definitions() -> None:
    subset = make_node(
        "test:subset",
        0.30,
        bid=0.28,
        ask=0.32,
        raw={"normalized_row": {"payoff_state_family_id": "payoff_test_family"}},
    )
    superset = make_node(
        "test:superset",
        0.50,
        bid=0.48,
        ask=0.52,
        raw={"normalized_row": {"payoff_state_family_id": "payoff_test_family"}},
    )
    snapshot = _snapshot([subset, superset], [_edge(RelationshipType.SUBSET, "test:subset", "test:superset")])

    report = build_probability_constraints_report(snapshot)
    row = next(item for item in report["probability_constraints"] if item["constraint_type"] == "subset_superset")

    assert row["state_family_id"] == "payoff_test_family"
    assert row["eligible_for_payoff_state_feasibility"] is False
    assert "missing_state_definitions" in row["payoff_state_blockers"]


def test_probability_constraints_report_validates_before_writing(fixture_snapshot, tmp_path) -> None:
    output = tmp_path / "market_graph_probability_constraints.json"

    report = write_probability_constraints_report(fixture_snapshot, output)

    assert output.exists()
    validate_probability_constraints_report(report)
