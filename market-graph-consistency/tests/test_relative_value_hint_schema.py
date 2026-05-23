from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from graph_engine.consistency.runner import run_consistency_checks
from graph_engine.reporting.hints import build_relative_value_hints_report
from graph_engine.reporting.schema_validation import SchemaValidationError, validate_json_schema_subset


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


def test_relative_value_hint_report_validates_against_schema(fixture_snapshot) -> None:
    validate_json_schema_subset(_valid_report(fixture_snapshot), _schema())


def test_generated_relative_value_hint_file_validates_against_schema() -> None:
    report = json.loads(GENERATED_HINT_PATH.read_text(encoding="utf-8"))

    validate_json_schema_subset(report, _schema())


def test_schema_rejects_non_diagnostic_report(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    report["diagnostic_only"] = False

    _assert_invalid(report, _schema())


def test_schema_rejects_unknown_action_labels(fixture_snapshot) -> None:
    schema = _schema()
    for bad_action in ["PA" + "PER", "POSSIBLE" + "_" + "ARB"]:
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


def test_schema_rejects_extra_unknown_hint_field(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    report["hints"][0]["unknown_contract_field"] = "unexpected"

    _assert_invalid(report, _schema())


def test_schema_rejects_extra_unknown_wrapper_field(fixture_snapshot) -> None:
    report = _valid_report(fixture_snapshot)
    report["unknown_contract_field"] = deepcopy({"nested": True})

    _assert_invalid(report, _schema())
