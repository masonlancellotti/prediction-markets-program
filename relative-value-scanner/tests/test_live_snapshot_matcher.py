import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.contract_relationship import (
    RELATIONSHIP_AMBIGUOUS,
    RELATIONSHIP_MUTUALLY_EXCLUSIVE,
    RELATIONSHIP_NEAR_EQUIVALENT,
    RELATIONSHIP_SOURCE_DETERMINISTIC_RULES,
    RELATIONSHIP_SUBSET,
)
from relative_value.live_snapshot_matcher import _event_keyword_tokens, load_snapshot, match_snapshot_files


NOW = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)


def _polymarket_snapshot(question: str = "Will the New York Knicks win?") -> dict:
    return {
        "schema_version": 1,
        "source": "polymarket_gamma",
        "captured_at": "2026-05-20T11:30:00+00:00",
        "event_count": 1,
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [
            {
                "venue": "polymarket",
                "event_id": "poly-event-1",
                "event_title": "New York Knicks vs Cleveland Cavaliers",
                "market_id": "poly-market-1",
                "question": question,
                "outcomes": [{"name": "Yes", "outcome_yes_token_price": 0.43}],
                "best_bid": 0.42,
                "best_ask": 0.44,
                "volume": 1000.0,
                "liquidity": 500.0,
                "end_date": "2026-05-21T00:00:00+00:00",
                "active": True,
                "closed": False,
                "raw": {},
            }
        ],
    }


def _kalshi_snapshot(question: str = "Will the New York Knicks win?") -> dict:
    return {
        "schema_version": 1,
        "source": "kalshi_markets",
        "captured_at": "2026-05-20T11:31:00+00:00",
        "event_count": None,
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [
            {
                "venue": "kalshi",
                "event_id": "kalshi-event-1",
                "event_title": "New York Knicks vs Cleveland Cavaliers",
                "market_id": "kalshi-market-1",
                "ticker": "KXNBA-26MAY20-NYK",
                "question": question,
                "title": question,
                "outcomes": [{"name": "Yes", "outcome_yes_token_price": 0.44}],
                "best_bid": 0.42,
                "best_ask": 0.44,
                "volume": 800.0,
                "liquidity": 300.0,
                "close_time": "2026-05-21T00:00:00+00:00",
                "active": True,
                "closed": False,
                "status": "active",
                "raw": {},
            }
        ],
    }


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _baseball_snapshots(poly_question: str, kalshi_question: str) -> tuple[dict, dict]:
    poly = _polymarket_snapshot(poly_question)
    kalshi = _kalshi_snapshot(kalshi_question)
    poly["normalized_markets"][0]["event_title"] = "MLB futures"
    poly["normalized_markets"][0]["raw"] = {"event_slug": "mlb-futures"}
    kalshi["normalized_markets"][0]["event_title"] = "MLB futures"
    kalshi["normalized_markets"][0]["raw"] = {"series_ticker": "KXMLB"}
    return poly, kalshi


def _sports_snapshots(
    poly_question: str,
    kalshi_question: str,
    *,
    event_title: str,
    event_slug: str,
    series_ticker: str,
) -> tuple[dict, dict]:
    poly = _polymarket_snapshot(poly_question)
    kalshi = _kalshi_snapshot(kalshi_question)
    poly["normalized_markets"][0]["event_title"] = event_title
    poly["normalized_markets"][0]["raw"] = {"event_slug": event_slug}
    kalshi["normalized_markets"][0]["event_title"] = event_title
    kalshi["normalized_markets"][0]["raw"] = {"series_ticker": series_ticker}
    return poly, kalshi


def test_valid_schema_v1_snapshots_load(tmp_path: Path) -> None:
    poly_path = _write(tmp_path / "poly.json", _polymarket_snapshot())
    kalshi_path = _write(tmp_path / "kalshi.json", _kalshi_snapshot())

    assert load_snapshot(poly_path, venue="polymarket").issues == ()
    assert load_snapshot(kalshi_path, venue="kalshi").issues == ()


def test_missing_and_unsupported_schema_versions_are_reported(tmp_path: Path) -> None:
    missing = _polymarket_snapshot()
    missing.pop("schema_version")
    unsupported = _kalshi_snapshot()
    unsupported["schema_version"] = 2

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", missing),
        _write(tmp_path / "kalshi.json", unsupported),
        now=NOW,
    )

    assert payload["pair_count"] == 0
    assert "missing_schema_version" in payload["snapshot_issues"]["polymarket"]
    assert "unsupported_schema_version" in payload["snapshot_issues"]["kalshi"]


def test_closed_inactive_markets_are_marked_ineligible(tmp_path: Path) -> None:
    poly = _polymarket_snapshot()
    poly["normalized_markets"][0]["closed"] = True
    kalshi = _kalshi_snapshot()
    kalshi["normalized_markets"][0]["active"] = False

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "WATCH"
    assert "polymarket_closed_inactive_market" in pair["ineligibility_reasons"]
    assert "kalshi_closed_inactive_market" in pair["ineligibility_reasons"]
    relationship = pair["contract_relationship"]
    assert relationship["relationship"] == RELATIONSHIP_NEAR_EQUIVALENT
    assert relationship["same_payoff"] is False
    assert relationship["manual_review_required"] is True
    assert relationship["confidence"] <= 0.5
    assert relationship["blocking_reasons"] == []


def test_clean_pair_with_liquidity_can_reach_manual_review(tmp_path: Path) -> None:
    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", _polymarket_snapshot()),
        _write(tmp_path / "kalshi.json", _kalshi_snapshot()),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "MANUAL_REVIEW"
    assert pair["ineligibility_reasons"] == []
    relationship = pair["contract_relationship"]
    assert relationship["relationship"] == RELATIONSHIP_NEAR_EQUIVALENT
    assert relationship["same_payoff"] is False
    assert relationship["manual_review_required"] is True
    assert relationship["confidence"] <= 0.5


def test_event_title_similarity_constrains_otherwise_similar_questions(tmp_path: Path) -> None:
    poly = _polymarket_snapshot("Will the New York Knicks win?")
    poly["normalized_markets"][0]["event_title"] = "New York Knicks vs Cleveland Cavaliers"
    kalshi = _kalshi_snapshot("Will the New York Knicks win?")
    kalshi["normalized_markets"][0]["event_title"] = "Los Angeles Lakers vs Boston Celtics"

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 0


def test_baseball_alcs_vs_overall_championship_is_ineligible(tmp_path: Path) -> None:
    poly, kalshi = _baseball_snapshots(
        "Will Tampa Bay Rays win the 2026 American League Championship Series?",
        "Will Tampa Bay win the 2026 Pro Baseball Championship?",
    )

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "WATCH"
    assert "sports_competition_scope_mismatch" in pair["ineligibility_reasons"]
    relationship = pair["contract_relationship"]
    assert relationship["relationship"] == RELATIONSHIP_SUBSET
    assert relationship["same_payoff"] is False
    assert relationship["source"] == RELATIONSHIP_SOURCE_DETERMINISTIC_RULES
    assert "sports_competition_scope_mismatch" in relationship["blocking_reasons"]
    json.dumps(relationship)
    assert "PAPER_CANDIDATE" not in json.dumps(payload)
    assert "POSSIBLE_ARB" not in json.dumps(payload)


def test_baseball_dodgers_vs_los_angeles_a_alias_mismatch_is_ineligible(tmp_path: Path) -> None:
    poly, kalshi = _baseball_snapshots(
        "Will Los Angeles Dodgers win the 2026 National League Championship Series?",
        "Will Los Angeles A win the 2026 Pro Baseball Championship?",
    )

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "WATCH"
    assert "sports_competition_scope_mismatch" in pair["ineligibility_reasons"]
    relationship = pair["contract_relationship"]
    assert relationship["relationship"] == RELATIONSHIP_MUTUALLY_EXCLUSIVE
    assert relationship["same_payoff"] is False
    assert "sports_competition_scope_mismatch" in relationship["blocking_reasons"]
    assert "sports_team_alias_mismatch" in pair["ineligibility_reasons"]
    assert "sports_team_alias_mismatch" in relationship["blocking_reasons"]
    assert "PAPER_CANDIDATE" not in json.dumps(payload)
    assert "POSSIBLE_ARB" not in json.dumps(payload)


def test_baseball_nlcs_vs_overall_championship_is_ineligible(tmp_path: Path) -> None:
    poly, kalshi = _baseball_snapshots(
        "Will San Diego Padres win the 2026 National League Championship Series?",
        "Will San Diego win the 2026 Pro Baseball Championship?",
    )

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "WATCH"
    assert "sports_competition_scope_mismatch" in pair["ineligibility_reasons"]
    relationship = pair["contract_relationship"]
    assert relationship["relationship"] == RELATIONSHIP_SUBSET
    assert relationship["same_payoff"] is False
    assert "sports_competition_scope_mismatch" in relationship["blocking_reasons"]
    assert "PAPER_CANDIDATE" not in json.dumps(payload)
    assert "POSSIBLE_ARB" not in json.dumps(payload)


def test_same_team_same_competition_baseball_future_can_match_normally(tmp_path: Path) -> None:
    poly, kalshi = _baseball_snapshots(
        "Will San Diego Padres win the 2026 National League Championship Series?",
        "Will San Diego Padres win the 2026 NLCS?",
    )

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "MANUAL_REVIEW"
    assert "sports_competition_scope_mismatch" not in pair["ineligibility_reasons"]
    relationship = pair["contract_relationship"]
    assert relationship["relationship"] == RELATIONSHIP_NEAR_EQUIVALENT
    assert relationship["same_payoff"] is False
    assert relationship["blocking_reasons"] == []
    assert "sports_team_alias_mismatch" not in pair["ineligibility_reasons"]


def test_nfl_afc_championship_vs_super_bowl_scope_mismatch(tmp_path: Path) -> None:
    poly, kalshi = _sports_snapshots(
        "Will Kansas City Chiefs win the 2026 AFC Championship?",
        "Will Kansas City Chiefs win the 2026 Super Bowl?",
        event_title="NFL futures",
        event_slug="nfl-futures",
        series_ticker="KXNFL",
    )

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "WATCH"
    assert "sports_competition_scope_mismatch" in pair["ineligibility_reasons"]
    assert "PAPER_CANDIDATE" not in json.dumps(payload)
    assert "POSSIBLE_ARB" not in json.dumps(payload)


def test_nhl_conference_finals_vs_stanley_cup_scope_mismatch(tmp_path: Path) -> None:
    poly, kalshi = _sports_snapshots(
        "Will Edmonton Oilers win the 2026 NHL Conference Finals?",
        "Will Edmonton Oilers win the 2026 Stanley Cup?",
        event_title="NHL futures",
        event_slug="nhl-futures",
        series_ticker="KXNHL",
    )

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "WATCH"
    assert "sports_competition_scope_mismatch" in pair["ineligibility_reasons"]


def test_singular_conference_final_vs_stanley_cup_scope_mismatch(tmp_path: Path) -> None:
    poly, kalshi = _sports_snapshots(
        "Will Carolina Hurricanes win the 2026 NHL Conference Final?",
        "Will Carolina Hurricanes win the 2026 Stanley Cup?",
        event_title="NHL futures",
        event_slug="nhl-futures",
        series_ticker="KXNHL",
    )

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "WATCH"
    assert "sports_competition_scope_mismatch" in pair["ineligibility_reasons"]


def test_nba_conference_finals_vs_nba_finals_scope_mismatch(tmp_path: Path) -> None:
    poly, kalshi = _sports_snapshots(
        "Will Boston Celtics win the 2026 NBA Eastern Conference Finals?",
        "Will Boston Celtics win the 2026 NBA Finals?",
        event_title="NBA futures",
        event_slug="nba-futures",
        series_ticker="KXNBA",
    )

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "WATCH"
    assert "sports_competition_scope_mismatch" in pair["ineligibility_reasons"]


def test_mlb_alds_vs_world_series_scope_mismatch(tmp_path: Path) -> None:
    poly, kalshi = _baseball_snapshots(
        "Will New York Yankees win the 2026 ALDS?",
        "Will New York Yankees win the 2026 World Series?",
    )

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "WATCH"
    assert "sports_competition_scope_mismatch" in pair["ineligibility_reasons"]


def test_premier_league_title_vs_champions_league_group_stage_scope_mismatch(tmp_path: Path) -> None:
    poly, kalshi = _sports_snapshots(
        "Will Arsenal win the 2026 Premier League title?",
        "Will Arsenal win the 2026 Champions League group stage?",
        event_title="Arsenal UEFA soccer futures",
        event_slug="uefa-soccer-futures",
        series_ticker="KXUEFA",
    )

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "WATCH"
    assert "sports_competition_scope_mismatch" in pair["ineligibility_reasons"]
    relationship = pair["contract_relationship"]
    assert relationship["relationship"] == RELATIONSHIP_SUBSET
    assert relationship["same_payoff"] is False
    assert "sports_competition_scope_mismatch" in relationship["blocking_reasons"]


def test_champions_league_round_of_16_vs_title_scope_mismatch(tmp_path: Path) -> None:
    poly, kalshi = _sports_snapshots(
        "Will Arsenal win the 2026 Champions League round of 16?",
        "Will Arsenal win the 2026 Champions League title?",
        event_title="Arsenal UEFA soccer futures",
        event_slug="uefa-soccer-futures",
        series_ticker="KXUEFA",
    )

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "WATCH"
    assert "sports_competition_scope_mismatch" in pair["ineligibility_reasons"]


def test_same_team_premier_league_title_future_can_match_normally(tmp_path: Path) -> None:
    poly, kalshi = _sports_snapshots(
        "Will Arsenal win the 2026 Premier League title?",
        "Will Arsenal win the 2026 Premier League title?",
        event_title="Arsenal UEFA soccer futures",
        event_slug="uefa-soccer-futures",
        series_ticker="KXUEFA",
    )

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "MANUAL_REVIEW"
    assert "sports_competition_scope_mismatch" not in pair["ineligibility_reasons"]
    relationship = pair["contract_relationship"]
    assert relationship["relationship"] == RELATIONSHIP_NEAR_EQUIVALENT
    assert relationship["same_payoff"] is False
    assert relationship["blocking_reasons"] == []


def test_same_team_super_bowl_future_can_match_normally(tmp_path: Path) -> None:
    poly, kalshi = _sports_snapshots(
        "Will Kansas City Chiefs win the 2026 Super Bowl?",
        "Will Kansas City Chiefs win the 2026 Super Bowl?",
        event_title="NFL futures",
        event_slug="nfl-futures",
        series_ticker="KXNFL",
    )

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "MANUAL_REVIEW"
    assert "sports_competition_scope_mismatch" not in pair["ineligibility_reasons"]


def test_same_team_alcs_future_can_match_normally(tmp_path: Path) -> None:
    poly, kalshi = _baseball_snapshots(
        "Will Tampa Bay Rays win the 2026 ALCS?",
        "Will Tampa Bay Rays win the 2026 American League Championship Series?",
    )

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "MANUAL_REVIEW"
    assert "sports_competition_scope_mismatch" not in pair["ineligibility_reasons"]


def test_ambiguous_wording_has_ambiguous_contract_relationship(tmp_path: Path) -> None:
    poly = _polymarket_snapshot("Will New York Knicks win game 1?")
    kalshi = _kalshi_snapshot("Will New York Knicks win game 2?")

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    pair = payload["pairs"][0]
    assert pair["action"] == "WATCH"
    assert "ambiguous_wording" in pair["ineligibility_reasons"]
    relationship = pair["contract_relationship"]
    assert relationship["relationship"] == RELATIONSHIP_AMBIGUOUS
    assert relationship["same_payoff"] is False
    assert relationship["manual_review_required"] is True
    assert relationship["blocking_reasons"] == ["ambiguous_wording"]


def test_weak_text_matches_do_not_become_candidate_pairs(tmp_path: Path) -> None:
    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", _polymarket_snapshot("Will it rain in Paris?")),
        _write(tmp_path / "kalshi.json", _kalshi_snapshot("Will the New York Knicks win?")),
        now=NOW,
    )

    assert payload["pair_count"] == 0


def test_disjoint_snapshots_still_produce_zero_pairs(tmp_path: Path) -> None:
    poly = _polymarket_snapshot("Will Bitcoin hit 100k by June?")
    poly["normalized_markets"][0]["event_title"] = "Bitcoin price milestones"
    poly["normalized_markets"][0]["raw"] = {"event_slug": "bitcoin-price"}
    kalshi = _kalshi_snapshot("Will the New York Knicks win?")
    kalshi["normalized_markets"][0]["event_title"] = "New York Knicks vs Cleveland Cavaliers"
    kalshi["normalized_markets"][0]["raw"] = {"series_ticker": "KXNBA"}

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 0


def test_close_settlement_time_bonus_can_surface_reasonable_question_match(tmp_path: Path) -> None:
    poly = _polymarket_snapshot("Will New York Knicks beat Cleveland")
    poly["normalized_markets"][0]["event_title"] = ""
    poly["normalized_markets"][0]["end_date"] = "2026-05-21T00:00:00+00:00"
    kalshi = _kalshi_snapshot("Knicks beat Cleveland Cavaliers")
    kalshi["normalized_markets"][0]["event_title"] = ""
    kalshi["normalized_markets"][0]["close_time"] = "2026-05-21T03:00:00+00:00"

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    fields = payload["pairs"][0]["matched_fields"]
    assert fields["question_similarity"] == 0.6667
    assert fields["settlement_time_delta_seconds"] == 10800.0
    assert fields["settlement_time_bonus"] > 0
    assert fields["final_similarity_score"] >= 0.68


def test_shared_event_keyword_bonus_can_surface_reasonable_question_match(tmp_path: Path) -> None:
    poly = _polymarket_snapshot("Will New York Knicks Cleveland")
    poly["normalized_markets"][0]["event_title"] = ""
    poly["normalized_markets"][0]["raw"] = {"event_slug": "nba-playoffs"}
    poly["normalized_markets"][0].pop("end_date")
    kalshi = _kalshi_snapshot("Will New York Knicks win game")
    kalshi["normalized_markets"][0]["event_title"] = ""
    kalshi["normalized_markets"][0]["raw"] = {"series_ticker": "KXNBA"}
    kalshi["normalized_markets"][0].pop("close_time")

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    fields = payload["pairs"][0]["matched_fields"]
    assert fields["question_similarity"] == 0.6667
    assert fields["shared_event_tokens"] == ["NBA"]
    assert fields["event_keyword_bonus"] > 0
    assert fields["settlement_time_bonus"] == 0


def test_event_keyword_tokens_avoid_compact_substrings_in_normal_text() -> None:
    assert "ETH" not in _event_keyword_tokens({"event_title": "weather markets", "raw": {"event_slug": "weather"}})
    assert "Fed" not in _event_keyword_tokens({"event_title": "federal budget vote", "raw": {"event_slug": "federal-budget"}})
    assert "IPO" not in _event_keyword_tokens({"event_title": "basketball tipoff time", "raw": {"event_slug": "tipoff-time"}})


def test_event_keyword_tokens_allow_compact_structured_tickers() -> None:
    assert "BTC" in _event_keyword_tokens({"event_title": "", "raw": {"series_ticker": "KXBTC"}})
    assert "NBA" in _event_keyword_tokens({"event_title": "", "raw": {"event_ticker": "KXNBA-26MAY20"}})


def test_shared_event_keyword_alone_with_weak_question_match_does_not_clear_bar(tmp_path: Path) -> None:
    poly = _polymarket_snapshot("Will Bitcoin hit 100k by June?")
    poly["normalized_markets"][0]["event_title"] = ""
    poly["normalized_markets"][0]["raw"] = {"event_slug": "nba-playoffs"}
    kalshi = _kalshi_snapshot("Will the New York Knicks win?")
    kalshi["normalized_markets"][0]["event_title"] = ""
    kalshi["normalized_markets"][0]["raw"] = {"series_ticker": "KXNBA"}

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 0


def test_bad_settlement_times_do_not_crash_or_get_time_bonus(tmp_path: Path) -> None:
    poly = _polymarket_snapshot()
    poly["normalized_markets"][0]["end_date"] = "not-a-date"
    kalshi = _kalshi_snapshot()
    kalshi["normalized_markets"][0]["close_time"] = "2026-05-21T00:00:00"

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    fields = payload["pairs"][0]["matched_fields"]
    assert fields["settlement_time_delta_seconds"] is None
    assert fields["settlement_time_bonus"] == 0
    assert fields["settlement_time_warning"] == "unparseable_or_naive_settlement_time"


def test_missing_settlement_time_warns_without_time_bonus(tmp_path: Path) -> None:
    poly = _polymarket_snapshot()
    poly["normalized_markets"][0].pop("end_date")
    kalshi = _kalshi_snapshot()

    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", poly),
        _write(tmp_path / "kalshi.json", kalshi),
        now=NOW,
    )

    assert payload["pair_count"] == 1
    fields = payload["pairs"][0]["matched_fields"]
    assert fields["settlement_time_delta_seconds"] is None
    assert fields["settlement_time_bonus"] == 0
    assert fields["settlement_time_warning"] == "missing_settlement_time"


def test_matcher_actions_remain_watch_or_manual_review_only(tmp_path: Path) -> None:
    payload = match_snapshot_files(
        _write(tmp_path / "poly.json", _polymarket_snapshot()),
        _write(tmp_path / "kalshi.json", _kalshi_snapshot()),
        now=NOW,
    )

    actions = {pair["action"] for pair in payload["pairs"]}
    assert actions <= {"WATCH", "MANUAL_REVIEW"}
    assert "PAPER" not in actions
    assert "PAPER_CANDIDATE" not in actions
    assert "POSSIBLE_ARB" not in actions


def test_cli_writes_live_snapshot_pairs(tmp_path: Path, capsys) -> None:
    poly_path = _write(tmp_path / "poly.json", _polymarket_snapshot())
    kalshi_path = _write(tmp_path / "kalshi.json", _kalshi_snapshot())
    output_path = tmp_path / "pairs.json"

    result = scan.main(
        [
            "match-live-snapshots",
            "--polymarket",
            str(poly_path),
            "--kalshi",
            str(kalshi_path),
            "--output",
            str(output_path),
            "--min-similarity",
            "0.68",
            "--max-snapshot-age-hours",
            "24",
        ]
    )

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["pair_count"] == 1
    assert payload["pairs"][0]["action"] in {"WATCH", "MANUAL_REVIEW"}
    assert "POSSIBLE_ARB" not in json.dumps(payload)
    assert "live_snapshot_match_status=OK pairs=1" in capsys.readouterr().out
