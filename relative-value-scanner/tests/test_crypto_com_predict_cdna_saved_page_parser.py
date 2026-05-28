from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.crypto_com_predict_cdna_saved_page_parser import (
    CDNA_RULE_1469_BTC_SOURCE_INDEX,
    CDNA_RULE_1472_ETH_SOURCE_INDEX,
    CDNA_UETH_SOURCE_INDEX,
    CDNA_UBTC_SOURCE_INDEX,
    MATCH_BASIS_RISK_POSSIBLE,
    MATCH_ONE_SIDED_FV_ONLY,
    PERMISSION_RESEARCH_ONLY,
    SHAPE_AMBIGUOUS,
    SHAPE_DEADLINE_HIT_BY_DATE,
    SHAPE_POINT_IN_TIME_THRESHOLD,
    SHAPE_YEAR_END_RANGE_BUCKET,
    build_crypto_com_predict_cdna_research_snapshot,
)


PROJECT_ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def test_saved_fixture_parses_btc_event() -> None:
    report = build_crypto_com_predict_cdna_research_snapshot(
        fixture_dir=PROJECT_ROOT / "venues" / "fixtures" / "crypto_com_predict_cdna",
        generated_at=NOW,
    )

    assert report["source"] == "crypto_com_predict_cdna_research_snapshot_v1"
    assert report["summary"]["parsed_rows"] >= 1
    row = next(item for item in report["rows"] if item["market_id"] == "CDNA-FAKE-MARKET-001")
    assert row["venue"] == "crypto_com_predict_cdna"
    assert row["permission"] == PERMISSION_RESEARCH_ONLY
    assert row["asset"] == "BTC"
    assert row["threshold_value"] == 100000.0
    assert row["threshold_operator"] == ">"
    assert row["measurement_date"] == "Jun 30, 2026"
    assert row["measurement_time"] == "5 PM EDT"
    assert row["timezone"] == "EDT"
    assert row["price_source_index"] == CDNA_UBTC_SOURCE_INDEX
    assert row["settlement_window"] == "60_seconds_preceding_at_least_25_midpoint_prices"
    assert row["basis_risk_compatible_with_kalshi"] is True
    assert row["source_exact_payoff_compatible_with_kalshi"] is False


def test_saved_html_fixture_parses_without_network(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "cdna_page.html").write_text(
        """
        <html>
          <head><title>Will Bitcoin be above $100,000 at 5 PM EDT on Jun 30, 2026?</title></head>
          <body data-event-id="EVT-HTML" data-market-id="MKT-HTML" data-market-type="binary_threshold">
            <h1>Will Bitcoin be above $100,000 at 5 PM EDT on Jun 30, 2026?</h1>
            <p>CDNA U-BTC midpoint data from Lukka or ICE Cryptocurrency Data is aggregated by Blockstream.</p>
            <p>The settlement value uses a 60-second period and at least 25 midpoint prices.</p>
          </body>
        </html>
        """,
        encoding="utf-8",
    )

    report = build_crypto_com_predict_cdna_research_snapshot(fixture_dir=fixture_dir, generated_at=NOW)
    row = report["rows"][0]

    assert row["event_id"] == "EVT-HTML"
    assert row["market_id"] == "MKT-HTML"
    assert row["asset"] == "BTC"
    assert row["threshold_value"] == 100000.0
    assert row["price_source_index"] == CDNA_UBTC_SOURCE_INDEX
    assert row["captured_at"] == NOW.isoformat()


def test_multiple_fixture_dirs_and_files_parse_btc_and_eth(tmp_path: Path) -> None:
    fixture_one = tmp_path / "venues" / "fixtures" / "crypto_com_predict_cdna"
    fixture_two = tmp_path / "reports" / "manual_snapshots" / "cdna"
    fixture_one.mkdir(parents=True)
    fixture_two.mkdir(parents=True)
    (fixture_one / "btc.json").write_text(
        json.dumps(
            {
                "event_title": "CDNA BTC threshold page",
                "captured_at": "2026-05-25T12:01:00Z",
                "markets": [
                    {
                        "market_id": "BTC-1",
                        "title": "Will BTC be above $100,000 at 5 PM EDT on Jun 30, 2026?",
                        "asset": "BTC",
                        "threshold": 100000,
                        "direction": "above",
                        "measurement_date": "Jun 30, 2026",
                        "measurement_time": "5 PM EDT",
                        "methodology": (
                            "CDNA U-BTC midpoint data from Lukka or ICE Cryptocurrency Data "
                            "aggregated by Blockstream with 60 seconds and at least 25 midpoint prices."
                        ),
                        "yes_display_price": 0.44,
                        "no_display_price": 0.56,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (fixture_two / "eth.html").write_text(
        """
        <html><body data-market-id="ETH-1">
        <h1>Will Ethereum be above $4,000 at 5 PM EDT on Jun 30, 2026?</h1>
        <p>CDNA U-ETH midpoint data from Lukka or ICE Cryptocurrency Data is aggregated by Blockstream.</p>
        <p>Settlement uses a 60-second period immediately before the event time and at least 25 midpoint prices.</p>
        <span>Yes 41 cents</span>
        </body></html>
        """,
        encoding="utf-8",
    )

    report = build_crypto_com_predict_cdna_research_snapshot(
        fixture_dirs=(fixture_one, fixture_two),
        generated_at=NOW,
    )

    assert report["summary"]["parsed_rows"] == 2
    assert report["summary"]["btc_rows"] == 1
    assert report["summary"]["eth_rows"] == 1
    btc = next(row for row in report["rows"] if row["asset"] == "BTC")
    eth = next(row for row in report["rows"] if row["asset"] == "ETH")
    assert btc["price_source_index"] == CDNA_UBTC_SOURCE_INDEX
    assert eth["price_source_index"] == CDNA_UETH_SOURCE_INDEX
    assert btc["quote_display"]["non_executable"] is True
    assert report["summary"]["basis_risk_compatible_with_kalshi"] == 2
    assert report["summary"]["exact_payoff_compatible_with_kalshi"] == 0


def test_combined_json_array_fixture_expands_one_row_per_threshold(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "cdna"
    fixture_dir.mkdir()
    (fixture_dir / "combined.json").write_text(json.dumps(_combined_fixture()), encoding="utf-8")

    report = build_crypto_com_predict_cdna_research_snapshot(fixture_dir=fixture_dir, generated_at=NOW)

    assert report["summary"]["events_read"] == 5
    assert report["summary"]["parsed_rows"] == 7
    assert report["summary"]["rows_by_asset"] == {"BTC": 4, "ETH": 3}
    assert report["summary"]["rows_by_market_type"] == {
        "all_time_high_by_date": 1,
        "deadline_threshold_touch": 1,
        "earliest_timeframe_threshold_touch": 1,
        "point_in_time_threshold": 2,
        "year_end_range_bucket": 2,
    }
    assert report["summary"]["rows_by_market_shape"] == {
        "all_time_high_by_date": 1,
        "deadline_threshold_touch": 1,
        "earliest_timeframe_threshold_touch": 1,
        "point_in_time_threshold": 2,
        "year_end_range_bucket": 2,
    }
    assert report["summary"]["range_bucket_rows"] == 2
    assert report["summary"]["deadline_rows"] == 2
    assert report["summary"]["deadline_or_range_hit_rows"] == 5
    assert report["summary"]["all_time_high_rows"] == 1
    assert report["summary"]["point_in_time_rows"] == 2
    assert report["summary"]["basis_risk_compatible_with_kalshi"] == 2
    assert report["summary"]["exact_payoff_compatible_with_kalshi"] == 0
    assert "PAPER_CANDIDATE" not in json.dumps(report)

    range_row = next(row for row in report["rows"] if row["market_type"] == "year_end_range_bucket")
    assert range_row["shape_class"] == SHAPE_YEAR_END_RANGE_BUCKET
    assert range_row["market_shape_conservative"] == "year_end_range_bucket"
    assert range_row["matchability_class"] == MATCH_ONE_SIDED_FV_ONLY
    assert range_row["price_source_index"] == CDNA_RULE_1469_BTC_SOURCE_INDEX
    assert range_row["lower"] == 85000.0
    assert range_row["upper"] == 89999.99
    assert range_row["basis_risk_compatible_with_kalshi"] is False
    assert "cdna_saved_fixture_only" in range_row["blockers"]
    assert "settlement_source_unverified" in range_row["blockers"]
    assert "range_hit_vs_close_price_mismatch" in range_row["blockers"]
    assert "range_bucket_fv_only" in range_row["blockers"]

    deadline_row = next(row for row in report["rows"] if row["market_type"] == "deadline_threshold_touch")
    assert deadline_row["shape_class"] == SHAPE_DEADLINE_HIT_BY_DATE
    assert deadline_row["market_shape_conservative"] == "deadline_threshold_touch"
    assert deadline_row["matchability_class"] == MATCH_ONE_SIDED_FV_ONLY
    assert deadline_row["threshold_value"] == 200000.0
    assert deadline_row["strike"] == 200000.0
    assert deadline_row["threshold_operator"] == ">="
    assert deadline_row["comparator"] == ">="
    assert deadline_row["basis_risk_compatible_with_kalshi"] is False
    assert "deadline_vs_point_in_time_mismatch" in deadline_row["blockers"]
    assert "deadline_threshold_touch_fv_only" in deadline_row["blockers"]

    eth_point_rows = [row for row in report["rows"] if row["market_type"] == "point_in_time_threshold"]
    assert len(eth_point_rows) == 2
    for row in eth_point_rows:
        assert row["shape_class"] == SHAPE_POINT_IN_TIME_THRESHOLD
        assert row["market_shape_conservative"] == "point_in_time_threshold"
        assert row["matchability_class"] == MATCH_BASIS_RISK_POSSIBLE
        assert row["price_source_index"] == CDNA_RULE_1472_ETH_SOURCE_INDEX
        assert row["basis_risk_compatible_with_kalshi"] is True
        assert row["source_exact_payoff_compatible_with_kalshi"] is False
        assert row["permission"] == PERMISSION_RESEARCH_ONLY
        assert row["diagnostic_only"] is True
        assert row["affects_evaluator_gates"] is False
        assert row["execution_allowed_in_project_now"] is False
        assert row["can_create_candidate_pair"] is False
        assert row["can_create_paper_candidate"] is False
        assert row["exact_payoff_claimed"] is False
        assert row["paper_candidate_emitted"] is False
        assert row["raw_event_index"] == 4
        assert row["raw_threshold_index"] in {0, 1}


def test_ambiguous_combined_fixture_shape_stays_blocked(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "cdna"
    fixture_dir.mkdir()
    (fixture_dir / "ambiguous.json").write_text(
        json.dumps(
            [
                {
                    "source_platform": "crypto_com_predict",
                    "capture_method": "manual_public_page_extract",
                    "captured_at_utc": "2026-05-26T10:00:00Z",
                    "source_url": "https://example.invalid/ambiguous",
                    "title": "Crypto.com Predict unknown crypto page",
                    "asset": "BTC",
                    "market_type": "unclear_custom_shape",
                    "settlement_rules_methodology_text": "Saved page text without a clear threshold, target date, or reviewed settlement source.",
                    "thresholds": [{"selection": "Maybe"}],
                }
            ]
        ),
        encoding="utf-8",
    )

    report = build_crypto_com_predict_cdna_research_snapshot(fixture_dir=fixture_dir, generated_at=NOW)
    row = report["rows"][0]

    assert row["shape_class"] == SHAPE_AMBIGUOUS
    assert row["market_shape_conservative"] == "ambiguous"
    assert row["diagnostic_only"] is True
    assert row["exact_payoff_claimed"] is False
    assert row["paper_candidate_emitted"] is False
    assert "ambiguous_contract_shape" in row["blockers"]
    assert "missing_threshold" in row["blockers"]
    assert "missing_target_date" in row["blockers"]
    assert "price_source_unverified" in row["blockers"]
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_unknown_eth_methodology_is_cleanly_blocked(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "eth_unknown.json").write_text(
        json.dumps(
            {
                "market_id": "ETH-UNKNOWN",
                "title": "Will ETH be above $4,000 at 5 PM EDT on Jun 30, 2026?",
                "asset": "ETH",
                "threshold": 4000,
                "operator": ">",
                "measurement_date": "Jun 30, 2026",
                "settlement_window": "60_seconds_preceding",
                "settlement_source": "Unreviewed Crypto.com page text without CDNA methodology proof",
            }
        ),
        encoding="utf-8",
    )

    report = build_crypto_com_predict_cdna_research_snapshot(fixture_dir=fixture_dir, generated_at=NOW)
    row = report["rows"][0]

    assert row["asset"] == "ETH"
    assert row["price_source_index"] == "Unreviewed Crypto.com page text without CDNA methodology proof"
    assert "high_unreviewed" in row["blockers"]
    assert row["basis_risk_compatible_with_kalshi"] is False


def test_research_only_permissions_and_no_candidate_creation() -> None:
    report = build_crypto_com_predict_cdna_research_snapshot(
        fixture_dir=PROJECT_ROOT / "venues" / "fixtures" / "crypto_com_predict_cdna",
        generated_at=NOW,
    )
    row = report["rows"][0]

    assert row["permission"] == PERMISSION_RESEARCH_ONLY
    assert row["execution_allowed_in_project_now"] is False
    assert row["can_create_candidate_pair"] is False
    assert row["can_create_paper_candidate"] is False
    assert row["diagnostic_only"] is True
    assert row["affects_evaluator_gates"] is False
    assert "not_integrated_with_matcher_or_evaluator" in row["blockers"]
    assert report["safety"]["affects_evaluator_gates"] is False


def test_source_methodology_is_basis_risk_not_exact() -> None:
    report = build_crypto_com_predict_cdna_research_snapshot(
        fixture_dir=PROJECT_ROOT / "venues" / "fixtures" / "crypto_com_predict_cdna",
        generated_at=NOW,
    )
    row = report["rows"][0]

    assert row["price_source_index"] == CDNA_UBTC_SOURCE_INDEX
    assert row["basis_risk_compatible_with_kalshi"] is True
    assert row["source_exact_payoff_compatible_with_kalshi"] is False
    assert row["basis_risk_severity_hint_vs_kalshi_brti"] == "moderate_known_different_sources_same_window"
    assert "differs from Kalshi BRTI" in row["not_exact_payoff_reason"]
    assert report["summary"]["exact_payoff_compatible_with_kalshi"] == 0
    severity_counts = report["summary"]["basis_risk_severity_hint_counts_vs_kalshi_brti"]
    assert severity_counts.get("moderate_known_different_sources_same_window") == 1


def test_parser_module_adds_no_transport_or_secret_imports() -> None:
    source = (PROJECT_ROOT / "relative_value" / "crypto_com_predict_cdna_saved_page_parser.py").read_text(
        encoding="utf-8"
    )
    import_lines = [line for line in source.splitlines() if line.startswith("import ") or line.startswith("from ")]

    forbidden_import_terms = (
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        "websocket",
        "urllib",
        "http.client",
        "ssl",
        "auth",
        "credential",
        "wallet",
    )
    assert all(term not in line.lower() for line in import_lines for term in forbidden_import_terms)


def test_parse_crypto_com_predict_cdna_fixtures_cli_writes_report(tmp_path, capsys) -> None:
    output = tmp_path / "crypto_com_predict_cdna_research_snapshot.json"

    result = scan.main(
        [
            "parse-crypto-com-predict-cdna-fixtures",
            "--fixture-dir",
            str(PROJECT_ROOT / "venues" / "fixtures" / "crypto_com_predict_cdna"),
            "--json-output",
            str(output),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "crypto_com_predict_cdna_research_snapshot_status=OK" in stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["source"] == "crypto_com_predict_cdna_research_snapshot_v1"
    assert payload["summary"]["btc_rows"] >= 1
    assert payload["summary"]["can_create_paper_candidate_count"] == 0
    assert "PAPER_CANDIDATE" not in json.dumps(payload)


def test_cli_accepts_repeated_fixture_dirs(tmp_path, capsys) -> None:
    fixture_one = tmp_path / "one"
    fixture_two = tmp_path / "two"
    fixture_one.mkdir()
    fixture_two.mkdir()
    (fixture_one / "btc.json").write_text(
        json.dumps(
            {
                "market_id": "BTC-REPEAT",
                "title": "Will BTC be above $100,000 at 5 PM EDT on Jun 30, 2026?",
                "settlement_rule_text": "CDNA U-BTC midpoint data from Lukka or ICE Cryptocurrency Data aggregated by Blockstream. 60 seconds and at least 25 midpoint prices.",
            }
        ),
        encoding="utf-8",
    )
    (fixture_two / "eth.json").write_text(
        json.dumps(
            {
                "market_id": "ETH-REPEAT",
                "title": "Will ETH be above $4,000 at 5 PM EDT on Jun 30, 2026?",
                "settlement_rule_text": "CDNA U-ETH midpoint data from Lukka or ICE Cryptocurrency Data aggregated by Blockstream. 60 seconds and at least 25 midpoint prices.",
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "snapshot.json"

    result = scan.main(
        [
            "parse-crypto-com-predict-cdna-fixtures",
            "--fixture-dir",
            str(fixture_one),
            "--fixture-dir",
            str(fixture_two),
            "--json-output",
            str(output),
        ]
    )

    assert result == 0
    assert "crypto_com_predict_cdna_research_snapshot_status=OK" in capsys.readouterr().out
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"]["parsed_rows"] == 2
    assert payload["summary"]["btc_rows"] == 1
    assert payload["summary"]["eth_rows"] == 1
    assert payload["summary"]["exact_payoff_compatible_with_kalshi"] == 0


def _combined_fixture() -> list[dict]:
    return [
        {
            "source_platform": "Crypto.com Predict",
            "capture_method": "manual_public_page_extract",
            "captured_at_utc": "2026-05-26T10:00:00Z",
            "source_url": "https://example.invalid/btc-range",
            "title": "Bitcoin Price at the End of 2026",
            "asset": "BTC",
            "market_type": "year_end_range_bucket",
            "date_time": {"resolution_reference_time": "11:59 PM Eastern Time on December 31, 2026"},
            "settlement_rules_methodology_text": (
                "This market resolves to the range bucket containing the Nadex BTC Index price at "
                "11:59 PM Eastern Time on December 31, 2026. Settlement is determined solely in "
                "accordance with CDNA Rule 14.69. Rulebook: https://www.nadex.com/rules/."
            ),
            "thresholds": [
                {"selection": "85,000 to 89,999.99", "lower": 85000.0, "upper": 89999.99, "yes_display_price": "$0.12"},
                {"selection": "65,000 to 69,999.99", "lower": 65000.0, "upper": 69999.99, "yes_display_price": "$0.20"},
            ],
        },
        {
            "source_platform": "Crypto.com Predict",
            "capture_method": "manual_public_page_extract",
            "captured_at_utc": "2026-05-26T10:00:00Z",
            "source_url": "https://example.invalid/btc-deadline",
            "title": "Will Bitcoin be above $200k by next year?",
            "asset": "BTC",
            "market_type": "deadline_threshold_touch",
            "date_time": {"resolution_reference_time": "any time on or before 11:59 PM Eastern Time on December 31, 2026"},
            "settlement_rules_methodology_text": (
                "This market resolves Yes if the Nadex BTC Index reaches 200,000 or above any time "
                "on or before 11:59 PM Eastern Time on December 31, 2026. Settlement uses CDNA Rule 14.69."
            ),
            "thresholds": [{"selection": "Yes", "threshold": 200000.0, "operator": ">=", "no_display_price": "$0.96"}],
        },
        {
            "source_platform": "Crypto.com Predict",
            "capture_method": "manual_public_page_extract",
            "captured_at_utc": "2026-05-26T10:00:00Z",
            "source_url": "https://example.invalid/btc-earliest",
            "title": "When will Bitcoin cross $100k again?",
            "asset": "BTC",
            "market_type": "earliest_timeframe_threshold_touch",
            "date_time": {"resolution_reference_time": "earliest specified timeframe ending 11:59 PM ET on December 31, 2026"},
            "settlement_rules_methodology_text": (
                "This market resolves for the earliest timeframe in which the Nadex BTC Index reaches "
                "100,000 or above. Settlement uses CDNA Rule 14.69."
            ),
            "thresholds": [{"selection": "Before June 2026", "threshold": 100000.0, "operator": ">=", "yes_display_price": "$0.98"}],
        },
        {
            "source_platform": "Crypto.com Predict",
            "capture_method": "manual_public_page_extract",
            "captured_at_utc": "2026-05-26T10:00:00Z",
            "source_url": "https://example.invalid/eth-ath",
            "title": "Ethereum all time high by",
            "asset": "ETH",
            "market_type": "all_time_high_by_date",
            "date_time": {"resolution_reference_time": "any time on or before 11:59 PM Eastern Time on the date specified"},
            "settlement_rules_methodology_text": "This market resolves if the Ethereum Expiration Value exceeds all prior values. See CDNA Rule 14.69.",
            "thresholds": [{"selection": "December 31, 2026", "chance_to_win_display": "15%", "yes_display_price": "$0.15"}],
        },
        {
            "source_platform": "Crypto.com Predict",
            "capture_method": "manual_public_page_extract",
            "captured_at_utc": "2026-05-26T10:00:00Z",
            "source_url": "https://example.invalid/eth-point",
            "title": "Ethereum price on 23 May at 9:00 am ET",
            "asset": "ETH",
            "market_type": "point_in_time_threshold",
            "date_time": {"resolution_reference_time": "May 23, 2026 at 9:00 am ET"},
            "settlement_rules_methodology_text": (
                "This market resolves Yes if the Expiration Value for Ethereum is greater than the "
                "specified strike price at the specified time. Settlement is determined solely in "
                "accordance with CDNA Rule 14.72. Rulebook: https://www.nadex.com/rules/."
            ),
            "thresholds": [
                {"selection": ">$2,057.00", "threshold": 2057.0, "operator": ">", "outcome": "No"},
                {"selection": ">$2,050.00", "threshold": 2050.0, "operator": ">", "outcome": "Yes"},
            ],
        },
    ]
