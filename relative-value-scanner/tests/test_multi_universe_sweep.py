import json
from pathlib import Path

import scan


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _pipeline_summary(label: str, *, pair_count: int, watch_count: int, top_reasons: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "source": "targeted_pipeline_runner",
        "label": label,
        "summary": {
            "polymarket_normalized_count": pair_count + 10,
            "kalshi_normalized_count": pair_count + 1,
            "pair_count": pair_count,
            "evaluator_counts": {
                "WATCH": watch_count,
                "MANUAL_REVIEW": 1,
                "PAPER_CANDIDATE": 0,
            },
            "top_rejection_reasons": top_reasons,
            "gap_distribution": {
                "gross_gap_lte_0_count": 1,
                "gross_gap_gt_0_lte_0_005_count": 1,
                "gross_gap_gt_0_005_lte_0_01_count": 1,
                "gross_gap_gt_0_01_lte_0_02_count": 1,
                "gross_gap_gt_0_02_count": 1,
                "estimated_net_gap_gt_0_count": 3,
                "estimated_net_gap_lte_0_count": 1,
            },
        },
    }


def test_multi_universe_sweep_invokes_each_manifest_row_and_writes_aggregate(monkeypatch, tmp_path: Path) -> None:
    manifest = tmp_path / "sweep_manifest.json"
    _write(
        manifest,
        {
            "universes": [
                {
                    "label": "nba_kxnba",
                    "polymarket_tag_slug": "nba",
                    "kalshi_series_ticker": "KXNBA",
                },
                {
                    "label": "nfl_kxnfl",
                    "polymarket_tag_id": 450,
                    "kalshi_event_ticker": "KXNFL-26",
                },
                {
                    "label": "mlb_kxmlb",
                    "polymarket_tag_slug": "mlb",
                    "kalshi_series_ticker": "KXMLB",
                },
            ]
        },
    )
    calls = []

    def fake_run_targeted_pipeline(**kwargs):
        calls.append(kwargs.copy())
        label = kwargs["label"]
        if label == "nfl_kxnfl":
            return 7
        reasons = [
            {"reason": "settlement_delta_exceeds_limit", "count": 2},
            {"reason": "estimated_net_gap_below_minimum", "count": 1},
            {"reason": "no_positive_bid_ask_gap", "count": 1},
            {"reason": "unit_mismatch_not_accepted", "count": 1},
        ]
        _write(
            kwargs["output_dir"] / f"{label}_pipeline_summary.json",
            _pipeline_summary(label, pair_count=4 if label == "nba_kxnba" else 2, watch_count=3, top_reasons=reasons),
        )
        return 0

    monkeypatch.setattr(scan, "run_targeted_pipeline", fake_run_targeted_pipeline)

    result = scan.main(
        [
            "run-multi-universe-sweep",
            "--manifest",
            str(manifest),
            "--sweep-label",
            "daily_review",
            "--output-dir",
            str(tmp_path),
            "--max-settlement-delta-seconds",
            "43200",
            "--min-net-gap",
            "0.02",
            "--min-top-of-book-size",
            "5",
            "--accept-unit-mismatch",
        ]
    )

    assert result == 0
    assert [call["label"] for call in calls] == ["nba_kxnba", "nfl_kxnfl", "mlb_kxmlb"]
    assert calls[0]["polymarket_tag_slug"] == "nba"
    assert calls[0]["kalshi_series_ticker"] == "KXNBA"
    assert calls[1]["polymarket_tag_id"] == 450
    assert calls[1]["kalshi_event_ticker"] == "KXNFL-26"
    assert calls[2]["polymarket_tag_slug"] == "mlb"
    assert calls[2]["kalshi_series_ticker"] == "KXMLB"
    for call in calls:
        assert call["max_settlement_delta_seconds"] == 43200.0
        assert call["min_net_gap"] == 0.02
        assert call["min_top_of_book_size"] == 5.0
        assert call["accept_unit_mismatch"] is True

    summary = json.loads((tmp_path / "daily_review_sweep_summary.json").read_text(encoding="utf-8"))
    assert summary["schema_version"] == 1
    assert summary["completed_count"] == 2
    assert summary["failed_count"] == 1
    assert len(summary["universes"]) == 3
    assert summary["universes"][0]["status"] == "completed"
    assert summary["universes"][0]["polymarket_normalized_count"] == 14
    assert summary["universes"][0]["kalshi_normalized_count"] == 5
    assert summary["universes"][0]["pair_count"] == 4
    assert summary["universes"][0]["evaluator_counts"]["WATCH"] == 3
    assert len(summary["universes"][0]["top_rejection_reasons"]) == 3
    assert summary["universes"][0]["gap_distribution"]["estimated_net_gap_gt_0_count"] == 3
    assert summary["universes"][1]["status"] == "failed"
    assert summary["universes"][1]["failure_reason"] == "run_targeted_pipeline_returned_7"
    assert summary["universes"][1]["gap_distribution"] == {
        "gross_gap_lte_0_count": 0,
        "gross_gap_gt_0_lte_0_005_count": 0,
        "gross_gap_gt_0_005_lte_0_01_count": 0,
        "gross_gap_gt_0_01_lte_0_02_count": 0,
        "gross_gap_gt_0_02_count": 0,
        "estimated_net_gap_gt_0_count": 0,
        "estimated_net_gap_lte_0_count": 0,
    }

    markdown = (tmp_path / "daily_review_sweep_summary.md").read_text(encoding="utf-8")
    assert "Gap > 0" in markdown
    assert "Net > 0" in markdown
    assert "| nba_kxnba | completed | 14 | 5 | 4 | 4 | 3 |" in markdown
    assert "| nba_kxnba | completed |" in markdown
    assert "settlement_delta_exceeds_limit:2" in markdown
    assert "estimated_net_gap_below_minimum:1" in markdown
    assert "no_positive_bid_ask_gap:1" in markdown
    assert "unit_mismatch_not_accepted" not in markdown
    assert "POSSIBLE_ARB" not in json.dumps(summary)
    assert "POSSIBLE_ARB" not in markdown


def test_multi_universe_sweep_exits_one_when_no_universe_completes(monkeypatch, tmp_path: Path) -> None:
    manifest = tmp_path / "sweep_manifest.json"
    _write(
        manifest,
        {
            "universes": [
                {
                    "label": "nba_kxnba",
                    "polymarket_tag_slug": "nba",
                    "kalshi_series_ticker": "KXNBA",
                }
            ]
        },
    )

    def fake_run_targeted_pipeline(**kwargs):
        return 1

    monkeypatch.setattr(scan, "run_targeted_pipeline", fake_run_targeted_pipeline)

    result = scan.main(
        [
            "run-multi-universe-sweep",
            "--manifest",
            str(manifest),
            "--sweep-label",
            "all_failed",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert result == 1
    summary = json.loads((tmp_path / "all_failed_sweep_summary.json").read_text(encoding="utf-8"))
    assert summary["completed_count"] == 0
    assert summary["failed_count"] == 1
    assert summary["universes"][0]["failure_reason"] == "run_targeted_pipeline_returned_1"
