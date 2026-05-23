from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.mlb_same_scope_audit import (
    audit_same_scope_mlb_candidate_files,
    build_mlb_same_scope_targeting_report,
    classify_mlb_competition_scope,
    classify_mlb_scope,
)
from relative_value.paper_candidate_evaluator import ACTION_MANUAL_REVIEW, PaperCandidateEvaluatorConfig, evaluate_paper_candidates
from relative_value.same_payoff_evidence import SAME_PAYOFF_BOARD_CLASSIFIER_VERSION


NOW = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)


def _row(poly_question: str, kalshi_question: str) -> dict:
    return {
        "polymarket": {"market_id": "poly-1", "question": poly_question, "event_title": poly_question},
        "kalshi": {"ticker": "KXMLB-26-TB", "question": kalshi_question, "event_title": kalshi_question},
    }


def test_world_series_vs_alcs_is_subset_superset_not_same_scope() -> None:
    scope = classify_mlb_scope(
        _row(
            "Will Tampa Bay Rays win the 2026 American League Championship Series?",
            "Will Tampa Bay win the 2026 Pro Baseball Championship?",
        )
    )

    assert scope["classification"] == "world_series_vs_league_championship_subset_superset"


def test_same_world_series_vs_world_series_fixture_can_be_same_scope() -> None:
    scope = classify_mlb_scope(
        _row(
            "Will Tampa Bay Rays win the 2026 World Series?",
            "Will Tampa Bay win the 2026 Pro Baseball Championship?",
        )
    )

    assert scope["classification"] == "exact_same_competition_scope"


def test_dodgers_vs_laa_angels_is_not_team_entity_match() -> None:
    scope = classify_mlb_scope(
        _row(
            "Will Los Angeles Dodgers win the 2026 World Series?",
            "Will Los Angeles A win the 2026 Pro Baseball Championship?",
        )
    )

    assert scope["team_entity_match"] is False
    assert scope["classification"] == "team_entity_mismatch"


def test_dodgers_vs_angels_full_name_is_not_team_entity_match() -> None:
    scope = classify_mlb_scope(
        _row(
            "Will Los Angeles Dodgers win the 2026 World Series?",
            "Will Los Angeles Angels win the 2026 Pro Baseball Championship?",
        )
    )

    assert scope["team_entity_match"] is False
    assert scope["classification"] == "team_entity_mismatch"


def test_world_series_row_classified_as_world_series() -> None:
    assert classify_mlb_competition_scope({"question": "Will Tampa Bay Rays win the 2026 World Series?"}) == "WORLD_SERIES"


def test_alcs_row_classified_as_alcs() -> None:
    assert classify_mlb_competition_scope({"question": "Will Tampa Bay Rays win the 2026 American League Championship Series?"}) == "ALCS"
    assert classify_mlb_competition_scope({"question": "Will Tampa Bay Rays win ALCS?"}) == "ALCS"


def test_nlcs_row_classified_as_nlcs() -> None:
    assert classify_mlb_competition_scope({"question": "Will New York Mets win the 2026 National League Championship Series?"}) == "NLCS"
    assert classify_mlb_competition_scope({"question": "Will New York Mets win NLCS?"}) == "NLCS"


def test_game_row_classified_as_game() -> None:
    assert classify_mlb_competition_scope({"question": "Will Tampa Bay Rays beat Boston Red Sox on June 1?"}) == "GAME"


def test_game_winner_vs_outright_mismatch_blocks() -> None:
    scope = classify_mlb_scope(
        _row(
            "Will Tampa Bay Rays beat Boston Red Sox on June 1?",
            "Will Tampa Bay win the 2026 Pro Baseball Championship?",
        )
    )

    assert scope["classification"] == "game_winner_vs_series_or_outright_mismatch"


def test_same_scope_targeting_reports_world_series_overlap_and_mismatches() -> None:
    report = build_mlb_same_scope_targeting_report(
        polymarket_snapshot={
            "schema_version": 1,
            "normalized_markets": [
                {"market_id": "poly-ws", "question": "Will Tampa Bay Rays win the 2026 World Series?"},
                {"market_id": "poly-alcs", "question": "Will Tampa Bay Rays win the 2026 ALCS?"},
                {"market_id": "poly-game", "question": "Will Tampa Bay Rays beat Boston Red Sox on June 1?"},
            ],
        },
        kalshi_snapshot={
            "schema_version": 1,
            "normalized_markets": [
                {"ticker": "KXMLB-26-TB", "question": "Will Tampa Bay win the 2026 Pro Baseball Championship?"},
            ],
        },
        pairs_payload={
            "schema_version": 1,
            "pairs": [
                {
                    "polymarket": {"market_id": "poly-alcs", "question": "Will Tampa Bay Rays win the 2026 ALCS?"},
                    "kalshi": {"ticker": "KXMLB-26-TB", "question": "Will Tampa Bay win the 2026 Pro Baseball Championship?"},
                }
            ],
        },
        generated_at=NOW,
    )

    counts = report["summary"]["source_counts_by_scope"]
    assert counts["polymarket"]["WORLD_SERIES"] == 1
    assert counts["polymarket"]["ALCS"] == 1
    assert counts["polymarket"]["GAME"] == 1
    assert counts["kalshi"]["WORLD_SERIES"] == 1
    assert report["summary"]["overlapping_same_scope_inventory"] is True
    assert report["summary"]["overlap_scopes"] == ["WORLD_SERIES"]
    assert report["scope_pair_mismatches"][0]["reason"] == "world_series_vs_league_championship_mismatch"


def test_missing_enriched_joins_are_reported(tmp_path: Path) -> None:
    pairs = {
        "schema_version": 1,
        "source": "fixture_pairs",
        "generated_at": "2026-05-23T11:59:00+00:00",
        "pair_count": 1,
        "pairs": [
            {
                "action": "WATCH",
                "polymarket": {"market_id": "poly-1", "question": "Will Tampa Bay Rays win the 2026 World Series?"},
                "kalshi": {"ticker": "KXMLB-26-TB", "question": "Will Tampa Bay win the 2026 Pro Baseball Championship?"},
                "ineligibility_reasons": [],
            }
        ],
    }
    empty_snapshot = {"schema_version": 1, "source": "fixture_empty", "normalized_markets": []}
    pairs_path = _write(tmp_path / "pairs.json", pairs)
    poly_path = _write(tmp_path / "poly.json", empty_snapshot)
    kalshi_path = _write(tmp_path / "kalshi.json", empty_snapshot)

    payload = audit_same_scope_mlb_candidate_files(
        pairs_path=pairs_path,
        polymarket_enriched_path=poly_path,
        kalshi_enriched_path=kalshi_path,
        json_output_path=tmp_path / "audit.json",
        markdown_output_path=tmp_path / "audit.md",
        board_json_output_path=tmp_path / "board.json",
        board_markdown_output_path=tmp_path / "board.md",
        derived_pairs_output_path=tmp_path / "derived.json",
        evaluator_output_path=tmp_path / "eval.json",
        now=NOW,
    )

    blockers = payload["rows"][0]["blockers"]
    assert "missing:polymarket_enriched_market" in blockers
    assert "missing:kalshi_enriched_market" in blockers
    assert payload["summary"]["trusted_same_payoff_evidence_count"] == 0


def test_evaluator_requires_trusted_same_payoff_board_v1_evidence() -> None:
    pairs = _minimal_evaluator_pairs(
        {
            "relationship": "EQUIVALENT",
            "same_payoff": True,
            "confidence": 0.95,
            "blocking_reasons": [],
            "manual_review_required": False,
            "source": "unknown_source",
            "same_payoff_board_evidence": {
                "classifier_version": SAME_PAYOFF_BOARD_CLASSIFIER_VERSION,
                "strict_pass_count": 11,
                "strict_comparator_count": 11,
            },
        }
    )

    row = evaluate_paper_candidates(
        pairs_payload=pairs,
        polymarket_payload=_minimal_poly_snapshot(),
        kalshi_payload=_minimal_kalshi_snapshot(),
        detected_at=NOW,
        config=PaperCandidateEvaluatorConfig(accept_unit_mismatch=True),
    )["ledger"][0]

    assert row["action"] == ACTION_MANUAL_REVIEW
    assert row["missed_fill_reason"] == "relationship_same_payoff_not_proven"


def test_audit_command_emits_no_forbidden_labels(tmp_path: Path, capsys) -> None:
    pairs = {
        "schema_version": 1,
        "source": "fixture_pairs",
        "generated_at": "2026-05-23T11:59:00+00:00",
        "pair_count": 1,
        "pairs": [
            {
                "action": "WATCH",
                "polymarket": {"market_id": "poly-1", "question": "Will Tampa Bay Rays win the 2026 World Series?"},
                "kalshi": {"ticker": "KXMLB-26-TB", "question": "Will Tampa Bay win the 2026 Pro Baseball Championship?"},
                "ineligibility_reasons": [],
            }
        ],
    }
    empty_snapshot = {"schema_version": 1, "source": "fixture_empty", "normalized_markets": []}
    result = scan.main(
        [
            "audit-same-scope-mlb-candidates",
            "--pairs",
            str(_write(tmp_path / "pairs.json", pairs)),
            "--polymarket-enriched",
            str(_write(tmp_path / "poly.json", empty_snapshot)),
            "--kalshi-enriched",
            str(_write(tmp_path / "kalshi.json", empty_snapshot)),
            "--json-output",
            str(tmp_path / "audit.json"),
            "--markdown-output",
            str(tmp_path / "audit.md"),
            "--board-json-output",
            str(tmp_path / "board.json"),
            "--board-markdown-output",
            str(tmp_path / "board.md"),
            "--derived-pairs-output",
            str(tmp_path / "derived.json"),
            "--evaluator-output",
            str(tmp_path / "eval.json"),
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "PAPER" not in output
    assert "POSSIBLE_ARB" not in output
    assert "trade" not in output.lower()


def test_same_scope_targeting_command_emits_no_forbidden_labels(tmp_path: Path, capsys) -> None:
    poly = {
        "schema_version": 1,
        "normalized_markets": [{"market_id": "poly-ws", "question": "Will Tampa Bay Rays win the 2026 World Series?"}],
    }
    kalshi = {
        "schema_version": 1,
        "normalized_markets": [{"ticker": "KXMLB-26-TB", "question": "Will Tampa Bay win the 2026 Pro Baseball Championship?"}],
    }

    result = scan.main(
        [
            "diagnose-mlb-same-scope-targeting",
            "--polymarket-snapshot",
            str(_write(tmp_path / "poly.json", poly)),
            "--kalshi-snapshot",
            str(_write(tmp_path / "kalshi.json", kalshi)),
            "--json-output",
            str(tmp_path / "targeting.json"),
            "--markdown-output",
            str(tmp_path / "targeting.md"),
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "PAPER" not in output
    assert "POSSIBLE_ARB" not in output
    assert "trade" not in output.lower()


def test_default_scan_py_remains_static_fixture(capsys) -> None:
    result = scan.main([])

    assert result == 0
    assert "data_source_mode=STATIC_FIXTURE" in capsys.readouterr().out


def _minimal_evaluator_pairs(contract_relationship: dict) -> dict:
    return {
        "schema_version": 1,
        "source": "fixture_pairs",
        "generated_at": "2026-05-23T11:59:00+00:00",
        "pair_count": 1,
        "pairs": [
            {
                "action": "MANUAL_REVIEW",
                "polymarket": {"market_id": "poly-1", "question": "Will Tampa Bay Rays win the 2026 World Series?"},
                "kalshi": {"ticker": "KXMLB-26-TB", "question": "Will Tampa Bay win the 2026 Pro Baseball Championship?"},
                "ineligibility_reasons": [],
                "contract_relationship": contract_relationship,
            }
        ],
    }


def _minimal_poly_snapshot() -> dict:
    return {
        "schema_version": 1,
        "source": "poly",
        "normalized_markets": [
            {
                "venue": "polymarket",
                "market_id": "poly-1",
                "question": "Will Tampa Bay Rays win the 2026 World Series?",
                "end_date": "2026-05-23T13:00:00+00:00",
                "orderbook_enrichment": {
                    "enrichment_status": "enriched",
                    "orderbook_captured_at": "2026-05-23T11:59:30+00:00",
                    "best_bid": 0.66,
                    "best_ask": 0.68,
                    "depth_at_best_bid": 5.0,
                    "depth_at_best_ask": 5.0,
                },
            }
        ],
    }


def _minimal_kalshi_snapshot() -> dict:
    return {
        "schema_version": 1,
        "source": "kalshi",
        "normalized_markets": [
            {
                "venue": "kalshi",
                "ticker": "KXMLB-26-TB",
                "market_id": "kalshi-1",
                "question": "Will Tampa Bay win the 2026 Pro Baseball Championship?",
                "end_date": "2026-05-23T13:00:00+00:00",
                "orderbook_enrichment": {
                    "enrichment_status": "enriched",
                    "orderbook_captured_at": "2026-05-23T11:59:35+00:00",
                    "best_bid": 0.58,
                    "best_ask": 0.60,
                    "depth_at_best_bid": 5.0,
                    "depth_at_best_ask": 5.0,
                },
            }
        ],
    }


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
