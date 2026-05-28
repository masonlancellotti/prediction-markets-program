from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.cdna_vs_kalshi_btc_basis_risk import (
    BTC_BASIS_RISK_REVIEW,
    MANUAL_BASIS_RISK_REVIEW,
    build_cdna_vs_kalshi_btc_basis_risk_report,
)
from relative_value.crypto_com_predict_cdna_saved_page_parser import CDNA_UBTC_SOURCE_INDEX


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def test_matched_pair_produces_btc_basis_risk_review(tmp_path: Path) -> None:
    cdna, standardized = _write_reports(tmp_path)

    report = build_cdna_vs_kalshi_btc_basis_risk_report(
        cdna_path=cdna,
        standardized_path=standardized,
        generated_at=NOW,
    )

    assert report["summary"]["basis_risk_row_count"] == 1
    row = report["rows"][0]
    assert row["relationship_class"] == BTC_BASIS_RISK_REVIEW
    assert row["source_a"] == CDNA_UBTC_SOURCE_INDEX
    assert row["source_b"] == "CF Benchmarks / BRTI"
    assert row["source_pair_known_reputable"] is True
    assert row["basis_risk_severity_hint"] == "moderate_known_different_sources_same_window"
    assert row["allowed_next_action"] == MANUAL_BASIS_RISK_REVIEW
    assert row["blockers"] == [
        "different_settlement_source",
        "basis_risk_not_exact_same_payoff",
        "cdna_research_only_not_executable",
    ]
    assert row["diagnostic_only"] is True
    assert row["affects_evaluator_gates"] is False
    assert row["paper_candidate_emitted"] is False
    assert row["exact_payoff_claimed"] is False


def test_date_mismatch_produces_no_review_row(tmp_path: Path) -> None:
    cdna, standardized = _write_reports(tmp_path, kalshi_date="Jul 1, 2026")

    report = build_cdna_vs_kalshi_btc_basis_risk_report(
        cdna_path=cdna,
        standardized_path=standardized,
        generated_at=NOW,
    )

    assert report["rows"] == []
    assert report["summary"]["mismatch_counts"] == {"measurement_date_mismatch": 1}


def test_operator_mismatch_produces_no_review_row(tmp_path: Path) -> None:
    cdna, standardized = _write_reports(tmp_path, kalshi_operator="<")

    report = build_cdna_vs_kalshi_btc_basis_risk_report(
        cdna_path=cdna,
        standardized_path=standardized,
        generated_at=NOW,
    )

    assert report["rows"] == []
    assert report["summary"]["mismatch_counts"] == {"threshold_operator_mismatch": 1}


def test_threshold_mismatch_produces_no_review_row(tmp_path: Path) -> None:
    cdna, standardized = _write_reports(tmp_path, kalshi_threshold=100001.0)

    report = build_cdna_vs_kalshi_btc_basis_risk_report(
        cdna_path=cdna,
        standardized_path=standardized,
        generated_at=NOW,
    )

    assert report["rows"] == []
    assert report["summary"]["mismatch_counts"] == {"threshold_value_mismatch": 1}


def test_no_exact_payoff_or_paper_candidate_emitted(tmp_path: Path) -> None:
    cdna, standardized = _write_reports(tmp_path)

    report = build_cdna_vs_kalshi_btc_basis_risk_report(
        cdna_path=cdna,
        standardized_path=standardized,
        generated_at=NOW,
    )
    encoded = json.dumps(report)

    assert "PAPER_CANDIDATE" not in encoded
    assert "EXACT_PAYOFF_REVIEW_READY" not in encoded
    assert report["summary"]["paper_candidate_count"] == 0
    assert report["summary"]["exact_payoff_claimed_count"] == 0
    assert report["safety"]["affects_evaluator_gates"] is False
    assert report["safety"]["treats_cdna_and_kalshi_btc_as_exact_same_payoff"] is False


def test_range_and_deadline_cdna_rows_are_not_basis_risk_eligible(tmp_path: Path) -> None:
    cdna_path = tmp_path / "crypto_com_predict_cdna_research_snapshot.json"
    standardized_path = tmp_path / "standardized_family_candidates.json"
    cdna_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "crypto_com_predict_cdna_research_snapshot_v1",
                "rows": [
                    {
                        "venue": "crypto_com_predict_cdna",
                        "asset": "BTC",
                        "market_type": "year_end_range_bucket",
                        "shape_class": "YEAR_END_RANGE_BUCKET",
                        "lower": 85000,
                        "upper": 89999.99,
                        "price_source_index": "CDNA Rule 14.69 / Nadex BTC Index",
                        "basis_risk_compatible_with_kalshi": False,
                        "source_exact_payoff_compatible_with_kalshi": False,
                    },
                    {
                        "venue": "crypto_com_predict_cdna",
                        "asset": "BTC",
                        "market_type": "deadline_threshold_touch",
                        "shape_class": "DEADLINE_HIT_BY_DATE",
                        "threshold_value": 200000,
                        "threshold_operator": ">=",
                        "price_source_index": "CDNA Rule 14.69 / Nadex BTC Index",
                        "basis_risk_compatible_with_kalshi": False,
                        "source_exact_payoff_compatible_with_kalshi": False,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    standardized_path.write_text(
        json.dumps(_standardized_payload(date="Dec 31, 2026", operator=">=", threshold=200000.0)),
        encoding="utf-8",
    )

    report = build_cdna_vs_kalshi_btc_basis_risk_report(
        cdna_path=cdna_path,
        standardized_path=standardized_path,
        generated_at=NOW,
    )

    assert report["summary"]["cdna_btc_rows_considered"] == 0
    assert report["summary"]["basis_risk_row_count"] == 0
    assert report["rows"] == []


def test_compare_cdna_vs_kalshi_btc_basis_risk_cli_writes_json(tmp_path: Path, capsys) -> None:
    cdna, standardized = _write_reports(tmp_path)
    output = tmp_path / "cdna_vs_kalshi_btc_basis_risk.json"

    result = scan.main(
        [
            "compare-cdna-vs-kalshi-btc-basis-risk",
            "--cdna",
            str(cdna),
            "--standardized",
            str(standardized),
            "--json-output",
            str(output),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "cdna_vs_kalshi_btc_basis_risk_status=OK" in stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"]["basis_risk_row_count"] == 1
    assert payload["rows"][0]["relationship_class"] == BTC_BASIS_RISK_REVIEW


def _write_reports(
    tmp_path: Path,
    *,
    kalshi_date: str = "Jun 30, 2026",
    kalshi_operator: str = ">",
    kalshi_threshold: float = 100000.0,
) -> tuple[Path, Path]:
    cdna_path = tmp_path / "crypto_com_predict_cdna_research_snapshot.json"
    standardized_path = tmp_path / "standardized_family_candidates.json"
    cdna_path.write_text(json.dumps(_cdna_payload()), encoding="utf-8")
    standardized_path.write_text(
        json.dumps(_standardized_payload(date=kalshi_date, operator=kalshi_operator, threshold=kalshi_threshold)),
        encoding="utf-8",
    )
    return cdna_path, standardized_path


def _cdna_payload() -> dict:
    return {
        "schema_version": 1,
        "source": "crypto_com_predict_cdna_research_snapshot_v1",
        "rows": [
            {
                "venue": "crypto_com_predict_cdna",
                "permission": "research_only",
                "execution_allowed_in_project_now": False,
                "can_create_candidate_pair": False,
                "can_create_paper_candidate": False,
                "asset": "BTC",
                "threshold_value": 100000.0,
                "threshold_operator": ">",
                "measurement_date": "Jun 30, 2026",
                "measurement_time": "5 PM EDT",
                "price_source_index": CDNA_UBTC_SOURCE_INDEX,
                "settlement_window": "60_seconds_preceding_at_least_25_midpoint_prices",
                "basis_risk_compatible_with_kalshi": True,
                "source_exact_payoff_compatible_with_kalshi": False,
                "event_id": "CDNA-EVENT",
                "market_id": "CDNA-MARKET",
                "title": "Fake CDNA BTC fixture",
                "raw_source_file": "venues/fixtures/crypto_com_predict_cdna/example_market.json",
                "raw_row_index": 0,
            }
        ],
        "warnings": [],
    }


def _standardized_payload(*, date: str, operator: str, threshold: float) -> dict:
    return {
        "schema_version": 1,
        "source": "standardized_family_candidates_v1",
        "rows": [
            {
                "family": "CRYPTO_PRICE_THRESHOLD",
                "typed_key": {
                    "asset": "BTC",
                    "threshold_value": threshold,
                    "threshold_operator": operator,
                    "measurement_date": date,
                    "timestamp": "5 PM EDT",
                    "settlement_window": "60_seconds_preceding",
                    "price_source_index": "cf benchmarks",
                },
                "markets": [
                    {
                        "venue": "kalshi",
                        "event_id": "KXBTC-26JUN3017",
                        "event_ticker": "KXBTC-26JUN3017",
                        "market_id": "KXBTC-26JUN3017-T100000",
                        "ticker": "KXBTC-26JUN3017-T100000",
                        "review_readiness_tier": "FAMILY_TYPED_REVIEW_READY",
                        "source_file": "reports/kalshi_fixture.json",
                        "row_index": 0,
                    }
                ],
            }
        ],
        "warnings": [],
    }
