from __future__ import annotations

import json
from datetime import datetime, timezone

from relative_value.paper_fill_simulator import simulate_paper_fill_journal
from relative_value.structural_manifest_scout import (
    STATUS_BLOCKED_DEPTH,
    STATUS_BLOCKED_METADATA,
    STATUS_BLOCKED_REFERENCE_ONLY,
    STATUS_BLOCKED_STALE,
    STATUS_MANIFEST_REVIEW_CANDIDATE,
    scout_structural_manifest_candidates,
)


NOW = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)


def _market(
    ticker: str,
    outcome: str,
    *,
    event_ticker: str = "KXTEST-26",
    ask: float = 0.20,
    depth: float = 5.0,
    captured_at: str = "2026-05-24T11:59:30+00:00",
    **extra,
) -> dict:
    row = {
        "venue": "kalshi",
        "event_id": event_ticker,
        "ticker": ticker,
        "question": f"{outcome} wins",
        "raw": {
            "event_ticker": event_ticker,
            "ticker": ticker,
            "yes_sub_title": outcome,
            "title": f"{outcome} wins",
            "rules_primary": "Resolution source is official Kalshi event metadata.",
            "rules_secondary": "All outcomes use the same listed event result.",
            "close_time": "2026-12-31T22:00:00Z",
            "expected_expiration_time": "2026-12-31T23:00:00Z",
            "expiration_time": "2027-01-01T00:00:00Z",
            "latest_expiration_time": "2027-01-01T00:00:00Z",
            "yes_ask_dollars": ask,
            "yes_ask_size_fp": depth,
            "updated_time": captured_at,
        },
    }
    row.update(extra)
    return row


def _payload(rows: list[dict]) -> dict:
    return {"normalized_markets": rows}


def _report(rows: list[dict], **kwargs) -> dict:
    return scout_structural_manifest_candidates(_payload(rows), generated_at=NOW, **kwargs)


def test_scout_never_emits_stop_for_review_or_paper_candidate() -> None:
    report = _report([_market("KXTEST-A", "A"), _market("KXTEST-B", "B")])
    encoded = json.dumps(report)

    assert "STOP_FOR_REVIEW" not in encoded
    assert "PAPER_CANDIDATE" not in encoded
    assert "STRUCTURAL_BASKET_REVIEW" not in encoded
    assert report["summary"]["paper_candidate_count"] == 0
    assert report["safety"]["paper_candidate_emitted"] is False


def test_low_provisional_sum_asks_remains_manifest_review_only() -> None:
    report = _report([_market("KXTEST-A", "A", ask=0.05), _market("KXTEST-B", "B", ask=0.06)])
    row = report["rows"][0]

    assert row["status"] == STATUS_MANIFEST_REVIEW_CANDIDATE
    assert row["provisional_sum_asks"] == 0.11
    assert row["diagnostic_only"] is True
    assert row["not_exhaustive_evidence"] is True
    assert row["requires_local_manifest"] is True
    assert row["affects_evaluator_gates"] is False


def test_scout_rows_are_rejected_by_paper_fill_simulator_as_ungated() -> None:
    report = _report([_market("KXTEST-A", "A", ask=0.05), _market("KXTEST-B", "B", ask=0.06)])
    journal = simulate_paper_fill_journal(input_payload={"rows": report["rows"]}, generated_at=NOW)

    assert journal["summary"]["simulated_fill_count"] == 0
    assert journal["summary"]["blocked_count"] == 1
    assert "ungated_exact_same_payoff_row" in journal["journal"][0]["blockers"]


def test_title_only_grouping_is_blocked() -> None:
    title_only_raw = {
        "title": "Shared title",
        "yes_sub_title": "A",
        "rules_primary": "Resolution source is official Kalshi event metadata.",
        "rules_secondary": "All outcomes use the same listed event result.",
        "close_time": "2026-12-31T22:00:00Z",
        "expected_expiration_time": "2026-12-31T23:00:00Z",
        "expiration_time": "2027-01-01T00:00:00Z",
        "latest_expiration_time": "2027-01-01T00:00:00Z",
        "yes_ask_dollars": 0.20,
        "yes_ask_size_fp": 5.0,
        "updated_time": "2026-05-24T11:59:30+00:00",
    }
    report = _report(
        [
            _market("KXTEST-A", "A", event_ticker="", event_id=None, raw=title_only_raw),
            _market("KXTEST-B", "B", event_ticker="", event_id=None, raw={**title_only_raw, "yes_sub_title": "B"}),
        ]
    )
    row = report["rows"][0]

    assert row["status"] == STATUS_BLOCKED_METADATA
    assert "title_only_group_not_trusted" in row["missing_metadata_blockers"]
    assert report["safety"]["uses_title_similarity_for_exhaustiveness"] is False


def test_reference_only_rows_are_blocked() -> None:
    report = _report([_market("KXTEST-A", "A", reference_only=True), _market("KXTEST-B", "B")])
    row = report["rows"][0]

    assert row["status"] == STATUS_BLOCKED_REFERENCE_ONLY
    assert "reference_only_source" in row["missing_metadata_blockers"]


def test_stale_blocker_works() -> None:
    report = _report(
        [_market("KXTEST-A", "A", captured_at="2026-05-24T10:00:00+00:00"), _market("KXTEST-B", "B")],
        max_quote_age_seconds=60.0,
    )
    row = report["rows"][0]

    assert row["status"] == STATUS_BLOCKED_STALE
    assert "stale_quote" in row["missing_metadata_blockers"]


def test_depth_blocker_works() -> None:
    report = _report([_market("KXTEST-A", "A", depth=0.0), _market("KXTEST-B", "B")])
    row = report["rows"][0]

    assert row["status"] == STATUS_BLOCKED_DEPTH
    assert "insufficient_depth" in row["missing_metadata_blockers"]


def test_mixed_or_missing_metadata_blocks_manifest_candidate() -> None:
    mixed = _market(
        "KXTEST-B",
        "B",
        raw={
            "event_ticker": "KXTEST-26",
            "ticker": "KXTEST-B",
            "yes_sub_title": "B",
            "rules_primary": "Different rules.",
            "close_time": "2027-01-01T22:00:00Z",
            "yes_ask_dollars": 0.20,
            "yes_ask_size_fp": 5.0,
            "updated_time": "2026-05-24T11:59:30+00:00",
        },
    )
    report = _report([_market("KXTEST-A", "A"), mixed])
    row = report["rows"][0]

    assert row["status"] == STATUS_BLOCKED_METADATA
    assert "missing_or_mixed_rules" in row["missing_metadata_blockers"]
    assert "missing_or_mixed_times" in row["missing_metadata_blockers"]
