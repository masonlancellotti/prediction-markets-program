from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from graph_engine.consistency.runner import run_consistency_checks
from graph_engine.reporting.hints import build_relative_value_hints_report
from graph_engine.reporting.schema_validation import (
    SchemaValidationError,
    validate_json_schema_subset,
    validate_relative_value_hint_contract,
)


SCHEMA_PATH = Path("schemas/relative_value_hint.schema.json")
GENERATED_HINT_PATH = Path("reports/market_graph_relative_value_hints.json")


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _valid_report(fixture_snapshot) -> dict:
    return build_relative_value_hints_report(fixture_snapshot, run_consistency_checks(fixture_snapshot))


def _assert_invalid(report: dict, schema: dict) -> None:
    try:
        validate_json_schema_subset(report, schema)
    except SchemaValidationError:
        return
    raise AssertionError("schema validation should fail")


def _assert_contract_invalid(report: dict) -> None:
    try:
        validate_relative_value_hint_contract(report)
    except SchemaValidationError:
        return
    raise AssertionError("hint contract validation should fail")


def test_relative_value_hint_report_validates_against_schema(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    validate_json_schema_subset(report, _schema())
    validate_relative_value_hint_contract(report)


def test_generated_relative_value_hint_file_validates_against_schema() -> None:
    report = json.loads(GENERATED_HINT_PATH.read_text(encoding="utf-8"))

    validate_json_schema_subset(report, _schema())


def test_schema_rejects_non_diagnostic_report(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    report["diagnostic_only"] = False

    _assert_invalid(report, _schema())


def test_schema_rejects_unknown_action_labels(fixture_snapshot) -> None:
    schema = _schema()
    for bad_action in ["PA" + "PER", "PAPER_CANDIDATE", "POSSIBLE" + "_" + "ARB"]:
        report = _valid_report(fixture_snapshot)
        report["allowed_actions"] = ["WATCH", bad_action]

        _assert_invalid(report, schema)


def test_schema_rejects_execution_like_action_cap(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    report["hints"][0]["max_action_cap"] = "EXEC" + "UTION"

    _assert_invalid(report, _schema())


def test_schema_rejects_relation_type_action_vocabulary_mismatch(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    report["hints"][0]["relation_type"] = "MANUAL_REVIEW"

    _assert_invalid(report, _schema())


def test_schema_rejects_exact_same_payoff_relation_type(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    report["hints"][0]["relation_type"] = "EXACT_SAME_PAYOFF"

    _assert_invalid(report, _schema())


def test_schema_rejects_exact_same_payoff_relation_count(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    report["counts_by_relation_type"]["EXACT_SAME_PAYOFF"] = 1

    _assert_invalid(report, _schema())


def test_schema_rejects_extra_unknown_hint_field(fixture_snapshot) -> None:
    schema = _schema()
    for field_name in [
        "PAPER_CANDIDATE",
        "POSSIBLE_ARB",
        "executable",
        "order",
        "fill_size",
        "size_usd",
        "pnl",
        "profit_usd",
        "trade_permission",
    ]:
        report = _valid_report(fixture_snapshot)
        report["hints"][0][field_name] = "unexpected"

        _assert_invalid(report, schema)


def test_schema_rejects_extra_unknown_wrapper_field(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    report["unknown_contract_field"] = deepcopy({"nested": True})

    _assert_invalid(report, _schema())


def test_hint_contract_rejects_non_diagnostic_hint(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    report["hints"][0]["diagnostic_only"] = False

    _assert_contract_invalid(report)


def test_hint_contract_rejects_hint_action_list_drift(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    report["hints"][0]["allowed_actions"] = ["WATCH"]

    _assert_contract_invalid(report)


def test_hint_contract_rejects_structural_exact_same_payoff_claims(fixture_snapshot) -> None:
    for relation_type in ["SUBSET", "SUPERSET", "COMPLEMENT", "MUTUALLY_EXCLUSIVE", "EXHAUSTIVE_GROUP"]:
        report = _valid_report(fixture_snapshot)
        report["hints"][0]["relation_type"] = relation_type
        report["hints"][0]["hard_bound_type"] = "same_payoff_equality_if_settlement_proven"

        _assert_contract_invalid(report)


def test_hint_contract_rejects_exact_same_payoff_from_old_file(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    report["hints"][0]["relation_type"] = "EXACT_SAME_PAYOFF"

    _assert_contract_invalid(report)


def test_hint_contract_rejects_same_payoff_without_settlement_source_proof(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    same_payoff = next(hint for hint in report["hints"] if hint["relation_type"] == "SAME_PAYOFF")
    same_payoff["settlement_source_proven"] = False

    _assert_contract_invalid(report)


def test_hint_contract_rejects_same_payoff_with_missing_settlement_source_proof(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    same_payoff = next(hint for hint in report["hints"] if hint["relation_type"] == "SAME_PAYOFF")
    del same_payoff["settlement_source_proven"]

    _assert_contract_invalid(report)


def test_hint_contract_rejects_same_payoff_with_wrong_hard_bound_type(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    same_payoff = next(hint for hint in report["hints"] if hint["relation_type"] == "SAME_PAYOFF")
    same_payoff["hard_bound_type"] = "none"

    _assert_contract_invalid(report)


def test_hint_contract_rejects_same_payoff_with_prohibited_field(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    same_payoff = next(hint for hint in report["hints"] if hint["relation_type"] == "SAME_PAYOFF")
    same_payoff["profit_usd"] = 1

    try:
        validate_json_schema_subset(report, _schema())
    except SchemaValidationError:
        return
    raise AssertionError("SAME_PAYOFF with prohibited field should fail schema validation")


def test_positive_diagnostic_hint_fixture_shape_passes(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    hint = deepcopy(report["hints"][0])
    positive_fixture = {
        "diagnostic_only": True,
        "banner": report["banner"],
        "snapshot_id": "positive-diagnostic-fixture",
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "hint_count": 1,
        "counts_by_relation_type": {hint["relation_type"]: 1},
        "counts_by_max_action_cap": {hint["max_action_cap"]: 1},
        "hints": [hint],
    }

    validate_json_schema_subset(positive_fixture, _schema())
    validate_relative_value_hint_contract(positive_fixture)
