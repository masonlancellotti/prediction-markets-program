import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.reference_diagnostics import explain_reference_context_files
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
