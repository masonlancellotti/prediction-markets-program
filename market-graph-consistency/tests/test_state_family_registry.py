from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from graph_engine.loader import load_fixture_markets
from graph_engine.payoff_state import compile_payoff_families
from graph_engine.payoff_state_feasibility import check_no_arb_consistency
from graph_engine.reporting.schema_validation import SchemaValidationError
from graph_engine.state_family_registry import (
    ConstraintExplanation,
    REGISTRY_BANNER,
    build_state_family_registry_report,
    explain_payoff_state_diagnostic,
    registry_entry_for_formula_family,
    state_family_registry,
    validate_state_family_registry_report,
    write_state_family_registry_report,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "venues" / "fixtures"


def test_registry_lists_every_supported_formula_family() -> None:
    families = {entry.formula_family for entry in state_family_registry()}
    assert {"BTC_THRESHOLD", "FED_MEETING_RANGE", "SPORTS_CHAMPION", "WEATHER_RANGE", "UNKNOWN"} <= families


def test_registry_marks_btc_threshold_safe_and_sports_blocked() -> None:
    btc = registry_entry_for_formula_family("BTC_THRESHOLD")
    sports = registry_entry_for_formula_family("SPORTS_CHAMPION")

    assert btc.is_finite_state_safe is True
    assert btc.finite_state_family_type == "threshold_ladder"
    assert sports.is_finite_state_safe is False
    assert sports.block_reasons


def test_registry_unknown_family_falls_back_safely() -> None:
    entry = registry_entry_for_formula_family("DEFINITELY_NOT_REGISTERED")

    assert entry.formula_family == "UNKNOWN"
    assert entry.is_finite_state_safe is False
    assert entry.finite_state_family_type is None
    assert "unsupported_formula_family" in entry.block_reasons


def test_registry_report_is_diagnostic_only_and_capped() -> None:
    report = build_state_family_registry_report()

    assert report["banner"] == REGISTRY_BANNER
    assert report["diagnostic_only"] is True
    assert report["affects_evaluator_gates"] is False
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    for entry in report["state_family_registry_entries"]:
        assert entry["diagnostic_only"] is True
        assert entry["affects_evaluator_gates"] is False
        assert entry["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
        assert entry["suggested_max_action_cap"] in {"WATCH", "MANUAL_REVIEW"}


def test_registry_report_rejects_attempt_to_set_evaluator_ready() -> None:
    report = build_state_family_registry_report()
    mutated = deepcopy(report)
    mutated["state_family_registry_entries"][0]["affects_evaluator_gates"] = True

    with pytest.raises(SchemaValidationError):
        validate_state_family_registry_report(mutated)


@pytest.mark.parametrize(
    "embedded_key",
    [
        "paper_candidate",
        "possible_arb",
        "exact_same_payoff",
        "trade_permission",
    ],
)
def test_registry_report_rejects_embedded_compound_phrase_key(embedded_key: str) -> None:
    report = build_state_family_registry_report()
    report["state_family_registry_entries"][0][embedded_key] = "fixture"

    with pytest.raises(SchemaValidationError):
        validate_state_family_registry_report(report)


def test_registry_report_validates_before_writing(tmp_path) -> None:
    json_path = tmp_path / "registry.json"
    md_path = tmp_path / "registry.md"

    report = write_state_family_registry_report(json_path, md_path)

    assert json_path.exists()
    assert md_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8")) == report
    broad = re.compile(
        r"PAPER_CANDIDATE|POSSIBLE_ARB|EXACT_SAME_PAYOFF|exact_same_payoff|paper_candidate|"
        r"executable|PnL|pnl|profit|fill|size|trade|order|trade_permission|place_order|cancel_order",
        re.IGNORECASE,
    )
    for path in (json_path, md_path):
        assert broad.search(path.read_text(encoding="utf-8")) is None, path.name


def test_explanation_engine_produces_review_packet_for_infeasible_family() -> None:
    snapshot, _ = load_fixture_markets(FIXTURES_DIR)
    matrices = {matrix.family_id: matrix for matrix in compile_payoff_families(snapshot)}
    matrix = matrices["payoff_infeasible_exhaustive"]
    explanation = explain_payoff_state_diagnostic(matrix, check_no_arb_consistency(matrix))

    assert isinstance(explanation, ConstraintExplanation)
    assert explanation.family_id == "payoff_infeasible_exhaustive"
    assert explanation.max_action_cap == "MANUAL_REVIEW"
    assert explanation.diagnostic_only is True
    assert explanation.affects_evaluator_gates is False
    assert "infeasible" in explanation.explanation_text.lower()
    assert explanation.review_questions


def test_explanation_engine_marks_blocked_family_watch_only() -> None:
    snapshot, _ = load_fixture_markets(FIXTURES_DIR)
    matrices = {matrix.family_id: matrix for matrix in compile_payoff_families(snapshot)}
    matrix = matrices["payoff_ambiguous_missing_states"]
    explanation = explain_payoff_state_diagnostic(matrix, check_no_arb_consistency(matrix))

    assert explanation.max_action_cap == "WATCH"
    assert explanation.blockers
