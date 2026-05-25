from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph_engine.loader import load_fixture_markets
from graph_engine.reporting.json_report import PROHIBITED_VIOLATION_FIELDS
from graph_engine.reporting.schema_validation import SchemaValidationError
from graph_engine.reporting.venue_native_groups import (
    build_venue_native_exhaustive_groups_report,
    validate_venue_native_exhaustive_groups_report,
    write_venue_native_exhaustive_groups_report,
)
from graph_engine.snapshot_loader import load_schema_v1_snapshots


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROHIBITED_TOKENS = sorted(PROHIBITED_VIOLATION_FIELDS)


def _fixture_report() -> dict:
    snapshot, source_metadata = load_fixture_markets(PROJECT_ROOT / "venues" / "fixtures")
    return build_venue_native_exhaustive_groups_report(snapshot, source_metadata)


def _row_by_group(report: dict, group_id: str) -> dict:
    rows = {row["group_id"]: row for row in report["venue_native_exhaustive_groups"]}
    return rows[group_id]


def test_complete_venue_native_exhaustive_group_is_manual_review_only() -> None:
    report = _fixture_report()

    row = _row_by_group(report, "native_city_mayor_2026_winner")

    assert row["diagnostic_only"] is True
    assert row["affects_evaluator_gates"] is False
    assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert row["max_action_cap"] == "MANUAL_REVIEW"
    assert row["market_ids"] == [
        "fixture_native:city_mayor_alice",
        "fixture_native:city_mayor_bob",
        "fixture_native:city_mayor_carol",
    ]
    assert row["missing_outcome_blockers"] == []
    assert row["completeness_evidence"]["event_id"] == "native_city_mayor_2026"
    assert row["completeness_evidence"]["outcome_list"] == ["Alice", "Bob", "Carol"]
    assert row["completeness_evidence"]["completeness_marker"] == "complete"
    assert row["completeness_evidence"]["source_files"] == ["venue_native_exhaustive_groups.json"]


def test_incomplete_venue_native_exhaustive_group_is_watch_or_blocked() -> None:
    report = _fixture_report()

    row = _row_by_group(report, "native_award_2026_winner")

    assert row["max_action_cap"] == "WATCH"
    assert "missing_native_completeness_marker" in row["missing_outcome_blockers"]
    assert "missing_native_outcomes:Nominee C" in row["missing_outcome_blockers"]


def test_title_similarity_alone_cannot_create_exhaustive_evidence() -> None:
    report = _fixture_report()
    all_market_ids = {
        market_id
        for row in report["venue_native_exhaustive_groups"]
        for market_id in row["market_ids"]
    }

    assert "fixture_native:similar_title_alpha" not in all_market_ids
    assert "fixture_native:similar_title_beta" not in all_market_ids


def test_venue_native_group_with_other_none_outcome_is_kept() -> None:
    report = _fixture_report()

    row = _row_by_group(report, "native_policy_result_2026_outcome")

    assert row["max_action_cap"] == "MANUAL_REVIEW"
    assert row["completeness_evidence"]["outcome_list"] == ["Pass", "Fail", "Other/None"]
    assert row["completeness_evidence"]["has_other_or_none_outcome"] is True
    assert row["missing_outcome_blockers"] == []


def test_saved_file_venue_native_exhaustive_metadata_is_detected(tmp_path) -> None:
    snapshot_path = tmp_path / "saved_native_groups.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "snapshot_id": "saved-native-groups-001",
                "as_of": "2026-05-19T18:20:00+00:00",
                "venue": "saved_fixture",
                "normalized_markets": [
                    {
                        "market_id": "saved_fixture:committee_yes",
                        "title": "Committee result yes",
                        "yes_price": 0.62,
                        "venue_native_event_id": "saved_committee_2026",
                        "venue_native_group_id": "saved_committee_2026_result",
                        "venue_native_outcome": "Yes",
                        "venue_native_outcome_list": ["Yes", "No"],
                        "venue_native_completeness": "complete",
                    },
                    {
                        "market_id": "saved_fixture:committee_no",
                        "title": "Committee result no",
                        "yes_price": 0.38,
                        "venue_native_event_id": "saved_committee_2026",
                        "venue_native_group_id": "saved_committee_2026_result",
                        "venue_native_outcome": "No",
                        "venue_native_outcome_list": ["Yes", "No"],
                        "venue_native_completeness": "complete",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    snapshot, source_metadata = load_schema_v1_snapshots(snapshot_paths=[snapshot_path])

    report = build_venue_native_exhaustive_groups_report(snapshot, source_metadata)
    row = _row_by_group(report, "saved_committee_2026_result")

    assert row["venue"] == "saved_fixture"
    assert row["max_action_cap"] == "MANUAL_REVIEW"
    assert row["completeness_evidence"]["source_files"] == ["saved_native_groups.json"]


def test_venue_native_exhaustive_group_report_validates_before_writing(tmp_path) -> None:
    snapshot, source_metadata = load_fixture_markets(PROJECT_ROOT / "venues" / "fixtures")
    json_output = tmp_path / "venue_native_exhaustive_groups.json"
    md_output = tmp_path / "venue_native_exhaustive_groups.md"

    report = write_venue_native_exhaustive_groups_report(snapshot, source_metadata, json_output, md_output)

    assert json_output.exists()
    assert md_output.exists()
    assert json.loads(json_output.read_text(encoding="utf-8")) == report
    validate_venue_native_exhaustive_groups_report(report)


@pytest.mark.parametrize("token", ["trade", "fill", "size", "paper"])
def test_venue_native_exhaustive_group_report_rejects_bare_prohibited_tokens(token: str) -> None:
    report = _fixture_report()
    report["venue_native_exhaustive_groups"][0]["reason_for_review"] = token

    with pytest.raises(SchemaValidationError):
        validate_venue_native_exhaustive_groups_report(report)


def test_venue_native_exhaustive_group_report_contains_no_prohibited_tokens() -> None:
    report = _fixture_report()
    rendered = json.dumps(report).lower()

    for token in PROHIBITED_TOKENS:
        assert token.lower() not in rendered
