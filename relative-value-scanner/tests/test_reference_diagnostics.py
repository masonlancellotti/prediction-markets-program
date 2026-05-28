import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.reference_diagnostics import build_reference_odds_fv_report, explain_reference_context_files
from venues.the_odds_api import build_the_odds_api_reference_snapshot


NOW = datetime(2026, 5, 21, 12, 5, tzinfo=timezone.utc)


def _snapshot() -> dict:
    return {
        "schema_version": 1,
        "source": "polymarket_gamma",
        "captured_at": "2026-05-21T12:00:00+00:00",
        "normalized_markets": [
            {
                "venue": "polymarket",
                "market_id": "poly-celtics",
                "question": "Will the Boston Celtics win against the New York Knicks?",
                "event_title": "New York Knicks at Boston Celtics",
                "end_date": "2026-05-21T23:00:00+00:00",
                "active": True,
                "closed": False,
                "liquidity": 100.0,
            }
        ],
    }


def _odds_response() -> list[dict]:
    return [
        {
            "id": "event-1",
            "sport_key": "basketball_nba",
            "sport_title": "NBA",
            "commence_time": "2026-05-21T23:00:00Z",
            "home_team": "Boston Celtics",
            "away_team": "New York Knicks",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "last_update": "2026-05-21T12:00:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "last_update": "2026-05-21T12:00:00Z",
                            "outcomes": [
                                {"name": "Boston Celtics", "price": -120},
                                {"name": "New York Knicks", "price": 110},
                            ],
                        }
                    ],
                }
            ],
        }
    ]


def _reference_snapshot(*, stale_after_seconds: int = 900) -> dict:
    return build_the_odds_api_reference_snapshot(
        _odds_response(),
        sport_key="basketball_nba",
        regions="us",
        markets="h2h",
        odds_format="american",
        retrieved_at=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
        stale_after_seconds=stale_after_seconds,
    )


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_any(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _odds_api_raw_event() -> list[dict]:
    return [
        {
            "id": "mlb-event-1",
            "sport_key": "baseball_mlb",
            "sport_title": "MLB",
            "commence_time": "2026-06-01T23:00:00Z",
            "home_team": "Boston Red Sox",
            "away_team": "New York Yankees",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Boston Red Sox", "price": -120},
                                {"name": "New York Yankees", "price": 110},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Boston Red Sox", "price": -110, "point": -1.5},
                                {"name": "New York Yankees", "price": -110, "point": 1.5},
                            ],
                        },
                    ],
                }
            ],
        }
    ]


def _write_odds_snapshot(reports: Path, payload=None) -> Path:
    return _write_any(
        reports / "manual_snapshots" / "the_odds_api" / "20260526" / "oddsapi_mlb_odds.json",
        payload if payload is not None else {"captured_at": "2026-05-26T12:00:00+00:00", "raw_response": _odds_api_raw_event()},
    )


def _write_normalized_target(reports: Path, *, line=None, probability=0.55) -> Path:
    return _write_any(
        reports / "normalized_markets_v0.json",
        {
            "schema_version": 1,
            "source": "normalized_market_contract_v0",
            "normalized_markets": [
                {
                    "venue": "kalshi",
                    "market_id": "kalshi-mlb-1",
                    "title": "New York Yankees at Boston Red Sox",
                    "sport": "MLB",
                    "league": "MLB",
                    "event_time": "2026-06-01T23:00:00+00:00",
                    "participants": ["New York Yankees", "Boston Red Sox"],
                    "market_type": "h2h" if line is None else "spreads",
                    "line": line,
                    "probability": probability,
                }
            ],
        },
    )


def test_reference_diagnostics_find_plausible_matches(tmp_path: Path) -> None:
    snapshot_path = _write(tmp_path / "snapshot.json", _snapshot())
    reference_path = _write(tmp_path / "reference.json", _reference_snapshot())

    payload = explain_reference_context_files(
        snapshot_path=snapshot_path,
        reference_snapshot_path=reference_path,
        now=NOW,
    )

    assert payload["diagnostic_match_count"] == 2
    row = payload["diagnostic_rows"][0]
    assert row["action"] == "REFERENCE_ONLY_DIAGNOSTIC"
    assert row["executable_market_title"] == "Will the Boston Celtics win against the New York Knicks?"
    assert row["reference_event_title"] == "New York Knicks at Boston Celtics"
    assert row["bookmaker"] == "DraftKings"
    assert row["market_type"] == "h2h"
    assert row["no_vig_probability"] is not None
    assert row["retrieved_at"] == "2026-05-21T12:00:00+00:00"
    assert row["stale_after"] == "2026-05-21T12:15:00+00:00"
    assert row["match_score"] > 0
    assert row["match_reason"] == "title_entity_similarity_only"


def test_stale_reference_rows_are_flagged(tmp_path: Path) -> None:
    snapshot_path = _write(tmp_path / "snapshot.json", _snapshot())
    reference_path = _write(tmp_path / "reference.json", _reference_snapshot(stale_after_seconds=60))

    payload = explain_reference_context_files(
        snapshot_path=snapshot_path,
        reference_snapshot_path=reference_path,
        now=NOW,
    )

    assert payload["stale_reference_record_count"] == 2
    assert payload["diagnostic_rows"][0]["reference_status"] == "stale"
    assert "stale_reference_record" in payload["diagnostic_rows"][0]["reference_diagnostics"]


def test_malformed_reference_rows_are_skipped_and_reported(tmp_path: Path) -> None:
    snapshot_path = _write(tmp_path / "snapshot.json", _snapshot())
    reference = _reference_snapshot()
    reference["normalized_records"].append({"source_type": "REFERENCE_ONLY"})
    reference_path = _write(tmp_path / "reference.json", reference)

    payload = explain_reference_context_files(
        snapshot_path=snapshot_path,
        reference_snapshot_path=reference_path,
        now=NOW,
    )

    assert payload["reference_record_count"] == 3
    assert payload["malformed_reference_record_count"] == 1
    assert payload["skipped_reference_record_count"] == 1
    assert payload["diagnostic_match_count"] == 2


def test_reference_diagnostics_emit_no_disallowed_actions(tmp_path: Path, capsys) -> None:
    snapshot_path = _write(tmp_path / "snapshot.json", _snapshot())
    reference_path = _write(tmp_path / "reference.json", _reference_snapshot())

    result = scan.main(
        [
            "explain-reference-context",
            "--snapshot",
            str(snapshot_path),
            "--reference-snapshot",
            str(reference_path),
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "REFERENCE_ONLY_DIAGNOSTIC" in output
    assert "PAPER_CANDIDATE" not in output
    assert "POSSIBLE_ARB" not in output
    assert "explain_reference_context_status=OK matches=2" in output


def test_reference_snapshot_cannot_be_used_as_executable_snapshot(tmp_path: Path, capsys) -> None:
    reference_path = _write(tmp_path / "reference.json", _reference_snapshot())

    result = scan.main(
        [
            "explain-reference-context",
            "--snapshot",
            str(reference_path),
            "--reference-snapshot",
            str(reference_path),
        ]
    )

    assert result == 1
    assert "snapshot must be an executable venue snapshot" in capsys.readouterr().out


def test_default_scan_output_remains_unchanged(capsys) -> None:
    result = scan.main([])

    assert result == 0
    assert "relative_value_scan_status=OFFLINE_COMPLETE candidates=7 possible_arbs=0" in capsys.readouterr().out


def test_reference_fv_unmatched_rows_are_summarized_not_candidates(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write_odds_snapshot(reports)

    report = build_reference_odds_fv_report(input_dir=reports, generated_at=NOW)

    assert report["summary"]["odds_events_read"] == 1
    assert report["summary"]["reference_markets_read"] == 4
    assert report["summary"]["matched_rows"] == 0
    assert report["summary"]["unmatched_reference_rows"] == 4
    assert report["summary"]["residual_rows"] == 0
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_reference_fv_matches_same_game_same_market_without_executable_claim(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write_odds_snapshot(reports)
    _write_normalized_target(reports, probability=0.6)

    report = build_reference_odds_fv_report(input_dir=reports, generated_at=NOW)

    assert report["summary"]["matched_rows"] == 2
    row = report["residual_rows"][0]
    assert row["diagnostic_only"] is True
    assert row["affects_evaluator_gates"] is False
    assert row["reference_only_source"] is True
    assert row["executable_leg"] is False
    assert row["paper_candidate_emitted"] is False
    assert row["allowed_next_action"] == "FAIR_VALUE_WATCH"
    assert "reference_only_source" in row["blockers"]
    assert "not_executable" in row["blockers"]
    assert "no_same_payoff_claim" in row["blockers"]


def test_reference_fv_spread_requires_same_line(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write_odds_snapshot(reports)
    _write_normalized_target(reports, line=-2.5, probability=0.52)

    report = build_reference_odds_fv_report(input_dir=reports, generated_at=NOW)

    assert report["summary"]["matched_rows"] == 0


def test_reference_fv_cli_writes_outputs(tmp_path: Path, capsys) -> None:
    reports = tmp_path / "reports"
    _write_odds_snapshot(reports)
    _write_normalized_target(reports, probability=0.6)
    json_output = reports / "the_odds_api_fv_residuals.json"
    markdown_output = reports / "the_odds_api_fv_residuals.md"

    rc = scan.main(
        [
            "audit-reference-odds-fv",
            "--input-dir",
            str(reports),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert rc == 0
    assert "reference_odds_fv_status=OK" in capsys.readouterr().out
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["source"] == "reference_odds_fv_residuals_v1"
    assert "PAPER_CANDIDATE" not in json.dumps(payload)
    assert "reference_only_source: `true`" in markdown_output.read_text(encoding="utf-8")
