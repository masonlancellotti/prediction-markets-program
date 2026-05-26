from __future__ import annotations

import json

import pytest

import scan
from graph_engine.reporting.schema_validation import SchemaValidationError
from graph_engine.reporting.signal_persistence import (
    build_signal_persistence_report,
    validate_signal_persistence_report,
    write_signal_persistence_report,
)


def _indicator_report(*rows: dict) -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "signals": list(rows),
    }


def _indicator(
    signal_id: str,
    markets: list[str],
    severity: float,
    *,
    signal_type: str = "SUBSET_SUPERSET_PRICE_VIOLATION",
    confidence: str = "HIGH",
) -> dict:
    return {
        "signal_id": signal_id,
        "signal_type": signal_type,
        "markets_involved": list(markets),
        "relationship_evidence_type": "graph_edge:fixture",
        "severity_score": severity,
        "confidence_tier": confidence,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "review_blockers": ["not_evaluator_input"],
    }


def _probability_report(*rows: dict) -> dict:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "probability_constraints": list(rows),
    }


def _probability_constraint(constraint_id: str, markets: list[str], severity: float, gap: float) -> dict:
    return {
        "constraint_id": constraint_id,
        "constraint_type": "subset_superset",
        "markets_involved": list(markets),
        "inequality_checked": "P(subset) <= P(superset)",
        "severity_score": severity,
        "confidence_tier": "HIGH",
        "observed_gap": gap,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": ["WATCH", "MANUAL_REVIEW"],
        "review_blockers": ["not_evaluator_input"],
    }


def _rows_by_status(report: dict) -> dict[str, list[dict]]:
    by_status: dict[str, list[dict]] = {}
    for row in report["signal_persistence_rows"]:
        by_status.setdefault(row["persistence_status"], []).append(row)
    return by_status


def test_first_run_without_previous_file_succeeds(tmp_path) -> None:
    current_path = tmp_path / "current.json"
    missing_previous = tmp_path / "missing_previous.json"
    output = tmp_path / "signal_persistence.json"
    markdown = tmp_path / "signal_persistence.md"
    current_path.write_text(
        json.dumps(_indicator_report(_indicator("signal:a", ["m1", "m2"], 50.0))),
        encoding="utf-8",
    )

    report = write_signal_persistence_report([current_path], [missing_previous], output, markdown)

    assert output.exists()
    assert markdown.exists()
    assert report["summary"]["missing_previous_baseline_count"] == 1
    assert report["signal_persistence_rows"][0]["persistence_status"] == "MISSING_PREVIOUS_BASELINE"
    validate_signal_persistence_report(report)


def test_persistent_signal_is_detected() -> None:
    current = _indicator_report(_indicator("signal:a", ["m1", "m2"], 50.0))
    previous = _indicator_report(_indicator("signal:a", ["m1", "m2"], 50.0))

    report = build_signal_persistence_report([current], [previous])
    row = report["signal_persistence_rows"][0]

    assert row["persistence_status"] == "PERSISTENT_SIGNAL"
    assert row["current_severity"] == 50.0
    assert row["previous_severity"] == 50.0
    assert row["severity_delta"] == 0.0
    assert row["persistence_count"] == 2


def test_worsened_improved_and_resolved_severity_are_detected() -> None:
    current = _indicator_report(
        _indicator("signal:worse", ["a", "b"], 70.0),
        _indicator("signal:better", ["c", "d"], 20.0),
    )
    previous = _indicator_report(
        _indicator("signal:worse", ["a", "b"], 50.0),
        _indicator("signal:better", ["c", "d"], 40.0),
        _indicator("signal:resolved", ["e", "f"], 30.0),
    )

    report = build_signal_persistence_report([current], [previous])
    by_status = _rows_by_status(report)

    assert by_status["WORSENED_SIGNAL"][0]["severity_delta"] == 20.0
    assert by_status["IMPROVED_SIGNAL"][0]["severity_delta"] == -20.0
    assert by_status["RESOLVED_SIGNAL"][0]["current_severity"] == 0.0
    assert report["summary"]["worsened_count"] == 1
    assert report["summary"]["improved_count"] == 1
    assert report["summary"]["resolved_count"] == 1
    assert report["summary"]["top_worsening_signals"][0]["persistence_status"] == "WORSENED_SIGNAL"


def test_stable_keys_are_order_insensitive_for_market_lists() -> None:
    current = _indicator_report(_indicator("signal:a", ["m2", "m1"], 50.0))
    previous = _indicator_report(_indicator("signal:a", ["m1", "m2"], 50.0))

    report = build_signal_persistence_report([current], [previous])

    assert report["summary"]["persistent_count"] == 1
    assert report["summary"]["new_count"] == 0
    assert report["summary"]["resolved_count"] == 0


def test_probability_constraint_gap_is_preserved() -> None:
    current = _probability_report(_probability_constraint("constraint:a", ["m1", "m2"], 75.0, 0.12))
    previous = _probability_report(_probability_constraint("constraint:a", ["m1", "m2"], 70.0, 0.08))

    report = build_signal_persistence_report([current], [previous])
    row = report["signal_persistence_rows"][0]

    assert row["persistence_status"] == "WORSENED_SIGNAL"
    assert row["current_gap"] == 0.12
    assert row["previous_gap"] == 0.08
    assert row["current_confidence"] == "HIGH"


def test_output_remains_diagnostic_only_with_capped_actions() -> None:
    report = build_signal_persistence_report(
        [_indicator_report(_indicator("signal:a", ["m1", "m2"], 50.0))],
        [_indicator_report(_indicator("signal:a", ["m1", "m2"], 50.0))],
    )

    assert report["diagnostic_only"] is True
    assert report["affects_evaluator_gates"] is False
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    for row in report["signal_persistence_rows"]:
        assert row["diagnostic_only"] is True
        assert row["affects_evaluator_gates"] is False
        assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]

    report["signal_persistence_rows"][0]["allowed_actions"] = ["WATCH", "EXECUTE"]
    with pytest.raises(SchemaValidationError):
        validate_signal_persistence_report(report)


def test_scan_command_writes_signal_persistence_report(tmp_path) -> None:
    current_path = tmp_path / "current.json"
    output = tmp_path / "signal_persistence.json"
    markdown = tmp_path / "signal_persistence.md"
    current_path.write_text(
        json.dumps(_indicator_report(_indicator("signal:a", ["m1", "m2"], 50.0))),
        encoding="utf-8",
    )

    result = scan.main(
        [
            "write-signal-persistence-report",
            "--current",
            str(current_path),
            "--previous",
            str(tmp_path / "missing.json"),
            "--json-output",
            str(output),
            "--markdown-output",
            str(markdown),
        ]
    )

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    validate_signal_persistence_report(payload)
