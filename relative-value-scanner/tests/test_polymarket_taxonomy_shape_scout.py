from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import scan
from relative_value.polymarket_taxonomy_shape_scout import (
    ACTION_BASIS_RISK_REVIEW,
    ACTION_MANUAL_REVIEW,
    ACTION_SOURCE_REVIEW,
    ACTION_WATCH,
    B_AMBIGUOUS_DATE,
    B_AMBIGUOUS_SHAPE,
    B_AMBIGUOUS_SOURCE,
    B_DEADLINE_VS_POINT,
    B_EXACT_PAYOFF_NOT_PROVEN,
    B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME,
    B_MISSING_CLOB_BOOK,
    B_MISSING_TYPED_KEY,
    B_MULTI_CONDITION,
    B_RANGE_VS_CLOSE,
    B_SETTLEMENT_RULES_MISSING,
    B_SETTLEMENT_WINDOW_MISMATCH,
    B_TITLE_ONLY_MATCH,
    SHAPE_AMBIGUOUS,
    SHAPE_ALL_TIME_HIGH_BY_DATE,
    SHAPE_CRYPTO_DEADLINE_RANGE_HIT,
    SHAPE_DEADLINE,
    SHAPE_ELECTION_CANDIDATE,
    SHAPE_EVENT_WINNER,
    SHAPE_MACRO_RATE_MEETING,
    SHAPE_POINT_IN_TIME,
    SHAPE_RANGE_BUCKET,
    SHAPE_SPORTS_FUTURES,
    build_polymarket_taxonomy_shape_scout_report,
    write_polymarket_taxonomy_shape_scout_files,
)


def _taxonomy_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "polymarket_market_taxonomy_v1",
        "taxonomy_rows": rows,
        "summary": {},
        "safety": {},
    }


def _row(
    *,
    market_id: int,
    family: str,
    shape: str,
    question: str,
    typed_keys: dict[str, Any] | None = None,
    typed_key_complete: bool = False,
    settlement_source_present: bool = True,
    settlement_rules_text_present: bool = True,
    token_ids: list[str] | None = None,
    blockers: list[str] | None = None,
    condition_id: str | None = None,
) -> dict[str, Any]:
    return {
        "row_index": market_id,
        "market_id": market_id,
        "condition_id": condition_id or f"0xcond_{market_id}",
        "event_id": f"event_{market_id}",
        "event_slug": f"event-slug-{market_id}",
        "market_slug": f"market-slug-{market_id}",
        "venue": "polymarket",
        "captured_at": "2026-05-26T05:12:32+00:00",
        "raw_source_file": "reports\\manual_snapshots\\polymarket_universe\\fake.json",
        "source_url": f"https://polymarket.com/market/market-{market_id}",
        "question": question,
        "title": None,
        "family": family,
        "market_shape": shape,
        "typed_keys": typed_keys or {},
        "typed_key_complete": typed_key_complete,
        "settlement_source_present": settlement_source_present,
        "settlement_rules_text_present": settlement_rules_text_present,
        "token_ids": token_ids or [f"tok_{market_id}_yes", f"tok_{market_id}_no"],
        "blockers": blockers or [],
        "book_files_by_token_id": {},
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "status": "active",
    }


def _clob_payload(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "normalized_markets": [
            {
                "market_id": e["market_id"],
                "condition_id": e.get("condition_id"),
                "question": e.get("question"),
                "best_bid": e.get("best_bid"),
                "best_ask": e.get("best_ask"),
                "orderbook_enrichment": {
                    "enrichment_status": "enriched",
                    "best_bid": e.get("best_bid"),
                    "best_ask": e.get("best_ask"),
                    "depth_at_best_bid": e.get("depth_at_best_bid", 1000.0),
                    "depth_at_best_ask": e.get("depth_at_best_ask", 1000.0),
                    "spread": e.get("spread", 0.02),
                    "orderbook_captured_at": e.get("orderbook_captured_at", "2026-05-26T00:00:00+00:00"),
                    "source_endpoint": "polymarket.gamma",
                    "depth_within_1c": {"ask": 100.0, "bid": 100.0, "total": 200.0},
                    "depth_within_3c": {"ask": 200.0, "bid": 200.0, "total": 400.0},
                    "depth_within_5c": {"ask": 300.0, "bid": 300.0, "total": 600.0},
                },
            }
            for e in entries
        ],
        "orderbook_enrichment": {"enriched_count": len(entries)},
    }


def _setup(tmp_path: Path, rows: list[dict[str, Any]], clob_entries: list[dict[str, Any]] | None = None) -> Path:
    (tmp_path / "polymarket_market_taxonomy.json").write_text(json.dumps(_taxonomy_payload(rows)), encoding="utf-8")
    if clob_entries:
        (tmp_path / "polymarket_orderbook_enriched_snapshot.json").write_text(
            json.dumps(_clob_payload(clob_entries)), encoding="utf-8"
        )
    return tmp_path


def test_point_in_time_threshold_classified_correctly(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=101,
            family="CRYPTO",
            shape="POINT_IN_TIME_THRESHOLD",
            question="Will ETH be above $2050 at 9am ET on May 23, 2026?",
            typed_keys={"deadline_or_date": "May 23, 2026", "threshold": 2050.0, "comparator": ">"},
            typed_key_complete=True,
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert row["market_shape"] == SHAPE_POINT_IN_TIME
    assert row["can_create_candidate_pair"] is False
    assert row["paper_candidate"] is False
    assert row["exact_ready"] is False
    # Should be MANUAL_REVIEW since there's no CLOB book; SOURCE_REVIEW only if source missing.
    assert row["allowed_next_action"] in {ACTION_MANUAL_REVIEW, ACTION_SOURCE_REVIEW, ACTION_WATCH}


def test_bitcoin_hit_by_deadline_not_point_in_time(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=151,
            family="CRYPTO",
            shape="POINT_IN_TIME_THRESHOLD",
            question="When will Bitcoin hit $150k? Will Bitcoin hit $150k by June 30, 2026?",
            typed_keys={
                "measurement_date": "June 30, 2026",
                "threshold_value": 150000.0,
                "threshold_operator": ">=",
                "asset": "BTC",
            },
            typed_key_complete=True,
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert row["market_shape"] == SHAPE_CRYPTO_DEADLINE_RANGE_HIT
    assert row["market_shape"] != SHAPE_POINT_IN_TIME
    assert row["conservative_shape_override"] is True
    assert row["deadline_touch_phrase_detected"] is True
    assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW
    assert B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME in row["blockers"]
    assert B_DEADLINE_VS_POINT in row["blockers"]
    assert B_SETTLEMENT_WINDOW_MISMATCH in row["blockers"]
    assert B_EXACT_PAYOFF_NOT_PROVEN in row["blockers"]
    assert report["summary"]["deadline_touch_phrase_reclassified_rows"] == 1


def test_bitcoin_reach_before_deadline_not_point_in_time(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=152,
            family="CRYPTO",
            shape="POINT_IN_TIME_THRESHOLD",
            question="Will Bitcoin reach $150k before Dec 31, 2026?",
            typed_keys={
                "measurement_date": "December 31, 2026",
                "threshold_value": 150000.0,
                "threshold_operator": ">=",
                "asset": "BTC",
            },
            typed_key_complete=True,
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert row["market_shape"] == SHAPE_CRYPTO_DEADLINE_RANGE_HIT
    assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW
    assert B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME in row["blockers"]
    assert row["exact_ready"] is False


def test_interval_hit_market_not_point_in_time(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=155,
            family="MACRO_ECONOMIC_RELEASE",
            shape="POINT_IN_TIME_THRESHOLD",
            question="What will WTI Crude Oil hit in May 2026? Will WTI Crude Oil hit (HIGH) $150 in May?",
            typed_keys={
                "measurement_date": "May 2026",
                "threshold_value": 150.0,
                "threshold_operator": ">=",
                "entity": "WTI Crude Oil",
            },
            typed_key_complete=True,
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert row["market_shape"] == SHAPE_DEADLINE
    assert row["market_shape"] != SHAPE_POINT_IN_TIME
    assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW
    assert B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME in row["blockers"]


def test_eth_above_on_date_at_time_can_remain_point_in_time(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=153,
            family="CRYPTO",
            shape="POINT_IN_TIME_THRESHOLD",
            question="Will ETH be above $5,000 on December 31, 2026 at 5:00 PM ET?",
            typed_keys={
                "measurement_date": "December 31, 2026",
                "measurement_time": "5:00 PM ET",
                "threshold_value": 5000.0,
                "threshold_operator": ">",
                "asset": "ETH",
            },
            typed_key_complete=True,
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert row["market_shape"] == SHAPE_POINT_IN_TIME
    assert row["conservative_shape_override"] is False
    assert B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME not in row["blockers"]


def test_all_time_high_by_date_classified_as_deadline_shape(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=154,
            family="CRYPTO",
            shape="POINT_IN_TIME_THRESHOLD",
            question="Will Bitcoin hit a new all-time high by December 31, 2026?",
            typed_keys={"measurement_date": "December 31, 2026", "asset": "BTC"},
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert row["market_shape"] == SHAPE_ALL_TIME_HIGH_BY_DATE
    assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW
    assert B_HIT_BY_DEADLINE_NOT_POINT_IN_TIME in row["blockers"]


def test_deadline_threshold_touch_blocked_from_exact(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=201,
            family="OTHER_UNKNOWN",
            shape="DEADLINE_HIT_BY_DATE",
            question="Will BTC touch 200k before end of 2026?",
            typed_keys={"deadline_or_date": "December 31, 2026", "threshold": 200000.0, "comparator": ">="},
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert row["market_shape"] == SHAPE_DEADLINE
    assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW
    assert B_DEADLINE_VS_POINT in row["blockers"]
    assert row["exact_ready"] is False


def test_crypto_deadline_or_range_remaps_to_crypto_specific_shape(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=301,
            family="CRYPTO",
            shape="RANGE_BUCKET",
            question="BTC end-of-year range bucket?",
            typed_keys={"deadline_or_date": "December 31, 2026"},
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert row["market_shape"] == SHAPE_CRYPTO_DEADLINE_RANGE_HIT
    assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW
    assert B_RANGE_VS_CLOSE in row["blockers"]


def test_range_bucket_classified_and_blocked(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=302,
            family="WEATHER",
            shape="RANGE_BUCKET",
            question="What will NYC high temp range be on July 4 2026?",
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert row["market_shape"] == SHAPE_RANGE_BUCKET
    assert row["allowed_next_action"] == ACTION_BASIS_RISK_REVIEW
    assert B_RANGE_VS_CLOSE in row["blockers"]


def test_event_winner_classified(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=401,
            family="POLITICS_ELECTION_RESULT",
            shape="ELECTION_WINNER",
            question="Who will win the 2028 US Presidential Election?",
            typed_keys={"deadline_or_date": "November 7, 2028", "entity": "Election"},
            typed_key_complete=True,
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert row["market_shape"] == SHAPE_EVENT_WINNER
    assert row["recommended_pair"] == "Polymarket_vs_Kalshi"
    assert row["paper_candidate"] is False


def test_election_candidate_classified(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=402,
            family="POLITICS_ELECTION_RESULT",
            shape="NOMINATION_WINNER",
            question="Will Person X be the GOP nominee in 2028?",
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert row["market_shape"] == SHAPE_ELECTION_CANDIDATE


def test_macro_rate_meeting_classified(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=403,
            family="MACRO_FED_RATES",
            shape="MACRO_RATE_TARGET",
            question="Will the Fed cut rates at the September 2026 FOMC meeting?",
            typed_keys={"deadline_or_date": "September 17, 2026", "entity": "Fed", "comparator": "cut"},
            typed_key_complete=True,
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert row["market_shape"] == SHAPE_MACRO_RATE_MEETING
    assert "IBKR" in row["recommended_pair"] or "Kalshi" in row["recommended_pair"]


def test_sports_futures_classified(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=404,
            family="SPORTS_FUTURES",
            shape="SPORTS_FUTURES_WINNER",
            question="Will the OKC Thunder win the 2026 NBA Finals?",
            typed_keys={"entity": "OKC Thunder", "deadline_or_date": "June 2026"},
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert row["market_shape"] == SHAPE_SPORTS_FUTURES


def test_ambiguous_source_or_date_blocks_to_source_or_manual(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=405,
            family="OTHER_UNKNOWN",
            shape="POINT_IN_TIME_THRESHOLD",
            question="Some vague threshold question",
            settlement_source_present=False,
            settlement_rules_text_present=False,
            typed_keys={},
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert B_AMBIGUOUS_SOURCE in row["blockers"]
    assert B_SETTLEMENT_RULES_MISSING in row["blockers"]
    assert B_AMBIGUOUS_DATE in row["blockers"]
    assert row["allowed_next_action"] == ACTION_SOURCE_REVIEW


def test_clob_book_attached_count(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=601,
            family="MACRO_FED_RATES",
            shape="POINT_IN_TIME_THRESHOLD",
            question="Will the Fed cut rates at FOMC?",
            condition_id="0xcond_601",
            typed_keys={"deadline_or_date": "2026-09-17"},
            typed_key_complete=True,
        ),
        _row(
            market_id=602,
            family="SPORTS_FUTURES",
            shape="SPORTS_FUTURES_WINNER",
            question="Will OKC win the title?",
            typed_keys={"entity": "OKC"},
        ),
    ]
    clob = [{"market_id": 601, "condition_id": "0xcond_601", "question": "fed", "best_bid": 0.5, "best_ask": 0.55, "orderbook_captured_at": "2026-05-26T00:00:00+00:00"}]
    input_dir = _setup(tmp_path, rows, clob)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir, now=datetime(2026, 5, 27, tzinfo=timezone.utc))
    assert report["summary"]["clob_book_attached"] == 1
    fed_row = next(r for r in report["rows"] if r["market_id"] == 601)
    other_row = next(r for r in report["rows"] if r["market_id"] == 602)
    assert fed_row["clob_book_attached"] is True
    assert other_row["clob_book_attached"] is False
    assert B_MISSING_CLOB_BOOK in other_row["blockers"]


def test_top_ranked_candidate_requires_typed_fields(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=701,
            family="MACRO_FED_RATES",
            shape="MACRO_RATE_TARGET",
            question="Fed FOMC September 2026 — cut or hold?",
            typed_keys={"deadline_or_date": "September 17, 2026", "entity": "Fed", "comparator": "cut", "threshold": 0.25},
            typed_key_complete=True,
        ),
        _row(
            market_id=702,
            family="OTHER_UNKNOWN",
            shape="POINT_IN_TIME_THRESHOLD",
            question="Some vague market",
            settlement_source_present=False,
            settlement_rules_text_present=False,
            typed_keys={},
        ),
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    top = report["summary"]["top_25_candidates"]
    assert top
    assert top[0]["market_shape"] == SHAPE_MACRO_RATE_MEETING
    assert top[0]["family"] == "MACRO_FED_RATES"
    assert top[0]["typed_key_complete"] is True


def test_no_paper_candidate_emitted(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=801,
            family="MACRO_FED_RATES",
            shape="MACRO_RATE_TARGET",
            question="Fed cuts?",
            typed_keys={"deadline_or_date": "September 17, 2026"},
            typed_key_complete=True,
        ),
        _row(
            market_id=802,
            family="CRYPTO",
            shape="RANGE_BUCKET",
            question="BTC range bucket",
        ),
    ]
    input_dir = _setup(tmp_path, rows)
    json_output = tmp_path / "scout.json"
    md_output = tmp_path / "scout.md"
    write_polymarket_taxonomy_shape_scout_files(input_dir=input_dir, json_output=json_output, markdown_output=md_output)
    json_text = json_output.read_text(encoding="utf-8")
    md_text = md_output.read_text(encoding="utf-8")
    forbidden = "PAPER" + "_CANDIDATE"
    assert forbidden not in json_text
    assert forbidden not in md_text
    payload = json.loads(json_text)
    assert payload["summary"]["exact_ready_rows"] == 0
    assert payload["summary"]["paper_candidate_rows"] == 0
    for row in payload["rows"]:
        assert row["can_create_candidate_pair"] is False
        assert row["can_create_paper_candidate"] is False
        assert row["exact_ready"] is False
        assert row["paper_candidate"] is False
        assert row["execution_ready"] is False
        assert row["source_exact_payoff_compatible_with_kalshi"] is False


def test_multi_condition_market_blocks_pair(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=901,
            family="POLITICS_ELECTION_RESULT",
            shape="ELECTION_WINNER",
            question="Election with many candidates",
            token_ids=["tok_a", "tok_b", "tok_c", "tok_d", "tok_e"],
            typed_keys={"deadline_or_date": "2028-11-07"},
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert B_MULTI_CONDITION in row["blockers"]


def test_ambiguous_shape_yields_watch(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=902,
            family="OTHER_UNKNOWN",
            shape="UNKNOWN_OR_COMPOUND",
            question="Something compound",
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert row["market_shape"] == SHAPE_AMBIGUOUS
    assert B_AMBIGUOUS_SHAPE in row["blockers"]
    assert row["allowed_next_action"] == ACTION_WATCH


def test_title_only_match_blocker_always_present(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=903,
            family="MACRO_FED_RATES",
            shape="MACRO_RATE_TARGET",
            question="Fed cuts?",
            typed_keys={"deadline_or_date": "September 17, 2026"},
            typed_key_complete=True,
        )
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    row = report["rows"][0]
    assert B_TITLE_ONLY_MATCH in row["blockers"]


def test_cli_writes_outputs(tmp_path: Path, capsys) -> None:
    rows = [
        _row(
            market_id=1001,
            family="MACRO_FED_RATES",
            shape="MACRO_RATE_TARGET",
            question="Fed?",
            typed_keys={"deadline_or_date": "2026-09-17"},
            typed_key_complete=True,
        )
    ]
    input_dir = _setup(tmp_path, rows)
    json_output = tmp_path / "out.json"
    md_output = tmp_path / "out.md"
    result = scan.main(
        [
            "polymarket-taxonomy-shape-scout",
            "--input-dir",
            str(input_dir),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(md_output),
        ]
    )
    assert result == 0
    stdout = capsys.readouterr().out
    assert "polymarket_taxonomy_shape_scout=OK" in stdout
    assert "diagnostic_only=true" in stdout
    assert "exact_ready_rows=0" in stdout
    assert "paper_candidate_rows=0" in stdout
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["schema_kind"] == "polymarket_taxonomy_shape_scout_v1"


def test_summary_shape_counts_match_input(tmp_path: Path) -> None:
    rows = [
        _row(market_id=1101, family="MACRO_FED_RATES", shape="MACRO_RATE_TARGET", question="A"),
        _row(market_id=1102, family="POLITICS_ELECTION_RESULT", shape="NOMINATION_WINNER", question="B"),
        _row(market_id=1103, family="CRYPTO", shape="RANGE_BUCKET", question="C"),
        _row(market_id=1104, family="OTHER_UNKNOWN", shape="UNKNOWN_OR_COMPOUND", question="D"),
        _row(market_id=1105, family="OTHER_UNKNOWN", shape="POINT_IN_TIME_THRESHOLD", question="E"),
    ]
    input_dir = _setup(tmp_path, rows)
    report = build_polymarket_taxonomy_shape_scout_report(input_dir=input_dir)
    sc = report["summary"]["shape_counts"]
    assert sc.get(SHAPE_MACRO_RATE_MEETING) == 1
    assert sc.get(SHAPE_ELECTION_CANDIDATE) == 1
    assert sc.get(SHAPE_CRYPTO_DEADLINE_RANGE_HIT) == 1
    assert sc.get(SHAPE_AMBIGUOUS) == 1
    assert sc.get(SHAPE_POINT_IN_TIME) == 1
    assert report["summary"]["deadline_or_range_hit_blocked"] >= 1


def test_ops_status_includes_polymarket_shape_counts(tmp_path: Path) -> None:
    rows = [
        _row(
            market_id=1201,
            family="MACRO_FED_RATES",
            shape="MACRO_RATE_TARGET",
            question="Fed",
            typed_keys={"deadline_or_date": "2026-09-17"},
            typed_key_complete=True,
        ),
        _row(market_id=1202, family="CRYPTO", shape="RANGE_BUCKET", question="BTC range"),
    ]
    input_dir = _setup(tmp_path, rows)
    write_polymarket_taxonomy_shape_scout_files(
        input_dir=input_dir,
        json_output=input_dir / "polymarket_taxonomy_shape_scout.json",
        markdown_output=input_dir / "polymarket_taxonomy_shape_scout.md",
    )
    from relative_value.relative_value_ops_status import build_relative_value_ops_status_report

    ops = build_relative_value_ops_status_report(input_dir=input_dir)
    poly_status = (ops["summary"] or {}).get("polymarket_taxonomy_shape_scout") or {}
    assert poly_status.get("present") is True
    assert poly_status.get("total_rows", 0) == 2
    assert poly_status.get("point_in_time_candidates", 0) == 0  # MACRO_RATE_TARGET maps to macro_rate_meeting
    assert poly_status.get("deadline_or_range_hit_blocked", 0) == 1  # CRYPTO RANGE_BUCKET
    assert poly_status.get("paper_candidate_rows", 0) == 0
    assert poly_status.get("exact_ready_rows", 0) == 0
