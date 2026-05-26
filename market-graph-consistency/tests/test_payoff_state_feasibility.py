from __future__ import annotations

import json

import pytest

import scan
from graph_engine.models import GraphSnapshot, RelationshipEdge, RelationshipType
from graph_engine.payoff_state import ContractPayoff, FiniteState, PayoffMatrix
from graph_engine.payoff_state_feasibility import check_no_arb_consistency
from graph_engine.reporting.payoff_state_feasibility_bridge import (
    FEASIBILITY_BRIDGE_STATUSES,
    build_payoff_state_feasibility_bridge_report,
    validate_payoff_state_feasibility_bridge_report,
)
from graph_engine.reporting.schema_validation import SchemaValidationError
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


def _snapshot(nodes, edges=None) -> GraphSnapshot:
    return GraphSnapshot(
        snapshot_id="payoff-state-bridge-test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={node.market_id: node for node in nodes},
        edges=list(edges or []),
        exclusion_sets=[],
    )


def _family_raw(family_id: str, state_id: str, family_type: str = "exhaustive_group") -> dict:
    states = [
        {
            "state_id": "left",
            "state_description": "Left state occurs",
            "exhaustive_membership": True,
            "mutual_exclusion_membership": True,
        },
        {
            "state_id": "right",
            "state_description": "Right state occurs",
            "exhaustive_membership": True,
            "mutual_exclusion_membership": True,
        },
    ]
    return {
        "payoff_state_family_id": family_id,
        "payoff_state_family_type": family_type,
        "payoff_state_family_description": "Two-state fixture family.",
        "payoff_state_states": states,
        "payoff_state_payoffs": {
            "left": 1 if state_id == "left" else 0,
            "right": 1 if state_id == "right" else 0,
        },
        "payoff_state_required_evidence_fields": ["settlement_source", "settlement_window", "fixture_rules"],
    }


def _by_family(report: dict) -> dict[str, dict]:
    return {
        row["state_family_id"]: row
        for row in report["payoff_state_feasibility_bridge"]
        if row["state_family_id"]
    }


def test_missing_state_family_summary_rolls_up_blocked_rows(fixture_snapshot) -> None:
    # The bridge surfaces probability constraints that have no fixture-declared
    # state family as BLOCKED_MISSING_STATE_FAMILY rows. The ops-status surface
    # needs a single roll-up so operators can see the queue length at a glance
    # without scanning every row.
    report = build_payoff_state_feasibility_bridge_report(fixture_snapshot)
    summary = report["missing_state_family_summary"]
    blocked = [
        row
        for row in report["payoff_state_feasibility_bridge"]
        if row["feasibility_status"] == "BLOCKED_MISSING_STATE_FAMILY"
    ]

    assert summary["row_count"] == len(blocked)
    assert summary["diagnostic_only"] is True
    assert summary["affects_evaluator_gates"] is False
    if blocked:
        assert summary["next_step"] == "ADD_FIXTURE_STATE_FAMILY_FOR_CONSTRAINT"
        # The rolled-up market list should be the union of all blocked rows'
        # markets (without duplicates) and the constraint types should match
        # the per-row "constraint_types_represented" union.
        expected_markets = sorted({m for row in blocked for m in row["markets_involved"]})
        assert summary["unique_markets_involved"] == expected_markets


def test_feasible_family_reports_feasible(fixture_snapshot) -> None:
    row = _by_family(build_payoff_state_feasibility_bridge_report(fixture_snapshot))["payoff_feasible_exhaustive"]

    assert row["feasibility_status"] == "FEASIBLE"
    assert row["infeasibility_gap"] == 0.0
    assert row["minimal_repair_estimate"] == 0.0
    assert row["diagnostic_only"] is True
    assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]


def test_infeasible_family_reports_diagnostic_infeasible(fixture_snapshot) -> None:
    row = _by_family(build_payoff_state_feasibility_bridge_report(fixture_snapshot))["payoff_infeasible_exhaustive"]

    assert row["feasibility_status"] == "INFEASIBLE_DIAGNOSTIC"
    assert row["infeasibility_gap"] > 0
    assert row["minimal_repair_estimate"] == row["infeasibility_gap"]
    assert row["per_contract_repair"]
    assert set(row["per_contract_repair"]).issubset(set(row["markets_involved"]))
    assert max(row["per_contract_repair"].values()) > 0
    assert "finite_state_feasibility" in row["violated_constraints"]


def test_interval_family_inside_bid_ask_bounds_reports_feasible() -> None:
    left = make_node(
        "test:left",
        0.50,
        bid=0.39,
        ask=0.41,
        settlement_source="fixture_source",
        raw=_family_raw("payoff_interval_feasible", "left"),
    )
    right = make_node(
        "test:right",
        0.50,
        bid=0.59,
        ask=0.61,
        settlement_source="fixture_source",
        raw=_family_raw("payoff_interval_feasible", "right"),
    )
    row = _by_family(build_payoff_state_feasibility_bridge_report(_snapshot([left, right])))[
        "payoff_interval_feasible"
    ]

    assert row["feasibility_status"] == "FEASIBLE"
    assert row["probability_input_mode"] == "BID_ASK_INTERVAL"
    assert row["bound_gap_semantics"] == "sum_of_two_sided_bid_ask_interval_repair_after_tolerance"
    assert row["infeasibility_gap"] == 0.0
    assert all(item["probability_input_mode"] == "BID_ASK_INTERVAL" for item in row["probability_inputs_used"])


def test_direct_contract_bounds_infer_interval_mode() -> None:
    states = [
        FiniteState(
            state_id="left",
            state_description="Left state occurs",
            family_id="direct_interval_family",
            exhaustive_membership=True,
            mutual_exclusion_membership=True,
        ),
        FiniteState(
            state_id="right",
            state_description="Right state occurs",
            family_id="direct_interval_family",
            exhaustive_membership=True,
            mutual_exclusion_membership=True,
        ),
    ]
    contracts = [
        ContractPayoff(
            contract_id="direct:left",
            family_id="direct_interval_family",
            payoff_by_state={"left": 1.0, "right": 0.0},
            required_evidence_fields=["fixture_rules"],
            observed_probability=0.50,
            bid_bound=0.39,
            ask_bound=0.41,
        ),
        ContractPayoff(
            contract_id="direct:right",
            family_id="direct_interval_family",
            payoff_by_state={"left": 0.0, "right": 1.0},
            required_evidence_fields=["fixture_rules"],
            observed_probability=0.50,
            bid_bound=0.59,
            ask_bound=0.61,
        ),
    ]
    matrix = PayoffMatrix(
        family_id="direct_interval_family",
        family_type="exhaustive_group",
        family_description="Direct interval fixture family.",
        states=states,
        contracts=contracts,
        structural_metadata={},
        blockers=[],
        confidence_basis={"basis": "fixture"},
    )

    result = check_no_arb_consistency(matrix)

    assert result.feasibility_status == "feasible"
    assert result.probability_input_mode == "BID_ASK_INTERVAL"
    assert result.bound_gap_semantics == "sum_of_two_sided_bid_ask_interval_repair_after_tolerance"
    assert result.bound_gap == 0.0
    assert result.blockers == []


def test_direct_infeasible_result_exposes_per_contract_repair() -> None:
    states = [
        FiniteState(
            state_id="only",
            state_description="Only state occurs",
            family_id="direct_repair_family",
            exhaustive_membership=True,
            mutual_exclusion_membership=True,
        )
    ]
    contracts = [
        ContractPayoff(
            contract_id="direct:only",
            family_id="direct_repair_family",
            payoff_by_state={"only": 1.0},
            required_evidence_fields=["fixture_rules"],
            observed_probability=0.40,
        )
    ]
    matrix = PayoffMatrix(
        family_id="direct_repair_family",
        family_type="exhaustive_group",
        family_description="Direct repair fixture family.",
        states=states,
        contracts=contracts,
        structural_metadata={},
        blockers=[],
        confidence_basis={"basis": "fixture"},
    )

    result = check_no_arb_consistency(matrix)

    assert result.feasibility_status == "infeasible"
    assert result.per_contract_repair["direct:only"] == pytest.approx(result.bound_gap)


def test_interval_family_outside_bid_ask_bounds_reports_infeasible_gap() -> None:
    left = make_node(
        "test:left",
        0.50,
        bid=0.72,
        ask=0.74,
        settlement_source="fixture_source",
        raw=_family_raw("payoff_interval_infeasible", "left"),
    )
    right = make_node(
        "test:right",
        0.50,
        bid=0.72,
        ask=0.74,
        settlement_source="fixture_source",
        raw=_family_raw("payoff_interval_infeasible", "right"),
    )
    row = _by_family(build_payoff_state_feasibility_bridge_report(_snapshot([left, right])))[
        "payoff_interval_infeasible"
    ]

    assert row["feasibility_status"] == "INFEASIBLE_DIAGNOSTIC"
    assert row["probability_input_mode"] == "BID_ASK_INTERVAL"
    assert row["infeasibility_gap"] > 0
    assert row["minimal_repair_estimate"] == row["infeasibility_gap"]
    assert row["per_contract_repair"]
    assert max(row["per_contract_repair"].values()) > 0
    assert "exhaustive_sum_bound" in row["violated_constraints"]


def test_missing_state_family_probability_constraint_blocks(fixture_snapshot) -> None:
    report = build_payoff_state_feasibility_bridge_report(fixture_snapshot)

    assert any(
        row["feasibility_status"] == "BLOCKED_MISSING_STATE_FAMILY"
        and "missing_payoff_state_family_id" in row["review_blockers"]
        for row in report["payoff_state_feasibility_bridge"]
    )


def test_missing_probability_inputs_block() -> None:
    left = make_node(
        "test:left",
        0.50,
        bid=None,
        ask=None,
        settlement_source="fixture_source",
        raw=_family_raw("payoff_missing_probability", "left"),
    )
    right = make_node(
        "test:right",
        0.50,
        bid=None,
        ask=None,
        settlement_source="fixture_source",
        raw=_family_raw("payoff_missing_probability", "right"),
    )
    for node in [left, right]:
        node.yes_price = None
        node.no_price = None
    row = _by_family(build_payoff_state_feasibility_bridge_report(_snapshot([left, right])))[
        "payoff_missing_probability"
    ]

    assert row["feasibility_status"] == "BLOCKED_MISSING_PROBABILITY_INPUTS"
    assert "missing_probability_input" in row["review_blockers"]


def test_midpoint_only_inputs_remain_non_actionable() -> None:
    left = make_node(
        "test:left",
        0.50,
        bid=None,
        ask=None,
        settlement_source="fixture_source",
        raw=_family_raw("payoff_midpoint_only", "left"),
    )
    right = make_node(
        "test:right",
        0.50,
        bid=None,
        ask=None,
        settlement_source="fixture_source",
        raw=_family_raw("payoff_midpoint_only", "right"),
    )
    row = _by_family(build_payoff_state_feasibility_bridge_report(_snapshot([left, right])))["payoff_midpoint_only"]

    assert row["feasibility_status"] == "FEASIBLE"
    assert row["probability_input_mode"] == "DIAGNOSTIC_MIDPOINT_FALLBACK"
    assert row["bound_gap_semantics"] == "diagnostic_midpoint_equality_repair_after_tolerance_non_actionable"
    assert "diagnostic_midpoint_used" in row["review_blockers"]
    assert "non_actionable_input" in row["review_blockers"]
    assert all(item["diagnostic_midpoint_used"] is True for item in row["probability_inputs_used"])
    assert all(item["non_actionable_input"] is True for item in row["probability_inputs_used"])


def test_unsupported_constraint_type_blocks() -> None:
    subset = make_node(
        "test:subset",
        0.60,
        bid=0.58,
        ask=0.62,
        settlement_source="fixture_source",
        raw=_family_raw("payoff_unsupported_subset", "left"),
    )
    superset = make_node(
        "test:superset",
        0.70,
        bid=0.68,
        ask=0.72,
        settlement_source="fixture_source",
        raw=_family_raw("payoff_unsupported_subset", "right"),
    )
    snapshot = _snapshot(
        [subset, superset],
        [_edge(RelationshipType.SUBSET, "test:subset", "test:superset")],
    )

    row = _by_family(build_payoff_state_feasibility_bridge_report(snapshot))["payoff_unsupported_subset"]

    assert row["feasibility_status"] == "BLOCKED_UNSUPPORTED_CONSTRAINT_TYPE"
    assert "unsupported_constraint_type_for_feasibility" in row["review_blockers"]
    assert row["source_probability_constraint_ids"]


@pytest.mark.parametrize("permission", ["PAPER_CANDIDATE", "TRADE", "EXECUTE", "ORDER", "BUY", "SELL"])
def test_outputs_cannot_include_disallowed_permissions(fixture_snapshot, permission: str) -> None:
    report = build_payoff_state_feasibility_bridge_report(fixture_snapshot)
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    for row in report["payoff_state_feasibility_bridge"]:
        assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]

    report["payoff_state_feasibility_bridge"][0]["allowed_actions"] = ["WATCH", permission]
    with pytest.raises(SchemaValidationError):
        validate_payoff_state_feasibility_bridge_report(report)


def test_status_set_matches_contract() -> None:
    assert FEASIBILITY_BRIDGE_STATUSES == {
        "FEASIBLE",
        "INFEASIBLE_DIAGNOSTIC",
        "BLOCKED_MISSING_STATE_FAMILY",
        "BLOCKED_MISSING_PAYOFF_MATRIX",
        "BLOCKED_MISSING_PROBABILITY_INPUTS",
        "BLOCKED_UNSUPPORTED_CONSTRAINT_TYPE",
    }


def test_scan_writes_payoff_state_feasibility_bridge_report() -> None:
    output = PROJECT_ROOT / "reports" / "market_graph_payoff_state_feasibility_bridge.json"
    result = scan.main([])

    assert result == 0
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    validate_payoff_state_feasibility_bridge_report(payload)


def test_infeasible_rows_surface_structural_lp_split_and_directions(fixture_snapshot) -> None:
    # Reviewers reading the bridge directly should be able to see both the
    # structural-sum gap (interval mass excess vs. the family target) and the
    # LP per-contract slack, plus the per-contract direction labels. Without
    # this split the 0.24 vs 0.18 mismatch between bound_gap and the
    # per_contract_repair sum on the exhaustive fixture is opaque.
    rows = _by_family(build_payoff_state_feasibility_bridge_report(fixture_snapshot))
    row = rows["payoff_infeasible_exhaustive"]

    assert row["structural_bound_gap"] >= row["lp_bound_gap"]
    assert row["binding_structural_constraint"] == "exhaustive_sum_exceeds_target"
    assert row["worst_contract_id"] in row["per_contract_repair"]
    assert row["worst_contract_repair_gap"] == max(row["per_contract_repair"].values())
    directions = row["per_contract_repair_directions"]
    assert set(directions) == set(row["per_contract_repair"])
    # All non-trivial repairs in the over-sum exhaustive fixture indicate the
    # observed price is above the LP-implied value.
    for contract_id, gap in row["per_contract_repair"].items():
        if gap > 0:
            assert directions[contract_id] == "price_above_lp_feasible_value"
        else:
            assert directions[contract_id] == "within_lp_feasible_value"


def test_child_parent_infeasible_row_marks_parent_as_below_lp_value(fixture_snapshot) -> None:
    # The child/parent infeasible fixture has child price too high vs parent
    # price; the LP wants to raise the parent's price, which is the diagnostic
    # signal a reviewer needs.
    rows = _by_family(build_payoff_state_feasibility_bridge_report(fixture_snapshot))
    row = rows["payoff_child_parent_violation"]

    assert row["binding_structural_constraint"] == "child_lower_exceeds_parent_upper"
    directions = row["per_contract_repair_directions"]
    parent_id = next(
        contract_id for contract_id in row["markets_involved"] if contract_id.endswith("parent")
    )
    assert directions[parent_id] == "price_below_lp_feasible_value"


def test_feasible_rows_have_zero_structural_and_lp_gaps(fixture_snapshot) -> None:
    rows = _by_family(build_payoff_state_feasibility_bridge_report(fixture_snapshot))
    row = rows["payoff_feasible_exhaustive"]

    assert row["structural_bound_gap"] == 0.0
    assert row["lp_bound_gap"] == 0.0
    assert row["worst_contract_id"] is None
    assert row["worst_contract_repair_gap"] == 0.0
    assert row["binding_structural_constraint"] is None
    assert row["per_contract_repair_directions"] == {}


def test_infeasible_row_rejects_missing_worst_contract_id(fixture_snapshot) -> None:
    report = build_payoff_state_feasibility_bridge_report(fixture_snapshot)
    infeasible = next(
        row
        for row in report["payoff_state_feasibility_bridge"]
        if row["feasibility_status"] == "INFEASIBLE_DIAGNOSTIC"
    )
    infeasible["worst_contract_id"] = None

    with pytest.raises(SchemaValidationError):
        validate_payoff_state_feasibility_bridge_report(report)


def test_per_contract_repair_directions_must_use_supported_labels(fixture_snapshot) -> None:
    report = build_payoff_state_feasibility_bridge_report(fixture_snapshot)
    infeasible = next(
        row
        for row in report["payoff_state_feasibility_bridge"]
        if row["feasibility_status"] == "INFEASIBLE_DIAGNOSTIC"
    )
    first_contract = next(iter(infeasible["per_contract_repair_directions"]))
    infeasible["per_contract_repair_directions"][first_contract] = "unsupported_direction_label"

    with pytest.raises(SchemaValidationError):
        validate_payoff_state_feasibility_bridge_report(report)
