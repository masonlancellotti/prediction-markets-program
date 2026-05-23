from __future__ import annotations

import json

from graph_engine.consistency.runner import run_consistency_checks
from graph_engine.reporting.hints import BANNER, build_relative_value_hints_report


def _hints_by_relation(report: dict, relation_type: str) -> list[dict]:
    return [hint for hint in report["hints"] if hint["relation_type"] == relation_type]


def test_relative_value_hints_export_is_diagnostic_only(fixture_snapshot) -> None:
    report = build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))
    serialized = json.dumps(report).lower()

    assert report["diagnostic_only"] is True
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert report["banner"] == BANNER
    assert report["hint_count"] > 0
    for prohibited in ("pnl", "profit", "dollars", "fill", "edge_bps", "possible_arb"):
        assert prohibited not in serialized


def test_sports_structural_implication_hints_are_emitted(fixture_snapshot) -> None:
    report = build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))
    ids = {hint["graph_hint_id"] for hint in _hints_by_relation(report, "SUBSET")}

    assert "hint:IMPLICATION_VIOLATION:edge_world_series_implies_al_champion" in ids
    assert "hint:IMPLICATION_VIOLATION:edge_stanley_cup_implies_conference" in ids
    assert "hint:IMPLICATION_VIOLATION:edge_nba_champion_implies_conference" in ids


def test_btc_same_window_hint_and_different_window_downgrade(fixture_snapshot) -> None:
    report = build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))
    by_id = {hint["graph_hint_id"]: hint for hint in report["hints"]}

    same_window = by_id["hint:SUBSET_OVER_SUPERSET:edge_btc_120k_subset_btc_100k_same_window"]
    different_window = by_id["hint:AMBIGUOUS_WORDING:edge_btc_120k_subset_btc_100k_different_window"]

    assert same_window["relation_type"] == "SUBSET"
    assert same_window["hard_bound_type"] == "upper_probability_bound"
    assert different_window["relation_type"] == "MANUAL_REVIEW"
    assert "threshold_basis_mismatch" in different_window["blockers"]


def test_exhaustive_group_requires_complete_set_for_exhaustive_hint(fixture_snapshot) -> None:
    report = build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))
    complete = [
        hint
        for hint in _hints_by_relation(report, "EXHAUSTIVE_GROUP")
        if hint["source_market_id"].startswith("fixture:election_candidate")
    ]
    incomplete = [
        hint
        for hint in _hints_by_relation(report, "MUTUALLY_EXCLUSIVE")
        if hint["source_market_id"].startswith("fixture:award_nominee")
    ]

    assert complete
    assert incomplete
    assert all("exhaustive_group_not_complete" in hint["blockers"] for hint in incomplete)
