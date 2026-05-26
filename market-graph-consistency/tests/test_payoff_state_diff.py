from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from graph_engine.loader import load_fixture_markets
from graph_engine.reporting.payoff_state_diff import (
    build_payoff_state_diff_report,
    validate_payoff_state_diff_contract,
    write_payoff_state_diff_report,
)
from graph_engine.reporting.payoff_state_report import (
    build_payoff_state_diagnostics_report,
    write_payoff_state_diagnostics_report,
)
from graph_engine.reporting.schema_validation import SchemaValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "venues" / "fixtures"


def _fixture_report() -> dict:
    snapshot, _ = load_fixture_markets(FIXTURES_DIR)
    return build_payoff_state_diagnostics_report(snapshot)


def test_diff_against_identical_report_reports_no_changes() -> None:
    report = _fixture_report()
    diff = build_payoff_state_diff_report(report, deepcopy(report))

    assert diff["summary"]["added_count"] == 0
    assert diff["summary"]["removed_count"] == 0
    assert diff["summary"]["changed_count"] == 0
    assert diff["summary"]["unchanged_count"] == report["family_count"]
    assert diff["field_changes"] == []


def test_diff_detects_added_and_removed_families() -> None:
    old = _fixture_report()
    new = deepcopy(old)
    new["payoff_state_diagnostics"] = [
        row for row in new["payoff_state_diagnostics"] if row["family_id"] != "payoff_feasible_exhaustive"
    ]
    new["family_count"] = len(new["payoff_state_diagnostics"])

    diff = build_payoff_state_diff_report(old, new)

    assert diff["summary"]["removed_count"] == 1
    assert diff["summary"]["added_count"] == 0
    assert diff["removed_families"][0]["family_id"] == "payoff_feasible_exhaustive"


def test_diff_detects_changed_bound_gap_and_status() -> None:
    old = _fixture_report()
    new = deepcopy(old)
    target = next(row for row in new["payoff_state_diagnostics"] if row["family_id"] == "payoff_infeasible_exhaustive")
    target["bound_gap"] = 0.5
    target["feasibility_status"] = "feasible"
    target["max_action_cap"] = "WATCH"
    target["diagnostic_priority"] = "WATCH"
    target["violated_constraints"] = []

    diff = build_payoff_state_diff_report(old, new)

    fields_changed = {change["field"] for change in diff["field_changes"]}
    assert "bound_gap" in fields_changed
    assert "feasibility_status" in fields_changed
    assert "max_action_cap" in fields_changed


def test_diff_validator_rejects_disallowed_field() -> None:
    old = _fixture_report()
    diff = build_payoff_state_diff_report(old, deepcopy(old))
    diff["field_changes"].append(
        {"family_id": "x", "field": "unsupported_field", "old_value": 1, "new_value": 2}
    )

    with pytest.raises(SchemaValidationError):
        validate_payoff_state_diff_contract(diff)


def test_diff_writes_safe_reports(tmp_path) -> None:
    snapshot, _ = load_fixture_markets(FIXTURES_DIR)
    old_json = tmp_path / "old.json"
    new_json = tmp_path / "new.json"
    write_payoff_state_diagnostics_report(snapshot, old_json, tmp_path / "old.md")
    payload = json.loads(old_json.read_text(encoding="utf-8"))
    # mutate a family in the new report
    target = next(row for row in payload["payoff_state_diagnostics"] if row["family_id"] == "payoff_infeasible_exhaustive")
    target["bound_gap"] = round(target["bound_gap"] + 0.1, 6)
    new_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    diff_json = tmp_path / "diff.json"
    diff_md = tmp_path / "diff.md"
    report = write_payoff_state_diff_report(old_json, new_json, diff_json, diff_md)

    assert json.loads(diff_json.read_text(encoding="utf-8")) == report
    broad = re.compile(
        r"PAPER_CANDIDATE|POSSIBLE_ARB|EXACT_SAME_PAYOFF|exact_same_payoff|paper_candidate|"
        r"executable|PnL|pnl|profit|fill|size|trade|order|trade_permission|place_order|cancel_order",
        re.IGNORECASE,
    )
    for path in (diff_json, diff_md):
        assert broad.search(path.read_text(encoding="utf-8")) is None, path.name
