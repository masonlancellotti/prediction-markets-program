from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.sports_mlb_daily_residual_risk_scout import (
    ACTION_MANUAL_REVIEW,
    ACTION_RESIDUAL_REVIEW,
    ACTION_WATCH,
    B_LIVE_GAME_EXCLUDED,
    B_MISSING_DEPTH,
    B_MISSING_FEE_MODEL,
    B_NOT_MLB_DAILY_GAME_WINNER,
    B_RESIDUAL_NOT_ACCEPTED,
    B_SIZE_UNIT_REVIEW,
    B_STALE_QUOTE,
    B_UNSUPPORTED_SCOPE,
    RESIDUAL_RISK_TYPE,
    build_sports_mlb_daily_residual_risk_report,
    write_sports_mlb_daily_residual_risk_files,
)


NOW = datetime(2026, 5, 28, 5, 0, tzinfo=timezone.utc)


def test_without_residual_risk_acceptance_all_rows_blocked(tmp_path: Path) -> None:
    kalshi, polymarket = _write_evidence(tmp_path, [_kalshi_game()], [_polymarket_game()])

    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=polymarket,
        date="2026-05-28",
        generated_at=NOW,
    )

    assert report["matched_games"] == 1
    assert report["summary_counts"]["rows"] == 2
    assert all(B_RESIDUAL_NOT_ACCEPTED in row["blockers"] for row in report["rows"])
    assert all(row["action"] == ACTION_WATCH for row in report["rows"])


def test_acceptance_allows_normal_state_shadow_review_when_quotes_depth_and_fees_pass(tmp_path: Path) -> None:
    kalshi, polymarket = _write_evidence(tmp_path, [_kalshi_game()], [_polymarket_game()])

    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=polymarket,
        date="2026-05-28",
        accept_mlb_daily_contingency_risk=True,
        generated_at=NOW,
    )

    review_rows = [row for row in report["rows"] if row["action"] == ACTION_RESIDUAL_REVIEW]
    assert review_rows
    row = review_rows[0]
    assert row["direction"] == "A"
    assert row["gross_edge"] > 0
    assert row["net_edge"] > 0
    assert row["net_edge_status"] == "OK"
    assert row["human_accepted_residual_risk"] is True
    assert row["residual_risk_type"] == RESIDUAL_RISK_TYPE
    assert row["strict_exact_arb"] is False
    assert row["exact_ready"] is False
    assert row["paper_candidate"] is False
    assert report["summary_counts"]["residual_review_rows"] == 1


def test_outputs_never_emit_forbidden_paper_candidate_literal(tmp_path: Path) -> None:
    kalshi, polymarket = _write_evidence(tmp_path, [_kalshi_game()], [_polymarket_game()])
    json_output = tmp_path / "out.json"
    md_output = tmp_path / "out.md"

    report = write_sports_mlb_daily_residual_risk_files(
        kalshi_evidence=kalshi,
        polymarket_evidence=polymarket,
        date="2026-05-28",
        accept_mlb_daily_contingency_risk=True,
        include_live_games=False,
        json_output=json_output,
        markdown_output=md_output,
        generated_at=NOW,
    )

    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in json_output.read_text(encoding="utf-8")
    assert forbidden not in md_output.read_text(encoding="utf-8")
    assert report["paper_candidate_emitted"] is False
    assert report["summary_counts"]["exact_ready_rows"] == 0
    assert report["summary_counts"]["paper_candidate_rows"] == 0
    assert all(row["exact_ready"] is False for row in report["rows"])
    assert all(row["paper_candidate"] is False for row in report["rows"])


def test_live_game_excluded_by_default(tmp_path: Path) -> None:
    kalshi, polymarket = _write_evidence(
        tmp_path,
        [_kalshi_game(status="LIVE - Top 3rd")],
        [_polymarket_game(status="LIVE (in progress)")],
    )

    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=polymarket,
        date="2026-05-28",
        accept_mlb_daily_contingency_risk=True,
        generated_at=NOW,
    )

    assert all(B_LIVE_GAME_EXCLUDED in row["blockers"] for row in report["rows"])
    assert all(row["action"] == ACTION_WATCH for row in report["rows"])
    assert report["summary_counts"]["live_game_rows"] == 2


def test_live_game_only_proceeds_when_include_live_games_is_present(tmp_path: Path) -> None:
    kalshi, polymarket = _write_evidence(
        tmp_path,
        [_kalshi_game(status="LIVE - Top 3rd")],
        [_polymarket_game(status="LIVE (in progress)")],
    )

    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=polymarket,
        date="2026-05-28",
        accept_mlb_daily_contingency_risk=True,
        include_live_games=True,
        generated_at=NOW,
    )

    assert any(row["action"] == ACTION_RESIDUAL_REVIEW for row in report["rows"])
    assert all(B_LIVE_GAME_EXCLUDED not in row["blockers"] for row in report["rows"])


def test_missing_fee_model_prevents_reviewable_net_edge(tmp_path: Path) -> None:
    kalshi, polymarket = _write_evidence(tmp_path, [_kalshi_game()], [_polymarket_game()])

    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=polymarket,
        date="2026-05-28",
        accept_mlb_daily_contingency_risk=True,
        generated_at=NOW,
        fee_models_available=False,
    )

    positive_gross = next(row for row in report["rows"] if row["gross_edge"] and row["gross_edge"] > 0)
    assert positive_gross["net_edge_status"] == "FEE_REVIEW_REQUIRED"
    assert positive_gross["net_edge"] is None
    assert B_MISSING_FEE_MODEL in positive_gross["blockers"]
    assert positive_gross["action"] == ACTION_MANUAL_REVIEW
    assert report["summary_counts"]["residual_review_rows"] == 0


def test_quote_age_above_default_blocks_review(tmp_path: Path) -> None:
    kalshi, polymarket = _write_evidence(
        tmp_path,
        [_kalshi_game(quote_timestamp="2026-05-28T04:00:00Z")],
        [_polymarket_game(quote_timestamp="2026-05-28T04:00:00Z")],
    )

    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=polymarket,
        date="2026-05-28",
        accept_mlb_daily_contingency_risk=True,
        generated_at=NOW,
    )

    positive_gross = next(row for row in report["rows"] if row["gross_edge"] and row["gross_edge"] > 0)
    assert B_STALE_QUOTE in positive_gross["blockers"]
    assert positive_gross["action"] == ACTION_WATCH
    assert report["summary_counts"]["residual_review_rows"] == 0


def test_missing_or_uncertain_size_units_blocks_shadow_review(tmp_path: Path) -> None:
    kalshi, polymarket = _write_evidence(
        tmp_path,
        [_kalshi_game()],
        [_polymarket_game(size_unit=None)],
    )

    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=polymarket,
        date="2026-05-28",
        accept_mlb_daily_contingency_risk=True,
        generated_at=NOW,
    )

    positive_gross = next(row for row in report["rows"] if row["gross_edge"] and row["gross_edge"] > 0)
    assert B_SIZE_UNIT_REVIEW in positive_gross["blockers"]
    assert positive_gross["action"] == ACTION_MANUAL_REVIEW
    assert report["summary_counts"]["residual_review_rows"] == 0


def test_missing_quote_depth_prevents_reviewable_row(tmp_path: Path) -> None:
    kalshi, polymarket = _write_evidence(
        tmp_path,
        [_kalshi_game()],
        [_polymarket_game(laa_ask_size=None)],
    )

    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=polymarket,
        date="2026-05-28",
        accept_mlb_daily_contingency_risk=True,
        generated_at=NOW,
    )

    affected = next(row for row in report["rows"] if row["direction"] == "A")
    assert B_MISSING_DEPTH in affected["blockers"]
    assert affected["available_size"] is None
    assert affected["action"] == ACTION_WATCH
    assert report["summary_counts"]["residual_review_rows"] == 0


def test_team_matching_handles_laa_det_and_hou_tex_aliases(tmp_path: Path) -> None:
    kalshi, polymarket = _write_evidence(
        tmp_path,
        [_kalshi_game(), _kalshi_game(key="MLB-2026-05-28-HOU-TEX", team_a="Houston Astros", team_b="Texas Rangers")],
        [_polymarket_game(), _polymarket_game(key="MLB-2026-05-28-HOU-TEX", team_a="HOU", team_b="TEX")],
    )

    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=polymarket,
        date="2026-05-28",
        accept_mlb_daily_contingency_risk=True,
        generated_at=NOW,
    )

    assert report["matched_games"] == 2
    assert report["summary_counts"]["rows"] == 4
    assert not report["unmatched_game_keys"]
    assert {row["cross_platform_game_key"] for row in report["rows"]} == {
        "MLB-2026-05-28-LAA-DET",
        "MLB-2026-05-28-HOU-TEX",
    }


def test_valid_two_team_mlb_daily_game_passes_market_scope_gate(tmp_path: Path) -> None:
    kalshi, polymarket = _write_evidence(tmp_path, [_kalshi_game()], [_polymarket_game()])

    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=polymarket,
        date="2026-05-28",
        accept_mlb_daily_contingency_risk=True,
        generated_at=NOW,
    )

    assert all(B_NOT_MLB_DAILY_GAME_WINNER not in row["blockers"] for row in report["rows"])
    assert all(B_UNSUPPORTED_SCOPE not in row["blockers"] for row in report["rows"])


def test_mlb_world_series_futures_input_is_blocked_without_edge_math(tmp_path: Path) -> None:
    teams = [f"Team {index}" for index in range(30)]
    kalshi, polymarket = _write_evidence(
        tmp_path,
        [_kalshi_game(market_type="championship_futures", outcomes=teams)],
        [_polymarket_game(market_type="championship_futures", outcomes=teams)],
    )

    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=polymarket,
        date="2026-05-28",
        accept_mlb_daily_contingency_risk=True,
        generated_at=NOW,
    )

    assert report["summary_counts"]["residual_review_rows"] == 0
    assert report["rows"][0]["action"] == "IGNORE_BLOCKED"
    assert report["rows"][0]["gross_edge"] is None
    assert {B_NOT_MLB_DAILY_GAME_WINNER, B_UNSUPPORTED_SCOPE} & set(report["rows"][0]["blockers"])


def test_non_mlb_evidence_is_blocked(tmp_path: Path) -> None:
    kalshi_path, polymarket_path = _write_evidence(tmp_path, [_kalshi_game()], [_polymarket_game()])
    for path in (kalshi_path, polymarket_path):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["league"] = "NBA"
        for game in payload["games"]:
            game["league"] = "NBA"
        path.write_text(json.dumps(payload), encoding="utf-8")

    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi_path,
        polymarket_evidence=polymarket_path,
        date="2026-05-28",
        accept_mlb_daily_contingency_risk=True,
        generated_at=NOW,
    )

    assert report["summary_counts"]["residual_review_rows"] == 0
    assert report["rows"][0]["action"] == "IGNORE_BLOCKED"
    assert B_NOT_MLB_DAILY_GAME_WINNER in report["rows"][0]["blockers"]


def test_spread_total_or_player_prop_market_type_is_blocked(tmp_path: Path) -> None:
    for market_type in ("spread", "total", "player_prop"):
        kalshi, polymarket = _write_evidence(
            tmp_path,
            [_kalshi_game(market_type=market_type)],
            [_polymarket_game(market_type=market_type)],
        )
        report = build_sports_mlb_daily_residual_risk_report(
            kalshi_evidence=kalshi,
            polymarket_evidence=polymarket,
            date="2026-05-28",
            accept_mlb_daily_contingency_risk=True,
            generated_at=NOW,
        )
        assert report["rows"][0]["action"] == "IGNORE_BLOCKED"
        assert B_NOT_MLB_DAILY_GAME_WINNER in report["rows"][0]["blockers"]


def test_three_outcome_or_extra_outcome_game_is_blocked(tmp_path: Path) -> None:
    kalshi, polymarket = _write_evidence(
        tmp_path,
        [_kalshi_game(outcomes=["Detroit Tigers", "Los Angeles Angels", "Draw"])],
        [_polymarket_game(outcomes=["Los Angeles Angels", "Detroit Tigers", "Draw"])],
    )

    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=polymarket,
        date="2026-05-28",
        accept_mlb_daily_contingency_risk=True,
        generated_at=NOW,
    )

    assert report["rows"][0]["action"] == "IGNORE_BLOCKED"
    assert B_NOT_MLB_DAILY_GAME_WINNER in report["rows"][0]["blockers"]


def test_outcome_teams_not_equal_home_away_are_blocked(tmp_path: Path) -> None:
    kalshi, polymarket = _write_evidence(
        tmp_path,
        [_kalshi_game()],
        [_polymarket_game(outcomes=["Los Angeles Angels", "Houston Astros"])],
    )

    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=polymarket,
        date="2026-05-28",
        accept_mlb_daily_contingency_risk=True,
        generated_at=NOW,
    )

    assert report["rows"][0]["action"] == "IGNORE_BLOCKED"
    assert B_NOT_MLB_DAILY_GAME_WINNER in report["rows"][0]["blockers"]


def test_residual_cancellation_and_postponement_mismatch_notes_are_retained(tmp_path: Path) -> None:
    kalshi, polymarket = _write_evidence(tmp_path, [_kalshi_game()], [_polymarket_game()])

    report = build_sports_mlb_daily_residual_risk_report(
        kalshi_evidence=kalshi,
        polymarket_evidence=polymarket,
        date="2026-05-28",
        accept_mlb_daily_contingency_risk=True,
        generated_at=NOW,
    )

    notes = " ".join(report["rows"][0]["residual_risk_notes"])
    assert "last fair market price" in notes
    assert "50-50" in notes
    assert "shortened/suspended/extra-innings" in notes


def test_cli_writes_residual_risk_scout_outputs(tmp_path: Path, capsys) -> None:
    kalshi, polymarket = _write_evidence(tmp_path, [_kalshi_game()], [_polymarket_game()])
    json_output = tmp_path / "cli.json"
    md_output = tmp_path / "cli.md"

    result = scan.main(
        [
            "sports-mlb-daily-residual-risk-scout",
            "--kalshi-evidence",
            str(kalshi),
            "--polymarket-evidence",
            str(polymarket),
            "--date",
            "2026-05-28",
            "--accept-mlb-daily-contingency-risk",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(md_output),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "sports_mlb_daily_residual_risk_scout_status=OK" in stdout
    assert "shadow_paper_only=true" in stdout
    assert "exact_ready_rows=0" in stdout
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "sports_mlb_daily_residual_risk_scout_v1"
    assert payload["paper_candidate_emitted"] is False


def _write_evidence(tmp_path: Path, kalshi_games: list[dict], polymarket_games: list[dict]) -> tuple[Path, Path]:
    kalshi_path = tmp_path / "kalshi.json"
    polymarket_path = tmp_path / "polymarket.json"
    kalshi_path.write_text(
        json.dumps({"platform": "Kalshi", "league": "MLB", "games": kalshi_games}),
        encoding="utf-8",
    )
    polymarket_path.write_text(
        json.dumps({"platform": "Polymarket", "league": "MLB", "games": polymarket_games}),
        encoding="utf-8",
    )
    return kalshi_path, polymarket_path


def _kalshi_game(
    *,
    key: str = "MLB-2026-05-28-LAA-DET",
    team_a: str = "Detroit Tigers",
    team_b: str = "Los Angeles Angels",
    status: str = "Pre-game",
    quote_timestamp: str = "2026-05-28T04:55:00Z",
    market_type: str = "game_winner",
    outcomes: list[str] | None = None,
) -> dict:
    outcome_names = outcomes or [team_a, team_b]
    return {
        "platform": "Kalshi",
        "cross_platform_game_key": key,
        "league": "MLB",
        "game_date": "2026-05-28",
        "teams": f"{team_b} vs. {team_a}",
        "home_team": team_a,
        "away_team": team_b,
        "market_type": market_type,
        "ids": {
            "market_tickers": {
                team_a: f"KXMLBGAME-{key}-{team_a}",
                team_b: f"KXMLBGAME-{key}-{team_b}",
            }
        },
        "postponement_rules": "If not started within 48h, resolves at last fair market price.",
        "cancellation_rules": "If canceled and not started within 48h, resolves at last fair market price.",
        "suspended_or_shortened_game_rules": "If suspended and not resumed within 48h, last fair market price.",
        "extra_innings_rules": "All extra innings included.",
        "quotes": {
            "game_status_at_fetch": status,
            "quote_timestamp_utc": quote_timestamp,
            "outcomes": [
                {
                    "team": team,
                    "market_ticker": f"KXMLBGAME-{key}-{team}",
                    "yes_ask": 0.40 if index == 0 else 0.58,
                    "yes_ask_size_dollars": 100.0,
                }
                for index, team in enumerate(outcome_names)
            ],
        },
    }


def _polymarket_game(
    *,
    key: str = "MLB-2026-05-28-LAA-DET",
    team_a: str = "Los Angeles Angels",
    team_b: str = "Detroit Tigers",
    status: str = "Pre-game",
    laa_ask_size: float | None = 100.0,
    quote_timestamp: str = "2026-05-28T04:55:00Z",
    market_type: str = "game_winner",
    outcomes: list[str] | None = None,
    size_unit: str | None = "notional_usd",
) -> dict:
    outcome_names = outcomes or [team_a, team_b]
    return {
        "platform": "Polymarket",
        "cross_platform_game_key": key,
        "league": "MLB",
        "game_date": "2026-05-28",
        "teams": f"{team_a} vs. {team_b}",
        "home_team": team_b,
        "away_team": team_a,
        "market_type": market_type,
        "ids": {
            "market_id": f"pm-{key}",
            "token_ids": {
                team_a: f"token-{team_a}",
                team_b: f"token-{team_b}",
            },
        },
        "postponement_rules": "If postponed, market remains open until completed.",
        "cancellation_rules": "If canceled entirely with no make-up game, resolves 50-50.",
        "suspended_or_shortened_game_rules": "Not explicitly stated in rules text.",
        "extra_innings_rules": "Not explicitly stated; winner in any number of innings counts.",
        "quotes": {
            "market_status_at_fetch": status,
            "quote_timestamp_iso": quote_timestamp,
            "outcomes": [
                {
                    "team": team,
                    "ask": "0.50" if index == 0 else "0.42",
                    "ask_size": None if index == 0 and laa_ask_size is None else "100.0",
                    **({"ask_size_unit": size_unit} if size_unit is not None else {}),
                }
                for index, team in enumerate(outcome_names)
            ],
        },
    }
