from __future__ import annotations

from copy import deepcopy
import json

from graph_engine.consistency.runner import run_consistency_checks
from graph_engine.reporting.hint_diff import build_hint_diff_report, write_hint_diff_report
from graph_engine.reporting.hints import build_relative_value_hints_report
from graph_engine.reporting.schema_validation import SchemaValidationError


def _valid_report(fixture_snapshot) -> dict:
    return build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))


def _by_id(report: dict) -> dict[str, dict]:
    return {hint["graph_hint_id"]: hint for hint in report["hints"]}


def test_hint_diff_detects_new_and_removed_hints(fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    removed = new_report["hints"].pop(0)
    added = deepcopy(new_report["hints"][0])
    added["graph_hint_id"] = "hint:fixture_added_structural_hint"
    new_report["hints"].append(added)

    diff = build_hint_diff_report(old_report, new_report)

    assert diff["diagnostic_only"] is True
    assert diff["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert {item["graph_hint_id"] for item in diff["removed_hints"]} == {removed["graph_hint_id"]}
    assert {item["graph_hint_id"] for item in diff["new_hints"]} == {added["graph_hint_id"]}


def test_hint_diff_detects_subset_to_ambiguous_downgrade(fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    target = _by_id(new_report)["hint:SUBSET_OVER_SUPERSET:edge_btc_120k_subset_btc_100k_same_window"]
    target["relation_type"] = "AMBIGUOUS_WORDING"
    target["direction"] = "none"
    target["hard_bound_type"] = "none"

    diff = build_hint_diff_report(old_report, new_report)
    changes = {(change["field"], change["old_value"], change["new_value"]) for change in diff["field_changes"]}

    assert ("relation_type", "SUBSET", "AMBIGUOUS_WORDING") in changes
    assert ("direction", "source_implies_target", "none") in changes
    assert ("hard_bound_type", "upper_probability_bound", "none") in changes


def test_hint_diff_detects_blocker_and_settlement_proof_changes(fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    ambiguous = _by_id(new_report)["hint:AMBIGUOUS_WORDING:edge_btc_120k_subset_btc_100k_different_window"]
    same_payoff = _by_id(new_report)["hint:REWORD_MISMATCH:edge_openai_1t_same_event_cross_venue"]
    ambiguous["blockers"].append("fixture_added_review_blocker")
    same_payoff["settlement_source_proven"] = False

    diff = build_hint_diff_report(old_report, new_report)
    fields = {change["field"] for change in diff["field_changes"]}

    assert "blockers" in fields
    assert "settlement_source_proven" in fields


def test_hint_diff_detects_cap_changes(fixture_snapshot) -> None:
    old_report = _valid_report(fixture_snapshot)
    new_report = deepcopy(old_report)
    target = _by_id(new_report)["hint:AMBIGUOUS_WORDING:edge_btc_120k_subset_btc_100k_different_window"]
    target["max_action_cap"] = "MANUAL_REVIEW"

    diff = build_hint_diff_report(old_report, new_report)

    assert diff["summary"]["upgraded_count"] == 1
    assert diff["upgraded_hints"][0]["graph_hint_id"] == target["graph_hint_id"]


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
