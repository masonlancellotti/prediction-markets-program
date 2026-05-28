from __future__ import annotations

import json
from datetime import datetime, timezone

import scan
from relative_value.relative_value_ops_status import (
    TIER_DISCOVERY_READY,
    TIER_EXACT_PAYOFF_REVIEW_READY,
    TIER_FAMILY_TYPED_REVIEW_READY,
    TIER_SETTLEMENT_SOURCE_REVIEW_READY,
    build_relative_value_ops_status_report,
    render_relative_value_ops_status_markdown,
)


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _burden_report():
    markets = [
        {
            "venue": "kalshi",
            "event_id": "KXFED-27APR",
            "event_ticker": "KXFED-27APR",
            "ticker": "KXFED-27APR-T4.25",
            "family": "FED_FOMC",
            "review_readiness_tier": TIER_FAMILY_TYPED_REVIEW_READY,
            "settlement_source_url_present": False,
            "registry_match": None,
            "blockers": ["missing_settlement_source_for_evaluator"],
        },
        {
            "venue": "kalshi",
            "event_id": "KXBTC-26MAY2517",
            "event_ticker": "KXBTC-26MAY2517",
            "ticker": "KXBTC-26MAY2517-T86249.99",
            "family": "CRYPTO_PRICE_THRESHOLD",
            "review_readiness_tier": TIER_FAMILY_TYPED_REVIEW_READY,
            "settlement_source_url_present": False,
            "registry_match": None,
            "blockers": ["missing_settlement_source_for_evaluator"],
        },
        {
            "venue": "polymarket",
            "event_id": "nba-finals",
            "event_slug": "nba-finals",
            "ticker": "poly-nba",
            "family": "SPORTS_FUTURES_CHAMPIONSHIP",
            "review_readiness_tier": TIER_SETTLEMENT_SOURCE_REVIEW_READY,
            "settlement_source_url_present": True,
            "registry_match": None,
            "blockers": ["missing_required_typed_keys"],
        },
        {
            "venue": "kalshi",
            "event_id": "KXFED-27MAY",
            "event_ticker": "KXFED-27MAY",
            "ticker": "KXFED-27MAY-T4.50",
            "family": "FED_FOMC",
            "review_readiness_tier": TIER_EXACT_PAYOFF_REVIEW_READY,
            "settlement_source_url_present": False,
            "registry_match": {"entry_id": "fed-registry"},
            "blockers": ["missing_quote_depth_for_execution"],
        },
        {
            "venue": "kalshi",
            "event_id": "KXOTHER",
            "event_ticker": "KXOTHER",
            "ticker": "KXOTHER-1",
            "family": "OTHER_UNKNOWN",
            "review_readiness_tier": TIER_DISCOVERY_READY,
            "settlement_source_url_present": False,
            "registry_match": None,
            "blockers": ["family_not_classified"],
        },
    ]
    return {
        "schema_version": 1,
        "source": "settlement_evidence_burden_v1",
        "summary": {
            "unique_market_count": 5,
            "by_review_readiness_tier": {
                TIER_DISCOVERY_READY: 1,
                TIER_FAMILY_TYPED_REVIEW_READY: 2,
                TIER_SETTLEMENT_SOURCE_REVIEW_READY: 1,
                TIER_EXACT_PAYOFF_REVIEW_READY: 1,
                "EXECUTION_EVALUATION_READY": 0,
            },
            "by_family": {
                "FED_FOMC": {"market_count": 2},
                "CRYPTO_PRICE_THRESHOLD": {"market_count": 1},
                "SPORTS_FUTURES_CHAMPIONSHIP": {"market_count": 1},
                "OTHER_UNKNOWN": {"market_count": 1},
            },
            "top_blockers": [{"blocker": "missing_settlement_source_for_evaluator", "count": 2}],
        },
        "venues": [{"venue": "kalshi"}, {"venue": "polymarket"}],
        "markets": markets,
        "warnings": [],
    }


def _normalized_report():
    return {
        "schema_version": 1,
        "source": "normalized_market_contract_v0",
        "normalized_markets": [
            {
                "venue": "polymarket",
                "market_id": "poly-nba",
                "ticker": "poly-nba",
                "readiness": {"quote_depth_ready": False},
                "blockers": ["missing_orderbook"],
            },
            {
                "venue": "kalshi",
                "market_id": "KXFED-27MAY-T4.50",
                "ticker": "KXFED-27MAY-T4.50",
                "readiness": {"quote_depth_ready": False},
                "blockers": ["missing_orderbook"],
            },
            {
                "venue": "kalshi",
                "market_id": "KXBTC-26MAY2517-T86249.99",
                "ticker": "KXBTC-26MAY2517-T86249.99",
                "readiness": {"quote_depth_ready": True},
                "blockers": ["missing_settlement_source_url"],
            },
        ],
    }


def _normalized_coverage():
    return {
        "schema_version": 1,
        "source": "normalized_market_contract_v0_coverage",
        "summary": {
            "normalized_count": 3,
            "venue_count": 2,
            "quote_depth_ready": 1,
            "top_blockers": [{"blocker": "missing_orderbook", "count": 2}],
        },
        "venues": [{"venue": "kalshi"}, {"venue": "polymarket"}],
    }


def _venue_metadata():
    return {
        "schema_version": 1,
        "source": "venue_metadata_coverage_audit_v1",
        "summary": {
            "unique_market_count": 5,
            "top_blockers": [{"blocker": "missing_settlement_source", "count": 2}],
        },
        "venues": [{"venue": "kalshi"}, {"venue": "polymarket"}],
    }


def _triage():
    return {
        "schema_version": 1,
        "source": "cross_platform_opportunity_triage_v1",
        "summary": {
            "row_count": 3,
            "paper_candidate_count": 0,
            "relationship_class_counts": {"EXACT_EQUALITY_CANDIDATE": 2, "SIMILARITY_ONLY_RESEARCH": 1},
            "top_blockers": [{"blocker": "requires_existing_evaluator_gates_before_paper", "count": 2}],
        },
        "rows": [],
    }


def _write_core_reports(tmp_path):
    reports = tmp_path / "reports"
    _write(reports / "settlement_evidence_burden.json", _burden_report())
    _write(reports / "normalized_markets_v0.json", _normalized_report())
    _write(reports / "normalized_markets_v0_coverage.json", _normalized_coverage())
    _write(reports / "venue_metadata_coverage.json", _venue_metadata())
    _write(reports / "cross_platform_opportunity_triage.json", _triage())
    return reports


def _report(input_dir):
    return build_relative_value_ops_status_report(
        input_dir=input_dir,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_missing_reports_are_blockers_not_crashes(tmp_path) -> None:
    report = _report(tmp_path / "reports")

    assert report["summary"]["missing_report_count"] >= 5
    assert report["highest_priority_next_action"]["action"] == "GENERATE_MISSING_CORE_REPORTS"
    assert any(item["blocker"] == "saved_report_missing" for item in report["top_blockers"])


def test_counts_loaded_correctly_from_saved_reports(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    report = _report(reports)

    summary = report["summary"]
    assert summary["unique_market_count"] == 5
    assert summary["venues"] == ["kalshi", "polymarket"]
    assert summary["families"]["FED_FOMC"] == 2
    assert summary["discovery_ready_count"] == 1
    assert summary["family_typed_review_ready_count"] == 2
    assert summary["settlement_source_review_ready_count"] == 1
    assert summary["exact_payoff_review_ready_count"] == 1
    assert summary["cross_platform_candidate_counts"]["triage_row_count"] == 3


def test_no_paper_claim_without_existing_evaluator_report(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    report = _report(reports)

    assert report["paper_status"]["count"] == 0
    assert "PAPER_CANDIDATE" not in json.dumps(report)
    assert any(reason["reason_code"] == "no_existing_evaluator_positive_paper_report" for reason in report["not_ready_for_paper_because"])


def test_blockers_and_highlights_are_surfaced(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    report = _report(reports)

    blocker_names = {item["blocker"] for item in report["top_blockers"]}
    assert "missing_settlement_source_for_evaluator" in blocker_names
    assert "missing_orderbook" in blocker_names
    highlights = report["highlights"]
    assert highlights["fed_fomc_typed_ready_count"] == 2
    assert highlights["crypto_typed_ready_count"] == 1
    assert highlights["sports_source_ready_count"] == 1
    assert {"family": "SPORTS_FUTURES_CHAMPIONSHIP", "count": 1} in highlights["families_with_source_url_but_missing_quote_depth"]
    assert {"family": "CRYPTO_PRICE_THRESHOLD", "count": 1} in highlights["families_with_quote_depth_but_missing_registry_or_source"]


def test_next_action_is_deterministic_and_conservative(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    report = _report(reports)

    assert report["highest_priority_next_action"] == {
        "action": "ADD_SAVED_QUOTE_DEPTH_FRESHNESS_AND_FEE_EVIDENCE",
        "reason": "Exact-payoff review rows exist but none are execution-evaluation ready.",
        "report_only": True,
    }


def test_audit_clears_stale_paper_rows_so_ops_status_does_not_claim_positive_action(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    # An old evaluator file with PAPER_CANDIDATE rows still present in the input dir.
    _write(
        reports / "old_paper_candidates.json",
        {
            "schema_version": 1,
            "source": "paper_candidate_evaluator",
            "ledger": [
                {
                    "schema_version": 1,
                    "candidate_id": "old__pair__1",
                    "action": "PAPER_CANDIDATE",
                    "polymarket": {"market_id": "old-poly"},
                    "kalshi": {"ticker": "old-kalshi"},
                }
            ],
        },
    )
    # The forensic audit has already classified that row as stale / fake-edge.
    _write(
        reports / "existing_paper_candidate_audit.json",
        {
            "schema_version": 1,
            "source": "existing_paper_candidate_audit_v1",
            "summary": {
                "total_paper_candidate_rows_found": 1,
                "current_needs_review_count": 0,
                "stale_count": 1,
                "likely_fake_or_blocked_count": 1,
                "recommended_next_action": "ARCHIVE_OR_REGENERATE_FROM_CURRENT_REPORTS",
            },
            "candidates": [],
        },
    )

    report = _report(reports)
    paper_status = report["paper_status"]

    assert paper_status["count"] == 1, "raw walk count is preserved for traceability"
    assert paper_status["current_needs_review_count"] == 0
    assert paper_status["positive_evaluator_report_present"] is False
    assert paper_status["audit_present"] is True
    assert paper_status.get("audit_recommended_next_action") == "ARCHIVE_OR_REGENERATE_FROM_CURRENT_REPORTS"
    assert "existing_evaluator_positive_action" not in report["summary"]
    assert report["highest_priority_next_action"]["action"] in {
        "ARCHIVE_OR_REGENERATE_STALE_PAPER_CANDIDATES",
        "ADD_SAVED_QUOTE_DEPTH_FRESHNESS_AND_FEE_EVIDENCE",
        "REVIEW_CANONICAL_REGISTRY_OR_SOURCE_URLS",
    }
    # The misleading REVIEW_EXISTING_EVALUATOR_OUTPUT path must NOT fire when the audit cleared every row.
    assert report["highest_priority_next_action"]["action"] != "REVIEW_EXISTING_EVALUATOR_OUTPUT"
    # And the not-ready-for-paper reason should explain the stale classification.
    reason_codes = {reason["reason_code"] for reason in report["not_ready_for_paper_because"]}
    assert "existing_evaluator_paper_rows_classified_stale_or_blocked" in reason_codes


def test_ops_status_mentions_stale_archive_plan_when_present(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "old_paper_candidates.json",
        {
            "schema_version": 1,
            "source": "paper_candidate_evaluator",
            "ledger": [
                {
                    "schema_version": 1,
                    "candidate_id": "old__pair__1",
                    "action": "PAPER_CANDIDATE",
                    "polymarket": {"market_id": "old-poly"},
                    "kalshi": {"ticker": "old-kalshi"},
                }
            ],
        },
    )
    _write(
        reports / "existing_paper_candidate_audit.json",
        {
            "schema_version": 1,
            "source": "existing_paper_candidate_audit_v1",
            "summary": {
                "total_paper_candidate_rows_found": 1,
                "current_needs_review_count": 0,
                "stale_count": 1,
                "likely_fake_or_blocked_count": 1,
                "recommended_next_action": "ARCHIVE_OR_REGENERATE_FROM_CURRENT_REPORTS",
            },
        },
    )
    _write(
        reports / "stale_report_archive_plan.json",
        {
            "schema_version": 1,
            "source": "stale_report_archive_plan_v1",
            "summary": {
                "archive_candidate_count": 1,
                "suggested_command_count": 2,
            },
            "archive_dir": str(reports / "archive" / "2026-01-01"),
        },
    )

    report = _report(reports)

    assert report["summary"]["stale_report_archive_plan_present"] is True
    assert report["summary"]["stale_report_archive_plan_archive_candidate_count"] == 1
    assert report["stale_report_archive_plan"]["present"] is True
    assert report["highest_priority_next_action"]["action"] == "REVIEW_STALE_REPORT_ARCHIVE_PLAN"
    reason_codes = {reason["reason_code"] for reason in report["not_ready_for_paper_because"]}
    assert "stale_report_archive_plan_available" in reason_codes


def test_ops_status_advances_after_stale_archive_applied(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    burden = _burden_report()
    burden["markets"] = [row for row in burden["markets"] if row["review_readiness_tier"] != TIER_EXACT_PAYOFF_REVIEW_READY]
    burden["summary"]["by_review_readiness_tier"][TIER_EXACT_PAYOFF_REVIEW_READY] = 0
    _write(reports / "settlement_evidence_burden.json", burden)
    _write(reports / "existing_paper_candidate_audit.json", {
        "schema_version": 1,
        "source": "existing_paper_candidate_audit_v1",
        "summary": {
            "total_paper_candidate_rows_found": 1,
            "current_needs_review_count": 0,
            "stale_count": 1,
            "likely_fake_or_blocked_count": 1,
            "recommended_next_action": "ARCHIVE_OR_REGENERATE_FROM_CURRENT_REPORTS",
        },
    })
    _write(reports / "stale_report_archive_plan.json", {
        "schema_version": 1,
        "source": "stale_report_archive_plan_v1",
        "summary": {"archive_candidate_count": 1, "suggested_command_count": 2},
        "archive_dir": str(reports / "archive" / "2026-01-01"),
    })
    _write(reports / "stale_report_archive_applied.json", {
        "schema_version": 1,
        "source": "stale_report_archive_applied_v1",
        "status": "APPLIED",
        "summary": {
            "applied_move_count": 1,
            "noop_move_count": 0,
            "refused_move_count": 0,
            "covers_stale_archive_plan": True,
        },
    })
    _write(reports / "archive" / "2026-01-01" / "old_paper_candidates.json", {
        "schema_version": 1,
        "source": "paper_candidate_evaluator",
        "ledger": [{"candidate_id": "old__pair__1", "action": "PAPER_CANDIDATE"}],
    })
    _write(reports / "family_graduation_crypto.json", _family_graduation_report("CRYPTO_PRICE_THRESHOLD", ready_for_review=570, groups=12))
    _write(reports / "family_graduation_fed.json", _family_graduation_report("FED_FOMC", ready_for_review=218, groups=8))

    report = _report(reports)

    assert report["paper_status"]["count"] == 0
    assert report["paper_status"]["archive_applied_present"] is True
    assert report["summary"]["stale_report_archive_applied_present"] is True
    assert report["highest_priority_next_action"]["action"] == "REVIEW_FAMILY_GRADUATION_PROPOSALS"


def test_cli_relative_value_ops_status_writes_outputs(tmp_path, capsys) -> None:
    reports = _write_core_reports(tmp_path)
    json_output = reports / "relative_value_ops_status.json"
    markdown_output = reports / "relative_value_ops_status.md"

    rc = scan.main(
        [
            "relative-value-ops-status",
            "--input-dir",
            str(reports),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )
    stdout = capsys.readouterr().out

    assert rc == 0
    assert "relative_value_ops_status=OK" in stdout
    assert json_output.exists()
    assert markdown_output.exists()
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["source"] == "relative_value_ops_status_v1"


def _family_graduation_report(family: str, *, ready_for_review: int, groups: int) -> dict:
    return {
        "schema_version": 1,
        "source": "family_graduation_plan_v1",
        "family": family,
        "summary": {
            "candidate_row_count": ready_for_review,
            "family_typed_ready_count": ready_for_review,
            "ready_for_human_registry_review_count": ready_for_review,
            "registry_proposal_count": ready_for_review,
            "registry_proposal_group_count": groups,
            "existing_reviewed_registry_match_count": 0,
            "projected_exact_review_if_registry_reviewed_count": ready_for_review,
            "projected_exact_review_from_existing_registry_count": 0,
            "projected_execution_ready_count": 0,
            "paper_candidate_count": 0,
        },
        "rows": [],
    }


def test_family_graduation_status_surfaces_in_summary(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(reports / "family_graduation_crypto.json", _family_graduation_report("CRYPTO_PRICE_THRESHOLD", ready_for_review=570, groups=12))
    _write(reports / "family_graduation_fed.json", _family_graduation_report("FED_FOMC", ready_for_review=218, groups=8))

    report = _report(reports)
    status = report["summary"]["family_graduation_status"]

    assert status["family_graduation_reports_present"] == 2
    assert status["total_ready_for_human_registry_review_count"] == 788
    assert status["total_registry_proposal_group_count"] == 20
    assert status["best_family_for_human_review"] == "CRYPTO_PRICE_THRESHOLD"
    assert status["registry_proposal_is_trust"] is False


def test_canonical_registry_coverage_status_surfaces_in_summary(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "canonical_registry_coverage.json",
        {
            "schema_version": 1,
            "source": "canonical_registry_coverage_v1",
            "summary": {
                "scopes_total": 18,
                "scopes_reviewed": 2,
                "scopes_unreviewed": 16,
                "top_leverage_scope": "CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|2026-05-28",
                "rows_covered_by_reviewed_scopes": 218,
                "rows_uncovered": 786,
            },
            "safety": {
                "registry_proposal_is_trust": False,
                "affects_evaluator_gates": False,
            },
        },
    )

    report = _report(reports)
    coverage = report["summary"]["canonical_registry_coverage"]

    assert coverage == {
        "present": True,
        "scopes_total": 18,
        "scopes_reviewed": 2,
        "scopes_unreviewed": 16,
        "top_leverage_scope": "CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|2026-05-28",
        "rows_covered_by_reviewed_scopes": 218,
        "rows_uncovered": 786,
        "registry_proposal_is_trust": False,
    }
    markdown = render_relative_value_ops_status_markdown(report)
    assert "### canonical_registry_coverage" in markdown
    assert "rows_covered_by_reviewed_scopes: `218`" in markdown
    assert report["highest_priority_next_action"]["action"] == "RUN_PAPER_READINESS_PROBE"


def test_canonical_registry_expiry_status_surfaces_in_summary(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "canonical_registry_expiry_audit.json",
        {
            "schema_version": 1,
            "source": "canonical_registry_expiry_audit_v1",
            "summary": {
                "registry_entries_total": 9,
                "registry_entries_valid_current_review": 7,
                "registry_entries_expired": 1,
                "registry_entries_expiring_soon": 2,
                "registry_entries_missing_review_until": 1,
            },
        },
    )

    report = _report(reports)
    expiry = report["summary"]["canonical_registry_expiry_audit"]
    markdown = render_relative_value_ops_status_markdown(report)

    assert expiry == {
        "present": True,
        "registry_entries_total": 9,
        "registry_entries_valid_current_review": 7,
        "registry_entries_expired": 1,
        "registry_entries_expiring_soon": 2,
        "registry_entries_missing_review_until": 1,
    }
    assert "### canonical_registry_expiry_audit" in markdown
    assert "registry_entries_expired: `1`" in markdown
    assert "registry_entries_expiring_soon: `2`" in markdown


def test_standardized_btc_basis_risk_counts_surface_in_ops_status(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "standardized_family_candidates.json",
        {
            "schema_version": 1,
            "source": "standardized_family_candidates_v1",
            "summary": {
                "candidate_group_count": 2,
                "candidate_pair_count": 0,
                "cross_venue_candidate_group_count": 0,
                "cross_venue_candidate_pair_count": 0,
                "btc_basis_risk_review_count": 3,
                "btc_basis_risk_discovery_count": 1,
                "crypto_related_fv_watch_rows": 2,
                "crypto_related_fv_watch_by_asset": {"BTC": 2},
                "basis_risk_relationship_class_counts": {
                    "BTC_BASIS_RISK_REVIEW": 3,
                    "DISCOVERY_ONLY": 1,
                },
            },
        },
    )

    report = _report(reports)
    counts = report["summary"]["cross_platform_candidate_counts"]
    markdown = render_relative_value_ops_status_markdown(report)

    assert counts["standardized_btc_basis_risk_review_count"] == 3
    assert counts["standardized_btc_basis_risk_discovery_count"] == 1
    assert counts["standardized_basis_risk_relationship_class_counts"] == {
        "BTC_BASIS_RISK_REVIEW": 3,
        "DISCOVERY_ONLY": 1,
    }
    assert counts["standardized_crypto_related_fv_watch_rows"] == 2
    assert counts["standardized_crypto_related_fv_watch_by_asset"] == {"BTC": 2}
    assert "standardized_btc_basis_risk_review_count: `3`" in markdown
    assert "standardized_crypto_related_fv_watch_rows: `2`" in markdown


def test_polymarket_point_in_time_typed_key_audit_surfaces_in_ops_status(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "polymarket_point_in_time_typed_key_audit.json",
        {
            "schema_version": 1,
            "schema_kind": "polymarket_point_in_time_typed_key_audit_v1",
            "source": "polymarket_point_in_time_typed_key_audit_v1",
            "report_path": str(reports / "polymarket_point_in_time_typed_key_audit.json"),
            "summary": {
                "point_in_time_rows_seen": 4,
                "point_in_time_rows_audited": 3,
                "excluded_fake_point_in_time_rows": 1,
                "typed_complete_rows": 2,
                "targeted_clob_refresh_candidate_rows": 1,
                "rows_with_clob_attached": 1,
                "rows_with_bid_ask_size": 1,
                "exact_ready_rows": 0,
                "paper_candidate_rows": 0,
                "execution_ready_rows": 0,
                "top_blockers": [
                    {"blocker": "missing_clob_book", "count": 2},
                    {"blocker": "title_only_match_not_equivalence", "count": 3},
                ],
                "top_targeted_clob_refresh_candidates": [
                    {
                        "market_slug": "btc-point-test",
                        "market_family": "crypto_price",
                        "typed_key_completeness_score": 94.0,
                        "blockers": ["missing_clob_book", "title_only_match_not_equivalence"],
                    }
                ],
            },
        },
    )

    report = _report(reports)
    status = report["summary"]["polymarket_point_in_time_typed_key_audit"]
    markdown = render_relative_value_ops_status_markdown(report)

    assert status["present"] is True
    assert status["point_in_time_rows_audited"] == 3
    assert status["excluded_fake_point_in_time_rows"] == 1
    assert status["typed_complete_rows"] == 2
    assert status["targeted_clob_refresh_candidate_rows"] == 1
    assert status["rows_with_clob_attached"] == 1
    assert status["exact_ready_rows"] == 0
    assert status["paper_candidate_rows"] == 0
    assert "### polymarket_point_in_time_typed_key_audit" in markdown
    assert "point_in_time_rows_audited: `3`" in markdown
    assert "targeted_clob_refresh_candidate_rows: `1`" in markdown
    assert "btc-point-test" in markdown


def test_cdna_and_pending_registry_status_surface_in_summary(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "crypto_com_predict_cdna_research_snapshot.json",
        {
            "schema_version": 1,
            "source": "crypto_com_predict_cdna_research_snapshot_v1",
            "summary": {
                "parsed_rows": 1,
                "btc_rows": 1,
                "eth_rows": 0,
                "point_in_time_rows": 1,
                "deadline_or_range_hit_rows": 0,
                "basis_risk_compatible_with_kalshi": 1,
                "exact_payoff_compatible_with_kalshi": 0,
                "top_blockers": [
                    {"blocker": "cdna_saved_fixture_only", "count": 1},
                    {"blocker": "settlement_source_unverified", "count": 1},
                ],
            },
        },
    )
    _write(
        reports / "pending_registry_entries_plan.json",
        {
            "schema_version": 1,
            "source": "pending_registry_entries_plan_v1",
            "summary": {
                "pending_files_planned": 3,
                "pending_files_written": 3,
                "skipped_reviewed_scopes": 0,
                "top_scopes": ["scope-a", "scope-b", "scope-c"],
            },
        },
    )

    report = _report(reports)
    summary = report["summary"]
    markdown = render_relative_value_ops_status_markdown(report)

    cdna = summary["crypto_com_predict_cdna_research_snapshot"]
    assert cdna["present"] is True
    assert cdna["parsed_rows"] == 1
    assert cdna["btc_rows"] == 1
    assert cdna["eth_rows"] == 0
    assert cdna["point_in_time_rows"] == 1
    assert cdna["deadline_or_range_hit_rows"] == 0
    assert cdna["basis_risk_compatible_with_kalshi"] == 1
    assert cdna["top_blockers"] == ["cdna_saved_fixture_only", "settlement_source_unverified"]
    assert cdna["can_create_candidate_pair"] is False
    assert cdna["can_create_paper_candidate"] is False
    pending = summary["pending_registry_entries"]
    assert pending["present"] is True
    assert pending["pending_files_written"] == 3
    assert pending["registry_proposal_is_trust"] is False
    assert pending["reviewer_must_validate"] is True

    assert "### crypto_com_predict_cdna_research_snapshot" in markdown
    assert "eth_rows: `0`" in markdown
    assert "point_in_time_rows: `1`" in markdown
    assert "deadline_or_range_hit_rows: `0`" in markdown
    assert "basis_risk_compatible_with_kalshi: `1`" in markdown
    assert "top_blockers: `cdna_saved_fixture_only, settlement_source_unverified`" in markdown
    assert "### pending_registry_entries" in markdown
    assert "pending_files_written: `3`" in markdown


def test_reference_odds_fv_status_surfaces_in_summary(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "the_odds_api_fv_residuals.json",
        {
            "schema_version": 1,
            "source": "reference_odds_fv_residuals_v1",
            "summary": {
                "odds_events_read": 4,
                "reference_markets_read": 16,
                "matched_rows": 3,
                "unmatched_reference_rows": 13,
                "residual_rows": 3,
            },
            "safety": {
                "reference_only_source": True,
                "executable_leg": False,
                "affects_evaluator_gates": False,
            },
        },
    )

    report = _report(reports)
    status = report["summary"]["reference_odds_fv"]
    markdown = render_relative_value_ops_status_markdown(report)

    assert status == {
        "present": True,
        "odds_events_read": 4,
        "reference_markets_read": 16,
        "matched_rows": 3,
        "unmatched_reference_rows": 13,
        "residual_rows": 3,
        "reference_only_source": True,
        "executable_leg": False,
        "affects_evaluator_gates": False,
        "generated_at": None,
    }
    assert "### reference_odds_fv" in markdown
    assert "matched_rows: `3`" in markdown
    assert "reference_only_source: `true`" in markdown


def test_paper_readiness_probe_status_surfaces_in_summary(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "paper_readiness_probe.json",
        {
            "schema_version": 1,
            "source": "paper_readiness_probe_v1",
            "summary": {
                "total_rows_considered": 8,
                "rows_blocked_by_stale_quote": 3,
                "rows_blocked_by_missing_quote": 2,
                "rows_blocked_by_fee": 5,
                "rows_blocked_by_pair_review": 8,
                "paper_ready_count": 0,
            },
        },
    )

    report = _report(reports)
    status = report["summary"]["paper_readiness_probe"]
    markdown = render_relative_value_ops_status_markdown(report)

    assert status == {
        "present": True,
        "total_rows_considered": 8,
        "rows_blocked_by_stale_quote": 3,
        "rows_blocked_by_missing_quote": 2,
        "rows_blocked_by_fee": 5,
        "rows_blocked_by_pair_review": 8,
        "paper_ready_count": 0,
    }
    assert "### paper_readiness_probe" in markdown
    assert "total_rows_considered: `8`" in markdown


def test_paper_readiness_probe_next_action_fires_only_when_expected(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    burden = _burden_report()
    burden["summary"]["by_review_readiness_tier"]["EXECUTION_EVALUATION_READY"] = 0
    _write(reports / "settlement_evidence_burden.json", burden)
    _write(
        reports / "canonical_registry_coverage.json",
        {
            "schema_version": 1,
            "source": "canonical_registry_coverage_v1",
            "summary": {
                "scopes_total": 2,
                "scopes_reviewed": 1,
                "scopes_unreviewed": 1,
                "rows_covered_by_reviewed_scopes": 4,
                "rows_uncovered": 2,
            },
        },
    )

    report = _report(reports)
    assert report["highest_priority_next_action"]["action"] == "RUN_PAPER_READINESS_PROBE"

    burden["summary"]["by_review_readiness_tier"]["EXECUTION_EVALUATION_READY"] = 1
    _write(reports / "settlement_evidence_burden.json", burden)
    report_execution_ready = _report(reports)
    assert report_execution_ready["highest_priority_next_action"]["action"] != "RUN_PAPER_READINESS_PROBE"


def test_no_canonical_scopes_reviewed_reason_fires_only_when_expected(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    report_without_coverage = _report(reports)
    reason_codes = {reason["reason_code"] for reason in report_without_coverage["not_ready_for_paper_because"]}
    assert "no_canonical_scopes_reviewed_yet" not in reason_codes

    _write(
        reports / "canonical_registry_coverage.json",
        {
            "schema_version": 1,
            "source": "canonical_registry_coverage_v1",
            "summary": {
                "scopes_total": 3,
                "scopes_reviewed": 0,
                "scopes_unreviewed": 3,
                "rows_covered_by_reviewed_scopes": 0,
                "rows_uncovered": 25,
                "top_leverage_scope": "CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|2026-05-28",
            },
        },
    )
    report_unreviewed = _report(reports)
    reason_codes = {reason["reason_code"] for reason in report_unreviewed["not_ready_for_paper_because"]}
    assert "no_canonical_scopes_reviewed_yet" in reason_codes

    _write(
        reports / "canonical_registry_coverage.json",
        {
            "schema_version": 1,
            "source": "canonical_registry_coverage_v1",
            "summary": {
                "scopes_total": 3,
                "scopes_reviewed": 1,
                "scopes_unreviewed": 2,
                "rows_covered_by_reviewed_scopes": 10,
                "rows_uncovered": 15,
                "top_leverage_scope": "CRYPTO_PRICE_THRESHOLD|kalshi|KXBTC|BTC|2026-05-28",
            },
        },
    )
    report_reviewed = _report(reports)
    reason_codes = {reason["reason_code"] for reason in report_reviewed["not_ready_for_paper_because"]}
    assert "no_canonical_scopes_reviewed_yet" not in reason_codes


def test_family_graduation_drives_next_action_when_no_exact_review_yet(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    # Remove the exact-payoff-ready row so the next-action chain reaches the
    # family-graduation branch instead of ADD_SAVED_QUOTE_DEPTH_FRESHNESS_AND_FEE_EVIDENCE.
    burden = _burden_report()
    burden["markets"] = [row for row in burden["markets"] if row["review_readiness_tier"] != TIER_EXACT_PAYOFF_REVIEW_READY]
    burden["summary"]["by_review_readiness_tier"][TIER_EXACT_PAYOFF_REVIEW_READY] = 0
    _write(reports / "settlement_evidence_burden.json", burden)
    _write(reports / "family_graduation_crypto.json", _family_graduation_report("CRYPTO_PRICE_THRESHOLD", ready_for_review=570, groups=12))
    _write(reports / "family_graduation_fed.json", _family_graduation_report("FED_FOMC", ready_for_review=218, groups=8))

    report = _report(reports)
    action = report["highest_priority_next_action"]

    assert action["action"] == "REVIEW_FAMILY_GRADUATION_PROPOSALS"
    assert action["registry_proposal_is_trust"] is False
    assert "CRYPTO_PRICE_THRESHOLD" in action["reason"]
    assert "Registry proposals are not trust" in action["reason"]


def test_family_graduation_branch_skipped_when_reports_missing(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    report = _report(reports)
    status = report["summary"]["family_graduation_status"]
    assert status["family_graduation_reports_present"] == 0
    assert status["total_ready_for_human_registry_review_count"] == 0
    # With no family_graduation reports, the next-action falls back to the existing chain.
    assert report["highest_priority_next_action"]["action"] != "REVIEW_FAMILY_GRADUATION_PROPOSALS"


def test_ibkr_forecastex_authenticated_zero_candidates_drives_seed_conid_next_action(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "ibkr_forecastex_access_doctor.json",
        {
            "schema_kind": "ibkr_forecastex_access_doctor_v1",
            "status": "OK",
            "reachable": True,
            "authenticated": True,
            "blockers": ["account_permission_review_required"],
        },
    )
    _write(
        reports / "ibkr_forecastex_discovery_candidates.json",
        {
            "source": "ibkr_forecastex_discovery_candidates_v1",
            "summary": {
                "discovery_status": "SEED_CONIDS_REQUIRED",
                "discovery_statuses": ["LOCAL_SESSION_OK_BUT_NO_FORECASTX_FOUND", "SEED_CONIDS_REQUIRED"],
                "candidate_count": 0,
                "forecastx_candidate_count": 0,
                "normalized_possible_count": 0,
                "seed_candidate_count": 0,
            },
            "seed_conids_count": 0,
        },
    )

    report = _report(reports)
    markdown = render_relative_value_ops_status_markdown(report)

    assert report["summary"]["ibkr_forecastex_access_doctor"]["authenticated"] is True
    assert report["summary"]["ibkr_forecastex_discovery_candidates"]["forecastx_candidate_count"] == 0
    assert report["highest_priority_next_action"]["action"] == "PROVIDE_IBKR_FORECASTEX_CONID_OR_REFINE_DISCOVERY"
    assert "### ibkr_forecastex" in markdown
    assert "gateway_authenticated: `true`" in markdown


def test_ibkr_forecastex_ff_underlier_drives_contract_info_next_action(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "ibkr_forecastex_access_doctor.json",
        {
            "schema_kind": "ibkr_forecastex_access_doctor_v1",
            "status": "OK",
            "reachable": True,
            "authenticated": True,
            "blockers": ["account_permission_review_required"],
        },
    )
    _write(
        reports / "ibkr_forecastex_discovery_candidates.json",
        {
            "source": "ibkr_forecastex_discovery_candidates_v1",
            "summary": {
                "discovery_status": "FORECASTX_UNDERLIER_FOUND_NEEDS_STRIKES_OR_CONTRACT_INFO",
                "discovery_statuses": ["FORECASTX_UNDERLIER_FOUND_NEEDS_STRIKES_OR_CONTRACT_INFO"],
                "documented_seed_ff_attempted": True,
                "ff_underlier_found": True,
                "forecastx_underlier_candidates": 1,
                "forecastx_tradable_contract_candidates": 0,
                "forecastx_marketdata_rows": 0,
                "candidate_count": 1,
                "forecastx_candidate_count": 1,
                "normalized_possible_count": 0,
            },
        },
    )

    report = _report(reports)

    assert report["summary"]["ibkr_forecastex_discovery_candidates"]["ff_underlier_found"] is True
    assert report["highest_priority_next_action"]["action"] == "FORECASTX_UNDERLIER_FOUND_NEEDS_STRIKES_OR_CONTRACT_INFO"


def test_ibkr_forecastex_contract_info_without_marketdata_drives_permission_next_action(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "ibkr_forecastex_access_doctor.json",
        {
            "schema_kind": "ibkr_forecastex_access_doctor_v1",
            "status": "OK",
            "reachable": True,
            "authenticated": True,
            "blockers": ["account_permission_review_required"],
        },
    )
    _write(
        reports / "ibkr_forecastex_discovery_candidates.json",
        {
            "source": "ibkr_forecastex_discovery_candidates_v1",
            "summary": {
                "discovery_status": "FORECASTX_CONTRACT_INFO_FOUND_NEEDS_MARKETDATA_PERMISSION",
                "discovery_statuses": ["FORECASTX_CONTRACT_INFO_FOUND_NEEDS_MARKETDATA_PERMISSION"],
                "documented_seed_ff_attempted": True,
                "ff_underlier_found": True,
                "forecastx_underlier_candidates": 1,
                "forecastx_tradable_contract_candidates": 2,
                "forecastx_marketdata_rows": 0,
                "candidate_count": 3,
                "forecastx_candidate_count": 3,
                "normalized_possible_count": 2,
            },
        },
    )

    report = _report(reports)

    assert report["summary"]["ibkr_forecastex_discovery_candidates"]["forecastx_tradable_contract_candidates"] == 2
    assert report["highest_priority_next_action"]["action"] == "FORECASTX_CONTRACT_INFO_FOUND_NEEDS_MARKETDATA_PERMISSION"


def test_ibkr_forecastex_quote_diagnostics_surface_in_ops_status(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "ibkr_forecastex_discovery_candidates.json",
        {
            "source": "ibkr_forecastex_discovery_candidates_v1",
            "summary": {
                "discovery_status": "FORECASTX_CANDIDATES_FOUND",
                "forecastx_tradable_contract_candidates": 2,
                "forecastx_marketdata_rows": 2,
                "final_tradable_rows": 2,
            },
        },
    )
    _write(
        reports / "ibkr_forecastex_quote_diagnostics.json",
        {
            "source": "ibkr_forecastex_quote_diagnostics_v1",
            "schema_kind": "ibkr_forecastex_quote_diagnostics_v1",
            "summary": {
                "final_contract_rows": 2,
                "marketdata_rows": 2,
                "quote_rows_mapped_to_contracts": 2,
                "rows_with_bid": 1,
                "rows_with_ask": 1,
                "rows_with_bid_ask": 0,
                "rows_with_bid_ask_size": 0,
                "rows_with_timestamp": 2,
                "rows_quote_diagnostic_complete": 0,
                "rows_execution_ready": 0,
                "top_quote_blockers": [
                    {"blocker": "ibkr_forecastex_missing_bid", "count": 1},
                    {"blocker": "ibkr_forecastex_not_execution_ready", "count": 2},
                ],
                "blockers_by_count": {
                    "ibkr_forecastex_missing_bid": 1,
                    "ibkr_forecastex_not_execution_ready": 2,
                },
            },
        },
    )

    report = _report(reports)
    markdown = render_relative_value_ops_status_markdown(report)
    quote = report["summary"]["ibkr_forecastex_quote_diagnostics"]

    assert quote["final_contract_rows"] == 2
    assert quote["quote_rows_mapped_to_contracts"] == 2
    assert quote["rows_with_bid"] == 1
    assert quote["rows_with_ask"] == 1
    assert quote["rows_execution_ready"] == 0
    assert "quote_rows_mapped_to_contracts: `2`" in markdown
    assert "quote_rows_execution_ready: `0`" in markdown
    assert "ibkr_forecastex_missing_bid:1" in markdown
    assert "exchange_venue, not tab/source platform alone, determines independence" in markdown


def test_ibkr_forecastex_partial_quote_diagnostics_drive_next_action(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "ibkr_forecastex_access_doctor.json",
        {
            "schema_kind": "ibkr_forecastex_access_doctor_v1",
            "status": "OK",
            "reachable": True,
            "authenticated": True,
            "blockers": ["account_permission_review_required"],
        },
    )
    _write(
        reports / "ibkr_forecastex_discovery_candidates.json",
        {
            "source": "ibkr_forecastex_discovery_candidates_v1",
            "summary": {
                "discovery_status": "FORECASTX_CANDIDATES_FOUND",
                "forecastx_tradable_contract_candidates": 4,
                "forecastx_marketdata_rows": 4,
                "final_tradable_rows": 4,
            },
        },
    )
    _write(
        reports / "ibkr_forecastex_quote_diagnostics.json",
        {
            "source": "ibkr_forecastex_quote_diagnostics_v1",
            "schema_kind": "ibkr_forecastex_quote_diagnostics_v1",
            "summary": {
                "final_contract_rows": 4,
                "marketdata_rows": 4,
                "quote_rows_mapped_to_contracts": 4,
                "rows_with_bid": 3,
                "rows_with_ask": 3,
                "rows_with_bid_ask": 2,
                "rows_with_bid_ask_size": 2,
                "rows_with_timestamp": 4,
                "rows_quote_diagnostic_complete": 2,
                "rows_execution_ready": 0,
                "top_quote_blockers": [
                    {"blocker": "ibkr_forecastex_incomplete_top_of_book", "count": 2},
                    {"blocker": "ibkr_forecastex_missing_bid", "count": 1},
                    {"blocker": "ibkr_forecastex_missing_ask", "count": 1},
                    {"blocker": "ibkr_forecastex_not_execution_ready", "count": 4},
                ],
                "blockers_by_count": {
                    "ibkr_forecastex_incomplete_top_of_book": 2,
                    "ibkr_forecastex_missing_bid": 1,
                    "ibkr_forecastex_missing_ask": 1,
                    "ibkr_forecastex_not_execution_ready": 4,
                },
            },
        },
    )

    report = _report(reports)
    action = report["highest_priority_next_action"]

    assert action["action"] == "FORECASTX_QUOTE_PARTIAL_REVIEW_PERMISSIONS_OR_DEPTH"
    assert action["final_contract_rows"] == 4
    assert action["rows_quote_diagnostic_complete"] == 2
    assert action["incomplete_quote_rows"] == 2
    assert action["rows_execution_ready"] == 0
    assert "Review ForecastEx market data permissions" in action["reason"]
    assert "ibkr_forecastex_incomplete_top_of_book:2" in action["reason"]


def test_ibkr_forecastex_consistency_warnings_surface_in_ops_status(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "ibkr_forecastex_discovery_candidates.json",
        {
            "source": "ibkr_forecastex_discovery_candidates_v1",
            "summary": {
                "discovery_status": "FORECASTX_CANDIDATES_FOUND",
                "forecastx_tradable_contract_candidates": 4,
                "forecastx_marketdata_rows": 4,
                "final_tradable_rows": 4,
            },
        },
    )
    _write(
        reports / "ibkr_forecastex_quote_diagnostics.json",
        {
            "source": "ibkr_forecastex_quote_diagnostics_v1",
            "schema_kind": "ibkr_forecastex_quote_diagnostics_v1",
            "summary": {
                "final_contract_rows": 3,
                "marketdata_rows": 2,
                "quote_rows_mapped_to_contracts": 1,
                "rows_quote_diagnostic_complete": 0,
                "rows_execution_ready": 0,
            },
        },
    )

    report = _report(reports)
    warnings = report["summary"]["ibkr_forecastex_consistency_warnings"]
    markdown = render_relative_value_ops_status_markdown(report)

    assert any(warning.startswith("ibkr_final_tradable_rows_mismatch") for warning in warnings)
    assert any(warning.startswith("ibkr_quote_rows_mapped_marketdata_rows_mismatch") for warning in warnings)
    assert "consistency_warnings:" in markdown


def test_ibkr_forecastex_auth_expired_drives_reauth_next_action(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "ibkr_forecastex_access_doctor.json",
        {
            "schema_kind": "ibkr_forecastex_access_doctor_v1",
            "status": "LOCAL_GATEWAY_REACHABLE_SESSION_NOT_AUTHENTICATED",
            "reachable": True,
            "authenticated": False,
            "blockers": ["ibkr_local_authenticated_session_required"],
        },
    )
    _write(
        reports / "ibkr_forecastex_discovery_candidates.json",
        {
            "source": "ibkr_forecastex_discovery_candidates_v1",
            "summary": {
                "discovery_status": "ACCOUNT_PERMISSION_REVIEW_REQUIRED",
                "discovery_statuses": ["ACCOUNT_PERMISSION_REVIEW_REQUIRED"],
                "candidate_count": 0,
            },
        },
    )

    report = _report(reports)

    assert report["highest_priority_next_action"]["action"] == "LOCAL_GATEWAY_REAUTH_REQUIRED"


def test_ibkr_forecastex_raw_shape_summary_exhaustion_drives_seed_conid_next_action(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "ibkr_forecastex_access_doctor.json",
        {
            "schema_kind": "ibkr_forecastex_access_doctor_v1",
            "status": "OK",
            "reachable": True,
            "authenticated": True,
            "blockers": ["account_permission_review_required"],
        },
    )
    _write(
        reports / "ibkr_forecastex_discovery_candidates.json",
        {
            "source": "ibkr_forecastex_discovery_candidates_v1",
            "summary": {
                "discovery_status": "FORECASTX_UNDERLIER_FOUND_NEEDS_STRIKES_OR_CONTRACT_INFO",
                "discovery_statuses": ["FORECASTX_UNDERLIER_FOUND_NEEDS_STRIKES_OR_CONTRACT_INFO"],
                "documented_seed_ff_attempted": True,
                "ff_underlier_found": True,
                "forecastx_underlier_candidates": 57,
                "forecastx_tradable_contract_candidates": 0,
                "forecastx_marketdata_rows": 0,
                "candidate_count": 852,
                "forecastx_candidate_count": 57,
                "normalized_possible_count": 0,
            },
        },
    )
    _write(
        reports / "ibkr_forecastex_raw_shape_summary.json",
        {
            "schema_kind": "ibkr_forecastex_raw_shape_summary_v1",
            "generated_at": "2026-05-26T10:43:27+00:00",
            "snapshot_dir": "reports/manual_snapshots/ibkr_forecastex/20260526T104327Z",
            "summary": {
                "raw_files_read": 29,
                "forecastx_identifier_files": 29,
                "final_tradable_contract_field_files": 0,
                "call_put_right_files": 0,
                "expiry_or_month_files": 1,
                "strike_field_files": 19,
                "event_contract_field_files": 1,
                "binary_yes_no_files": 0,
                "final_tradable_contract_fields_present": False,
                "final_tradable_contract_blockers": [
                    "final_tradable_forecastex_contract_not_found",
                    "missing_call_put_right",
                    "missing_expiry_or_month",
                    "missing_strike_or_event_threshold",
                    "underlier_only_no_tradable_contract_fields",
                ],
                "endpoint_counts": {
                    "/iserver/secdef/info": 19,
                    "/iserver/secdef/search": 10,
                },
                "blockers_by_count": {
                    "final_tradable_forecastex_contract_not_found": 29,
                    "missing_call_put_right": 29,
                },
            },
        },
    )

    report = _report(reports)
    markdown = render_relative_value_ops_status_markdown(report)

    summary = report["summary"]["ibkr_forecastex_raw_shape_summary"]
    assert summary["present"] is True
    assert summary["raw_files_read"] == 29
    assert summary["forecastx_identifier_files"] == 29
    assert summary["final_tradable_contract_field_files"] == 0
    assert summary["call_put_right_files"] == 0
    assert summary["final_tradable_contract_fields_present"] is False
    assert summary["read_only_secdef_paths_exhausted"] is True

    next_action = report["highest_priority_next_action"]
    assert next_action["action"] == "FORECASTX_READ_ONLY_SECDEF_FINAL_CONTRACT_FIELDS_EXHAUSTED"
    assert next_action["report_only"] is True
    assert next_action["raw_files_read"] == 29
    assert next_action["call_put_right_files"] == 0
    assert next_action["forecastx_identifier_files"] == 29
    assert "29 read-only secdef responses" in next_action["reason"]

    assert "raw_shape_summary_present: `true`" in markdown
    assert "raw_shape_read_only_secdef_paths_exhausted: `true`" in markdown
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_ibkr_forecastex_raw_shape_summary_below_threshold_keeps_underlier_next_action(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "ibkr_forecastex_access_doctor.json",
        {
            "schema_kind": "ibkr_forecastex_access_doctor_v1",
            "status": "OK",
            "reachable": True,
            "authenticated": True,
            "blockers": ["account_permission_review_required"],
        },
    )
    _write(
        reports / "ibkr_forecastex_discovery_candidates.json",
        {
            "source": "ibkr_forecastex_discovery_candidates_v1",
            "summary": {
                "discovery_status": "FORECASTX_UNDERLIER_FOUND_NEEDS_STRIKES_OR_CONTRACT_INFO",
                "discovery_statuses": ["FORECASTX_UNDERLIER_FOUND_NEEDS_STRIKES_OR_CONTRACT_INFO"],
                "documented_seed_ff_attempted": True,
                "ff_underlier_found": True,
                "forecastx_underlier_candidates": 1,
                "forecastx_tradable_contract_candidates": 0,
                "forecastx_marketdata_rows": 0,
                "candidate_count": 1,
                "forecastx_candidate_count": 1,
                "normalized_possible_count": 0,
            },
        },
    )
    # Only 3 raw files: below the conservative exhaustion threshold.
    _write(
        reports / "ibkr_forecastex_raw_shape_summary.json",
        {
            "schema_kind": "ibkr_forecastex_raw_shape_summary_v1",
            "summary": {
                "raw_files_read": 3,
                "forecastx_identifier_files": 3,
                "final_tradable_contract_field_files": 0,
                "call_put_right_files": 0,
                "expiry_or_month_files": 0,
                "strike_field_files": 0,
                "event_contract_field_files": 0,
                "binary_yes_no_files": 0,
                "final_tradable_contract_fields_present": False,
                "endpoint_counts": {"/iserver/secdef/search": 3},
                "blockers_by_count": {},
            },
        },
    )

    report = _report(reports)

    summary = report["summary"]["ibkr_forecastex_raw_shape_summary"]
    assert summary["present"] is True
    assert summary["raw_files_read"] == 3
    # Below threshold => not yet conclusive => no exhaustion claim.
    assert summary["read_only_secdef_paths_exhausted"] is False
    assert (
        report["highest_priority_next_action"]["action"]
        == "FORECASTX_UNDERLIER_FOUND_NEEDS_STRIKES_OR_CONTRACT_INFO"
    )


def test_kxmlb_event_evidence_summary_is_surfaced_when_present(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "kalshi_kxmlb26_event_evidence_summary.json",
        {
            "schema_version": 1,
            "source": "kalshi_event_evidence_summary_v1",
            "summary": {
                "event_ticker": "KXMLB-26",
                "market_count": 30,
                "explicit_outcome_list_exists": False,
                "explicit_completeness_evidence_exists": False,
                "settlement_rules_source_evidence_exists": False,
                "fresh_orderbook_depth_exists": False,
                "local_manifest_v1_would_pass_if_reviewer_fields_added": False,
                "ready_for_human_manifest_review": False,
                "top_blockers": [
                    {"blocker": "explicit_event_level_outcome_list", "count": 1},
                    {"blocker": "fresh_orderbook_depth", "count": 1},
                ],
            },
        },
    )

    report = _report(reports)
    markdown = render_relative_value_ops_status_markdown(report)
    kxmlb = report["summary"]["kalshi_kxmlb26_event_evidence_summary"]

    assert kxmlb["present"] is True
    assert kxmlb["market_count"] == 30
    assert kxmlb["ready_for_human_manifest_review"] is False
    assert "explicit_event_level_outcome_list" in kxmlb["top_blockers"]
    assert "### kalshi_kxmlb26_event_evidence" in markdown
    assert "ready_for_human_manifest_review: `false`" in markdown


def test_core_trio_peer_coverage_surfaces_in_ops_status(tmp_path) -> None:
    reports = _write_core_reports(tmp_path)
    _write(
        reports / "core_trio_peer_coverage_audit.json",
        {
            "source": "core_trio_peer_coverage_audit_v1",
            "report_path": str(reports / "core_trio_peer_coverage_audit.json"),
            "summary": {
                "total_core_trio_rows": 42,
                "peer_coverage_families": 3,
                "families_with_kalshi_peer_rows": 1,
                "families_without_kalshi_peer_rows": 2,
                "families_with_kalshi_peer_row_names": ["crypto_price_threshold"],
                "families_without_kalshi_peer_row_names": ["company_metric", "weather"],
                "strongest_overlap_family": "crypto_price_threshold",
                "top_10_next_fetch_targets": [
                    {
                        "family": "company_metric",
                        "priority_score": 20.0,
                        "reason": "missing saved Kalshi peer family",
                        "blockers": ["no_saved_kalshi_peer_family"],
                    }
                ],
                "top_10_closest_existing_overlaps": [
                    {
                        "lane": "polymarket_vs_kalshi",
                        "family": "crypto_price_threshold",
                        "overlap_score": 80.0,
                        "typed_key": "crypto_price_threshold|BTC|100000|above|2026-12-31",
                        "blockers": ["title_only_match_not_equivalence"],
                    }
                ],
                "top_blockers": [{"blocker": "no_saved_kalshi_peer_family", "count": 2}],
                "exact_ready_rows": 0,
                "paper_candidate_rows": 0,
            },
        },
    )

    report = _report(reports)
    status = report["summary"]["core_trio_peer_coverage"]
    markdown = render_relative_value_ops_status_markdown(report)

    assert status["present"] is True
    assert status["strongest_overlap_family"] == "crypto_price_threshold"
    assert status["next_recommended_lane"] == "company_metric"
    assert status["exact_ready_rows"] == 0
    assert status["paper_candidate_rows"] == 0
    assert "### core_trio_peer_coverage" in markdown
    assert "company_metric" in markdown
