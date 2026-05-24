import json
from pathlib import Path

import scan


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _pipeline_summary_payload() -> dict:
    return {
        "schema_version": 1,
        "source": "targeted_pipeline_runner",
        "label": "nba_kxnba",
        "summary": {
            "polymarket_normalized_count": 14,
            "kalshi_normalized_count": 5,
            "polymarket_enriched_count": 11,
            "polymarket_enrichment_market_count": 14,
            "kalshi_enriched_count": 5,
            "kalshi_enrichment_market_count": 5,
            "pair_count": 4,
            "evaluator_counts": {
                "WATCH": 3,
                "MANUAL_REVIEW": 1,
                "PAPER_CANDIDATE": 0,
            },
            "gap_distribution": {
                "gross_gap_lte_0_count": 1,
                "gross_gap_gt_0_lte_0_005_count": 1,
                "gross_gap_gt_0_005_lte_0_01_count": 1,
                "gross_gap_gt_0_01_lte_0_02_count": 1,
                "gross_gap_gt_0_02_count": 1,
                "estimated_net_gap_gt_0_count": 3,
                "estimated_net_gap_lte_0_count": 1,
            },
            "near_miss_summary": {
                "net_gap": {
                    "count": 1,
                    "min_distance": 0.001,
                    "max_distance": 0.001,
                    "median_distance": 0.001,
                },
                "settlement_delta": {
                    "count": 2,
                    "min_distance": 1800.0,
                    "max_distance": 3600.0,
                    "median_distance": 2700.0,
                },
                "settlement_delta_near_pass": {
                    "count": 1,
                    "min_distance": 100.0,
                    "max_distance": 100.0,
                    "median_distance": 100.0,
                },
            },
            "top_rejection_reasons": [
                {"reason": "settlement_delta_exceeds_limit", "count": 2},
                {"reason": "estimated_net_gap_below_minimum", "count": 1},
            ],
        },
        "later_markout_command": "python scan.py replay-paper-candidate-markouts --ledger reports/x.json",
    }


def test_explain_pipeline_summary_prints_single_universe_block(tmp_path: Path, capsys) -> None:
    path = tmp_path / "nba_kxnba_pipeline_summary.json"
    _write(path, _pipeline_summary_payload())

    result = scan.main(["explain-pipeline-summary", "--summary", str(path)])

    assert result == 0
    output = capsys.readouterr().out
    assert "Pipeline: nba_kxnba" in output
    assert "polymarket_normalized_count: 14" in output
    assert "kalshi_normalized_count: 5" in output
    assert "polymarket_enriched: 11/14" in output
    assert "kalshi_enriched: 5/5" in output
    assert "pair_count: 4" in output
    assert "WATCH=3 MANUAL_REVIEW=1 PAPER_CANDIDATE=0" in output
    assert "Gap > 0 total: 4" in output
    assert "Net > 0: 3" in output
    assert "near_miss.net_gap.median_distance: 0.001" in output
    assert "near_miss.settlement_delta.median_distance: 2700.0" in output
    assert "near_miss.settlement_delta_near_pass.median_distance: 100.0" in output
    assert "top_rejection_reasons: settlement_delta_exceeds_limit:2,estimated_net_gap_below_minimum:1" in output
    assert "later_markout_command: python scan.py replay-paper-candidate-markouts --ledger reports/x.json" in output
    assert "explain_pipeline_summary_status=OK" in output


def test_explain_pipeline_summary_rejects_wrong_source(tmp_path: Path, capsys) -> None:
    path = tmp_path / "wrong_source_pipeline_summary.json"
    payload = _pipeline_summary_payload()
    payload["source"] = "multi_universe_sweep"
    _write(path, payload)

    result = scan.main(["explain-pipeline-summary", "--summary", str(path)])

    assert result == 1
    assert "source must be targeted_pipeline_runner" in capsys.readouterr().out


def test_explain_pipeline_summary_missing_file_returns_clear_error(tmp_path: Path, capsys) -> None:
    path = tmp_path / "missing_pipeline_summary.json"

    result = scan.main(["explain-pipeline-summary", "--summary", str(path)])

    assert result == 1
    output = capsys.readouterr().out
    assert "explain_pipeline_summary_status=FAILED" in output
    assert "file not found" in output
