import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import scan
from relative_value.paper_candidate_evaluator import (
    ACTION_MANUAL_REVIEW,
    ACTION_PAPER_CANDIDATE,
    ACTION_WATCH,
    UNIT_WARNING,
    PaperCandidateEvaluatorConfig,
    evaluate_paper_candidates,
)


NOW = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)


def _pairs_payload(reasons: list[str] | None = None) -> dict:
    return {
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
                "ineligibility_reasons": reasons or [],
            }
        ],
    }


def _polymarket_payload(**overrides) -> dict:
    row = {
        "venue": "polymarket",
        "market_id": "poly-1",
        "question": "Will the New York Knicks win?",
        "event_title": "Knicks vs Cavaliers",
        "end_date": "2026-05-20T13:00:00+00:00",
        "outcomes": [{"name": "Yes", "outcome_yes_token_price": 0.65}],
        "raw": {"clobTokenIds": '["yes-token", "no-token"]'},
        "orderbook_enrichment": {
            "orderbook_captured_at": "2026-05-20T11:59:30+00:00",
            "best_bid": 0.66,
            "best_ask": 0.68,
            "spread": 0.02,
            "depth_at_best_bid": 5.0,
            "depth_at_best_ask": 4.0,
            "enrichment_status": "enriched",
            "enrichment_warnings": [],
        },
    }
    row.update(overrides)
    return {
        "schema_version": 1,
        "source": "polymarket_gamma",
        "captured_at": "2026-05-20T11:55:00+00:00",
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [row],
    }


def _kalshi_payload(**overrides) -> dict:
    row = {
        "venue": "kalshi",
        "market_id": "kalshi-market-1",
        "ticker": "KXKNICKS",
        "question": "Will the New York Knicks win?",
        "event_title": "Knicks vs Cavaliers",
        "close_time": "2026-05-20T13:00:00+00:00",
        "outcomes": [{"name": "Yes", "outcome_yes_token_price": 0.60}],
        "orderbook_enrichment": {
            "orderbook_captured_at": "2026-05-20T11:59:35+00:00",
            "best_bid": 0.58,
            "best_ask": 0.60,
            "spread": 0.02,
            "depth_at_best_bid": 3.0,
            "depth_at_best_ask": 6.0,
            "enrichment_status": "enriched",
            "enrichment_warnings": [],
        },
    }
    row.update(overrides)
    return {
        "schema_version": 1,
        "source": "kalshi_markets",
        "captured_at": "2026-05-20T11:55:00+00:00",
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [row],
    }


def _evaluate(
    *,
    pairs: dict | None = None,
    poly: dict | None = None,
    kalshi: dict | None = None,
    accept_unit_mismatch: bool = False,
    min_net_gap: float = 0.01,
) -> dict:
    return evaluate_paper_candidates(
        pairs_payload=pairs or _pairs_payload(),
        polymarket_payload=poly or _polymarket_payload(),
        kalshi_payload=kalshi or _kalshi_payload(),
        detected_at=NOW,
        config=PaperCandidateEvaluatorConfig(
            accept_unit_mismatch=accept_unit_mismatch,
            min_net_gap=min_net_gap,
        ),
    )


def _first(payload: dict) -> dict:
    assert payload["ledger_count"] == 1
    return payload["ledger"][0]


def test_clean_positive_gap_caps_at_manual_review_without_unit_ack() -> None:
    row = _first(_evaluate())

    assert row["action"] == ACTION_MANUAL_REVIEW
    assert row["opportunity_class"] == "near_equivalent_manual_review"
    assert row["gap"]["gross_gap"] == pytest.approx(0.06)
    assert row["gap"]["estimated_net_gap"] == pytest.approx(0.04)
    assert row["gap"]["size_unit_warning"] == UNIT_WARNING
    assert row["missed_fill_reason"] == "unit_mismatch_not_accepted"


def test_default_fees_are_split_by_venue() -> None:
    row = _first(_evaluate())

    assert row["gap"]["polymarket_fee"] == 0.0
    assert row["gap"]["kalshi_fee"] > 0.0


def test_accept_unit_mismatch_allows_paper_candidate_but_never_paper_or_arb() -> None:
    payload = _evaluate(accept_unit_mismatch=True)
    row = _first(payload)

    assert row["action"] == ACTION_PAPER_CANDIDATE
    assert row["opportunity_class"] == "strict_cross_venue_equivalent"
    assert payload["counts_by_action"] == {
        ACTION_WATCH: 0,
        ACTION_MANUAL_REVIEW: 0,
        ACTION_PAPER_CANDIDATE: 1,
    }
    assert {row["action"] for row in payload["ledger"]} <= {ACTION_WATCH, ACTION_MANUAL_REVIEW, ACTION_PAPER_CANDIDATE}


def test_ledger_shape_has_required_nested_keys_and_null_markouts() -> None:
    row = _first(_evaluate())

    assert row["schema_version"] == 1
    assert row["candidate_id"] == "poly-1__KXKNICKS"
    assert row["polymarket"]["yes_token_id"] == "yes-token"
    assert row["polymarket"]["would_enter_side"] == "SELL_YES"
    assert row["kalshi"]["would_enter_side"] == "BUY_YES"
    assert set(row["markouts"]) == {"t_plus_30s", "t_plus_5m", "t_plus_30m", "t_plus_2h"}
    for markout in row["markouts"].values():
        assert all(value is None for value in markout.values())


def test_missing_enriched_join_is_watch() -> None:
    pairs = _pairs_payload()
    pairs["pairs"][0]["kalshi"]["ticker"] = "KXMISSING"

    row = _first(_evaluate(pairs=pairs))

    assert row["action"] == ACTION_WATCH
    assert row["opportunity_class"] == "ineligible"
    assert row["ineligibility_reasons"] == ["missing_kalshi_enriched_market"]


def test_unenriched_orderbook_is_watch() -> None:
    poly = _polymarket_payload(
        orderbook_enrichment={
            "orderbook_captured_at": "2026-05-20T11:59:30+00:00",
            "best_bid": None,
            "best_ask": None,
            "depth_at_best_bid": None,
            "depth_at_best_ask": None,
            "enrichment_status": "unenriched",
            "enrichment_warnings": ["missing_token_id"],
        }
    )

    row = _first(_evaluate(poly=poly))

    assert row["action"] == ACTION_WATCH
    assert row["missed_fill_reason"] == "orderbook_not_enriched"
    assert "polymarket_orderbook_not_enriched" in row["ineligibility_reasons"]
    assert "polymarket_missing_token_id" in row["ineligibility_reasons"]


def test_enrichment_warnings_are_prefixed_by_venue() -> None:
    kalshi = _kalshi_payload(
        orderbook_enrichment={
            "orderbook_captured_at": "2026-05-20T11:59:35+00:00",
            "best_bid": None,
            "best_ask": None,
            "depth_at_best_bid": None,
            "depth_at_best_ask": None,
            "enrichment_status": "unenriched",
            "enrichment_warnings": ["orderbook_unavailable"],
        }
    )

    row = _first(_evaluate(kalshi=kalshi))

    assert row["action"] == ACTION_WATCH
    assert "kalshi_orderbook_unavailable" in row["ineligibility_reasons"]


def test_missing_bid_or_ask_is_watch() -> None:
    kalshi_row = _kalshi_payload()["normalized_markets"][0]
    kalshi_row["orderbook_enrichment"]["best_ask"] = None
    kalshi = _kalshi_payload()
    kalshi["normalized_markets"][0] = kalshi_row

    row = _first(_evaluate(kalshi=kalshi))

    assert row["action"] == ACTION_WATCH
    assert row["missed_fill_reason"] == "missing_best_bid_or_ask"
    assert "kalshi_best_ask_missing" in row["ineligibility_reasons"]


def test_stale_quote_never_promotes() -> None:
    poly_row = _polymarket_payload()["normalized_markets"][0]
    poly_row["orderbook_enrichment"]["orderbook_captured_at"] = "2026-05-20T11:58:59+00:00"
    poly = _polymarket_payload()
    poly["normalized_markets"][0] = poly_row

    row = _first(_evaluate(poly=poly, accept_unit_mismatch=True))

    assert row["action"] == ACTION_WATCH
    assert row["missed_fill_reason"] == "stale_or_missing_quote_time"
    assert "polymarket_stale_quote" in row["ineligibility_reasons"]


def test_depth_on_actual_hit_side_is_required() -> None:
    kalshi_row = _kalshi_payload()["normalized_markets"][0]
    kalshi_row["orderbook_enrichment"]["depth_at_best_ask"] = 0.5
    kalshi = _kalshi_payload()
    kalshi["normalized_markets"][0] = kalshi_row

    row = _first(_evaluate(kalshi=kalshi, accept_unit_mismatch=True))

    assert row["action"] == ACTION_WATCH
    assert row["missed_fill_reason"] == "insufficient_top_of_book_depth"
    assert "kalshi_insufficient_top_of_book_depth" in row["ineligibility_reasons"]


def test_missing_or_naive_settlement_time_caps_at_manual_review() -> None:
    kalshi = _kalshi_payload(close_time="2026-05-20T13:00:00")

    row = _first(_evaluate(kalshi=kalshi, accept_unit_mismatch=True))

    assert row["action"] == ACTION_MANUAL_REVIEW
    assert row["opportunity_class"] == "near_equivalent_manual_review"
    assert row["missed_fill_reason"] == "settlement_time_missing_or_naive"


def test_settlement_delta_over_limit_is_watch() -> None:
    kalshi = _kalshi_payload(close_time="2026-05-20T15:00:01+00:00")

    row = _first(_evaluate(kalshi=kalshi, accept_unit_mismatch=True))

    assert row["action"] == ACTION_WATCH
    assert row["missed_fill_reason"] == "settlement_delta_exceeds_limit"


def test_ambiguous_wording_caps_at_manual_review() -> None:
    row = _first(_evaluate(pairs=_pairs_payload(["ambiguous_wording"]), accept_unit_mismatch=True))

    assert row["action"] == ACTION_MANUAL_REVIEW
    assert row["missed_fill_reason"] == "ambiguous_wording"
    assert row["ineligibility_reasons"] == ["ambiguous_wording"]


def test_other_matcher_ineligibility_reason_is_watch() -> None:
    row = _first(_evaluate(pairs=_pairs_payload(["kalshi_closed_inactive_market"]), accept_unit_mismatch=True))

    assert row["action"] == ACTION_WATCH
    assert row["missed_fill_reason"] == "matcher_ineligibility_reason"
    assert row["ineligibility_reasons"] == ["kalshi_closed_inactive_market"]


def test_reference_venue_forces_reference_only_watch() -> None:
    poly = _polymarket_payload(venue="sportsbook_reference")

    row = _first(_evaluate(poly=poly, accept_unit_mismatch=True))

    assert row["action"] == ACTION_WATCH
    assert row["opportunity_class"] == "reference_only_watch"
    assert row["missed_fill_reason"] == "reference_only_watch"


def test_bid_ask_gap_only_never_midpoint() -> None:
    poly_row = _polymarket_payload()["normalized_markets"][0]
    poly_row["orderbook_enrichment"]["best_bid"] = 0.50
    poly_row["orderbook_enrichment"]["best_ask"] = 0.90
    kalshi_row = _kalshi_payload()["normalized_markets"][0]
    kalshi_row["orderbook_enrichment"]["best_bid"] = 0.49
    kalshi_row["orderbook_enrichment"]["best_ask"] = 0.51
    poly = _polymarket_payload()
    kalshi = _kalshi_payload()
    poly["normalized_markets"][0] = poly_row
    kalshi["normalized_markets"][0] = kalshi_row

    row = _first(_evaluate(poly=poly, kalshi=kalshi, accept_unit_mismatch=True))

    assert row["action"] == ACTION_WATCH
    assert row["gap"]["gross_gap"] == pytest.approx(-0.01)
    assert row["missed_fill_reason"] == "no_positive_bid_ask_gap"


def test_fees_block_marginal_gap() -> None:
    poly_row = _polymarket_payload()["normalized_markets"][0]
    poly_row["orderbook_enrichment"]["best_bid"] = 0.625
    kalshi = _kalshi_payload()
    poly = _polymarket_payload()
    poly["normalized_markets"][0] = poly_row

    row = _first(_evaluate(poly=poly, kalshi=kalshi, accept_unit_mismatch=True))

    assert row["action"] == ACTION_WATCH
    assert row["gap"]["gross_gap"] == pytest.approx(0.025)
    assert row["gap"]["estimated_net_gap"] < 0.01
    assert row["missed_fill_reason"] == "estimated_net_gap_below_minimum"


def test_schema_version_must_be_one() -> None:
    pairs = _pairs_payload()
    pairs["schema_version"] = 2

    with pytest.raises(ValueError, match="pairs schema_version must be 1"):
        _evaluate(pairs=pairs)


def test_inputs_are_deep_copied_and_not_mutated() -> None:
    pairs = _pairs_payload()
    poly = _polymarket_payload()
    kalshi = _kalshi_payload()
    before = (copy.deepcopy(pairs), copy.deepcopy(poly), copy.deepcopy(kalshi))

    _evaluate(pairs=pairs, poly=poly, kalshi=kalshi)

    assert (pairs, poly, kalshi) == before


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_evaluate_paper_candidates_cli_success(tmp_path: Path, capsys) -> None:
    output = tmp_path / "ledger.json"

    result = scan.main(
        [
            "evaluate-paper-candidates",
            "--pairs",
            str(_write(tmp_path / "pairs.json", _pairs_payload())),
            "--polymarket-enriched",
            str(_write(tmp_path / "poly.json", _polymarket_payload())),
            "--kalshi-enriched",
            str(_write(tmp_path / "kalshi.json", _kalshi_payload())),
            "--output",
            str(output),
            "--max-quote-age-seconds",
            "999999999",
            "--accept-unit-mismatch",
        ]
    )

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ledger_count"] == 1
    assert payload["counts_by_action"][ACTION_PAPER_CANDIDATE] == 1
    assert "paper_candidate_evaluator_status=OK candidates=1 paper=1 manual_review=0 watch=0" in capsys.readouterr().out


def test_evaluate_paper_candidates_cli_failure(tmp_path: Path, capsys) -> None:
    pairs = _pairs_payload()
    pairs["schema_version"] = 999

    result = scan.main(
        [
            "evaluate-paper-candidates",
            "--pairs",
            str(_write(tmp_path / "pairs.json", pairs)),
            "--polymarket-enriched",
            str(_write(tmp_path / "poly.json", _polymarket_payload())),
            "--kalshi-enriched",
            str(_write(tmp_path / "kalshi.json", _kalshi_payload())),
            "--output",
            str(tmp_path / "ledger.json"),
        ]
    )

    assert result == 1
    assert "paper_candidate_evaluator_status=FAILED message=pairs schema_version must be 1" in capsys.readouterr().out
