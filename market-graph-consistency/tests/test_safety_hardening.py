from __future__ import annotations

import json
from datetime import datetime, timezone

from graph_engine.consistency.checks import check_implication, check_same_event_reworded, check_subset
from graph_engine.consistency.runner import run_consistency_checks
from graph_engine.models import (
    ExclusionSet,
    GraphSnapshot,
    RelationshipEdge,
    RelationshipType,
    ViolationKind,
    coerce_bool,
)
from graph_engine.reporting.json_report import _assert_safe_violation_schema, build_json_report
from tests.conftest import make_node


def _edge(
    relation: RelationshipType,
    *,
    src: str = "test:a",
    dst: str = "test:b",
    source: str = "manual",
    confidence: float = 0.95,
    reviewed_by: str | None = "reviewer",
    **kwargs,
) -> RelationshipEdge:
    return RelationshipEdge(
        edge_id=f"edge_{relation.value.lower()}",
        src_market_id=src,
        dst_market_id=dst,
        relation=relation,
        confidence=confidence,
        source=source,
        rationale="fixture relationship",
        evidence=["fixture"],
        created_at="2026-05-19T18:00:00+00:00",
        reviewed_by=reviewed_by,
        **kwargs,
    )


def test_reference_only_nodes_cannot_create_hard_violations() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:a": make_node("test:a", 0.85, reference_only=True),
            "test:b": make_node("test:b", 0.10),
        },
    )

    violation = check_implication(snapshot, _edge(RelationshipType.IMPLICATION))

    assert violation is not None
    assert violation.kind == ViolationKind.AMBIGUOUS_WORDING
    assert "reference_only_node" in violation.blockers
    assert violation.max_action_cap == "WATCH"
    assert violation.magnitude == 0.0


def test_same_event_reworded_without_settlement_source_proof_downgrades() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:a": make_node("test:a", 0.80),
            "test:b": make_node("test:b", 0.20),
        },
    )

    violation = check_same_event_reworded(snapshot, _edge(RelationshipType.SAME_EVENT_REWORDED))

    assert violation is not None
    assert violation.kind == ViolationKind.AMBIGUOUS_WORDING
    assert "settlement_source_not_proven" in violation.blockers


def test_same_event_reworded_with_settlement_source_proof_can_emit_reword_mismatch() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:a": make_node("test:a", 0.80),
            "test:b": make_node("test:b", 0.20),
        },
    )

    violation = check_same_event_reworded(
        snapshot,
        _edge(RelationshipType.SAME_EVENT_REWORDED, settlement_source_proven=True),
    )

    assert violation is not None
    assert violation.kind == ViolationKind.REWORD_MISMATCH


def test_btc_threshold_chain_requires_same_source_and_window() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:btc_120": make_node(
                "test:btc_120",
                0.70,
                observable="BTC_USD",
                settlement_source="coinbase_index",
                window="2026-06-30",
            ),
            "test:btc_100": make_node(
                "test:btc_100",
                0.50,
                observable="BTC_USD",
                settlement_source="coinbase_index",
                window="2026-06-30",
            ),
        },
    )

    violation = check_subset(
        snapshot,
        _edge(
            RelationshipType.SUBSET,
            src="test:btc_120",
            dst="test:btc_100",
            observable="BTC_USD",
            window="2026-06-30",
        ),
    )

    assert violation is not None
    assert violation.kind == ViolationKind.SUBSET_OVER_SUPERSET
    assert violation.max_action_cap == "MANUAL_REVIEW"


def test_btc_threshold_chain_with_missing_source_or_window_downgrades() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:btc_120": make_node(
                "test:btc_120",
                0.70,
                observable="BTC_USD",
                settlement_source="coinbase_index",
                window="2026-06-30",
            ),
            "test:btc_100": make_node(
                "test:btc_100",
                0.50,
                observable="BTC_USD",
            ),
        },
    )

    violation = check_subset(snapshot, _edge(RelationshipType.SUBSET, src="test:btc_120", dst="test:btc_100"))

    assert violation is not None
    assert violation.kind == ViolationKind.AMBIGUOUS_WORDING
    assert violation.max_action_cap == "WATCH"
    assert "threshold_basis_mismatch" in violation.blockers


def test_btc_threshold_chain_downgrades_on_source_or_window_mismatch() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:btc_120": make_node(
                "test:btc_120",
                0.70,
                observable="BTC_USD",
                settlement_source="coinbase_index",
                window="2026-06-30",
            ),
            "test:btc_100": make_node(
                "test:btc_100",
                0.50,
                observable="BTC_USD",
                settlement_source="binance_index",
                window="2026-07-31",
            ),
        },
    )

    violation = check_subset(snapshot, _edge(RelationshipType.SUBSET, src="test:btc_120", dst="test:btc_100"))

    assert violation is not None
    assert violation.kind == ViolationKind.AMBIGUOUS_WORDING
    assert violation.max_action_cap == "WATCH"
    assert "threshold_basis_mismatch" in violation.blockers


def test_partition_vs_subset_exclusion_sets_both_remain_diagnostics() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:a": make_node("test:a", 0.50),
            "test:b": make_node("test:b", 0.45),
            "test:c": make_node("test:c", 0.40),
        },
        exclusion_sets=[
            ExclusionSet("partition_set", ["test:a", "test:b", "test:c"], "partition", 0.03),
            ExclusionSet("subset_set", ["test:a", "test:b", "test:c"], "subset", 0.03),
        ],
    )

    violations = run_consistency_checks(snapshot)

    assert {violation.violation_id for violation in violations} == {
        "SUM_OVER_ONE:partition_set",
        "SUM_OVER_ONE:subset_set",
    }
    assert all(violation.action.value in {"WATCH", "MANUAL_REVIEW"} for violation in violations)


def test_stale_node_caps_action_and_adds_review_question() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-21T18:00:00+00:00",
        nodes={
            "test:a": make_node("test:a", 0.85, as_of=datetime(2026, 5, 19, tzinfo=timezone.utc)),
            "test:b": make_node("test:b", 0.10),
        },
    )

    violation = check_implication(snapshot, _edge(RelationshipType.IMPLICATION))

    assert violation is not None
    assert "stale_input" in violation.blockers
    assert violation.max_action_cap_reason == "stale_input_manual_review_cap"
    assert violation.max_action_cap == "MANUAL_REVIEW"
    assert violation.action.value == "MANUAL_REVIEW"


def test_llm_sourced_edge_caps_action() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:a": make_node("test:a", 0.95),
            "test:b": make_node("test:b", 0.10),
        },
    )

    violation = check_implication(
        snapshot,
        _edge(RelationshipType.IMPLICATION, source="llm", confidence=0.99, reviewed_by=None),
    )

    assert violation is not None
    assert violation.edge_source == "llm"
    assert "llm_edge_unreviewed" in violation.blockers
    assert violation.action.value == "WATCH"
    assert violation.max_action_cap == "WATCH"


def test_violation_json_has_no_pnl_dollar_trade_or_promoted_fields() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:a": make_node("test:a", 0.85),
            "test:b": make_node("test:b", 0.10),
        },
        edges=[_edge(RelationshipType.IMPLICATION)],
    )
    report = build_json_report(snapshot, run_consistency_checks(snapshot))
    serialized = json.dumps(report).lower()

    for prohibited in ("pnl", "profit", "dollars", "fill", "edge_bps", "paper", "possible_arb"):
        assert prohibited not in serialized
    assert "trade" not in serialized
    assert report["violations"][0]["magnitude_unit"] == "probability"


def test_recursive_violation_schema_guard_rejects_nested_forbidden_fields() -> None:
    rows = [
        {
            "violation_id": "safe",
            "nested": [{"metadata": {"pnl": 12}}],
        }
    ]

    try:
        _assert_safe_violation_schema(rows)
    except ValueError as exc:
        assert "pnl" in str(exc)
    else:
        raise AssertionError("nested prohibited field should fail")


def test_coerce_bool_fail_closed_for_false_and_unknown_strings() -> None:
    assert coerce_bool("true") is True
    assert coerce_bool("1") is True
    assert coerce_bool("yes") is True
    assert coerce_bool("on") is True
    assert coerce_bool("y") is True
    assert coerce_bool("false") is False
    assert coerce_bool("0") is False
    assert coerce_bool("no") is False
    assert coerce_bool("off") is False
    assert coerce_bool("n") is False
    assert coerce_bool("maybe") is False
