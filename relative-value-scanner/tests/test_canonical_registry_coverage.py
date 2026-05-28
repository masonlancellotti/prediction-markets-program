from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.canonical_registry_coverage import build_canonical_registry_coverage_report


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def test_no_registry_path_leaves_all_scopes_unreviewed(tmp_path: Path) -> None:
    reports = _write_reports(tmp_path, [_row()])

    report = build_canonical_registry_coverage_report(
        input_dir=reports,
        registry_path=None,
        generated_at=NOW,
    )

    assert report["summary"]["scopes_total"] == 1
    assert report["summary"]["scopes_reviewed"] == 0
    assert report["summary"]["scopes_unreviewed"] == 1
    assert report["summary"]["rows_covered_by_reviewed_scopes"] == 0
    assert report["summary"]["rows_uncovered"] == 1
    assert report["scopes"][0]["registry_match_count"] == 0
    assert report["scopes"][0]["registry_proposal_is_trust"] is False


def test_one_reviewed_entry_matching_scope_increases_reviewed_and_covered_exactly(tmp_path: Path) -> None:
    reports = _write_reports(tmp_path, [_row(threshold=100000.0), _row(threshold=101000.0, ticker="KXBTC-26MAY28-T101000")])
    registry = tmp_path / "registry.json"
    _write_registry(registry, threshold=100000.0)

    report = build_canonical_registry_coverage_report(
        input_dir=reports,
        registry_path=registry,
        generated_at=NOW,
    )
    scope = report["scopes"][0]

    assert report["summary"]["scopes_total"] == 1
    assert report["summary"]["scopes_reviewed"] == 1
    assert report["summary"]["scopes_unreviewed"] == 0
    assert report["summary"]["rows_covered_by_reviewed_scopes"] == 1
    assert report["summary"]["rows_uncovered"] == 1
    assert scope["registry_match_count"] == 1
    assert scope["registry_entry_id_if_matched"] == "crypto-btc-reviewed"
    assert scope["reviewer"] == "fixture-reviewer"
    assert scope["reviewed_at"] == "2026-05-25"


def test_wrong_scope_registry_entry_does_not_match(tmp_path: Path) -> None:
    reports = _write_reports(tmp_path, [_row()])
    registry = tmp_path / "registry.json"
    _write_registry(registry, threshold=100000.0, event_ticker_prefix="KXETH")

    report = build_canonical_registry_coverage_report(
        input_dir=reports,
        registry_path=registry,
        generated_at=NOW,
    )

    assert report["summary"]["scopes_reviewed"] == 0
    assert report["summary"]["rows_covered_by_reviewed_scopes"] == 0
    assert report["scopes"][0]["registry_match_count"] == 0
    assert report["scopes"][0]["registry_entry_id_if_matched"] is None


def test_matching_scope_changes_coverage_only_when_reviewed_entry_is_valid(tmp_path: Path) -> None:
    reports = _write_reports(tmp_path, [_row()])
    invalid_registry = tmp_path / "invalid_registry.json"
    _write_scope_registry(invalid_registry, canonical_source_url="<TODO: confirm with venue rules>")

    invalid_report = build_canonical_registry_coverage_report(
        input_dir=reports,
        registry_path=invalid_registry,
        generated_at=NOW,
    )

    assert invalid_report["summary"]["scopes_reviewed"] == 0
    assert invalid_report["summary"]["rows_covered_by_reviewed_scopes"] == 0

    valid_registry = tmp_path / "valid_registry.json"
    _write_scope_registry(valid_registry, canonical_source_url="https://www.example.com/reviewed-btcd-source")
    valid_report = build_canonical_registry_coverage_report(
        input_dir=reports,
        registry_path=valid_registry,
        generated_at=NOW,
    )

    assert valid_report["summary"]["scopes_reviewed"] == 1
    assert valid_report["summary"]["rows_covered_by_reviewed_scopes"] == 1
    assert valid_report["summary"]["rows_uncovered"] == 0


def test_event_ticker_scoped_registry_entry_covers_only_matching_rows(tmp_path: Path) -> None:
    reports = _write_reports(
        tmp_path,
        [
            _row(
                scope_key="CRYPTO_PRICE_THRESHOLD|kalshi|KXBTCD|BTC|May 22, 2026",
                ticker="KXBTCD-26MAY2217-T92499.99",
                measurement_date="May 22, 2026",
                threshold=92499.99,
            ),
            _row(
                scope_key="CRYPTO_PRICE_THRESHOLD|kalshi|KXBTCD|BTC|May 22, 2026",
                ticker="KXBTCD-26MAY2207-T86799.99",
                measurement_date="May 22, 2026",
                threshold=86799.99,
            ),
        ],
    )
    registry = tmp_path / "registry.json"
    _write_scope_registry(
        registry,
        canonical_source_url="https://www.example.com/reviewed-btcd-source",
        event_ticker_prefix="KXBTCD",
        event_ticker="KXBTCD-26MAY2217",
        measurement_date="May 22, 2026",
        price_source_index="brti",
    )

    report = build_canonical_registry_coverage_report(
        input_dir=reports,
        registry_path=registry,
        generated_at=NOW,
    )
    scope = report["scopes"][0]

    assert scope["row_count"] == 2
    assert scope["registry_match_count"] == 1
    assert report["summary"]["scopes_reviewed"] == 1
    assert report["summary"]["rows_covered_by_reviewed_scopes"] == 1
    assert report["summary"]["rows_uncovered"] == 1


def test_cli_canonical_registry_coverage_default_loads_example_registry_when_present(tmp_path: Path, monkeypatch) -> None:
    reports = _write_reports(tmp_path, [_row()])
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    registry_path = docs / "example_canonical_convention_registry_v0.json"
    _write_registry(registry_path, threshold=100000.0)
    monkeypatch.setattr(scan, "PROJECT_ROOT", tmp_path)

    json_output = reports / "canonical_registry_coverage.json"
    markdown_output = reports / "canonical_registry_coverage.md"
    rc = scan.main(
        [
            "audit-canonical-registry-coverage",
            "--input-dir",
            str(reports),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert rc == 0
    assert payload["registry_path"] == str(registry_path)
    assert payload["summary"]["scopes_reviewed"] == 1
    assert payload["summary"]["rows_covered_by_reviewed_scopes"] == 1


def test_report_emits_no_paper_candidate_literal(tmp_path: Path) -> None:
    reports = _write_reports(tmp_path, [_row()])

    report = build_canonical_registry_coverage_report(
        input_dir=reports,
        registry_path=None,
        generated_at=NOW,
    )
    encoded = json.dumps(report)

    assert report["summary"]["paper_candidate_count"] == 0
    assert report["safety"]["paper_candidate_emitted"] is False
    assert "PAPER_CANDIDATE" not in encoded


def test_scope_sorting_is_deterministic_by_review_leverage(tmp_path: Path) -> None:
    reports = _write_reports(
        tmp_path,
        [
            _row(scope_key="CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|May 28, 2026", ticker="KXBTC-26MAY28-T100000"),
            _row(scope_key="CRYPTO_PRICE_THRESHOLD|kalshi|KXETH|ETH|May 29, 2026", ticker="KXETH-26MAY29-T5000", asset="ETH", measurement_date="May 29, 2026"),
            _row(scope_key="CRYPTO_PRICE_THRESHOLD|kalshi|KXETH|ETH|May 29, 2026", ticker="KXETH-26MAY29-T6000", asset="ETH", threshold=6000.0, measurement_date="May 29, 2026"),
        ],
    )

    report = build_canonical_registry_coverage_report(
        input_dir=reports,
        registry_path=None,
        generated_at=NOW,
    )

    assert [scope["scope_key"] for scope in report["scopes"]] == [
        "CRYPTO_PRICE_THRESHOLD|kalshi|KXETH|ETH|May 29, 2026",
        "CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|May 28, 2026",
    ]
    assert [scope["scope_key"] for scope in report["summary"]["scopes_sorted_by_review_leverage"]] == [
        "CRYPTO_PRICE_THRESHOLD|kalshi|KXETH|ETH|May 29, 2026",
        "CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|May 28, 2026",
    ]
    assert report["next_manual_review"]["top_unreviewed_scopes"][0]["scope_key"] == "CRYPTO_PRICE_THRESHOLD|kalshi|KXETH|ETH|May 29, 2026"
    skeleton = report["next_manual_review"]["top_unreviewed_scopes"][0]["registry_entry_skeleton"]
    assert skeleton["canonical_source_url"] == "<TODO: canonical_source_url>"
    assert skeleton["evidence_quote_or_excerpt"] == "<TODO: evidence_quote_or_excerpt>"
    assert skeleton["reviewer"] == "<TODO: reviewer>"
    assert skeleton["reviewed_at"] == "<TODO: reviewed_at>"
    assert skeleton["review_until"] == "<TODO: review_until>"
    assert skeleton["confidence"] == "<TODO: confidence>"


def _write_reports(tmp_path: Path, rows: list[dict]) -> Path:
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "settlement_evidence_burden.json").write_text(
        json.dumps({"source": "settlement_evidence_burden_v1", "markets": []}),
        encoding="utf-8",
    )
    (reports / "family_graduation_crypto.json").write_text(
        json.dumps(
            {
                "source": "family_graduation_plan_v1",
                "family": "CRYPTO_PRICE_THRESHOLD",
                "rows": rows,
                "registry_proposal_groups": [],
            }
        ),
        encoding="utf-8",
    )
    (reports / "family_graduation_fed.json").write_text(
        json.dumps({"source": "family_graduation_plan_v1", "family": "FED_FOMC", "rows": [], "registry_proposal_groups": []}),
        encoding="utf-8",
    )
    return reports


def _row(
    *,
    scope_key: str = "CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|May 28, 2026",
    ticker: str = "KXBTC-26MAY28-T100000",
    threshold: float = 100000.0,
    asset: str = "BTC",
    measurement_date: str = "May 28, 2026",
) -> dict:
    return {
        "family": "CRYPTO_PRICE_THRESHOLD",
        "venue": "kalshi",
        "event_ticker": ticker.split("-T", 1)[0],
        "ticker": ticker,
        "market_id": ticker,
        "typed_keys": {
            "asset": asset,
            "threshold_value": threshold,
            "threshold_operator": "above",
            "measurement_date": measurement_date,
            "price_source_index": "brti",
        },
        "required_typed_keys": [
            "asset",
            "threshold_value",
            "threshold_operator",
            "measurement_date",
            "price_source_index",
        ],
        "present_typed_keys": [
            "asset",
            "threshold_value",
            "threshold_operator",
            "measurement_date",
            "price_source_index",
        ],
        "missing_typed_keys": [],
        "registry_proposal": {
            "proposal_id": f"CRYPTO_PRICE_THRESHOLD:kalshi:{ticker}",
            "source_url_candidate": "https://www.cfbenchmarks.com/data/indices/BRTI",
            "source_url_candidate_status": "hint_unreviewed_must_validate_against_venue_rules",
            "source_kind": "crypto_index_official",
            "official_source_description": "Fixture crypto index source.",
            "can_upgrade_to_exact_review_if_reviewed": True,
            "recommended_registry_scope": {
                "scope_kind": "FAMILY_CRYPTO_PRICE_THRESHOLD:venue:event_ticker_prefix:asset:measurement_date",
                "scope_key": scope_key,
                "scope_fields": {
                    "family": "CRYPTO_PRICE_THRESHOLD",
                    "venue": "kalshi",
                    "event_ticker_prefix": ticker.split("-", 1)[0],
                    "asset": asset,
                    "measurement_date": measurement_date,
                },
            },
        },
        "projection": {"can_upgrade_to_exact_review_if_reviewed": True},
    }


def _write_registry(path: Path, *, threshold: float, event_ticker_prefix: str = "KXBTC") -> None:
    path.write_text(
        json.dumps(
            {
                "registry_version": "canonical_convention_registry_v0",
                "entries": [
                    {
                        "registry_version": "canonical_convention_registry_v0",
                        "entry_id": "crypto-btc-reviewed",
                        "family": "CRYPTO_PRICE_THRESHOLD",
                        "reviewer": "fixture-reviewer",
                        "reviewed_at": "2026-05-25",
                        "applies_to_scope": {"venue": "kalshi", "event_ticker_prefix": event_ticker_prefix},
                        "typed_key_requirements": {
                            "required": [
                                "asset",
                                "threshold_value",
                                "threshold_operator",
                                "measurement_date",
                                "price_source_index",
                            ],
                            "match": {
                                "asset": "BTC",
                                "threshold_value": threshold,
                                "threshold_operator": "above",
                                "measurement_date": "May 28, 2026",
                                "price_source_index": "brti",
                            },
                        },
                        "canonical_source_kind": "crypto_index_official",
                        "canonical_source_url": "https://www.cfbenchmarks.com/data/indices/BRTI",
                        "evidence_quote_or_excerpt": "Fixture reviewed source excerpt.",
                        "limitations": "Fixture registry entry.",
                        "review_until": "2026-12-31",
                        "confidence": "high",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_scope_registry(
    path: Path,
    *,
    canonical_source_url: str,
    event_ticker_prefix: str = "KXBTC",
    event_ticker: str | None = None,
    measurement_date: str = "May 28, 2026",
    price_source_index: str = "brti",
) -> None:
    applies_to_scope = {"venue": "kalshi", "event_ticker_prefix": event_ticker_prefix}
    if event_ticker is not None:
        applies_to_scope["event_ticker"] = event_ticker
    path.write_text(
        json.dumps(
            {
                "registry_version": "canonical_convention_registry_v0",
                "entries": [
                    {
                        "registry_version": "canonical_convention_registry_v0",
                        "entry_id": "crypto-btc-scope-reviewed",
                        "family": "CRYPTO_PRICE_THRESHOLD",
                        "reviewer": "fixture-reviewer",
                        "reviewed_at": "2026-05-25",
                        "applies_to_scope": applies_to_scope,
                        "typed_key_requirements": {
                            "required": [
                                "asset",
                                "measurement_date",
                                "price_source_index",
                            ],
                            "match": {
                                "asset": "BTC",
                                "measurement_date": measurement_date,
                                "price_source_index": price_source_index,
                            },
                        },
                        "canonical_source_kind": "crypto_index_official",
                        "canonical_source_url": canonical_source_url,
                        "evidence_quote_or_excerpt": {
                            "kind": "saved_venue_rules_excerpt",
                            "text": "Saved venue rules identify CF Benchmarks' Bitcoin Real-Time Index as the settlement source.",
                        },
                        "limitations": "Fixture source-convention scope only.",
                        "review_until": "2026-08-23",
                        "confidence": "medium",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
