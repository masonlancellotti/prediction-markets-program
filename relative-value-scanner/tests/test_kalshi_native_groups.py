from __future__ import annotations

import json
from datetime import datetime, timezone

from relative_value.kalshi_native_groups import audit_kalshi_native_groups, render_kalshi_native_groups_markdown


NOW = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)


def _market(ticker: str, outcome: str, **extra) -> dict:
    row = {
        "ticker": ticker,
        "yes_sub_title": outcome,
        "question": f"{outcome} wins",
        "orderbook_enrichment": {
            "best_ask": 0.20,
            "depth_at_best_ask": 5.0,
            "orderbook_captured_at": "2026-05-24T11:59:30+00:00",
        },
    }
    row.update(extra)
    return row


def _event(**overrides) -> dict:
    event = {
        "event_ticker": "KXTEST-2026",
        "event_id": "event-1",
        "series_ticker": "KXTEST",
        "outcome_list": ["A", "B", "C"],
        "all_outcomes_included": True,
        "markets": [
            _market("KXTEST-A", "A"),
            _market("KXTEST-B", "B"),
            _market("KXTEST-C", "C"),
        ],
    }
    event.update(overrides)
    return event


def _audit(payload: dict) -> dict:
    return audit_kalshi_native_groups(payload, generated_at=NOW)


def test_explicit_complete_event_group_becomes_trusted_source_candidate() -> None:
    report = _audit({"events": [_event()]})
    group = report["groups"][0]

    assert group["status"] == "COMPLETE_EXHAUSTIVE_GROUP"
    assert group["blockers"] == []
    assert group["source"] == "kalshi_event_metadata"
    assert group["venue_native"] is True
    assert report["summary"]["complete_groups"] == 1
    assert report["summary"]["candidate_input_row_count"] == 3
    candidate = report["structural_basket_detector_inputs"][0]
    assert candidate["exhaustive_group"]["source"] == "kalshi_event_metadata"
    assert candidate["exhaustive_group"]["venue_native"] is True
    assert candidate["exhaustive_group"]["all_outcomes_included"] is True
    assert candidate["exhaustive_group"]["expected_outcome_count"] == 3


def test_missing_outcome_list_blocks() -> None:
    event = _event()
    event.pop("outcome_list")
    report = _audit({"events": [event]})
    group = report["groups"][0]

    assert group["status"] == "INCOMPLETE_GROUP"
    assert "missing_outcome_list" in group["blockers"]
    assert "partial_event_metadata" in group["blockers"]
    assert report["summary"]["candidate_input_row_count"] == 0


def test_missing_completeness_blocks() -> None:
    event = _event()
    event.pop("all_outcomes_included")
    report = _audit({"events": [event]})
    group = report["groups"][0]

    assert group["status"] == "INCOMPLETE_GROUP"
    assert "missing_completeness_evidence" in group["blockers"]
    assert "not_explicitly_exhaustive" in group["blockers"]
    assert report["summary"]["candidate_input_row_count"] == 0


def test_title_only_group_blocks() -> None:
    report = _audit(
        {
            "normalized_markets": [
                _market("KXTEST-A", "A", event_title="Shared Event Title"),
                _market("KXTEST-B", "B", event_title="Shared Event Title"),
            ]
        }
    )
    group = report["groups"][0]

    assert group["status"] == "INCOMPLETE_GROUP"
    assert "title_only_group_not_trusted" in group["blockers"]
    assert "missing_venue_native_event_id" in group["blockers"]
    assert report["safety"]["uses_title_similarity_for_exhaustiveness"] is False


def test_partial_outcome_set_blocks() -> None:
    event = _event(markets=[_market("KXTEST-A", "A"), _market("KXTEST-B", "B")])
    report = _audit({"events": [event]})
    group = report["groups"][0]

    assert group["status"] == "INCOMPLETE_GROUP"
    assert "partial_event_metadata" in group["blockers"]
    assert report["summary"]["candidate_input_row_count"] == 0


def test_reference_only_source_blocks() -> None:
    event = _event(reference_only=True)
    report = _audit({"events": [event]})
    group = report["groups"][0]

    assert group["status"] == "INCOMPLETE_GROUP"
    assert "reference_only_source" in group["blockers"]
    assert report["summary"]["candidate_input_row_count"] == 0


def test_adapter_never_emits_stop_for_review_or_paper_candidate() -> None:
    report = _audit({"events": [_event()]})
    encoded = json.dumps(report)
    markdown = render_kalshi_native_groups_markdown(report)

    assert report["summary"]["stop_for_review_count"] == 0
    assert report["summary"]["paper_candidate_count"] == 0
    assert report["safety"]["stop_for_review_emitted"] is False
    assert report["safety"]["paper_candidate_emitted"] is False
    assert "STOP_FOR_REVIEW" not in encoded
    assert "PAPER_CANDIDATE" not in encoded
    assert "STOP_FOR_REVIEW" not in markdown
