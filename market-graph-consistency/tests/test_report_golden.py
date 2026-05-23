from __future__ import annotations

import json
from pathlib import Path
import re

from graph_engine.consistency.runner import run_consistency_checks
from graph_engine.reporting.json_report import build_json_report, write_json_report
from graph_engine.reporting.md_report import build_markdown_report, write_markdown_report
from graph_engine.snapshot_loader import load_schema_v1_snapshots


PROHIBITED_REPORT_TOKENS = ["TR" + "ADE", "PA" + "PER", "POSSIBLE" + "_" + "ARB"]
EXPECTED_REPORT = Path(__file__).parent / "fixtures" / "expected_report.md"
SNAPSHOT_FIXTURES = Path(__file__).parent / "fixtures"


def test_report_golden_stable_sections(fixture_snapshot) -> None:
    violations = run_consistency_checks(fixture_snapshot)
    markdown = build_markdown_report(fixture_snapshot, violations)

    assert "# Graph Consistency Summary" in markdown
    assert "## IMPLICATION_VIOLATION" in markdown
    assert "## SUM_OVER_ONE" in markdown
    assert "Highest action: `MANUAL_REVIEW`" in markdown
    assert "`manifold:openai_first_agi_2027` | yes=0.460" in markdown


def test_markdown_report_matches_strict_golden(fixture_snapshot) -> None:
    violations = run_consistency_checks(fixture_snapshot)

    assert build_markdown_report(fixture_snapshot, violations) == EXPECTED_REPORT.read_text(encoding="utf-8")


def test_markdown_highest_action_empty_report_is_ignore(fixture_snapshot) -> None:
    markdown = build_markdown_report(fixture_snapshot, [])

    report = build_json_report(fixture_snapshot, [])

    assert "Highest action: `IGNORE`" in markdown
    assert report["summary"]["highest_action"] == "IGNORE"


def test_markdown_report_uses_saved_snapshot_notes_for_scope() -> None:
    snapshot, _ = load_schema_v1_snapshots(
        snapshot_paths=[SNAPSHOT_FIXTURES / "schema_v1_snapshot_polymarket.json"]
    )

    markdown = build_markdown_report(snapshot, [])

    assert "- Scope: Read-only schema-v1 saved snapshot prototype." in markdown
    assert "No live ingestion or relationship extraction was performed." in markdown
    assert "offline fixture review only" not in markdown


def test_json_report_structure_schema_sanity(fixture_snapshot) -> None:
    violations = run_consistency_checks(fixture_snapshot)
    report = build_json_report(fixture_snapshot, violations, [{"file": "fixture.json"}])

    assert report["snapshot_id"] == fixture_snapshot.snapshot_id
    assert report["summary"]["market_count"] == 7
    assert report["summary"]["highest_action"] == "MANUAL_REVIEW"
    assert report["summary"]["counts_by_kind"]["SUM_OVER_ONE"] == 1
    assert report["notes"] == fixture_snapshot.notes
    assert report["violations"][0]["violation_id"]
    assert report["source_fixture_metadata"] == [{"file": "fixture.json"}]


def test_written_reports_contain_no_prohibited_tokens(tmp_path, fixture_snapshot) -> None:
    violations = run_consistency_checks(fixture_snapshot)
    json_path = tmp_path / "summary.json"
    md_path = tmp_path / "summary.md"

    write_json_report(fixture_snapshot, violations, json_path)
    write_markdown_report(fixture_snapshot, violations, md_path)

    combined = json_path.read_text(encoding="utf-8") + md_path.read_text(encoding="utf-8")
    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"]["highest_action"] == "MANUAL_REVIEW"
    for token in PROHIBITED_REPORT_TOKENS:
        assert re.search(rf"\b{token}\b", combined) is None
