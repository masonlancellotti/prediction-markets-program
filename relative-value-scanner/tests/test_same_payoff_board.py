from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.same_payoff_board import build_same_payoff_board


NOW = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)


def _pair(poly_id: str = "poly-1", kalshi_ticker: str = "KXNBA-1", similarity: float = 0.98) -> dict:
    return {
        "schema_version": 1,
        "source": "live_snapshot_matcher",
        "generated_at": "2026-05-23T11:59:00+00:00",
        "pair_count": 1,
        "pairs": [
            {
                "action": "MANUAL_REVIEW",
                "polymarket": {
                    "market_id": poly_id,
                    "question": "Will New York Knicks win?",
                    "event_title": "New York Knicks vs Cleveland Cavaliers",
                },
                "kalshi": {
                    "ticker": kalshi_ticker,
                    "question": "Will New York Knicks win?",
                    "event_title": "New York Knicks vs Cleveland Cavaliers",
                },
                "similarity_score": similarity,
                "ineligibility_reasons": [],
                "contract_relationship": {
                    "relationship": "NEAR_EQUIVALENT",
                    "same_payoff": False,
                    "confidence": 0.4,
                    "blocking_reasons": [],
                    "manual_review_required": True,
                    "source": "deterministic_rules",
                },
            }
        ],
    }


def _market(
    venue: str,
    *,
    question: str = "Will New York Knicks win?",
    event_title: str = "New York Knicks vs Cleveland Cavaliers",
    settlement_rule: str = "official nba box score team points",
    end_date: str = "2026-05-24T02:00:00+00:00",
    outcomes: list[dict] | None = None,
    orderbook_captured_at: str = "2026-05-23T11:55:00+00:00",
    enrichment_status: str = "enriched",
    source_type: str | None = None,
    currency: str | None = None,
) -> dict:
    row = {
        "venue": venue,
        "market_id": "poly-1" if venue == "polymarket" else "kalshi-market-1",
        "event_title": event_title,
        "question": question,
        "settlement_rule": settlement_rule,
        "end_date": end_date,
        "outcomes": outcomes or [{"name": "Yes"}, {"name": "No"}],
        "active": True,
        "closed": False,
        "orderbook_enrichment": {
            "orderbook_captured_at": orderbook_captured_at,
            "best_bid": 0.45,
            "best_ask": 0.55,
            "depth_at_best_bid": 10.0,
            "depth_at_best_ask": 12.0,
            "enrichment_status": enrichment_status,
            "enrichment_warnings": [],
        },
        "raw": {"event_slug": "nba-knicks-cavaliers", "series_ticker": "KXNBA"},
    }
    if source_type is not None:
        row["source_type"] = source_type
    if currency is not None:
        row["currency"] = currency
    if venue == "kalshi":
        row["ticker"] = "KXNBA-1"
        row["close_time"] = end_date
    else:
        row["condition_id"] = "0xpoly1"
    return row


def _snapshot(venue: str, market: dict) -> dict:
    return {
        "schema_version": 1,
        "source": f"{venue}_enriched",
        "captured_at": "2026-05-23T11:50:00+00:00",
        "normalized_count": 1,
        "normalized_markets": [market],
    }


def _board(*, poly: dict | None = None, kalshi: dict | None = None, pairs: dict | None = None) -> dict:
    return build_same_payoff_board(
        pairs_payload=pairs or _pair(),
        polymarket_payload=_snapshot("polymarket", poly or _market("polymarket")),
        kalshi_payload=_snapshot("kalshi", kalshi or _market("kalshi")),
        generated_at=NOW,
    )


def _first(payload: dict) -> dict:
    assert payload["row_count"] == 1
    return payload["rows"][0]


def _mlb_pair(poly_id: str = "poly-mlb", kalshi_ticker: str = "KXMLB-26-NYY") -> dict:
    payload = _pair(poly_id=poly_id, kalshi_ticker=kalshi_ticker)
    payload["pairs"][0]["polymarket"] = {
        "market_id": poly_id,
        "question": "Will the New York Yankees win the 2026 World Series?",
        "event_title": "MLB World Series Champion 2026",
    }
    payload["pairs"][0]["kalshi"] = {
        "ticker": kalshi_ticker,
        "question": "Will New York Y win the 2026 Pro Baseball Championship?",
        "event_title": None,
    }
    return payload


def _mlb_market(
    venue: str,
    *,
    team_question: str,
    ticker: str = "KXMLB-26-NYY",
    event_title: str | None = "MLB World Series Champion 2026",
    market_type: str | None = None,
    settlement_rule: str = "official mlb world series winner",
    end_date: str = "2026-11-01T04:00:00+00:00",
) -> dict:
    row = _market(
        venue,
        question=team_question,
        event_title=event_title,
        settlement_rule=settlement_rule,
        end_date=end_date,
        source_type="EXECUTABLE_VENUE",
    )
    row["raw"] = {"series_ticker": "KXMLB", "event_ticker": "KXMLB-26"}
    if market_type is not None:
        row["market_type"] = market_type
        row["raw"]["market_type"] = market_type
    if venue == "kalshi":
        row["ticker"] = ticker
        row["market_id"] = ticker
        row["event_title"] = event_title
    else:
        row["market_id"] = "poly-mlb"
    return row


def _mlb_board(*, poly: dict | None = None, kalshi: dict | None = None, pairs: dict | None = None) -> dict:
    return build_same_payoff_board(
        pairs_payload=pairs or _mlb_pair(),
        polymarket_payload=_snapshot(
            "polymarket",
            poly
            or _mlb_market(
                "polymarket",
                team_question="Will the New York Yankees win the 2026 World Series?",
                market_type="binary_event",
            ),
        ),
        kalshi_payload=_snapshot(
            "kalshi",
            kalshi
            or _mlb_market(
                "kalshi",
                team_question="Will New York Y win the 2026 Pro Baseball Championship?",
                market_type="binary",
            ),
        ),
        generated_at=NOW,
    )


def test_exact_same_payoff_fixture_passes_evidence_checks() -> None:
    row = _first(_board())

    assert row["same_payoff"] is True
    assert row["recommended_next_action"] == "RELATIONSHIP_REVIEW"
    assert row["strict_blockers"] == []
    assert row["strict_missing_fields"] == []
    assert row["info_blockers"] == []
    assert "kalshi_fee_model_or_rate" not in row["info_missing_fields"]
    assert "polymarket_fee_model_or_rate" in row["info_missing_fields"]
    assert row["blockers"] == []
    assert row["same_payoff_evidence"]["settlement_source"]["status"] == "PASS"
    assert row["same_payoff_evidence"]["outcome_direction_polarity"]["status"] == "PASS"
    assert row["existing_contract_relationship"]["same_payoff"] is False


def test_opposite_outcome_polarity_mismatch_blocks() -> None:
    kalshi = _market("kalshi", question="Will New York Knicks not win?")

    row = _first(_board(kalshi=kalshi))

    assert row["same_payoff"] is False
    assert "outcome_direction_polarity_mismatch" in row["blockers"]
    assert row["recommended_next_action"] == "SKIP"


def test_numeric_threshold_drift_blocks() -> None:
    poly = _market("polymarket", question="Will Cleveland Cavaliers score over 91.5 points?")
    kalshi = _market("kalshi", question="Will Cleveland Cavaliers score over 92.5 points?")

    row = _first(_board(poly=poly, kalshi=kalshi))

    assert row["same_payoff"] is False
    assert "threshold_strike_mismatch" in row["blockers"]


def test_settlement_date_drift_blocks() -> None:
    kalshi = _market("kalshi", end_date="2026-05-24T05:30:01+00:00")

    row = _first(_board(kalshi=kalshi))

    assert row["same_payoff"] is False
    assert "settlement_date_drift" in row["blockers"]


def test_settlement_source_drift_blocks() -> None:
    kalshi = _market("kalshi", settlement_rule="official nba league standings")

    row = _first(_board(kalshi=kalshi))

    assert row["same_payoff"] is False
    assert "settlement_source_mismatch" in row["blockers"]


def test_settlement_rule_tiebreak_drift_blocks() -> None:
    poly = _market("polymarket", settlement_rule="official nba box score team points tie void")
    kalshi = _market("kalshi", settlement_rule="official nba box score team points tie push")

    row = _first(_board(poly=poly, kalshi=kalshi))

    assert row["same_payoff"] is False
    assert "settlement_rule_tiebreak_mismatch" in row["blockers"]


def test_no_side_spread_or_side_definition_ambiguity_blocks() -> None:
    poly = _market("polymarket", question="Will Cleveland cover the no spread?", outcomes=[{"name": "Cleveland"}, {"name": "New York"}])

    row = _first(_board(poly=poly))

    assert row["same_payoff"] is False
    assert "no_side_spread_or_side_definition_ambiguous" in row["blockers"]


def test_usd_usdc_or_unit_mismatch_blocks() -> None:
    poly = _market("polymarket", currency="USDC")
    kalshi = _market("kalshi", currency="USD")

    row = _first(_board(poly=poly, kalshi=kalshi))

    assert row["same_payoff"] is False
    assert "unit_or_liquidity_unit_mismatch" in row["blockers"]


def test_stale_quote_blocks_readiness_language() -> None:
    poly = _market("polymarket", orderbook_captured_at="2026-05-23T10:00:00+00:00")

    payload = _board(poly=poly)
    row = _first(payload)
    serialized = json.dumps(payload)

    assert "polymarket_stale_quote" in row["blockers"]
    assert row["recommended_next_action"] == "ENRICH_IF_APPROVED"
    assert "PAPER" not in serialized
    assert "POSSIBLE_ARB" not in serialized


def test_reference_only_source_cannot_become_executable_leg() -> None:
    poly = _market("polymarket", source_type="REFERENCE_ONLY")

    row = _first(_board(poly=poly))

    assert row["same_payoff"] is False
    assert "polymarket_not_executable_kalshi_polymarket_leg" in row["blockers"]
    assert row["recommended_next_action"] == "SKIP"


def test_browns_vs_guardians_is_unrelated_and_not_same_payoff() -> None:
    poly = _market("polymarket", question="Will the Cleveland Browns win?", event_title="Cleveland Browns game")
    kalshi = _market("kalshi", question="Will the Cleveland Guardians win?", event_title="Cleveland Guardians game")

    row = _first(_board(poly=poly, kalshi=kalshi))

    assert row["same_payoff"] is False
    assert "relationship_shape_unrelated" in row["blockers"]
    assert row["same_payoff_evidence"]["relationship_shape"]["values"]["relationship"] == "UNRELATED"


def test_world_series_vs_alcs_is_subset_or_superset_not_same_payoff() -> None:
    poly = _market("polymarket", question="Will Cleveland win the World Series?", event_title="MLB futures")
    kalshi = _market("kalshi", question="Will Cleveland win the ALCS?", event_title="MLB futures")

    row = _first(_board(poly=poly, kalshi=kalshi))

    assert row["same_payoff"] is False
    assert "relationship_shape_subset_or_superset" in row["blockers"]
    assert row["same_payoff_evidence"]["relationship_shape"]["values"]["relationship"] == "SUBSET_OR_SUPERSET"


def test_mlb_yankees_world_series_entity_scope_comparators_pass() -> None:
    row = _first(_mlb_board())

    assert row["same_payoff_evidence"]["market_event_entity"]["status"] == "PASS"
    assert row["same_payoff_evidence"]["sports_league_team"]["status"] == "PASS"
    assert row["same_payoff_evidence"]["market_type"]["status"] == "PASS"
    assert row["same_payoff_evidence"]["threshold_strike"]["status"] == "PASS"
    assert "market_event_entity_mismatch" not in row["blockers"]
    assert "sports_league_team_mismatch" not in row["blockers"]
    assert "market_type_mismatch" not in row["blockers"]
    assert "threshold_strike_mismatch" not in row["blockers"]


def test_mlb_tampa_bay_world_series_entity_scope_comparators_pass() -> None:
    pairs = _mlb_pair(poly_id="poly-tb", kalshi_ticker="KXMLB-26-TB")
    pairs["pairs"][0]["polymarket"]["question"] = "Will the Tampa Bay Rays win the 2026 World Series?"
    pairs["pairs"][0]["kalshi"]["question"] = "Will Tampa Bay win the 2026 Pro Baseball Championship?"
    poly = _mlb_market(
        "polymarket",
        team_question="Will the Tampa Bay Rays win the 2026 World Series?",
        market_type="binary_event",
    )
    poly["market_id"] = "poly-tb"
    kalshi = _mlb_market(
        "kalshi",
        team_question="Will Tampa Bay win the 2026 Pro Baseball Championship?",
        ticker="KXMLB-26-TB",
        market_type="binary",
    )

    row = _first(_mlb_board(poly=poly, kalshi=kalshi, pairs=pairs))

    assert row["same_payoff_evidence"]["market_event_entity"]["status"] == "PASS"
    assert row["same_payoff_evidence"]["sports_league_team"]["status"] == "PASS"


def test_mlb_dodgers_vs_angels_laa_still_blocks() -> None:
    pairs = _mlb_pair(poly_id="poly-lad", kalshi_ticker="KXMLB-26-LAA")
    pairs["pairs"][0]["polymarket"]["question"] = "Will the Los Angeles Dodgers win the 2026 World Series?"
    pairs["pairs"][0]["kalshi"]["question"] = "Will Los Angeles A win the 2026 Pro Baseball Championship?"
    poly = _mlb_market("polymarket", team_question="Will the Los Angeles Dodgers win the 2026 World Series?")
    poly["market_id"] = "poly-lad"
    kalshi = _mlb_market("kalshi", team_question="Will Los Angeles A win the 2026 Pro Baseball Championship?", ticker="KXMLB-26-LAA")

    row = _first(_mlb_board(poly=poly, kalshi=kalshi, pairs=pairs))

    assert row["same_payoff"] is False
    assert "market_event_entity_mismatch" in row["blockers"]
    assert "sports_league_team_mismatch" in row["blockers"]


def test_mlb_red_sox_vs_white_sox_still_blocks() -> None:
    pairs = _mlb_pair(poly_id="poly-bos", kalshi_ticker="KXMLB-26-CWS")
    pairs["pairs"][0]["polymarket"]["question"] = "Will the Boston Red Sox win the 2026 World Series?"
    pairs["pairs"][0]["kalshi"]["question"] = "Will Chicago WS win the 2026 Pro Baseball Championship?"
    poly = _mlb_market("polymarket", team_question="Will the Boston Red Sox win the 2026 World Series?")
    poly["market_id"] = "poly-bos"
    kalshi = _mlb_market("kalshi", team_question="Will Chicago WS win the 2026 Pro Baseball Championship?", ticker="KXMLB-26-CWS")

    row = _first(_mlb_board(poly=poly, kalshi=kalshi, pairs=pairs))

    assert row["same_payoff"] is False
    assert "market_event_entity_mismatch" in row["blockers"]
    assert "sports_league_team_mismatch" in row["blockers"]


def test_mlb_binary_type_compatibility_is_narrow() -> None:
    row = _first(_mlb_board())
    non_mlb_poly = _market("polymarket")
    non_mlb_kalshi = _market("kalshi")
    non_mlb_kalshi["market_type"] = "binary"
    non_mlb_kalshi["raw"]["market_type"] = "binary"
    non_mlb_row = _first(_board(poly=non_mlb_poly, kalshi=non_mlb_kalshi))

    assert row["same_payoff_evidence"]["market_type"]["status"] == "PASS"
    assert non_mlb_row["same_payoff_evidence"]["market_type"]["status"] == "FAIL"
    assert "market_type_mismatch" in non_mlb_row["blockers"]


def test_mlb_non_threshold_outright_does_not_fail_threshold_strike() -> None:
    row = _first(_mlb_board())

    assert row["same_payoff_evidence"]["threshold_strike"]["status"] == "PASS"
    assert "threshold_strike_mismatch" not in row["blockers"]


def test_threshold_mismatch_still_fails_when_threshold_exists() -> None:
    poly = _market("polymarket", question="Will Cleveland Cavaliers score over 91.5 points?")
    kalshi = _market("kalshi", question="Will Cleveland Cavaliers score over 92.5 points?")

    row = _first(_board(poly=poly, kalshi=kalshi))

    assert row["same_payoff_evidence"]["threshold_strike"]["status"] == "FAIL"
    assert "threshold_strike_mismatch" in row["blockers"]


def test_mlb_settlement_date_drift_remains_blocker_without_safe_evidence() -> None:
    kalshi = _mlb_market(
        "kalshi",
        team_question="Will New York Y win the 2026 Pro Baseball Championship?",
        market_type="binary",
        end_date="2026-11-01T08:30:01+00:00",
    )

    row = _first(_mlb_board(kalshi=kalshi))

    assert row["same_payoff"] is False
    assert "settlement_date_drift" in row["blockers"]
    assert row["same_payoff_evidence"]["settlement_time"]["values"]["kalshi"]["end_date"] == "2026-11-01T08:30:01+00:00"


def test_mlb_world_series_four_hour_timezone_drift_passes_settlement_time() -> None:
    poly = _mlb_market(
        "polymarket",
        team_question="Will the New York Yankees win the 2026 World Series?",
        market_type="binary_event",
        end_date="2026-10-31T23:55:00+00:00",
    )
    kalshi = _mlb_market(
        "kalshi",
        team_question="Will New York Y win the 2026 Pro Baseball Championship?",
        market_type="binary",
        end_date="2026-11-01T04:00:00+00:00",
    )

    row = _first(_mlb_board(poly=poly, kalshi=kalshi))

    assert row["same_payoff_evidence"]["settlement_time"]["status"] == "PASS"
    assert row["same_payoff_evidence"]["settlement_time"]["values"]["normalization"] == "mlb_world_series_timezone_convention_drift"
    assert "settlement_date_drift" not in row["blockers"]


def test_non_mlb_four_hour_drift_still_fails() -> None:
    kalshi = _market("kalshi", end_date="2026-05-24T06:00:00+00:00")

    row = _first(_board(kalshi=kalshi))

    assert row["same_payoff_evidence"]["settlement_time"]["status"] == "FAIL"
    assert "settlement_date_drift" in row["blockers"]


def test_mlb_missing_settlement_source_remains_missing_if_genuine() -> None:
    poly = _mlb_market(
        "polymarket",
        team_question="Will the New York Yankees win the 2026 World Series?",
        market_type="binary_event",
        settlement_rule="official baseball futures market",
    )
    kalshi = _mlb_market(
        "kalshi",
        team_question="Will New York Y win the 2026 Pro Baseball Championship?",
        market_type="binary",
        settlement_rule="",
    )
    kalshi.pop("settlement_rule", None)

    row = _first(_mlb_board(poly=poly, kalshi=kalshi))

    assert row["same_payoff_evidence"]["settlement_source"]["status"] == "MISSING"
    assert "kalshi_settlement_source_or_rule" in row["missing_fields"]


def test_mlb_one_sided_explicit_world_series_source_passes() -> None:
    kalshi = _mlb_market(
        "kalshi",
        team_question="Will New York Y win the 2026 Pro Baseball Championship?",
        market_type="binary",
        settlement_rule="",
    )
    kalshi.pop("settlement_rule", None)

    row = _first(_mlb_board(kalshi=kalshi))

    assert row["same_payoff_evidence"]["settlement_source"]["status"] == "PASS"
    assert row["same_payoff_evidence"]["settlement_source"]["values"]["normalization"] == "mlb_world_series_named_primary_source_one_sided"
    assert "kalshi_settlement_source_or_rule" not in row["missing_fields"]


def test_mlb_stale_quote_remains_blocker_not_semantic_mismatch() -> None:
    poly = _mlb_market(
        "polymarket",
        team_question="Will the New York Yankees win the 2026 World Series?",
        market_type="binary_event",
    )
    poly["orderbook_enrichment"]["orderbook_captured_at"] = "2026-05-23T10:00:00+00:00"

    row = _first(_mlb_board(poly=poly))

    assert "polymarket_stale_quote" in row["blockers"]
    assert "polymarket_stale_quote" in row["info_blockers"]
    assert row["strict_blockers"] == []
    assert row["strict_missing_fields"] == []
    assert "market_event_entity_mismatch" not in row["blockers"]
    assert "sports_league_team_mismatch" not in row["blockers"]


def test_btc_threshold_subset_or_superset_is_not_same_payoff() -> None:
    poly = _market("polymarket", question="Will BTC be above 120000 by June 30?", event_title="BTC price")
    kalshi = _market("kalshi", question="Will BTC be above 100000 by June 30?", event_title="BTC price")

    row = _first(_board(poly=poly, kalshi=kalshi))

    assert row["same_payoff"] is False
    assert "relationship_shape_subset_or_superset" in row["blockers"]
    assert row["same_payoff_evidence"]["relationship_shape"]["values"]["relationship"] == "SUBSET_OR_SUPERSET"


def test_openai_ipo_timing_vs_openai_anthropic_ordering_is_overlap_not_equivalent() -> None:
    poly = _market("polymarket", question="Will OpenAI IPO before 2027?", event_title="OpenAI IPO")
    kalshi = _market("kalshi", question="Will OpenAI IPO before Anthropic?", event_title="OpenAI and Anthropic IPO ordering")

    row = _first(_board(poly=poly, kalshi=kalshi))

    assert row["same_payoff"] is False
    assert "relationship_shape_overlap_not_equivalent" in row["blockers"]
    assert row["same_payoff_evidence"]["relationship_shape"]["values"]["relationship"] == "OVERLAP_NOT_EQUIVALENT"


def test_board_has_no_paper_possible_arb_or_trade_labels() -> None:
    serialized = json.dumps(_board())

    assert "PAPER" not in serialized
    assert "POSSIBLE_ARB" not in serialized
    assert "trade" not in serialized.lower()


def test_same_payoff_board_cli_writes_reports(tmp_path: Path, capsys) -> None:
    pairs = tmp_path / "pairs.json"
    poly = tmp_path / "poly.json"
    kalshi = tmp_path / "kalshi.json"
    json_output = tmp_path / "board.json"
    markdown_output = tmp_path / "board.md"
    pairs.write_text(json.dumps(_pair()), encoding="utf-8")
    poly.write_text(json.dumps(_snapshot("polymarket", _market("polymarket"))), encoding="utf-8")
    kalshi.write_text(json.dumps(_snapshot("kalshi", _market("kalshi"))), encoding="utf-8")

    result = scan.main(
        [
            "same-payoff-board",
            "--pairs",
            str(pairs),
            "--polymarket-enriched",
            str(poly),
            "--kalshi-enriched",
            str(kalshi),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert result == 0
    assert json_output.exists()
    assert markdown_output.exists()
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["strict_same_payoff_pass_count"] == 1
    stdout = capsys.readouterr().out
    assert "same_payoff_board_status=OK rows=1 strict_same_payoff_passes=1" in stdout
    assert "PAPER" not in stdout
    assert "POSSIBLE_ARB" not in stdout


def test_default_scan_py_remains_static_fixture(capsys) -> None:
    result = scan.main([])

    assert result == 0
    output = capsys.readouterr().out
    assert "data_source_mode=STATIC_FIXTURE" in output
    assert "live_fetch_attempted=false" in output
