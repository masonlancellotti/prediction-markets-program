from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.mlb_world_series_revival_status import build_mlb_world_series_revival_status_report


NOW = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _pairs(poly_id: str = "poly-mlb", kalshi_ticker: str = "KXMLB-26-NYY") -> dict:
    return {
        "schema_version": 1,
        "source": "mlb_world_series_pairs_fixture",
        "generated_at": "2026-05-23T11:59:00+00:00",
        "pair_count": 1,
        "pairs": [
            {
                "action": "WATCH",
                "polymarket": {
                    "market_id": poly_id,
                    "question": "Will the New York Yankees win the 2026 World Series?",
                    "event_title": "MLB World Series Champion 2026",
                },
                "kalshi": {
                    "ticker": kalshi_ticker,
                    "market_id": kalshi_ticker,
                    "question": "Will New York Y win the 2026 Pro Baseball Championship?",
                    "event_title": "KXMLB 2026",
                },
                "similarity_score": 0.99,
                "ineligibility_reasons": [],
                "same_payoff_asserted": False,
            }
        ],
    }


def _market(venue: str, *, captured_at: str = "2026-05-23T11:59:30+00:00") -> dict:
    market = {
        "venue": venue,
        "market_id": "poly-mlb" if venue == "polymarket" else "KXMLB-26-NYY",
        "question": (
            "Will the New York Yankees win the 2026 World Series?"
            if venue == "polymarket"
            else "Will New York Y win the 2026 Pro Baseball Championship?"
        ),
        "event_title": "MLB World Series Champion 2026",
        "settlement_rule": "official mlb world series winner",
        "end_date": "2026-11-01T04:00:00+00:00",
        "close_time": "2026-11-01T04:00:00+00:00",
        "market_type": "binary_event" if venue == "polymarket" else "binary",
        "source_type": "EXECUTABLE_VENUE",
        "outcomes": [{"name": "Yes"}, {"name": "No"}],
        "raw": {"series_ticker": "KXMLB", "event_ticker": "KXMLB-26", "market_type": "binary"},
        "orderbook_enrichment": {
            "orderbook_captured_at": captured_at,
            "best_bid": 0.70 if venue == "polymarket" else 0.50,
            "best_ask": 0.72 if venue == "polymarket" else 0.52,
            "depth_at_best_bid": 10.0,
            "depth_at_best_ask": 12.0,
            "enrichment_status": "enriched",
            "enrichment_warnings": [],
        },
    }
    if venue == "polymarket":
        market["condition_id"] = "0xpolymlb"
        market["raw"]["clobTokenIds"] = '["yes-token", "no-token"]'
    else:
        market["ticker"] = "KXMLB-26-NYY"
    return market


def _enriched_payload(venue: str, *, captured_at: str = "2026-05-23T11:59:30+00:00") -> dict:
    return {
        "schema_version": 1,
        "source": f"{venue}_enriched_fixture",
        "captured_at": "2026-05-23T11:58:00+00:00",
        "market_count": 1,
        "normalized_count": 1,
        "normalized_markets": [_market(venue, captured_at=captured_at)],
    }


def _triage(poly_id: str = "poly-mlb", kalshi_ticker: str = "KXMLB-26-NYY") -> dict:
    return {
        "schema_version": 1,
        "source": "cross_platform_opportunity_triage_v1",
        "rows": [
            {
                "row_id": "triage-1",
                "venue_a": "polymarket",
                "market_id_a": poly_id,
                "venue_b": "kalshi",
                "ticker_b": kalshi_ticker,
                "relationship_class": "EXACT_EQUALITY_CANDIDATE",
                "evidence_summary": "explicit_evidence_source=same_payoff_board_v1 MLB World Series KXMLB",
            }
        ],
    }


def _write_fixture_reports(reports: Path, *, captured_at: str = "2026-05-23T11:59:30+00:00") -> None:
    _write(reports / "cross_platform_opportunity_triage.json", _triage())
    _write(reports / "mlb_world_series_pairs_run.json", _pairs())
    _write(reports / "mlb_fresh_polymarket_enriched.json", _enriched_payload("polymarket", captured_at=captured_at))
    _write(reports / "mlb_fresh_kalshi_enriched.json", _enriched_payload("kalshi", captured_at=captured_at))


def test_revival_status_rebuilds_strict_evidence_and_runs_saved_evaluator(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write_fixture_reports(reports)

    report = build_mlb_world_series_revival_status_report(input_dir=reports, generated_at=NOW)

    assert report["pairs_found"] == 1
    assert report["triage_exact_equality_candidates"]["exact_equality_candidate_rows"] == 1
    assert report["triage_exact_equality_candidates"]["rows_with_matching_source_rows"] == 1
    assert report["strict_same_payoff_pass_count"] == 1
    assert report["trusted_relationships_attached"] == 1
    assert report["evaluator_rows"] == 1
    assert report["paper_candidate_count"] == 0
    assert report["stale_input_files"] == []
    assert report["missing_orderbook_enrichment"] == []
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_stale_saved_orderbooks_block_current_gates(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write_fixture_reports(reports, captured_at="2026-05-23T10:00:00+00:00")

    report = build_mlb_world_series_revival_status_report(input_dir=reports, generated_at=NOW)

    assert "stale_orderbook_enrichment" in report["blockers"]
    assert "missing_or_stale_orderbook_enrichment" in report["blockers"]
    assert report["strict_same_payoff_pass_count"] == 1
    assert report["trusted_relationships_attached"] == 1
    assert report["paper_candidate_count"] == 0
    assert {row["venue"] for row in report["stale_input_files"]} == {"polymarket", "kalshi"}
    assert {row["reason"] for row in report["missing_orderbook_enrichment"]} == {"stale_orderbook_captured_at"}


def test_missing_source_rows_are_reported(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write(reports / "cross_platform_opportunity_triage.json", _triage(poly_id="missing-poly", kalshi_ticker="KXMLB-26-BOS"))

    report = build_mlb_world_series_revival_status_report(input_dir=reports, generated_at=NOW)

    assert report["triage_exact_equality_candidates"]["exact_equality_candidate_rows"] == 1
    assert report["triage_exact_equality_candidates"]["rows_with_matching_source_rows"] == 0
    assert "triage_exact_rows_missing_matching_source_rows" in report["blockers"]
    assert "missing_mlb_world_series_pairs_file" in report["blockers"]


def test_revival_status_cli_writes_reports(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write_fixture_reports(reports)
    json_output = tmp_path / "status.json"
    markdown_output = tmp_path / "status.md"

    result = scan.main(
        [
            "run-mlb-world-series-revival-status",
            "--input-dir",
            str(reports),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert result == 0
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["source"] == "mlb_world_series_revival_status_v1"
    assert payload["pairs_found"] == 1
    assert markdown_output.exists()
