import json
from pathlib import Path

import scan


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _summary_payload() -> dict:
    return {
        "schema_version": 1,
        "source": "multi_universe_sweep",
        "sweep_label": "daily_review",
        "completed_count": 1,
        "failed_count": 1,
        "universes": [
            {
                "label": "nba_kxnba",
                "status": "completed",
                "polymarket_normalized_count": 14,
                "kalshi_normalized_count": 5,
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
            {
                "label": "nhl_kxnhl",
                "status": "failed",
                "polymarket_normalized_count": None,
                "kalshi_normalized_count": None,
                "pair_count": None,
                "evaluator_counts": {},
                "gap_distribution": {},
                "near_miss_summary": {
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
                    "settlement_delta_near_pass": {
                        "count": 0,
                        "min_distance": None,
                        "max_distance": None,
                        "median_distance": None,
                    },
                },
                "top_rejection_reasons": [],
            },
        ],
    }


def test_explain_sweep_summary_prints_universe_blocks_and_footer(tmp_path: Path, capsys) -> None:
    path = tmp_path / "daily_review_sweep_summary.json"
    _write(path, _summary_payload())

    result = scan.main(["explain-sweep-summary", "--summary", str(path)])

    assert result == 0
    output = capsys.readouterr().out
    assert "Universe: nba_kxnba" in output
    assert "Universe: nhl_kxnhl" in output
    assert "polymarket_normalized_count: 14" in output
    assert "kalshi_normalized_count: 5" in output
    assert "pair_count: 4" in output
    assert "WATCH=3 MANUAL_REVIEW=1 PAPER_CANDIDATE=0" in output
    assert "Gap > 0 total: 4" in output
    assert "Net > 0: 3" in output
    assert "near_miss.net_gap.median_distance: 0.001" in output
    assert "near_miss.settlement_delta.median_distance: 2700.0" in output
    assert "near_miss.settlement_delta_near_pass.median_distance: 100.0" in output
    assert "near_miss.net_gap.median_distance: n/a" in output
    assert "top_rejection_reasons: settlement_delta_exceeds_limit:2,estimated_net_gap_below_minimum:1" in output
    assert "Aggregate: total_universes=2 completed=1 failed=1" in output
    assert "explain_sweep_summary_status=OK" in output


def test_explain_sweep_summary_rejects_wrong_schema_version(tmp_path: Path, capsys) -> None:
    path = tmp_path / "bad_sweep_summary.json"
    payload = _summary_payload()
    payload["schema_version"] = 2
    _write(path, payload)

    result = scan.main(["explain-sweep-summary", "--summary", str(path)])

    assert result == 1
    assert "explain_sweep_summary_status=FAILED message=sweep_summary schema_version must be 1" in capsys.readouterr().out


def test_explain_sweep_summary_rejects_wrong_source(tmp_path: Path, capsys) -> None:
    path = tmp_path / "wrong_source_sweep_summary.json"
    payload = _summary_payload()
    payload["source"] = "targeted_pipeline_runner"
    _write(path, payload)

    result = scan.main(["explain-sweep-summary", "--summary", str(path)])

    assert result == 1
    assert "source must be multi_universe_sweep" in capsys.readouterr().out


def test_explain_sweep_summary_rejects_non_object_json(tmp_path: Path, capsys) -> None:
    path = tmp_path / "bad_sweep_summary.json"
    _write(path, [])

    result = scan.main(["explain-sweep-summary", "--summary", str(path)])

    assert result == 1
    assert "JSON must be an object" in capsys.readouterr().out


def test_explain_sweep_summary_missing_file_returns_clear_error(tmp_path: Path, capsys) -> None:
    path = tmp_path / "missing_sweep_summary.json"

    result = scan.main(["explain-sweep-summary", "--summary", str(path)])

    assert result == 1
    output = capsys.readouterr().out
    assert "explain_sweep_summary_status=FAILED" in output
    assert "sweep_summary file not found" in output
