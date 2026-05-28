from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph_engine.relationships.rv_edge_taxonomy import (
    ACTION_BASIS_RISK_REVIEW,
    ACTION_IGNORE_LOW_CONFIDENCE,
    ACTION_MANUAL_REVIEW,
    RV_RELATIONSHIP_TYPES,
)
from graph_engine.reporting.rv_diagnostic_ingest import (
    build_rv_diagnostic_relationship_edges_report,
    render_rv_diagnostic_relationship_edges_markdown,
    validate_rv_diagnostic_relationship_edges_report,
    write_rv_diagnostic_relationship_edges_report,
)
from graph_engine.reporting.safety import find_prohibited_rendered_text


def _kalshi_audit_payload() -> dict:
    return {
        "rows": [
            {
                "market_id": "KXBTC-26MAY2207-T68200",
                "event_ticker": "KXBTC-26MAY2207",
                "asset": "BTC",
                "market_shape": "point_in_time_threshold",
                "comparator": "below",
                "threshold": 68200,
                "target_date": "2026-05-22",
                "target_time": "07:00",
                "timezone": "EDT",
                "settlement_source": "CME CF Bitcoin Reference Rate",
                "settlement_close_time": "2026-05-22T11:00:00Z",
                "settlement_resolution_time": "2026-05-22T11:05:00Z",
                "title": "Bitcoin price at May 22, 2026 at 7am EDT?",
                "typed_completeness_score": 0.92,
                "yes_no_side": "YES",
                "quote": {"present": True},
                "blockers": ["missing_quote", "stale_or_missing_quote"],
                "peer_evidence": {
                    "cdna_candidates": [
                        {
                            "title": "Bitcoin price on 22 May at 7 am ET",
                            "source_url": "https://crypto.com/predict/bitcoin-may-22",
                            "asset": "BTC",
                            "market_shape": "point_in_time_threshold",
                        }
                    ],
                    "polymarket_candidates": [
                        {
                            "market_id": "987654",
                            "condition_id": "0xabc",
                            "title": "Will Bitcoin hit $68k by May 22 2026?",
                            "market_family": "CRYPTO",
                            "market_shape": "deadline_threshold_touch",
                        }
                    ],
                },
                "peer_hints": ["has_peer"],
            },
            {
                "market_id": "KXETH-26MAY2207-T1380",
                "asset": "ETH",
                "market_shape": "point_in_time_threshold",
                "comparator": "above",
                "threshold": 1380,
                "target_date": "2026-05-22",
                "title": "Ethereum price at May 22, 2026 at 7am EDT?",
                "typed_completeness_score": 0.45,
                "quote": {"present": False},
                "blockers": ["missing_quote"],
                "peer_evidence": {"cdna_candidates": [], "polymarket_candidates": []},
                "peer_hints": ["no_saved_peer"],
            },
        ],
    }


def _polymarket_pit_payload() -> dict:
    return {
        "rows": [
            {
                "market_id": "1299974",
                "condition_id": "0x3de44d7bad27379abaebb57485589cbb2a309aba16219bcbe182124acdf70800",
                "asset_or_family": "CRYPTO",
                "market_family": "company_metric",
                "market_shape": "point_in_time_threshold",
                "comparator": ">=",
                "threshold": 400,
                "target_date": "December 31, 2026",
                "target_time": "4:00 PM ET",
                "title": "StandX FDV above $400M one day after launch?",
                "settlement_source_present": True,
                "typed_key_completeness_score": 0.83,
                "blockers": ["title_only_match_not_equivalence", "no_saved_peer_family"],
                "peer_lane_hints": {
                    "likely_no_current_peer": True,
                    "likely_kalshi_peer_family": None,
                    "likely_cdna_peer_family": None,
                },
                "clob_book_attached": True,
            }
        ],
    }


def _cdna_basis_risk_payload() -> dict:
    return {
        "rows": [
            {
                "row_id": "ETH::point_in_time_threshold::2_022.00::KXETH-26MAY2207-T1380",
                "allowed_next_action": "BASIS_RISK_REVIEW",
                "basis_risk_priority_score": 20.0,
                "cdna": {
                    "asset": "ETH",
                    "comparator": ">",
                    "market_shape_conservative": "point_in_time_threshold",
                    "market_type": "point_in_time_threshold",
                    "outcome": ">$2,022.00",
                    "settlement_source": None,
                    "source_url": "https://web.crypto.com/explore/predict/events/details/eth-22",
                    "target_date": "May 23, 2026",
                    "threshold_value": 2022.0,
                    "title": "Ethereum price on 23 May at 9:00 am ET",
                    "price_source_index": "CDNA Rule 14.72 / CDNA ETH source",
                },
                "peer": {
                    "peer_threshold": 1380.0,
                    "settlement_close_time": "2026-05-22T11:00:00Z",
                    "settlement_resolution_time": "2026-05-22T11:05:00Z",
                    "ticker_or_event": "KXETH-26MAY2207-T1380",
                    "title": "Ethereum price at May 22, 2026 at 7am EDT?",
                    "venue": "kalshi",
                },
                "blockers": [
                    "cdna_saved_fixture_only",
                    "settlement_source_unverified",
                    "threshold_distance_large_relative_to_peer",
                ],
            },
            {
                "row_id": "BTC::deadline_threshold_touch::70_000::KXBTC-26MAY2207-T70000",
                "allowed_next_action": "BASIS_RISK_REVIEW",
                "basis_risk_priority_score": 12.0,
                "cdna": {
                    "asset": "BTC",
                    "comparator": ">=",
                    "market_shape_conservative": "deadline_threshold_touch",
                    "title": "Bitcoin hit $70k before Jun 2026?",
                    "threshold_value": 70000.0,
                },
                "peer": {},
                "blockers": ["deadline_vs_point_in_time_mismatch"],
            },
        ]
    }


def _cross_venue_payload() -> dict:
    return {
        "rows": [
            {
                "row_id": "xv:fed:1",
                "lane": "POLYMARKET_FED_vs_KALSHI_FED_FOMC",
                "active_platforms": ["kalshi", "polymarket"],
                "allowed_next_action": "BASIS_RISK_REVIEW",
                "review_priority_score": 4.5,
                "left": {
                    "exchange_venue": "polymarket",
                    "market_id": "poly_fed_jun",
                    "event_family": "FED_FOMC",
                    "settlement_source": "Federal Reserve Board",
                    "comparator": "greater_than",
                },
                "right": {
                    "exchange_venue": "kalshi",
                    "market_id": "KXFED-26JUN-T2.75",
                    "event_family": "FED_FOMC",
                    "settlement_source": "Federal Reserve Board",
                    "comparator": "greater_than",
                },
                "comparison": {
                    "settlement_source_relation": "midpoint_vs_upper_bound_mismatch",
                    "same_family": True,
                    "same_market_shape": True,
                    "same_meeting_date": True,
                    "same_threshold_after_convention_translation": "approx_equivalent",
                },
                "blockers": ["midpoint_vs_upper_bound_mismatch", "fee_model_missing"],
            }
        ]
    }


def _core_trio_payload() -> dict:
    return {
        "families": [
            {
                "family": "crypto_price_threshold",
                "blockers": ["cdna_basis_risk_only"],
                "next_fetch_query_suggestion": "Fetch BTC and ETH Kalshi series.",
                "kalshi_typed_complete_rows_found": 1237,
                "polymarket_typed_complete_rows": 3,
                "cdna_point_in_time_rows": 6,
            },
            {
                "family": "weather",
                "blockers": ["polymarket_peer_missing_settlement_source"],
                "next_fetch_query_suggestion": "Fetch Kalshi weather snapshot.",
                "kalshi_typed_complete_rows_found": 50,
                "polymarket_typed_complete_rows": 1,
                "cdna_point_in_time_rows": 0,
            },
        ]
    }


def _ibkr_payload() -> dict:
    return {
        "rows": [
            {
                "contract_conid": 779027722,
                "symbol": "FF",
                "maturity_date": "20260617",
                "title": "JUN 17 '26 1.38 YES @FORECASTX",
                "quote_blockers": [
                    "ibkr_forecastex_marketdata_permission_review_required",
                    "ibkr_forecastex_not_execution_ready",
                ],
            }
        ],
        "summary": {
            "final_contract_rows": 28,
            "rows_quote_diagnostic_complete": 8,
            "top_quote_blockers": [
                {"blocker": "ibkr_forecastex_marketdata_permission_review_required", "count": 28},
            ],
        },
    }


def _write_fake_rv_reports(tmp_path: Path) -> Path:
    rv_dir = tmp_path / "rv_reports"
    rv_dir.mkdir()
    (rv_dir / "kalshi_crypto_typed_key_audit.json").write_text(json.dumps(_kalshi_audit_payload()), encoding="utf-8")
    (rv_dir / "polymarket_point_in_time_typed_key_audit.json").write_text(json.dumps(_polymarket_pit_payload()), encoding="utf-8")
    (rv_dir / "cdna_crypto_basis_risk_scout.json").write_text(json.dumps(_cdna_basis_risk_payload()), encoding="utf-8")
    (rv_dir / "cross_venue_opportunity_scout.json").write_text(json.dumps(_cross_venue_payload()), encoding="utf-8")
    (rv_dir / "core_trio_peer_coverage_audit.json").write_text(json.dumps(_core_trio_payload()), encoding="utf-8")
    (rv_dir / "ibkr_forecastex_quote_diagnostics.json").write_text(json.dumps(_ibkr_payload()), encoding="utf-8")
    return rv_dir


def test_importer_builds_nodes_and_edges(tmp_path: Path) -> None:
    rv_dir = _write_fake_rv_reports(tmp_path)
    report = build_rv_diagnostic_relationship_edges_report(rv_dir)
    summary = report["summary"]
    assert summary["total_nodes"] >= 4
    assert summary["total_edges"] >= 3
    type_names = {row["relationship_type"] for row in summary["edges_by_relationship_type"]}
    assert "DEADLINE_TOUCH_VS_POINT_IN_TIME" in type_names
    assert "BASIS_RISK_SAME_ASSET_DIFFERENT_SOURCE" in type_names or "SAME_EVENT_DIFFERENT_SOURCE_REVIEW" in type_names
    assert "NO_CURRENT_PEER" in type_names


def test_importer_report_is_diagnostic_only_and_no_exact_promotion(tmp_path: Path) -> None:
    rv_dir = _write_fake_rv_reports(tmp_path)
    report = build_rv_diagnostic_relationship_edges_report(rv_dir)
    assert report["diagnostic_only"] is True
    assert report["affects_evaluator_gates"] is False
    for edge in report["edges"]:
        assert edge["exact_payoff"] is False
        assert edge["can_create_candidate_pair"] is False
        assert edge["can_emit_evaluator_input"] is False


def test_importer_writes_files_and_clean_markdown(tmp_path: Path) -> None:
    rv_dir = _write_fake_rv_reports(tmp_path)
    json_out = tmp_path / "edges.json"
    md_out = tmp_path / "edges.md"
    report = write_rv_diagnostic_relationship_edges_report(
        rv_reports_dir=rv_dir, json_output=json_out, markdown_output=md_out
    )
    text = md_out.read_text(encoding="utf-8")
    assert "# RV Diagnostic Relationship Edges" in text
    assert find_prohibited_rendered_text(text) == []
    assert json_out.exists()
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    validate_rv_diagnostic_relationship_edges_report(payload)
    assert payload["summary"]["total_edges"] == report["summary"]["total_edges"]


def test_importer_handles_missing_directory(tmp_path: Path) -> None:
    report = build_rv_diagnostic_relationship_edges_report(tmp_path / "does_not_exist")
    assert report["summary"]["total_edges"] == 0
    assert report["inputs"]["available"] == []
    assert "kalshi_crypto_typed_key_audit" in report["inputs"]["missing"]


def test_importer_markdown_renderer_is_safe(tmp_path: Path) -> None:
    rv_dir = _write_fake_rv_reports(tmp_path)
    report = build_rv_diagnostic_relationship_edges_report(rv_dir)
    md = render_rv_diagnostic_relationship_edges_markdown(report)
    assert find_prohibited_rendered_text(md) == []


def test_cdna_scout_basis_risk_action_is_basis_risk(tmp_path: Path) -> None:
    rv_dir = _write_fake_rv_reports(tmp_path)
    report = build_rv_diagnostic_relationship_edges_report(rv_dir)
    cdna_edges = [edge for edge in report["edges"] if edge["left_venue"] == "cdna"]
    assert cdna_edges
    actions = {edge["action"] for edge in cdna_edges}
    assert ACTION_BASIS_RISK_REVIEW in actions or ACTION_MANUAL_REVIEW in actions
    for edge in cdna_edges:
        assert edge["relationship_family"] in {"basis_risk", "near_exact_review", "structural", "weak_signal"}


def test_no_current_peer_edge_default_action(tmp_path: Path) -> None:
    rv_dir = _write_fake_rv_reports(tmp_path)
    report = build_rv_diagnostic_relationship_edges_report(rv_dir)
    no_peer = [edge for edge in report["edges"] if edge["relationship_type"] == "NO_CURRENT_PEER"]
    assert no_peer
    assert all(edge["action"] == ACTION_IGNORE_LOW_CONFIDENCE for edge in no_peer)
