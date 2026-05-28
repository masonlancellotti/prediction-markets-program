from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import scan
from relative_value.cross_venue_opportunity_scout import (
    ACTION_BASIS_RISK_REVIEW,
    ACTION_IGNORE_BLOCKED,
    ACTION_MANUAL_REVIEW,
    ACTION_SOURCE_REVIEW,
    ACTION_WATCH,
    B_BROKER_ROUTE_NOT_INDEPENDENT,
    B_COMPARATOR_MISMATCH,
    B_DO_NOT_CROSS_COMPARE,
    B_IBKR_KALSHI_SAME,
    B_IBKR_PLANNED,
    B_IBKR_UI_NOT_CAPTURED,
    B_MIDPOINT_VS_UPPER,
    B_POINT_VS_DEADLINE,
    B_CDNA_SETTLEMENT_BASIS_RISK,
    B_EXACT_PAYOFF_NOT_PROVEN,
    B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME,
    B_POLYMARKET_MISSING_CLOB_BOOK,
    B_POLYMARKET_REGISTRY_BLOCKS,
    B_POLYMARKET_STALE_OR_MISSING_QUOTE,
    B_POLYMARKET_TITLE_ONLY,
    B_RANGE_VS_CLOSE,
    B_REFERENCE_ONLY,
    B_REGISTRY_BLOCKS,
    B_SETTLEMENT_RULES_NEED_REVIEW,
    B_SETTLEMENT_SOURCE_MISMATCH,
    B_SETTLEMENT_WINDOW_MISMATCH,
    B_THRESHOLD_MISSING,
    LANE_CDNA_BTC_VS_KALSHI_BTC,
    LANE_IBKR_FF_VS_KALSHI_FED,
    LANE_ODDS_API_REFERENCE,
    LANE_POLYMARKET_CRYPTO_VS_CDNA_CRYPTO,
    LANE_POLYMARKET_CRYPTO_VS_KALSHI_CRYPTO,
    LANE_POLYMARKET_FED_VS_KALSHI_FED,
    LANE_SX_BET,
    build_cross_venue_opportunity_scout_report,
    write_cross_venue_opportunity_scout_files,
)
from relative_value.source_registry import ImplementationStatus, SOURCE_REGISTRY


def _write_input(input_dir: Path, relpath: str, payload: Any) -> Path:
    path = input_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _ibkr_quote_diagnostics_payload(strike: float = 4.375) -> dict[str, Any]:
    return {
        "schema_kind": "ibkr_forecastex_quote_diagnostics_v1",
        "source": "ibkr_forecastex_quote_diagnostics_v1",
        "generated_at": "2026-05-26T20:00:00+00:00",
        "rows": [
            {
                "access_platform": "IBKR",
                "affects_evaluator_gates": False,
                "ask": 0.04,
                "ask_size": 10.0,
                "bid": 0.01,
                "bid_size": 10.0,
                "contract_conid": 748336910,
                "diagnostic_only": True,
                "exchange_venue": "FORECASTX",
                "executable_venue": "FORECASTX",
                "execution_ready": False,
                "marketdata_status_raw": "R",
                "maturity_date": "20260617",
                "month": "JUN26",
                "observed_at": "2026-05-26T19:14:40+00:00",
                "quote_blockers": [],
                "quote_diagnostic_complete": True,
                "quote_timestamp": "2026-05-26T19:14:40+00:00",
                "right": "C",
                "source_platform": "IBKR",
                "strike": strike,
                "symbol": "FF",
                "venue": "IBKR_FORECASTEX",
                "yes_no_side": "YES",
            }
        ],
        "summary": {"final_contract_rows": 1, "rows_quote_diagnostic_complete": 1, "rows_execution_ready": 0},
    }


def _ibkr_normalized_payload() -> dict[str, Any]:
    return {
        "schema_kind": "ibkr_forecastex_normalized_draft_v1",
        "summary": {"final_tradable_rows": 1},
    }


def _ibkr_memo_validation_payload(passed: bool = True) -> dict[str, Any]:
    return {
        "schema_kind": "ibkr_forecastex_ff_manual_ui_memo_validation_v1",
        "source": "ibkr_forecastex_manual_ui_memo_validation_v1",
        "validation_passed": passed,
        "blockers": [],
        "summary": {"validation_blocker_count": 0, "source_registry_unchanged": True},
    }


def _ibkr_jun26_memo_payload() -> dict[str, Any]:
    return {
        "schema_kind": "ibkr_forecastex_ff_manual_ui_memo_v1",
        "ibkr_forecastx_month_reviewed": "JUN26",
        "api_month_currently_fetched": "JUN26",
        "applies_to_other_months": "yes_verified",
        "threshold_semantics": "midpoint",
        "comparator_semantics": "greater_than",
        "settlement_event_date": "2026-06-17",
        "fomc_meeting_date": "2026-06-17",
        "settlement_source_name": "Federal Reserve Board – Open Market Operations",
        "settlement_source_url": "https://www.federalreserve.gov/monetarypolicy/openmarket.htm",
        "expiration_and_last_trading_time": "2026-06-17T13:00:00-05:00",
        "ibkr_ui_capture_status": "not_captured",
        "sample_strikes": [4.375],
    }


def _normalized_markets_payload_with_kalshi_fed(meeting_date: str = "2026-06-17", thresholds: tuple[float, ...] = (4.50, 4.25, 4.00)) -> dict[str, Any]:
    rows = []
    for threshold in thresholds:
        rows.append(
            {
                "venue": "kalshi",
                "event_ticker": "KXFED-26JUN",
                "ticker": f"KXFED-26JUN-T{threshold:.2f}",
                "market_id": f"market_kxfed_{threshold}",
                "title": f"Will the upper bound of the federal funds rate be above {threshold:.2f}% following the Fed's Jun 17, 2026 meeting?",
                "settlement": {
                    "resolution_time": f"{meeting_date}T18:05:00Z",
                    "close_time": f"{meeting_date}T17:55:00Z",
                    "settlement_source_url": None,
                    "settlement_rules_text": "Resolution from Federal Reserve.",
                    "settlement_source_kind": "text_evidence",
                },
                "quote_depth": {
                    "best_yes_bid_price": 0.10,
                    "best_yes_ask_price": 0.12,
                    "best_yes_bid_size": 100.0,
                    "best_yes_ask_size": 100.0,
                    "captured_at": "2026-05-26T19:00:00+00:00",
                },
            }
        )
    return {"normalized_markets": rows}


def _odds_api_payload(tmp_path: Path) -> None:
    odds_dir = tmp_path / "manual_snapshots" / "the_odds_api" / "20260526_x"
    odds_dir.mkdir(parents=True, exist_ok=True)
    (odds_dir / "oddsapi_baseball_mlb_odds.json").write_text(
        json.dumps([
            {
                "id": "odds_evt_1",
                "sport_key": "baseball_mlb",
                "sport_title": "MLB",
                "home_team": "Cleveland Guardians",
                "away_team": "Washington Nationals",
                "commence_time": "2026-05-26T22:11:00Z",
                "bookmakers": [],
            }
        ]),
        encoding="utf-8",
    )


def _cdna_payload(*, range_hit: bool = True) -> dict[str, Any]:
    blockers = [
        "research_only_saved_fixture",
        "settlement_source_unverified",
        "execution_not_allowed_in_project_now",
        "candidate_pair_creation_forbidden",
    ]
    if range_hit:
        blockers.extend(["range_hit_vs_close_price_mismatch", "range_bucket_fv_only", "not_basis_risk_comparable_with_kalshi_point_in_time"])
    return {
        "schema_kind": "crypto_com_predict_cdna_research_snapshot_v1",
        "source": "crypto_com_predict_cdna_research_snapshot_v1",
        "rows": [
            {
                "venue": "crypto_com_predict_cdna",
                "source_platform": "crypto_com_predict_cdna",
                "asset": "BTC",
                "title": "Bitcoin range hit by end of year",
                "market_id": "cdna_btc_eoy",
                "event_id": "cdna_btc_eoy_event",
                "target_date": "2026-12-31",
                "threshold_value": 100000.0,
                "threshold_operator": ">=",
                "comparator": ">=",
                "market_shape_normalized": "range_hit",
                "market_shape": "range_hit",
                "settlement_source": "Crypto.com Predict reference",
                "settlement_source_url": None,
                "measurement_time": "2026-12-31T23:59:59Z",
                "captured_at_utc": "2026-05-26T18:00:00+00:00",
                "raw_source_file": "manual_snapshots/cdna/sample.json",
                "blockers": blockers,
                "basis_risk_compatible_with_kalshi": False,
                "source_exact_payoff_compatible_with_kalshi": False,
                "can_create_candidate_pair": False,
                "can_create_paper_candidate": False,
                "diagnostic_only": True,
            }
        ],
    }


def _kalshi_crypto_row(*, asset: str = "BTC", threshold: float = 86000.0, date: str = "2026-05-25") -> dict[str, Any]:
    prefix = "KXBTC" if asset == "BTC" else "KXETH"
    ticker = f"{prefix}-26MAY2517-T{threshold:.0f}"
    title_asset = "Bitcoin" if asset == "BTC" else "Ethereum"
    return {
        "venue": "kalshi",
        "event_ticker": f"{prefix}-26MAY2517",
        "ticker": ticker,
        "market_id": ticker,
        "title": f"{title_asset} price above {threshold:.0f} at close?",
        "settlement": {
            "resolution_time": f"{date}T21:05:00Z",
            "close_time": f"{date}T21:00:00Z",
            "settlement_rules_text": "Resolution uses CF Benchmarks real-time index average.",
            "settlement_source_kind": "rules_text_only",
            "settlement_source_url": None,
        },
        "quote_depth": {
            "best_yes_bid_price": 0.45,
            "best_yes_ask_price": 0.50,
            "best_yes_bid_size": 100.0,
            "best_yes_ask_size": 100.0,
            "captured_at": "2026-05-26T19:00:00+00:00",
        },
    }


def _polymarket_enriched_payload(
    *,
    shape: str = "point_in_time_threshold",
    asset: str = "BTC",
    threshold: float = 86000.0,
    date_text: str = "May 25, 2026",
    question: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    title = title or f"{asset} test market"
    blockers = [
        B_POLYMARKET_REGISTRY_BLOCKS,
        B_POLYMARKET_MISSING_CLOB_BOOK,
        B_POLYMARKET_STALE_OR_MISSING_QUOTE,
        B_POLYMARKET_TITLE_ONLY,
    ]
    return {
        "schema_kind": "polymarket_taxonomy_shape_scout_enriched_v1",
        "source": "polymarket_taxonomy_shape_scout_enriched_v1",
        "rows": [
            {
                "row_id": f"poly_{shape}_{asset}",
                "venue": "polymarket",
                "family": "CRYPTO",
                "market_shape": shape,
                "market_id": f"poly_{shape}_{asset}",
                "market_slug": f"poly-{shape}-{asset.lower()}",
                "condition_id": "0xpoly",
                "title": title,
                "question": question or f"Will {asset} be above {threshold:.0f} on {date_text}?",
                "source_url": "https://example.com/source",
                "settlement_rules_text_present": True,
                "settlement_source_present": True,
                "exact_matchability_score": 43.0,
                "blockers": blockers,
                "typed_keys": {
                    "asset": asset,
                    "measurement_date": date_text,
                    "measurement_time": "5:00 PM ET",
                    "price_source_index": "Binance",
                    "threshold_operator": ">=",
                    "threshold_value": threshold,
                },
                "clob_refresh": {
                    "attached_quote": {
                        "attached": True,
                        "missing_book": False,
                        "inferred_from_midpoint_or_complement": False,
                        "bid": 0.42,
                        "ask": 0.47,
                        "bid_size": 12.0,
                        "ask_size": 15.0,
                        "quote_timestamp": "2026-05-26T19:30:00+00:00",
                        "observed_at": "2026-05-26T19:30:00+00:00",
                        "raw_book_file": "reports/manual_snapshots/polymarket_clob_taxonomy/book_test.json",
                        "token_id": "token_yes",
                        "condition_id": "0xpoly",
                    }
                },
                "clob_book_attached": True,
            }
        ],
        "summary": {"total_rows": 1, "exact_ready_rows": 0, "paper_candidate_rows": 0},
    }


def _cdna_point_in_time_payload(*, asset: str = "BTC", threshold: float = 86000.0, date_text: str = "May 25, 2026") -> dict[str, Any]:
    return {
        "schema_kind": "crypto_com_predict_cdna_research_snapshot_v1",
        "source": "crypto_com_predict_cdna_research_snapshot_v1",
        "rows": [
            {
                "venue": "crypto_com_predict_cdna",
                "source_platform": "crypto_com_predict_cdna",
                "asset": asset,
                "title": f"{asset} price on {date_text}",
                "market_shape": "POINT_IN_TIME_THRESHOLD",
                "market_shape_normalized": "point_in_time_threshold",
                "target_date": date_text,
                "measurement_date": date_text,
                "measurement_time": "5:00 PM ET",
                "threshold_value": threshold,
                "threshold_operator": ">=",
                "comparator": ">=",
                "settlement_source": None,
                "settlement_source_url": "https://www.nadex.com/rules/.",
                "captured_at_utc": "2026-05-26T19:00:00+00:00",
                "blockers": [
                    "research_only_saved_fixture",
                    "settlement_source_unverified",
                    "candidate_pair_creation_forbidden",
                    "basis_risk_possible_not_exact_payoff",
                ],
                "basis_risk_compatible_with_kalshi": True,
                "source_exact_payoff_compatible_with_kalshi": False,
                "can_create_candidate_pair": False,
                "can_create_paper_candidate": False,
                "diagnostic_only": True,
            }
        ],
        "summary": {"point_in_time_rows": 1},
    }


def _setup_inputs(tmp_path: Path, *, include_kalshi_fed: bool = True, include_cdna: bool = True, include_odds: bool = False) -> Path:
    input_dir = tmp_path
    _write_input(input_dir, "ibkr_forecastex_quote_diagnostics.json", _ibkr_quote_diagnostics_payload())
    _write_input(input_dir, "ibkr_forecastex_normalized_draft.json", _ibkr_normalized_payload())
    _write_input(input_dir, "ibkr_forecastex_manual_ui_memo_validation.json", _ibkr_memo_validation_payload())
    _write_input(input_dir, "manual_snapshots/ibkr_forecastex/ff_jun26_manual_ui_memo.json", _ibkr_jun26_memo_payload())
    if include_kalshi_fed:
        _write_input(input_dir, "normalized_markets_v0.json", _normalized_markets_payload_with_kalshi_fed())
    else:
        _write_input(input_dir, "normalized_markets_v0.json", {"normalized_markets": []})
    if include_cdna:
        _write_input(input_dir, "crypto_com_predict_cdna_research_snapshot.json", _cdna_payload())
    if include_odds:
        _odds_api_payload(tmp_path)
    return input_dir


def test_ibkr_ff_vs_kalshi_fomc_emits_source_review_not_candidate_pair(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)

    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir)

    ibkr_rows = [r for r in report["rows"] if r["lane"] == LANE_IBKR_FF_VS_KALSHI_FED]
    assert ibkr_rows, "expected an IBKR FF vs Kalshi FED row"
    row = ibkr_rows[0]
    assert row["allowed_next_action"] == ACTION_SOURCE_REVIEW
    assert row["can_create_candidate_pair"] is False
    assert row["can_create_paper_candidate"] is False
    assert row["exact_ready"] is False
    assert row["paper_candidate"] is False
    assert B_MIDPOINT_VS_UPPER in row["blockers"]
    assert B_SETTLEMENT_SOURCE_MISMATCH in row["blockers"]
    assert B_IBKR_UI_NOT_CAPTURED in row["blockers"]
    assert B_IBKR_PLANNED in row["blockers"]
    assert B_REGISTRY_BLOCKS in row["blockers"]


def test_ibkr_ff_row_with_ibkr_ui_not_captured_remains_blocked(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir)
    row = next(r for r in report["rows"] if r["lane"] == LANE_IBKR_FF_VS_KALSHI_FED)
    assert B_IBKR_UI_NOT_CAPTURED in row["blockers"]
    assert B_SETTLEMENT_RULES_NEED_REVIEW in row["blockers"]
    assert row["allowed_next_action"] == ACTION_SOURCE_REVIEW


def test_ibkr_kalshi_broker_route_fake_edge_remains_blocked() -> None:
    from relative_value.cross_venue_opportunity_scout import _compose_row

    left_ibkr_kalshi = {
        "venue": "IBKR_KALSHI",
        "source_platform": "IBKR",
        "access_platform": "IBKR",
        "exchange_venue": "KALSHI",
        "executable_venue": "KALSHI",
        "market_id_or_conid": "ibkr_route_market",
        "ticker_or_symbol": "IBKR_ROUTE_KALSHI_TICKER",
        "event_family": "FED_FOMC",
        "settlement_event_date": "2026-06-17",
        "threshold": 4.25,
        "threshold_semantics": "upper_bound",
        "comparator": "greater_than",
        "market_shape": "binary_yes_no",
        "settlement_source": "Federal Reserve",
        "settlement_source_url": "https://example.com",
        "settlement_time": None,
        "payout_unit": "1.00_USD",
        "quote": {"bid": None, "ask": None, "bid_size": None, "ask_size": None, "timestamp": None, "complete": False},
        "fee_model_status": "documented",
        "source_registry_status": {
            "source_id": "kalshi",
            "implementation_status": ImplementationStatus.IMPLEMENTED_READ_ONLY.value,
            "can_create_candidate_pair": True,
        },
        "memo_evidence_status": "n/a",
        "source_files": [],
    }
    right_direct_kalshi = {
        "venue": "kalshi",
        "source_platform": "kalshi",
        "access_platform": "kalshi",
        "exchange_venue": "KALSHI",
        "executable_venue": "KALSHI",
        "market_id_or_conid": "direct_kalshi_market",
        "ticker_or_symbol": "KXFED-26JUN-T4.25",
        "event_family": "FED_FOMC",
        "settlement_event_date": "2026-06-17",
        "threshold": 4.25,
        "threshold_semantics": "upper_bound",
        "comparator": "greater_than",
        "market_shape": "binary_yes_no",
        "settlement_source": "Federal Reserve",
        "settlement_source_url": "https://example.com",
        "settlement_time": None,
        "payout_unit": "1.00_USD",
        "quote": {"bid": None, "ask": None, "bid_size": None, "ask_size": None, "timestamp": None, "complete": False},
        "fee_model_status": "documented",
        "source_registry_status": {
            "source_id": "kalshi",
            "implementation_status": ImplementationStatus.IMPLEMENTED_READ_ONLY.value,
            "can_create_candidate_pair": True,
        },
        "memo_evidence_status": "n/a",
        "source_files": [],
    }
    row = _compose_row(
        lane=LANE_IBKR_FF_VS_KALSHI_FED,
        left=left_ibkr_kalshi,
        right=right_direct_kalshi,
        comparison_extras={},
        lane_specific_blockers=[],
        inputs={"input_dir": Path(".")},
        row_id="test_ibkr_kalshi_route",
    )
    assert B_IBKR_KALSHI_SAME in row["blockers"]
    assert B_BROKER_ROUTE_NOT_INDEPENDENT in row["blockers"]
    assert B_DO_NOT_CROSS_COMPARE in row["blockers"]
    assert row["allowed_next_action"] == ACTION_IGNORE_BLOCKED


def test_polymarket_deadline_vs_kalshi_point_in_time_emits_basis_or_source_review(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    # Add a Polymarket Fed-ish row to the normalized markets payload.
    normalized = json.loads((input_dir / "normalized_markets_v0.json").read_text(encoding="utf-8"))
    normalized["normalized_markets"].append(
        {
            "venue": "polymarket",
            "market_id": "poly_fed_market",
            "token_id": "poly_token",
            "title": "Will the Fed raise rates at the June FOMC meeting?",
            "event_slug": "fed-june",
            "settlement": {"close_time": "2026-06-17T20:00:00Z", "settlement_source_url": None},
            "quote_depth": {
                "best_yes_bid_price": 0.40,
                "best_yes_ask_price": 0.45,
                "best_yes_bid_size": 200.0,
                "best_yes_ask_size": 200.0,
                "captured_at": "2026-05-26T19:00:00+00:00",
            },
        }
    )
    _write_input(input_dir, "normalized_markets_v0.json", normalized)

    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir)
    poly_rows = [r for r in report["rows"] if r["lane"] == LANE_POLYMARKET_FED_VS_KALSHI_FED]
    assert poly_rows, "expected at least one Polymarket-vs-Kalshi Fed row"
    row = poly_rows[0]
    # Polymarket Fed has no threshold typed, comparator unknown — should be MANUAL_REVIEW or SOURCE_REVIEW, never PAPER.
    assert row["allowed_next_action"] in {ACTION_MANUAL_REVIEW, ACTION_SOURCE_REVIEW, ACTION_WATCH}
    assert row["can_create_paper_candidate"] is False
    assert row["paper_candidate"] is False
    assert B_THRESHOLD_MISSING in row["blockers"]


def test_enriched_polymarket_quote_removes_missing_clob_book_blocker(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    normalized = json.loads((input_dir / "normalized_markets_v0.json").read_text(encoding="utf-8"))
    normalized["normalized_markets"].append(_kalshi_crypto_row())
    _write_input(input_dir, "normalized_markets_v0.json", normalized)
    enriched_path = _write_input(input_dir, "polymarket_taxonomy_shape_scout_enriched.json", _polymarket_enriched_payload())

    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir, polymarket_enriched_json=enriched_path)
    row = next(r for r in report["rows"] if r["lane"] == LANE_POLYMARKET_CRYPTO_VS_KALSHI_CRYPTO)

    assert B_POLYMARKET_MISSING_CLOB_BOOK not in row["blockers"]
    assert B_POLYMARKET_STALE_OR_MISSING_QUOTE not in row["blockers"]
    assert row["left"]["quote"]["complete"] is True
    assert row["left"]["quote"]["bid"] == 0.42
    assert row["left"]["quote"]["ask"] == 0.47


def test_enriched_polymarket_row_still_has_title_only_match_blocker(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    normalized = json.loads((input_dir / "normalized_markets_v0.json").read_text(encoding="utf-8"))
    normalized["normalized_markets"].append(_kalshi_crypto_row())
    _write_input(input_dir, "normalized_markets_v0.json", normalized)
    enriched_path = _write_input(input_dir, "polymarket_taxonomy_shape_scout_enriched.json", _polymarket_enriched_payload())

    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir, polymarket_enriched_json=enriched_path)
    row = next(r for r in report["rows"] if r["lane"] == LANE_POLYMARKET_CRYPTO_VS_KALSHI_CRYPTO)

    assert B_POLYMARKET_TITLE_ONLY in row["blockers"]
    assert B_POLYMARKET_REGISTRY_BLOCKS in row["blockers"]
    assert row["can_create_candidate_pair"] is False


def test_enriched_point_in_time_polymarket_can_be_source_review_not_exact_ready(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    normalized = json.loads((input_dir / "normalized_markets_v0.json").read_text(encoding="utf-8"))
    normalized["normalized_markets"].append(_kalshi_crypto_row())
    _write_input(input_dir, "normalized_markets_v0.json", normalized)
    enriched_path = _write_input(input_dir, "polymarket_taxonomy_shape_scout_enriched.json", _polymarket_enriched_payload())

    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir, polymarket_enriched_json=enriched_path)
    row = next(r for r in report["rows"] if r["lane"] == LANE_POLYMARKET_CRYPTO_VS_KALSHI_CRYPTO)

    assert row["allowed_next_action"] == ACTION_SOURCE_REVIEW
    assert row["exact_ready"] is False
    assert report["summary"]["exact_ready_rows"] == 0
    assert report["summary"]["paper_candidate_rows"] == 0


def test_enriched_hit_by_deadline_polymarket_remains_basis_risk_not_source_review(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    normalized = json.loads((input_dir / "normalized_markets_v0.json").read_text(encoding="utf-8"))
    normalized["normalized_markets"].append(
        _kalshi_crypto_row(asset="BTC", threshold=150000.0, date="2026-06-30")
    )
    _write_input(input_dir, "normalized_markets_v0.json", normalized)
    enriched_path = _write_input(
        input_dir,
        "polymarket_taxonomy_shape_scout_enriched.json",
        _polymarket_enriched_payload(
            shape="point_in_time_threshold",
            asset="BTC",
            threshold=150000.0,
            date_text="June 30, 2026",
            title="When will Bitcoin hit $150k?",
            question="Will Bitcoin hit $150k by June 30, 2026?",
        ),
    )

    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir, polymarket_enriched_json=enriched_path)
    row = next(r for r in report["rows"] if r["lane"] == LANE_POLYMARKET_CRYPTO_VS_KALSHI_CRYPTO)

    assert row["left"]["market_shape"] == "crypto_deadline_range_hit"
    assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW
    assert row["allowed_next_action"] != ACTION_SOURCE_REVIEW
    assert B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME in row["blockers"]
    assert B_SETTLEMENT_WINDOW_MISMATCH in row["blockers"]
    assert B_EXACT_PAYOFF_NOT_PROVEN in row["blockers"]
    assert B_RANGE_VS_CLOSE in row["blockers"]
    assert B_POINT_VS_DEADLINE in row["blockers"]
    assert row["left"]["quote"]["complete"] is True
    assert row["exact_ready"] is False
    assert report["summary"]["exact_ready_rows"] == 0
    assert report["summary"]["paper_candidate_rows"] == 0


def test_enriched_reach_before_deadline_polymarket_remains_basis_risk(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    normalized = json.loads((input_dir / "normalized_markets_v0.json").read_text(encoding="utf-8"))
    normalized["normalized_markets"].append(
        _kalshi_crypto_row(asset="BTC", threshold=150000.0, date="2026-12-31")
    )
    _write_input(input_dir, "normalized_markets_v0.json", normalized)
    enriched_path = _write_input(
        input_dir,
        "polymarket_taxonomy_shape_scout_enriched.json",
        _polymarket_enriched_payload(
            shape="point_in_time_threshold",
            asset="BTC",
            threshold=150000.0,
            date_text="December 31, 2026",
            question="Will Bitcoin reach $150k before Dec 31, 2026?",
        ),
    )

    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir, polymarket_enriched_json=enriched_path)
    row = next(r for r in report["rows"] if r["lane"] == LANE_POLYMARKET_CRYPTO_VS_KALSHI_CRYPTO)

    assert row["left"]["market_shape"] == "crypto_deadline_range_hit"
    assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW
    assert B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME in row["blockers"]
    assert row["can_create_candidate_pair"] is False
    assert row["paper_candidate"] is False


@pytest.mark.parametrize(
    ("shape", "expected_blocker"),
    [
        ("deadline_threshold_touch", B_POINT_VS_DEADLINE),
        ("range_hit", B_RANGE_VS_CLOSE),
    ],
)
def test_deadline_or_range_hit_enriched_polymarket_remains_basis_risk_review(
    tmp_path: Path, shape: str, expected_blocker: str
) -> None:
    input_dir = _setup_inputs(tmp_path)
    normalized = json.loads((input_dir / "normalized_markets_v0.json").read_text(encoding="utf-8"))
    normalized["normalized_markets"].append(_kalshi_crypto_row())
    _write_input(input_dir, "normalized_markets_v0.json", normalized)
    enriched_path = _write_input(input_dir, "polymarket_taxonomy_shape_scout_enriched.json", _polymarket_enriched_payload(shape=shape))

    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir, polymarket_enriched_json=enriched_path)
    row = next(r for r in report["rows"] if r["lane"] == LANE_POLYMARKET_CRYPTO_VS_KALSHI_CRYPTO)

    assert expected_blocker in row["blockers"]
    assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW
    assert row["exact_ready"] is False


def test_enriched_polymarket_vs_cdna_stays_basis_risk_without_source_proof(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    _write_input(input_dir, "crypto_com_predict_cdna_research_snapshot.json", _cdna_point_in_time_payload())
    enriched_path = _write_input(input_dir, "polymarket_taxonomy_shape_scout_enriched.json", _polymarket_enriched_payload())

    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir, polymarket_enriched_json=enriched_path)
    row = next(r for r in report["rows"] if r["lane"] == LANE_POLYMARKET_CRYPTO_VS_CDNA_CRYPTO)

    assert B_CDNA_SETTLEMENT_BASIS_RISK in row["blockers"]
    assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW
    assert row["paper_candidate"] is False


def test_enriched_polymarket_summary_counts_and_no_paper_candidate(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    normalized = json.loads((input_dir / "normalized_markets_v0.json").read_text(encoding="utf-8"))
    normalized["normalized_markets"].append(_kalshi_crypto_row())
    _write_input(input_dir, "normalized_markets_v0.json", normalized)
    enriched_path = _write_input(input_dir, "polymarket_taxonomy_shape_scout_enriched.json", _polymarket_enriched_payload())

    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir, polymarket_enriched_json=enriched_path)
    summary = report["summary"]

    assert summary["polymarket_enriched_rows_loaded"] == 1
    assert summary["polymarket_rows_with_bid_ask_size"] == 1
    assert summary["polymarket_rows_with_timestamp"] == 1
    assert summary["polymarket_overlap_rows"] >= 1
    assert summary["exact_ready_rows"] == 0
    assert summary["paper_candidate_rows"] == 0
    assert all(r["paper_candidate"] is False for r in report["rows"])


def test_cdna_range_hit_vs_kalshi_point_in_time_emits_basis_risk_review(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir)
    cdna_rows = [r for r in report["rows"] if r["lane"] == LANE_CDNA_BTC_VS_KALSHI_BTC]
    assert cdna_rows
    row = cdna_rows[0]
    assert B_RANGE_VS_CLOSE in row["blockers"]
    assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW
    assert row["can_create_candidate_pair"] is False
    assert row["paper_candidate"] is False


def test_odds_api_row_is_reference_only_and_not_executable(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path, include_odds=True)
    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir)
    odds_rows = [r for r in report["rows"] if r["lane"] == LANE_ODDS_API_REFERENCE]
    assert odds_rows
    row = odds_rows[0]
    assert B_REFERENCE_ONLY in row["blockers"]
    assert row["allowed_next_action"] == ACTION_WATCH
    assert row["can_create_candidate_pair"] is False
    assert row["can_create_paper_candidate"] is False
    assert row["exact_ready"] is False


def test_missing_threshold_blocks_pair_creation(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    # Mutate Kalshi rows to remove threshold (rewrite tickers).
    normalized = json.loads((input_dir / "normalized_markets_v0.json").read_text(encoding="utf-8"))
    for row in normalized["normalized_markets"]:
        row["ticker"] = "KXFED-26JUN-NO_STRIKE_HERE"
    _write_input(input_dir, "normalized_markets_v0.json", normalized)
    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir)
    row = next(r for r in report["rows"] if r["lane"] == LANE_IBKR_FF_VS_KALSHI_FED)
    assert B_THRESHOLD_MISSING in row["blockers"]


def test_comparator_mismatch_blocks_pair(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    # Mutate Kalshi titles to say "at or above" (different comparator)
    normalized = json.loads((input_dir / "normalized_markets_v0.json").read_text(encoding="utf-8"))
    for row in normalized["normalized_markets"]:
        row["title"] = row["title"].replace(" above ", " at or above ")
    _write_input(input_dir, "normalized_markets_v0.json", normalized)
    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir)
    row = next(r for r in report["rows"] if r["lane"] == LANE_IBKR_FF_VS_KALSHI_FED)
    assert B_COMPARATOR_MISMATCH in row["blockers"]


def test_source_registry_planned_not_implemented_marks_blocker(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir)
    ibkr_row = next(r for r in report["rows"] if r["lane"] == LANE_IBKR_FF_VS_KALSHI_FED)
    assert B_REGISTRY_BLOCKS in ibkr_row["blockers"]
    assert B_IBKR_PLANNED in ibkr_row["blockers"]
    # Confirm registry itself is still planned not implemented.
    assert SOURCE_REGISTRY["forecastex_ibkr"].implementation_status == ImplementationStatus.PLANNED_NOT_IMPLEMENTED


def test_no_paper_candidate_strings_or_flags_in_outputs(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path, include_odds=True)
    json_output = tmp_path / "scout.json"
    markdown_output = tmp_path / "scout.md"
    write_cross_venue_opportunity_scout_files(
        input_dir=input_dir,
        json_output=json_output,
        markdown_output=markdown_output,
    )
    json_text = json_output.read_text(encoding="utf-8")
    md_text = markdown_output.read_text(encoding="utf-8")
    payload = json.loads(json_text)
    # No literal PAPER_CANDIDATE flag substring should appear in either output.
    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in json_text
    assert forbidden not in md_text
    summary = payload["summary"]
    assert summary["exact_ready_rows"] == 0
    assert summary["paper_candidate_rows"] == 0
    assert summary["execution_ready_rows"] == 0
    # Every row must have hard-locked diagnostic flags.
    for row in payload["rows"]:
        assert row["diagnostic_only"] is True
        assert row["can_create_candidate_pair"] is False
        assert row["can_create_paper_candidate"] is False
        assert row["exact_ready"] is False
        assert row["execution_ready"] is False
        assert row["paper_candidate"] is False
        assert row["affects_evaluator_gates"] is False


def test_exact_ready_and_paper_candidate_rows_remain_zero(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path, include_odds=True)
    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir)
    summary = report["summary"]
    assert summary["exact_ready_rows"] == 0
    assert summary["paper_candidate_rows"] == 0
    assert summary["execution_ready_rows"] == 0


def test_active_platform_filter_marks_ibkr_queued_and_ranks_core_trio(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    normalized = json.loads((input_dir / "normalized_markets_v0.json").read_text(encoding="utf-8"))
    normalized["normalized_markets"].append(_kalshi_crypto_row())
    _write_input(input_dir, "normalized_markets_v0.json", normalized)
    _write_input(input_dir, "crypto_com_predict_cdna_research_snapshot.json", _cdna_point_in_time_payload())

    report = build_cross_venue_opportunity_scout_report(
        input_dir=input_dir,
        active_platforms="kalshi,polymarket,cdna",
    )
    summary = report["summary"]
    ibkr_row = next(r for r in report["rows"] if r["lane"] == LANE_IBKR_FF_VS_KALSHI_FED)
    assert ibkr_row["active_platform_status"] == "queued_inactive"
    assert "ibkr" in ibkr_row["excluded_inactive_platforms"]
    assert summary["active_platforms"] == ["cdna", "kalshi", "polymarket"]
    assert summary["top_lane"] != LANE_IBKR_FF_VS_KALSHI_FED
    assert summary["all_platform_top_lane"] == LANE_IBKR_FF_VS_KALSHI_FED
    assert summary["core_trio_top_lane_summary"]
    core_lanes = {row["lane"]: row for row in summary["core_trio_top_lane_summary"]}
    assert core_lanes[LANE_CDNA_BTC_VS_KALSHI_BTC]["active_ranked_rows"] >= 1
    assert summary["core_trio_top_lane"] in {
        LANE_POLYMARKET_CRYPTO_VS_KALSHI_CRYPTO,
        LANE_CDNA_BTC_VS_KALSHI_BTC,
        LANE_POLYMARKET_CRYPTO_VS_CDNA_CRYPTO,
    }
    assert summary["exact_ready_rows"] == 0
    assert summary["paper_candidate_rows"] == 0


def test_cli_scout_writes_outputs(tmp_path: Path, capsys) -> None:
    input_dir = _setup_inputs(tmp_path)
    enriched_path = _write_input(input_dir, "polymarket_taxonomy_shape_scout_enriched.json", _polymarket_enriched_payload())
    json_output = tmp_path / "out.json"
    md_output = tmp_path / "out.md"
    result = scan.main(
        [
            "cross-venue-opportunity-scout",
            "--input-dir",
            str(input_dir),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(md_output),
            "--polymarket-enriched-json",
            str(enriched_path),
        ]
    )
    assert result == 0
    stdout = capsys.readouterr().out
    assert "cross_venue_opportunity_scout=OK" in stdout
    assert "diagnostic_only=true" in stdout
    assert "exact_ready_rows=0" in stdout
    assert "paper_candidate_rows=0" in stdout
    assert "polymarket_enriched_rows_loaded=1" in stdout
    # JSON sanity
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "cross_venue_opportunity_scout_v1"
    assert payload["safety"]["source_registry_unchanged"] is True


def test_sx_bet_lane_emits_advisory_when_market_count_zero(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    sx_dir = input_dir / "manual_snapshots" / "sx_bet" / "20260526_x"
    sx_dir.mkdir(parents=True, exist_ok=True)
    (sx_dir / "sx_bet_research_snapshot.json").write_text(
        json.dumps(
            {
                "market_count": 0,
                "order_count": 0,
                "captured_at": "2026-05-26T05:23:38Z",
                "is_executable": False,
            }
        ),
        encoding="utf-8",
    )
    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir)
    sx_rows = [r for r in report["rows"] if r["lane"] == LANE_SX_BET]
    assert sx_rows
    assert sx_rows[0]["can_create_candidate_pair"] is False
    assert sx_rows[0]["paper_candidate"] is False


def test_top_10_review_targets_are_sorted_by_score(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    report = build_cross_venue_opportunity_scout_report(input_dir=input_dir)
    top10 = report["summary"]["top_10_review_targets"]
    scores = [t.get("review_priority_score") or 0 for t in top10]
    assert scores == sorted(scores, reverse=True)


def test_ops_status_surfaces_scout(tmp_path: Path) -> None:
    input_dir = _setup_inputs(tmp_path)
    normalized = json.loads((input_dir / "normalized_markets_v0.json").read_text(encoding="utf-8"))
    normalized["normalized_markets"].append(_kalshi_crypto_row())
    _write_input(input_dir, "normalized_markets_v0.json", normalized)
    _write_input(input_dir, "polymarket_taxonomy_shape_scout_enriched.json", _polymarket_enriched_payload())
    # Generate scout.
    scout_json = input_dir / "cross_venue_opportunity_scout.json"
    write_cross_venue_opportunity_scout_files(
        input_dir=input_dir,
        json_output=scout_json,
        markdown_output=input_dir / "cross_venue_opportunity_scout.md",
    )
    from relative_value.relative_value_ops_status import build_relative_value_ops_status_report

    ops_report = build_relative_value_ops_status_report(input_dir=input_dir)
    scout_summary = (ops_report["summary"] or {}).get("cross_venue_opportunity_scout") or {}
    assert scout_summary.get("present") is True
    assert scout_summary.get("exact_ready_rows") == 0
    assert scout_summary.get("paper_candidate_rows") == 0
    assert scout_summary.get("scout_row_count", 0) > 0
    assert scout_summary.get("polymarket_enriched_rows_loaded") == 1
    assert scout_summary.get("polymarket_rows_with_bid_ask_size") == 1
    assert scout_summary.get("top_enriched_polymarket_review_targets")
