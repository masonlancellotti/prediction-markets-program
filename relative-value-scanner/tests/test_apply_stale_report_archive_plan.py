from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.stale_report_archive_plan import (
    APPLIED_REPORT_SOURCE,
    apply_stale_report_archive_plan,
    write_stale_report_archive_plan_files,
)


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
ACTION = "PAPER_CANDIDATE"


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _stale_evaluator() -> dict:
    return {
        "schema_version": 1,
        "source": "paper_candidate_evaluator",
        "generated_at": "2026-05-21T00:00:00+00:00",
        "counts_by_action": {ACTION: 1},
        "ledger": [
            {
                "candidate_id": "old-poly__old-kalshi",
                "action": ACTION,
                "polymarket": {"market_id": "old-poly"},
                "kalshi": {"ticker": "old-kalshi"},
            }
        ],
    }


def _write_plan(reports: Path) -> Path:
    plan_path = reports / "stale_report_archive_plan.json"
    write_stale_report_archive_plan_files(
        input_dir=reports,
        json_output=plan_path,
        markdown_output=reports / "stale_report_archive_plan.md",
        generated_at=NOW,
    )
    return plan_path


def test_dry_run_modifies_nothing(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    stale_file = _write(reports / "old_paper_candidates.json", _stale_evaluator())
    plan_path = _write_plan(reports)

    report = apply_stale_report_archive_plan(plan_path=plan_path, apply=False, generated_at=NOW)

    assert report["mode"] == "dry_run"
    assert report["summary"]["planned_move_count"] == 1
    assert report["summary"]["applied_move_count"] == 0
    assert stale_file.exists()
    assert not (reports / "archive").exists()
    assert not (reports / "stale_report_archive_applied.json").exists()


def test_apply_moves_files_to_archive(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    stale_file = _write(reports / "old_paper_candidates.json", _stale_evaluator())
    plan_path = _write_plan(reports)
    applied_output = reports / "stale_report_archive_applied.json"

    report = apply_stale_report_archive_plan(
        plan_path=plan_path,
        applied_output=applied_output,
        apply=True,
        generated_at=NOW,
    )

    archived = reports / "archive" / "2026-05-25" / "old_paper_candidates.json"
    assert report["status"] == "APPLIED"
    assert report["summary"]["applied_move_count"] == 1
    assert not stale_file.exists()
    assert archived.exists()
    payload = json.loads(applied_output.read_text(encoding="utf-8"))
    assert payload["source"] == APPLIED_REPORT_SOURCE
    assert payload["safety"]["files_deleted"] is False
    assert payload["safety"]["uses_git_mv"] is False


def test_second_apply_is_noop(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write(reports / "old_paper_candidates.json", _stale_evaluator())
    plan_path = _write_plan(reports)
    applied_output = reports / "stale_report_archive_applied.json"

    apply_stale_report_archive_plan(
        plan_path=plan_path,
        applied_output=applied_output,
        apply=True,
        generated_at=NOW,
    )
    second = apply_stale_report_archive_plan(
        plan_path=plan_path,
        applied_output=applied_output,
        apply=True,
        generated_at=NOW,
    )

    assert second["status"] == "NOOP"
    assert second["summary"]["applied_move_count"] == 0
    assert second["summary"]["noop_move_count"] == 1
    assert second["summary"]["idempotent_noop"] is True


def test_unknown_paths_are_refused(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir(parents=True)
    plan_path = reports / "stale_report_archive_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "stale_report_archive_plan_v1",
                "input_dir": str(reports),
                "archive_dir": str(reports / "archive" / "2026-05-25"),
                "files": [
                    {
                        "relative_path": "../outside.json",
                        "source_file": str(tmp_path / "outside.json"),
                        "suggested_archive_path": str(reports / "archive" / "2026-05-25" / "outside.json"),
                        "archive_recommended": True,
                        "classification": "stale_evaluator_output",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = apply_stale_report_archive_plan(plan_path=plan_path, apply=True, generated_at=NOW)

    assert report["status"] == "REFUSED"
    assert report["summary"]["refused_move_count"] == 1
    assert report["summary"]["applied_move_count"] == 0
    assert not (reports / "archive").exists()


def test_cli_dry_run_prints_commands_without_moving(tmp_path: Path, capsys) -> None:
    reports = tmp_path / "reports"
    stale_file = _write(reports / "old_paper_candidates.json", _stale_evaluator())
    plan_path = _write_plan(reports)

    rc = scan.main(["apply-stale-report-archive-plan", "--plan", str(plan_path), "--dry-run"])
    stdout = capsys.readouterr().out

    assert rc == 0
    assert "stale_report_archive_apply_status=DRY_RUN" in stdout
    assert "Move-Item -LiteralPath" in stdout
    assert stale_file.exists()
