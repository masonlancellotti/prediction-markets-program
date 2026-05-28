import json
from datetime import datetime, timezone
from pathlib import Path

from relative_value.sx_bet_sports_typed_keys import (
    REFERENCE_ONLY_UNUSABLE,
    SPORTS_TYPED_KEYS_BLOCKED,
    SPORTS_TYPED_KEYS_COMPLETE,
    SPORTS_TYPED_KEYS_PARTIAL,
    build_sx_bet_sports_typed_keys_report,
)


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def test_complete_structured_sports_row_passes_typed_key_completeness(tmp_path: Path) -> None:
    draft = _write_draft(tmp_path, [_record()])

    report = build_sx_bet_sports_typed_keys_report(input_path=draft, generated_at=NOW)

    row = report["rows"][0]
    assert row["classification"] == SPORTS_TYPED_KEYS_COMPLETE
    assert row["typed_key"]["league"] == "NBA"
    assert row["typed_key"]["market_type"] == "moneyline"
    assert row["typed_key"]["participants_confidence"] == "HIGH"
    assert row["usable_for_future_overlap_review"] is True
    assert report["summary"]["complete"] == 1


def test_title_only_participants_are_low_confidence_and_do_not_pass_complete(tmp_path: Path) -> None:
    record = _record(participants=[], title="Boston Celtics vs New York Knicks")
    draft = _write_draft(tmp_path, [record])

    report = build_sx_bet_sports_typed_keys_report(input_path=draft, generated_at=NOW)

    row = report["rows"][0]
    assert row["classification"] == SPORTS_TYPED_KEYS_BLOCKED
    assert row["typed_key"]["participants"] == ["Boston Celtics", "New York Knicks"]
    assert row["typed_key"]["participants_confidence"] == "LOW"
    assert "participants" in row["low_confidence_fields"]
    assert "title_only_participants_low_confidence" in row["blockers"]
    assert row["usable_for_future_overlap_review"] is False


def test_missing_event_time_blocks(tmp_path: Path) -> None:
    draft = _write_draft(tmp_path, [_record(event_time=None)])

    report = build_sx_bet_sports_typed_keys_report(input_path=draft, generated_at=NOW)

    row = report["rows"][0]
    assert row["classification"] == SPORTS_TYPED_KEYS_BLOCKED
    assert "missing_event_time" in row["blockers"]
    assert report["summary"]["blocked"] == 1


def test_missing_void_rules_blocks_exact_review_readiness(tmp_path: Path) -> None:
    record = _record()
    record["settlement"]["void_rule"] = None
    draft = _write_draft(tmp_path, [record])

    report = build_sx_bet_sports_typed_keys_report(input_path=draft, generated_at=NOW)

    row = report["rows"][0]
    assert row["classification"] == SPORTS_TYPED_KEYS_PARTIAL
    assert "missing_void_rules" in row["blockers"]
    assert row["exact_review_ready"] is False
    assert report["summary"]["partial"] == 1


def test_reference_only_rows_remain_unusable(tmp_path: Path) -> None:
    draft = _write_draft(tmp_path, [_record()])

    report = build_sx_bet_sports_typed_keys_report(input_path=draft, generated_at=NOW)

    row = report["rows"][0]
    assert row["reference_only_status"] == REFERENCE_ONLY_UNUSABLE
    assert row["usable_as_executable_market"] is False
    assert row["diagnostic_only"] is True
    assert row["affects_evaluator_gates"] is False
    assert "reference_only_no_executable_market" in row["blockers"]


def test_no_paper_candidate_emitted(tmp_path: Path) -> None:
    draft = _write_draft(tmp_path, [_record()])

    report = build_sx_bet_sports_typed_keys_report(input_path=draft, generated_at=NOW)
    encoded = json.dumps(report)

    assert report["summary"]["candidate_count"] == 0
    assert report["summary"]["pair_count"] == 0
    assert report["safety"]["candidates_or_pairs_created"] is False
    assert report["safety"]["paper_candidate_emitted"] is False
    assert "PAPER_CANDIDATE" not in encoded


def test_missing_draft_report_fails_gracefully(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"

    report = build_sx_bet_sports_typed_keys_report(input_path=missing, generated_at=NOW)

    assert report["summary"]["total_rows"] == 0
    assert report["summary"]["warning_count"] == 1
    assert report["warnings"][0]["blocker"] == "input_report_missing"


def _write_draft(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "sx_bet_normalized_draft.json"
    path.write_text(
        json.dumps(
            {
                "source": "sx_bet_normalized_draft_v1",
                "records": records,
                "safety": {"affects_evaluator_gates": False},
            }
        ),
        encoding="utf-8",
    )
    return path


def _record(**overrides) -> dict:
    record = {
        "venue": "sx_bet",
        "market_id": "0xabc123",
        "event_id": "S1779385200:celtics:knicks",
        "title": "Boston Celtics vs New York Knicks",
        "sport": "Basketball",
        "league": "NBA",
        "event_time": "2026-05-21T23:00:00+00:00",
        "participants": ["Boston Celtics", "New York Knicks"],
        "market_type": 226,
        "line": None,
        "threshold": None,
        "outcomes": [
            {"side": "outcome_one", "name": "Boston Celtics"},
            {"side": "outcome_two", "name": "New York Knicks"},
        ],
        "settlement_rules_text": "Moneyline market; void if event is cancelled.",
        "settlement": {
            "void_rule": "Game cancelled or voided",
            "settlement_source_text": "official league result",
        },
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
    }
    record.update(overrides)
    return record
