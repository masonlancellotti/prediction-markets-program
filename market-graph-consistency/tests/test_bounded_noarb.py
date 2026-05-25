from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from graph_engine.bounded_noarb import (
    build_bounded_noarb_report,
    validate_bounded_noarb_report,
    write_bounded_noarb_report,
)
from graph_engine.loader import load_fixture_markets
from graph_engine.reporting.json_report import PROHIBITED_VIOLATION_FIELDS
from graph_engine.reporting.schema_validation import SchemaValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROHIBITED_TOKENS = sorted(PROHIBITED_VIOLATION_FIELDS)


def _fixture_report() -> dict:
    snapshot, _ = load_fixture_markets(PROJECT_ROOT / "venues" / "fixtures")
    return build_bounded_noarb_report(snapshot)


def _by_family(report: dict) -> dict[str, dict]:
    return {
        item["family_id"]: item
        for item in report["no_arb_consistency_diagnostics"]
    }


def test_bounded_noarb_feasible_case_is_watch_not_high_priority() -> None:
    row = _by_family(_fixture_report())["bounded_feasible_exhaustive"]

    assert row["diagnostic_only"] is True
    assert row["affects_evaluator_gates"] is False
    assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert row["feasibility_status"] == "feasible"
    assert row["max_action_cap"] == "WATCH"
    assert row["violated_constraints"] == []
    assert row["bound_gap"] == 0.0
    assert row["state_count"] == 3
    assert row["contract_count"] == 3


@pytest.mark.parametrize(
    ("family_id", "expected_violation"),
    [
        ("bounded_infeasible_exhaustive", "exhaustive_sum_bound"),
        ("bounded_mutual_over", "mutual_exclusion_upper_bound"),
        ("bounded_child_parent", "child_parent_bound"),
        ("bounded_threshold_ladder", "threshold_ladder_bound"),
    ],
)
def test_bounded_noarb_infeasible_cases_are_manual_review(family_id: str, expected_violation: str) -> None:
    row = _by_family(_fixture_report())[family_id]

    assert row["feasibility_status"] == "infeasible"
    assert row["max_action_cap"] == "MANUAL_REVIEW"
    assert expected_violation in row["violated_constraints"]
    assert row["bound_gap"] > 0
    assert row["normalized_bound_gap"] > 0
    assert row["confidence_basis"]["score"] >= 0.6


def test_bounded_noarb_missing_state_definitions_fail_closed() -> None:
    row = _by_family(_fixture_report())["bounded_missing_states"]

    assert row["feasibility_status"] == "blocked"
    assert row["max_action_cap"] == "WATCH"
    assert row["violated_constraints"] == []
    assert row["bound_gap"] == 0.0
    assert row["confidence_basis"]["score"] <= 0.2
    assert "missing_state_definitions" in row["blockers"]


def test_bounded_noarb_report_validates_before_writing(tmp_path) -> None:
    snapshot, _ = load_fixture_markets(PROJECT_ROOT / "venues" / "fixtures")
    json_output = tmp_path / "bounded_noarb_consistency.json"
    md_output = tmp_path / "bounded_noarb_consistency.md"

    report = write_bounded_noarb_report(snapshot, json_output, md_output)

    assert json_output.exists()
    assert md_output.exists()
    assert json.loads(json_output.read_text(encoding="utf-8")) == report
    validate_bounded_noarb_report(report)


@pytest.mark.parametrize("token", ["trade", "fill", "size", "paper"])
def test_bounded_noarb_report_rejects_bare_prohibited_terms(token: str) -> None:
    report = _fixture_report()
    report["no_arb_consistency_diagnostics"][0]["reason_for_review"] = token

    with pytest.raises(SchemaValidationError):
        validate_bounded_noarb_report(report)


def test_bounded_noarb_output_uses_diagnostic_terminology_only() -> None:
    report = _fixture_report()
    serialized = json.dumps(report).lower()

    assert report["diagnostic_only"] is True
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    for token in PROHIBITED_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", serialized, flags=re.IGNORECASE) is None
