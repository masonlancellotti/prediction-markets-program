from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

import scan
from relative_value.cross_platform_opportunity_triage import (
    BTC_BASIS_RISK_REVIEW,
    CRYPTO_RELATED_FV_WATCH,
    EXACT_EQUALITY_CANDIDATE,
    GRAPH_ADVISORY_CANDIDATE,
    SIMILARITY_ONLY_RESEARCH,
    build_cross_platform_opportunity_triage_report,
    write_cross_platform_opportunity_triage_files,
)


def test_similarity_only_row_cannot_become_exact_candidate(tmp_path) -> None:
    input_dir = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "similarity.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "venue_a": "VenueAlpha",
                        "market_id_a": "alpha-1",
                        "venue_b": "VenueBeta",
                        "market_id_b": "beta-1",
                        "relationship_class": "EXACT_EQUALITY_CANDIDATE",
                        "similarity_score": 0.99,
                        "evidence_summary": "titles are nearly identical",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_cross_platform_opportunity_triage_report(
        input_dir=input_dir,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["rows"][0]
    assert row["relationship_class"] == SIMILARITY_ONLY_RESEARCH
    assert row["diagnostic_only"] is True
    assert row["paper_candidate_emitted"] is False
    assert "text_similarity_not_exact_payoff" in row["blockers"]
    assert "exactness_request_downgraded" in row["blockers"]


def test_graph_hints_remain_advisory_and_do_not_promote_exact(tmp_path) -> None:
    input_dir = tmp_path / "reports"
    input_dir.mkdir()
    graph_path = tmp_path / "graph_hints.json"
    graph_path.write_text(
        json.dumps(
            {
                "diagnostic_only": True,
                "hints": [
                    {
                        "finding_id": "graph-1",
                        "source_venue": "GraphVenueA",
                        "source_market_id": "a",
                        "target_venue": "GraphVenueB",
                        "target_market_id": "b",
                        "relation_type": "EXACT_SAME_PAYOFF",
                        "relationship_class": "EXACT_EQUALITY_CANDIDATE",
                        "info_only_hint": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_cross_platform_opportunity_triage_report(
        input_dir=input_dir,
        graph_hints_path=graph_path,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["rows"][0]
    assert row["relationship_class"] == GRAPH_ADVISORY_CANDIDATE
    assert row["allowed_next_action"] == "WATCH"
    assert "graph_advisory_only" in row["blockers"]
    assert "graph_exact_label_not_trusted" in row["blockers"]
    assert row["affects_evaluator_gates"] is False


def test_exact_candidate_requires_explicit_stronger_evidence(tmp_path) -> None:
    input_dir = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "exact.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "venue_a": "VenueOne",
                        "ticker_a": "ONE-YES",
                        "venue_b": "VenueTwo",
                        "ticker_b": "TWO-YES",
                        "relationship_class": "EXACT_EQUALITY_CANDIDATE",
                        "contract_relationship": {
                            "relationship": "EQUIVALENT",
                            "same_payoff": True,
                            "source": "same_payoff_board_v1",
                        },
                    },
                    {
                        "venue_a": "VenueOne",
                        "ticker_a": "ONE-TEXT",
                        "venue_b": "VenueTwo",
                        "ticker_b": "TWO-TEXT",
                        "relationship_class": "EXACT_EQUALITY_CANDIDATE",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_cross_platform_opportunity_triage_report(
        input_dir=input_dir,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    exact = next(row for row in report["rows"] if row["ticker_a"] == "ONE-YES")
    blocked = next(row for row in report["rows"] if row["ticker_a"] == "ONE-TEXT")
    assert exact["relationship_class"] == EXACT_EQUALITY_CANDIDATE
    assert "requires_existing_evaluator_gates_before_paper" in exact["blockers"]
    assert "explicit_exact_evidence:same_payoff_board_v1" in exact["reason_codes"]
    assert blocked["relationship_class"] == EXACT_EQUALITY_CANDIDATE
    assert "missing_explicit_typed_evidence" in blocked["blockers"]
    assert blocked["paper_candidate_emitted"] is False


def test_ibkr_kalshi_route_is_blocked_as_fake_cross_venue_edge(tmp_path) -> None:
    input_dir = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "ibkr_kalshi.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "a": {
                            "venue": "kalshi",
                            "market_id": "KXTEST",
                            "exchange_venue": "KALSHI",
                            "executable_venue": "KALSHI",
                        },
                        "b": {
                            "venue": "IBKR_KALSHI",
                            "market_id": "IBKR-KXTEST",
                            "source_platform": "IBKR",
                            "access_platform": "IBKR",
                            "exchange_venue": "KALSHI",
                            "executable_venue": "KALSHI",
                        },
                        "relationship_class": EXACT_EQUALITY_CANDIDATE,
                        "contract_relationship": {
                            "relationship": "EQUIVALENT",
                            "same_payoff": True,
                            "source": "same_payoff_board_v1",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_cross_platform_opportunity_triage_report(
        input_dir=input_dir,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["rows"][0]
    assert row["executable_venue_a"] == "KALSHI"
    assert row["executable_venue_b"] == "KALSHI"
    assert row["access_platform_b"] == "IBKR"
    assert row["allowed_next_action"] == "WATCH"
    assert "ibkr_kalshi_is_same_exchange_as_direct_kalshi" in row["blockers"]
    assert "broker_route_not_independent_venue" in row["blockers"]
    assert "do_not_cross_compare_as_independent_arb" in row["blockers"]
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_arbitrary_venue_names_are_preserved_and_csv_is_written(tmp_path) -> None:
    input_dir = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "venues.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "venue_a": "ForecastExchangeResearch",
                        "market_id_a": "fx-123",
                        "venue_b": "AggregatorReference",
                        "market_id_b": "agg-456",
                        "relationship_class": "STALE_OR_LAG_CANDIDATE",
                        "reason_codes": ["quote_age_gap_observed"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    json_output = tmp_path / "out" / "triage.json"
    csv_output = tmp_path / "out" / "triage.csv"

    report = write_cross_platform_opportunity_triage_files(
        input_dir=input_dir,
        json_output=json_output,
        csv_output=csv_output,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["rows"][0]
    assert row["venue_a"] == "ForecastExchangeResearch"
    assert row["venue_b"] == "AggregatorReference"
    with csv_output.open(encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert csv_rows[0]["venue_a"] == "ForecastExchangeResearch"
    assert json_output.exists()


def test_btc_basis_risk_review_is_preserved_as_manual_review_tier(tmp_path) -> None:
    input_dir = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "basis.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "venue_a": "kalshi",
                        "ticker_a": "KXBTC-26MAY2517-T86249.99",
                        "venue_b": "polymarket",
                        "ticker_b": "BTC-26MAY2517-T86249.99",
                        "relationship_class": BTC_BASIS_RISK_REVIEW,
                        "basis_risk_reason": "different known BTC settlement sources",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_cross_platform_opportunity_triage_report(
        input_dir=input_dir,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["rows"][0]
    assert row["relationship_class"] == BTC_BASIS_RISK_REVIEW
    assert row["allowed_next_action"] == "MANUAL_BASIS_RISK_REVIEW"
    assert row["affects_evaluator_gates"] is False
    assert row["paper_candidate_emitted"] is False


def test_crypto_related_fv_watch_is_preserved_as_fair_value_watch(tmp_path) -> None:
    input_dir = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "fv_watch.json").write_text(
        json.dumps(
            {
                "crypto_related_fv_watch_rows": [
                    {
                        "market_a": {
                            "venue": "kalshi",
                            "market_id": "KXBTC-26MAY2517-T100000",
                            "ticker": "KXBTC-26MAY2517-T100000",
                        },
                        "market_b": {
                            "venue": "polymarket",
                            "market_id": "poly-monthly-high-100000",
                            "ticker": "poly-monthly-high-100000",
                        },
                        "relationship_class": CRYPTO_RELATED_FV_WATCH,
                        "not_exact_payoff_reason": "monthly_extreme_vs_point_in_time",
                        "blockers": [
                            "monthly_extreme_window_not_point_in_time",
                            "not_same_payoff",
                            "not_evaluator_eligible",
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_cross_platform_opportunity_triage_report(
        input_dir=input_dir,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    row = report["rows"][0]
    assert row["relationship_class"] == CRYPTO_RELATED_FV_WATCH
    assert row["allowed_next_action"] == "FAIR_VALUE_WATCH"
    assert row["affects_evaluator_gates"] is False
    assert row["paper_candidate_emitted"] is False
    assert "monthly_extreme_window_not_point_in_time" in row["blockers"]
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_same_pair_in_two_files_is_deduplicated_with_merged_source_files(tmp_path) -> None:
    input_dir = tmp_path / "reports"
    input_dir.mkdir()
    base_row = {
        "venue_a": "VenueA",
        "market_id_a": "a-1",
        "venue_b": "VenueB",
        "market_id_b": "b-1",
        "relationship_class": "EXACT_EQUALITY_CANDIDATE",
        "contract_relationship": {
            "relationship": "EQUIVALENT",
            "same_payoff": True,
            "source": "same_payoff_board_v1",
        },
    }
    (input_dir / "first.json").write_text(json.dumps({"rows": [base_row]}), encoding="utf-8")
    (input_dir / "second.json").write_text(json.dumps({"rows": [base_row]}), encoding="utf-8")

    report = build_cross_platform_opportunity_triage_report(
        input_dir=input_dir,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    exact_rows = [row for row in report["rows"] if row["relationship_class"] == EXACT_EQUALITY_CANDIDATE]
    assert len(exact_rows) == 1
    assert sorted(exact_rows[0]["source_files"]) == sorted(
        [str(input_dir / "first.json"), str(input_dir / "second.json")]
    )


def test_dedup_keeps_highest_priority_class_when_pair_seen_with_weaker_evidence(tmp_path) -> None:
    input_dir = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "weak.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "venue_a": "VenueA",
                        "market_id_a": "a-2",
                        "venue_b": "VenueB",
                        "market_id_b": "b-2",
                        "relationship_class": "EXACT_EQUALITY_CANDIDATE",
                        "similarity_score": 0.99,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (input_dir / "strong.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "venue_a": "VenueA",
                        "market_id_a": "a-2",
                        "venue_b": "VenueB",
                        "market_id_b": "b-2",
                        "relationship_class": "EXACT_EQUALITY_CANDIDATE",
                        "contract_relationship": {
                            "relationship": "EQUIVALENT",
                            "same_payoff": True,
                            "source": "same_payoff_board_v1",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = build_cross_platform_opportunity_triage_report(
        input_dir=input_dir,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert len(report["rows"]) == 1
    survivor = report["rows"][0]
    assert survivor["relationship_class"] == EXACT_EQUALITY_CANDIDATE
    # The trusted-evidence row must win over the similarity-only row.
    assert "explicit_exact_evidence:same_payoff_board_v1" in survivor["reason_codes"]
    assert "missing_explicit_typed_evidence" not in survivor["blockers"]


def test_missing_optional_graph_hints_path_fails_closed_without_crashing(tmp_path) -> None:
    input_dir = tmp_path / "reports"
    input_dir.mkdir()

    report = build_cross_platform_opportunity_triage_report(
        input_dir=input_dir,
        graph_hints_path=tmp_path / "missing_graph_hints.json",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert report["rows"] == []
    assert report["summary"]["warning_reason_codes"] == {"graph_hints_file_missing": 1}
    assert report["safety"]["graph_hints_can_create_exact_candidate"] is False


def test_triage_cross_platform_opportunities_cli_writes_reports(tmp_path, capsys) -> None:
    input_dir = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "rows.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "venue_a": "VenueA",
                        "market_id_a": "a",
                        "venue_b": "VenueB",
                        "market_id_b": "b",
                        "relationship_class": "SUBSET_SUPERSET_CANDIDATE",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    json_output = tmp_path / "triage.json"
    csv_output = tmp_path / "triage.csv"
    markdown_output = tmp_path / "triage.md"

    result = scan.main(
        [
            "triage-cross-platform-opportunities",
            "--input-dir",
            str(input_dir),
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
    assert "cross_platform_opportunity_triage_status=OK" in stdout
    assert "paper_candidates=0" in stdout
    assert json_output.exists()
    assert csv_output.exists()
    assert markdown_output.exists()
