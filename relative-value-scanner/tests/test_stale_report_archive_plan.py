from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.stale_report_archive_plan import (
    CLASS_CURRENT_REPORT_KEEP,
    CLASS_GENERATED_CURRENT_REPORT,
    CLASS_LEGACY_CANDIDATE_ARTIFACT,
    CLASS_STALE_EVALUATOR_OUTPUT,
    CLASS_STALE_PIPELINE_SUMMARY,
    build_stale_report_archive_plan,
)


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
ACTION = "PAPER_CANDIDATE"


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _report(input_dir: Path) -> dict:
    return build_stale_report_archive_plan(input_dir=input_dir, generated_at=NOW)


def _stale_evaluator() -> dict:
    return {
        "schema_version": 1,
        "source": "paper_candidate_evaluator",
        "generated_at": "2026-05-21T00:00:00+00:00",
        "counts_by_action": {ACTION: 1, "WATCH": 0, "MANUAL_REVIEW": 0},
        "ledger": [
            {
                "candidate_id": "old-poly__old-kalshi",
                "action": ACTION,
                "polymarket": {"market_id": "old-poly"},
                "kalshi": {"ticker": "old-kalshi"},
            }
        ],
    }


def test_stale_evaluator_report_is_planned_for_archive(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write(reports / "old_paper_candidates.json", _stale_evaluator())

    report = _report(reports)
    rows = {row["relative_path"]: row for row in report["files"]}

    row = rows["old_paper_candidates.json"]
    assert row["classification"] == CLASS_STALE_EVALUATOR_OUTPUT
    assert row["archive_recommended"] is True
    assert "Move-Item -LiteralPath" in row["suggested_move_command"]
    assert "reports\\archive\\2026-05-25" in row["suggested_move_command"] or "reports/archive/2026-05-25" in row["suggested_move_command"]


def test_current_ops_status_is_not_archived(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write(
        reports / "relative_value_ops_status.json",
        {
            "schema_version": 1,
            "source": "relative_value_ops_status_v1",
            "generated_at": "2026-05-25T11:00:00+00:00",
            "summary": {},
        },
    )

    report = _report(reports)
    row = {row["relative_path"]: row for row in report["files"]}["relative_value_ops_status.json"]

    assert row["classification"] in {CLASS_CURRENT_REPORT_KEEP, CLASS_GENERATED_CURRENT_REPORT}
    assert row["archive_recommended"] is False
    assert row["suggested_move_command"] is None


def test_plan_is_non_mutating_and_flags_pipeline_references(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    stale_path = _write(reports / "mlb_kxmlb_48h_unitok_paper_candidates.json", _stale_evaluator())
    summary_path = _write(
        reports / "mlb_kxmlb_48h_unitok_pipeline_summary.json",
        {
            "schema_version": 1,
            "source": "targeted_pipeline_runner",
            "summary": {"evaluator_counts": {ACTION: 1}},
            "paths": {"paper_candidates": str(stale_path)},
        },
    )

    before = {stale_path: stale_path.exists(), summary_path: summary_path.exists()}
    report = _report(reports)

    assert before == {stale_path: True, summary_path: True}
    assert stale_path.exists()
    assert summary_path.exists()
    assert not (reports / "archive").exists()
    rows = {row["relative_path"]: row for row in report["files"]}
    assert rows["mlb_kxmlb_48h_unitok_paper_candidates.json"]["classification"] in {
        CLASS_STALE_EVALUATOR_OUTPUT,
        CLASS_LEGACY_CANDIDATE_ARTIFACT,
    }
    assert rows["mlb_kxmlb_48h_unitok_pipeline_summary.json"]["classification"] == CLASS_STALE_PIPELINE_SUMMARY
    assert report["summary"]["files_moved_or_deleted"] is False


def test_generated_move_commands_are_safe_strings_only(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write(reports / "old_paper_candidates.json", _stale_evaluator())

    report = _report(reports)

    assert report["suggested_commands"]
    for command in report["suggested_commands"]:
        text = command["command"]
        assert command["executes_in_plan"] is False
        assert command["safe_string_only"] is True
        assert "Remove-Item" not in text
        assert "del " not in text.lower()
        assert "&&" not in text
        assert ";" not in text
        assert "*" not in text


def test_no_candidate_promotion_and_cli_writes_outputs(tmp_path: Path, capsys) -> None:
    reports = tmp_path / "reports"
    _write(reports / "old_paper_candidates.json", _stale_evaluator())
    json_output = reports / "stale_report_archive_plan.json"
    markdown_output = reports / "stale_report_archive_plan.md"

    result = scan.main(
        [
            "plan-stale-report-archive",
            "--input-dir",
            str(reports),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )
    stdout = capsys.readouterr().out

    assert result == 0
    assert "stale_report_archive_plan_status=OK" in stdout
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["source"] == "stale_report_archive_plan_v1"
    assert payload["safety"]["candidate_rows_created"] is False
    assert payload["summary"]["candidate_promotion"] is False
    assert "PAPER_CANDIDATE" not in json.dumps(payload)
    assert markdown_output.exists()
