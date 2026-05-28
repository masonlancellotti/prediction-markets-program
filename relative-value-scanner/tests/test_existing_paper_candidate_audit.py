from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import scan
from relative_value.existing_paper_candidate_audit import (
    DUPLICATE_ROW,
    FAILS_CURRENT_NORMALIZED_GATES,
    STALE_SOURCE_FILE,
    build_existing_paper_candidate_audit_report,
)


ACTION = "PAPER_CANDIDATE"


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _candidate(candidate_id="poly-1__KX-1", *, generated_at="2026-01-01T00:00:00+00:00"):
    return {
        "schema_version": 1,
        "source": "paper_candidate_evaluator",
        "generated_at": generated_at,
        "counts_by_action": {ACTION: 1, "WATCH": 0, "MANUAL_REVIEW": 0},
        "ledger": [
            {
                "schema_version": 1,
                "candidate_id": candidate_id,
                "action": ACTION,
                "opportunity_class": "strict_cross_venue_equivalent",
                "contract_relationship": {
                    "source": "same_payoff_board_v1",
                    "same_payoff": True,
                    "blocking_reasons": [],
                    "same_payoff_board_evidence": {"classifier_version": "same-payoff-board-v1"},
                },
                "polymarket": {
                    "market_id": "poly-1",
                    "question": "Will Team win?",
                    "best_bid": 0.15,
                    "best_ask": 0.16,
                    "depth_at_best_bid": 10,
                    "depth_at_best_ask": 12,
                    "quote_captured_at": generated_at,
                },
                "kalshi": {
                    "ticker": "KX-1",
                    "question": "Will Team win?",
                    "best_bid": 0.14,
                    "best_ask": 0.15,
                    "depth_at_best_bid": 11,
                    "depth_at_best_ask": 13,
                    "quote_captured_at": generated_at,
                },
                "gap": {
                    "gross_gap": 0.02,
                    "estimated_net_gap": 0.012,
                    "kalshi_fee": 0.008,
                    "polymarket_fee": 0.0,
                    "settlement_delta_seconds": 0,
                },
                "ineligibility_reasons": [],
            }
        ],
    }


def _current_reports(input_dir, *, evaluator_ready=False, execution_ready=0):
    _write(
        input_dir / "normalized_markets_v0.json",
        {
            "schema_version": 1,
            "source": "normalized_market_contract_v0",
            "normalized_markets": [
                {
                    "venue": "polymarket",
                    "market_id": "poly-1",
                    "readiness": {
                        "quote_depth_ready": evaluator_ready,
                        "evaluator_metadata_ready": evaluator_ready,
                    },
                    "blockers": [] if evaluator_ready else ["missing_quote_timestamp"],
                },
                {
                    "venue": "kalshi",
                    "ticker": "KX-1",
                    "market_id": "KX-1",
                    "readiness": {
                        "quote_depth_ready": evaluator_ready,
                        "evaluator_metadata_ready": evaluator_ready,
                    },
                    "blockers": [] if evaluator_ready else ["missing_quote_timestamp"],
                },
            ],
        },
    )
    tier = "EXECUTION_EVALUATION_READY" if execution_ready else "EXACT_PAYOFF_REVIEW_READY"
    _write(
        input_dir / "settlement_evidence_burden.json",
        {
            "schema_version": 1,
            "source": "settlement_evidence_burden_v1",
            "summary": {
                "by_review_readiness_tier": {
                    "EXECUTION_EVALUATION_READY": execution_ready,
                    "EXACT_PAYOFF_REVIEW_READY": 2 if not execution_ready else 0,
                }
            },
            "markets": [
                {
                    "venue": "polymarket",
                    "ticker": "poly-1",
                    "review_readiness_tier": tier,
                    "blockers": [] if execution_ready else ["missing_quote_depth_for_execution"],
                },
                {
                    "venue": "kalshi",
                    "ticker": "KX-1",
                    "review_readiness_tier": tier,
                    "blockers": [] if execution_ready else ["missing_quote_depth_for_execution"],
                },
            ],
        },
    )
    _write(input_dir / "venue_metadata_coverage.json", {"schema_version": 1, "source": "venue_metadata_coverage_audit_v1"})
    _write(input_dir / "standardized_family_candidates.json", {"schema_version": 1, "source": "standardized_family_candidates_v1"})
    _write(input_dir / "cross_platform_opportunity_triage.json", {"schema_version": 1, "source": "cross_platform_opportunity_triage_v1"})


def _report(input_dir):
    return build_existing_paper_candidate_audit_report(
        input_dir=input_dir,
        generated_at=datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc),
    )


def test_paper_rows_are_detected(tmp_path) -> None:
    reports = tmp_path / "reports"
    _current_reports(reports)
    _write(reports / "evaluator.json", _candidate())

    report = _report(reports)

    assert report["summary"]["total_paper_candidate_rows_found"] == 1
    assert report["candidates"][0]["candidate_id"] == "poly-1__KX-1"
    assert report["candidates"][0]["creates_new_candidate"] is False


def test_duplicate_rows_are_deduped(tmp_path) -> None:
    reports = tmp_path / "reports"
    _current_reports(reports)
    _write(reports / "evaluator_a.json", _candidate())
    _write(reports / "evaluator_b.json", _candidate())

    report = _report(reports)

    assert report["summary"]["total_paper_candidate_rows_found"] == 2
    assert report["summary"]["unique_candidate_count"] == 1
    assert report["summary"]["duplicate_row_count"] == 1
    assert any(DUPLICATE_ROW in row["classifications"] for row in report["candidates"])


def test_stale_files_are_flagged(tmp_path) -> None:
    reports = tmp_path / "reports"
    _current_reports(reports)
    path = _write(reports / "old_evaluator.json", _candidate(generated_at="2025-12-01T00:00:00+00:00"))
    old_ts = datetime(2025, 12, 1, tzinfo=timezone.utc).timestamp()
    os.utime(path, (old_ts, old_ts))

    report = _report(reports)

    assert STALE_SOURCE_FILE in report["candidates"][0]["classifications"]
    assert report["summary"]["stale_count"] == 1


def test_rows_that_fail_current_normalized_gates_are_flagged(tmp_path) -> None:
    reports = tmp_path / "reports"
    _current_reports(reports, evaluator_ready=False, execution_ready=0)
    _write(reports / "evaluator.json", _candidate(generated_at="2026-01-02T11:30:00+00:00"))

    report = _report(reports)
    row = report["candidates"][0]

    assert FAILS_CURRENT_NORMALIZED_GATES in row["classifications"]
    assert "current_settlement_evidence_burden_execution_evaluation_ready_count_zero" in row["blockers"]


def test_no_new_candidate_is_created(tmp_path) -> None:
    reports = tmp_path / "reports"
    _current_reports(reports, evaluator_ready=True, execution_ready=2)
    _write(reports / "evaluator.json", _candidate(generated_at="2026-01-02T11:30:00+00:00"))

    report = _report(reports)

    assert report["safety"]["paper_candidate_rows_created"] is False
    assert all(row["creates_new_candidate"] is False for row in report["candidates"])
    assert all(row["primary_classification"] != ACTION for row in report["candidates"])


def test_cli_audit_existing_paper_candidates_writes_outputs(tmp_path, capsys) -> None:
    reports = tmp_path / "reports"
    _current_reports(reports)
    _write(reports / "evaluator.json", _candidate())
    json_output = reports / "existing_paper_candidate_audit.json"
    markdown_output = reports / "existing_paper_candidate_audit.md"

    rc = scan.main(
        [
            "audit-existing-paper-candidates",
            "--input-dir",
            str(reports),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )
    stdout = capsys.readouterr().out

    assert rc == 0
    assert "existing_paper_candidate_audit_status=OK" in stdout
    assert json_output.exists()
    assert markdown_output.exists()
