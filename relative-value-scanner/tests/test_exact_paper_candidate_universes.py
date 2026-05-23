import json
from datetime import datetime, timezone
from pathlib import Path

import scan
from relative_value.exact_paper_candidate_universes import (
    UniverseSpec,
    build_exact_paper_candidate_universe_report,
    build_exact_paper_candidate_universe_report_files,
    default_exact_paper_candidate_universe_specs,
)


NOW = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)


def _snapshot(count: int, venue: str = "polymarket") -> dict:
    return {
        "schema_version": 1,
        "normalized_markets": [
            {
                "venue": venue,
                "market_id": f"{venue}-{index}",
                "ticker": f"KX-{index}" if venue == "kalshi" else None,
                "question": "Will the test happen?",
            }
            for index in range(count)
        ],
    }


def _pairs(count: int = 1) -> dict:
    return {
        "schema_version": 1,
        "pair_count": count,
        "pairs": [
            {
                "polymarket": {"market_id": f"poly-{index}"},
                "kalshi": {"ticker": f"KX-{index}"},
                "ineligibility_reasons": [],
            }
            for index in range(count)
        ],
    }


def _board(blockers: list[str] | None = None, strict_passes: int = 1) -> dict:
    return {
        "schema_version": 1,
        "strict_same_payoff_pass_count": strict_passes,
        "top_blockers": [{"blocker": blocker, "count": 1} for blocker in blockers or []],
        "rows": [],
    }


def _derived(trusted: int = 1) -> dict:
    return {
        "schema_version": 1,
        "pairs": [],
        "same_payoff_evidence_attachment": {
            "trusted_relationship_attached_count": trusted,
        },
    }


def _enriched(count: int = 1, venue: str = "polymarket") -> dict:
    return {
        "schema_version": 1,
        "normalized_markets": _snapshot(count, venue)["normalized_markets"],
        "orderbook_enrichment": {
            "market_count": count,
            "enriched_count": count,
            "unenriched_count": 0,
            "snapshot_warnings": [],
        },
    }


def _stale_enriched(count: int = 1, venue: str = "polymarket") -> dict:
    payload = _enriched(count, venue)
    payload["orderbook_enrichment"]["snapshot_warnings"] = ["stale_snapshot"]
    return payload


def _evaluator(action: str = "WATCH", reason: str = "estimated_net_gap_below_minimum") -> dict:
    counts = {"PAPER_CANDIDATE": 0, "MANUAL_REVIEW": 0, "WATCH": 0}
    counts[action] = 1
    return {
        "schema_version": 1,
        "counts_by_action": counts,
        "ledger": [
            {
                "candidate_id": "poly-1__KX-1",
                "action": action,
                "missed_fill_reason": reason,
                "ineligibility_reasons": [reason] if reason else [],
            }
        ],
    }


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_mlb_universe_with_trusted_relationship_and_fee_gap_blocker(tmp_path: Path) -> None:
    spec = UniverseSpec(
        universe_id="mlb",
        label="MLB",
        category="sports",
        polymarket_snapshot=_write(tmp_path / "poly.json", _snapshot(2, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "kalshi.json", _snapshot(2, "kalshi")),
        pairs=_write(tmp_path / "pairs.json", _pairs()),
        board=_write(tmp_path / "board.json", _board()),
        derived_pairs=_write(tmp_path / "derived.json", _derived(1)),
        evaluator=_write(tmp_path / "eval.json", _evaluator()),
        polymarket_enriched=_write(tmp_path / "poly_enriched.json", _enriched(1, "polymarket")),
        kalshi_enriched=_write(tmp_path / "kalshi_enriched.json", _enriched(1, "kalshi")),
    )

    report = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)
    row = report["universes"][0]

    assert row["readiness"] == "EXECUTION_DATA_AVAILABLE"
    assert row["trusted_relationship_count"] == 1
    assert row["evaluator_counts"]["WATCH"] == 1
    assert "fee_adjusted_gap_below_minimum" in row["blockers"]
    assert report["summary"]["closest_universe_id"] == "mlb"


def test_mlb_execution_data_with_stale_orderbooks_recommends_paper_check_refresh(tmp_path: Path) -> None:
    spec = UniverseSpec(
        universe_id="mlb_world_series_kxmlb",
        label="MLB World Series / KXMLB",
        category="sports",
        polymarket_snapshot=_write(tmp_path / "poly.json", _snapshot(2, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "kalshi.json", _snapshot(2, "kalshi")),
        pairs=_write(tmp_path / "pairs.json", _pairs()),
        board=_write(tmp_path / "board.json", _board()),
        derived_pairs=_write(tmp_path / "derived.json", _derived(1)),
        evaluator=_write(tmp_path / "eval.json", _evaluator()),
        polymarket_enriched=_write(tmp_path / "poly_enriched.json", _stale_enriched(1, "polymarket")),
        kalshi_enriched=_write(tmp_path / "kalshi_enriched.json", _enriched(1, "kalshi")),
    )

    row = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)["universes"][0]

    assert row["readiness"] == "EXECUTION_DATA_AVAILABLE"
    assert "stale_orderbooks" in row["blockers"]
    assert row["recommended_next_commands"] == [
        "python scan.py run-mlb-world-series-paper-check "
        "--polymarket-snapshot reports/live_readonly/mlb/polymarket_live_readonly_snapshot.json "
        "--kalshi-snapshot reports/live_readonly/mlb/kalshi_live_readonly_snapshot.json "
        "--rebuild-pairs-from-snapshots "
        "--accept-unit-mismatch --trust-settlement-normalization mlb_world_series_timezone_convention_drift"
    ]


def test_same_scope_inventory_without_trusted_relationship_is_reported(tmp_path: Path) -> None:
    spec = UniverseSpec(
        universe_id="nba",
        label="NBA",
        category="sports",
        polymarket_snapshot=_write(tmp_path / "poly.json", _snapshot(1, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "kalshi.json", _snapshot(1, "kalshi")),
        pairs=_write(tmp_path / "pairs.json", _pairs()),
        board=_write(tmp_path / "board.json", _board(["settlement_source_mismatch"], strict_passes=0)),
        derived_pairs=_write(tmp_path / "derived.json", _derived(0)),
        polymarket_enriched=_write(tmp_path / "poly_enriched.json", _enriched(1, "polymarket")),
        kalshi_enriched=_write(tmp_path / "kalshi_enriched.json", _enriched(1, "kalshi")),
    )

    row = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)["universes"][0]

    assert row["readiness"] == "SAME_SCOPE_PAIRS_AVAILABLE"
    assert row["same_scope_pair_count"] == 1
    assert row["trusted_relationship_count"] == 0
    assert "same_payoff_board_blockers" in row["blockers"]
    assert row["recommended_next_commands"] == [
        f"python scan.py same-payoff-board --pairs {spec.pairs} --polymarket-enriched {spec.polymarket_enriched} --kalshi-enriched {spec.kalshi_enriched}"
    ]


def test_execution_rows_without_trusted_relationship_stays_same_scope_available(tmp_path: Path) -> None:
    spec = UniverseSpec(
        universe_id="nba",
        label="NBA Champion / KXNBA",
        category="sports_championship_outright",
        polymarket_snapshot=_write(tmp_path / "poly.json", _snapshot(4, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "kalshi.json", _snapshot(4, "kalshi")),
        pairs=_write(tmp_path / "pairs.json", _pairs(4)),
        board=_write(tmp_path / "board.json", _board(["settlement_wording_mismatch"], strict_passes=0)),
        derived_pairs=_write(tmp_path / "derived.json", _derived(0)),
        evaluator=_write(tmp_path / "eval.json", _evaluator()),
        polymarket_enriched=_write(tmp_path / "poly_enriched.json", _enriched(4, "polymarket")),
        kalshi_enriched=_write(tmp_path / "kalshi_enriched.json", _enriched(4, "kalshi")),
    )

    row = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)["universes"][0]

    assert row["readiness"] == "SAME_SCOPE_PAIRS_AVAILABLE"
    assert row["same_scope_pair_count"] == 4
    assert row["execution_data_row_count"] == 1
    assert row["trusted_relationship_count"] == 0
    assert "same_payoff_board_blockers" in row["blockers"]
    assert row["recommended_next_commands"] == [
        f"python scan.py same-payoff-board --pairs {spec.pairs} --polymarket-enriched {spec.polymarket_enriched} --kalshi-enriched {spec.kalshi_enriched}"
    ]


def test_reference_only_sources_cannot_be_executable_legs(tmp_path: Path) -> None:
    spec = UniverseSpec(
        universe_id="reference",
        label="Reference",
        category="sports",
        polymarket_snapshot=_write(tmp_path / "poly.json", _snapshot(1, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "kalshi.json", _snapshot(1, "kalshi")),
        pairs=_write(tmp_path / "pairs.json", _pairs()),
        board=_write(tmp_path / "board.json", _board(["polymarket_not_executable_kalshi_polymarket_leg"], strict_passes=0)),
    )

    row = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)["universes"][0]

    assert row["readiness"] == "SAME_SCOPE_PAIRS_AVAILABLE"
    assert "reference_only_source" in row["blockers"]


def test_subset_superset_stays_non_paper_readiness(tmp_path: Path) -> None:
    spec = UniverseSpec(
        universe_id="btc",
        label="BTC",
        category="threshold",
        polymarket_snapshot=_write(tmp_path / "poly.json", _snapshot(1, "polymarket")),
        kalshi_snapshot=_write(tmp_path / "kalshi.json", _snapshot(1, "kalshi")),
        pairs=_write(tmp_path / "pairs.json", _pairs()),
        board=_write(tmp_path / "board.json", _board(["relationship_shape_subset_or_superset"], strict_passes=0)),
        evaluator=_write(tmp_path / "eval.json", _evaluator("MANUAL_REVIEW", "relationship_same_payoff_not_proven")),
    )

    row = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)["universes"][0]

    assert row["readiness"] == "SAME_SCOPE_PAIRS_AVAILABLE"
    assert row["evaluator_counts"]["MANUAL_REVIEW"] == 1
    assert "scope_mismatch" in row["blockers"]
    assert row["evaluator_counts"]["PAPER_CANDIDATE"] == 0


def test_report_does_not_emit_disallowed_action_labels_without_existing_candidate(tmp_path: Path) -> None:
    spec = UniverseSpec(
        universe_id="empty",
        label="Empty",
        category="sports",
    )

    report = build_exact_paper_candidate_universe_report(specs=[spec], generated_at=NOW)
    encoded = json.dumps(report)

    assert '"PAPER"' not in encoded
    assert "POSSIBLE_ARB" not in encoded
    assert "trade" not in encoded.lower()


def test_command_writes_reports_and_default_scan_remains_static_fixture(tmp_path: Path, capsys) -> None:
    result = build_exact_paper_candidate_universe_report_files(
        project_root=tmp_path,
        json_output_path=tmp_path / "universes.json",
        markdown_output_path=tmp_path / "universes.md",
        specs=[UniverseSpec(universe_id="empty", label="Empty", category="sports")],
        generated_at=NOW,
    )

    assert result["universes"][0]["readiness"] == "NO_INVENTORY"
    assert (tmp_path / "universes.json").exists()
    assert (tmp_path / "universes.md").exists()

    scan_result = scan.main([])
    assert scan_result == 0
    assert "data_source_mode=STATIC_FIXTURE" in capsys.readouterr().out


def test_default_recommended_commands_use_universe_specific_live_readonly_dirs(tmp_path: Path) -> None:
    specs = default_exact_paper_candidate_universe_specs(tmp_path)
    commands = " ".join(command for spec in specs for command in (spec.recommended_fetch_command, spec.recommended_pair_command) if command)

    assert "reports/live_readonly/mlb" in commands
    assert "reports/live_readonly/nba" in commands
    assert "reports/live_readonly/nhl" in commands
    assert "reports/live_readonly/btc" in commands
    assert "reports/live_readonly/fed" in commands
    assert "--output-dir reports/live_readonly --report-dir reports/live_readonly" not in commands
