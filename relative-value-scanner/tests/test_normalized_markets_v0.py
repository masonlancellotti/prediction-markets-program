from __future__ import annotations

import json
from datetime import datetime, timezone

import scan
from relative_value.normalized_markets_v0 import build_normalized_markets_v0_report


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


def _report(tmp_path):
    return build_normalized_markets_v0_report(
        input_dir=tmp_path / "reports",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_kalshi_like_fixture_normalizes_with_explicit_blockers(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "kalshi.json",
        source="kalshi_markets",
        markets=[
            {
                "venue": "kalshi",
                "event_id": "KXNBA-26",
                "ticker": "KXNBA-26-SAS",
                "market_id": "KXNBA-26-SAS",
                "title": "Will San Antonio win?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "best_bid": 0.37,
                "best_ask": 0.39,
                "raw": {
                    "event_ticker": "KXNBA-26",
                    "rules_primary": "If San Antonio wins, this market resolves Yes.",
                    "expected_expiration_time": "2026-06-30T14:00:00Z",
                    "close_time": "2028-06-29T14:00:00Z",
                    "yes_bid_size_fp": "100",
                    "yes_ask_size_fp": "200",
                    "updated_time": "2026-05-25T12:00:00Z",
                },
            }
        ],
    )

    report = _report(tmp_path)
    row = report["normalized_markets"][0]

    assert row["venue"] == "kalshi"
    assert row["event_ticker"] == "KXNBA-26"
    assert row["settlement"]["settlement_rules_text"] == "If San Antonio wins, this market resolves Yes."
    assert row["settlement"]["resolution_time_kind"] == "expected"
    assert row["quote_depth"]["captured_at"] is None
    assert row["fee_metadata"]["fee_model_status"] == "conservative_venue_default"
    assert row["readiness"]["fully_identity_ready"] is True
    assert row["readiness"]["fee_metadata_ready"] is True
    assert row["readiness"]["evaluator_metadata_ready"] is False
    assert {
        "missing_settlement_source_url",
        "settlement_rules_text_only",
        "resolution_time_expected_not_actual",
        "missing_quote_captured_at",
    } <= set(row["blockers"])


def test_polymarket_like_fixture_normalizes_with_explicit_blockers(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "polymarket.json",
        source="polymarket_gamma",
        markets=[
            {
                "venue": "polymarket",
                "event_id": "27830",
                "event_slug": "2026-nba-champion",
                "market_id": "553856",
                "question": "Will Oklahoma City win?",
                "raw": {
                    "outcomes": "[\"Yes\", \"No\"]",
                    "outcomePrices": "[\"0.425\", \"0.575\"]",
                    "clobTokenIds": "[\"yes-token\", \"no-token\"]",
                    "description": "Description text is advisory in the v0 contract.",
                    "resolutionSource": "https://www.nba.com/standings",
                    "endDate": "2026-07-01T00:00:00Z",
                    "updatedAt": "2026-05-25T12:00:00Z",
                    "feeSchedule": {"rate": 0.03},
                },
                "orderbook_enrichment": {
                    "enrichment_status": "enriched",
                    "best_bid": 0.42,
                    "best_ask": 0.43,
                    "depth_at_best_bid": 3377.66,
                    "depth_at_best_ask": 1431.24,
                    "depth_within_1c": {"bid": 1.0, "ask": 2.0, "total": 3.0},
                    "orderbook_captured_at": "2026-05-21T00:13:04.289692+00:00",
                    "source_endpoint": "https://clob.polymarket.com/book?token_id=yes-token",
                },
            }
        ],
    )

    report = _report(tmp_path)
    row = report["normalized_markets"][0]

    assert row["venue"] == "polymarket"
    assert row["token_id"] == "yes-token"
    assert row["outcomes"][0]["token_id"] == "yes-token"
    assert row["settlement"]["settlement_source_url"] == "https://www.nba.com/standings"
    assert row["settlement"]["settlement_source_kind"] == "external_url"
    assert row["settlement"]["resolution_time_kind"] == "deadline"
    assert row["quote_depth"]["captured_at"] == "2026-05-21T00:13:04.289692+00:00"
    assert row["readiness"]["quote_depth_ready"] is True
    assert row["readiness"]["fee_metadata_ready"] is True
    assert "missing_settlement_rules_text" in row["blockers"]
    assert row["readiness"]["evaluator_metadata_ready"] is False


def test_arbitrary_new_venue_normalizes_without_venue_specific_crash(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "venue_x.json",
        source="venue_x_saved",
        markets=[
            {
                "venue": "VenueX",
                "event_id": "event-x",
                "market_id": "market-x",
                "title": "Will fixture resolve?",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "rules": "Fixture rule text.",
                "resolution_source_url": "https://example.test/source",
                "resolution_time": "2026-06-01T00:00:00+00:00",
                "fee_metadata": {
                    "fee_model_status": "explicit",
                    "fee_model_name": "FixtureFeeModel",
                    "source": "fixture review",
                    "source_kind": "reviewed_saved_fixture",
                    "review_status": "reviewed",
                },
            }
        ],
    )

    report = _report(tmp_path)
    row = report["normalized_markets"][0]

    assert row["venue"] == "VenueX"
    assert row["readiness"]["fully_identity_ready"] is True
    assert row["readiness"]["settlement_metadata_ready"] is True
    assert row["readiness"]["fee_metadata_ready"] is True
    assert row["readiness"]["quote_depth_ready"] is False
    assert "missing_quote_captured_at" in row["blockers"]


def test_raw_source_and_evidence_pointers_are_preserved(tmp_path) -> None:
    snapshot = tmp_path / "reports" / "evidence.json"
    _write_snapshot(
        snapshot,
        markets=[
            {
                "venue": "EvidenceVenue",
                "event_id": "event-e",
                "market_id": "market-e",
                "title": "Evidence market",
                "outcomes": [{"name": "Yes"}],
                "rules": "Rule text.",
                "source_url": "https://example.test/evidence",
                "resolution_time": "2026-06-01T00:00:00+00:00",
            }
        ],
    )

    row = _report(tmp_path)["normalized_markets"][0]

    assert row["source_file"] == str(snapshot)
    assert row["row_index"] == 0
    assert row["field_evidence"]["market_id"]["path"] == "row.market_id"
    assert "row.rules" in row["settlement"]["raw_evidence_paths"]
    assert "row.source_url" in row["settlement"]["raw_evidence_paths"]


def test_record_update_timestamps_do_not_count_as_quote_freshness(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "stale_quote.json",
        source="kalshi_markets",
        markets=[
            {
                "venue": "kalshi",
                "event_id": "event-k",
                "market_id": "market-k",
                "title": "Stale quote market",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "best_bid": 0.40,
                "best_ask": 0.42,
                "depth_at_best_bid": 10,
                "depth_at_best_ask": 12,
                "raw": {"updatedAt": "2026-05-25T12:00:00Z"},
            }
        ],
    )

    row = _report(tmp_path)["normalized_markets"][0]

    assert row["quote_depth"]["captured_at"] is None
    assert row["readiness"]["quote_depth_ready"] is False
    assert "missing_quote_captured_at" in row["blockers"]


def test_fee_metadata_requires_reviewed_source_or_conservative_default(tmp_path) -> None:
    _write_snapshot(
        tmp_path / "reports" / "fee.json",
        markets=[
            {
                "venue": "UnknownVenue",
                "event_id": "event-fee",
                "market_id": "raw-fee-only",
                "title": "Raw fee schedule only",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "raw": {"feeSchedule": {"rate": 0.03}},
            },
            {
                "venue": "UnknownVenue",
                "event_id": "event-fee",
                "market_id": "reviewed-fee",
                "title": "Reviewed fee model",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
                "fee_metadata": {
                    "fee_model_status": "explicit",
                    "fee_model_name": "ReviewedFixtureFee",
                    "source": "saved review fixture",
                    "source_kind": "reviewed_saved_fixture",
                    "review_status": "reviewed",
                    "fee_rate": 0.01,
                },
            },
            {
                "venue": "kalshi",
                "event_id": "event-fee",
                "market_id": "venue-default",
                "title": "Conservative default fee model",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
            },
        ],
    )

    rows = {row["market_id"]: row for row in _report(tmp_path)["normalized_markets"]}

    assert rows["raw-fee-only"]["readiness"]["fee_metadata_ready"] is False
    assert "missing_fee_reviewed_source" in rows["raw-fee-only"]["blockers"]
    assert rows["reviewed-fee"]["readiness"]["fee_metadata_ready"] is True
    assert rows["reviewed-fee"]["fee_metadata"]["fee_rate"] == 0.01
    assert rows["venue-default"]["readiness"]["fee_metadata_ready"] is True
    assert rows["venue-default"]["fee_metadata"]["source_kind"] == "conservative_default"


def test_normalize_market_snapshots_cli_writes_report_and_coverage(tmp_path, capsys) -> None:
    _write_snapshot(
        tmp_path / "reports" / "cli.json",
        markets=[
            {
                "venue": "CliVenue",
                "event_id": "event-cli",
                "market_id": "market-cli",
                "title": "CLI market",
                "outcomes": [{"name": "Yes"}, {"name": "No"}],
            }
        ],
    )
    json_output = tmp_path / "normalized.json"
    coverage_output = tmp_path / "coverage.json"

    result = scan.main(
        [
            "normalize-market-snapshots",
            "--input-dir",
            str(tmp_path / "reports"),
            "--json-output",
            str(json_output),
            "--coverage-output",
            str(coverage_output),
        ]
    )

    assert result == 0
    stdout = capsys.readouterr().out
    assert "normalized_markets_v0_status=OK" in stdout
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    coverage = json.loads(coverage_output.read_text(encoding="utf-8"))
    assert payload["source"] == "normalized_market_contract_v0"
    assert coverage["source"] == "normalized_market_contract_v0_coverage"
    assert coverage["summary"]["normalized_count"] == 1
    assert payload["safety"]["feeds_evaluator_by_default"] is False
