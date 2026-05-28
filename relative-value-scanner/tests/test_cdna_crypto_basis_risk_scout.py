from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import scan
from relative_value.cdna_crypto_basis_risk_scout import (
    ACTION_BASIS_RISK_REVIEW,
    ACTION_MANUAL_REVIEW,
    ACTION_WATCH,
    B_ATH_METHODOLOGY,
    B_DEADLINE_VS_POINT,
    B_MISSING_PRICE_SOURCE,
    B_MISSING_SETTLEMENT_RULES,
    B_RANGE_VS_CLOSE,
    B_AMBIGUOUS_SHAPE,
    build_cdna_crypto_basis_risk_scout_report,
    write_cdna_crypto_basis_risk_scout_files,
)
from relative_value.crypto_com_predict_cdna_saved_page_parser import (
    SHAPE_ALL_TIME_HIGH_BY_DATE,
    SHAPE_AMBIGUOUS,
    SHAPE_DEADLINE_HIT_BY_DATE,
    SHAPE_EARLIEST_TIMEFRAME_THRESHOLD_TOUCH,
    SHAPE_POINT_IN_TIME_THRESHOLD,
    SHAPE_YEAR_END_RANGE_BUCKET,
    _parse_json_fixture,
)


def _combined_fixture_payload() -> list[dict[str, Any]]:
    return [
        {
            "source_platform": "crypto_com_predict",
            "source_url": "https://example.com/btc-year-end",
            "title": "Bitcoin Price at the End of 2026",
            "asset": "BTC",
            "market_type": "year_end_range_bucket",
            "thresholds": [
                {"selection": "85,000 to 89,999.99", "lower": 85000.0, "upper": 89999.99, "yes_display_price": "$0.12"},
                {"selection": "65,000 to 69,999.99", "lower": 65000.0, "upper": 69999.99, "yes_display_price": "$0.12"},
            ],
            "date_time": {"closes": "Jan 1, 2027 4:59 am UTC", "resolution_reference_time": "11:59 PM Eastern Time on December 31, 2026"},
            "settlement_rules_methodology_text": "This market resolves to the price range bucket that contains the Nadex BTC Index price at 11:59 PM Eastern Time on December 31, 2026. Settlement: CDNA Rule 14.69(c).",
        },
        {
            "source_platform": "crypto_com_predict",
            "source_url": "https://example.com/btc-200k",
            "title": "Will Bitcoin be above $200k by next year?",
            "asset": "BTC",
            "market_type": "deadline_threshold_touch",
            "thresholds": [
                {"selection": "Yes", "threshold": 200000.0, "operator": ">=", "yes_display_price": None, "no_display_price": "$0.96"}
            ],
            "date_time": {"closes": "Jan 1, 2027 4:59 am UTC", "resolution_reference_time": "any time on or before 11:59 PM Eastern Time on December 31, 2026"},
            "settlement_rules_methodology_text": "This market resolves to Yes if the Nadex BTC Index price reaches 200,000 at any time. Settlement: CDNA Rule 14.69(c).",
        },
        {
            "source_platform": "crypto_com_predict",
            "source_url": "https://example.com/btc-100k-earliest",
            "title": "When will Bitcoin cross $100k again?",
            "asset": "BTC",
            "market_type": "earliest_timeframe_threshold_touch",
            "thresholds": [
                {"selection": "Before June 2026", "threshold": 100000.0, "operator": ">=", "yes_display_price": "$0.98"},
                {"selection": "Before October 2026", "threshold": 100000.0, "operator": ">=", "yes_display_price": "$0.24"},
            ],
            "date_time": {"closes": "Jan 1, 2027 4:59 am UTC", "resolution_reference_time": "earliest specified timeframe"},
            "settlement_rules_methodology_text": "Settlement: CDNA Rule 14.69(c) Nadex BTC Index.",
        },
        {
            "source_platform": "crypto_com_predict",
            "source_url": "https://example.com/eth-ath",
            "title": "Ethereum all time high by",
            "asset": "ETH",
            "market_type": "all_time_high_by_date",
            "thresholds": [
                {"selection": "December 31, 2026", "yes_display_price": "$0.15"},
                {"selection": "September 30, 2026", "yes_display_price": "$0.10"},
            ],
            "date_time": {"closes": "Jan 1, 2027 4:59 am UTC", "resolution_reference_time": "the date specified in the market title"},
            "settlement_rules_methodology_text": "Settlement: CDNA Rule 14.69 Source Agency Expiration Value.",
        },
        {
            "source_platform": "crypto_com_predict",
            "source_url": "https://example.com/eth-may-23",
            "title": "Ethereum price on 23 May at 9:00 am ET",
            "asset": "ETH",
            "market_type": "point_in_time_threshold",
            "thresholds": [
                {"selection": ">$2,057.00", "threshold": 2057.0, "operator": ">", "outcome": "No"},
                {"selection": ">$2,050.00", "threshold": 2050.0, "operator": ">", "outcome": "No"},
                {"selection": ">$2,022.00", "threshold": 2022.0, "operator": ">", "outcome": "Yes"},
            ],
            "date_time": {"closes": "May 23, 2026 1:00 pm UTC", "resolution_reference_time": "May 23, 2026 at 9:00 am ET"},
            "settlement_rules_methodology_text": "Settlement: CDNA Rule 14.72(c). UETH expiration value.",
        },
    ]


def _write_combined_fixture(tmp_path: Path) -> Path:
    fixture_path = tmp_path / "crypto_com_predict_btc_eth_event_fixtures_combined.json"
    fixture_path.write_text(json.dumps(_combined_fixture_payload()), encoding="utf-8")
    return fixture_path


def test_top_level_json_array_parses(tmp_path: Path) -> None:
    fixture = _write_combined_fixture(tmp_path)
    from datetime import datetime, timezone

    rows = _parse_json_fixture(fixture, generated_at=datetime.now(timezone.utc))
    assert len(rows) > 0
    assets = {row.get("asset") for row in rows}
    assert "BTC" in assets and "ETH" in assets
    shapes = {row.get("market_shape_conservative") for row in rows}
    assert {"year_end_range_bucket", "deadline_threshold_touch", "earliest_timeframe_threshold_touch", "all_time_high_by_date", "point_in_time_threshold"}.issubset(shapes)


def test_btc_year_end_range_bucket_is_range_not_exact(tmp_path: Path) -> None:
    fixture = _write_combined_fixture(tmp_path)
    report = build_cdna_crypto_basis_risk_scout_report(input_fixture=fixture)
    rows = [r for r in report["rows"] if r["shape_class"] == SHAPE_YEAR_END_RANGE_BUCKET]
    assert rows
    for row in rows:
        assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW
        assert B_RANGE_VS_CLOSE in row["blockers"]
        assert row["exact_ready"] is False
        assert row["source_exact_payoff_compatible_with_kalshi"] is False


def test_btc_deadline_threshold_touch_is_deadline_not_exact(tmp_path: Path) -> None:
    fixture = _write_combined_fixture(tmp_path)
    report = build_cdna_crypto_basis_risk_scout_report(input_fixture=fixture)
    rows = [r for r in report["rows"] if r["shape_class"] == SHAPE_DEADLINE_HIT_BY_DATE]
    assert rows
    for row in rows:
        assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW
        assert B_DEADLINE_VS_POINT in row["blockers"]
        assert row["exact_ready"] is False


def test_btc_earliest_timeframe_is_deadline_class(tmp_path: Path) -> None:
    fixture = _write_combined_fixture(tmp_path)
    report = build_cdna_crypto_basis_risk_scout_report(input_fixture=fixture)
    rows = [r for r in report["rows"] if r["shape_class"] == SHAPE_EARLIEST_TIMEFRAME_THRESHOLD_TOUCH]
    assert rows
    for row in rows:
        assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW
        assert B_DEADLINE_VS_POINT in row["blockers"]


def test_eth_point_in_time_threshold_parses(tmp_path: Path) -> None:
    fixture = _write_combined_fixture(tmp_path)
    report = build_cdna_crypto_basis_risk_scout_report(input_fixture=fixture)
    rows = [r for r in report["rows"] if r["shape_class"] == SHAPE_POINT_IN_TIME_THRESHOLD]
    assert rows
    for row in rows:
        # Point-in-time without a peer falls to WATCH; manual review only when peer exists.
        assert row["allowed_next_action"] in {ACTION_WATCH, ACTION_MANUAL_REVIEW}
        assert row["exact_ready"] is False
        assert row["source_exact_payoff_compatible_with_kalshi"] is False
        assert row["cdna"]["asset"] == "ETH"


def test_all_time_high_rows_carry_methodology_blocker(tmp_path: Path) -> None:
    fixture = _write_combined_fixture(tmp_path)
    report = build_cdna_crypto_basis_risk_scout_report(input_fixture=fixture)
    rows = [r for r in report["rows"] if r["shape_class"] == SHAPE_ALL_TIME_HIGH_BY_DATE]
    assert rows
    for row in rows:
        assert B_ATH_METHODOLOGY in row["blockers"]
        assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW


def test_ambiguous_rows_stay_blocked(tmp_path: Path) -> None:
    fixture = tmp_path / "ambiguous.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "source_platform": "crypto_com_predict",
                    "title": "Some Crypto Event With No Recognized Shape",
                    "asset": "BTC",
                    "market_type": "unknown_or_unsupported_shape",
                    "thresholds": [
                        {"selection": "Some Outcome"}
                    ],
                    "date_time": {},
                }
            ]
        ),
        encoding="utf-8",
    )
    report = build_cdna_crypto_basis_risk_scout_report(input_fixture=fixture)
    rows = report["rows"]
    assert rows
    row = rows[0]
    assert row["shape_class"] == SHAPE_AMBIGUOUS
    assert B_AMBIGUOUS_SHAPE in row["blockers"]
    assert row["allowed_next_action"] == ACTION_WATCH


def test_basis_risk_scout_produces_basis_risk_review_not_exact(tmp_path: Path) -> None:
    fixture = _write_combined_fixture(tmp_path)
    report = build_cdna_crypto_basis_risk_scout_report(input_fixture=fixture)
    for row in report["rows"]:
        assert row["allowed_next_action"] in {ACTION_BASIS_RISK_REVIEW, ACTION_MANUAL_REVIEW, ACTION_WATCH}
        assert row["exact_ready"] is False
        assert row["source_exact_payoff_compatible_with_kalshi"] is False


def test_no_paper_candidate_emitted_anywhere(tmp_path: Path) -> None:
    fixture = _write_combined_fixture(tmp_path)
    json_output = tmp_path / "scout.json"
    md_output = tmp_path / "scout.md"
    write_cdna_crypto_basis_risk_scout_files(
        input_fixture=fixture,
        json_output=json_output,
        markdown_output=md_output,
    )
    json_text = json_output.read_text(encoding="utf-8")
    md_text = md_output.read_text(encoding="utf-8")
    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in json_text
    assert forbidden not in md_text
    payload = json.loads(json_text)
    assert payload["summary"]["exact_ready_rows"] == 0
    assert payload["summary"]["paper_candidate_rows"] == 0
    for row in payload["rows"]:
        assert row["can_create_candidate_pair"] is False
        assert row["can_create_paper_candidate"] is False
        assert row["exact_ready"] is False
        assert row["paper_candidate"] is False


def test_cli_writes_diagnostic_outputs(tmp_path: Path, capsys) -> None:
    fixture = _write_combined_fixture(tmp_path)
    json_output = tmp_path / "out.json"
    md_output = tmp_path / "out.md"
    peer_dir = tmp_path / "peers"
    peer_dir.mkdir()
    (peer_dir / "normalized_markets_v0.json").write_text(json.dumps({"normalized_markets": []}), encoding="utf-8")
    result = scan.main(
        [
            "cdna-crypto-basis-risk-scout",
            "--input",
            str(fixture),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(md_output),
            "--peer-input-dir",
            str(peer_dir),
        ]
    )
    assert result == 0
    stdout = capsys.readouterr().out
    assert "cdna_crypto_basis_risk_scout=OK" in stdout
    assert "diagnostic_only=true" in stdout
    assert "exact_ready_rows=0" in stdout
    assert "paper_candidate_rows=0" in stdout
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "cdna_crypto_basis_risk_scout_v1"
    assert payload["safety"]["source_exact_payoff_compatible_with_kalshi"] is False


def test_eth_point_in_time_peer_match_yields_manual_review(tmp_path: Path) -> None:
    fixture = _write_combined_fixture(tmp_path)
    peer_dir = tmp_path / "peers"
    peer_dir.mkdir()
    # Provide a Kalshi-shaped ETH peer to force point-in-time → MANUAL_REVIEW.
    (peer_dir / "normalized_markets_v0.json").write_text(
        json.dumps(
            {
                "normalized_markets": [
                    {
                        "venue": "kalshi",
                        "ticker": "KXETHD-26MAY23-T2050",
                        "title": "Will ETH be above $2050 at 9am ET on May 23, 2026?",
                        "event_ticker": "KXETHD-26MAY23",
                        "settlement": {"close_time": "2026-05-23T13:00:00Z", "resolution_time": "2026-05-23T13:05:00Z"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = build_cdna_crypto_basis_risk_scout_report(input_fixture=fixture, peer_input_dir=peer_dir)
    eth_point_rows = [r for r in report["rows"] if r["cdna"]["asset"] == "ETH" and r["shape_class"] == SHAPE_POINT_IN_TIME_THRESHOLD]
    assert eth_point_rows
    assert any(r["allowed_next_action"] == ACTION_MANUAL_REVIEW for r in eth_point_rows)
    for r in eth_point_rows:
        assert r["exact_ready"] is False


def test_summary_counts_match_fixture_shape(tmp_path: Path) -> None:
    fixture = _write_combined_fixture(tmp_path)
    report = build_cdna_crypto_basis_risk_scout_report(input_fixture=fixture)
    s = report["summary"]
    assert s["cdna_rows"] > 0
    assert s["cdna_btc_rows"] > 0
    assert s["cdna_eth_rows"] > 0
    assert s["point_in_time_rows"] > 0
    assert s["deadline_or_range_hit_rows"] > 0
    assert s["exact_ready_rows"] == 0
    assert s["paper_candidate_rows"] == 0


def test_missing_input_fixture_is_warning_not_crash(tmp_path: Path) -> None:
    fixture = tmp_path / "nonexistent.json"
    report = build_cdna_crypto_basis_risk_scout_report(input_fixture=fixture)
    assert report["summary"]["cdna_rows"] == 0
    assert any(w.get("reason_code") == "input_missing" for w in report["warnings"])


def test_ops_status_surfaces_cdna_parser_health_and_scout(tmp_path: Path) -> None:
    # Place CDNA snapshot + scout in a peer/input dir.
    input_dir = tmp_path
    fixture = _write_combined_fixture(input_dir)
    # Build CDNA snapshot file.
    from relative_value.crypto_com_predict_cdna_saved_page_parser import (
        write_crypto_com_predict_cdna_research_snapshot_file,
    )

    cdna_snapshot = input_dir / "crypto_com_predict_cdna_research_snapshot.json"
    write_crypto_com_predict_cdna_research_snapshot_file(
        fixture_dir=input_dir, json_output=cdna_snapshot
    )
    # Write CDNA scout.
    scout_json = input_dir / "cdna_crypto_basis_risk_scout.json"
    write_cdna_crypto_basis_risk_scout_files(
        input_fixture=fixture,
        json_output=scout_json,
        markdown_output=input_dir / "cdna_crypto_basis_risk_scout.md",
    )
    from relative_value.relative_value_ops_status import build_relative_value_ops_status_report

    ops_report = build_relative_value_ops_status_report(input_dir=input_dir)
    parser_health = (ops_report["summary"] or {}).get("cdna_parser_health") or {}
    scout_summary = (ops_report["summary"] or {}).get("cdna_crypto_basis_risk_scout") or {}
    assert parser_health.get("present") is True
    assert parser_health.get("rows", 0) > 0
    assert parser_health.get("btc_rows", 0) > 0
    assert parser_health.get("eth_rows", 0) > 0
    assert scout_summary.get("present") is True
    assert scout_summary.get("cdna_rows", 0) > 0
    assert scout_summary.get("paper_candidate_rows", 0) == 0
    assert scout_summary.get("exact_ready_rows", 0) == 0


def test_parser_emits_all_time_high_methodology_blocker(tmp_path: Path) -> None:
    fixture = _write_combined_fixture(tmp_path)
    from datetime import datetime, timezone

    rows = _parse_json_fixture(fixture, generated_at=datetime.now(timezone.utc))
    ath = [r for r in rows if r.get("market_shape_conservative") == "all_time_high_by_date"]
    assert ath
    for row in ath:
        assert "all_time_high_methodology_unverified" in row["blockers"]


def test_parser_emits_missing_price_source_when_not_documented(tmp_path: Path) -> None:
    fixture = tmp_path / "no_price_source.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "source_platform": "crypto_com_predict",
                    "title": "Some BTC market",
                    "asset": "BTC",
                    "market_type": "point_in_time_threshold",
                    "thresholds": [{"selection": ">$50000", "threshold": 50000.0, "operator": ">"}],
                    "date_time": {},
                    "settlement_rules_methodology_text": "Generic resolution text mentioning nothing about the price index used or the exchange that publishes it.",
                }
            ]
        ),
        encoding="utf-8",
    )
    from datetime import datetime, timezone

    rows = _parse_json_fixture(fixture, generated_at=datetime.now(timezone.utc))
    assert rows
    blockers = rows[0]["blockers"]
    assert "missing_price_source" in blockers
    assert "price_source_unverified" in blockers
