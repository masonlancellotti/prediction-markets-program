from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.paper_readiness_probe import build_paper_readiness_probe_report


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def test_reviewed_scope_with_stale_quote_appears_blocked(tmp_path: Path) -> None:
    reports = _write_reports(tmp_path, [_burden_row(quote_blocker="stale_quote")], reviewed=True)

    report = build_paper_readiness_probe_report(input_dir=reports, generated_at=NOW)
    row = report["rows"][0]

    assert report["summary"]["total_rows_considered"] == 1
    assert report["summary"]["rows_blocked_by_stale_quote"] == 1
    assert row["quote_freshness_blocker"] == "stale_quote"
    assert "fresh_quote_captured_at_under_staleness_policy" in row["required_fields_to_advance_one_tier"]
    assert row["missing_relationship_or_pair_review"] is True


def test_reviewed_scope_with_missing_quote_appears_blocked(tmp_path: Path) -> None:
    reports = _write_reports(
        tmp_path,
        [_burden_row(quote_blocker="missing_quote_captured_at")],
        reviewed=True,
        quote_depth_ready=False,
    )

    report = build_paper_readiness_probe_report(input_dir=reports, generated_at=NOW)
    row = report["rows"][0]

    assert report["summary"]["rows_blocked_by_missing_quote"] == 1
    assert row["missing_quote_depth_for_execution"] is True
    assert "quote_depth.captured_at" in row["required_fields_to_advance_one_tier"]
    assert "saved_orderbook_depth" in row["required_fields_to_advance_one_tier"]


def test_no_reviewed_scopes_produces_empty_probe(tmp_path: Path) -> None:
    reports = _write_reports(tmp_path, [_burden_row(quote_blocker="stale_quote")], reviewed=False)

    report = build_paper_readiness_probe_report(input_dir=reports, generated_at=NOW)

    assert report["summary"]["reviewed_scope_count"] == 0
    assert report["summary"]["total_rows_considered"] == 0
    assert report["rows"] == []
    assert report["next_operator_actions"][0]["action"] == "REVIEW_CANONICAL_REGISTRY_COVERAGE"


def test_probe_emits_no_paper_candidate_literal(tmp_path: Path) -> None:
    reports = _write_reports(tmp_path, [_burden_row(quote_blocker="stale_quote")], reviewed=True)

    report = build_paper_readiness_probe_report(input_dir=reports, generated_at=NOW)
    encoded = json.dumps(report)

    assert report["summary"]["paper_ready_count"] == 0
    assert report["safety"]["paper_candidate_emitted"] is False
    assert "PAPER_CANDIDATE" not in encoded


def test_cli_audit_paper_readiness_probe_writes_outputs(tmp_path: Path, capsys) -> None:
    reports = _write_reports(tmp_path, [_burden_row(quote_blocker="stale_quote")], reviewed=True)
    json_output = reports / "paper_readiness_probe.json"
    markdown_output = reports / "paper_readiness_probe.md"

    rc = scan.main(
        [
            "audit-paper-readiness-probe",
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
    assert "paper_readiness_probe_status=OK" in stdout
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["source"] == "paper_readiness_probe_v1"
    assert markdown_output.exists()


def _write_reports(
    tmp_path: Path,
    burden_rows: list[dict],
    *,
    reviewed: bool,
    quote_depth_ready: bool = True,
    fee_metadata_ready: bool = False,
) -> Path:
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    _write(
        reports / "settlement_evidence_burden.json",
        {
            "source": "settlement_evidence_burden_v1",
            "markets": burden_rows,
            "summary": {},
        },
    )
    _write(
        reports / "canonical_registry_coverage.json",
        {
            "source": "canonical_registry_coverage_v1",
            "scopes": [
                {
                    "scope_key": _scope_key(),
                    "family": "FED_FOMC",
                    "registry_match_count": 1 if reviewed else 0,
                    "registry_entry_id_if_matched": "fed-reviewed" if reviewed else None,
                    "review_status": "reviewed" if reviewed else "unreviewed",
                }
            ],
            "summary": {"scopes_total": 1, "scopes_reviewed": 1 if reviewed else 0},
        },
    )
    _write(
        reports / "family_graduation_fed.json",
        {
            "source": "family_graduation_plan_v1",
            "family": "FED_FOMC",
            "rows": [_graduation_row()],
        },
    )
    _write(
        reports / "family_graduation_crypto.json",
        {"source": "family_graduation_plan_v1", "family": "CRYPTO_PRICE_THRESHOLD", "rows": []},
    )
    _write(
        reports / "normalized_markets_v0.json",
        {
            "source": "normalized_market_contract_v0",
            "normalized_markets": [
                {
                    "venue": "kalshi",
                    "market_id": "KXFED-27APR-T4.25",
                    "ticker": "KXFED-27APR-T4.25",
                    "readiness": {
                        "quote_depth_ready": quote_depth_ready,
                        "fee_metadata_ready": fee_metadata_ready,
                    },
                }
            ],
        },
    )
    return reports


def _burden_row(*, quote_blocker: str) -> dict:
    return {
        "venue": "kalshi",
        "event_id": "KXFED-27APR",
        "event_ticker": "KXFED-27APR",
        "market_id": "KXFED-27APR-T4.25",
        "ticker": "KXFED-27APR-T4.25",
        "family": "FED_FOMC",
        "review_readiness_tier": "SETTLEMENT_SOURCE_REVIEW_READY",
        "quote_freshness_status": {
            "captured_at": "2026-05-25T10:00:00+00:00" if quote_blocker == "stale_quote" else None,
            "age_seconds": 7200 if quote_blocker == "stale_quote" else None,
            "is_fresh": False,
            "blocker": quote_blocker,
        },
        "blockers": [quote_blocker],
    }


def _graduation_row() -> dict:
    return {
        "venue": "kalshi",
        "market_id": "KXFED-27APR-T4.25",
        "ticker": "KXFED-27APR-T4.25",
        "family": "FED_FOMC",
        "registry_proposal": {
            "recommended_registry_scope": {
                "scope_key": _scope_key(),
            }
        },
        "projection": {
            "projected_blockers_if_registry_or_source_added": ["pair_review_not_performed"]
        },
    }


def _scope_key() -> str:
    return "FED_FOMC|kalshi|KXFED|Apr 28, 2027"


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
