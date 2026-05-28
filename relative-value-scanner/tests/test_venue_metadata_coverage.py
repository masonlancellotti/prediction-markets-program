from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

import scan
from relative_value.venue_metadata_coverage import (
    TIER_BLOCKED_METADATA_INCOMPLETE,
    TIER_EXECUTION_EVALUATION_READY,
    TIER_INGESTED_ONLY,
    TIER_MATCH_CANDIDATE_READY,
    TIER_RELATIONSHIP_REVIEW_READY,
    build_venue_metadata_coverage_report,
    write_venue_metadata_coverage_files,
)


def _write_snapshot(path, *, source: str = "custom", markets: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": source,
                "captured_at": "2026-05-25T12:00:00+00:00",
                "normalized_markets": markets,
            }
        ),
        encoding="utf-8",
    )


def test_arbitrary_venue_names_and_source_file_are_preserved(tmp_path) -> None:
    snapshot = tmp_path / "reports" / "venue_x.json"
    _write_snapshot(
        snapshot,
        markets=[
            {
                "venue": "VenueX",
                "event_id": "event-1",
                "market_id": "market-1",
                "title": "Will the event happen?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules": "Resolves according to the saved fixture.",
                "settlement_source": "fixture_rules",
                "resolution_time": "2026-06-01T00:00:00+00:00",
                "status": "active",
            }
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["markets"][0]
    assert row["venue"] == "VenueX"
    assert row["market_id"] == "market-1"
    assert row["source_file"] == str(snapshot)
    assert row["readiness_tier"] == TIER_RELATIONSHIP_REVIEW_READY


def test_missing_event_outcome_and_settlement_fields_produce_blockers(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "partial.json",
        markets=[
            {
                "venue": "VenueY",
                "market_id": "market-2",
                "title": "Incomplete metadata row",
            }
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["markets"][0]
    assert row["readiness_tier"] == TIER_INGESTED_ONLY
    assert "missing_event_id" in row["blockers"]
    assert "missing_outcome_list" in row["blockers"]
    assert "missing_settlement_rules" in row["blockers"]
    assert "missing_settlement_source" in row["blockers"]
    assert "missing_resolution_time" in row["blockers"]


def test_per_venue_summary_counts_are_correct(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "summary.json",
        markets=[
            {
                "venue": "VenueZ",
                "event_id": "event-z",
                "market_id": "m1",
                "title": "Full metadata but no execution metadata",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules": "Rule text",
                "settlement_source": "venue_rules",
                "resolution_time": "2026-06-01T00:00:00+00:00",
                "status": "active",
            },
            {
                "venue": "VenueZ",
                "event_id": "event-z",
                "market_id": "m2",
                "title": "Missing outcomes",
                "rules": "Rule text",
                "resolution_time": "2026-06-01T00:00:00+00:00",
                "status": "closed",
            },
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    venue = report["venues"][0]
    assert venue["venue"] == "VenueZ"
    assert venue["market_count"] == 2
    assert venue["active_market_count"] == 1
    assert venue["markets_with_settlement_rules"] == 2
    assert venue["markets_with_outcomes"] == 1
    assert venue["markets_with_resolution_time"] == 2
    assert venue["match_ready_count"] == 1
    assert venue["evaluator_ready_count"] == 0


def test_execution_evaluation_ready_requires_orderbook_depth_freshness_and_fee(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "execution.json",
        markets=[
            {
                "venue": "VenueExec",
                "event_id": "event-exec",
                "market_id": "missing-exec",
                "title": "Missing execution fields",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules": "Rule text",
                "settlement_source": "venue_rules",
                "resolution_time": "2026-06-01T00:00:00+00:00",
            },
            {
                "venue": "VenueExec",
                "event_id": "event-exec",
                "market_id": "ready-exec",
                "title": "Execution metadata present",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules": "Rule text",
                "settlement_source": "venue_rules",
                "resolution_time": "2026-06-01T00:00:00+00:00",
                "best_ask": 0.42,
                "depth_at_best_ask": 10,
                "quote_timestamp": "2026-05-25T12:00:00+00:00",
                "fee_model_status": "known_fixture_fee_model",
            },
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    missing = next(row for row in report["markets"] if row["market_id"] == "missing-exec")
    ready = next(row for row in report["markets"] if row["market_id"] == "ready-exec")
    assert missing["readiness_tier"] != TIER_EXECUTION_EVALUATION_READY
    assert {"missing_orderbook", "missing_depth", "missing_quote_timestamp", "missing_fee_model"} <= set(missing["blockers"])
    assert ready["readiness_tier"] == TIER_EXECUTION_EVALUATION_READY
    assert ready["tradability_claimed"] is False


def test_missing_market_id_blocks_metadata_readiness(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "blocked.json",
        markets=[
            {
                "venue": "VenueBlocked",
                "event_id": "event-blocked",
                "title": "No market id",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
            }
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["markets"][0]
    assert row["readiness_tier"] == TIER_BLOCKED_METADATA_INCOMPLETE
    assert "missing_market_id" in row["blockers"]


def test_malformed_and_partial_saved_files_fail_closed(tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "bad.json").write_text("{not json", encoding="utf-8")
    (reports / "partial_snapshot.json").write_text(
        json.dumps({"schema_version": 1, "normalized_markets": "not-a-list"}),
        encoding="utf-8",
    )

    report = build_venue_metadata_coverage_report(
        input_dir=reports,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert report["markets"] == []
    assert report["summary"]["warning_count"] == 2
    assert {warning["reason_code"] for warning in report["warnings"]} == {
        "invalid_json",
        "snapshot_contains_no_market_rows",
    }


def test_writer_outputs_json_csv_and_markdown(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "writer.json",
        markets=[
            {
                "venue": "VenueWriter",
                "event_id": "event-writer",
                "market_id": "market-writer",
                "title": "Writer row",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
            }
        ],
    )
    json_output = tmp_path / "out" / "coverage.json"
    csv_output = tmp_path / "out" / "coverage.csv"
    markdown_output = tmp_path / "out" / "coverage.md"

    report = write_venue_metadata_coverage_files(
        input_dir=tmp_path / "reports",
        json_output=json_output,
        csv_output=csv_output,
        markdown_output=markdown_output,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert report["summary"]["market_count"] == 1
    assert json_output.exists()
    assert markdown_output.exists()
    with csv_output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["venue"] == "VenueWriter"
    assert "missing_settlement_rules" in rows[0]["blockers"]


def test_audit_venue_metadata_coverage_cli_writes_reports(tmp_path, capsys) -> None:
    _write_snapshot(
        tmp_path / "reports" / "cli.json",
        markets=[
            {
                "venue": "VenueCli",
                "event_id": "event-cli",
                "market_id": "market-cli",
                "title": "CLI row",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
            }
        ],
    )
    json_output = tmp_path / "coverage.json"
    csv_output = tmp_path / "coverage.csv"
    markdown_output = tmp_path / "coverage.md"

    result = scan.main(
        [
            "audit-venue-metadata-coverage",
            "--input-dir",
            str(tmp_path / "reports"),
            "--json-output",
            str(json_output),
            "--csv-output",
            str(csv_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "venue_metadata_coverage_status=OK" in stdout
    assert "markets=1" in stdout
    assert json_output.exists()
    assert csv_output.exists()
    assert markdown_output.exists()


def test_explicit_alias_fields_are_recognized_with_evidence_pointers(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "aliases.json",
        markets=[
            {
                "venue": "AliasVenue",
                "eventSlug": "alias-event",
                "clobTokenId": "token-123",
                "question": "Alias market",
                "raw": {
                    "outcomes": "[\"Yes\", \"No\"]",
                    "outcomePrices": "[\"0.4\", \"0.6\"]",
                    "resolutionSource": "venue rulebook",
                    "resolutionDate": "2026-06-01T00:00:00+00:00",
                    "snapshotTime": "2026-05-25T12:00:00+00:00",
                },
                "rules_primary": "Explicit rules only.",
                "best_ask": 0.4,
                "depth_at_best_ask": 12,
                "fee_model": "known_fixture_fee_model",
            }
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["markets"][0]
    assert row["event_id"] == "alias-event"
    assert row["market_id"] == "token-123"
    assert row["settlement_source"] == "venue rulebook"
    assert row["resolution_time"] == "2026-06-01T00:00:00+00:00"
    assert row["quote_timestamp"] == "2026-05-25T12:00:00+00:00"
    assert row["field_evidence"]["event_id"]["path"] == "row.eventSlug"
    assert row["field_evidence"]["market_id"]["path"] == "row.clobTokenId"
    assert row["field_evidence"]["settlement_source"]["path"] == "row.raw.resolutionSource"
    assert row["field_evidence"]["outcomes"]["path"] == "row.raw.outcomes"


def test_blocker_drilldown_groups_by_venue_and_source_file(tmp_path) -> None:
    one = tmp_path / "reports" / "one.json"
    two = tmp_path / "reports" / "two.json"
    _write_snapshot(
        one,
        markets=[
            {"venue": "DrillVenue", "market_id": "m1", "title": "Missing fields"},
            {"venue": "DrillVenue", "market_id": "m2", "title": "Missing fields"},
        ],
    )
    _write_snapshot(two, markets=[{"venue": "DrillVenue", "market_id": "m3", "title": "Missing fields"}])

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    drilldown = [
        row
        for row in report["blocker_drilldown"]
        if row["blocker"] == "missing_outcome_list" and row["venue"] == "DrillVenue"
    ]
    assert sorted(row["affected_market_count"] for row in drilldown) == [1, 2]
    assert any(row["source_file"] == str(one) and row["example_market_ids"] == ["m1", "m2"] for row in drilldown)
    assert all("row.outcomes" in row["example_raw_field_paths_checked"] for row in drilldown)


def test_category_breadth_summary_counts_topics(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "categories.json",
        markets=[
            {
                "venue": "BreadthVenue",
                "event_id": "sports-event",
                "market_id": "sports-1",
                "title": "Sports row",
                "category": "sports",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules": "Rules",
                "settlement_source": "rules",
                "resolution_time": "2026-06-01T00:00:00+00:00",
            },
            {
                "venue": "BreadthVenue",
                "event_id": "macro-event",
                "market_id": "macro-1",
                "title": "Macro row",
                "topic": "macro",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
            },
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    breadth = {(row["venue"], row["category"]): row for row in report["category_breadth"]}
    assert breadth[("BreadthVenue", "sports")]["market_count"] == 1
    assert breadth[("BreadthVenue", "sports")]["match_ready_count"] == 1
    assert breadth[("BreadthVenue", "macro")]["market_count"] == 1


def test_record_update_timestamps_do_not_count_as_quote_freshness(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "freshness.json",
        markets=[
            {
                "venue": "FreshnessVenue",
                "event_id": "event-fresh",
                "market_id": "fresh-1",
                "title": "Stale orderbook + recently edited metadata",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules": "Resolves per venue rulebook.",
                "settlement_source": "venue_rules",
                "resolution_time": "2026-06-01T00:00:00+00:00",
                "best_ask": 0.42,
                "depth_at_best_ask": 10,
                "fee_model_status": "known_fixture_fee_model",
                "raw": {
                    "updatedAt": "2026-05-25T12:00:00+00:00",
                    "updated_at": "2026-05-25T12:00:00+00:00",
                    "last_update_time": "2026-05-25T12:00:00+00:00",
                    "updated_time": "2026-05-25T12:00:00+00:00",
                },
            },
            {
                "venue": "FreshnessVenue",
                "event_id": "event-fresh",
                "market_id": "fresh-2",
                "title": "Stale orderbook with true orderbook capture timestamp",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules": "Resolves per venue rulebook.",
                "settlement_source": "venue_rules",
                "resolution_time": "2026-06-01T00:00:00+00:00",
                "best_ask": 0.42,
                "depth_at_best_ask": 10,
                "fee_model_status": "known_fixture_fee_model",
                "orderbook_enrichment": {
                    "enrichment_status": "enriched",
                    "orderbook_captured_at": "2026-05-25T12:00:00+00:00",
                    "best_ask": 0.42,
                    "depth_at_best_ask": 10,
                },
            },
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    stale = next(row for row in report["markets"] if row["market_id"] == "fresh-1")
    captured = next(row for row in report["markets"] if row["market_id"] == "fresh-2")
    assert stale["quote_timestamp"] is None
    assert "missing_quote_timestamp" in stale["blockers"]
    assert stale["readiness_tier"] != TIER_EXECUTION_EVALUATION_READY
    assert captured["quote_timestamp"] == "2026-05-25T12:00:00+00:00"
    assert captured["readiness_tier"] == TIER_EXECUTION_EVALUATION_READY


def test_broad_kalshi_without_orderbook_remains_not_evaluator_ready(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "kalshi_broad.json",
        source="kalshi_markets",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXBROAD",
                "ticker": "KXBROAD-YES",
                "title": "Will broad market resolve yes?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules_primary": "Explicit rules.",
                "settlement_source": "kalshi_rules",
                "expiration_time": "2026-06-01T00:00:00+00:00",
                "updated_time": "2026-05-25T12:00:00+00:00",
            }
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["markets"][0]
    assert row["venue"] == "kalshi"
    assert row["readiness_tier"] != TIER_EXECUTION_EVALUATION_READY
    assert {"missing_orderbook", "missing_depth", "missing_top_of_book", "missing_quote_timestamp"} <= set(row["blockers"])
    assert row["quote_timestamp"] is None


def test_enriched_kalshi_orderbook_capture_depth_and_freshness_are_evaluator_ready(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "kalshi_enriched.json",
        source="kalshi_markets",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXREADY",
                "ticker": "KXREADY-YES",
                "title": "Will enriched market resolve yes?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules_primary": "Explicit rules.",
                "settlement_source": "kalshi_rules",
                "expiration_time": "2026-06-01T00:00:00+00:00",
                "orderbook_enrichment": {
                    "enrichment_status": "enriched",
                    "orderbook_captured_at": "2026-05-25T12:00:00+00:00",
                    "best_bid": 0.42,
                    "best_ask": 0.44,
                    "depth_at_best_bid": 10,
                    "depth_at_best_ask": 7,
                    "source_endpoint": "https://example.test/markets/KXREADY-YES/orderbook",
                },
            }
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["markets"][0]
    assert row["readiness_tier"] == TIER_EXECUTION_EVALUATION_READY
    assert row["quote_timestamp"] == "2026-05-25T12:00:00+00:00"
    assert row["field_evidence"]["quote_timestamp"]["path"] == "row.orderbook_enrichment.orderbook_captured_at"


def test_liquidity_alone_does_not_count_as_depth(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "liquidity.json",
        source="kalshi_markets",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXLIQ",
                "ticker": "KXLIQ-YES",
                "title": "Will liquidity-only market resolve yes?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules_primary": "Explicit rules.",
                "settlement_source": "kalshi_rules",
                "expiration_time": "2026-06-01T00:00:00+00:00",
                "best_ask": 0.44,
                "liquidity": 1000000,
                "quote_timestamp": "2026-05-25T12:00:00+00:00",
            }
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["markets"][0]
    assert "missing_depth" in row["blockers"]
    assert row["has_depth"] is False
    assert row["readiness_tier"] != TIER_EXECUTION_EVALUATION_READY


def test_malformed_unenriched_orderbook_does_not_count_as_quote_freshness(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "malformed_orderbook.json",
        source="kalshi_markets",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXMAL",
                "ticker": "KXMAL-YES",
                "title": "Will malformed orderbook market resolve yes?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules_primary": "Explicit rules.",
                "settlement_source": "kalshi_rules",
                "expiration_time": "2026-06-01T00:00:00+00:00",
                "orderbook_enrichment": {
                    "enrichment_status": "unenriched",
                    "orderbook_captured_at": "2026-05-25T12:00:00+00:00",
                    "best_bid": None,
                    "best_ask": None,
                    "depth_at_best_bid": None,
                    "depth_at_best_ask": None,
                    "enrichment_warnings": ["parse_error"],
                },
            }
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["markets"][0]
    assert row["quote_timestamp"] is None
    assert {"missing_depth", "missing_top_of_book", "missing_quote_timestamp"} <= set(row["blockers"])
    assert row["readiness_tier"] != TIER_EXECUTION_EVALUATION_READY


def test_summary_reports_unique_market_count_when_files_duplicate_markets(tmp_path) -> None:
    market = {
        "venue": "DupVenue",
        "event_id": "event-dup",
        "market_id": "shared-1",
        "title": "Same market in two files",
        "outcomes": [{"name": "Yes"}, {"name": "No"}],
        "rules": "Rules",
        "settlement_source": "rules",
        "resolution_time": "2026-06-01T00:00:00+00:00",
    }
    _write_snapshot(tmp_path / "reports" / "snapshot.json", markets=[market])
    _write_snapshot(tmp_path / "reports" / "enriched.json", markets=[market])

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    summary = report["summary"]
    assert summary["market_row_count"] == 2
    assert summary["unique_market_count"] == 1
    assert summary["unique_evaluator_ready_market_count"] == 0


def test_title_only_outcome_and_settlement_text_do_not_count(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "title_only.json",
        markets=[
            {
                "venue": "TitleOnlyVenue",
                "event_id": "event-title",
                "market_id": "market-title",
                "title": "Will Team A or Team B win according to official settlement rules?",
                "resolution_time": "2026-06-01T00:00:00+00:00",
            }
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["markets"][0]
    assert "missing_outcome_list" in row["blockers"]
    assert "missing_settlement_rules" in row["blockers"]
    assert "missing_settlement_source" in row["blockers"]


def test_settlement_metadata_rules_text_is_not_source_url(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "kalshi_rules_only.json",
        source="kalshi_markets",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXRULES",
                "ticker": "KXRULES-YES",
                "title": "Will rules-only market resolve yes?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "raw": {
                    "rules_primary": "Primary settlement rule text.",
                    "rules_secondary": "Secondary settlement rule text.",
                    "expected_expiration_time": "2026-06-01T00:00:00Z",
                },
            }
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    metadata = report["markets"][0]["settlement_metadata"]
    assert "Primary settlement rule text." in metadata["settlement_rules_text"]
    assert "Secondary settlement rule text." in metadata["settlement_rules_text"]
    assert metadata["settlement_source_url"] is None
    assert metadata["settlement_source_kind"] == "rules_text_only"
    assert "row.raw.rules_primary" in metadata["raw_evidence_paths"]
    assert "row.raw.rules_secondary" in metadata["raw_evidence_paths"]
    assert {
        "missing_settlement_source_url",
        "settlement_rules_text_only",
        "source_evidence_missing",
        "resolution_time_expected_not_actual",
    } <= set(metadata["blockers"])


def test_settlement_metadata_description_only_is_advisory(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "description_only.json",
        source="polymarket_gamma",
        markets=[
            {
                "venue": "polymarket",
                "event_slug": "desc-event",
                "market_id": "desc-market",
                "title": "Description-only market",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "raw": {
                    "description": "Description text that is not an external settlement source.",
                    "endDate": "2026-06-01T00:00:00Z",
                },
            }
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["markets"][0]
    metadata = row["settlement_metadata"]
    assert row["settlement_rules"] == "Description text that is not an external settlement source."
    assert metadata["settlement_rules_text"] is None
    assert metadata["settlement_source_url"] is None
    assert metadata["settlement_source_kind"] == "description_only"
    assert metadata["advisory_only_fields"] == [
        {
            "path": "row.raw.description",
            "reason": "description_is_not_settlement_source",
            "value_preview": "Description text that is not an external settlement source.",
        }
    ]
    assert {"missing_settlement_source_url", "description_only_not_source", "source_evidence_missing"} <= set(
        metadata["blockers"]
    )


def test_settlement_metadata_expected_expiration_time_is_not_actual(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "expected_time.json",
        source="kalshi_markets",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXEXPECTED",
                "ticker": "KXEXPECTED-YES",
                "title": "Expected expiration market",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules_primary": "Explicit rules.",
                "raw": {"expected_expiration_time": "2026-06-01T00:00:00Z"},
            }
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    metadata = report["markets"][0]["settlement_metadata"]
    assert metadata["resolution_time"] == "2026-06-01T00:00:00Z"
    assert metadata["resolution_time_kind"] == "expected"
    assert "resolution_time_expected_not_actual" in metadata["blockers"]
    assert report["summary"]["markets_with_resolution_time_expected"] == 1
    assert report["summary"]["markets_with_resolution_time_actual"] == 0


def test_settlement_metadata_explicit_source_url_requires_explicit_source_field(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "source_urls.json",
        source="polymarket_gamma",
        markets=[
            {
                "venue": "polymarket",
                "event_slug": "url-event",
                "market_id": "rules-url-only",
                "title": "Rules URL is still rules text",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules": "Rules text references https://example.test/not-a-source-field",
                "endDate": "2026-06-01T00:00:00Z",
            },
            {
                "venue": "polymarket",
                "event_slug": "url-event",
                "market_id": "explicit-source-url",
                "title": "Explicit source URL market",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules": "Rules text.",
                "raw": {
                    "source_url": "https://example.test/settlement-source",
                    "actual_resolution_time": "2026-06-02T00:00:00Z",
                },
            },
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    rules_url_only = next(row for row in report["markets"] if row["market_id"] == "rules-url-only")
    explicit = next(row for row in report["markets"] if row["market_id"] == "explicit-source-url")
    assert rules_url_only["settlement_metadata"]["settlement_source_url"] is None
    assert "missing_settlement_source_url" in rules_url_only["settlement_metadata"]["blockers"]
    assert explicit["settlement_metadata"]["settlement_source_url"] == "https://example.test/settlement-source"
    assert explicit["settlement_metadata"]["settlement_source_kind"] == "external_url"
    assert explicit["settlement_metadata"]["resolution_time_kind"] == "actual"
    assert "missing_settlement_source_url" not in explicit["settlement_metadata"]["blockers"]
    assert report["summary"]["markets_with_explicit_source_url"] == 1
    assert report["summary"]["markets_blocked_by_settlement_source"] == 1


def test_settlement_metadata_blockers_do_not_weaken_legacy_readiness(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "legacy_ready.json",
        source="kalshi_markets",
        markets=[
            {
                "venue": "kalshi",
                "event_ticker": "KXREADY",
                "ticker": "KXREADY-YES",
                "title": "Legacy-ready market with text source only",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules_primary": "Explicit rules.",
                "settlement_source": "Official venue text evidence, but no URL.",
                "expiration_time": "2026-06-01T00:00:00+00:00",
                "best_ask": 0.42,
                "depth_at_best_ask": 12,
                "quote_timestamp": "2026-05-25T12:00:00+00:00",
            }
        ],
    )

    report = build_venue_metadata_coverage_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["markets"][0]
    assert row["readiness_tier"] == TIER_EXECUTION_EVALUATION_READY
    assert row["tradability_claimed"] is False
    assert row["paper_candidate_emitted"] is False
    assert {"missing_settlement_source_url", "settlement_rules_text_only", "source_kind_unknown"} <= set(
        row["settlement_metadata"]["blockers"]
    )
    assert report["safety"]["affects_evaluator_gates"] is False
