from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from relative_value.fees import FlatFeeModel
from relative_value.structural_basket_detector import (
    STATUS_FEES_KILL,
    STATUS_INSUFFICIENT_DEPTH,
    STATUS_NOT_EXHAUSTIVE_EVIDENCE,
    STATUS_STALE_ORDERBOOK,
    STATUS_STOP_FOR_REVIEW,
    build_structural_basket_review_report,
    render_structural_basket_review_markdown,
)


NOW = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)


def _row(market_id: str, ask: float, depth: float = 5.0, captured_at: str = "2026-05-24T11:59:30+00:00", **extra) -> dict:
    row = {
        "venue": "kalshi",
        "market_id": market_id,
        "ticker": market_id.upper(),
        "event_id": "event-1",
        "group_id": "event-1",
        "question": f"Outcome {market_id}",
        "resolution_date": "2026-12-31",
        "settlement_time": "2026-12-31T23:00:00+00:00",
        "close_time": "2026-12-31T22:00:00+00:00",
        "rules": "All outcomes resolve from the same official event result.",
        "settlement_source": "official venue event result",
        "orderbook_enrichment": {
            "best_bid": 0.01,
            "best_ask": ask,
            "midpoint": 0.01,
            "depth_at_best_bid": 100.0,
            "depth_at_best_ask": depth,
            "orderbook_captured_at": captured_at,
        },
    }
    row.update(extra)
    return row


def _snapshot(rows: list[dict]) -> dict:
    return {"schema_version": 1, "normalized_markets": rows}


def _manifest(
    ids: list[str],
    *,
    source: str = "local_manifest_v1",
    expected_count: int | None = None,
    trusted_local_manifest: bool = True,
) -> dict:
    return {
        "exhaustive_groups": [
            {
                "venue": "kalshi",
                "group_id": "event-1",
                "exhaustive": True,
                "source": source,
                "evidence": "hand-reviewed venue event metadata states all outcomes included",
                "trusted_local_manifest": trusted_local_manifest,
                "reviewer": "unit-test-reviewer",
                "reviewed_at": "2026-05-24T12:00:00+00:00",
                "market_tickers": [value.upper() for value in ids],
                "outcome_list": [value.upper() for value in ids],
                "outcome_market_ids": ids,
                "expected_outcome_count": expected_count if expected_count is not None else len(ids),
                "complete": True,
                "settlement_source_raw_evidence": "Official Kalshi event metadata resolves every listed outcome from the same source.",
                "rules_evidence": "All outcomes resolve from the same official event result.",
            }
        ]
    }


def _report(rows: list[dict], manifest: dict | None = None, fee: float = 0.0) -> dict:
    return build_structural_basket_review_report(
        snapshot_payloads=[_snapshot(rows)],
        manifest_payload=manifest,
        detected_at=NOW,
        fee_models={"kalshi": FlatFeeModel(fee)},
        max_quote_age_seconds=120.0,
        min_depth=1.0,
    )


def test_complete_exhaustive_group_sum_asks_below_one_passes_stop_for_review() -> None:
    report = _report(
        [_row("a", 0.20), _row("b", 0.25), _row("c", 0.30)],
        _manifest(["a", "b", "c"]),
        fee=0.01,
    )
    row = report["rows"][0]

    assert row["status"] == STATUS_STOP_FOR_REVIEW
    assert row["sum_asks"] == 0.75
    assert row["conservative_fees"] == 0.03
    assert row["total_cost_after_fees"] == 0.78
    assert row["uses_midpoint"] is False
    assert row["uses_ask_side_only"] is True
    assert row["settlement_audit_status"] == "PASS"
    assert row["resolution_metadata_complete"] is True
    assert row["normalized_resolution_key"]["settlement_source_key"] == "official venue event result"
    assert report["summary"]["stop_for_review_count"] == 1
    assert report["summary"]["paper_candidate_count"] == 0
    assert report["safety"]["paper_candidate_emitted"] is False
    assert "STOP_FOR_REVIEW" in render_structural_basket_review_markdown(report)


def test_incomplete_exhaustive_group_fails_closed() -> None:
    report = _report([_row("a", 0.20), _row("b", 0.25)], _manifest(["a", "b", "c"], expected_count=3))
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert "explicit_exhaustive_group_incomplete" in row["blockers"]
    assert row["paper_candidate_emitted"] is False


def test_all_legs_same_resolution_metadata_can_pass_audit() -> None:
    report = _report([_row("a", 0.20), _row("b", 0.25)], _manifest(["a", "b"]))
    row = report["rows"][0]

    assert row["settlement_audit_status"] == "PASS"
    assert row["settlement_audit_blockers"] == []
    assert row["status"] == STATUS_STOP_FOR_REVIEW


def test_mixed_resolution_date_blocks_stop_for_review() -> None:
    report = _report(
        [_row("a", 0.20), _row("b", 0.25, resolution_date="2027-01-01")],
        _manifest(["a", "b"]),
    )
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert row["settlement_audit_status"] == "FAIL"
    assert "mixed_resolution_timing" in row["settlement_audit_blockers"]
    assert report["summary"]["stop_for_review_count"] == 0


def test_mixed_resolution_criteria_blocks_stop_for_review() -> None:
    report = _report(
        [_row("a", 0.20), _row("b", 0.25, rules="This outcome uses a different tiebreak rule.")],
        _manifest(["a", "b"]),
    )
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert "mixed_resolution_criteria" in row["settlement_audit_blockers"]


def test_missing_resolution_metadata_blocks_stop_for_review() -> None:
    report = _report(
        [_row("a", 0.20), _row("b", 0.25, resolution_date=None, settlement_time=None, rules=None, settlement_source=None)],
        _manifest(["a", "b"]),
    )
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert row["resolution_metadata_complete"] is False
    assert "missing_resolution_metadata" in row["settlement_audit_blockers"]
    assert report["summary"]["stop_for_review_count"] == 0


def test_different_event_group_ids_block_stop_for_review() -> None:
    report = _report(
        [_row("a", 0.20), _row("b", 0.25, event_id="event-2", group_id="event-2")],
        _manifest(["a", "b"]),
    )
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert "mixed_event_group_metadata" in row["settlement_audit_blockers"]


def test_settlement_audit_does_not_use_title_similarity() -> None:
    report = _report(
        [
            _row("a", 0.20, rules="Criteria A", question="Same title"),
            _row("b", 0.25, rules="Criteria B", question="Same title"),
        ],
        _manifest(["a", "b"]),
    )
    row = report["rows"][0]

    assert "mixed_resolution_criteria" in row["settlement_audit_blockers"]
    assert row["resolution_summary"][0]["resolution_criteria_key"] != row["resolution_summary"][1]["resolution_criteria_key"]


def test_settlement_audit_does_not_trust_graph_hints() -> None:
    report = build_structural_basket_review_report(
        snapshot_payloads=[_snapshot([_row("a", 0.20), _row("b", 0.25, settlement_source="different source")])],
        manifest_payload=_manifest(["a", "b"]),
        graph_hints_payload={"settlement_equivalence": [{"group_id": "event-1", "trusted": True}]},
        detected_at=NOW,
        fee_models={"kalshi": FlatFeeModel(0.0)},
    )
    row = report["rows"][0]

    assert "mixed_settlement_source" in row["settlement_audit_blockers"]
    assert report["safety"]["uses_graph_hints_for_exhaustiveness"] is False
    assert report["summary"]["stop_for_review_count"] == 0


def test_unknown_exhaustive_source_is_blocked() -> None:
    report = _report([_row("a", 0.20), _row("b", 0.25)], _manifest(["a", "b"], source="manual_guess"))
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert "exhaustive_evidence_source_not_trusted" in row["blockers"]


def test_title_only_exhaustive_source_is_blocked() -> None:
    report = _report([_row("a", 0.20), _row("b", 0.25)], _manifest(["a", "b"], source="title_similarity"))
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert "exhaustive_evidence_source_not_trusted" in row["blockers"]
    assert report["safety"]["uses_title_similarity_for_exhaustiveness"] is False


def test_stale_orderbook_fails_closed() -> None:
    report = _report(
        [_row("a", 0.20, captured_at="2026-05-24T11:00:00+00:00"), _row("b", 0.25)],
        _manifest(["a", "b"]),
    )
    row = report["rows"][0]

    assert row["status"] == STATUS_STALE_ORDERBOOK
    assert "stale_orderbook" in row["blockers"]


def test_missing_depth_fails_closed() -> None:
    report = _report([_row("a", 0.20, depth=0.0), _row("b", 0.25)], _manifest(["a", "b"]))
    row = report["rows"][0]

    assert row["status"] == STATUS_INSUFFICIENT_DEPTH
    assert "insufficient_ask_depth" in row["blockers"]


def test_fees_can_kill_apparent_candidate() -> None:
    report = _report([_row("a", 0.49), _row("b", 0.49)], _manifest(["a", "b"]), fee=0.02)
    row = report["rows"][0]

    assert row["sum_asks"] == 0.98
    assert row["total_cost_after_fees"] == 1.02
    assert row["status"] == STATUS_FEES_KILL
    assert "fees_kill_or_no_positive_basket_gap" in row["blockers"]


def test_midpoint_is_never_used_for_candidate() -> None:
    report = _report([_row("a", 0.60), _row("b", 0.60)], _manifest(["a", "b"]))
    row = report["rows"][0]

    assert row["sum_asks"] == 1.20
    assert row["status"] == STATUS_FEES_KILL
    assert row["uses_midpoint"] is False
    assert report["safety"]["uses_midpoint"] is False


def test_graph_hint_exhaustive_source_is_blocked() -> None:
    report = build_structural_basket_review_report(
        snapshot_payloads=[_snapshot([_row("a", 0.20), _row("b", 0.25)])],
        manifest_payload=_manifest(["a", "b"], source="graph_hint"),
        graph_hints_payload={"groups": [{"group_id": "event-1", "relation_type": "EXHAUSTIVE"}]},
        detected_at=NOW,
        fee_models={"kalshi": FlatFeeModel(0.0)},
    )
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert "exhaustive_evidence_source_not_trusted" in row["blockers"]
    assert report["safety"]["uses_graph_hints_for_exhaustiveness"] is False
    assert report["safety"]["graph_hints_payload_ignored"] is True


def test_trusted_local_manifest_v1_requires_trusted_local_manifest_true() -> None:
    report = _report(
        [_row("a", 0.20), _row("b", 0.25)],
        _manifest(["a", "b"], source="local_manifest_v1", trusted_local_manifest=False),
    )
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert "trusted_local_manifest_required" in row["blockers"]


def test_local_manifest_fixture_is_explicit_exhaustive_evidence() -> None:
    fixture = Path("tests/fixtures/local_manifest_v1/kalshi_event_manifest.json")
    manifest = json.loads(fixture.read_text(encoding="utf-8"))
    report = _report([_row("a", 0.20), _row("b", 0.25)], manifest)
    row = report["rows"][0]

    assert row["status"] == STATUS_STOP_FOR_REVIEW
    assert row["evidence"]["source"] == "local_manifest_v1"
    assert row["evidence"]["manifest"]["reviewer"] == "unit-test-reviewer"
    assert row["paper_candidate_emitted"] is False
    assert report["summary"]["paper_candidate_count"] == 0


def test_local_manifest_missing_reviewer_and_reviewed_at_fails_closed() -> None:
    manifest = _manifest(["a", "b"])
    group = manifest["exhaustive_groups"][0]
    group.pop("reviewer")
    group.pop("reviewed_at")
    report = _report([_row("a", 0.20), _row("b", 0.25)], manifest)
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert "missing_manifest_reviewer" in row["blockers"]
    assert "missing_manifest_reviewed_at" in row["blockers"]
    assert report["summary"]["stop_for_review_count"] == 0


def test_local_manifest_incomplete_outcome_list_fails_closed() -> None:
    manifest = _manifest(["a", "b"])
    manifest["exhaustive_groups"][0]["outcome_list"] = ["A"]
    report = _report([_row("a", 0.20), _row("b", 0.25)], manifest)
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert "incomplete_manifest_outcome_list" in row["blockers"]


def test_local_manifest_title_only_evidence_fails_closed() -> None:
    manifest = _manifest(["a", "b"])
    group = manifest["exhaustive_groups"][0]
    group["evidence"] = "title similarity says this is a complete group"
    report = _report([_row("a", 0.20), _row("b", 0.25)], manifest)
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert "title_only_manifest_evidence" in row["blockers"]


def test_local_manifest_graph_hint_evidence_fails_closed() -> None:
    manifest = _manifest(["a", "b"])
    group = manifest["exhaustive_groups"][0]
    group["rules_evidence"] = "market graph hint marks the outcomes exhaustive"
    report = _report([_row("a", 0.20), _row("b", 0.25)], manifest)
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert "graph_hint_manifest_evidence" in row["blockers"]


def test_local_manifest_reference_only_source_fails_closed() -> None:
    manifest = _manifest(["a", "b"])
    manifest["exhaustive_groups"][0]["source_kind"] = "reference"
    report = _report([_row("a", 0.20), _row("b", 0.25)], manifest)
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert "reference_only_source" in row["blockers"]
    assert report["summary"]["stop_for_review_count"] == 0


def test_kalshi_event_metadata_requires_venue_native_true() -> None:
    report = _report([_row("a", 0.20), _row("b", 0.25)], _manifest(["a", "b"], source="kalshi_event_metadata"))
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert "venue_native_exhaustive_evidence_required" in row["blockers"]


def test_reference_only_row_is_blocked() -> None:
    report = _report(
        [_row("a", 0.20, reference_only=True), _row("b", 0.25)],
        _manifest(["a", "b"]),
    )
    row = report["rows"][0]

    assert row["status"] == STATUS_NOT_EXHAUSTIVE_EVIDENCE
    assert "reference_only_source" in row["blockers"]


def test_reference_only_row_cannot_enter_stop_for_review() -> None:
    report = _report(
        [_row("a", 0.20, source_kind="reference"), _row("b", 0.25)],
        _manifest(["a", "b"]),
    )

    assert report["summary"]["stop_for_review_count"] == 0
    assert report["rows"][0]["status"] != STATUS_STOP_FOR_REVIEW
    assert "reference_only_source" in report["rows"][0]["blockers"]


def test_native_venue_exhaustive_group_metadata_is_accepted() -> None:
    evidence = {
        "group_id": "event-1",
        "all_outcomes_included": True,
        "source": "kalshi_event_metadata",
        "evidence": "venue native event metadata",
        "outcome_market_ids": ["a", "b"],
        "expected_outcome_count": 2,
    }
    report = _report(
        [
            _row("a", 0.20, exhaustive_group=evidence),
            _row("b", 0.25, exhaustive_group=evidence),
        ],
        manifest=None,
    )

    assert report["rows"][0]["status"] == STATUS_STOP_FOR_REVIEW
    assert report["rows"][0]["evidence"]["venue_native"] is True
