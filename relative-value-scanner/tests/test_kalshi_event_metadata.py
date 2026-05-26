from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from relative_value.kalshi_event_metadata import (
    EVENT_METADATA_AUDIT_SOURCE,
    EVENT_METADATA_JOIN_SOURCE,
    KALSHI_EVENT_METADATA_EVIDENCE_SOURCE,
    NORMALIZER_SOURCE,
    NormalizedKalshiEventMetadata,
    audit_kalshi_event_metadata,
    audit_kalshi_event_metadata_files,
    join_kalshi_event_metadata,
    join_kalshi_event_metadata_files,
    normalize_kalshi_event_metadata_payload,
    render_kalshi_event_metadata_audit_markdown,
    render_kalshi_event_metadata_join_markdown,
)
from relative_value.kalshi_native_groups import audit_kalshi_native_groups
from relative_value.structural_basket_detector import (
    STATUS_INSUFFICIENT_DEPTH,
    STATUS_NOT_EXHAUSTIVE_EVIDENCE,
    STATUS_STALE_ORDERBOOK,
    STATUS_STOP_FOR_REVIEW,
    build_structural_basket_review_report,
)


NOW = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kalshi_event_metadata"


def _complete_event_payload() -> dict:
    return json.loads((FIXTURE_DIR / "complete_event.json").read_text(encoding="utf-8"))


def _matching_snapshot_payload() -> dict:
    return json.loads((FIXTURE_DIR / "snapshot_matching_complete_event.json").read_text(encoding="utf-8"))


def _snapshot_with_reference_only_market(field: str, value) -> dict:
    snapshot = _matching_snapshot_payload()
    snapshot["events"][0]["markets"][0][field] = value
    return snapshot


def _market(ticker: str, outcome: str, *, ask: float = 0.20, depth: float = 25.0, captured: str = "2026-05-24T11:59:30+00:00") -> dict:
    return {
        "ticker": ticker,
        "market_ticker": ticker,
        "yes_sub_title": outcome,
        "title": f"{outcome} wins",
        "rules_primary": "Resolution source is official Kalshi event metadata.",
        "close_time": "2026-12-31T22:00:00Z",
        "expected_expiration_time": "2026-12-31T23:00:00Z",
        "expiration_time": "2027-01-01T00:00:00Z",
        "latest_expiration_time": "2027-01-01T00:00:00Z",
        "outcomes": ["Yes", "No"],
        "orderbook_enrichment": {
            "best_ask": ask,
            "depth_at_best_ask": depth,
            "orderbook_captured_at": captured,
        },
    }


def test_normalize_complete_event_metadata_is_trusted() -> None:
    events = normalize_kalshi_event_metadata_payload(_complete_event_payload(), source_path="complete_event.json")

    assert len(events) == 1
    event = events[0]
    assert event.source == NORMALIZER_SOURCE
    assert event.event_ticker == "KXEVT-2026-EXAMPLE"
    assert event.outcome_list == ["Outcome A", "Outcome B", "Outcome C"]
    assert event.complete is True
    assert event.market_tickers == [
        "KXEVT-2026-EXAMPLE-A",
        "KXEVT-2026-EXAMPLE-B",
        "KXEVT-2026-EXAMPLE-C",
    ]
    assert event.blockers == []
    assert event.reference_only is False
    assert event.is_trusted_for_completeness() is True


def test_normalizer_does_not_promote_per_market_yes_no_to_event_outcome_list() -> None:
    payload = {
        "event_ticker": "KXBTC-26MAY",
        "event_id": "kxbtc-26may",
        "outcomes": ["Yes", "No"],
        "complete": True,
        "rules_primary": "Threshold ladder.",
        "settlement_source_raw_evidence": "cf benchmarks btc index",
        "markets": [
            {"market_ticker": "KXBTC-26MAY-T86000", "outcomes": ["Yes", "No"]},
            {"market_ticker": "KXBTC-26MAY-T87000", "outcomes": ["Yes", "No"]},
        ],
    }
    event = normalize_kalshi_event_metadata_payload(payload)[0]

    assert event.outcome_list is None
    assert "missing_event_outcome_list" in event.blockers
    assert "per_market_binary_outcomes_only_at_event_level" in event.blockers
    assert event.is_trusted_for_completeness() is False
    assert event.per_market_binary_outcomes_seen == [["Yes", "No"], ["Yes", "No"]]


def test_title_only_event_metadata_is_blocked() -> None:
    payload = {
        "title": "Some event with no tickers",
        "outcome_list": ["A", "B"],
        "complete": True,
        "rules_primary": "Some rule",
        "settlement_source_raw_evidence": "Some source",
        "markets": [],
    }
    event = normalize_kalshi_event_metadata_payload(payload)[0]

    assert "missing_event_ticker" in event.blockers
    assert "missing_event_id" in event.blockers
    assert "title_only_event" in event.blockers
    assert event.is_trusted_for_completeness() is False


def test_count_only_evidence_does_not_create_completeness() -> None:
    payload = {
        "event_ticker": "KXEVT-COUNT",
        "event_id": "kxevt-count",
        "rules_primary": "Some rule",
        "settlement_source_raw_evidence": "Some source",
        "expected_outcome_count": 3,
        "markets": [
            {"market_ticker": "KXEVT-COUNT-A"},
            {"market_ticker": "KXEVT-COUNT-B"},
            {"market_ticker": "KXEVT-COUNT-C"},
        ],
    }
    event = normalize_kalshi_event_metadata_payload(payload)[0]

    assert event.outcome_list is None
    assert "missing_event_outcome_list" in event.blockers
    assert "event_not_marked_complete" in event.blockers
    assert event.is_trusted_for_completeness() is False


def test_outcome_count_market_count_mismatch_blocks() -> None:
    payload = {
        "event_ticker": "KXEVT-MISMATCH",
        "event_id": "kxevt-mismatch",
        "outcome_list": ["A", "B", "C"],
        "complete": True,
        "rules_primary": "Some rule",
        "settlement_source_raw_evidence": "Some source",
        "markets": [
            {"market_ticker": "KXEVT-MISMATCH-A"},
            {"market_ticker": "KXEVT-MISMATCH-B"},
        ],
    }
    event = normalize_kalshi_event_metadata_payload(payload)[0]

    assert "outcome_count_vs_market_count_mismatch" in event.blockers
    assert event.is_trusted_for_completeness() is False


def test_mixed_market_rules_blocks() -> None:
    payload = {
        "event_ticker": "KXEVT-MIXED-RULES",
        "event_id": "kxevt-mixed-rules",
        "outcome_list": ["A", "B"],
        "complete": True,
        "rules_primary": "Top-level rule",
        "settlement_source_raw_evidence": "Top-level source",
        "markets": [
            {"market_ticker": "KXEVT-MIXED-RULES-A", "rules_primary": "Rule one"},
            {"market_ticker": "KXEVT-MIXED-RULES-B", "rules_primary": "Rule two — wildly different"},
        ],
    }
    event = normalize_kalshi_event_metadata_payload(payload)[0]

    assert "mixed_market_rules" in event.blockers
    assert event.is_trusted_for_completeness() is False


def test_mixed_market_times_blocks() -> None:
    payload = {
        "event_ticker": "KXEVT-MIXED-TIMES",
        "event_id": "kxevt-mixed-times",
        "outcome_list": ["A", "B"],
        "complete": True,
        "rules_primary": "Top-level rule",
        "settlement_source_raw_evidence": "Top-level source",
        "markets": [
            {"market_ticker": "KXEVT-MIXED-TIMES-A", "close_time": "2026-12-31T22:00:00Z"},
            {"market_ticker": "KXEVT-MIXED-TIMES-B", "close_time": "2027-01-01T22:00:00Z"},
        ],
    }
    event = normalize_kalshi_event_metadata_payload(payload)[0]

    assert "mixed_market_times" in event.blockers
    assert event.is_trusted_for_completeness() is False


def test_reference_only_metadata_blocks_trust() -> None:
    payload = dict(_complete_event_payload())
    payload["reference_only"] = True
    event = normalize_kalshi_event_metadata_payload(payload)[0]

    assert event.reference_only is True
    assert "reference_only_source" in event.blockers
    assert event.is_trusted_for_completeness() is False


def test_missing_rules_or_settlement_evidence_blocks() -> None:
    base = _complete_event_payload()
    no_rules = dict(base)
    no_rules.pop("rules_primary", None)
    no_rules.pop("rules_secondary", None)
    no_rules["markets"] = [{**m} for m in base["markets"]]
    for market in no_rules["markets"]:
        market.pop("rules_primary", None)
    no_settlement = dict(base)
    no_settlement.pop("settlement_source_raw_evidence", None)

    assert "missing_rules_evidence" in normalize_kalshi_event_metadata_payload(no_rules)[0].blockers
    assert "missing_settlement_source_evidence" in normalize_kalshi_event_metadata_payload(no_settlement)[0].blockers


def test_audit_never_emits_stop_for_review_or_paper_candidate() -> None:
    payload = _complete_event_payload()
    blocked_payload = {
        "event_ticker": "KXEVT-BLOCKED",
        "outcomes": ["Yes", "No"],
        "markets": [{"market_ticker": "X", "outcomes": ["Yes", "No"]}],
    }
    report = audit_kalshi_event_metadata([payload, blocked_payload], generated_at=NOW)
    encoded = json.dumps(report)

    assert "STOP_FOR_REVIEW" not in encoded
    assert "PAPER_CANDIDATE" not in encoded
    assert report["source"] == EVENT_METADATA_AUDIT_SOURCE
    assert report["summary"]["stop_for_review_count"] == 0
    assert report["summary"]["paper_candidate_count"] == 0
    assert report["summary"]["events_trusted_for_completeness"] == 1
    assert report["summary"]["events_blocked"] >= 1
    assert report["safety"]["paper_candidate_emitted"] is False
    assert report["safety"]["stop_for_review_emitted"] is False
    assert report["safety"]["affects_evaluator_gates"] is False


def test_join_with_matching_snapshot_enables_kalshi_native_groups_candidate() -> None:
    metadata_payload = _complete_event_payload()
    snapshot_payload = _matching_snapshot_payload()

    result = join_kalshi_event_metadata(
        snapshot_payload=snapshot_payload,
        metadata_payloads=[metadata_payload],
        generated_at=NOW,
        snapshot_path="snapshot.json",
        source_paths=["complete_event.json"],
    )
    report = result["report"]
    enriched = result["enriched_snapshot"]

    assert report["source"] == EVENT_METADATA_JOIN_SOURCE
    assert report["summary"]["events_matched_to_snapshot"] == 1
    assert report["summary"]["events_trusted_after_join"] == 1
    assert report["summary"]["stop_for_review_count"] == 0
    assert report["summary"]["paper_candidate_count"] == 0

    audit = audit_kalshi_native_groups(enriched, generated_at=NOW)
    assert audit["summary"]["candidate_input_row_count"] == 3
    assert audit["summary"]["stop_for_review_count"] == 0
    assert audit["summary"]["paper_candidate_count"] == 0
    group = audit["groups"][0]
    assert group["status"] == "COMPLETE_EXHAUSTIVE_GROUP"
    assert group["group_classification"] == "COMPLETE_EVENT_GROUP"
    assert group["outcome_list"] == ["Outcome A", "Outcome B", "Outcome C"]


def test_market_only_snapshot_without_metadata_still_fails_closed() -> None:
    snapshot_payload = {
        "normalized_markets": [
            _market("KXBARE-A", "Outcome A"),
            _market("KXBARE-B", "Outcome B"),
        ]
    }
    for market in snapshot_payload["normalized_markets"]:
        market["event_ticker"] = "KXBARE"
        market["event_id"] = "KXBARE"

    audit = audit_kalshi_native_groups(snapshot_payload, generated_at=NOW)

    assert audit["summary"]["candidate_input_row_count"] == 0
    assert any("missing_outcome_list" in (group["blockers"]) for group in audit["groups"])


def test_join_blocks_when_metadata_tickers_absent_from_snapshot() -> None:
    metadata_payload = _complete_event_payload()
    snapshot_payload = _matching_snapshot_payload()
    snapshot_payload["events"][0]["markets"] = snapshot_payload["events"][0]["markets"][:2]

    result = join_kalshi_event_metadata(
        snapshot_payload=snapshot_payload,
        metadata_payloads=[metadata_payload],
        generated_at=NOW,
    )
    report = result["report"]
    row = report["rows"][0]

    assert row["matched_to_snapshot"] is True
    assert row["missing_in_snapshot"] == ["KXEVT-2026-EXAMPLE-C"]
    assert "manifest_market_tickers_absent_from_snapshot" in row["join_blockers"]
    assert row["trusted_for_completeness_after_join"] is False

    # The injected event block must NOT carry completeness markers when the join fails.
    enriched_event = next(
        event
        for event in result["enriched_snapshot"]["events"]
        if event.get("event_metadata_source") == KALSHI_EVENT_METADATA_EVIDENCE_SOURCE
    )
    assert "outcome_list" not in enriched_event
    assert enriched_event.get("event_metadata_trusted_for_completeness") is False


def test_join_blocks_when_metadata_is_reference_only() -> None:
    metadata_payload = dict(_complete_event_payload())
    metadata_payload["reference_only"] = True

    result = join_kalshi_event_metadata(
        snapshot_payload=_matching_snapshot_payload(),
        metadata_payloads=[metadata_payload],
        generated_at=NOW,
    )
    row = result["report"]["rows"][0]

    assert "reference_only_source" in row["metadata_blockers"]
    assert row["trusted_for_completeness_after_join"] is False
    audit = audit_kalshi_native_groups(result["enriched_snapshot"], generated_at=NOW)
    assert audit["summary"]["candidate_input_row_count"] == 0
    # The enriched event carries a reference_only marker; native groups audit must block it.
    assert any("reference_only_source" in group["blockers"] for group in audit["groups"])


def test_structural_basket_still_requires_freshness_and_depth_after_join() -> None:
    metadata_payload = _complete_event_payload()
    snapshot_payload = _matching_snapshot_payload()
    # Force stale orderbook for one market.
    snapshot_payload["events"][0]["markets"][0]["orderbook_enrichment"]["orderbook_captured_at"] = (
        "2026-05-24T00:00:00+00:00"
    )
    enriched = join_kalshi_event_metadata(
        snapshot_payload=snapshot_payload,
        metadata_payloads=[metadata_payload],
        generated_at=NOW,
    )["enriched_snapshot"]
    audit = audit_kalshi_native_groups(enriched, generated_at=NOW)
    rows = audit["structural_basket_detector_inputs"]
    assert rows, "expected candidate input rows after join (so downstream gates can be tested)"

    report = build_structural_basket_review_report(
        snapshot_payloads=[{"normalized_markets": rows}],
        detected_at=NOW,
        max_quote_age_seconds=60.0,
    )
    statuses = [row["status"] for row in report["rows"]]

    assert report["summary"]["stop_for_review_count"] == 0
    assert STATUS_STOP_FOR_REVIEW not in statuses
    # At least one gate (stale orderbook, missing depth, or not-exhaustive) must block.
    assert any(
        status in {STATUS_STALE_ORDERBOOK, STATUS_INSUFFICIENT_DEPTH, STATUS_NOT_EXHAUSTIVE_EVIDENCE}
        for status in statuses
    )


def test_structural_basket_blocks_when_depth_below_min_after_join() -> None:
    metadata_payload = _complete_event_payload()
    snapshot_payload = _matching_snapshot_payload()
    for market in snapshot_payload["events"][0]["markets"]:
        market["orderbook_enrichment"]["depth_at_best_ask"] = 0.5
    enriched = join_kalshi_event_metadata(
        snapshot_payload=snapshot_payload,
        metadata_payloads=[metadata_payload],
        generated_at=NOW,
    )["enriched_snapshot"]
    audit = audit_kalshi_native_groups(enriched, generated_at=NOW)
    rows = audit["structural_basket_detector_inputs"]

    report = build_structural_basket_review_report(
        snapshot_payloads=[{"normalized_markets": rows}],
        detected_at=NOW,
        max_quote_age_seconds=1800.0,
        min_depth=1.0,
    )
    statuses = [row["status"] for row in report["rows"]]

    assert report["summary"]["stop_for_review_count"] == 0
    assert STATUS_INSUFFICIENT_DEPTH in statuses


def test_audit_and_join_file_helpers_write_outputs(tmp_path: Path) -> None:
    metadata_path = tmp_path / "complete_event.json"
    metadata_path.write_text(json.dumps(_complete_event_payload()), encoding="utf-8")
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(_matching_snapshot_payload()), encoding="utf-8")

    audit_json = tmp_path / "audit.json"
    audit_md = tmp_path / "audit.md"
    audit_kalshi_event_metadata_files(
        metadata_paths=[metadata_path],
        json_output=audit_json,
        markdown_output=audit_md,
        generated_at=NOW,
    )
    assert audit_json.exists()
    assert "Kalshi Event Metadata Audit" in audit_md.read_text(encoding="utf-8")

    join_json = tmp_path / "join.json"
    join_md = tmp_path / "join.md"
    enriched_out = tmp_path / "enriched.json"
    result = join_kalshi_event_metadata_files(
        snapshot_path=snapshot_path,
        metadata_paths=[metadata_path],
        json_output=join_json,
        markdown_output=join_md,
        enriched_snapshot_output=enriched_out,
        generated_at=NOW,
    )
    assert join_json.exists()
    assert "Kalshi Event Metadata Join Report" in join_md.read_text(encoding="utf-8")
    assert enriched_out.exists()
    enriched_payload = json.loads(enriched_out.read_text(encoding="utf-8"))
    assert any(
        event.get("event_metadata_trusted_for_completeness") is True
        for event in enriched_payload.get("events", [])
    )
    assert result["report"]["summary"]["events_trusted_after_join"] == 1


def test_render_markdown_includes_safety_notice_strings() -> None:
    metadata_payload = _complete_event_payload()
    audit_report = audit_kalshi_event_metadata([metadata_payload], generated_at=NOW)
    join_report = join_kalshi_event_metadata(
        snapshot_payload=_matching_snapshot_payload(),
        metadata_payloads=[metadata_payload],
        generated_at=NOW,
    )["report"]

    audit_md = render_kalshi_event_metadata_audit_markdown(audit_report)
    join_md = render_kalshi_event_metadata_join_markdown(join_report)

    assert "Saved-file-only diagnostic" in audit_md
    assert "STOP_FOR_REVIEW" not in audit_md
    assert "Saved-file-only diagnostic" in join_md
    assert "STOP_FOR_REVIEW" not in join_md


def test_normalized_dataclass_is_immutable() -> None:
    event = normalize_kalshi_event_metadata_payload(_complete_event_payload())[0]
    try:
        event.event_ticker = "tampered"  # type: ignore[misc]
    except Exception as exc:
        assert isinstance(exc, (AttributeError, TypeError))
    else:
        raise AssertionError("NormalizedKalshiEventMetadata must be frozen")
    assert isinstance(event, NormalizedKalshiEventMetadata)


# ---------------------------------------------------------------------------
# join_kalshi_event_metadata -> enriched normalized_markets path
# ---------------------------------------------------------------------------


def _detect(enriched_snapshot: dict, *, max_quote_age_seconds: float = 1800.0, min_depth: float = 1.0) -> dict:
    return build_structural_basket_review_report(
        snapshot_payloads=[enriched_snapshot],
        detected_at=NOW,
        max_quote_age_seconds=max_quote_age_seconds,
        min_depth=min_depth,
    )


def test_trusted_join_writes_normalized_markets_with_venue_native_exhaustive_group() -> None:
    result = join_kalshi_event_metadata(
        snapshot_payload=_matching_snapshot_payload(),
        metadata_payloads=[_complete_event_payload()],
        generated_at=NOW,
    )
    enriched = result["enriched_snapshot"]

    rows = enriched.get("normalized_markets") or []
    assert len(rows) == 3, "trusted join must materialize one row per market"
    assert result["report"]["summary"]["enriched_normalized_market_row_count"] == 3
    assert enriched["event_metadata_join"]["trusted_join_count"] == 1

    for row in rows:
        assert row["venue"] == "kalshi"
        assert row["reference_only"] is False
        assert row["event_metadata_join_source"] == EVENT_METADATA_JOIN_SOURCE
        assert row["settlement_source_status"] == "explicit"
        assert row["settlement_source_raw_evidence"] == (
            "Official Kalshi event metadata resolves every listed outcome from the same source."
        )
        assert row["resolution_date"] == "2026-12-31"
        assert row["settlement_time"] == "2027-01-01T00:00:00Z"
        assert row["rules_primary"].startswith("Resolution source is official Kalshi event metadata.")
        assert row["orderbook_enrichment"]["depth_at_best_ask"] == 25.0
        evidence = row["exhaustive_group"]
        assert evidence["source"] == KALSHI_EVENT_METADATA_EVIDENCE_SOURCE
        assert evidence["venue_native"] is True
        assert evidence["all_outcomes_included"] is True
        assert evidence["outcome_market_ids"] == [
            "KXEVT-2026-EXAMPLE-A",
            "KXEVT-2026-EXAMPLE-B",
            "KXEVT-2026-EXAMPLE-C",
        ]
        assert evidence["expected_outcome_count"] == 3
        assert evidence["outcome_list"] == ["Outcome A", "Outcome B", "Outcome C"]


def test_detect_structural_baskets_consumes_enriched_snapshot_directly() -> None:
    """End-to-end: join produces an enriched_snapshot that detect-structural-baskets
    can read directly without needing audit-kalshi-native-groups in between."""
    enriched = join_kalshi_event_metadata(
        snapshot_payload=_matching_snapshot_payload(),
        metadata_payloads=[_complete_event_payload()],
        generated_at=NOW,
    )["enriched_snapshot"]

    report = _detect(enriched)
    assert report["summary"]["evaluated_group_count"] == 1
    row = report["rows"][0]
    assert row["status"] == STATUS_STOP_FOR_REVIEW
    assert row["settlement_audit_status"] == "PASS"
    assert row["evidence"]["source"] == KALSHI_EVENT_METADATA_EVIDENCE_SOURCE
    assert row["evidence"]["venue_native"] is True
    assert row["uses_midpoint"] is False
    assert row["paper_candidate_emitted"] is False
    assert report["summary"]["paper_candidate_count"] == 0
    assert report["safety"]["uses_title_similarity_for_exhaustiveness"] is False
    assert report["safety"]["uses_graph_hints_for_exhaustiveness"] is False


def test_detect_blocks_when_metadata_is_reference_only() -> None:
    metadata = dict(_complete_event_payload())
    metadata["reference_only"] = True
    enriched = join_kalshi_event_metadata(
        snapshot_payload=_matching_snapshot_payload(),
        metadata_payloads=[metadata],
        generated_at=NOW,
    )["enriched_snapshot"]

    assert enriched["normalized_markets"] == [], "reference-only metadata must not emit normalized_markets"
    report = _detect(enriched)
    assert report["summary"]["evaluated_group_count"] == 0
    assert report["summary"]["stop_for_review_count"] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reference_only", True),
        ("source_kind", "reference"),
        ("venue_type", "reference"),
    ],
)
def test_join_blocks_when_snapshot_market_is_reference_only(field: str, value) -> None:
    result = join_kalshi_event_metadata(
        snapshot_payload=_snapshot_with_reference_only_market(field, value),
        metadata_payloads=[_complete_event_payload()],
        generated_at=NOW,
    )
    enriched = result["enriched_snapshot"]
    row = result["report"]["rows"][0]

    assert "snapshot_reference_only_source" in row["join_blockers"]
    assert row["snapshot_reference_only"] is True
    assert row["snapshot_reference_only_markets"] == ["KXEVT-2026-EXAMPLE-A"]
    assert row["trusted_for_completeness_after_join"] is False
    assert result["report"]["summary"]["events_trusted_after_join"] == 0
    assert result["report"]["summary"]["enriched_normalized_market_row_count"] == 0
    assert enriched["normalized_markets"] == []

    report = _detect(enriched)
    assert report["summary"]["evaluated_group_count"] == 0
    assert report["summary"]["stop_for_review_count"] == 0
    assert report["summary"]["paper_candidate_count"] == 0


def test_detect_blocks_when_metadata_tickers_missing_from_snapshot() -> None:
    snapshot = _matching_snapshot_payload()
    snapshot["events"][0]["markets"] = snapshot["events"][0]["markets"][:2]
    enriched = join_kalshi_event_metadata(
        snapshot_payload=snapshot,
        metadata_payloads=[_complete_event_payload()],
        generated_at=NOW,
    )["enriched_snapshot"]

    assert enriched["normalized_markets"] == [], "join with missing tickers must not emit normalized_markets"
    report = _detect(enriched)
    assert report["summary"]["evaluated_group_count"] == 0
    assert report["summary"]["stop_for_review_count"] == 0


def test_detect_blocks_when_metadata_is_title_only() -> None:
    enriched = join_kalshi_event_metadata(
        snapshot_payload={"events": [{"title": "Some event", "markets": []}]},
        metadata_payloads=[
            {
                "title": "Some event",
                "outcome_list": ["A", "B", "C"],
                "complete": True,
                "rules_primary": "rule",
                "settlement_source_raw_evidence": "source",
            }
        ],
        generated_at=NOW,
    )["enriched_snapshot"]

    assert enriched["normalized_markets"] == []
    report = _detect(enriched)
    assert report["summary"]["evaluated_group_count"] == 0


def test_detect_blocks_when_only_per_market_yes_no_outcomes_exist() -> None:
    snapshot = {
        "events": [
            {
                "event_ticker": "KXBTC-26MAY",
                "event_id": "kxbtc-26may",
                "markets": [
                    {
                        "ticker": "KXBTC-26MAY-T86000",
                        "market_ticker": "KXBTC-26MAY-T86000",
                        "rules_primary": "threshold rule",
                        "settlement_source": "cf benchmarks btc index",
                        "outcomes": ["Yes", "No"],
                        "orderbook_enrichment": {
                            "best_ask": 0.20,
                            "depth_at_best_ask": 25.0,
                            "orderbook_captured_at": "2026-05-24T11:59:30+00:00",
                        },
                    },
                    {
                        "ticker": "KXBTC-26MAY-T87000",
                        "market_ticker": "KXBTC-26MAY-T87000",
                        "rules_primary": "threshold rule",
                        "settlement_source": "cf benchmarks btc index",
                        "outcomes": ["Yes", "No"],
                        "orderbook_enrichment": {
                            "best_ask": 0.10,
                            "depth_at_best_ask": 25.0,
                            "orderbook_captured_at": "2026-05-24T11:59:30+00:00",
                        },
                    },
                ],
            }
        ]
    }
    metadata = {
        "event_ticker": "KXBTC-26MAY",
        "event_id": "kxbtc-26may",
        "outcomes": ["Yes", "No"],
        "complete": True,
        "rules_primary": "threshold rule",
        "settlement_source_raw_evidence": "cf benchmarks btc index",
        "markets": [
            {"market_ticker": "KXBTC-26MAY-T86000", "outcomes": ["Yes", "No"]},
            {"market_ticker": "KXBTC-26MAY-T87000", "outcomes": ["Yes", "No"]},
        ],
    }
    enriched = join_kalshi_event_metadata(
        snapshot_payload=snapshot,
        metadata_payloads=[metadata],
        generated_at=NOW,
    )["enriched_snapshot"]

    assert enriched["normalized_markets"] == []
    report = _detect(enriched)
    assert report["summary"]["stop_for_review_count"] == 0


def test_detect_blocks_when_count_only_evidence_attempted() -> None:
    metadata = {
        "event_ticker": "KXEVT-COUNT",
        "event_id": "kxevt-count",
        "rules_primary": "rule",
        "settlement_source_raw_evidence": "source",
        "expected_outcome_count": 3,
        "markets": [
            {"market_ticker": "KXEVT-COUNT-A"},
            {"market_ticker": "KXEVT-COUNT-B"},
            {"market_ticker": "KXEVT-COUNT-C"},
        ],
    }
    snapshot = {
        "events": [
            {
                "event_ticker": "KXEVT-COUNT",
                "event_id": "kxevt-count",
                "markets": [
                    {
                        "market_ticker": ticker,
                        "ticker": ticker,
                        "rules_primary": "rule",
                        "orderbook_enrichment": {
                            "best_ask": 0.25,
                            "depth_at_best_ask": 25.0,
                            "orderbook_captured_at": "2026-05-24T11:59:30+00:00",
                        },
                    }
                    for ticker in ("KXEVT-COUNT-A", "KXEVT-COUNT-B", "KXEVT-COUNT-C")
                ],
            }
        ]
    }
    enriched = join_kalshi_event_metadata(
        snapshot_payload=snapshot,
        metadata_payloads=[metadata],
        generated_at=NOW,
    )["enriched_snapshot"]

    assert enriched["normalized_markets"] == []


def test_detect_blocks_when_mixed_settlement_rules_in_metadata() -> None:
    metadata = dict(_complete_event_payload())
    markets = [dict(market) for market in metadata["markets"]]
    markets[0]["rules_primary"] = "Wildly different rule for outcome A."
    metadata["markets"] = markets
    enriched = join_kalshi_event_metadata(
        snapshot_payload=_matching_snapshot_payload(),
        metadata_payloads=[metadata],
        generated_at=NOW,
    )["enriched_snapshot"]

    assert enriched["normalized_markets"] == []


def test_enriched_market_only_snapshot_without_metadata_still_blocks() -> None:
    """Snapshot has no event metadata join — detect must still fail closed."""
    snapshot = {
        "events": [
            {
                "event_ticker": "KXBARE",
                "markets": [
                    {
                        "market_ticker": "KXBARE-A",
                        "ticker": "KXBARE-A",
                        "outcomes": ["Yes", "No"],
                        "orderbook_enrichment": {
                            "best_ask": 0.20,
                            "depth_at_best_ask": 25.0,
                            "orderbook_captured_at": "2026-05-24T11:59:30+00:00",
                        },
                    },
                    {
                        "market_ticker": "KXBARE-B",
                        "ticker": "KXBARE-B",
                        "outcomes": ["Yes", "No"],
                        "orderbook_enrichment": {
                            "best_ask": 0.20,
                            "depth_at_best_ask": 25.0,
                            "orderbook_captured_at": "2026-05-24T11:59:30+00:00",
                        },
                    },
                ],
            }
        ]
    }
    # No metadata supplied — join is a no-op and emits no enriched normalized_markets.
    enriched = join_kalshi_event_metadata(
        snapshot_payload=snapshot,
        metadata_payloads=[],
        generated_at=NOW,
    )["enriched_snapshot"]
    assert enriched.get("normalized_markets") == []
    report = _detect(enriched)
    assert report["summary"]["evaluated_group_count"] == 0
    assert report["summary"]["stop_for_review_count"] == 0


def test_enriched_rows_still_blocked_by_stale_orderbook() -> None:
    snapshot = _matching_snapshot_payload()
    snapshot["events"][0]["markets"][0]["orderbook_enrichment"]["orderbook_captured_at"] = (
        "2026-05-24T00:00:00+00:00"
    )
    enriched = join_kalshi_event_metadata(
        snapshot_payload=snapshot,
        metadata_payloads=[_complete_event_payload()],
        generated_at=NOW,
    )["enriched_snapshot"]

    report = _detect(enriched, max_quote_age_seconds=60.0)
    row = report["rows"][0]
    assert row["status"] == STATUS_STALE_ORDERBOOK
    assert report["summary"]["stop_for_review_count"] == 0


def test_enriched_rows_still_blocked_by_insufficient_depth() -> None:
    snapshot = _matching_snapshot_payload()
    for market in snapshot["events"][0]["markets"]:
        market["orderbook_enrichment"]["depth_at_best_ask"] = 0.5
    enriched = join_kalshi_event_metadata(
        snapshot_payload=snapshot,
        metadata_payloads=[_complete_event_payload()],
        generated_at=NOW,
    )["enriched_snapshot"]

    report = _detect(enriched, min_depth=1.0)
    row = report["rows"][0]
    assert row["status"] == STATUS_INSUFFICIENT_DEPTH
    assert report["summary"]["stop_for_review_count"] == 0


def test_audit_kalshi_native_groups_does_not_double_count_after_join() -> None:
    """The audit must produce exactly one row per market even when the enriched
    snapshot contains both events[].markets and normalized_markets (added by join)."""
    enriched = join_kalshi_event_metadata(
        snapshot_payload=_matching_snapshot_payload(),
        metadata_payloads=[_complete_event_payload()],
        generated_at=NOW,
    )["enriched_snapshot"]

    audit = audit_kalshi_native_groups(enriched, generated_at=NOW)
    assert audit["summary"]["candidate_input_row_count"] == 3
    group = audit["groups"][0]
    assert group["market_count"] == 3
    assert group["status"] == "COMPLETE_EXHAUSTIVE_GROUP"


def test_paper_fill_simulator_refuses_ungated_enriched_rows() -> None:
    """Even though the enriched rows look like good legs, the paper fill simulator
    must not paper-simulate them unless the upstream structural detector has
    already gated them with STOP_FOR_REVIEW / STRUCTURAL_BASKET_REVIEW status."""
    from relative_value.paper_fill_simulator import simulate_paper_fill_journal

    enriched = join_kalshi_event_metadata(
        snapshot_payload=_matching_snapshot_payload(),
        metadata_payloads=[_complete_event_payload()],
        generated_at=NOW,
    )["enriched_snapshot"]
    rows = enriched["normalized_markets"]
    ungated_row = {
        "source_candidate_id": "raw-leg",
        "candidate_type": "structural_basket",
        # No status field — must be treated as ungated.
        "legs": [
            {"venue": "kalshi", "side": "BUY_YES", "orderbook_enrichment": row["orderbook_enrichment"]}
            for row in rows
        ],
        "gross_payout_cents": 100.0,
    }
    journal = simulate_paper_fill_journal(
        input_payload={"rows": [ungated_row]},
        generated_at=NOW,
    )
    assert journal["summary"]["simulated_fill_count"] == 0
    assert "ungated_structural_basket_row" in journal["journal"][0]["blockers"]


def test_join_safety_block_remains_intact() -> None:
    result = join_kalshi_event_metadata(
        snapshot_payload=_matching_snapshot_payload(),
        metadata_payloads=[_complete_event_payload()],
        generated_at=NOW,
    )
    safety = result["report"]["safety"]
    enriched_safety = result["enriched_snapshot"]["event_metadata_join"]["safety"]
    assert safety["paper_candidate_emitted"] is False
    assert safety["stop_for_review_emitted"] is False
    assert safety["uses_title_similarity_for_exhaustiveness"] is False
    assert safety["uses_graph_hints_for_exhaustiveness"] is False
    assert safety["uses_count_only_evidence"] is False
    assert safety["affects_evaluator_gates"] is False
    assert enriched_safety["places_orders"] is False
    assert enriched_safety["stop_for_review_emitted"] is False


def test_full_saved_file_pipeline_end_to_end(tmp_path: Path) -> None:
    """Full saved-file CLI sequence, exercised via the file-level helpers:

    1. audit-kalshi-event-metadata
    2. join-kalshi-event-metadata --enriched-snapshot-output enriched.json
    3. detect-structural-baskets --snapshot enriched.json

    Strict fixture inputs (explicit event-level outcome_list, rules,
    settlement_source, matching tickers, fresh deep orderbooks) so the basket
    review can naturally reach STOP_FOR_REVIEW — the only place that status
    is allowed to surface in this report. Still saved-file-only, no orders.
    """
    from relative_value.structural_basket_detector import build_structural_basket_review_report_files

    metadata_path = FIXTURE_DIR / "e2e_event_metadata.json"
    snapshot_path = FIXTURE_DIR / "e2e_snapshot.json"

    audit_json = tmp_path / "audit.json"
    audit_md = tmp_path / "audit.md"
    audit_report = audit_kalshi_event_metadata_files(
        metadata_paths=[metadata_path],
        json_output=audit_json,
        markdown_output=audit_md,
        generated_at=NOW,
    )
    assert audit_report["summary"]["events_trusted_for_completeness"] == 1
    assert audit_report["summary"]["events_blocked"] == 0
    assert audit_report["summary"]["stop_for_review_count"] == 0
    assert audit_report["summary"]["paper_candidate_count"] == 0
    assert audit_report["safety"]["paper_candidate_emitted"] is False

    join_json = tmp_path / "join.json"
    join_md = tmp_path / "join.md"
    enriched_path = tmp_path / "enriched_snapshot.json"
    join_result = join_kalshi_event_metadata_files(
        snapshot_path=snapshot_path,
        metadata_paths=[metadata_path],
        json_output=join_json,
        markdown_output=join_md,
        enriched_snapshot_output=enriched_path,
        generated_at=NOW,
    )
    assert join_result["report"]["summary"]["events_trusted_after_join"] == 1
    assert join_result["report"]["summary"]["enriched_normalized_market_row_count"] == 3
    assert join_result["report"]["summary"]["stop_for_review_count"] == 0
    assert join_result["report"]["summary"]["paper_candidate_count"] == 0
    assert enriched_path.exists()
    enriched_payload = json.loads(enriched_path.read_text(encoding="utf-8"))
    assert len(enriched_payload.get("normalized_markets") or []) == 3

    basket_json = tmp_path / "structural_basket_review.json"
    basket_md = tmp_path / "structural_basket_review.md"
    basket_report = build_structural_basket_review_report_files(
        snapshot_paths=[enriched_path],
        manifest_path=None,
        json_output=basket_json,
        markdown_output=basket_md,
        detected_at=NOW,
        max_quote_age_seconds=1800.0,
        min_depth=1.0,
    )
    assert basket_report["summary"]["evaluated_group_count"] == 1
    assert basket_report["summary"]["paper_candidate_count"] == 0
    assert basket_report["safety"]["paper_candidate_emitted"] is False
    assert basket_report["safety"]["uses_midpoint"] is False
    assert basket_report["safety"]["uses_title_similarity_for_exhaustiveness"] is False
    assert basket_report["safety"]["uses_graph_hints_for_exhaustiveness"] is False
    row = basket_report["rows"][0]
    assert row["status"] == STATUS_STOP_FOR_REVIEW
    assert row["evidence"]["source"] == KALSHI_EVENT_METADATA_EVIDENCE_SOURCE
    assert row["evidence"]["venue_native"] is True
    assert row["settlement_audit_status"] == "PASS"
    assert row["paper_candidate_emitted"] is False
    # Sum asks must equal the per-leg best_ask sum exactly (no midpoint).
    assert row["sum_asks"] == 0.85


def test_join_preserves_existing_normalized_markets_without_double_counting() -> None:
    snapshot = _matching_snapshot_payload()
    # Pre-existing normalized_markets entry that shares a ticker with metadata.
    snapshot["normalized_markets"] = [
        {
            "venue": "kalshi",
            "market_ticker": "KXEVT-2026-EXAMPLE-A",
            "ticker": "KXEVT-2026-EXAMPLE-A",
            "event_id": "kxevt-2026-example",
            "group_id": "kxevt-2026-example",
            "orderbook_enrichment": {
                "best_ask": 0.40,
                "depth_at_best_ask": 5.0,
                "orderbook_captured_at": "2026-05-24T11:59:30+00:00",
            },
        }
    ]
    enriched = join_kalshi_event_metadata(
        snapshot_payload=snapshot,
        metadata_payloads=[_complete_event_payload()],
        generated_at=NOW,
    )["enriched_snapshot"]
    rows = enriched["normalized_markets"]
    # 1 preserved + 2 new trusted rows (the third ticker matched the preserved entry and was deduped).
    assert len(rows) == 3
    preserved = next(row for row in rows if row.get("best_ask") is None and row.get("market_ticker") == "KXEVT-2026-EXAMPLE-A")
    assert preserved.get("event_metadata_join_source") is None, "preserved entry must not be silently rewritten"
