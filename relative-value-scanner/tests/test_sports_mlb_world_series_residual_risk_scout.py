from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.sports_mlb_world_series_residual_risk_scout import (
    ACTION_OPERATOR_REVIEW,
    ACTION_RESIDUAL_REVIEW,
    B_INSUFFICIENT_DEPTH,
    B_MISSING_FEE,
    B_REMOTE_NOT_ACCEPTED,
    B_STALE_QUOTE,
    B_UNCLEAR_KALSHI_SIZE_UNITS,
    build_sports_mlb_world_series_residual_risk_report,
    write_sports_mlb_world_series_residual_risk_files,
)


NOW = datetime(2026, 5, 28, 7, 10, tzinfo=timezone.utc)


def test_scope_validation_accepts_valid_mlb_world_series_championship_futures(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)

    report = _build(kalshi, poly)

    assert report["scope_validation"]["valid"] is True
    assert report["matched_team_rows"] == 1
    assert report["summary_counts"]["rows"] == 2


def test_scope_validation_rejects_daily_games(tmp_path: Path) -> None:
    kalshi = tmp_path / "kalshi.json"
    poly = tmp_path / "poly.json"
    kalshi.write_text(json.dumps({"platform": "Kalshi", "league": "MLB", "season": "2026", "games": []}), encoding="utf-8")
    poly.write_text(json.dumps(_poly_payload()), encoding="utf-8")

    report = _build(kalshi, poly)

    assert report["scope_validation"]["valid"] is False
    assert report["rows"][0]["action"] == "IGNORE_BLOCKED"


def test_scope_validation_rejects_non_mlb(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path, kalshi_overrides={"league": "NBA"})

    report = _build(kalshi, poly)

    assert report["scope_validation"]["valid"] is False
    assert report["rows"][0]["action"] == "IGNORE_BLOCKED"


def test_scope_validation_rejects_malformed_non_championship_evidence(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path, kalshi_overrides={"batch": "daily_games"})

    report = _build(kalshi, poly)

    assert report["scope_validation"]["valid"] is False
    assert report["rows"][0]["action"] == "IGNORE_BLOCKED"


def test_team_matching_works_for_required_aliases(tmp_path: Path) -> None:
    kalshi_rows = [
        _kalshi_outcome("LAD", "Los Angeles D", "KXMLB-26-LAD"),
        _kalshi_outcome("NYY", "Yankees", "KXMLB-26-NYY"),
        _kalshi_outcome("ATL", "Braves", "KXMLB-26-ATL"),
        _kalshi_outcome("TOR", "Blue Jays", "KXMLB-26-TOR"),
        _kalshi_outcome("CWS", "White Sox", "KXMLB-26-CWS"),
        _kalshi_outcome("ATH", "Athletics", "KXMLB-26-ATH"),
        _kalshi_outcome("LAA", "Angels", "KXMLB-26-LAA"),
    ]
    poly_rows = [
        _poly_outcome("Los Angeles Dodgers", "m-lad", "yes-lad", "no-lad", aliases=["Dodgers"]),
        _poly_outcome("New York Yankees", "m-nyy", "yes-nyy", "no-nyy", aliases=["NYY"]),
        _poly_outcome("Atlanta Braves", "m-atl", "yes-atl", "no-atl", aliases=["ATL"]),
        _poly_outcome("Toronto Blue Jays", "m-tor", "yes-tor", "no-tor", aliases=["TOR"]),
        _poly_outcome("Chicago White Sox", "m-cws", "yes-cws", "no-cws", aliases=["CWS"]),
        _poly_outcome("Oakland A's", "m-ath", "yes-ath", "no-ath", aliases=["A's"]),
        _poly_outcome("Los Angeles Angels", "m-laa", "yes-laa", "no-laa", aliases=["LAA"]),
    ]
    kalshi, poly = _write_evidence(tmp_path, kalshi_outcomes=kalshi_rows, poly_outcomes=poly_rows)

    report = _build(kalshi, poly)

    assert report["matched_team_rows"] == 7
    assert {row["canonical_team_key"] for row in report["rows"]} == {"LAD", "NYY", "ATL", "TOR", "CWS", "ATH", "LAA"}


def test_direction_a_uses_kalshi_yes_ask_plus_polymarket_no_ask_for_gross_edge(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)

    report = _build(kalshi, poly)
    row = _row(report, "KALSHI_YES_POLYMARKET_NO")

    assert row["kalshi_price"] == 0.4
    assert row["polymarket_price"] == 0.55
    assert row["gross_edge"] == 0.05


def test_direction_b_uses_polymarket_yes_ask_plus_kalshi_no_ask_for_gross_edge(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)

    report = _build(kalshi, poly)
    row = _row(report, "POLYMARKET_YES_KALSHI_NO")

    assert row["kalshi_price"] == 0.55
    assert row["polymarket_price"] == 0.35
    assert row["gross_edge"] == 0.1


def test_direction_a_current_exit_pair_value_uses_kalshi_yes_bid_plus_polymarket_no_bid(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)

    report = _build(kalshi, poly)
    row = _row(report, "KALSHI_YES_POLYMARKET_NO")

    assert row["current_exit_pair_value"] == 0.83
    assert row["current_exit_pair_value_status"] == "available"
    assert row["exit_bid_legs"][0]["platform"] == "Kalshi"
    assert row["exit_bid_legs"][0]["side"] == "YES"
    assert row["exit_bid_legs"][0]["bid"] == 0.39
    assert row["exit_bid_legs"][1]["platform"] == "Polymarket"
    assert row["exit_bid_legs"][1]["side"] == "NO"
    assert row["exit_bid_legs"][1]["bid"] == 0.44


def test_direction_b_current_exit_pair_value_uses_polymarket_yes_bid_plus_kalshi_no_bid(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)

    report = _build(kalshi, poly)
    row = _row(report, "POLYMARKET_YES_KALSHI_NO")

    assert row["current_exit_pair_value"] == 0.78
    assert row["current_exit_pair_value_status"] == "available"
    assert row["exit_bid_legs"][0]["platform"] == "Kalshi"
    assert row["exit_bid_legs"][0]["side"] == "NO"
    assert row["exit_bid_legs"][0]["bid"] == 0.44
    assert row["exit_bid_legs"][1]["platform"] == "Polymarket"
    assert row["exit_bid_legs"][1]["side"] == "YES"
    assert row["exit_bid_legs"][1]["bid"] == 0.34


def test_missing_exit_bid_is_partial_and_not_guessed(tmp_path: Path) -> None:
    kalshi = _kalshi_payload(outcomes=[_kalshi_outcome("LAD", "Los Angeles Dodgers", "KXMLB-26-LAD", yes_bid=None)])
    kalshi_path, poly_path = _write_raw(tmp_path, kalshi, _poly_payload())

    report = _build(kalshi_path, poly_path)
    row = _row(report, "KALSHI_YES_POLYMARKET_NO")

    assert row["current_exit_pair_value"] is None
    assert row["current_exit_pair_value_status"] == "partial"
    assert "missing_kalshi_exit_bid" in row["exit_data_blockers"]


def test_midpoint_or_bid_is_not_used_for_gross_edge(tmp_path: Path) -> None:
    kalshi = _kalshi_payload(outcomes=[_kalshi_outcome("LAD", "Los Angeles Dodgers", "KXMLB-26-LAD", yes_ask="0.41")])
    poly = _poly_payload(outcomes=[_poly_outcome("Los Angeles Dodgers", "m-lad", "yes-lad", "no-lad", no_ask="0.56")])
    kalshi_path, poly_path = _write_raw(tmp_path, kalshi, poly)

    report = _build(kalshi_path, poly_path)
    row = _row(report, "KALSHI_YES_POLYMARKET_NO")

    assert row["gross_edge"] == 0.03


def test_midpoint_or_ask_is_not_used_for_exit_value(tmp_path: Path) -> None:
    kalshi = _kalshi_payload(
        outcomes=[
            _kalshi_outcome(
                "LAD",
                "Los Angeles Dodgers",
                "KXMLB-26-LAD",
                yes_bid="0.10",
                yes_ask="0.41",
            )
        ]
    )
    poly = _poly_payload(outcomes=[_poly_outcome("Los Angeles Dodgers", "m-lad", "yes-lad", "no-lad", no_bid="0.20", no_ask="0.56")])
    kalshi_path, poly_path = _write_raw(tmp_path, kalshi, poly)

    report = _build(kalshi_path, poly_path)
    row = _row(report, "KALSHI_YES_POLYMARKET_NO")

    assert row["current_exit_pair_value"] == 0.3


def test_missing_no_quote_blocks_row(tmp_path: Path) -> None:
    poly = _poly_payload(outcomes=[_poly_outcome("Los Angeles Dodgers", "m-lad", "yes-lad", "no-lad", no_ask=None)])
    kalshi, poly_path = _write_raw(tmp_path, _kalshi_payload(), poly)

    report = _build(kalshi, poly_path, operator=True)
    row = _row(report, "KALSHI_YES_POLYMARKET_NO")

    assert "missing_polymarket_no_quote" in row["blockers"]
    assert row["action"] == "WATCH"


def test_missing_or_unclear_size_units_block_positive_action(tmp_path: Path) -> None:
    kalshi = _kalshi_payload(outcomes=[_kalshi_outcome("LAD", "Los Angeles Dodgers", "KXMLB-26-LAD", include_unit=False)])
    kalshi_path, poly_path = _write_raw(tmp_path, kalshi, _poly_payload())

    report = _build(kalshi_path, poly_path, operator=True)
    row = _row(report, "POLYMARKET_YES_KALSHI_NO")

    assert B_UNCLEAR_KALSHI_SIZE_UNITS in row["blockers"]
    assert row["size_unit_status"] == "blocked_unclear"
    assert row["size_gate_passed"] is False
    assert row["action"] != ACTION_RESIDUAL_REVIEW
    assert row["action"] != ACTION_OPERATOR_REVIEW


def test_polymarket_share_size_converts_to_notional_as_price_times_size(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)

    report = _build(kalshi, poly, operator=True)
    row = _row(report, "POLYMARKET_YES_KALSHI_NO")

    assert row["polymarket_ask"] == 0.35
    assert row["polymarket_leg_notional"] == 35.0
    assert row["polymarket_size_units"] == "token_or_share_quantity"
    assert row["polymarket_size_unit_interpretation"] == "token_or_share_quantity_converted_to_notional_as_ask_price_times_size"


def test_kalshi_orderbook_size_converts_to_notional_as_price_times_size(tmp_path: Path) -> None:
    kalshi = _kalshi_payload(
        outcomes=[
            _kalshi_outcome(
                "LAD",
                "Los Angeles Dodgers",
                "KXMLB-26-LAD",
                include_unit=False,
                quote_source="Kalshi public CLOB API orderbook",
            )
        ]
    )
    kalshi_path, poly_path = _write_raw(tmp_path, kalshi, _poly_payload())

    report = _build(kalshi_path, poly_path, operator=True)
    row = _row(report, "KALSHI_YES_POLYMARKET_NO")

    assert row["kalshi_ask"] == 0.4
    assert row["kalshi_leg_notional"] == 20.0
    assert row["kalshi_size_units"] == "orderbook_contract_quantity"
    assert row["kalshi_size_unit_interpretation"] == "raw_orderbook_size_converted_to_notional_as_ask_price_times_size"
    assert row["size_unit_status"] == "normalized"


def test_available_notional_is_minimum_of_both_leg_notionals(tmp_path: Path) -> None:
    kalshi = _kalshi_payload(
        outcomes=[
            _kalshi_outcome(
                "LAD",
                "Los Angeles Dodgers",
                "KXMLB-26-LAD",
                include_unit=False,
                quote_source="Kalshi public CLOB API orderbook",
            )
        ]
    )
    kalshi_path, poly_path = _write_raw(tmp_path, kalshi, _poly_payload())

    report = _build(kalshi_path, poly_path, operator=True)
    row = _row(report, "POLYMARKET_YES_KALSHI_NO")

    assert row["kalshi_leg_notional"] == 27.5
    assert row["polymarket_leg_notional"] == 35.0
    assert row["available_notional"] == 27.5
    assert row["size_gate_passed"] is True


def test_legacy_size_unit_review_blocker_removed_when_units_normalize(tmp_path: Path) -> None:
    kalshi = _kalshi_payload(
        outcomes=[
            _kalshi_outcome(
                "LAD",
                "Los Angeles Dodgers",
                "KXMLB-26-LAD",
                include_unit=False,
                quote_source="Kalshi public CLOB API orderbook",
            )
        ]
    )
    kalshi_path, poly_path = _write_raw(tmp_path, kalshi, _poly_payload())

    report = _build(kalshi_path, poly_path, operator=True)
    row = _row(report, "POLYMARKET_YES_KALSHI_NO")

    assert "quote_size_unit_review_required" not in row["blockers"]
    assert B_UNCLEAR_KALSHI_SIZE_UNITS not in row["blockers"]
    assert row["size_gate_passed"] is True


def test_insufficient_available_notional_blocks_operator_review(tmp_path: Path) -> None:
    kalshi = _kalshi_payload(
        outcomes=[
            _kalshi_outcome(
                "LAD",
                "Los Angeles Dodgers",
                "KXMLB-26-LAD",
                yes_ask_size="5",
                no_ask_size="5",
            )
        ]
    )
    poly = _poly_payload(
        outcomes=[
            _poly_outcome(
                "Los Angeles Dodgers",
                "m-lad",
                "yes-lad",
                "no-lad",
                yes_ask_size="10",
                no_ask_size="10",
            )
        ]
    )
    kalshi_path, poly_path = _write_raw(tmp_path, kalshi, poly)

    report = _build(kalshi_path, poly_path, operator=True)
    row = _row(report, "POLYMARKET_YES_KALSHI_NO")

    assert B_INSUFFICIENT_DEPTH in row["blockers"]
    assert row["size_gate_passed"] is False
    assert row["action"] != ACTION_OPERATOR_REVIEW


def test_stale_or_missing_quote_blocks_positive_action(tmp_path: Path) -> None:
    kalshi = _kalshi_payload(outcomes=[_kalshi_outcome("LAD", "Los Angeles Dodgers", "KXMLB-26-LAD", quote_timestamp="2026-05-28T01:00:00Z")])
    kalshi_path, poly_path = _write_raw(tmp_path, kalshi, _poly_payload())

    report = _build(kalshi_path, poly_path, operator=True)
    row = _row(report, "POLYMARKET_YES_KALSHI_NO")

    assert B_STALE_QUOTE in row["blockers"]
    assert row["action"] != ACTION_RESIDUAL_REVIEW
    assert row["action"] != ACTION_OPERATOR_REVIEW


def test_missing_fee_model_blocks_residual_shadow_review(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)

    report = _build(kalshi, poly, operator=True, fee_models_available=False)
    row = _row(report, "POLYMARKET_YES_KALSHI_NO")

    assert B_MISSING_FEE in row["blockers"]
    assert row["action"] == "MANUAL_REVIEW"
    assert row["action"] != ACTION_OPERATOR_REVIEW


def test_without_remote_tail_risk_acceptance_all_rows_are_blocked(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)

    report = _build(kalshi, poly, accept=False, operator=True)

    assert all(B_REMOTE_NOT_ACCEPTED in row["blockers"] for row in report["rows"])
    assert report["operator_arb_mode"] is False
    assert report["summary_counts"]["residual_review_rows"] == 0
    assert report["summary_counts"]["operator_arb_review_rows"] == 0


def test_with_flag_row_can_become_residual_shadow_review_when_all_gates_pass(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)

    report = _build(kalshi, poly)
    row = _row(report, "POLYMARKET_YES_KALSHI_NO")

    assert row["action"] == ACTION_RESIDUAL_REVIEW
    assert report["summary_counts"]["operator_arb_review_rows"] == 0
    assert row["net_edge"] and row["net_edge"] > 0
    assert row["exact_ready"] is False
    assert row["paper_candidate"] is False


def test_with_tail_risk_flag_only_never_emits_operator_arb_review(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)

    report = _build(kalshi, poly, operator=False)

    assert report["operator_arb_mode"] is False
    assert report["operator_accepted_as_arb"] is False
    assert report["summary_counts"]["operator_arb_review_rows"] == 0
    assert all(row["action"] != ACTION_OPERATOR_REVIEW for row in report["rows"])


def test_with_operator_flag_and_positive_net_edge_can_reach_operator_review(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)

    report = _build(kalshi, poly, operator=True)
    row = _row(report, "POLYMARKET_YES_KALSHI_NO")

    assert report["operator_arb_mode"] is True
    assert row["action"] == ACTION_OPERATOR_REVIEW
    assert row["operator_paper_review"] is True
    assert row["operator_accepted_as_arb"] is True
    assert row["net_edge"] and row["net_edge"] > 0
    assert row["mathematical_strict_exact_arb"] is False
    assert row["strict_exact_arb"] is False
    assert row["exact_ready"] is False
    assert row["standard_paper_candidate"] is False
    assert report["summary_counts"]["operator_arb_review_rows"] >= 1


def test_exact_and_paper_flags_remain_false(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)

    report = _build(kalshi, poly)

    assert report["exact_ready_rows"] == 0
    assert report["paper_candidate_rows"] == 0
    assert report["standard_paper_candidate_rows"] == 0
    assert report["global_paper_candidate_emitted"] is False
    assert report["mathematical_strict_exact_arb"] is False
    assert report["paper_candidate_emitted"] is False
    assert all(
        row["exact_ready"] is False
        and row["paper_candidate"] is False
        and row["standard_paper_candidate"] is False
        and row["mathematical_strict_exact_arb"] is False
        for row in report["rows"]
    )


def test_no_forbidden_upper_candidate_literal_in_outputs(tmp_path: Path) -> None:
    kalshi, poly = _write_evidence(tmp_path)
    report = write_sports_mlb_world_series_residual_risk_files(
        kalshi_evidence=kalshi,
        polymarket_evidence=poly,
        season=2026,
        accept_world_series_remote_tail_risk=True,
        operator_accepted_as_arb=True,
        max_quote_age_seconds=3600,
        min_available_notional=10,
        json_output=tmp_path / "report.json",
        markdown_output=tmp_path / "report.md",
        generated_at=NOW,
    )

    combined = (tmp_path / "report.json").read_text(encoding="utf-8") + (tmp_path / "report.md").read_text(encoding="utf-8")
    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in combined
    assert report["global_paper_candidate_emitted"] is False
    assert report["standard_paper_candidate_rows"] == 0
    assert report["paper_candidate_emitted"] is False


def test_cli_writes_residual_risk_scout_report_with_fake_writer(tmp_path: Path, monkeypatch, capsys) -> None:
    def fake_writer(**kwargs):
        report = {
            "summary_counts": {
                "rows": 2,
                "operator_arb_review_rows": 0,
                "residual_review_rows": 0,
                "manual_review_rows": 1,
                "watch_rows": 1,
                "ignore_blocked_rows": 0,
                "positive_gross_rows": 1,
                "positive_net_rows": 0,
            },
            "top_blockers": [{"blocker": "quote_size_unit_review_required", "count": 1}],
            "human_accepted_remote_tail_risk": kwargs["accept_world_series_remote_tail_risk"],
            "operator_accepted_as_arb": kwargs["operator_accepted_as_arb"],
            "operator_arb_mode": kwargs["accept_world_series_remote_tail_risk"] and kwargs["operator_accepted_as_arb"],
            "kalshi_rows_loaded": 1,
            "polymarket_rows_loaded": 1,
            "matched_team_rows": 1,
        }
        kwargs["json_output"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["json_output"].write_text(json.dumps(report), encoding="utf-8")
        kwargs["markdown_output"].write_text("# report\n", encoding="utf-8")
        return report

    monkeypatch.setattr(scan, "write_sports_mlb_world_series_residual_risk_files", fake_writer)

    result = scan.main(
        [
            "sports-mlb-world-series-residual-risk-scout",
            "--kalshi-evidence",
            str(tmp_path / "kalshi.json"),
            "--polymarket-evidence",
            str(tmp_path / "poly.json"),
            "--season",
            "2026",
            "--accept-world-series-remote-tail-risk",
            "--operator-accepted-as-arb",
            "--json-output",
            str(tmp_path / "report.json"),
            "--markdown-output",
            str(tmp_path / "report.md"),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "sports_mlb_world_series_residual_risk_scout_status=OK" in stdout
    assert "shadow_paper_only=true" in stdout
    assert "operator_accepted_as_arb=true" in stdout
    assert "operator_arb_review_rows=0" in stdout
    assert "exact_ready_rows=0" in stdout


def _build(path_a: Path, path_b: Path, *, accept: bool = True, operator: bool = False, fee_models_available: bool = True) -> dict:
    return build_sports_mlb_world_series_residual_risk_report(
        kalshi_evidence=path_a,
        polymarket_evidence=path_b,
        season=2026,
        accept_world_series_remote_tail_risk=accept,
        operator_accepted_as_arb=operator,
        max_quote_age_seconds=3600,
        min_available_notional=10,
        generated_at=NOW,
        fee_models_available=fee_models_available,
    )


def _write_evidence(
    tmp_path: Path,
    *,
    kalshi_outcomes: list[dict] | None = None,
    poly_outcomes: list[dict] | None = None,
    kalshi_overrides: dict | None = None,
) -> tuple[Path, Path]:
    kalshi = _kalshi_payload(outcomes=kalshi_outcomes)
    if kalshi_overrides:
        kalshi.update(kalshi_overrides)
    poly = _poly_payload(outcomes=poly_outcomes)
    return _write_raw(tmp_path, kalshi, poly)


def _write_raw(tmp_path: Path, kalshi: dict, poly: dict) -> tuple[Path, Path]:
    kalshi_path = tmp_path / "kalshi.json"
    poly_path = tmp_path / "poly.json"
    kalshi_path.write_text(json.dumps(kalshi), encoding="utf-8")
    poly_path.write_text(json.dumps(poly), encoding="utf-8")
    return kalshi_path, poly_path


def _row(report: dict, direction: str) -> dict:
    return next(row for row in report["rows"] if row["direction"] == direction)


def _kalshi_payload(*, outcomes: list[dict] | None = None) -> dict:
    return {
        "schema_kind": "kalshi_championship_futures_normalized_evidence_v1",
        "diagnostic_only": True,
        "platform": "Kalshi",
        "batch": "championship_futures",
        "league": "MLB",
        "season": "2026",
        "market": {
            "platform": "Kalshi",
            "batch": "championship_futures",
            "league": "MLB",
            "season": "2026",
            "market_title": "Pro Baseball Champion 2026",
        },
        "validation": {"team_outcomes_observed": 30},
        "outcomes": outcomes or [_kalshi_outcome("LAD", "Los Angeles Dodgers", "KXMLB-26-LAD")],
    }


def _poly_payload(*, outcomes: list[dict] | None = None) -> dict:
    return {
        "schema_kind": "polymarket_mlb_world_series_2026_normalized_evidence_v1",
        "diagnostic_only": True,
        "platform": "Polymarket",
        "batch": "championship_futures",
        "league": "MLB",
        "season": "2026",
        "market_title": "MLB World Series Champion 2026",
        "validation": {"team_outcomes_observed": 30},
        "market_structure": {"listed_team_count": 30, "other_outcome_exists": True},
        "outcomes": outcomes or [_poly_outcome("Los Angeles Dodgers", "m-lad", "yes-lad", "no-lad")],
    }


def _kalshi_outcome(
    code: str,
    team: str,
    ticker: str | None,
    *,
    yes_bid: str | None = "0.39",
    yes_ask: str | None = "0.40",
    no_bid: str | None = "0.44",
    no_ask: str | None = "0.55",
    yes_bid_size: str | None = "50",
    yes_ask_size: str | None = "50",
    no_bid_size: str | None = "50",
    no_ask_size: str | None = "50",
    quote_timestamp: str = "2026-05-28T07:00:00Z",
    include_unit: bool = True,
    quote_source: str | None = None,
) -> dict:
    quote = {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "yes_bid_size": yes_bid_size,
        "yes_ask_size": yes_ask_size,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "no_bid_size": no_bid_size,
        "no_ask_size": no_ask_size,
        "depth_status": "full_clob",
        "quote_timestamp": quote_timestamp,
        "required_quote_fields_present": True,
    }
    if include_unit:
        quote["size_unit"] = "dollar_notional"
    if quote_source:
        quote["quote_source"] = quote_source
    return {
        "team_name": team,
        "team_aliases": [code, team.split()[-1]],
        "market_ticker": ticker,
        "quote_status": "present_full_clob",
        "quote": quote,
        "blockers_remaining": [],
    }


def _poly_outcome(
    team: str,
    market_id: str,
    yes_token: str | None,
    no_token: str | None,
    *,
    yes_bid: str | None = "0.34",
    yes_ask: str | None = "0.35",
    no_bid: str | None = "0.44",
    no_ask: str | None = "0.55",
    yes_bid_size: str | None = "100",
    yes_ask_size: str | None = "100",
    no_bid_size: str | None = "100",
    no_ask_size: str | None = "100",
    quote_timestamp: str = "2026-05-28T07:00:00Z",
    aliases: list[str] | None = None,
) -> dict:
    return {
        "team_name": team,
        "outcome_name": team,
        "team_aliases": aliases or [team.split()[-1]],
        "market_id": market_id,
        "condition_id": f"condition-{market_id}",
        "token_id_yes": yes_token,
        "token_id_no": no_token,
        "quote_status": "present",
        "quote": {
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "yes_bid_size": yes_bid_size,
            "yes_ask_size": yes_ask_size,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "no_bid_size": no_bid_size,
            "no_ask_size": no_ask_size,
            "depth_status": "full_clob",
            "quote_timestamp": quote_timestamp,
            "required_quote_fields_present": True,
            "size_unit": "shares",
            "quote_source": "Polymarket public CLOB",
        },
        "blockers_remaining": [],
    }
