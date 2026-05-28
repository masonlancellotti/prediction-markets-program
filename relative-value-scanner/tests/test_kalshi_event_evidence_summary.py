from __future__ import annotations

import json
from datetime import datetime, timezone

from relative_value.kalshi_event_evidence_summary import build_kalshi_event_evidence_summary


NOW = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _complete_event(*, complete=True, captured_at="2026-05-26T11:45:00Z"):
    return {
        "events": [
            {
                "event_ticker": "KXMLB-26",
                "event_id": "KXMLB-26",
                "series_ticker": "KXMLB",
                "title": "2026 Pro Baseball Championship",
                "outcome_list": ["Washington", "Toronto"],
                "complete": complete,
                "rules_primary": "Official Kalshi event metadata states all outcomes resolve from the championship winner.",
                "settlement_source": "Official Kalshi event metadata source.",
                "markets": [
                    {
                        "market_ticker": "KXMLB-26-WSH",
                        "yes_sub_title": "Washington",
                        "rules_primary": "If Washington wins, this resolves to Yes.",
                        "best_ask": 0.20,
                        "depth_at_best_ask": 10,
                        "orderbook_captured_at": captured_at,
                    },
                    {
                        "market_ticker": "KXMLB-26-TOR",
                        "yes_sub_title": "Toronto",
                        "rules_primary": "If Toronto wins, this resolves to Yes.",
                        "best_ask": 0.30,
                        "depth_at_best_ask": 10,
                        "orderbook_captured_at": captured_at,
                    },
                ],
            }
        ]
    }


def test_explicit_event_level_outcome_list_is_required(tmp_path) -> None:
    reports = tmp_path / "reports"
    _write_json(
        reports / "manifest_scouts" / "kxmlb.json",
        {
            "rows": [
                {
                    "event_ticker": "KXMLB-26",
                    "venue_native_group_id": "KXMLB-26",
                    "markets": [
                        {"market_ticker": "KXMLB-26-WSH", "outcome": "Washington"},
                        {"market_ticker": "KXMLB-26-TOR", "outcome": "Toronto"},
                    ],
                }
            ]
        },
    )

    report = build_kalshi_event_evidence_summary(input_dir=reports, generated_at=NOW)

    assert report["summary"]["explicit_outcome_list_exists"] is False
    assert "explicit_event_level_outcome_list" in report["missing_fields"]
    assert report["ready_for_human_manifest_review"] is False


def test_explicit_completeness_evidence_is_required(tmp_path) -> None:
    reports = tmp_path / "reports"
    event = _complete_event(complete=False)
    _write_json(reports / "kxmlb_event.json", event)

    report = build_kalshi_event_evidence_summary(input_dir=reports, generated_at=NOW)

    assert report["summary"]["explicit_outcome_list_exists"] is True
    assert report["summary"]["explicit_completeness_evidence_exists"] is False
    assert "explicit_completeness_or_exhaustiveness_evidence" in report["missing_fields"]
    assert report["ready_for_human_manifest_review"] is False


def test_stale_orderbook_depth_does_not_pass(tmp_path) -> None:
    reports = tmp_path / "reports"
    _write_json(reports / "kxmlb_event.json", _complete_event(captured_at="2026-05-25T00:00:00Z"))

    report = build_kalshi_event_evidence_summary(
        input_dir=reports,
        generated_at=NOW,
        max_quote_age_seconds=1800,
    )

    assert report["summary"]["explicit_outcome_list_exists"] is True
    assert report["summary"]["explicit_completeness_evidence_exists"] is True
    assert report["summary"]["fresh_orderbook_depth_exists"] is False
    assert "stale_orderbook_depth" in report["quote_depth_evidence"]["blockers"]
    assert "fresh_orderbook_depth" in report["missing_fields"]


def test_title_ticker_and_count_cannot_create_completeness(tmp_path) -> None:
    reports = tmp_path / "reports"
    _write_json(
        reports / "manifest_templates" / "kxmlb.template.json",
        {
            "exhaustive_groups": [
                {
                    "source": "local_manifest_v1",
                    "manifest_template": True,
                    "group_id": "KXMLB-26",
                    "venue_native_event_id": "KXMLB-26",
                    "market_tickers": ["KXMLB-26-WSH", "KXMLB-26-TOR"],
                    "outcome_list": [],
                    "complete": False,
                    "trusted_local_manifest": False,
                }
            ]
        },
    )

    report = build_kalshi_event_evidence_summary(input_dir=reports, generated_at=NOW)

    assert report["summary"]["market_count"] == 2
    assert report["summary"]["explicit_outcome_list_exists"] is False
    assert report["summary"]["explicit_completeness_evidence_exists"] is False
    assert report["summary"]["event_level_market_list_exists"] is False
    assert report["ready_for_human_manifest_review"] is False


def test_kxmlb_style_evidence_stays_blocked_when_fields_are_missing(tmp_path) -> None:
    reports = tmp_path / "reports"
    _write_json(
        reports / "manifest_scouts" / "kxmlb.json",
        {
            "rows": [
                {
                    "event_ticker": "KXMLB-26",
                    "venue_native_group_id": "KXMLB-26",
                    "has_shared_rules": False,
                    "has_orderbooks": True,
                    "markets": [
                        {
                            "market_ticker": "KXMLB-26-WSH",
                            "outcome": "Washington",
                            "rules_primary": "If Washington wins the championship, then this resolves Yes.",
                            "best_ask": 0.2,
                            "depth_at_best_ask": 5,
                            "orderbook_captured_at": "2026-05-26T11:55:00Z",
                        },
                        {
                            "market_ticker": "KXMLB-26-TOR",
                            "outcome": "Toronto",
                            "rules_primary": "If Toronto wins the championship, then this resolves Yes.",
                            "best_ask": 0.3,
                            "depth_at_best_ask": 5,
                            "orderbook_captured_at": "2026-05-26T11:55:00Z",
                        },
                    ],
                }
            ]
        },
    )

    report = build_kalshi_event_evidence_summary(input_dir=reports, generated_at=NOW)

    assert report["summary"]["apparent_outcome_count"] == 2
    assert report["summary"]["fresh_orderbook_depth_exists"] is True
    assert report["summary"]["explicit_outcome_list_exists"] is False
    assert report["summary"]["shared_rules_source_evidence_exists"] is False
    assert report["summary"]["local_manifest_v1_would_pass_if_reviewer_fields_added"] is False
    assert report["ready_for_human_manifest_review"] is False


def test_report_emits_no_paper_candidate_literal(tmp_path) -> None:
    reports = tmp_path / "reports"
    _write_json(reports / "kxmlb_event.json", _complete_event())

    report = build_kalshi_event_evidence_summary(input_dir=reports, generated_at=NOW)

    assert "PAPER_CANDIDATE" not in json.dumps(report)
