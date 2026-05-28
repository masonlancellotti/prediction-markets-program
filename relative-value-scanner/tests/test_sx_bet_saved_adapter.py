import json
from datetime import datetime, timezone
from pathlib import Path

from relative_value.sx_bet_saved_adapter import (
    READINESS_TIER,
    build_sx_bet_saved_normalization_report,
    write_sx_bet_saved_normalization_files,
)


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def test_saved_sx_bet_fixture_normalizes(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "venues" / "fixtures"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "sx_bet_research_sample.json").write_text(json.dumps(_raw_fixture()), encoding="utf-8")

    report = build_sx_bet_saved_normalization_report(
        project_root=tmp_path,
        input_dir=tmp_path / "reports",
        generated_at=NOW,
    )

    assert report["coverage"]["summary"]["rows_read"] == 1
    assert report["coverage"]["summary"]["normalized_records"] == 1
    record = report["records"][0]
    assert record["venue"] == "sx_bet"
    assert record["market_id"] == "0xabc123"
    assert record["event_id"] == "S1779385200:celtics:knicks"
    assert record["sport"] == "Basketball"
    assert record["league"] == "NBA"
    assert record["readiness_tier"] == READINESS_TIER


def test_missing_market_id_blocks_identity_readiness(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_research_snapshot(
        reports / "sx_bet_research_snapshot.json",
        [
            {
                "event_title": "Missing id market",
                "sport": "Basketball",
                "league": "NBA",
                "starts_at": "2026-05-21T23:00:00Z",
                "outcome_one_name": "A",
                "outcome_two_name": "B",
            }
        ],
    )

    report = build_sx_bet_saved_normalization_report(project_root=tmp_path, input_dir=reports, generated_at=NOW)

    assert report["records"][0]["market_id"] is None
    assert "missing_market_id" in report["records"][0]["blockers"]


def test_missing_event_time_is_reported(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_research_snapshot(
        reports / "sx_bet_research_snapshot.json",
        [
            {
                "market_hash": "0xmissingtime",
                "event_title": "Missing time",
                "sport": "Basketball",
                "league": "NBA",
                "outcome_one_name": "A",
                "outcome_two_name": "B",
            }
        ],
    )

    report = build_sx_bet_saved_normalization_report(project_root=tmp_path, input_dir=reports, generated_at=NOW)

    assert report["coverage"]["summary"]["event_time_present"] == 0
    assert "missing_event_time" in report["records"][0]["blockers"]


def test_quote_reference_fields_do_not_become_executable_depth(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "venues" / "fixtures"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "sx_bet_research_sample.json").write_text(json.dumps(_raw_fixture()), encoding="utf-8")

    report = build_sx_bet_saved_normalization_report(
        project_root=tmp_path,
        input_dir=tmp_path / "reports",
        generated_at=NOW,
    )
    record = report["records"][0]

    assert record["quote"]["quote_fields_present"] is True
    assert record["depth"]["depth_fields_present"] is True
    assert record["quote"]["executable_quote"] is False
    assert record["depth"]["executable_depth"] is False
    assert "sx_bet_depth_units_not_executable" in record["blockers"]
    assert "sx_bet_quote_fields_research_only" in record["blockers"]


def test_output_is_diagnostic_only_and_not_evaluator_input(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "venues" / "fixtures"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "sx_bet_research_sample.json").write_text(json.dumps(_raw_fixture()), encoding="utf-8")

    report = build_sx_bet_saved_normalization_report(
        project_root=tmp_path,
        input_dir=tmp_path / "reports",
        generated_at=NOW,
    )
    encoded = json.dumps(report)

    assert report["safety"]["diagnostic_only"] is True
    assert report["safety"]["affects_evaluator_gates"] is False
    assert all(record["diagnostic_only"] is True for record in report["records"])
    assert all(record["affects_evaluator_gates"] is False for record in report["records"])
    assert all(record["is_executable"] is False for record in report["records"])
    assert "PAPER_CANDIDATE" not in encoded


def test_unknown_saved_shapes_fail_closed(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "sx_bet_unknown.json").write_text(
        json.dumps({"source": "sx_bet_unknown", "items": [{"market_hash": "0xnope"}]}),
        encoding="utf-8",
    )

    report = build_sx_bet_saved_normalization_report(project_root=tmp_path, input_dir=reports, generated_at=NOW)

    assert report["coverage"]["summary"]["normalized_records"] == 0
    assert report["coverage"]["summary"]["warning_count"] == 1
    assert report["warnings"][0]["blocker"] == "unsupported_sx_bet_saved_shape"


def test_write_outputs_json_and_coverage(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "venues" / "fixtures"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "sx_bet_research_sample.json").write_text(json.dumps(_raw_fixture()), encoding="utf-8")
    json_output = tmp_path / "reports" / "sx_bet_normalized_draft.json"
    coverage_output = tmp_path / "reports" / "sx_bet_normalized_draft_coverage.json"

    outputs = write_sx_bet_saved_normalization_files(
        project_root=tmp_path,
        input_dir=tmp_path / "reports",
        json_output=json_output,
        coverage_output=coverage_output,
        generated_at=NOW,
    )

    assert outputs["coverage"]["summary"]["normalized_records"] == 1
    assert json_output.exists()
    assert coverage_output.exists()


def _write_research_snapshot(path: Path, rows: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "source": "sx_bet_research",
                "source_id": "sx_bet",
                "schema_kind": "sx_bet_research_snapshot_v1",
                "research_markets": rows,
            }
        ),
        encoding="utf-8",
    )


def _raw_fixture() -> dict:
    return {
        "markets": [
            {
                "marketHash": "0xabc123",
                "eventName": "Boston Celtics vs New York Knicks",
                "leagueLabel": "NBA",
                "sportLabel": "Basketball",
                "sportXeventId": "S1779385200:celtics:knicks",
                "type": 52,
                "line": None,
                "mainLine": True,
                "status": "ACTIVE",
                "gameTime": "2026-05-21T23:00:00Z",
                "outcomeOneName": "Boston Celtics",
                "outcomeTwoName": "New York Knicks",
                "outcomeVoidName": "Game cancelled or voided",
                "settlementSource": "official league result",
                "settlementRule": "Moneyline market; void if event is cancelled or neither outcome is valid.",
            }
        ],
        "orders": [
            {
                "orderHash": "0xorder1",
                "marketHash": "0xabc123",
                "isMakerBettingOutcomeOne": False,
                "percentageOdds": "42000000000000000000",
                "totalBetSize": "758990000",
                "fillAmount": "0",
            },
            {
                "orderHash": "0xorder2",
                "marketHash": "0xabc123",
                "isMakerBettingOutcomeOne": True,
                "percentageOdds": "52000000000000000000",
                "totalBetSize": "999650000",
                "fillAmount": "0",
            },
        ],
    }
