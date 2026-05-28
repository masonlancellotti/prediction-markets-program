from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scan
from relative_value.core_trio_peer_coverage_audit import (
    B_NO_KALSHI_PEER,
    B_TITLE_ONLY_MATCH,
    build_core_trio_peer_coverage_audit_report,
    write_core_trio_peer_coverage_audit_files,
)


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_base_reports(tmp_path: Path, *, point_rows: list[dict[str, Any]] | None = None, cdna_rows: list[dict[str, Any]] | None = None, kalshi_rows: list[dict[str, Any]] | None = None, enriched_rows: list[dict[str, Any]] | None = None) -> None:
    _write(
        tmp_path / "polymarket_point_in_time_typed_key_audit.json",
        {
            "source": "polymarket_point_in_time_typed_key_audit_v1",
            "summary": {"exact_ready_rows": 0, "paper_candidate_rows": 0},
            "rows": point_rows or [],
        },
    )
    _write(
        tmp_path / "polymarket_taxonomy_shape_scout_enriched.json",
        {
            "source": "polymarket_taxonomy_shape_scout_enriched_v1",
            "summary": {"exact_ready_rows": 0, "paper_candidate_rows": 0},
            "rows": enriched_rows or [],
        },
    )
    _write(
        tmp_path / "cdna_crypto_basis_risk_scout.json",
        {
            "source": "cdna_crypto_basis_risk_scout_v1",
            "summary": {"exact_ready_rows": 0, "paper_candidate_rows": 0},
            "rows": cdna_rows or [],
        },
    )
    _write(
        tmp_path / "crypto_com_predict_cdna_research_snapshot.json",
        {
            "source": "crypto_com_predict_cdna_research_snapshot_v1",
            "summary": {"exact_ready_rows": 0, "paper_candidate_rows": 0},
            "rows": [],
        },
    )
    _write(
        tmp_path / "normalized_markets_v0.json",
        {
            "source": "normalized_markets_v0",
            "normalized_markets": kalshi_rows or [],
        },
    )
    _write(
        tmp_path / "relative_value_ops_status.json",
        {"source": "relative_value_ops_status_v1", "summary": {}},
    )


def _poly_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "row_id": "poly_1",
        "market_id": "poly_market_1",
        "condition_id": "0xcond",
        "market_slug": "will-test-company-revenue-be-above-100",
        "question": "Will TestCo revenue be above $100 million on December 31, 2026 at 5:00 PM ET?",
        "market_family": "company_metric",
        "asset_or_family": "TESTCO_REVENUE",
        "threshold": 100.0,
        "comparator": "above",
        "target_date": "2026-12-31",
        "target_time": "5:00 PM ET",
        "timezone": "ET",
        "settlement_source_present": True,
        "token_ids": ["yes", "no"],
        "typed_key_complete_for_review": True,
        "quote": {
            "bid": 0.2,
            "ask": 0.3,
            "bid_size": 10,
            "ask_size": 11,
            "quote_timestamp": "2026-05-27T00:00:00+00:00",
        },
        "blockers": ["title_only_match_not_equivalence"],
    }
    row.update(overrides)
    return row


def _cdna_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "row_id": "cdna_eth_1",
        "shape_class": "point_in_time_threshold",
        "cdna": {
            "asset": "ETH",
            "selection_label": "ETH above 5000 at year end",
            "threshold_value": 5000,
            "comparator": "above",
            "target_date": "2026-12-31",
            "settlement_source": "Crypto.com price index",
        },
        "blockers": ["settlement_source_unverified"],
    }
    row.update(overrides)
    return row


def _kalshi_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "venue": "kalshi",
        "event_ticker": "KXETH-26DEC31",
        "ticker": "KXETH-26DEC31-T5000",
        "title": "Will Ethereum be above $5000 on December 31, 2026 at 5:00 PM ET?",
        "settlement": {
            "resolution_time": "2026-12-31T22:00:00Z",
            "settlement_source_url": "https://kalshi.com/rules",
        },
        "quote_depth": {
            "best_yes_bid": 1,
            "best_yes_ask": 2,
            "best_yes_bid_size": 100,
            "best_yes_ask_size": 100,
            "captured_at": "2026-05-27T00:00:00+00:00",
        },
    }
    row.update(overrides)
    return row


def _family(report: dict[str, Any], family: str) -> dict[str, Any]:
    for row in report["families"]:
        if row["family"] == family:
            return row
    raise AssertionError(f"missing family {family}")


def test_company_metric_rows_without_kalshi_peer_are_reported(tmp_path: Path) -> None:
    _write_base_reports(tmp_path, point_rows=[_poly_row()])

    report = build_core_trio_peer_coverage_audit_report(
        input_dir=tmp_path,
        generated_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
    )
    company = _family(report, "company_metric")

    assert company["polymarket_typed_complete_rows"] == 1
    assert company["kalshi_candidate_rows_found"] == 0
    assert B_NO_KALSHI_PEER in company["blockers"]
    assert "company_metric" in report["summary"]["families_without_kalshi_peer_row_names"]


def test_cdna_eth_point_in_time_rows_appear_in_crypto_price_coverage(tmp_path: Path) -> None:
    _write_base_reports(tmp_path, cdna_rows=[_cdna_row()], kalshi_rows=[_kalshi_row()])

    report = build_core_trio_peer_coverage_audit_report(input_dir=tmp_path)
    crypto = _family(report, "crypto_price_threshold")

    assert crypto["cdna_rows"] == 1
    assert crypto["cdna_point_in_time_rows"] == 1
    assert crypto["kalshi_candidate_rows_found"] == 1


def test_polymarket_deadline_range_hit_rows_do_not_count_as_point_in_time(tmp_path: Path) -> None:
    _write_base_reports(
        tmp_path,
        enriched_rows=[
            {
                "row_id": "poly_deadline",
                "market_shape": "crypto_deadline_range_hit",
                "question": "Will Bitcoin hit $150k by June 30, 2026?",
            }
        ],
    )

    report = build_core_trio_peer_coverage_audit_report(input_dir=tmp_path)

    assert report["summary"]["polymarket_rows"] == 0
    assert report["summary"]["polymarket_deadline_or_range_hit_rows_excluded"] == 1
    assert report["summary"]["exact_ready_rows"] == 0


def test_title_only_match_does_not_create_overlap(tmp_path: Path) -> None:
    question = "Will Bitcoin be above $100000 on December 31, 2026 at 5:00 PM ET?"
    _write_base_reports(
        tmp_path,
        point_rows=[
            _poly_row(
                row_id="poly_btc",
                question=question,
                market_family="crypto_price",
                asset_or_family="BTC",
                threshold=100000,
                comparator="above",
                target_date="2026-12-31",
            )
        ],
        kalshi_rows=[
            {
                "venue": "kalshi",
                "ticker": "KXBTC-26DEC31",
                "title": question,
                "settlement": {"resolution_time": "2026-12-31T22:00:00Z"},
            }
        ],
    )

    report = build_core_trio_peer_coverage_audit_report(input_dir=tmp_path)
    crypto = _family(report, "crypto_price_threshold")

    assert crypto["date_threshold_comparator_overlap_count"] == 0
    assert B_TITLE_ONLY_MATCH in crypto["blockers"]
    assert report["summary"]["date_threshold_comparator_overlap_count"] == 0


def test_exact_ready_and_paper_candidate_rows_remain_zero_and_no_paper_action_emitted(tmp_path: Path) -> None:
    _write_base_reports(tmp_path, point_rows=[_poly_row()], cdna_rows=[_cdna_row()], kalshi_rows=[_kalshi_row()])
    json_output = tmp_path / "core_trio_peer_coverage_audit.json"
    markdown_output = tmp_path / "core_trio_peer_coverage_audit.md"

    report = write_core_trio_peer_coverage_audit_files(
        input_dir=tmp_path,
        json_output=json_output,
        markdown_output=markdown_output,
    )

    forbidden = "PAPER" + "_CANDIDATE"
    assert report["summary"]["exact_ready_rows"] == 0
    assert report["summary"]["paper_candidate_rows"] == 0
    assert forbidden not in json_output.read_text(encoding="utf-8")
    assert forbidden not in markdown_output.read_text(encoding="utf-8")


def test_cli_writes_core_trio_peer_coverage_outputs(tmp_path: Path, capsys) -> None:
    _write_base_reports(tmp_path, point_rows=[_poly_row()], cdna_rows=[_cdna_row()], kalshi_rows=[_kalshi_row()])
    json_output = tmp_path / "audit.json"
    markdown_output = tmp_path / "audit.md"

    result = scan.main(
        [
            "core-trio-peer-coverage-audit",
            "--input-dir",
            str(tmp_path),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "core_trio_peer_coverage_audit=OK" in stdout
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "core_trio_peer_coverage_audit_v1"
    assert payload["summary"]["paper_candidate_rows"] == 0
