import json
from datetime import datetime, timezone
from pathlib import Path

from relative_value.sx_bet_sports_overlap import (
    BLOCKED_TYPED_KEY_MISMATCH,
    EXACT_TYPED_KEY_MATCH,
    REFERENCE_ONLY,
    build_sx_bet_sports_overlap_report,
)


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def test_exact_structured_sports_keys_produce_diagnostic_overlap(tmp_path: Path) -> None:
    sx_path = _write_sx_typed_keys(tmp_path, [_sx_row()])
    _write_normalized(tmp_path, [_target_row()])
    _write_burden(tmp_path, [])

    report = build_sx_bet_sports_overlap_report(
        sx_bet_typed_keys_path=sx_path,
        input_dir=tmp_path,
        generated_at=NOW,
    )

    assert report["summary"]["overlap_rows"] == 1
    assert report["summary"]["exact_typed_key_matches"] == 1
    scope = report["summary"]["scope_mismatch_breakdown"]
    assert scope["sx_bet_rows_total"] == 1
    assert scope["sx_bet_rows_usable"] == 1
    assert scope["kalshi_polymarket_targets_total"] == 1
    assert scope["kalshi_polymarket_targets_game_level"] == 1
    assert scope["kalshi_polymarket_targets_futures_or_championship"] == 0
    row = report["rows"][0]
    assert row["confidence_tier"] == EXACT_TYPED_KEY_MATCH
    assert row["matched_venue"] == "polymarket"
    assert row["matched_market_id"] == "pm-celtics-knicks-moneyline"
    assert row["diagnostic_only"] is True


def test_mismatched_event_time_blocks(tmp_path: Path) -> None:
    sx_path = _write_sx_typed_keys(tmp_path, [_sx_row()])
    target = _target_row()
    target["sports_typed_key"]["event_time"] = "2026-05-22T23:00:00+00:00"
    _write_normalized(tmp_path, [target])
    _write_burden(tmp_path, [])

    report = build_sx_bet_sports_overlap_report(
        sx_bet_typed_keys_path=sx_path,
        input_dir=tmp_path,
        generated_at=NOW,
    )

    assert report["summary"]["overlap_rows"] == 1
    assert report["summary"]["exact_typed_key_matches"] == 0
    assert report["rows"][0]["confidence_tier"] == BLOCKED_TYPED_KEY_MISMATCH
    assert "event_time_mismatch" in report["rows"][0]["blockers"]


def test_mismatched_line_blocks(tmp_path: Path) -> None:
    sx_path = _write_sx_typed_keys(tmp_path, [_sx_row(market_type="spread", side="team_spread_sides", line=-2.5)])
    target = _target_row(market_type="spread", side="team_spread_sides", line=-3.5)
    _write_normalized(tmp_path, [target])
    _write_burden(tmp_path, [])

    report = build_sx_bet_sports_overlap_report(
        sx_bet_typed_keys_path=sx_path,
        input_dir=tmp_path,
        generated_at=NOW,
    )

    assert report["summary"]["overlap_rows"] == 1
    assert report["summary"]["partial_matches"] == 1
    assert "line_mismatch" in report["rows"][0]["blockers"]


def test_title_only_similarity_does_not_match(tmp_path: Path) -> None:
    sx_path = _write_sx_typed_keys(tmp_path, [_sx_row()])
    _write_normalized(
        tmp_path,
        [
            {
                "venue": "polymarket",
                "market_id": "pm-title-only",
                "title": "Boston Celtics vs New York Knicks",
            }
        ],
    )
    _write_burden(tmp_path, [])

    report = build_sx_bet_sports_overlap_report(
        sx_bet_typed_keys_path=sx_path,
        input_dir=tmp_path,
        generated_at=NOW,
    )

    assert report["summary"]["overlap_rows"] == 0
    assert report["rows"] == []


def test_reference_only_sx_bet_rows_do_not_become_executable(tmp_path: Path) -> None:
    sx_path = _write_sx_typed_keys(tmp_path, [_sx_row()])
    _write_normalized(tmp_path, [_target_row()])
    _write_burden(tmp_path, [])

    report = build_sx_bet_sports_overlap_report(
        sx_bet_typed_keys_path=sx_path,
        input_dir=tmp_path,
        generated_at=NOW,
    )

    row = report["rows"][0]
    assert row["allowed_next_action"] == REFERENCE_ONLY
    assert "reference_only_no_executable_market" in row["blockers"]
    assert row["usable_as_executable_market"] is False
    assert row["affects_evaluator_gates"] is False
    assert report["summary"]["blocked_reference_only"] == 1


def test_no_evaluator_gates_affected_and_no_forbidden_statuses(tmp_path: Path) -> None:
    sx_path = _write_sx_typed_keys(tmp_path, [_sx_row()])
    _write_normalized(tmp_path, [_target_row()])
    _write_burden(tmp_path, [])

    report = build_sx_bet_sports_overlap_report(
        sx_bet_typed_keys_path=sx_path,
        input_dir=tmp_path,
        generated_at=NOW,
    )
    encoded = json.dumps(report)

    assert report["summary"]["candidate_count"] == 0
    assert report["summary"]["pair_count"] == 0
    assert report["safety"]["affects_evaluator_gates"] is False
    assert report["safety"]["candidates_or_pairs_created"] is False
    assert "PAPER_CANDIDATE" not in encoded
    assert "EXACT_PAYOFF_REVIEW_READY" not in encoded
    assert "EXECUTION_EVALUATION_READY" not in encoded


def test_scope_mismatch_breakdown_is_exposed(tmp_path: Path) -> None:
    sx_path = _write_sx_typed_keys(tmp_path, [_sx_row()])
    target = _target_row()
    target["sports_typed_key"]["league"] = "MLB"
    target["sports_typed_key"]["market_type"] = "futures/championship"
    target["sports_typed_key"]["participants"] = ["Boston Red Sox"]
    target["sports_typed_key"]["side"] = "winner_or_field_sides"
    _write_normalized(tmp_path, [target])
    _write_burden(tmp_path, [])

    report = build_sx_bet_sports_overlap_report(
        sx_bet_typed_keys_path=sx_path,
        input_dir=tmp_path,
        generated_at=NOW,
    )

    scope = report["summary"]["scope_mismatch_breakdown"]
    assert scope["sx_bet_rows_total"] == 1
    assert scope["sx_bet_rows_usable"] == 1
    assert scope["kalshi_polymarket_targets_total"] == 1
    assert scope["kalshi_polymarket_targets_game_level"] == 0
    assert scope["kalshi_polymarket_targets_futures_or_championship"] == 1
    assert scope["leagues_with_sx_but_no_targets"] == ["nba"]
    assert scope["leagues_with_targets_but_no_sx"] == ["mlb"]


def test_require_game_level_target_flags_futures_only_inputs(tmp_path: Path) -> None:
    sx_path = _write_sx_typed_keys(tmp_path, [_sx_row()])
    target = _target_row()
    target["sports_typed_key"]["market_type"] = "futures/championship"
    target["sports_typed_key"]["participants"] = ["Boston Celtics"]
    target["sports_typed_key"]["side"] = "winner_or_field_sides"
    _write_normalized(tmp_path, [target])
    _write_burden(tmp_path, [])

    report = build_sx_bet_sports_overlap_report(
        sx_bet_typed_keys_path=sx_path,
        input_dir=tmp_path,
        require_game_level_target=True,
        generated_at=NOW,
    )
    encoded = json.dumps(report)

    assert report["require_game_level_target"] is True
    assert report["summary"]["overlap_rows"] == 0
    assert report["rows"] == []
    assert report["reasons"] == ["no_game_level_kalshi_polymarket_targets_in_input_dir"]
    assert report["summary"]["scope_mismatch_breakdown"]["kalshi_polymarket_targets_total"] == 1
    assert report["summary"]["scope_mismatch_breakdown"]["kalshi_polymarket_targets_game_level"] == 0
    assert report["summary"]["scope_mismatch_breakdown"]["kalshi_polymarket_targets_futures_or_championship"] == 1
    assert "no_game_level_kalshi_polymarket_targets_in_input_dir" in {
        row["blocker"] for row in report["summary"]["top_blockers"]
    }
    assert "PAPER_CANDIDATE" not in encoded
    assert "EXACT_PAYOFF_REVIEW_READY" not in encoded
    assert "EXECUTION_EVALUATION_READY" not in encoded


def _write_sx_typed_keys(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "sx_bet_sports_typed_keys.json"
    path.write_text(
        json.dumps(
            {
                "source": "sx_bet_sports_typed_keys_v1",
                "rows": rows,
                "summary": {"total_rows": len(rows)},
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_normalized(tmp_path: Path, rows: list[dict]) -> None:
    (tmp_path / "normalized_markets_v0.json").write_text(
        json.dumps({"source": "normalized_market_contract_v0", "normalized_markets": rows}),
        encoding="utf-8",
    )


def _write_burden(tmp_path: Path, rows: list[dict]) -> None:
    (tmp_path / "settlement_evidence_burden.json").write_text(
        json.dumps({"source": "settlement_evidence_burden_v1", "markets": rows}),
        encoding="utf-8",
    )


def _sx_row(*, market_type: str = "moneyline", side: str = "team_win_sides", line=None) -> dict:
    return {
        "venue": "sx_bet",
        "market_id": "0xsx",
        "row_index": 0,
        "raw_source_file": "saved_sx.json",
        "raw_row_index": 3,
        "classification": "SPORTS_TYPED_KEYS_COMPLETE",
        "usable_for_future_overlap_review": True,
        "typed_key": _typed_key(market_type=market_type, side=side, line=line),
        "blockers": ["reference_only_no_executable_market"],
    }


def _target_row(*, market_type: str = "moneyline", side: str = "team_win_sides", line=None) -> dict:
    return {
        "venue": "polymarket",
        "market_id": "pm-celtics-knicks-moneyline",
        "ticker": "pm-token",
        "source_file": "saved_pm.json",
        "sports_typed_key": _typed_key(market_type=market_type, side=side, line=line),
        "blockers": ["missing_settlement_source_url"],
    }


def _typed_key(*, market_type: str, side: str, line) -> dict:
    return {
        "league": "NBA",
        "event_time": "2026-05-21T23:00:00+00:00",
        "participants": ["Boston Celtics", "New York Knicks"],
        "market_type": market_type,
        "side": side,
        "line": line,
        "threshold": line,
        "season": "2026",
    }
