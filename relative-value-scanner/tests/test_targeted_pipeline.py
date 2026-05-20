import json
from pathlib import Path

import scan


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_run_targeted_pipeline_cli_uses_saved_file_steps_without_network(monkeypatch, tmp_path: Path, capsys) -> None:
    calls = []

    def fake_fetch_polymarket(limit, output, timeout_seconds=10.0, **kwargs):
        calls.append(("fetch_polymarket", output.name, kwargs))
        assert limit == 50
        assert kwargs["tag_slug"] == "nba"
        _write(
            output,
            {
                "schema_version": 1,
                "normalized_count": 3,
                "normalized_markets": [],
            },
        )
        return 0

    def fake_fetch_kalshi(limit, output, timeout_seconds=10.0, **kwargs):
        calls.append(("fetch_kalshi", output.name, kwargs))
        assert limit == 50
        assert kwargs["series_ticker"] == "KXNBA"
        assert kwargs["max_pages"] == 2
        _write(
            output,
            {
                "schema_version": 1,
                "normalized_count": 2,
                "normalized_markets": [],
            },
        )
        return 0

    def fake_enrich_orderbooks(snapshot, venue, output, timeout_seconds=10.0, max_snapshot_age_hours=24.0):
        calls.append((f"enrich_{venue}", output.name, {"snapshot": snapshot.name}))
        market_count = 3 if venue == "polymarket" else 2
        enriched_count = 2 if venue == "polymarket" else 2
        _write(
            output,
            {
                "schema_version": 1,
                "normalized_markets": [],
                "orderbook_enrichment": {
                    "market_count": market_count,
                    "enriched_count": enriched_count,
                    "unenriched_count": market_count - enriched_count,
                },
            },
        )
        return 0

    def fake_match_live_snapshots(polymarket, kalshi, output, **kwargs):
        calls.append(("match_live_snapshots", output.name, {"polymarket": polymarket.name, "kalshi": kalshi.name}))
        _write(
            output,
            {
                "schema_version": 1,
                "pair_count": 2,
                "pairs": [],
            },
        )
        return 0

    def fake_evaluate_paper_candidates(pairs, polymarket_enriched, kalshi_enriched, output, **kwargs):
        calls.append(("evaluate_paper_candidates", output.name, {"pairs": pairs.name}))
        assert kwargs["max_quote_age_seconds"] == 1234.0
        assert kwargs["max_settlement_delta_seconds"] == 43200.0
        assert kwargs["min_top_of_book_size"] == 5.0
        assert kwargs["min_net_gap"] == 0.02
        assert kwargs["accept_unit_mismatch"] is True
        _write(
            output,
            {
                "schema_version": 1,
                "ledger_count": 2,
                "counts_by_action": {
                    "WATCH": 1,
                    "MANUAL_REVIEW": 1,
                    "PAPER_CANDIDATE": 0,
                },
                "ledger": [
                    {
                        "action": "WATCH",
                        "gap": {"gross_gap": -0.01, "estimated_net_gap": 0.01, "settlement_delta_seconds": 45000.0},
                        "ineligibility_reasons": ["settlement_delta_exceeds_limit"],
                        "missed_fill_reason": "settlement_delta_exceeds_limit",
                    },
                    {
                        "action": "MANUAL_REVIEW",
                        "gap": {"gross_gap": 0.003, "estimated_net_gap": None},
                        "ineligibility_reasons": ["unit_mismatch_not_accepted"],
                        "missed_fill_reason": "unit_mismatch_not_accepted",
                    },
                    {
                        "action": "WATCH",
                        "gap": {"gross_gap": 0.007, "estimated_net_gap": -0.002},
                        "ineligibility_reasons": [],
                        "missed_fill_reason": None,
                    },
                    {
                        "action": "WATCH",
                        "gap": {"gross_gap": 0.015, "estimated_net_gap": 0.004},
                        "ineligibility_reasons": [],
                        "missed_fill_reason": None,
                    },
                    {
                        "action": "WATCH",
                        "gap": {"gross_gap": 0.025, "estimated_net_gap": 0.019},
                        "ineligibility_reasons": ["estimated_net_gap_below_minimum"],
                        "missed_fill_reason": "estimated_net_gap_below_minimum",
                    },
                ],
            },
        )
        return 0

    monkeypatch.setattr(scan, "fetch_polymarket", fake_fetch_polymarket)
    monkeypatch.setattr(scan, "fetch_kalshi", fake_fetch_kalshi)
    monkeypatch.setattr(scan, "enrich_orderbooks", fake_enrich_orderbooks)
    monkeypatch.setattr(scan, "match_live_snapshots", fake_match_live_snapshots)
    monkeypatch.setattr(scan, "evaluate_paper_candidates", fake_evaluate_paper_candidates)

    result = scan.main(
        [
            "run-targeted-pipeline",
            "--polymarket-tag-slug",
            "nba",
            "--kalshi-series-ticker",
            "KXNBA",
            "--label",
            "nba_kxnba",
            "--output-dir",
            str(tmp_path),
            "--max-quote-age-seconds",
            "1234",
            "--max-settlement-delta-seconds",
            "43200",
            "--min-top-of-book-size",
            "5",
            "--min-net-gap",
            "0.02",
            "--accept-unit-mismatch",
        ]
    )

    assert result == 0
    assert [call[0] for call in calls] == [
        "fetch_polymarket",
        "fetch_kalshi",
        "enrich_polymarket",
        "enrich_kalshi",
        "match_live_snapshots",
        "evaluate_paper_candidates",
    ]
    expected_files = {
        "nba_kxnba_polymarket_snapshot.json",
        "nba_kxnba_kalshi_snapshot.json",
        "nba_kxnba_polymarket_enriched.json",
        "nba_kxnba_kalshi_enriched.json",
        "nba_kxnba_pairs.json",
        "nba_kxnba_paper_candidates.json",
        "nba_kxnba_pipeline_summary.json",
    }
    assert expected_files <= {path.name for path in tmp_path.iterdir()}

    summary = json.loads((tmp_path / "nba_kxnba_pipeline_summary.json").read_text(encoding="utf-8"))
    assert summary["summary"]["polymarket_normalized_count"] == 3
    assert summary["summary"]["kalshi_normalized_count"] == 2
    assert summary["summary"]["polymarket_enriched_count"] == 2
    assert summary["summary"]["kalshi_enriched_count"] == 2
    assert summary["summary"]["pair_count"] == 2
    assert summary["summary"]["evaluator_counts"] == {
        "WATCH": 1,
        "MANUAL_REVIEW": 1,
        "PAPER_CANDIDATE": 0,
    }
    assert {
        "reason": "missed_fill:settlement_delta_exceeds_limit",
        "count": 1,
    } in summary["summary"]["top_rejection_reasons"]
    assert summary["summary"]["gap_distribution"] == {
        "gross_gap_lte_0_count": 1,
        "gross_gap_gt_0_lte_0_005_count": 1,
        "gross_gap_gt_0_005_lte_0_01_count": 1,
        "gross_gap_gt_0_01_lte_0_02_count": 1,
        "gross_gap_gt_0_02_count": 1,
        "estimated_net_gap_gt_0_count": 3,
        "estimated_net_gap_lte_0_count": 1,
    }
    assert summary["summary"]["near_miss_summary"] == {
        "net_gap": {
            "count": 1,
            "min_distance": 0.001,
            "max_distance": 0.001,
            "median_distance": 0.001,
        },
        "settlement_delta": {
            "count": 1,
            "min_distance": 1800.0,
            "max_distance": 1800.0,
            "median_distance": 1800.0,
        },
    }
    assert "PAPER" not in json.dumps(summary["summary"]["evaluator_counts"]).replace("PAPER_CANDIDATE", "")

    output_text = capsys.readouterr().out
    assert "targeted_pipeline_status=OK label=nba_kxnba" in output_text
    assert "polymarket_normalized=3 kalshi_normalized=2" in output_text
    assert "pairs=2 watch=1 manual_review=1 paper_candidate=0" in output_text
    assert "later_markout_command=python scan.py replay-paper-candidate-markouts" in output_text


def test_run_targeted_pipeline_rejects_unsafe_label(capsys) -> None:
    result = scan.main(
        [
            "run-targeted-pipeline",
            "--polymarket-tag-slug",
            "nba",
            "--kalshi-series-ticker",
            "KXNBA",
            "--label",
            "..\\outside",
        ]
    )

    assert result == 1
    assert "targeted_pipeline_status=FAILED message=label may contain only" in capsys.readouterr().out


def test_gap_distribution_boundaries_and_nan_handling() -> None:
    distribution = scan._gap_distribution(
        {
            "ledger": [
                {"gap": {"gross_gap": 0.0, "estimated_net_gap": None}},
                {"gap": {"gross_gap": 0.005, "estimated_net_gap": None}},
                {"gap": {"gross_gap": 0.01, "estimated_net_gap": None}},
                {"gap": {"gross_gap": 0.02, "estimated_net_gap": None}},
                {"gap": {"gross_gap": float("nan"), "estimated_net_gap": None}},
            ]
        }
    )

    assert distribution["gross_gap_lte_0_count"] == 1
    assert distribution["gross_gap_gt_0_lte_0_005_count"] == 1
    assert distribution["gross_gap_gt_0_005_lte_0_01_count"] == 1
    assert distribution["gross_gap_gt_0_01_lte_0_02_count"] == 1
    assert distribution["gross_gap_gt_0_02_count"] == 0
    assert distribution["estimated_net_gap_gt_0_count"] == 0
    assert distribution["estimated_net_gap_lte_0_count"] == 0


def test_near_miss_summary_counts_only_watch_net_gap_near_misses() -> None:
    summary = scan._near_miss_summary(
        {
            "ledger": [
                {
                    "action": "WATCH",
                    "missed_fill_reason": "estimated_net_gap_below_minimum",
                    "gap": {"estimated_net_gap": 0.019},
                },
                {
                    "action": "WATCH",
                    "missed_fill_reason": "estimated_net_gap_below_minimum",
                    "gap": {"estimated_net_gap": None},
                },
                {
                    "action": "MANUAL_REVIEW",
                    "missed_fill_reason": "estimated_net_gap_below_minimum",
                    "gap": {"estimated_net_gap": 0.018},
                },
                {
                    "action": "WATCH",
                    "missed_fill_reason": "no_positive_bid_ask_gap",
                    "gap": {"estimated_net_gap": 0.017},
                },
            ]
        },
        min_net_gap=0.02,
    )

    assert summary["net_gap"] == {
        "count": 1,
        "min_distance": 0.001,
        "max_distance": 0.001,
        "median_distance": 0.001,
    }
    assert summary["settlement_delta"] == {
        "count": 0,
        "min_distance": None,
        "max_distance": None,
        "median_distance": None,
    }


def test_near_miss_summary_counts_only_watch_settlement_delta_near_misses() -> None:
    summary = scan._near_miss_summary(
        {
            "ledger": [
                {
                    "action": "WATCH",
                    "missed_fill_reason": "settlement_delta_exceeds_limit",
                    "gap": {"settlement_delta_seconds": 45000},
                },
                {
                    "action": "WATCH",
                    "missed_fill_reason": "settlement_delta_exceeds_limit",
                    "gap": {"settlement_delta_seconds": None},
                },
                {
                    "action": "MANUAL_REVIEW",
                    "missed_fill_reason": "settlement_delta_exceeds_limit",
                    "gap": {"settlement_delta_seconds": 47000},
                },
                {
                    "action": "WATCH",
                    "missed_fill_reason": "estimated_net_gap_below_minimum",
                    "gap": {"settlement_delta_seconds": 48000},
                },
            ]
        },
        max_settlement_delta_seconds=43200,
    )

    assert summary["settlement_delta"] == {
        "count": 1,
        "min_distance": 1800.0,
        "max_distance": 1800.0,
        "median_distance": 1800.0,
    }
    assert summary["net_gap"] == {
        "count": 0,
        "min_distance": None,
        "max_distance": None,
        "median_distance": None,
    }


def test_near_miss_summary_empty_shape_when_no_qualifying_rows() -> None:
    assert scan._near_miss_summary({"ledger": []}, min_net_gap=0.02) == {
        "net_gap": {
            "count": 0,
            "min_distance": None,
            "max_distance": None,
            "median_distance": None,
        },
        "settlement_delta": {
            "count": 0,
            "min_distance": None,
            "max_distance": None,
            "median_distance": None,
        },
    }
