from __future__ import annotations

import json

import scan
from graph_engine.loader import load_fixture_markets
from graph_engine.reporting.safety import find_prohibited_report_tokens
from graph_engine.reporting.saved_quote_overlay_status import validate_saved_quote_overlay_status_report
from graph_engine.reporting.stale_lag_watchlist import (
    FRESHNESS_BUCKET_FRESH,
    FRESHNESS_BUCKET_MISSING_TIMESTAMP,
    build_stale_lag_watchlist_report,
)
from tests.conftest import PROJECT_ROOT


REAL_QUOTE_FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "real_quote_fixtures"
BTC_PAIR = {"fixture:btc_over_140k_june", "fixture:btc_over_120k_june"}


def _blocker_count(report: dict, blocker: str) -> int:
    return sum(
        1
        for row in report["stale_lag_watchlist"]
        if blocker in row.get("blockers", [])
    )


def _row_for_pair(report: dict, market_ids: set[str]) -> dict:
    for row in report["stale_lag_watchlist"]:
        if set(row["markets_involved"]) == market_ids:
            return row
    raise AssertionError(f"missing row for {sorted(market_ids)}")


def test_real_quote_fixture_overlay_refreshes_quote_inputs_and_keeps_reports_diagnostic() -> None:
    default_snapshot, _, default_mode = scan._load_fixture_mode()
    overlay_snapshot, metadata, overlay_mode = scan._load_fixture_mode(REAL_QUOTE_FIXTURES)

    default_report = build_stale_lag_watchlist_report(default_snapshot)
    overlay_report = build_stale_lag_watchlist_report(overlay_snapshot)
    pair_row = _row_for_pair(overlay_report, BTC_PAIR)

    assert default_mode == "fixtures"
    assert overlay_mode == "fixtures_with_real_quote_fixtures"
    assert metadata[-1]["markets_overlayed"] == 2
    assert metadata[-1]["markets_added"] == 5
    assert metadata[-1]["markets_imported"] == 7
    assert metadata[-1]["quote_rows_imported"] == 5
    assert metadata[-1]["blockers"] == []
    assert overlay_report["freshness_buckets"][FRESHNESS_BUCKET_FRESH] > 0
    assert pair_row["freshness_bucket"] == FRESHNESS_BUCKET_FRESH
    assert {
        pair_row["quote_age_seconds"],
        pair_row["related_market_quote_age_seconds"],
    } == {60, 240}
    assert "non_actionable_probability_input" not in pair_row["blockers"]
    assert _blocker_count(default_report, "non_actionable_probability_input") == 32
    assert overlay_report["diagnostic_only"] is True
    assert overlay_report["affects_evaluator_gates"] is False
    assert overlay_report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert find_prohibited_report_tokens(overlay_report) == []


def test_real_quote_fixture_flag_writes_fresh_ops_status(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(scan, "REPORTS_DIR", tmp_path)

    result = scan.main(["--real-quote-fixtures-dir", str(REAL_QUOTE_FIXTURES)])

    stale_report = json.loads((tmp_path / "market_graph_stale_lag_watchlist.json").read_text(encoding="utf-8"))
    ops_report = json.loads((tmp_path / "market_graph_ops_status.json").read_text(encoding="utf-8"))
    overlay_report = json.loads((tmp_path / "market_graph_saved_quote_overlay_status.json").read_text(encoding="utf-8"))
    rv_packets = json.loads((tmp_path / "graph_to_relative_value_investigation_packets.json").read_text(encoding="utf-8"))
    top_blockers = ops_report["top_blockers_by_frequency"]
    pair_row = _row_for_pair(stale_report, BTC_PAIR)

    assert result == 0
    assert stale_report["freshness_buckets"][FRESHNESS_BUCKET_FRESH] > 0
    assert ops_report["summary"]["freshness_buckets"][FRESHNESS_BUCKET_FRESH] > 0
    assert ops_report["diagnostic_only"] is True
    assert ops_report["affects_evaluator_gates"] is False
    assert ops_report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert top_blockers
    assert "non_actionable_probability_input" not in pair_row["blockers"]
    assert overlay_report["markets_imported"] == 7
    assert overlay_report["quote_rows_imported"] == 5
    assert overlay_report["freshness_buckets"][FRESHNESS_BUCKET_FRESH] > 0
    assert overlay_report["freshness_buckets"][FRESHNESS_BUCKET_MISSING_TIMESTAMP] > 0
    assert overlay_report["top_blockers_before_overlay"]
    assert overlay_report["top_blockers_after_overlay"]
    assert overlay_report["packet_kind_counts"]
    assert overlay_report["top_rel_value_handoff_candidates"]
    validate_saved_quote_overlay_status_report(overlay_report)
    reference_packets = [
        packet for packet in rv_packets["investigation_packets"] if packet["packet_kind"] == "FAIR_VALUE_REFERENCE_ONLY"
    ]
    assert reference_packets
    assert all(packet["diagnostic_only"] is True for packet in reference_packets)
    assert all(packet["affects_evaluator_gates"] is False for packet in reference_packets)
    assert all(packet["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"] for packet in reference_packets)
    assert all("reference_only_source" in packet["packet_blockers"] for packet in reference_packets)
    assert find_prohibited_report_tokens(stale_report) == []
    assert find_prohibited_report_tokens(ops_report) == []
    assert find_prohibited_report_tokens(overlay_report) == []
    assert find_prohibited_report_tokens(rv_packets) == []


def test_saved_rv_fixture_shapes_are_imported_with_source_blockers() -> None:
    overlay_snapshot, metadata, mode = scan._load_fixture_mode(REAL_QUOTE_FIXTURES)
    nodes = overlay_snapshot.nodes

    assert mode == "fixtures_with_real_quote_fixtures"
    assert metadata[-1]["files_imported"] == [
        "ibkr_forecastex_quote_diagnostics.json",
        "kalshi_saved_quote_row.json",
        "midpoint_only_row.json",
        "missing_timestamp_quote_row.json",
        "odds_api_reference_only_row.json",
        "schema_v1_real_quotes.json",
    ]
    ibkr = nodes["ibkr_forecastex:1001"]
    kalshi = nodes["kalshi:kxbtcsample-26jun30-t120000"]
    odds = nodes["the_odds_api:odds-api-reference-btc-sample"]
    missing_timestamp = nodes["sx_bet:sx-bet-missing-timestamp-sample"]
    midpoint_only = nodes["polymarket:polymarket-midpoint-only-sample"]

    assert ibkr.bid == 0.44
    assert ibkr.ask == 0.48
    assert ibkr.raw["bid_size"] == 100.0
    assert ibkr.raw["ask_size"] == 90.0
    assert "rv_saved_file_only" in ibkr.raw["review_blockers"]
    assert "settlement_not_verified_by_graph" in ibkr.raw["review_blockers"]
    assert kalshi.bid == 0.62
    assert kalshi.ask == 0.66
    assert odds.reference_only is True
    assert "reference_only_source" in odds.raw["review_blockers"]
    assert missing_timestamp.raw["quote_timestamp_missing"] is True
    assert "missing_quote_timestamp" in missing_timestamp.raw["review_blockers"]
    assert midpoint_only.bid is None
    assert midpoint_only.ask is None
    assert midpoint_only.yes_price is None
    assert "midpoint_only_saved_row" in midpoint_only.raw["review_blockers"]


def test_fixture_mode_unchanged_when_real_quote_flag_absent() -> None:
    direct_snapshot, _ = load_fixture_markets(PROJECT_ROOT / "venues" / "fixtures")
    scan_snapshot, metadata, mode = scan._load_fixture_mode()
    market_id = "fixture:btc_over_140k_june"

    assert mode == "fixtures"
    assert metadata[-1]["file"] != "real_quote_fixture_overlay"
    assert scan_snapshot.snapshot_id == direct_snapshot.snapshot_id
    assert scan_snapshot.nodes[market_id].yes_price == direct_snapshot.nodes[market_id].yes_price
    assert scan_snapshot.nodes[market_id].bid == direct_snapshot.nodes[market_id].bid
    assert scan_snapshot.nodes[market_id].ask == direct_snapshot.nodes[market_id].ask
    assert scan_snapshot.nodes[market_id].as_of == direct_snapshot.nodes[market_id].as_of


def test_ambiguous_real_quote_fixture_rows_are_blocked_without_guessing(tmp_path) -> None:
    ambiguous = tmp_path / "ambiguous.json"
    ambiguous.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "snapshot_id": "ambiguous-real-quote-fixture",
                "as_of": "2026-05-20T18:00:00+00:00",
                "venue": "fixture",
                "normalized_markets": [
                    {
                        "market_id": "fixture:btc_over_140k_june",
                        "title": "BTC above 140k by June 30",
                        "bid": 0.58,
                        "ask": 0.66,
                        "as_of": "2026-05-20T17:59:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    snapshot, metadata, mode = scan._load_fixture_mode(tmp_path)
    blocker_names = metadata[-1]["blockers"]

    assert mode == "fixtures_with_real_quote_fixtures"
    assert metadata[-1]["markets_overlayed"] == 0
    assert any("real_quote_fixture_missing_observed_yes_price" in blocker for blocker in blocker_names)
    assert snapshot.nodes["fixture:btc_over_140k_june"].yes_price == 0.74
