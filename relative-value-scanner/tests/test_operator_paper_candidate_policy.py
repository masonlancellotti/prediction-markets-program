"""Behavioral tests for the unified PAPER_CANDIDATE / WATCH / IGNORE_BLOCKED policy.

These cover the ten test scenarios called out in the operator-risk-mode rollout:
1.  Crypto basis row with operator-risk-mode=aggressive can become a paper
    candidate of class OPERATOR_ACCEPTED_RISK.
2.  The same row without operator acceptance (conservative mode) stays WATCH.
3.  A CDNA fill-first row can become a paper candidate of class CDNA_FILL_FIRST.
4.  CDNA rows never claim strict_exact_arb pre-fill.
5.  A strict exact fixture still produces class STRICT_EXACT via the existing
    strict evaluator.
6.  Stale quotes cannot become paper candidates regardless of mode.
7.  Missing ask quotes cannot become paper candidates.
8.  Title-similarity-only rows from the strict evaluator never reach
    PAPER_CANDIDATE.
9.  No scout uses midpoint pricing.
10. The existing strict evaluator behavior is intact and unchanged.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from relative_value.crypto_threshold_basis_review_scout import (
    build_crypto_threshold_basis_review_scout_report,
)
from relative_value.paper_candidate_evaluator import (
    ACTION_PAPER_CANDIDATE,
    PaperCandidateEvaluatorConfig,
    evaluate_paper_candidates,
)
from relative_value.three_venue_operator_scout import build_three_venue_operator_scout_report


NOW = datetime(2026, 5, 29, 9, 25, tzinfo=timezone.utc)


# -------------------------- Helpers ---------------------------------------- #


def _write_crypto_evidence(tmp_path: Path) -> tuple[Path, Path]:
    kalshi = tmp_path / "kalshi.json"
    poly = tmp_path / "poly.json"
    kalshi.write_text(
        json.dumps(_crypto_kalshi_payload(yes_ask="0.40", yes_ask_size="100", no_ask="0.60", no_ask_size="100")),
        encoding="utf-8",
    )
    poly.write_text(
        json.dumps(_crypto_poly_payload(no_ask="0.55", no_ask_size="100", yes_ask="0.45", yes_ask_size="100")),
        encoding="utf-8",
    )
    return kalshi, poly


def _crypto_kalshi_payload(*, yes_ask: str, yes_ask_size: str, no_ask: str, no_ask_size: str) -> dict:
    return {
        "schema_kind": "polished_crypto_market_family_evidence_v1",
        "diagnostic_only": True,
        "platform": "Kalshi",
        "category": "crypto",
        "market_family": "btc_price_threshold",
        "asset": "BTC",
        "market_shape": "point_in_time_threshold",
        "comparator": "above",
        "target_date": "2026-05-29",
        "target_time": "12:00 ET",
        "timezone": "ET",
        "price_source": "CF Benchmarks Bitcoin Real-Time Index (BRTI)",
        "settlement_source": "CF Benchmarks BRTI",
        "outcomes": [
            {
                "market_title": "Bitcoin price on May 29, 2026?",
                "market_ticker": "KXBTCD-26MAY2912-T69999.99",
                "outcome_name": "$70,000 or above",
                "yes_ask": yes_ask,
                "yes_ask_size": yes_ask_size,
                "no_ask": no_ask,
                "no_ask_size": no_ask_size,
                "strike_floor": 69999.99,
                "depth_status": "full_clob",
                "quote_timestamp": "2026-05-29T09:20:00Z",
            }
        ],
    }


def _crypto_poly_payload(*, no_ask: str, no_ask_size: str, yes_ask: str, yes_ask_size: str) -> dict:
    return {
        "schema_kind": "polished_crypto_market_family_evidence_v1",
        "diagnostic_only": True,
        "platform": "Polymarket",
        "category": "crypto",
        "market_family": "btc_price_threshold",
        "asset": "BTC",
        "market_shape": "point_in_time_threshold",
        "comparator": "above",
        "target_date": "2026-05-29",
        "target_time": "12:00 ET",
        "timezone": "ET",
        "price_source": "Binance",
        "settlement_source": "Binance BTC/USDT Close",
        "rules_text": "This resolves using Binance BTC/USDT 12:00 in the ET timezone (noon).",
        "outcomes": [
            {
                "market_title": "Will the price of Bitcoin be above $70,000 on May 29?",
                "market_ticker": "bitcoin-above-70000-on-may-29-2026",
                "platform_market_id": "2361673",
                "condition_id": "0xabc",
                "token_id_yes": "yes",
                "token_id_no": "no",
                "yes_ask": yes_ask,
                "yes_ask_size": yes_ask_size,
                "no_ask": no_ask,
                "no_ask_size": no_ask_size,
                "depth_status": "full_clob",
                "quote_timestamp": "2026-05-29T09:20:00Z",
            }
        ],
    }


def _write_three_venue_family(tmp_path: Path, *, timestamp: str = "2026-05-29T20:12:00Z") -> Path:
    folder = tmp_path / "family"
    folder.mkdir()

    base = lambda platform: {  # noqa: E731
        "schema_kind": "test_three_venue_evidence_v1",
        "diagnostic_only": True,
        "platform": platform,
        "category": "sports",
        "market_family": "NBA Champion 2026",
        "market_found": True,
        "quotes": {"quote_timestamp_utc": timestamp},
    }

    kalshi = base("kalshi")
    kalshi["outcomes"] = [
        {
            "team": "Oklahoma City Thunder",
            "yes_ask": 0.2,
            "yes_ask_size": 100,
            "no_ask": 0.7,
            "no_ask_size": 100,
            "depth_status": "full_clob",
            "quote_timestamp": timestamp,
        }
    ]
    poly = base("polymarket")
    poly["outcomes"] = [
        {
            "team": "Oklahoma City Thunder",
            "yes_ask": 0.25,
            "yes_ask_size": 100,
            "no_ask": 0.6,
            "no_ask_size": 100,
            "depth_status": "full_clob",
            "quote_timestamp": timestamp,
        }
    ]
    cdna = base("cdna")
    cdna["outcomes"] = [
        {
            "team": "Oklahoma City Thunder",
            "yes": 0.1,
            "no": 0.72,
            "status": "active",
            "symbol": "OKC",
            "quote_timestamp": timestamp,
            "depth_status": "display_price_only",
        }
    ]
    (folder / "kalshi_raw_evidence.json").write_text(json.dumps(kalshi), encoding="utf-8")
    (folder / "polymarket_raw_evidence.json").write_text(json.dumps(poly), encoding="utf-8")
    (folder / "cdna_raw_evidence.json").write_text(json.dumps(cdna), encoding="utf-8")
    return folder


# -------------------------- Crypto basis tests ----------------------------- #


def test_crypto_basis_row_becomes_operator_paper_candidate_in_aggressive_mode(tmp_path: Path) -> None:
    kalshi, poly = _write_crypto_evidence(tmp_path)

    report = build_crypto_threshold_basis_review_scout_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=poly,
        asset="BTC",
        generated_at=NOW,
        operator_risk_mode="aggressive",
    )

    paper = [row for row in report["rows"] if row.get("paper_candidate")]
    assert paper, "expected at least one paper candidate in aggressive mode"
    row = paper[0]
    assert row["action"] == "PAPER_CANDIDATE"
    assert row["paper_candidate_class"] == "OPERATOR_ACCEPTED_RISK"
    assert row["strict_exact_arb"] is False
    assert row["mathematical_strict_exact_arb"] is False
    assert row["operator_assumptions_accepted"] is True
    assert "crypto_source_index_basis_risk" in row["assumptions_accepted"]
    assert "target_time_basis_risk" in row["assumptions_accepted"]
    assert report["summary_counts"]["operator_paper_candidate_rows"] >= 1
    assert report["summary_counts"]["total_paper_candidate_rows"] >= 1


def test_crypto_basis_row_without_operator_acceptance_stays_watch(tmp_path: Path) -> None:
    kalshi, poly = _write_crypto_evidence(tmp_path)

    report = build_crypto_threshold_basis_review_scout_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=poly,
        asset="BTC",
        generated_at=NOW,
        operator_risk_mode="conservative",
    )

    assert all(row.get("paper_candidate") is False for row in report["rows"])
    direction_rows = [row for row in report["rows"] if row["direction"] != "UNMATCHED"]
    assert direction_rows
    assert all(row["action"] == "WATCH" for row in direction_rows)
    assert report["summary_counts"]["total_paper_candidate_rows"] == 0


# -------------------------- CDNA fill-first tests -------------------------- #


def test_cdna_fill_first_row_becomes_paper_candidate_in_aggressive_mode(tmp_path: Path) -> None:
    folder = _write_three_venue_family(tmp_path)

    report = build_three_venue_operator_scout_report(
        family_folders=[folder],
        include_cdna=True,
        operator_accept_cdna_display_price_risk=True,
        cdna_operator_size_cap=1,
        max_quote_age_seconds=900,
        min_available_notional=10,
        operator_risk_mode="aggressive",
        generated_at=datetime(2026, 5, 29, 20, 15, tzinfo=timezone.utc),
    )

    cdna_paper = [
        row
        for row in report["rows"]
        if row["basket_type"].startswith("cdna_") and row.get("paper_candidate")
    ]
    assert cdna_paper, "expected at least one CDNA fill-first paper candidate"
    row = cdna_paper[0]
    assert row["action"] == "PAPER_CANDIDATE"
    assert row["paper_candidate_class"] == "CDNA_FILL_FIRST"
    assert row["candidate_action"] == "FILL_CDNA_FIRST_THEN_HEDGE_EXACT_FILLED_QUANTITY"
    assert "cdna_display_price_assumed_fillable_at_operator_cap" in row["assumptions_accepted"]
    assert "cdna_executable_size_unverified_pre_fill" in row["assumptions_accepted"]
    assert report["summary_counts"]["cdna_fill_first_paper_candidate_rows"] >= 1


def test_cdna_row_never_claims_strict_exact_arb_pre_fill(tmp_path: Path) -> None:
    folder = _write_three_venue_family(tmp_path)

    report = build_three_venue_operator_scout_report(
        family_folders=[folder],
        include_cdna=True,
        operator_accept_cdna_display_price_risk=True,
        cdna_operator_size_cap=1,
        max_quote_age_seconds=900,
        min_available_notional=10,
        operator_risk_mode="aggressive",
        generated_at=datetime(2026, 5, 29, 20, 15, tzinfo=timezone.utc),
    )

    cdna_rows = [row for row in report["rows"] if row["basket_type"].startswith("cdna_")]
    assert cdna_rows
    for row in cdna_rows:
        assert row["strict_exact_arb"] is False
        assert row["mathematical_strict_exact_arb"] is False
        assert row.get("paper_candidate_class") in {"CDNA_FILL_FIRST", "NONE"}


# -------------------------- Strict evaluator preservation ------------------ #


def test_strict_exact_fixture_keeps_class_strict_exact() -> None:
    from relative_value.contract_relationship import RELATIONSHIP_EQUIVALENT
    from relative_value.same_payoff_evidence import (
        SAME_PAYOFF_BOARD_CLASSIFIER_VERSION,
        SAME_PAYOFF_BOARD_SOURCE,
    )

    trusted_same_payoff_relationship = {
        "relationship": RELATIONSHIP_EQUIVALENT,
        "same_payoff": True,
        "confidence": 0.95,
        "blocking_reasons": [],
        "manual_review_required": False,
        "source": SAME_PAYOFF_BOARD_SOURCE,
        "same_payoff_board_evidence": {
            "classifier_version": SAME_PAYOFF_BOARD_CLASSIFIER_VERSION,
            "strict_pass_count": 11,
            "strict_comparator_count": 11,
            "board_generated_at": "2026-05-20T11:59:00+00:00",
            "board_row_id": "poly-1__KXKNICKS",
            "evidence_hash": "abc123",
        },
    }
    pair_payload = {
        "schema_version": 1,
        "source": "live_snapshot_matcher",
        "generated_at": "2026-05-20T11:59:00+00:00",
        "pair_count": 1,
        "pairs": [
            {
                "action": "PAPER_CANDIDATE",
                "polymarket": {
                    "market_id": "poly-1",
                    "question": "Will the New York Knicks win?",
                    "event_title": "Knicks vs Cavaliers",
                },
                "kalshi": {
                    "ticker": "KXKNICKS",
                    "question": "Will the New York Knicks win?",
                    "event_title": "Knicks vs Cavaliers",
                },
                "similarity_score": 0.98,
                "matched_fields": {},
                "ineligibility_reasons": [],
                "contract_relationship": trusted_same_payoff_relationship,
            }
        ],
    }
    polymarket_payload = {
        "schema_version": 1,
        "source": "polymarket_gamma",
        "captured_at": "2026-05-20T11:55:00+00:00",
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [
            {
                "venue": "polymarket",
                "market_id": "poly-1",
                "question": "Will the New York Knicks win?",
                "event_title": "Knicks vs Cavaliers",
                "end_date": "2026-05-20T13:00:00+00:00",
                "outcomes": [{"name": "Yes", "outcome_yes_token_price": 0.55}],
                "raw": {"clobTokenIds": '["yes-token", "no-token"]'},
                "orderbook_enrichment": {
                    "orderbook_captured_at": "2026-05-20T11:59:30+00:00",
                    "best_bid": 0.54,
                    "best_ask": 0.55,
                    "spread": 0.01,
                    "depth_at_best_bid": 50.0,
                    "depth_at_best_ask": 50.0,
                    "enrichment_status": "enriched",
                    "enrichment_warnings": [],
                },
            }
        ],
    }
    kalshi_payload = {
        "schema_version": 1,
        "source": "kalshi_markets",
        "captured_at": "2026-05-20T11:55:00+00:00",
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [
            {
                "venue": "kalshi",
                "market_id": "kalshi-market-1",
                "ticker": "KXKNICKS",
                "question": "Will the New York Knicks win?",
                "event_title": "Knicks vs Cavaliers",
                "close_time": "2026-05-20T13:00:00+00:00",
                "outcomes": [{"name": "Yes", "outcome_yes_token_price": 0.44}],
                "orderbook_enrichment": {
                    "orderbook_captured_at": "2026-05-20T11:59:35+00:00",
                    "best_bid": 0.43,
                    "best_ask": 0.44,
                    "spread": 0.01,
                    "depth_at_best_bid": 50.0,
                    "depth_at_best_ask": 50.0,
                    "enrichment_status": "enriched",
                    "enrichment_warnings": [],
                },
            }
        ],
    }

    report = evaluate_paper_candidates(
        pairs_payload=pair_payload,
        polymarket_payload=polymarket_payload,
        kalshi_payload=kalshi_payload,
        detected_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        config=PaperCandidateEvaluatorConfig(accept_unit_mismatch=True),
    )

    paper = [row for row in report["ledger"] if row.get("action") == ACTION_PAPER_CANDIDATE]
    assert paper, f"expected one strict paper candidate, got ledger={report['ledger']}"
    row = paper[0]
    assert row["paper_candidate_class"] == "STRICT_EXACT"
    assert row["strict_exact_arb"] is True
    assert row["mathematical_strict_exact_arb"] is True


# -------------------------- Hard-gate exclusions --------------------------- #


def test_stale_quote_cannot_become_paper_candidate(tmp_path: Path) -> None:
    kalshi = tmp_path / "kalshi.json"
    poly = tmp_path / "poly.json"
    kalshi_payload = _crypto_kalshi_payload(yes_ask="0.40", yes_ask_size="100", no_ask="0.60", no_ask_size="100")
    poly_payload = _crypto_poly_payload(no_ask="0.55", no_ask_size="100", yes_ask="0.45", yes_ask_size="100")
    kalshi_payload["outcomes"][0]["quote_timestamp"] = "2026-05-28T00:00:00Z"
    poly_payload["outcomes"][0]["quote_timestamp"] = "2026-05-28T00:00:00Z"
    kalshi.write_text(json.dumps(kalshi_payload), encoding="utf-8")
    poly.write_text(json.dumps(poly_payload), encoding="utf-8")

    report = build_crypto_threshold_basis_review_scout_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=poly,
        asset="BTC",
        generated_at=NOW,
        operator_risk_mode="aggressive",
        max_quote_age_seconds=60,
    )

    assert any("stale_or_missing_quote" in (row.get("blockers") or []) for row in report["rows"])
    assert all(row.get("paper_candidate") is False for row in report["rows"])


def test_missing_ask_cannot_become_paper_candidate(tmp_path: Path) -> None:
    kalshi = tmp_path / "kalshi.json"
    poly = tmp_path / "poly.json"
    kalshi_payload = _crypto_kalshi_payload(yes_ask="0.40", yes_ask_size="100", no_ask="0.60", no_ask_size="100")
    poly_payload = _crypto_poly_payload(no_ask="0.55", no_ask_size="100", yes_ask="0.45", yes_ask_size="100")
    poly_payload["outcomes"][0]["no_ask"] = None
    poly_payload["outcomes"][0]["yes_ask"] = None
    kalshi.write_text(json.dumps(kalshi_payload), encoding="utf-8")
    poly.write_text(json.dumps(poly_payload), encoding="utf-8")

    report = build_crypto_threshold_basis_review_scout_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=poly,
        asset="BTC",
        generated_at=NOW,
        operator_risk_mode="aggressive",
    )

    assert all(row.get("paper_candidate") is False for row in report["rows"])
    assert any("missing_quote" in (row.get("blockers") or []) for row in report["rows"])


def test_title_similarity_only_pair_never_reaches_paper_candidate() -> None:
    pair_payload = {
        "schema_version": 1,
        "source": "live_snapshot_matcher",
        "generated_at": "2026-05-20T11:59:00+00:00",
        "pair_count": 1,
        "pairs": [
            {
                "action": "MANUAL_REVIEW",
                "polymarket": {
                    "market_id": "poly-1",
                    "question": "Will the New York Knicks win?",
                    "event_title": "Knicks vs Cavaliers",
                },
                "kalshi": {
                    "ticker": "KXKNICKS",
                    "question": "Will the New York Knicks win?",
                    "event_title": "Knicks vs Cavaliers",
                },
                "similarity_score": 0.95,
                "matched_fields": {},
                "ineligibility_reasons": ["title_similarity_only"],
                "contract_relationship": {"relationship": "title_similarity_only"},
            }
        ],
    }
    polymarket_payload = {
        "schema_version": 1,
        "source": "polymarket_gamma",
        "captured_at": "2026-05-20T11:55:00+00:00",
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [
            {
                "venue": "polymarket",
                "market_id": "poly-1",
                "question": "Will the New York Knicks win?",
                "event_title": "Knicks vs Cavaliers",
                "end_date": "2026-05-20T13:00:00+00:00",
                "outcomes": [{"name": "Yes", "outcome_yes_token_price": 0.55}],
                "raw": {"clobTokenIds": '["yes-token", "no-token"]'},
                "orderbook_enrichment": {
                    "orderbook_captured_at": "2026-05-20T11:59:30+00:00",
                    "best_bid": 0.54,
                    "best_ask": 0.55,
                    "spread": 0.01,
                    "depth_at_best_bid": 50.0,
                    "depth_at_best_ask": 50.0,
                    "enrichment_status": "enriched",
                    "enrichment_warnings": [],
                },
            }
        ],
    }
    kalshi_payload = {
        "schema_version": 1,
        "source": "kalshi_markets",
        "captured_at": "2026-05-20T11:55:00+00:00",
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [
            {
                "venue": "kalshi",
                "market_id": "kalshi-market-1",
                "ticker": "KXKNICKS",
                "question": "Will the New York Knicks win?",
                "event_title": "Knicks vs Cavaliers",
                "close_time": "2026-05-20T13:00:00+00:00",
                "outcomes": [{"name": "Yes", "outcome_yes_token_price": 0.44}],
                "orderbook_enrichment": {
                    "orderbook_captured_at": "2026-05-20T11:59:35+00:00",
                    "best_bid": 0.43,
                    "best_ask": 0.44,
                    "spread": 0.01,
                    "depth_at_best_bid": 50.0,
                    "depth_at_best_ask": 50.0,
                    "enrichment_status": "enriched",
                    "enrichment_warnings": [],
                },
            }
        ],
    }

    report = evaluate_paper_candidates(
        pairs_payload=pair_payload,
        polymarket_payload=polymarket_payload,
        kalshi_payload=kalshi_payload,
        detected_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        config=PaperCandidateEvaluatorConfig(),
    )

    assert all(row.get("action") != ACTION_PAPER_CANDIDATE for row in report["ledger"])
    assert all(row.get("paper_candidate") is False for row in report["ledger"])


# -------------------------- Pricing-discipline ----------------------------- #


def test_three_venue_scout_does_not_use_midpoint(tmp_path: Path) -> None:
    folder = _write_three_venue_family(tmp_path)

    report = build_three_venue_operator_scout_report(
        family_folders=[folder],
        include_cdna=False,
        operator_accept_cdna_display_price_risk=False,
        cdna_operator_size_cap=1,
        max_quote_age_seconds=900,
        min_available_notional=10,
        operator_risk_mode="aggressive",
        generated_at=datetime(2026, 5, 29, 20, 15, tzinfo=timezone.utc),
    )

    kalshi_yes_row = next(row for row in report["rows"] if row["direction"] == "KALSHI_YES_POLYMARKET_NO")
    # Kalshi yes_ask was 0.2, Polymarket no_ask was 0.6 — both true asks, not a midpoint.
    assert kalshi_yes_row["leg_1"]["entry_price"] == 0.2
    assert kalshi_yes_row["leg_2"]["entry_price"] == 0.6
    assert kalshi_yes_row["entry_cost"] == 0.8
    assert kalshi_yes_row["gross_edge"] == 0.2


def test_strict_evaluator_action_value_is_still_paper_candidate() -> None:
    # The strict evaluator's user-facing action label is PAPER_CANDIDATE,
    # confirming the new unified vocabulary did not regress the strict path.
    from relative_value.paper_candidate_evaluator import (
        ACTION_MANUAL_REVIEW,
        ACTION_PAPER_CANDIDATE,
        ACTION_WATCH,
    )

    assert ACTION_PAPER_CANDIDATE == "PAPER_CANDIDATE"
    assert ACTION_WATCH == "WATCH"
    assert ACTION_MANUAL_REVIEW == "MANUAL_REVIEW"


# Sports-MLB daily/world-series operator-acceptance behavior is covered by the
# scout-specific test files (see tests/test_sports_mlb_daily_residual_risk_scout.py
# and tests/test_sports_mlb_world_series_residual_risk_scout.py).
