import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.family_graduation import build_family_graduation_report
from relative_value.settlement_evidence_burden import (
    FAMILY_CRYPTO_PRICE_THRESHOLD,
    TIER_DISCOVERY_READY,
    TIER_EXACT_PAYOFF_REVIEW_READY,
    TIER_FAMILY_TYPED_REVIEW_READY,
    TIER_SETTLEMENT_SOURCE_REVIEW_READY,
)


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def test_family_with_typed_ready_rows_gets_registry_proposal(tmp_path: Path) -> None:
    _write_burden(tmp_path, [_crypto_row()])
    _write_normalized(tmp_path, [_normalized_row()])

    report = build_family_graduation_report(
        input_dir=tmp_path,
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        generated_at=NOW,
    )

    assert report["family"] == FAMILY_CRYPTO_PRICE_THRESHOLD
    assert report["summary"]["candidate_row_count"] == 1
    assert report["summary"]["family_typed_ready_count"] == 1
    assert report["summary"]["registry_proposal_count"] == 1
    row = report["rows"][0]
    assert row["registry_proposal"]["source_kind"] == "crypto_index_official"
    assert row["registry_proposal"]["source_url_candidate"] == "https://www.cfbenchmarks.com/data/indices/BRTI"
    assert row["registry_proposal"]["reviewer_required"] is True
    assert row["registry_proposal"]["can_upgrade_to_exact_review_if_reviewed"] is True


def test_registry_proposal_does_not_upgrade_without_reviewed_registry_entry(tmp_path: Path) -> None:
    _write_burden(tmp_path, [_crypto_row()])
    _write_normalized(tmp_path, [_normalized_row()])

    report = build_family_graduation_report(
        input_dir=tmp_path,
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        generated_at=NOW,
    )
    projection = report["rows"][0]["projection"]

    assert projection["existing_reviewed_registry_match"] is False
    assert projection["current_tier_preserved"] == TIER_FAMILY_TYPED_REVIEW_READY
    assert projection["projected_tier_from_existing_registry_or_source"] == TIER_FAMILY_TYPED_REVIEW_READY
    assert projection["projected_tier_if_registry_reviewed"] == TIER_EXACT_PAYOFF_REVIEW_READY
    assert "registry_or_source_evidence_requires_human_review" in projection["projected_blockers_if_registry_or_source_added"]
    assert report["summary"]["existing_reviewed_registry_match_count"] == 0


def test_reviewed_registry_entry_projects_source_review_not_exact_without_quote_freshness(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_burden(tmp_path, [_crypto_row()])
    _write_normalized(tmp_path, [_normalized_row(quote_ready=False, fee_ready=False)])
    _write_registry(registry_path)

    report = build_family_graduation_report(
        input_dir=tmp_path,
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        registry_path=registry_path,
        generated_at=NOW,
    )
    projection = report["rows"][0]["projection"]

    assert projection["existing_reviewed_registry_match"] is True
    assert projection["projected_tier_from_existing_registry_or_source"] == TIER_SETTLEMENT_SOURCE_REVIEW_READY
    assert projection["projected_tier_if_registry_reviewed"] == TIER_SETTLEMENT_SOURCE_REVIEW_READY
    assert projection["can_upgrade_to_exact_review_if_reviewed"] is False
    assert projection["projected_execution_ready"] is False
    assert projection["can_upgrade_to_execution_evaluation_if_reviewed"] is False
    assert "missing_quote_captured_at" in projection["projected_blockers_if_registry_or_source_added"]
    assert "missing_quote_depth_or_freshness" in projection["projected_blockers_if_registry_or_source_added"]
    assert "missing_fee_metadata" in projection["projected_blockers_if_registry_or_source_added"]
    assert report["summary"]["existing_reviewed_registry_match_count"] == 1
    assert report["summary"]["projected_exact_review_from_existing_registry_count"] == 0
    assert report["summary"]["projected_execution_ready_count"] == 0


def test_missing_typed_key_blocks_graduation(tmp_path: Path) -> None:
    _write_burden(tmp_path, [_crypto_row(missing_typed_keys=["price_source_index"], tier=TIER_DISCOVERY_READY)])
    _write_normalized(tmp_path, [_normalized_row()])

    report = build_family_graduation_report(
        input_dir=tmp_path,
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        generated_at=NOW,
    )
    row = report["rows"][0]
    projection = row["projection"]

    assert row["missing_typed_keys"] == ["price_source_index"]
    assert row["registry_proposal"]["can_upgrade_to_exact_review_if_reviewed"] is False
    assert projection["projected_tier_if_registry_reviewed"] == TIER_DISCOVERY_READY
    assert "missing_required_typed_keys" in projection["projected_blockers_if_registry_or_source_added"]
    assert "missing_typed_key:price_source_index" in projection["projected_blockers_if_registry_or_source_added"]


def test_registry_proposal_carries_recommended_scope_and_url_status(tmp_path: Path) -> None:
    _write_burden(tmp_path, [_crypto_row()])
    _write_normalized(tmp_path, [_normalized_row()])

    report = build_family_graduation_report(
        input_dir=tmp_path,
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        generated_at=NOW,
    )
    proposal = report["rows"][0]["registry_proposal"]
    scope = proposal["recommended_registry_scope"]

    assert proposal["source_url_candidate_status"] == "hint_unreviewed_must_validate_against_venue_rules"
    assert scope["scope_kind"] == "FAMILY_CRYPTO_PRICE_THRESHOLD:venue:event_ticker_prefix:asset:measurement_date"
    assert scope["scope_fields"]["asset"] == "BTC"
    assert scope["scope_fields"]["event_ticker_prefix"] == "KXBTC"
    assert scope["scope_fields"]["measurement_date"] == "May 28, 2026"
    assert "Proposal is not trusted evidence." in proposal["limitations"]
    assert any("must be validated against the venue" in item for item in proposal["limitations"])


def test_registry_proposal_groups_collapse_many_rows_to_one_canonical_scope(tmp_path: Path) -> None:
    rows = [
        _crypto_row_with_ticker(f"KXBTC-26MAY28-T{100000 + 50 * i}")
        for i in range(5)
    ]
    _write_burden(tmp_path, rows)
    _write_normalized(tmp_path, [_normalized_row()])

    report = build_family_graduation_report(
        input_dir=tmp_path,
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        generated_at=NOW,
    )

    assert report["summary"]["registry_proposal_count"] == 5
    assert report["summary"]["registry_proposal_group_count"] == 1
    groups = report["registry_proposal_groups"]
    assert len(groups) == 1
    group = groups[0]
    assert group["row_count"] == 5
    assert group["rows_eligible_to_upgrade_to_exact_review_if_reviewed"] == 5
    assert group["scope_fields"]["asset"] == "BTC"
    assert group["scope_fields"]["measurement_date"] == "May 28, 2026"
    assert len(group["example_proposal_ids"]) == 5


def test_stale_quote_capture_blocks_family_graduation_exact_projection(tmp_path: Path) -> None:
    _write_burden(tmp_path, [_crypto_row()])
    _write_normalized(tmp_path, [_normalized_row(quote_ready=True, quote_captured_at="2026-05-25T11:00:00Z")])

    report = build_family_graduation_report(
        input_dir=tmp_path,
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        generated_at=NOW,
    )
    row = report["rows"][0]
    projection = row["projection"]

    assert row["quote_freshness_status"]["blocker"] == "stale_quote"
    assert projection["projected_tier_if_registry_reviewed"] == TIER_SETTLEMENT_SOURCE_REVIEW_READY
    assert projection["can_upgrade_to_exact_review_if_reviewed"] is False
    assert "stale_quote" in projection["projected_blockers_if_registry_or_source_added"]


def test_registry_status_indicates_when_no_registry_was_supplied(tmp_path: Path) -> None:
    _write_burden(tmp_path, [_crypto_row()])
    _write_normalized(tmp_path, [_normalized_row()])

    report = build_family_graduation_report(
        input_dir=tmp_path,
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        generated_at=NOW,
    )

    status = report["registry_status"]
    assert status["registry_path_supplied"] is False
    assert status["registry_loaded"] is False
    assert status["registry_entry_count"] == 0
    assert status["match_attempts_against_registry"] is False
    assert status["registry_proposal_is_trust"] is False
    assert "audit-canonical-registry-coverage" in status["notes"]


def test_registry_status_indicates_when_registry_was_loaded(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    _write_burden(tmp_path, [_crypto_row()])
    _write_normalized(tmp_path, [_normalized_row()])
    _write_registry(registry_path)

    report = build_family_graduation_report(
        input_dir=tmp_path,
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        registry_path=registry_path,
        generated_at=NOW,
    )

    status = report["registry_status"]
    assert status["registry_path_supplied"] is True
    assert status["registry_loaded"] is True
    assert status["registry_entry_count"] == 1
    assert status["match_attempts_against_registry"] is True
    assert "actual matching reviewed registry entries" in status["notes"]


def test_cli_family_graduation_default_loads_example_registry_when_present(tmp_path: Path, monkeypatch) -> None:
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    registry_path = docs / "example_canonical_convention_registry_v0.json"
    _write_registry(registry_path)
    _write_burden(tmp_path, [_crypto_row()])
    _write_normalized(tmp_path, [_normalized_row()])
    monkeypatch.setattr(scan, "PROJECT_ROOT", tmp_path)

    json_output = tmp_path / "family_graduation_crypto.json"
    markdown_output = tmp_path / "family_graduation_crypto.md"
    rc = scan.main(
        [
            "plan-family-graduation",
            "--family",
            FAMILY_CRYPTO_PRICE_THRESHOLD,
            "--input-dir",
            str(tmp_path),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )

    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert rc == 0
    assert payload["registry_path"] == str(registry_path)
    assert payload["registry_status"]["registry_path_supplied"] is True
    assert payload["registry_status"]["registry_loaded"] is True
    assert payload["registry_status"]["registry_entry_count"] == 1
    assert "actual matching reviewed registry entries" in payload["registry_status"]["notes"]
    assert payload["summary"]["existing_reviewed_registry_match_count"] == 1


def test_family_graduation_report_emits_no_paper_candidate(tmp_path: Path) -> None:
    _write_burden(tmp_path, [_crypto_row()])
    _write_normalized(tmp_path, [_normalized_row()])

    report = build_family_graduation_report(
        input_dir=tmp_path,
        family=FAMILY_CRYPTO_PRICE_THRESHOLD,
        generated_at=NOW,
    )
    encoded = json.dumps(report)

    assert report["summary"]["paper_candidate_count"] == 0
    assert report["safety"]["paper_candidate_emitted"] is False
    assert report["safety"]["affects_evaluator_gates"] is False
    assert "PAPER_CANDIDATE" not in encoded


def _write_burden(tmp_path: Path, rows: list[dict]) -> None:
    (tmp_path / "settlement_evidence_burden.json").write_text(
        json.dumps({"source": "settlement_evidence_burden_v1", "markets": rows}),
        encoding="utf-8",
    )


def _write_normalized(tmp_path: Path, rows: list[dict]) -> None:
    (tmp_path / "normalized_markets_v0.json").write_text(
        json.dumps({"source": "normalized_market_contract_v0", "normalized_markets": rows}),
        encoding="utf-8",
    )


def _write_registry(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "registry_version": "canonical_convention_registry_v0",
                "entries": [
                    {
                        "registry_version": "canonical_convention_registry_v0",
                        "entry_id": "crypto-btc-brti-100000-2026-05-28",
                        "family": FAMILY_CRYPTO_PRICE_THRESHOLD,
                        "reviewer": "fixture-reviewer",
                        "reviewed_at": "2026-05-25",
                        "applies_to_scope": {"venue": "kalshi", "ticker_prefix": "KXBTC"},
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
                                "threshold_value": 100000.0,
                                "threshold_operator": "above",
                                "measurement_date": "May 28, 2026",
                                "price_source_index": "brti",
                            },
                        },
                        "canonical_source_kind": "crypto_index_official",
                        "canonical_source_url": "https://www.cfbenchmarks.com/data/indices/BRTI",
                        "evidence_quote_or_excerpt": "Fixture reviewed index convention excerpt.",
                        "limitations": "Fixture entry for exact typed-key registry match only.",
                        "review_until": "2026-12-31",
                        "confidence": "high",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _crypto_row_with_ticker(ticker: str) -> dict:
    row = _crypto_row()
    row["ticker"] = ticker
    row["market_id"] = ticker
    return row


def _crypto_row(
    *,
    missing_typed_keys: list[str] | None = None,
    tier: str = TIER_FAMILY_TYPED_REVIEW_READY,
) -> dict:
    missing = missing_typed_keys or []
    evidence = {
        "asset": {"value": "BTC", "source": "ticker_prefix_or_rules_text"},
        "threshold_value": {"value": 100000.0, "source": "ticker_T_pattern"},
        "threshold_operator": {"value": "above", "source": "rules_text:operator_phrase"},
        "measurement_date": {"value": "May 28, 2026", "source": "rules_text:date_pattern"},
        "price_source_index": {"value": "brti", "source": "rules_text:index_phrase"},
    }
    for key in missing:
        evidence.pop(key, None)
    required = ["asset", "threshold_value", "threshold_operator", "measurement_date", "price_source_index"]
    return {
        "venue": "kalshi",
        "event_id": "KXBTC-26MAY28",
        "event_ticker": "KXBTC-26MAY28",
        "event_slug": None,
        "market_id": "KXBTC-26MAY28-T100000",
        "ticker": "KXBTC-26MAY28-T100000",
        "title": "Fixture BTC threshold market",
        "family": FAMILY_CRYPTO_PRICE_THRESHOLD,
        "review_readiness_tier": tier,
        "settlement_source_url_present": False,
        "registry_match": None,
        "required_typed_keys": required,
        "present_typed_keys": [key for key in required if key not in missing],
        "missing_typed_keys": missing,
        "typed_key_evidence": evidence,
        "blockers": ["missing_required_typed_keys"] if missing else [],
        "source_file": "fixture.json",
        "row_index": 0,
    }


def _normalized_row(
    *,
    quote_ready: bool = True,
    fee_ready: bool = False,
    quote_captured_at: str | None = "2026-05-25T12:00:00Z",
) -> dict:
    return {
        "venue": "kalshi",
        "event_id": "KXBTC-26MAY28",
        "event_ticker": "KXBTC-26MAY28",
        "market_id": "KXBTC-26MAY28-T100000",
        "ticker": "KXBTC-26MAY28-T100000",
        "readiness": {
            "quote_depth_ready": quote_ready,
            "fee_metadata_ready": fee_ready,
        },
        "quote_depth": {
            "captured_at": quote_captured_at if quote_ready else None,
            "blockers": [] if quote_ready else ["missing_quote_captured_at"],
        },
        "fee_metadata": {
            "fee_model_status": "reviewed" if fee_ready else "missing",
            "review_status": "reviewed" if fee_ready else "missing",
            "blockers": [] if fee_ready else ["missing_fee_metadata"],
        },
    }
