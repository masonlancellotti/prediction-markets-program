from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from relative_value.kalshi_native_groups import (
    audit_kalshi_native_groups,
    audit_kalshi_native_groups_file,
    kalshi_native_group_audit_paths,
    render_kalshi_native_groups_markdown,
    safe_kalshi_native_group_audit_label,
)


NOW = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)


def _market(ticker: str, outcome: str, **extra) -> dict:
    row = {
        "ticker": ticker,
        "yes_sub_title": outcome,
        "question": f"{outcome} wins",
        "rules_primary": "Resolution source is official Kalshi event metadata.",
        "settlement_source": "official Kalshi event metadata",
        "expected_expiration_time": "2026-12-31T23:00:00Z",
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
    assert candidate["rules"] == "Resolution source is official Kalshi event metadata."
    assert candidate["settlement_source_status"] == "explicit"
    assert candidate["settlement_source_raw_evidence"] == "official Kalshi event metadata"


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


def test_ticker_only_group_blocks() -> None:
    report = _audit({"normalized_markets": [_market("KXTEST-A", "A"), _market("KXTEST-B", "B")]})
    group = report["groups"][0]

    assert group["status"] == "INCOMPLETE_GROUP"
    assert "missing_venue_native_event_id" in group["blockers"]
    assert "missing_completeness_evidence" in group["blockers"]


def test_partial_outcome_set_blocks() -> None:
    event = _event(markets=[_market("KXTEST-A", "A"), _market("KXTEST-B", "B")])
    report = _audit({"events": [event]})
    group = report["groups"][0]

    assert group["status"] == "INCOMPLETE_GROUP"
    assert "partial_event_metadata" in group["blockers"]
    assert report["summary"]["candidate_input_row_count"] == 0


def test_explicit_outcome_list_and_complete_true_creates_candidate_input_rows() -> None:
    event = _event(complete=True)
    event.pop("all_outcomes_included")
    report = _audit({"events": [event]})

    assert report["summary"]["candidate_input_row_count"] == 3
    assert report["groups"][0]["status"] == "COMPLETE_EXHAUSTIVE_GROUP"


def test_fed_threshold_ladder_remains_blocked_without_manifest() -> None:
    report = _audit(
        {
            "normalized_markets": [
                _market(
                    "KXFED-27APR-T4.25",
                    "Above 4.25%",
                    event_id="KXFED-27APR",
                    raw={"event_ticker": "KXFED-27APR", "floor_strike": 4.25},
                ),
                _market(
                    "KXFED-27APR-T4.00",
                    "Above 4.00%",
                    event_id="KXFED-27APR",
                    raw={"event_ticker": "KXFED-27APR", "floor_strike": 4.00},
                ),
            ]
        }
    )
    group = report["groups"][0]

    assert group["status"] == "INCOMPLETE_GROUP"
    assert "threshold_ladder_not_exhaustive" in group["blockers"]
    assert "missing_completeness_evidence" in group["blockers"]
    assert report["summary"]["candidate_input_row_count"] == 0


def test_threshold_ladder_can_pass_only_with_trusted_manifest() -> None:
    report = _audit(
        {
            "trusted_exhaustive_groups": [
                {
                    "group_id": "KXFED-27APR",
                    "trusted_local_manifest": True,
                    "complete": True,
                    "outcome_list": ["Above 4.25%", "Above 4.00%"],
                }
            ],
            "normalized_markets": [
                _market(
                    "KXFED-27APR-T4.25",
                    "Above 4.25%",
                    event_id="KXFED-27APR",
                    raw={"event_ticker": "KXFED-27APR", "floor_strike": 4.25},
                ),
                _market(
                    "KXFED-27APR-T4.00",
                    "Above 4.00%",
                    event_id="KXFED-27APR",
                    raw={"event_ticker": "KXFED-27APR", "floor_strike": 4.00},
                ),
            ],
        }
    )

    assert report["groups"][0]["status"] == "COMPLETE_EXHAUSTIVE_GROUP"
    assert report["summary"]["candidate_input_row_count"] == 2


def test_shared_explicit_rules_propagate_to_structural_input_rows() -> None:
    report = _audit({"events": [_event()]})
    candidate = report["structural_basket_detector_inputs"][0]

    assert candidate["rules"] == "Resolution source is official Kalshi event metadata."
    assert candidate["resolution_criteria"] == "Resolution source is official Kalshi event metadata."
    assert candidate["settlement_source_status"] == "explicit"


def test_mixed_rules_block() -> None:
    event = _event(
        markets=[
            _market("KXTEST-A", "A", rules_primary="Rule A from official Kalshi event metadata."),
            _market("KXTEST-B", "B", rules_primary="Rule B from official Kalshi event metadata."),
            _market("KXTEST-C", "C", rules_primary="Rule C from official Kalshi event metadata."),
        ]
    )
    report = _audit({"events": [event]})

    assert report["groups"][0]["status"] == "INCOMPLETE_GROUP"
    assert "mixed_resolution_criteria" in report["groups"][0]["blockers"]
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


def test_nested_output_directory_is_created_automatically(tmp_path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"events": [_event()]}), encoding="utf-8")
    json_output = tmp_path / "deep" / "nested" / "audit.json"
    markdown_output = tmp_path / "deep" / "nested" / "audit.md"

    report = audit_kalshi_native_groups_file(
        snapshot_path=snapshot,
        json_output=json_output,
        markdown_output=markdown_output,
    )

    assert json_output.exists()
    assert markdown_output.exists()
    assert report["summary"]["paper_candidate_count"] == 0


def test_very_long_snapshot_path_defaults_to_short_output_filename() -> None:
    long_path = (
        Path("C:/Users/mason/Downloads/prediction-markets-program/relative-value-scanner/reports/live_readonly/fed")
        / ("nested_" * 20)
        / "kalshi_live_readonly_snapshot.json"
    )
    paths = kalshi_native_group_audit_paths(long_path)

    assert paths["json_output"].name == "fed.json"
    assert paths["markdown_output"].name == "fed.md"
    assert len(paths["json_output"].name) < 50
    assert "C__Users_mason" not in paths["json_output"].name


def test_explicit_short_output_writes_successfully(tmp_path) -> None:
    snapshot = tmp_path / "kalshi.json"
    snapshot.write_text(json.dumps({"events": [_event()]}), encoding="utf-8")
    output = tmp_path / "reports" / "native_group_audits" / "fed.json"

    report = audit_kalshi_native_groups_file(snapshot_path=snapshot, json_output=output, markdown_output=None)

    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"]["candidate_input_row_count"] == 3
    assert report["summary"]["stop_for_review_count"] == 0


def test_safe_audit_label_truncates_and_hashes_unknown_long_paths() -> None:
    long_path = Path("C:/very/long") / ("kalshi_" + "x" * 200 + ".json")
    label = safe_kalshi_native_group_audit_label(long_path)

    assert len(label) <= 40
    assert label.endswith(label[-8:])
    assert "\\" not in label
    assert "/" not in label
