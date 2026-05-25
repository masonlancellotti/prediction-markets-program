from __future__ import annotations

from copy import deepcopy
import json
import re

import pytest

from graph_engine.consistency.runner import run_consistency_checks
from graph_engine.reporting.diagnostic_diff import (
    build_diagnostic_diff_report,
    validate_diagnostic_diff_contract,
    write_diagnostic_diff_report,
)
from graph_engine.reporting.hint_diff import build_hint_diff_report, render_console_summary, write_hint_diff_report
from graph_engine.reporting.hints import build_relative_value_hints_report
from graph_engine.reporting.json_report import build_json_report
from graph_engine.reporting.json_report import PROHIBITED_VIOLATION_FIELDS
from graph_engine.reporting.schema_validation import SchemaValidationError


PROHIBITED_DIFF_TOKENS = sorted(
    PROHIBITED_VIOLATION_FIELDS
    | {
        "PAPER_CANDIDATE",
        "POSSIBLE_ARB",
        "executable-arb",
        "fill-size",
        "trade-permission",
    }
)


def _valid_report(fixture_snapshot) -> dict:
    return build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))


def _by_id(report: dict) -> dict[str, dict]:
    return {hint["graph_hint_id"]: hint for hint in report["hints"]}


def _diagnostic_report(fixture_snapshot) -> dict:
    return build_json_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))


def test_hint_diff_detects_added_and_removed_hints(fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    removed = new_report["hints"].pop(0)
    added = deepcopy(new_report["hints"][0])
    added["graph_hint_id"] = "hint:fixture_added_structural_hint"
    new_report["hints"].append(added)

    diff = build_hint_diff_report(old_report, new_report)

    assert diff["diagnostic_only"] is True
    assert diff["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert {item["graph_hint_id"] for item in diff["added_hints"]} == {added["graph_hint_id"]}
    assert {item["graph_hint_id"] for item in diff["removed_hints"]} == {removed["graph_hint_id"]}
    assert {item["graph_hint_id"] for item in diff["new_hints"]} == {added["graph_hint_id"]}
    assert diff["summary"]["added_count"] == 1
    assert diff["summary"]["removed_count"] == 1
    assert diff["unchanged_count"] == len(old_report["hints"]) - 1
    assert all(item["diagnostic_only"] is True for item in diff["added_hints"])
    assert all(item["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"} for item in diff["added_hints"])


def test_hint_diff_detects_subset_to_ambiguous_downgrade(fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    target = _by_id(new_report)["hint:SUBSET_OVER_SUPERSET:edge_btc_120k_subset_btc_100k_same_window"]
    target["relation_type"] = "AMBIGUOUS_WORDING"
    target["direction"] = "none"
    target["hard_bound_type"] = "none"

    diff = build_hint_diff_report(old_report, new_report)
    changes = {(change["field"], change["old_value"], change["new_value"]) for change in diff["field_changes"]}
    changed = _by_id({"hints": diff["changed_hints"]})[target["graph_hint_id"]]

    assert ("relation_type", "SUBSET", "AMBIGUOUS_WORDING") in changes
    assert ("direction", "source_implies_target", "none") in changes
    assert ("hard_bound_type", "upper_probability_bound", "none") in changes
    assert changed["severity_or_priority_change"]


def test_hint_diff_detects_blocker_and_settlement_proof_changes(fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    ambiguous = _by_id(new_report)["hint:AMBIGUOUS_WORDING:edge_btc_120k_subset_btc_100k_different_window"]
    same_payoff = _by_id(new_report)["hint:REWORD_MISMATCH:edge_openai_1t_same_event_cross_venue"]
    ambiguous["blockers"].append("fixture_added_review_blocker")
    same_payoff["settlement_source_proven"] = False

    diff = build_hint_diff_report(old_report, new_report)
    fields = {change["field"] for change in diff["field_changes"]}
    changed = _by_id({"hints": diff["changed_hints"]})

    assert "blockers" in fields
    assert "settlement_source_proven" in fields
    assert changed[ambiguous["graph_hint_id"]]["reason_change"]
    assert changed[same_payoff["graph_hint_id"]]["reason_change"]


def test_hint_diff_detects_cap_changes(fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    target = _by_id(new_report)["hint:AMBIGUOUS_WORDING:edge_btc_120k_subset_btc_100k_different_window"]
    target["max_action_cap"] = "MANUAL_REVIEW"

    diff = build_hint_diff_report(old_report, new_report)

    assert diff["summary"]["upgraded_count"] == 1
    assert diff["upgraded_hints"][0]["graph_hint_id"] == target["graph_hint_id"]
    assert diff["action_change"][0]["graph_hint_id"] == target["graph_hint_id"]
    assert diff["changed_hints"][0]["action_change"]


def test_hint_diff_no_change_case_has_unchanged_count_and_no_deltas(fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)

    diff = build_hint_diff_report(old_report, new_report)

    assert diff["summary"]["added_count"] == 0
    assert diff["summary"]["removed_count"] == 0
    assert diff["summary"]["changed_count"] == 0
    assert diff["summary"]["unchanged_count"] == len(old_report["hints"])
    assert diff["unchanged_count"] == len(old_report["hints"])
    assert diff["added_hints"] == []
    assert diff["removed_hints"] == []
    assert diff["changed_hints"] == []
    assert diff["severity_or_priority_change"] == []
    assert diff["reason_change"] == []
    assert diff["action_change"] == []


def test_hint_diff_console_summary_includes_top_watch_and_manual_review(fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    target = _by_id(new_report)["hint:AMBIGUOUS_WORDING:edge_btc_120k_subset_btc_100k_different_window"]
    target["max_action_cap"] = "MANUAL_REVIEW"
    added = deepcopy(new_report["hints"][0])
    added["graph_hint_id"] = "hint:fixture_added_watch_item"
    new_report["hints"].append(added)

    summary = render_console_summary(build_hint_diff_report(old_report, new_report))

    assert "Added hints: 1" in summary
    assert "Removed hints: 0" in summary
    assert "Changed hints: 1" in summary
    assert "Top WATCH items:" in summary
    assert "Top MANUAL_REVIEW items:" in summary


def test_hint_diff_writer_validates_both_inputs(tmp_path, fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    json_output = tmp_path / "diff.json"
    md_output = tmp_path / "diff.md"
    old_path.write_text(json.dumps(old_report), encoding="utf-8")
    new_path.write_text(json.dumps(new_report), encoding="utf-8")

    write_hint_diff_report(old_path, new_path, json_output, md_output)

    diff = json.loads(json_output.read_text(encoding="utf-8"))
    assert diff["diagnostic_only"] is True
    assert md_output.read_text(encoding="utf-8").startswith("# Market Graph Hint Diff")


def test_written_hint_diff_reports_contain_no_prohibited_tokens(tmp_path, fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    json_output = tmp_path / "market_graph_hint_diff.json"
    md_output = tmp_path / "market_graph_hint_diff.md"
    old_path.write_text(json.dumps(old_report), encoding="utf-8")
    new_path.write_text(json.dumps(new_report), encoding="utf-8")

    write_hint_diff_report(old_path, new_path, json_output, md_output)

    combined = json_output.read_text(encoding="utf-8") + md_output.read_text(encoding="utf-8")
    for token in PROHIBITED_DIFF_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", combined, flags=re.IGNORECASE) is None


def test_hint_diff_output_rejects_exact_same_payoff_relation_change(fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    target = _by_id(new_report)["hint:SUBSET_OVER_SUPERSET:edge_btc_120k_subset_btc_100k_same_window"]
    target["relation_type"] = "EXACT_SAME_PAYOFF"

    try:
        build_hint_diff_report(old_report, new_report)
    except SchemaValidationError:
        return
    raise AssertionError("diff output should reject disallowed relation values")


def test_hint_diff_output_rejects_prohibited_change_values(fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    target = _by_id(new_report)["hint:AMBIGUOUS_WORDING:edge_btc_120k_subset_btc_100k_different_window"]
    target["blockers"].append("POSSIBLE" + "_" + "ARB")

    try:
        build_hint_diff_report(old_report, new_report)
    except SchemaValidationError:
        return
    raise AssertionError("diff output should reject prohibited change values")


@pytest.mark.parametrize("token", ["trade", "fill", "size", "paper"])
def test_hint_diff_output_rejects_bare_prohibited_change_values(fixture_snapshot, token: str) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    target = _by_id(new_report)["hint:AMBIGUOUS_WORDING:edge_btc_120k_subset_btc_100k_different_window"]
    target["blockers"].append(token)

    try:
        build_hint_diff_report(old_report, new_report)
    except SchemaValidationError:
        return
    raise AssertionError("diff output should reject bare prohibited change values")


def test_hint_diff_rejects_non_diagnostic_input(tmp_path, fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    new_report["diagnostic_only"] = False
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_path.write_text(json.dumps(old_report), encoding="utf-8")
    new_path.write_text(json.dumps(new_report), encoding="utf-8")

    try:
        write_hint_diff_report(old_path, new_path, tmp_path / "diff.json", tmp_path / "diff.md")
    except SchemaValidationError:
        return
    raise AssertionError("non-diagnostic input should fail schema validation")


def test_hint_diff_rejects_prohibited_labels_and_fields(tmp_path, fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    new_report["allowed_actions"] = ["WATCH", "POSSIBLE" + "_" + "ARB"]
    new_report["hints"][0]["profit"] = 1
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_path.write_text(json.dumps(old_report), encoding="utf-8")
    new_path.write_text(json.dumps(new_report), encoding="utf-8")

    try:
        write_hint_diff_report(old_path, new_path, tmp_path / "diff.json", tmp_path / "diff.md")
    except SchemaValidationError:
        return
    raise AssertionError("prohibited input should fail schema validation")


def test_diagnostic_diff_detects_added_removed_changed_and_unchanged(fixture_snapshot) -> None:
    old_report = _diagnostic_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    old_multi = old_report["multi_leg_constraints"]["multi_leg_constraints"]
    new_multi = new_report["multi_leg_constraints"]["multi_leg_constraints"]
    removed = new_multi.pop(0)
    added = deepcopy(new_multi[0])
    added["constraint_id"] = "multi_leg:fixture_added_constraint"
    new_multi.append(added)
    changed = new_multi[0]
    changed["bound_gap"] = round(changed["bound_gap"] + 0.01, 6)
    changed["diagnostic_priority"] = "WATCH" if changed["diagnostic_priority"] == "MANUAL_REVIEW" else "MANUAL_REVIEW"
    changed["max_action_cap"] = changed["diagnostic_priority"]
    changed["blockers"] = ["fixture_added_blocker"]

    diff = build_diagnostic_diff_report(old_report, new_report)

    assert diff["diagnostic_only"] is True
    assert diff["affects_evaluator_gates"] is False
    assert diff["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert diff["summary"]["added_count"] == 1
    assert diff["summary"]["removed_count"] == 1
    assert diff["summary"]["changed_count"] >= 1
    assert diff["summary"]["unchanged_count"] > 0
    assert {item["constraint_id"] for item in diff["removed_constraints"]} == {f"multi_leg:{removed['constraint_id']}"}
    assert {item["constraint_id"] for item in diff["added_constraints"]} == {f"multi_leg:{added['constraint_id']}"}
    changed_fields = {change["field"] for change in diff["field_changes"]}
    assert {"bound_gap", "diagnostic_priority", "blockers"} <= changed_fields


def test_diagnostic_diff_detects_formula_relation_change(fixture_snapshot) -> None:
    old_report = _diagnostic_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    target = next(
        item
        for item in new_report["formula_diagnostics"]["formula_diagnostics"]
        if item["formula_relation"] != "ambiguous_not_exact"
    )
    target["formula_relation"] = "ambiguous_not_exact"
    target["diagnostic_priority"] = "WATCH"
    target["max_action_cap"] = "WATCH"

    diff = build_diagnostic_diff_report(old_report, new_report)
    fields = {change["field"] for change in diff["field_changes"]}

    assert "formula_relation" in fields
    assert "diagnostic_priority" in fields


def test_diagnostic_diff_no_change_case(fixture_snapshot) -> None:
    old_report = _diagnostic_report(fixture_snapshot)
    new_report = deepcopy(old_report)

    diff = build_diagnostic_diff_report(old_report, new_report)

    assert diff["summary"]["added_count"] == 0
    assert diff["summary"]["removed_count"] == 0
    assert diff["summary"]["changed_count"] == 0
    assert diff["field_changes"] == []


def test_diagnostic_diff_writer_is_saved_file_only(tmp_path, fixture_snapshot) -> None:
    old_report = _diagnostic_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    old_path = tmp_path / "old_diagnostics.json"
    new_path = tmp_path / "new_diagnostics.json"
    json_output = tmp_path / "market_graph_diagnostic_diff.json"
    md_output = tmp_path / "market_graph_diagnostic_diff.md"
    old_path.write_text(json.dumps(old_report), encoding="utf-8")
    new_path.write_text(json.dumps(new_report), encoding="utf-8")

    write_diagnostic_diff_report(old_path, new_path, json_output, md_output)

    diff = json.loads(json_output.read_text(encoding="utf-8"))
    markdown = md_output.read_text(encoding="utf-8")
    assert diff["diagnostic_only"] is True
    assert "This diff compares saved diagnostic files only." in markdown


def test_diagnostic_diff_rejects_prohibited_tokens(fixture_snapshot) -> None:
    old_report = _diagnostic_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    new_report["multi_leg_constraints"]["multi_leg_constraints"][0]["review_reason"] = "POSSIBLE" + "_" + "ARB"

    try:
        build_diagnostic_diff_report(old_report, new_report)
    except SchemaValidationError:
        return
    raise AssertionError("diagnostic diff should reject prohibited values")


@pytest.mark.parametrize("token", ["trade", "fill", "size", "paper"])
def test_diagnostic_diff_rejects_bare_prohibited_tokens(fixture_snapshot, token: str) -> None:
    old_report = _diagnostic_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    new_report["multi_leg_constraints"]["multi_leg_constraints"][0]["review_reason"] = token

    try:
        build_diagnostic_diff_report(old_report, new_report)
    except SchemaValidationError:
        return
    raise AssertionError("diagnostic diff should reject bare prohibited values")


def test_diagnostic_diff_rejects_evaluator_fields(fixture_snapshot) -> None:
    old_report = _diagnostic_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    new_report["formula_diagnostics"]["formula_diagnostics"][0]["profit_usd"] = 1

    try:
        build_diagnostic_diff_report(old_report, new_report)
    except (SchemaValidationError, ValueError):
        return
    raise AssertionError("diagnostic diff should reject evaluator-like fields")


def test_diagnostic_diff_contract_rejects_disallowed_actions(fixture_snapshot) -> None:
    diff = build_diagnostic_diff_report(_diagnostic_report(fixture_snapshot), _diagnostic_report(fixture_snapshot))
    diff["allowed_actions"] = ["WATCH", "POSSIBLE" + "_" + "ARB"]

    try:
        validate_diagnostic_diff_contract(diff)
    except SchemaValidationError:
        return
    raise AssertionError("diagnostic diff should reject disallowed action labels")


def test_written_diagnostic_diff_contains_no_prohibited_tokens(tmp_path, fixture_snapshot) -> None:
    old_report = _diagnostic_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    old_path = tmp_path / "old_diagnostics.json"
    new_path = tmp_path / "new_diagnostics.json"
    json_output = tmp_path / "market_graph_diagnostic_diff.json"
    md_output = tmp_path / "market_graph_diagnostic_diff.md"
    old_path.write_text(json.dumps(old_report), encoding="utf-8")
    new_path.write_text(json.dumps(new_report), encoding="utf-8")

    write_diagnostic_diff_report(old_path, new_path, json_output, md_output)

    combined = json_output.read_text(encoding="utf-8") + md_output.read_text(encoding="utf-8")
    for token in PROHIBITED_DIFF_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", combined, flags=re.IGNORECASE) is None
