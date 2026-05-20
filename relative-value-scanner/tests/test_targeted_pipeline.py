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
                        "ineligibility_reasons": ["settlement_delta_exceeds_limit"],
                        "missed_fill_reason": "settlement_delta_exceeds_limit",
                    },
                    {
                        "action": "MANUAL_REVIEW",
                        "ineligibility_reasons": ["unit_mismatch_not_accepted"],
                        "missed_fill_reason": "unit_mismatch_not_accepted",
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
    assert summary["summary"]["top_rejection_reasons"][0] == {
        "reason": "missed_fill:settlement_delta_exceeds_limit",
        "count": 1,
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
